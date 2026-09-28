#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""StagePage 冒烟测试（offscreen）—— 页面逻辑 + 导航三处同步。

做两件事：

  ① **渲染真实的 StagePage.qml**，用真的 StageBridge 连进程内的假板子，
     断言门闸提示、点动、急停、绝对定位、预设记录、离开页面释放相机
     这一整条链路（页面 → bridge → 协议 → 板子）。

  ② **静态校验 MainWindow.ui.qml 的"三处同步"**：导航按钮顺序、
     ButtonGroup.buttons 数组、StackLayout 子项顺序必须一致 ——
     这是本项目最容易漏的一处（`currentIndex` 用 indexOf 算，错了就整组页面错位，
     而且不会报任何错，只是点 A 显示 B）。

运行（不需要真板子、不需要相机）：
    source DuAD_SoftwareContent/pyqml/bin/activate
    QT_QPA_PLATFORM=offscreen python3 tests/test_stage_page.py

⚠ 交互用**发信号**（`btn.clicked.emit()`）而不是模拟鼠标坐标：
   offscreen 下按坐标点需要窗口 exposed + mapToScene 换算，脆且难查。
   这样测的是"回调接线对不对"，代价是不覆盖命中测试 —— 对冒烟来说够用。
"""

import os
import re
import sys
import tempfile
import time
from pathlib import Path

# 与真程序（main.py）的 URL 解析语义对齐：不设它的话，`../images/Z轴平台.svg`
# 这类"相对 StagePage.qml 写的"路径会按组件自身目录解析 → 图标静默消失
# （test_zstage_page.py / render_page.py 同款，见 AGENTS 第 22.1 条第 3 点）。
os.environ.setdefault("QML_COMPAT_RESOLVE_URLS_ON_ASSIGNMENT", "1")

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "DuAD_SoftwareContent"
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import (QObject, Property, QPoint, QPointF, QSettings,  # noqa: E402
                            Qt, QUrl, Signal, Slot)
from PySide6.QtTest import QTest                                     # noqa: E402
from PySide6.QtGui import QGuiApplication                            # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine                      # noqa: E402
# ⚠ import QQuickItem 不只是为了类型注释：**不 import 它，PySide 会把 QML 里的
#   Item 当成普通 QObject 返回**，于是 `mapToItem(page, ...)` 报
#   "Unknown argument type #1 used in call of meta function"（踩过一次）。
from PySide6.QtQuick import QQuickItem                               # noqa: E402

from Src.stage_bridge import StageBridge                             # noqa: E402
from test_stage_bridge import TOKEN, FakeBoardServer                 # noqa: E402

DEG_PER_MM = 11.25


# ============================================================
# 被页面引用的两个桥的最小替身
# ============================================================
class FakeAppBridge(QObject):
    """只需要 collectingOwner（预览的采集仲裁）+ 几个 App.qml 用到的槽。"""

    collectingOwnerChanged = Signal()
    helpRequested = Signal()

    def __init__(self):
        super().__init__()
        self._owner = ""
        self._cam = False
        # 模拟 main.py 的仲裁"拒绝/回退"：采集起不来时它会把 owner 退回 ""。
        # 用它验证页面有没有把失败原因说出来（而不是静默弹回）。
        self.reject_owners = False

    def _get_owner(self):
        return self._owner

    def _set_owner(self, v):
        if self.reject_owners and v:
            return
        if self._owner != v:
            self._owner = v
            self.collectingOwnerChanged.emit()

    collectingOwner = Property(str, _get_owner, _set_owner,
                               notify=collectingOwnerChanged)

    # ⚠ 真 AppBridge 有 cameraConnected（跨页状态中枢），替身必须也有 ——
    #   漏了它 StagePage 里 `enabled: AppBridge.cameraConnected` 会得到
    #   undefined 并报 "Unable to assign [undefined] to bool"。
    #   （这个警告就是这么被发现的：替身不完整会伪装成页面 bug。）
    cameraConnectedChanged = Signal()

    def _get_cam(self):
        return self._cam

    def _set_cam(self, v):
        # ⚠ 必须有 setter：只读 Property 从 Python 侧赋值会抛异常，
        #   而测试里我们要模拟"相机连上/断开"来驱动开关的 enabled。
        if self._cam != bool(v):
            self._cam = bool(v)
            self.cameraConnectedChanged.emit()

    cameraConnected = Property(bool, _get_cam, _set_cam,
                               notify=cameraConnectedChanged)

    @Property(str, constant=True)
    def homeDir(self):
        return str(Path.home())

    @Slot(str, result=bool)
    def isDir(self, p):
        return Path(p).is_dir()

    @Slot(result=bool)
    def shouldShowHelp(self):
        return False

    @Slot()
    def markHelpShown(self):
        pass


class FakeCameraBridge(QObject):
    cameraConnectedChanged = Signal()
    frameIndexChanged = Signal()
    imageWidthChanged = Signal()
    imageHeightChanged = Signal()

    def __init__(self):
        super().__init__()
        self._connected = False
        self._index = 0
        # ⚠ 这两个是页面用来算预览比例的（`_camRatio`）：整宽预览条很宽、
        #   相机是 2448×2048 近方形，不约束比例画面就被拉扁。
        #   替身缺了它们，页面读到 undefined → 悄悄退回默认比例（不报错），
        #   于是"比例没接上"这类问题在替身测试里永远查不出来。
        self._w = 0
        self._h = 0

    def _get_connected(self):
        return self._connected

    cameraConnected = Property(bool, _get_connected, notify=cameraConnectedChanged)

    def _get_index(self):
        return self._index

    frameIndex = Property(int, _get_index, notify=frameIndexChanged)
    imageWidth = Property(int, lambda self: self._w, notify=imageWidthChanged)
    imageHeight = Property(int, lambda self: self._h, notify=imageHeightChanged)


class FakeZStageBridge(QObject):
    """Z 轴升降平台桥的替身（`backend/Src/zstage_bridge.py`）。

    ⚠ 为什么必须补它：`StagePage` 现在引用了 `ZStageBridge`。替身缺属性时页面会报
      `Unable to assign [undefined] to ...` / `Cannot read property 'x' of null`，
      **看着像页面写错了属性**，其实是替身不完整（AGENTS.md 的"通用坑"最后一条）。
      所以下面把页面引用到的**每一个**属性/槽都补齐，一个不漏。

    它是**纯替身**：不连任何板子，属性值固定（未连接状态）。
    真正走协议的那条链路在 `tests/test_zstage_page.py` 里用**真的** ZStageBridge
    + 进程内假板子验（替身的诚实度决定了测试能发现什么，见 AGENTS.md 第 11 条）。
    """

    changed = Signal()

    def __init__(self):
        super().__init__()
        self.calls = []          # 记录槽调用，方便断言"点了没反应"那类问题
        self._connected = False
        self._proto = []
        # ⚠ 公用协议框（ProtoHub）在构造时要读每个桥的 `proto_log` —— 替身也得有，
        #   否则 `ProtoHub({...})` 直接 AttributeError（看着像工具/页面的问题，
        #   其实是替身不全：AGENTS.md 通用坑最后一条）。
        from Src.proto_log import ProtoLog

        self.proto_log = ProtoLog("Z", parent=self)

    # ── 连接 ──
    connected = Property(bool, lambda self: self._connected, notify=changed)
    connecting = Property(bool, lambda self: False, notify=changed)
    # 页面/公用协议框会读它（决定"驱动器帧镜像"开关能不能点）——
    # 真桥是 True（Z 固件有 trace），替身照抄，否则 QML 报 Unable to assign [undefined] to bool
    supportsTrace = Property(bool, lambda self: True, notify=changed)
    lastError = Property(str, lambda self: "", notify=changed)
    canMove = Property(bool, lambda self: False, notify=changed)
    # ⚠ 页面引用的每个属性都得在替身里出现：漏一个 → QML 报
    #   `Unable to assign [undefined] to bool`，看着像页面写错了，其实是替身没补全
    #   （AGENTS.md「冒烟测试/通用坑」里那条）。`canJog` 是"点动不受基准闸限制"那位。
    canJog = Property(bool, lambda self: False, notify=changed)
    datumHint = Property(str, lambda self: "未连接 —— 点上面的 Z 轴平台卡片连接",
                         notify=changed)

    # ── 设置类 ──
    host = Property(str, lambda self: "", notify=changed)
    port = Property(int, lambda self: 3333, notify=changed)
    token = Property(str, lambda self: "", notify=changed)
    rpm = Property(int, lambda self: 300, notify=changed)
    acc = Property(int, lambda self: 100, notify=changed)
    uiMaxRpm = Property(int, lambda self: 1200, notify=changed)
    uiMaxAcc = Property(int, lambda self: 200, notify=changed)
    uiMinAcc = Property(int, lambda self: 1, notify=changed)
    step = Property(float, lambda self: 1.0, notify=changed)
    stepChoices = Property("QVariantList", lambda self: [0.1, 1.0, 10.0, 50.0],
                           notify=changed)
    limitLo = Property(float, lambda self: 0.0, notify=changed)
    limitHi = Property(float, lambda self: 250.0, notify=changed)

    # ── 遥测 ──
    z = Property(float, lambda self: 0.0, notify=changed)
    skew = Property(float, lambda self: 0.0, notify=changed)
    jointA = Property(int, lambda self: 0, notify=changed)
    jointB = Property(int, lambda self: 0, notify=changed)
    voltage = Property(float, lambda self: 0.0, notify=changed)
    enabled = Property(bool, lambda self: False, notify=changed)
    datum = Property(bool, lambda self: False, notify=changed)
    limitsSet = Property(bool, lambda self: False, notify=changed)
    firmwareLo = Property(float, lambda self: 0.0, notify=changed)
    firmwareHi = Property(float, lambda self: 250.0, notify=changed)
    moving = Property(bool, lambda self: False, notify=changed)
    homing = Property(int, lambda self: 0, notify=changed)
    autohome = Property(bool, lambda self: False, notify=changed)
    fault = Property(str, lambda self: "none", notify=changed)
    faultText = Property(str, lambda self: "", notify=changed)
    signText = Property(str, lambda self: "", notify=changed)
    rssi = Property(int, lambda self: 0, notify=changed)

    # ── 协议框 ──
    # ⚠ 协议框是**唯一**要求"塞几行进去"的替身属性：页面把它当 model 用
    protoLines = Property("QVariantList", lambda self: list(self._proto), notify=changed)
    protoPaused = Property(bool, lambda self: False, notify=changed)
    traceOn = Property(bool, lambda self: False, notify=changed)

    def _rec(self, name, *args):
        self.calls.append((name,) + args)

    # ── 槽（签名与真桥一致；返回 bool 的照原样返回）──
    @Slot(str, int, str, result=bool)
    def connectDevice(self, host, port, token):
        self._rec("connectDevice", host, port, token)
        return True

    @Slot()
    def disconnectDevice(self):
        self._rec("disconnectDevice")

    @Slot(float, result=bool)
    def jogUp(self, mm):
        self._rec("jogUp", mm)
        return True

    @Slot(float, result=bool)
    def jogDown(self, mm):
        self._rec("jogDown", mm)
        return True

    @Slot(float, result=bool)
    def moveTo(self, mm):
        self._rec("moveTo", mm)
        return True

    @Slot(float)
    def setStep(self, mm):
        self._rec("setStep", mm)

    @Slot(result=bool)
    def setZero(self):
        self._rec("setZero")
        return True

    @Slot(result=bool)
    def homeNow(self):
        self._rec("homeNow")
        return True

    @Slot()
    def stopNow(self):
        self._rec("stopNow")

    @Slot(int, int, result=bool)
    def setSpeed(self, rpm, acc):
        self._rec("setSpeed", rpm, acc)
        return True

    @Slot(float, float, result=bool)
    def setSoftLimits(self, lo, hi):
        self._rec("setSoftLimits", lo, hi)
        return True

    @Slot(bool, result=bool)
    def setAutohome(self, on):
        self._rec("setAutohome", on)
        return True

    @Slot(bool, result=bool)
    def setTrace(self, on):
        self._rec("setTrace", on)
        return True

    @Slot(bool)
    def setProtoPaused(self, paused):
        self._rec("setProtoPaused", paused)

    @Slot()
    def clearProto(self):
        self._rec("clearProto")

    @Slot(str, result=bool)
    def sendCommand(self, text):
        self._rec("sendCommand", text)
        return True


# ============================================================
# 测试驱动
# ============================================================
class Runner:
    def __init__(self, app):
        self.app = app
        self.fails = []

    def pump(self, sec):
        end = time.time() + sec
        while time.time() < end:
            self.app.processEvents()
            time.sleep(0.005)

    def wait_for(self, pred, sec=5.0):
        end = time.time() + sec
        while time.time() < end:
            self.app.processEvents()
            if pred():
                return True
            time.sleep(0.005)
        return False

    def check(self, name, cond, extra=""):
        print(("  [ok] " if cond else "  [FAIL] ") + name + (("   " + extra) if extra else ""))
        if not cond:
            self.fails.append(name)


HARNESS = b"""
import QtQuick
import QtQuick.Controls
import "pages"

