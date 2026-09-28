# -*- coding: utf-8 -*-
"""协议显示框的**记录器** —— 两块板子共用同一套"封顶 / 节流 / 暂停"语义。

为什么抽出来（2026-09-28）：用户要求"协议显示改成公用的"——
他只有一块板子在调，插哪块就想看哪块的指令，界面上不该有两套协议框。
于是每块板子的桥各持一个 `ProtoLog`，再由 `ProtoHub` 把两路按**到达顺序**
汇成一条（带 `[XY]` / `[Z]` 前缀），界面只画一个框。

⚠ 两条本项目的硬约定，挪进来时逐字保留：

1. **封顶**（`PROTO_CAP`）：开着驱动器帧镜像时一行接一行，不封顶会吃光内存。
2. **发射节流**（`PROTO_EMIT_MS`）：每来一行都 emit 的话，开着镜像时每秒上百次
   列表重建会把 GUI 线程打满，而 GUI 不读 socket 会让固件的 `send()` 阻塞到超时 ——
   **显示太勤会反过来伤害固件**。所以攒一批再通知界面。
   ⚠ `lineAdded` 是**逐行**发的（给 `ProtoHub` 汇总用，只做列表 append，很便宜），
   节流只加在"通知界面刷新"的 `changed` 上。
"""

from __future__ import annotations

from typing import List

from PySide6.QtCore import QObject, QTimer, Signal

PROTO_CAP = 400          # 单个来源最多留多少行
PROTO_EMIT_MS = 80       # 通知界面的最小间隔（毫秒）


class ProtoLog(QObject):
    """某一块板子的协议行记录。"""

    changed = Signal()          # 节流后的"该刷新了"（界面读 lines）
    lineAdded = Signal(str, str)  # (tag, text) —— 逐行，给公用协议框汇总

    def __init__(self, tag: str, parent: QObject | None = None):
        super().__init__(parent)
        self._tag = tag
        self._lines: List[str] = []
        self._paused = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(PROTO_EMIT_MS)
        self._timer.timeout.connect(self.changed.emit)

    # ── 只读状态 ──────────────────────────────────────────────
    @property
    def tag(self) -> str:
        return self._tag

    @property
    def lines(self) -> List[str]:
        return list(self._lines)

    @property
    def paused(self) -> bool:
        return self._paused

    # ── 写入 ──────────────────────────────────────────────────
    def add(self, text: str) -> None:
        """追加一行。暂停时不追加（用户正盯着某一帧看）。"""
        if self._paused:
            return
        self._lines.append(text)
        if len(self._lines) > PROTO_CAP:
            del self._lines[: len(self._lines) - PROTO_CAP]
        self.lineAdded.emit(self._tag, text)
        if not self._timer.isActive():
            self._timer.start()

    def clear(self) -> None:
        self._lines = []
        self.changed.emit()

    def set_paused(self, paused: bool) -> None:
        self._paused = bool(paused)
        self.changed.emit()
