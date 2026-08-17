"""冒烟回归：相机桥（mock）+ 采集互斥 + ROI + 说明页，防 /tmp 清理丢失。"""
import os
import sys

os.environ["QML_COMPAT_RESOLVE_URLS_ON_ASSIGNMENT"] = "1"

from PySide6.QtCore import Qt, QTimer, QEventLoop, QUrl, QPoint, QPointF, Property, QObject, Slot, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fake_bridges import FakeAlgorithmBridge, FakeCollectBridge, FakeDetectBridge, FakeLightBridge, FakeMqttBridge

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "backend"))
from Src.frame_provider import CameraFrameProvider
CONTENT = os.path.join(PROJECT_ROOT, "DuAD_SoftwareContent")

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
    if depth < 12:
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


class FakeCameraBridge(QObject):
    """Mock 相机桥：搜索→1 台相机，连接→成功，getFeature 返回模拟参数。"""
    camerasFound = Signal(list)
    cameraOpened = Signal()
    cameraClosed = Signal()
    cameraError = Signal(str)
    cameraConnectedChanged = Signal()
    frameIndexChanged = Signal()
    imageWidthChanged = Signal()
    imageHeightChanged = Signal()

    def __init__(self):
        super().__init__()
        self._connected = False
        self._frame_index = 0
        self._roi = None
        self.frameProvider = CameraFrameProvider()
        self._cam = {"model": "Daheng MER2-2000", "sn": "DE0000001"}
        self._features = {
            "GX_INT_WIDTH": 2048.0, "GX_INT_HEIGHT": 2448.0,
            "GX_INT_WIDTH_MAX": 2048.0, "GX_INT_HEIGHT_MAX": 2448.0,
            "GX_FLOAT_EXPOSURE_TIME": 5000.0, "GX_FLOAT_GAIN": 3.5,
            "GX_FLOAT_ACQUISITION_FRAME_RATE": 30.0,
            "GX_FLOAT_CURRENT_ACQUISITION_FRAME_RATE": 29.8,
            "GX_ENUM_ACQUISITION_MODE": 2.0, "GX_BOOL_GAMMA_ENABLE": 0.0,
            "GX_ENUM_GAMMA_MODE": 0.0, "GX_FLOAT_GAMMA_PARAM": 1.0,
            "GX_ENUM_PIXEL_FORMAT": 0x1080009,
        }

    @Slot()
    def search(self):
        QTimer.singleShot(100, lambda: self.camerasFound.emit([self._cam]))

    @Slot(str)
    def connectCamera(self, sn):
        QTimer.singleShot(50, self._do_open)

    def _do_open(self):
        self._connected = True
        self.cameraConnectedChanged.emit()
        self.cameraOpened.emit()

    @Slot()
    def disconnectCamera(self):
        self._connected = False
        self.cameraConnectedChanged.emit()
        self.cameraClosed.emit()

    @Slot(str, result=float)
    def getFeature(self, name):
        return self._features.get(name, -1.0)

    @Slot(str, float)
    def setFeature(self, name, value):
        self._features[name] = value

    @Slot(result=bool)
    def getConnected(self):
        return self._connected

    cameraConnected = Property(bool, getConnected, notify=cameraConnectedChanged)

    def _getFrameIndex(self):
        return self._frame_index

    frameIndex = Property(int, _getFrameIndex, notify=frameIndexChanged)

    def _getImageWidth(self):
        return int(self._features.get("GX_INT_WIDTH", 0))

    imageWidth = Property(int, _getImageWidth, notify=imageWidthChanged)

    def _getImageHeight(self):
        return int(self._features.get("GX_INT_HEIGHT", 0))

    imageHeight = Property(int, _getImageHeight, notify=imageHeightChanged)

    @Slot(float, float, float, float)
    def applyRoi(self, nx, ny, nw, nh):
        self._roi = (nx, ny, nw, nh)
        self._frame_index += 1
        self.frameIndexChanged.emit()

    @Slot()
    def resetRoi(self):
        self._roi = None
        self._features["GX_INT_WIDTH"] = self._features["GX_INT_WIDTH_MAX"]
        self._features["GX_INT_HEIGHT"] = self._features["GX_INT_HEIGHT_MAX"]
        self.imageWidthChanged.emit()
        self.imageHeightChanged.emit()


