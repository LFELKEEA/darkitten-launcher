from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from ..java import pick_java
from ..loaders import LOADER_NAMES
from ..versions import check_installation, resolve_version
from ..workers import LoaderInstallWorker, LoaderVersionsWorker
from .dialogs import StyledDialog
from .widgets import SegmentedControl

LOADER_HINTS = {
    "fabric": "直接写入 Fabric 版本配置，不需要运行安装器，也不需要 Java。",
    "forge": "将运行 Forge 官方安装器生成客户端文件，安装器本身需要 Java。",
    "neoforge": "将运行 NeoForge 官方安装器，文件下载会自动使用国内镜像加速。",
}


class LoaderDialog(StyledDialog):
    def __init__(self, main) -> None:
        super().__init__(main, "安装模组加载器", "download", width=660)
        self.main = main
        self._installing = False
        self._versions_worker: LoaderVersionsWorker | None = None
        self._install_worker: LoaderInstallWorker | None = None
        self._task = None
        self._bases = [
            v for v in main.versions if not v.loader and not v.error
        ]

        intro = QLabel(
            "为已安装的原版游戏添加 Fabric、NeoForge 或 Forge。"
            "加载器会单独保存到“加载器”页面，并自动绑定到选择的游戏版本；"
            "之后也可以把它分配给其他同基础版本的游戏。"
        )
        intro.setObjectName("DialogText")
        intro.setWordWrap(True)
        self.body.addWidget(intro)

        form = QFrame()
        form.setObjectName("SoftCard")
        form.setAttribute(Qt.WA_StyledBackground, True)
        form_layout = QVBoxLayout(form)
        form_layout.setContentsMargins(14, 12, 14, 12)
        form_layout.setSpacing(10)

        base_row = QHBoxLayout()
        base_row.setSpacing(10)
        base_label = QLabel("游戏版本")
        base_label.setFixedWidth(62)
        base_label.setStyleSheet("font-weight: 700; font-size: 12px;")
        self.base_combo = QComboBox()
        self.base_combo.setMinimumWidth(390)
        base_row.addWidget(base_label)
        base_row.addWidget(self.base_combo, 1)
        form_layout.addLayout(base_row)

        loader_row = QHBoxLayout()
        loader_row.setSpacing(10)
        loader_label = QLabel("加载器")
        loader_label.setFixedWidth(62)
        loader_label.setStyleSheet("font-weight: 700; font-size: 12px;")
        self.loader_tabs = SegmentedControl()
        self.loader_tabs.add_item("Fabric", "fabric")
        self.loader_tabs.add_item("NeoForge", "neoforge")
        self.loader_tabs.add_item("Forge", "forge")
        loader_row.addWidget(loader_label)
        loader_row.addWidget(self.loader_tabs)
        loader_row.addStretch(1)
        form_layout.addLayout(loader_row)

        version_row = QHBoxLayout()
        version_row.setSpacing(10)
        version_label = QLabel("加载器版本")
        version_label.setFixedWidth(62)
        version_label.setStyleSheet("font-weight: 700; font-size: 12px;")
        self.version_combo = QComboBox()
        self.version_combo.setMinimumWidth(390)
        version_row.addWidget(version_label)
        version_row.addWidget(self.version_combo, 1)
        form_layout.addLayout(version_row)

        self.info_label = QLabel("")
        self.info_label.setObjectName("Tiny")
        self.info_label.setWordWrap(True)
        form_layout.addWidget(self.info_label)

        self.empty_label = QLabel("")
        self.empty_label.setObjectName("Muted")
        self.empty_label.setWordWrap(True)
        self.empty_label.setVisible(False)
        form_layout.addWidget(self.empty_label)
        self.body.addWidget(form)

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
        self.log.setPlaceholderText("安装日志会显示在这里")
        self.log.setVisible(False)
        self.body.addWidget(self.log)

        note = QLabel("版本列表来自官方元数据；Forge、NeoForge、Fabric 的文件会自动通过国内镜像加速下载。")
        note.setObjectName("Tiny")
        note.setWordWrap(True)
        self.body.addWidget(note)

        self.close_btn = self.add_button("关闭", "Secondary", self.reject)
        self.install_btn = self.add_button("安装", "Primary", self._install)
        self.install_btn.setMinimumWidth(104)
        self.finished.connect(self._detach_download_center)

        self._populate_bases()
        self.base_combo.currentIndexChanged.connect(lambda _: self._reload_versions())
        self.loader_tabs.changed.connect(lambda _: self._reload_versions())
        self.version_combo.currentIndexChanged.connect(lambda _: self._update_action())
        main.java_installs_changed.connect(self._refresh_java)
        main.ensure_java_detected()
        self._reload_versions()
        self._refresh_java(main.java_installs)

    # ------------------------------------------------------------------
    def _populate_bases(self) -> None:
        self.base_combo.blockSignals(True)
        for summary in self._bases:
            label = summary.id
            if summary.type_name:
                label += f" · {summary.type_name}"
            self.base_combo.addItem(label, summary.id)
        preferred = self.main.selected_version
        index = self.base_combo.findData(preferred)
        if index < 0:
            summary = next(
                (v for v in self.main.versions if v.id == preferred), None
            )
            if summary and summary.loader:
                try:
                    base_id = resolve_version(
                        self.main.config.game_path, summary.id
                    ).chain[0]
                    index = self.base_combo.findData(base_id)
                except Exception:
                    index = -1
        if index >= 0:
            self.base_combo.setCurrentIndex(index)
        self.base_combo.blockSignals(False)

        if not self._bases:
            self.empty_label.setText(
                "还没有安装原版游戏。请先到“下载”页面安装一个原版版本，再回来添加加载器。"
            )
            self.empty_label.setVisible(True)
            self.base_combo.setEnabled(False)
            self.install_btn.setEnabled(False)

        summary = next(
            (v for v in self._bases if v.id == self.base_combo.currentData()),
            None,
        )
        if summary and summary.loader:
            self.loader_tabs.set_value(
                {"Fabric": "fabric", "Forge": "forge", "NeoForge": "neoforge"}.get(
                    summary.loader, "fabric"
                )
            )

    def _current_base(self):
        base_id = self.base_combo.currentData()
        return next((v for v in self._bases if v.id == base_id), None)

    def _refresh_java(self, _installs=None) -> None:
        self._update_info()
        self._update_action()

    def _chosen_java(self):
        summary = self._current_base()
        required = max(8, summary.java_major if summary else 8)
        java = pick_java(
            self.main.java_installs, required, self.main.config.java_path
        )
        if java and java.major and java.major < required:
            return None, required, java
        return java, required, None

    def _update_info(self) -> None:
        summary = self._current_base()
        if not summary:
            self.info_label.setText("")
            return
        kind = self.loader_tabs.current()
        parts = [LOADER_HINTS.get(kind, "")]
        if kind in ("forge", "neoforge"):
            java, required, fallback = self._chosen_java()
            if java:
                parts.append(
                    f"将使用 Java {java.major or '?'}（{java.path}）"
                )
            elif fallback:
                parts.append(
                    f"需要 Java {required}+，当前只有 Java {fallback.major or '?'}，请先在设置中指定 Java。"
                )
            else:
                parts.append(f"未检测到 Java，安装 {LOADER_NAMES[kind]} 需要 Java {required}+。")
        try:
            missing = check_installation(
                self.main.config.game_path,
                resolve_version(self.main.config.game_path, summary.id),
                check_assets=False,
            )
        except Exception:
            missing = []
        if missing:
            parts.append(f"基础版本缺少 {len(missing)} 项文件，安装时会自动补全。")
        self.info_label.setText("\n".join(p for p in parts if p))

    def _reload_versions(self) -> None:
        if self._installing:
            return
        summary = self._current_base()
        kind = self.loader_tabs.current()
        self.version_combo.blockSignals(True)
        self.version_combo.clear()
        self.version_combo.blockSignals(False)
        self.install_btn.setEnabled(False)
        self._update_info()
        if not summary or not kind:
            self.version_combo.addItem("没有可用的游戏版本", "")
            return

        self.version_combo.addItem("正在获取版本列表…", "")
        worker = LoaderVersionsWorker(kind, summary.id, self)
        self._versions_worker = worker
        worker.ready.connect(
            lambda data, error, w=worker: self._versions_ready(w, data, error)
        )
        worker.start()

    def _versions_ready(
        self, worker: LoaderVersionsWorker, data, error: str
    ) -> None:
        if worker is not self._versions_worker:
            return
        self.version_combo.blockSignals(True)
        self.version_combo.clear()
        if error:
            self.version_combo.addItem("获取失败", "")
            self.version_combo.blockSignals(False)
            self.info_label.setText(f"获取 {LOADER_NAMES.get(worker.kind, '')} 版本失败：{error}")
            self._update_action()
            return
        if not data:
            self.version_combo.addItem("没有可用版本", "")
            self.version_combo.blockSignals(False)
            self.info_label.setText(
                f"{LOADER_NAMES.get(worker.kind, '')} 目前没有适配该游戏版本的版本。"
            )
            self._update_action()
            return

        first_stable = next((i for i, item in enumerate(data) if item.get("stable")), -1)
        for index, item in enumerate(data):
            version = str(item.get("version") or "")
            suffix = ""
            if index == first_stable:
                suffix = "  ·  推荐"
            elif first_stable < 0 and index == 0:
                suffix = "  ·  最新"
            self.version_combo.addItem(version + suffix, version)
        self.version_combo.setCurrentIndex(first_stable if first_stable >= 0 else 0)
        self.version_combo.blockSignals(False)
        self._update_action()

    def _update_action(self) -> None:
        if self._installing:
            self.install_btn.setEnabled(True)
            return
        summary = self._current_base()
        version = self.version_combo.currentData()
        enabled = bool(summary and version)
        if enabled and self.loader_tabs.current() in ("forge", "neoforge"):
            java, _required, _fallback = self._chosen_java()
            enabled = java is not None
        self.install_btn.setEnabled(enabled)

    # ------------------------------------------------------------------
    def _install(self) -> None:
        if self._installing:
            self._cancel_install()
            return
        summary = self._current_base()
        kind = self.loader_tabs.current()
        version = str(self.version_combo.currentData() or "")
        if not summary or not version:
            return
        java_path = ""
        if kind in ("forge", "neoforge"):
            java, _required, _fallback = self._chosen_java()
            if not java:
                self.main.toast("未找到满足要求的 Java，请先在设置中指定", "error")
                return
            java_path = java.path

        self._installing = True
        self._set_busy(True)
        self.progress.setValue(0)
        self.progress_text.setText("准备安装…")
        self.progress_box.setVisible(True)
        self.log.clear()
        self.log.setVisible(True)
        self.install_btn.setText("取消安装")
        self.close_btn.setEnabled(True)
        self.close_btn.setText("后台运行")
        self.adjustSize()

        worker = LoaderInstallWorker(
            self.main.config.game_path,
            summary.id,
            kind,
            version,
            java_path=java_path,
            source=self.main.config.download_source,
            parent=self,
        )
        self._install_worker = worker
        worker.progress.connect(self._on_progress)
        worker.log_line.connect(self._append_log)
        worker.finished.connect(self._on_finished)
        self._task = self.main.download_center.start_worker(
            worker,
            title=f"安装加载器 · {kind} {version}",
            content_type="loader-install",
            target=self.main.config.version_game_path(version),
            source=self.main.config.download_source,
            metadata={
                "base_id": summary.id,
                "loader": kind,
                "loader_version": version,
            },
            forward_log=False,
        )

    def _cancel_install(self) -> None:
        if self._install_worker:
            self.progress_text.setText("正在取消…")
            self.install_btn.setEnabled(False)
            self._install_worker.cancel()

    def _set_busy(self, busy: bool) -> None:
        self.base_combo.setEnabled(not busy and bool(self._bases))
        self.loader_tabs.setEnabled(not busy)
        self.version_combo.setEnabled(not busy)
        self.close_btn.setEnabled(True)
        self.close_btn.setText("后台运行" if busy else "关闭")
        self.install_btn.setText("取消安装" if busy else "安装")
        if not busy:
            self._update_action()

    def _on_progress(self, percent: int, message: str) -> None:
        self.progress.setValue(max(0, min(100, percent)))
        self.progress_text.setText(message)

    def _append_log(self, line: str) -> None:
        self.log.appendPlainText(line)
        bar = self.log.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _detach_download_center(self, _result: int = 0) -> None:
        if self._task is not None and not self._task.running:
            self._task = None

    def _on_finished(self, success: bool, message: str, version_id: str) -> None:
        self._installing = False
        self._install_worker = None
        if self._task is not None:
            # 对话框还活着时由这里负责收尾，主窗口不要再弹第二次提示。
            self._task.metadata["ui_handled"] = True
        self._set_busy(False)
        self.close_btn.setEnabled(True)
        self.install_btn.setEnabled(True)
        if success:
            self.progress.setValue(100)
            self.progress_text.setText("安装完成")
            self.main.refresh_versions()
            profile = next(
                (v for v in self.main.versions if v.id == version_id), None
            )
            meta_base = (
                str(self._task.metadata.get("base_id") or "")
                if self._task is not None
                else ""
            )
            base_id = (
                profile.base_id
                if profile and profile.base_id
                else (meta_base or str(self.base_combo.currentData() or ""))
            )
            if profile and profile.is_loader_profile and base_id:
                self.main.config.bind_loader(base_id, version_id)
                self.main.config.last_version = base_id
                self.main.config.save()
                self.main.select_version(base_id)
            elif base_id:
                self.main.select_version(base_id)
            self.main.refresh_versions()
            self.main.toast(
                f"{version_id} 已安装，并绑定到 {base_id or '游戏版本'}",
                "success",
            )
            QTimer.singleShot(450, self.accept)
        elif "取消" in str(message):
            self.progress_text.setText("安装已取消")
            self.install_btn.setText("安装")
            self.main.toast("加载器安装已取消", "info")
        else:
            self.progress_text.setText(message)
            self.install_btn.setText("安装")
            self.main.toast(f"安装失败：{message}", "error")

    def reject(self) -> None:
        if self._installing:
            self.main.toast(
                "加载器安装已转到顶栏继续，可以继续操作启动器；"
                "点击顶栏进度可以查看任务",
                "info",
            )
        super().reject()
