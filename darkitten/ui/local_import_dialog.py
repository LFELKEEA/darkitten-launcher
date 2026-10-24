from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
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
    QVBoxLayout,
    QWidget,
)

from ..local_import import (
    LocalImportError,
    LocalImportInfo,
    VersionFolderInfo,
    detect_local_import,
)
from ..packs import normalize_version_id, unique_version_id
from ..versions import version_json_path
from ..workers import LocalContentImportWorker, VersionFolderImportWorker
from . import icons
from .dialogs import StyledDialog
from .widgets import badge, colors, local_transfer_paths


class LocalImportDialog(StyledDialog):
    """把本地版本文件夹、Mod、材质和光影直接放进游戏目录。"""

    def __init__(
        self,
        main,
        initial_path: Path | str | None = None,
        initial_paths: list[Path | str] | None = None,
    ) -> None:
        super().__init__(main, "叼回本地文件", "folder", colors()["accent"], width=680)
        self.main = main
        self._sources: list[Path] = []
        self._infos: list[LocalImportInfo] = []
        self._version_info: VersionFolderInfo | None = None
        self._worker: LocalContentImportWorker | VersionFolderImportWorker | None = None
        self._mode = "idle"
        self.imported_version = ""
        self.setAcceptDrops(True)

        intro = QLabel(
            "把文件直接拖进来。Mod 会原样复制到 mods；材质和光影保持 ZIP 让游戏直接读取；"
            "版本文件夹会直接搬进 versions；整合包和服务端会转到各自的专属导入流程。"
        )
        intro.setObjectName("DialogText")
        intro.setWordWrap(True)
        self.body.addWidget(intro)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        self.path_edit.setAcceptDrops(False)
        self.path_edit.setPlaceholderText("还没有选择文件或文件夹")
        self.choose_btn = QPushButton("选择文件")
        self.choose_btn.setObjectName("Secondary")
        self.choose_btn.setIcon(icons.icon("folder", colors()["text2"], 15))
        self.choose_btn.setCursor(Qt.PointingHandCursor)
        self.choose_btn.clicked.connect(self._choose_files)
        self.folder_btn = QPushButton("选择版本文件夹")
        self.folder_btn.setObjectName("Secondary")
        self.folder_btn.setIcon(icons.icon("package", colors()["text2"], 15))
        self.folder_btn.setCursor(Qt.PointingHandCursor)
        self.folder_btn.clicked.connect(self._choose_folder)
        row.addWidget(self.path_edit, 1)
        row.addWidget(self.choose_btn)
        row.addWidget(self.folder_btn)
        self.body.addLayout(row)

        self.preview = QFrame()
        self.preview.setObjectName("SoftCard")
        self.preview.setAttribute(Qt.WA_StyledBackground, True)
        preview_layout = QVBoxLayout(self.preview)
        preview_layout.setContentsMargins(14, 12, 14, 12)
        preview_layout.setSpacing(7)
        head = QHBoxLayout()
        head.setSpacing(7)
        self.preview_name = QLabel("")
        self.preview_name.setStyleSheet("font-size: 15px; font-weight: 800;")
        self.preview_badge = badge("", "blue")
        head.addWidget(self.preview_name)
        head.addWidget(self.preview_badge)
        head.addStretch(1)
        preview_layout.addLayout(head)
        self.preview_meta = QLabel("")
        self.preview_meta.setObjectName("Tiny")
        self.preview_meta.setWordWrap(True)
        preview_layout.addWidget(self.preview_meta)
        self.preview_note = QLabel("")
        self.preview_note.setObjectName("Tiny")
        self.preview_note.setWordWrap(True)
        preview_layout.addWidget(self.preview_note)
        self.preview.setVisible(False)
        self.body.addWidget(self.preview)

        self.content_options = QWidget()
        content_layout = QVBoxLayout(self.content_options)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(6)
        target_label = QLabel("放进哪个游戏版本")
        target_label.setObjectName("SegLabel")
        content_layout.addWidget(target_label)
        self.version_combo = QComboBox()
        self.version_combo.currentIndexChanged.connect(self._update_target_hint)
        content_layout.addWidget(self.version_combo)
        self.target_hint = QLabel("")
        self.target_hint.setObjectName("Tiny")
        self.target_hint.setWordWrap(True)
        content_layout.addWidget(self.target_hint)
        self.content_options.setVisible(False)
        self.body.addWidget(self.content_options)

        self.version_options = QWidget()
        version_layout = QVBoxLayout(self.version_options)
        version_layout.setContentsMargins(0, 0, 0, 0)
        version_layout.setSpacing(6)
        name_label = QLabel("导入后的版本名称")
        name_label.setObjectName("SegLabel")
        version_layout.addWidget(name_label)
        self.name_edit = QLineEdit()
        self.name_edit.setAcceptDrops(False)
        self.name_edit.setMaxLength(64)
        self.name_edit.textChanged.connect(self._clear_error)
        version_layout.addWidget(self.name_edit)
        self.name_hint = QLabel("")
        self.name_hint.setObjectName("Tiny")
        self.name_hint.setWordWrap(True)
        version_layout.addWidget(self.name_hint)
        self.complete = QCheckBox("自动补齐依赖库和资源文件（需要联网）")
        self.complete.setChecked(True)
        version_layout.addWidget(self.complete)
        self.version_options.setVisible(False)
        self.body.addWidget(self.version_options)

        self.progress_box = QWidget()
        progress_layout = QVBoxLayout(self.progress_box)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.setSpacing(5)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(7)
        self.progress_text = QLabel("")
        self.progress_text.setObjectName("Tiny")
        self.progress_text.setWordWrap(True)
        progress_layout.addWidget(self.progress)
        progress_layout.addWidget(self.progress_text)
        self.progress_box.setVisible(False)
        self.body.addWidget(self.progress_box)

        self.error = QLabel("")
        self.error.setWordWrap(True)
        self.error.setStyleSheet(f"color: {colors()['red']}; font-size: 11px;")
        self.error.setVisible(False)
        self.body.addWidget(self.error)

        self.cancel_btn = self.add_button("取消", "Secondary", self._on_cancel)
        self.import_btn = self.add_button("直接放进游戏", "Primary", self._start_import)
        self.import_btn.setEnabled(False)

        self.finished.connect(self._cleanup)
        paths = [Path(item) for item in (initial_paths or [])]
        if initial_path:
            paths.append(Path(initial_path))
        if paths:
            self._load_paths(paths)

    # ------------------------------------------------------------------
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._mode == "idle" and local_transfer_paths(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if self._mode == "idle" and local_transfer_paths(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = local_transfer_paths(event.mimeData())
        if self._mode != "idle" or not paths:
            event.ignore()
            return
        event.acceptProposedAction()
        self._load_paths(paths)

    def _choose_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择本地内容或版本包",
            str(self.main.config.game_path),
            (
                "支持的文件 (*.jar *.litemod *.zip *.dklpack *.mrpack "
                "*.jar.disabled *.litemod.disabled);;所有文件 (*)"
            ),
        )
        if files:
            self._load_paths([Path(item) for item in files])

    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "选择版本文件夹或完整游戏目录", str(self.main.config.game_path)
        )
        if folder:
            self._load_paths([Path(folder)])

    def _load_paths(self, paths: list[Path]) -> None:
        self._hide_error()
        if not paths:
            return
        try:
            infos = [detect_local_import(path) for path in paths]
        except LocalImportError as exc:
            self._show_error(str(exc))
            return

        special = [info for info in infos if info.kind in ("server", "modpack", "version_zip")]
        if special:
            if len(infos) != 1:
                self._show_error("专属导入包一次只能处理一个。")
                return
            self._route_to_special(special[0])
            return

        if any(info.kind == "version_folder" for info in infos):
            if len(infos) != 1:
                self._show_error("版本文件夹一次只能导入一个。")
                return
            self._show_version(infos[0])
            return

        if any(info.kind != "content" for info in infos):
            self._show_error("有一项内容无法识别，请换一个文件试试。")
            return
        self._show_contents(infos)

    def _route_to_special(self, info: LocalImportInfo) -> None:
        path = info.path
        self._mode = "routing"
        self.accept()
        QTimer.singleShot(0, lambda p=path: self.main.open_local_import(p))

    def _show_contents(self, infos: list[LocalImportInfo]) -> None:
        self._mode = "idle"
        self._infos = infos
        self._sources = [info.path for info in infos]
        self._version_info = None
        self.path_edit.setText(
            str(infos[0].path)
            if len(infos) == 1
            else f"已选择 {len(infos)} 个本地内容文件"
        )
        labels = {info.label for info in infos}
        self.preview_name.setText(
            infos[0].title if len(infos) == 1 else f"{len(infos)} 个本地内容"
        )
        self.preview_badge.setText(next(iter(labels)) if len(labels) == 1 else "本地内容")
        self.preview_meta.setText(" · ".join(info.detail for info in infos))
        self.preview_note.setText(
            "非压缩文件直接复制到目标文件夹；材质 / 光影 ZIP 保持原样，"
            "游戏会直接读取；Mod 合集会在目标文件夹里就地解开。源文件不会被移动或删除。"
        )
        self.preview.setVisible(True)
        self.version_options.setVisible(False)
        self._fill_versions()
        self.content_options.setVisible(True)
        self.import_btn.setText("直接放进游戏")
        self.import_btn.setEnabled(self.version_combo.count() > 0)
        self._update_target_hint()

    def _show_version(self, info: LocalImportInfo) -> None:
        self._mode = "idle"
        self._sources = []
        self._infos = [info]
        self._version_info = info.version_folder
        self.path_edit.setText(str(info.path))
        candidate = self._version_info.primary if self._version_info else None
        self.preview_name.setText(info.title)
        self.preview_badge.setText("版本文件夹")
        self.preview_meta.setText(info.detail)
        self.preview_note.setText(
            "文件会直接复制进 versions/<版本名>，不经过临时 ZIP；"
            "关闭游戏后再导入更稳妥，同名版本不会覆盖。"
        )
        self.preview.setVisible(True)
        self.content_options.setVisible(False)
        self.version_options.setVisible(True)
        default_id = (
            unique_version_id(self.main.config.game_path, candidate.version_id)
            if candidate
            else info.title
        )
        self.name_edit.setText(default_id)
        isolation = (
            "版本隔离已开启，这个版本会使用自己的 mods / saves 目录。"
            if self.main.config.version_isolation
            else "版本隔离已关闭，内容会使用共享游戏目录。"
        )
        self.name_hint.setText(
            f"来源结构：{candidate.layout_label if candidate else '版本文件夹'}。"
            f"同名版本会自动改名。{isolation}"
        )
        self.complete.setChecked(bool(self.main.config.auto_complete))
        self.import_btn.setText("直接复制版本")
        self.import_btn.setEnabled(True)

    def _fill_versions(self) -> None:
        current = self.version_combo.currentData()
        preferred = self.main.selected_version or self.main.config.last_version
        self.version_combo.blockSignals(True)
        self.version_combo.clear()
        chosen = -1
        for index, summary in enumerate(self.main.game_version_summaries()):
            profile = self.main.loader_profile_for(summary.id)
            suffix = f" · {profile.loader}" if profile else ""
            self.version_combo.addItem(f"{summary.id}{suffix}", summary.id)
            if summary.id == (current or preferred):
                chosen = index
        if chosen < 0 and self.version_combo.count():
            chosen = 0
        if chosen >= 0:
            self.version_combo.setCurrentIndex(chosen)
        self.version_combo.blockSignals(False)

    def _update_target_hint(self) -> None:
        version_id = str(self.version_combo.currentData() or "")
        if not version_id:
            self.target_hint.setText("还没有游戏版本。先安装或导入一个原版版本，猫猫才有地方放内容。")
            self.import_btn.setEnabled(False)
            return
        self.import_btn.setEnabled(self._mode == "idle")
        kinds = {info.content_plan.kind for info in self._infos if info.content_plan}
        if kinds and kinds <= {"resourcepack", "resourcepack_folder"}:
            target = self.main.config.resourcepacks_path(version_id)
        elif kinds and kinds <= {"shaderpack", "shaderpack_folder"}:
            target = self.main.config.shaderpacks_path(version_id)
        else:
            target = self.main.config.version_game_path(version_id)
        self.target_hint.setText(
            f"目标：{target}"
            + (
                "（同名文件自动加序号，不覆盖旧文件）"
                if "content_bundle" not in kinds
                else "（同名文件会跳过）"
            )
        )

    # ------------------------------------------------------------------
    def _start_import(self) -> None:
        if self._mode != "idle":
            return
        self._hide_error()
        if self._version_info is not None:
            self._start_version_import()
            return
        if not self._sources:
            self._show_error("还没有选择要导入的内容。")
            return
        version_id = str(self.version_combo.currentData() or "")
        if not version_id:
            self._show_error("先选择一个游戏版本。")
            return
        self._begin_busy()
        worker = LocalContentImportWorker(
            self._sources,
            self.main.config.game_path,
            version_id,
            isolated=self.main.config.version_isolation,
            parent=self,
        )
        self._worker = worker
        worker.progress.connect(self._on_progress)
        worker.log_line.connect(self.main.log)
        worker.finished.connect(self._on_content_finished)
        worker.start()

    def _start_version_import(self) -> None:
        if not self._version_info:
            return
        target = normalize_version_id(self.name_edit.text())
        if not target:
            self._show_error("版本名称不能为空。")
            return
        if version_json_path(self.main.config.game_path, target).is_file() or (
            self.main.config.game_path / "versions" / target
        ).exists():
            suggestion = unique_version_id(self.main.config.game_path, target)
            self.name_edit.setText(suggestion)
            self._show_error(f"版本 {target} 已经存在，已换成 {suggestion}。")
            return
        candidate = self._version_info.primary
        self._begin_busy()
        worker = VersionFolderImportWorker(
            self._version_info.path,
            self.main.config.game_path,
            target,
            candidate_json=candidate.json_path,
            complete=self.complete.isChecked(),
            isolated=self.main.config.version_isolation,
            source=self.main.config.download_source,
            threads=self.main.config.download_threads,
            parent=self,
        )
        self._worker = worker
        worker.progress.connect(self._on_progress)
        worker.log_line.connect(self.main.log)
        worker.finished.connect(self._on_version_finished)
        worker.start()

    def _begin_busy(self) -> None:
        self._mode = "import"
        self.progress.setValue(0)
        self.progress_text.setText("准备导入…")
        self.progress_box.setVisible(True)
        self.choose_btn.setEnabled(False)
        self.folder_btn.setEnabled(False)
        self.name_edit.setEnabled(False)
        self.complete.setEnabled(False)
        self.version_combo.setEnabled(False)
        self.import_btn.setEnabled(False)
        self.cancel_btn.setText("取消导入")
        self.cancel_btn.setEnabled(True)

    def _on_progress(self, percent: int, message: str) -> None:
        self.progress.setValue(percent)
        self.progress_text.setText(message)

    def _on_content_finished(self, success: bool, message: str, result) -> None:
        self._worker = None
        if not success:
            self._finish_busy()
            self._show_error(message)
            return
        self.progress.setValue(100)
        self.progress_text.setText(message)
        targets = getattr(result, "targets", {}) or {}
        detail = "、".join(f"{key} {value}" for key, value in targets.items())
        self.main.log(f"本地内容导入完成：{message}" + (f"（{detail}）" if detail else ""))
        self.main.toast(message + "，已经放进对应文件夹", "success")
        if hasattr(self.main, "mods_page"):
            self.main.mods_page.refresh_mods()
        self.accept()

    def _on_version_finished(self, success: bool, message: str, version_id: str) -> None:
        self._worker = None
        if not success:
            self._finish_busy()
            self._show_error(message)
            return
        self.progress.setValue(100)
        self.progress_text.setText(message)
        self.imported_version = version_id
        self.main.log(f"版本文件夹导入完成：{version_id} · {message}")
        self.main.refresh_versions()
        self.main.select_version(version_id)
        self.main.toast(f"{version_id} 已经直接搬进猫窝", "success")
        self.accept()

    def _finish_busy(self) -> None:
        self._mode = "idle"
        self.choose_btn.setEnabled(True)
        self.folder_btn.setEnabled(True)
        self.name_edit.setEnabled(True)
        self.complete.setEnabled(True)
        self.version_combo.setEnabled(True)
        self.import_btn.setEnabled(True)
        self.cancel_btn.setText("取消")
        self.cancel_btn.setEnabled(True)

    def _on_cancel(self) -> None:
        if self._worker is not None and self._mode == "import":
            self._worker.cancel()
            self.cancel_btn.setText("正在取消…")
            self.cancel_btn.setEnabled(False)
            return
        self.reject()

    def _cleanup(self) -> None:
        if self._worker is not None and self._mode == "import":
            self._worker.cancel()

    def _show_error(self, message: str) -> None:
        self.error.setText(message)
        self.error.setVisible(True)

    def _hide_error(self) -> None:
        self.error.setVisible(False)

    def _clear_error(self, _text: str = "") -> None:
        self._hide_error()
