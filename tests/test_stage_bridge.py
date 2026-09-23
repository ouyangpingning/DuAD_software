#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""StageBridge 回归测试 —— 用进程内的假板子验证二轴平台桥的逻辑。

为什么要有它：真板子不在手边时，协议分帧、命令队列、单位换算、四道闸的界面
配合、预设读写这些逻辑照样能验。**真机上没验过的东西，至少先在这里验一遍。**

运行（不需要真板子、不需要相机）：
    source DuAD_SoftwareContent/pyqml/bin/activate
    QT_QPA_PLATFORM=offscreen python3 tests/test_stage_bridge.py

假板子严格按固件行为实现，几个关键点都是照抄固件的：
  · 连上后第一行必须是口令，否则回 `#ERR bad token` 并断开
  · 每条命令的应答以 `#OK` / `#ERR <code>` 结束
  · `travel` 没设过就拒绝 `move`（fail-closed）
  · 没有基准就拒绝 `move`
  · `json` 在轮询时顺手判定"非阻塞移动是否到位"（对应固件的 pend_poll）
"""

import json
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from PySide6.QtCore import QCoreApplication, QSettings         # noqa: E402
from PySide6.QtNetwork import QAbstractSocket                  # noqa: E402
from Src.stage_bridge import (COUNTS_PER_MM, DEG_PER_MM, MAX_ACC,     # noqa: E402
                              MM_PER_DEG, StageBridge)

TOKEN = "deadbeef"
WS_DEG = None      # 由 bridge 自动下发，假板子只记录

# ⚠ 测试**必须**用隔离的 QSettings。
# 踩过的坑：StageBridge 默认写 `DuAD/DuADSoftware` 这个真实作用域，
# 测试一跑就把假板子的 host/port/token 写进用户真实配置 ——
# 用户打开页面看到的是 127.0.0.1:44403 / deadbeef，点连接当然连不上，
# 而且失败提示很小，表现成"点了没反应"。改孤立文件后就不可能再污染。
_SETTINGS_FILE = Path(tempfile.mkdtemp(prefix="duad_stage_test_")) / "s.ini"


def make_bridge():
    return StageBridge(settings=QSettings(str(_SETTINGS_FILE), QSettings.Format.IniFormat))


# ============================================================
# 假板子
# ============================================================
class FakeBoard:
    """一台符合固件行为的 PD42S1 双轴控制器（内存版）。"""

    def __init__(self):
        self.xdeg = 0.0
        self.ydeg = 0.0
        self.datum = 0
        self.travel = 0
        self.moving = 0
        self.last = "none"
        self.travel_win = None
        self.commands = []
        self.lock = threading.Lock()
        # 收到命令但不回应答 —— 用来制造"真超时"（客户端 3s 超时）
        self.silent = False
        self.home_cfg = None
        # 会**让电机真的动**的命令。用来钉死两件事：
        #   ① 连接时不许有任何一条（真机 bug：点连接就回零）
        #   ② 回零必须走 nowait 的非阻塞路径
        self.motion_commands = []
        # 固件 json 的 "homing"：0 没回零过 / 1 正在回零 / 2 完成 / 3 失败或被打断
        self.homing = 0
        self.homing_polls = 0        # 再被轮询几次才宣布"回零完成"
        self.homing_polls_needed = 2 # 调大就能让"回零中"一直挂着（测急停用）

    def json_line(self):
        return json.dumps({
            "xdeg": round(self.xdeg, 3), "ydeg": round(self.ydeg, 3),
            "adeg": round(self.xdeg / 2, 2), "bdeg": round(self.ydeg / 2, 2),
            "v": 24.2, "en": 1, "datum": self.datum, "travel": self.travel,
            "moving": self.moving, "homing": self.homing, "last": self.last,
            "rssi": -58, "ip": "192.168.1.42",
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
                # 对应固件 cmd_json 里的 pend_poll()：轮询即到位检测
                if self.moving:
                    self.moving = 0
                    self.last = "ok"
                # 对应固件 home_poll()：回零也靠轮询收尾
                if self.homing == 1:
                    self.homing_polls += 1
                    if self.homing_polls >= self.homing_polls_needed:
                        self.homing = 2
                        self.datum = 1
                        self.moving = 0
                return [self.json_line()], True

            if h == "travel":
                if len(a) == 5:
                    self.travel_win = [float(v) for v in a[1:]]
                    self.travel = 1
                    return ["travel 已生效"], True
                self.travel = 0
                return ["travel off"], True

            if h == "move":
                if not self.datum or not self.travel:
                    return ["MOVE 拒绝: 缺基准或缺行程（未发运动指令）"], False
                self.xdeg, self.ydeg = float(a[1]), float(a[2])
                self.moving = 1
                self.last = "none"
                return ["MOVE (%.3f,%.3f)" % (self.xdeg, self.ydeg),
                        "  (非阻塞: 用 'json' 轮询到位)"], True

            if h == "zero":
                self.datum = 1
                self.xdeg = self.ydeg = 0.0
                return ["  left: ok", "  right: ok"], True

            if h == "hset":
                # 写驱动器（0x91）。真机上这台驱动器收到它就会自己开始找零点 ——
                # 所以下面记一个 motion 记录，专门用来断言"连接时不许发它"。
                if len(a) != 6:
                    return ["usage: hset <axis> <kind> <dir> <rpm> <mA>"], False
                self.home_cfg = a[1:]
                self.motion_commands.append(cmd)
                return ["  %s: 回零方式已设" % a[1]], True

            if h == "hcfg":
                # 只记在固件 RAM 里：**绝不碰驱动器，绝不动电机**
                if len(a) != 4:
                    return ["usage: hcfg <kind> <rpm> <mA>"], False
                self.home_cfg = a[1:]
                return ["  回零参数已记入固件(两轴)：方式=%s %s rpm %s mA" % tuple(a[1:])], True

            if h == "stop":
                self.moving = 0
                self.last = "aborted"
                # 固件里 stop 会顺带发 0x93 把在途回零也打断（s_home.active → home_abort_all）
                if self.homing == 1:
                    self.homing = 3
                return ["  left: ok", "  right: ok"], True

            if h == "home":
                if not self.home_cfg:
                    return ["  left: 拒绝 —— 本次会话还没配置过回零参数。"], False
                if "nowait" not in a:
                    # 阻塞版：固件会一直等到回零结束才回话 —— 网络路径绝不能用它，
                    # 否则急停被堵在 socket 里（真机踩到的就是这个）
                    time.sleep(0.3)          # 顺便验证客户端的超时设置
                    return ["home done"], True
                self.motion_commands.append(cmd)
                self.homing = 1
                self.homing_polls = 0
                self.moving = 1
                return ["  (非阻塞回零：用 'json' 的 homing 字段判结束)"], True

            if h == "habort":
                self.homing = 3
                self.moving = 0
                return ["  all: 中断回零: ok"], True

            return ["Unrecognized command: '%s'" % h], False


class FakeBoardServer:
    def __init__(self):
        self.board = FakeBoard()
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
            if first != TOKEN:
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
                    if self.board.silent:
                        continue          # 读掉但不回话 → 客户端会超时
                    lines, ok = self.board.handle(cmd)
                    for ln in lines:
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


def main():
    app = QCoreApplication(sys.argv)
    server = FakeBoardServer()
    r = Runner(app)

    print(f"假板子监听 127.0.0.1:{server.port}（口令 {TOKEN}）\n")

    print("=== 1) 前置校验（不该发起连接的输入）===")
    b_bad = make_bridge()
    r.check("空 IP 被拒", b_bad.connectDevice("", 3333, "x") is False and "IP" in b_bad.lastError)
    r.check("空口令被拒",
            b_bad.connectDevice("1.2.3.4", 3333, "") is False and "口令" in b_bad.lastError)

    print("=== 2) 错误口令 ===")
    b2 = make_bridge()
    b2.connectDevice("127.0.0.1", server.port, "wrongtoken")
    # ⚠ 不能等 "not connected"：刚发起连接时它本来就是 False，会立刻为真、
    #    于是在握手完成之前就读了 lastError。要等的是"错误已经出来"。
    r.check("被拒后拿到可读错误", r.wait_for(lambda: "口令" in b2.lastError),
            repr(b2.lastError))
    r.check("最终未连接", not b2.connected)
    b2.disconnectDevice()

    print("=== 3) 连接与口令 ===")
    b = make_bridge()
    b.connectDevice("127.0.0.1", server.port, TOKEN)
    r.check("connected 变 True", r.wait_for(lambda: b.connected), f"connected={b.connected}")
    r.check("connecting 已复位", not b.connecting)
    r.check("无历史错误", b.lastError == "", repr(b.lastError))

    print("=== 4) 连上后自动下发工作区（固件 fail-closed，不推就会拒绝一切移动）===")
    r.check("travelSet 变 True", r.wait_for(lambda: b.travelSet), f"travelSet={b.travelSet}")

    print("=== 5) 单位换算（界面唯一换算边界）===")
    r.check("1mm = 11.25 度", abs(DEG_PER_MM - 11.25) < 1e-9, f"{DEG_PER_MM}")
    r.check("1 度 = 32/360 mm", abs(MM_PER_DEG - 32.0 / 360.0) < 1e-12)
    r.check("1600 counts/mm", COUNTS_PER_MM == 1600.0)

    print("=== 6) 无基准时拒绝移动（对应固件基准闸）===")
    r.check("datum 为 False", r.wait_for(lambda: not b.datum))
    r.check("moveTo 返回 False", b.moveTo(10, 20) is False)
    r.check("给出可读原因", "基准" in b.lastError, b.lastError)
    r.check("canMove 为 False", not b.canMove())

    print("=== 7) 立基准 → 绝对移动 → 到位 ===")
    r.check("zeroAll 接受", b.zeroAll() is True)
    r.check("datum 变 True", r.wait_for(lambda: b.datum))
    r.check("位置归零", abs(b.posX) < 1e-6 and abs(b.posY) < 1e-6)
    r.check("canMove 变 True", r.wait_for(lambda: b.canMove()))
    r.check("moveTo 接受", b.moveTo(10.0, 20.0) is True)
    r.check("乐观置 moving", b.moving is True)
    r.check("到位后 moving 变 False", r.wait_for(lambda: not b.moving))
    r.check("lastMove 为 ok", b.lastMove == "ok", f"last={b.lastMove}")
    r.check("位置到达 (10,20) mm",
            abs(b.posX - 10) < 0.01 and abs(b.posY - 20) < 0.01,
            f"({b.posX:.3f},{b.posY:.3f})")

    print("=== 8) 相对点动 ===")
    b.jog(5.0, -4.0)
    r.check("点动到位 (15,16)",
            r.wait_for(lambda: abs(b.posX - 15) < 0.01 and abs(b.posY - 16) < 0.01),
            f"({b.posX:.3f},{b.posY:.3f})")

    print("=== 9) 目标按工作区夹取（界面先夹，固件闸是兜底）===")
    b.moveTo(9999.0, -9999.0)
    # ⚠ 期望值改成读 `b.wsXMax` 而不是写死 300：默认值已经从 300 改成 360
    #   （2026-09-13 用户给的实际行程）。写死数字会让"改默认值"变成一次改测试的体力活，
    #   而这条测试真正要断言的只是"夹到工作区上界"。
    r.check(f"夹到 ({b.wsXMax:.0f},0) 且不越界",
            r.wait_for(lambda: abs(b.posX - b.wsXMax) < 0.01 and abs(b.posY) < 0.01),
            f"({b.posX:.2f},{b.posY:.2f}) X{b.wsXMin}..{b.wsXMax} Y{b.wsYMin}..{b.wsYMax}")
    r.check("默认工作区是 360×360（2026-09-13 用户实测值）",
            b.wsXMax == 360.0 and b.wsYMax == 360.0, f"X{b.wsXMax} Y{b.wsYMax}")

    print("=== 10) 工作区再设置（含角点反填）===")
    b.setWorkspace(200.0, 50.0, 20.0, 10.0)      # 故意反着填
    r.check("反填被归一化",
            b.wsXMin == 20.0 and b.wsXMax == 200.0 and b.wsYMin == 10.0 and b.wsYMax == 50.0,
            f"X{b.wsXMin}..{b.wsXMax} Y{b.wsYMin}..{b.wsYMax}")
    b.setWorkspace(0.0, 0.0, 300.0, 300.0)

    print("=== 11) 速度参数（上限必须与固件一致）===")
    b.setSpeed(0, 999)                            # 越界值应被夹住
    r.check("rpm 下限夹到 1", b.rpm == 1, f"rpm={b.rpm}")
    # ⚠ 固件组帧时 `if (acc > 200) return -1;` 直接拒帧，所以这里必须是 200。
    #   曾经写成 255（比固件宽 55），表现是"加减速拖到 201~255 没反应"。
    r.check("acc 上限夹到固件的 200（不是 255）", b.acc == MAX_ACC == 200, f"acc={b.acc}")
    b.setSpeed(300, 100)

    print("=== 11b) 移动指令里确实带着 rpm/acc（「调了速度没反应」的判据）===")
    b.setSpeed(137, 44)
    r.wait_for(lambda: not b.moving, 5)
    server.board.commands.clear()
    b.moveTo(5.0, 5.0)
    got = r.wait_for(lambda: any(c.startswith("move") for c in server.board.commands), 3)
    mv = [c for c in server.board.commands if c.startswith("move")]
    r.check("板子收到 move", got, str(mv[:1]))
    if mv:
        parts = mv[0].split()
        r.check("move 末尾两个数就是 rpm 与 acc",
                len(parts) == 5 and parts[3] == "137" and parts[4] == "44", mv[0])
    b.setSpeed(300, 100)

    print("=== 12) 预设位置 ===")
    b.moveTo(42.0, 24.0)
    r.wait_for(lambda: abs(b.posX - 42) < 0.01)
    r.check("savePreset 成功", b.savePreset("工位A") is True)
    r.check("presets 有一条", len(b.presets) == 1, str(b.presets))
    r.check("预设值正确",
            abs(b.presets[0]["x"] - 42) < 0.01 and abs(b.presets[0]["y"] - 24) < 0.01,
            str(b.presets[0]))
    r.check("空名字被拒", b.savePreset("   ") is False)
    b.moveTo(0.0, 0.0)
    r.wait_for(lambda: abs(b.posX) < 0.01)
    b.gotoPreset(0)
    r.check("gotoPreset 回到预设点",
            r.wait_for(lambda: abs(b.posX - 42) < 0.01 and abs(b.posY - 24) < 0.01),
            f"({b.posX:.2f},{b.posY:.2f})")
    r.check("同名覆盖不新增", b.savePreset("工位A") is True and len(b.presets) == 1)
    r.check("越界索引被拒", b.gotoPreset(99) is False)
    r.check("deletePreset 成功", b.deletePreset(0) is True and len(b.presets) == 0)

    print("=== 13) 急停（插队首，不被长移动堵住）===")
    b.moveTo(100.0, 100.0)
    r.check("stopNow 接受", b.stopNow() is True)
    r.check("moving 立刻清掉", b.moving is False)
    r.pump(0.8)
    r.check("lastMove 变 aborted", b.lastMove == "aborted", f"last={b.lastMove}")

    print("=== 14a) 传输类错误必须能自动消失（不能一直挂着）===")
    # 踩过的坑：原来只有一种错误、且只在"重新连接"时清空 ——
    # 一次超时之后诊断面板会一直显示"超时（板子没有应答）"，
    # 哪怕板子早就好端端在应答了。
    server.board.silent = True                 # 让它不吭声
    b.pollNow()                                # 这条会超时
    r.check("超时后给出提示", r.wait_for(lambda: "超时" in b.lastError, 8),
            repr(b.lastError))
    server.board.silent = False                # 恢复正常
    r.check("链路恢复后提示自动消失",
            r.wait_for(lambda: b.lastError == "", 8), repr(b.lastError))

    print("=== 14b) 粘性错误（要用户处理）不能被自动清掉 ===")
    b.moveTo(99999.0, 0.0)                     # 会被工作区夹取，正常成功
    r.wait_for(lambda: not b.moving, 5)
    b2_sticky = make_bridge()
    # 空 IP 属于"要用户填"的粘性错误：连一次失败后不该被别的成功清掉
    r.check("参数类错误是粘性的（不自动消失）",
            b2_sticky.connectDevice("", 3333, "x") is False and b2_sticky.lastError != "")
    b2_sticky.disconnectDevice()

    print("=== 14c) diagText 必须是会刷新的属性（不是创建时求值一次）===")
    # 踩过的坑：diagText 原来是 @Slot，QML 里 `text: StageBridge.diagText()`
    # 这种"绑定里调函数"没有依赖追踪 → 值冻结在创建那一刻 →
    # 诊断面板一直显示"板子状态 未连接"。
    r.check("连上后 diagText 不再是「未连接」",
            b.connected and b.diagText != "未连接", repr(b.diagText))
    r.check("diagText 含坐标与电压", "mm" in b.diagText and "V" in b.diagText, b.diagText)
    r.check("datumHint 是可读的当前状态", isinstance(b.datumHint, str) and b.datumHint == "",
            repr(b.datumHint))

    print("=== 14d) 回零有闸：不先登记参数就该被界面挡住 ===")
    b._home_cfg_sent = False
    b._set_error("")
    r.check("未配参数时 homeAll 被拒", b.homeAll() is False)
    # ⚠ 这两条断言 2026-09-13 改过：原来要求提示里出现「应用回零参数」（按钮名）。
    #   大扫除把那个按钮删了，再要求出现它的名字，就变成**要求提示语指向一个不存在的控件**
    #   —— 正是本项目明令禁止的那类话。改成：说清楚是哪道闸 + 给可执行的下一步。
    r.check("给出的是可操作的提示（说清闸 + 给可执行的下一步）",
            "回零参数" in b.lastError and "hcfg" in b.lastError, b.lastError)
    r.check("⚠ 提示语不许指向已删除的控件", "应用回零参数" not in b.lastError, b.lastError)

    print("=== 14e) 登记回零参数必须用 hcfg —— 它不写驱动器、不动电机 ===")
    server.board.commands.clear()
    server.board.motion_commands.clear()
    r.check("setHomeConfig 接受", b.setHomeConfig(0, 60, 800) is True)
    r.check("板子收到 hcfg（一条，两轴一起）",
            r.wait_for(lambda: "hcfg 0 60 800" in server.board.commands, 5),
            str(server.board.commands[:3]))
    # ⚠ 这条是本次真机 bug 的核心：hset 写 0x91，而这台驱动器收到 0x91
    #   就会自己开始找零点 —— 所以"只是登记参数"绝不能用 hset。
    r.check("登记参数不许发 hset（会写驱动器 → 台面自己会动）",
            not [c for c in server.board.commands if c.startswith("hset")],
            str([c for c in server.board.commands if c.startswith("hset")]))
    r.check("homeCfgSent 变 True", r.wait_for(lambda: b.homeCfgSent, 3))
    r.check("限位电流 0 被拒（否则永远检测不到死点）",
            b.setHomeConfig(0, 0, 0) is False
            and "限位电流" in b.lastError, b.lastError)

    print("=== 14e2) 限位电流太小必须挡住 —— 真机 200mA 两轴都报“未找到零点” ===")
    # 2026-09-13 真机：用户把限位电流设成 200mA，两个驱动器回零都回报
    # 「失败(未找到零点)」；而这个值会在每次连接时被自动重下发，静默地一直用下去。
    r.check("200mA 被拒且给出推荐范围",
            b.setHomeConfig(0, 60, 200) is False
            and "太小" in b.lastError and "800" in b.lastError, b.lastError)
    r.check("300mA 是允许的下限（手册示例值就是 300；真机也是 300 才通的）",
            b.setHomeConfig(0, 60, 300) is True)
    r.check("800mA 正常", b.setHomeConfig(0, 60, 800) is True)

    print("=== 14e3) 保存的坏值要在加载时自愈，不能每次连接默默重下发 ===")
    st = QSettings(str(_SETTINGS_FILE), QSettings.Format.IniFormat)
    # 同时踩两个旧默认值：ma=200（回零必失败）+ rpm=60（满行程 9.4s，顶到驱动器 10s 超时）
    st.setValue("stage/home_cfg",
                json.dumps({"kind": 0, "rev": 0, "rpm": 60, "ma": 200}))
    st.sync()
    b2 = make_bridge()                       # 新桥读同一份 settings
    r.check("限位电流读回来时被修正", b2.homeMa == 800, str(b2.homeMa))
    r.check("旧的 60rpm 也迁到 300（不然会撞驱动器回零超时）",
            b2.homeRpm == 300, str(b2.homeRpm))
    r.check("修正后写回了 settings（下次不会再读到坏值）",
            "200" not in str(st.value("stage/home_cfg", ""))
            and "60," not in str(st.value("stage/home_cfg", "")),
            str(st.value("stage/home_cfg", "")))
    b2.deleteLater()
    # 清理：后面的用例不该受这份被改过的配置影响
    st.setValue("stage/home_cfg",
                json.dumps({"kind": 0, "rev": 0, "rpm": 300, "ma": 300}))
    st.sync()

    print("=== 14g) 闸提示在条件满足后必须自己消失 ===")
    # 截图里真实出现过自相矛盾的场面：状态条上「已立基准/行程已设」全绿，
    # 下面红框却还挂着"还没有基准：先「设为原点」"。用户看到会不信任界面。
    b._datum = False                     # 白盒：假装遥测还没报回基准
    b.moveTo(1.0, 1.0)
    r.check("无基准时被拒并给出闸提示", "基准" in b.lastError, repr(b.lastError))
    b.zeroAll()                          # 立基准
    r.check("立基准后闸提示自动消失",
            r.wait_for(lambda: b.lastError == "", 6), repr(b.lastError))

    print("=== 14f) 自动回零：必须用非阻塞 nowait（否则急停被堵在 socket 里）===")
    server.board.commands.clear()
    server.board.homing = 0
    server.board.homing_polls_needed = 2
    b._homing = 0
    t0 = time.time()
    r.check("homeAll 接受", b.homeAll() is True)
    r.check("立刻返回（nowait 的全部意义就在这里）", time.time() - t0 < 0.2,
            "%.3fs" % (time.time() - t0))
    r.check("板子收到 home corner -1 -1 nowait（两趟：先 X 后 Y）",
            r.wait_for(lambda: "home corner -1 -1 nowait" in server.board.commands, 5),
            str(server.board.commands[:2]))
    r.check("进入回零中态", r.wait_for(lambda: b.homing == 1, 3), str(b.homing))
    r.check("回零中不许重复触发（免得两次回零叠在一起）", b.homeAll() is False)
    r.check("轮询到结束 → homing=2（靠 json 收尾，不需要阻塞）",
            r.wait_for(lambda: b.homing == 2, 8), str(b.homing))
    r.check("回零完成后有可读提示", "完成" in b.homingText, b.homingText)
    r.check("回零后仍连着", b.connected)

    print("=== 14f2) 回零期间「停止」必须能打断 —— 真机踩到的正是这条 ===")
    # 真机现象：台面顶在边缘不停，用户在界面上按停止**没有任何反应**，
    # 因为阻塞版 home 把 TCP 任务占了几十秒，stop 只能排在它后面。
    server.board.homing = 0
    server.board.homing_polls_needed = 9999   # 让"回零中"一直挂着，模拟顶住不停
    b._homing = 0
    server.board.commands.clear()
    r.check("触发回零", b.homeAll() is True)
    r.check("处于回零中", r.wait_for(lambda: b.homing == 1, 3), str(b.homing))
    server.board.commands.clear()
    r.check("stopNow 接受", b.stopNow() is True)
    r.check("板子真的收到了 stop all（没有被回零堵住）",
            r.wait_for(lambda: "stop all" in server.board.commands, 5),
            str(server.board.commands[:3]))
    r.pump(0.6)
    r.check("停止后界面不再显示回零中", b.homing == 3, str(b.homing))

    print("=== 14f3) 专用中断入口 habort ===")
    server.board.homing = 0
    b._homing = 1                              # 假装在回零
    server.board.commands.clear()
    r.check("homeAbort 接受", b.homeAbort() is True)
    r.check("板子收到 habort all（front=True，插队首）",
            r.wait_for(lambda: "habort all" in server.board.commands, 5),
            str(server.board.commands[:3]))
    server.board.homing_polls_needed = 2
    server.board.homing = 0
    r.pump(0.4)
    r.check("回零后仍连着", b.connected)

    print("=== 14f4) 连接时绝不许有「会让电机动」的命令 —— 真机 bug 的回归守卫 ===")
    # 用户原话："这个自动回零会在上位机点击连接的时候回零，其他情况不会回零。"
    # 根因：连接时自动重下发 `hset`（0x91 写驱动器），而这台 PD42S1 收到 0x91
    # 就会自己开始找零点。改成 `hcfg`（只记 RAM）之后，连接序列里不该再有
    # hset / home / move / zero 这类会动的命令。
    st = QSettings(str(_SETTINGS_FILE), QSettings.Format.IniFormat)
    st.setValue("stage/home_cfg",
                json.dumps({"kind": 0, "rev": 0, "rpm": 60, "ma": 300}))
    st.sync()
    b3 = make_bridge()
    server.board.commands.clear()
    b3.connectDevice("127.0.0.1", server.port, TOKEN)
    r.check("b3 连上", r.wait_for(lambda: b3.connected, 5))
    r.pump(1.0)                                # 让连接后的自动下发全部走完
    after = list(server.board.commands)
    bad = [c for c in after
           if c.split()[0] in ("hset", "home", "move", "g0", "turn", "abs", "zero")
           or c.startswith("home ")]
    r.check("连接后的命令里没有一条会让电机动（真机 bug 的回归守卫）",
            not bad, str(bad))
    r.check("回零参数改走 hcfg（登记到 RAM，不写驱动器）",
            any(c.startswith("hcfg") for c in after), str(after))
    b3.disconnectDevice()
    b3.deleteLater()

    print("=== 14f5) 单趟回零（调试用）也要带 nowait 且方向写对 ===")
    server.board.commands.clear()
    b._homing = 0
    r.check("homePass('y', -1) 接受", b.homePass("y", -1) is True)
    r.check("板子收到 home y -1 nowait",
            r.wait_for(lambda: "home y -1 nowait" in server.board.commands, 5),
            str(server.board.commands[:3]))
    b._homing = 0
    r.check("非法方向被拒", b.homePass("y", 0) is False)
    b._homing = 0
    server.board.homing = 0
    server.board.homing_polls_needed = 2

    print("=== 15) 断开前的收尾检查 ===")
    # diagText 现在是 Property（不是函数），14c 已经断言过它的内容与刷新性
    r.check("diagText 仍可读", "mm" in b.diagText, b.diagText)

    # ============================================================
    # 16) 退出回零点 + 「上次停在哪儿」核对（2026-09-13 用户提的方案）
    # ============================================================
    # 这一组要一个**刚立过基准、台面在零点、停着**的状态。
    # ⚠ `_log` 只 print + emit，不存历史 —— 所以这里自己挂一个收集器。
    print("=== 16) 退出回零点 / 位置核对 ===")
    server.board.datum = 1
    server.board.moving = 0
    server.board.xdeg = server.board.ydeg = 0.0
    b._datum = True
    b._travel_set = True
    b._moving = False
    b._queue = []
    b._in_flight = None
    b._apply_json(server.board.json_line())

    r.check("parkOnExit 默认开", b.parkOnExit is True, f"parkOnExit={b.parkOnExit}")
    b.setParkOnExit(False)
    b._settings.sync()
    r.check("setParkOnExit 能关掉", b.parkOnExit is False)
    r.check("关掉后落盘", make_bridge().parkOnExit is False)
    b.setParkOnExit(True)
    b._settings.sync()
    r.check("再开回来并落盘", make_bridge().parkOnExit is True)

    # ① 正常退出：应该发一条 move 回零点，并记下「停在哪儿」
    server.board.commands.clear()
    b.parkAndDisconnect()
    moved = [c for c in server.board.commands if c.startswith("move ")]
    r.check("退出时发了回零点的 move", len(moved) == 1, str(server.board.commands[:3]))
    # 台面本来就在 (0,0)，固件角度域也是 0 —— 这条同时钉住「单位只换算一次」
    r.check("回零点的目标就是 0,0（角度域）",
            bool(moved) and moved[0].split()[1:3] == ["0.000", "0.000"], str(moved))
    r.check("已断开", r.wait_for(lambda: not b.connected))
    r.check("记下了停在哪儿", b._last_park is not None, str(b._last_park))

    print("--- 16b) 再连上：位置没变 → 不该报警 ---")
    b2 = make_bridge()
    logs2 = []
    b2.logMessage.connect(logs2.append)
    b2.connectDevice("127.0.0.1", server.port, TOKEN)
    r.check("重连成功", r.wait_for(lambda: b2.connected), f"connected={b2.connected}")
    # 关键：核对必须**等第一次遥测**才做
    r.check("核对已执行（不再 pending）",
            r.wait_for(lambda: b2._park_check_pending is False),
            f"pending={b2._park_check_pending}")
    r.check("位置一致时一个字都不说",
            not any("和上次离开时不一样" in ln for ln in logs2), str(logs2[-3:]))
    r.check("读到了上次停在哪", b2._last_park is not None, str(b2._last_park))
    b2.disconnectDevice()
    r.wait_for(lambda: not b2.connected)

    print("--- 16c) 板子重启过（datum=0）→ 必须提示重立基准 ---")
    server.board.datum = 0
    server.board.xdeg = server.board.ydeg = 0.0   # ⚠ 位置读出来**还是 0**！
    b3 = make_bridge()
    logs3 = []
    b3.logMessage.connect(logs3.append)
    b3.connectDevice("127.0.0.1", server.port, TOKEN)
    r.check("重连成功", r.wait_for(lambda: b3.connected))
    r.check("提示了「板子重启或 24V 掉电」+ 要重立基准",
            r.wait_for(lambda: any("基准闸是空的" in ln for ln in logs3)), str(logs3[-4:]))
    # ★ 这一组的核心：**位置读到 0 完全不能证明"在零点"** ——
    #   驱动器上电也是从 0 开始数的。判据必须靠固件的 datum 闸。
    r.check("datum=0 时即使位置吻合也不算已有基准", b3.datum is False)
    b3.disconnectDevice()
    r.wait_for(lambda: not b3.connected)

    print("--- 16d) datum=1 但位置对不上 → 报警 ---")
    server.board.datum = 1
    server.board.xdeg = 84.0        # json 的 xdeg 是**台面 X 的角度**：84° ÷ 11.25 = 7.47mm
    server.board.ydeg = 0.0
    b4 = make_bridge()
    logs4 = []
    b4.logMessage.connect(logs4.append)
    b4.connectDevice("127.0.0.1", server.port, TOKEN)
    r.check("重连成功", r.wait_for(lambda: b4.connected))
    r.check("位置对不上时报警",
            r.wait_for(lambda: any("和上次离开时不一样" in ln for ln in logs4)),
            str(logs4[-4:]))
    b4.disconnectDevice()
    r.wait_for(lambda: not b4.connected)

    print("--- 16e) 没基准时退出：不发 move（免得盲开车）---")
    server.board.datum = 0
    server.board.commands.clear()
    b5 = make_bridge()
    b5.connectDevice("127.0.0.1", server.port, TOKEN)
    r.check("重连成功", r.wait_for(lambda: b5.connected))
    r.check("没基准", b5.datum is False)
    b5.parkAndDisconnect()
    r.check("没基准就不发 move",
            not any(c.startswith("move ") for c in server.board.commands),
            str(server.board.commands[:3]))
    r.check("已断开", r.wait_for(lambda: not b5.connected))
    server.board.datum = 1
    server.board.xdeg = server.board.ydeg = 0.0

    print("=== 17) 断开 ===")
    b.disconnectDevice()
    r.check("socket 回到未连接态",
            r.wait_for(lambda: b._sock.state() == QAbstractSocket.SocketState.UnconnectedState),
            f"state={b._sock.state()}")
    r.check("connected 为 False", not b.connected)
    r.check("断开后拒绝下发命令", b.moveTo(1, 1) is False)

    server.close()
    print()
    if r.fails:
        print(f"FAILED {len(r.fails)}: {r.fails}")
        return 1
    print("StageBridge 全部断言通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
