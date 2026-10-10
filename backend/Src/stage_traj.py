"""轨迹几何（纯函数）+ 点列序列器（PathRunner）。

为什么单独一个文件：`stage_bridge.py` 已经 1400+ 行，而这两块东西**都不需要 Qt**
—— 几何是纯函数（脱离事件循环就能单测），序列器也只调调用方给的几个闭包
（`send_move` / `log` / `tr` / `arm_dwell`），自己一个 Qt 对象都不碰。
好处很实在：`tests/test_stage_traj.py` 毫秒级跑完，不必起假板子。

单位一律 **mm**（与 `stage_bridge.py` 的界面单位一致；"度"的换算只在桥那一层做，
本文件里绝不出现角度）。

台面坐标系（俯视）：**+X = 右，+Y = 向里**，与 `PositionReadout.qml` 的画法一致。
所以本文件的几何函数拿到的是"台面 mm"坐标，不含任何屏幕方向翻转 ——
翻转只发生在 QML 画图那一步。

⚠ 本文件里**没有**"并发发多条 move"的任何用法：固件那条 `move` 是**非阻塞**
点到点（PD42S1 的 0xF3 相对位置 = 一条完整的梯形曲线，末尾必然减速到 0），
而且在途时再发一条会**静默覆盖**上一条的目标（见 docs/17「轨迹与网格」一节）。
所以序列器必须"发一段 → 等到位 → 再发下一段"，这是本文件存在的全部理由。
"""

from __future__ import annotations

import math
import time
from typing import Callable, List, Optional, Sequence, Tuple

Point = Tuple[float, float]

# ── 采样默认值 ────────────────────────────────────────────────────
# 段长 5mm 的几何误差：弦高 = step²/(8R)。R=50mm → 0.0625mm，肉眼看不出来。
# 想更精细就调小，代价是段数（=总时长）线性上升。
DEFAULT_STEP_MM = 5.0
MIN_STEP_MM = 0.2
MAX_STEP_MM = 50.0


# ================================================================
# 几何（纯函数）
# ================================================================
def _dist(a: Point, b: Point) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def dedupe(points: Sequence[Point], tol: float = 0.05) -> List[Point]:
    """去掉相邻的重复/极近点。

    两个理由，都不是洁癖：
      1. 连续两次 `move` 到同一个点毫无意义，只是白等一个序列器周期；
      2. 更危险的是它会给序列器制造"目标 == 当前位置"的**假到位判据**
         —— 刚发出去就可能被判成"已到位"，于是下一段的增量从运动中的
         瞬时位置算出来（固件 `motor_bus_plan_abs` 是读回当前位置算增量的）。
    """

    out: List[Point] = []
    for p in points:
        p = (float(p[0]), float(p[1]))
        if not out or _dist(out[-1], p) > tol:
            out.append(p)
    return out


def simplify(points: Sequence[Point], tol: float = 0.2) -> List[Point]:
    """Douglas-Peucker 抽稀：压掉共线的冗余点。

    为什么录制轨迹非压不可：十字点动按住连发是"400ms 后每 150ms 一格"
    （JogPad.qml），一秒能产生 6~7 个点、一分钟 400 个点。不压的话
    QSettings 里一条轨迹十几 KB，重放时还全是无意义的微段（每段都要
    一个完整的"等到位"周期，等于把一分钟的手抖重放成三分钟）。

    ⚠ tol 是**弦高**容差，不是点距。0.2mm 对 0.1mm 步长的精细操作是能看出来的，
    所以录制轨迹的调用方别把 tol 放大。
    """

    pts = [(float(p[0]), float(p[1])) for p in points]
    if len(pts) < 3:
        return pts

    def _dp(lo: int, hi: int, keep: List[bool]) -> None:
        if hi <= lo + 1:
            return
        ax, ay = pts[lo]
        bx, by = pts[hi]
        dx, dy = bx - ax, by - ay
        seg = math.hypot(dx, dy)
        worst, worst_i = -1.0, -1
        for i in range(lo + 1, hi):
            px, py = pts[i]
            if seg < 1e-9:
                d = math.hypot(px - ax, py - ay)
            else:
                # 点到弦的垂距（DP 的标准判据）。递归保证了大体走向，
                # 垂距足够表达"这一段偏离弦多少"。
                d = abs(dy * (px - ax) - dx * (py - ay)) / seg
            if d > worst:
                worst, worst_i = d, i
        if worst > tol:
            keep[worst_i] = True
            _dp(lo, worst_i, keep)
            _dp(worst_i, hi, keep)

    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    _dp(0, len(pts) - 1, keep)
    return [p for p, k in zip(pts, keep) if k]


