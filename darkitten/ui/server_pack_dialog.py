from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QStandardPaths, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from ..packs import human_size
from ..server_pack import ServerPackInfo, inspect_server_pack
from ..servers import ServerInfo, _safe_folder, scan_servers
from ..workers import ServerPackExportWorker, ServerPackImportWorker
from . import icons
from .dialogs import ConfirmDialog, StyledDialog
from .widgets import badge, colors


def _json_count(path: Path) -> int:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return 0
    return len(data) if isinstance(data, list) else 0


def _server_snapshot(root: Path) -> dict[str, int]:
    worlds: list[str] = []
    try:
        for item in root.iterdir():
            if item.is_dir() and (item / "level.dat").is_file():
                worlds.append(item.name)
    except OSError:
        pass
    players = 0
    for world in worlds:
        folder = root / world / "playerdata"
        if not folder.is_dir():
            continue
        try:
            players += sum(
                1 for item in folder.iterdir() if item.suffix.lower() == ".dat"
            )
        except OSError:
            pass
    return {
        "worlds": len(worlds),
        "players": players,
        "whitelist": _json_count(root / "whitelist.json"),
        "ops": _json_count(root / "ops.json"),
        "banned": _json_count(root / "banned-players.json")
        + _json_count(root / "banned-ips.json"),
    }


def _unique_directory(path: Path) -> Path:
    result = path
    index = 2
    while result.exists():
        result = path.with_name(f"{path.name}-{index}")
        index += 1
    return result


def _unique_server_name(name: str) -> str:
    base = str(name or "Minecraft 服务端").strip() or "Minecraft 服务端"
    names = {item.name.strip().casefold() for item in scan_servers()}
    candidate = base
    index = 2
    while candidate.casefold() in names:
        candidate = f"{base} ({index})"
        index += 1
    return candidate


