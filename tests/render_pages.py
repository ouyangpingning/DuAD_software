#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把**任意一个页面**渲染成 PNG，用于肉眼检查版面与质感。

和 `tests/render_page.py` 的区别：那个是平台控制页专用的（带进程内假板子、能造
真实数据、能量几何），本工具是**通用目检**——用真实的桥（没有硬件时它们自己降级：
相机 SDK 缺失 → stub、串口打不开 → 空列表），把页面摆出来看"卡片阴影/按钮/间距"
这类跨页一致的观感问题。

用法：
    source DuAD_SoftwareContent/pyqml/bin/activate
    python3 tests/render_pages.py camera  /tmp/camera.png  [宽 高]
    python3 tests/render_pages.py settings /tmp/settings.png 1280 900

可选页面：camera / light / comm / collect / settings / detect / stage

⚠ 已知噪音（不是错误）：`image://camera/...` 没有注册 image provider，属预期。
图标在本工具出的图里是**画得出来的**（要目检图标本身的长相用 `tests/render_icons.py`）。
"""

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

# ⚠ 与 main.py 一致：相对 URL 按"赋值所在 qml 文件"解析（不设它图标会静默消失）
os.environ.setdefault("QML_COMPAT_RESOLVE_URLS_ON_ASSIGNMENT", "1")

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "DuAD_SoftwareContent"
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(CONTENT))          # main.AppBridge

# ⚠ **必须在 import main 之前**把 backend/libs 塞进 LD_LIBRARY_PATH：
#   main.py 在**模块级**会自检缺不缺这个目录，缺了就直接 `os.execv` 重启整个进程
#   （AGENTS 里"不要绕过 main.py 解释器自检"说的就是它）。导入它就等于让本工具
#   白白重启一次，而且重启后命令行参数/输出都会重来一遍，很难看懂。
_libs = ROOT / "backend" / "libs"
if _libs.is_dir() and str(_libs) not in os.environ.get("LD_LIBRARY_PATH", "").split(":"):
    _old = os.environ.get("LD_LIBRARY_PATH", "")
    os.environ["LD_LIBRARY_PATH"] = str(_libs) + ((":" + _old) if _old else "")

PAGES = {
    "camera":   "CameraPage.qml",
    "light":    "LightPage.qml",
    "comm":     "CommPage.qml",
    "collect":  "CollectPage.qml",
    "settings": "SettingsPage.qml",
    "detect":   "DetectPage.qml",
    "stage":    "StagePage.qml",
}

HARNESS = b"""
import QtQuick
import "pages"

Window {
    id: win
    width: %WIDTH%
    height: %HEIGHT%
    visible: true
    %PAGE% {
        objectName: "pageUnderTest"
        anchors.fill: parent
    }
}
"""


def build_context(engine, app):
    """注册真实桥。没有硬件时它们自己降级，页面照常渲染（全部显示"未连接"）。"""
    from PySide6.QtCore import QSettings
    from main import AppBridge
    from Src.camera_bridge import CameraBridge
    from Src.light_bridge import LightBridge
    from Src.mqtt_bridge import MqttBridge
    from Src.collect_bridge import CollectBridge
    from Src.algorithm_bridge import AlgorithmBridge
    from Src.stage_bridge import StageBridge
    from Src.zstage_bridge import ZStageBridge
    from Src.proto_hub import ProtoHub
    from Src.realtime_detect_bridge import RealtimeDetectBridge

    # ⚠ 必须留 Python 引用：context property **不增加引用计数**，临时对象会被 GC
    #   → QML 侧读到 null（这条踩过多次，见 AGENTS）。
    keep = {}
    sdir = Path(tempfile.mkdtemp(prefix="render_pages_"))
    autosave = QSettings(str(sdir / "auto.ini"), QSettings.Format.IniFormat)

    # ⚠ AppBridge 要 (app, engine)：它自己管 QTranslator 与 QSettings
    keep["AppBridge"] = AppBridge(app, engine)
    keep["CameraBridge"] = CameraBridge()
    keep["LightBridge"] = LightBridge()
    keep["MqttBridge"] = MqttBridge()
    keep["CollectBridge"] = CollectBridge(keep["CameraBridge"])
    keep["AlgorithmBridge"] = AlgorithmBridge()
    keep["StageBridge"] = StageBridge(settings=QSettings(str(sdir / "xy.ini"),
                                                         QSettings.Format.IniFormat))
    keep["ZStageBridge"] = ZStageBridge(settings=QSettings(str(sdir / "z.ini"),
                                                           QSettings.Format.IniFormat))
    keep["ProtoHub"] = ProtoHub({"xy": keep["StageBridge"], "z": keep["ZStageBridge"]})
    # DetectPage 还要 DetectBridge（帧 → 推理 → 热力图 provider）
    keep["DetectBridge"] = RealtimeDetectBridge(
        keep["CameraBridge"], keep["AlgorithmBridge"], keep["CameraBridge"].frameProvider)

    ctx = engine.rootContext()
    for k, v in keep.items():
        ctx.setContextProperty(k, v)
    return keep, autosave


def main():
    ap = argparse.ArgumentParser(description="渲染任意页面以便目检")
    ap.add_argument("page", choices=sorted(PAGES), help="要渲染哪个页面")
    ap.add_argument("out", nargs="?", default=None, help="输出 PNG")
    ap.add_argument("width", nargs="?", type=int, default=1440)
    ap.add_argument("height", nargs="?", type=int, default=980)
    args = ap.parse_args()
    out = args.out or f"/tmp/{args.page}.png"

    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickWindow        # noqa: F401  grabWindow 需要

    app = QGuiApplication(sys.argv[:1])
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(CONTENT))
    warnings = []
    engine.warnings.connect(lambda ws: warnings.extend(w.toString() for w in ws))

    keep, autosave = build_context(engine, app)     # noqa: F841  (keep 防 GC)

    src = (HARNESS.replace(b"%WIDTH%", str(args.width).encode())
                  .replace(b"%HEIGHT%", str(args.height).encode())
                  .replace(b"%PAGE%", PAGES[args.page].removesuffix(".qml").encode()))
    engine.loadData(src, QUrl.fromLocalFile(str(CONTENT / "_render_any.qml")))

    end = time.time() + 1.2
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)

    if not engine.rootObjects():
        print(f"页面加载失败：{args.page}", file=sys.stderr)
        for w in warnings:
            print("  " + w, file=sys.stderr)
        return 1

    win = engine.rootObjects()[0]
    # ⚠ 必须在加载**之后**再显式设一次尺寸：offscreen 平台会把 QML 里写的
    #   width/height 改掉（实测请求 1440×980 拿到 945×1035，且与请求值无关）。
    #   tests/test_stage_page.py 里那句 `win.setProperty("width", …)` 是同一个原因。
    win.setProperty("width", args.width)
    win.setProperty("height", args.height)
    end = time.time() + 0.3
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    img = QQuickWindow.grabWindow(win)
    img.save(out)
    print(f"已保存 {out}  {img.width()}x{img.height()}")

    NOISE = ("Invalid image provider", "overrides a member of the base object",
             "is not a valid import URL")
    real = [w for w in warnings if not any(n in w for n in NOISE)]
    print(f"=== QML 警告 === 共 {len(warnings)} 条，真实错误 {len(real)} 条")
    for w in real:
        print("  ✗ " + w)
    return 1 if real else 0


if __name__ == "__main__":
    sys.exit(main())
