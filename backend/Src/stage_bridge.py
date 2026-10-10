"""StageBridge — 二轴相机平台（ESP32 + 两台 PD42S1 闭环驱动器）的 WiFi 控制桥。

对应的固件在 `研究生毕业设计/esp/Steppermotor`；设计与协议见
`docs/17-二轴平台与WiFi控制.md`。

协议（行分隔文本，与板子的 USB 控制台**语法完全一致**）：
    连接后第一行必须是口令          → 板子回 `#OK auth`
    之后每行 = 一条命令              → 板子回：命令输出 + 末行 `#OK` 或 `#ERR <code>`
    空闲时用 `json` 轮询            → 一行 JSON 拿全状态

为什么用 QTcpSocket 而不是线程 + 阻塞读：
    QTcpSocket 是异步的、走 Qt 事件循环，不需要额外线程，也不会像 LightBridge
    那样在发送时阻塞 UI。轮询用 QTimer，收到数据走 readyRead 信号。

单位约定（**这个文件是唯一的换算边界**）：
    固件的笛卡尔坐标单位是"电机度"（360 = 电机一圈），界面一律用毫米。
    GT2-16 齿带轮 → 每圈 32mm → `1 度 = 32/360 mm`、`1 mm = 11.25 度`、`1600 counts/mm`。
    界面层永远不碰"度"，固件层永远不碰"mm" —— 换算只在这里做一次。

安全相关的三条（都来自固件侧的设计，UI 必须配合，详见 docs/17 §5）：
  1. 移动一律用**非阻塞**的 `move`，绝不用会等到位的 `g0`。
     否则一条急停会被几秒的长移动堵在通道里，等于没有急停。
  2. 固件有四道闸（基准/行程/防呆/运动互斥），拒绝时**不发任何运动指令**。
     UI 要把拒绝原因显示出来，否则用户只看到"点了没反应"。
  3. 绝对运动的基准是驱动器**单圈编码器**给的，掉电即失效（手册 2.3③）。
     所以"预设位置"只在本次上电重新立过基准之后才有意义 —— 页面必须提示这一点。
"""

from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import (QCoreApplication, QObject, QSettings, QThread, QTimer,
                            Property, Signal, Slot)
from PySide6.QtNetwork import QAbstractSocket, QTcpSocket

from Src import stage_traj

# ── 机械换算（GT2-16 齿带轮，每圈 32mm）────────────────────────────
MM_PER_DEG = 32.0 / 360.0          # 0.088888…
DEG_PER_MM = 1.0 / MM_PER_DEG      # 11.25
COUNTS_PER_MM = 1600.0             # 51200 / 32

# ── 轮询节奏 ──────────────────────────────────────────────────────
POLL_IDLE_MS = 1000                # 空闲：1Hz 足够（状态条不需要更勤）
POLL_MOVING_MS = 200               # 运动中：5Hz，位置读数的观感就靠它

# ── 命令超时 ──────────────────────────────────────────────────────
# `move` 在固件侧立即返回，但排到队尾也要等一下，别给太长（免得队列卡死）。
CMD_TIMEOUT_MS = 3000
MOVE_TIMEOUT_MS = 2000

# 建连超时。⚠ 必须有：连一个**不存在**的 IP 时 TCP 会一直重传 SYN，
# Qt 自己不会超时 —— 现象是卡片一直转"正在连接…"、永远不报错。
# 局域网内 8 秒足够，而且能给出"是不是没连同一个热点"这种可操作的提示。
CONNECT_TIMEOUT_MS = 8000

# 回零参数的保守默认值。
# ⚠ **方向 `rev` 不能替用户猜**：反了会把台面顶到机械死点。
#    现在方向由**固件**按 COREXY_DEFAULT 从"想去哪个角"反推（`home corner`），
#    所以这里只剩 方式/速度/限位电流 三个值要用户给。
HOME_DEFAULT = {"kind": 0, "rev": 0, "rpm": 300, "ma": 800}
# ⚠ 回零速度别用 60rpm：满行程 300mm = 9.375 电机圈（1mm = 11.25°），60rpm 要 **9.4 秒**，
#   几乎顶到驱动器默认 10 秒的回零超时（还没算加减速）→ 从远端起步就会超时失败。
#   300rpm 只要约 1.9 秒，余量充足。想更慢请用 `htmo` 放宽超时，别硬压速度。
OLD_DEFAULT_HOME_RPM = 60   # 旧默认值；读 settings 时按"没设过"迁到 300
# 限位电流 = **电流阈值**（手册 §2.3⑩「电流阈值检测」+ 厂家客服确认）：相电流越过它就算到位。
#   所以它不是"越大越有劲"，而是两端都有坑：
#     · 太小（接近空转电流 ~40mA）→ 电机一动就**假报「回零完成」**；
#     · 太大（超过堵转电流 ~1500~2700mA）→ 永远触发不了。
#   中间很大一段都能用（300/600/800 都行），本项目默认 800mA、下限设 300mA 只是留出余量。
HOME_MA_MIN = 300

# ── 退出回零点 / 位置核对（2026-09-13，用户提的方案）──
PARK_TIMEOUT_MS = 8000      # 退出时最多等这么久；等不到就放弃（位置仍然准，只是没停在零点）
PARK_DRIFT_MM = 0.5         # 连上时位置和"上次停在哪"差多少就提示"被动过"

# 零点角 = 「X 左 + Y 外」= 台面坐标 (−X, −Y)。
# ⚠ 2026-09-13 **写死**（原来是个界面下拉框，用户拍板去掉）：
#   用户原话："我打算固定零点在 X 轴左侧和 Y 轴的外侧"。
#   零点只能有一个 —— 它同时被预设位置、工作区、退出回零点引用着，
#   一旦能选，就会出现"这次回左·外、下次回右·里"把整套坐标系挪走的事故。
#   xdir/ydir 是**台面坐标**的 ±X/±Y：+X = 右，+Y = 向里。
#   两轴各自的旋转方向仍由**固件**按 COREXY_DEFAULT 反推（`home corner`）——
#   符号矩阵不在这一层复制。
DATUM_CORNER = (-1, -1)
HOME_KIND_MAX = 3          # 固件：0左无限位/1右无限位/2左有限位/3右有限位
HOME_RPM_MAX = 6000        # 固件 cmd_hset 的上限
HOME_MA_MAX = 3000         # 固件 cmd_hset 的上限

DEFAULT_PORT = 3333
DEFAULT_HOST = ""        # 故意留空：填一个像模像样的假 IP 会让人以为已经配好了
DEFAULT_TOKEN = ""

# ── 速度/加减速的上限：**必须以固件为准** ──────────────────────────
# 固件组帧时有一道检查（components/pd42s1/pd42s1.c）：
#     if (speed_rpm > 6000 || acc > 200) return -1;
# 超了就直接拒帧、指令根本发不出去。
# ⚠ 这里原来 acc 的上限写成 255（比固件宽），界面滑块也放到 255 ——
#   结果拖到 201~255 时**什么都不发生**，表现成"改了加减速没反应"。
MAX_RPM = 6000           # 固件上限（`pd42s1.c::cmd_move`：speed_rpm > 6000 直接拒帧）
MAX_ACC = 200            # 固件上限（不是 255！）
# 界面滑块上限（2026-09-13 从 1200 提到 3000，用户要求）。
#   为什么不是直接给到 6000：**驱动器能输出 ≠ 电机带得动**。
#   手册 §2.1 写的是"最大工作电流 3000mA，最高转速 6000RPM"，所以 6000 是协议上限；
#   但 1.8° 电机 3000rpm = 10kHz 电频率，24V + 4.3mH 相电感下电流来不及建立 →
#   **扭矩随转速急剧下降**，3000 以上基本只适合空载。3000 是个"还能带点载"的实用顶。
#   ⚠ 这个常量**必须被界面真正用上**：以前它写在这儿却没人引用，而滑块里硬编码 `to: 1200`
#     —— 同一个事实两份，改一处不生效（本项目第 4 条坑）。
UI_MAX_RPM = 3000
UI_MAX_ACC = MAX_ACC     # 加减速没有"带不动"的问题，直接给到固件上限
                         # （固件其实允许到 6000；这是现场实测觉得够快的值）

# 巡航速度 / 加减速档位的默认值（2026-09-13 按用户实机手感定的）。
# ⚠ 改默认值必须连**旧默认值**一起记下来，见 `_migrating_setting`：
#   老用户的 QSettings 里已经存着旧值，"只改常量"对他们完全无效。
DEFAULT_RPM = 1200       # ≈640 mm/s 皮带线速（1200rpm ÷ 60 × 32mm）
DEFAULT_ACC = 1          # 最柔和的起步/停车。⚠ 固件里 0 = 不加速直接启动（禁止），
                         #   所以 1 才是真正的最小值
OLD_DEFAULT_RPM = 300
OLD_DEFAULT_ACC = 100

# 工作区（台面行程）默认值。360 是用户 2026-09-13 给的实测值（原默认 300）。
DEFAULT_WS = 360
OLD_DEFAULT_WS = 300

# ── 轨迹与网格（网格点选 / 圆 / 矩形 / 多段线 / 录制重放）────────────
# 走轨迹时的轮询节奏。**必须**比 POLL_MOVING_MS 快：
# 序列器是"等板子报到位 → 立刻发下一段"，而两段之间那**一帧遥测的延迟会
# 乘上段数**变成总时长 —— 63 段的圆，每段多等 200ms 就是凭空多出 12 秒。
# 板子那边一条 json 要读好几个驱动器寄存器（docs/17 §4.3 记的"双轴 10Hz
# 余量充足"），120ms 是安全且明显更快的取值。
PATH_POLL_MS = 120

# ⚠ 2026-10-10：原这里有「网格点选/吸附分辨率 GRID_DEFAULT_ROWS/COLS、GRID_MAX」。
# 用户要求台面背景只留标尺、不留格线，点选也不再吸附格心 → 这一组连同
# `gridRows/gridCols/setGrid/snapToGrid` 一起删掉了（见「轨迹与网格」那一段的注释）。

# 轨迹的"每段额外开销"**估计**（秒），只用于界面显示预计耗时。
# 它包含：json 轮询拿到"已到位"的延迟 + 固件 100ms 的 state_tick 收尾 +
# 指令往返。⚠ 这是估计值，真机跑一条轨迹后按实测校正（docs/17 记了做法）。
PATH_SEG_OVERHEAD_S = 0.22

# 轨迹起点"就在脚下"时的跳过阈值（mm）。省掉一次"移动到原地"——
# 那一次要白白等一个完整的到位周期（0.2~0.3 秒），而且毫无意义。
PATH_SKIP_START_MM = 0.3

# 录制轨迹抽稀的弦高容差（mm）。0.2mm 对 0.1mm 步长的精细操作是能看出来的，
# 所以别再放大 —— 放大就等于"重放时走的和我当初走的不是同一条路"。
REC_SIMPLIFY_TOL_MM = 0.2


