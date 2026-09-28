#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把某个页面渲染成 PNG，用于**肉眼检查布局**（以及量控件几何）。

为什么需要它：QML 布局出问题时的典型症状是"某个控件不见了 / 错位了"，
而这种问题**靠读代码猜非常低效**。offscreen 平台可以直接把窗口抓成图，
一眼就能看出是"没渲染"还是"被推出可视区裁掉了"。

用法：
    source DuAD_SoftwareContent/pyqml/bin/activate
    QT_QPA_PLATFORM=offscreen python3 tests/render_page.py [输出.png] [宽度] [高度] [展开]

    # 例：看平台页、诊断面板展开、窗口开高一点以便一次看全
    QT_QPA_PLATFORM=offscreen python3 tests/render_page.py /tmp/stage.png 1400 1700 diag

    # 平铺对比：全部折叠 / 展开设置 / 展开诊断
    for m in none setup diag; do
      QT_QPA_PLATFORM=offscreen python3 tests/render_page.py /tmp/p_$m.png 1400 1700 $m
    done

⚠ 两个 PySide 的坑（都踩过）：
  1. **必须 `from PySide6.QtQuick import QQuickWindow`**：不 import 定义类型的模块，
     `rootObjects()` 拿回来的就只是 `QWindow`，没有 `grabWindow()`。
  2. **不要用 `findChild(type(win), ...)`** —— 第一个参数要传 `QObject` 类型对象。

配套手法：控件"看不见"时，用 `--dump` 打印相关行的宽度与子控件坐标，
判断是"宽度被撑爆推出去"还是"根本没创建"。
"""

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

# ⚠ 与 main.py 一致：相对 URL 按"赋值所在 qml 文件"解析。
#   不设它，`StagePage.qml` 里写的 `../images/Z轴平台.svg` 会解析成 `pages/images/…`
#   （不存在）→ 截图里那个图标是空白，而"真程序里明明是好的"。
os.environ.setdefault("QML_COMPAT_RESOLVE_URLS_ON_ASSIGNMENT", "1")

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "DuAD_SoftwareContent"
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import QObject, QPointF, QSettings, QUrl        # noqa: E402
from PySide6.QtGui import QGuiApplication                           # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine                     # noqa: E402
from PySide6.QtQuick import QQuickWindow                            # noqa: E402  ← 别删，见文件头说明

from Src.stage_bridge import StageBridge                            # noqa: E402
from Src.zstage_bridge import ZStageBridge                          # noqa: E402
from test_stage_page import FakeAppBridge, FakeCameraBridge         # noqa: E402
from test_stage_bridge import TOKEN, FakeBoardServer                # noqa: E402
from test_zstage_page import Z_TOKEN, FakeZBoardServer              # noqa: E402

HARNESS = b"""
import QtQuick
import QtQuick.Controls
import "pages"

