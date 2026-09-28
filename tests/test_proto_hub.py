#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公用协议显示框（ProtoHub）回归测试 —— 两块板子的协议流汇成一条。

为什么单独一个文件：这里验的是**跨两块板子**的行为 —— 合并、前缀、暂停/清空一起生效、
自定义命令按目标分发。任何单个桥的测试都覆盖不到这些（它们是 2026-09-28 用户要求
"协议显示改成公用的"才出现的）。

运行（不需要真板子）：
    source DuAD_SoftwareContent/pyqml/bin/activate
    QT_QPA_PLATFORM=offscreen python3 tests/test_proto_hub.py

⚠ 两个桥都用**真** StageBridge / ZStageBridge + 进程内假板子 —— 协议记录是桥内部的
行为，用替身测不出来（替身没有 socket、也没有 _proto_add 的调用点）。
"""

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import QCoreApplication, QSettings           # noqa: E402

from Src.proto_hub import HUB_CAP, ProtoHub                      # noqa: E402
from Src.stage_bridge import StageBridge                         # noqa: E402
from Src.zstage_bridge import ZStageBridge                       # noqa: E402
from test_stage_bridge import TOKEN, FakeBoardServer             # noqa: E402
# ⚠ Z 轴的假板子要从 **test_zstage_bridge** 借：那个 FakeBoard 才有 `flood_proto`
#   （test_zstage_page 里那个是页面用的精简版，没有灌爆能力 —— 踩过一次：
#    给一个不存在的属性赋值不会报错，只是永远灌不爆，断言就变成了假测试）。
from test_zstage_bridge import TOKEN as Z_TOKEN                  # noqa: E402
from test_zstage_bridge import FakeBoardServer as FakeZBoardServer   # noqa: E402


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
    app = QCoreApplication(sys.argv[:1])
    r = Runner(app)

    xy_srv = FakeBoardServer()
    z_srv = FakeZBoardServer()
    # ⚠ 隔离 QSettings：两个桥默认写用户的真实作用域（踩过一次，见 test_stage_bridge.py 头部）
    sdir = Path(tempfile.mkdtemp(prefix="duad_protohub_"))
    stage = StageBridge(settings=QSettings(str(sdir / "xy.ini"), QSettings.Format.IniFormat))
    zstage = ZStageBridge(settings=QSettings(str(sdir / "z.ini"), QSettings.Format.IniFormat))
    hub = ProtoHub({"xy": stage, "z": zstage})
    r.check("来源顺序 = 二轴在前、Z 在后", list(hub.sources) == ["xy", "z"],
            str(list(hub.sources)))

    print("=== 1) 两块板子都连上 ===")
    r.check("二轴连接发起", stage.connectDevice("127.0.0.1", xy_srv.port, TOKEN) is True)
    r.check("Z 轴连接发起", zstage.connectDevice("127.0.0.1", z_srv.port, Z_TOKEN) is True)
    r.check("二轴已连接", r.wait_for(lambda: stage.connected), f"{stage.connected}")
    r.check("Z 轴已连接", r.wait_for(lambda: zstage.connected), f"{zstage.connected}")

    print("=== 2) 两路的收发都进同一个框（带来源前缀）===")
    r.check("二轴发了轮询命令", r.wait_for(
        lambda: any(l.startswith("[XY] → json") for l in hub.lines), 4),
        str([l for l in hub.lines if l.startswith("[XY]")][:2]))
    r.check("Z 轴发了轮询命令", r.wait_for(
        lambda: any(l.startswith("[Z] → json") for l in hub.lines), 4),
        str([l for l in hub.lines if l.startswith("[Z]")][:2]))
    # ⚠ 要**等**应答到齐再断言：轮询是异步的，刚连上时框里可能只有握手那几行
    #   （第一版就是这么假红的）
    r.check("两路的**应答**也在（← 开头）",
            r.wait_for(lambda: any(l.startswith("[XY] ← {") for l in hub.lines)
                       and any(l.startswith("[Z] ← {") for l in hub.lines), 4),
            str(hub.lines[:2]))
    r.check("口令应答也记下来了（← #OK auth）",
            any("#OK auth" in l for l in hub.lines), str(hub.lines[:3]))
    r.check("口令本身**不记**（别把秘密写进日志）",
            not any("deadbeef" in l for l in hub.lines))
    # 合并顺序 = 到达顺序：自己发的那条后面紧跟自己的应答
    seq = [l for l in hub.lines if l.startswith("[XY]")]
    i_send = next(i for i, l in enumerate(seq) if l.startswith("[XY] → json"))
    r.check("同一条命令的「发」在「收」之前（顺序没被打乱）",
            r.wait_for(lambda: any(l.startswith("[XY] ← ")
                                   for l in seq[i_send + 1:]), 3),
            str(seq[i_send:i_send + 3]))

    print("=== 3) 自定义命令按目标分发 ===")
    xy_srv.board.commands.clear()
    z_srv.board.commands.clear()
    # 用两边假板子都**认得**且不动电机的命令（未知命令也会进框，但那样断言的
    # 是错误路径，下一段单独验）
    r.check("发给二轴：接口返回 True", hub.sendCommand("xy", "hcfg 0 300 300") is True)
    r.check("二轴板子收到了",
            r.wait_for(lambda: "hcfg 0 300 300" in xy_srv.board.commands, 3),
            str(xy_srv.board.commands[-2:]))
    r.check("Z 轴板子没收到（没串台）", "hcfg 0 300 300" not in z_srv.board.commands)
    r.check("发给 Z：接口返回 True", hub.sendCommand("z", "zset rpm 300") is True)
    r.check("Z 板子收到了", r.wait_for(lambda: "zset rpm 300" in z_srv.board.commands, 3),
            str(z_srv.board.commands[-2:]))
    r.check("框里能看到这两条（带各自前缀）",
            any(l == "[XY] → hcfg 0 300 300" for l in hub.lines)
            and any(l == "[Z] → zset rpm 300" for l in hub.lines),
            str([l for l in hub.lines if "hcfg" in l or "zset" in l][:2]))
    # 未知命令也必须留下痕迹（"发了但没反应"要能从框里看出来）
    hub.sendCommand("xy", "no_such_cmd")
    r.check("未知命令照样进框（连错误一起）",
            r.wait_for(lambda: any("no_such_cmd" in l for l in hub.lines), 3),
            str([l for l in hub.lines if "no_such" in l][:2]))
    r.check("不认识的来源返回 False（不静默丢弃）", hub.sendCommand("bogus", "x") is False)

    print("=== 4) 暂停 / 清空 是两路一起生效 ===")
    r.check("暂停接口返回后两路都 paused", (hub.setPaused(True) or True)
            and bool(hub.paused) is True and bool(stage.protoPaused) is True
            and bool(zstage.protoPaused) is True,
            f"hub={hub.paused} xy={stage.protoPaused} z={zstage.protoPaused}")
    n_paused = len(hub.lines)
    hub.sendCommand("xy", "stat all")
    hub.sendCommand("z", "pos")
    r.pump(1.2)                      # 轮询还在跑，但两路都不该再记
    r.check("暂停期间不再追加（收发照常）", len(hub.lines) == n_paused,
            f"{n_paused} → {len(hub.lines)}")
    # ⚠ 清空**要在暂停状态下断言**：轮询还在跑，一解除暂停立刻又塞进新行，
    #   "清空后必须是 0 行"就成了随机失败的假断言（test_zstage_page 12c 踩过同一个坑）
    hub.clear()
    r.pump(0.2)
    r.check("暂停中清空 → 合并列表为空", len(hub.lines) == 0, f"{len(hub.lines)}")
    r.check("清空也清了两路自己的记录",
            len(stage.protoLines) == 0 and len(zstage.protoLines) == 0,
            f"xy={len(stage.protoLines)} z={len(zstage.protoLines)}")
    hub.setPaused(False)
    r.check("继续后又能记了", r.wait_for(lambda: len(hub.lines) > 0, 4),
            f"{len(hub.lines)}")

    print("=== 5) 合并列表封顶（灌爆它）===")
    # 假 Z 板子能把一条 json 回成很多行（对应固件 `trace on` 时每帧两行）
    z_srv.board.flood_proto = HUB_CAP + 200
    r.check("真的灌爆过（Z 桥自己的记录先被灌到封顶）",
            r.wait_for(lambda: len(zstage.protoLines) >= 300, 8),
            f"{len(zstage.protoLines)}")
    r.pump(1.2)
    z_srv.board.flood_proto = 0
    r.check(f"合并列表封顶在 HUB_CAP={HUB_CAP}", len(hub.lines) <= HUB_CAP,
            f"{len(hub.lines)}")
    r.check("封顶后保留的是**最后**几行（不是整段清空）",
            len(hub.lines) >= HUB_CAP - 5 and any("[Z]" in l for l in hub.lines[-3:]),
            str(hub.lines[-2:]))

    stage.disconnectDevice()
    zstage.disconnectDevice()
    r.pump(0.3)
    xy_srv.close()
    z_srv.close()
    print()
    if r.fails:
        print(f"FAILED {len(r.fails)}: {r.fails}")
        return 1
    print("ProtoHub（公用协议显示框）全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
