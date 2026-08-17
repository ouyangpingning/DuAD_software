"""CollectBridge — 图像采集页的实时保存管线。

QML 只负责配置与展示；真正的节流、队列、写盘全部在 Python 侧完成：
    CameraBridge.rawFrameReady(np RGB)
      → maxsize=1 最新帧队列
      → 后台线程按 saveInterval 节流
      → PIL 保存 {prefix}_{timestamp}_{counter:05d}.{jpg|png|bmp}

与 DetectPage 的互斥由 AppBridge.collectingOwner 仲裁：
    owner=="collect" 时 main.py 调 start()
    owner 变为其他值/相机断开时 main.py 调 stop()
"""
import os
import queue
import re
import threading
import time
from pathlib import Path

import numpy as np
from PIL import Image
from PySide6.QtCore import QObject, Signal, Property, Slot, Qt

_ALLOWED_FORMATS = {"jpg", "jpeg", "png", "bmp"}


class CollectBridge(QObject):
    """QML 可调用的采集保存桥。"""

    savingChanged = Signal()
    configChanged = Signal()
    savedCountChanged = Signal()
    lastSavedPathChanged = Signal()
    saveError = Signal(str)          # 配置/写盘错误（中文，QML 直接显示）

    def __init__(self, camera_bridge, parent=None):
        super().__init__(parent)
        self._camera_bridge = camera_bridge
        self._queue = queue.Queue(maxsize=1)
        self._worker = None
        self._worker_lock = threading.Lock()
        self._saving = False
        self._saved_count = 0
        self._last_saved_path = ""
        self._config = {
            "path": None,
            "prefix": "capture",
            "format": "jpg",
            "interval": 1.0,
        }
        # 与 RealtimeDetectBridge 一样：直连接收原始帧，不经过 Qt 主线程事件队列
        camera_bridge.rawFrameReady.connect(self._submit_frame, Qt.DirectConnection)

    # ── 状态属性 ──────────────────────────────────────────
    def _getSaving(self) -> bool:
        return self._saving

    saving = Property(bool, _getSaving, notify=savingChanged)

    def _getConfigured(self) -> bool:
        return self._config.get("path") is not None

    configured = Property(bool, _getConfigured, notify=configChanged)

    def _getSavedCount(self) -> int:
        return self._saved_count

    savedCount = Property(int, _getSavedCount, notify=savedCountChanged)

    def _getLastSavedPath(self) -> str:
        return self._last_saved_path

    lastSavedPath = Property(str, _getLastSavedPath, notify=lastSavedPathChanged)

    # ── 配置（QML 在申请 collectingOwner="collect" 前调用）────
    @Slot(str, str, str, float, result=bool)
    def configure(self, save_path: str, prefix: str, fmt: str, interval: float) -> bool:
        """配置保存目录/前缀/格式/间隔。成功返回 True，失败发 saveError。"""
        if self._saving:
            self.saveError.emit("采集中不能修改保存设置")
            return False
        try:
            p = Path(str(save_path or "")).expanduser()
            if not p.is_absolute():
                p = (Path.home() / p).resolve()
            prefix = re.sub(r'[\\/:*?"<>|\s]+', "_", str(prefix or "")).strip("_")
            if not prefix:
                prefix = "capture"
            fmt = str(fmt or "jpg").lower().lstrip(".")
            if fmt not in _ALLOWED_FORMATS:
                self.saveError.emit(f"不支持的图片格式: {fmt}")
                return False
            if fmt == "jpeg":
                fmt = "jpg"
            interval = max(0.1, min(3600.0, float(interval)))
            p.mkdir(parents=True, exist_ok=True)
            self._config = {
                "path": p,
                "prefix": prefix,
                "format": fmt,
                "interval": interval,
            }
            self.configChanged.emit()
            print(f"[CollectBridge] 保存配置: {p} / {prefix}_*.{fmt} / "
                  f"每 {interval:.1f}s 一张")
            return True
        except Exception as e:
            print(f"[CollectBridge] 配置失败: {e}")
            self.saveError.emit(f"保存配置失败: {e}")
            return False

    # ── 会话控制（main.py 根据 collectingOwner 驱动）────────
    def start(self):
        """开始保存会话（相机帧已由 CameraBridge.startGather 驱动）。"""
        if self._config.get("path") is None:
            self.saveError.emit("请先设置保存目录")
            return
        self._saved_count = 0
        self.savedCountChanged.emit()
        self._saving = True
        self.savingChanged.emit()
        with self._worker_lock:
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(
                    target=self._worker_loop, name="collect-save", daemon=True
                )
                self._worker.start()
        print("[CollectBridge] 图像保存会话已启动")

    def stop(self):
        """停止保存会话。"""
        if not self._saving:
            return
        self._saving = False
        self._drain_queue()
        self.savingChanged.emit()
        print(f"[CollectBridge] 图像保存会话已停止，本次共保存 {self._saved_count} 张")

    # ── 帧入口（只保留最新帧）────────────────────────────
    def _submit_frame(self, numpy_img):
        if not self._saving or numpy_img is None:
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

    # ── 后台保存循环 ─────────────────────────────────────
    def _worker_loop(self):
        last_save = 0.0
        while True:
            img = self._queue.get()
            if img is None or not self._saving:
                continue
            now = time.monotonic()
            if now - last_save < self._config["interval"]:
                continue
            try:
                path = self._save_frame(img)
            except Exception as e:
                print(f"[CollectBridge] 保存失败: {e}")
                self.saveError.emit(f"保存图像失败: {e}")
                continue
            last_save = now
            self._saved_count += 1
            self.savedCountChanged.emit()
            self._last_saved_path = str(path)
            self.lastSavedPathChanged.emit()

    def _save_frame(self, numpy_img: np.ndarray) -> Path:
        cfg = self._config
        ts = time.strftime("%Y%m%d_%H%M%S")
        filename = f"{cfg['prefix']}_{ts}_{self._saved_count + 1:05d}.{cfg['format']}"
        out = cfg["path"] / filename
        image = Image.fromarray(np.asarray(numpy_img), "RGB")
        if cfg["format"] in ("jpg", "jpeg"):
            image.save(out, format="JPEG", quality=95)
        else:
            image.save(out, format=cfg["format"].upper())
        return out
