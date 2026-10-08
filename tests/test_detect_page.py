#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DetectPage 结论卡 —— 「正常 / 异常 / 待机」三态。

2026-10-08 评审发现：没连相机、没跑推理时，右下角照样亮着绿色「正常」
（分数默认 0 < 阈值 1.7）。这和全站规矩"状态未知不许画成绿色"冲突
（ThemedButton 文件头：没连上时显示绿色等于撒谎）。本测试钉住：

  1. 没有结果来源（无测试结果、未实时推理）→ 「待机」
  2. 有结果且 分数 ≤ 阈值 → 「正常」
  3. 有结果且 分数 >  阈值 → 「异常」
  4. 测试推理进行中 → 「待机」（结果还没回来，不能沿用上一张图的结论）

用法：
    source DuAD_SoftwareContent/pyqml/bin/activate
    QT_QPA_PLATFORM=offscreen python3 -u tests/test_detect_page.py
"""

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QML_COMPAT_RESOLVE_URLS_ON_ASSIGNMENT", "1")
os.environ["QT_QPA_PLATFORM"] = "offscreen"

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "DuAD_SoftwareContent"
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(CONTENT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import QObject, QUrl                        # noqa: E402
from PySide6.QtGui import QGuiApplication                       # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine                 # noqa: E402

from render_pages import build_context                          # noqa: E402
from test_stage_page import Runner                              # noqa: E402


def pump(app, sec=0.2):
    end = time.time() + sec
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def main():
    app = QGuiApplication(sys.argv[:1])
    r = Runner(app)
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(CONTENT))
    keep, _ = build_context(engine, app)        # noqa: F841  (防 GC)
    src = (b'import QtQuick\nimport "pages"\n'
           b'Window { width: 1440; height: 900; visible: true\n'
           b'    DetectPage { objectName: "page"; anchors.fill: parent }\n}\n')
    engine.loadData(src, QUrl.fromLocalFile(str(CONTENT / "_detect_test.qml")))
    pump(app, 0.8)
    win = engine.rootObjects()[0]               # ⚠ 存变量防 wrapper 被回收
    page = win.findChild(QObject, "page")
    card = win.findChild(QObject, "detectVerdict")
    r.check("结论卡存在（objectName=detectVerdict）", card is not None)
    if card is None:
        return 1

    thr = keep["AlgorithmBridge"].property("threshold")
    print(f"      当前阈值 = {thr}")

    r.check("1) 无结果来源 → 待机", card.property("verdict") == "idle",
            f"verdict={card.property('verdict')!r}")

    page.setProperty("_testActive", True)
    page.setProperty("_inferring", False)
    page.setProperty("_score", thr - 1.0)
    pump(app)
    r.check("2) 有结果且分数 ≤ 阈值 → 正常", card.property("verdict") == "normal",
            f"verdict={card.property('verdict')!r}")

    page.setProperty("_score", thr + 1.0)
    pump(app)
    r.check("3) 有结果且分数 > 阈值 → 异常", card.property("verdict") == "anomaly",
            f"verdict={card.property('verdict')!r}")

    page.setProperty("_inferring", True)
    pump(app)
    r.check("4) 测试推理进行中 → 待机", card.property("verdict") == "idle",
            f"verdict={card.property('verdict')!r}")

    print()
    if r.fails:
        print("DetectPage 结论卡 失败 %d 条：" % len(r.fails))
        for f in r.fails:
            print("   ✗ " + f)
        return 1
    print("DetectPage 结论卡 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
