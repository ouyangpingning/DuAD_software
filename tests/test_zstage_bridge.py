#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ZStageBridge 回归测试 —— 用进程内的假板子验证 Z 轴平台桥的逻辑。

为什么要有它：真板子不在手边时，协议分帧、命令队列、非阻塞语义、四道闸的界面配合、
协议显示框、急停插队这些逻辑照样能验。**真机上没验过的东西，至少先在这里验一遍。**

运行（不需要真板子、不需要相机）：
    source DuAD_SoftwareContent/pyqml/bin/activate
    QT_QPA_PLATFORM=offscreen python3 tests/test_zstage_bridge.py

假板子严格按 **Zstage 固件**的行为实现（`研究生毕业设计/esp/Zstage`）：
  · 连上后第一行必须是口令，否则回 `#ERR bad token` 并断开
  · 每条命令的应答以 `#OK` / `#ERR <code>` 结束
  · `json` 轮询顺手判定"非阻塞移动是否到位"（对应固件 pend_poll）
  · 没有基准 / 没设软限位 → 运动命令**一条都不发**，直接 `#ERR`（fail-closed）
  · `zup` / `zdown` / `zmove` / `zhome … nowait` 都是**非阻塞**的
  · `zsign`（不带参数）是只读的，会打印 `sa=… sb=…`
  · `trace on` 之后每一帧收发都会多打一行 `@T/@R <axis> <hex…>`
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
from Src.zstage_bridge import (UI_MAX_RPM, UI_MIN_ACC, ZStageBridge)   # noqa: E402

TOKEN = "deadbeef"

# ⚠ 测试**必须**用隔离的 QSettings。默认作用域 `DuAD/DuADSoftware` 是真实程序的，
# 一跑就把假板子的 host/port/token 写进用户真实配置 → 用户打开页面看到的是假板子的值，
# 点连接当然连不上，失败提示又小，表现成"点了没反应"。（stage_bridge 踩过一次。）
_SETTINGS_FILE = Path(tempfile.mkdtemp(prefix="duad_zstage_test_")) / "s.ini"


def make_bridge():
    return ZStageBridge(settings=QSettings(str(_SETTINGS_FILE), QSettings.Format.IniFormat))


# 会**让电机真的动**的命令头。用来钉死两件事：
#   ① 连接期不许有任何一条（真机 bug：点连接就回零）
#   ② 运动必须走非阻塞变体
MOTION_HEADS = ("zup", "zdown", "zmove", "zrel", "zabs", "zhome", "ztilt")


