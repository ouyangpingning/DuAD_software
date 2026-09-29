#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 `images/*.svg` 全部渲染成一张接触表，用来**目检图标本身**。

为什么需要它：页面截图（`render_page.py`）**能**看到图标，但那是"图标在按钮里长什么样"，
看不清**图标本身**——形状、粗细、四边留白、和同目录其它图标是否同一套。
本脚本用 `QSvgRenderer` 直接把 `images/*.svg` 排成接触表，绕开 QML，
最省事也最不受环境（后端/DPI）影响。用户新给一批 SVG 之后，先跑一次它。

用法：
    source DuAD_SoftwareContent/pyqml/bin/activate
    python3 tests/render_icons.py [输出.png]
"""

import sys
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parent.parent
IMAGES = ROOT / "DuAD_SoftwareContent" / "images"

COLS = 6        # 每行几个
BOX = 120       # 图标框边长
PAD = 16        # 框间距
LABEL_H = 26    # 每个框下面的文件名 + viewBox


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "icon_sheet.png")
    app = QGuiApplication(sys.argv[:1])          # noqa: F841  (Qt 需要先建应用)

    files = sorted(IMAGES.glob("*.svg"))
    if not files:
        print(f"没找到图标：{IMAGES}", file=sys.stderr)
        return 1

    rows = (len(files) + COLS - 1) // COLS
    img = QImage(COLS * (BOX + PAD) + PAD, rows * (BOX + PAD + LABEL_H) + PAD,
                 QImage.Format_ARGB32)
    img.fill(QColor("#eaf4f7"))

    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    font = QFont()
    font.setPixelSize(11)
    p.setFont(font)

    for i, path in enumerate(files):
        r, c = divmod(i, COLS)
        x = PAD + c * (BOX + PAD)
        y = PAD + r * (BOX + PAD + LABEL_H)
        p.fillRect(x, y, BOX, BOX, QColor("#ffffff"))

        renderer = QSvgRenderer(str(path))
        vb = renderer.viewBoxF()
        ar = (vb.width() / vb.height()) if vb.height() else 1.0
        w = BOX - 20
        h = w / ar
        if h > BOX - 20:
            h = BOX - 20
            w = h * ar
        renderer.render(p, QRectF(x + (BOX - w) / 2, y + (BOX - h) / 2, w, h))

        p.setPen(QColor("#212121"))
        # viewBox 也打出来：它不一致（1024 / 1040 / 1129 / 24）本身就是一个信号 ——
        # 说明图标来自不同图标库，光学大小和粗细多半也不一致。
        p.drawText(QRectF(x, y + BOX + 2, BOX, LABEL_H), Qt.AlignHCenter | Qt.AlignTop,
                   path.stem + f"\n{int(vb.width())}x{int(vb.height())}")
    p.end()

    img.save(out)
    print(f"已保存 {out}（{len(files)} 个图标）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
