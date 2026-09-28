# -*- coding: utf-8 -*-
"""公用协议显示框：把两块板子的协议流汇成**一条**。

用户原话（2026-09-28）："板子的协议显示修改为公用的，因为我只要将 usb 线接到不同的
板子上，这个就会有对应的指令。" —— 两块板子跑的是**同一套行协议**（见 docs/18 §4），
所以界面上只需要一个框：哪块板子在说话，就带前缀出现在里面。

设计取舍：

- **不做"选一块看一块"**，而是两路合并 + `[XY]` / `[Z]` 前缀。
  理由很实际：两块板子可以同时连着，出问题时最想看的恰恰是"谁先答的、谁没答"——
  分成两个框看就没法对齐时间线了。
- **暂停/清空是两路一起**（`setPaused` / `clear`）：用户按暂停是想"冻住这一刻"，
  只冻一路没有意义。
- **发送是带目标的**（`sendCommand(source, text)`）：命令总得有个去处，
  默认跟着上面那个来源选择器走（界面负责给 source）。

⚠ 合并后的上限 `HUB_CAP` 比单路大：两路各 `PROTO_CAP` 行时，合并列表不能只剩一半。
"""

from __future__ import annotations

from typing import Dict, List

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot

from Src.proto_log import PROTO_EMIT_MS

# 合并后的封顶：两路都可能各留满 PROTO_CAP，取 2 倍再收一点余量
HUB_CAP = 800


class ProtoHub(QObject):
    """把若干个 `ProtoLog`（每块板子一个）汇成一条带前缀的流。"""

    changed = Signal()

    def __init__(self, bridges: Dict[str, object], parent: QObject | None = None):
        """`bridges`: {来源键: 桥对象}，桥对象要有 `.proto_log`。

        ⚠ 用**有序字典**：界面上的来源顺序（先二轴、后 Z）就是这里的插入顺序。
        """
        super().__init__(parent)
        self._bridges: Dict[str, object] = dict(bridges)
        self._lines: List[str] = []
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(PROTO_EMIT_MS)
        self._timer.timeout.connect(self.changed.emit)
        for br in self._bridges.values():
            br.proto_log.lineAdded.connect(self._on_line)

    # ── 汇总 ──────────────────────────────────────────────────
    @Slot(str, str)
    def _on_line(self, tag: str, text: str) -> None:
        self._lines.append(f"[{tag}] {text}")
        if len(self._lines) > HUB_CAP:
            del self._lines[: len(self._lines) - HUB_CAP]
        if not self._timer.isActive():
            self._timer.start()

    # ── 给界面读的属性（都带 notify）──────────────────────────
    lines = Property("QVariantList", lambda self: list(self._lines), notify=changed)
    sources = Property("QVariantList", lambda self: list(self._bridges.keys()),
                       notify=changed)
    # 任一路暂停就算暂停（界面上的开关是两路一起控制的，见文件头）
    paused = Property(bool, lambda self: any(br.proto_log.paused
                                             for br in self._bridges.values()),
                      notify=changed)

    # ── 操作 ──────────────────────────────────────────────────
    @Slot(bool)
    def setPaused(self, paused: bool) -> None:
        for br in self._bridges.values():
            br.proto_log.set_paused(bool(paused))
        self.changed.emit()

    @Slot()
    def clear(self) -> None:
        for br in self._bridges.values():
            br.proto_log.clear()
        self._lines = []
        self.changed.emit()

    @Slot(str, str, result=bool)
    def sendCommand(self, source: str, text: str) -> bool:
        """把一行自定义命令发给指定的那块板子。

        ⚠ 来源键不认识时**返回 False 并说清楚**，不静默丢弃 ——
        "点了没反应"是本项目最忌讳的形态。
        """
        br = self._bridges.get(source)
        if br is None:
            return False
        return bool(br.sendCommand(text))