# ============================================================
# 假板子
# ============================================================
class FakeBoard:
    """一台符合 Zstage 固件行为的 Z 轴控制器（内存版）。"""

    def __init__(self):
        self.z = 0.0
        self.skew = 0.0
        self.a = 0
        self.b = 0
        self.datum = 0
        self.lim = 1                 # 固件出厂就预置了 0~250mm 且 lim_set=1
        self.zmin, self.zmax = 0.0, 250.0
        self.moving = 0
        self.homing = 0
        self.last = "none"
        self.fault = "none"
        self.auto = 0
        self.en = 1                      # 两轴是否使能（json 的 en）
        # 无限位回零的参数（固件 NVS 出厂默认；`zset home` 改它，json 报它）
        self.home_rpm = 400
        self.home_ma = 100        # 出厂默认（2026-09-29 现场实测：100mA 可用）
        self.home_tmo = 12000
        self.home_dir = "down"           # 板子里存的方向（只给 zautohome 用）
        self.home_dir_cmd = None         # 最后一次 `zhome <dir>` 用的方向
        self.rpm = 300
        self.acc = 100
        self.tgt = 0.0
        self.sign = (-1, -1)          # 现场实测：同向 + 整体极性为负
        self.commands = []
        self.motion_commands = []
        self.trace = False
        self.lock = threading.Lock()
        # 某条命令故意变慢（测"急停插队"用）
        self.slow_head = None
        self.slow_sec = 0.0
        # 让 json 一次多回几行（测协议框封顶用；0 = 正常一行）
        self.flood_proto = 0
        # 收到这个命令头就**不回话**（制造真超时，用来覆盖 _on_cmd_timeout）
        self.silent_head = None

    def json_line(self):
        return json.dumps({
            "z": round(self.z, 2), "skew": round(self.skew, 2),
            "a": self.a, "b": self.b,
            "v": 24.2, "en": self.en, "datum": self.datum, "lim": self.lim,
            "zmin": round(self.zmin, 2), "zmax": round(self.zmax, 2),
            "moving": self.moving, "homing": self.homing, "last": self.last,
            "fault": self.fault, "tgt": round(self.tgt, 2), "auto": self.auto,
            # 回零参数（固件 json 的 hma/hrpm/htmo）：`zset home` 改它，界面回读它
            "hma": self.home_ma, "hrpm": self.home_rpm, "htmo": self.home_tmo,
            "umrev": 8000, "rssi": -58, "ip": "192.168.1.43",
        })

    def _motion_denied(self, head=""):
        """固件那两道闸（fail-closed）。返回拒绝原因行，或 None 表示放行。

        ⚠ **点动是例外**：`zup`/`zdown` 无基准也允许（单次 ≤20mm，固件只打一行 ⚠），
        因为"把平台挪到参考位置"就是立基准的前置步骤。假板子第一版把这条也拒了，
        等于把**错的语义固化进测试**（评审指出）—— 于是"首次立基准只能用手推"
        这个界面缺陷永远测不出来。
        """
        if head in ("zup", "zdown"):
            return None                      # 点动不受基准闸限制（见上）
        if not self.datum:
            return "  ZMOVE 拒绝: 还没有基准 —— 先把平台推到靠块/机械死点，再 'zzero'。"
        if not self.lim:
            return "  ZMOVE 拒绝: 软限位还没设 —— 先 'zlim <最低mm> <最高mm>'。"
        return None

    def handle(self, cmd):
        """返回 (输出行列表, 是否成功)。"""
        with self.lock:
            self.commands.append(cmd)
            a = cmd.split()
            if not a:
                return [], True
            h = a[0]
            if h in MOTION_HEADS:
                self.motion_commands.append(cmd)

            if self.slow_head == h and self.slow_sec:
                time.sleep(self.slow_sec)

            if h == "json":
                if self.flood_proto:
                    return [self.json_line() for _ in range(self.flood_proto)], True
                if self.moving:
                    self.moving = 0
                    self.last = "ok"
                if self.homing:
                    self.homing = 2
                    self.datum = 1
                return [self.json_line()], True

            if h == "zsign":
                if len(a) < 2:
                    return ["  当前方向符号 sa=%+d sb=%+d —— 两轴同向转（同向安装）"
                            % self.sign,
                            "  出厂默认 sa=%+d sb=%+d（本机现场实测为**同向**）" % self.sign], True
                self.sign = (int(a[1]), int(a[2]))
                return ["  方向符号 = sa%+d sb%+d（同向安装）" % self.sign], True

            if h in ("zup", "zdown", "zmove", "zrel", "zabs", "ztilt"):
                if h in ("zup", "zdown") and float(a[1]) > 20.0:
                    # 固件：zup/zdown 单次点动上限 20mm（要更长用 zrel）
                    return ["  %s: 单次点动上限 20mm（要更长用 'zrel'）" % h], False
                why = self._motion_denied(h)
                if why:
                    return [why], False
                if h == "zmove":
                    self.z = float(a[1])
                    self.tgt = self.z
                elif h == "zup":
                    self.z += float(a[1])
                elif h == "zdown":
                    self.z -= float(a[1])
                self.moving = 1
                self.last = "none"
                return ["  Z %s …: ok" % h, "  (非阻塞: 用 'json' 轮询到位)"], True

            if h == "zzero":
                self.datum = 1
                self.z = self.skew = 0.0
                self.a = self.b = 0
                return ["  零点已设（基准已立）"], True

            if h == "zhome":
                if "nowait" not in a:
                    # 阻塞版：固件会一直等到回零结束 —— 用来证明"界面绝不能走这条"
                    time.sleep(0.5)
                if a[1:2] and a[1] in ("up", "down"):
                    self.home_dir_cmd = a[1]
                else:
                    self.home_dir_cmd = "down"
                if self.home_dir_cmd == "up":
                    self.homing = 1
                    return ["  向上回零 …（可选路径）"], True
                self.homing = 1
                return ["  向下回零 …（可选路径）"], True

            if h in ("en", "dis"):
                self.en = 1 if h == "en" else 0
                return ["  %s all: ok" % h], True

            if h == "stop":
                self.moving = 0
                self.homing = 3
                self.last = "aborted"
                return ["  stop: 两轴已刹车"], True

            if h == "zlim":
                if len(a) == 1:
                    return ["  软限位 = %.2f ~ %.2f mm" % (self.zmin, self.zmax)], True
                lo, hi = float(a[1]), float(a[2])
                if hi <= lo or hi - lo > 1000.0:
                    return ["  zlim: 参数不合法"], False
                self.zmin, self.zmax, self.lim = lo, hi, 1
                return ["  软限位 = %.2f ~ %.2f mm（存 NVS）" % (lo, hi)], True

            if h == "zset":
                if a[1] == "rpm":
                    self.rpm = int(a[2])
                elif a[1] == "acc":
                    self.acc = int(a[2])
                elif a[1] == "home":
                    # 固件 `zset home <rpm> <mA> [timeout_ms]`：rpm/mA 必须在场
                    # （`argc > 3`），少一个就是 #ERR —— 照抄这条，桥少发字段能测出来
                    if len(a) < 4:
                        return ["  home 参数范围: rpm 1~6000, mA 1~3000"], False
                    self.home_rpm, self.home_ma = int(a[2]), int(a[3])
                    if len(a) > 4:
                        self.home_tmo = int(a[4])
                    return ["  回零参数 = %drpm / %umA / %ums"
                            % (self.home_rpm, self.home_ma, self.home_tmo)], True
                return ["  ok"], True

            if h == "zautohome":
                self.auto = 1 if a[1] == "on" else 0
                # 方向是可选参数；带了才改板子里的方向（固件 cmd_zautohome）
                if len(a) > 2:
                    self.home_dir = a[2]
                return ["  上电自动回零 = %s，方向 = %s"
                        % ("开" if self.auto else "关", self.home_dir)], True

            if h == "trace":
                self.trace = (len(a) > 1 and a[1] == "on")
                return ["  驱动器通信帧镜像：%s" % ("**开**" if self.trace else "关")], True

            if h == "zcfg":
                return ["  丝杠导程 8.000 mm/圈 → 6400 counts/mm",
                        "  方向符号 sa=-1 sb=-1（两轴同向转）"], True

            if h == "pos":
                return ["  left : %+9d counts" % self.a, "  right: %+9d counts" % self.b], True

            return ["  (未知命令 %s)" % h], True

    def trace_lines(self, cmd):
        """`trace on` 时固件多打的驱动器帧镜像行（@T 发 / @R 收）。"""
        if not self.trace:
            return []
        # 一帧真实报文的样子：C5 <addr> <code> <data…> <chk> 5C
        return ["@TX left  C5 01 2A 00 00 00 00 D0 5C",
                "@RX left  C5 01 2A 00 00 0C 80 51 5C"]


