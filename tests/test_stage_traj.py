#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""轨迹几何 + 点列序列器回归测试（**纯 Python，不需要 Qt/事件循环/假板子**）。

为什么单独一个测试文件：`stage_traj.py` 是刻意做成"不碰 Qt"的 —— 几何是纯函数，
序列器只调几个闭包 + 注入的单调时钟。所以这里能用**假时钟**把"什么时候发下一段"
钉死，毫秒级跑完；不必起 QCoreApplication、不必等真时间。

这块逻辑的正确性全在"时序"上，而时序 bug 在真机上表现为"轨迹走着走着偏了"，
极难归因。所以下面每一条都是时序断言，不是"能跑就行"。

运行：
    source DuAD_SoftwareContent/pyqml/bin/activate
    python3 -u tests/test_stage_traj.py
"""

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from Src import stage_traj as T                                    # noqa: E402


class Runner:
    def __init__(self):
        self.fails = []

    def check(self, name, cond, extra=""):
        print(("  [ok] " if cond else "  [FAIL] ") + name + (("   " + extra) if extra else ""))
        if not cond:
            self.fails.append(name)


# ================================================================
# 假台面 + 假时钟
# ================================================================
class FakeStage:
    """只做序列器真正关心的事：收 move、记账、模拟"走完/没走完"。"""

    def __init__(self):
        self.sent = []              # 收到的 move 目标（按顺序）
        self.target = None
        self.pos = (0.0, 0.0)
        self.moving = False
        self.reject_next = False
        self.seq = 0

    def send_move(self, x, y):
        if self.reject_next:
            self.reject_next = False
            return False
        # ⚠ 真实固件允许"在途时再发一条"并静默覆盖（docs/17「轨迹与网格」）。
        #   测试替身**故意不拦**：这样"序列器有没有并发发两条"就完全暴露在
        #   `sent` 的长度与顺序上，而不是被替身的宽容掩盖过去。
        self.sent.append((x, y))
        self.target = (x, y)
        self.moving = True
        return True

    def arrive(self):
        """让台面走到当前目标。"""
        if self.target is not None:
            self.pos = self.target
        self.moving = False

    def frame(self, runner, clock, dt=0.2, moving=None, pos=None):
        """推进假时钟 + 交付一帧遥测。"""
        clock[0] += dt
        self.seq += 1
        if moving is not None:
            self.moving = moving
        p = self.pos if pos is None else pos
        runner.on_telemetry(self.seq, p[0], p[1], self.moving)


def make_runner(stage, clock, logs=None, dwells=None, tr=None):
    logs = logs if logs is not None else []
    r = T.PathRunner(
        send_move=stage.send_move,
        log=logs.append,
        tr=tr,
        now_fn=lambda: clock[0],
        arm_dwell=(lambda ms, cb: dwells.append((ms, cb))) if dwells is not None else None,
    )
    return r


def drive_to_end(runner, stage, clock, max_frames=1000):
    """反复交付"到位"帧，直到序列器不再是 busy。"""
    n = 0
    while runner.busy and n < max_frames:
        stage.arrive()
        stage.frame(runner, clock)
        n += 1
    return n


# ================================================================
# 几何
# ================================================================
def test_geometry(r):
    print("=== 1) 几何：去重 / 抽稀 ===")
    pts = [(0, 0), (0, 0), (0, 0.01), (10, 0)]
    d = T.dedupe(pts)
    r.check("相邻重复点被去掉", d == [(0, 0), (10, 0)], str(d))
    r.check("去重不会合并真正不同的点",
            T.dedupe([(0, 0), (1, 0)]) == [(0, 0), (1, 0)])

    line = [(float(i), 0.0) for i in range(100)]
    s = T.simplify(line)
    r.check("共线 100 点被压成 2 点", len(s) == 2, f"n={len(s)}")
    corner = [(0, 0), (50, 0), (100, 0), (100, 50), (100, 100)]
    s2 = T.simplify(corner)
    r.check("拐点被保留（不会被抽掉）", (100, 0) in s2 and len(s2) == 3, str(s2))
    # 抽稀的容差是弦高：偏离 0.1mm 的点在 tol=0.2 时应被抽掉
    r.check("弦高小于容差的点被抽掉",
            len(T.simplify([(0, 0), (50, 0.1), (100, 0)], tol=0.2)) == 2)
    r.check("弦高大于容差的点被保留",
            len(T.simplify([(0, 0), (50, 5.0), (100, 0)], tol=0.2)) == 3)

    print("=== 2) 几何：重采样 ===")
    out = T.resample([(0, 0), (100, 0)], step=5.0)
    segs = [math.dist(a, b) for a, b in zip(out, out[1:])]
    r.check("首末点保留", out[0] == (0.0, 0.0) and out[-1] == (100.0, 0.0))
    r.check("每段不超过步长", max(segs) <= 5.0 + 1e-6, f"max={max(segs):.4f}")
    r.check("没有零长度段", min(segs) > 1e-6)
    # 零头要带到下一段：直线 100mm / 5mm 应该正好 20 段、21 个点
    r.check("正好整除时点数正确", len(out) == 21, f"n={len(out)}")
    # 折线上一个 3mm 的段后面接 100mm：不能在每个转角都吐出一个短段
    out2 = T.resample([(0, 0), (3, 0), (103, 0)], step=5.0)
    segs2 = [math.dist(a, b) for a, b in zip(out2, out2[1:])]
    r.check("转角后不产生多余短段", min(segs2) > 1.0, f"min={min(segs2):.4f}")

    closed = T.resample([(0, 0), (10, 0), (10, 10), (0, 10)], step=3.0, closed=True)
    r.check("闭合折线回到起点", math.dist(closed[-1], closed[0]) < 1e-6, str(closed[-1]))
    r.check("闭合折线不重复输出首点", math.dist(closed[-1], closed[-2]) > 1e-6)

    print("=== 3) 几何：圆 / 矩形 / 点阵 ===")
    c = T.sample_circle(100, 100, 50, step=5.0)
    rr = [math.dist(p, (100, 100)) for p in c]
    r.check("圆上所有点到圆心距离相等",
            max(abs(v - 50.0) for v in rr) < 1e-6, f"max_err={max(abs(v-50) for v in rr):.2e}")
    n_expect = math.ceil(2 * math.pi * 50 / 5.0)
    r.check("段数 = ceil(周长/段长)", len(c) == n_expect, f"{len(c)} vs {n_expect}")
    r.check("最后一点不回重复起点", math.dist(c[0], c[-1]) > 1e-6)

    rect = T.sample_rect(10, 20, 110, 70, step=5.0)
    r.check("矩形四个角都在落点里",
            all(any(math.dist(p, k) < 1e-6 for p in rect)
                for k in [(10, 20), (110, 20), (110, 70), (10, 70)]))
    r.check("矩形两角顺序反了也能归一化",
            T.sample_rect(110, 70, 10, 20, step=5.0) == rect)

    g = T.sample_grid(3, 4, 0, 0, 120, 90)
    r.check("点阵个数 = 行×列", len(g) == 12, f"n={len(g)}")
    r.check("点阵取格心（不在边界上）",
            all(0 < p[0] < 120 and 0 < p[1] < 90 for p in g))
    r.check("格心坐标正确",
            math.dist(g[0], (15.0, 15.0)) < 1e-6, str(g[0]))
    r.check("蛇形：第 2 行反向",
            math.dist(g[4], (105.0, 45.0)) < 1e-6, str(g[4]))
    g2 = T.sample_grid(3, 4, 0, 0, 120, 90, serpentine=False)
    r.check("关掉蛇形时第 2 行正序",
            math.dist(g2[4], (15.0, 45.0)) < 1e-6, str(g2[4]))

    print("=== 4) 几何：网格点阵（「网格」模式生成的巡检点）===")
    # ⚠ 2026-10-10 删除了 `snap_to_cell`（屏幕比例 → 最近格心）：台面背景不再画
    #   格线、点选也不再吸附，那个函数没有消费者了。`sample_grid`（上面那组）
    #   保留 —— 它就是「网格」模式生成巡检点阵用的，与"背景格线"从来是两回事。

    print("=== 5) 几何：平滑样条过点 ===")
    hand = [(0, 0), (30, 40), (60, 0), (90, 40)]
    cr = T.catmull_rom(hand, samples_per_seg=8)
    r.check("样条**过**用户点的位置",
            all(any(math.dist(p, k) < 1e-6 for p in cr) for k in hand))
    r.check("样条点数 = (段数)×采样 + 1", len(cr) == 3 * 8 + 1, f"n={len(cr)}")
    r.check("少于 3 个点时退化为原折线", T.catmull_rom([(0, 0), (1, 1)]) == [(0.0, 0.0), (1.0, 1.0)])


# ================================================================
# 序列器
# ================================================================
def test_runner(r):
    print("=== 6) 序列器：逐段走、绝不并发 ===")
    clock = [1000.0]
    st = FakeStage()
    logs = []
    run = make_runner(st, clock, logs=logs)
    done_events = []
    run.on_progress = lambda d, t: done_events.append((d, t))
    ok = run.start([(0, 0), (10, 0), (10, 10)], seq=0)
    r.check("start 返回 True", ok is True)
    r.check("只发了第一段", st.sent == [(0.0, 0.0)], str(st.sent))
    r.check("状态 running", run.state == T.STATE_RUNNING)

    # ⚠ 第 0 段的落点就是起点，所以"第一帧就报到位"是**正确**行为。
    #   为了单独验 MIN_SEG_TIME，前两帧必须把累计时间压在 0.08s 以内。
    st.frame(run, clock, dt=0.02)          # moving=True（send_move 里置的）
    st.arrive()
    st.frame(run, clock, dt=0.02)          # 累计 0.04s < MIN_SEG_TIME
    r.check("MIN_SEG_TIME 内不认到位、不发下一段", st.sent == [(0.0, 0.0)], str(st.sent))

    st.arrive()
    st.frame(run, clock, dt=0.15)          # 累计 0.19s > MIN_SEG_TIME
    r.check("到位后立刻发下一段", st.sent == [(0.0, 0.0), (10.0, 0.0)], str(st.sent))
    r.check("发第二段时第一段已确认（绝不并发）", run.done == 1, f"done={run.done}")

    n = drive_to_end(run, st, clock)
    r.check("三段全部走完", st.sent == [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)], str(st.sent))
    r.check("状态 done", run.state == T.STATE_DONE, run.state)
    r.check("进度到 3/3", (run.done, run.total) == (3, 3), f"{run.done}/{run.total}")
    r.check("进度回调收到 3/3", done_events[-1] == (3, 3), str(done_events[-1]))
    r.check("不再 busy", not run.busy)

    print("=== 7) 序列器：陈旧帧不得被判成到位 ===")
    # 场景：一段 move 刚发出去，一条**在它之前**发出的 json 才被应用 ——
    # 那一帧的 moving=0、位置还是上一段的目标。若不看序号就会误发下一段。
    clock = [2000.0]
    st = FakeStage()
    run = make_runner(st, clock)
    run.start([(0, 0), (0.5, 0)], seq=0)     # 段长很小，tol 会缩到 0.125
    st.arrive()
    # 手工造一帧"序号没涨"的陈旧帧（seq 还是 0）
    run.on_telemetry(0, 0.0, 0.0, False)
    r.check("陈旧帧（seq 没涨）不被当成到位", run.done == 0, f"done={run.done}")
    st.frame(run, clock, dt=0.2)              # 新帧（seq=1）+ 位置已到
    r.check("新帧才认到位", run.done == 1, f"done={run.done}")

    print("=== 8) 序列器：板子报停但没到位 → 中止（不是推进）===")
    # 固件 pend_poll 超时后只标 TIMEOUT、**不发刹车**，所以这种情况真的会发生。
    # 若把它当"到位"，下一段的增量就会从运动中的瞬时位置算出来。
    clock = [3000.0]
    st = FakeStage()
    run = make_runner(st, clock)
    run.start([(0, 0), (100, 0), (100, 100)], seq=0)
    st.arrive()                                  # 第 0 段落点=起点，板子瞬间到位
    st.frame(run, clock, dt=0.2)                 # → 确认第 0 段，发出第 1 段
    r.check("起点段确认后发出第二段",
            st.sent == [(0.0, 0.0), (100.0, 0.0)], str(st.sent))
    # 现在第 1 段在途：板子一直报"不在动"、位置却停在原地
    st.frame(run, clock, dt=0.2, moving=False, pos=(0.0, 0.0))
    r.check("第一帧不急着判死（可能只是位置读回慢一帧）",
            run.state == T.STATE_RUNNING, run.state)
    for _ in range(5):
        st.frame(run, clock, dt=0.2, moving=False, pos=(0.0, 0.0))
    r.check("持续报停且没到位 → 中止", run.state == T.STATE_ABORTED, run.state)
    r.check("中止原因说得清是哪一段",
            "第 2 段" in run.abort_reason, run.abort_reason[:40])
    r.check("中止后不再补发下一段",
            st.sent == [(0.0, 0.0), (100.0, 0.0)], str(st.sent))

    print("=== 9) 序列器：被固件拒绝 → 立刻中止 ===")
    # 最典型的场景：还没立基准 / 没设工作区 —— 固件 fail-closed 直接拒。
    clock = [4000.0]
    st = FakeStage()
    run = make_runner(st, clock)
    st.reject_next = True
    ok = run.start([(0, 0), (10, 0)], seq=0)
    r.check("第一段被拒时 start 返回 False", ok is False)
    r.check("拒绝后状态 aborted", run.state == T.STATE_ABORTED, run.state)
    r.check("拒绝原因可读", "被固件拒绝" in run.abort_reason, run.abort_reason)
    r.check("被拒后一段都没发出去", st.sent == [], str(st.sent))

    print("=== 10) 序列器：急停/中止必须立刻停手 ===")
    clock = [5000.0]
    st = FakeStage()
    run = make_runner(st, clock)
    run.start([(0, 0), (10, 0), (20, 0), (30, 0)], seq=0)
    r.check("中止前在跑", run.busy)
    run.abort("用户点了停止")
    r.check("abort 后不再 busy", not run.busy)
    n_before = len(st.sent)
    # 急停本身会让板子报 moving=0 —— 序列器若还活着就会把这帧当"到位"而补发下一段
    for _ in range(5):
        st.arrive()
        st.frame(run, clock, dt=0.2)
    r.check("中止后即使收到'到位'帧也不再发段", len(st.sent) == n_before,
            f"{n_before} → {len(st.sent)}")
    r.check("abort 原因保留", run.abort_reason == "用户点了停止", run.abort_reason)

    print("=== 11) 序列器：暂停停在落点、继续接着走 ===")
    clock = [6000.0]
    st = FakeStage()
    run = make_runner(st, clock)
    run.start([(0, 0), (10, 0), (20, 0)], seq=0)
    st.arrive()
    st.frame(run, clock, dt=0.2)                 # 第 1 段到位，第 2 段发出
    r.check("暂停前已发第 2 段", st.sent == [(0.0, 0.0), (10.0, 0.0)], str(st.sent))
    run.pause()
    st.arrive()
    st.frame(run, clock, dt=0.2)                 # 第 2 段走完 → 停在落点
    r.check("暂停后不再发第 3 段", st.sent == [(0.0, 0.0), (10.0, 0.0)], str(st.sent))
    r.check("暂停后已完成 2 段", run.done == 2, f"done={run.done}")
    r.check("状态 paused", run.state == T.STATE_PAUSED, run.state)
    run.resume()
    r.check("继续后发出第 3 段（没有重发第 2 段）",
            st.sent == [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0)], str(st.sent))
    drive_to_end(run, st, clock)
    r.check("继续后能走完", run.state == T.STATE_DONE, run.state)

    print("=== 12) 序列器：到点停留（二期拍照的钩子，一期不传就没有）===")
    clock = [7000.0]
    st = FakeStage()
    dwells = []
    run = make_runner(st, clock, dwells=dwells)
    run.set_dwell(300)
    run.start([(0, 0), (10, 0)], seq=0)
    st.arrive()
    st.frame(run, clock, dt=0.2)
    r.check("第 1 段到位后进入停留、先不发第 2 段",
            len(st.sent) == 1 and len(dwells) == 1, f"sent={st.sent} dwells={dwells}")
    r.check("停留时长传给调用方", dwells[0][0] == 300)
    dwells[0][1]()                               # 模拟延时到点
    r.check("停留结束后发第 2 段", len(st.sent) == 2, str(st.sent))
    # 未传 arm_dwell 时：即便设了 dwell 也不能卡住（宁可不停留，也不能停在半路）
    clock = [7100.0]
    st2 = FakeStage()
    run2 = make_runner(st2, clock)               # 不传 dwells
    run2.set_dwell(300)
    run2.start([(0, 0), (10, 0)], seq=0)
    st2.arrive()
    st2.frame(run2, clock, dt=0.2)
    r.check("没有 arm_dwell 时不停留、直接往下走", len(st2.sent) == 2, str(st2.sent))

    print("=== 13) 序列器：边界情况 ===")
    clock = [8000.0]
    st = FakeStage()
    run = make_runner(st, clock)
    ok = run.start([], seq=0)
    r.check("空轨迹被拒", ok is False and run.state == T.STATE_ABORTED, run.state)
    r.check("空轨迹有可读原因", run.abort_reason != "", run.abort_reason)

    clock = [8100.0]
    st = FakeStage()
    run = make_runner(st, clock)
    run.start([(5, 5), (5, 5), (5, 5)], seq=0)   # 全重复 → dedupe 成 1 个点
    r.check("全重复点被压成一个点", st.sent == [(5.0, 5.0)], str(st.sent))
    st.arrive()
    n = drive_to_end(run, st, clock)
    r.check("单点轨迹走完（只发一段）", st.sent == [(5.0, 5.0)] and run.state == T.STATE_DONE,
            f"sent={st.sent} state={run.state}")

    clock = [8200.0]
    st = FakeStage()
    run = make_runner(st, clock)
    # 小段长时容差必须跟着缩，否则第一帧就判"到位"
    run.start([(0, 0), (0.4, 0)], seq=0)
    r.check("段长 0.4mm 时容差缩到 ≤0.1mm", run._tol <= 0.1 + 1e-9, f"tol={run._tol}")

    print("=== 14) 序列器：i18n —— 日志必须走注入的 tr ===")
    clock = [9000.0]
    st = FakeStage()
    logs = []
    seen = []
    run = make_runner(st, clock, logs=logs, tr=lambda s: seen.append(s) or ("[EN]" + s))
    run.start([(0, 0), (1, 0)], seq=0)
    drive_to_end(run, st, clock)
    r.check("每条日志都过了 tr（界面日志面板要显示译文）",
            len(seen) >= 2 and all(s.startswith("[EN]") for s in logs),
            str(logs))
    r.check("tr 收到的是原文（不是已拼好的中文句子才去翻）",
            any("轨迹开始" in s for s in seen), str(seen[:2]))


def main():
    r = Runner()
    test_geometry(r)
    test_runner(r)
    print()
    if r.fails:
        print(f"FAILED {len(r.fails)}: {r.fails}")
        return 1
    print("stage_traj 全部断言通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