def resample(points: Sequence[Point], step: float = DEFAULT_STEP_MM,
             closed: bool = False) -> List[Point]:
    """按最大弦长 `step` 重采样折线（含首点，不含重复的末点）。

    ⚠ **每一段的两个端点（顶点）都必须精确命中**，不能像"沿着整条折线按弧长
    均匀打点"那样做。理由是一个真踩到的 bug：矩形按 5mm 均匀打点后，
    边长 100/50 不是 5 的整数倍，三个拐角**一个都没落到点上** ——
    于是"矩形"变成了一个三面被削掉 3~4mm 角的形状，而代码看起来完全正常。
    所以这里是**逐段独立**重采样：每段切成 ceil(段长/step) 份，段末精确落在顶点上。
    代价是各段内部的小段长会有零点几毫米的差异，但**形状永远是对的** ——
    对"我要走一个矩形"这种需求，形状正确压倒段长均匀。

    `closed=True` 时把首点接回末尾（圆/矩形用），但**不重复输出首点** ——
    序列器的最后一段会自然走回起点，重复一个点只会多一次无意义的移动。
    """

    pts = [(float(p[0]), float(p[1])) for p in points]
    if closed and len(pts) >= 2 and _dist(pts[0], pts[-1]) > 1e-9:
        pts = pts + [pts[0]]
    pts = dedupe(pts)
    if len(pts) < 2:
        return pts
    step = max(MIN_STEP_MM, float(step))

    out: List[Point] = [pts[0]]
    for a, b in zip(pts, pts[1:]):
        seg = _dist(a, b)
        if seg < 1e-9:
            continue
        n = max(1, int(math.ceil(seg / step)))
        for i in range(1, n + 1):
            if i == n:
                p = b          # 顶点直接取原值：浮点累加会让它差最后几个 ulp
            else:
                k = i / n
                p = (a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k)
            if _dist(out[-1], p) > 1e-9:
                out.append(p)
    return out


def catmull_rom(points: Sequence[Point], samples_per_seg: int = 16) -> List[Point]:
    """过点 Catmull-Rom 样条：手点出来的多段线勾了「平滑」时先过一遍它。

    为什么不用贝塞尔：贝塞尔**不过控制点**，用户点的位置会被拉歪；而
    Catmull-Rom 是插值样条 —— 点在哪，曲线就过哪。对"我手画的这条线"
    这个语义来说，过点才是对的。
    """

    pts = dedupe([(float(p[0]), float(p[1])) for p in points])
    if len(pts) < 3:
        return pts
    # 两端各镜像一个虚拟点，让首末段也有切线（否则首末段会退化成直线）
    ext = [(2 * pts[0][0] - pts[1][0], 2 * pts[0][1] - pts[1][1])] + pts \
        + [(2 * pts[-1][0] - pts[-2][0], 2 * pts[-1][1] - pts[-2][1])]

    n = max(2, int(samples_per_seg))
    out: List[Point] = []
    for i in range(1, len(ext) - 2):
        p0, p1, p2, p3 = ext[i - 1], ext[i], ext[i + 1], ext[i + 2]
        for s in range(n):
            t = s / n
            t2, t3 = t * t, t * t * t
            x = 0.5 * ((2 * p1[0])
                       + (-p0[0] + p2[0]) * t
                       + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                       + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3)
            y = 0.5 * ((2 * p1[1])
                       + (-p0[1] + p2[1]) * t
                       + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                       + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)
            out.append((x, y))
    out.append(ext[-2])
    return out


def sample_polyline(points: Sequence[Point], step: float = DEFAULT_STEP_MM,
                    smooth: bool = False, closed: bool = False) -> List[Point]:
    """多段线 → 落点序列。`smooth` 先过 Catmull-Rom 再重采样。"""
    pts = list(points)
    if smooth:
        pts = catmull_rom(pts)
    return resample(pts, step=step, closed=closed)