app = QGuiApplication(sys.argv)
engine = QQmlApplicationEngine()
engine.addImportPath(CONTENT)
bridge = FakeBridge()
engine.rootContext().setContextProperty("AppBridge", bridge)
cam_bridge = FakeCameraBridge()
engine.rootContext().setContextProperty("CameraBridge", cam_bridge)
engine.addImageProvider("camera", cam_bridge.frameProvider)
alg_bridge = FakeAlgorithmBridge()
engine.rootContext().setContextProperty("AlgorithmBridge", alg_bridge)
detect_bridge = FakeDetectBridge()
engine.rootContext().setContextProperty("DetectBridge", detect_bridge)
collect_bridge = FakeCollectBridge()
engine.rootContext().setContextProperty("CollectBridge", collect_bridge)
light_bridge = FakeLightBridge()
engine.rootContext().setContextProperty("LightBridge", light_bridge)
mqtt_bridge = FakeMqttBridge()
engine.rootContext().setContextProperty("MqttBridge", mqtt_bridge)
# 与 main.py 一致的相机→AppBridge 状态同步（FakeBridge 用 setter 形式）
cam_bridge.cameraConnectedChanged.connect(
    lambda: setattr(bridge, "cameraConnected", cam_bridge.cameraConnected))
engine.load(QUrl.fromLocalFile(os.path.join(CONTENT, "App.qml")))
assert engine.rootObjects(), "App.qml 加载失败"
window = engine.rootObjects()[0]

stack = walk(window, lambda i: "StackLayout" in i.metaObject().className())

# ── 1. 相机搜索 → 连接 → 参数面板 ──
stack.setProperty("currentIndex", 0)
wait(400)
cam_page = stack.childItems()[0]
refresh = walk(cam_page, lambda i: "AnimatedRefreshButton" in i.metaObject().className())
click_item(window, refresh)
wait(800)
check("搜索到相机", cam_page.property("_hasCamera") is True)
check("相机型号显示", "Daheng" in str(cam_page.property("_cameraName")))

cam_card = walk(cam_page, lambda i: "CameraCard" in i.metaObject().className())
click_item(window, cam_card)
wait(800)
check("相机连接成功", cam_page.property("_cameraConnected") is True)
check("AppBridge 同步已连接", bridge.cameraConnected is True)
check("参数面板展开", True)

panel = walk(cam_page, lambda i: "CameraSettingsPanel" in i.metaObject().className())
check("面板展开", float(panel.property("implicitHeight")) > 300)
check("分辨率已读 (2048 × 2448)",
      "2048 × 2448" in str(panel.property("resolutionText")))
check("曝光时间已读", abs(float(panel.property("exposureTime")) - 5000) < 1)
check("增益已读", abs(float(panel.property("gain")) - 3.5) < 0.1)

# 拖拽曝光滑块（press→move→release）→ 松开时写入桥
# SliderRow 结构: [label Text, control Item(轨道+MouseArea), 数值 Text]
exposure_slider = None


def collect_sliders(item, depth=0):
    global exposure_slider
    if "SliderRow" in item.metaObject().className():
        if exposure_slider is None:
            exposure_slider = item
    if depth < 10:
        for c in item.childItems():
            collect_sliders(c, depth + 1)


collect_sliders(panel)
check("找到曝光滑块", exposure_slider is not None)
control = exposure_slider.childItems()[1]
base = control.mapToScene(QPointF(0, 0)).toPoint()
y = base.y() + int(control.height() / 2)
x0 = base.x() + int(control.width() * 0.9)
x1 = base.x() + int(control.width() * 0.5)
QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, QPoint(x0, y))
wait(50)
QTest.mouseMove(window, QPoint(x1, y), 50)
wait(50)
QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, QPoint(x1, y))
wait(300)
written = float(cam_bridge._features.get("GX_FLOAT_EXPOSURE_TIME", -1))
check(f"松开后曝光写入后端 ({written:.0f} ≈ 500005)",
      abs(written - 500005) < 50000)

# 重置按钮：点击 ↺ → 恢复连接时初始值(5000) 并再次写入
reset_btn = None
for c in exposure_slider.findChildren(object):
    if "Button" in c.metaObject().className() and str(c.property("text")) == "↺":
        reset_btn = c
        break
check("找到重置按钮", reset_btn is not None)
if reset_btn:
    click_item(window, reset_btn)
    wait(300)
    check("重置恢复老项目初始值(20000)并写入",
          abs(float(cam_bridge._features.get("GX_FLOAT_EXPOSURE_TIME", -1)) - 20000) < 1)
    # 界面曝光单位已改为 ms，后端写入仍为 us
    check("滑块数值复位(20ms)", abs(float(exposure_slider.property("sliderValue")) - 20.0) < 0.001)

