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

import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "DuAD_SoftwareContent"
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import QObject, Property, QSettings, QUrl, Signal, Slot   # noqa: E402
from PySide6.QtGui import QGuiApplication                            # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine                      # noqa: E402

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

    def __init__(self):
        super().__init__()
        self._connected = False
        self._index = 0

    def _get_connected(self):
        return self._connected

    cameraConnected = Property(bool, _get_connected, notify=cameraConnectedChanged)

    def _get_index(self):
        return self._index

    frameIndex = Property(int, _get_index, notify=frameIndexChanged)


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

    engine = QQmlApplicationEngine()
    engine.addImportPath(str(CONTENT))
    ctx = engine.rootContext()
    ctx.setContextProperty("StageBridge", stage)
    ctx.setContextProperty("AppBridge", app_bridge)
    ctx.setContextProperty("CameraBridge", camera_bridge)

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

    print("=== 5) 步长选择（点 10mm）===")
    chip10 = find("stepChip_10")
    r.check("找到 10mm 步长按钮", chip10 is not None)
    # 步长是否真的生效，在第 7 步用"点动的实际幅度"验证 ——
    # 直接读 step 属性只能证明赋值成功，证明不了它被用上了。
    print("=== 6) 立基准（点「把当前位置设为原点」）===")
    zero_btn = find("zeroButton")
    r.check("找到设为原点按钮", zero_btn is not None)
    zero_btn.clicked.emit()
    r.check("基准已建立", r.wait_for(lambda: stage.datum), f"datum={stage.datum}")
    r.check("提示已清空", page.property("_gateHint") == "", page.property("_gateHint"))
    r.check("canMove 变 True", r.wait_for(lambda: page.property("_canMove") is True))
    if jog_pad is not None:
        r.check("方向键已启用", jog_pad.property("interactive") is True)

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

    print("=== 11c2) 诊断面板的「板子状态」必须真的会刷新 ===")
    # 踩过的坑：StagePage 里原来写 `diagText: StageBridge.diagText()`，
    # 而 diagText 当时是 @Slot —— QML 绑定对**函数调用没有依赖追踪**，
    # 只在创建时求值一次，于是诊断面板永远显示"板子状态 未连接"，
    # 哪怕板子早已连上并在正常轮询。
    status = find("boardStatus")
    r.check("找到板子状态块", status is not None)
    # 这块现在是 Text（不再是 ReadonlyRow）—— 因为 70+ 字符的摘要
    # 在 ReadonlyRow 里不能收缩，会把整列撑宽、把同列的下拉框/开关挤出卡片。
    r.check("已连接时状态不再是「未连接」",
            status is not None and status.property("text") != "未连接",
            repr(status.property("text")) if status else "N/A")
    r.check("状态里含坐标与电压",
            status is not None and "mm" in str(status.property("text"))
            and "V" in str(status.property("text")),
            repr(status.property("text")) if status else "N/A")

    print("=== 11c2b) 诊断面板大扫除后只剩排障设施（2026-09-13）===")
    # 用户拍板：无限位回零整套 UI 删掉（改走限位开关），只留通用排障设施。
    # ⚠ 下面这几条**是反向断言**：删掉的东西不许偷偷回来 ——
    #   它们每一个都对应过真机上的困惑（"点了没反应"/"回零期间按停止没反应"）。
    for gone in ("homeKind", "homeCorner", "homeButton", "homeAbortButton",
                 "homePassXm", "homePassXp", "homePassYm", "homePassYp",
                 "applyHomeCfgButton", "homeRpm", "homeMa"):
        r.check("已删除的控件不在界面上：" + gone, find(gone) is None)
    r.check("保留「立即刷新状态」", find("pollButton") is not None)
    r.check("保留板子状态摘要", find("boardStatus") is not None)

    print("=== 11c4) 卡片顺序：平台设置必须在手动控制上面（齿轮点开就在眼前）===")
    # 用户 2026-09-13 的要求，理由很实际：平台卡片右上角的齿轮是 `_setupExpanded` 的开关，
    # 而设置面板原来在最下面 —— 点齿轮后要往下滚半屏才看得到它展开了，
    # 现象上就是"点了没反应"。所以这里**量 y 坐标**，不靠"我改过了"。
    jog = find("jogPanel")
    setup = find("setupPanel")
    r.check("找到手动控制卡", jog is not None)
    r.check("找到平台设置卡", setup is not None)
    if jog is not None and setup is not None:
        r.check("平台设置在手动控制上面", float(setup.property("y")) < float(jog.property("y")),
                "setupY=%.0f jogY=%.0f" % (setup.property("y"), jog.property("y")))

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
        r.check("文案是动作而不是状态", ("使能（" in txt) or ("失能（" in txt), txt)

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


    # 布局硬约束（用户明确要求）：左列缩略图、右列指标+文字，且**两列等高**。
    # ⚠ 等高这条不能靠"看起来差不多"：边长是个常数（写死 190，见组件的 _mapSide），
    #   而右列高度随文字折行变化 —— 所以要**量**，不然哪天文案一长就又对不齐了。
    readout = find("posReadout")
    r.check("找到位置读数组件", readout is not None)
    if readout is not None:
        map_x = float(readout.property("_mapX"))
        nums_x = float(readout.property("_numsX"))
        map_h = float(readout.property("_mapH"))
        nums_h = float(readout.property("_numsH"))
        r.check("缩略图在左、读数在右（两列并排）", map_x < nums_x,
                "mapX=%.0f numsX=%.0f" % (map_x, nums_x))
        r.check("缩略图已放大（>=150px）", map_h >= 150, "mapH=%.0f" % map_h)
        r.check("缩略图与右列等高（±30px）", abs(map_h - nums_h) <= 30,
                "mapH=%.0f numsH=%.0f 差=%.0f" % (map_h, nums_h, abs(map_h - nums_h)))


    print("=== 11d) 离开页面自动释放相机 ===")
    r.check("当前确实在预览", page.property("_previewOn") is True)
    page.setProperty("visible", False)
    r.pump(0.3)
    r.check("离开页面释放了相机", app_bridge.collectingOwner == "",
            repr(app_bridge.collectingOwner))
    r.check("回来时不再显示失败", page.property("_previewFailed") is False)
    page.setProperty("visible", True)
    r.pump(0.2)

    print("=== 12) 窄屏退化为单列 ===")
    win.setProperty("width", 900)
    r.pump(0.3)
    r.check("窄屏标记生效", page.property("_wide") is False, f"_wide={page.property('_wide')}")
    win.setProperty("width", 1400)
    r.pump(0.3)
    r.check("宽屏恢复双栏", page.property("_wide") is True)

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