def sample_circle(cx: float, cy: float, r: float,
                  step: float = DEFAULT_STEP_MM) -> List[Point]:
    """圆 → 落点序列（闭合，不重复末点）。

    段数按"弦长 step"算而不是按固定点数：这样段长恒定，不会出现
    "小圆很精细、大圆很粗糙"。段数 = ceil(周长 / step)。
    """
    r = abs(float(r))
    if r < 1e-6:
        return [(float(cx), float(cy))]
    step = max(MIN_STEP_MM, float(step))
    n = max(8, int(math.ceil(2 * math.pi * r / step)))
    return [(cx + r * math.cos(2 * math.pi * i / n),
             cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)]


def sample_rect(x0: float, y0: float, x1: float, y1: float,
                step: float = DEFAULT_STEP_MM) -> List[Point]:
    """矩形（四个角，闭合）→ 落点序列。两个角点顺序随意，内部会归一化。"""
    xa, xb = min(x0, x1), max(x0, x1)
    ya, yb = min(y0, y1), max(y0, y1)
    corners = [(xa, ya), (xb, ya), (xb, yb), (xa, yb)]
    return resample(corners, step=step, closed=True)


def sample_grid(rows: int, cols: int, x0: float, y0: float, x1: float, y1: float,
                serpentine: bool = True) -> List[Point]:
    """巡检点阵 → 落点序列。

    取**格心**而不是格线交点：拍摄覆盖讲的是"每个格子中心拍一张，
    到格内任意位置的最大距离最短"，格心正是这个解；而且它天然不会把点落在
    工作区边界上（边界上相机视野只剩半截）。

    `serpentine=True`（蛇形）时奇数行反向 —— 相邻行的跳转距离从"整行宽"
    降到"一格宽"，一趟巡检能省掉大量空行程。
    """
    rows = max(1, int(rows))
    cols = max(1, int(cols))
    xa, xb = min(x0, x1), max(x0, x1)
    ya, yb = min(y0, y1), max(y0, y1)
    cw = (xb - xa) / cols
    ch = (yb - ya) / rows
    out: List[Point] = []
    for r in range(rows):
        cy = ya + ch * (r + 0.5)
        cs = range(cols)
        if serpentine and (r % 2 == 1):
            cs = range(cols - 1, -1, -1)
        for c in cs:
            out.append((xa + cw * (c + 0.5), cy))
    return out


def polyline_length(points: Sequence[Point]) -> float:
    """折线总长（mm）。用于界面上的"预计耗时"估算。"""
    return sum(_dist(a, b) for a, b in zip(points, points[1:]))


# ⚠ 2026-10-10 删除了 `snap_to_cell()`（屏幕比例 → 最近格心）。
# 用户要求台面背景"只留刻度标尺、不要网格"，点选也就不再吸附格心
# —— 背景没有格子，吸附失去视觉依据；而且不吸附精度更高（±1mm vs 18mm 格心）。
# 现在点选的换算在 `TrajectoryGridCard.qml` 的 `_mmOf()` 里做（那是卡片里唯一的
# 坐标变换，与画图共用）。`sample_grid()` **保留**：它是「网格」模式生成巡检
# 点阵用的，与"背景格线"从来不是一回事。


# ================================================================
# 点列序列器
# ================================================================
STATE_IDLE = "idle"
STATE_RUNNING = "running"
STATE_PAUSED = "paused"
STATE_DONE = "done"
STATE_ABORTED = "aborted"

# 每段"多久算卡死"的兜底：理论耗时 = 段长 / 线速度，但真正的开销是驱动器
# 内部梯形曲线的起步/刹停斜坡（远大于匀速段）。所以给一个很大的绝对余量；
# 这是**兜底**，不是精度控制 —— 宁可慢，也不要在半路误判超时而把整条轨迹打断。
SEG_TIMEOUT_BASE_S = 4.0
SEG_TIMEOUT_FACTOR = 4.0

# 到位判据用的**保守线速度下界**（mm/s）。只用于上面那个超时估算。
SEG_TIMEOUT_MIN_SPEED = 20.0

