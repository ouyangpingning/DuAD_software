"""RealtimeDetectBridge — DetectPage 实时检测管线。

职责：
    1. 接收 CameraBridge.rawFrameReady 的最新相机帧（只保留最新帧，推理
       慢时自动丢旧帧，不会在队列里堆积）；
    2. 在后台线程调用 AlgorithmBridge.predict_frame（复用同一个 ONNX
       session，避免为实时管线重复加载 124MB 模型）；
    3. 把热力图/掩模定位图写入 CameraFrameProvider，经信号通知 QML 重取。

QML 侧：
    image://camera/original?t=<CameraBridge.frameIndex>   原图
    image://camera/heatmap?t=<DetectBridge.resultCounter> 热力图
    image://camera/mask?t=<DetectBridge.maskCounter>       缺陷定位图
    DetectBridge.scoreReady(score) → 更新异常分数
"""
import queue
import threading
import time
from collections import deque

import numpy as np
from PySide6.QtCore import QObject, Signal, Property, Qt
from PySide6.QtGui import QImage


def _numpy_rgb_to_qimage(img: np.ndarray) -> QImage:
    """numpy RGB [H,W,3] uint8 → 独立 QImage（拷贝，脱离 numpy 生命周期）。"""
    h, w, _ = img.shape
    return QImage(img.data, w, h, w * 3, QImage.Format_RGB888).copy()


