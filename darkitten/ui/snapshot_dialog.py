from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..snapshots import SnapshotInfo, list_snapshots, snapshot_dir
from .dialogs import ConfirmDialog, StyledDialog
from .widgets import badge, colors


class _SnapshotRow(QFrame):
    def __init__(
        self,
        info: SnapshotInfo,
        on_restore,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.info = info
        self.setObjectName("SelectRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(12)

        texts = QVBoxLayout()
        texts.setSpacing(3)
        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        title = QLabel(info.created_text)
        title.setStyleSheet("font-weight: 700; font-size: 12px;")
        title_row.addWidget(title)
        if info.note:
            title_row.addWidget(badge(info.note[:16], "purple"))
        title_row.addStretch(1)
        texts.addLayout(title_row)

        detail = QLabel(
            f"{info.files} 个文件 · {info.size_text}"
            + (f" · 跳过 {info.skipped} 个占用文件" if info.skipped else "")
        )
        detail.setObjectName("Tiny")
        texts.addWidget(detail)
        texts.addWidget(_muted(info.path.name))
        layout.addLayout(texts, 1)

        restore = QPushButton("恢复")
        restore.setObjectName("Secondary")
        restore.setCursor(Qt.PointingHandCursor)
        restore.clicked.connect(lambda: on_restore(info))
        layout.addWidget(restore)


def _muted(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Tiny")
    label.setStyleSheet(f"color: {colors()['text2']};")
    label.setToolTip(text)
    return label


class SnapshotDialog(StyledDialog):
    restore_requested = Signal(object)

    def __init__(
        self,
        parent,
        game_path: Path,
        version_id: str,
    ) -> None:
        super().__init__(parent, "世界与配置快照", "log", width=560)
        self.game_path = Path(game_path)
        self.version_id = version_id

        desc = QLabel(
            f"当前版本：{version_id or '未选择'}\n"
            "快照包含 saves、config、defaultconfigs 与选项文件。"
            "恢复时只覆盖同名文件，不会删除快照之外的新内容。"
        )
        desc.setObjectName("DialogText")
        desc.setWordWrap(True)
        self.body.addWidget(desc)

        snapshots = list_snapshots(self.game_path)
        if not snapshots:
            empty = QLabel("还没有快照。回到设置页点一下「创建快照」就好。")
            empty.setObjectName("Muted")
            empty.setWordWrap(True)
            empty.setAlignment(Qt.AlignCenter)
            empty.setMinimumHeight(90)
            self.body.addWidget(empty)
        else:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setMinimumHeight(min(360, 74 * len(snapshots) + 8))
            container = QWidget()
            layout = QVBoxLayout(container)
            layout.setContentsMargins(0, 0, 4, 0)
            layout.setSpacing(8)
            for info in snapshots:
                layout.addWidget(_SnapshotRow(info, self._confirm_restore))
            layout.addStretch(1)
            scroll.setWidget(container)
            self.body.addWidget(scroll)

        open_btn = QPushButton("打开备份目录")
        open_btn.setObjectName("Ghost")
        open_btn.setCursor(Qt.PointingHandCursor)
        open_btn.clicked.connect(self._open_folder)
        self.footer.addWidget(open_btn)
        self.add_button("关闭", "Secondary", self.reject)

    def _open_folder(self) -> None:
        folder = snapshot_dir(self.game_path)
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _confirm_restore(self, info: SnapshotInfo) -> None:
        dialog = ConfirmDialog(
            self,
            "恢复世界与配置",
            "将用快照中的文件覆盖当前版本的同名文件。"
            "快照之外新建的世界或配置会保留。",
            primary_text="恢复",
            secondary_text="取消",
            icon_name="warning",
            icon_color=colors()["orange"],
            details=(
                f"快照：{info.created_text}\n"
                f"版本：{info.version_id or self.version_id or '未知'}\n"
                f"内容：{info.files} 个文件 · {info.size_text}"
            ),
        )
        if not dialog.exec():
            return
        self.restore_requested.emit(info.path)
        self.accept()
