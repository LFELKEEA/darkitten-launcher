from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from ..java import pick_java
from ..loaders import LOADER_NAMES
from ..migration import (
    CATEGORY_NAMES,
    DEFAULT_CATEGORIES,
    MigrationError,
    collect_migration_plan,
)
from ..versions import resolve_version
from ..workers import LoaderVersionsWorker, MigrationWorker
from .dialogs import StyledDialog
from .widgets import Card, SegmentedControl

CATEGORY_ORDER = (
    "saves",
    "config",
    "mods",
    "resourcepacks",
    "shaderpacks",
    "options",
)

CATEGORY_HINTS = {
    "saves": "存档",
    "config": "配置",
    "mods": "模组",
    "resourcepacks": "资源包",
    "shaderpacks": "光影",
    "options": "选项 / 服务器列表",
}


class MigrationDialog(StyledDialog):
    """把存档、配置和 Mod 复制到另一个版本，或升级加载器后迁移。"""

    def __init__(self, main, source_id: str = "") -> None:
        super().__init__(main, "迁移游戏版本", "package", width=720)
        self.main = main
        self._busy = False
        self._finished_ok = False
        self._finished_target = ""
        self._versions_worker: LoaderVersionsWorker | None = None
        self._worker: MigrationWorker | None = None
        self._plan = None
        self._loader_error = ""
        self._summaries = {
            v.id: v for v in main.versions if not v.error
        }

        intro = QLabel(
            "把源版本的存档、配置、资源包和 Mod 复制到目标版本，源版本不会被修改。"
            "迁移到另一个已安装版本，或为同一个 MC 版本安装新的加载器后迁移。"
        )
        intro.setObjectName("DialogText")
        intro.setWordWrap(True)
        self.body.addWidget(intro)

        self.mode_tabs = SegmentedControl()
        self.mode_tabs.add_item("迁移到已有版本", "existing")
        self.mode_tabs.add_item("升级加载器", "loader")
        self.body.addWidget(self.mode_tabs)

        self.body.addWidget(self._build_form())
        self.body.addWidget(self._build_categories())
        self.body.addWidget(self._build_options())

        self.preview_box = QFrame()
        self.preview_box.setObjectName("SoftCard")
        self.preview_box.setAttribute(Qt.WA_StyledBackground, True)
        preview_layout = QVBoxLayout(self.preview_box)
        preview_layout.setContentsMargins(12, 9, 12, 9)
        preview_layout.setSpacing(3)
        self.preview = QLabel("选择源版本后显示迁移内容。")
        self.preview.setObjectName("Tiny")
        self.preview.setWordWrap(True)
        preview_layout.addWidget(self.preview)
        self.body.addWidget(self.preview_box)

        self.progress_box = QWidget()
        progress_layout = QVBoxLayout(self.progress_box)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.setSpacing(5)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        self.progress_text = QLabel("")
        self.progress_text.setObjectName("Tiny")
        progress_layout.addWidget(self.progress)
        progress_layout.addWidget(self.progress_text)
        self.progress_box.setVisible(False)
        self.body.addWidget(self.progress_box)

        self.log = QPlainTextEdit()
        self.log.setObjectName("SoftLog")
        self.log.setReadOnly(True)
        self.log.setFixedHeight(128)
        self.log.setMaximumBlockCount(1200)
        self.log.setPlaceholderText("迁移日志会显示在这里")
        self.log.setVisible(False)
        self.body.addWidget(self.log)

        note = QLabel(
            "Mod 会先通过 Modrinth 的 SHA-1 记录匹配目标加载器/MC 版本，"
            "匹配失败时按下方选项保留原文件或跳过。"
        )
        note.setObjectName("Tiny")
        note.setWordWrap(True)
        self.body.addWidget(note)

        self.close_btn = self.add_button("关闭", "Secondary", self.reject)
        self.start_btn = self.add_button("开始迁移", "Primary", self._start_or_finish)
        self.start_btn.setMinimumWidth(110)

        self._populate_sources(source_id)
        self._mode_changed(self.mode_tabs.current())
        self.mode_tabs.changed.connect(self._mode_changed)
        self.source_combo.currentIndexChanged.connect(lambda _: self._source_changed())
        self.target_combo.currentIndexChanged.connect(lambda _: self._refresh_preview())
        self.loader_tabs.changed.connect(lambda _: self._reload_loader_versions())
        self.loader_version_combo.currentIndexChanged.connect(
            lambda _: self._update_info()
        )
        for box in self.category_boxes.values():
            box.toggled.connect(lambda _: self._refresh_preview())
        main.java_installs_changed.connect(lambda _: self._update_info())
        main.ensure_java_detected()
        self._refresh_preview()
        self._update_action()
        self.adjustSize()

    # ------------------------------------------------------------------
    def _build_form(self) -> QWidget:
        form = QFrame()
        form.setObjectName("SoftCard")
        form.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(form)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(9)

        source_row = QHBoxLayout()
        source_row.setSpacing(10)
        source_label = QLabel("源版本")
        source_label.setFixedWidth(70)
        source_label.setStyleSheet("font-weight: 700; font-size: 12px;")
        self.source_combo = QComboBox()
        self.source_combo.setMinimumWidth(380)
        source_row.addWidget(source_label)
        source_row.addWidget(self.source_combo, 1)
        layout.addLayout(source_row)

        self.existing_box = QWidget()
        existing = QVBoxLayout(self.existing_box)
        existing.setContentsMargins(0, 0, 0, 0)
        existing.setSpacing(9)
        target_row = QHBoxLayout()
        target_row.setSpacing(10)
        target_label = QLabel("目标版本")
        target_label.setFixedWidth(70)
        target_label.setStyleSheet("font-weight: 700; font-size: 12px;")
        self.target_combo = QComboBox()
        self.target_combo.setMinimumWidth(380)
        target_row.addWidget(target_label)
        target_row.addWidget(self.target_combo, 1)
        existing.addLayout(target_row)
        self.existing_hint = QLabel("")
        self.existing_hint.setObjectName("Tiny")
        self.existing_hint.setWordWrap(True)
        existing.addWidget(self.existing_hint)
        layout.addWidget(self.existing_box)

        self.loader_box = QWidget()
        loader = QVBoxLayout(self.loader_box)
        loader.setContentsMargins(0, 0, 0, 0)
        loader.setSpacing(9)
        kind_row = QHBoxLayout()
        kind_row.setSpacing(10)
        kind_label = QLabel("新加载器")
        kind_label.setFixedWidth(70)
        kind_label.setStyleSheet("font-weight: 700; font-size: 12px;")
        self.loader_tabs = SegmentedControl()
        self.loader_tabs.add_item("Fabric", "fabric")
        self.loader_tabs.add_item("NeoForge", "neoforge")
        self.loader_tabs.add_item("Forge", "forge")
        kind_row.addWidget(kind_label)
        kind_row.addWidget(self.loader_tabs)
        kind_row.addStretch(1)
        loader.addLayout(kind_row)

        version_row = QHBoxLayout()
        version_row.setSpacing(10)
        version_label = QLabel("加载器版本")
        version_label.setFixedWidth(70)
        version_label.setStyleSheet("font-weight: 700; font-size: 12px;")
        self.loader_version_combo = QComboBox()
        self.loader_version_combo.setMinimumWidth(380)
        version_row.addWidget(version_label)
        version_row.addWidget(self.loader_version_combo, 1)
        loader.addLayout(version_row)

        self.loader_info = QLabel("")
        self.loader_info.setObjectName("Tiny")
        self.loader_info.setWordWrap(True)
        loader.addWidget(self.loader_info)
        layout.addWidget(self.loader_box)

        self.empty_label = QLabel("")
        self.empty_label.setObjectName("Muted")
        self.empty_label.setWordWrap(True)
        self.empty_label.setVisible(False)
        layout.addWidget(self.empty_label)
        return form

    def _build_categories(self) -> QWidget:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(7)
        title = QLabel("要迁移的内容")
        title.setObjectName("H2")
        layout.addWidget(title)

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(4)
        self.category_boxes: dict[str, QCheckBox] = {}
        for index, key in enumerate(CATEGORY_ORDER):
            box = QCheckBox(CATEGORY_HINTS[key])
            box.setChecked(key in DEFAULT_CATEGORIES)
            self.category_boxes[key] = box
            grid.addWidget(box, index // 3, index % 3)
        layout.addLayout(grid)
        tip = QLabel("“选项”包含 options.txt、optionsof.txt 和 servers.dat。")
        tip.setObjectName("Tiny")
        layout.addWidget(tip)
        return card

    def _build_options(self) -> QWidget:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 11, 14, 11)
        layout.setSpacing(5)
        self.match_mods_box = QCheckBox("通过 Modrinth 匹配目标加载器/MC 版本的 Mod")
        self.match_mods_box.setChecked(True)
        self.copy_unmatched_box = QCheckBox("匹配失败时保留原 Mod 文件（可能不兼容）")
        self.copy_unmatched_box.setChecked(True)
        self.overwrite_box = QCheckBox("覆盖目标版本中的同名文件")
        self.overwrite_box.setChecked(False)
        layout.addWidget(self.match_mods_box)
        layout.addWidget(self.copy_unmatched_box)
        layout.addWidget(self.overwrite_box)
        self.match_mods_box.toggled.connect(self._update_option_state)
        return card

    # ------------------------------------------------------------------
    def _populate_sources(self, preferred: str) -> None:
        self.source_combo.blockSignals(True)
        for summary in self.main.versions:
            if summary.error:
                continue
            label = summary.id
            if summary.loader:
                label += f" · {summary.loader}"
            elif summary.type_name:
                label += f" · {summary.type_name}"
            self.source_combo.addItem(label, summary.id)
        if not self.source_combo.count():
            self.source_combo.addItem("没有可迁移的版本", "")
        index = self.source_combo.findData(preferred)
        if index < 0:
            index = self.source_combo.findData(self.main.selected_version)
        if index < 0:
            index = 0
        self.source_combo.setCurrentIndex(index)
        self.source_combo.blockSignals(False)
        self._populate_targets()
        if not self.main.versions:
            self.empty_label.setText(
                "还没有安装版本。先到“下载”页面安装游戏，再回来迁移。"
            )
            self.empty_label.setVisible(True)
            self.start_btn.setEnabled(False)

    def _populate_targets(self) -> None:
        source = self.source_combo.currentData()
        self.target_combo.blockSignals(True)
        self.target_combo.clear()
        candidates = [
            v for v in self.main.versions if not v.error and v.id != source
        ]
        for summary in candidates:
            label = summary.id
            if summary.loader:
                label += f" · {summary.loader}"
            elif summary.type_name:
                label += f" · {summary.type_name}"
            if summary.id == self.main.selected_version:
                label += "  ·  当前选中"
            self.target_combo.addItem(label, summary.id)
        if not candidates:
            self.target_combo.addItem("没有其他已安装版本", "")
        self.target_combo.blockSignals(False)
        self.existing_hint.setText(
            "目标版本必须已经安装。若想安装新的加载器版本，请切换到“升级加载器”。"
        )
        if not candidates:
            self.existing_hint.setText(
                "至少需要两个已安装版本才能迁移到已有版本；"
                "也可以切换到“升级加载器”，为同一个 MC 版本安装新的加载器。"
            )

    def _source_changed(self) -> None:
        self._populate_targets()
        self._refresh_preview()
        self._reload_loader_versions()
        self._update_info()

    def _mode_changed(self, mode: str) -> None:
        loader_mode = mode == "loader"
        self.existing_box.setVisible(not loader_mode)
        self.loader_box.setVisible(loader_mode)
        self.start_btn.setText("安装并迁移" if loader_mode else "开始迁移")
        if loader_mode:
            self._reload_loader_versions()
        self._refresh_preview()
        self._update_info()
        self._update_action()
        self.adjustSize()

    # ------------------------------------------------------------------
    def _source_mc(self) -> str:
        source_id = str(self.source_combo.currentData() or "")
        if not source_id:
            return ""
        try:
            resolved = resolve_version(self.main.config.game_path, source_id)
            return str(resolved.chain[0] if resolved.chain else resolved.id)
        except Exception:
            return source_id

    def _source_summary(self):
        return self._summaries.get(str(self.source_combo.currentData() or ""))

    def _chosen_java(self):
        summary = self._source_summary()
        required = max(8, summary.java_major if summary else 8)
        java = pick_java(
            self.main.java_installs, required, self.main.config.java_path
        )
        if java and java.major and java.major < required:
            return None, required, java
        return java, required, None

    def _reload_loader_versions(self) -> None:
        if self._busy or self.mode_tabs.current() != "loader":
            return
        kind = self.loader_tabs.current()
        mc_version = self._source_mc()
        self._loader_error = ""
        self.loader_version_combo.blockSignals(True)
        self.loader_version_combo.clear()
        self.loader_version_combo.blockSignals(False)
        if not mc_version:
            self._loader_error = "请先选择源版本。"
            self.loader_version_combo.addItem("请先选择源版本", "")
            self._update_action()
            return
        self.loader_version_combo.addItem("正在获取版本列表…", "")
        worker = LoaderVersionsWorker(kind, mc_version, self)
        self._versions_worker = worker
        worker.ready.connect(
            lambda data, error, w=worker: self._loader_versions_ready(
                w, data, error
            )
        )
        worker.start()
        self._update_action()

    def _loader_versions_ready(self, worker, data, error: str) -> None:
        if worker is not self._versions_worker:
            return
        self.loader_version_combo.blockSignals(True)
        self.loader_version_combo.clear()
        if error:
            self._loader_error = (
                f"获取 {LOADER_NAMES.get(worker.kind, '')} 版本失败：{error}"
            )
            self.loader_version_combo.addItem("获取失败", "")
            self.loader_version_combo.blockSignals(False)
            self._update_info()
            return
        if not data:
            self._loader_error = (
                f"{LOADER_NAMES.get(worker.kind, '')} 目前没有适配 "
                f"{worker.mc_version} 的版本。"
            )
            self.loader_version_combo.addItem("没有可用版本", "")
            self.loader_version_combo.blockSignals(False)
            self._update_info()
            return
        self._loader_error = ""
        first_stable = next(
            (i for i, item in enumerate(data) if item.get("stable")), -1
        )
        for index, item in enumerate(data):
            version = str(item.get("version") or "")
            suffix = ""
            if index == first_stable:
                suffix = "  ·  推荐"
            elif first_stable < 0 and index == 0:
                suffix = "  ·  最新"
            self.loader_version_combo.addItem(version + suffix, version)
        self.loader_version_combo.setCurrentIndex(
            first_stable if first_stable >= 0 else 0
        )
        self.loader_version_combo.blockSignals(False)
        self._update_info()

    def _update_info(self) -> None:
        if self.mode_tabs.current() != "loader":
            self._update_action()
            return
        if self._loader_error:
            self.loader_info.setText(self._loader_error)
            self._update_action()
            return
        kind = self.loader_tabs.current()
        version = str(self.loader_version_combo.currentData() or "")
        mc_version = self._source_mc()
        parts = []
        if version and mc_version:
            parts.append(
                f"将在 MC {mc_version} 上安装 "
                f"{LOADER_NAMES.get(kind, kind)} {version}，安装完成后自动迁移。"
            )
        if kind in ("forge", "neoforge"):
            java, required, fallback = self._chosen_java()
            if java:
                parts.append(
                    f"将使用 Java {java.major or '?'}（{java.path}）运行安装器。"
                )
            elif fallback:
                parts.append(
                    f"需要 Java {required}+，当前只有 Java "
                    f"{fallback.major or '?'}，请先在设置中指定 Java。"
                )
            else:
                parts.append(
                    f"未检测到 Java，安装 {LOADER_NAMES[kind]} 需要 Java {required}+。"
                )
        self.loader_info.setText("\n".join(parts))
        self._update_action()

    def _selected_categories(self) -> list[str]:
        return [
            key for key in CATEGORY_ORDER
            if key in self.category_boxes and self.category_boxes[key].isChecked()
        ]

    def _refresh_preview(self) -> None:
        if self._busy:
            return
        source_id = str(self.source_combo.currentData() or "")
        if not source_id:
            self.preview.setText("没有可迁移的源版本。")
            self._update_action()
            return
        categories = self._selected_categories()
        if not categories:
            self.preview.setText("还没有勾选任何内容。")
            self._plan = None
            self._update_action()
            return
        loader_mode = self.mode_tabs.current() == "loader"
        target_id = "" if loader_mode else str(self.target_combo.currentData() or "")
        try:
            plan = collect_migration_plan(
                self.main.config.game_path,
                source_id,
                target_id,
                categories,
                isolated=bool(self.main.config.version_isolation),
            )
        except MigrationError as exc:
            self.preview.setText(str(exc))
            self._plan = None
            self._update_action()
            return
        except Exception as exc:
            self.preview.setText(f"无法读取迁移内容：{exc}")
            self._plan = None
            self._update_action()
            return
        self._plan = plan
        counts = "、".join(
            f"{CATEGORY_NAMES.get(key, key)} {count}"
            for key, count in plan.file_counts.items()
            if count
        )
        lines = [
            f"源：{plan.source_id}（MC {plan.source_mc}"
            + (f" · {plan.source_loader}" if plan.source_loader else "")
            + "）"
        ]
        if target_id:
            lines.append(
                f"目标：{plan.target_id}（MC {plan.target_mc}"
                + (f" · {plan.target_loader}" if plan.target_loader else "")
                + "）"
            )
        elif loader_mode:
            lines.append("目标：安装新加载器后创建的版本")
        lines.append(
            f"共 {plan.total_files} 个文件 · "
            f"{plan.total_bytes / 1024 / 1024:.1f} MB"
            + (f" · {counts}" if counts else " · 没有找到内容")
        )
        if plan.same_dir:
            lines.append("源与目标共用同一个游戏目录，只会重新匹配 Mod，不复制其他内容。")
        elif plan.conflicts:
            lines.append(
                f"目标已有 {plan.conflicts} 个同名文件，默认跳过；"
                "需要替换时勾选“覆盖同名文件”。"
            )
        self.preview.setText("\n".join(lines))
        self._update_action()

    def _update_option_state(self) -> None:
        self.copy_unmatched_box.setEnabled(
            self.match_mods_box.isChecked() and not self._busy
        )

    def _update_action(self) -> None:
        if self._busy or self._finished_ok:
            return
        enabled = bool(self.source_combo.currentData())
        enabled = enabled and bool(self._selected_categories())
        if self.mode_tabs.current() == "loader":
            enabled = enabled and bool(self.loader_version_combo.currentData())
            if self.loader_tabs.current() in ("forge", "neoforge"):
                java, _required, _fallback = self._chosen_java()
                enabled = enabled and java is not None
        else:
            enabled = enabled and bool(self.target_combo.currentData())
        self.start_btn.setEnabled(enabled)

    # ------------------------------------------------------------------
    def _start_or_finish(self) -> None:
        if self._finished_ok:
            self.accept()
            return
        if self._busy:
            self._cancel()
            return
        if not self._selected_categories():
            self.main.toast("至少勾选一类要迁移的内容", "info")
            return
        source_id = str(self.source_combo.currentData() or "")
        if not source_id:
            self.main.toast("请选择源版本", "info")
            return

        loader_mode = self.mode_tabs.current() == "loader"
        loader_kind = ""
        loader_version = ""
        java_path = ""
        target_id = ""
        if loader_mode:
            loader_kind = self.loader_tabs.current()
            loader_version = str(self.loader_version_combo.currentData() or "")
            if not loader_version:
                self.main.toast("请选择加载器版本", "info")
                return
            if loader_kind in ("forge", "neoforge"):
                java, _required, _fallback = self._chosen_java()
                if not java:
                    self.main.toast("未找到满足要求的 Java，请先在设置中指定", "error")
                    return
                java_path = java.path
        else:
            target_id = str(self.target_combo.currentData() or "")
            if not target_id:
                self.main.toast("请选择目标版本", "info")
                return

        self._busy = True
        self._finished_ok = False
        self._set_busy(True)
        self.progress.setValue(0)
        self.progress_text.setText("准备迁移…")
        self.progress_box.setVisible(True)
        self.log.clear()
        self.log.setVisible(True)
        self.start_btn.setText("取消迁移")
        self.close_btn.setEnabled(False)
        self.adjustSize()

        worker = MigrationWorker(
            self.main.config.game_path,
            source_id,
            target_id,
            self._selected_categories(),
            match_mods=self.match_mods_box.isChecked(),
            copy_unmatched=self.copy_unmatched_box.isEnabled()
            and self.copy_unmatched_box.isChecked(),
            overwrite=self.overwrite_box.isChecked(),
            isolated=bool(self.main.config.version_isolation),
            loader_kind=loader_kind,
            loader_version=loader_version,
            java_path=java_path,
            source=self.main.config.download_source,
            threads=self.main.config.download_threads,
            parent=self,
        )
        self._worker = worker
        worker.progress.connect(self._on_progress)
        worker.log_line.connect(self._append_log)
        worker.finished.connect(self._on_finished)
        worker.start()

    def _cancel(self) -> None:
        if self._worker:
            self.progress_text.setText("正在取消…")
            self.start_btn.setEnabled(False)
            self._worker.cancel()

    def _set_busy(self, busy: bool) -> None:
        self.mode_tabs.setEnabled(not busy)
        self.source_combo.setEnabled(not busy and self.source_combo.count() > 0)
        self.target_combo.setEnabled(not busy)
        self.loader_tabs.setEnabled(not busy)
        self.loader_version_combo.setEnabled(not busy)
        for box in self.category_boxes.values():
            box.setEnabled(not busy)
        self.match_mods_box.setEnabled(not busy)
        self.copy_unmatched_box.setEnabled(
            not busy and self.match_mods_box.isChecked()
        )
        self.overwrite_box.setEnabled(not busy)
        self.close_btn.setEnabled(not busy)
        self.start_btn.setText("取消迁移" if busy else "开始迁移")
        if not busy and not self._finished_ok:
            self._update_action()

    def _on_progress(self, percent: int, message: str) -> None:
        self.progress.setValue(max(0, min(100, percent)))
        self.progress_text.setText(message)

    def _append_log(self, line: str) -> None:
        self.log.appendPlainText(line)
        bar = self.log.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _on_finished(self, success: bool, message: str, version_id: str) -> None:
        self._busy = False
        self._worker = None
        self._set_busy(False)
        self.close_btn.setEnabled(True)
        self.start_btn.setEnabled(True)
        if success:
            self._finished_ok = True
            self._finished_target = version_id
            self.progress.setValue(100)
            self.progress_text.setText("迁移完成")
            self.start_btn.setText("完成")
            if version_id:
                self.main.config.last_version = version_id
                self.main.config.save()
                self.main.refresh_versions()
                self.main.select_version(version_id)
            self.main.set_status("版本迁移完成", "success")
            self.main.toast(message or "迁移完成", "success")
            self._append_log("迁移完成：" + message)
            self._set_busy(False)
            self.start_btn.setText("完成")
            self.start_btn.setEnabled(True)
        else:
            self.progress_text.setText(message)
            self.start_btn.setText("安装并迁移" if self.mode_tabs.current() == "loader" else "开始迁移")
            self.main.toast(f"迁移失败：{message}", "error")
            self._append_log("迁移失败：" + message)
            self._update_action()

    def reject(self) -> None:
        if self._busy:
            self._cancel()
            return
        super().reject()