class RealtimeDetectBridge(QObject):
    """实时检测桥（Python 内部管线，QML 只消费信号与属性）。"""

    scoreReady = Signal(float)       # 新分数（异常分数，未归一化）
    inferenceFailed = Signal(str)    # 推理错误（中文，QML 可 log/显示）
    resultUpdated = Signal()         # 热力图已更新（resultCounter 自增后发出）
    maskUpdated = Signal()           # 掩模定位图已更新
    runningChanged = Signal()
    realtimeFpsChanged = Signal()
    lastInferenceMsChanged = Signal()
    lastScoreChanged = Signal()

    def __init__(self, camera_bridge, algorithm_bridge, frame_provider, parent=None):
        super().__init__(parent)
        self._camera_bridge = camera_bridge
        self._algorithm_bridge = algorithm_bridge
        self._frame_provider = frame_provider
        self._queue = queue.Queue(maxsize=1)
        self._run_event = threading.Event()
        self._worker = None
        self._worker_lock = threading.Lock()
        self._epoch = 0          # start() 自增；旧会话慢推理完成后不允许回写结果
        self._running = False
        self._algorithm_enabled = False
        self._result_counter = 0
        self._mask_counter = 0
        self._has_result = False
        self._has_mask = False
        self._last_score = 0.0
        self._last_inference_ms = 0.0
        self._realtime_fps = 0.0
        self._result_times = deque(maxlen=30)
        self._model_missing_logged = False
        self._last_error_at = 0.0
        self._last_error_msg = ""

        # DirectConnection：CameraBridge 可能在 SDK 采集回调线程直接发帧；
        # 实时管线只需要一个 maxsize=1 的 Python queue，不必让每一帧都排队
        # 经过 Qt 主线程事件队列（30fps 下会拖慢 UI 且堆积 QImage 信号）。
        camera_bridge.rawFrameReady.connect(self._submit_frame, Qt.DirectConnection)

    # ── 状态属性 ──────────────────────────────────────────
    def _getRunning(self) -> bool:
        return self._running

    running = Property(bool, _getRunning, notify=runningChanged)

    def _getResultCounter(self) -> int:
        return self._result_counter

    # QML Image source 缓存 bust 参数：每次自增强制重新向 provider 取图
    resultCounter = Property(int, _getResultCounter, notify=resultUpdated)

    def _getMaskCounter(self) -> int:
        return self._mask_counter

    maskCounter = Property(int, _getMaskCounter, notify=maskUpdated)

    def _getHasResult(self) -> bool:
        return self._has_result

    hasResult = Property(bool, _getHasResult, notify=resultUpdated)

    def _getHasMask(self) -> bool:
        return self._has_mask

    hasMask = Property(bool, _getHasMask, notify=maskUpdated)

    def _getLastScore(self) -> float:
        return self._last_score

    lastScore = Property(float, _getLastScore, notify=lastScoreChanged)

    def _getLastInferenceMs(self) -> float:
        return self._last_inference_ms

    lastInferenceMs = Property(float, _getLastInferenceMs, notify=lastInferenceMsChanged)

    def _getRealtimeFps(self) -> float:
        return self._realtime_fps

    realtimeFps = Property(float, _getRealtimeFps, notify=realtimeFpsChanged)

    # ── 管线控制（main.py 根据 AppBridge 状态驱动）──────────
    def set_algorithm_enabled(self, enabled: bool):
        """算法开关（来自 AppBridge.algorithmEnabled）。关闭时清掉旧结果。"""
        enabled = bool(enabled)
        if self._algorithm_enabled == enabled:
            return
        self._algorithm_enabled = enabled
        if not enabled:
            self.clear_results()

    def start(self):
        """开始实时采集会话（相机帧已由 CameraBridge.startGather 驱动）。"""
        self.clear_results()
        self._model_missing_logged = False
        self._last_error_at = 0.0
        self._last_error_msg = ""
        self._result_times.clear()
        if self._realtime_fps != 0.0:
            self._realtime_fps = 0.0
            self.realtimeFpsChanged.emit()
        self._epoch += 1
        self._running = True
        self.runningChanged.emit()
        self._run_event.set()
        with self._worker_lock:
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(
                    target=self._worker_loop, name="realtime-detect", daemon=True
                )
                self._worker.start()
        print("[RealtimeDetectBridge] 实时检测管线已启动")

    def stop(self):
        """停止实时采集会话（丢弃队列中的旧帧，停止消费新结果）。"""
        self._running = False
        self._run_event.clear()
        self._drain_queue()
        self.runningChanged.emit()
        print("[RealtimeDetectBridge] 实时检测管线已停止")

    def on_model_changed(self):
        """切换模型：清空旧模型结果，并允许下一次“模型未就绪”日志重新提示。"""
        self._model_missing_logged = False
        self._last_error_at = 0.0
        self._last_error_msg = ""
        self.clear_results()

    def clear_results(self):
        """清空上次热力图/分数（切换模型、断开相机、关闭算法时调用）。"""
        self._frame_provider.clear_frame("heatmap")
        self._frame_provider.clear_frame("mask")
        self._has_result = False
        self._has_mask = False
        self._result_counter += 1
        self._mask_counter += 1
        self.resultUpdated.emit()
        self.maskUpdated.emit()
        if self._last_score != 0.0:
            self._last_score = 0.0
            self.lastScoreChanged.emit()
            self.scoreReady.emit(0.0)

    # ── 相机帧入口（只保留最新帧）────────────────────────
    def _submit_frame(self, numpy_img):
        if not self._running:
            return
        if numpy_img is None:
            return
        try:
            self._queue.put_nowait(numpy_img)
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(numpy_img)
            except queue.Full:
                pass

    def _drain_queue(self):
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return

    # ── 后台推理循环（最新帧优先，无新帧时阻塞等待）────────
    def _worker_loop(self):
        while True:
            img = self._queue.get()
            if img is None or not self._run_event.is_set():
                continue
            if not self._running:
                continue
            epoch = self._epoch
            if not self._algorithm_enabled:
                # 算法关闭时只采集原图，不推理
                continue
            if not getattr(self._algorithm_bridge, "modelPath", ""):
                if not self._model_missing_logged:
                    print("[RealtimeDetectBridge] 模型未选择：只显示原图，不执行推理")
                    self._model_missing_logged = True
                continue

            t0 = time.time()
            try:
                result = self._algorithm_bridge.predict_frame(img)
            except Exception as e:
                # 持续失败（例如 CUDA provider 无设备）时不要每帧刷日志/信号，
                # 也不要在每帧重复构造 ONNX session：退避 0.5s 再取最新帧。
                now = time.monotonic()
                msg = str(e)
                if msg != self._last_error_msg or now - self._last_error_at >= 2.0:
                    self._last_error_msg = msg
                    self._last_error_at = now
                    print(f"[RealtimeDetectBridge] 实时推理异常: {e}")
                    self.inferenceFailed.emit(f"实时推理失败: {e}")
                time.sleep(0.5)
                continue

            if result is None:
                if not self._model_missing_logged:
                    print("[RealtimeDetectBridge] 模型加载失败或未就绪")
                    self._model_missing_logged = True
                    self.inferenceFailed.emit("实时推理未就绪，请检查模型文件")
                time.sleep(0.2)
                continue

            if not self._running or epoch != self._epoch:
                # 推理期间用户停止/重开了采集：旧会话结果不回写 UI
                continue
            self._model_missing_logged = False
            self._last_error_msg = ""
            heatmap_rgb, score, mask_overlay = result
            self._last_inference_ms = (time.time() - t0) * 1000.0
            self.lastInferenceMsChanged.emit()

            # 先写 provider，再自增计数器/发信号：QML 收到通知时一定取到新图
            heat_qimg = _numpy_rgb_to_qimage(heatmap_rgb)
            self._frame_provider.set_frame("heatmap", heat_qimg)
            self._has_result = True
            self._result_counter += 1
            self.resultUpdated.emit()

            if mask_overlay is not None:
                mask_qimg = _numpy_rgb_to_qimage(mask_overlay)
                self._frame_provider.set_frame("mask", mask_qimg)
                self._has_mask = True
            else:
                self._frame_provider.clear_frame("mask")
                self._has_mask = False
            self._mask_counter += 1
            self.maskUpdated.emit()

            self._last_score = float(score)
            self.scoreReady.emit(float(score))
            self.lastScoreChanged.emit()
            self._update_realtime_fps()

    def _update_realtime_fps(self):
        """用最近 30 次推理结果估算频率（≈ 1 / 平均推理耗时）。"""
        now = time.monotonic()
        self._result_times.append(now)
        if len(self._result_times) >= 2:
            span = now - self._result_times[0]
            if span > 1e-6:
                new_fps = (len(self._result_times) - 1) / span
                if abs(new_fps - self._realtime_fps) > 0.05:
                    self._realtime_fps = new_fps
                    self.realtimeFpsChanged.emit()
