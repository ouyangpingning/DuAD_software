"""Detect 实时管线冒烟：CameraBridge 帧 → RealtimeDetectBridge → provider + 分数。"""
import os
import sys
import threading
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QObject, Signal, QTimer, QEventLoop
from PySide6.QtGui import QGuiApplication, QImage

PROJECT_ROOT = os.environ.get(
    "DUAD_PROJECT_ROOT",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "backend"))

import ctypes
from ctypes import c_ubyte, c_int, c_void_p, c_ulonglong, Structure, addressof, pointer

import numpy as np
import Src.camera as camera_module
from Src.algorithm_bridge import AlgorithmBridge
from Src.camera_bridge import CameraBridge
from Src.frame_provider import CameraFrameProvider
from Src.realtime_detect_bridge import RealtimeDetectBridge


class FakeCamera(QObject):
    rawFrameReady = Signal(object)

    def __init__(self):
        super().__init__()
        self.cameraConnected = False


class FakeAlg:
    def __init__(self):
        self.modelPath = "fake.onnx"
        self.calls = 0

    def predict_frame(self, img):
        self.calls += 1
        time.sleep(0.03)   # 模拟真实推理耗时，保证多帧能形成频率估计
        heat = np.zeros((16, 16, 3), dtype=np.uint8)
        heat[..., 0] = 255
        mask = np.zeros((16, 16, 3), dtype=np.uint8)
        mask[..., 1] = 255
        return heat, 2.25, mask


def wait_until(fn, timeout=3000):
    deadline = time.time() + timeout / 1000.0
    while time.time() < deadline:
        if fn():
            return True
        QTestWait(30)
    return fn()


class QTestWait:
    def __init__(self, ms):
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()


app = QGuiApplication(sys.argv)
results = []


def check(name, cond):
    results.append((name, cond))
    print(("PASS" if cond else "FAIL"), "-", name)


# ── 1. CameraBridge 帧转换 + provider ──
cam = CameraBridge()
frame = np.zeros((32, 48, 3), dtype=np.uint8)
frame[..., 2] = 255
scores = []


def on_raw(img):
    scores.append(("raw", img.shape))


cam.rawFrameReady.connect(on_raw)
cam._onFrame(frame)
check("帧索引递增", cam.frameIndex == 1)
check("原图 provider 有帧", cam.frameProvider.has_frame("original") is True)
check("rawFrameReady 收到 numpy", scores and scores[0] == ("raw", (32, 48, 3)))

provider_img = cam.frameProvider.requestImage("original?t=1", None, None)
check("provider 返回 RGB 帧", provider_img.width() == 48 and provider_img.height() == 32)

# ── 2. 实时检测管线：最新帧 + 分数信号 + heatmap/mask provider ──
fake_cam = FakeCamera()
fake_alg = FakeAlg()
provider = CameraFrameProvider()
bridge = RealtimeDetectBridge(fake_cam, fake_alg, provider)
got_scores = []
bridge.scoreReady.connect(lambda s: got_scores.append(s))
bridge.set_algorithm_enabled(True)
bridge.start()
check("管线运行", bridge.running is True)

for i in range(4):
    fake_cam.rawFrameReady.emit(np.zeros((32, 48, 3), dtype=np.uint8))
    wait_until(lambda: fake_alg.calls >= i + 1, timeout=500)

ok = wait_until(lambda: fake_alg.calls >= 4 and len(got_scores) >= 3)
check("后台完成实时推理", ok and len(got_scores) >= 1)
check("分数正确", got_scores and abs(got_scores[-1] - 2.25) < 1e-6)
check("热力图 provider 有帧", provider.has_frame("heatmap") is True)
check("定位图 provider 有帧", provider.has_frame("mask") is True)
check("hasResult/hasMask 置位", bridge.hasResult is True and bridge.hasMask is True)
check("推理频率已估算", bridge.realtimeFps > 0)

bridge.stop()
check("管线停止", bridge.running is False)

# 停止后队列不再消费
calls_before = fake_alg.calls
for i in range(3):
    fake_cam.rawFrameReady.emit(np.zeros((32, 48, 3), dtype=np.uint8))
wait_until(lambda: False, timeout=150) if False else None
time.sleep(0.05)
check("停止后不继续推理", fake_alg.calls == calls_before)

# ── 3. clear_results 清空 provider ──
bridge.clear_results()
check("清空热力图", provider.has_frame("heatmap") is False)
check("清空定位图", provider.has_frame("mask") is False)

# ── 4. 像素格式 → Bayer 排列映射（黄/蓝互换回归防护）──
class FrameParam(Structure):
    _fields_ = [
        ("user_param_index", c_void_p),
        ("status", c_int),
        ("image_buf", c_void_p),
        ("image_size", c_int),
        ("width", c_int),
        ("height", c_int),
        ("pixel_format", c_int),
        ("frame_id", c_ulonglong),
        ("timestamp", c_ulonglong),
        ("reserved", c_int),
    ]


