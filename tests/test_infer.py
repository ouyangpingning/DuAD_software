"""DetectPage 测试推理冒烟：打开图片 → ONNX 推理 → 热力图/分数显示（真机模型）。"""
import os
import sys

os.environ["QML_COMPAT_RESOLVE_URLS_ON_ASSIGNMENT"] = "1"

from PySide6.QtCore import Qt, QTimer, QEventLoop, QUrl, QPoint, QPointF, Property, QObject, Slot, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTENT = os.path.join(PROJECT_ROOT, "DuAD_SoftwareContent")
sys.path.insert(0, os.path.join(PROJECT_ROOT, "backend"))
from env import ensure_sdk_paths

ensure_sdk_paths()

from Src.algorithm_bridge import AlgorithmBridge
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fake_bridges import FakeCameraBridge, FakeCollectBridge, FakeDetectBridge, FakeLightBridge, FakeMqttBridge

results = []


def check(name, cond):
    results.append((name, cond))
    print(("PASS" if cond else "FAIL"), "-", name)


def wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def walk(item, pred, depth=0):
    if pred(item):
        return item
    if depth < 20:
        ch = item.childItems() if isinstance(item, QQuickItem) else (
            [item.property("contentItem")] if isinstance(item.property("contentItem"), QQuickItem) else [])
        for c in ch:
            hit = walk(c, pred, depth + 1)
            if hit:
                return hit
    return None


def find_btn(item, text, depth=0):
    if "Button" in item.metaObject().className() and item.property("text") == text:
        return item
    if depth < 14:
        for c in item.childItems():
            hit = find_btn(c, text, depth + 1)
            if hit:
                return hit
    return None


def click_item(window, item):
    p = item.mapToScene(QPointF(0, 0)).toPoint() + QPoint(int(item.width() / 2), int(item.height() / 2))
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, p)


class FakeBridge(QObject):
    helpRequested = Signal()
    camChanged = Signal(); colChanged = Signal(); ownerChanged = Signal(); algChanged = Signal()

    def __init__(self):
        super().__init__()
        self._h = os.path.expanduser("~")
        self._cam = False; self._col = False; self._owner = ""; self._alg = False

    @Property(int, constant=True)
    def languageIndex(self):
        return 1

    @Property(str, constant=True)
    def homeDir(self):
        return self._h

    @Slot(str, result=bool)
    def isDir(self, path):
        return os.path.isdir(path)

    @Slot(result=bool)
    def shouldShowHelp(self):
        return False

    @Slot()
    def markHelpShown(self):
        pass

    @Property(bool, notify=camChanged)
    def cameraConnected(self):
        return self._cam

    @cameraConnected.setter
    def cameraConnected(self, v):
        self._cam = bool(v); self.camChanged.emit()

    @Property(bool, notify=colChanged)
    def collecting(self):
        return self._col

    @collecting.setter
    def collecting(self, v):
        self._col = bool(v); self.colChanged.emit()

    @Property(str, notify=ownerChanged)
    def collectingOwner(self):
        return self._owner

    @collectingOwner.setter
    def collectingOwner(self, v):
        v = str(v) if v else ""
        if v not in ("", "collect", "detect"):
            return
        if self._owner != v:
            self._owner = v
            self._col = bool(v)
            self.ownerChanged.emit()
            self.colChanged.emit()

    @Property(bool, notify=algChanged)
    def algorithmEnabled(self):
        return self._alg

    @algorithmEnabled.setter
    def algorithmEnabled(self, v):
        self._alg = bool(v); self.algChanged.emit()


class MinimalCameraBridge(QObject):
    """DetectPage 联调引用所需的轻量相机桥（本测试不采相机帧）。"""
    frameIndexChanged = Signal()
    imageWidthChanged = Signal()
    imageHeightChanged = Signal()

    def __init__(self):
        super().__init__()
        self._frame_index = 0

    def _getFrameIndex(self):
        return self._frame_index

    frameIndex = Property(int, _getFrameIndex, notify=frameIndexChanged)

    def _getImageWidth(self):
        return 2448

    imageWidth = Property(int, _getImageWidth, notify=imageWidthChanged)

    def _getImageHeight(self):
        return 2048

    imageHeight = Property(int, _getImageHeight, notify=imageHeightChanged)

    @Slot(str, result=float)
    def getFeature(self, name):
        return 0.0

    @Slot(float, float, float, float)
    def applyRoi(self, nx, ny, nw, nh):
        pass

    @Slot()
    def resetRoi(self):
        pass


# 生成测试图片（512x512 渐变 + 噪点）
import numpy as np
from PIL import Image

test_img = "/tmp/opencode/test_bottle.png"
arr = (np.linspace(0, 255, 512, dtype=np.uint8)[:, None] * np.ones((1, 512), dtype=np.uint8))
arr = np.stack([arr] * 3, axis=2)
arr = (arr + np.random.randint(0, 40, arr.shape).astype(np.uint8)).clip(0, 255)
Image.fromarray(arr).save(test_img)