class FakeBoardServer:
    def __init__(self):
        self.board = FakeBoard()
        self._stop = False
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(4)
        self.port = self._sock.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
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
                    if self.board.silent_head and cmd.split() \
                            and cmd.split()[0] == self.board.silent_head:
                        continue          # 读掉但不回话 → 客户端必然超时
                    lines, ok = self.board.handle(cmd)
                    # 固件里 trace 的镜像行是**事务中间**插进去的：
                    # 它们出现在命令输出之前/之间，而且不在 #OK 之内。
                    for ln in self.board.trace_lines(cmd):
                        self._w(f, ln)
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
    board = server.board
    r = Runner(app)

    print(f"假板子监听 127.0.0.1:{server.port}（口令 {TOKEN}）\n")

    print("=== 1) 前置校验（不该发起连接的输入）===")
    b_bad = make_bridge()
    r.check("空 IP 被拒", b_bad.connectDevice("", 3333, "x") is False and "IP" in b_bad.lastError)
    r.check("空口令被拒",
            b_bad.connectDevice("1.2.3.4", 3333, "") is False and "口令" in b_bad.lastError)
    r.check("非法端口被拒",
            b_bad.connectDevice("1.2.3.4", 99999, "x") is False and "端口" in b_bad.lastError)

    print("=== 2) 错误口令 ===")
    b2 = make_bridge()
    b2.connectDevice("127.0.0.1", server.port, "wrongtoken")
    # ⚠ 不能等 "not connected"：刚发起连接时它本来就是 False，会立刻为真、
    #    于是在握手完成之前就读了 lastError。要等的是"错误已经出来"。
    r.check("被拒后拿到可读错误", r.wait_for(lambda: "口令" in b2.lastError), repr(b2.lastError))
    r.check("最终未连接", not b2.connected)
    b2.disconnectDevice()

    print("=== 3) 连接与口令 ===")
    b = make_bridge()
    b.connectDevice("127.0.0.1", server.port, TOKEN)
    r.check("connected 变 True", r.wait_for(lambda: b.connected), f"connected={b.connected}")
    r.check("connecting 已复位", not b.connecting)
    r.check("无历史错误", b.lastError == "", repr(b.lastError))

    print("=== 4) 连接序列必须是只读的（不许有任何会动电机的命令）===")
    # 对应 AGENTS.md 第 12 条：真机上"点一下连接，台面自己回零了"就是连接期发了 hset。
    # Z 轴这边 `zhome` 更狠：一发就去找零点。所以连接期只许发 json / zsign。
    r.pump(0.4)
    r.check("连接期没有任何运动命令", board.motion_commands == [], str(board.motion_commands))
    conn_cmds = [c for c in board.commands if not c.startswith("json")]
    r.check("连接期只发了只读命令（zsign）",
            all(c.split()[0] in ("zsign", "quit", "exit") for c in conn_cmds), str(conn_cmds))

    print("=== 5) 方向符号（zsign 读回解析）===")
    r.check("signText 解析出 sa/sb", r.wait_for(lambda: b.signText != ""), repr(b.signText))
    r.check("符号是现场实测的 -1/-1", b.signText == "sa=-1  sb=-1", repr(b.signText))

    print("=== 6) 遥测解析（json 一条命令拿全）===")
    board.z, board.skew, board.a, board.b = 123.45, 0.07, -790080, -790080
    board.fault = "skew"
    r.check("z 更新", r.wait_for(lambda: abs(b.z - 123.45) < 0.01), f"z={b.z}")
    r.check("skew 更新", abs(b.skew - 0.07) < 0.001, f"skew={b.skew}")
    r.check("原始 counts 也带回来", b.jointA == -790080 and b.jointB == -790080)
    r.check("电压/使能", abs(b.voltage - 24.2) < 0.01 and b.enabled)
    r.check("软限位（固件实际值）", abs(b.firmwareLo) < 1e-6 and abs(b.firmwareHi - 250) < 1e-6)
    r.check("fault 有界面文字", b.fault == "skew" and "高差" in b.faultText, repr(b.faultText))
    board.fault = "none"

    print("=== 7) 两道闸：没基准就一条运动指令都不发（fail-closed）===")
    board.datum = 0
    r.check("datum 变 False", r.wait_for(lambda: not b.datum))
    n_before = len(board.motion_commands)
    # ⚠ 点动**不受基准闸限制**：固件故意允许无基准相对点动（单次 ≤20mm），
    #   因为"把平台挪到参考位置"正是立基准的前置步骤。绝对定位则必须被拒。
    r.check("moveTo 返回 False（绝对定位要基准）", b.moveTo(50.0) is False)
    r.check("canJog 仍为 True（点动没被基准闸锁死）", b.canJog)
    r.check("给出可读原因（提到基准）", "基准" in b.lastError, b.lastError)
    r.check("canMove 为 False", not b.canMove)
    r.check("datumHint 非空（按钮点不动要说原因）", len(b.datumHint) > 0, b.datumHint[:40])
    r.pump(0.3)
    r.check("绝对定位确实一条都没发出去", len(board.motion_commands) == n_before,
            str(board.motion_commands[n_before:]))
    # 反过来：点动要能发出去（否则首次立基准只能用手推平台）
    r.check("点动在无基准时也能发", b.jogUp(0) is True)
    r.check("命令是 zup（无基准也允许）",
            r.wait_for(lambda: any(c.startswith("zup") for c in board.commands)),
            str(board.commands[-3:]))
    # ⚠ 点动是"乐观置 moving"，必须等轮询把它收回来再进下一组 ——
    #   否则下一步的 zzero 会被"还在运动"拒掉（这条断言本身也验证了乐观置位的闭环）。
    r.check("点动后 moving 由 json 轮询收回", r.wait_for(lambda: not b.moving))

    print("=== 8) 立基准 → 闸提示自动消失 → 能动了 ===")
    r.check("setZero 接受", b.setZero() is True)
    r.check("datum 变 True", r.wait_for(lambda: b.datum))
    r.check("闸提示被自动清掉（不能自相矛盾）", r.wait_for(lambda: b.lastError == ""),
            repr(b.lastError))
    r.check("canMove 变 True", r.wait_for(lambda: b.canMove))

    print("=== 9) 点动/定位命令的**原文**（非阻塞 + 带 rpm/acc）===")
    r.check("jogUp(0) 用当前步长", b.jogUp(0) is True)
    # ⚠ 只有两个参数：固件的 cmd_zup/cmd_zdown **不读第三个参数**（acc 走板子自己的）。
    #   发 `... 300 100` 会让日志以为 acc 生效了 —— 那是假象（评审读固件源码发现）。
    r.check("命令是 zup 1.000 300（两个参数，acc 由板子自己定）",
            r.wait_for(lambda: "zup 1.000 300" in board.commands),
            str(board.commands[-3:]))
    r.check("点动命令里没有 acc（不发假参数）",
            not any(c.startswith("zup 1.000 300 1") for c in board.commands))
    r.check("jogDown(2.5)", b.jogDown(2.5) is True)
    r.check("命令是 zdown 2.500 300",
            r.wait_for(lambda: "zdown 2.500 300" in board.commands), str(board.commands[-3:]))
    r.check("moveTo(100)", b.moveTo(100.0) is True)
    r.check("命令是 zmove 100.000 300 100",
            r.wait_for(lambda: "zmove 100.000 300 100" in board.commands), str(board.commands[-3:]))
    r.check("乐观置 moving", r.wait_for(lambda: b.moving))
    r.check("轮询后到位", r.wait_for(lambda: not b.moving))

    print("=== 10) 目标按**固件实际在用的**软限位夹取 ===")
    r.check("超出上限被夹到 250", b.moveTo(999.0) is True)
    r.check("命令里是 250.000", r.wait_for(lambda: "zmove 250.000 300 100" in board.commands),
            str(board.commands[-2:]))
    r.check("低于下限被夹到 0", b.moveTo(-5.0) is True)
    r.check("命令里是 0.000", r.wait_for(lambda: "zmove 0.000 300 100" in board.commands),
            str(board.commands[-2:]))

    print("=== 11) 撤掉基准后立刻恢复「拒绝」（基准在 RAM，板子一重启就没）===")
    board.datum = 0
    # ⚠ 要等的是**桥真的从 json 学到 datum=0**，不能等 `not canMove`：
    #   `canMove` 里还带着 `not moving`，而上一条断言刚乐观置过 moving ——
    #   那个条件会立刻为真，于是又变成"在板子状态同步之前就下了结论"。
    #   （同一个坑在这个文件里出现过两次，见第 2 组那条注释。）
    r.check("桥从 json 学到 datum=0", r.wait_for(lambda: not b.datum), f"datum={b.datum}")
    r.check("canMove 变 False", not b.canMove)
    r.check("moveTo 再次被拒", b.moveTo(10.0) is False and "基准" in b.lastError, b.lastError)
    board.datum = 1
    r.check("恢复基准后又能动", r.wait_for(lambda: b.canMove))

    print("=== 12) 软限位设置（含本地校验）===")
    r.check("上限<=下限 被拒", b.setSoftLimits(100.0, 50.0) is False and "上限" in b.lastError)
    r.check("跨度>1000 被拒", b.setSoftLimits(0.0, 2000.0) is False and "1000" in b.lastError)
    r.check("正常设置接受", b.setSoftLimits(0.0, 240.0) is True)
    r.check("成功之后那条参数提示自动消失（不能一直挂着与现状矛盾的红字）",
            r.wait_for(lambda: b.lastError == ""), repr(b.lastError))
    r.check("命令是 zlim 0.000 240.000",
            r.wait_for(lambda: "zlim 0.000 240.000" in board.commands), str(board.commands[-2:]))
    r.check("固件侧软限位跟着变", r.wait_for(lambda: abs(b.firmwareHi - 240.0) < 0.01),
            f"firmwareHi={b.firmwareHi}")

    print("=== 13) 速度/加减速：只发改动的那一项；acc=0 会被夹到 1 ===")
    n_before = len([c for c in board.commands if c.startswith("zset")])
    b.setSpeed(600, 100)                    # 只有 rpm 变了
    # ⚠ 断言方式：必须 **pump 一段固定时间让队列泵完**，再数最终条数。
    #   第一版写的是 `wait_for(数量 == n_before+1)` —— 那只要求"某一刻恰好 +1"，
    #   而第二条命令是异步泵出去的：**故意把 setSpeed 改成两项都发，这条照样通过**
    #   （评审用 break-test 证明它是假断言）。
    r.pump(0.5)
    zsets = [c for c in board.commands if c.startswith("zset")][n_before:]
    r.check("只发了一条 zset（rpm 变了、acc 没变）", zsets == ["zset rpm 600"], str(zsets))
    r.check("没有多发 acc", "zset acc" not in " ".join(zsets), str(zsets))
    b.setSpeed(600, 0)                      # acc=0 是"直接启动"，必须被夹到 1
    r.check("acc=0 被夹到 1（手册：0 = 无斜坡直接启动）",
            r.wait_for(lambda: "zset acc 1" in board.commands), str(board.commands[-2:]))
    r.check("界面属性同步", b.rpm == 600 and b.acc == UI_MIN_ACC, f"rpm={b.rpm} acc={b.acc}")
    r.check("uiMaxRpm 就是固件的夹取上限 1200", UI_MAX_RPM == 1200, str(UI_MAX_RPM))

    print("=== 14) 自动回零：走 nowait 的非阻塞路径 ===")
    r.check("homeNow 接受", b.homeNow() is True)
    r.check("命令是 zhome down nowait",
            r.wait_for(lambda: "zhome down nowait" in board.commands), str(board.commands[-2:]))
    r.check("homing 起来后能轮询到完成", r.wait_for(lambda: b.homing == 2), f"homing={b.homing}")

    print("=== 15) 急停：永远能发，而且**插队到队首** ===")
    # ⚠ 构造要点：要让**队列非空**时再按急停，才测得出"插队"。
    #   第一版只发了一条在途命令就按急停 —— 那时队列本来是空的，
    #   front=True 与不加**完全等价**，于是这条断言是假的：
    #   故意把 front=True 删掉，测试照样全绿（break-test 抓到的）。
    board.slow_head, board.slow_sec = "zcfg", 0.8
    b.sendCommand("zcfg")                              # ① 占住在途（慢）
    r.pump(0.05)
    b.sendCommand("pos")                               # ② 先进队
    b.stopNow()                                        # ③ 必须插到 ② 前面
    r.pump(1.8)
    idx = len(board.commands) - 1 - board.commands[::-1].index("zcfg")
    nxt = board.commands[idx + 1: idx + 3]
    r.check("急停插到了先排队的那条前面", nxt == ["stop all", "pos"], str(nxt))
    r.check("急停的原文是 'stop all'", "stop all" in board.commands)
    board.slow_head, board.slow_sec = None, 0.0

    print("=== 16) 协议显示框：每一行收发都留痕 ===")
    b.clearProto()
    b.pollNow()
    r.check("记下了发出去的 json", r.wait_for(lambda: any(
        ln.startswith("→ json") for ln in b.protoLines)), str(b.protoLines[:3]))
    r.check("记下了收回来的 json 行", r.wait_for(lambda: any(
        ln.startswith("← {") for ln in b.protoLines)), str(b.protoLines[:3]))
    r.check("记下了 #OK 结束标记", any(ln.strip() == "← #OK" for ln in b.protoLines),
            str(b.protoLines[-3:]))
    # ⚠ 第一版写 `len(...) < 5000`，而当时只有 3 行 —— **恒真**，等于没测。
    #   这里真的灌到超过 PROTO_CAP，再断言"封顶了且正好是 CAP"。
    from Src.zstage_bridge import PROTO_CAP as _CAP
    board.flood_proto = _CAP + 50            # 假板子会把 json 回成多行（见 FakeBoard）
    b.pollNow()
    r.pump(0.6)
    board.flood_proto = 0
    r.check("协议行封顶在 PROTO_CAP（真的灌爆过）",
            len(b.protoLines) <= _CAP, f"{len(b.protoLines)} vs {_CAP}")
    r.check("封顶后仍保留最后几行（不是整段清空）", len(b.protoLines) >= _CAP - 5,
            str(len(b.protoLines)))

    print("=== 17) 驱动器帧镜像（@TX/@RX）：只进协议框，不干扰命令判定 ===")
    r.check("setTrace(True) 接受", b.setTrace(True) is True)
    r.check("命令是 trace on", r.wait_for(lambda: "trace on" in board.commands))
    r.check("traceOn 变 True", r.wait_for(lambda: b.traceOn))
    b.clearProto()
    b.sendCommand("pos")
    r.check("协议框里出现 @T/@R 帧行", r.wait_for(lambda: any(
        ln.strip().startswith("@TX left") for ln in b.protoLines)), str(b.protoLines[:4]))
    r.check("帧行带 C5 帧头和 5C 帧尾", any(
        "C5" in ln and "5C" in ln for ln in b.protoLines), str(b.protoLines[:4]))
    # ⚠ 关键：帧行走的是 `@` 那条**独立分支**，不能被算成"命令的应答行" ——
    #   它是异步插进来的，混进应答里会让命令输出对不上号（协议框里以 `←` 开头的
    #   只应该是真正的应答行，帧行是 `   @T …` 这种缩进形式）。
    r.check("帧行没有被当成命令应答行（走的是 @ 分支）",
            not any(ln.startswith("← @") for ln in b.protoLines), str(b.protoLines[:4]))
    r.check("pos 的应答正常收到", r.wait_for(
        lambda: any("left :" in ln for ln in b.protoLines)), str(b.protoLines[-3:]))
    r.check("pos 命令正常完成、无错误", b.lastError == "" or "参数" in b.lastError,
            repr(b.lastError))
    r.check("setTrace(False) 接受", b.setTrace(False) is True)
    r.check("命令是 trace off", r.wait_for(lambda: b.traceOn is False))

    print("=== 18) 暂停 / 清空协议框 ===")
    b.setProtoPaused(True)
    n = len(b.protoLines)
    b.sendCommand("pos")
    r.pump(0.4)
    r.check("暂停期间不再追加", len(b.protoLines) == n, f"{n} → {len(b.protoLines)}")
    b.setProtoPaused(False)
    b.clearProto()
    r.check("清空后为空", len(b.protoLines) == 0)

    print("=== 19) 自定义命令框（原样下发；空/换行拒绝）===")
    r.check("空命令被拒", b.sendCommand("   ") is False)
    r.check("含换行被拒", b.sendCommand("pos\nstop all") is False and "换行" in b.lastError)
    r.check("正常命令原样发出", b.sendCommand("zcfg") is True)
    r.check("板子确实收到了 zcfg", r.wait_for(lambda: "zcfg" in board.commands))

    print("=== 20) 校平（ztilt）的单次限幅 ===")
    r.check("±10mm 以内接受", b.tilt(0.3) is True)
    r.check("超过 10mm 被拒", b.tilt(12.0) is False and "10mm" in b.lastError)

    print("=== 20b) 命令超时路径（板子收到但不回话）===")
    # ⚠ 这一组是补上来的：整份测试原来没有"超时"二字，`_on_cmd_timeout` 从未被执行过
    #   （评审指出）。超时是**最容易出错又最难在真机上复现**的路径，必须覆盖。
    board.silent_head = "zcfg"
    r.check("在途命令不会被立刻判超时", b.lastError == "" or "超时" not in b.lastError,
            repr(b.lastError))
    b.sendCommand("zcfg")                       # 有去无回
    r.check("超时后给出可读错误（transient）", r.wait_for(lambda: "超时" in b.lastError, 6),
            repr(b.lastError))
    r.check("超时后队列没卡死（下一条能发出去）", b.sendCommand("pos") is True)
    board.silent_head = None
    r.check("链路恢复后超时提示自动消失（transient 该自己走）",
            r.wait_for(lambda: b.lastError == ""), repr(b.lastError))
    r.check("桥还活着（json 轮询继续）", r.wait_for(lambda: b.connected))

    print("=== 20c) 失能 / 使能（自锁丝杠上「手推平台到底」这条主线）===")
    # ⚠ 这台机器的丝杠**自锁**（现场确认：断电后平台不动），所以失能后平台停在原地 ——
    #   「失能」是"用手把平台推到靠块再 zzero"这条主线的正确做法。
    #   早先这个按钮故意没做（理由是"不自锁、会掉下来"），那个前提是推算错的。
    r.check("setEnabled(False) 接受", b.setEnabled(False) is True)
    r.check("命令是 dis all", r.wait_for(lambda: "dis all" in board.commands),
            str(board.commands[-2:]))
    r.check("固件侧 en 变 0", r.wait_for(lambda: not b.enabled), f"enabled={b.enabled}")
    r.check("setEnabled(True) 接受", b.setEnabled(True) is True)
    r.check("命令是 en all", r.wait_for(lambda: "en all" in board.commands),
            str(board.commands[-2:]))
    r.check("固件侧 en 变 1", r.wait_for(lambda: b.enabled))

    print("=== 20d) 无限位回零的参数（限位电流 / 转速 / 超时）===")
    # 背景（2026-09-29 用户提的）：界面上**没法设限位电流** —— 而它正是"自动回零能不能成"
    # 的关键。⚠ 本机经验区是**实测**出来的 60~300mA（100mA 可用；200~250mA 顶到超时也不触发），
    # 不是 42 电机手册口径的 600mA 级 —— 早先的 300~1500 已被现场否掉。
    # 固件早就有 `zset home <rpm> <mA> [timeout_ms]`（存 NVS），只是桥里没接。
    r.check("默认值 = 固件出厂（400rpm / **100mA** / 12000ms）",
            (b.homeRpm, b.homeMa, b.homeTmo) == (400, 100, 12000),
            f"{b.homeRpm}/{b.homeMa}/{b.homeTmo}")
    r.check("经验区是从桥里读的（QML 不许再抄一份 60/300）",
            (b.uiHomeMaSweetLo, b.uiHomeMaSweetHi) == (60, 300),
            f"{b.uiHomeMaSweetLo}~{b.uiHomeMaSweetHi}")
    # ⚠ 「超时够不够」的体检值（与固件 home_travel_ms() 同一套算法）：
    #   现场实测用户填 4000ms，而满行程要好几秒 —— 回零会在中途被判超时失败，
    #   而现象只是"回零没跑完就停了"，用户无从判断。
    r.check("满行程需求按板子参数算（0~250mm / 8mm 导程 / 400rpm → 7~9s）",
            7000 <= int(b.homeTmoNeedMs) <= 9000, str(b.homeTmoNeedMs))
    r.check("量程与固件 `zset home` 的检查一致（rpm 1~6000 / mA 1~3000）",
            (b.uiHomeRpmMin, b.uiHomeRpmMax) == (1, 6000)
            and (b.uiHomeMaMin, b.uiHomeMaMax) == (1, 3000),
            f"{b.uiHomeRpmMin}~{b.uiHomeRpmMax} / {b.uiHomeMaMin}~{b.uiHomeMaMax}")

    n_before = len([c for c in board.commands if c.startswith("zset home")])
    r.check("setHomeParams 接受合法值", b.setHomeParams(500, 200, 15000) is True)
    r.check("三个值**一起**发（固件要求 rpm+mA 都在场）",
            r.wait_for(lambda: any(c == "zset home 500 200 15000" for c in board.commands)),
            str([c for c in board.commands if c.startswith("zset home")][:2]))
    r.pump(0.4)
    r.check("只发了一条（不重复占在途）",
            len([c for c in board.commands if c.startswith("zset home")]) == n_before + 1,
            str([c for c in board.commands if c.startswith("zset home")][n_before:]))
    r.check("板子接受后回读到新值（json hma/hrpm/htmo）",
            r.wait_for(lambda: (b.homeMa, b.homeRpm, b.homeTmo) == (200, 500, 15000)),
            f"{b.homeRpm}/{b.homeMa}/{b.homeTmo}")

    board.commands.clear()
    r.check("与板子当前值相同的三个数 → 本地判定，不发出去",
            b.setHomeParams(500, 200, 15000) is True)
    r.pump(0.4)
    r.check("确实一条 zset home 都没发（省一次在途）",
            not any(c.startswith("zset home") for c in board.commands),
            str([c for c in board.commands if c.startswith("zset home")]))

    # 越界值必须在**本地**被拦下：界面比固件宽 = 用户能填一个必然被拒的值（项目铁律）
    for bad, why in (((500, 5000, 15000), "mA 超 3000"),
                     ((500, 0, 15000), "mA 小于 1"),
                     ((0, 800, 15000), "rpm 小于 1"),
                     ((500, 800, 10), "超时太短")):
        n0 = len([c for c in board.commands if c.startswith("zset home")])
        r.check(f"{why} → 桥本地拒绝（不发出去）", b.setHomeParams(*bad) is False,
                f"{bad} err={b.lastError}")
        r.check(f"{why} → 理由里写清了范围（用户要知道填多少才对）",
                "超出范围" in b.lastError and str(bad[0] if "rpm" in b.lastError else
                                              bad[1] if "电流" in b.lastError else bad[2])
                in b.lastError, b.lastError)
        r.pump(0.2)
        r.check(f"{why} → 板子确实没收到",
                len([c for c in board.commands if c.startswith("zset home")]) == n0)

    print("=== 20e) 自动回零的方向（只存上位机，发 zhome <方向>）===")
    # 用户 2026-09-29 的选择：方向**只管手动按钮**，板子里那个方向归「上电自动回零」。
    r.check("默认向下（出厂方向：往底座死点走，重力帮忙）", b.homeDir == "down", b.homeDir)
    board.commands.clear()
    r.check("切到向上", b.setHomeDir("up") is True and b.homeDir == "up", b.homeDir)
    r.check("不认识的方向被拒，且不改状态",
            b.setHomeDir("sideways") is False and b.homeDir == "up", f"{b.homeDir} {b.lastError}")
    r.check("homeNow 照实发 `zhome up nowait`（原来写死 down）",
            b.homeNow() is True and r.wait_for(lambda: "zhome up nowait" in board.commands),
            str([c for c in board.commands if c.startswith("zhome")][:2]))
    r.check("非阻塞版（绝不能发阻塞的 zhome）",
            all("nowait" in c for c in board.commands if c.startswith("zhome")),
            str([c for c in board.commands if c.startswith("zhome")][:2]))
    b.stopNow()
    r.pump(0.3)
    r.check("切回向下", b.setHomeDir("down") is True and b.homeDir == "down", b.homeDir)

    print("=== 20f) 上电自动回零：开关能发，但**不许带方向** ===")
    # 带方向就等于顺手把板子里存的方向改掉 —— 而那个方向属于另一条路径（见 20e）
    board.commands.clear()
    r.check("setAutohome(True) 接受", b.setAutohome(True) is True)
    r.check("发的是 `zautohome on`（正好两个词）",
            r.wait_for(lambda: "zautohome on" in board.commands),
            str([c for c in board.commands if c.startswith("zautohome")][:2]))
    r.pump(0.4)
    r.check("没有 extra 方向参数", all(len(c.split()) == 2 for c in board.commands
                                      if c.startswith("zautohome")),
            str([c for c in board.commands if c.startswith("zautohome")][:2]))
    r.check("板子里存的方向**没被改**（仍是 down）", board.home_dir == "down", board.home_dir)
    r.check("乐观置位：不等 json 就先亮", b.autohome is True)
    r.check("setAutohome(False)", b.setAutohome(False) is True
            and r.wait_for(lambda: not b.autohome))

    print("=== 20g) 急停超时：必须比固件最坏耗时更长（否则迟到应答会配错命令）===")
    # 固件的打断路径最坏 ≈ 0x93(2×250ms×2轴) + 刹车(150+500×2 每轴) ≈ 2.5s；
    # 桥原来用默认 3s，而迟到的那条 `#OK` 会与**下一条**命令配对错位（本文件顶部列为事故）。
    from Src.zstage_bridge import SLOW_CMD_TIMEOUT_MS
    r.check("`stop` 有专门的超时档（不再吃默认 3000ms）", "stop" in SLOW_CMD_TIMEOUT_MS,
            str(SLOW_CMD_TIMEOUT_MS))
    r.check("它的值 >= 固件最坏耗时（含余量）", SLOW_CMD_TIMEOUT_MS.get("stop", 0) >= 6000,
            str(SLOW_CMD_TIMEOUT_MS.get("stop")))

    print("=== 21) 断线：基准与软限位状态必须作废（板子可能重启过）===")
    r.check("断开前 datum=True", b.datum)
    b.disconnectDevice()
    r.check("connected 变 False", r.wait_for(lambda: not b.connected))
    r.check("datum 清掉", not b.datum)
    r.check("limitsSet 清掉", not b.limitsSet)
    r.check("canMove 为 False", not b.canMove)
    r.check("traceOn 复位", not b.traceOn)

    print("=== 22) 未连接时的拒绝路径（不能静默失败）===")
    n_all = len(board.commands)
    r.check("jogUp 被拒且给出原因", b.jogUp(1) is False and "连接" in b.lastError, b.lastError)
    r.check("stopNow 明确告知没发出去",
            (b.stopNow() is None) and "急停" in b.lastError, b.lastError)
    r.check("setZero 被拒", b.setZero() is False)
    r.pump(0.2)
    r.check("没有任何命令到达板子", len(board.commands) == n_all)

    print("=== 23) QSettings 用的是注入的隔离文件（不许污染用户真实配置）===")
    s = QSettings(str(_SETTINGS_FILE), QSettings.Format.IniFormat)
    r.check("host 写进了隔离文件", s.value("zstage/host") == "127.0.0.1",
            repr(s.value("zstage/host")))
    r.check("测试用的假板子端口也写进去了（证明真的在用这个文件）",
            str(s.value("zstage/port")) == str(server.port), repr(s.value("zstage/port")))
    r.check("回零方向也存在隔离文件里（只存本机，不写板子）",
            str(s.value("zstage/home_dir")) in ("down", "up"), repr(s.value("zstage/home_dir")))

    server.close()
    print("\n========================================")
    if r.fails:
        print(f"{len(r.fails)} 项失败：")
        for f in r.fails:
            print("  · " + f)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
