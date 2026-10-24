from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..mod_conflicts import ModDescriptor, ModIssue
from .dialogs import StyledDialog
from .widgets import badge, colors


class _IssueRow(QFrame):
    def __init__(self, issue: ModIssue, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SelectRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(11, 9, 11, 9)
        layout.setSpacing(10)

        color = {
            "error": "red",
            "warning": "orange",
            "info": "blue",
        }.get(issue.severity, "gray")
        layout.addWidget(badge(issue.badge, color), 0, Qt.AlignTop)

        texts = QVBoxLayout()
        texts.setSpacing(2)
        title = QLabel(issue.title)
        title.setStyleSheet("font-weight: 700; font-size: 12px;")
        texts.addWidget(title)
        message = QLabel(issue.message)
        message.setObjectName("Tiny")
        message.setWordWrap(True)
        message.setTextInteractionFlags(Qt.TextSelectableByMouse)
        texts.addWidget(message)
        if issue.filename:
            names = "、".join(
                item for item in [issue.filename, *issue.related] if item
            )
            files = QLabel(names)
            files.setObjectName("Tiny")
            files.setWordWrap(True)
            files.setStyleSheet(f"color: {colors()['muted']};")
            texts.addWidget(files)
        layout.addLayout(texts, 1)


class ModHealthDialog(StyledDialog):
    def __init__(
        self,
        parent,
        issues: list[ModIssue],
        descriptors: list[ModDescriptor] | None = None,
    ) -> None:
        errors = sum(1 for item in issues if item.severity == "error")
        warnings = sum(1 for item in issues if item.severity == "warning")
        title = (
            f"环境检查：{errors} 个错误 / {warnings} 个警告"
            if issues
            else "环境检查：没有发现问题"
        )
        super().__init__(
            parent,
            title,
            "warning" if issues else "check",
            colors()["red"] if errors else colors()["green"],
            width=720,
        )
        self.issues = list(issues)
        self.descriptors = list(descriptors or [])
        intro = QLabel(
            "检查范围：加载器混装、重复 modId、必需前置、前置版本区间和 Mod 声明的冲突。"
            "结果只作提示，不会自动禁用或删除文件。"
        )
        intro.setObjectName("Muted")
        intro.setWordWrap(True)
        self.body.addWidget(intro)

        if not issues:
            empty = QLabel("当前 mods 文件夹没有检测到明显问题，猫猫盖章。")
            empty.setObjectName("Muted")
            empty.setAlignment(Qt.AlignCenter)
            empty.setMinimumHeight(160)
            self.body.addWidget(empty, 1)
        else:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setMinimumHeight(300)
            scroll.setMaximumHeight(460)
            holder = QWidget()
            layout = QVBoxLayout(holder)
            layout.setContentsMargins(0, 0, 4, 0)
            layout.setSpacing(5)
            for issue in issues:
                layout.addWidget(_IssueRow(issue, holder))
            layout.addStretch(1)
            scroll.setWidget(holder)
            self.body.addWidget(scroll, 1)

        self.add_button("复制检查报告", "Secondary", self._copy)
        self.add_button("关闭", "Secondary", self.reject)

    def _copy(self) -> None:
        lines = ["Darkitten Launcher Mod 环境检查"]
        if not self.issues:
            lines.append("没有发现问题。")
        for issue in self.issues:
            lines.append(f"[{issue.badge}] {issue.title}")
            lines.append(f"  {issue.message}")
            if issue.filename:
                lines.append(f"  文件：{issue.filename}")
        QApplication.clipboard().setText("\n".join(lines))
