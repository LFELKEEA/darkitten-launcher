from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..java import JavaInfo, pick_java
from ..servers import (
    EULA_URL,
    SERVER_NAMES,
    ServerInfo,
    check_port,
    new_server_folder,
    required_java_major,
    save_server,
)
from ..workers import LoaderVersionsWorker, ManifestWorker, ServerInstallWorker
from .dialogs import StyledDialog
from .widgets import SegmentedControl, Switch, colors

COMMON_VERSIONS = [
    "1.21.1",
    "1.21",
    "1.20.6",
    "1.20.4",
    "1.20.1",
    "1.19.4",
    "1.18.2",
    "1.16.5",
    "1.12.2",
    "1.8.9",
]
LOADER_KINDS = {"fabric", "forge", "neoforge"}
TYPE_HINTS = {
    "paper": "自动下载 Paper 最新稳定构建，适合大多数插件服；启动快、性能好。",
    "spigot": "使用官方 BuildTools 现场编译。需要 Git，首次构建通常需要 10-30 分钟。",
    "fabric": "下载 Fabric Loader 与服务端依赖，直接组装启动参数，不依赖 Fabric 安装器的元数据服务。",
    "forge": "运行 Forge 官方服务端安装器，依赖会自动通过 BMCLAPI 镜像加速。",
    "neoforge": "运行 NeoForge 官方服务端安装器，依赖会自动通过 BMCLAPI 镜像加速。",
}


