#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""设计令牌守卫 —— 颜色"看不看得清"用数字说话，不靠肉眼。

2026-10-08 UI 评审的结论：原强调色 #aee9e7 在白底上对比度只有 ~1.4:1，
「选中的步长」「当前导航项」「主操作」彼此分不出主次。本轮引入实色强调 `accent`，
本文件把"看得清"钉成 WCAG 对比度下限，**对每套配色 × 亮/暗主题逐一检查** ——
以后有人新增配色或调色，挑了个好看但读不清的颜色，这里会直接报出来。

用法：
    source DuAD_SoftwareContent/pyqml/bin/activate
    QT_QPA_PLATFORM=offscreen python3 -u tests/test_design_tokens.py
"""

import os
import sys
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "DuAD_SoftwareContent"

from PySide6.QtCore import QUrl                                 # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication               # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine                 # noqa: E402

PRESETS = ["default", "ocean", "forest", "sunset"]
THEMES = ["light", "dark"]

# (前景令牌, 背景令牌, 下限, 为什么)
PAIRS = [
    # accentContent 而不是 textOnAccent：后者是急停红上的白字、永远是白；
    # 实色强调在暗色主题下是**亮底配深字**，两者语义不同，分开两个令牌
    ("accentContent",      "accent", 4.5, "主按钮 / 选中步长上的文字"),
    ("accent",        "cardBg", 3.0, "强调色描边、强调色图形在卡片上（非文字 3:1）"),
    ("accentText",    "cardBg", 4.5, "强调色文字（导航选中项、链接）"),
    ("accentText",    "accentSoft", 4.5, "导航选中块里的强调文字"),
    ("textSecondary", "cardBg", 4.5, "次要文字"),
    ("textPrimary",   "pageBg", 7.0, "正文"),
]

QML = b"""
import QtQuick
import DuAD_Software
QtObject {
    property var colors: Colors
}
"""


def _lum(c: QColor) -> float:
    def ch(v):
        v = v / 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(c.red()) + 0.7152 * ch(c.green()) + 0.0722 * ch(c.blue())


def contrast(a: QColor, b: QColor) -> float:
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def main():
    app = QGuiApplication(sys.argv[:1])
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(CONTENT))
    engine.loadData(QML, QUrl.fromLocalFile(str(CONTENT / "_tokens.qml")))
    roots = engine.rootObjects()
    if not roots:
        print("✗ Colors 单例加载失败")
        return 1
    root = roots[0]
    colors = root.property("colors")
    # 动画会让读数停在半路，测试只关心终值
    colors.setProperty("animDuration", 0)

    fails = []
    for theme in THEMES:
        for preset in PRESETS:
            colors.setTheme(theme)
            colors.setPreset(preset)
            app.processEvents()
            for fg, bg, floor, why in PAIRS:
                cf, cb = colors.property(fg), colors.property(bg)
                if not isinstance(cf, QColor) or not isinstance(cb, QColor):
                    fails.append(f"[{theme}/{preset}] 令牌缺失：{fg} 或 {bg}")
                    continue
                ratio = contrast(cf, cb)
                ok = ratio >= floor
                mark = "✓" if ok else "✗"
                print(f"  {mark} [{theme:5}/{preset:7}] {fg} on {bg}: "
                      f"{ratio:4.2f} (≥{floor})  {why}")
                if not ok:
                    fails.append(f"[{theme}/{preset}] {fg}({cf.name()}) on "
                                 f"{bg}({cb.name()}) = {ratio:.2f} < {floor}：{why}")

    print()
    if fails:
        print("设计令牌守卫 失败 %d 条：" % len(fails))
        for f in fails:
            print("   ✗ " + f)
        return 1
    print("设计令牌守卫 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
