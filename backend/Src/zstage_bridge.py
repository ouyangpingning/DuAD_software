"""ZStageBridge — Z 轴双丝杠升降平台（ESP32 + 两台 PD42S1 闭环驱动器）的 WiFi 控制桥。

对应的固件在 `研究生毕业设计/esp/Zstage`；协议与固件侧的安全设计见
`docs/18-Z轴平台控制.md`。

协议（行分隔文本，与板子的 USB 控制台**语法完全一致**）：
    连接后第一行必须是口令          → 板子回 `#OK auth`
    之后每行 = 一条命令              → 板子回：命令输出 + 末行 `#OK` 或 `#ERR <code>`
    空闲时用 `json` 轮询            → 一行 JSON 拿全状态

**单位**：这个桥**不做任何换算** —— Z 固件对外就是毫米（mm），界面也是毫米。
（对比 `stage_bridge.py`：那边固件是"电机度"，所以有个 11.25 的换算边界。
 这里没有这一层，别照着抄一个假的换算出来。）

安全相关的四条（都来自固件侧的设计，UI 必须配合）：
  1. 移动一律用**非阻塞**的 `zup` / `zdown` / `zmove`，绝不用会等到位的
     `zrel` / `zabs` —— 否则一条急停会被几秒的长移动堵在通道里，等于没有急停。
  2. 固件对绝对定位是 **fail-closed**：没有基准、或没设软限位，就直接拒绝。
     UI 必须把原因显示出来（`datumHint`），否则用户只看到"点了没反应"。
  3. `stop all` **永远可点**，它的 enabled 只跟 `connected` 走。
  4. 方向符号（`zsign`）错的时候两轴会**对着拧**平台。固件侧的运动中偏斜看门狗
     会刹住，但界面必须把符号显示出来（`signText`）让人一眼能核对。

**协议显示框**：`protoLines` 记录**每一条**发出去和收回来的行（含 `json` 轮询），
以及固件开了 `trace` 之后镜像回来的 PD42S1 原始帧（`@TX` / `@RX`）——
从"界面命令"到"驱动器帧"两层都能看到。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from PySide6.QtCore import (QObject, QSettings, QTimer, Property, Signal, Slot)
from PySide6.QtNetwork import QAbstractSocket, QTcpSocket

# ── 轮询节奏（与 stage_bridge 一致：空闲 1Hz、运动中 5Hz）────────────
POLL_IDLE_MS = 1000
POLL_MOVING_MS = 200

# ── 命令超时 ──────────────────────────────────────────────────────
# 本桥只发非阻塞命令（立即回话），所以 3 秒足够。
# ⚠ 但**自定义命令框**能敲任何命令，其中有几条是几十秒级的：
#   `hcalib`/`hfactory`（驱动器识别电机，固件给到 30s）、阻塞版 `zrel`/`zabs`/`zhome`。
#   超时短于真实耗时 → 迟到应答会与**下一条**命令配对错位（丢一帧遥测，
#   甚至把上一条的失败原因写在下一条命令名下）。所以按命令头给一张表。
CMD_TIMEOUT_MS = 3000
SLOW_CMD_TIMEOUT_MS = {
    "hcalib": 30000, "hfactory": 30000, "save": 15000, "hauto": 10000,
    "zrel": 30000, "zabs": 30000, "zhome": 60000, "ztilt": 30000,
    # ⚠ `stop all` 不在"立即回话"那一档里：固件的打断路径最坏要
    #   ① 0x93 打断（2 次 × 250ms × 2 轴）+ ② 刹车（150ms + 500ms×2 每轴）
    #   ≈ 2.5s，而**原来 3s 的默认超时比它还短** —— 迟到的 `#OK` 会与下一条命令
    #   配对错位（这个文件顶部就把它列为事故），界面还会挂一条假的"急停超时"。
    #   给到 8s：留够重发窗口(HOME_ABORT_BUDGET_MS 1.5s) + 总线争用的余量。
    "stop": 8000,
}
# `zzero` 要读两次位置再写驱动器零点，比普通命令慢；给宽一点。
ZERO_TIMEOUT_MS = 8000

# 建连超时：连一个**不存在**的 IP 时 TCP 会一直重传 SYN、Qt 自己不超时，
# 现象是卡片永远转"正在连接…"、永不报错。
CONNECT_TIMEOUT_MS = 8000

DEFAULT_PORT = 3333
DEFAULT_HOST = ""        # 故意留空：填一个像模像样的假 IP 会让人以为已经配好了
DEFAULT_TOKEN = ""

# ── 速度/加减速的上限：**必须以固件为准** ──────────────────────────
# Z 固件 `console_cmds.c::clamp_rpm()` 把速度夹到 `BOARD_Z_MAX_RPM = 1200`，
# 超了会**按 1200 执行并在输出里加一行说明**（不是拒绝，但界面给出的值会骗人）。
# 所以界面上限就是 1200 —— 界面**不能**给出固件会改掉的值。
# ⚠ 驱动器的协议上限是 6000（`pd42s1.c::cmd_move`），但那是**另一层**的上限：
#   这里不重复声明它（第 19/22 条：跨层常量只许有一份，重复的必然漂移）。
UI_MAX_RPM = 1200        # = 固件 BOARD_Z_MAX_RPM（8mm 导程上 1200rpm = 160mm/s）
MAX_ACC = 200            # 固件 `zset acc` 的上限
# ⚠ 加减速档：**数值越大加减速越大，且 0 = 直接启动（无斜坡）**（手册原文）。
#   所以 1 才是"最柔和"，0 必须排除 —— 见 UI_MIN_ACC。
UI_MIN_ACC = 1
UI_MAX_ACC = MAX_ACC

# 出厂默认（与固件 `board_config.h` 一致：300rpm / acc 100）
DEFAULT_RPM = 300
DEFAULT_ACC = 100

# ── 无限位回零的参数（固件 `zset home <rpm> <mA> <timeout_ms>`，存板子 NVS）────
# ⚠ 范围与固件 `cmd_zset` 的检查**逐条对齐**（super 1~6000 / mA 1~3000），
#   这些数字只许有这一份：界面比固件宽 = 用户能填一个必然被拒的值。
HOME_RPM_MIN, HOME_RPM_MAX = 1, 6000
HOME_MA_MIN, HOME_MA_MAX = 1, 3000
HOME_TMO_MIN, HOME_TMO_MAX = 1000, 120000
# 经验甜点区：低于空转电流(≈40mA)会「一动就报完成」（假成功），
# 高于堵转电流(≈1500~2700mA)永远不触发（假失败）。固件在 `zset home` 里
# 会用同样两条线打警告 —— 界面在**填的当下**就把话说清楚，别等下发完才说。
# ⚠⚠ 2026-09-29 现场实测把本机的经验区改小了：**100mA 能正常判到位**，而
#   **200~250mA 会「顶到驱动器超时也不触发」** —— 说明这台机器顶住时相电流只有
#   一百多 mA（不是 42 电机手册口径的 600mA 级）。早先的 300~1500 是从别处抄的
#   经验值，**实测被否掉**。现在与固件 `board_config.h` 的 60~300mA 对齐。
HOME_MA_SWEET_LO, HOME_MA_SWEET_HI = 60, 300
# 出厂默认（= 固件 `board_config.h`：BOARD_Z_HOME_RPM 400 / MA 100 / TMO 12000）
DEFAULT_HOME_RPM = 400
DEFAULT_HOME_MA = 100
DEFAULT_HOME_TMO = 12000

# 回零方向（`zhome up|down`）。⚠ 它是**上位机侧**的选择（存 QSettings），
#   不写板子：板子上的方向（`home_dir`）只归「上电自动回零」用 —— 用户明确
#   要求这两条路径分开，别把它们悄悄合并。
HOME_DIRS = ("down", "up")
DEFAULT_HOME_DIR = "down"

# 软限位默认值（固件出厂 = 0 ~ 250mm，对应 300mm 丝杠的 250mm 有效行程）。
DEFAULT_Z_LO = 0.0
DEFAULT_Z_HI = 250.0
# 固件 `zlim` 的检查：上限必须大于下限，且行程不超过 1000mm。
Z_LIMIT_SPAN_MAX = 1000.0

# 点动步长（mm）——与二轴平台页面同一组手感值。
# ⚠ 上限 20mm 是**固件的硬限制**：`zup`/`zdown` 里 `if (mm > 20.0f)` 直接拒绝。
#   给到 50 的话那一档点下去**永远是被拒绝**——界面上就是"点了没反应"。
STEP_CHOICES = (0.1, 1.0, 10.0, 20.0)
DEFAULT_STEP = 1.0

# 协议框的封顶与节流语义 2026-09-28 抽到了 `Src/proto_log.py`（两块板子共用一套，
# 因为协议框改成公用的了）。这里仍然把常量转出来：既有测试与调用点按名字引用它们。
from Src.proto_log import PROTO_CAP, PROTO_EMIT_MS, ProtoLog   # noqa: E402
# 诊断日志同样封顶（阶段一只有几百行，但连接一挂就是几小时）
LOG_CAP = 300

# `zsign` 的读回格式（固件：`  当前方向符号 sa=-1 sb=-1 —— ...`）
_SIGN_RE = re.compile(r"sa=([+-]?\d+)\s+sb=([+-]?\d+)")

# 固件 json 的 fault 字段 → 界面文字
FAULT_TEXT = {
    "none": "",
    "overtravel": "超程保护动作过（越过软限位外沿）",
    "skew": "两侧高差超限（两轴在对着使劲）",
    "runaway": "位置/驱动器状态异常（失控保护）",
}


def _fnum(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _inum(value: Any, fallback: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _axis_fault_text(err: str) -> str:
    """把固件 `{"err":"left=ESP_ERR_TIMEOUT right=ok"}` 翻成一句人话。

    ⚠ 只在**两边都不正常**时才提超时码；一边好一边坏时只说坏的那边 ——
    现场那条红字原来是 `{"err":"pos read failed: left=ESP_ERR_TIMEOUT right=ESP_OK"}`，
    用户看不懂"ESP_ERR_TIMEOUT"更不知道该做什么。
    """
    bad = []
    for part in err.replace("pos read failed:", "").split():
        if "=" not in part:
            continue
        name, val = part.split("=", 1)
        v = val.strip().lower()
        # 固件可能回 `ok` 也可能回 `ESP_OK`（读成功时按轴回不同的字面量）——
        # 判"好"用**包含 ok**，别写成等于。
        if "ok" in v or v in ("1", "0"):
            continue
        bad.append("左轴" if name.strip().startswith("left") else
                   "右轴" if name.strip().startswith("right") else name.strip())
    if not bad:
        return "驱动器没有应答"
    return "、".join(bad) + "没有应答"


def _bool(value: Any, fallback: bool = False) -> bool:
    """QSettings 存布尔会因平台不同读回 "true"/"1"/True —— 统一在这儿认。

    ⚠ 不能只用 `bool(value)`：QSettings 在 Windows 注册表里读回来是字符串 "false"，
    而 `bool("false")` 是 **True** —— 一个静默的反向 bug。
    """
    if value is None:
        return fallback
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off", ""):
        return False
    return fallback


class ZStageBridge(QObject):
    """QML 可调用的 Z 轴平台桥。"""

    # ── 连接状态 ──────────────────────────────────────────
    connectedChanged = Signal()
    connectingChanged = Signal()
    lastErrorChanged = Signal()
    logMessage = Signal(str)              # 诊断面板的日志行（人看的摘要）

    # ── 遥测（来自 json 轮询）─────────────────────────────
    telemetryChanged = Signal()
    movingChanged = Signal()

    # ── 协议显示框（每一条收发原文）───────────────────────
    protoChanged = Signal()
    logChanged = Signal()

    def __init__(self, parent=None, settings=None):
        """settings 可注入 —— **测试必须传一个文件隔离的 QSettings**。

        默认是 `QSettings("DuAD","DuADSoftware")`，和真实程序同一个作用域；
        测试若直接用默认值，会把假板子的地址/端口/口令写进用户的真实配置，
        下次用户打开页面看到的是假板子的值 → 表现成"点了没反应"。
        （这个坑在 stage_bridge 上踩过一次，见 AGENTS.md「两条踩过的坑」第 1 条。）
        """
        super().__init__(parent)

        self._sock = QTcpSocket(self)
        self._sock.connected.connect(self._on_connected)
        self._sock.disconnected.connect(self._on_disconnected)
        self._sock.readyRead.connect(self._on_ready_read)
        self._sock.errorOccurred.connect(self._on_socket_error)

        self._settings = settings if settings is not None else QSettings("DuAD", "DuADSoftware")

        self._host = str(self._settings.value("zstage/host", DEFAULT_HOST))
        self._port = _inum(self._settings.value("zstage/port", DEFAULT_PORT), DEFAULT_PORT)
        self._token = str(self._settings.value("zstage/token", DEFAULT_TOKEN))
        # ⚠ 从 QSettings 读回来也要**夹取**：手改过 ini（或旧版本写下的值）可能是
        #   `acc=0` —— 手册里 0 是"直接启动、无斜坡"，界面明令排除，但它会原样进
        #   每一条运动命令（`_motion_cmd` 直接用 self._acc）。老用户的 ini 里可能就有。
        self._rpm = min(max(_inum(self._settings.value("zstage/rpm", DEFAULT_RPM), DEFAULT_RPM), 1),
                        UI_MAX_RPM)
        self._acc = min(max(_inum(self._settings.value("zstage/acc", DEFAULT_ACC), DEFAULT_ACC),
                            UI_MIN_ACC), UI_MAX_ACC)
        self._step = _fnum(self._settings.value("zstage/step", DEFAULT_STEP), DEFAULT_STEP)
        self._lim_lo = _fnum(self._settings.value("zstage/lim_lo", DEFAULT_Z_LO), DEFAULT_Z_LO)
        self._lim_hi = _fnum(self._settings.value("zstage/lim_hi", DEFAULT_Z_HI), DEFAULT_Z_HI)
        # 回零方向（只存本机，见 HOME_DIRS 的注释）。⚠ 也要认老值/手改的 ini：
        #   不认的值一律回落到出厂方向，别把一个乱字符串拼进 `zhome <dir> nowait`。
        hd = str(self._settings.value("zstage/home_dir", DEFAULT_HOME_DIR)).strip().lower()
        self._home_dir = hd if hd in HOME_DIRS else DEFAULT_HOME_DIR

        # ── 状态位 ──────────────────────────────────────
        self._connecting = False
        self._authenticated = False
        self._last_error = ""
        self._error_kind = None      # None / "transient" / "gate" / "action"
        self._error_gate = None      # kind == "gate" 时记是哪道闸
        self._error_src = ""         # 本地校验类错误属于哪一类操作
        self._rx_buf = b""

        # 遥测镜像（全部来自 json；单位 mm / V / dBm）
        self._z = 0.0
        self._skew = 0.0
        self._joint_a = 0            # counts（诊断用原始值）
        self._joint_b = 0
        self._voltage = 0.0
        self._enabled = False
        self._datum = False
        self._lim_set = False
        self._json_lo = DEFAULT_Z_LO     # 固件**实际**在用的软限位（json zmin/zmax）
        self._json_hi = DEFAULT_Z_HI
        self._moving = False
        self._last_move = "none"
        self._homing = 0
        self._fault = "none"
        self._target = 0.0
        self._autohome = False
        self._um_per_rev = 8000
        self._rssi = 0
        self._board_ip = ""
        # 回零参数：**板子实际在用的**（json 的 hma/hrpm/htmo）。
        # 值先按出厂默认填，连上后由 json 覆盖 —— 界面显示"板子当前生效"靠它。
        self._home_ma = DEFAULT_HOME_MA
        self._home_rpm = DEFAULT_HOME_RPM
        self._home_tmo = DEFAULT_HOME_TMO

        # 方向符号（`zsign` 读回来的；json 里没有这两个字段）
        self._sign_text = ""
        self._sign_pending = False

        # ── 命令队列（单在途：板子的 TCP 任务是串行的，发多了只会排更长的队）──
        self._queue: List[Dict[str, Any]] = []
        self._in_flight: Optional[Dict[str, Any]] = None
        self._resp_lines: List[str] = []

        # ── 协议显示框 / 日志 ──────────────────────────────
        # 协议行交给共用的 ProtoLog（封顶/节流/暂停都在那边，见 proto_log.py）；
        # 公用协议框通过 ProtoHub 直接读它，本桥只把 lines/paused 转发给 QML。
        self.proto_log = ProtoLog("Z", parent=self)
        self.proto_log.changed.connect(self.protoChanged.emit)
        self._log_lines: List[str] = []
        self._trace_on = False

        # 轮询定时器：认证通过后才跑
        self._poll = QTimer(self)
        self._poll.setInterval(POLL_IDLE_MS)
        self._poll.timeout.connect(self._poll_tick)

        self._cmd_timer = QTimer(self)
        self._cmd_timer.setSingleShot(True)
        self._cmd_timer.timeout.connect(self._on_cmd_timeout)

        self._connect_timer = QTimer(self)
        self._connect_timer.setSingleShot(True)
        self._connect_timer.timeout.connect(self._on_connect_timeout)

        self._log("ZStageBridge 就绪（单位：mm，与固件一致，不做换算）")

    # ============================================================
    # 内部工具
    # ============================================================
    def _log(self, msg: str):
        print(f"[ZStageBridge] {msg}")
        self._log_lines.append(msg)
        if len(self._log_lines) > LOG_CAP:
            del self._log_lines[: len(self._log_lines) - LOG_CAP]
        self.logChanged.emit()
        self.logMessage.emit(msg)

    def _proto_add(self, text: str):
        """往协议显示框追加一行（节流/封顶/暂停都在 ProtoLog 里）。"""
        self.proto_log.add(text)

    def _gate_error(self, gate: str, msg: str):
        """记录"某道闸没满足"的错误。`gate` ∈ {"datum","lim","setup"}。

        这类错误的特点是**条件一旦满足它就该消失** —— 否则会出现自相矛盾：
        状态条上「已立基准 / 已设软限位」全绿，下面却还挂着红色的
        "还没有基准：先设为原点"。用户看到矛盾会开始不信任整个界面。
        """
        self._error_gate = gate
        self._set_error(msg, kind="gate")

    def _set_error(self, msg: str, kind: str = "action", src: str = ""):
        """设置错误提示。

        `kind` 三种：
        - `"transient"` 传输类（超时 / socket / 断开）→ **链路恢复就自动清**。
        - `"gate"` 前置条件缺失（缺基准 / 未设软限位）→ **条件满足时自动清**。
        - `"action"`（默认）其它要用户处理的 → 不自动清（否则用户来不及看见原因）。

        `src` 是"这条提示属于哪一类操作"。本地参数校验类的错误（软限位填反了、
        校平超限、命令带换行）**只应由同类操作的成功来清** —— 见 `_clear_error_if`：
        用户改对了再点一次，红色横幅就该消失；否则界面会一直挂着一条与现状矛盾的
        提示（"行程超过 1000mm"）而实际设置早就正常了。
        """
        self._last_error = msg
        self._error_kind = kind if msg else None
        self._error_src = src if msg else ""
        if not msg:
            self._error_gate = None
        self.lastErrorChanged.emit()
        if msg:
            self._log(f"错误: {msg}")

    def _clear_error_if(self, src: str):
        """同类操作成功了 → 把之前那条本地校验错误收掉（别让它一直挂着）。"""
        if self._last_error and self._error_src == src:
            self._log("同类操作已成功，清掉之前那条参数提示")
            self._set_error("")

    def _maybe_clear_error(self):
        """拿到遥测/命令成功后调用：能自动清的提示就清掉。"""
        if not self._last_error:
            return
        if self._error_kind == "transient":
            self._set_error("")
            return
        if self._error_kind == "gate":
            g = self._error_gate
            satisfied = (
                (g == "datum" and self._datum)
                or (g == "lim" and self._lim_set)
                or (g is None and self._datum and self._lim_set)
            )
            if satisfied:
                self._log("前置条件已满足，清掉之前的闸提示")
                self._set_error("")

    # ============================================================
    # 连接状态属性
    # ============================================================
    def _get_connected(self) -> bool:
        return (self._sock.state() == QAbstractSocket.SocketState.ConnectedState
                and self._authenticated)

    connected = Property(bool, _get_connected, notify=connectedChanged)

    connecting = Property(bool, lambda self: self._connecting, notify=connectingChanged)
    lastError = Property(str, lambda self: self._last_error, notify=lastErrorChanged)

    # ============================================================
    # 设置类属性（存 QSettings）
    # ============================================================
    host = Property(str, lambda self: self._host, notify=telemetryChanged)
    port = Property(int, lambda self: self._port, notify=telemetryChanged)
    token = Property(str, lambda self: self._token, notify=telemetryChanged)
    rpm = Property(int, lambda self: self._rpm, notify=telemetryChanged)
    acc = Property(int, lambda self: self._acc, notify=telemetryChanged)
    uiMaxRpm = Property(int, lambda self: UI_MAX_RPM, notify=telemetryChanged)
    uiMaxAcc = Property(int, lambda self: UI_MAX_ACC, notify=telemetryChanged)
    uiMinAcc = Property(int, lambda self: UI_MIN_ACC, notify=telemetryChanged)
    step = Property(float, lambda self: self._step, notify=telemetryChanged)
    stepChoices = Property("QVariantList", lambda self: list(STEP_CHOICES),
                           notify=telemetryChanged)
    limitLo = Property(float, lambda self: self._lim_lo, notify=telemetryChanged)
    limitHi = Property(float, lambda self: self._lim_hi, notify=telemetryChanged)

    # ── 无限位回零的参数 ──────────────────────────────────
    # 量程/甜点区一律从桥里读（项目铁律：同一个数字出现在两处迟早漂移）。
    # QML 侧只做显示，不许再抄一份 1~3000 / 300~1500。
    homeDir = Property(str, lambda self: self._home_dir, notify=telemetryChanged)
    uiHomeRpmMin = Property(int, lambda self: HOME_RPM_MIN, notify=telemetryChanged)
    uiHomeRpmMax = Property(int, lambda self: HOME_RPM_MAX, notify=telemetryChanged)
    uiHomeMaMin = Property(int, lambda self: HOME_MA_MIN, notify=telemetryChanged)
    uiHomeMaMax = Property(int, lambda self: HOME_MA_MAX, notify=telemetryChanged)
    uiHomeMaSweetLo = Property(int, lambda self: HOME_MA_SWEET_LO, notify=telemetryChanged)
    uiHomeMaSweetHi = Property(int, lambda self: HOME_MA_SWEET_HI, notify=telemetryChanged)
    uiHomeTmoMin = Property(int, lambda self: HOME_TMO_MIN, notify=telemetryChanged)
    uiHomeTmoMax = Property(int, lambda self: HOME_TMO_MAX, notify=telemetryChanged)

    # ============================================================
    # 遥测属性
    # ============================================================
    z = Property(float, lambda self: self._z, notify=telemetryChanged)
    skew = Property(float, lambda self: self._skew, notify=telemetryChanged)
    jointA = Property(int, lambda self: self._joint_a, notify=telemetryChanged)
    jointB = Property(int, lambda self: self._joint_b, notify=telemetryChanged)
    voltage = Property(float, lambda self: self._voltage, notify=telemetryChanged)
    enabled = Property(bool, lambda self: self._enabled, notify=telemetryChanged)
    datum = Property(bool, lambda self: self._datum, notify=telemetryChanged)
    limitsSet = Property(bool, lambda self: self._lim_set, notify=telemetryChanged)
    firmwareLo = Property(float, lambda self: self._json_lo, notify=telemetryChanged)
    firmwareHi = Property(float, lambda self: self._json_hi, notify=telemetryChanged)
    moving = Property(bool, lambda self: self._moving, notify=movingChanged)
    lastMove = Property(str, lambda self: self._last_move, notify=telemetryChanged)
    homing = Property(int, lambda self: self._homing, notify=telemetryChanged)
    autohome = Property(bool, lambda self: self._autohome, notify=telemetryChanged)
    target = Property(float, lambda self: self._target, notify=telemetryChanged)
    umPerRev = Property(int, lambda self: self._um_per_rev, notify=telemetryChanged)
    rssi = Property(int, lambda self: self._rssi, notify=telemetryChanged)
    boardIp = Property(str, lambda self: self._board_ip, notify=telemetryChanged)
    signText = Property(str, lambda self: self._sign_text, notify=telemetryChanged)
    # **板子实际在用的**回零参数（json hma/hrpm/htmo）—— 与上面输入框里的
    # "待下发"值不同，界面要能同时显示两者（"我填的" vs "板子正在用的"）。
    homeMa = Property(int, lambda self: self._home_ma, notify=telemetryChanged)
    homeRpm = Property(int, lambda self: self._home_rpm, notify=telemetryChanged)
    homeTmo = Property(int, lambda self: self._home_tmo, notify=telemetryChanged)

    def _get_home_tmo_need_ms(self) -> int:
        """按**板子当前参数**估算"满行程回零大约要多久"（ms）—— 与固件 `home_travel_ms()` 同一套算法。

        ⚠ 它只是"够不够"的体检值，**不是控制判据**：真实回零距离通常远小于满行程。
        为什么要在界面上给（2026-09-29 现场实测）：用户把超时填成 4000ms，而本机满行程
        250mm 在 400rpm / 8mm 导程下要 ~4.7s（含加减速约 8s）—— 平台离死点较远时这次
        回零会在**中途**被驱动器判超时而放弃，现象只是"回零没跑完就停了"，
        从"失败"两个字里根本看不出是超时不够。
        """
        lead_mm = self._um_per_rev / 1000.0
        travel_mm = self._json_hi - self._json_lo
        rpm = self._home_rpm
        if lead_mm <= 0 or travel_mm <= 0 or rpm <= 0:
            return 0
        sec = travel_mm / lead_mm * 60.0 / rpm
        return int(sec * 1500.0) + 1000        # ×1.5（加减速）+ 1s 余量

    homeTmoNeedMs = Property(int, _get_home_tmo_need_ms, notify=telemetryChanged)

    def _get_fault(self) -> str:
        return self._fault

    fault = Property(str, _get_fault, notify=telemetryChanged)

    def _get_fault_text(self) -> str:
        return FAULT_TEXT.get(self._fault, self._fault if self._fault != "none" else "")

    faultText = Property(str, _get_fault_text, notify=telemetryChanged)

    def _get_can_move(self) -> bool:
        """现在能不能发运动命令（UI 用它决定按钮灰不灰）。

        ⚠ 必须是**带 notify 的 Property**，不能写成 `@Slot(result=bool)` 的函数：
        QML 绑定里调函数**没有依赖追踪**，只在创建时求值一次，之后就永远不刷新
        —— 症状是"连上了按钮还是灰的"，不报错、不警告（AGENTS.md 第 6 条坑）。
        """
        return self._get_connected() and self._datum and self._lim_set and not self._moving

    canMove = Property(bool, _get_can_move, notify=telemetryChanged)

    def _get_can_jog(self) -> bool:
        """能不能点动：只要连着、且当前没有运动在途。

        ⚠ 刻意**不**要求基准/软限位 —— 见 `_require_ready(need_datum=False)`。
        点动本身的危险由固件兜住（单次 ≤20mm + 关节常识闸 + 失控看门狗）。"""
        return self._get_connected() and not self._moving

    canJog = Property(bool, _get_can_jog, notify=telemetryChanged)

    def _get_datum_hint(self) -> str:
        """按钮为什么点不动 —— 在点击**之前**就写出来（本项目铁律）。"""
        if not self._get_connected():
            return "未连接 —— 点上面的 Z 轴平台卡片连接"
        if not self._datum:
            return ("还没立基准：把平台推到靠块/机械死点后点「设为原点」。"
                    "驱动器用单圈编码器，一掉 24V 就丢位置，每次上电都要重立一次。"
                    "（可以先用「向上 / 向下」点动把平台挪过去 —— 无基准时单次 ≤20mm、"
                    "软限位不生效，所以慢点走。）")
        if not self._lim_set:
            return "还没设软限位：在「Z 轴设置」里填 0 ~ 250 后点应用（固件是 fail-closed 的）。"
        return ""

    datumHint = Property(str, _get_datum_hint, notify=telemetryChanged)

    def _get_diag_text(self) -> str:
        """诊断面板用的一行摘要（含原始 counts，排查时才看）。"""
        if not self._get_connected():
            return "未连接"
        return ("Z {:.2f} mm  偏斜 {:.2f} mm  |  关节 A {} B {} counts  |  "
                "{:.1f}V  {}  |  导程 {:g}µm/圈  信号 {} dBm  板子 {}".format(
                    self._z, self._skew, self._joint_a, self._joint_b,
                    self._voltage, "已使能" if self._enabled else "未使能",
                    self._um_per_rev, self._rssi, self._board_ip or "?"))

    diagText = Property(str, _get_diag_text, notify=telemetryChanged)

    # ============================================================
    # 协议显示框 / 日志属性
    # ============================================================
    protoLines = Property("QVariantList", lambda self: self.proto_log.lines,
                          notify=protoChanged)
    logLines = Property("QVariantList", lambda self: list(self._log_lines), notify=logChanged)
    protoPaused = Property(bool, lambda self: self.proto_log.paused, notify=protoChanged)
    traceOn = Property(bool, lambda self: self._trace_on, notify=telemetryChanged)
    # 这块板子的固件有没有 `trace`（驱动器帧镜像）。X/Y 那块**没有** —— 公用协议框
    # 据此把帧镜像开关置灰并说明原因（见 StageBridge.supportsTrace 的注释）。
    supportsTrace = Property(bool, lambda self: True)

    @Slot(bool)
    def setProtoPaused(self, paused: bool):
        """暂停/继续记录协议行 —— 用户要盯着某一帧看时用（不停止收发）。"""
        self.proto_log.set_paused(paused)

    @Slot()
    def clearProto(self):
        self.proto_log.clear()

    # ============================================================
    # 连接
    # ============================================================
    @Slot(str, int, str, result=bool)
    def connectDevice(self, host: str, port: int, token: str) -> bool:
        host = (host or "").strip()
        token = (token or "").strip()
        try:
            new_port = int(port)
        except (TypeError, ValueError):
            new_port = DEFAULT_PORT
        if new_port <= 0 or new_port > 65535:
            self._set_error(f"端口 {port} 不合法（1~65535）")
            return False
        if not host:
            self._set_error("还没填板子 IP —— 在「Z 轴设置」里填（板子 USB 控制台敲 net 能看到）")
            return False
        if not token:
            self._set_error("还没填口令 —— 在「Z 轴设置」里填（板子 USB 控制台敲 net 能看到）")
            return False

        # 已经连着同一个目标：只更新参数，**不要断开重连**。
        if (self._get_connected() and host == self._host
                and new_port == self._port and token == self._token):
            self._log("已连接同一目标，只更新参数（不重连）")
            self._settings.setValue("zstage/host", host)
            self._settings.setValue("zstage/port", new_port)
            self._settings.setValue("zstage/token", token)
            return True

        self._host = host
        self._port = new_port
        self._token = token
        self._settings.setValue("zstage/host", self._host)
        self._settings.setValue("zstage/port", self._port)
        self._settings.setValue("zstage/token", self._token)

        self._authenticated = False
        self._rx_buf = b""
        self._resp_lines = []
        self._in_flight = None
        self._queue = []
        self._set_error("")
        self._connecting = True
        self.connectingChanged.emit()
        self.telemetryChanged.emit()

        self._log(f"正在连接 {self._host}:{self._port} …")
        self._sock.connectToHost(self._host, self._port)
        self._connect_timer.start(CONNECT_TIMEOUT_MS)
        return True

    def _on_connect_timeout(self):
        if self._sock.state() != QAbstractSocket.SocketState.ConnectingState:
            return                      # 已经连上或已经失败了，超时是多余的
        self._sock.abort()
        self._connecting = False
        self.connectingChanged.emit()
        self._set_error(
            f"连接 {self._host}:{self._port} 超时（{CONNECT_TIMEOUT_MS // 1000} 秒）。"
            "排查：① 电脑和板子连的是同一个热点吗 ② 板子 IP 有没有变"
            "（手机热点每次可能不同，板子上敲 net 再看一眼）"
            "③ 手机热点是不是开了「客户端隔离」", kind="transient")

    @Slot()
    def disconnectDevice(self):
        self._poll.stop()
        self._cmd_timer.stop()
        self._in_flight = None
        self._queue = []
        if self._sock.state() != QAbstractSocket.SocketState.UnconnectedState:
            try:
                self._sock.write(b"quit\n")
                self._sock.flush()
            except Exception:
                pass
            self._sock.disconnectFromHost()
        self._authenticated = False
        self._connecting = False
        self._trace_on = False      # 固件那边可能还开着，但链路断了就当它关了
        self._sign_pending = False
        self._sign_text = ""        # 换一块板子重连时，旧符号不能留在界面上
        if self._moving:
            self._moving = False
            self.movingChanged.emit()
        self.connectingChanged.emit()
        self.connectedChanged.emit()
        self._log("已断开")

    def _on_connected(self):
        self._connect_timer.stop()
        self._connecting = False
        self.connectingChanged.emit()
        self._log("TCP 已连接，正在交口令 …")
        # 协议要求：第一行必须是口令
        self._sock.write(self._token.encode("utf-8") + b"\n")

    def _on_disconnected(self):
        was = self._get_connected()
        self._connect_timer.stop()
        self._poll.stop()
        self._cmd_timer.stop()
        self._authenticated = False
        self._connecting = False
        self._trace_on = False
        self._sign_pending = False
        self._sign_text = ""
        self._in_flight = None
        self._queue = []
        if self._moving:
            self._moving = False
            self.movingChanged.emit()
        self.connectingChanged.emit()
        if was:
            self._set_error("与板子的连接已断开", kind="transient")
        # ⚠ 无论主动断开还是掉线，**基准与软限位状态一律作废**：
        #   `datum` 在固件的 RAM 里，链路一断就无从知道板子有没有重启过
        #   （重启 = 基准没了），继续显示"已立基准"会让用户以为绝对定位是安全的。
        #   重连后第一次 `json` 就会把真实值刷回来，所以这里宁可保守。
        #   （早先这段写在 `if was:` 里面 —— 而 `disconnectDevice()` 会**先**把
        #    `_authenticated` 置 False，于是主动断开这条路径根本清不掉。测试抓到的。）
        self._datum = False
        self._lim_set = False
        self.telemetryChanged.emit()
        self.connectedChanged.emit()
        self._log("连接已关闭")

    def _on_socket_error(self, err):
        if err == QAbstractSocket.SocketError.RemoteHostClosedError:
            return                      # 正常断开，不用报错
        self._connect_timer.stop()
        self._connecting = False
        self.connectingChanged.emit()
        self._set_error(f"连接失败: {self._sock.errorString()}", kind="transient")

    # ============================================================
    # 收数据：按行分帧
    # ============================================================
    def _on_ready_read(self):
        self._rx_buf += bytes(self._sock.readAll())
        while b"\n" in self._rx_buf:
            raw, self._rx_buf = self._rx_buf.split(b"\n", 1)
            line = raw.decode("utf-8", errors="replace").rstrip("\r")
            self._handle_line(line)

    def _handle_line(self, line: str):
        # ⓪ 驱动器原始帧（固件 `trace on` 时镜像回来的）—— 只进协议框，
        #    不进 _resp_lines：它是**异步插进来**的，不能当成命令输出参与判定。
        if line.startswith("@"):
            self._proto_add(f"   {line}")
            return

        # ① 协议框：把**每一条**收到的行都记下来（含 json 轮询）。
        self._proto_add(f"← {line}")

        # ② 认证应答（还没进入正常命令流程）
        if not self._authenticated:
            if line == "#OK auth":
                self._authenticated = True
                self._log("口令通过，通道可用")
                self._set_error("")
                self.connectedChanged.emit()
                self._poll.start(POLL_IDLE_MS)
                # ⚠ 连接期只许发**幂等且无副作用**的命令（AGENTS.md 第 12 条）：
                #   `json` 只读；`zsign` 不带参数也是只读（读方向符号）。
                #   ⚠ 绝不在这里发 `zzero`/`zhome`/`zlim`/`zset` —— 它们会改状态，
                #     其中 `zhome` 直接让电机去找零点（驱动器收到 0x91 就动）。
                self._sign_pending = True
                self._enqueue("zsign", note="读方向符号")
            elif line.startswith("#ERR"):
                # ⚠ 认证期的 #ERR **不一定**是口令错：板子还会回 `#ERR busy`
                #   （已有别的客户端连着）和 `#ERR auth timeout`。一律说"口令被拒"
                #   会让人反复核对一个本来正确的口令（评审指出）。
                if "busy" in line:
                    self._set_error("板子已经有别的客户端连着（#ERR busy）——"
                                    "关掉另一个客户端/界面再试")
                elif "timeout" in line:
                    self._set_error("交口令超时（#ERR auth timeout）—— 重试一次；"
                                    "一直这样请查网络/板子是否卡住")
                else:
                    self._set_error(f"口令被拒（{line}）—— 在板子 USB 控制台敲 net 看正确口令")
                self.disconnectDevice()
            return

        # ③ 命令应答的结束标记。
        # ⚠ 用 `==` / `startswith("#ERR")`，**不要**放宽成 `startswith("#")`：
        #   `#` 是保留前缀（认证期的 `#OK auth`、`#ERR busy` 都是）。
        #   （早期注释写"固件 ver 的输出有 # 开头的行"——查过两个固件都没有，
        #    理由不成立但结论是对的，改成上面这条真实的理由。）
        if line == "#OK" or line.startswith("#ERR"):
            self._finish_command(line == "#OK", line)
            return

        if line:
            self._resp_lines.append(line)
            # json 的应答只有一行，直接解析，不必等结束标记
            if line.startswith("{") and self._in_flight and self._in_flight.get("json"):
                self._apply_json(line)
            # zsign 的读回：把符号抠出来显示（json 里没有这两个字段）
            if self._sign_pending:
                m = _SIGN_RE.search(line)
                if m:
                    self._sign_text = f"sa={m.group(1)}  sb={m.group(2)}"
                    self._sign_pending = False
                    self.telemetryChanged.emit()

    def _finish_command(self, ok: bool, marker: str):
        flight = self._in_flight
        self._in_flight = None
        self._cmd_timer.stop()

        if flight is not None and not ok:
            note = flight.get("note", flight.get("cmd", "命令"))
            detail = " / ".join(self._resp_lines[-3:]) if self._resp_lines else marker
            # 运动类命令被拒 = **闸没满足**（缺基准 / 没设软限位 / 还在运动），
            # 按 gate 处理：条件一旦满足就自动消失。否则会出现"状态条全绿、下面还
            # 挂着红的『还没有基准』"那种自相矛盾（本项目明令禁止）。
            # 其它命令（zlim/zset/zcfg…）的拒绝则按 action 处理，带 src ——
            # 同类操作成功时清掉（见 _clear_error_if）。
            if flight.get("src") in ("jog", "move", "zero", "home", "tilt"):
                self._gate_error(None, f"{note} 被拒绝：{detail}")
            else:
                self._set_error(f"{note} 被拒绝：{detail}", src=flight.get("src", ""))
        elif flight is not None and flight.get("note"):
            self._log(f"{flight['note']} 完成")
            # 成功的命令也常带警告（固件里以 ⚠ 开头的行）——以前只在失败时显示，
            # 等于把最有用的排查信息扔掉了（`mode`/`zsign`/`json` 都会带）。
            for ln in self._resp_lines:
                if "⚠" in ln:
                    self._log(ln.strip())

        if ok:
            self._maybe_clear_error()

        self._resp_lines = []
        self._pump_queue()

    def _on_cmd_timeout(self):
        flight = self._in_flight
        self._in_flight = None
        # 超时的可能是连接期那条 zsign —— 别让它永远挂着"pending"
        self._sign_pending = False
        if flight is not None:
            self._set_error(f"{flight.get('note', flight.get('cmd'))} 超时（板子没有应答）",
                            kind="transient")
        self._resp_lines = []
        self._pump_queue()

    # ============================================================
    # 命令队列
    # ============================================================
    def _enqueue(self, cmd: str, note: str = "", timeout_ms: int = CMD_TIMEOUT_MS,
                 json_reply: bool = False, front: bool = False, src: str = ""):
        """排队 + 泵出去。⚠ **没有返回值**（与 stage_bridge 一致）——
        别写 `if not self._enqueue(...)`：`not None` 恒为真，分支永远会走。"""
        if not self._authenticated:
            self._set_error("未连接 Z 轴平台，命令未发送")
            return
        if not json_reply:
            self._log(f"→ {cmd}")
        # 自定义命令框能敲到几十秒级的命令（hcalib/hfactory/阻塞 zrel…）——
        # 超时短了会让迟到应答与**下一条**命令配对错位。见 SLOW_CMD_TIMEOUT_MS。
        head = cmd.split()[0] if cmd.split() else ""
        if timeout_ms == CMD_TIMEOUT_MS and head in SLOW_CMD_TIMEOUT_MS:
            timeout_ms = SLOW_CMD_TIMEOUT_MS[head]
        item = {"cmd": cmd, "note": note, "timeout_ms": timeout_ms, "json": json_reply,
                "src": src}
        if front:
            self._queue.insert(0, item)
        else:
            self._queue.append(item)
        self._pump_queue()

    def _pump_queue(self):
        if self._in_flight is not None or not self._authenticated:
            return
        if not self._queue:
            return
        item = self._queue.pop(0)
        self._in_flight = item
        self._resp_lines = []
        self._proto_add(f"→ {item['cmd']}")
        self._sock.write(item["cmd"].encode("utf-8") + b"\n")
        self._cmd_timer.start(item["timeout_ms"])

    @Slot()
    def pollNow(self):
        """立刻轮询一次（界面刚做完操作时用，不用等下一个 tick）。"""
        if self._authenticated and self._in_flight is None:
            self._enqueue("json", json_reply=True)

    # ============================================================
    # 轮询
    # ============================================================
    def _poll_tick(self):
        # 运动中加快轮询：位置读数的实时感全靠它。
        # json 在固件侧还会顺手判定"非阻塞移动是否到位"，所以轮询本身就是到位检测。
        want = POLL_MOVING_MS if self._moving else POLL_IDLE_MS
        if self._poll.interval() != want:
            self._poll.setInterval(want)
        if self._queue or self._in_flight is not None:
            return                      # 别把用户的命令挤到后面
        self._enqueue("json", json_reply=True)

    def _apply_json(self, line: str):
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return
        if "err" in data:
            # ⚠ transient：这是**总线偶发超时**的报法（驱动器偶尔晚答，固件已重试过一次），
            #   固件那边的设计就是"偶发一次不锁死"。用 action 的话红字会**永久粘住**，
            #   之后一切正常也不消失（评审实测过）。
            # ⚠ 文案要说人话：固件回的是 `left=ESP_ERR_TIMEOUT right=OK` 这种原始字符串，
            #   直接甩到横幅上用户看不懂（2026-09-29 现场截图就是一行裸 JSON）。
            self._set_error(f"读位置超时：{_axis_fault_text(str(data['err']))}"
                            "——驱动器偶尔晚答，下一次轮询会自动恢复",
                            kind="transient")
            return

        self._z = _fnum(data.get("z"))
        self._skew = _fnum(data.get("skew"))
        self._joint_a = _inum(data.get("a"))
        self._joint_b = _inum(data.get("b"))
        self._voltage = _fnum(data.get("v"))
        self._enabled = bool(_inum(data.get("en")))
        self._datum = bool(_inum(data.get("datum")))
        self._lim_set = bool(_inum(data.get("lim")))
        self._json_lo = _fnum(data.get("zmin"), DEFAULT_Z_LO)
        self._json_hi = _fnum(data.get("zmax"), DEFAULT_Z_HI)
        self._last_move = str(data.get("last", "none"))
        self._homing = _inum(data.get("homing"))
        self._fault = str(data.get("fault", "none"))
        self._target = _fnum(data.get("tgt"))
        self._autohome = bool(_inum(data.get("auto")))
        self._um_per_rev = _inum(data.get("umrev"), 8000)
        # 板子实际在用的回零参数（固件 json 的 hma/hrpm/htmo）。
        # ⚠ 老固件没有这三个字段 → 这里会退回出厂默认值，界面照样能显示，
        #   只是"板子当前生效"那一行按出厂值写。这不是错误，所以不打红字。
        self._home_ma = _inum(data.get("hma"), DEFAULT_HOME_MA)
        self._home_rpm = _inum(data.get("hrpm"), DEFAULT_HOME_RPM)
        self._home_tmo = _inum(data.get("htmo"), DEFAULT_HOME_TMO)
        self._rssi = _inum(data.get("rssi"))
        self._board_ip = str(data.get("ip", ""))

        self._maybe_clear_error()

        moving = bool(_inum(data.get("moving")))
        if moving != self._moving:
            self._moving = moving
            self.movingChanged.emit()

        self.telemetryChanged.emit()

    # ============================================================
    # 运动（全部非阻塞）
    # ============================================================
    def _clamp_target(self, z_mm: float):
        """按软限位夹取目标，返回 (夹取后的值, 是否被夹过)。

        固件侧有软限位闸会拒绝越界，但那是**兜底**；界面先把目标夹住并显示夹取后的值，
        用户才不会看到"点了没反应"。两道防线各有各的用处。
        夹取用**固件实际在用的**窗口（json 的 zmin/zmax），不是本地设置 ——
        两者可能不一致（比如别人用 USB 控制台改过）。
        """
        lo, hi = self._json_lo, self._json_hi
        if hi <= lo:                    # 固件没设限位时 json 里是出厂值，兜一下
            lo, hi = DEFAULT_Z_LO, DEFAULT_Z_HI
        clamped = min(max(z_mm, lo), hi)
        return clamped, abs(clamped - z_mm) > 1e-6

    def _require_ready(self, what: str, need_datum: bool = True) -> bool:
        """运动前的统一前置检查 —— 拒绝的原因要**说清楚**。

        `need_datum=False` 给**点动**用：固件是**故意**允许无基准相对点动的
        （`zup`/`zdown` 单次 ≤20mm，并会打一行 ⚠ 说明软限位不生效）——
        因为"把平台挪到参考位置"正是立基准的前置步骤。桥要是把它一起锁死，
        首次立基准就只剩"用手推"这一条路了（两个独立评审都指出了这点）。
        绝对定位仍然必须要有基准 + 软限位。"""
        if not self._get_connected():
            self._set_error(f"{what} 未发送：还没连接 Z 轴平台")
            return False
        if not need_datum:
            return True
        if not self._datum:
            self._gate_error("datum", f"{what} 被拒绝：还没有基准 —— 先点「设为原点」")
            return False
        if not self._lim_set:
            self._gate_error("lim", f"{what} 被拒绝：还没设软限位 —— 在「Z 轴设置」里填 0~250")
            return False
        return True

    def _note_motion_sent(self):
        """运动指令已经发出 → **乐观**置 `moving`。

        为什么乐观：固件的 `moving` 要等下一次 `json` 才回来，而空闲轮询是 1Hz ——
        不乐观置位的话，点一下方向键要过一秒界面才"知道"在动（而且轮询频率还停在
        1Hz，"运动中 5Hz"那条也就永远不会触发，位置读数看起来一顿一顿）。
        ⚠ 代价：命令被固件拒绝时这一位最多多挂一个轮询周期，下一次 json 就会纠正。
        这与 `stage_bridge.py` 的做法一致（它的测试里也有一条"乐观置 moving"）。
        """
        if not self._moving:
            self._moving = True
            self.movingChanged.emit()
            # ⚠ 必须**同时**发 telemetryChanged：`canMove`/`canJog` 这些派生量挂在
            #   telemetryChanged 上，只发 movingChanged 的话 QML 直绑 `canMove`
            #   在"刚点完点动"那一刻不会重算（按钮看着还能点）。两个独立评审都抓到了。
            self.telemetryChanged.emit()

    def _motion_cmd(self, verb: str, z_mm: float) -> str:
        return "{} {:.3f} {} {}".format(verb, z_mm, self._rpm, self._acc)

    def _jog_cmd(self, verb: str, mm: float) -> str:
        """⚠ 点动只发 `zup <mm> <rpm>`：固件的 `cmd_zup`/`cmd_zdown` **不读第三个参数**
        （加减速走板子自己的 `s_p.acc`）。发一个 acc 进去会让日志与测试都以为它生效了 ——
        那是假象（评审读固件源码发现的）。"""
        return "{} {:.3f} {}".format(verb, mm, self._rpm)

    @Slot(float, result=bool)
    def jogUp(self, mm: float) -> bool:
        """向上点动。`mm <= 0` 时用当前步长。非阻塞（固件 `zup`）。"""
        step = self._step if mm is None or mm <= 0 else float(mm)
        if not self._require_ready("向上点动", need_datum=False):
            return False
        self._clear_error_if("jog")
        self._enqueue(self._jog_cmd("zup", step), note=f"向上 {step:g}mm", src="jog")
        self._note_motion_sent()
        return True

    @Slot(float, result=bool)
    def jogDown(self, mm: float) -> bool:
        """向下点动。非阻塞（固件 `zdown`）。"""
        step = self._step if mm is None or mm <= 0 else float(mm)
        if not self._require_ready("向下点动", need_datum=False):
            return False
        self._clear_error_if("jog")
        self._enqueue(self._jog_cmd("zdown", step), note=f"向下 {step:g}mm", src="jog")
        self._note_motion_sent()
        return True

    @Slot(float, result=bool)
    def moveTo(self, z_mm: float) -> bool:
        """绝对高度定位（非阻塞 `zmove`）。目标先按软限位夹取再发。"""
        if not self._require_ready("绝对定位"):
            return False
        clamped, was_clamped = self._clamp_target(float(z_mm))
        if was_clamped:
            self._log(f"目标 {z_mm:.2f}mm 超出软限位 "
                      f"[{self._json_lo:.2f}, {self._json_hi:.2f}]，已夹到 {clamped:.2f}mm")
        self._clear_error_if("move")
        self._enqueue(self._motion_cmd("zmove", clamped),
                      note=f"移动到 {clamped:.2f}mm", src="move")
        self._note_motion_sent()
        return True

    @Slot(bool, result=bool)
    def setEnabled(self, on: bool) -> bool:
        """使能 / 失能两轴（固件 `en all` / `dis all`）。

        ⚠ 本机丝杠**自锁**（2026-09-27 现场确认：断电后平台不动），所以**失能后平台
        停在原地不会掉** —— 「失能」正是"用手把平台推到靠块/机械死点再 `zzero` 立基准"
        这条主线的正确做法（`zzero` 要求平台已经物理停在参考位置）。
        ⚠ 但自锁只保证**静态**停得住：失能后没有闭环抱它，外力/振动仍可能推动平台 ——
        别在平台下面放手，也别在失能状态下搬整机。
        ⚠ 只想"停住"而不失能：用 `stopNow()`（`stop all` = 刹车 + 保持使能）。

        早先这个按钮**故意没做**，理由写的是"丝杠不自锁、失能会掉下来" —— 那个前提
        是按导程角推算的，**推错了**（摩擦角按 μ≈0.1 取，而梯形丝杠配塑料/铜螺母常在
        0.15~0.25）。现场的实测结论一改，这个决定也跟着改了。
        """
        if not self._get_connected():
            self._set_error("使能/失能未下发：还没连接")
            return False
        self._clear_error_if("enable")
        self._enqueue("en all" if on else "dis all",
                      note=("使能两轴（闭环抱住平台）" if on else "失能两轴（可手推平台）"),
                      src="enable")
        return True

    @Slot(float, result=bool)
    def tilt(self, mm: float) -> bool:
        """只动一侧校平（`ztilt`）—— 偏斜报警后的维修动作，需要基准。"""
        if not self._require_ready("校平"):
            return False
        if abs(float(mm)) > 10.0:
            self._set_error("校平单次限 ±10mm（差得多说明机械有问题，先查机械）", src="tilt")
            return False
        self._clear_error_if("tilt")
        self._enqueue(f"ztilt left {float(mm):.3f} {min(self._rpm, 120)}", note="校平左侧",
                      src="tilt")
        self._note_motion_sent()
        return True

    @Slot()
    def stopNow(self):
        """急停。⚠ **永远排在最前面、永不被禁用**（enabled 只跟 connected 走）。

        固件的 `stop all` 会：先刹车 → 无条件给两轴发 0x93（退出回零状态机）→
        收掉在途状态 → 打印。它**能打断正在阻塞等待的命令**，所以是真急停。
        """
        if not self._authenticated:
            self._set_error("未连接，急停命令没发出去（请直接断板子电源）")
            return
        self._enqueue("stop all", note="急停", front=True)

    # ============================================================
    # 标定 / 参数
    # ============================================================
    @Slot(result=bool)
    def setZero(self) -> bool:
        """把当前位置定为 Z=0（固件 `zzero`）。无物理限位器，这是**主线**做法：
        先把平台推到靠块/机械死点，再点这个。"""
        if not self._get_connected():
            self._set_error("设为原点未发送：还没连接")
            return False
        if self._moving:
            self._set_error("设为原点被拒绝：还在运动 —— 先等它停或按急停", src="zero")
            return False
        self._clear_error_if("zero")
        self._enqueue("zzero", note="设为原点（立基准）", timeout_ms=ZERO_TIMEOUT_MS, src="zero")
        return True

    @Slot(result=bool)
    def homeNow(self) -> bool:
        """无限位回零（驱动器靠电流阈值判机械死点），非阻塞。

        ⚠ 这是可选路径：两侧丝杠必须同时顶到各自的死点，否则会把平台拧歪。
        所以界面上它要写明"可选"，主线是「推到靠块 → 设为原点」。
        方向来自界面（`setHomeDir`，存本机 QSettings）：出厂默认"向下"——
        向下是往底座死点走，重力帮忙、撞不坏；"向上"只在需要往另一头找零点时用。
        ⚠ 固件本来就支持 `zhome up|down`，这里照实发 —— 别再写死。
        """
        if not self._get_connected():
            self._set_error("自动回零未发送：还没连接")
            return False
        if self._moving:
            self._set_error("自动回零被拒绝：还在运动 —— 先等它停或按急停", src="home")
            return False
        self._clear_error_if("home")
        self._enqueue(f"zhome {self._home_dir} nowait",
                      note=f"自动回零（{'向上' if self._home_dir == 'up' else '向下'}）",
                      src="home")
        self._note_motion_sent()
        return True

    @Slot(str, result=bool)
    def setHomeDir(self, direction: str) -> bool:
        """选自动回零的方向（`up` / `down`）。**只存本机**，不写板子。

        ⚠ 与板子的 `home_dir`（`zautohome` 用的方向）是两件事，用户明确要求分开：
           手动按钮走这里，上电自动回零走板子里存的方向。
        """
        d = str(direction).strip().lower()
        if d not in HOME_DIRS:
            self._set_error(f"回零方向 {direction} 不认识（只认 {'/'.join(HOME_DIRS)}）",
                            src="home")
            return False
        self._home_dir = d
        self._settings.setValue("zstage/home_dir", d)
        self.telemetryChanged.emit()
        self._log(f"自动回零方向 = {d}")
        return True

    @Slot(int, int, int, result=bool)
    def setHomeParams(self, rpm: int, ma: int, tmo: int) -> bool:
        """写无限位回零的三个参数（固件 `zset home <rpm> <mA> <timeout_ms>`，存 NVS）。

        ⚠ **限位电流是回零能不能成的关键**：低于空转电流(≈40mA)会「一动就报完成」
        （假成功，平台其实没顶到死点），高于堵转电流(≈1500~2700mA)永远不触发
        （假失败，平台一直硬顶到驱动器超时）。甜点区 300~800mA。
        界线与固件 `cmd_zset` 逐条对齐，并在**填的当下**说清楚（不等到下发后才说）。

        ⚠ 三个值必须一起发：固件的 `zset home` 要求 `<rpm> <mA>` 都在场
        （`argc > 3`），只改一个字段也得把另两个带上 —— 所以这里的输入是
        "板子实际在用的值"（json hma/hrpm/htmo）而不是上一次点过的值。
        """
        if not self._get_connected():
            self._set_error("回零参数未下发：还没连接")
            return False

        rpm, ma, tmo = int(rpm), int(ma), int(tmo)
        if not (HOME_RPM_MIN <= rpm <= HOME_RPM_MAX):
            self._set_error(f"回零转速 {rpm} 超出范围（{HOME_RPM_MIN}~{HOME_RPM_MAX}rpm）",
                            src="home")
            return False
        if not (HOME_MA_MIN <= ma <= HOME_MA_MAX):
            self._set_error(f"限位电流 {ma} 超出范围（{HOME_MA_MIN}~{HOME_MA_MAX}mA）",
                            src="home")
            return False
        if not (HOME_TMO_MIN <= tmo <= HOME_TMO_MAX):
            self._set_error(f"回零超时 {tmo} 超出范围（{HOME_TMO_MIN}~{HOME_TMO_MAX}ms）",
                            src="home")
            return False

        if ma < HOME_MA_SWEET_LO or ma > HOME_MA_SWEET_HI:
            # 警告而不是拒绝：现场确实需要改这个值（换电机/换丝杠），但后果要说清楚。
            # 文案与固件 `zset home` 打的那两行一致（本机经验区 60~300mA，推荐 100）。
            self._log(f"⚠ 限位电流 {ma}mA 不在本机经验区 "
                      f"{HOME_MA_SWEET_LO}~{HOME_MA_SWEET_HI}mA（推荐 {DEFAULT_HOME_MA}）："
                      + ("过低接近空转电流(≈40mA)，会「一动就报回零完成」（假成功）"
                         if ma < HOME_MA_SWEET_LO
                         else "过高会「永远不触发」——本机顶住时相电流只有一百多 mA"))

        if (rpm, ma, tmo) == (self._home_rpm, self._home_ma, self._home_tmo):
            self._clear_error_if("homeparam")
            self._log("回零参数与板子当前值相同，没有下发")
            return True

        self._clear_error_if("homeparam")
        self._enqueue(f"zset home {rpm} {ma} {tmo}",
                      note=f"回零参数 {rpm}rpm/{ma}mA/{tmo}ms", src="homeparam")
        return True

    @Slot(float, float, result=bool)
    def setSoftLimits(self, lo: float, hi: float) -> bool:
        """设软限位（固件 `zlim`）。⚠ 单位是 mm、相对**基准零点**。"""
        lo, hi = float(lo), float(hi)
        if hi <= lo:
            self._set_error("软限位上限必须大于下限", src="lim")
            return False
        if hi - lo > Z_LIMIT_SPAN_MAX:
            self._set_error(f"行程 {hi - lo:.0f}mm 超过 {Z_LIMIT_SPAN_MAX:.0f}mm —— 肯定填错了",
                            src="lim")
            return False
        if not self._get_connected():
            self._set_error("软限位未下发：还没连接")
            return False
        self._clear_error_if("lim")
        self._lim_lo, self._lim_hi = lo, hi
        self._settings.setValue("zstage/lim_lo", lo)
        self._settings.setValue("zstage/lim_hi", hi)
        self._enqueue(f"zlim {lo:.3f} {hi:.3f}", note="设置软限位", src="lim")
        self.telemetryChanged.emit()
        return True

    @Slot(int, int, result=bool)
    def setSpeed(self, rpm: int, acc: int) -> bool:
        """设置默认速度/加减速档（固件 `zset`，存板子 NVS）。

        ⚠ 只发**变了的那一项**：`zset` 一次只改一个字段，两条都发等于多占一次在途。
        ⚠ acc 的 0 是"直接启动、无斜坡"（手册原文），所以下限是 1。
        """
        if not self._get_connected():
            self._set_error("速度未下发：还没连接")
            return False
        rpm = max(1, min(int(rpm), UI_MAX_RPM))
        acc = max(UI_MIN_ACC, min(int(acc), UI_MAX_ACC))
        if rpm != self._rpm:
            self._rpm = rpm
            self._settings.setValue("zstage/rpm", rpm)
            self._enqueue(f"zset rpm {rpm}", note=f"默认速度 {rpm}rpm", src="speed")
        if acc != self._acc:
            self._acc = acc
            self._settings.setValue("zstage/acc", acc)
            self._enqueue(f"zset acc {acc}", note=f"加减速档 {acc}", src="speed")
        self.telemetryChanged.emit()
        return True

    @Slot(bool, result=bool)
    def setAutohome(self, on: bool) -> bool:
        """上电自动回零开关（`zautohome`，存板子 NVS）。⚠ 开着 = 板子一上电
        （约 3 秒后）就自己朝机械死点撞一次 —— 用户不在场时那就是"平台自己动了"。

        ⚠ **不带方向**（原来发的是 `on down`）：固件的方向是可选参数，带上就等于
        顺手把板子里的方向改掉。手动按钮的方向由 `setHomeDir()` 管（只存本机），
        两者是两件事 —— 用户明确要求不要合并。
        """
        if not self._get_connected():
            self._set_error("自动回零开关未下发：还没连接")
            return False
        # **乐观**置位（与 _note_motion_sent 同一套理由）：界面的开关已经拨过去了，
        # 等下一次 json(1Hz) 才更新的话开关会先弹回旧值再弹过来 —— 一闪一闪像坏了。
        # 板子真的拒绝时，跟着来的那一次 json 会把这一位纠正回来（UI 有回同步）。
        self._autohome = bool(on)
        self._enqueue("zautohome " + ("on" if on else "off"),
                      note="上电自动回零 " + ("开" if on else "关"))
        self.telemetryChanged.emit()
        return True

    @Slot(float)
    def setStep(self, mm: float):
        """设置点动步长（只存本机，固件不知道这件事）。"""
        self._step = float(mm)
        self._settings.setValue("zstage/step", self._step)
        self.telemetryChanged.emit()

    @Slot(bool, result=bool)
    def setTrace(self, on: bool) -> bool:
        """开关**驱动器原始帧**镜像（固件 `trace`）。

        这是"自定义协议显示框"的第二层：开了之后固件会把每一帧 PD42S1 报文
        （`C5 <addr> <code> ... <chk> 5C`）以 `@TX` / `@RX` 前缀镜像回来。
        ⚠ 打开期间每帧两行，运动中会刷得很快 —— 排障时开、看完就关。
        """
        if not self._get_connected():
            self._set_error("协议帧开关未下发：还没连接")
            return False
        self._trace_on = bool(on)
        self._enqueue("trace " + ("on" if on else "off"),
                      note="驱动器帧镜像 " + ("开" if on else "关"))
        self.telemetryChanged.emit()
        return True

    @Slot(str, result=bool)
    def sendCommand(self, text: str) -> bool:
        """把用户敲的一行**原样**发给板子（自定义命令框）。

        故意的：这是给"想自己摸协议"的人用的 —— 固件那边就是同一张命令表，
        USB 控制台能敲的这里都能敲。回显与应答都在协议框里。
        ⚠ 不做任何白名单：能敲就能发。但运动命令仍然受固件的四道闸约束
        （基准/软限位/超程看门狗/偏斜看门狗），所以乱敲不会绕过安全保护。
        """
        cmd = (text or "").strip()
        if not cmd:
            return False
        if not self._get_connected():
            self._set_error("命令未发送：还没连接")
            return False
        if "\n" in cmd or "\r" in cmd:
            self._set_error("命令不能包含换行", src="cmd")
            return False
        self._clear_error_if("cmd")
        self._enqueue(cmd, note=f"手动命令 {cmd}", src="cmd")
        return True
