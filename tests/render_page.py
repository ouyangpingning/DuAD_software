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
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "DuAD_SoftwareContent"
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import QObject, QSettings, QUrl                 # noqa: E402
from PySide6.QtGui import QGuiApplication                           # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine                     # noqa: E402
from PySide6.QtQuick import QQuickWindow                            # noqa: E402  ← 别删，见文件头说明

from Src.stage_bridge import StageBridge                            # noqa: E402
from test_stage_page import FakeAppBridge, FakeCameraBridge         # noqa: E402
from test_stage_bridge import TOKEN, FakeBoardServer                # noqa: E402

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


def main():
    ap = argparse.ArgumentParser(description="渲染 StagePage 以便检查布局")
    ap.add_argument("out", nargs="?", default="/tmp/stage.png")
    ap.add_argument("width", nargs="?", type=int, default=1400)
    ap.add_argument("height", nargs="?", type=int, default=1700)
    ap.add_argument("expand", nargs="?", default="diag",
                    choices=["none", "setup", "diag", "both"])
    ap.add_argument("--dump", action="store_true",
                    help="额外打印关键行的宽度与子控件坐标（判断'被撑爆'还是'没创建'）")
    args = ap.parse_args()

    app = QGuiApplication(sys.argv[:1])
    server = FakeBoardServer()                 # 进程内假板子：让页面有真实数据可显示
    sfile = Path(tempfile.mkdtemp(prefix="render_")) / "s.ini"
    stage = StageBridge(settings=QSettings(str(sfile), QSettings.Format.IniFormat))
    fake_app, fake_cam = FakeAppBridge(), FakeCameraBridge()
    fake_app.cameraConnected = False           # 预览默认关；要测预览把它改成 True

    eng = QQmlApplicationEngine()
    eng.addImportPath(str(CONTENT))
    ctx = eng.rootContext()
    ctx.setContextProperty("StageBridge", stage)
    ctx.setContextProperty("AppBridge", fake_app)
    ctx.setContextProperty("CameraBridge", fake_cam)

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

    page.setProperty("_setupExpanded", args.expand in ("setup", "both"))
    page.setProperty("_diagExpanded", args.expand in ("diag", "both"))
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

    img = QQuickWindow.grabWindow(win)          # ⚠ 见文件头：必须 QQuickWindow
    img.save(args.out)
    print(f"已保存 {args.out}  {img.width()}x{img.height()}")
    server.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
