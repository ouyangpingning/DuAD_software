#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Z 轴升降平台那一区（StagePage 左列）的端到端冒烟测试。

与 `test_stage_page.py` 的分工：
  · `test_stage_page.py` —— X/Y 二轴平台 + 导航三处同步（**不许回归**）；
  · **本文件** —— Z 轴那一段：设备卡片、遥测、点动、协议显示框、折叠设置面板。
  两者都渲染**同一个** StagePage.qml（Z 区就在这个页面的左列里）。

本文件用的是**真的 ZStageBridge** + 进程内假 Z 轴板子，不是替身 ——
所以它同时验证了协议分帧、命令队列、json 遥测、属性绑定这几层。
（AGENTS.md 第 11 条：替身的诚实度决定了测试能发现什么。）

假板子严格按 Zstage 固件的行为实现，关键点都照抄固件：
  · 连上后第一行必须是口令，否则回 `#ERR bad token`
  · 每条命令的应答以 `#OK` / `#ERR <code>` 结束
  · `zup/zdown` 是**相对**点动：没有基准也允许（固件对无基准的相对运动限 ±50mm）
  · `zmove`（绝对定位）**没有基准 / 没有软限位就拒绝**（固件是 fail-closed 的）
  · `json` 在轮询时顺手判定"非阻塞移动是否到位"（对应固件的 pend_poll）
  · `trace on` 之后每条命令都多回两行 `@TX` / `@RX` 驱动器原始帧
  · `stop all` 打断运动与回零、并清掉故障锁存

运行（不需要真板子、不需要相机）：
    source DuAD_SoftwareContent/pyqml/bin/activate
    QT_QPA_PLATFORM=offscreen python3 -u tests/test_zstage_page.py
"""

import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "DuAD_SoftwareContent"
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import QObject, QPointF, QSettings, QUrl           # noqa: E402
from PySide6.QtGui import QGuiApplication                               # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine                         # noqa: E402
from PySide6.QtQuick import QQuickItem                                  # noqa: E402

from Src.stage_bridge import StageBridge                                # noqa: E402
from Src.zstage_bridge import ZStageBridge                              # noqa: E402
from test_stage_page import FakeAppBridge, FakeCameraBridge             # noqa: E402

# ⚠ 与 main.py 保持同一套 URL 解析语义：main.py 在创建 QGuiApplication **之前**设了
#   `QML_COMPAT_RESOLVE_URLS_ON_ASSIGNMENT=1`，相对 URL 按"赋值所在 qml 文件"解析。
#   不设它的话，`StagePage.qml` 里写的 `../images/Z轴平台.svg` 会按组件自身所在目录
#   解析成 `pages/images/...`（不存在）→ 图标静默消失，而"真程序里却是好的" ——
#   测试环境与真程序不一致，比测不到更糟。必须在 QGuiApplication 之前设。
os.environ.setdefault("QML_COMPAT_RESOLVE_URLS_ON_ASSIGNMENT", "1")

Z_TOKEN = "deadbeef"
# 卡片"内容区"的宽度上限 = 内容区自身宽度（卡片宽 - 左右各 24 边距）。
# 这条是 AGENTS.md 第 9 条那个坑的守卫：
# 一行长文本不收缩 → 它把整个 ColumnLayout 撑宽 → 同列其它行的右侧控件被顶出卡片。
# ⚠ 2026-09-27 从"写死 420"改成"跟内容区比"：页面重排成**两列**以后卡片宽度由窗口
#   决定（不再是恒定的 460），写死的数字会在宽窗口下把满宽的正常行全报成 FAIL ——
#   守卫一旦开始误报，就会被下一个人顺手删掉。
def row_limit(body):
    return float(body.property("width")) + 1.0


# ============================================================
# 假 Z 轴板子（严格按 Zstage 固件行为）
# ============================================================
class FakeZBoard:
    def __init__(self):
        self.z = 0.0
        self.datum = 0
        self.lim = 0
        self.lim_win = [0.0, 250.0]
        self.moving = 0
        self.last = "none"
        self.homing = 0
        self.auto = 0
        self.fault = "none"
        self.trace = False
        self.rpm = 300
        self.acc = 100
        # 使能状态（json 的 en）：假板子照固件从 0 开始，但**本文件的历史断言**
        # 都建立在"连着就是已使能"上（原来这里硬写 "en": 1）。为不动别处的断言，
        # 初值仍是 1；`en all` / `dis all` 会真的改它 —— 界面颜色断言就靠这个。
        self.en = 1
        # 无限位回零的参数（固件 NVS 出厂默认）：`zset home` 改它，json 报它
        self.home_rpm = 400
        self.home_ma = 100       # 出厂默认（现场实测 100mA 可用）
        self.home_tmo = 12000
        self.home_dir = "down"       # 板子里存的方向（只给 zautohome 用）
        self.home_dir_cmd = None     # 最后一次 `zhome <dir>` 用的方向
        self.home_cmds = []
        self.step_seen = []
        self.commands = []
        # 轮询几次之后才算"走到位"（对应固件 pend_poll 的到位判定）
        self.move_polls_needed = 1
        self._move_polls = 0
        self.lock = threading.Lock()

    def json_line(self):
        return json.dumps({
            "z": round(self.z, 3), "skew": 0.02, "a": int(self.z * 6400),
            "b": int(self.z * 6400), "v": 24.1, "en": self.en, "datum": self.datum,
            "lim": self.lim, "zmin": self.lim_win[0], "zmax": self.lim_win[1],
            "moving": self.moving, "homing": self.homing, "last": self.last,
            "fault": self.fault, "tgt": self.z, "auto": self.auto,
            "umrev": 8000,
            # 回零参数（固件 json 的 hma/hrpm/htmo）—— 界面"板子当前生效"那一行靠它
            "hma": self.home_ma, "hrpm": self.home_rpm, "htmo": self.home_tmo,
            "rssi": -61, "ip": "192.168.1.43",
        })

    def handle(self, cmd):
        """返回 (输出行列表, 是否成功)。"""
        with self.lock:
            self.commands.append(cmd)
            a = cmd.split()
            if not a:
                return [], True
            h = a[0]

            if h == "json":
                # 对应固件 cmd_json 里的 pend_poll()：轮询即"到位判定"
                if self.moving:
                    self._move_polls += 1
                    if self._move_polls >= self.move_polls_needed:
                        self.moving = 0
                        self.last = "ok"
                        self._move_polls = 0
                if self.homing == 1:
                    self.homing = 2
                    self.datum = 1
                    self.z = 0.0
                return [self.json_line()], True

            if h == "zsign":
                # 固件的读回格式（桥用正则抠 sa/sb）
                return ["  当前方向符号 sa=-1 sb=-1 —— 两轴同向转（同向安装，没有反向补偿）"], True

            if h in ("zup", "zdown"):
                # 相对点动：**没有基准也允许**（固件对无基准的相对运动限 ±50mm）
                mm = float(a[1])
                self.z += mm if h == "zup" else -mm
                self.moving = 1
                self.last = "none"
                return ["  Z %+.2fmm: ok" % mm], True

            if h == "zmove":
                # 绝对定位：fail-closed —— 缺基准或缺软限位一律拒绝（未发运动指令）
                if not self.datum:
                    return ["  zmove 拒绝: 还没有基准 —— 先把平台推到靠块/机械死点，再 'zzero'。"], False
                if not self.lim:
                    return ["  zmove 拒绝: 软限位还没设 —— 先 'zlim <最低mm> <最高mm>'。"], False
                tgt = float(a[1])
                if tgt < self.lim_win[0] or tgt > self.lim_win[1]:
                    return ["  zmove 拒绝: 目标超出软限位"], False
                self.z = tgt
                self.moving = 1
                self.last = "none"
                return ["  Z -> %.2fmm: ok" % tgt], True

            if h == "zzero":
                self.datum = 1
                self.z = 0.0
                return ["  left : ok", "  right: ok", "  基准已建立"], True

            if h == "zlim":
                if len(a) == 2 and a[1] == "off":
                    self.lim = 0
                    return ["  软限位已关闭"], True
                self.lim_win = [float(a[1]), float(a[2])]
                self.lim = 1
                return ["  软限位已设"], True

            if h == "zset":
                if a[1] == "rpm":
                    self.rpm = int(a[2])
                elif a[1] == "acc":
                    self.acc = int(a[2])
                elif a[1] == "home":
                    # `zset home <rpm> <mA> [timeout_ms]` —— 固件要求 rpm/mA **都在场**
                    # （`argc > 3`），超时可省（省了=保留原值）。照抄这个语义，
                    # 否则桥少发一个字段也测不出来。
                    if len(a) < 4:
                        return ["  home 参数范围: rpm 1~6000, mA 1~3000"], False
                    self.home_rpm, self.home_ma = int(a[2]), int(a[3])
                    if len(a) > 4:
                        self.home_tmo = int(a[4])
                    return ["  回零参数 = %drpm / %umA / %ums"
                            % (self.home_rpm, self.home_ma, self.home_tmo)], True
                else:
                    return ["  zset: 参数不合法"], False
                return ["  已设 %s = %s" % (a[1], a[2])], True

            if h in ("en", "dis"):
                self.en = 1 if h == "en" else 0
                return ["  left : ok", "  right: ok"], True

            if h == "zautohome":
                # 方向是**可选**参数：不带就保留板子里存的方向（固件 cmd_zautohome）。
                self.auto = 1 if (len(a) > 1 and a[1] == "on") else 0
                if len(a) > 2:
                    self.home_dir = a[2]
                return ["  上电自动回零 = %s，方向 = %s"
                        % ("开" if self.auto else "关", self.home_dir)], True

            if h == "zhome":
                # `zhome [up|down] [nowait]`；方向写进日志供断言（桥原来写死 down）
                self.home_cmds.append(cmd)
                if len(a) > 1 and a[1] in ("up", "down"):
                    self.home_dir_cmd = a[1]
                self.homing = 1
                self.moving = 1
                return ["  回零已启动（非阻塞）"], True

            if h == "stop":
                self.moving = 0
                self.homing = 3
                self.last = "aborted"
                self.fault = "none"
                return ["  两个电机已刹车", "  故障锁存已清"], True

            if h == "trace":
                self.trace = (len(a) > 1 and a[1] == "on")
                return ["  驱动器帧镜像 = %s" % ("开" if self.trace else "关")], True

            if h == "error":
                # 让命令被拒绝，用来测"#ERR → 界面报错"
                return ["  这条命令是测试用的，故意失败"], False

            # 未知命令照固件回 #ERR
            return ["  %s: 未知命令" % h], False

    def trace_lines(self, cmd):
        """trace on 时固件镜像回来的驱动器原始帧（每帧两行）。"""
        if not self.trace:
            return []
        n = sum(len(c) for c in cmd) % 256
        return ["@TX left C5 01 F3 00 00 01 2C %02X 5C" % n,
                "@RX left C5 01 F3 01 00 5C"]


class FakeZBoardServer:
    """进程内的假 Z 轴板子（TCP 行协议，与固件同一套）。"""

    def __init__(self):
        self.board = FakeZBoard()
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(4)
        self.port = self._sock.getsockname()[1]
        self._stop = False
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def _accept_loop(self):
        while not self._stop:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _w(self, f, s):
        f.write((s + "\n").encode("utf-8"))

    def _serve(self, conn):
        f = conn.makefile("rwb")
        try:
            first = f.readline().decode("utf-8", "replace").strip()
            if first != Z_TOKEN:
                self._w(f, "#ERR bad token")
                f.flush()
            else:
                self._w(f, "#OK auth")
                f.flush()
                while True:
                    raw = f.readline()
                    if not raw:
                        break
                    cmd = raw.decode("utf-8", "replace").strip()
                    if cmd in ("quit", "exit"):
                        self._w(f, "#OK")
                        f.flush()
                        break
                    lines, ok = self.board.handle(cmd)
                    for ln in lines:
                        self._w(f, ln)
                    for ln in self.board.trace_lines(cmd):
                        self._w(f, ln)
                    self._w(f, "#OK" if ok else "#ERR 1")
                    f.flush()
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def close(self):
        self._stop = True
        try:
            self._sock.close()
        except OSError:
            pass


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
    height: 1700
    visible: true

    StagePage {
        id: stagePage
        objectName: "stagePage"
        anchors.fill: parent
    }
}
"""