class ServerPackExportDialog(StyledDialog):
    """把服务端连同世界、玩家数据与权限设置打包成 ZIP 或文件夹。"""

    def __init__(
        self,
        main,
        info: ServerInfo,
    ) -> None:
        super().__init__(main, "把服务端装进纸箱", "package", colors()["accent"], width=600)
        self.main = main
        self.info = info
        self._worker: ServerPackExportWorker | None = None
        self._busy = False
        self._result_path = ""
        self._folder_name = _safe_folder(info.name or info.folder) or info.folder
        snapshot = _server_snapshot(Path(info.path))
        desktop = QStandardPaths.writableLocation(QStandardPaths.DesktopLocation)
        self._target_dir = Path(desktop) if desktop else Path.home()
        if not self._target_dir.is_dir():
            self._target_dir = Path.home()

        intro = QLabel(
            f"把 <b>{info.name}</b> 连同世界、玩家数据和权限设置一起打包，"
            "换台电脑解压或拖回启动器就能继续开服。"
        )
        intro.setObjectName("DialogText")
        intro.setWordWrap(True)
        self.body.addWidget(intro)

        stats = QFrame()
        stats.setObjectName("SoftCard")
        stats.setAttribute(Qt.WA_StyledBackground, True)
        stats_box = QHBoxLayout(stats)
        stats_box.setContentsMargins(12, 9, 12, 9)
        stats_box.setSpacing(7)
        stats_box.addWidget(badge(f"世界 {snapshot['worlds']}", "green"))
        stats_box.addWidget(badge(f"玩家 {snapshot['players']}", "blue"))
        if snapshot["whitelist"]:
            stats_box.addWidget(badge(f"白名单 {snapshot['whitelist']}", "purple"))
        if snapshot["ops"]:
            stats_box.addWidget(badge(f"OP {snapshot['ops']}", "orange"))
        if snapshot["banned"]:
            stats_box.addWidget(badge(f"封禁 {snapshot['banned']}", "red"))
        stats_box.addStretch(1)
        stats_box.addWidget(badge(info.type_name, "blue"))
        self.body.addWidget(stats)

        format_label = QLabel("打包格式")
        format_label.setObjectName("SegLabel")
        self.body.addWidget(format_label)
        self.zip_radio = QRadioButton("ZIP 压缩包（方便分享，也能直接拖回启动器）")
        self.zip_radio.setChecked(True)
        self.zip_radio.toggled.connect(self._sync_destination)
        self.folder_radio = QRadioButton("文件夹（解压好的目录，可直接查看和修改）")
        self.folder_radio.toggled.connect(self._sync_destination)
        self.body.addWidget(self.zip_radio)
        self.body.addWidget(self.folder_radio)

        dest_label = QLabel("保存位置")
        dest_label.setObjectName("SegLabel")
        self.body.addWidget(dest_label)
        dest_row = QHBoxLayout()
        dest_row.setSpacing(8)
        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        self.path_edit.setPlaceholderText("还没有选择保存位置")
        self.choose_btn = QPushButton("选择位置")
        self.choose_btn.setObjectName("Secondary")
        self.choose_btn.setIcon(icons.icon("folder", colors()["text2"], 15))
        self.choose_btn.setCursor(Qt.PointingHandCursor)
        self.choose_btn.clicked.connect(self._choose_destination)
        dest_row.addWidget(self.path_edit, 1)
        dest_row.addWidget(self.choose_btn)
        self.body.addLayout(dest_row)

        note = QLabel(
            "会保留：世界存档、玩家数据、白名单 / OP / 封禁名单、"
            "server.properties、插件或 Mod 及其配置。"
            "不会打包 logs、crash-reports、缓存和 session.lock。"
        )
        note.setObjectName("Tiny")
        note.setWordWrap(True)
        self.body.addWidget(note)

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
        self.open_btn = self.add_button("打开所在文件夹", "Soft", self._open_result)
        self.open_btn.setVisible(False)
        self.export_btn = self.add_button("开始打包", "Primary", self._start_export)
        self._sync_destination()

    # ------------------------------------------------------------------
    def _current_target(self) -> Path:
        name = f"{self._folder_name}.zip" if self.zip_radio.isChecked() else self._folder_name
        return self._target_dir / name

    def _sync_destination(self) -> None:
        target = self._current_target()
        if not self.zip_radio.isChecked():
            target = _unique_directory(target)
        self.path_edit.setText(str(target))

    def _choose_destination(self) -> None:
        current = self._current_target()
        if self.zip_radio.isChecked():
            path, _ = QFileDialog.getSaveFileName(
                self,
                "保存服务端压缩包",
                str(current),
                "ZIP 压缩包 (*.zip)",
            )
            if not path:
                return
            target = Path(path)
            if target.suffix.lower() != ".zip":
                target = target.with_suffix(".zip")
            self._target_dir = target.parent
            self.path_edit.setText(str(target))
            return
        folder = QFileDialog.getExistingDirectory(
            self,
            "选择保存位置",
            str(current.parent if current.parent.exists() else self._target_dir),
        )
        if not folder:
            return
        self._target_dir = Path(folder)
        self._sync_destination()

    def _start_export(self) -> None:
        if self._busy:
            return
        target_text = self.path_edit.text().strip()
        if not target_text:
            self._show_error("先选一个保存位置吧。")
            return
        target = Path(target_text)
        as_zip = self.zip_radio.isChecked()
        if as_zip and target.suffix.lower() != ".zip":
            target = target.with_suffix(".zip")
            self.path_edit.setText(str(target))
        if not as_zip:
            target = _unique_directory(target)
            self.path_edit.setText(str(target))
        if as_zip and target.exists():
            dialog = ConfirmDialog(
                self,
                "覆盖已有文件",
                f"{target.name} 已经存在，继续会覆盖它。确定吗？",
                primary_text="覆盖",
                secondary_text="取消",
                icon_name="warning",
                icon_color=colors()["orange"],
            )
            if not dialog.exec():
                return
        self._hide_error()
        self._begin_busy()
        worker = ServerPackExportWorker(
            self.info, target, as_zip=as_zip, parent=self
        )
        self._worker = worker
        worker.progress.connect(self._on_progress)
        worker.log_line.connect(self.main.log)
        worker.finished.connect(self._on_finished)
        worker.start()

    def _begin_busy(self) -> None:
        self._busy = True
        self._hide_error()
        self.zip_radio.setEnabled(False)
        self.folder_radio.setEnabled(False)
        self.choose_btn.setEnabled(False)
        self.progress.setValue(0)
        self.progress_box.setVisible(True)
        self.progress_text.setText("正在扫描服务端文件…")
        self.export_btn.setEnabled(False)
        self.cancel_btn.setText("取消打包")

    def _on_progress(self, percent: int, message: str) -> None:
        self.progress.setValue(percent)
        self.progress_text.setText(message)

    def _on_finished(self, success: bool, message: str, path: str) -> None:
        self._worker = None
        self._busy = False
        if success:
            self._result_path = path
            self.progress.setValue(100)
            self.progress_text.setText(message)
            self.export_btn.setEnabled(False)
            self.export_btn.setText("打包完成")
            self.cancel_btn.setText("关闭")
            self.open_btn.setVisible(True)
            self.main.log(message)
            self.main.toast("服务端已经装进纸箱，喵。", "success")
            return
        self.progress_box.setVisible(False)
        self.zip_radio.setEnabled(True)
        self.folder_radio.setEnabled(True)
        self.choose_btn.setEnabled(True)
        self.export_btn.setEnabled(True)
        self.export_btn.setText("重新打包")
        self.cancel_btn.setText("取消")
        self._show_error(message or "打包失败")
        self.main.log(f"服务端打包失败：{message}")

    def _open_result(self) -> None:
        if not self._result_path:
            return
        target = Path(self._result_path)
        folder = target if target.is_dir() else target.parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _on_cancel(self) -> None:
        if self._busy and self._worker:
            self._worker.cancel()
            self.cancel_btn.setEnabled(False)
            self.progress_text.setText("正在把纸箱收回来…")
            return
        self.reject()

    def _show_error(self, text: str) -> None:
        self.error.setText(text)
        self.error.setVisible(True)

    def _hide_error(self) -> None:
        self.error.clear()
        self.error.setVisible(False)

    def reject(self) -> None:
        if self._busy:
            dialog = ConfirmDialog(
                self,
                "停止打包",
                "纸箱还没封好，现在离开会取消打包。确定吗？",
                primary_text="停止打包",
                secondary_text="继续",
                icon_name="warning",
                icon_color=colors()["orange"],
            )
            if not dialog.exec():
                return
            if self._worker:
                self._worker.cancel()
            self._busy = False
        super().reject()

    def result_path(self) -> str:
        return self._result_path


