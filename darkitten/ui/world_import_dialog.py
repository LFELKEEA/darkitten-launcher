"""导入其他游戏版本的完整存档（所有维度、玩家数据与物品栏）。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..world_import import (
    WorldImportError,
    WorldSource,
    inspect_world_source,
    is_newer_version,
)
from ..workers import WorldImportWorker
from .dialogs import NoticeDialog, StyledDialog
from .widgets import colors


class WorldImportDialog(StyledDialog):
    """选择一个世界文件夹或 zip，复制到服务端或启动器存档目录。"""

    def __init__(
        self,
        parent,
        *,
        target_text: str,
        title: str = "导入完整存档",
        default_name: str = "world",
        name_label: str = "世界文件夹名称",
        initial_path: str | Path | None = None,
        server_info=None,
        dest_parent: str | Path | None = None,
        target_version: str = "",
    ) -> None:
        super().__init__(parent, title, "globe", width=620)
        self.server_info = server_info
        self.dest_parent = Path(dest_parent) if dest_parent is not None else None
        self.target_version = str(target_version or "").strip()
        self.source: WorldSource | None = None
        self.import_result = None
        self._worker: WorldImportWorker | None = None
        self._busy = False
        self._initial_path = str(initial_path or "")

        intro = QLabel(target_text)
        intro.setObjectName("DialogText")
        intro.setWordWrap(True)
        self.body.addWidget(intro)

        source_card = QFrame()
        source_card.setObjectName("SoftCard")
        source_card.setAttribute(Qt.WA_StyledBackground, True)
        source_box = QVBoxLayout(source_card)
        source_box.setContentsMargins(12, 10, 12, 10)
        source_box.setSpacing(7)

        source_head = QHBoxLayout()
        source_head.setSpacing(6)
        source_title = QLabel("存档来源")
        source_title.setStyleSheet("font-weight: 700;")
        source_head.addWidget(source_title)
        source_hint = QLabel("支持世界文件夹或 .zip 压缩包")
        source_hint.setObjectName("Tiny")
        source_head.addWidget(source_hint, 1)
        source_box.addLayout(source_head)

        pick_row = QHBoxLayout()
        pick_row.setSpacing(6)
        self.source_edit = QLineEdit()
        self.source_edit.setReadOnly(True)
        self.source_edit.setPlaceholderText("尚未选择存档")
        pick_row.addWidget(self.source_edit, 1)
        self.folder_btn = QPushButton("选择文件夹")
        self.folder_btn.setObjectName("Soft")
        self.folder_btn.setCursor(Qt.PointingHandCursor)
        self.folder_btn.clicked.connect(self._pick_folder)
        pick_row.addWidget(self.folder_btn)
        self.zip_btn = QPushButton("选择压缩包")
        self.zip_btn.setObjectName("Soft")
        self.zip_btn.setCursor(Qt.PointingHandCursor)
        self.zip_btn.clicked.connect(self._pick_zip)
        pick_row.addWidget(self.zip_btn)
        source_box.addLayout(pick_row)

        self.info_label = QLabel("")
        self.info_label.setObjectName("Tiny")
        self.info_label.setWordWrap(True)
        self.info_label.setVisible(False)
        source_box.addWidget(self.info_label)
        self.version_warning = QLabel("")
        self.version_warning.setObjectName("Tiny")
        self.version_warning.setWordWrap(True)
        self.version_warning.setStyleSheet(f"color: {colors()['orange']};")
        self.version_warning.setVisible(False)
        source_box.addWidget(self.version_warning)
        self.body.addWidget(source_card)

        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        name_label_widget = QLabel(name_label)
        name_label_widget.setFixedWidth(108)
        name_label_widget.setStyleSheet("font-weight: 700; font-size: 12px;")
        name_row.addWidget(name_label_widget)
        self.name_edit = QLineEdit(default_name)
        self.name_edit.setMaxLength(64)
        self.name_edit.setPlaceholderText("导入后使用的文件夹名")
        name_row.addWidget(self.name_edit, 1)
        self.body.addLayout(name_row)

        self.name_hint = QLabel("")
        self.name_hint.setObjectName("Tiny")
        self.name_hint.setWordWrap(True)
        self.body.addWidget(self.name_hint)

        self.note = QLabel(
            "会把整个世界文件夹原样复制过去：主世界、下界、末地、自定义维度、"
            "playerdata、stats、advancements 和 data 都会保留，不修改玩家数据、"
            "物品栏或进度，跨版本升级交给 Minecraft 首次打开世界时完成。"
        )
        self.note.setObjectName("Tiny")
        self.note.setWordWrap(True)
        self.body.addWidget(self.note)

        self.backup_note = QLabel(
            "目标位置已有同名世界时，会先重命名为「名字.bak-时间」备份，不会删除。"
        )
        self.backup_note.setObjectName("Tiny")
        self.backup_note.setWordWrap(True)
        self.backup_note.setStyleSheet(f"color: {colors()['muted']};")
        self.body.addWidget(self.backup_note)

        self.progress_box = QWidget()
        progress_layout = QVBoxLayout(self.progress_box)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.setSpacing(4)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        self.progress_text = QLabel("")
        self.progress_text.setObjectName("Tiny")
        self.progress_text.setWordWrap(True)
        progress_layout.addWidget(self.progress)
        progress_layout.addWidget(self.progress_text)
        self.progress_box.setVisible(False)
        self.body.addWidget(self.progress_box)

        self.close_btn = self.add_button("取消", "Secondary", self._on_close)
        self.import_btn = self.add_button("开始导入", "Primary", self._start)
        self.import_btn.setEnabled(False)
        self.import_btn.setMinimumWidth(100)

        if self._initial_path:
            self.set_source(self._initial_path)

    # ------------------------------------------------------------------
    def set_source(self, path: str | Path) -> None:
        try:
            source = inspect_world_source(path)
        except WorldImportError as error:
            NoticeDialog(
                self,
                "无法读取存档",
                str(error),
                icon_name="warning",
            ).exec()
            return
        self.source = source
        self.source_edit.setText(str(source.path))
        self.info_label.setText(
            f"世界「{source.name}」 · {source.version_text} · 种子 {source.seed_text}\n"
            f"维度：{source.dimension_text} · {source.player_text} · "
            f"{source.files} 个文件 · {source.size_text}"
        )
        self.info_label.setVisible(True)
        if is_newer_version(source.version_name, self.target_version):
            self.version_warning.setText(
                f"来源版本 {source.version_name} 高于目标版本 {self.target_version}："
                "Minecraft 不能降级打开世界，导入后可能无法加载。"
            )
            self.version_warning.setVisible(True)
        else:
            self.version_warning.setVisible(False)
        self.name_edit.setText(source.folder_name or "world")
        self._update_name_hint()
        self.import_btn.setEnabled(True)

    def _pick_folder(self) -> None:
        start = self._initial_path or str(Path.home())
        path = QFileDialog.getExistingDirectory(
            self, "选择世界文件夹（里面要有 level.dat）", start
        )
        if path:
            self.set_source(path)

    def _pick_zip(self) -> None:
        start = Path(self._initial_path) if self._initial_path else Path.home()
        if start.is_file():
            start = start.parent
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择存档压缩包",
            str(start),
            "存档压缩包 (*.zip);;所有文件 (*.*)",
        )
        if path:
            self.set_source(path)

    def _update_name_hint(self) -> None:
        if self.server_info is not None:
            self.name_hint.setText(
                "这是服务端主世界的文件夹名，会同步写入 server.properties 的 "
                "level-name。建议保持 world。"
            )
        else:
            self.name_hint.setText(
                "这是导入到 saves 目录后的文件夹名；游戏内显示的世界名称来自 "
                "level.dat，不会因为这里改名而改变。"
            )

    # ------------------------------------------------------------------
    def _start(self) -> None:
        if self._busy:
            self._cancel()
            return
        if self.source is None:
            return
        name = self.name_edit.text().strip()
        if not name:
            self.name_hint.setText("世界文件夹名称不能为空。")
            self.name_hint.setStyleSheet(f"color: {colors()['red']};")
            return
        self._set_busy(True)
        self.progress.setValue(0)
        self.progress_text.setText("准备导入…")
        worker = WorldImportWorker(
            self.source,
            dest_parent=self.dest_parent,
            folder_name=name,
            server_info=self.server_info,
            parent=self,
        )
        self._worker = worker
        worker.progress.connect(self._on_progress)
        worker.finished.connect(self._on_finished)
        worker.start()

    def _cancel(self) -> None:
        if self._worker is not None:
            self.progress_text.setText("正在取消…")
            self.import_btn.setEnabled(False)
            self._worker.cancel()

    def _on_progress(self, percent: int, message: str) -> None:
        self.progress.setValue(max(0, min(100, int(percent))))
        self.progress_text.setText(message)

    def _on_finished(self, success: bool, message: str, result) -> None:
        self._worker = None
        self._set_busy(False)
        if success:
            self.progress.setValue(100)
            self.progress_text.setText(message)
            self.import_result = result
            parent = self.parentWidget()
            toast = getattr(parent, "toast", None)
            if callable(toast):
                toast(message, "success")
            self.accept()
            return
        self.progress_text.setText(message)
        if "取消" in message:
            self.import_btn.setText("开始导入")
            return
        self.import_btn.setText("重试导入")
        self.name_hint.setText(message)
        self.name_hint.setStyleSheet(f"color: {colors()['red']};")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.folder_btn.setEnabled(not busy)
        self.zip_btn.setEnabled(not busy)
        self.name_edit.setEnabled(not busy)
        self.close_btn.setText("取消并关闭" if busy else "取消")
        self.close_btn.setEnabled(True)
        self.import_btn.setText("取消导入" if busy else "开始导入")
        self.import_btn.setEnabled(True)
        self.progress_box.setVisible(busy)

    def _on_close(self) -> None:
        if self._busy and self._worker is not None:
            self._worker.cancel()
        self.reject()