def as_list(v):
    """QML 的 JS 数组从 Python 读回来可能是 QJSValue —— 过一道 toVariant()。"""
    return v.toVariant() if hasattr(v, "toVariant") else v


def main():
    app = QGuiApplication(sys.argv[:1])
    zserver = FakeZBoardServer()
    r = Runner(app)

    print(f"假 Z 轴板子监听 127.0.0.1:{zserver.port}\n")

    # ⚠ 隔离 QSettings：测试绝不能写用户的真实配置（AGENTS.md 第 1 条坑）
    sdir = Path(tempfile.mkdtemp(prefix="duad_zstage_page_test_"))
    stage = StageBridge(settings=QSettings(str(sdir / "xy.ini"), QSettings.Format.IniFormat))
    zstage = ZStageBridge(settings=QSettings(str(sdir / "z.ini"), QSettings.Format.IniFormat))
    app_bridge, camera_bridge = FakeAppBridge(), FakeCameraBridge()

    engine = QQmlApplicationEngine()
    engine.addImportPath(str(CONTENT))
    ctx = engine.rootContext()
    ctx.setContextProperty("StageBridge", stage)
    ctx.setContextProperty("AppBridge", app_bridge)
    ctx.setContextProperty("CameraBridge", camera_bridge)
    ctx.setContextProperty("ZStageBridge", zstage)
    from Src.proto_hub import ProtoHub

    proto_hub = ProtoHub({"xy": stage, "z": zstage})   # ⚠ 保持引用，见上
    ctx.setContextProperty("ProtoHub", proto_hub)

    warnings = []
    engine.warnings.connect(lambda ws: warnings.extend(w.toString() for w in ws))

    engine.loadData(HARNESS, QUrl.fromLocalFile(str(CONTENT / "_smoke_zstage.qml")))
    r.pump(0.5)

    print("=== 1) 页面加载（Z 区跟着 StagePage 一起加载）===")
    if not engine.rootObjects():
        print("  [FAIL] StagePage 加载失败")
        for w in warnings:
            print("      " + w)
        zserver.close()
        return 1
    win = engine.rootObjects()[0]
    page = win.findChild(QObject, "stagePage")
    r.check("StagePage 实例化成功", page is not None)
    # ⚠ 判据必须是"**加载完成那一刻**"：进程末尾那些
    #   `Cannot read property 'x' of null` 是 Python 侧对象先于 QML 被回收的退出噪音。
    real_errors = [w for w in warnings
                   if "Invalid image provider" not in w
                   and any(k in w for k in ("is not defined", "TypeError", "ReferenceError",
                                            "Unable to assign", "Cannot assign",
                                            "is not a type", "SyntaxError",
                                            "Cannot override"))]
    r.check("Z 区没有引入任何 QML 绑定/类型错误", not real_errors, "; ".join(real_errors[:3]))
    if page is None:
        zserver.close()
        return 1

    def find(name):
        return page.findChild(QObject, name)

    # ============================================================
    print("=== 2) Z 区控件齐不齐（按 objectName 找）===")
    # 用户点名的那些控件，一个都不能少 —— 少一个就是"界面画了但没法用/没法测"
    wanted = [
        ("zControllerCard", "Z 轴设备卡片"),
        ("zEnableButton", "失能/使能按钮"),
        ("zJogUpButton", "向上按钮"),
        ("zJogDownButton", "向下按钮"),
        ("zStopButton", "急停按钮"),
        ("zZeroButton", "设为原点按钮"),
        ("zTargetInput", "绝对定位输入框"),
        ("zMoveToButton", "移动到按钮"),
        ("protoPanel", "协议显示框"),
        ("protoList", "协议报文列表"),
        ("protoCommandInput", "自定义命令输入框"),
        ("protoSendButton", "自定义命令发送按钮"),
        ("protoTraceSwitch", "驱动器帧镜像开关"),
        ("protoPauseSwitch", "暂停记录开关"),
        ("protoClearButton", "清空按钮"),
        ("zHostField", "Z 板子 IP 输入框"),
        ("zTokenField", "Z 板子口令输入框"),
        ("zApplyButton", "Z 设置应用按钮"),
        ("zControllerCard", "Z 连接卡（闸门状态在它状态行里）"),
        ("zRpmSliderRow", "转速滑块行"),
    ]
    missing = [n for n, _ in wanted if find(n) is None]
    r.check("Z 区全部控件都在", not missing,
            ("缺: " + ", ".join(missing)) if missing else f"{len(wanted)} 个")

    # ============================================================
    print("=== 3) 卡片文案：Z 卡片用覆写值，X/Y 卡片零回归 ===")
    # StageControllerCard 新加了 title/connectingText/iconSource 三个**可选**属性。
    # 这条断言同时钉两头：Z 卡片显示 Z 的标题；X/Y 卡片**一个字都没变**。
    zcard = find("zControllerCard")
    if zcard is not None:
        r.check("Z 卡片标题是「Z 轴升降平台」",
                str(zcard.property("title")) == "Z 轴升降平台",
                repr(zcard.property("title")))
        r.check("Z 卡片图标指向 Z轴平台.svg",
                "Z轴平台.svg" in str(zcard.property("iconSource")),
                repr(zcard.property("iconSource")))
        r.check("Z 卡片连接中文案是 Z 的",
                "Z 轴" in str(zcard.property("connectingText")),
                repr(zcard.property("connectingText")))
    xy_cards = [c for c in page.findChildren(QObject)
                if "StageControllerCard" in c.metaObject().className()]
    xy_titles = [str(c.property("title")) for c in xy_cards]
    r.check("X/Y 卡片标题仍是默认的「二轴相机平台」（零回归）",
            "二轴相机平台" in xy_titles, str(xy_titles))
    r.check("两张卡片都在（X/Y + Z）", len(xy_cards) >= 2, f"找到 {len(xy_cards)} 张")

    # 图标路径：`../images/Z轴平台.svg` 是相对 **StagePage.qml**（pages/）写的，
    # 只有在"按赋值所在文件解析 URL"的语义下才对（main.py 就是这么设的，本测试头部
    # 也设了同一个环境变量）。路径写错时 Qt 只打一行 "Cannot open" 就把图标**静默**
    # 画成空白 —— 所以这里把它变成一条断言。
    icon_err = [w for w in warnings if "Z轴平台.svg" in w and "Cannot open" in w]
    r.check("Z 轴图标能打开（路径没有被解析成 pages/images/…）", not icon_err,
            "; ".join(icon_err[:1]))

    # ============================================================
    print("=== 4) 未连接：运动按钮禁用 + 把原因写在旁边 ===")
    r.pump(0.2)
    r.check("Z 未连接", not zstage.connected)
    up = find("zJogUpButton")
    dn = find("zJogDownButton")
    hint = find("zGateHint")
    r.check("向上按钮禁用", up is not None and bool(up.property("enabled")) is False)
    r.check("向下按钮禁用", dn is not None and bool(dn.property("enabled")) is False)
    # "点不动必须在点击前把原因写出来" —— 本项目铁律，所以这段文字非空
    r.check("禁用的原因写在旁边（非空）",
            hint is not None and len(str(hint.property("text")).strip()) > 1,
            repr(hint.property("text")) if hint else "N/A")
    r.check("原因里点明是「未连接」",
            hint is not None and "未连接" in str(hint.property("text")),
            repr(hint.property("text")) if hint else "N/A")
    # 急停：未连接时不可点（唯一的例外条件就是 connected）
    stopb = find("zStopButton")
    r.check("未连接时急停不可点", stopb is not None
            and bool(stopb.property("enabled")) is False)

    # ============================================================
    print("=== 5) 连上假 Z 板子 ===")
    hostf, tokenf = find("zHostField"), find("zTokenField")
    hostf.setProperty("text", "127.0.0.1")
    tokenf.setProperty("text", Z_TOKEN)
    r.check("连接发起成功", zstage.connectDevice("127.0.0.1", zserver.port, Z_TOKEN) is True)
    r.check("已连接", r.wait_for(lambda: zstage.connected), f"connected={zstage.connected}")
    # Z 连上**不许**影响 X/Y（两块板子、两条链路）
    r.check("X/Y 仍然是未连接（两条链路互不影响）", not stage.connected)
    r.check("连接后读到方向符号", r.wait_for(lambda: len(zstage.signText) > 0),
            repr(zstage.signText))
    r.check("方向符号格式与固件一致（sa=-1 sb=-1）",
            "sa=-1" in zstage.signText and "sb=-1" in zstage.signText,
            repr(zstage.signText))

    print("=== 6) 闸门阶梯①：还没有基准（按钮全灰 + 写明原因）===")
    # 桥的语义（zstage_bridge._require_ready）：
    #   · **绝对定位**要求"有基准 + 有软限位"；
    #   · **点动不受基准闸限制**（固件故意允许无基准相对点动 ≤20mm，因为"把平台挪到
    #     参考位置"就是立基准的前置步骤 —— 界面要是把点动也灰掉，首次立基准就只剩
    #     "用手推平台"这一条路了；两个独立评审都指出过）。
    # 所以逐档量：没基准 → 有基准没软限位 → 全开，绝对定位每档都要"灰得有理由"。
    r.pump(0.3)
    r.check("假板子还没有基准", not zstage.datum)
    r.check("canMove 为 False", bool(zstage.canMove) is False)
    r.check("提示是「还没立基准」", "基准" in str(hint.property("text")),
            repr(hint.property("text")))
    r.check("向上按钮**仍可用**（点动不受基准闸限制，否则没法挪到靠块立基准）",
            bool(up.property("enabled")) is True)
    r.check("向下按钮**仍可用**", bool(dn.property("enabled")) is True)
    r.check("绝对定位按钮禁用（它必须要有基准）",
            bool(find("zMoveToButton").property("enabled")) is False)

    print("=== 7) 闸门阶梯②：立了基准、但还没设软限位 ===")
    zb = find("zZeroButton")
    r.check("找到设为原点按钮", zb is not None)
    zb.clicked.emit()
    r.check("基准已建立", r.wait_for(lambda: zstage.datum), f"datum={zstage.datum}")
    r.pump(0.3)
    # ⚠ 这里**不能**断言提示清空：桥的第二道闸是软限位，而那正是"点不动要说原因"
    #   该说的东西。写测试时先入为主地以为"立了基准就该放行"，是假断言。
    r.check("提示换成「还没设软限位」",
            "软限位" in str(hint.property("text")), repr(hint.property("text")))
    r.check("软限位没设时 canMove 仍为 False", bool(zstage.canMove) is False)
    r.check("软限位没设时点动仍可用（只有绝对定位被软限位闸拦住）",
            bool(up.property("enabled")) is True)

    print("=== 8) 设置面板：量程来自桥 + 应用真的下发 ===")
    rpm_row = find("zRpmSliderRow")
    r.check("找到 Z 转速滑块行", rpm_row is not None)
    if rpm_row is not None:
        # AGENTS.md 第 19 条：同一个数字出现在前后端两处，迟早漂移，而且漂移是静默的
        r.check("转速滑块上界 == ZStageBridge.uiMaxRpm（量程只许有一份）",
                abs(float(rpm_row.property("to")) - float(zstage.uiMaxRpm)) < 0.5,
                f"to={rpm_row.property('to')} uiMaxRpm={zstage.uiMaxRpm}")
        r.check("界面量程不超过固件上限 1200", float(zstage.uiMaxRpm) <= 1200,
                f"uiMaxRpm={zstage.uiMaxRpm}")
    acc_row = find("zAccSliderRow")
    r.check("加减速滑块下界 >= 1（固件的 0 = 直接启动、无斜坡，最伤这种刚性平台）",
            acc_row is not None and float(acc_row.property("from")) >= 1.0,
            f"from={acc_row.property('from') if acc_row else 'N/A'}")

    # ⚠ 「上电自动回零」开关的历史换过一次方向，读这条之前先看日期：
    #   2026-09-28 曾**从界面删掉**（用户：只留最常用的操作）→ 那时这里是
    #   "控件不许偷偷回来"的反向断言；
    #   2026-09-29 用户**要求加回来**（配合限位电流一起调）→ 断言反过来：
    #   控件必须在、且真的发命令，而且 `zautohome` **不许带方向**
    #   （方向归手动按钮的 setHomeDir，两条路径分开 —— 带 `down` 就等于
    #    顺手把板子里存的方向改掉了）。
    ah = find("zAutohomeSwitch")
    r.check("「上电自动回零」开关在界面上（2026-09-29 用户要求加回）", ah is not None)
    r.check("开关显示的是**板子里**的当前状态（json auto）",
            ah is not None and bool(ah.property("on")) == bool(zstage.autohome),
            f"switch={ah.property('on') if ah else None} bridge={zstage.autohome}")
    zserver.board.commands.clear()
    zstage.setAutohome(True)
    r.check("桥能下发 zautohome（能力一直在）",
            r.wait_for(lambda: any(c.startswith("zautohome") for c in zserver.board.commands), 3),
            str([c for c in zserver.board.commands if c.startswith("zautohome")][:1]))
    r.check("下发的 zautohome **不带方向**（带方向会顺手改掉板子里的方向）",
            all(len(c.split()) == 2 for c in zserver.board.commands
                if c.startswith("zautohome")),
            str([c for c in zserver.board.commands if c.startswith("zautohome")][:2]))
    r.check("板子接受后开关跟着亮（乐观置位 + json 回读）",
            r.wait_for(lambda: bool(find("zAutohomeSwitch").property("on")) is True),
            f"switch={find('zAutohomeSwitch').property('on')} board={zserver.board.auto}")
    zstage.setAutohome(False)
    r.pump(0.3)

    # ── 使能状态的颜色指示（2026-09-29 用户："使能要有颜色指示"）──
    # 语义是按用户原话定的：**未使能 = 绿（success）**（电机松着、可手推，安全），
    # **已使能 = 淡红（dangerSoft）**（闭环抱死 + 带电）。刻意不用实心红 danger，
    # 免得跟同一张卡里的「停止」撞脸。
    # ── 遥测图标的大小与颜色（用户 2026-09-29："后面两个图标有点大"）──
    # ⚠ 这条守卫为什么用断言而不是截图：这两个图标是 `IconImage`（ColorOverlay + SVG），
    #   离屏渲染下不一定画得出来（连卡片右上角的齿轮都可能不画，见 docs/19 §32），
    #   所以"大小/颜色"这类版面决定用属性断言钉住，人眼复核交给真机。
    #   两张卡共用 StageControllerCard.qml，所以这条同时也管住 X/Y 那张。
    print("=== 8a0) 遥测图标：比原来的 24px 小，但别小到看不见 ===")
    # 背景（2026-09-29 两次现场）：① 用户说"后面两个图标有点大" —— 真因是它们作为
    # RowLayout 的子项，`width: 11` 被布局覆盖，实际按 IconImage 的 implicitWidth=24 画；
    # ② 只把尺寸改小（Layout.preferred 9）后用户反馈"图标直接消失了" ——
    #    IconImage 染色走 ColorOverlay，**创建后被布局改尺寸**可能拿不到纹理，
    #    而且当时还把颜色改成了浅灰，9px 浅灰在浅色卡片上几乎看不见。
    # 所以最终写法 = 尺寸**创建时定死**（implicit 10）+ preferred 10 + **保持默认深色**。
    for card_name, label in (("zControllerCard", "Z 轴"), ("xyControllerCard", "X/Y")):
        card = find(card_name)
        r.check(f"找到{label}连接卡", card is not None)
        if card is None:
            continue
        for icon_name, what in (("cardVoltageIcon", "电压闪电"), ("cardRssiIcon", "信号格")):
            ic = card.findChild(QObject, icon_name) if card else None
            r.check(f"{label}：{what}图标存在（objectName 是测试的抓手）", ic is not None)
            if ic is None:
                continue
            # 只对**可见**的图标断言实际尺寸：布局只给可见子项分配几何，
            # 不可见时保持 implicit —— 那是正常现象，不是回归。
            vis = bool(ic.property("visible"))
            r.check(f"{label}：{what}图标可见时是 10×10（原来被布局按 24 画）",
                    (not vis) or (int(ic.property("width")) == 10
                                  and int(ic.property("height")) == 10),
                    f"visible={vis} {ic.property('width')}x{ic.property('height')}")
            # ⚠ implicit 也必须一起钉住：那是"创建时定尺寸"的那一半，
            #   少了它 = ColorOverlay 可能在布局改写尺寸后画不出东西（用户实测过）。
            r.check(f"{label}：{what}图标的 implicit 尺寸同为 10（创建时定死）",
                    int(ic.property("implicitWidth")) == 10
                    and int(ic.property("implicitHeight")) == 10,
                    f"implicit={ic.property('implicitWidth')}x{ic.property('implicitHeight')}")

    # 同类坑的另一半：两张手动控制卡的标题图标(16)与「绝对定位」靶心(13)，
    # 原来也都在按 24px 画 —— 用 objectName 钉住（Z 轴那张是用户天天看的）。
    for icon_name, want, what in (("jogTitleIcon", 16, "卡标题图标"),
                                  ("jogTargetIcon", 13, "绝对定位靶心")):
        ic = find(icon_name)
        r.check(f"Z 手动控制卡：{what}存在（objectName 是抓手）", ic is not None)
        if ic is not None:
            r.check(f"Z 手动控制卡：{what} = {want}px（原来被布局按 24 画）",
                    int(ic.property("width")) == want and int(ic.property("height")) == want,
                    f"{ic.property('width')}x{ic.property('height')}")

    print("=== 8a) 使能状态用颜色说话（未使能=绿 / 已使能=淡红）===")
    en_btn = find("zEnableButton")
    r.check("找到使能按钮", en_btn is not None)
    r.check("假板子当前已使能（json en=1）", bool(zstage.enabled) is True,
            f"enabled={zstage.enabled}")
    r.check("已使能 → tone = dangerSoft（淡红）",
            str(en_btn.property("tone")) == "dangerSoft" if en_btn else False,
            f"tone={en_btn.property('tone') if en_btn else 'N/A'}")
    # 文字是**动作**（此刻已使能 ⇒ 按钮写"失能"），状态由颜色说 —— 这条 2026-09-28
    # 定下的规矩没变；先断言再点，点完文字就会翻成"使能"。
    r.check("使能按钮的文字仍然是**动作**（本次是「失能」），不是状态词",
            str(en_btn.property("text")) == "失能",
            repr(en_btn.property("text")))
    en_btn.clicked.emit()
    r.check("点击后真的发了失能命令（dis all）",
            r.wait_for(lambda: any(c.startswith("dis") for c in zserver.board.commands), 3),
            str([c for c in zserver.board.commands if c.startswith("dis")][:1]))
    r.check("失能后按钮 tone 变成 success（淡绿）",
            r.wait_for(lambda: str(find("zEnableButton").property("tone")) == "success", 3),
            f"tone={find('zEnableButton').property('tone')} enabled={zstage.enabled}")
    r.check("失能后文字翻成「使能」（动作跟着状态走）",
            str(find("zEnableButton").property("text")) == "使能",
            repr(find("zEnableButton").property("text")))
    r.check("X/Y 那张卡是同一套颜色语言（否则两张卡看起来像两个软件）",
            str(find("enableButton").property("tone")) in ("success", "dangerSoft"),
            f"tone={find('enableButton').property('tone')}")
    find("zEnableButton").clicked.emit()          # 恢复使能，别影响后面的用例
    r.check("使能回来（en all）", r.wait_for(lambda: bool(zstage.enabled) is True, 3))

    # ── 无限位回零的参数（限位电流 / 转速 / 超时）──────────────
    print("=== 8a2) 自动回零参数：板子值回读 + 写入 + 甜点区警告 ===")
    ma_row = find("zHomeMaField")
    rpm_row2 = find("zHomeRpmField")
    tmo_row = find("zHomeTmoField")
    r.check("三个输入框都在（限位电流/回零转速/回零超时）",
            ma_row is not None and rpm_row2 is not None and tmo_row is not None)
    r.check("限位电流框显示的是**板子实际在用的值**（json hma）",
            ma_row is not None and str(ma_row.property("text")) == str(zserver.board.home_ma),
            f"field={ma_row.property('text') if ma_row else None} board={zserver.board.home_ma}")
    cur = find("zHomeCurrentText")
    r.check("有「板子当前生效」那一行（我填的 vs 板子正在用的要能对上）",
            cur is not None and "100" in str(cur.property("text")),
            repr(cur.property("text")) if cur else "None")

    warn = find("zHomeMaWarn")
    # ⚠ 断言 `visible` 之前**必须先把折叠节展开**：Qt Quick 的 visible 会向下传染
    #   （父项不可见 ⇒ 子项的 visible 读出来也是 false），而这里量的是"警告该不该出现"。
    #   折叠着断言永远失败，而那不是 bug —— 这条踩过一次，别再删掉这两行。
    page.setProperty("_zSetupOpen", True)
    r.pump(0.4)
    r.check("100mA 在经验区 → 不显示警告",
            warn is not None and bool(warn.property("visible")) is False)
    ma_row.setProperty("text", "40")
    r.pump(0.2)
    r.check("填 40mA（已接近空转 40mA）→ 当场警告「假成功」",
            bool(find("zHomeMaWarn").property("visible")) is True
            and "假成功" in str(find("zHomeMaWarn").property("text")),
            repr(find("zHomeMaWarn").property("text")))
    ma_row.setProperty("text", "2000")
    r.pump(0.2)
    r.check("填 2000mA（远高于经验区）→ 警告「永远不触发」",
            "永远不触发" in str(find("zHomeMaWarn").property("text")),
            repr(find("zHomeMaWarn").property("text")))

    ma_row.setProperty("text", "200")
    rpm_row2.setProperty("text", "400")
    tmo_row.setProperty("text", "20000")
    r.pump(0.2)
    r.check("超时 20000ms 够用 → 不显示「超时偏短」警告",
            bool(find("zHomeTmoWarn").property("visible")) is False,
            repr(find("zHomeTmoWarn").property("text")))
    # ⚠ 现场实测的坑（2026-09-29）：用户把超时填成 4000ms，而满行程要好几秒 ——
    #   平台离死点远时这次回零会在**中途**被判超时，现象只是"回零没跑完就停了"。
    r.check("桥算出的满行程需求与固件同一套算法（限位 0~250mm / 导程 8mm / 400rpm → 7~9s）",
            7000 <= int(zstage.homeTmoNeedMs) <= 9000, str(zstage.homeTmoNeedMs))
    tmo_row.setProperty("text", "4000")
    r.pump(0.3)
    r.check("超时 4000ms 短于满行程需求 → 当场警告「超时偏短」",
            bool(find("zHomeTmoWarn").property("visible")) is True
            and "超时偏短" in str(find("zHomeTmoWarn").property("text")),
            repr(find("zHomeTmoWarn").property("text")))
    tmo_row.setProperty("text", "20000")
    r.pump(0.2)
    zserver.board.commands.clear()
    find("zHomeApplyButton").clicked.emit()
    r.check("「写入回零参数」把三个值一起发出去（固件要求 rpm+mA 都在场）",
            r.wait_for(lambda: any(c.startswith("zset home") for c in zserver.board.commands), 3),
            str([c for c in zserver.board.commands if c.startswith("zset home")][:1]))
    r.check("发出去的就是界面上填的那三个值",
            any(c == "zset home 400 200 20000" for c in zserver.board.commands),
            str([c for c in zserver.board.commands if c.startswith("zset home")][:2]))
    r.check("板子接受后界面回读到新值（限位电流 200）",
            r.wait_for(lambda: int(zstage.homeMa) == 200), f"homeMa={zstage.homeMa}")
    # 越界值要被桥**拦在本地**（界面比固件宽 = 用户能填一个必然被拒的值）
    r.check("超出 1~3000 的限位电流被桥本地拒绝（不会发出去）",
            zstage.setHomeParams(400, 5000, 12000) is False
            and not any(c == "zset home 400 5000 12000" for c in zserver.board.commands),
            repr(zstage.lastError))
    ma_row.setProperty("text", "200")
    r.pump(0.2)

    # ── 自动回零的方向（只存上位机）──────────────────────────
    print("=== 8a3) 自动回零方向：界面上选，发 zhome <方向> ===")
    r.check("默认方向是「向下」（出厂方向：往底座死点走，重力帮忙）",
            str(zstage.homeDir) == "down", f"homeDir={zstage.homeDir}")
    down_chip, up_chip = find("zHomeDirChip_down"), find("zHomeDirChip_up")
    r.check("方向芯片都在（向下/向上）", down_chip is not None and up_chip is not None)
    r.check("默认高亮「向下」", down_chip is not None
            and bool(down_chip.property("checked")) is True)
    up_chip.clicked.emit()
    r.check("点「向上」→ 桥里的方向跟着改（单向数据流）",
            r.wait_for(lambda: str(zstage.homeDir) == "up"), f"homeDir={zstage.homeDir}")
    r.check("换方向**不写板子**（板子里那个方向归「上电自动回零」）",
            not any(c.startswith("zautohome") for c in
                    [x for x in zserver.board.commands if "up" in x]),
            str([c for c in zserver.board.commands if c.startswith("zautohome")][:2]))
    zserver.board.home_cmds.clear()
    zstage.homeNow()
    r.check("自动回零照实发 `zhome up nowait`（原来写死 down）",
            r.wait_for(lambda: any(c.startswith("zhome up") for c in zserver.board.commands), 3),
            str([c for c in zserver.board.commands if c.startswith("zhome")][:2]))
    zstage.stopNow()
    r.pump(0.3)
    find("zHomeDirChip_down").clicked.emit()
    r.pump(0.2)
    # 恢复折叠状态：后面的用例建立在"设置节收着"的基础上（展开是第 14 节干的活）
    page.setProperty("_zSetupOpen", False)
    r.pump(0.3)

    print("=== 8b) 闸门阶梯③：应用软限位 → 闸门全开 ===")
    lo, hi = find("zLimitLoField"), find("zLimitHiField")
    r.check("软限位输入框可访问", lo is not None and hi is not None)
    lo.setProperty("text", "0")
    hi.setProperty("text", "240")
    zserver.board.commands.clear()
    find("zApplyButton").clicked.emit()
    r.check("板子收到 zlim 0 240",
            r.wait_for(lambda: any(c.startswith("zlim") for c in zserver.board.commands), 3),
            str([c for c in zserver.board.commands if c.startswith("zlim")][:1]))
    r.check("桥记住的软限位也更新了",
            abs(float(zstage.limitHi) - 240.0) < 0.01, f"limitHi={zstage.limitHi}")
    r.check("闸门全开：canMove 变 True", r.wait_for(lambda: zstage.canMove is True))
    r.pump(0.2)
    # ⚠ 判据是"**不显示**"而不是"文本为空"：那一行的文案是恒定前缀 "⚠ " + 原因，
    #   没有原因时它整体隐藏。拿文本判空会得到一个永远为假的假断言。
    r.check("提示清空（桥的 datumHint 为空且那一行隐藏）",
            str(zstage.datumHint) == "" and bool(hint.property("visible")) is False,
            f"datumHint={zstage.datumHint!r} visible={hint.property('visible')}")
    r.check("向上按钮可用了", bool(up.property("enabled")) is True)

    print("=== 9) 步长 + 点动（步长选项来自 stepChoices）===")
    # ⚠ Repeater 生成的 delegate **不在 QObject 树里**（Qt 6.11 的 QQmlDelegateModel
    #   把 delegate 放在内容项里），`findChild(QObject, "zStepChip_10")` **找不到**它 ——
    #   必须走**可视 item 树**（QQuickItem.childItems()）。这是实测出来的（见报告），
    #   不知道这条的话会误判成"控件没生成"。
    def chips():
        row = find("zStepRow")
        if row is None or not isinstance(row, QQuickItem):
            return {}
        return {c.objectName(): c for c in row.childItems() if c.objectName()}

    cmap = chips()
    r.check("步长按钮按 stepChoices 生成（个数一致）",
            len(cmap) == len(zstage.stepChoices),
            f"chips={sorted(cmap)} choices={zstage.stepChoices}")
    r.check("有 10mm 那一档", "zStepChip_10" in cmap, str(sorted(cmap)))
    if "zStepChip_10" in cmap:
        cmap["zStepChip_10"].clicked.emit()
        r.pump(0.2)
        r.check("选中步长即 setStep（桥里变成 10）",
                abs(float(zstage.step) - 10.0) < 1e-6, f"step={zstage.step}")
    zserver.board.commands.clear()
    up.clicked.emit()
    got = r.wait_for(lambda: any(c.startswith("zup") for c in zserver.board.commands), 3)
    ups = [c for c in zserver.board.commands if c.startswith("zup")]
    r.check("板子收到 zup", got, str(ups[:1]))
    if ups:
        parts = ups[0].split()
        r.check("zup 带上了步长 10mm", abs(float(parts[1]) - 10.0) < 0.01, ups[0])
        # ⚠ 点动只有两个参数：固件的 cmd_zup/cmd_zdown **不读第三个参数**（acc 走板子
        #   自己的 s_p.acc）。发 `... 300 100` 会让日志以为 acc 生效了 —— 那是假象。
        r.check("zup 带上了 rpm（且**不带** acc —— 固件不读它）",
                len(parts) == 3 and int(parts[2]) > 0, ups[0])
    r.check("遥测高度跟到 10mm", r.wait_for(lambda: abs(zstage.z - 10.0) < 0.5),
            f"z={zstage.z}")
    r.wait_for(lambda: not zstage.moving, 5)

    print("=== 10) 绝对定位 ===")
    zserver.board.commands.clear()
    tgt = find("zTargetInput")
    tgt.setProperty("text", "100")
    mv = find("zMoveToButton")
    mv.clicked.emit()
    got = r.wait_for(lambda: any(c.startswith("zmove") for c in zserver.board.commands), 3)
    moves = [c for c in zserver.board.commands if c.startswith("zmove")]
    r.check("板子收到 zmove 100mm",
            got and abs(float(moves[0].split()[1]) - 100.0) < 0.01, str(moves[:1]))
    r.check("遥测高度跟到 100mm", r.wait_for(lambda: abs(zstage.z - 100.0) < 0.5),
            f"z={zstage.z}")

    print("=== 11) 急停：**运动中也要可点**（本项目铁律）===")
    # 造一个"一直在动"的状态：把到位判定拉长，moving 就不会自己归 0
    zserver.board.move_polls_needed = 10_000
    r.wait_for(lambda: not zstage.moving, 5)
    up.clicked.emit()
    r.check("已进入运动状态", r.wait_for(lambda: zstage.moving is True),
            f"moving={zstage.moving}")
    r.check("运动中：急停仍然可点（enabled 只跟 connected 走）",
            bool(stopb.property("enabled")) is True,
            f"stop.enabled={stopb.property('enabled')}")
    r.check("运动中：绝对定位被禁用", bool(mv.property("enabled")) is False)
    zserver.board.commands.clear()
    stopb.clicked.emit()
    got = r.wait_for(lambda: "stop all" in zserver.board.commands, 3)
    r.check("板子收到 stop all（急停排在最前面）", got, str(zserver.board.commands[-1:]))
    r.check("急停后不再显示运动中", r.wait_for(lambda: not zstage.moving))
    zserver.board.move_polls_needed = 1

    print("=== 12) 公用协议显示框（用户特别要求的那块）===")
    # 2026-09-28：协议框改成**两块板子公用**（ProtoHub 汇总两路，带 [XY]/[Z] 前缀）。
    # 所以这里比对的是**合并流**，不是某一块桥自己的 protoLines。
    lst = find("protoList")
    r.check("协议列表可访问", lst is not None)
    r.check("协议框已经攒下收发记录", len(proto_hub.lines) >= 3,
            f"{len(proto_hub.lines)} 行")
    # ⚠ 协议框是**节流发射**的（PROTO_EMIT_MS=80ms；ProtoHub 也一样）——
    #   要比对就得先让界面追上。
    r.wait_for(lambda: len(as_list(lst.property("model"))) == len(proto_hub.lines), 2)
    model = as_list(lst.property("model"))
    r.check("ListView 的 model 长度 == 合并流的行数（等节流追上）",
            len(model) == len(proto_hub.lines),
            f"model={len(model)} hub={len(proto_hub.lines)}")
    r.check("ListView 真的建出了 delegate（count 一致）",
            int(lst.property("count")) == len(proto_hub.lines),
            f"count={lst.property('count')}")
    r.check("合并行带来源前缀（[Z]）",
            all(str(l).startswith("[") for l in model),
            str(model[:2]))
    r.check("协议行里有发出去的「→」和收回来的「←」",
            any("→" in str(l) for l in model) and any("←" in str(l) for l in model),
            str(model[:2]))

    print("=== 12b) 驱动器帧镜像开关（@TX/@RX）===")
    tr = find("protoTraceSwitch")
    r.check("协议框里有帧镜像开关", tr is not None)
    n_before = len(zstage.protoLines)
    tr.setProperty("on", True)
    tr.toggled.emit()
    r.check("板子收到 trace on",
            r.wait_for(lambda: any(c == "trace on" for c in zserver.board.commands), 3),
            str([c for c in zserver.board.commands if c.startswith("trace")][:1]))
    r.check("桥也认为帧镜像开着", r.wait_for(lambda: zstage.traceOn is True))
    r.wait_for(lambda: len(zstage.protoLines) > n_before, 4)
    zstage.sendCommand("pos")
    r.check("开了镜像后协议框里出现 @TX/@RX 驱动器原始帧",
            r.wait_for(lambda: any("@TX" in str(l) for l in zstage.protoLines), 4),
            str([l for l in zstage.protoLines if "@" in str(l)][:1]))

    print("=== 12c) 帧镜像的代价：暂停记录 / 清空 ===")
    ps = find("protoPauseSwitch")
    ps.setProperty("on", True)
    ps.toggled.emit()
    r.pump(0.3)
    r.check("桥进入暂停记录", bool(zstage.protoPaused) is True)
    n_paused = len(zstage.protoLines)
    zstage.sendCommand("pos")
    r.pump(1.0)
    r.check("暂停后不再追加新行（收发照常）", len(zstage.protoLines) == n_paused,
            f"{n_paused} → {len(zstage.protoLines)}")
    # ⚠ 清空要在**暂停着**的时候断言：否则下一次 json 轮询（1Hz）立刻又塞进一行，
    #   "清空后必须是 0 行"就成了随机失败的假断言。
    clr = find("protoClearButton")
    clr.clicked.emit()
    r.pump(0.3)
    r.check("清空后协议框为空（暂停状态下）", len(zstage.protoLines) == 0,
            f"{len(zstage.protoLines)} 行")
    ps.setProperty("on", False)
    ps.toggled.emit()
    r.pump(0.2)

    print("=== 12d) 自定义命令框 ===")
    cmdf = find("protoCommandInput")
    sendb = find("protoSendButton")
    r.check("命令框与发送按钮可访问", cmdf is not None and sendb is not None)
    zserver.board.commands.clear()
    cmdf.setProperty("text", "zcfg")
    sendb.clicked.emit()
    r.check("板子收到了自定义命令 zcfg",
            r.wait_for(lambda: "zcfg" in zserver.board.commands, 3),
            str(zserver.board.commands[-2:]))
    r.check("发送后输入框被清空", str(cmdf.property("text")) == "",
            repr(cmdf.property("text")))
    zserver.board.commands.clear()
    cmdf.setProperty("text", "ver all")
    cmdf.accepted.emit()
    r.check("回车也能发出去",
            r.wait_for(lambda: "ver all" in zserver.board.commands, 3),
            str(zserver.board.commands[-2:]))

    print("=== 13) 故障显示（固件报出来的才显示）===")
    fault_box = find("zFaultText")
    r.check("找到故障文本框", fault_box is not None)
    if fault_box is not None:
        r.check("正常时故障框不显示",
                fault_box.property("visible") in (False, None, 0),
                f"visible={fault_box.property('visible')}")
        zserver.board.fault = "skew"
        r.check("故障文字跟出来（两侧高差超限）",
                r.wait_for(lambda: len(zstage.faultText) > 0, 4),
                repr(zstage.faultText))
        r.pump(0.3)
        r.check("故障框变成可见", bool(fault_box.property("visible")) is True,
                f"visible={fault_box.property('visible')}")
        zserver.board.fault = "none"
        r.pump(0.5)

    # ============================================================
    print("=== 14) 几何守卫：Z 区每一行都不许撑破卡片 ===")
    # AGENTS.md 第 9 条：一行不收缩的长文本会把整列撑宽，把**同一列其它行**的
    # 右侧控件顶出卡片（真实事故：ComboRow 的下拉框、SwitchRow 的开关全不见了）。
    # 所以这里**量**两个东西：① 卡片内容区里每一行 ≤ 420；
    # ② 卡片内任何可见控件的右边界都在卡片内。
    # ⚠ 走**可视 item 树**（QQuickItem.childItems()）而不是 QObject 树：
    #   Repeater/ListView 的 delegate 在 QObject 树里根本查不到（见第 9 段），
    #   用 findChildren 会漏掉它们 —— 而"漏掉"在这里就等于"没守住"。
    def walk(item):
        yield item
        for c in item.childItems():
            yield from walk(c)

    for card_name, body_name, label in (
            ("zTelemetryPanel", "zTelemetryBody", "Z 状态读数（抽屉里）"),
            ("zJogPanel", "zJogBody", "手动控制卡"),
            ("protoPanel", "protoBody", "协议框"),
            ("zSetupPanel", "zSetupBody", "设置面板")):
        card = find(card_name)
        body = find(body_name)
        if card is None or body is None or not isinstance(card, QQuickItem) \
                or not isinstance(body, QQuickItem):
            r.check(f"{label}: 找得到卡片与内容区", False)
            continue
        # 2026-09-28 三列版面：Z 状态读数在右列「状态」折叠节、Z 设置在右列
        # 「设置」折叠节、协议框在中列「高级」里 —— 量之前把它们都展开，
        # 否则量到的是"没东西"而不是"没撑破"。
        page.setProperty("_advExpanded", True)
        page.setProperty("_zStatusOpen", True)
        page.setProperty("_zSetupOpen", True)
        r.pump(0.5)
        worst_row, worst_name = 0.0, ""
        for ch in body.childItems():         # 内容区的**直接**子行
            if not ch.isVisible():
                continue
            if float(ch.width()) > worst_row:
                worst_row = float(ch.width())
                worst_name = ch.objectName() or ch.metaObject().className()
        limit = row_limit(body)
        r.check(f"{label}: 每一行都不超出内容区（{limit:.0f}px）", worst_row <= limit,
                f"最宽一行 {worst_row:.0f}px ({worst_name})")
        cw = float(card.property("width"))
        out = []
        for ch in walk(card):
            if ch is card or not ch.isVisible():
                continue
            w = float(ch.width())
            # 满宽背景（宽度 == 卡片宽）不算"被顶出去"
            if w >= cw - 1.0:
                continue
            p = ch.mapToItem(card, QPointF(0, 0))
            if p.x() + w > cw + 1.0:
                out.append(f"{ch.objectName() or ch.metaObject().className()}"
                           f"(x={p.x():.0f} w={w:.0f} 右={p.x() + w:.0f})")
        r.check(f"{label}: 卡片内没有控件超出右边界（卡片宽 {cw:.0f}）", not out,
                "; ".join(out[:3]))
        # ⚠ 高度也要量：只量宽度会漏掉"卡片塌成一条、内容全被裁掉"那种错
        #   （真发生过：implicitHeight 挂到 Rectangle 上 → 整张卡只剩 32px）。
        chh = float(card.property("height"))
        r.check(f"{label}: 卡片高度正常（内容没被裁掉）", chh >= 80.0,
                f"height={chh:.0f}")

    # ============================================================
    print("=== 14b) 界面只留动态信息：删掉的静态说明不许长回来 ===")
    # 用户 2026-09-28 原话："只把最常用的操作留在上位机上，细节/测试留在后端控制台。"
    # 于是删掉了 20 多段贴在界面上的说明文字 —— 它们全都能在 docs/17、docs/18、
    # 板子控制台的 help 里查到。留下的是**当下状态**（卡片状态行 / gateHint）与
    # **该怎么做**（故障处置、未配置地址）。
    # 这条用"禁止短语"钉住：删了又长回来时这里会红。不写这条的话，半年后
    # 没人记得"当初为什么删" —— 然后一条条又贴回去了（AGENTS.md 第 18 条同一个道理）。
    # ⚠ 「上电自动回零」2026-09-29 从这个黑名单里**移除**了：它不再是"删掉的静态
    #   说明"，而是用户要求加回来的**控件**（开关的 label 就是这四个字，会命中短语
    #   检查）。黑名单盯的是"解释性段落"，不是"控件标签" —— 两个混在一起会逼着
    #   下一个人把控件改名来骗测试。
    banned = ["关节读数", "方向符号 %1", "固件实际在用的软限位", "可选路径",
              "json 是轮询命令", "暂停记录」只停界面追加", "自定义命令的语法",
              "每点一次走一个步长", "超出软限位的目标"]
    texts = []
    for t in page.findChildren(QObject):
        if "Text" in t.metaObject().className():
            v = t.property("text")
            if v is not None:
                texts.append(str(v))
    hit = [b for b in banned if any(b in x for x in texts)]
    r.check("已删的说明段落没有留在界面上", not hit, str(hit))

    # ============================================================
    print("=== 15) 版面形态：Z 是右列 + 右半边控制卡（2026-09-28 三列手绘稿）===")
    # ⚠ 这条断言**换过三次内容**了，别当成"以前也这么写"：
    #   第一版：Z 串在 X/Y 那一列里、位于 X/Y 设置之后手动控制之前；
    #   第二版（2026-09-27）：两台板子各占一列、各自滚；
    #   第三版（2026-09-28 手绘稿）：左右滑出抽屉 + 操作卡并排；
    #   第四版（2026-09-28 第二张手绘稿）：**三列**——左右两条窄列（连接卡→状态
    #     折叠→读数→设置折叠→设置面板）+ 中列（预览→控制卡并排→「高级」）。
    # ⚠ 必须**显式设窗口宽**再断言：offscreen 平台的屏幕只有 800×800，
    #   窗口创建时会被裁（踩过一次：断言全红，一度以为是布局没生效）。
    win.setProperty("width", 1680)
    win.setProperty("height", 1040)
    # ⚠ 先复位：第 14 步的几何守卫把折叠节和「高级」都打开过（那是量几何需要的）
    page.setProperty("_zStatusOpen", False)
    page.setProperty("_xyStatusOpen", False)
    page.setProperty("_zSetupOpen", False)
    page.setProperty("_xySetupOpen", False)
    page.setProperty("_advExpanded", False)
    r.pump(0.5)
    page_scroll = find("pageScroll")
    z_fold, z_body = find("zStatusFold"), find("zStatusBody")
    xy_fold = find("xyStatusFold")
    # ⚠ 2026-09-28：状态**只在连接卡里**（用户："状态只在卡片上显示"），
    #   独立的 StatusLine 已删 —— 所以这里找的是 Z 连接卡本身。
    z_card = find("zControllerCard")
    r.check("找到整页滚动容器 / 两个状态折叠节 / Z 连接卡（闸门在它状态行里）",
            page_scroll is not None and xy_fold is not None and z_fold is not None
            and z_card is not None and find("zStatusLine") is None)
    if page_scroll is not None and xy_fold is not None and z_fold is not None:
        r.check("Z 状态折叠节默认收起（用户：「默认不打开」）",
                page.property("_zStatusOpen") is False,
                f"open={page.property('_zStatusOpen')}")
        r.check("Z 折叠节在 X/Y 折叠节右边（左右两条窄列各一个）",
                float(z_fold.mapToItem(page, QPointF(0, 0)).x())
                > float(xy_fold.mapToItem(page, QPointF(0, 0)).x()) + 1,
                f"xyX={xy_fold.mapToItem(page, QPointF(0, 0)).x():.0f} "
                f"zX={z_fold.mapToItem(page, QPointF(0, 0)).x():.0f}")
        # 打开 Z 折叠节 → 里面的读数（高度/偏斜）要出来且不被裁
        page.setProperty("_zStatusOpen", True)
        r.pump(0.5)
        tele = find("zTelemetryPanel")
        r.check("打开后 Z 读数在折叠节内、宽高都没被裁",
                tele is not None and z_body is not None
                and float(tele.property("width")) <= float(z_body.property("width")) + 1
                and float(tele.property("height")) <= float(z_body.property("height")) + 1,
                f"tele={tele.property('width') if tele else '?'}x"
                f"{tele.property('height') if tele else '?'} "
                f"body={z_body.property('width') if z_body else '?'}x"
                f"{z_body.property('height') if z_body else '?'}")
        page.setProperty("_zStatusOpen", False)
        r.pump(0.3)
        # 两张控制卡并排（左 X/Y、右 Z）—— 手绘稿里它们都在中列那一行
        xy_jog, z_jog = find("jogPanel"), find("zJogPanel")
        if xy_jog is not None and z_jog is not None:
            from PySide6.QtCore import QPointF as _P
            xj = xy_jog.mapToItem(page, _P(0, 0))
            zj = z_jog.mapToItem(page, _P(0, 0))
            r.check("两张手动控制卡并排（Z 在右半边）", zj.x() > xj.x() + 1,
                    f"xyX={xj.x():.0f} zX={zj.x():.0f}")
    win.setProperty("width", 1400)
    win.setProperty("height", 900)
    r.pump(0.3)

    print("=== 16) 断开：Z 区回到禁用 + 说明原因 ===")
    zstage.disconnectDevice()
    r.check("已断开", r.wait_for(lambda: not zstage.connected))
    r.pump(0.3)
    r.check("断开后向上按钮禁用", bool(up.property("enabled")) is False)
    r.check("断开后协议框提示未连接",
            "未连接" in str(find("protoHint").property("text")),
            repr(find("protoHint").property("text")))
    # X/Y 全程没被 Z 影响过
    r.check("全程 X/Y 保持未连接（互不影响）", not stage.connected)

    zserver.close()
    print()
    if r.fails:
        print(f"FAILED {len(r.fails)}: {r.fails}")
        return 1
    print("ZStagePage（Z 轴那一区）冒烟测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