app = QGuiApplication(sys.argv)
engine = QQmlApplicationEngine()
engine.addImportPath(CONTENT)
bridge = FakeBridge()
engine.rootContext().setContextProperty("AppBridge", bridge)
# 用带 metadata 的新模型（训练期标定：图像阈值 + 像素 F1-max 阈值），
# 掩模定位图依赖 pixel_threshold（旧模型无 metadata 时无掩模输出）
_NEW_MODEL_DIR = ("/run/media/lxb/Soft/资料/刘祥宾/研究生论文/"
                  "模型权重保存0-2-4-all-shot&log/MvTec-AD/DuAD/model_onnx")
_new_model = os.path.join(_NEW_MODEL_DIR, "bottle_k4_s0_full.onnx")
alg_bridge = AlgorithmBridge(
    model_path=_new_model if os.path.exists(_new_model)
    else os.path.join(PROJECT_ROOT, "bottle_k4_s0_full.onnx"))
engine.rootContext().setContextProperty("AlgorithmBridge", alg_bridge)
camera_bridge = FakeCameraBridge()
engine.rootContext().setContextProperty("CameraBridge", camera_bridge)
detect_bridge = FakeDetectBridge()
engine.rootContext().setContextProperty("DetectBridge", detect_bridge)
collect_bridge = FakeCollectBridge()
engine.rootContext().setContextProperty("CollectBridge", collect_bridge)
light_bridge = FakeLightBridge()
engine.rootContext().setContextProperty("LightBridge", light_bridge)
mqtt_bridge = FakeMqttBridge()
engine.rootContext().setContextProperty("MqttBridge", mqtt_bridge)
engine.load(QUrl.fromLocalFile(os.path.join(CONTENT, "App.qml")))
assert engine.rootObjects(), "App.qml 加载失败"
window = engine.rootObjects()[0]

stack = walk(window, lambda i: "StackLayout" in i.metaObject().className())
stack.setProperty("currentIndex", 3)
wait(500)
detect_page = stack.childItems()[3]

# 展开侧栏
handle = None
for c in detect_page.findChildren(object):
    if c.objectName() == "sidePanelHandle":
        handle = c
        break
click_item(window, handle)
wait(300)

# 1. 打开图片按钮存在
open_btn = find_btn(detect_page, "打开图片")
check("打开图片按钮", open_btn is not None)

# 2. 原生文件选择器能打开（FileDialog）
click_item(window, open_btn)
wait(500)
picker = None
for c in detect_page.findChildren(object):
    if "FileDialog" in c.metaObject().className() or "QFileDialogOptions" in c.metaObject().className():
        picker = c
        break
# offscreen 下原生对话框不置 visible（真机正常弹出），此处只验证对象存在
check("文件选择器存在(原生FileDialog)", picker is not None)
if picker:
    picker.close()
wait(200)

# 3. 直接设置测试图片（模拟选择文件）→ 执行推理
detect_page.setProperty("_testImagePath", test_img)
wait(200)
infer_btn = find_btn(detect_page, "执行推理")
check("执行推理按钮可用", infer_btn is not None and infer_btn.property("enabled") is True)
click_item(window, infer_btn)
wait(200)
check("推理中状态", detect_page.property("_inferring") is True)

# 4. 等待推理完成（onnx CPU 推理可能数秒）
for _ in range(60):
    wait(500)
    if not detect_page.property("_inferring"):
        break
check("推理完成", detect_page.property("_inferring") is False)
check("测试结果激活", detect_page.property("_testActive") is True)
heat_path = detect_page.property("_testHeatmapPath")
check("热力图文件生成", isinstance(heat_path, str) and os.path.exists(heat_path))
score = float(detect_page.property("_score"))
# 分数语义：判别器负输出的 patch max，带符号、无界、未归一化（正常图 ≈ -1~1.5，
# 异常图 > 阈值）。噪声测试图相对 bottle 类别明显异常，分数应超过标定阈值。
threshold = float(alg_bridge.property("threshold"))
check(f"异常分数超过标定阈值 ({score:.4f} > {threshold:.4f})", score > threshold)

# 5. 图像窗口切换到测试结果
origin_view = walk(detect_page, lambda i: "ImageView" in i.metaObject().className())
check("原图窗口显示测试图片", origin_view.property("simulated") is False
      and origin_view.property("imageActive") is True)
views = []


def collect_views(item, depth=0):
    if "ImageView" in item.metaObject().className():
        views.append(item)
    if depth < 12:
        for c in item.childItems():
            collect_views(c, depth + 1)


collect_views(detect_page)
heat_view = views[1] if len(views) > 1 else None
# 异常定位窗口：显示二值掩模叠加图（maskReady → _testMaskPath）；旧模型无
# 像素阈值时退化为空 source（此时 imageSource 为空字符串，不算 file://）
mask_path = detect_page.property("_testMaskPath")
check("掩模定位图生成", isinstance(mask_path, str) and len(mask_path) > 0
      and os.path.exists(mask_path))
check("异常定位窗口显示掩模", heat_view is not None
      and heat_view.property("imageActive") is True
      and "file://" in str(heat_view.property("imageSource")))

failed = [n for n, ok in results if not ok]
print()
print(f"{len(results) - len(failed)}/{len(results)} passed")
sys.exit(1 if failed else 0)
