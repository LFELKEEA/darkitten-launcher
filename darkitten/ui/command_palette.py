from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
)

from .dialogs import StyledDialog
from .widgets import colors


class CommandPalette(StyledDialog):
    """Ctrl+K 快捷命令面板。"""

    def __init__(
        self,
        parent,
        commands: Iterable[dict],
    ) -> None:
        super().__init__(parent, "快捷命令", "search", width=540)
        self.commands = [dict(item) for item in commands]
        self.chosen = ""

        self.input = QLineEdit()
        self.input.setPlaceholderText("输入命令，例如：下载 / 设置 / 快照 / 诊断")
        self.input.setClearButtonEnabled(True)
        self.input.textChanged.connect(self._apply_filter)
        self.input.returnPressed.connect(self._activate_current)
        self.input.installEventFilter(self)
        self.body.addWidget(self.input)

        self.list = QListWidget()
        self.list.setMinimumHeight(300)
        self.list.setFocusPolicy(Qt.NoFocus)
        self.list.setStyleSheet(
            "QListWidget {"
            " background: transparent; border: none; outline: none; }"
            "QListWidget::item {"
            " padding: 9px 12px; border-radius: 8px; }"
            f"QListWidget::item:selected {{ background: {colors()['accent_soft']};"
            f" color: {colors()['text']}; }}"
        )
        self.list.itemClicked.connect(lambda _item: self._activate_current())
        self.body.addWidget(self.list)

        hint = QLabel("↑ ↓ 选择 · Enter 执行 · Esc 关闭")
        hint.setObjectName("Tiny")
        self.body.addWidget(hint)

        self._reload()
        self.add_button("关闭", "Secondary", self.reject)

    # ------------------------------------------------------------------
    def _reload(self) -> None:
        self.list.clear()
        for command in self.commands:
            item = QListWidgetItem(
                f"{command.get('title', '')}   ·   {command.get('subtitle', '')}"
            )
            item.setData(Qt.UserRole, str(command.get("id") or ""))
            item.setToolTip(str(command.get("keywords") or ""))
            self.list.addItem(item)
        for row in range(self.list.count()):
            self.list.item(row).setHidden(False)
        if self.list.count():
            self.list.setCurrentRow(0)
        self._empty_hint()

    def _empty_hint(self) -> None:
        visible = any(
            not self.list.item(row).isHidden() for row in range(self.list.count())
        )
        if not visible and self.list.count():
            self.list.setCurrentRow(-1)

    def _matches(self, item: QListWidgetItem, query: str) -> bool:
        text = f"{item.text()} {item.toolTip()}".casefold()
        return all(word in text for word in query.split())

    def _apply_filter(self, text: str) -> None:
        query = str(text or "").strip().casefold()
        first_visible = -1
        for row in range(self.list.count()):
            item = self.list.item(row)
            visible = not query or self._matches(item, query)
            item.setHidden(not visible)
            if visible and first_visible < 0:
                first_visible = row
        if first_visible >= 0:
            self.list.setCurrentRow(first_visible)
        else:
            self.list.setCurrentRow(-1)
        self._empty_hint()

    def _move(self, delta: int) -> None:
        count = self.list.count()
        if not count:
            return
        current = self.list.currentRow()
        visible = [
            row for row in range(count) if not self.list.item(row).isHidden()
        ]
        if not visible:
            return
        if current not in visible:
            self.list.setCurrentRow(visible[0])
            return
        index = visible.index(current)
        self.list.setCurrentRow(visible[(index + delta) % len(visible)])

    def _activate_current(self) -> None:
        item = self.list.currentItem()
        if item is None or item.isHidden():
            visible = [
                self.list.item(row)
                for row in range(self.list.count())
                if not self.list.item(row).isHidden()
            ]
            if not visible:
                return
            item = visible[0]
        self.chosen = str(item.data(Qt.UserRole) or "")
        if self.chosen:
            self.accept()

    def eventFilter(self, obj, event) -> bool:
        if obj is self.input and event.type() == QEvent.KeyPress:
            key = event.key()
            if key == Qt.Key_Down:
                self._move(1)
                return True
            if key == Qt.Key_Up:
                self._move(-1)
                return True
            if key == Qt.Key_Escape:
                self.reject()
                return True
        return super().eventFilter(obj, event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.input.setFocus(Qt.OtherFocusReason)
