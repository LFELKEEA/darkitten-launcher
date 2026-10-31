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

from ..config import APP_DIR
from ..modrinth_pack import inspect_modrinth_pack, looks_like_modrinth_pack
from ..packs import human_size, inspect_pack, normalize_version_id, unique_version_id
from ..version_zip import VersionZipCandidate, VersionZipInfo, inspect_version_zip
from ..workers import PackFetchWorker, VersionZipImportWorker
from . import icons
from .dialogs import StyledDialog
from .download_center import TASK_CANCELLED, DownloadTask, TASK_SUCCESS
from .widgets import badge, colors, local_archive_paths


class VersionZipDialog(StyledDialog):
    """从本地 ZIP 或链接导入普通 Minecraft 版本包。"""

    def __init__(self, main, initial_path: Path | str | None = None) -> None:
        super().__init__(main, "拆开版本压缩包", "package", colors()["accent"], width=660)
        self.main = main
        self._info: VersionZipInfo | None = None
        self._candidate: VersionZipCandidate | None = None
        self._task: DownloadTask | None = None
        self._download_bound = False
        self._mode = "idle"
        self._attempted_version_id = ""
        self.imported_version = ""
        self.setAcceptDrops(True)

        intro = QLabel(
            "把别人分享的 ZIP 版本包拖进来，或者选择文件、粘贴链接。"
            "猫猫会解开版本 JSON、客户端 jar、mods，必要时补齐原版依赖。"
            "DKL / Modrinth 整合包会自动转到整合包导入。"
        )
        intro.setObjectName("DialogText")
        intro.setWordWrap(True)
        self.body.addWidget(intro)

        local_label = QLabel("本地版本包")
        local_label.setObjectName("SegLabel")
        self.body.addWidget(local_label)
        local_row = QHBoxLayout()
        local_row.setSpacing(8)
        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        self.path_edit.setAcceptDrops(False)
        self.path_edit.setPlaceholderText("还没有选择文件")
        self.choose_btn = QPushButton("选择文件")
        self.choose_btn.setObjectName("Secondary")
        self.choose_btn.setIcon(icons.icon("folder", colors()["text2"], 15))
        self.choose_btn.setCursor(Qt.PointingHandCursor)
        self.choose_btn.clicked.connect(self._choose_file)
        local_row.addWidget(self.path_edit, 1)
        local_row.addWidget(self.choose_btn)
        self.body.addLayout(local_row)

        url_label = QLabel("或者从链接下载")
        url_label.setObjectName("SegLabel")
        self.body.addWidget(url_label)
        url_row = QHBoxLayout()
        url_row.setSpacing(8)
        self.url_edit = QLineEdit()
        self.url_edit.setAcceptDrops(False)
        self.url_edit.setPlaceholderText("https://…/某个猫猫版本.zip")
        self.url_edit.returnPressed.connect(self._download_url)
        self.download_btn = QPushButton("下载并查看")
        self.download_btn.setObjectName("Soft")
        self.download_btn.setIcon(icons.icon("download", colors()["accent"], 15))
        self.download_btn.setCursor(Qt.PointingHandCursor)
        self.download_btn.clicked.connect(self._download_url)
        url_row.addWidget(self.url_edit, 1)
        url_row.addWidget(self.download_btn)
        self.body.addLayout(url_row)

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
        head.addWidget(self.preview_name)
        self.preview_loader = badge("", "purple")
        self.preview_mc = badge("", "blue")
        head.addWidget(self.preview_loader)
        head.addWidget(self.preview_mc)
        head.addStretch(1)
        preview_layout.addLayout(head)

        self.candidate_picker = QComboBox()
        self.candidate_picker.setVisible(False)
        self.candidate_picker.currentIndexChanged.connect(self._on_candidate_changed)
        preview_layout.addWidget(self.candidate_picker)

        self.preview_meta = QLabel("")
        self.preview_meta.setObjectName("Tiny")
        self.preview_meta.setWordWrap(True)
        preview_layout.addWidget(self.preview_meta)
        self.preview_contents = QLabel("")
        self.preview_contents.setObjectName("Tiny")
        self.preview_contents.setWordWrap(True)
        preview_layout.addWidget(self.preview_contents)
        self.preview.setVisible(False)
        self.body.addWidget(self.preview)

        self.import_options = QWidget()
        options_layout = QVBoxLayout(self.import_options)
        options_layout.setContentsMargins(0, 0, 0, 0)
        options_layout.setSpacing(6)
        name_label = QLabel("导入后的版本名称（也是版本文件夹名）")
        name_label.setObjectName("SegLabel")
        options_layout.addWidget(name_label)
        self.name_edit = QLineEdit()
        self.name_edit.setAcceptDrops(False)
        self.name_edit.setPlaceholderText("例如：猫猫的 1.21.1 版本")
        self.name_edit.setMaxLength(64)
        self.name_edit.textChanged.connect(self._clear_error)
        options_layout.addWidget(self.name_edit)
        self.name_hint = QLabel("")
        self.name_hint.setObjectName("Tiny")
        self.name_hint.setWordWrap(True)
        options_layout.addWidget(self.name_hint)
        self.complete = QCheckBox("自动补齐原版、依赖库和资源文件（需要联网）")
        self.complete.setChecked(True)
        self.complete.setToolTip(
            "ZIP 里只有版本 JSON 和 jar 时建议勾选；已经完整的离线包可以取消。"
        )
        options_layout.addWidget(self.complete)
        self.overwrite = QCheckBox("覆盖目标版本中已经存在的文件")
        self.overwrite.setToolTip(
            "不勾选时会跳过同名文件；重新导入时可用于替换损坏的文件。"
        )
        options_layout.addWidget(self.overwrite)
        self.import_options.setVisible(False)
        self.body.addWidget(self.import_options)

        self.cancel_btn = self.add_button("取消", "Secondary", self._on_cancel)
        self.import_btn = self.add_button("导入版本", "Primary", self._start_import)
        self.import_btn.setEnabled(False)

        self.finished.connect(self._detach_download_center)
        if initial_path:
            self._load_archive(Path(initial_path))

    # ------------------------------------------------------------------
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._mode == "idle" and local_archive_paths(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if self._mode == "idle" and local_archive_paths(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = local_archive_paths(event.mimeData())
        if self._mode != "idle" or not paths:
            event.ignore()
            return
        event.acceptProposedAction()
        self._load_archive(paths[0])

    def _choose_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择版本压缩包",
            str(self.main.config.game_path),
            "版本包 / 整合包 (*.zip *.dklpack *.mrpack);;所有文件 (*)",
        )
        if path:
            self._load_archive(Path(path))

    def _load_archive(self, path: Path) -> None:
        self.path_edit.setText(str(path))
        self.progress_box.setVisible(False)
        self._hide_error()
        self._info = None
        self._candidate = None
        self.preview.setVisible(False)
        self.import_options.setVisible(False)
        self.import_btn.setEnabled(False)
        try:
            inspect_pack(path)
        except Exception:
            pass
        else:
            self._route_to_pack(path)
            return
        if looks_like_modrinth_pack(path):
            try:
                inspect_modrinth_pack(path)
            except Exception as exc:
                self._show_error(f"Modrinth 整合包无法导入：{exc}")
                return
            self._route_to_pack(path)
            return
        try:
            info = inspect_version_zip(path)
        except Exception as exc:
            self._show_error(str(exc))
            return
        self._info = info
        self._attempted_version_id = ""
        self._fill_candidates()
        self.preview.setVisible(True)
        self.import_options.setVisible(True)
        self.import_btn.setEnabled(True)
        self.import_btn.setText("导入版本")

    def _route_to_pack(self, path: Path) -> None:
        main = self.main
        self._mode = "idle"
        self._task = None
        self.accept()
        QTimer.singleShot(0, lambda p=str(path): main.open_pack_import(p))

    def _fill_candidates(self) -> None:
        if not self._info:
            return
        picker = self.candidate_picker
        picker.blockSignals(True)
        picker.clear()
        for candidate in self._info.candidates:
            picker.addItem(
                f"{candidate.version_id}  ·  Minecraft {candidate.minecraft_version}",
                candidate.json_member,
            )
        picker.setVisible(len(self._info.candidates) > 1)
        picker.setCurrentIndex(0)
        picker.blockSignals(False)
        self._show_candidate(self._info.candidates[0])

    def _on_candidate_changed(self, index: int) -> None:
        if not self._info or index < 0:
            return
        member = str(self.candidate_picker.itemData(index) or "")
        try:
            candidate = self._info.find(json_member=member)
        except Exception:
            return
        self._show_candidate(candidate)

    def _show_candidate(self, candidate: VersionZipCandidate) -> None:
        self._candidate = candidate
        self._hide_error()
        self.preview_name.setText(candidate.version_id)
        self.preview_loader.setText(candidate.loader or "原版")
        self.preview_loader.setVisible(bool(candidate.loader))
        self.preview_mc.setText(f"Minecraft {candidate.minecraft_version}")
        archive_bytes = self._info.archive_bytes if self._info else 0
        self.preview_meta.setText(
            f"{candidate.total_files} 个文件 · 解压后 {human_size(candidate.total_bytes)} · "
            f"压缩包 {human_size(archive_bytes)} · {candidate.layout_label}"
        )
        parts: list[str] = []
        if candidate.mods:
            parts.append(f"Mod {candidate.mods}")
        if candidate.config:
            parts.append(f"配置 {candidate.config}")
        if candidate.libraries:
            parts.append(f"依赖库 {candidate.libraries}")
        if candidate.assets:
            parts.append(f"资源文件 {candidate.assets}")
        self.preview_contents.setText(
            "压缩包里装着：" + ("、".join(parts) or "版本 JSON")
        )
        default_id = unique_version_id(self.main.config.game_path, candidate.version_id)
        self.name_edit.setText(default_id)
        isolation = (
            "版本隔离已开启，mods 会放进这个版本自己的文件夹。"
            if self.main.config.version_isolation
            else "版本隔离已关闭，mods 会放进共享游戏目录。"
        )
        self.name_hint.setText(
            "默认沿用版本 JSON 里的名称；同名版本会自动加序号。" + isolation
        )

    @staticmethod
    def _fetch_result(path: str, error: str) -> tuple[bool, str, str]:
        if error:
            return False, str(error), ""
        target = Path(path)
        return True, "压缩包下载完成", target.name

    def _bind_download_center(self) -> None:
        if self._download_bound:
            return
        center = self.main.download_center
        center.task_progress.connect(self._on_task_progress)
        center.task_finished.connect(self._on_task_finished)
        self._download_bound = True

    def _detach_download_center(self, _result: int = 0) -> None:
        if not self._download_bound:
            return
        center = self.main.download_center
        try:
            center.task_progress.disconnect(self._on_task_progress)
            center.task_finished.disconnect(self._on_task_finished)
        except (RuntimeError, TypeError):
            pass
        self._download_bound = False

    def _on_task_progress(self, task_id: int, percent: int, message: str) -> None:
        if self._task is None or int(task_id) != self._task.id:
            return
        self.progress.setValue(percent)
        self.progress_text.setText(message)

    def _on_task_finished(self, task: DownloadTask) -> None:
        if self._task is None or task.id != self._task.id:
            return
        self._task = None
        self._mode = "idle"
        self.cancel_btn.setText("取消")
        if task.status == TASK_SUCCESS:
            self.progress.setValue(100)
            self.progress_text.setText(task.message)
            if task.content_type == "pack-fetch":
                self._set_controls_enabled(True)
                self.progress_box.setVisible(False)
                path = task.target / task.filename
                if path.is_file():
                    self._load_archive(path)
                else:
                    self._show_error("下载完成但找不到压缩包，请重试。")
                return
            self.imported_version = task.filename
            self.accept()
            return
        self._set_controls_enabled(True)
        self.progress_box.setVisible(False)
        self.import_btn.setEnabled(True)
        self.import_btn.setText("重新导入")
        cancelled = task.status == TASK_CANCELLED
        message = task.error or task.message or (
            "导入已取消" if cancelled else "导入失败"
        )
        target = normalize_version_id(self.name_edit.text())
        target_dir = self.main.config.game_path / "versions" / target
        if target and target_dir.exists():
            self._attempted_version_id = target
            message = (
                f"{message}\n已保留已经解压的内容，"
                "再次点击“重新导入”会接着补全；已完成的文件不会重复处理。"
            )
        if cancelled:
            message = (
                "导入已取消。\n已保留已经解压的内容，再次点击"
                "“重新导入”会接着补全；已完成的文件不会重复处理。"
                if target_dir.exists()
                else "导入已取消。可以再次点击“重新导入”重新开始。"
            )
        self._show_error(message)
        self.main.log(
            f"版本包任务{'已取消' if cancelled else '失败'}：{message}"
        )

    def _download_url(self) -> None:
        if self._mode != "idle":
            return
        url = self.url_edit.text().strip()
        if not url:
            self._show_error("链接是空的，猫猫找不到压缩包。")
            return
        self._hide_error()
        self._begin_busy("fetch")
        worker = PackFetchWorker(
            url,
            threads=self.main.config.download_threads,
            parent=None,
            inspect_result=False,
            default_suffix=".zip",
        )
        self._bind_download_center()
        self._task = self.main.download_center.start_worker(
            worker,
            title="下载版本包",
            content_type="pack-fetch",
            source="url",
            target=APP_DIR / "cache" / "packs",
            pack_mode=True,
            finished_adapter=self._fetch_result,
        )

    # ------------------------------------------------------------------
    def _start_import(self) -> None:
        if self._mode != "idle" or not self._candidate:
            return
        target = normalize_version_id(self.name_edit.text())
        if not target:
            self._show_error("导入名称不能为空。")
            return
        version_dir = self.main.config.game_path / "versions" / target
        resume = bool(
            self._attempted_version_id
            and self._attempted_version_id == target
            and version_dir.exists()
        )
        if version_dir.exists() and not resume:
            suggestion = unique_version_id(self.main.config.game_path, target)
            self.name_edit.setText(suggestion)
            self._show_error(
                f"版本 {target} 已经存在。可以改用 {suggestion}，或先删除旧版本。"
            )
            return
        self._hide_error()
        # 记住本次尝试的目标，取消或失败后用同一个名字可以直接续传。
        self._attempted_version_id = target
        self._begin_busy("import")
        worker = VersionZipImportWorker(
            self._info.path if self._info else Path(self.path_edit.text()),
            self.main.config.game_path,
            target,
            candidate_json=self._candidate.json_member,
            overwrite=self.overwrite.isChecked(),
            resume=resume,
            isolated=self.main.config.version_isolation,
            complete=self.complete.isChecked(),
            source=self.main.config.download_source,
            threads=self.main.config.download_threads,
            parent=self,
        )
        self._bind_download_center()
        self._task = self.main.download_center.start_worker(
            worker,
            title=f"导入版本 · {target}",
            content_type="version-import",
            source=self.main.config.download_source,
            target=version_dir,
        )

    def _begin_busy(self, mode: str) -> None:
        self._mode = mode
        self._hide_error()
        self._set_controls_enabled(False)
        self.progress.setValue(0)
        self.progress_box.setVisible(True)
        self.progress_text.setText(
            "正在下载压缩包…" if mode == "fetch" else "正在拆开压缩包…"
        )
        self.cancel_btn.setText("取消下载" if mode == "fetch" else "取消导入")
        self.cancel_btn.setEnabled(True)
        self.import_btn.setEnabled(False)

    def _set_controls_enabled(self, enabled: bool) -> None:
        self.choose_btn.setEnabled(enabled)
        self.download_btn.setEnabled(enabled)
        self.url_edit.setEnabled(enabled)
        self.name_edit.setEnabled(enabled)
        self.complete.setEnabled(enabled)
        self.overwrite.setEnabled(enabled)
        self.candidate_picker.setEnabled(enabled)
        self.import_btn.setEnabled(enabled and self._candidate is not None)

    def _on_cancel(self) -> None:
        if self._mode != "idle" and self._task is not None:
            self.cancel_btn.setEnabled(False)
            self.progress_text.setText("正在收回猫爪…")
            self.main.download_center.cancel(self._task.id)
            return
        self.reject()

    def _show_error(self, text: str) -> None:
        self.error.setText(text)
        self.error.setVisible(True)

    def _clear_error(self) -> None:
        self.error.clear()
        self.error.setVisible(False)

    def _hide_error(self) -> None:
        self._clear_error()

    def reject(self) -> None:
        if self._mode != "idle" and self._task is not None:
            self._detach_download_center()
            self.main.toast(
                "任务已转到顶栏继续，可以继续操作启动器；点击顶栏进度查看任务",
                "info",
            )
        super().reject()
