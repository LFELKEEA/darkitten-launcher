from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..mod_updates import ModUpdate
from .dialogs import StyledDialog
from .widgets import badge, colors


class _UpdateRow(QFrame):
    def __init__(self, update: ModUpdate, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.update = update
        self.setObjectName("SelectRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(10)

        self.check = QCheckBox()
        self.check.setChecked(True)
        self.check.setToolTip("选中后会更新这个 Mod")
        layout.addWidget(self.check)

        texts = QVBoxLayout()
        texts.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        name = QLabel(update.mod.name)
        name.setStyleSheet("font-weight: 700; font-size: 12px;")
        title_row.addWidget(name)
        title_row.addWidget(badge("可更新", "green"))
        title_row.addWidget(badge(update.source_label, "purple"))
        title_row.addStretch(1)
        texts.addLayout(title_row)

        current = update.mod.version or "未知版本"
        version = QLabel(f"{current}  →  {update.version_label}")
        version.setObjectName("Tiny")
        version.setWordWrap(True)
        texts.addWidget(version)

        meta = QLabel(f"{update.file_name} · {update.size_text}")
        meta.setObjectName("Tiny")
        meta.setToolTip(update.file_name)
        texts.addWidget(meta)

        if update.dependencies:
            dep = QLabel(
                f"该版本声明了 {len(update.dependencies)} 个必需前置，"
                "更新后如启动报缺前置，请到下载页补齐。"
            )
            dep.setObjectName("Tiny")
            dep.setWordWrap(True)
            dep.setStyleSheet(f"color: {colors()['orange']};")
            texts.addWidget(dep)
        layout.addLayout(texts, 1)

        if update.page_url:
            from . import icons

            from PySide6.QtWidgets import QPushButton

            open_btn = QPushButton("项目页")
            open_btn.setObjectName("Ghost")
            open_btn.setCursor(Qt.PointingHandCursor)
            open_btn.setIcon(icons.icon("external", colors()["text2"], 13))
            open_btn.setToolTip(update.page_url)
            open_btn.clicked.connect(lambda: _open_url(update.page_url))
            layout.addWidget(open_btn)


def _open_url(url: str) -> None:
    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl(str(url)))
    except Exception:
        pass


class ModUpdatesDialog(StyledDialog):
    def __init__(self, parent, updates: list[ModUpdate]) -> None:
        super().__init__(
            parent,
            f"发现 {len(updates)} 个 Mod 更新",
            "download",
            colors()["green"],
            width=700,
        )
        self.updates = list(updates)
        self.selected: list[ModUpdate] = []
        self._rows: list[_UpdateRow] = []

        intro = QLabel(
            "只显示适配当前游戏版本与加载器的文件。更新前会把旧文件备份到 "
            "mods/.dkl-meta/backups，不会直接删除。"
        )
        intro.setObjectName("Muted")
        intro.setWordWrap(True)
        self.body.addWidget(intro)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setMinimumHeight(300)
        scroll.setMaximumHeight(440)
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(5)
        for update in self.updates:
            row = _UpdateRow(update, holder)
            self._rows.append(row)
            layout.addWidget(row)
        layout.addStretch(1)
        scroll.setWidget(holder)
        self.body.addWidget(scroll, 1)

        self.summary = QLabel("")
        self.summary.setObjectName("Tiny")
        self.body.addWidget(self.summary)

        self.update_btn = self.add_button("全部更新", "Primary", self._confirm)
        self.add_button("取消", "Secondary", self.reject)
        for row in self._rows:
            row.check.toggled.connect(self._refresh_summary)
        self._refresh_summary()

    def _checked(self) -> list[ModUpdate]:
        return [
            row.update
            for row in self._rows
            if row.check.isChecked()
        ]

    def _refresh_summary(self) -> None:
        count = len(self._checked())
        self.summary.setText(f"已选择 {count} / {len(self.updates)} 个 Mod")
        self.update_btn.setEnabled(count > 0)
        self.update_btn.setText(f"更新选中 ({count})" if count > 0 else "更新选中")

    def _confirm(self) -> None:
        selected = self._checked()
        if not selected:
            return
        self.selected = selected
        self.accept()
