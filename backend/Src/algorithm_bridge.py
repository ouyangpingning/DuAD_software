"""
AlgorithmBridge — 异常检测算法的 QML 桥（测试推理）。

功能：加载 ONNX 模型 → 对单张图片执行一次推理 → 返回异常分数 + 热力图。
推理在后台线程执行（DINOv2 类大模型 CPU 推理耗时 1~3s，不能阻塞 UI 线程）。
热力图以临时 PNG 文件返回（QML Image 直接加载，避免 ImageProvider 复杂度）。

用法（QML）：
    AlgorithmBridge.modelChanged()      → 模型就绪/加载失败
    AlgorithmBridge.inferenceReady(score, heatmapPath)
    AlgorithmBridge.inferenceError(msg)
    AlgorithmBridge.loadModel(path)
    AlgorithmBridge.inferImage(imgPath)
    AlgorithmBridge.warmup()            → 后台预热（建 session + 编译 kernel）
"""

import ctypes
import gc
import math
import os
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
from PIL import Image
from PySide6.QtCore import QObject, Slot, Signal, Property

from alg.deploy.onnx_infer import ONNXAnomalyDetector


def _release_memory():
    """归还 glibc 堆顶端的空闲内存（onnxruntime session 释放后 RSS 才真正下降）。

    onnxruntime 已禁用 CPU arena（enable_cpu_mem_arena=False），session 析构时
    大块内存 free 回 glibc，但 glibc 不主动 munmap → 切换多个 124MB 模型时 RSS
    会累积膨胀。malloc_trim(0) 强制归还 heap 顶端空闲块，实测 547MB → 65MB。
    非 glibc 平台（macOS/Windows）无此函数，静默跳过。
    """
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass


class AlgorithmBridge(QObject):
    """QML 可调用的算法桥（后台线程推理）。"""

    modelReady = Signal(str)  # 模型就绪（参数 = 模型名/描述）
    modelUnloaded = Signal()   # 模型已卸载（session 已释放，RSS 应下降）
    inferenceReady = Signal(float, str)  # (异常分数, 热力图 PNG 路径)
    maskReady = Signal(str)  # 二值掩模叠加图 PNG 路径（异常像素红色高亮）
    inferenceError = Signal(str)  # 错误信息（中文）
    lastInferenceMsChanged = Signal()
    modelPathChanged = Signal()
    thresholdChanged = Signal()
    pixelThresholdChanged = Signal()

    # 未标定时回退的经验阈值（bottle 实测正常/异常分布间隔中点）
    _DEFAULT_THRESHOLD = 1.7

    def __init__(self, model_path: str = "", parent=None):
        super().__init__(parent)
        self._model_path = model_path
        self._detector = None  # 惰性加载（首次推理时线程内创建）
        self._lock = threading.Lock()
        self._build_lock = threading.Lock()  # 防止 warmup/测试推理/实时推理并发建 session
        self._infer_lock = threading.Lock()  # 单 session 串行推理，避免显存/内存尖峰
        self._tmp_dir = tempfile.mkdtemp(prefix="duad_heatmap_")
        self._last_inference_ms = 0.0
        self._threshold = self._DEFAULT_THRESHOLD
        self._pixel_threshold = None  # 像素级分割阈值（仅来自 metadata，json 无此字段）
        self._pixel_threshold_override = None  # 用户可调像素阈值（None=用 metadata F1-max）
        self._refine_mask = False  # 精细定位：掩模阈值取 max(F1阈值, 图内P99)
        self._vmin = None  # 热力图固定显示尺度（<model>.scale.json；None=逐图百分位）
        self._vmax = None
        self._load_threshold_from_json()
        self._load_scale_from_json()

    def _load_scale_from_json(self):
        """从热力图固定显示尺度文件读取 vmin/vmax（calibrate_scale.py 产出）。

        查找顺序：模型旁 `<模型>.onnx.scale.json`（服务器端/真机标定优先）→
        项目内 `backend/model_scales/<模型>.scale.json`（本地统计，模型目录
        只读时用）。固定尺度下所有图共用同一色阶：正常图深紫、缺陷像素超出
        vmax 亮黄（方案 2，替代逐图百分位归一化的"正常图也泛黄"）。文件都不
        存在时 vmin/vmax 保持 None，predict 回退逐图百分位归一化。
        """
        self._vmin = None
        self._vmax = None
        if not self._model_path:
            return
        candidates = [self._model_path + ".scale.json"]
        # 项目内 model_scales/ 目录（按模型文件名查找）
        local_dir = Path(__file__).resolve().parent.parent / "model_scales"
        candidates.append(local_dir / (os.path.basename(self._model_path) + ".scale.json"))
        json_path = next((c for c in candidates if os.path.exists(str(c))), None)
        if not json_path:
            return
        try:
            import json

            with open(str(json_path), "r", encoding="utf-8") as f:
                data = json.load(f)
            self._vmin = float(data["vmin"])
            self._vmax = float(data["vmax"])
            print(f"[AlgorithmBridge] 热力图固定尺度已加载: "
                  f"[{self._vmin:.3f}, {self._vmax:.3f}] ({json_path})")
        except Exception as e:
            print(f"[AlgorithmBridge] 读取显示尺度文件失败: {e}")

    def _load_threshold_from_json(self):
        """从 <模型名>.onnx.threshold.json 读取标定阈值（calibrate_threshold.py 产出）。

        优先 recommended；JSON 不存在时重置回默认值（切换模型后不能残留
        旧模型的阈值），读取失败时保持默认。新模型（训练时已把阈值写入 ONNX
        metadata）会在此之后由 _sync_threshold_from_metadata 覆盖为 metadata 值。"""
        self._threshold = self._DEFAULT_THRESHOLD
        if not self._model_path:
            return
        json_path = self._model_path + ".threshold.json"
        if not os.path.exists(json_path):
            return
        try:
            import json

            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._threshold = float(data["recommended"])
            print(f"[AlgorithmBridge] 阈值已从标定文件加载: {self._threshold:.3f}")
        except Exception as e:
            print(f"[AlgorithmBridge] 读取阈值标定文件失败: {e}")

    def _sync_threshold_from_metadata(self, detector):
        """detector 建好后从 ONNX metadata 同步阈值（metadata 优先于 json）。

        训练时标定的阈值写入模型 metadata（export_onnx.py），这里在 session
        就绪后读取并覆盖 json 值；旧模型无 metadata 时 image_threshold 为 None，
        保持 json/默认值不动。
        """
        t = getattr(detector, "image_threshold", None)
        if t is not None:
            t = float(t)
            if abs(t - self._threshold) > 1e-12:
                self._threshold = t
                print(
                    f"[AlgorithmBridge] 阈值已从模型 metadata 加载: {self._threshold:.3f}"
                )
                self.thresholdChanged.emit()
        # 像素级分割阈值（原始 amap 尺度，契约 3.2）；未标定时 detector 侧为 None
        p = getattr(detector, "pixel_threshold", None)
        if p is not None:
            p = float(p)
            if self._pixel_threshold is None or abs(p - self._pixel_threshold) > 1e-12:
                self._pixel_threshold = p
                print(
                    f"[AlgorithmBridge] 像素分割阈值已从 metadata 加载: {self._pixel_threshold:.3f}"
                )
                self.pixelThresholdChanged.emit()
        # 热力图固定显示尺度：metadata（训练期标定）优先于本地 scale.json
        vm = getattr(detector, "heatmap_vmin", None)
        vx = getattr(detector, "heatmap_vmax", None)
        if vm is not None and vx is not None:
            vm, vx = float(vm), float(vx)
            if (self._vmin, self._vmax) != (vm, vx):
                self._vmin, self._vmax = vm, vx
                print(f"[AlgorithmBridge] 热力图显示尺度已从 metadata 加载: "
                      f"[{self._vmin:.3f}, {self._vmax:.3f}]")

    def _getLastInferenceMs(self) -> float:
        return self._last_inference_ms

    # 推理耗时（毫秒）— 供 QML 展示，不改 inferenceReady 签名
    lastInferenceMs = Property(
        float, _getLastInferenceMs, notify=lastInferenceMsChanged
    )

    def _getModelPath(self) -> str:
        return self._model_path

    # 当前模型路径 — QML 显示模型名（切换类别需换 onnx 文件）
    modelPath = Property(str, _getModelPath, notify=modelPathChanged)

    def _getThreshold(self) -> float:
        return self._threshold

    # 异常判定阈值 — 来自标定脚本（模型同目录 *.threshold.json），
    # 切换模型时随模型更新；QML 侧作为异常阈值输入框的默认值
    threshold = Property(float, _getThreshold, notify=thresholdChanged)

    def _getPixelThreshold(self) -> float:
        # 未标定时返回 NaN（QML 侧用 NaN 表示无值，退化为手动阈值）
        return (
            self._pixel_threshold if self._pixel_threshold is not None else float("nan")
        )

    # 像素级分割阈值 — 来自模型 metadata（训练时标定的 F1-max 阈值，
    # 原始 amap 尺度）；NaN 表示未标定（QML 侧退化为手动阈值）
    pixelThreshold = Property(float, _getPixelThreshold, notify=pixelThresholdChanged)

    @Slot(float)
    def setPixelThreshold(self, value: float):
        """设置用户像素阈值覆盖（下次推理生效）。

        NaN 表示恢复使用模型 metadata 的 F1-max 阈值。
        """
        if math.isnan(value):
            self._pixel_threshold_override = None
        else:
            self._pixel_threshold_override = float(value)

    @Slot(bool)
    def setRefineMask(self, enabled: bool):
        """精细定位开关（下次推理生效）。

        开启后掩模阈值 = max(F1 阈值, 图内 P99)，定位收窄到最异常区域
        （视觉接近服务器端 GT 逐图阈值效果，但不依赖 GT）。
        """
        self._refine_mask = bool(enabled)

    @Slot(str)
    def loadModel(self, path: str):
        """指定模型路径（下次推理生效，懒加载）。

        模型路径变化时必须在 lock 下丢弃旧 session（_detector 置 None）：
        懒加载只在 None 时重建，否则切换类别后推理仍走旧模型（静默错误）。
        随后后台重新预热，切换后首次推理不卡加载/编译。
        """
        if path and os.path.exists(path):
            if path == self._model_path:
                return
            with self._lock:
                old = self._detector
                self._detector = None  # 释放旧 session，下次推理重建
                self._model_path = path
            # 显式释放旧 session 的大块内存（del 触发 C++ 析构；gc.collect
            # 兜底循环引用；malloc_trim 归还 glibc 堆），避免切换多个模型时
            # RSS 累积膨胀
            if old is not None:
                del old
                gc.collect()
                _release_memory()
            self._load_threshold_from_json()  # 新模型可能带自己的标定阈值
            self._load_scale_from_json()  # 新模型可能带自己的固定显示尺度
            self.modelPathChanged.emit()
            self.thresholdChanged.emit()
            self.modelReady.emit(os.path.basename(path))
            self.warmup()
        else:
            self.inferenceError.emit(f"模型文件不存在: {path}")

    @Slot()
    def unloadModel(self):
        """卸载当前 ONNX 模型并释放 session 内存。

        session 析构 + gc.collect + malloc_trim 的组合与 loadModel 切模型一致：
        大块内存会立即归还 glibc；阈值/像素阈值/热力图尺度全部恢复默认，
        避免残留旧模型参数。
        """
        with self._lock:
            old = self._detector
            self._detector = None
            self._model_path = ""

        if old is not None:
            del old
            gc.collect()
            _release_memory()
            print("[AlgorithmBridge] ONNX 模型已卸载，session 内存已释放")

        self._threshold = self._DEFAULT_THRESHOLD
        self._pixel_threshold = None
        self._pixel_threshold_override = None
        self._refine_mask = False
        self._vmin = None
        self._vmax = None
        self.modelPathChanged.emit()
        self.thresholdChanged.emit()
        self.pixelThresholdChanged.emit()
        self.modelUnloaded.emit()

    @Slot()
    def warmup(self):
        """后台预热：建 session + 跑一次 dummy 推理，把图优化/kernel 编译
        提前到启动阶段，消除首次"执行推理"的 3~5s 假卡顿。模型未指定时跳过。"""
        if not self._model_path:
            return
        threading.Thread(target=self._warmup_worker, daemon=True).start()

    def predict_frame(self, image_rgb: np.ndarray):
        """同步预测一帧 numpy RGB 图（实时管线与测试推理共用）。

        返回 (heatmap_rgb, score, mask_overlay_or_None)；模型未加载/文件不存在
        时返回 None。调用方必须从后台线程调用（ONNX 推理耗时 70ms~3s）。
        """
        if image_rgb is None or not self._model_path or not os.path.exists(self._model_path):
            return None
        with self._infer_lock:
            detector = self._get_detector()
            heatmap_rgb, score = detector.predict(
                image_rgb,
                vmin=self._vmin,
                vmax=self._vmax,
                pixel_threshold=self._pixel_threshold_override,
                refine_quantile=0.99 if self._refine_mask else None,
            )
            mask_overlay = self._build_mask_overlay(image_rgb, detector)
        return heatmap_rgb, score, mask_overlay

    @Slot(str)
    def inferImage(self, img_path: str):
        """提交单图推理任务（异步，结果经信号返回）。"""
        if not img_path or not os.path.exists(img_path):
            self.inferenceError.emit("图片文件不存在")
            return
        if not self._model_path or not os.path.exists(self._model_path):
            self.inferenceError.emit("模型未加载，请先指定 ONNX 模型文件")
            return
        threading.Thread(
            target=self._infer_worker, args=(img_path,), daemon=True
        ).start()

    # ── 后台线程：加载模型（首次）+ 推理 + 保存热力图 ──
    def _get_detector(self) -> ONNXAnomalyDetector:
        # 懒加载模型（线程内创建，避免阻塞 UI；模型已加载则复用）。
        # _build_lock 保证同一时刻只有一个线程构造 session（warmup 和
        # 实时推理可能同时触发懒加载，否则会双份 124MB session 内存峰值）。
        # session 构造不在 _lock 内进行：不能长锁阻塞 loadModel。
        with self._build_lock:
            with self._lock:
                det = self._detector
                path = self._model_path
            if det is not None:
                self._sync_threshold_from_metadata(det)
                return det

            if not path or not os.path.exists(path):
                return None
            new_det = ONNXAnomalyDetector(path)
            with self._lock:
                if self._detector is None and self._model_path == path:
                    # 构造期间模型未切换：采用为当前 session
                    self._detector = new_det
                    det = new_det
                    adopted = True
                else:
                    # 构造期间模型已切换：丢弃刚构造的过时 session
                    det = self._detector
                    adopted = False
            if not adopted:
                del new_det
                gc.collect()
                _release_memory()
            else:
                # session 就绪后，用模型 metadata 里的阈值覆盖 json/默认值（metadata 优先）
                self._sync_threshold_from_metadata(det)
            return det

    def _warmup_worker(self):
        try:
            with self._infer_lock:
                det = self._get_detector()
                det.predict(np.zeros((64, 64, 3), dtype=np.uint8))
            print(f"[AlgorithmBridge] 模型预热完成（{det.session.get_providers()}）")
        except Exception as e:
            print(f"[AlgorithmBridge] 预热失败（首次推理会重试）: {e}")

    def _infer_worker(self, img_path: str):
        try:
            # 读图 + 推理（用户像素阈值覆盖优先，否则 metadata F1-max；
            # 热力图用固定显示尺度 vmin/vmax，无 scale 文件时逐图百分位；
            # 精细定位开启时掩模阈值收窄到 max(F1阈值, 图内P99)）
            img = np.asarray(Image.open(img_path).convert("RGB"))
            t0 = time.time()
            result = self.predict_frame(img)
            if result is None:
                self.inferenceError.emit("模型未加载，请先指定 ONNX 模型文件")
                return
            heatmap_rgb, score, mask_overlay = result
            elapsed_ms = (time.time() - t0) * 1000.0
            self._last_inference_ms = elapsed_ms
            self.lastInferenceMsChanged.emit()

            # 热力图（plasma）存临时 PNG —— 默认显示
            out_path = os.path.join(self._tmp_dir, f"heatmap_{os.getpid()}.png")
            Image.fromarray(heatmap_rgb).save(out_path, format="PNG")

            # 二值掩模叠加图（异常像素高亮，定位缺陷位置；无像素阈值时为 None）
            # 始终生成：QML 侧 switch 决定显示热力图还是定位图，切换无需重推
            mask_path = self._save_mask_overlay(img, mask_overlay)

            # 先发掩模定位图、再发推理结果（QML 侧 _testActive 置 true 时
            # _testMaskPath 已就绪，避免 imageSource 出现空的 file:// 协议警告）
            if mask_path:
                self.maskReady.emit(mask_path)
            self.inferenceReady.emit(score, out_path)
        except Exception as e:
            print(f"[AlgorithmBridge] 推理异常: {e}")
            self.inferenceError.emit(f"推理失败: {e}")

    def _build_mask_overlay(self, img: np.ndarray, detector) -> np.ndarray:
        """生成二值掩模叠加图：原图缩放到模型尺寸，异常像素红色高亮。

        掩模 = detector.last_anomaly_mask（hm_smooth > pixel_threshold，
        原始尺度，契约 3.2）。pixel_threshold 未标定（None）时返回 None。
        """
        mask = detector.last_anomaly_mask
        if mask is None:
            return None
        ts = detector.target_size
        orig = np.asarray(Image.fromarray(img).resize((ts, ts), Image.BILINEAR))
        overlay = orig.copy()
        hit = mask > 0
        if hit.any():
            # 异常像素高亮：红色 + 85% 覆盖。⚠ 必须用 float 混合——uint8
            # 整数乘除会溢出（255*85 mod 256≈171 → //100≈1），高亮会暗到近黑。
            highlight = np.array([255.0, 0.0, 0.0], dtype=np.float32)
            overlay[hit] = (
                orig[hit].astype(np.float32) * 0.15 + highlight * 0.85
            ).astype(np.uint8)
        return overlay

    def _save_mask_overlay(self, img: np.ndarray, mask_overlay=None) -> str:
        """把掩模叠加 ndarray 存为 PNG（旧签名兼容：传 detector 时内部重建）。"""
        if mask_overlay is None:
            return None
        if hasattr(mask_overlay, "target_size"):
            mask_overlay = self._build_mask_overlay(img, mask_overlay)
            if mask_overlay is None:
                return None
        p = os.path.join(self._tmp_dir, f"mask_{os.getpid()}.png")
        Image.fromarray(mask_overlay).save(p, format="PNG")
        return p
