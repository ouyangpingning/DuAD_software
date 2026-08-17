"""相机联调冒烟：无相机环境验证 QML↔CameraBridge 链路（搜索空列表、连接报错、状态流转）。"""
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

PROJECT_ROOT = "/run/media/lxb/Soft/资料/刘祥宾/研究生论文/算法对应上位机/DuAD_Software"
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


# 复用 main.py 的注册逻辑（CameraBridge 真实后端）
sys.path.insert(0, os.path.join(PROJECT_ROOT, "backend"))
from Src.camera_bridge import CameraBridge

app = QGuiApplication(sys.argv)
engine = QQmlApplicationEngine()
engine.addImportPath(CONTENT)

bridge = FakeBridge()
engine.rootContext().setContextProperty("AppBridge", bridge)
camera_bridge = CameraBridge()   # 保持引用防 GC
engine.rootContext().setContextProperty("CameraBridge", camera_bridge)
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
camera_bridge.cameraConnectedChanged.connect(
    lambda: bridge._setCameraConnected(camera_bridge.cameraConnected))

engine.load(QUrl.fromLocalFile(os.path.join(CONTENT, "App.qml")))
assert engine.rootObjects(), "App.qml 加载失败"
window = engine.rootObjects()[0]

stack = walk(window, lambda i: "StackLayout" in i.metaObject().className())
stack.setProperty("currentIndex", 0)
wait(400)
cam_page = stack.childItems()[0]

# ── 1. 搜索（无相机 → 空列表）──
refresh = walk(cam_page, lambda i: "AnimatedRefreshButton" in i.metaObject().className())
click_item(window, refresh)
wait(800)
check("搜索后无相机 (hasCamera=False)", cam_page.property("_hasCamera") is False)
check("AppBridge 相机未连接", bridge.cameraConnected is False)

# ── 2. 尝试连接（无设备 → cameraError，状态不卡死）──
cam_card = walk(cam_page, lambda i: "CameraCard" in i.metaObject().className())
cam_card.setProperty("hasCamera", True)   # 模拟有相机可点
click_item(window, cam_card)
wait(800)
check("连接失败后 connecting 复位", cam_page.property("_connecting") is False)
check("未连接成功", cam_page.property("_cameraConnected") is False)

# ── 3. 参数面板（未连接时不展开）──
panel = walk(cam_page, lambda i: "CameraSettingsPanel" in i.metaObject().className())
check("参数面板未展开", float(panel.property("implicitHeight")) == 0.0)

# ── 4. 后端桥直连验证（QML 可调用性已在上面体现，Python 侧再验证）──
check("CameraBridge 已注册", camera_bridge is not None)
check("cameraConnected 初始 False", camera_bridge.cameraConnected is False)

failed = [n for n, ok in results if not ok]
print()
print(f"{len(results) - len(failed)}/{len(results)} passed")
sys.exit(1 if failed else 0)