# 位置到位容差（mm）。固件的到位判据是三条取或：驱动器 0x30 到位标志 /
# 位置进入 ARRIVE_TOL_COUNTS(256 counts ≈ 0.16mm) / Lim 引脚。
# 这里放宽到 0.5mm，只用来识别"固件报了超时但其实没到位"这种异常；
# 正常到位时位置误差在几个 counts 以内。
ARRIVE_TOL_MM = 0.5

# 一段发出去之后，至少过这么久才认"已到位"。
# 防的是**陈旧帧**：一条在 move 之前就发出去的 json，可能在我们发完 move
# 之后才被应用 —— 那一帧的 moving 还是 0、位置还是上一段的目标。若目标恰好
# 很接近（密集采样的小段），就会被误判成"已到位"，于是下一段的增量从
# "运动中的瞬时位置"算出来，误差逐段累积。
MIN_SEG_TIME_S = 0.08

# "板子报了不在动、但位置没到"要连续持续多久才认定是真的没走完（秒）。
# 不能一帧就判死：`json` 的 moving 由 100ms 的 state_tick 收尾，
# 位置读回也可能比 moving 慢一帧。但也不能等太久 —— 这段时间台面**可能还在动**。
STALL_GRACE_S = 0.6

# 到点停留（二期"到点拍照"用）的上限
MAX_DWELL_MS = 60000