Window {
    id: win
    width: 1400
    height: 900
    visible: true

    StagePage {
        id: stagePage
        objectName: "stagePage"
        anchors.fill: parent
    }
}
"""


def check_nav_sync():
    """静态校验 MainWindow.ui.qml 的三处顺序一致。"""
    src = (CONTENT / "MainWindow.ui.qml").read_text(encoding="utf-8")
    nav = re.findall(r'NavButton \{\s*\n\s*id:\s*(\w+)', src)
    group = re.search(r'buttons:\s*\[([^\]]+)\]', src)
    stack = re.findall(r'(\w+Page) \{\}\s*//\s*(\d+)', src)

    print("=== 导航三处同步（静态校验）===")
    print(f"      NavButton 顺序 : {nav}")
    print(f"      ButtonGroup    : {[b.strip() for b in group.group(1).split(',')] if group else None}")
    print(f"      StackLayout    : {[s[0] for s in stack]}")

    if not (nav and group and stack):
        return ["导航三处解析失败"]

    fails = []
    group_list = [b.strip() for b in group.group(1).split(",")]
    if nav != group_list:
        fails.append(f"NavButton 与 ButtonGroup 不一致: {nav} vs {group_list}")

    # 每个 NavButton 对应的页面必须按同样顺序出现在 StackLayout 里
    pages = [s[0] for s in stack]
    # ⚠ 不能一律去掉 "btn" 前缀：设置页那个按钮的 id 就叫 `settings`（没有 btn）。
    #   统一成"去掉可选 btn 前缀 → 首字母大写 → 补 Page"。
    def _page_of(nav_id: str) -> str:
        name = nav_id[3:] if nav_id.startswith("btn") else nav_id
        return name[0].upper() + name[1:] + "Page"
    expected = [_page_of(n) for n in nav]
    if pages != expected:
        fails.append(f"StackLayout 与 NavButton 不一致: {pages} vs {expected}")

    # 序号注释也要连续，否则以后插页面容易插错位置
    idx = [int(s[1]) for s in stack]
    if idx != list(range(len(idx))):
        fails.append(f"StackLayout 序号注释不连续: {idx}")

    # StagePage 必须在
    if "StagePage" not in pages:
        fails.append("StackLayout 里没有 StagePage")
    return fails


def check_collecting_owners():
    """静态校验"页面 ↔ main.py"的采集所有权契约。

    这条校验是有来历的：加平台页实时预览时，我在 main.py 的仲裁分支里加了
    `owner == "stage"`，**却漏了 setter 里的白名单** `COLLECTING_OWNERS`。
    于是 `AppBridge.collectingOwner = "stage"` 被静默丢弃 ——
    现象是"预览开关点了没反应"，而且没有任何日志。

    页面测试用的假 AppBridge 照单全收，**测不出这个 bug**（假桥没有白名单）。
    所以只能在文本层面校验这个契约：页面写到的每个 owner，
    既要在 COLLECTING_OWNERS 里（否则被丢弃），也要有仲裁分支（否则没人执行）。
    """
    main_py = (CONTENT / "main.py").read_text(encoding="utf-8")
    m = re.search(r"COLLECTING_OWNERS\s*=\s*\(([^)]*)\)", main_py)
    print("=== 采集所有权契约（静态校验）===")
    if not m:
        return ["main.py 里找不到 COLLECTING_OWNERS"]
    declared = tuple(x.strip().strip('"\'') for x in m.group(1).split(",") if x.strip())
    print(f"      COLLECTING_OWNERS = {declared}")

    # 页面里的字面量：赋值与比较都算
    used = set()
    for qml in sorted((CONTENT / "pages").glob("*.qml")):
        txt = qml.read_text(encoding="utf-8")
        for lit in re.findall(r'collectingOwner\s*=\s*"([^"]*)"', txt):
            if lit:
                used.add(lit)
        for lit in re.findall(r'collectingOwner\s*===\s*"([^"]*)"', txt):
            if lit:
                used.add(lit)
    print(f"      页面用到的 owner = {sorted(used)}")

    fails = []
    for owner in sorted(used):
        if owner not in declared:
            fails.append(f"页面写了 collectingOwner={owner!r}，但它不在 COLLECTING_OWNERS={declared} 里"
                         " —— 会被 setter 静默丢弃（预览开关「点了没反应」就是这么来的）")
        if f'== "{owner}"' not in main_py:
            fails.append(f"owner {owner!r} 没有任何仲裁分支（main.py 里找不到 == \"{owner}\"）"
                         f" —— 没人会执行 startGather")
    # 反向：声明了但没人用，通常是漏接页面
    for owner in declared:
        if owner not in used:
            fails.append(f"COLLECTING_OWNERS 声明了 {owner!r}，但没有任何页面使用它")
    return fails


def main():
    app = QGuiApplication(sys.argv)
    server = FakeBoardServer()
    r = Runner(app)

    print(f"假板子监听 127.0.0.1:{server.port}\n")

    # ── 静态检查不依赖渲染，先做 ──
    nav_fails = check_nav_sync()
    for f in nav_fails:
        r.check(f, False)
    if not nav_fails:
        r.check("导航三处顺序一致", True)

    owner_fails = check_collecting_owners()
    for f in owner_fails:
        r.check(f, False)
    if not owner_fails:
        r.check("页面与 main.py 的采集所有权契约一致", True)
    print()

    # ── 渲染 StagePage ──
    # ⚠ 隔离 QSettings：测试绝不能写用户的真实配置（踩过一次，见 test_stage_bridge.py）
    sfile = Path(tempfile.mkdtemp(prefix="duad_stage_page_test_")) / "s.ini"
    stage = StageBridge(settings=QSettings(str(sfile), QSettings.Format.IniFormat))
    app_bridge = FakeAppBridge()
    camera_bridge = FakeCameraBridge()
    cam = camera_bridge          # 12c 里改分辨率用

    engine = QQmlApplicationEngine()
    engine.addImportPath(str(CONTENT))
    ctx = engine.rootContext()
    ctx.setContextProperty("StageBridge", stage)
    ctx.setContextProperty("AppBridge", app_bridge)
    ctx.setContextProperty("CameraBridge", camera_bridge)
    # ⚠ StagePage 现在还引用 ZStageBridge（Z 轴升降平台那一区）。
    #   替身必须保持 Python 侧引用（GC 掉 → QML 侧 null → 那一区静默失效）。
    z_fake = FakeZStageBridge()
    ctx.setContextProperty("ZStageBridge", z_fake)
    # 公用协议框（2026-09-28）：页面引用 ProtoHub，不注册页面直接加载失败。
    # 用**真的** ProtoHub + 真的 StageBridge + 假 Z 桥：跨桥汇总这段逻辑值得走真代码。
    from Src.proto_hub import ProtoHub

    proto_hub = ProtoHub({"xy": stage, "z": z_fake})   # ⚠ 保持引用，见上
    ctx.setContextProperty("ProtoHub", proto_hub)

    warnings = []
    engine.warnings.connect(lambda ws: warnings.extend(w.toString() for w in ws))

    engine.loadData(HARNESS, QUrl.fromLocalFile(str(CONTENT / "_smoke_stage.qml")))
    r.pump(0.5)

    print("=== 1) 页面加载 ===")
    if not engine.rootObjects():
        print("  [FAIL] StagePage 加载失败")
        for w in warnings:
            print("      " + w)
        server.close()
        return 1
    win = engine.rootObjects()[0]
    page = win.findChild(QObject, "stagePage")
    r.check("StagePage 实例化成功", page is not None)
    # 冒烟环境不注册 image provider（真程序里由 main.py 的
    # engine.addImageProvider("camera", ...) 提供），所以这条是预期噪音，滤掉。
    # 其余任何绑定/类型错误都算失败 —— 它们几乎总是"页面写了不存在的属性"。
    real_errors = [w for w in warnings
                   if "Invalid image provider" not in w
                   and any(k in w for k in ("is not defined", "TypeError", "ReferenceError",
                                            "Unable to assign", "Cannot assign",
                                            "is not a type", "SyntaxError"))]
    r.check("无 QML 绑定/类型错误", not real_errors, "; ".join(real_errors[:3]))
    if page is None:
        server.close()
        return 1

    def find(name):
        return page.findChild(QObject, name)

    print("=== 2) 未连接时的门闸提示 ===")
    r.pump(0.2)
    r.check("未连接", not stage.connected)
    r.check("提示未连接", "未连接" in page.property("_gateHint"), page.property("_gateHint"))
    r.check("不能移动", page.property("_canMove") is False)

    print("=== 3) 连接假板子 ===")
    setup_host = find("hostField")
    setup_token = find("tokenField")
    r.check("设置面板字段可访问", setup_host is not None and setup_token is not None)
    # 「退出时回到零点」开关（2026-09-13）：默认必须是**开**的。
    # ⚠ 这条断言专门抓"绑定没接上"那类静默错误 —— 开关画出来是关的、逻辑却是开的，
    #   用户就会以为这个功能关着（本项目"回话与事实不符"那类坑的同一个形状）。
    park = find("parkOnExit")
    r.check("有关机行为开关", park is not None)
    if park is not None:
        r.check("退出回零点默认开", park.property("on") is True,
                f"on={park.property('on')}")
    setup_host.setProperty("text", "127.0.0.1")
    setup_token.setProperty("text", TOKEN)
    ok = stage.connectDevice("127.0.0.1", server.port, TOKEN)
    r.check("连接发起成功", ok is True)
    r.check("已连接", r.wait_for(lambda: stage.connected), f"connected={stage.connected}")
    r.check("工作区已自动下发", r.wait_for(lambda: stage.travelSet),
            f"travelSet={stage.travelSet}")

    print("=== 4) 无基准 → 提示基准，且方向键不可用 ===")
    r.check("无基准", not stage.datum)
    r.check("提示缺少基准", "基准" in page.property("_gateHint"), page.property("_gateHint"))
    r.check("canMove 仍为 False", page.property("_canMove") is False)
    jog_pad = find("jogPad")
    if jog_pad is not None:
        r.check("方向键已禁用", jog_pad.property("interactive") is False)

    print("=== 4b) 一行状态（替代圆点状态条）如实反映闸门 ===")
    # 2026-09-28：X/Y 与 Z 顶上原来各是一排红绿圆点，现在是一行灰字 + 只在**异常**时
    # 出现的红项。这条断言钉"它真的跟状态走"，而不是"画出来了" ——
    # 绑定写错时，静态截图完全看不出来。
    # ⚠ 2026-09-28：状态**只在连接卡里显示**了（用户："状态只在卡片上显示"）——
    #   原来卡片下面还挂着一条独立的 StatusLine，同一个"未连接"出现两次。
    #   现在闸门是卡片的 `gates` 属性、由卡片的状态行画红项，所以这里改读卡片。
    def sl_items(card_name):
        it = find(card_name)
        if it is None:
            return []
        v = it.property("gates")
        v = v.toVariant() if hasattr(v, "toVariant") else v
        return [dict(d) for d in v]

    xy_sl = sl_items("xyControllerCard")
    r.check("连接卡带闸门状态（gates，取代了独立状态行）",
            find("xyControllerCard") is not None
            and find("xyStatusLine") is None,
            f"card={find('xyControllerCard') is not None} oldLine={find('xyStatusLine')}")
    r.check("无基准时状态行报出红项「无基准」",
            any((not d["ok"]) and ("基准" in str(d["text"])) for d in xy_sl), str(xy_sl))
    r.check("闸门里不重复电压/信号（它们在卡片副标题上）",
            not any(("V" in str(d["text"])) or ("dBm" in str(d["text"])) for d in xy_sl),
            str(xy_sl))

    print("=== 5) 步长选择（点 10mm）===")
    chip10 = find("stepChip_10")
    r.check("找到 10mm 步长按钮", chip10 is not None)
    # 步长是否真的生效，在第 7 步用"点动的实际幅度"验证 ——
    # 直接读 step 属性只能证明赋值成功，证明不了它被用上了。
    print("=== 6) 立基准（点「⌂ 设为原点」）===")
    zero_btn = find("zeroButton")
    r.check("找到设为原点按钮", zero_btn is not None)
    zero_btn.clicked.emit()
    r.check("基准已建立", r.wait_for(lambda: stage.datum), f"datum={stage.datum}")
    r.check("提示已清空", page.property("_gateHint") == "", page.property("_gateHint"))
    r.check("canMove 变 True", r.wait_for(lambda: page.property("_canMove") is True))
    if jog_pad is not None:
        r.check("方向键已启用", jog_pad.property("interactive") is True)

    r.check("立基准后闸门里的基准项转正（不再有红项）",
            all(d["ok"] for d in sl_items("xyControllerCard")),
            str(sl_items("xyControllerCard")))

    print("=== 7) 点动 10mm（步长按钮 → JogPad → bridge → 板子）===")
    server.board.commands.clear()
    if chip10 is not None:
        chip10.clicked.emit()
    r.pump(0.15)
    if jog_pad is not None:
        jog_pad.jog.emit(0.0, 10.0)          # +Y 10mm
    got = r.wait_for(lambda: any(c.startswith("move") for c in server.board.commands), 3)
    move_cmds = [c for c in server.board.commands if c.startswith("move")]
    r.check("板子收到 move", got, str(move_cmds[:2]))
    if move_cmds:
        parts = move_cmds[0].split()
        # 10mm × 11.25 = 112.5 度；X 不动
        r.check("换算正确 (112.5, 0) 度",
                abs(float(parts[1]) - 0.0) < 0.01 and abs(float(parts[2]) - 112.5) < 0.01,
                move_cmds[0])

    print("=== 8) 绝对定位（输入框 → 移动到该位置）===")
    r.wait_for(lambda: not stage.moving, 5)
    server.board.commands.clear()
    tx, ty = find("targetX"), find("targetY")
    r.check("目标输入框可访问", tx is not None and ty is not None)
    tx.setProperty("text", "50")
    ty.setProperty("text", "30")
    move_btn = find("moveToButton")
    r.check("找到移动按钮", move_btn is not None)
    move_btn.clicked.emit()
    got = r.wait_for(lambda: any(c.startswith("move") for c in server.board.commands), 3)
    move_cmds = [c for c in server.board.commands if c.startswith("move")]
    r.check("板子收到绝对 move", got, str(move_cmds[:2]))
    if move_cmds:
        parts = move_cmds[0].split()
        # 50mm × 11.25 = 562.5；30mm × 11.25 = 337.5
        r.check("绝对目标换算正确 (562.5, 337.5)",
                abs(float(parts[1]) - 562.5) < 0.01 and abs(float(parts[2]) - 337.5) < 0.01,
                move_cmds[0])
    r.check("目标叉已设置",
            abs(page.property("_targetX") - 50) < 0.01
            and abs(page.property("_targetY") - 30) < 0.01,
            f"({page.property('_targetX')}, {page.property('_targetY')})")

    print("=== 9) 急停 ===")
    r.wait_for(lambda: not stage.moving, 5)
    server.board.commands.clear()
    stop_btn = find("stopButton")
    r.check("找到停止按钮", stop_btn is not None)
    # 急停的关键性质：不在运动中也必须可点
    r.check("未运动时也处于可点状态", bool(stop_btn.property("enabled")) is True)
    stop_btn.clicked.emit()
    got = r.wait_for(lambda: "stop all" in server.board.commands, 3)
    r.check("板子收到 stop all", got, str(server.board.commands[-2:]))
    r.check("目标叉已清除", str(page.property("_targetX")) == "nan",
            str(page.property("_targetX")))

    print("=== 10) 记录预设位置 ===")
    r.wait_for(lambda: not stage.moving, 5)
    name_row = find("presetName")
    r.check("名称输入框可访问", name_row is not None)
    name_row.setProperty("text", "工位1")
    r.pump(0.15)
    # 记录按钮没有 objectName，按"文本匹配"找到它（页面里唯一带这个文案的按钮）
    save_btn = None
    for b in page.findChildren(QObject):
        if b.property("text") == "记录当前位置":
            save_btn = b
            break
    r.check("找到记录按钮", save_btn is not None)
    if save_btn is not None:
        before = len(stage.presets)
        save_btn.clicked.emit()
        r.check("预设已新增", r.wait_for(lambda: len(stage.presets) == before + 1),
                str(stage.presets))
        if stage.presets:
            r.check("预设名正确", stage.presets[-1]["name"] == "工位1",
                    str(stage.presets[-1]))
        for i in range(len(stage.presets)):
            stage.deletePreset(0)     # 清干净，别污染用户配置

    print("=== 11) 预览开关：点它要真的申请到 collectingOwner ===")
    sw = find("previewSwitch")
    r.check("找到预览开关", sw is not None)
    app_bridge.cameraConnected = False
    r.pump(0.15)
    r.check("相机未连接时开关不可用", bool(sw.property("enabled")) is False)

    app_bridge.cameraConnected = True
    r.pump(0.15)
    r.check("相机连上后开关可用", bool(sw.property("enabled")) is True)

    # 模拟 SwitchRow 内部按钮的行为：先翻转 on，再发 toggled
    sw.setProperty("on", True)
    sw.toggled.emit()
    r.pump(0.3)
    r.check("点了之后 owner 变成 stage", app_bridge.collectingOwner == "stage",
            repr(app_bridge.collectingOwner))
    r.check("页面识别到自己在预览", page.property("_previewOn") is True)
    r.check("没有误报失败", page.property("_previewFailed") is False)

    # 再点一次 → 释放
    sw.setProperty("on", False)
    sw.toggled.emit()
    r.pump(0.3)
    r.check("再点一次释放相机", app_bridge.collectingOwner == "",
            repr(app_bridge.collectingOwner))

    print("=== 11b) 被别的页面抢占时开关要自动回同步 ===")
    sw.setProperty("on", True)
    sw.toggled.emit()
    r.pump(0.3)
    app_bridge.collectingOwner = "detect"          # 模拟 DetectPage 抢走相机
    r.pump(0.3)
    r.check("开关自动拨回 off", bool(sw.property("on")) is False)
    r.check("页面不再认为自己持有", page.property("_previewOn") is False)
    app_bridge.collectingOwner = ""

    print("=== 11c) 预览起不来时要把原因写出来（不能静默弹回）===")
    app_bridge.reject_owners = True                # 模拟仲裁把 owner 退回 ""
    sw.setProperty("on", True)
    sw.toggled.emit()
    r.pump(1.3)                                    # 等过 900ms 的监视窗口
    r.check("owner 没生效", app_bridge.collectingOwner == "",
            repr(app_bridge.collectingOwner))
    r.check("页面报出了失败原因", page.property("_previewFailed") is True)
    app_bridge.reject_owners = False
    sw.setProperty("on", True)
    sw.toggled.emit()
    r.pump(0.4)
    r.check("恢复正常后失败标记清除", page.property("_previewFailed") is False)

    print("=== 11c2) 二轴诊断面板**已整块删除**（2026-09-28 用户要求）===")
    # 用户原话："你把二轴平台的诊断给删除吧" —— 板子状态摘要 / 指令日志 / 立即刷新
    # 全部去掉。这些是**排障细节**：板子 USB 控制台能看，本页「高级」里的公用协议框
    # 也能看（收发原文 + 驱动器帧镜像）。它们是 2026-09-13 那次"无限位回零大扫除"的
    # 遗留，现在连最后一块也走了。
    #
    # ⚠ 反向断言（AGENTS 第 18 条）：删掉的东西不许偷偷回来。这几个 objectName
    #   每一个都对应过真机上的困惑（"点了没反应"/"回零期间按停止没反应"）。
    for gone in ("boardStatus", "pollButton", "diagPanel",
                 "homeKind", "homeCorner", "homeButton", "homeAbortButton",
                 "homePassXm", "homePassXp", "homePassYm", "homePassYp",
                 "applyHomeCfgButton", "homeRpm", "homeMa"):
        r.check("已删除的控件不在界面上：" + gone, find(gone) is None)
    # 但"能看协议"这件事必须还在 —— 只是换了地方（公用协议框）
    r.check("排障能力没跟着一起删：公用协议框还在（高级里）",
            find("protoPanel") is not None)

    print("=== 11c4) 手动控制留在首屏、设置收进折叠节（+ 齿轮必须把人带过去）===")
    # 用户 2026-09-13 的要求是"平台设置必须在手动控制上面"，
    # 理由是"点齿轮后要往下滚半屏才看得到它展开了，现象上就是点了没反应"。
    # 2026-09-28 第二张手绘稿（三列版面）：设置搬进**各自窄列的折叠节**，
    # 物理位置保证不了"在上面"，改用**展开 + 滚过去**保证（下面那段齿轮断言钉它）。
    # 所以这里只钉两件事：手动控制卡在页面上；设置在默认形态下是收起的。
    # ⚠ 不要再拿 y 比大小：两者父级不同，收起时后者的坐标是无效值，比较毫无意义。
    jog = find("jogPanel")
    setup = find("setupPanel")
    r.check("找到手动控制卡", jog is not None)
    r.check("找到二轴平台设置卡（在左列的「平台设置」折叠节里）", setup is not None)
    r.check("默认形态下设置是收起的（首屏只留操作）",
            page.property("_xySetupOpen") is False,
            f"xySetupOpen={page.property('_xySetupOpen')}")

    # 齿轮的契约：点一下必须"展开 + 滚到眼前"，否则用户看到的就是"点了没反应"。
    # 三列版面里齿轮指向**本列**的「平台设置」折叠节 —— 断言 _xySetupOpen 被置真
    # **且整页滚到了它的折叠头**。⚠ 只断言 expanded==True 是假断言（那正是
    #   坏掉时也为真的那一半），contentY 真的变了才算"把人带过去"。
    page.setProperty("_xySetupOpen", False)
    r.pump(0.3)
    pscroll = find("pageScroll")
    pscroll.setProperty("contentY", 0)
    r.pump(0.2)
    before = float(pscroll.property("contentY"))
    for b in page.findChildren(QObject):
        # 齿轮是 AnimatedRefreshButton；两张卡各有一个 —— 只点 **XY 卡**上的那个
        # （parent 是卡片实例，XY 卡带 objectName "xyControllerCard"）。
        if ("AnimatedRefreshButton" in b.metaObject().className()
                and b.property("visible")
                and b.parent() is not None
                and str(b.parent().property("objectName") or "") == "xyControllerCard"):
            b.clicked.emit()
            break
    r.pump(0.8)
    after = float(pscroll.property("contentY"))
    r.check("点齿轮 → 本列「平台设置」展开 + 整页滚到它（不是「点了没反应」）",
            bool(page.property("_xySetupOpen")) and after > before + 1,
            f"open={page.property('_xySetupOpen')} contentY {before:.0f}→{after:.0f}")
    page.setProperty("_xySetupOpen", False)
    pscroll.setProperty("contentY", 0)
    r.pump(0.3)

    print("=== 11c5) 使能/失能按钮（原来状态看得见、操作没有）===")
    # 状态条一直显示"未使能"，而界面没有任何地方能改 —— 那是"点了没反应"的镜像。
    ebtn = find("enableButton")
    r.check("有「使能/失能」按钮", ebtn is not None)
    if ebtn is not None:
        r.check("连着时按钮可用", bool(ebtn.property("enabled")) is True,
                f"enabled={ebtn.property('enabled')}")
        # 文案必须是**动作**：显示"未使能"时按钮该写"使能…"，
        # 否则用户看着"未使能"再去点写着"未使能"的按钮，不知道会发生什么。
        txt = str(ebtn.property("text"))
        # 2026-09-28 精简：长文案缩成"图标 + 两个字"，解释挪进 ToolTip。
        # ⚠ 仍然钉"动作而不是状态"这条性质（写着"未使能"的按钮点下去会变成使能 = 迷惑）。
        r.check("文案是动作而不是状态，且已缩短（图标+短词）",
                ("失能" in txt or "使能" in txt) and len(txt) <= 6, txt)

    print("=== 11c7) 加减速滑块：低值区要占足够行程（对数刻度）===")
    # 用户 2026-09-13 要求"1-50 之间再细分"。加减速是 0~200 的档位，
    # 低端手感差异大、高端几乎无感 —— 线性轨道上 1~50 只有 25% 行程，拖不准。
    # ⚠ 这条要**量行程占比**，不能只断言 `logScale == true`：
    #   映射函数写错时开关照样是 true，而低端依旧挤在一起（假断言）。
    acc = find("accSliderRow")
    r.check("找到加减速滑块行", acc is not None)
    if acc is not None:
        r.check("已切到对数刻度", bool(acc.property("logScale")) is True,
                f"logScale={acc.property('logScale')}")
        share = float(acc.property("_lowShare"))
        r.check("1~50 占轨道 >=60%（线性只有 25%）", share >= 0.60,
                f"lowShare={share:.2f}")
        # ⚠ QML 的 JS 数组从 Python 读回来是 QJSValue（不能直接迭代）——
        #   要过一道 toVariant() 才是 Python list。这里踩过一次 TypeError。
        ticks = acc.property("snapTicks")
        ticks = ticks.toVariant() if hasattr(ticks, "toVariant") else ticks
        r.check("低端刻度够密（1~50 内至少 8 个）",
                sum(1 for t in ticks if float(t) <= 50) >= 8, str(ticks))
        r.check("刻度上限 = 固件上限 200",
                abs(float(ticks[-1]) - float(acc.property("to"))) < 0.5,
                f"last={ticks[-1]} to={acc.property('to')}")

    print("=== 11c6) 速度滑块的量程必须来自 bridge（不许在 QML 里写死）===")
    # 真踩过的坑：`UI_MAX_RPM` 在 bridge 里声明得好好的，**却没人引用**，
    # 而滑块里硬编码 `to: 1200` —— 同一个事实两份，改常量界面纹丝不动。
    # 所以这里断言"滑块上界 == StageBridge.uiMaxRpm"，谁再把数字写回 QML 就会红。
    rpm_slider = find("rpmSliderRow")
    r.check("找到转速滑块行", rpm_slider is not None)
    if rpm_slider is not None:
        # SliderRow 自己就暴露 from/to，直接读行对象即可（不用去挖里面的 Slider）
        upper = float(rpm_slider.property("to"))
        r.check("滑块上界取自 StageBridge.uiMaxRpm",
                abs(upper - float(stage.uiMaxRpm)) < 0.5,
                f"slider.to={upper} bridge.uiMaxRpm={stage.uiMaxRpm}")
        # 真正的硬约束：界面**不能**给出固件会拒收的值（拒帧 = 拖了没反应）。
        r.check("界面量程不超过固件上限", float(stage.uiMaxRpm) <= 6000,
                f"uiMaxRpm={stage.uiMaxRpm}")
        r.check("2026-09-13 用户要求提到 3000", float(stage.uiMaxRpm) >= 3000,
                f"uiMaxRpm={stage.uiMaxRpm}")


    # 位置读数现在是**上下两段**、住在左列的「状态」折叠节里（2026-09-28 三列版面）。
    # ⚠ 量之前必须先**打开折叠节**：收起时内容不参与布局，量出来全是 0（假红）。
    page.setProperty("_xyStatusOpen", True)
    r.pump(0.4)
    fold = find("xyStatusFold")
    body = find("xyStatusBody")
    readout = find("posReadout")
    r.check("找到位置读数组件与它的折叠节",
            readout is not None and fold is not None and body is not None)
    if readout is not None and body is not None:
        map_y = float(readout.property("_mapY"))
        nums_y = float(readout.property("_numsY"))
        map_h = float(readout.property("_mapH"))
        r.check("缩略图在上、读数在下（上下两段）", map_y < nums_y,
                "mapY=%.0f numsY=%.0f" % (map_y, nums_y))
        r.check("缩略图仍然够大（>=150px）", map_h >= 150, "mapH=%.0f" % map_h)
        # 内容必须整体落在折叠节里（读数比以前多了一行图例，容易顶出去）
        r.check("读数整体不超出折叠节高度（内容没被裁）",
                float(readout.property("height")) <= float(body.property("height")) + 1,
                "readoutH=%.0f bodyH=%.0f" % (readout.property("height"),
                                              body.property("height")))
        r.check("读数不超出折叠节宽度（窄列里最容易撑破）",
                float(readout.property("width")) <= float(body.property("width")) + 1,
                "readoutW=%.0f bodyW=%.0f" % (readout.property("width"),
                                              body.property("width")))
    page.setProperty("_xyStatusOpen", False)
    r.pump(0.3)

    print("=== 11d) 离开页面自动释放相机 ===")
    r.check("当前确实在预览", page.property("_previewOn") is True)
    page.setProperty("visible", False)
    r.pump(0.3)
    r.check("离开页面释放了相机", app_bridge.collectingOwner == "",
            repr(app_bridge.collectingOwner))
    r.check("回来时不再显示失败", page.property("_previewFailed") is False)
    page.setProperty("visible", True)
    r.pump(0.2)

    print("=== 11c9) 界面只留动态信息：删掉的静态说明不许长回来（2026-09-28 精简）===")
    # 与 test_zstage_page.py 的 14b 同源：用户要求"只留最常用的操作，细节去控制台"。
    # 删掉的说明都能在 docs/17、docs/18 与板子 help 里查到；留下的是当下状态与该怎么办。
    banned = ["失能后可以用手推台面调机械", "超出工作区的目标会被自动夹到边界",
              "转速越高，能带的负载越小", "加减速是对数刻度", "步进点动（0.1~10mm）",
              "把台面开回零点角再退出", "坐标以「本次上电后立的基准」为准",
              "日志里的运动指令是固件的角度域", "预览占用了相机采集", "参考：走 100mm 约"]
    texts = []
    for t in page.findChildren(QObject):
        if "Text" in t.metaObject().className():
            v = t.property("text")
            if v is not None:
                texts.append(str(v))
    hit = [b for b in banned if any(b in x for x in texts)]
    r.check("已删的说明段落没有留在界面上", not hit, str(hit))

    print("=== 12) 版面形态：整页一条滚动链 + 左右列折叠节（2026-09-28 三列手绘稿）===")
    # 上一版是"左右滑出抽屉"，第二张手绘稿改成**三列**：
    # 左右两条窄列（各：连接卡→状态折叠→读数→设置折叠→设置面板）+ 中央主列
    # （预览→两张手动控制卡并排→「高级」）。整页仍然只有一条滚动链。
    page.setProperty("_xyStatusOpen", False)
    page.setProperty("_zStatusOpen", False)
    page.setProperty("_xySetupOpen", False)
    page.setProperty("_zSetupOpen", False)
    page.setProperty("_advExpanded", False)
    win.setProperty("width", 1680)
    win.setProperty("height", 1040)
    r.pump(0.5)
    pscroll = find("pageScroll")
    xy_fold, z_fold = find("xyStatusFold"), find("zStatusFold")
    xy_body, z_body = find("xyStatusBody"), find("zStatusBody")
    adv = find("advHeader")
    r.check("找到整页滚动容器、两个状态折叠节与「高级」",
            pscroll is not None and xy_fold is not None and z_fold is not None
            and adv is not None)
    if pscroll is not None and xy_fold is not None and z_fold is not None:
        r.check("五个折叠节默认全部收起（手绘稿：「默认不打开」）",
                page.property("_xyStatusOpen") is False
                and page.property("_zStatusOpen") is False
                and page.property("_xySetupOpen") is False
                and page.property("_zSetupOpen") is False
                and page.property("_advExpanded") is False,
                f"xy={page.property('_xyStatusOpen')} z={page.property('_zStatusOpen')} "
                f"xySet={page.property('_xySetupOpen')} zSet={page.property('_zSetupOpen')} "
                f"adv={page.property('_advExpanded')}")
        # 收起 = 内容不参与布局（防止"收起了页面还长高"那种隐性回归）
        r.check("收起时状态内容不参与布局（visible=false）",
                (xy_body is None or bool(xy_body.property("visible")) is False)
                and (z_body is None or bool(z_body.property("visible")) is False),
                f"xy={xy_body.property('visible') if xy_body else None} "
                f"z={z_body.property('visible') if z_body else None}")
        # 点折叠头 = 打开。⚠ 这里**真发一次鼠标点击**，而不是 `mouseArea.clicked.emit()`：
        #   Qt 6 的 `clicked` 信号带 `QQuickMouseEvent*` 参数，emit() 会报
        #   "needs 1 argument(s), 0 given"。真点一次还顺带验了折叠头的位置与命中区域。
        pt = xy_fold.mapToScene(QPointF(float(xy_fold.property("width")) / 2,
                                        float(xy_fold.property("height")) / 2))
        QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier,
                         QPoint(int(pt.x()), int(pt.y())))
        r.pump(0.6)
        r.check("点左列「状态」折叠头 → 打开（且是页面状态在变）",
                page.property("_xyStatusOpen") is True,
                f"open={page.property('_xyStatusOpen')}")
        r.check("打开后内容参与布局（visible=true 且有宽度）",
                xy_body is not None and bool(xy_body.property("visible")) is True
                and float(xy_body.property("width")) > 100,
                f"vis={xy_body.property('visible') if xy_body else None} "
                f"w={xy_body.property('width') if xy_body else None}")
        page.setProperty("_xyStatusOpen", False)
        r.pump(0.4)
        # 首屏（1080p）：两张连接卡 + 预览 + 两张控制卡都在，不用滚
        # ⚠ 这一版**故意**允许首屏超一点：预览按 2448:2048 等比、用户点名"可以大一些"。
        #   硬保证仍然是 12a2：点动/急停/设原点这些**控件**必须落在首屏内。
        #   这里只钉"超出的量是有限的"，免得哪天悄悄长出一大截（比如折叠区没真正收起）。
        _ch = float(pscroll.property("contentHeight"))
        _vh = float(pscroll.property("height"))
        r.check("默认形态下首屏只超一点（≤ 40% 视口高）", _ch <= _vh * 1.40,
                f"content={_ch:.0f} view={_vh:.0f}")
        page.setProperty("_advExpanded", True)
        r.pump(0.4)
        r.check("展开「高级」后整页可以滚（里面是预设位置与协议框）",
                bool(pscroll.property("interactive")) is True,
                f"content={pscroll.property('contentHeight')} view={pscroll.property('height')}")
        page.setProperty("_advExpanded", False)
        r.pump(0.3)

    print("=== 12a2) 首屏够得着：点动 / 急停 / 设原点必须在第一屏内 ===")
    # 2026-09-28 精简时定的硬约束。理由很实际：点动与急停是高频动作，
    # "要往下滚一下才够得着"就是不合格 —— 当时正是靠这条发现「停止」的底边
    # 落在折叠线以下 9px（然后把读数卡里三行重复信息删掉才腾出位置）。
    # ⚠ 必须先把窗口设成 1080p 全屏（页面 1680×1040）再量：
    #   offscreen 的 harness 窗口会被屏幕尺寸裁，量出来的"首屏"比真实场景矮一截。
    win.setProperty("width", 1680)
    win.setProperty("height", 1040)
    r.pump(0.4)
    pscroll = find("pageScroll")
    vh = float(pscroll.property("height"))
    for n in ("jogPad", "stopButton", "zeroButton",
              "zJogUpButton", "zJogDownButton", "zStopButton", "zZeroButton"):
        it = find(n)
        if it is None:
            r.check("首屏内：" + n, False, "找不到控件")
            continue
        # ⚠ 量的是**整页坐标**（这一版只有一条滚动链，没有"列"了）
        bottom = it.mapToItem(pscroll, QPointF(0, 0)).y() + float(it.property("height"))
        r.check("1080p 首屏内：" + n, bottom <= vh + 1,
                "底=%.0f 可视高=%.0f" % (bottom, vh))
    win.setProperty("width", 1400)
    win.setProperty("height", 900)
    r.pump(0.3)

    print("=== 12b) 两台板子的连接卡片：并排 + 标题不许变成 IP ===")
    # 用户 2026-09-27 手绘稿：两张连接卡并排放在页顶。
    # ⚠ 这里钉的是一个**很容易犯的错**：卡片原来在连上以后把标题换成 `host:port`
    #   （只有一台板子时那样挺方便）。两台并排以后，两块板子用的是**同一个端口 3333**，
    #   标题全变成地址就分不清哪张卡是哪台了 —— 所以标题永远是平台名，
    #   地址下沉到副标题第一段。
    cards = [c for c in page.findChildren(QObject)
             if "StageControllerCard" in c.metaObject().className()]
    r.check("两张连接卡都在", len(cards) >= 2, f"找到 {len(cards)} 张")
    r.check("页面里确实连着板子（下面几条才有意义）", bool(stage.connected))
    for c in cards:
        t = str(c.property("title"))
        r.check("卡片标题始终是平台名（不是 IP）：" + t, ":" not in t, f"title={t!r}")
        # 副标题只在连上时才有内容（未连接时是空串，占位由卡片自己画）
        if bool(c.property("connected")):
            addr = str(c.property("host")) + ":" + str(c.property("port"))
            r.check("已连接的那张把地址写在副标题里：" + t, addr in str(c.property("subtitle")),
                    f"期望含 {addr}，实际 subtitle={c.property('subtitle')!r}")
    if len(cards) >= 2:
        # ⚠ 量 x/y 必须 mapToItem 到页面：两张卡各自的父级是**不同**的 GridLayout 格子，
        #   父级相对坐标都是 (0,0)，直接读 x/y 会得出"两张卡重叠"的假结论（踩过一次）。
        pos = [c.mapToItem(page, QPointF(0, 0)) for c in cards]  # 需要 QQuickItem，见文件头 import
        r.check("两张卡并排（x 不同、y 相同）",
                pos[1].x() > pos[0].x() + 1 and abs(pos[1].y() - pos[0].y()) < 1,
                f"pos={[(round(p.x()), round(p.y())) for p in pos]}")

    print("=== 12c) 预览条：按相机比例显示，不拉扁 ===")
    # 整宽预览条很宽（~1300px），而相机是 2448×2048 近方形 —— ImageView 的
    # aspectRatio=0 表示"铺满"，那会把画面横向拉扁（看着不对，却查不出哪儿错）。
    views = [v for v in page.findChildren(QObject)
             if v.metaObject().className().startswith("ImageView")]
    r.check("找得到预览组件", len(views) >= 1, f"{len(views)} 个")
    if views:
        cam.imageWidthChanged.emit()
        cam.imageHeightChanged.emit()
        cam._w, cam._h = 2448, 2048
        cam.imageWidthChanged.emit()
        cam.imageHeightChanged.emit()
        r.pump(0.3)
        ratio = float(views[0].property("aspectRatio"))
        r.check("预览比例取自相机（2448:2048）", abs(ratio - 2448 / 2048) < 0.01,
                f"aspectRatio={ratio:.3f}")
        cam._w, cam._h = 0, 0
        cam.imageWidthChanged.emit()
        cam.imageHeightChanged.emit()
        r.pump(0.2)

    print("=== 12d) 公用协议显示框：两块板子一个框（2026-09-28 用户要求）===")
    # 用户原话："板子的协议显示修改为公用的，因为我只要将 usb 线接到不同的板子上，
    # 这个就会有对应的指令。" 所以它现在在「高级」里、两路合并（ProtoHub）。
    proto = find("protoPanel")
    r.check("有公用协议框", proto is not None)
    r.check("它默认收在「高级」里", page.property("_advExpanded") is False)
    if proto is not None:
        r.check("收起时协议框不参与布局（visible=false）",
                bool(proto.property("visible")) is False,
                f"visible={proto.property('visible')}")
        page.setProperty("_advExpanded", True)
        r.pump(0.4)
        r.check("展开后协议框出来且高度正常",
                bool(proto.property("visible")) is True
                and float(proto.property("height")) >= 80,
                f"visible={proto.property('visible')} h={proto.property('height')}")
        def _delegate(host, name):
            """在**可视树**里按 objectName 找控件。

            ⚠ Repeater 造出来的 delegate **不在 QObject 树里**（实测 `parent()` 是 None，
              所以 `findChild`/`findChildren` 一个都找不到）—— 只能走 childItems() 递归。
              这是 AGENTS.md"通用坑"里那条的另一种表现（那边说的是 ListView 的 delegate）。
            """
            def walk(it):
                yield it
                for c in it.childItems():
                    yield from walk(c)
            for it in walk(host):
                if it.objectName() == name:
                    return it
            return None

        btn_xy = _delegate(proto, "protoSource_xy")
        r.check("两个来源的切换按钮都在（二轴 / Z）",
                btn_xy is not None and _delegate(proto, "protoSource_z") is not None)
        # 切来源要真的改状态（发送目标跟着它走）
        page.setProperty("_protoSource", "z")
        r.pump(0.2)
        # 真点一次（坐标取自控件自身，别再 emit MouseArea.clicked —— Qt6 那个信号带参数）
        # ⚠ QTest 发的是**窗口坐标**：协议框在展开的「高级」深处（页面 y≈2000+），
        #   不先滚进视口的话点击落在窗口外面，等于没点（第一版就是这么假红的）。
        if btn_xy is not None:
            pc = find("pageScroll")
            y_in = btn_xy.mapToItem(pc.property("contentItem"), QPointF(0, 0)).y()
            pc.setProperty("contentY", max(0.0, y_in - 150))
            r.pump(0.4)
            pt2 = btn_xy.mapToScene(QPointF(float(btn_xy.width()) / 2,
                                            float(btn_xy.height()) / 2))
            QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier,
                             QPoint(int(pt2.x()), int(pt2.y())))
        r.pump(0.4)
        r.check("点「二轴相机平台」→ 协议框来源切到 xy",
                str(page.property("_protoSource")) == "xy",
                repr(page.property("_protoSource")))
        # 合并流的接线：协议框读的是 ProtoHub，不是某一块桥
        def _as_list(v):
            """QML 的 JS 数组读回 Python 是 QJSValue，要过一道 toVariant()。"""
            return v.toVariant() if hasattr(v, "toVariant") else v

        r.check("协议框绑的是公用流（ProtoHub.lines）",
                len(_as_list(proto.property("lines"))) == len(_as_list(proto_hub.lines)),
                f"panel={len(_as_list(proto.property('lines')))} "
                f"hub={len(_as_list(proto_hub.lines))}")
        page.setProperty("_protoSource", "z")
        page.setProperty("_advExpanded", False)
        r.pump(0.3)

    print("=== 13) 断开 ===")
    server.board.commands.clear()
    stage.disconnectDevice()
    r.check("已断开", r.wait_for(lambda: not stage.connected))
    r.check("断开后提示未连接", "未连接" in page.property("_gateHint"),
            page.property("_gateHint"))

    server.close()
    print()
    if r.fails:
        print(f"FAILED {len(r.fails)}: {r.fails}")
        return 1
    print("StagePage 冒烟测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