class ServerCreateDialog(StyledDialog):
    def __init__(self, main) -> None:
        super().__init__(main, "新建服务端", "server", icon_color="#0E9F9A", width=720)
        self.main = main
        self.server: ServerInfo | None = None
        self._versions_worker: LoaderVersionsWorker | None = None
        self._manifest_worker: ManifestWorker | None = None
        self._install_worker: ServerInstallWorker | None = None
        self._task = None
        self._pending_info: ServerInfo | None = None
        self._installing = False
        self._name_auto = True
        self._form_widgets: list[QWidget] = []

        intro = QLabel(
            "在本地创建独立服务端。文件保存在启动器目录的 servers 文件夹中，"
            "支持 Paper、Spigot、Fabric、Forge 与 NeoForge。"
        )
        intro.setObjectName("DialogText")
        intro.setWordWrap(True)
        self.body.addWidget(intro)

        form = QFrame()
        form.setObjectName("SoftCard")
        form.setAttribute(Qt.WA_StyledBackground, True)
        form_layout = QVBoxLayout(form)
        form_layout.setContentsMargins(14, 12, 14, 12)
        form_layout.setSpacing(9)

        self.name_edit = self._add_line_row(form_layout, "名称", "例如：朋友服")
        self.name_edit.textEdited.connect(self._on_name_edited)

        type_row = QHBoxLayout()
        type_row.setSpacing(10)
        type_row.addWidget(self._row_label("类型"))
        self.type_tabs = SegmentedControl()
        for key in ("paper", "spigot", "fabric", "forge", "neoforge"):
            self.type_tabs.add_item(SERVER_NAMES[key], key)
        self.type_tabs.changed.connect(self._on_type_changed)
        type_row.addWidget(self.type_tabs)
        type_row.addStretch(1)
        form_layout.addLayout(type_row)

        self.mc_combo = self._add_combo_row(form_layout, "游戏版本")
        self.mc_combo.setEditable(True)
        self.mc_combo.setInsertPolicy(QComboBox.NoInsert)
        self.mc_combo.addItems(COMMON_VERSIONS)
        self.mc_combo.setCurrentText("1.21.1")
        self.mc_combo.activated.connect(lambda _: self._on_mc_changed())
        self.mc_combo.lineEdit().editingFinished.connect(self._on_mc_changed)

        self.loader_widget = QWidget()
        loader_layout = QHBoxLayout(self.loader_widget)
        loader_layout.setContentsMargins(0, 0, 0, 0)
        loader_layout.setSpacing(10)
        loader_layout.addWidget(self._row_label("加载器版本"))
        self.loader_combo = QComboBox()
        self.loader_combo.setMinimumWidth(360)
        self.loader_combo.currentIndexChanged.connect(lambda _: self._update_action())
        loader_layout.addWidget(self.loader_combo, 1)
        form_layout.addWidget(self.loader_widget)

        self.memory_spin = self._add_spin_row(
            form_layout, "内存", 1024, 32768, max(2048, min(8192, int(main.config.memory_mb or 4096))), 512
        )
        self.memory_spin.setSuffix(" MB")
        self.port_spin = self._add_spin_row(form_layout, "端口", 1024, 65535, 25565, 1)
        self.port_spin.valueChanged.connect(lambda _: self._update_port_note())

        self.java_combo = self._add_combo_row(form_layout, "Java")
        self.java_combo.setMinimumWidth(360)

        online_row = QHBoxLayout()
        online_row.setSpacing(10)
        online_row.addWidget(self._row_label("正版验证"))
        self.online_switch = Switch()
        online_row.addWidget(self.online_switch)
        self.online_label = QLabel("关闭时允许离线玩家进入；开启后仅正版账户可连接。")
        self.online_label.setObjectName("Tiny")
        online_row.addWidget(self.online_label, 1)
        form_layout.addLayout(online_row)

        eula_row = QHBoxLayout()
        eula_row.setSpacing(8)
        eula_row.addWidget(self._row_label("EULA"))
        self.eula_check = QCheckBox("我已阅读并同意 Minecraft 最终用户许可协议")
        eula_row.addWidget(self.eula_check)
        eula_link = QLabel(f'<a href="{EULA_URL}">查看 EULA</a>')
        eula_link.setOpenExternalLinks(True)
        eula_link.setStyleSheet(f"color: {colors()['accent']}; font-size: 11px;")
        eula_row.addWidget(eula_link)
        eula_row.addStretch(1)
        form_layout.addLayout(eula_row)

        self.port_note = QLabel("")
        self.port_note.setObjectName("Tiny")
        self.port_note.setWordWrap(True)
        form_layout.addWidget(self.port_note)

        self.hint_label = QLabel(TYPE_HINTS["paper"])
        self.hint_label.setObjectName("Tiny")
        self.hint_label.setWordWrap(True)
        form_layout.addWidget(self.hint_label)

        self.java_note = QLabel("")
        self.java_note.setObjectName("Tiny")
        self.java_note.setWordWrap(True)
        form_layout.addWidget(self.java_note)
        self.body.addWidget(form)

        self._form_widgets = [
            self.name_edit,
            self.type_tabs,
            self.mc_combo,
            self.loader_widget,
            self.memory_spin,
            self.port_spin,
            self.java_combo,
            self.online_switch,
            self.eula_check,
        ]

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
        self.log.setFixedHeight(132)
        self.log.setMaximumBlockCount(1500)
        self.log.setPlaceholderText("安装日志会显示在这里")
        self.log.setVisible(False)
        self.body.addWidget(self.log)

        self.close_btn = self.add_button("关闭", "Secondary", self.reject)
        self.install_btn = self.add_button("创建并安装", "Primary", self._install)
        self.install_btn.setMinimumWidth(120)
        self.finished.connect(self._detach_download_center)

        self.eula_check.toggled.connect(lambda _: self._update_action())
        self.java_combo.currentIndexChanged.connect(lambda _: self._update_java_note())
        self.type_tabs.set_value("paper")
        main.java_installs_changed.connect(self._refresh_java)
        main.ensure_java_detected()
        self._refresh_java(main.java_installs)
        self._load_manifest()
        self._refresh_loader_versions()
        self._update_port_note()
        self._update_name()
        self._update_action()

    # ------------------------------------------------------------------
    def _row_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setFixedWidth(78)
        label.setStyleSheet("font-weight: 700; font-size: 12px;")
        return label

    def _add_line_row(self, layout: QVBoxLayout, title: str, placeholder: str) -> QLineEdit:
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(self._row_label(title))
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        edit.setMaxLength(48)
        row.addWidget(edit, 1)
        layout.addLayout(row)
        return edit

    def _add_combo_row(self, layout: QVBoxLayout, title: str) -> QComboBox:
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(self._row_label(title))
        combo = QComboBox()
        row.addWidget(combo, 1)
        layout.addLayout(row)
        return combo

    def _add_spin_row(
        self,
        layout: QVBoxLayout,
        title: str,
        minimum: int,
        maximum: int,
        value: int,
        step: int,
    ) -> QSpinBox:
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(self._row_label(title))
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setSingleStep(step)
        spin.setValue(max(minimum, min(maximum, value)))
        spin.setMinimumWidth(180)
        row.addWidget(spin)
        row.addStretch(1)
        layout.addLayout(row)
        return spin

    # ------------------------------------------------------------------
    def _current_kind(self) -> str:
        return self.type_tabs.current() or "paper"

    def _current_mc(self) -> str:
        return self.mc_combo.currentText().strip()

    def _on_name_edited(self, _text: str) -> None:
        self._name_auto = False

    def _update_name(self) -> None:
        if self._name_auto:
            kind = self._current_kind()
            mc = self._current_mc()
            self.name_edit.setText(f"{SERVER_NAMES[kind]} {mc}".strip())

    def _on_type_changed(self, _value: str) -> None:
        kind = self._current_kind()
        self.loader_widget.setVisible(kind in LOADER_KINDS)
        self.hint_label.setText(TYPE_HINTS.get(kind, ""))
        self._update_name()
        self._refresh_loader_versions()
        self._update_action()

    def _on_mc_changed(self) -> None:
        self._update_name()
        self._refresh_loader_versions()
        self._refresh_java()
        self._update_port_note()
        self._update_action()

    def _refresh_loader_versions(self) -> None:
        kind = self._current_kind()
        if kind not in LOADER_KINDS:
            self.loader_widget.setVisible(False)
            self._versions_worker = None
            return
        self.loader_widget.setVisible(True)
        self.loader_combo.clear()
        self.loader_combo.addItem("正在获取版本…", "")
        self.loader_combo.setEnabled(False)
        mc = self._current_mc()
        self._update_action()
        if not mc:
            self.loader_combo.clear()
            self.loader_combo.addItem("请先填写游戏版本", "")
            return
        worker = LoaderVersionsWorker(kind, mc, self)
        self._versions_worker = worker
        worker.ready.connect(
            lambda data, error, w=worker, k=kind, m=mc: self._loader_versions_ready(
                w, k, m, data, error
            )
        )
        worker.start()

    def _loader_versions_ready(
        self,
        worker: LoaderVersionsWorker,
        kind: str,
        mc: str,
        data,
        error: str,
    ) -> None:
        if worker is not self._versions_worker:
            return
        if kind != self._current_kind() or mc != self._current_mc():
            return
        self.loader_combo.clear()
        if error:
            self.loader_combo.addItem("获取失败", "")
            self.hint_label.setText(f"获取 {SERVER_NAMES[kind]} 版本失败：{error}")
            self._update_action()
            return
        if not data:
            self.loader_combo.addItem("没有可用版本", "")
            self.hint_label.setText(f"{SERVER_NAMES[kind]} 目前没有适配 Minecraft {mc} 的版本。")
            self._update_action()
            return
        for item in data:
            version = str(item.get("version") or "")
            if not version:
                continue
            suffix = "  ·  推荐" if item.get("stable") else ""
            self.loader_combo.addItem(version + suffix, version)
        self.loader_combo.setCurrentIndex(0)
        self.loader_combo.setEnabled(True)
        self._update_action()

    def _load_manifest(self) -> None:
        worker = ManifestWorker(self.main.config.download_source, parent=self)
        self._manifest_worker = worker
        worker.ready.connect(self._manifest_ready)
        worker.start()

    def _manifest_ready(self, data, error: str) -> None:
        if error or not isinstance(data, dict):
            return
        current = self._current_mc()
        existing = {self.mc_combo.itemText(i) for i in range(self.mc_combo.count())}
        releases = [
            str(item.get("id") or "")
            for item in data.get("versions") or []
            if isinstance(item, dict) and item.get("type") == "release"
        ]
        self.mc_combo.blockSignals(True)
        for version in releases:
            if version and version not in existing:
                self.mc_combo.addItem(version)
        if current:
            index = self.mc_combo.findText(current)
            if index >= 0:
                self.mc_combo.setCurrentIndex(index)
            else:
                self.mc_combo.setEditText(current)
        self.mc_combo.blockSignals(False)

    def _refresh_java(self, installs=None) -> None:
        installs = list(installs if installs is not None else self.main.java_installs or [])
        current = self.java_combo.currentData()
        self.java_combo.blockSignals(True)
        self.java_combo.clear()
        self.java_combo.addItem("自动选择（推荐）", "")
        for info in installs:
            if not isinstance(info, JavaInfo):
                continue
            self.java_combo.addItem(info.display, info.path)
        if current:
            index = self.java_combo.findData(current)
            if index >= 0:
                self.java_combo.setCurrentIndex(index)
        self.java_combo.blockSignals(False)
        self._update_java_note()
        self._update_action()

    def _chosen_java(self) -> JavaInfo | None:
        required = required_java_major(self._current_mc())
        preferred = str(self.java_combo.currentData() or "")
        installs = list(self.main.java_installs or [])
        if preferred:
            for info in installs:
                if info.path.lower() == preferred.lower():
                    return info
            return None
        return pick_java(installs, required)

    def _update_java_note(self) -> None:
        required = required_java_major(self._current_mc())
        chosen = self._chosen_java()
        if chosen is None:
            self.java_note.setText(f"没有找到 Java，Minecraft {self._current_mc()} 至少需要 Java {required}。")
            self.java_note.setStyleSheet(f"color: {colors()['red']};")
            return
        if chosen.major < required:
            self.java_note.setText(
                f"当前选择的 Java {chosen.major or '?'} 过低，"
                f"Minecraft {self._current_mc()} 至少需要 Java {required}。"
            )
            self.java_note.setStyleSheet(f"color: {colors()['red']};")
        else:
            self.java_note.setText(f"需要 Java {required}+ · 当前 Java {chosen.major}")
            self.java_note.setStyleSheet(f"color: {colors()['green']};")
        self._update_action()

    def _update_port_note(self) -> None:
        port = int(self.port_spin.value())
        if check_port(port):
            self.port_note.setText(f"端口 {port} 可用。")
            self.port_note.setStyleSheet(f"color: {colors()['muted']};")
        else:
            self.port_note.setText(f"端口 {port} 已被占用，启动前请修改端口或关闭占用程序。")
            self.port_note.setStyleSheet(f"color: {colors()['orange']};")

    def _update_action(self) -> None:
        if self._installing:
            self.install_btn.setEnabled(True)
            return
        kind = self._current_kind()
        mc = self._current_mc()
        chosen = self._chosen_java()
        required = required_java_major(mc)
        enabled = bool(self.name_edit.text().strip() and mc)
        enabled = enabled and chosen is not None and chosen.major >= required
        enabled = enabled and self.eula_check.isChecked()
        if kind in LOADER_KINDS:
            enabled = enabled and bool(self.loader_combo.currentData())
        self.install_btn.setEnabled(enabled)

    # ------------------------------------------------------------------
    def _install(self) -> None:
        if self._installing:
            self._cancel_install()
            return
        if not self.install_btn.isEnabled():
            return
        kind = self._current_kind()
        mc = self._current_mc()
        chosen = self._chosen_java()
        if chosen is None:
            self.main.toast("没有可用的 Java", "error")
            return
        loader_version = (
            str(self.loader_combo.currentData() or "")
            if kind in LOADER_KINDS
            else ""
        )
        if kind in LOADER_KINDS and not loader_version:
            self.main.toast("请选择加载器版本", "error")
            return

        if self._pending_info is not None:
            info = self._pending_info
            info.name = self.name_edit.text().strip()
            info.kind = kind
            info.mc_version = mc
            info.loader_version = loader_version
            info.java_path = chosen.path
            info.memory_mb = self.memory_spin.value()
            info.port = self.port_spin.value()
            info.online_mode = self.online_switch.isChecked()
        else:
            name = self.name_edit.text().strip()
            folder = new_server_folder(name, kind, mc)
            info = ServerInfo(
                folder=folder,
                name=name,
                kind=kind,
                mc_version=mc,
                loader_version=loader_version,
                java_path=chosen.path,
                memory_mb=self.memory_spin.value(),
                port=self.port_spin.value(),
                online_mode=self.online_switch.isChecked(),
            )
            self._pending_info = info
        save_server(info)

        self._installing = True
        self._set_form_enabled(False)
        self.progress.setValue(0)
        self.progress_text.setText("准备安装…")
        self.progress_box.setVisible(True)
        self.log.clear()
        self.log.setVisible(True)
        self.install_btn.setText("取消安装")
        self.close_btn.setEnabled(True)
        self.close_btn.setText("后台运行")
        self.adjustSize()

        worker = ServerInstallWorker(
            info,
            java_path=chosen.path,
            accept_eula=True,
            source=self.main.config.download_source,
            parent=self,
        )
        self._install_worker = worker
        worker.progress.connect(self._on_progress)
        worker.log_line.connect(self._append_log)
        worker.finished.connect(self._on_finished)
        self._task = self.main.download_center.start_worker(
            worker,
            title=f"安装服务端 · {info.name}",
            content_type="server-install",
            target=info.path,
            source=self.main.config.download_source,
            metadata={
                "folder": info.folder,
                "name": info.name,
                "kind": info.kind,
            },
            forward_log=False,
        )

    def _cancel_install(self) -> None:
        if self._install_worker:
            self.progress_text.setText("正在取消…")
            self.install_btn.setEnabled(False)
            self._install_worker.cancel()

    def _detach_download_center(self, _result: int = 0) -> None:
        if self._task is not None and not self._task.running:
            self._task = None

    def _set_form_enabled(self, enabled: bool) -> None:
        for widget in self._form_widgets:
            widget.setEnabled(enabled)
        if enabled:
            self.loader_widget.setEnabled(self._current_kind() in LOADER_KINDS)
            self._update_java_note()
            self._update_action()

    def _on_progress(self, percent: int, message: str) -> None:
        self.progress.setValue(max(0, min(100, percent)))
        self.progress_text.setText(message)

    def _append_log(self, line: str) -> None:
        self.log.appendPlainText(line)
        bar = self.log.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _on_finished(self, success: bool, message: str) -> None:
        self._installing = False
        self._install_worker = None
        if self._task is not None:
            self._task.metadata["ui_handled"] = True
        self._set_form_enabled(True)
        self.close_btn.setEnabled(True)
        self.close_btn.setText("关闭")
        self.install_btn.setText("创建并安装")
        if success and self._pending_info is not None:
            self.progress.setValue(100)
            self.progress_text.setText("安装完成")
            self.server = self._pending_info
            if hasattr(self.main, "server_page"):
                self.main.server_page.refresh_servers(self.server.folder)
            self.main.toast(f"{self.server.name} 服务端已就绪", "success")
            QTimer.singleShot(500, self.accept)
            return
        if "取消" in str(message):
            self.progress_text.setText("安装已取消")
            self.main.toast("服务端安装已取消", "info")
            return
        self.progress_text.setText(message)
        self.install_btn.setText("重试安装")
        self.main.toast(f"服务端安装失败：{message}", "error")

    def reject(self) -> None:
        if self._installing:
            self.main.toast(
                "服务端安装已转到顶栏继续，可以继续操作启动器；"
                "点击顶栏进度可以查看任务",
                "info",
            )
        super().reject()