class PathRunner:
    """点列序列器：一段一段地发 `move`，等板子报到位，再发下一段。

    **为什么不能连发**（本文件存在的理由）：
      · 固件的 `move` 只把目标登记在 `s_pend` 里就返回；在途时再发一条会
        **静默覆盖**上一条的目标（`s_motion_lock` 只覆盖命令自身那 45~50ms，
        压根不看 `PEND_RUNNING`）；
      · `move` 的增量是固件**读回当前位置**算出来的，运动中去读就是个移动靶；
      · 驱动器收到的 0xF3 是"一条完整的梯形位置曲线"，末尾必然减速到 0，
        本来就得等它走完。

    调用方要给的（全是普通可调用对象，不要求是 Qt 对象）：
      · `send_move(x, y) -> bool`：下发一条**绝对**移动（速度用调用方自己的
        轨迹速度）。返回 False = 被拒（未连接/无基准/无行程/运动中），
        序列器立刻中止并报原因。
      · `log(msg)`：写进桥的日志/协议框。
      · `tr(text)`：翻译。**必须传桥的 `self.tr`** —— 序列器的日志行会画在
        诊断面板里，属于用户可见文案（AGENTS.md：别以为"日志就不用翻"）。
      · `now_fn()`：单调时钟（秒）。默认 `time.monotonic`，测试注入假时钟。
      · `arm_dwell(ms, cb)`（可选）：延时 ms 后调 cb，用于到点停留/到点拍照。
        不传就完全没有停留。

    遥测由调用方驱动：每收到一帧 `json` 就调 `on_telemetry(seq, x, y, moving)`，
    `seq` 是**单调递增的帧序号**（桥自己维护）。序号是必需的，理由见 MIN_SEG_TIME_S。
    """

    def __init__(self,
                 send_move: Callable[[float, float], bool],
                 log: Callable[[str], None],
                 tr: Optional[Callable[[str], str]] = None,
                 now_fn: Callable[[], float] = time.monotonic,
                 arm_dwell: Optional[Callable[[int, Callable[[], None]], None]] = None):
        self._send_move = send_move
        self._log = log
        self._tr = tr if tr is not None else (lambda s: s)
        self._now = now_fn
        self._arm_dwell = arm_dwell

        self._points: List[Point] = []
        self._total = 0
        self._done = 0              # 已确认到位的段数
        self._sent = -1             # 已发出的最后一段号
        self._state = STATE_IDLE
        self._tol = ARRIVE_TOL_MM
        self._dwell_ms = 0
        self._target: Optional[Point] = None
        self._last_seq = 0
        self._sent_seq = 0
        self._sent_at = 0.0
        self._deadline = 0.0
        self._stall_since: Optional[float] = None
        self._waiting_dwell = False
        self._abort_reason = ""

        # 状态/进度变化回调（调用方拿去 emit Qt 信号）
        self.on_progress: Optional[Callable[[int, int], None]] = None
        self.on_state: Optional[Callable[[str, str], None]] = None

    # ── 只读状态 ──────────────────────────────────────────────
    @property
    def state(self) -> str:
        return self._state

    @property
    def done(self) -> int:
        """已确认到位的段数（进度条用）。"""
        return self._done

    @property
    def total(self) -> int:
        return self._total

    @property
    def busy(self) -> bool:
        return self._state in (STATE_RUNNING, STATE_PAUSED)

    @property
    def abort_reason(self) -> str:
        return self._abort_reason

    @property
    def dwell_ms(self) -> int:
        return self._dwell_ms

    def set_dwell(self, ms: int) -> None:
        """每段到位后的停留（ms），0 = 不停留。

        即使是 0 也有意义：二期"到点拍照"要的就是这个停留 —— 机械余振没停
        就走，照片是糊的。
        """
        self._dwell_ms = max(0, min(MAX_DWELL_MS, int(ms)))

    # ── 生命周期 ──────────────────────────────────────────────
    def start(self, points: Sequence[Point], seq: int) -> bool:
        """开始走一条轨迹。`seq` = 当前遥测帧序号（用来排除陈旧帧）。"""
        pts = dedupe(points)
        if not pts:
            self._abort_reason = self._tr("轨迹是空的")
            self._finish(STATE_ABORTED)
            return False
        self._points = pts
        self._total = len(pts)
        self._done = 0
        self._sent = -1
        self._abort_reason = ""
        self._stall_since = None
        self._waiting_dwell = False
        self._last_seq = int(seq)
        self._sent_seq = int(seq)
        if len(pts) >= 2:
            # 段太短时容差不能比段长还大，否则第一帧就判"到位"
            min_seg = min(_dist(a, b) for a, b in zip(pts, pts[1:]))
            self._tol = max(0.02, min(ARRIVE_TOL_MM, min_seg / 4.0))
        else:
            self._tol = ARRIVE_TOL_MM
        self._set_state(STATE_RUNNING)
        self._log(self._tr("轨迹开始：共 {} 段").format(self._total))
        self._send_next()
        return self._state == STATE_RUNNING

    def pause(self) -> bool:
        """暂停：**不在中途急停**，而是走完当前段后不再发下一段。

        这样台面永远停在一个明确的落点上，恢复时不用重新规划 ——
        也不会把"暂停"变成一次半路的急刹（半路停下会让"下一个落点在哪"
        变成一件要重新算的事）。
        """
        if self._state != STATE_RUNNING:
            return False
        self._set_state(STATE_PAUSED)
        self._log(self._tr("轨迹已暂停：走完当前落点后停下，点「继续」接着走"))
        return True

    def resume(self) -> bool:
        if self._state != STATE_PAUSED:
            return False
        self._set_state(STATE_RUNNING)
        self._log(self._tr("轨迹继续"))
        if self._waiting_dwell:
            return True                 # 停"到点停留"里了，等它自己回来
        self._send_next()
        return True

    def abort(self, reason: str = "") -> bool:
        """中止整条轨迹（急停 / 断线 / 用户点停止 / 被拒）。

        ⚠ 必须在桥的 `stopNow()` **之前**调用：急停本身会让 `moving` 变成 0，
        若序列器还在跑，它会把这帧当成"这一段到位了"而发出下一段
        —— 于是刚急停完，台面又自己动起来。
        """
        if not self.busy:
            return False
        self._abort_reason = reason
        # ⚠ 不要写成 self._tr("轨迹中止").format() —— 译文里若出现 `{}`
        #   会抛 KeyError/IndexError，把一次中止变成一次异常。
        msg = self._tr("轨迹中止")
        if reason:
            msg = msg + "：" + reason
        self._log(msg)
        self._finish(STATE_ABORTED)
        return True

    def reset(self) -> None:
        """回到 idle。跑着的时候不动，必须先 abort。

        ⚠ **不清 `total`/`done`** —— 界面在"跑完/中止"之后还要显示
        "63/63"或"中止于 21/63"，清了就只剩一个空进度条，用户不知道上一趟
        走到哪了。下一次 `start()` 会重置它们。
        """
        if self.busy:
            return
        self._target = None
        self._waiting_dwell = False
        self._abort_reason = ""
        self._set_state(STATE_IDLE)

    # ── 遥测驱动 ──────────────────────────────────────────────
    def on_telemetry(self, seq: int, x: float, y: float, moving: bool) -> None:
        """每帧 `json` 调一次。"""
        self._last_seq = int(seq)
        if self._state in (STATE_IDLE, STATE_DONE, STATE_ABORTED):
            return

        if moving:
            self._stall_since = None
            return

        if self._state == STATE_PAUSED:
            # 暂停期间只关心"当前这一段走完没有"：走完了就停在落点上不动
            if self._target is not None and _dist((x, y), self._target) <= self._tol:
                self._target = None
                self._done += 1
                self._notify_progress()
            return

        if self._target is None:
            return

        now = self._now()
        # ── 陈旧帧闸门 ──
        # ① 序号必须比"发这条 move 时的最新帧"更新；
        # ② 且距离发出至少要过 MIN_SEG_TIME_S。
        if seq <= self._sent_seq or (now - self._sent_at) < MIN_SEG_TIME_S:
            return

        if _dist((x, y), self._target) <= self._tol:
            # 到位
            self._done += 1
            self._target = None
            self._stall_since = None
            self._notify_progress()
            if self._dwell_ms > 0 and self._done < self._total and self._arm_dwell is not None:
                self._waiting_dwell = True
                self._arm_dwell(self._dwell_ms, self._after_dwell)
                return
            self._send_next()
            return

        # 板子说"不在动了"，但位置离目标还差得远 → 这不是到位，是**没走完**。
        # 固件的 `pend_poll` 超时后只把状态标成 TIMEOUT、**不发刹车**
        # （console_cmds.c:506-530），所以"moving=0 但没到位"完全可能。
        # 这时继续发下一段的后果最严重：增量会从运动中的瞬时位置算出来。
        if self._stall_since is None:
            self._stall_since = now
        if (now - self._stall_since) >= STALL_GRACE_S or now >= self._deadline:
            self.abort(self._tr(
                "第 {} 段没有走完（离目标还差 {:.2f}mm，板子已报停）—— "
                "多半是固件的运动超时兜底触发了。检查：目标是否被工作区夹取、"
                "速度/加减速是否太激进、是否有机械卡阻").format(self._sent + 1, _dist((x, y), self._target)))

    # ── 内部 ──────────────────────────────────────────────────
    def _after_dwell(self) -> None:
        self._waiting_dwell = False
        if self._state == STATE_RUNNING:
            self._send_next()

    def _send_next(self) -> None:
        if self._state != STATE_RUNNING:
            return
        nxt = self._sent + 1
        if nxt >= self._total:
            self._finish(STATE_DONE)
            return
        p = self._points[nxt]
        now = self._now()
        seg = 0.0 if nxt == 0 else _dist(self._points[nxt - 1], p)
        if not self._send_move(p[0], p[1]):
            self.abort(self._tr(
                "第 {} 段被固件拒绝（未连接 / 缺基准 / 未设工作区 / 台面正在动）").format(nxt + 1))
            return
        self._sent = nxt
        self._target = p
        self._sent_seq = self._last_seq
        self._sent_at = now
        self._stall_since = None
        self._deadline = now + self._seg_timeout(seg)
        self._notify_progress()

    def _seg_timeout(self, seg_mm: float) -> float:
        est = seg_mm / SEG_TIMEOUT_MIN_SPEED
        return SEG_TIMEOUT_BASE_S + est * SEG_TIMEOUT_FACTOR

    def _finish(self, state: str) -> None:
        self._target = None
        if state == STATE_DONE:
            self._done = self._total
            self._log(self._tr("轨迹走完：共 {} 段").format(self._total))
        self._set_state(state)

    def _set_state(self, s: str) -> None:
        if s == self._state:
            return
        self._state = s
        if self.on_state is not None:
            self.on_state(s, self._abort_reason)

    def _notify_progress(self) -> None:
        if self.on_progress is not None:
            self.on_progress(min(self._done, self._total), self._total)


__all__ = [
    "dedupe", "simplify", "resample", "catmull_rom", "polyline_length",
    "sample_polyline", "sample_circle", "sample_rect", "sample_grid",
    "PathRunner", "STATE_IDLE", "STATE_RUNNING", "STATE_PAUSED",
    "STATE_DONE", "STATE_ABORTED",
    "DEFAULT_STEP_MM", "MIN_STEP_MM", "MAX_STEP_MM",
]
