"""导入其他游戏版本的存档，可选只导入主世界、下界或末地。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..world_import import (
    DIMENSION_KEYS,
    DIMENSION_NAMES,
    WorldImportError,
    WorldSource,
    available_dimensions,
    inspect_world_source,
    is_newer_version,
    scan_local_world_options,
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
        title: str = "导入存档",
        default_name: str = "world",
        name_label: str = "世界文件夹名称",
        initial_path: str | Path | None = None,
        server_info=None,
        dest_parent: str | Path | None = None,
        target_version: str = "",
    ) -> None:
        super().__init__(parent, title, "globe", width=660)
        self.server_info = server_info
        self.dest_parent = Path(dest_parent) if dest_parent is not None else None
        self.target_version = str(target_version or "").strip()
        self.source: WorldSource | None = None
        self.import_result = None
        self._worker: WorldImportWorker | None = None
        self._busy = False
        self._initial_path = str(initial_path or "")
        owner = getattr(parent, "main", parent)
        self.config = getattr(owner, "config", None)

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

        local_row = QHBoxLayout()
        local_row.setSpacing(6)
        local_title = QLabel("启动器选择")
        local_title.setFixedWidth(96)
        local_title.setStyleSheet("font-weight: 700; font-size: 12px;")
        local_row.addWidget(local_title)
        self.local_combo = QComboBox()
        # 下拉框只保留一个较小的最小宽度，长条目按可用空间省略，避免把卡片撑出边界。
        self.local_combo.setMinimumContentsLength(14)
        self.local_combo.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon
        )
        self.local_combo.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.local_combo.setMinimumWidth(180)
        self.local_combo.setMaxVisibleItems(18)
        self.local_combo.currentIndexChanged.connect(self._on_local_changed)
        local_row.addWidget(self.local_combo, 1)
        self.refresh_local_btn = QPushButton("刷新")
        self.refresh_local_btn.setObjectName("Soft")
        self.refresh_local_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_local_btn.setFixedWidth(56)
        self.refresh_local_btn.clicked.connect(self._reload_local_options)
        local_row.addWidget(self.refresh_local_btn)
        source_box.addLayout(local_row)

        pick_row = QHBoxLayout()
        pick_row.setSpacing(6)
        self.source_edit = QLineEdit()
        self.source_edit.setReadOnly(True)
        self.source_edit.setPlaceholderText("尚未选择存档")
        pick_row.addWidget(self.source_edit, 1)
        self.folder_btn = QPushButton("文件夹")
        self.folder_btn.setObjectName("Soft")
        self.folder_btn.setCursor(Qt.PointingHandCursor)
        self.folder_btn.clicked.connect(self._pick_folder)
        pick_row.addWidget(self.folder_btn)
        self.zip_btn = QPushButton("压缩包")
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

        self.dimension_box = QWidget()
        dimension_layout = QVBoxLayout(self.dimension_box)
        dimension_layout.setContentsMargins(0, 0, 0, 0)
        dimension_layout.setSpacing(4)
        dimension_row = QHBoxLayout()
        dimension_row.setSpacing(12)
        dimension_title = QLabel("导入维度")
        dimension_title.setStyleSheet("font-weight: 700; font-size: 12px;")
        dimension_row.addWidget(dimension_title)
        self.dimension_boxes: dict[str, QCheckBox] = {}
        for key in DIMENSION_KEYS:
            box = QCheckBox(DIMENSION_NAMES[key])
            box.setChecked(True)
            box.setEnabled(False)
            box.toggled.connect(self._on_dimension_changed)
            self.dimension_boxes[key] = box
            dimension_row.addWidget(box)
        dimension_row.addStretch(1)
        dimension_layout.addLayout(dimension_row)
        self.dimension_hint = QLabel(
            "全部选中会替换主世界、下界和末地，level.dat、玩家数据和服务端配置"
            "仍然保留；只选部分维度时，未选中的维度同样保持原样。"
        )
        self.dimension_hint.setObjectName("Tiny")
        self.dimension_hint.setWordWrap(True)
        dimension_layout.addWidget(self.dimension_hint)
        self.dimension_box.setVisible(self.server_info is not None)
        self.body.addWidget(self.dimension_box)

        self.note = QLabel("")
        self.note.setObjectName("Tiny")
        self.note.setWordWrap(True)
        self.body.addWidget(self.note)
        self._update_dimension_note()

        self.backup_note = QLabel(
            "目标位置已有同名世界或同名维度时，会先重命名为「名字.bak-时间」备份，不会删除。"
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

        self._reload_local_options()
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
        if self.server_info is None:
            self.name_edit.setText(source.folder_name or "world")
        self.name_hint.setStyleSheet("")
        self._update_dimensions()
        self._update_name_hint()
        self._sync_local_selection(source.path)
        if self.server_info is not None and not self._selected_dimensions():
            self.import_btn.setEnabled(False)
        else:
            self.import_btn.setEnabled(True)

    def _update_dimensions(self) -> None:
        if self.server_info is None:
            return
        available = set(available_dimensions(self.source)) if self.source else set()
        for key, box in self.dimension_boxes.items():
            exists = key in available
            box.setProperty("available", exists)
            box.blockSignals(True)
            box.setEnabled(exists and not self._busy)
            box.setChecked(exists)
            box.blockSignals(False)
        self._update_dimension_note()

    def _selected_dimensions(self) -> list[str]:
        return [
            key for key in DIMENSION_KEYS
            if self.dimension_boxes[key].isChecked()
            and self.dimension_boxes[key].isEnabled()
        ]

    def _on_dimension_changed(self) -> None:
        self._update_dimension_note()
        self._update_name_hint()
        if self.server_info is not None and not self._busy:
            self.import_btn.setEnabled(bool(self._selected_dimensions()))

    def _update_dimension_note(self) -> None:
        if not hasattr(self, "note"):
            return
        self.note.setStyleSheet("")
        if self.server_info is None:
            self.note.setText(
                "会把整个世界文件夹原样复制过去：主世界、下界、末地、自定义维度、"
                "playerdata、stats、advancements 和 data 都会保留，不修改玩家数据、"
                "物品栏或进度，跨版本升级交给 Minecraft 首次打开世界时完成。"
            )
            return
        if self.source is None:
            self.note.setText("选择存档后会自动识别可导入的主世界、下界和末地数据。")
            return
        selected = self._selected_dimensions()
        if not selected:
            if any(
                box.property("available") is not False
                for box in self.dimension_boxes.values()
            ):
                self.note.setText("至少选择一个要导入的维度。")
            else:
                self.note.setText(
                    "来源存档里没有识别到主世界、下界或末地数据，不能按维度导入。"
                )
            return
        all_selected = set(selected) == set(DIMENSION_KEYS)
        labels = "、".join(DIMENSION_NAMES[key] for key in selected)
        if all_selected:
            self.note.setText(
                "将替换目标世界的主世界、下界和末地，自定义维度也会一起导入；"
                "level.dat、玩家数据和服务端配置保持原样，"
                "目标已有维度会先备份为 .bak-时间。"
            )
        else:
            self.note.setText(
                f"只导入{labels}：其他维度、level.dat、玩家数据和服务端配置"
                "保持原样；目标已有同名维度会先备份为 .bak-时间。"
                "目标世界不存在时会创建，并复制 level.dat 等公共文件。"
            )

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

    # ------------------------------------------------------------------
    def _reload_local_options(self, _checked: bool = False) -> None:
        if not hasattr(self, "local_combo"):
            return
        current = self.local_combo.currentData()
        options = []
        if self.config is not None:
            try:
                options = scan_local_world_options(
                    self.config.game_path,
                    version_names=getattr(self.config, "version_names", {}) or {},
                    exclude_server=getattr(self.server_info, "folder", "") or "",
                )
            except Exception:
                options = []
        self.local_combo.blockSignals(True)
        self.local_combo.clear()
        self.local_combo.addItem(
            "从启动器里的存档中选择…" if options
            else "没有找到启动器里的存档，可手动选择",
            "",
        )
        for option in options:
            self.local_combo.addItem(option.label, str(option.path))
            index = self.local_combo.count() - 1
            self.local_combo.setItemData(index, option.detail, Qt.ToolTipRole)
        if current:
            for index in range(1, self.local_combo.count()):
                if self.local_combo.itemData(index) == current:
                    self.local_combo.setCurrentIndex(index)
                    break
        self.local_combo.blockSignals(False)
        self.local_combo.setEnabled(bool(options) and not self._busy)

    def _on_local_changed(self, _index: int = 0) -> None:
        if self._busy:
            return
        path = self.local_combo.currentData()
        if path:
            self.set_source(str(path))

    def _sync_local_selection(self, path: str | Path) -> None:
        if not hasattr(self, "local_combo"):
            return
        try:
            target = Path(path).resolve()
        except OSError:
            target = Path(path)
        self.local_combo.blockSignals(True)
        self.local_combo.setCurrentIndex(0)
        for index in range(1, self.local_combo.count()):
            data = self.local_combo.itemData(index)
            if not data:
                continue
            try:
                same = Path(str(data)).resolve() == target
            except OSError:
                same = False
            if same:
                self.local_combo.setCurrentIndex(index)
                break
        self.local_combo.blockSignals(False)

    def _update_name_hint(self) -> None:
        if self.server_info is not None:
            self.name_hint.setText(
                "这是服务端主世界的文件夹名，会同步写入 server.properties 的 "
                "level-name。保持当前值会合并到已有世界；改成新名字会创建新世界。"
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
        dimensions = None
        if self.server_info is not None:
            selected = self._selected_dimensions()
            if not selected:
                self.note.setText("至少选择一个要导入的维度。")
                self.note.setStyleSheet(f"color: {colors()['red']};")
                return
            # 全选也走按维度导入：Paper/Spigot 服务端会把下界、末地放到
            # world_nether、world_the_end，并且 level.dat、玩家数据保持原样。
            dimensions = selected
        self._set_busy(True)
        self.progress.setValue(0)
        self.progress_text.setText("准备导入…")
        worker = WorldImportWorker(
            self.source,
            dest_parent=self.dest_parent,
            folder_name=name,
            server_info=self.server_info,
            dimensions=dimensions,
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
        self.refresh_local_btn.setEnabled(not busy)
        has_local = any(
            self.local_combo.itemData(index)
            for index in range(self.local_combo.count())
        )
        self.local_combo.setEnabled(not busy and has_local)
        self.name_edit.setEnabled(not busy)
        for box in self.dimension_boxes.values():
            box.setEnabled(not busy and box.property("available") is not False)
        self.close_btn.setText("取消并关闭" if busy else "取消")
        self.close_btn.setEnabled(True)
        self.import_btn.setText("取消导入" if busy else "开始导入")
        self.import_btn.setEnabled(True)
        self.progress_box.setVisible(busy)

    def _on_close(self) -> None:
        if self._busy and self._worker is not None:
            self._worker.cancel()
        self.reject()