def _fnum(value: Any, fallback: float = 0.0) -> float:
    """把 JSON 里的值安全地转成 float（板子偶尔会回 null/字符串）。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _inum(value: Any, fallback: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


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


class StageBridge(QObject):
    """QML 可调用的二轴平台桥。"""

    # ── 连接状态 ──────────────────────────────────────────
    connectedChanged = Signal()
    connectingChanged = Signal()
    lastErrorChanged = Signal()
    logMessage = Signal(str)              # 诊断面板的日志行

    # ── 遥测（来自 json 轮询）─────────────────────────────
    telemetryChanged = Signal()
    movingChanged = Signal()

    # ── 预设 ──────────────────────────────────────────────
    presetsChanged = Signal()

    # ── 轨迹与网格（网格点选 / 图形轨迹 / 录制重放）─────────
    pathsChanged = Signal()           # 轨迹列表变了（增删改名/内容变）
    pathChanged = Signal()            # 运行状态/进度/中止原因变了
    # ⚠ 单独一个"能不能跑"的信号，**不能复用 pathChanged**：
    #   pathCanRun/pathHint 依赖 connected/datum/travelSet/moving/recording，
    #   而这些都不发 pathChanged —— 曾经因此出现"平台已连上、底部却一直写着
    #   『未连接平台』"（因为那个 Property 从加载后再没重新求值过，正是本项目
    #   第 6 条坑的镜像：Property 的 notify 太窄 = 界面永远停在第一次的值）。
    #   反过来也不该把 pathChanged 绑到遥测上：那会让 previewPoints（要重算
    #   整条轨迹的落点）跟着 1~8Hz 的遥测反复重算。
    pathEnableChanged = Signal()
    recordChanged = Signal()          # 录制开关或已录点数变了

    def __init__(self, parent=None, settings=None):
        """settings 可注入 —— **测试必须传一个文件隔离的 QSettings**，
        否则测试会用真实程序的 `DuAD/DuADSoftware` 作用域，把假板子的
        地址/端口/口令写进用户的真实配置（这个坑踩过一次：见 tests/ 里两处用法）。"""
        super().__init__(parent)

        self._sock = QTcpSocket(self)
        self._sock.connected.connect(self._on_connected)
        self._sock.disconnected.connect(self._on_disconnected)
        self._sock.readyRead.connect(self._on_ready_read)
        self._sock.errorOccurred.connect(self._on_socket_error)

        self._settings = settings if settings is not None else QSettings("DuAD", "DuADSoftware")

        # 回零参数：存 QSettings。注意**不是连上就下发** —— 见 `_send_home_cfg` 的注释：
        # 真机上"点连接就回零"就是它引起的（驱动器收到 0x91 会动）。
        self._home_cfg = dict(HOME_DEFAULT)
        self._home_cfg_saved = False
        self._load_home_cfg()
        self._home_cfg_sent = False

        self._host = str(self._settings.value("stage/host", DEFAULT_HOST))
        self._port = _inum(self._settings.value("stage/port", DEFAULT_PORT), DEFAULT_PORT)
        self._token = str(self._settings.value("stage/token", DEFAULT_TOKEN))
        self._rpm = self._migrating_setting("stage/rpm", OLD_DEFAULT_RPM, DEFAULT_RPM)
        self._acc = self._migrating_setting("stage/acc", OLD_DEFAULT_ACC, DEFAULT_ACC)
        # 轨迹默认速度（与手动那对分开存，理由见 setPathSpeed 的注释）
        self._path_rpm = max(1, min(MAX_RPM, _inum(self._settings.value("stage/path_rpm", 1200), 1200)))
        self._path_acc = max(1, min(MAX_ACC, _inum(self._settings.value("stage/path_acc", 30), 30)))
        # 零点角写死见 DATUM_CORNER（不再有 `stage/home_corner` 这个设置项）

        # 工作区（台面 X/Y 行程，mm）。固件侧 `travel` 的单位是度，下发时换算。
        # 默认 **360 × 360**（2026-09-13 用户给的实测值；原来是 300）。
        # ⚠ 用迁移而不是直接改常量：老用户的 settings 里已经存着 300，
        #   `value(key, 360)` 是读不到 360 的 —— 用户会以为"改了没用"。
        #   自己量过并填了别的值的人不受影响（只有恰好等于 300 才迁）。
        self._ws_xmin = _fnum(self._settings.value("stage/ws_xmin", 0.0))
        self._ws_ymin = _fnum(self._settings.value("stage/ws_ymin", 0.0))
        self._ws_xmax = float(self._migrating_setting("stage/ws_xmax", OLD_DEFAULT_WS, DEFAULT_WS))
        self._ws_ymax = float(self._migrating_setting("stage/ws_ymax", OLD_DEFAULT_WS, DEFAULT_WS))

        # ── 「退出时回到零点」+「上次停在哪儿」（2026-09-13，用户提的方案）──
        #   思路：别人只用上位机 → 退出时把台面开回零点角 → 下次开机先核对
        #   "还在不在那个角上"，就能判断**基准还能不能信**。
        #   ⚠ 关键前提：**驱动器只要不断电就一直数着位置**，所以上位机重启/断线/
        #     崩溃都不影响基准；真正的杀手只有两个——**板子/驱动器掉电**（单圈编码器
        #     丢多圈位置）和**有人用手推动了台面**。
        #   前者由固件的基准闸兜住（`s_datum` 在 RAM 里，一重启就清空 → json 的
        #   datum=0 → 上位机提示重新立基准）；后者靠下面记的"上次离开时的位置"核对。
        self._park_on_exit = _bool(self._settings.value("stage/park_on_exit", True))
        # 只有**正常退出并且真的停在了零点**才记 —— 崩溃/强杀那次不记，
        # 免得下次把"上上次的位置"当成依据，报一个假警。
        park = str(self._settings.value("stage/last_park_pos", ""))
        self._last_park = None
        if "," in park:
            try:
                lx, ly = park.split(",", 1)
                self._last_park = (float(lx), float(ly))
            except ValueError:
                self._last_park = None
        self._park_check_pending = False
        self._park_failed = False

        # ── 轨迹与网格（2026-10-08）─────────────────────────
        # 轨迹列表存 QSettings（与预设同一套路）。
        self._selected_path = -1
        # 遥测帧序号：序列器的"陈旧帧"判据全靠它。
        # 为什么不用时间戳就够：一条在 move **之前**发出去的 json 可能在我们
        # 发完 move 之后才被应用，那一帧的 moving 还是 0、位置还是上一段的目标。
        # 只看时间的话，密集采样的小段（几十毫秒）会被这种帧骗过去。
        self._tel_seq = 0
        self._recording = False
        self._rec_points: List[Tuple[float, float]] = []
        self._path_speed: Tuple[int, int] = (DEFAULT_RPM, DEFAULT_ACC)
        self._dwell_cb = None
        self._dwell_timer = QTimer(self)
        self._dwell_timer.setSingleShot(True)
        self._dwell_timer.timeout.connect(self._on_dwell_timeout)
        # ⚠ 序列器是**普通 Python 对象**（不是 QObject）：它只调我们给的两个
        #   闭包 + 注入的 tr/时钟，所以能脱离 Qt 事件循环单测（tests/test_stage_traj.py）。
        #   tr=self.tr 是必须的 —— 它的日志行会画在「诊断」面板里，属于用户可见文案。
        self._runner = stage_traj.PathRunner(
            send_move=self._path_send_move,
            log=self._log,
            tr=self.tr,
            arm_dwell=self._arm_dwell)
        self._runner.on_state = self._on_path_state
        self._runner.on_progress = self._on_path_progress
        # "能不能跑"跟随这几个信号一起刷新（见 pathEnableChanged 的注释）
        for _sig in (self.telemetryChanged, self.connectedChanged,
                     self.movingChanged, self.recordChanged, self.pathChanged):
            _sig.connect(self.pathEnableChanged.emit)

        # ── 状态位 ──────────────────────────────────────
        self._connecting = False
        self._authenticated = False
        self._last_error = ""
        self._error_kind = None      # None / "transient" / "gate" / "action"
        self._error_gate = None      # kind=="gate" 时记是哪道闸
        self._rx_buf = b""

        # 遥测镜像
        self._pos_x = 0.0            # mm
        self._pos_y = 0.0            # mm
        self._voltage = 0.0
        self._enabled = False
        self._datum = False
        self._travel_set = False
        self._moving = False
        self._last_move = "none"
        # 回零状态（固件 json 的 "homing"）：
        #   0=本次会话还没回零过 / 1=正在回零 / 2=完成 / 3=失败或被打断
        # 为什么单独跟踪而不是复用 moving：回零**没有目标位置**，判不了"到位"，
        # 界面需要区分"在动"和"在回零"（回零中要提示"可随时按停止"）。
        self._homing = 0
        self._rssi = 0
        self._board_ip = ""
        self._jog_axis = (0, 0)      # 关节角度（诊断用，度）

        # ── 命令队列（单在途：板子的 TCP 任务是串行的，发多了只会排更长的队）──
        self._queue: List[Dict[str, Any]] = []
        self._in_flight: Optional[Dict[str, Any]] = None
        self._resp_lines: List[str] = []

        # 轮询定时器：连上且认证通过后才跑
        self._poll = QTimer(self)
        self._poll.setInterval(POLL_IDLE_MS)
        self._poll.timeout.connect(self._poll_tick)

        # 在途命令超时
        self._cmd_timer = QTimer(self)
        self._cmd_timer.setSingleShot(True)
        self._cmd_timer.timeout.connect(self._on_cmd_timeout)

        # 建连超时（连不存在的 IP 时 Qt 自己不超时，会一直挂在 ConnectingState）
        self._connect_timer = QTimer(self)
        self._connect_timer.setSingleShot(True)
        self._connect_timer.timeout.connect(self._on_connect_timeout)

        self._log(self.tr("StageBridge 就绪（单位：界面 mm ↔ 固件度，1mm = 11.25°）"))

    # ============================================================
    # 内部工具
    # ============================================================
    def _migrating_setting(self, key: str, old_default: int, new_default: int) -> int:
        """读一个"有默认值"的设置；**值恰好等于旧默认值时按"没设过"处理**，迁到新默认值。

        为什么需要：改了默认值常量对老用户是**无效**的 —— 他们的 QSettings 里
        已经存着旧值了，`value(key, 新默认)` 永远读不到新默认。
        （用户要的就是"默认改成 1200/1"，所以这里必须迁。）
        用户自己调过的值不等于旧默认值，不会被碰。
        """
        v = _inum(self._settings.value(key, new_default), new_default)
        if v == old_default and old_default != new_default:
            self._settings.setValue(key, new_default)
            self._log(self.tr('速度默认值更新：{} {} → {}（没改过的话就跟着新默认走，改过的值不受影响）').format(key.split('/')[-1], old_default, new_default))
            return new_default
        return v

    def _log(self, msg: str):
        print(f"[StageBridge] {msg}")
        self.logMessage.emit(msg)

    def _gate_error(self, gate: str, msg: str):
        """记录"某道闸没满足"的错误。`gate` ∈ {"datum","travel","homecfg"}。

        这类错误的特点是**条件一旦满足它就该消失** —— 否则会出现自相矛盾：
        状态条上「已立基准/行程已设」全绿，下面却还挂着红色的
        "还没有基准：先「设为原点」"（截图里真实出现过）。
        用户看到这种矛盾会开始不信任整个界面。
        """
        self._error_gate = gate
        self._set_error(msg, kind="gate")

    def _set_error(self, msg: str, kind: str = "action"):
        """设置错误提示。

        `kind` 三种：
        - `"transient"` 传输类（超时 / socket / 断开）→ **链路恢复就自动清**。
          踩过的坑：原来只有一种错误、且只在"重新连接"时清空，所以一次超时之后
          诊断面板会一直挂着"超时（板子没有应答）"，哪怕板子早就在正常应答。
        - `"gate"` 前置条件缺失（缺基准 / 未设工作区 / 未配回零参数）→
          **条件满足时自动清**（`_maybe_clear_error`）。
        - `"action"`（默认）其它要用户处理的 → 不自动清，否则用户来不及看见原因。
        """
        self._last_error = msg
        self._error_kind = kind if msg else None
        if not msg:
            self._error_gate = None
        self.lastErrorChanged.emit()
        if msg:
            self._log(self.tr('错误: {}').format(msg))

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
                or (g == "travel" and self._travel_set)
                or (g == "homecfg" and self._home_cfg_sent)
                or (g is None and self._datum and self._travel_set)
            )
            if satisfied:
                self._log(self.tr("前置条件已满足，清掉之前的闸提示"))
                self._set_error("")

    # ============================================================
    # 连接状态属性
    # ============================================================
    def _get_connected(self) -> bool:
        return (self._sock.state() == QAbstractSocket.SocketState.ConnectedState
                and self._authenticated)

    connected = Property(bool, _get_connected, notify=connectedChanged)

    def _get_connecting(self) -> bool:
        return self._connecting

    connecting = Property(bool, _get_connecting, notify=connectingChanged)

    def _get_last_error(self) -> str:
        return self._last_error

    lastError = Property(str, _get_last_error, notify=lastErrorChanged)

    def _get_host(self) -> str:
        return self._host

    host = Property(str, _get_host, notify=telemetryChanged)

    def _get_port(self) -> int:
        return self._port

    port = Property(int, _get_port, notify=telemetryChanged)

    def _get_token(self) -> str:
        return self._token

    token = Property(str, _get_token, notify=telemetryChanged)

    def _get_rpm(self) -> int:
        return self._rpm

    rpm = Property(int, _get_rpm, notify=telemetryChanged)

    # 两个滑块的量程由这里给（唯一事实源）。
    # ⚠ 别再在 QML 里硬编码 `to: 1200` —— 那正是"同一个事实两份、改一处不生效"。
    def _get_ui_max_rpm(self) -> int:
        return UI_MAX_RPM

    uiMaxRpm = Property(int, _get_ui_max_rpm, notify=telemetryChanged)

    def _get_ui_max_acc(self) -> int:
        return UI_MAX_ACC

    uiMaxAcc = Property(int, _get_ui_max_acc, notify=telemetryChanged)

    def _get_acc(self) -> int:
        return self._acc

    acc = Property(int, _get_acc, notify=telemetryChanged)

    # ============================================================
    # 遥测属性（全部为界面单位：mm / V / dBm）
    # ============================================================
    def _get_pos_x(self) -> float:
        return self._pos_x

    posX = Property(float, _get_pos_x, notify=telemetryChanged)

    def _get_pos_y(self) -> float:
        return self._pos_y

    posY = Property(float, _get_pos_y, notify=telemetryChanged)

    def _get_voltage(self) -> float:
        return self._voltage

    voltage = Property(float, _get_voltage, notify=telemetryChanged)

    def _get_enabled(self) -> bool:
        return self._enabled

    enabled = Property(bool, _get_enabled, notify=telemetryChanged)

    @Slot(bool, result=bool)
    def setMotorEnabled(self, on: bool) -> bool:
        """使能 / 失能两个电机（固件 `en all` / `dis all`）。

        为什么界面要给这个开关（用户 2026-09-13 提的）：
          · **失能是为了用手推台面** —— 调机械、对基准时得让它松掉；
          · **使能是为了顶住位置** —— 失能状态下台面可以被外力推动（或自重下滑），
            而"位置对不对"完全依赖编码器连续计数，**推一下坐标系就废了**。
            ⚠ 而且 `dis` 之后 json 会一直报"未使能"，用户看到却没法改回来 ——
            那是"点了没反应"的镜像：**状态能看见、却没有对应的操作**。

        ⚠ 三个已知的坑（固件侧，注释在 console_cmds.c 的 en_dis_common）：
          1. `zero`(0xF8) 和**任何运动命令都会自动使能** —— 所以"失能"要放在这些之后，
             否则你以为失能了、其实一动就又使能了；
          2. `dis` 有可能**回了 ok 却没生效**（驱动器还停在运动/回零状态，或硬件 EN 脚
             把它钉住）——固件会读回 0x2F 确认并把原因打在日志里，这里只要转述；
          3. 运动中的驱动器会自己保持使能，`0xFA` 的失能被它覆盖。
        """
        if not self.connected:
            self._set_error(self.tr("未连接平台"))
            return False
        if not on:
            # 失能 = 台面可以被外力推动。轨迹继续走毫无意义：坐标系已经从
            # 编码器脚下溜走了，接着走就是"按错误的坐标走到错误的地方"。
            self._abort_path(self.tr("电机失能"))
        cmd = "en all" if on else "dis all"
        self._enqueue(cmd, note=self.tr("使能电机") if on else self.tr("失能电机"))
        self._log("→ {}（{}）".format(cmd, self.tr("运动命令之后会自动使能，注意顺序") if not on
                                     else self.tr("台面会被顶住，手推不动")))
        return True

    def _get_datum(self) -> bool:
        return self._datum

    datum = Property(bool, _get_datum, notify=telemetryChanged)

    def _get_travel_set(self) -> bool:
        return self._travel_set

    travelSet = Property(bool, _get_travel_set, notify=telemetryChanged)

    def _get_moving(self) -> bool:
        return self._moving

    moving = Property(bool, _get_moving, notify=movingChanged)

    def _get_last_move(self) -> str:
        return self._last_move

    lastMove = Property(str, _get_last_move, notify=telemetryChanged)

    def _get_homing(self) -> int:
        return self._homing

    homing = Property(int, _get_homing, notify=telemetryChanged)

    def _get_homing_text(self) -> str:
        return {
            0: "",
            1: self.tr("正在回零…（要停下直接按「停止」—— 回零期间它也进得去）"),
            2: self.tr("最近一次回零：完成（两轴已有基准）"),
            3: self.tr("最近一次回零：失败或被打断 —— 看下面的日志"),
        }.get(self._homing, "")

    homingText = Property(str, _get_homing_text, notify=telemetryChanged)

    def _get_rssi(self) -> int:
        return self._rssi

    rssi = Property(int, _get_rssi, notify=telemetryChanged)

    def _get_board_ip(self) -> str:
        return self._board_ip

    boardIp = Property(str, _get_board_ip, notify=telemetryChanged)

    # ⚠ 必须是**带 notify 的 Property**，不能是 @Slot（函数）。
    #   QML 里写 `text: StageBridge.diagText()` 这种"绑定里调函数"**没有依赖追踪** ——
    #   只在创建时求值一次，之后永远不刷新。踩过的坑：诊断面板因此一直显示
    #   "板子状态 未连接"，哪怕已经连上并在正常轮询。
    #   凡是要跟着状态变的文本，一律用 Property + notify。
    def _get_datum_hint(self) -> str:
        """给界面用的一句人话：现在能不能做绝对运动，不能的话缺什么。

        对应固件那四道闸。把它们翻译成界面提示，比让用户对着灰按钮猜要好。
        """
        if not self.connected:
            return self.tr("未连接平台")
        if not self._datum:
            # 单圈编码器：掉电必丢，所以每次上电都得重立一次（口径见《使用说明》§6.3）
            return self.tr("缺少基准（每次上电都要重立）：推到位后点「设为原点」")
        if not self._travel_set:
            return self.tr("未设置工作区：绝对移动会被固件拒绝")
        return ""

    datumHint = Property(str, _get_datum_hint, notify=telemetryChanged)

    # ============================================================
    # 退出时回到零点（用户 2026-09-13 提的方案）
    # ============================================================
    def _get_park_on_exit(self) -> bool:
        return self._park_on_exit

    parkOnExit = Property(bool, _get_park_on_exit, notify=telemetryChanged)

    @Slot(bool)
    def setParkOnExit(self, on: bool):
        self._park_on_exit = bool(on)
        self._settings.setValue("stage/park_on_exit", self._park_on_exit)
        self._log(self.tr("退出时回到零点：{}").format(self.tr("开") if self._park_on_exit else self.tr("关")))
        self.telemetryChanged.emit()

    @Slot()
    def forgetParkPos(self):
        """清掉"上次停在哪儿"的记录（重新手动立基准时调）。

        为什么立完基准要清：那一刻台面在零点，而记录里可能还是上次停在别处的
        旧值 —— 留着它，下次连上就会报一个毫无意义的"位置变了"。
        """
        self._last_park = None
        self._settings.remove("stage/last_park_pos")
        self._log(self.tr("已清掉「上次停在哪儿」的记录（重新立基准后不需要它了）"))

    def _park_target(self):
        """回零点该去哪 —— 按工作区夹一下（工作区可能不是从 0 开始）。"""
        return self._clamp(0.0, 0.0)

    @Slot()
    def parkAndDisconnect(self):
        """退出时调用：先把台面开回零点角，再断开。

        ⚠ 三点必须说清楚（否则以后会有人以为"不回零点基准就丢了"）：
        1. **不回零点，基准也不会丢** —— 驱动器只要不断电就一直数着位置，
           上位机什么时候关、断线、崩掉都不影响。所以这一步**不是保鲜手段**。
        2. 它的真正价值是**把台面留在一个已知姿态**：下次开机能用"还在不在这个角上"
           来核对"走之后有没有人动过它"（`_check_park`），而且便于手动复核/重立基准。
        3. 这一步**失败也无所谓**：位置跟踪是连续的，停在半路坐标照样准。
           所以这里只记日志，不报错、不阻塞退出超过 `PARK_TIMEOUT_MS`。
        """
        # 先把"上次停在哪儿"清掉：只有这一趟**真的停稳在零点**才会重新写上。
        # 崩溃/超时那次就留空 → 下次连接不做位置核对（宁可不查，也不报假警）。
        if self.connected and self._datum:
            self._settings.remove("stage/last_park_pos")
        # 退出的回零点也是一条 `move`：轨迹还活着的话，两条会互相覆盖目标
        self._abort_path(self.tr("退出程序"))
        if self._recording:
            self.cancelRecording()
        if not self.connected:
            self.disconnectDevice()
            return
        if not self._park_on_exit or not self._datum or not self._travel_set or self._moving:
            self._log(self.tr("退出：这次不回零点（{}）").format(
                self.tr("设置里关掉了") if not self._park_on_exit else
                self.tr("还没有基准") if not self._datum else
                self.tr("还没设工作区") if not self._travel_set else self.tr("台面正在动")))
            self.disconnectDevice()
            return

        tx, ty = self._park_target()
        self._log(self.tr("退出：正在回到零点 ({:.1f}, {:.1f}) mm …").format(tx, ty))
        # ⚠ `_enqueue` 没有返回值（它只排队 + 泵出去），别写成 `if not self._enqueue(...)`
        #   —— 那样 `not None` 恒为真，会"发了就走"（真机上表现为永远不回零点）。
        self._enqueue(self._move_cmd(tx, ty), note=self.tr("退出回零点"))

        # 阻塞等一下（退出流程，等得值），但**有上限** —— 绝不为了"停得漂亮"
        # 把程序卡住（`stop all` 也随时能打断它）。
        # 两段等：① 命令被应答；② 固件把 moving 归 0（json 轮询是秒级的，得给它时间）。
        app = QCoreApplication.instance()

        def _pump(ms: int) -> None:
            if app is not None:
                app.processEvents()
            QThread.msleep(ms)

        waited = 0
        while (waited < PARK_TIMEOUT_MS and self._authenticated and
               (self._in_flight is not None or self._queue)):
            _pump(50)
            waited += 50
        settle = 0
        while (waited < PARK_TIMEOUT_MS and settle < 2000 and
               self._authenticated and self._moving):
            _pump(50)
            waited += 50
            settle += 50

        if (self._in_flight is not None or self._queue or self._moving
                or not self._authenticated):
            self._log(self.tr("⚠ 退出回零点没等到位（超时 {}s / 或连接断了）—— 台面位置仍然准，"
                      "只是没停在零点角上").format(PARK_TIMEOUT_MS // 1000))
            self._park_failed = True
        else:
            self._log(self.tr("✓ 已停在零点 ({:.2f}, {:.2f}) mm").format(self._pos_x, self._pos_y))
            self._last_park = (self._pos_x, self._pos_y)
            self._settings.setValue("stage/last_park_pos",
                                    "{:.3f},{:.3f}".format(self._pos_x, self._pos_y))
            self._park_failed = False
        self.disconnectDevice()

    def _check_park(self):
        """连上后第一次拿到位置时核对："我们走之后，台面被动过吗？"

        ⚠⚠ 判据**不能只看位置是不是 0**：驱动器上电时也是从 0 开始数的，
        于是"随便停在哪儿"和"正好停在零点"读出来一模一样。
        真正能区分的是**固件的基准闸**（`datum`，在 RAM 里，板子一重启就清空）：
          · `datum == 0` → 板子重启过 → 驱动器丢了多圈位置 → 基准作废，必须重立；
          · `datum == 1` 且位置和上次离开时一致 → 基准仍然可信；
          · `datum == 1` 但位置对不上 → 有人推动了台面/皮带打滑 → 提示重立。
        """
        self._park_check_pending = False
        if not self._datum:
            # ⚠ 说全两种原因：板子重启会清基准闸；**只关 24V 不关板子**也会 ——
            #   后者由固件的 bus_watch_tick() 检测到（驱动器的编码器丢了多圈位置）。
            #   只说"板子重启过"会把人往错方向带（他明明没重启过板子）。
            self._log(self.tr("⚠ 基准闸是空的：**板子重启过，或者 24V 掉过电** → "
                      "驱动器丢了多圈位置，坐标作废，需要重新立一次基准："
                      "把滑座推到靠块/硬限位，再点「⌂ 设为原点」"))
            return
        if self._last_park is None:
            return                      # 上次不是正常退出（崩溃/超时），不做核对
        dx = abs(self._pos_x - self._last_park[0])
        dy = abs(self._pos_y - self._last_park[1])
        if dx > PARK_DRIFT_MM or dy > PARK_DRIFT_MM:
            self._log(self.tr("⚠ 台面位置和上次离开时不一样：现在 ({:.2f}, {:.2f})，"
                      "上次停在 ({:.2f}, {:.2f}) mm —— 相差 {:.2f}mm。"
                      "要么有人手动挪过，要么皮带打滑。"
                      "坐标可能已经不准，建议重新立基准。").format(
                          self._pos_x, self._pos_y,
                          self._last_park[0], self._last_park[1], max(dx, dy)))

    @Slot(result=bool)
    def canMove(self) -> bool:
        """是否允许下发运动（界面按钮的 enabled 用它）。"""
        return self.connected and self._datum and self._travel_set and not self._moving

    # ============================================================
    # 工作区（台面行程，mm）—— 固件侧 `travel` 用度
    # ============================================================
    def _get_ws_xmin(self) -> float:
        return self._ws_xmin

    wsXMin = Property(float, _get_ws_xmin, notify=telemetryChanged)

    def _get_ws_ymin(self) -> float:
        return self._ws_ymin

    wsYMin = Property(float, _get_ws_ymin, notify=telemetryChanged)

    def _get_ws_xmax(self) -> float:
        return self._ws_xmax

    wsXMax = Property(float, _get_ws_xmax, notify=telemetryChanged)

    def _get_ws_ymax(self) -> float:
        return self._ws_ymax

    wsYMax = Property(float, _get_ws_ymax, notify=telemetryChanged)

    @Slot(float, float, float, float)
    def setWorkspace(self, xmin: float, ymin: float, xmax: float, ymax: float):
        """保存工作区并下发给固件（单位换算成度）。

        固件对 `g0`/`move` 是 fail-closed 的：不设行程就一律拒绝。
        所以连上之后必须把工作区推下去，否则用户点任何移动都是"被拒绝"。
        """
        # 允许用户把两个角点反过来填
        self._ws_xmin = min(xmin, xmax)
        self._ws_xmax = max(xmin, xmax)
        self._ws_ymin = min(ymin, ymax)
        self._ws_ymax = max(ymin, ymax)

        self._settings.setValue("stage/ws_xmin", self._ws_xmin)
        self._settings.setValue("stage/ws_ymin", self._ws_ymin)
        self._settings.setValue("stage/ws_xmax", self._ws_xmax)
        self._settings.setValue("stage/ws_ymax", self._ws_ymax)
        self.telemetryChanged.emit()

        if self.connected:
            self._send_travel()

    def _send_travel(self):
        cmd = "travel {:.3f} {:.3f} {:.3f} {:.3f}".format(
            self._ws_xmin * DEG_PER_MM, self._ws_ymin * DEG_PER_MM,
            self._ws_xmax * DEG_PER_MM, self._ws_ymax * DEG_PER_MM)
        self._enqueue(cmd, note=self.tr("下发工作区"))

    @Slot(int, int)
    def setSpeed(self, rpm: int, acc: int):
        """速度/加减速只存在上位机，每次移动时带上（固件的 move 支持它们作参数）。"""
        # 夹到固件真的会接受的范围（见 MAX_ACC 的注释：超了固件直接拒帧）
        self._rpm = max(1, min(MAX_RPM, int(rpm)))
        self._acc = max(1, min(MAX_ACC, int(acc)))
        self._settings.setValue("stage/rpm", self._rpm)
        self._settings.setValue("stage/acc", self._acc)
        self.telemetryChanged.emit()

    # ── 轨迹速度（2026-10-10）──────────────────────────────────
    # ⚠ 与手动点动速度**刻意分开**存储（用户要求"轨迹的 rpm/acc 放到设置界面改"）。
    #   为什么不能直接沿用 `rpm`/`acc`：手动点动是"按住就走一小段"，
    #   默认 acc 是 **1**；而轨迹每段都是一条**完整的梯形曲线**（起步→匀速→刹停），
    #   5mm 的小段用 acc=1 会全程在爬坡，一段要好几秒 —— 整条轨迹慢到没法用。
    #   所以轨迹有自己的一对默认值（1200 / 30），两者都在「二轴相机平台设置」里改。
    def _get_path_rpm(self) -> int:
        return self._path_rpm

    pathRpm = Property(int, _get_path_rpm, notify=pathChanged)

    def _get_path_acc(self) -> int:
        return self._path_acc

    pathAcc = Property(int, _get_path_acc, notify=pathChanged)

    @Slot(int, int)
    def setPathSpeed(self, rpm: int, acc: int):
        """轨迹默认速度（存本机，每次保存轨迹时随每条轨迹一起记下）。"""
        self._path_rpm = max(1, min(MAX_RPM, int(rpm)))
        self._path_acc = max(1, min(MAX_ACC, int(acc)))
        self._settings.setValue("stage/path_rpm", self._path_rpm)
        self._settings.setValue("stage/path_acc", self._path_acc)
        self.pathChanged.emit()
        self._log(self.tr("轨迹速度：{} rpm / acc {}").format(self._path_rpm, self._path_acc))

    # ============================================================
    # 连接 / 断开
    # ============================================================
    @Slot(str, int, str, result=bool)
    def connectDevice(self, host: str, port: int, token: str) -> bool:
        if self._sock.state() != QAbstractSocket.SocketState.UnconnectedState:
            self._sock.abort()

        host = (host or "").strip()
        token = (token or "").strip()
        # 这两条是"点了没反应"的头号原因：参数没填全就直接拒绝，卡片不会有任何变化。
        # 所以提示里必须说清**去哪填**、**怎么拿**，页面还会顺手把设置面板展开。
        if not host:
            self._set_error(self.tr("还没填板子的 IP —— 请在下面「平台设置」里填入，"
                            "板子 USB 控制台敲 net 就会打印出来"))
            return False
        if not token:
            self._set_error(self.tr("还没填连接口令 —— 请在下面「平台设置」里填入，"
                            "板子 USB 控制台敲 net 就会打印出来"))
            return False

        new_port = int(port) if port else DEFAULT_PORT
        # 已经连着同一个目标：只更新参数，**不要断开重连**。
        # 用户点「应用设置」多半只是想改速度/工作区，重连会让状态闪一下、
        # 还白等一次握手（连不上时更糟）。
        if (self._get_connected() and host == self._host
                and new_port == self._port and token == self._token):
            self._log(self.tr("已连接同一目标，只更新参数（不重连）"))
            self._settings.setValue("stage/host", host)
            self._settings.setValue("stage/port", new_port)
            self._settings.setValue("stage/token", token)
            return True

        self._host = host
        self._port = new_port
        self._token = token
        self._settings.setValue("stage/host", self._host)
        self._settings.setValue("stage/port", self._port)
        self._settings.setValue("stage/token", self._token)

        self._authenticated = False
        self._rx_buf = b""
        self._resp_lines = []
        self._in_flight = None
        self._queue = []
        self._set_error("")
        self._connecting = True
        self.connectingChanged.emit()
        self.telemetryChanged.emit()

        self._log(self.tr('正在连接 {}:{} …').format(self._host, self._port))
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
            self.tr('连接 {}:{} 超时（{} 秒）。排查：① 电脑和板子连的是同一个热点吗 ② 板子 IP 有没有变（手机热点每次可能不同，板子上敲 net 再看一眼）③ 手机热点是不是开了「客户端隔离」').format(self._host, self._port, CONNECT_TIMEOUT_MS // 1000), kind="transient")

    @Slot()
    def disconnectDevice(self):
        self._abort_path(self.tr("断开连接"))
        if self._recording:
            self.cancelRecording()
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
        self._moving = False
        self.connectingChanged.emit()
        self.connectedChanged.emit()
        self.movingChanged.emit()
        self._log(self.tr("已断开"))

    def _on_connected(self):
        self._connect_timer.stop()
        self._connecting = False
        self.connectingChanged.emit()
        self._log(self.tr("TCP 已连接，正在交口令 …"))
        # 协议要求：第一行必须是口令
        self._sock.write(self._token.encode("utf-8") + b"\n")

    def _on_disconnected(self):
        was = self._get_connected()
        # 断线之后板子的状态完全未知：轨迹没法接着走（连"到没到位"都收不到）
        self._abort_path(self.tr("连接已断开"))
        if self._recording:
            self.cancelRecording()
        # 断线后固件那边的 hset 状态未知（可能是板子重启），闸重新关上
        self._home_cfg_sent = False
        self._connect_timer.stop()
        self._poll.stop()
        self._cmd_timer.stop()
        self._authenticated = False
        self._connecting = False
        self._in_flight = None
        self._queue = []
        if self._moving:
            self._moving = False
            self.movingChanged.emit()
        self.connectingChanged.emit()
        if was:
            self._set_error(self.tr("与板子的连接已断开"), kind="transient")
        self.connectedChanged.emit()
        self._log(self.tr("连接已关闭"))

    def _on_socket_error(self, err):
        if err == QAbstractSocket.SocketError.RemoteHostClosedError:
            return                      # 正常断开，不用报错
        self._connect_timer.stop()
        self._connecting = False
        self.connectingChanged.emit()
        self._set_error(self.tr('连接失败: {}').format(self._sock.errorString()), kind="transient")

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
        # ⓪ 驱动器原始帧（固件 `trace on` 时镜像回来的，`@TX …` / `@RX …`）。
        # 2026-10-10 起界面上的协议显示框与「帧镜像」开关整套删掉了，所以这里
        # **直接丢掉**：板子那边可能还开着 trace，若不拦，这些帧会被当成命令输出
        # 塞进 `_resp_lines`，排障时反而误导。
        if line.startswith("@"):
            return

        # ① 认证应答（还没进入正常命令流程）
        if not self._authenticated:
            if line == "#OK auth":
                self._authenticated = True
                self._log(self.tr("口令通过，通道可用"))
                self._set_error("")
                self.connectedChanged.emit()
                self._poll.start(POLL_IDLE_MS)
                # 连上第一件事：把工作区推给固件（否则 fail-closed 会拒绝一切移动）
                self._send_travel()
                # ⚠ 这里以前还有一句 `if self._home_cfg_saved: self._send_home_cfg()`，
                #   发的是 `hset`（0x91 写驱动器）。真机实测：**这台 PD42S1 收到 0x91
                #   就会自己开始找零点** —— 于是"点一下连接，台面自己回零了"。
                #   （用户原话："这个自动回零会在上位机点击连接的时候回零，其他情况不会"。）
                #   现在改用 `hcfg`：只把参数登记在固件 RAM 里，**完全不碰驱动器**。
                #   真正的 0x91 由 `home corner` 在受控时刻、带着算好的方向去写。
                if self._home_cfg_saved:
                    self._send_home_cfg()
                # 等第一次遥测到手再做"台面有没有被动过"的核对（_check_park）
                self._park_check_pending = True
            elif line.startswith("#ERR"):
                self._set_error(self.tr('口令被拒（{}）—— 在板子 USB 控制台敲 net 看正确口令').format(line))
                self.disconnectDevice()
            return

        # ② 命令应答的结束标记。
        #    注意只有**精确等于** #OK 或**以 #ERR 开头**才算标记 —— 命令输出里
        #    可能出现以 # 开头的普通行（固件 ver 的输出就有）。
        if line == "#OK" or line.startswith("#ERR"):
            self._finish_command(line == "#OK", line)
            return

        if line:
            self._resp_lines.append(line)
            # json 的应答只有一行，直接解析，不必等结束标记
            if line.startswith("{") and self._in_flight and self._in_flight.get("json"):
                self._apply_json(line)

    def _finish_command(self, ok: bool, marker: str):
        flight = self._in_flight
        self._in_flight = None
        self._cmd_timer.stop()

        if flight is not None and not ok:
            note = flight.get("note", flight.get("cmd", self.tr("命令")))
            detail = " / ".join(self._resp_lines[-3:]) if self._resp_lines else marker
            self._set_error(self.tr('{} 被拒绝：{}').format(note, detail))
        elif flight is not None and flight.get("note"):
            self._log(self.tr('{} 完成').format(flight['note']))
            # 命令成功时固件也常常带警告（⚠ 开头）—— 比如 home 会报告驱动器里的
            # 限位电流/回零超时/上电自动回零。以前这些行只在失败时才显示，
            # 等于把最有用的排查信息扔掉了。
            # verbose=True 的命令（回零系列）连普通行一起显示：那里面有
            # "第 1 趟/第 2 趟"和每趟两轴的方向，是排查回零唯一的一手信息。
            for ln in self._resp_lines:
                if "⚠" in ln or flight.get("verbose"):
                    self._log(ln.strip())

        if ok:
            # 链路恢复了就该把"超时/没有应答"这类提示收掉（粘性错误不动）
            self._maybe_clear_error()

        self._resp_lines = []
        self._pump_queue()

    def _on_cmd_timeout(self):
        flight = self._in_flight
        self._in_flight = None
        if flight is not None:
            self._set_error(self.tr('{} 超时（板子没有应答）').format(flight.get('note', flight.get('cmd'))),
                            kind="transient")
        self._resp_lines = []
        self._pump_queue()

    # ============================================================
    # 命令队列
    # ============================================================
    def _enqueue(self, cmd: str, note: str = "", timeout_ms: int = CMD_TIMEOUT_MS,
                 json_reply: bool = False, front: bool = False, verbose: bool = False):
        if not self._authenticated:
            self._set_error(self.tr("未连接平台，命令未发送"))
            return
        # 把非轮询命令原样记进日志（诊断面板能看到）。
        # 这是"我调了速度到底生效没有"最直接的证据 —— 移动命令里就带着 rpm/acc。
        # json 轮询不记，否则日志会被刷屏。
        if not json_reply:
            self._log(f"→ {cmd}")
        item = {"cmd": cmd, "note": note, "timeout_ms": timeout_ms, "json": json_reply,
                "verbose": verbose}
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
        # 注意 json 在固件侧还会顺手判定"非阻塞移动是否到位"，所以轮询本身就是到位检测。
        want = POLL_MOVING_MS if self._moving else POLL_IDLE_MS
        # ⚠ 跑轨迹时必须用**固定的**快节奏，不能跟着 `_moving` 在 200ms/1000ms
        #   之间来回跳。踩过的坑：每一段到位的那一帧里 moving 会变 0，若此时
        #   把间隔切回 1000ms，下一段的"已到位"就要等整整 1 秒才拿到 ——
        #   63 段的圆凭空多出 60 秒，而且现象是"轨迹一段快一段慢"，极难归因。
        if self._runner.busy:
            want = PATH_POLL_MS
        if self._poll.interval() != want:
            self._poll.setInterval(want)
        # 队列里有别的命令就先不发轮询（避免把用户的命令挤到后面）
        if self._queue or self._in_flight is not None:
            return
        self._enqueue("json", json_reply=True)

    def _apply_json(self, line: str):
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return
        if "err" in data:
            self._set_error(self.tr('板子读位置失败：{}').format(data['err']))
            return

        self._pos_x = _fnum(data.get("xdeg")) * MM_PER_DEG
        self._pos_y = _fnum(data.get("ydeg")) * MM_PER_DEG
        self._jog_axis = (_fnum(data.get("adeg")), _fnum(data.get("bdeg")))
        self._voltage = _fnum(data.get("v"))
        self._enabled = bool(_inum(data.get("en")))
        self._datum = bool(_inum(data.get("datum")))
        self._travel_set = bool(_inum(data.get("travel")))
        self._last_move = str(data.get("last", "none"))
        self._homing = _inum(data.get("homing"))
        self._rssi = _inum(data.get("rssi"))
        self._board_ip = str(data.get("ip", ""))

        # 拿到最新遥测后，看看之前那条"闸提示"是不是已经过时了
        self._maybe_clear_error()

        # 连上后的第一次遥测：核对"我们不在的时候台面被动过没有"（见 _check_park）
        if self._park_check_pending:
            self._check_park()

        moving = bool(_inum(data.get("moving")))
        if moving != self._moving:
            self._moving = moving
            self.movingChanged.emit()

        # ── 序列器的遥测驱动（必须放在 moving 算出来之后）──
        # "是否到位"用的就是**这一帧**的 moving + 位置，两者必须来自同一次
        # json 采样 —— 一个用新值、一个用上一帧的旧值，就会出现"位置到了但
        # moving 还是 1"或反过来的假判据。帧序号一并带上（陈旧帧闸门）。
        self._tel_seq += 1
        if self._runner.busy:
            self._runner.on_telemetry(self._tel_seq, self._pos_x, self._pos_y, moving)

        self.telemetryChanged.emit()

    def _get_diag_text(self) -> str:
        """诊断面板用的一行摘要（含原始角度，排查时才看）。"""
        if not self.connected:
            return self.tr("未连接")
        return (self.tr("X {:.2f} mm / Y {:.2f} mm  |  关节 A {:.1f}° B {:.1f}°  |  "
                "{:.1f}V  {}  |  信号 {} dBm  板子 {}").format(
                    self._pos_x, self._pos_y, self._jog_axis[0], self._jog_axis[1],
                    self._voltage, self.tr("已使能") if self._enabled else self.tr("未使能"),
                    self._rssi, self._board_ip or "?"))

    diagText = Property(str, _get_diag_text, notify=telemetryChanged)

    # ============================================================
    # 运动
    # ============================================================
    def _move_cmd(self, x_mm: float, y_mm: float,
                  rpm: Optional[int] = None, acc: Optional[int] = None) -> str:
        # 固件的角度域：1mm = 11.25°
        # rpm/acc 可覆盖：轨迹有自己的速度（默认 acc 会明显大于手动点动的
        # acc=1 —— 5mm 的小段用 acc=1 的话每段全程都在爬坡，根本走不动）。
        return "move {:.3f} {:.3f} {} {}".format(
            x_mm * DEG_PER_MM, y_mm * DEG_PER_MM,
            self._rpm if rpm is None else rpm,
            self._acc if acc is None else acc)

    def _clamp(self, x_mm: float, y_mm: float):
        """按工作区夹取目标。

        固件侧有行程闸会拒绝越界，但那是**兜底**；界面先把目标夹住并显示夹取后的值，
        用户才不会看到"点了没反应"。两道防线各有各的用处。
        """
        return (max(self._ws_xmin, min(self._ws_xmax, x_mm)),
                max(self._ws_ymin, min(self._ws_ymax, y_mm)))

    def _issue_move(self, x_mm: float, y_mm: float, rpm: Optional[int] = None,
                    acc: Optional[int] = None, record: bool = True,
                    note: Optional[str] = None) -> bool:
        """**所有绝对移动的唯一出口** —— 手动 / 预设 / 网格点选 / 轨迹序列器都走它。

        为什么要收敛成一个出口：`moveTo` 是录制功能的唯一挂钩点。只要守住这一个
        口子，"十字点动 / 绝对定位输入 / 网格点击 / 预设前往"就**全都自动被录下来**，
        不必在 QML 的每个按钮上都记得写一句"顺便记录一下"（那迟早会漏一个）。

        序列器自己发的段传 `record=False` —— 否则重放一条轨迹会把它录成两倍长
        （一边放一边录）。
        """
        if not self.connected:
            self._set_error(self.tr("未连接平台"))
            return False
        if not self._datum:
            self._gate_error("datum", self.tr("还没有基准：先「设为原点」或自动回零"))
            return False
        if not self._travel_set:
            self._gate_error("travel", self.tr("还没设置工作区，绝对移动会被固件拒绝"))
            return False

        cx, cy = self._clamp(float(x_mm), float(y_mm))
        if abs(cx - x_mm) > 0.05 or abs(cy - y_mm) > 0.05:
            self._log(self.tr('目标已按工作区夹取: ({:.2f},{:.2f}) → ({:.2f},{:.2f}) mm').format(x_mm, y_mm, cx, cy))
        self._enqueue(self._move_cmd(cx, cy, rpm, acc),
                      note=(note if note is not None
                            else self.tr('移动到 ({:.2f}, {:.2f}) mm').format(cx, cy)),
                      timeout_ms=MOVE_TIMEOUT_MS)
        if record and self._recording:
            # 记的是**夹取后的目标**，和固件实际收到的目标一致；
            # 相邻重复点由 stopRecording 里的 dedupe 收掉。
            self._rec_points.append((cx, cy))
            self.recordChanged.emit()
        # 乐观更新：立刻标成运动中，让 UI 马上禁用按钮、加快轮询
        if not self._moving:
            self._moving = True
            self.movingChanged.emit()
        return True

    @Slot(float, float, result=bool)
    def moveTo(self, x_mm: float, y_mm: float) -> bool:
        """绝对定位（非阻塞）。

        ⚠ 轨迹运行期间一律拒绝手动移动：固件的 `move` 是**读回当前位置**算增量的，
        而它允许在途时再发一条并**静默覆盖**目标（见 docs/17「轨迹与网格」）。
        两条运动同时发 = 增量从运动中的瞬时位置算出来，误差逐段累积，
        而且台面到底停在哪变得说不清。页面也会把控件禁掉，这是第二道防线。
        """
        if self._runner.busy:
            self._set_error(self.tr("轨迹正在运行 —— 先停止轨迹再手动移动"
                                    "（否则两条运动会互相覆盖目标，台面停在哪就说不清了）"))
            return False
        return self._issue_move(x_mm, y_mm)

    @Slot(float, float, result=bool)
    def jog(self, dx_mm: float, dy_mm: float) -> bool:
        """相对点动一格（mm）。步长由界面给，这里只做目标计算 + 夹取。"""
        if not self.connected:
            self._set_error(self.tr("未连接平台"))
            return False
        if not self._datum:
            self._gate_error("datum", self.tr("还没有基准：先「设为原点」或自动回零"))
            return False
        if not self._travel_set:
            self._gate_error("travel", self.tr("还没设置工作区，点动会被固件拒绝"))
            return False
        return self.moveTo(self._pos_x + float(dx_mm), self._pos_y + float(dy_mm))

    @Slot(result=bool)
    def stopNow(self) -> bool:
        """急停：刹车 + 清状态。

        ⚠ 这条**插到队首**而不是绕过队列：板子的 TCP 任务是串行的，
        而我们发的所有网络命令都是"立即返回"的（移动用非阻塞的 move），
        所以插队首之后最多等一条命令的时间（几十毫秒）就能执行 —— 不会像
        用阻塞的 g0 那样被几秒的长移动堵住。这是整个设计里最关键的一点。
        """
        if not self.connected:
            self._set_error(self.tr("未连接平台，无法停止"))
            return False
        # ⚠ 必须**先掐掉序列器再发 stop**：stop 会让板子把 moving 报成 0，
        #   序列器若还活着就会把这帧当成"这一段到位了"，于是刚急停完、
        #   台面又自己动起来 —— 这正是"急停不干净"最典型的样子。
        self._abort_path(self.tr("急停"))
        self._enqueue("stop all", note=self.tr("急停"), front=True, timeout_ms=CMD_TIMEOUT_MS)
        if self._moving:
            self._moving = False
            self.movingChanged.emit()
        # 固件里 stop 也会顺带把回零打断（0x93），这里同步把状态收掉，
        # 免得界面继续显示"正在回零"直到下一次 json 回来
        if self._homing == 1:
            self._homing = 3
        self.telemetryChanged.emit()
        return True

    @Slot(result=bool)
    def zeroAll(self) -> bool:
        """把当前位置定为原点（立基准）。瞬时、无需硬件 —— 台架对基准就用它。"""
        if not self.connected:
            self._set_error(self.tr("未连接平台"))
            return False
        # 重新立基准 = 整个坐标系挪到脚下：在跑的轨迹、正在录的点**都失去意义**
        # （同一串坐标在旧基准下是位置 A、在新基准下是位置 B）。
        self._abort_path(self.tr("重新立基准"))
        if self._recording:
            self.cancelRecording()
            self._log(self.tr("因为重新立了基准，这次录制已作废（坐标含义变了）"))
        self._enqueue("zero all", note=self.tr("设为原点"))
        self._pos_x = 0.0
        self._pos_y = 0.0
        # 刚立的基准就在脚下 —— "上次停在哪儿"那条旧记录已经没意义了（见 forgetParkPos）
        self.forgetParkPos()
        self.telemetryChanged.emit()
        return True

    @Slot(result=bool)
    def homeAll(self) -> bool:
        """**回零点角（两趟）** —— 把滑座顶到「X 左 + Y 外」那个角。

        ⚠ 2026-09-13：**角落已经写死**，不再让用户选。
        用户拍板："固定零点在 X 轴左侧和 Y 轴的外侧" —— 也就是 `(xdir, ydir) = (−1, −1)`。
        写死的好处很实在：零点只有一个地方，预设位置、工作区、退出回零点全都指着它，
        不可能出现"这次回左·外、下次回右·里"这种把坐标系整体挪走的事故。

        为什么是"两趟"：CoreXY 里 **单轴 = 45° 对角线**，所以"一趟 `home all`"
        只能让滑座顶到**一条边**；要真到角落必须两趟（两轴同向 = 纯 X，
        两轴反向 = 纯 Y）。两轴方向由**固件**按 `COREXY_DEFAULT` 反推 —— 符号矩阵是
        固件的知识，上位机不复制一份。
        """
        if not self.connected:
            self._set_error(self.tr("未连接平台"))
            return False
        if self._homing == 1:
            self._set_error(self.tr("已经在回零中 —— 要停下按「停止」"))
            return False
        # 回零占着整台机器（固件那边 `motion_begin` 会拒掉别的运动）：
        # 轨迹必须让路，否则会得到一串"已有运动指令在执行"的拒绝
        self._abort_path(self.tr("回零"))
        if not self._home_cfg_sent:
            # ⚠ 措辞必须**不指向已删除的控件**。2026-09-13 大扫除把
            #   「应用回零参数」按钮去掉了，这里原来的提示"先点上面的「应用回零参数」"
            #   就变成了指路指到墙上 —— 本项目明令禁止那一类话。
            self._gate_error("homecfg",
                             self.tr("回零参数还没登记（固件 `home` 那道闸要求的）。"
                             "界面已改为限位开关归零、暂时没有回零入口；需要时在板子控制台上敲 "
                             "'hcfg <方式> <rpm> <mA>' 再敲 'home corner -1 -1'"))
            return False
        xd, yd = DATUM_CORNER
        self._enqueue(f"home corner {xd} {yd} nowait", note=self.tr("回零点角（两趟：先 X 后 Y）"),
                      timeout_ms=CMD_TIMEOUT_MS, verbose=True)
        self._homing = 1
        if not self._moving:
            self._moving = True
            self.movingChanged.emit()
        self.telemetryChanged.emit()
        return True

    @Slot(str, int, result=bool)
    def homePass(self, axis: str, direction: int) -> bool:
        """**单趟回零**（调试用）：只顶一个方向，好确认"方向对不对"。

        `axis` 取 "x"/"y"，`direction` 取 ±1。排查角落回零失败时先用它：
        哪一趟不对，一眼就能看出来。
        """
        if not self.connected:
            self._set_error(self.tr("未连接平台"))
            return False
        if axis not in ("x", "y") or direction not in (-1, 1):
            self._set_error(self.tr("单趟回零参数不对（axis 只能 x/y，方向只能 ±1）"))
            return False
        if self._homing == 1:
            self._set_error(self.tr("已经在回零中 —— 要停下按「停止」"))
            return False
        self._abort_path(self.tr("单趟回零"))
        if not self._home_cfg_sent:
            self._gate_error("homecfg", self.tr("还没给回零参数 —— 先点「应用回零参数」"))
            return False
        self._enqueue(f"home {axis} {direction} nowait",
                      note=self.tr('单趟回零（{}，朝 {}{}）').format(self.tr('纯 X') if axis == 'x' else self.tr('纯 Y'), '+' if direction > 0 else '−', axis.upper()),
                      timeout_ms=CMD_TIMEOUT_MS, verbose=True)
        self._homing = 1
        if not self._moving:
            self._moving = True
            self.movingChanged.emit()
        self.telemetryChanged.emit()
        return True

    # ⚠ 这里原来有 `homeCorner` Property + `setHomeCorner()` + `stage/home_corner` 设置项。
    #   2026-09-13 删掉：零点角**写死成 DATUM_CORNER = (−1, −1)**（X 左 + Y 外）。
    #   理由见 `homeAll()` 的注释 —— 零点只能有一个，不该是个可选项。

    @Slot(result=bool)
    def homeAbort(self) -> bool:
        """中断回零（0x93）。与「停止」等效 —— 停止里也已经带了这一脚。"""
        if not self.connected:
            self._set_error(self.tr("未连接平台"))
            return False
        self._enqueue("habort all", note=self.tr("中断回零"), front=True,
                      timeout_ms=CMD_TIMEOUT_MS)
        self._homing = 3
        self.telemetryChanged.emit()
        return True

    @Slot(int, result=bool)
    def setDatumAxis(self, axis: int) -> bool:
        """单轴立基准（axis: 0=left/A, 1=right/B）—— 只给诊断区用。"""
        if not self.connected:
            self._set_error(self.tr("未连接平台"))
            return False
        self._enqueue("zero " + ("left" if axis == 0 else "right"), note=self.tr("单轴立基准"))
        return True

    # ============================================================
    # 回零参数（hset）
    # ============================================================
    # ⚠ 固件的 `home` 有一道**闸**：本次会话必须先 `hset` 明确
    #   方式/方向/速度/限位电流，否则直接拒绝。原因是驱动器**不回读 0x91**，
    #   贸然触发等于用未知参数撞机械死点。
    #   所以界面必须提供配 hset 的地方 —— 只放一个「自动回零」按钮的话，
    #   它永远是死的（点了只会得到固件的拒绝信息）。
    def _load_home_cfg(self):
        raw = self._settings.value("stage/home_cfg", "")
        if not raw:
            return
        try:
            d = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            self._log(self.tr("回零参数数据损坏，已忽略"))
            return
        if isinstance(d, dict) and all(k in d for k in ("kind", "rev", "rpm", "ma")):
            self._home_cfg = {k: int(d[k]) for k in ("kind", "rev", "rpm", "ma")}
            self._home_cfg_saved = True
            # 旧版本存进来的值可能根本不可用：真机踩到 200mA，两轴回零都报
            # 「失败(未找到零点)」。而 `_send_home_cfg` 在**每次连接**时都会把它重下发，
            # 于是一个坏值会被静默地一直用下去 —— 所以在这里就修好并说清楚。
            if self._home_cfg["ma"] < HOME_MA_MIN:
                self._log(self.tr('⚠ 保存的回零限位电流只有 {}mA，太小了，已自动改成 {}mA（推荐 800mA）。它是**电流阈值**：相电流越过它就算到位；太小（接近空转电流 ~40mA）会**一动就假报「回零完成」**。').format(self._home_cfg['ma'], HOME_DEFAULT['ma']))
                self._home_cfg["ma"] = HOME_DEFAULT["ma"]
                self._settings.setValue("stage/home_cfg",
                                        json.dumps(self._home_cfg, ensure_ascii=False))
            if self._home_cfg["rpm"] == OLD_DEFAULT_HOME_RPM:
                # 旧默认 60rpm 有超时风险（满行程 9.4s vs 驱动器 10s 超时），迁到 300。
                # 用户自己填过的其它值不动。
                self._log(self.tr('回零速度默认值更新：{} → {}rpm（60rpm 走满行程要 9.4 秒，几乎顶到驱动器 10 秒的回零超时）').format(OLD_DEFAULT_HOME_RPM, HOME_DEFAULT['rpm']))
                self._home_cfg["rpm"] = HOME_DEFAULT["rpm"]
                self._settings.setValue("stage/home_cfg",
                                        json.dumps(self._home_cfg, ensure_ascii=False))

    def _get_home_kind(self) -> int:
        return int(self._home_cfg["kind"])

    homeKind = Property(int, _get_home_kind, notify=telemetryChanged)

    def _get_home_rev(self) -> bool:
        """台架/控制台路径才用的单轴方向。⚠ 界面已不再使用它（方向由固件算），
        保留属性只为不破坏已有调用方。"""
        return bool(self._home_cfg.get("rev", 0))

    homeRev = Property(bool, _get_home_rev, notify=telemetryChanged)

    def _get_home_rpm(self) -> int:
        return int(self._home_cfg["rpm"])

    homeRpm = Property(int, _get_home_rpm, notify=telemetryChanged)

    def _get_home_ma(self) -> int:
        return int(self._home_cfg["ma"])

    homeMa = Property(int, _get_home_ma, notify=telemetryChanged)

    def _get_home_cfg_sent(self) -> bool:
        """本次会话是否已经把 hset 下发成功（固件那道具闸看的就是这个）。"""
        return self._home_cfg_sent

    homeCfgSent = Property(bool, _get_home_cfg_sent, notify=telemetryChanged)

    @Slot(int, int, int, result=bool)
    def setHomeConfig(self, kind: int, rpm: int, ma: int) -> bool:
        """登记回零参数（方式/速度/限位电流）—— 用 `hcfg`：**只记在板子 RAM 里**。

        ⚠ 参数里**没有方向**：两轴方向由固件按 COREXY_DEFAULT 从"想去哪个角"反推
        （`home corner` / `home x|y`）。以前这里让用户填一个"两轴一起反转"的开关，
        结果永远只能走一个方向 —— 用户报的"没法在角落里回零"就是这个。

        ⚠ 也**不要**改回 `hset`：这台 PD42S1 收到 0x91 就会自己开始找零点，
        于是"点一下连接、点一下应用，台面自己就回零了"（真机原话）。
        """
        if not self.connected:
            self._set_error(self.tr("未连接平台"))
            return False

        kind = max(0, min(HOME_KIND_MAX, int(kind)))
        # 上限以固件 cmd_hcfg 的校验为准，超了会被 usage 拒绝
        rpm = max(1, min(HOME_RPM_MAX, int(rpm)))
        ma = max(0, min(HOME_MA_MAX, int(ma)))
        if ma < HOME_MA_MIN:
            # 2026-09-13 真机：用户填了 200mA，两轴回零都报「失败(未找到零点)」。
            # 这个字段只对无限位回零有效、就是判死点用的 —— 太小推不动台面，
            # 也判不出死点。手册示例值是 300mA，本项目建议 800~1200mA。
            # 拒绝而不是只警告：用错的限位电流去回零 = 台面顶着乱撞，代价远高于一次拒绝。
            self._set_error(
                self.tr('限位电流 {}mA 太小（本项目推荐 800mA，下限 {}mA）—— 它是**电流阈值**：相电流越过它就算到位。太小（接近空转电流 ~40mA）会**一动就假报「回零完成」**（台面还没到边就算到了），比失败更危险。（确实要用小电流做实验，请在板子控制台上直接敲 hset）').format(ma, HOME_MA_MIN))
            return False

        # 不再接收方向：方向由固件按 COREXY_DEFAULT 算（见 docstring）。
        # 字典里保留 rev 键只为兼容旧配置文件。
        self._home_cfg.update({"kind": kind, "rpm": rpm, "ma": ma})
        self._settings.setValue("stage/home_cfg",
                               json.dumps(self._home_cfg, ensure_ascii=False))
        self._home_cfg_saved = True
        self.telemetryChanged.emit()
        self._send_home_cfg()
        return True

    def _send_home_cfg(self):
        """把回零参数登记到固件 RAM 里（`hcfg`）—— **不写驱动器，不会让电机动**。

        ⚠ 这里**故意不用 `hset`**：`hset` 是 0x91，写进驱动器；而这台 PD42S1
        收到 0x91 之后就会自己开始找零点。以前每次连接都自动 `hset`，
        现象就是"点连接就回零"。`hcfg` 只改固件内的副本，安全。
        （方向也不在这里定：`home corner` 由固件按 COREXY_DEFAULT 算。）
        """
        c = self._home_cfg
        self._enqueue(f"hcfg {c['kind']} {c['rpm']} {c['ma']}",
                      note=self.tr("回零参数（只登记，不动电机）"))
        self._home_cfg_sent = True
        self.telemetryChanged.emit()
        self._maybe_clear_error()
        self._log(self.tr('回零参数已登记（未写驱动器、未触发回零）：方式={} {}rpm 限位电流={}mA').format(c['kind'], c['rpm'], c['ma']))

    # ============================================================
    # 轨迹与网格（2026-10-08）
    # ============================================================
    # 这一节解决三件事，共用**一套数据模型**：
    #   ① 网格点选 —— 在工作区俯视图上点一下，相机就走到那个格心（粗定位；
    #      0.1mm 级的精调仍然归十字点动管，"密集网格"替代不了它）；
    #   ② 图形轨迹 —— 圆 / 矩形 / 多段线（可平滑）/ 巡检点阵；
    #   ③ 录制重放 —— 把手动操作记成一条轨迹，之后原样再走一遍。
    #
    # ⚠ **数据模型存"参数"而不是"点列表"**（录制那条除外）：圆只存圆心+半径+段长，
    #   点阵只存行列数+区域，多段线只存手点的那几个原始点。两个好处：
    #     · QSettings 里一条轨迹几百字节，而不是 157 个坐标；
    #     · 能改参数重新生成（"半径改成 60 再来一遍"），而不是一堆死坐标。
    #   落点序列在 runPath 时才现算（`_sample_params`）——所以将来改采样规则
    #   （段长/蛇形/平滑）对所有**已存**的轨迹立刻生效，不必迁移数据。
    #
    # ⚠ 执行侧一律走 `stage_traj.PathRunner`：固件的 `move` 是点到点、
    #   而且在途时再发一条会静默覆盖目标（见 docs/17）。**绝不能连发**。

    # ── ⚠ 2026-10-10 删除了「网格分辨率 + 格心吸附」一组 ────────────────
    # 用户要求台面背景"不要网格、只留刻度标尺"，于是：
    #   · `gridRows/gridCols/setGrid/gridChanged` —— 只用来画格线与吸附，没有别的消费者；
    #   · `snapToGrid()` + `stage_traj.snap_to_cell()` —— 吸到格心用的，背景没格子之后
    #     它失去视觉依据，而且**不吸附精度更高**（±1mm vs 18mm 的格心）。
    # 现在点选由卡片自己把比例换算成 mm（`_mmOf`），`StagePage` 直接 `moveTo`。
    # ⚠ QSettings 里的 `stage/grid_rows|grid_cols` 成了遗留键：不写迁移代码（留着无害）。
    # ⚠ 「网格」**模式**还在（它生成巡检点阵），它的 `params.rows/cols` 是**巡逻点数**，
    #   与这里的格线分辨率从来不是一回事。

    # ── 轨迹列表（存 QSettings，与预设同一套路）────────────
    def _load_paths(self) -> List[Dict[str, Any]]:
        raw = str(self._settings.value("stage/paths", ""))
        if not raw:
            return []
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return [p for p in data
                        if isinstance(p, dict) and "name" in p and "kind" in p]
        except (json.JSONDecodeError, TypeError):
            self._log(self.tr("轨迹数据损坏，已忽略"))
        return []

    def _save_paths(self, paths: List[Dict[str, Any]]):
        self._settings.setValue("stage/paths", json.dumps(paths, ensure_ascii=False))
        self.pathsChanged.emit()
        # 预览/预计耗时都跟着列表走；Property 只能有一个 notify，所以一并发这个
        self.pathChanged.emit()

    def _get_paths(self):
        return self._load_paths()

    paths = Property("QVariantList", _get_paths, notify=pathsChanged)

    def _get_selected_path(self) -> int:
        return self._selected_path

    def _get_preview_points(self):
        """当前选中轨迹的落点序列（给界面画折线用）。"""
        pts = self._sample_path(self._selected_path)
        return [[round(x, 3), round(y, 3)] for x, y in pts]

    # 由 pathChanged 通知（选中项变了或列表变了都发它）
    previewPoints = Property("QVariantList", _get_preview_points, notify=pathChanged)

    selectedPath = Property(int, _get_selected_path, notify=pathChanged)

    def _get_path_estimate(self) -> float:
        """选中轨迹的**预计耗时**（秒）。

        ⚠ 这是估算，用于让用户对"这条轨迹要跑多久"有概念（尤其是巡检点阵：
        36 个点 ≈ 十几秒，而 400 个点是几分钟）。真正的每段开销要真机实测校正
        —— 它主要由 `PATH_SEG_OVERHEAD_S`（轮询到位 + 固件 100ms tick）决定，
        而不是由速度决定：5mm 的小段根本跑不到巡航速度。
        """
        paths = self._load_paths()
        if self._selected_path < 0 or self._selected_path >= len(paths):
            return 0.0
        pts = self._sample_params(paths[self._selected_path])
        if len(pts) < 2:
            return 0.0
        rpm = max(1, _inum(paths[self._selected_path].get("rpm"), self._rpm))
        speed = max(1.0, rpm / 60.0 * 32.0)      # GT2-16 齿带轮：每圈 32mm
        est = 0.0
        for a, b in zip(pts, pts[1:]):
            est += max(PATH_SEG_OVERHEAD_S, math.dist(a, b) / speed)
        return est

    pathEstimateS = Property(float, _get_path_estimate, notify=pathChanged)

    def _area_from(self, prm: Dict[str, Any]) -> Tuple[float, float, float, float]:
        """轨迹的区域：默认就是当前工作区（用户不必把行程再填一遍）。"""
        return (_fnum(prm.get("x0"), self._ws_xmin), _fnum(prm.get("y0"), self._ws_ymin),
                _fnum(prm.get("x1"), self._ws_xmax), _fnum(prm.get("y1"), self._ws_ymax))

    def _sample_params(self, p: Dict[str, Any]) -> List[Tuple[float, float]]:
        """把一条轨迹的 kind+params 算成落点序列（**已按工作区夹取**）。"""
        kind = str(p.get("kind", ""))
        prm = p.get("params") or {}
        if not isinstance(prm, dict):
            return []
        step = _fnum(prm.get("step"), stage_traj.DEFAULT_STEP_MM)
        step = max(stage_traj.MIN_STEP_MM, min(stage_traj.MAX_STEP_MM, step))
        try:
            if kind == "grid":
                x0, y0, x1, y1 = self._area_from(prm)
                pts = stage_traj.sample_grid(_inum(prm.get("rows"), 6),
                                             _inum(prm.get("cols"), 6),
                                             x0, y0, x1, y1,
                                             bool(prm.get("serpentine", True)))
            elif kind == "circle":
                pts = stage_traj.sample_circle(_fnum(prm.get("cx")), _fnum(prm.get("cy")),
                                               _fnum(prm.get("r")), step)
            elif kind == "rect":
                x0, y0, x1, y1 = self._area_from(prm)
                pts = stage_traj.sample_rect(x0, y0, x1, y1, step)
            elif kind == "polyline":
                raw = prm.get("points") or []
                hand = [(_fnum(q[0]), _fnum(q[1])) for q in raw
                        if isinstance(q, (list, tuple)) and len(q) >= 2]
                pts = stage_traj.sample_polyline(hand, step,
                                                 bool(prm.get("smooth", False)),
                                                 bool(prm.get("closed", False)))
            elif kind == "recorded":
                raw = prm.get("points") or []
                pts = [(_fnum(q[0]), _fnum(q[1])) for q in raw
                       if isinstance(q, (list, tuple)) and len(q) >= 2]
            else:
                return []
        except (TypeError, ValueError, IndexError, KeyError):
            return []
        # 一律夹到工作区：固件那边行程闸是 fail-closed，越界直接拒帧，
        # 表现成"轨迹走到一半停住"。这里先夹住（用户看得见日志里的夹取记录）。
        return [self._clamp(x, y) for x, y in pts]

    def _sample_path(self, index: int) -> List[Tuple[float, float]]:
        paths = self._load_paths()
        if index < 0 or index >= len(paths):
            return []
        return self._sample_params(paths[index])

    @Slot(int, result="QVariantList")
    def pathPoints(self, index: int):
        return [[round(x, 3), round(y, 3)] for x, y in self._sample_path(index)]

    @Slot(str, str, result="QVariantList")
    def draftPoints(self, kind: str, params_json: str):
        """**还没保存**的一版轨迹 → 落点序列（供界面上实时预览）。

        为什么要走桥（而不是在 QML 里自己算几何）：预览必须是"**将要执行的那串点**"，
        而采样里还夹着工作区夹取、样条平滑、按段长重采样、圆的最少 8 段下限……
        在 QML 里再写一份就是"同一事实两份"（本项目第 4 条坑），而且
        用户已经被这种不一致坑过一次（"圆能画到台面外，但执行时会被夹回去"）。
        这里直接复用 `_sample_params` —— 它也是保存/执行时走的那一个。

        ⚠ 这是**纯几何**，不连板子、不下发命令、不写 QSettings，也不进录制
        （录制守的是 `_issue_move` 那一个出口）—— 所以未连接时也能画预览。
        ⚠ 参数走 **JSON 字符串**：QML 的 JS 对象过到 Python 是嵌套的
        QVariantMap/QVariantList，`json.dumps` 序列化不了（与 savePath 同一套约定）。
        """
        import json
        try:
            prm = json.loads(params_json) if params_json else {}
        except (ValueError, TypeError):
            return []
        if not isinstance(prm, dict):
            return []
        pts = self._sample_params({"kind": str(kind), "params": prm})
        return [[round(x, 3), round(y, 3)] for x, y in pts]

    @Slot(int)
    def selectPath(self, index: int):
        paths = self._load_paths()
        self._selected_path = index if 0 <= index < len(paths) else -1
        self.pathChanged.emit()

    @Slot(str, str, str, int, int, result=bool)
    def savePath(self, name: str, kind: str, params_json: str,
                 rpm: int, acc: int) -> bool:
        """保存一条参数化轨迹。

        ⚠ `params_json` 是**JSON 字符串**而不是 QVariantMap：QML 的 JS 对象
        过到 Python 会变成嵌套的 QVariantMap/QVariantList，`json.dumps` 不能
        直接序列化它们，得先递归转换 —— 那是一类"看着能跑、偶尔炸"的坑。
        QML 侧写 `JSON.stringify({...})` 就完全绕开了它，而且存盘格式本来就是 JSON。
        """
        kind = (kind or "").strip()
        if kind not in ("grid", "circle", "rect", "polyline"):
            self._set_error(self.tr("不支持的轨迹类型：{}").format(kind))
            return False
        try:
            prm = json.loads(params_json) if params_json else {}
        except (json.JSONDecodeError, TypeError):
            self._set_error(self.tr("轨迹参数不是合法的 JSON"))
            return False
        if not isinstance(prm, dict):
            self._set_error(self.tr("轨迹参数必须是一个对象"))
            return False
        name = (name or "").strip()
        if not name:
            self._set_error(self.tr("请给轨迹起个名字"))
            return False
        # 先试算一遍落点：参数不完整时**现在就报**，而不是等用户点了执行才发现
        pts = self._sample_params({"kind": kind, "params": prm})
        if not pts:
            self._set_error(self.tr("这条轨迹算不出落点 —— 检查半径/区域/段长是否合理"))
            return False

        entry = {"name": name, "kind": kind, "params": prm,
                 "rpm": max(1, min(MAX_RPM, int(rpm))),
                 "acc": max(1, min(MAX_ACC, int(acc)))}
        paths = self._load_paths()
        for i, p in enumerate(paths):
            if p.get("name") == name:          # 同名覆盖（与预设一致）
                paths[i] = entry
                self._selected_path = i
                self._save_paths(paths)
                self._log(self.tr('轨迹「{}」已更新（{} 段，预计 {:.0f} 秒）').format(
                    name, len(pts), self.pathEstimateS))
                return True
        paths.append(entry)
        self._selected_path = len(paths) - 1
        self._save_paths(paths)
        self._log(self.tr('已保存轨迹「{}」（{} 段，预计 {:.0f} 秒）').format(
            name, len(pts), self.pathEstimateS))
        return True

    @Slot(int, str, result=bool)
    def renamePath(self, index: int, name: str) -> bool:
        name = (name or "").strip()
        paths = self._load_paths()
        if not name or index < 0 or index >= len(paths):
            return False
        if any(p.get("name") == name for i, p in enumerate(paths) if i != index):
            self._set_error(self.tr("已经有同名的轨迹了"))
            return False
        paths[index]["name"] = name
        self._save_paths(paths)
        return True

    @Slot(int, result=bool)
    def deletePath(self, index: int) -> bool:
        paths = self._load_paths()
        if index < 0 or index >= len(paths):
            return False
        gone = paths.pop(index)
        if self._selected_path == index:
            self._selected_path = -1
        elif self._selected_path > index:
            self._selected_path -= 1
        self._save_paths(paths)
        self._log(self.tr('已删除轨迹「{}」').format(gone.get('name')))
        return True

    # ── 执行（点列序列器）──────────────────────────────────
    def _get_path_state(self) -> str:
        return self._runner.state

    pathState = Property(str, _get_path_state, notify=pathChanged)

    def _get_path_done(self) -> int:
        return self._runner.done

    pathDone = Property(int, _get_path_done, notify=pathChanged)

    def _get_path_total(self) -> int:
        return self._runner.total

    pathTotal = Property(int, _get_path_total, notify=pathChanged)

    def _get_path_busy(self) -> bool:
        return self._runner.busy

    pathBusy = Property(bool, _get_path_busy, notify=pathChanged)

    def _get_path_reason(self) -> str:
        return self._runner.abort_reason

    pathAbortReason = Property(str, _get_path_reason, notify=pathChanged)

    def _get_path_can_run(self) -> bool:
        """界面按钮的 enabled。把"为什么不能跑"提前说清，而不是让用户猜灰按钮。"""
        return (self.connected and self._datum and self._travel_set
                and not self._runner.busy and not self._moving
                and not self._recording)

    pathCanRun = Property(bool, _get_path_can_run, notify=pathEnableChanged)

    def _get_path_hint(self) -> str:
        if not self.connected:
            return self.tr("未连接平台：先点上面的平台卡片连接")
        if self._recording:
            return self.tr("正在录制中 —— 先点「结束录制」再执行轨迹")
        if not self._datum:
            return self.tr("缺少基准：每次上电都要重立一次 —— 推到位后点「设为原点」")
        if not self._travel_set:
            return self.tr("未设置工作区：绝对移动会被固件拒绝")
        if self._runner.busy:
            return ""
        if self._moving:
            return self.tr("台面还在动 —— 等它停稳再开始轨迹")
        return ""

    pathHint = Property(str, _get_path_hint, notify=pathEnableChanged)

    def _path_send_move(self, x: float, y: float) -> bool:
        """序列器发一段（**不录制**，并用轨迹自己的速度）。

        `note=""` 是故意的：一条 63 段的圆如果每段都在诊断日志里打一行
        "xxx 完成"，日志就没法看了。协议框里那 63 行 `→ move ...` 仍然保留 ——
        那是排查时的原始事实，不能省。
        """
        rpm, acc = self._path_speed
        return self._issue_move(x, y, rpm=rpm, acc=acc, record=False, note="")

    def _on_path_state(self, state: str, reason: str):
        self.pathChanged.emit()

    def _on_path_progress(self, done: int, total: int):
        self.pathChanged.emit()

    def _on_dwell_timeout(self):
        cb = self._dwell_cb
        self._dwell_cb = None
        if cb is not None:
            cb()

    def _arm_dwell(self, ms: int, cb):
        """序列器的"到点停留"钩子（二期到点拍照就用它）。"""
        self._dwell_cb = cb
        self._dwell_timer.start(max(1, int(ms)))

    def _abort_path(self, reason: str) -> bool:
        """中止在跑的轨迹（急停/断线/回零/重新立基准/失能时都要调）。

        ⚠ 返回"是不是真的中止了"，方便调用方决定还要不要做别的收尾。
        """
        self._dwell_timer.stop()
        self._dwell_cb = None
        return self._runner.abort(reason)

    @Slot(int, result=bool)
    def runPath(self, index: int) -> bool:
        if not self.connected:
            self._set_error(self.tr("未连接平台"))
            return False
        if self._recording:
            self._set_error(self.tr("正在录制中 —— 先结束录制再执行轨迹"
                                    "（否则录制里会混进轨迹自己的移动）"))
            return False
        if self._runner.busy:
            self._set_error(self.tr("轨迹已经在跑了 —— 要打断按「停止」"))
            return False
        if not self._datum:
            self._gate_error("datum", self.tr("还没有基准：先「设为原点」或自动回零"))
            return False
        if not self._travel_set:
            self._gate_error("travel", self.tr("还没设置工作区，绝对移动会被固件拒绝"))
            return False
        if self._moving:
            self._set_error(self.tr("台面还在动 —— 等它停稳再开始轨迹"
                                    "（否则第一段的增量会从运动中的瞬时位置算出来）"))
            return False

        paths = self._load_paths()
        if index < 0 or index >= len(paths):
            self._set_error(self.tr("轨迹不存在"))
            return False
        p = paths[index]
        pts = self._sample_params(p)
        if not pts:
            self._set_error(self.tr("这条轨迹算不出落点 —— 打开它检查参数"))
            return False
        # 起点就在脚下：省掉一次"移动到原地"。那一次要白等一整个到位周期，
        # 而且 `move` 会用 counts=0 去动一下电机（虽然不会真走）。
        if len(pts) > 1 and math.dist(pts[0], (self._pos_x, self._pos_y)) <= PATH_SKIP_START_MM:
            pts = pts[1:]

        self._path_speed = (max(1, min(MAX_RPM, _inum(p.get("rpm"), self._rpm))),
                            max(1, min(MAX_ACC, _inum(p.get("acc"), self._acc))))
        self._selected_path = index
        self.pathChanged.emit()
        return self._runner.start(pts, self._tel_seq)

    @Slot(result=bool)
    def pausePath(self) -> bool:
        r = self._runner.pause()
        if r:
            self.pathChanged.emit()
        return r

    @Slot(result=bool)
    def resumePath(self) -> bool:
        r = self._runner.resume()
        if r:
            self.pathChanged.emit()
        return r

    @Slot(result=bool)
    def stopPath(self) -> bool:
        """用户点「停止轨迹」：只中止序列器，**不动电机**。

        为什么要和急停分开：轨迹中止时台面本来就会停在当前段的目标点上
        （段末速度为零），所以不需要刹车。真要立刻停下用「停止」（急停）。
        """
        r = self._abort_path(self.tr("用户停止"))
        if r:
            self.pathChanged.emit()
        return r

    @Slot()
    def clearPathState(self):
        self._runner.reset()
        self.pathChanged.emit()

    def _get_path_dwell(self) -> int:
        return self._runner.dwell_ms

    pathDwellMs = Property(int, _get_path_dwell, notify=pathChanged)

    @Slot(int)
    def setPathDwell(self, ms: int):
        """到点停留（ms）。一期仅"停一下"，二期它就是到点拍照的曝光窗口。"""
        self._runner.set_dwell(ms)
        self.pathChanged.emit()

    # ── 录制（把手动操作记成一条轨迹）───────────────────────
    def _get_recording(self) -> bool:
        return self._recording

    recording = Property(bool, _get_recording, notify=recordChanged)

    def _get_record_count(self) -> int:
        return len(self._rec_points)

    recordCount = Property(int, _get_record_count, notify=recordChanged)

    @Slot(result=bool)
    def startRecording(self) -> bool:
        if not self.connected:
            self._set_error(self.tr("未连接平台"))
            return False
        if self._runner.busy:
            self._set_error(self.tr("轨迹正在运行 —— 先停下来再开始录制"))
            return False
        if self._recording:
            return True
        self._recording = True
        # 先把**起点**记下来：重放时才知道"我是在哪儿开始这一套动作的"
        self._rec_points = [(self._pos_x, self._pos_y)]
        self.recordChanged.emit()
        self._log(self.tr("开始录制：之后的十字点动 / 绝对定位 / 网格点选 / 预设前往都会被记下来"))
        return True

    @Slot()
    def cancelRecording(self):
        if not self._recording:
            return
        self._recording = False
        self._rec_points = []
        self.recordChanged.emit()
        self._log(self.tr("已放弃这次录制"))

    @Slot(str, result=bool)
    def stopRecording(self, name: str) -> bool:
        if not self._recording:
            return False
        raw = list(self._rec_points)
        self._recording = False
        self._rec_points = []
        self.recordChanged.emit()

        # 抽稀：按住十字键连发一秒能产生 6~7 个点，不压的话重放会慢十倍。
        pts = stage_traj.simplify(stage_traj.dedupe(raw), REC_SIMPLIFY_TOL_MM)
        if len(pts) < 2:
            self._set_error(self.tr("这次录制只记到了不到两个不同的位置，没有存成轨迹"))
            return False
        name = (name or "").strip()
        if not name:
            # 名字带时间戳，用 tr("录制") 拼前缀，避免把中文拼进 format 模板
            import datetime
            name = self.tr("录制") + datetime.datetime.now().strftime("-%m%d-%H%M")

        entry = {"name": name, "kind": "recorded",
                 "params": {"points": [[round(x, 3), round(y, 3)] for x, y in pts]},
                 "rpm": self._rpm, "acc": self._acc}
        paths = self._load_paths()
        for i, p in enumerate(paths):
            if p.get("name") == name:
                paths[i] = entry
                self._selected_path = i
                self._save_paths(paths)
                self._log(self.tr('录制已更新轨迹「{}」（原始 {} 点 → 压缩成 {} 段）').format(
                    name, len(raw), len(pts)))
                return True
        paths.append(entry)
        self._selected_path = len(paths) - 1
        self._save_paths(paths)
        self._log(self.tr('录制已存成轨迹「{}」（原始 {} 点 → 压缩成 {} 段）').format(
            name, len(raw), len(pts)))
        return True

    # ============================================================
    # 预设位置（存 QSettings；只在"本次上电立过基准"之后才有意义）
    # ============================================================
    def _load_presets(self) -> List[Dict[str, Any]]:
        raw = self._settings.value("stage/presets", "")
        if not raw:
            return []
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return [p for p in data if isinstance(p, dict) and "name" in p]
        except (json.JSONDecodeError, TypeError):
            self._log(self.tr("预设位置数据损坏，已忽略"))
        return []

    def _save_presets(self, presets: List[Dict[str, Any]]):
        self._settings.setValue("stage/presets", json.dumps(presets, ensure_ascii=False))
        self.presetsChanged.emit()

    def _get_presets(self):
        return self._load_presets()

    presets = Property("QVariantList", _get_presets, notify=presetsChanged)

    @Slot(str, result=bool)
    def savePreset(self, name: str) -> bool:
        """把当前位置记成一个预设。"""
        name = (name or "").strip()
        if not name:
            self._set_error(self.tr("请给预设起个名字"))
            return False
        presets = self._load_presets()
        for p in presets:
            if p["name"] == name:          # 同名覆盖，避免列表里出现两条一样的
                p["x"], p["y"] = self._pos_x, self._pos_y
                self._save_presets(presets)
                self._log(self.tr('预设「{}」已更新为 ({:.2f}, {:.2f}) mm').format(name, self._pos_x, self._pos_y))
                return True
        presets.append({"name": name, "x": self._pos_x, "y": self._pos_y})
        self._save_presets(presets)
        self._log(self.tr('已记录预设「{}」= ({:.2f}, {:.2f}) mm').format(name, self._pos_x, self._pos_y))
        return True

    @Slot(int, result=bool)
    def deletePreset(self, index: int) -> bool:
        presets = self._load_presets()
        if index < 0 or index >= len(presets):
            return False
        gone = presets.pop(index)
        self._save_presets(presets)
        self._log(self.tr('已删除预设「{}」').format(gone.get('name')))
        return True

    @Slot(int, result=bool)
    def gotoPreset(self, index: int) -> bool:
        presets = self._load_presets()
        if index < 0 or index >= len(presets):
            self._set_error(self.tr("预设不存在"))
            return False
        p = presets[index]
        return self.moveTo(_fnum(p.get("x")), _fnum(p.get("y")))

    @Slot(int, result=bool)
    def captureAtPreset(self, index: int) -> bool:
        """前往预设位置并采一张图（可选功能：没装相机桥时返回 False）。

        这是这个页面真正的价值所在 —— "把相机移到工位再拍"一条动线完成。
        真正采图由页面调用采集桥完成，这里只负责移动与状态。
        """
        return self.gotoPreset(index)