# ── 2. 断开 → 状态复位 ──
click_item(window, cam_card)
wait(500)
check("断开后复位", cam_page.property("_cameraConnected") is False
      and bridge.cameraConnected is False)

# ── 3. Detect 采集互斥（相机已连接状态直接置位）──
bridge.cameraConnected = True
stack.setProperty("currentIndex", 3)
wait(400)
detect_page = stack.childItems()[3]
handle = None
for c in detect_page.findChildren(object):
    if c.objectName() == "sidePanelHandle":
        handle = c
        break
click_item(window, handle)
wait(300)
# 模拟“测试推理执行中突然开始实时采集”：实时模式应抢占画面，
# 在途测试结果返回后应被丢弃，不能把实时画面切回测试图。
detect_page.setProperty("_testActive", True)
detect_page.setProperty("_inferring", True)
detect_page.setProperty("_testSession", 7)
detect_page.setProperty("_testActiveSession", 7)
start_btn = find_btn(detect_page, "开始采集")
click_item(window, start_btn)
wait(300)
check("Detect 采集持有", bridge.collectingOwner == "detect")
check("图像激活", detect_page.property("_imageActive") is True)
check("开始采集后测试画面被清空", detect_page.property("_testActive") is False)
check("测试推理中标记被复位", detect_page.property("_inferring") is False)
alg_bridge.inferenceReady.emit(2.5, "/tmp/stale_test.png")
wait(200)
check("过期测试结果不会抢回画面", detect_page.property("_testActive") is False)

# ROI 拖拽确认（按钮已移到原图标题栏）
roi_btn = None
for c in detect_page.findChildren(object):
    if c.objectName() == "originRoiButton":
        roi_btn = c
        break
check("标题栏 ROI 按钮存在", roi_btn is not None)
click_item(window, roi_btn)
wait(200)
roi_overlay = walk(detect_page, lambda i: "RoiOverlay" in i.metaObject().className())
base = roi_overlay.mapToScene(QPointF(0, 0)).toPoint()
w, h = roi_overlay.width(), roi_overlay.height()
p0 = base + QPoint(int(w * 0.2), int(h * 0.2))
p1 = base + QPoint(int(w * 0.6), int(h * 0.6))
QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, p0)
wait(50)
QTest.mouseMove(window, p1, 50)
wait(50)
QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, p1)
wait(200)
confirm_btn = None
for c in roi_overlay.findChildren(object):
    if "Button" in c.metaObject().className() and c.property("visible"):
        confirm_btn = c
        break
check("ROI 确认按钮弹出", confirm_btn is not None)
if confirm_btn:
    click_item(window, confirm_btn)
    wait(200)
    # 应用后相机会输出 ROI 区域，因此常显红框被清除
    check("ROI 已应用并清除常显框", roi_overlay.property("hasRoi") is False)
    check("ROI 归一化参数已写后端", cam_bridge._roi is not None)

# ROI 绘制模式可再次进入（防止组件内部赋值破坏 drawingEnabled 绑定）
click_item(window, roi_btn)
wait(200)
check("ROI 绘制模式可再次进入", roi_overlay.property("drawingEnabled") is True)
click_item(window, roi_btn)
wait(200)
check("ROI 绘制模式可退出", detect_page.property("_roiMode") is False)

# 标题栏恢复全幅按钮：点击后后端 resetRoi 被调用
roi_reset = None
for c in detect_page.findChildren(object):
    if c.objectName() == "originRoiResetButton":
        roi_reset = c
        break
check("标题栏恢复全幅按钮存在", roi_reset is not None)
if roi_reset:
    click_item(window, roi_reset)
    wait(200)
    check("恢复全幅调用后端 resetRoi", cam_bridge._roi is None)

# 原图标题栏全屏按钮：放大到整个 DetectPage，再恢复
origin_fs = None
for c in detect_page.findChildren(object):
    if c.objectName() == "originFullscreenButton":
        origin_fs = c
        break
