#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全站 UI 主题守卫 —— 每个页面都要过这两条：

  1. **按钮必须主题化**：页面上任何 `Button` 都得是用 `Colors` 画的
     （`ThemedButton`，或白名单里有明确理由的共享组件）。没写 `background` 的
     Button 会套上 Qt Quick Controls 当前 style（本项目强制 Fusion）的默认外观：
     灰底 + 深灰渐变，**完全不吃 `Colors`** —— 换暗色主题/换配色时纹丝不动
     （2026-09-28 用户截图点名的问题，详见 docs/19 §33）。
  2. **图标必须真的存在**：`IconImage` 内部是 `Image` + `ColorOverlay`，源文件找不到时
     **不报错、不警告、什么都不画**（docs/19 §22.1-3）。所以把 `source` 解析成文件路径
     查存在，比一张张截图去盯可靠得多。

为什么值得单开一个测试文件：这两类问题都是**静默**的，而且刚才这一轮美化把它们
从一页扩散到了七页。改完任何页面的 UI 都该跑一次本文件。

用法：
    source DuAD_SoftwareContent/pyqml/bin/activate
    QT_QPA_PLATFORM=offscreen python3 -u tests/test_ui_theme.py
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

from render_pages import PAGES, build_context                   # noqa: E402
from test_stage_page import Runner                              # noqa: E402


# ── 按类型名放行：它们本来就用 Colors 画好了，只是不叫 ThemedButton ──
ALLOW_CLASS = {
    "ThemedButton": "本项目的标准按钮",
    "AnimatedRefreshButton": "齿轮按钮，悬停底色来自 Colors",
    "QQuickIndicatorButton": "滚动条滑块（Qt 内部类型），外观已在页面里重画",
    # DetectPage 图卡标题栏那三个小方按钮（ROI / 刷新 / 全屏）：
    # 28×28、带 checked 态，是专门画的一类，不是漏了 background
    "HeaderIconButton": "DetectPage 图卡标题栏的小方按钮，底色来自 Colors",
}
# ── 按祖先组件名放行：共享组件内部的 Button，均已用 Colors 画好 ──
ALLOW_ANCESTOR = {
    "SliderRow": "滑块行末尾的 ↺ 重置按钮",
    "SwitchRow": "pill 开关",
}


def _walk_items(root):
    """递归可视树。

    ⚠ 必须容错：页面里的 Repeater/ListView delegate 会在遍历过程中被销毁，
      PySide 拿到的是**已析构的 C++ 对象**，再读 metaObject() 就抛
      `RuntimeError: Internal C++ object already deleted`。跳过即可，不影响判定。
    """
    try:
        yield root
        children = root.childItems()
    except RuntimeError:
        return
    for c in children:
        yield from _walk_items(c)


def _icon_sources(page):
    out = []
    for it in _walk_items(page):
        try:
            if "IconImage" not in it.metaObject().className():
                continue
            u = it.property("source")
        except RuntimeError:
            continue
        # ⚠ property() 拿回来是 QUrl 对象，`str(u)` 是 repr，必须 toString()
        s = u.toString() if hasattr(u, "toString") else str(u or "")
        if s:
            out.append(s)
    return out


def _ancestor_classes(it):
    out, p = set(), it.parent()
    while p is not None:
        out.add(p.metaObject().className())
        p = p.parent()
    return out


def main():
    app = QGuiApplication(sys.argv[:1])
    r = Runner(app)

    for page_name, qml_file in sorted(PAGES.items()):
        print(f"=== {page_name}（{qml_file}）===")
        engine = QQmlApplicationEngine()
        engine.addImportPath(str(CONTENT))
        warnings = []
        engine.warnings.connect(lambda ws: warnings.extend(w.toString() for w in ws))
        keep, _ = build_context(engine, app)        # noqa: F841  (防 GC)

        src = ('import QtQuick\nimport "pages"\n'
               'Window { width: 1600; height: 1000; visible: true\n'
               f'    {qml_file.removesuffix(".qml")} {{ objectName: "pageUnderTest"; anchors.fill: parent }}\n'
               '}\n').encode()
        engine.loadData(src, QUrl.fromLocalFile(str(CONTENT / "_uitheme.qml")))
        end = time.time() + 0.8
        while time.time() < end:
            app.processEvents()
            time.sleep(0.01)

        if not engine.rootObjects():
            r.check(f"{page_name}：页面能加载", False, "；".join(warnings[:3]))
            continue
        # ⚠ 必须把窗口**存进变量**：`engine.rootObjects()[0]` 直接取下标用临时值的话，
        #   PySide 给回的 wrapper 归 Python 所有，下一行就可能已被回收 ——
        #   表现为 `RuntimeError: Internal C++ object already deleted`，
        #   而且是在**遍历子项**时才炸，看着像"子项被销毁"，其实根都没了。
        win = engine.rootObjects()[0]
        page = win.findChild(QObject, "pageUnderTest")
        if page is None:
            # 有的页面根不是 Item（比如 Window 里的 Loader），退一步用窗口本身
            page = win

        # ── 1) 图标源文件存在 ──
        srcs = sorted(set(_icon_sources(page)))
        missing = []
        for u in srcs:
            path = QUrl(u).toLocalFile() if u.startswith("file:") else u
            if not Path(path).is_file():
                missing.append(f"{u} → {path}")
        r.check(f"{page_name}：{len(srcs)} 个图标源文件都存在", not missing,
                "；".join(missing))

        # ── 2) 按钮全部主题化 ──
        stock, themed = [], 0
        for it in page.findChildren(QObject):
            try:
                cls = it.metaObject().className()
                if "Button" not in cls:
                    continue
                if any(k in cls for k in ALLOW_CLASS):
                    themed += 1
                    continue
                if any(k in a for a in _ancestor_classes(it) for k in ALLOW_ANCESTOR):
                    continue
                stock.append(f"{cls}(objectName={it.property('objectName')!r} "
                             f"text={it.property('text')!r})")
            except RuntimeError:
                continue
        r.check(f"{page_name}：按钮全部主题化（已主题化/已放行 {themed} 个）",
                not stock, "；".join(sorted(set(stock))))

        # 引擎留着（keep 里的桥是它创建的）；不强杀，交给进程退出
        engine.deleteLater()

    print()
    if r.fails:
        print("全站 UI 主题守卫 失败 %d 条：" % len(r.fails))
        for f in r.fails:
            print("   ✗ " + f)
        return 1
    print("全站 UI 主题守卫 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