Window {
    id: win
    width: %WIDTH%
    height: %HEIGHT%
    visible: true
    StagePage {
        id: stagePage
        objectName: "stagePage"
        anchors.fill: parent
    }
}
"""


def dump_z_geometry(page):
    """打印 Z 区每张卡：内容区最宽的一行 + 卡片内控件的最大右边界。

    为什么值得单独一段：Z 区是**新加的一整块**，而这类"被撑破"的布局事故
    （AGENTS.md 第 9 条）在截图里只能看出"某个控件不见了"，看不出"谁把谁顶出去了"。
    数字一贴出来，是不是被撑爆就一目了然。

    ⚠ 判据是"**行宽 > 内容区宽**"，不是某个写死的像素数（2026-09-27 改）：
      页面重排成两列以后卡片宽度由窗口决定（不再恒为 460），写死的 420 会
      在宽窗口下把正常满宽行全报成 ⚠，等于把守卫变成噪音。
    """
    from PySide6.QtQuick import QQuickItem

    def walk(item):
        yield item
        for c in item.childItems():
            yield from walk(c)

    print("=== 卡片几何（每行 ≤ 内容区宽；右边界必须 ≤ 卡片宽）===")
    for card_name, body_name, label in (
            ("zTelemetryPanel", "zTelemetryBody", "Z 状态（抽屉里）"),
            ("zJogPanel", "zJogBody", "Z 手动控制"),
            ("protoPanel", "protoBody", "公用协议框"),
            ("zSetupPanel", "zSetupBody", "Z 设置")):
        card = page.findChild(QObject, card_name)
        body = page.findChild(QObject, body_name)
        if card is None or body is None or not isinstance(card, QQuickItem) \
                or not isinstance(body, QQuickItem):
            print(f"  {label}: 找不到卡片")
            continue
        cw = float(card.property("width"))
        bw = float(body.property("width"))
        worst, worst_name = 0.0, ""
        for ch in body.childItems():
            if ch.isVisible() and float(ch.width()) > worst:
                worst, worst_name = float(ch.width()), \
                    (ch.objectName() or ch.metaObject().className())
        max_right, right_name = 0.0, ""
        for ch in walk(card):
            if ch is card or not ch.isVisible():
                continue
            w = float(ch.width())
            if w >= cw - 1.0:
                continue
            right = ch.mapToItem(card, QPointF(0, 0)).x() + w
            if right > max_right:
                max_right, right_name = right, (ch.objectName() or ch.metaObject().className())
        ch_ = float(card.property("height"))
        # ⚠ 高度也要量：宽度全对、内容却被裁掉的情况真的发生过
        #   （implicitHeight 挂错对象 → 整张卡塌成 32px，截图里只剩个标题）
        # 折叠着的面板高度 0 是**设计如此**（`--expand none`），不算坏。
        # 收起着的（高级没展开 / 抽屉没展开）不量：那不是"塌了"，是设计如此
        if not card.property("visible") or ch_ < 1:
            print(f"  {label:12s} 收起状态（未展开），跳过")
            continue
        bad = worst > bw + 1 or max_right > cw + 1 or (ch_ < 80 and not collapsed_ok)
        print(f"  {label:8s} 卡片 {cw:.0f}x{ch_:.0f} 内容区 {bw:.0f}  最宽一行={worst:.0f} "
              f"({worst_name})  最大右边界={max_right:.0f} ({right_name})  "
              f"{'⚠ 超了/塌了' if bad else 'OK'}")


def dump_page(page):
    """打印整页每一段的 y/高度，以及"首屏能不能看到"。

    为什么值得单独一段：这一页是**一条滚动链**（整页滚），最典型的坏味道是
    **高频控件被推到首屏之外**（点动、急停要滚一下才够得着）—— 而截图只能看见
    当前滚动位置，看不见"往下还有什么、有多远"。列高 = 可视高度，
    y + height > 可视高的就是要滚动才看得到的（✗）。
    """
    from PySide6.QtQuick import QQuickItem

    def find_desc(item, want):
        for c in item.childItems():
            if want in c.metaObject().className():
                return c
            r = find_desc(c, want)
            if r is not None:
                return r
        return None

    flick = page.findChild(QObject, "pageScroll")
    if flick is None or not isinstance(flick, QQuickItem):
        print("  找不到 pageScroll")
        return
    col = find_desc(flick, "ColumnLayout")
    vh = float(flick.property("height"))
    print(f"=== 整页分段（可视高 {vh:.0f}；✗ = 要滚动才看得到）===")
    for ch in col.childItems():
        if not ch.isVisible():
            print(f"     ·（收起）      {ch.objectName() or ch.metaObject().className()}")
            continue
        y, h = float(ch.property("y")), float(ch.property("height"))
        name = ch.objectName() or ch.metaObject().className()
        print(f"     {'✓' if y + h <= vh + 1 else '✗'} y={y:6.0f} h={h:5.0f}  {name}")
    print(f"     内容高 {float(flick.property('contentHeight')):.0f}"
          f"  整页{'可滚' if flick.property('interactive') else '不可滚'}")


def main():
    ap = argparse.ArgumentParser(description="渲染 StagePage 以便检查布局")
    ap.add_argument("out", nargs="?", default="/tmp/stage.png")
    ap.add_argument("width", nargs="?", type=int, default=1400)
    ap.add_argument("height", nargs="?", type=int, default=1700)
    ap.add_argument("expand", nargs="?", default="none",
                    choices=["none", "adv", "drawers", "setup", "both"])
    ap.add_argument("--dump", action="store_true",
                    help="额外打印关键行的宽度与子控件坐标（判断'被撑爆'还是'没创建'）")
    args = ap.parse_args()

    app = QGuiApplication(sys.argv[:1])
    server = FakeBoardServer()                 # 进程内假板子：让页面有真实数据可显示
    zserver = FakeZBoardServer()               # Z 轴那块板子（另一台，另一个 IP）
    sdir = Path(tempfile.mkdtemp(prefix="render_"))
    stage = StageBridge(settings=QSettings(str(sdir / "xy.ini"), QSettings.Format.IniFormat))
    zstage = ZStageBridge(settings=QSettings(str(sdir / "z.ini"), QSettings.Format.IniFormat))
    fake_app, fake_cam = FakeAppBridge(), FakeCameraBridge()
    fake_app.cameraConnected = False           # 预览默认关；要测预览把它改成 True

    eng = QQmlApplicationEngine()
    # ⚠ 收集 QML 警告/错误：**"渲染得出来"不等于"没有错"**。
    #   真实踩过的一次：`color: Colors.transparent`（单例里没有 transparent）只在
    #   **渲染 delegate 时**报一行 `Unable to assign [undefined] to QColor` ——
    #   页面照样显示、截图看着正常，只是那个按钮的颜色是错的；而页面测试只检查
    #   "加载完成那一刻"的 warnings，**完全抓不到**。所以渲染完必须再查一遍。
    qml_warnings = []
    eng.warnings.connect(lambda ws: qml_warnings.extend(w.toString() for w in ws))
    eng.addImportPath(str(CONTENT))
    ctx = eng.rootContext()
    ctx.setContextProperty("StageBridge", stage)
    ctx.setContextProperty("AppBridge", fake_app)
    ctx.setContextProperty("CameraBridge", fake_cam)
    # ⚠ StagePage 引用了 ZStageBridge —— 不注册它页面直接加载失败
    #   （`ZStageBridge is not defined`），而不是"Z 区空白"。
    ctx.setContextProperty("ZStageBridge", zstage)
    # ⚠ 公用协议框：页面引用 ProtoHub，不注册就是 "ProtoHub is not defined"（页面直接加载失败）
    from Src.proto_hub import ProtoHub

    # ⚠ 必须留一个 Python 引用：context property **不增加引用计数**，
    #   临时对象被 GC 掉 → QML 侧 ProtoHub 变 null → 协议框整块失效
    #   （AGENTS.md 的"FakeBridge 必须保持引用"同一条）。
    proto_hub = ProtoHub({"xy": stage, "z": zstage})
    ctx.setContextProperty("ProtoHub", proto_hub)

    harness = HARNESS.replace(b"%WIDTH%", str(args.width).encode()) \
                     .replace(b"%HEIGHT%", str(args.height).encode())
    eng.loadData(harness, QUrl.fromLocalFile(str(CONTENT / "_render.qml")))

    def pump(sec):
        end = time.time() + sec
        while time.time() < end:
            app.processEvents()
            time.sleep(0.01)

    pump(0.6)
    if not eng.rootObjects():
        print("页面加载失败", file=sys.stderr)
        return 1
    win = eng.rootObjects()[0]
    page = win.findChild(QObject, "stagePage")

    # 造一点真实数据，免得截出来全是 0 / "未连接"
    stage.connectDevice("127.0.0.1", server.port, TOKEN)
    for _ in range(80):
        pump(0.05)
        if stage.connected and stage.travelSet:
            break
    stage.zeroAll()
    pump(0.4)
    # 签名：(kind, rpm, ma) —— 方向不再由界面给（固件按 COREXY_DEFAULT 算）
    # ⚠ rpm 用 300 而不是 60：60rpm 满行程要 9.4s，几乎顶到驱动器 10s 回零超时，
    #   是"跑不到边缘就失败"的假故障来源（详见 docs/17 §13）。这里跟默认可视值保持一致。
    stage.setHomeConfig(0, 300, 300)
    pump(0.6)

    # Z 轴那块：连上 → 立基准 → 设软限位，让 Z 区的读数/状态条/protocol 框都有内容
    zstage.connectDevice("127.0.0.1", zserver.port, Z_TOKEN)
    for _ in range(80):
        pump(0.05)
        if zstage.connected:
            break
    zstage.setZero()
    # ⚠ 等基准真的从 json 回来再动 —— 否则 moveTo 会被桥的基准闸拒掉（"还没有基准"），
    #   截图里 Z 就停在 0.00，看着像功能没做。
    for _ in range(60):
        pump(0.05)
        if zstage.datum:
            break
    zstage.setSoftLimits(0.0, 250.0)
    # ⚠ 同理：软限位也是等 json 回来桥才知道（`lim` 字段），
    #   立刻 moveTo 会被"还没设软限位"拒掉。
    for _ in range(60):
        pump(0.05)
        if zstage.limitsSet:
            break
    zstage.moveTo(120.0)
    pump(0.8)

    # 2026-09-28 三列版面：左右列各有「状态」「设置」两个折叠节，中列有「高级」。
    # `adv` = 只展开高级；`drawers` = 只展开两侧状态；`setup` = 只展开两侧设置；
    # `both` = 全展开。
    page.setProperty("_advExpanded", args.expand in ("adv", "both"))
    page.setProperty("_xyStatusOpen", args.expand in ("drawers", "both"))
    page.setProperty("_zStatusOpen", args.expand in ("drawers", "both"))
    page.setProperty("_xySetupOpen", args.expand in ("setup", "both"))
    page.setProperty("_zSetupOpen", args.expand in ("setup", "both"))
    pump(0.9)

    if args.dump:
        def show(name):
            it = page.findChild(QObject, name)
            if it is None:
                print(f"  {name}: 找不到")
                return
            print(f"  {name}: w={it.property('width')} h={it.property('height')} "
                  f"visible={it.property('visible')}")
            host = it
            for want in ("ComboBox", "Button", "TextField", "Switch"):
                for ch in host.findChildren(QObject):
                    if want in ch.metaObject().className():
                        right = float(ch.property("x")) + float(ch.property("width"))
                        print(f"      {want}: x={ch.property('x')} w={ch.property('width')} "
                              f"右边界={right:.0f}")
                        break
        print("=== 关键行几何（左列控制流的卡片内容区约 412 宽；右列更宽）===")
        for n in ("boardStatus", "pollButton", "previewSwitch"):
            show(n)
        dump_page(page)
        dump_z_geometry(page)

    img = QQuickWindow.grabWindow(win)          # ⚠ 见文件头：必须 QQuickWindow
    img.save(args.out)
    print(f"已保存 {args.out}  {img.width()}x{img.height()}")
    server.close()
    zserver.close()

    # ── 渲染之后再查一遍 QML 警告：把"看着正常但属性是错的"这类问题变成硬失败 ──
    # 已知噪音（不是本次改动引入的，逐条说明为什么可以放过）：
    #   · `Invalid image provider: image://camera/...` —— offscreen 下没注册 image
    #     provider，属预期（AGENTS.md 明写了）。
    #   · `overrides a member of the base object`（enabled）—— 既有组件
    #     SwitchRow/InputRow/SliderRow 用 `property alias enabled:` 遮蔽了 Item.enabled，
    #     是工程级既有告警，与页面无关。
    NOISE = ("Invalid image provider", "overrides a member of the base object")
    real = [w for w in qml_warnings if not any(n in w for n in NOISE)]
    print(f"=== QML 警告 ===  共 {len(qml_warnings)} 条，其中真实错误 {len(real)} 条")
    for w in real:
        print("  ✗ " + w)
    if real:
        print("\n⚠ 渲染期有 QML 错误 —— 这类问题界面往往「看着正常」，但属性/颜色是错的。")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