class ServerPackImportDialog(StyledDialog):
    """从本地 ZIP 或文件夹导入服务端，保留世界、玩家与权限数据。"""

    def __init__(
        self,
        main,
        initial_path: Path | str | None = None,
    ) -> None:
        super().__init__(main, "领养一个服务端", "server", colors()["accent"], width=640)
        self.main = main
        self._info: ServerPackInfo | None = None
        self._worker: ServerPackImportWorker | None = None
        self._busy = False
        self.imported_folder = ""

        intro = QLabel(
            "把别人打包好的服务端 ZIP 或文件夹拖进来。"
            "猫猫会完整保留世界存档、玩家数据、白名单 / OP / 封禁名单、"
            "server.properties 和插件或 Mod 配置。"
        )
        intro.setObjectName("DialogText")
        intro.setWordWrap(True)
        self.body.addWidget(intro)

        source_label = QLabel("服务端 ZIP 或文件夹")
        source_label.setObjectName("SegLabel")
        self.body.addWidget(source_label)
        source_row = QHBoxLayout()
        source_row.setSpacing(8)
        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        self.path_edit.setPlaceholderText("还没有选择服务端包")
        self.file_btn = QPushButton("选择 ZIP")
        self.file_btn.setObjectName("Secondary")
        self.file_btn.setIcon(icons.icon("package", colors()["text2"], 15))
        self.file_btn.setCursor(Qt.PointingHandCursor)
        self.file_btn.clicked.connect(self._choose_file)
        self.dir_btn = QPushButton("选择文件夹")
        self.dir_btn.setObjectName("Secondary")
        self.dir_btn.setIcon(icons.icon("folder", colors()["text2"], 15))
        self.dir_btn.setCursor(Qt.PointingHandCursor)
        self.dir_btn.clicked.connect(self._choose_folder)
        source_row.addWidget(self.path_edit, 1)
        source_row.addWidget(self.file_btn)
        source_row.addWidget(self.dir_btn)
        self.body.addLayout(source_row)

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
        self.preview_type = badge("", "blue")
        head.addWidget(self.preview_type)
        self.preview_version = badge("", "purple")
        head.addWidget(self.preview_version)
        head.addStretch(1)
        preview_layout.addLayout(head)

        self.preview_meta = QLabel("")
        self.preview_meta.setObjectName("Tiny")
        self.preview_meta.setWordWrap(True)
        preview_layout.addWidget(self.preview_meta)
        self.preview_worlds = QLabel("")
        self.preview_worlds.setObjectName("Tiny")
        self.preview_worlds.setWordWrap(True)
        preview_layout.addWidget(self.preview_worlds)
        self.preview_data = QLabel("")
        self.preview_data.setObjectName("Tiny")
        self.preview_data.setWordWrap(True)
        preview_layout.addWidget(self.preview_data)
        self.preview_state = QLabel("")
        self.preview_state.setWordWrap(True)
        preview_layout.addWidget(self.preview_state)
        self.preview.setVisible(False)
        self.body.addWidget(self.preview)

        self.options = QWidget()
        options_layout = QVBoxLayout(self.options)
        options_layout.setContentsMargins(0, 0, 0, 0)
        options_layout.setSpacing(6)
        name_label = QLabel("导入后的服务端名称")
        name_label.setObjectName("SegLabel")
        options_layout.addWidget(name_label)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("例如：猫猫的生存服")
        self.name_edit.setMaxLength(64)
        self.name_edit.textChanged.connect(self._hide_error)
        options_layout.addWidget(self.name_edit)
        self.name_hint = QLabel(
            "同名服务端会自动使用不同的文件夹名，原来的世界和玩家数据不会被改动。"
        )
        self.name_hint.setObjectName("Tiny")
        self.name_hint.setWordWrap(True)
        options_layout.addWidget(self.name_hint)
        self.options.setVisible(False)
        self.body.addWidget(self.options)

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

        self.cancel_btn = self.add_button("取消", "Secondary", self._on_cancel)
        self.import_btn = self.add_button("导入服务端", "Primary", self._start_import)
        self.import_btn.setEnabled(False)
        if initial_path:
            self._load_source(Path(initial_path))

    # ------------------------------------------------------------------
    def _choose_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择服务端压缩包",
            str(Path.home()),
            "服务端压缩包 (*.zip);;所有文件 (*)",
        )
        if path:
            self._load_source(Path(path))

    def _choose_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "选择服务端文件夹", str(Path.home())
        )
        if path:
            self._load_source(Path(path))

    def _load_source(self, path: Path) -> None:
        self._info = None
        self.preview.setVisible(False)
        self.options.setVisible(False)
        self.import_btn.setEnabled(False)
        self.path_edit.setText(str(path))
        self._hide_error()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            info = inspect_server_pack(path)
        except Exception as exc:
            self._show_error(str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()

        self._info = info
        self.preview.setVisible(True)
        self.options.setVisible(True)
        self.preview_name.setText(info.name or path.stem)
        self.preview_type.setText(info.type_name)
        self.preview_version.setText(info.display_version)
        meta = f"{info.total_files} 个文件 · 解压后 {info.size_text}"
        if info.is_zip:
            meta += f" · 压缩包 {human_size(info.archive_bytes)}"
        self.preview_meta.setText(meta)
        self.preview_worlds.setText("世界：" + info.world_text)

        data_parts = [f"玩家数据 {info.players} 份"]
        if info.has_whitelist:
            data_parts.append("白名单")
        if info.has_ops:
            data_parts.append("OP 名单")
        if info.has_banned:
            data_parts.append("封禁名单")
        data_parts.append("server.properties")
        self.preview_data.setText("保留：" + " · ".join(data_parts))

        if info.installed:
            self.preview_state.setText("检测到服务端启动文件，导入后可以直接启动。")
            self.preview_state.setStyleSheet(
                f"color: {colors()['green']}; font-size: 11px;"
            )
        else:
            self.preview_state.setText(
                "没有检测到服务端启动文件。导入后可以打开设置页重新安装，"
                "世界和玩家数据会保留。"
            )
            self.preview_state.setStyleSheet(
                f"color: {colors()['orange']}; font-size: 11px;"
            )

        self.name_edit.setText(_unique_server_name(info.name or path.stem))
        self.import_btn.setEnabled(True)
        self.import_btn.setText("导入服务端")

    def _start_import(self) -> None:
        if self._busy or self._info is None:
            return
        name = self.name_edit.text().strip()
        if not name:
            self._show_error("导入名称不能为空。")
            return
        self._hide_error()
        self._begin_busy()
        worker = ServerPackImportWorker(
            self._info.path, name=name, parent=self
        )
        self._worker = worker
        worker.progress.connect(self._on_progress)
        worker.log_line.connect(self.main.log)
        worker.finished.connect(self._on_finished)
        worker.start()

    def _begin_busy(self) -> None:
        self._busy = True
        self.file_btn.setEnabled(False)
        self.dir_btn.setEnabled(False)
        self.name_edit.setEnabled(False)
        self.import_btn.setEnabled(False)
        self.progress.setValue(0)
        self.progress_box.setVisible(True)
        self.progress_text.setText("正在给新服务端腾位置…")
        self.cancel_btn.setText("取消导入")

    def _on_progress(self, percent: int, message: str) -> None:
        self.progress.setValue(percent)
        self.progress_text.setText(message)

    def _on_finished(self, success: bool, message: str, folder: str) -> None:
        self._worker = None
        self._busy = False
        if success:
            self.imported_folder = folder
            self.progress.setValue(100)
            self.progress_text.setText(message)
            self.main.log(f"服务端导入完成：{folder}")
            self.accept()
            return
        self.progress_box.setVisible(False)
        self.file_btn.setEnabled(True)
        self.dir_btn.setEnabled(True)
        self.name_edit.setEnabled(True)
        self.import_btn.setEnabled(True)
        self.import_btn.setText("重新导入")
        self.cancel_btn.setText("取消")
        self._show_error(message or "导入失败")
        self.main.log(f"服务端导入失败：{message}")

    def _on_cancel(self) -> None:
        if self._busy and self._worker:
            self._worker.cancel()
            self.cancel_btn.setEnabled(False)
            self.progress_text.setText("正在收回猫爪…")
            return
        self.reject()

    def _show_error(self, text: str) -> None:
        self.error.setText(text)
        self.error.setVisible(True)

    def _hide_error(self) -> None:
        self.error.clear()
        self.error.setVisible(False)

    def reject(self) -> None:
        if self._busy:
            dialog = ConfirmDialog(
                self,
                "停止导入",
                "服务端还没搬完，现在离开会取消导入。确定吗？",
                primary_text="停止",
                secondary_text="继续",
                icon_name="warning",
                icon_color=colors()["orange"],
            )
            if not dialog.exec():
                return
            if self._worker:
                self._worker.cancel()
            self._busy = False
        super().reject()