seen_filters = []


def fake_dx_raw8_to_rgb24(in_addr, out_addr, w, h, cvt_type, bayer, flip):
    seen_filters.append(bayer)
    return 0


camera_module.dx_raw8_to_rgb24 = fake_dx_raw8_to_rgb24
dev = camera_module.CameraDevice("fake")
converted = []
dev.image_captured.connect(lambda img: converted.append(img))
raw = np.arange(64, dtype=np.uint8)
buf = (c_ubyte * 64)(*raw)
param = FrameParam()
param.image_buf = addressof(buf)
param.image_size = 64
param.width = 8
param.height = 8
param.pixel_format = camera_module.GxPixelFormatEntry.BAYER_RG8

dev.capture_image(pointer(param))
check("RG8 使用 RG Bayer 排列", seen_filters and seen_filters[-1] == 1)
check("RG8 输出 RGB24", converted and converted[-1].shape == (8, 8, 3))

param.pixel_format = camera_module.GxPixelFormatEntry.MONO8
dev.capture_image(pointer(param))
check("Mono8 输出 RGB24", converted and converted[-1].shape == (8, 8, 3))
check("Mono8 三通道相同", np.array_equal(
    converted[-1][..., 0], converted[-1][..., 1]))

# ── 5. ONNX 模型卸载：session 引用清空 + 信号通知 ──
alg = AlgorithmBridge(model_path="")


class DummySession:
    pass


alg._detector = DummySession()
alg._threshold = 9.9
unload_events = []
alg.modelUnloaded.connect(lambda: unload_events.append(1))
alg.unloadModel()
check("卸载后 detector 置空", alg._detector is None)
check("卸载后 modelPath 置空", alg.modelPath == "")
check("卸载后阈值恢复默认", abs(alg.threshold - 1.7) < 1e-9)
check("卸载信号已发", len(unload_events) == 1)
check("无模型时 predict_frame 返回 None",
      alg.predict_frame(np.zeros((8, 8, 3), dtype=np.uint8)) is None)

# ── 6. ROI 应用：采集中自动停流→写参数→重启采集 ──
class FakeRoiDevice:
    def __init__(self):
        self.features = {
            "GX_INT_WIDTH": 2448.0, "GX_INT_HEIGHT": 2048.0,
            "GX_INT_WIDTH_MAX": 2448.0, "GX_INT_HEIGHT_MAX": 2048.0,
            "GX_INT_OFFSET_X": 0.0, "GX_INT_OFFSET_Y": 0.0,
        }
        self.stopped = 0
        self.started = 0

    def get_remote_feature(self, name, ftype):
        return self.features.get(name)

    def set_remote_feature(self, name, ftype, value):
        self.features[name] = float(value)
        return True

    def gather_stop(self):
        self.stopped += 1
        return True

    def gather_start(self):
        self.started += 1
        return True


roi_cam = CameraBridge()
roi_dev = FakeRoiDevice()
roi_cam._device = roi_dev
roi_cam._gathering = True
roi_cam.applyRoi(0.25, 0.25, 0.5, 0.5)
check("ROI 应用前自动停流", roi_dev.stopped == 1)
restarted = wait_until(lambda: roi_dev.started >= 1, timeout=2500)
check("ROI 应用后自动重开采集", restarted and roi_dev.started == 1)
check("ROI 宽度已写入", roi_dev.features["GX_INT_WIDTH"] > 0)
check("ROI 高度已写入", roi_dev.features["GX_INT_HEIGHT"] > 0)

# 恢复全幅应回到首次 ROI 前的设置值，而不是传感器最大分辨率
class FakeRoiDevicePreset(FakeRoiDevice):
    pass


preset_cam = CameraBridge()
preset_dev = FakeRoiDevicePreset()
preset_dev.features.update({
    "GX_INT_WIDTH": 1224.0, "GX_INT_HEIGHT": 1024.0,
    "GX_INT_WIDTH_MAX": 2448.0, "GX_INT_HEIGHT_MAX": 2048.0,
})
preset_cam._device = preset_dev
preset_cam._gathering = True
preset_cam.applyRoi(0.0, 0.0, 0.5, 0.5)
wait_until(lambda: preset_dev.started >= 1, timeout=2500)
preset_cam.resetRoi()
wait_until(lambda: preset_dev.started >= 2, timeout=2500)
check("恢复全幅回到首次 ROI 前宽度", preset_dev.features["GX_INT_WIDTH"] == 1224.0)
check("恢复全幅回到首次 ROI 前高度", preset_dev.features["GX_INT_HEIGHT"] == 1024.0)

failed = [n for n, ok in results if not ok]
print()
print(f"{len(results) - len(failed)}/{len(results)} passed")
sys.exit(1 if failed else 0)
