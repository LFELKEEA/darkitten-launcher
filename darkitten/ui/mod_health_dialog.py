from __future__ import annotations

from pathlib import Path

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
from ..mod_repair import (
    RepairError,
    apply_repair,
    build_repair_plan,
    latest_repair,
)
from .dialogs import ConfirmDialog, NoticeDialog, StyledDialog
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
                dict.fromkeys(
                    item for item in [issue.filename, *issue.related] if item
                )
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
        *,
        mods_dir: Path | None = None,
        game_version: str = "",
        loader: str = "",
        version_id: str = "",
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
        self.mods_dir = Path(mods_dir) if mods_dir else None
        self.repair_backup = None

        scope = QHBoxLayout()
        scope.setSpacing(6)
        scope.addWidget(QLabel("检查对象"))
        version_text = version_id or game_version or "未选择版本"
        scope.addWidget(badge(version_text, "blue"))
        scope.addWidget(badge(loader or "原版", "gray"))
        enabled = sum(1 for item in self.descriptors if item.valid)
        scope.addWidget(badge(f"{enabled} 个已启用 Mod", "gray"))
        scope.addStretch(1)
        scope_widget = QWidget()
        scope_widget.setLayout(scope)
        self.body.addWidget(scope_widget)

        intro = QLabel(
            "读取每个 Mod 的加载器、modId、前置与冲突声明，并按当前版本的"
            "加载器和实际安装版本判断。检查本身不会改动文件；一键解决只把"
            "确认冲突的文件移入备份，不改动、不覆盖原文件。"
        )
        intro.setObjectName("Muted")
        intro.setWordWrap(True)
        self.body.addWidget(intro)

        self.plan = None
        if issues and self.mods_dir is not None:
            self.plan = build_repair_plan(
                self.mods_dir, self.descriptors, loader
            )
        if self.plan is not None and self.plan.actions:
            fixable = QLabel(
                f"自动修复：可移出 {len(self.plan.actions)} 个冲突文件并保留备份。"
            )
            fixable.setObjectName("Tiny")
            fixable.setStyleSheet(f"color: {colors()['text2']};")
            self.body.addWidget(fixable)
        if self.plan is not None and self.plan.skipped:
            skipped = QLabel(
                f"另有 {len(self.plan.skipped)} 项需要手动处理（缺前置、"
                "版本更新或需要取舍的冲突），会在修复后保留提示。"
            )
            skipped.setObjectName("Tiny")
            skipped.setWordWrap(True)
            skipped.setStyleSheet(f"color: {colors()['muted']};")
            self.body.addWidget(skipped)

        existing = latest_repair(self.mods_dir) if self.mods_dir else None
        if existing is not None:
            note = QLabel(
                f"已有一份修复备份（{existing.state}，"
                f"{len(existing.files)} 个文件）。启动游戏确认正常后可以在"
                " Mod 页面删除它。"
            )
            note.setObjectName("Tiny")
            note.setWordWrap(True)
            note.setStyleSheet(f"color: {colors()['muted']};")
            self.body.addWidget(note)

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

        if self.plan is not None and self.plan.actions:
            self.add_button(
                f"一键解决（{len(self.plan.actions)}）",
                "Primary",
                self._repair,
            )
        self.add_button("复制检查报告", "Secondary", self._copy)
        self.add_button("关闭", "Secondary", self.reject)

    def _repair(self) -> None:
        if self.plan is None or not self.plan.actions or self.mods_dir is None:
            return
        details = "\n".join(
            f"· {item.path.name}：{item.reason}"
            for item in self.plan.actions[:8]
        )
        if len(self.plan.actions) > 8:
            details += f"\n…另有 {len(self.plan.actions) - 8} 个文件"
        if self.plan.skipped:
            details += "\n\n需要手动处理：\n" + "\n".join(
                f"· {item}" for item in self.plan.skipped[:4]
            )
            if len(self.plan.skipped) > 4:
                details += f"\n…另有 {len(self.plan.skipped) - 4} 项"
        confirm = ConfirmDialog(
            self,
            "移出冲突文件并备份",
            "将把下面这些文件移到版本目录下的 .dkl-mod-backups，"
            "不会直接删除。启动游戏确认正常后，再回来删除备份；"
            "如果游戏仍然异常，可以一键还原。",
            primary_text="开始修复",
            icon_name="warning",
            details=details,
        )
        if not confirm.exec():
            return
        try:
            self.repair_backup = apply_repair(
                self.mods_dir, self.plan.actions
            )
        except (RepairError, OSError) as exc:
            NoticeDialog(
                self,
                "修复没有完成",
                f"移出文件时出错：{exc}\n\n已尝试把移出的文件放回原位。",
                button_text="知道了",
                icon_name="warning",
            ).exec()
            return
        NoticeDialog(
            self,
            "已移出冲突文件",
            f"{len(self.repair_backup.files)} 个文件已移入备份。"
            "现在启动一次游戏；确认能正常进入游戏后，再到 Mod 页面删除备份。"
            "如果启动失败，随时可以还原。",
            button_text="知道了",
            icon_name="check",
        ).exec()
        self.accept()

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