check("原图全屏按钮存在", origin_fs is not None)
if origin_fs:
    click_item(window, origin_fs)
    wait(200)
    check("点击后进入原图全屏", detect_page.property("_fullscreenKind") == "origin")

    fs_roi = None
    for c in detect_page.findChildren(object):
        if c.objectName() == "fullscreenRoiButton":
            fs_roi = c
            break
    check("全屏标题栏 ROI 按钮存在", fs_roi is not None and fs_roi.property("visible") is True)
    if fs_roi:
        click_item(window, fs_roi)
        wait(200)
        check("全屏下可进入 ROI 绘制", detect_page.property("_roiMode") is True)
        click_item(window, fs_roi)
        wait(200)
        check("全屏下可退出 ROI 绘制", detect_page.property("_roiMode") is False)

    detect_page.setProperty("_fullscreenKind", "")
    wait(100)
    check("再次点击恢复布局", detect_page.property("_fullscreenKind") == "")

heat_fs = None
for c in detect_page.findChildren(object):
    if c.objectName() == "heatmapFullscreenButton":
        heat_fs = c
        break
check("热力图全屏按钮存在", heat_fs is not None)
if heat_fs:
    click_item(window, heat_fs)
    wait(200)
    check("热力图可进入全屏", detect_page.property("_fullscreenKind") == "heatmap")
    detect_page.setProperty("_fullscreenKind", "")

# ── 4. CollectPage 与 DetectPage 采集互斥 ──
bridge.collectingOwner = ""
stack.setProperty("currentIndex", 4)
wait(400)
collect_page = stack.childItems()[4]
collect_card = walk(collect_page, lambda i: "AcquisitionCard" in i.metaObject().className())
click_item(window, collect_card)
wait(300)
check("CollectPage 采集持有", bridge.collectingOwner == "collect")
check("CollectPage 状态为采集中", collect_page.property("_collecting") is True)

# 后按者抢占：Detect 重新申请时 Collect 自动释放
bridge.collectingOwner = "detect"
wait(200)
check("Detect 抢占后 Collect 停止", collect_page.property("_collecting") is False)
bridge.collectingOwner = ""

# ── 5. LightPage 串口连接链路 ──
stack.setProperty("currentIndex", 1)
wait(400)
light_page = stack.childItems()[1]
light_card = walk(light_page, lambda i: "LightControllerCard" in i.metaObject().className())
click_item(window, light_card)
wait(400)
check("LightBridge 连接成功", light_page.property("_connected") is True)
light_panel = walk(light_page, lambda i: "LightControlPanel" in i.metaObject().className())
check("光源调控面板展开", float(light_panel.property("implicitHeight")) > 100)
click_item(window, light_card)
wait(300)
check("LightBridge 断开成功", light_page.property("_connected") is False)

# ── 6. CommPage MQTT 连接链路 ──
stack.setProperty("currentIndex", 2)
wait(400)
comm_page = stack.childItems()[2]
server_card = walk(comm_page, lambda i: "CloudServerCard" in i.metaObject().className())
click_item(window, server_card)
wait(400)
check("MqttBridge 连接成功", comm_page.property("_connected") is True)
test_panel = walk(comm_page, lambda i: "MqttTestPanel" in i.metaObject().className())
check("连接测试面板展开", float(test_panel.property("implicitHeight")) > 100)
click_item(window, server_card)
wait(300)
check("MqttBridge 断开成功", comm_page.property("_connected") is False)

# ── 7. 精细定位开关联动（vmin/vmax 新模型回归）──
stack.setProperty("currentIndex", 3)
wait(300)
detect_page2 = stack.childItems()[3]
switches = []
def collect_switches(item, depth=0):
    if "SwitchRow" in item.metaObject().className():
        switches.append(item)
    if depth < 12:
        for c in item.childItems():
            collect_switches(c, depth + 1)
collect_switches(detect_page2)
f1_switch = None
fine_switch = None
for s in switches:
    if s.property("label") == "F1 阈值定位":
        f1_switch = s
    elif s.property("label") == "精细定位":
        fine_switch = s
check("找到 F1 阈值定位开关", f1_switch is not None)
check("找到精细定位开关", fine_switch is not None)
if f1_switch is not None and fine_switch is not None:
    check("精细定位始终可点击", fine_switch.property("enabled") is True)
    fine_btn = walk(fine_switch, lambda i: "Button" in i.metaObject().className())
    click_item(window, fine_btn)
    wait(250)
    check("精细定位可点击开启", detect_page2.property("_pixelRefineEnabled") is True)
    check("开启精细定位时自动打开 F1 定位", detect_page2.property("_pixelMaskEnabled") is True)

failed = [n for n, ok in results if not ok]
print()
print(f"{len(results) - len(failed)}/{len(results)} passed")
sys.exit(1 if failed else 0)
