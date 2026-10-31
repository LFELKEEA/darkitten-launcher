from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QDesktopServices,
    QImage,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...java import pick_java
from ...servers import (
    EULA_URL,
    SERVER_BADGE,
    AddonFile,
    ServerError,
    ServerInfo,
    ServerProcess,
    addons_dir,
    check_port,
    list_addons,
    required_java_major,
    save_server,
    scan_servers,
    send_to_recycle_bin,
    toggle_addon,
    update_server_properties,
    write_eula,
)
from ...workers import ServerInstallWorker
from .. import icons
from ..dialogs import ConfirmDialog, NoticeDialog
from ..server_addons import ServerAddonDialog
from ..server_config_dialog import ServerConfigDialog
from ..server_dialog import ServerCreateDialog
from ..widgets import (
    Card,
    ChibiCompanion,
    SegmentedControl,
    Switch,
    badge,
    colors,
    hline,
    icon_button,
    repolish,
    transparent_children,
)

KIND_COLORS = {
    "paper": "#3B6FF5",
    "spigot": "#D97706",
    "fabric": "#7152E8",
    "forge": "#B96B12",
    "neoforge": "#7152E8",
}
STATE_TEXT = {
    "stopped": "已停止",
    "starting": "启动中",
    "running": "运行中",
    "stopping": "停止中",
}
STATE_BADGE = {
    "stopped": "gray",
    "starting": "blue",
    "running": "green",
    "stopping": "orange",
}


class _ElidedLabel(QLabel):
    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = ""
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:
        self._full_text = " ".join(str(text or "").split())
        self.setToolTip(self._full_text)
        self._refresh()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self) -> None:
        width = self.contentsRect().width()
        if width <= 0:
            return
        elided = self.fontMetrics().elidedText(
            self._full_text, Qt.ElideRight, width
        )
        if QLabel.text(self) != elided:
            QLabel.setText(self, elided)


SERVER_ICON_NAMES = ("server-icon.png", "icon.png")


def _server_icon_pixmap(
    info: ServerInfo, size: int, radius: int
) -> QPixmap | None:
    """读取服务端根目录的图标；没有图标时返回 None，由调用方使用通用图标。"""
    path = None
    for name in SERVER_ICON_NAMES:
        candidate = info.path / name
        if candidate.is_file():
            path = candidate
            break
    if path is None:
        return None
    image = QImage(str(path))
    if image.isNull():
        return None
    canvas = QImage(size, size, QImage.Format_ARGB32)
    canvas.fill(Qt.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    clip = QPainterPath()
    clip.addRoundedRect(0.5, 0.5, size - 1, size - 1, radius, radius)
    painter.setClipPath(clip)
    scaled = image.scaled(
        size,
        size,
        Qt.KeepAspectRatioByExpanding,
        Qt.SmoothTransformation,
    )
    painter.drawImage(
        (size - scaled.width()) // 2,
        (size - scaled.height()) // 2,
        scaled,
    )
    painter.end()
    return QPixmap.fromImage(canvas)


class ServerRow(QFrame):
    clicked = Signal(str)

    def __init__(
        self,
        info: ServerInfo,
        state: str,
        selected: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.info = info
        self.state = state
        self.setObjectName("SelectRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(64)
        self.setProperty("selected", "true" if selected else "false")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(9, 7, 9, 7)
        layout.setSpacing(9)

        self.icon_label = QLabel()
        self.icon_label.setFixedSize(38, 38)
        self.icon_label.setAlignment(Qt.AlignCenter)
        self.icon_label.setStyleSheet(
            f"background: {colors()['card2']}; border-radius: 9px;"
        )
        layout.addWidget(self.icon_label)

        info_layout = QVBoxLayout()
        info_layout.setSpacing(2)
        self.name_label = _ElidedLabel(info.name)
        self.name_label.setStyleSheet("font-weight: 700; font-size: 12px;")
        info_layout.addWidget(self.name_label)

        meta = QHBoxLayout()
        meta.setSpacing(5)
        self.type_badge = badge(
            info.type_name, SERVER_BADGE.get(info.kind, "gray")
        )
        meta.addWidget(self.type_badge)
        version = QLabel(info.mc_version)
        version.setObjectName("Tiny")
        meta.addWidget(version)
        meta.addStretch(1)
        self.state_dot = QLabel("●")
        self.state_dot.setStyleSheet("font-size: 8px;")
        self.state_text = QLabel("")
        self.state_text.setObjectName("Tiny")
        meta.addWidget(self.state_dot)
        meta.addWidget(self.state_text)
        info_layout.addLayout(meta)

        layout.addLayout(info_layout, 1)
        transparent_children(self)
        self.refresh_theme()

    def refresh_theme(self) -> None:
        color = KIND_COLORS.get(self.info.kind, colors()["accent"])
        icon = _server_icon_pixmap(self.info, 38, 9)
        self.icon_label.setPixmap(icon or icons.pixmap("server", color, 22))
        self.icon_label.setStyleSheet(
            f"background: {colors()['card2']}; border-radius: 9px;"
        )
        self.set_state(self.state)
        repolish(self.type_badge)

    def set_state(self, state: str) -> None:
        self.state = state
        color_key = {
            "stopped": "muted",
            "starting": "accent",
            "running": "green",
            "stopping": "orange",
        }.get(state, "muted")
        self.state_dot.setStyleSheet(
            f"color: {colors()[color_key]}; font-size: 8px;"
        )
        self.state_text.setText(STATE_TEXT.get(state, state))
        self.state_text.setStyleSheet(
            f"color: {colors()[color_key]}; font-size: 11px;"
        )

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", "true" if selected else "false")
        repolish(self)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.info.folder)
        super().mouseReleaseEvent(event)


class AddonRow(QFrame):
    toggled = Signal(object, bool)

    def __init__(
        self, addon: AddonFile, is_plugin: bool, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.addon = addon
        self.setObjectName("SoftCard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(54)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(9)

        icon = QLabel()
        icon.setFixedSize(30, 30)
        icon.setAlignment(Qt.AlignCenter)
        icon.setPixmap(
            icons.pixmap(
                "plug" if is_plugin else "cube",
                colors()["purple"] if is_plugin else colors()["accent"],
                18,
            )
        )
        layout.addWidget(icon)

        info_layout = QVBoxLayout()
        info_layout.setSpacing(1)
        name = _ElidedLabel(addon.name)
        name.setStyleSheet("font-weight: 600; font-size: 12px;")
        info_layout.addWidget(name)
        meta = _ElidedLabel(f"{addon.filename} · {addon.size_text}")
        meta.setObjectName("Tiny")
        info_layout.addWidget(meta)
        layout.addLayout(info_layout, 1)

        self.switch = Switch()
        self.switch.setChecked(addon.enabled)
        self.switch.setToolTip("关闭后会在下次启动时禁用")
        self.switch.toggled.connect(
            lambda checked: self.toggled.emit(self.addon, checked)
        )
        layout.addWidget(self.switch)


class ServerPage(QWidget):
    def __init__(self, main, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.main = main
        self.servers: list[ServerInfo] = []
        self._rows: list[ServerRow] = []
        self._selected_folder = ""
        self._processes: dict[str, ServerProcess] = {}
        self._buffers: dict[str, list[str]] = {}
        self._expected_stop: set[str] = set()
        self._install_worker: ServerInstallWorker | None = None
        self._install_folder = ""
        self._build()
        self.main.java_installs_changed.connect(self._refresh_java_sources)
        self.main.ensure_java_detected()
        self.refresh_servers()

    # ------------------------------------------------------------------
    # 界面

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 18, 22, 22)
        outer.setSpacing(14)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = QLabel("猫猫服务器")
        title.setObjectName("PageTitle")
        sub = QLabel(
            "创建并托管 Paper、Spigot、Fabric、Forge 与 NeoForge 服务端，"
            "在启动器里启动、查看控制台并下载插件。"
        )
        sub.setObjectName("Muted")
        sub.setWordWrap(True)
        titles.addWidget(title)
        titles.addWidget(sub)
        header.addLayout(titles, 1)
        header.addWidget(ChibiCompanion(60, "server"), 0, Qt.AlignTop)

        self.create_btn = QPushButton("新建服务端")
        self.create_btn.setObjectName("Primary")
        self.create_btn.setCursor(Qt.PointingHandCursor)
        self.create_btn.setIcon(icons.icon("plus", "#FFFFFF", 15))
        self.create_btn.clicked.connect(self.open_create_dialog)
        self.import_btn = QPushButton("导入服务端")
        self.import_btn.setObjectName("Soft")
        self.import_btn.setCursor(Qt.PointingHandCursor)
        self.import_btn.setIcon(icons.icon("package", colors()["accent"], 15))
        self.import_btn.clicked.connect(lambda: self.open_import_dialog())
        header.addWidget(self.import_btn, 0, Qt.AlignTop)
        header.addWidget(self.create_btn, 0, Qt.AlignTop)
        outer.addLayout(header)

        columns = QHBoxLayout()
        columns.setSpacing(14)
        columns.addWidget(self._build_list_card())
        columns.addWidget(self._build_detail_card(), 1)
        outer.addLayout(columns, 1)

    def _build_list_card(self) -> QWidget:
        card = Card()
        card.setFixedWidth(308)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(11, 11, 11, 11)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(6)
        title = QLabel("我的服务端")
        title.setStyleSheet("font-weight: 700; font-size: 13px;")
        header.addWidget(title)
        self.count_label = QLabel("")
        self.count_label.setObjectName("Tiny")
        header.addWidget(self.count_label)
        header.addStretch(1)
        refresh_btn = icon_button("refresh", "刷新服务端列表", size=28, icon_size=15)
        refresh_btn.clicked.connect(lambda: self.refresh_servers(self._selected_folder))
        header.addWidget(refresh_btn)
        layout.addLayout(header)

        self.list_scroll = QScrollArea()
        self.list_scroll.setWidgetResizable(True)
        self.list_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list_holder = QWidget()
        self.list_layout = QVBoxLayout(self.list_holder)
        self.list_layout.setContentsMargins(0, 0, 3, 0)
        self.list_layout.setSpacing(3)
        self.list_layout.addStretch(1)
        self.list_scroll.setWidget(self.list_holder)
        layout.addWidget(self.list_scroll, 1)
        return card

    def _build_detail_card(self) -> QWidget:
        self.detail_card = Card()
        layout = QVBoxLayout(self.detail_card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.detail_placeholder = self._build_placeholder()
        self.detail_content = self._build_detail_content()
        layout.addWidget(self.detail_placeholder)
        layout.addWidget(self.detail_content)
        return self.detail_card

    def _build_placeholder(self) -> QWidget:
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.addStretch(1)
        self.empty_icon = QLabel()
        self.empty_icon.setAlignment(Qt.AlignCenter)
        self.empty_icon.setPixmap(icons.pixmap("server", colors()["muted"], 46))
        layout.addWidget(self.empty_icon)
        layout.addSpacing(10)
        self.empty_title = QLabel("还没有服务端")
        self.empty_title.setAlignment(Qt.AlignCenter)
        self.empty_title.setStyleSheet("font-size: 17px; font-weight: 700;")
        layout.addWidget(self.empty_title)
        self.empty_sub = QLabel(
            "选择 Paper、Spigot 或模组服务端，启动器会自动下载并生成启动文件。"
        )
        self.empty_sub.setObjectName("Muted")
        self.empty_sub.setAlignment(Qt.AlignCenter)
        self.empty_sub.setWordWrap(True)
        layout.addWidget(self.empty_sub)
        layout.addSpacing(10)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self.empty_btn = QPushButton("新建服务端")
        self.empty_btn.setObjectName("Primary")
        self.empty_btn.setCursor(Qt.PointingHandCursor)
        self.empty_btn.setIcon(icons.icon("plus", "#FFFFFF", 15))
        self.empty_btn.clicked.connect(self.open_create_dialog)
        button_row.addWidget(self.empty_btn)
        self.empty_import_btn = QPushButton("导入服务端包")
        self.empty_import_btn.setObjectName("Soft")
        self.empty_import_btn.setCursor(Qt.PointingHandCursor)
        self.empty_import_btn.setIcon(icons.icon("package", colors()["accent"], 15))
        self.empty_import_btn.clicked.connect(lambda: self.open_import_dialog())
        button_row.addWidget(self.empty_import_btn)
        button_row.addStretch(1)
        layout.addLayout(button_row)
        layout.addStretch(2)
        return holder

    def _build_detail_content(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 13, 16, 13)
        layout.setSpacing(9)

        header = QHBoxLayout()
        header.setSpacing(10)
        self.detail_icon = QLabel()
        self.detail_icon.setFixedSize(46, 46)
        self.detail_icon.setAlignment(Qt.AlignCenter)
        header.addWidget(self.detail_icon)

        titles = QVBoxLayout()
        titles.setSpacing(2)
        name_row = QHBoxLayout()
        name_row.setSpacing(6)
        self.detail_name = _ElidedLabel("")
        self.detail_name.setStyleSheet("font-size: 17px; font-weight: 800;")
        name_row.addWidget(self.detail_name, 1)
        self.type_badge = badge("Paper", "blue")
        name_row.addWidget(self.type_badge, 0)
        self.version_badge = badge("1.21.1", "gray")
        name_row.addWidget(self.version_badge, 0)
        self.state_badge = badge("已停止", "gray")
        name_row.addWidget(self.state_badge, 0)
        titles.addLayout(name_row)
        self.path_label = _ElidedLabel("")
        self.path_label.setObjectName("Tiny")
        titles.addWidget(self.path_label)
        header.addLayout(titles, 1)

        self.folder_btn = icon_button("folder", "打开服务端文件夹", size=32, icon_size=16)
        self.folder_btn.clicked.connect(self._open_server_folder)
        header.addWidget(self.folder_btn)

        self.pack_icon_btn = icon_button("package", "打包服务端", size=32, icon_size=16)
        self.pack_icon_btn.clicked.connect(self.export_server_pack)
        header.addWidget(self.pack_icon_btn)

        self.start_btn = QPushButton("启动")
        self.start_btn.setObjectName("Primary")
        self.start_btn.setCursor(Qt.PointingHandCursor)
        self.start_btn.setIcon(icons.icon("play", "#FFFFFF", 15))
        self.start_btn.clicked.connect(self.start_server)
        header.addWidget(self.start_btn)

        self.stop_btn = QPushButton("停止")
        self.stop_btn.setObjectName("Danger")
        self.stop_btn.setCursor(Qt.PointingHandCursor)
        self.stop_btn.setIcon(icons.icon("stop", "#FFFFFF", 13))
        self.stop_btn.clicked.connect(self.stop_server)
        header.addWidget(self.stop_btn)
        layout.addLayout(header)

        self.install_box = QWidget()
        install_layout = QVBoxLayout(self.install_box)
        install_layout.setContentsMargins(0, 0, 0, 0)
        install_layout.setSpacing(3)
        self.install_progress = QProgressBar()
        self.install_progress.setRange(0, 100)
        self.install_progress.setTextVisible(False)
        self.install_progress.setFixedHeight(6)
        self.install_note = QLabel("")
        self.install_note.setObjectName("Tiny")
        install_layout.addWidget(self.install_progress)
        install_layout.addWidget(self.install_note)
        self.install_box.setVisible(False)
        layout.addWidget(self.install_box)
        layout.addWidget(hline())

        tabs_row = QHBoxLayout()
        tabs_row.setSpacing(8)
        self.tabs = SegmentedControl()
        self.tabs.add_item("控制台", "console")
        self.tabs.add_item("插件", "addons")
        self.tabs.add_item("设置", "settings")
        self.tabs.changed.connect(self._on_tab_changed)
        tabs_row.addWidget(self.tabs)
        tabs_row.addStretch(1)
        self.tab_hint = QLabel("")
        self.tab_hint.setObjectName("Tiny")
        tabs_row.addWidget(self.tab_hint)
        layout.addLayout(tabs_row)

        self.tab_stack = QStackedWidget()
        self.tab_stack.addWidget(self._build_console_tab())
        self.tab_stack.addWidget(self._build_addons_tab())
        self.tab_stack.addWidget(self._build_settings_tab())
        layout.addWidget(self.tab_stack, 1)
        return content

    def _build_console_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        info_row = QHBoxLayout()
        info_row.setSpacing(6)
        self.console_hint = QLabel("服务端未启动")
        self.console_hint.setObjectName("Tiny")
        info_row.addWidget(self.console_hint)
        info_row.addStretch(1)
        self.address_label = QLabel("")
        self.address_label.setObjectName("Tiny")
        self.address_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        info_row.addWidget(self.address_label)
        self.copy_address_btn = icon_button(
            "copy", "复制服务器地址", size=26, icon_size=14
        )
        self.copy_address_btn.clicked.connect(self._copy_address)
        info_row.addWidget(self.copy_address_btn)
        layout.addLayout(info_row)

        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(4000)
        self.console.setPlaceholderText("服务端启动后，控制台输出会显示在这里…")
        layout.addWidget(self.console, 1)

        command_row = QHBoxLayout()
        command_row.setSpacing(6)
        self.command_edit = QLineEdit()
        self.command_edit.setPlaceholderText("输入控制台命令，例如 list、say 你好、stop…")
        self.command_edit.returnPressed.connect(self.send_command)
        command_row.addWidget(self.command_edit, 1)
        self.send_btn = QPushButton("发送")
        self.send_btn.setObjectName("Primary")
        self.send_btn.setCursor(Qt.PointingHandCursor)
        self.send_btn.clicked.connect(self.send_command)
        command_row.addWidget(self.send_btn)
        layout.addLayout(command_row)

        quick = QHBoxLayout()
        quick.setSpacing(4)
        for text, command in (
            ("list", "list"),
            ("help", "help"),
            ("say", "say "),
            ("清空", ""),
        ):
            button = QPushButton(text)
            button.setObjectName("Ghost")
            button.setCursor(Qt.PointingHandCursor)
            if command:
                button.clicked.connect(
                    lambda _=False, c=command: self._quick_command(c)
                )
            else:
                button.clicked.connect(self._clear_console)
            quick.addWidget(button)
        quick.addStretch(1)
        layout.addLayout(quick)
        return page

    def _build_addons_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(6)
        self.addon_path_label = _ElidedLabel("")
        self.addon_path_label.setObjectName("Tiny")
        top.addWidget(self.addon_path_label, 1)
        self.addon_refresh_btn = icon_button(
            "refresh", "刷新插件列表", size=28, icon_size=14
        )
        self.addon_refresh_btn.clicked.connect(self.refresh_addons)
        top.addWidget(self.addon_refresh_btn)
        self.addon_open_btn = QPushButton("打开目录")
        self.addon_open_btn.setObjectName("Ghost")
        self.addon_open_btn.setCursor(Qt.PointingHandCursor)
        self.addon_open_btn.setIcon(icons.icon("folder", colors()["text2"], 14))
        self.addon_open_btn.clicked.connect(self._open_addons_folder)
        top.addWidget(self.addon_open_btn)
        self.addon_download_btn = QPushButton("下载插件")
        self.addon_download_btn.setObjectName("Soft")
        self.addon_download_btn.setCursor(Qt.PointingHandCursor)
        self.addon_download_btn.setIcon(icons.icon("download", colors()["accent"], 14))
        self.addon_download_btn.clicked.connect(self.open_addon_dialog)
        top.addWidget(self.addon_download_btn)
        layout.addLayout(top)

        self.addon_scroll = QScrollArea()
        self.addon_scroll.setWidgetResizable(True)
        self.addon_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.addon_holder = QWidget()
        self.addon_layout = QVBoxLayout(self.addon_holder)
        self.addon_layout.setContentsMargins(0, 0, 3, 0)
        self.addon_layout.setSpacing(4)
        self.addon_layout.addStretch(1)
        self.addon_scroll.setWidget(self.addon_holder)
        layout.addWidget(self.addon_scroll, 1)

        self.addon_empty = QLabel("")
        self.addon_empty.setObjectName("Muted")
        self.addon_empty.setAlignment(Qt.AlignCenter)
        self.addon_empty.setWordWrap(True)
        self.addon_empty.setVisible(False)
        layout.addWidget(self.addon_empty)
        return page

    def _build_settings_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        scroll.setWidget(holder)
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(7)

        form = QFrame()
        form.setObjectName("SoftCard")
        form.setAttribute(Qt.WA_StyledBackground, True)
        form_layout = QVBoxLayout(form)
        form_layout.setContentsMargins(12, 10, 12, 10)
        form_layout.setSpacing(7)

        self.settings_name = self._form_line(form_layout, "名称")
        self.settings_memory = self._form_spin(
            form_layout, "内存", 1024, 32768, 512, " MB"
        )
        self.settings_port = self._form_spin(
            form_layout, "端口", 1024, 65535, 1, ""
        )
        self.settings_port.valueChanged.connect(self._update_port_hint)

        online_row = QHBoxLayout()
        online_row.setSpacing(8)
        online_row.addWidget(self._form_label("正版验证"))
        self.settings_online = Switch()
        online_row.addWidget(self.settings_online)
        self.settings_online.toggled.connect(lambda _: self._update_online_hint())
        self.online_hint = QLabel("")
        self.online_hint.setObjectName("Tiny")
        self.online_hint.setWordWrap(True)
        online_row.addWidget(self.online_hint, 1)
        form_layout.addLayout(online_row)

        java_row = QHBoxLayout()
        java_row.setSpacing(8)
        java_row.addWidget(self._form_label("Java"))
        self.settings_java = QComboBox()
        self.settings_java.setMinimumWidth(260)
        self.settings_java.currentIndexChanged.connect(
            lambda _: self._update_java_hint_for_current()
        )
        java_row.addWidget(self.settings_java, 1)
        self.java_hint = QLabel("")
        self.java_hint.setObjectName("Tiny")
        java_row.addWidget(self.java_hint)
        form_layout.addLayout(java_row)

        eula_row = QHBoxLayout()
        eula_row.setSpacing(8)
        eula_row.addWidget(self._form_label("EULA"))
        self.eula_badge = badge("未同意", "orange")
        eula_row.addWidget(self.eula_badge)
        self.eula_btn = QPushButton("同意 EULA")
        self.eula_btn.setObjectName("Ghost")
        self.eula_btn.setCursor(Qt.PointingHandCursor)
        self.eula_btn.clicked.connect(self._accept_eula_clicked)
        eula_row.addWidget(self.eula_btn)
        eula_link = QPushButton("查看条款")
        eula_link.setObjectName("Ghost")
        eula_link.setCursor(Qt.PointingHandCursor)
        eula_link.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(EULA_URL)))
        eula_row.addWidget(eula_link)
        eula_row.addStretch(1)
        form_layout.addLayout(eula_row)

        self.port_hint = QLabel("")
        self.port_hint.setObjectName("Tiny")
        self.port_hint.setWordWrap(True)
        form_layout.addWidget(self.port_hint)

        self.install_error_label = QLabel("")
        self.install_error_label.setWordWrap(True)
        self.install_error_label.setVisible(False)
        form_layout.addWidget(self.install_error_label)
        layout.addWidget(form)

        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.save_btn = QPushButton("保存设置")
        self.save_btn.setObjectName("Primary")
        self.save_btn.setCursor(Qt.PointingHandCursor)
        self.save_btn.clicked.connect(self.save_settings)
        actions.addWidget(self.save_btn)

        self.config_btn = QPushButton("编辑配置文件")
        self.config_btn.setObjectName("Soft")
        self.config_btn.setCursor(Qt.PointingHandCursor)
        self.config_btn.setIcon(icons.icon("edit", colors()["accent"], 14))
        self.config_btn.setToolTip(
            "在启动器内修改 server.properties 和插件/Mod 配置，"
            "每一项都有中文说明。"
        )
        self.config_btn.clicked.connect(self.open_config_dialog)
        actions.addWidget(self.config_btn)

        self.pack_btn = QPushButton("打包服务端")
        self.pack_btn.setObjectName("Soft")
        self.pack_btn.setCursor(Qt.PointingHandCursor)
        self.pack_btn.setIcon(icons.icon("package", colors()["accent"], 14))
        self.pack_btn.clicked.connect(self.export_server_pack)
        actions.addWidget(self.pack_btn)

        self.reinstall_btn = QPushButton("重新安装")
        self.reinstall_btn.setObjectName("Soft")
        self.reinstall_btn.setCursor(Qt.PointingHandCursor)
        self.reinstall_btn.clicked.connect(self._retry_install)
        self.reinstall_btn.setVisible(False)
        actions.addWidget(self.reinstall_btn)
        actions.addStretch(1)

        self.delete_btn = QPushButton("删除服务端")
        self.delete_btn.setObjectName("Danger")
        self.delete_btn.setCursor(Qt.PointingHandCursor)
        self.delete_btn.clicked.connect(self.delete_server)
        actions.addWidget(self.delete_btn)
        layout.addLayout(actions)

        note = QLabel(
            "修改端口、正版验证或内存后需要重启服务端才会生效。"
            "服务端文件夹位于启动器的 servers 目录。"
        )
        note.setObjectName("Tiny")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)
        return scroll

    @staticmethod
    def _form_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setFixedWidth(72)
        label.setStyleSheet("font-weight: 600; font-size: 12px;")
        return label

    def _form_line(self, layout: QVBoxLayout, title: str) -> QLineEdit:
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self._form_label(title))
        edit = QLineEdit()
        edit.setMaxLength(48)
        row.addWidget(edit, 1)
        layout.addLayout(row)
        return edit

    def _form_spin(
        self,
        layout: QVBoxLayout,
        title: str,
        minimum: int,
        maximum: int,
        step: int,
        suffix: str,
    ) -> QSpinBox:
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self._form_label(title))
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setSingleStep(step)
        spin.setSuffix(suffix)
        spin.setFixedWidth(160)
        row.addWidget(spin)
        row.addStretch(1)
        layout.addLayout(row)
        return spin

    # ------------------------------------------------------------------
    # 列表 / 选择

    def _state_of(self, info: ServerInfo) -> str:
        process = self._processes.get(info.folder)
        if process is not None:
            return process.state
        return "stopped"

    def refresh_servers(self, preferred_folder: str = "") -> None:
        preferred = preferred_folder or self._selected_folder
        self.servers = scan_servers()
        self.count_label.setText(f"{len(self.servers)} 个" if self.servers else "")

        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._rows = []

        if not self.servers:
            empty = QFrame()
            empty.setObjectName("SoftCard")
            empty.setAttribute(Qt.WA_StyledBackground, True)
            empty_layout = QVBoxLayout(empty)
            empty_layout.setContentsMargins(14, 18, 14, 18)
            empty_layout.setSpacing(4)
            title = QLabel("还没有服务端")
            title.setAlignment(Qt.AlignCenter)
            title.setStyleSheet("font-weight: 700;")
            empty_layout.addWidget(title)
            hint = QLabel(
                "点击右上角“新建服务端”，或导入别人打包好的服务端。"
            )
            hint.setObjectName("Tiny")
            hint.setAlignment(Qt.AlignCenter)
            hint.setWordWrap(True)
            empty_layout.addWidget(hint)
            self.list_layout.addWidget(empty)
            self.list_layout.addStretch(1)
            self._selected_folder = ""
            self.detail_placeholder.setVisible(True)
            self.detail_content.setVisible(False)
            self.empty_title.setText("还没有服务端")
            self.empty_sub.setText(
                "选择 Paper、Spigot 或模组服务端，启动器会自动下载并生成启动文件；"
                "也可以导入 ZIP 或服务端文件夹。"
            )
            self.empty_btn.setText("新建服务端")
            return

        target = preferred
        folders = {info.folder for info in self.servers}
        if target not in folders:
            target = self.servers[0].folder
        for info in self.servers:
            row = ServerRow(info, self._state_of(info), info.folder == target)
            row.clicked.connect(self._select)
            self.list_layout.addWidget(row)
            self._rows.append(row)
        self.list_layout.addStretch(1)

        self.detail_placeholder.setVisible(False)
        self.detail_content.setVisible(True)
        self._select(target, force=True)

    def _select(self, folder: str, force: bool = False) -> None:
        if not force and folder == self._selected_folder:
            self._load_console(folder)
            return
        info = next((item for item in self.servers if item.folder == folder), None)
        if info is None:
            return
        self._selected_folder = folder
        for row in self._rows:
            row.set_selected(row.info.folder == folder)
        self._show_detail(info)
        self._load_console(folder)
        self._load_settings(info)
        self.refresh_addons()
        self._update_controls()
        self.tab_stack.setCurrentIndex(0)
        self.tabs.set_value("console")

    def _apply_detail_icon(self, info: ServerInfo) -> None:
        icon = _server_icon_pixmap(info, 46, 11)
        self.detail_icon.setPixmap(
            icon
            or icons.pixmap(
                "server", KIND_COLORS.get(info.kind, colors()["accent"]), 26
            )
        )

    def _show_detail(self, info: ServerInfo) -> None:
        self.detail_name.setText(info.name)
        self.type_badge.setText(info.type_name)
        self.type_badge.setProperty("badge", SERVER_BADGE.get(info.kind, "gray"))
        repolish(self.type_badge)
        self.version_badge.setText(info.display_version)
        self.path_label.setText(str(info.path))
        self._apply_detail_icon(info)
        self.detail_icon.setStyleSheet(
            f"background: {colors()['accent_soft']}; border-radius: 11px;"
        )
        self.tabs.set_item_text(
            "addons", "插件" if info.is_plugin_server else "Mod"
        )
        self.addon_download_btn.setText(
            "下载插件" if info.is_plugin_server else "下载 Mod"
        )
        self.addon_path_label.setText(str(addons_dir(info)))
        self.address_label.setText(f"localhost:{info.port}")
        self.tab_stack.setCurrentIndex(0)

    # ------------------------------------------------------------------
    # 控制台

    def _load_console(self, folder: str) -> None:
        lines = self._buffers.get(folder, [])
        self.console.setPlainText("\n".join(lines))
        bar = self.console.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _append_console(self, folder: str, line: str) -> None:
        text = str(line or "").rstrip()
        if not text:
            return
        buffer = self._buffers.setdefault(folder, [])
        buffer.append(text)
        if len(buffer) > 2500:
            del buffer[:500]
        if folder == self._selected_folder:
            self.console.appendPlainText(text)
            bar = self.console.verticalScrollBar()
            bar.setValue(bar.maximum())

    def _clear_console(self) -> None:
        if not self._selected_folder:
            return
        self._buffers[self._selected_folder] = []
        self.console.clear()

    def _quick_command(self, command: str) -> None:
        if command.endswith(" "):
            self.command_edit.setText(command)
            self.command_edit.setFocus()
            return
        self.command_edit.setText(command)
        self.send_command()

    def send_command(self) -> None:
        info = self._current_info()
        if info is None:
            return
        process = self._processes.get(info.folder)
        if process is None or not process.is_running:
            self.toast("服务端还没有运行", "info")
            return
        command = self.command_edit.text().strip()
        if not command:
            return
        if command.startswith("/"):
            command = command[1:]
        try:
            process.send(command)
        except ServerError as exc:
            self.toast(str(exc), "error")
            return
        self._append_console(info.folder, f"> {command}")
        self.command_edit.clear()

    def _copy_address(self) -> None:
        info = self._current_info()
        if info is None:
            return
        QApplication.clipboard().setText(f"localhost:{info.port}")
        self.toast("服务器地址已复制", "success")

    # ------------------------------------------------------------------
    # 启动 / 停止

    def start_server(self) -> None:
        info = self._current_info()
        if info is None:
            return
        if info.folder in self._processes:
            self.toast("服务端已经在运行", "info")
            return
        if self._install_worker is not None and self._install_folder == info.folder:
            self.toast("服务端仍在安装中，请稍候", "info")
            return
        if not info.installed:
            NoticeDialog(
                self,
                "服务端尚未安装完成",
                "这个服务端缺少可启动文件，请先在设置页重新安装。",
                button_text="知道了",
                icon_name="warning",
            ).exec()
            self.tabs.set_value("settings")
            self.tab_stack.setCurrentIndex(2)
            return

        required = required_java_major(info.mc_version)
        java = pick_java(
            self.main.java_installs,
            required,
            info.java_path or self.main.config.java_path,
        )
        if java is None or (java.major and java.major < required):
            self._java_problem(info, required, java)
            return

        if not info.eula:
            if not self._accept_eula(info, start_after=False):
                return

        if not check_port(info.port):
            dialog = ConfirmDialog(
                self,
                "端口被占用",
                f"端口 {info.port} 已被其他程序占用，服务端将无法监听。",
                primary_text="修改端口",
                secondary_text="取消",
                icon_name="warning",
                icon_color=colors()["orange"],
                details="可以换一个端口（例如 25566），保存后重新启动。"
                "如果占用端口的是另一个 Minecraft 服务端，请先关闭它。",
            )
            if dialog.exec():
                self.tabs.set_value("settings")
                self.tab_stack.setCurrentIndex(2)
                self.settings_port.setFocus()
            return

        process = ServerProcess(info, java_path=java.path, parent=self)
        process.output.connect(
            lambda line, f=info.folder: self._append_console(f, line)
        )
        process.started.connect(
            lambda pid, f=info.folder: self._on_process_started(f, pid)
        )
        process.ready.connect(
            lambda f=info.folder: self._on_process_ready(f)
        )
        process.state_changed.connect(
            lambda state, f=info.folder: self._on_process_state(f, state)
        )
        process.stopped.connect(
            lambda code, f=info.folder: self._on_process_stopped(f, code)
        )

        try:
            process.start()
        except ServerError as exc:
            self.toast(str(exc), "error")
            NoticeDialog(
                self,
                "无法启动服务端",
                str(exc),
                button_text="知道了",
                icon_name="warning",
            ).exec()
            return

        self._processes[info.folder] = process
        self._expected_stop.discard(info.folder)
        self._buffers[info.folder] = []
        self.console.clear()
        self._append_console(info.folder, f"正在启动 {info.type_name} {info.mc_version}…")
        self._append_console(info.folder, f"Java：{java.path}")
        info.last_started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_server(info)
        self.tabs.set_value("console")
        self.tab_stack.setCurrentIndex(0)
        self._update_controls()
        self._refresh_rows_state()

    def _java_problem(self, info: ServerInfo, required: int, java) -> None:
        found = (
            f"当前选择的是 Java {java.major or '?'}（{java.path}），"
            if java is not None
            else "没有检测到可用的 Java。"
        )
        dialog = NoticeDialog(
            self,
            "需要 Java",
            f"{info.type_name} {info.mc_version} 需要 Java {required} 或更高版本。"
            f"{found}请先在设置页配置合适的 Java。",
            button_text="打开设置",
            icon_name="warning",
            secondary_text="取消",
        )
        if dialog.exec() and dialog.action_clicked:
            self.main.switch_page("settings")

    def _accept_eula_clicked(self) -> None:
        info = self._current_info()
        if info is not None:
            self._accept_eula(info, start_after=False)

    def _accept_eula(self, info: ServerInfo, start_after: bool = True) -> bool:
        dialog = ConfirmDialog(
            self,
            "同意 Minecraft EULA",
            "启动服务端需要接受 Minecraft 最终用户许可协议（EULA）。",
            primary_text="同意 EULA",
            secondary_text="取消",
            icon_name="warning",
            details="点击“同意 EULA”会写入 eula=true。条款地址："
            + EULA_URL
            + "。请确认你已阅读并同意该协议。",
        )
        if not dialog.exec():
            return False
        try:
            write_eula(info)
            save_server(info)
        except Exception as exc:
            self.toast(f"无法写入 EULA：{exc}", "error")
            return False
        self._update_eula_badge(info)
        self.toast("已同意 Minecraft EULA", "success")
        if start_after:
            QTimer.singleShot(0, self.start_server)
        return True

    def stop_server(self) -> None:
        info = self._current_info()
        if info is None:
            return
        process = self._processes.get(info.folder)
        if process is None or not process.is_running:
            self.toast("服务端没有在运行", "info")
            return
        dialog = ConfirmDialog(
            self,
            "停止服务端",
            f"确定停止 {info.name} 吗？",
            primary_text="停止服务端",
            secondary_text="取消",
            icon_name="stop",
            icon_color=colors()["red"],
            details="启动器会先发送 stop 命令让世界安全保存。"
            "如果 20 秒内没有退出，进程会被强制结束。",
        )
        if not dialog.exec():
            return
        self._expected_stop.add(info.folder)
        self._append_console(info.folder, "> stop")
        process.stop(20)
        self.toast("正在停止服务端…", "info")
        self._update_controls()
        self._refresh_rows_state()

    def _on_process_started(self, folder: str, pid: int) -> None:
        self._append_console(folder, f"服务端进程已启动（PID {pid}）")
        self._update_controls()
        self._refresh_rows_state()

    def _on_process_ready(self, folder: str) -> None:
        info = next((item for item in self.servers if item.folder == folder), None)
        name = info.name if info else folder
        self._append_console(folder, "服务端已就绪")
        self.toast(f"{name} 已就绪，可以连接了", "success")
        self._update_controls()
        self._refresh_rows_state()

    def _on_process_state(self, folder: str, state: str) -> None:
        if folder == self._selected_folder:
            self._update_controls()
        self._refresh_rows_state()

    def _on_process_stopped(self, folder: str, code: int) -> None:
        info = next((item for item in self.servers if item.folder == folder), None)
        name = info.name if info else folder
        expected = folder in self._expected_stop
        self._expected_stop.discard(folder)
        self._processes.pop(folder, None)
        self._append_console(
            folder, f"服务端进程已结束（退出代码 {code}）"
        )
        if code == 0:
            self.toast(f"{name} 已停止", "info")
        elif expected:
            self.toast(f"{name} 已停止（代码 {code}）", "info")
        else:
            self.toast(f"{name} 异常退出（代码 {code}），请查看控制台", "error")
        self._update_controls()
        self._refresh_rows_state()
        if info is not None:
            self._load_settings(info)

    def _refresh_rows_state(self) -> None:
        for row in self._rows:
            row.set_state(self._state_of(row.info))

    def _update_controls(self) -> None:
        info = self._current_info()
        if info is None:
            return
        process = self._processes.get(info.folder)
        state = process.state if process is not None else "stopped"
        self.state_badge.setText(STATE_TEXT.get(state, state))
        self.state_badge.setProperty("badge", STATE_BADGE.get(state, "gray"))
        repolish(self.state_badge)

        installing = (
            self._install_worker is not None and self._install_folder == info.folder
        )
        running = state in ("starting", "running", "stopping")
        self.start_btn.setVisible(state == "stopped")
        self.start_btn.setEnabled(not installing and info.installed)
        self.start_btn.setText("启动")
        self.stop_btn.setVisible(state != "stopped")
        self.stop_btn.setEnabled(state in ("starting", "running"))

        pid = ""
        if process is not None and process.process is not None:
            pid = f" · PID {process.process.pid}"
        hint = f"{info.type_name} {info.mc_version}"
        if state == "stopped":
            hint += " · 未启动"
        else:
            hint += f" · {STATE_TEXT.get(state, state)}{pid}"
        self.console_hint.setText(hint)
        self.address_label.setText(f"localhost:{info.port}")

        self.tab_hint.setText(
            "运行中修改的插件或设置需要重启后生效" if state != "stopped" else ""
        )
        self.command_edit.setEnabled(state == "running")
        self.send_btn.setEnabled(state == "running")
        self.eula_btn.setEnabled(not info.eula)
        self.settings_name.setEnabled(not installing)
        self.settings_memory.setEnabled(not installing)
        self.settings_port.setEnabled(not installing)
        self.settings_online.setEnabled(not installing)
        self.settings_java.setEnabled(not installing)
        self.save_btn.setEnabled(not installing)
        self.config_btn.setEnabled(not installing)
        self.delete_btn.setEnabled(not running and not installing)
        self.reinstall_btn.setEnabled(not running and not installing)
        self.pack_btn.setEnabled(not running and not installing)
        self.pack_icon_btn.setEnabled(not running and not installing)

    # ------------------------------------------------------------------
    # 插件

    def refresh_addons(self) -> None:
        info = self._current_info()
        if not hasattr(self, "addon_layout"):
            return
        while self.addon_layout.count():
            item = self.addon_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self.addon_layout.addStretch(1)
        self.addon_holder.setMinimumHeight(0)
        if info is None:
            self.addon_empty.setText("")
            self.addon_empty.setVisible(False)
            return

        self.addon_path_label.setText(str(addons_dir(info)))
        addons = list_addons(info)
        if not addons:
            self.addon_empty.setText(
                f"还没有{info.addon_label}。点击右上角“下载{info.addon_label}”"
                f"从 Modrinth 获取适配 {info.mc_version} 的项目。"
            )
            self.addon_empty.setVisible(True)
            return
        self.addon_empty.setVisible(False)
        for addon in addons:
            row = AddonRow(addon, info.is_plugin_server)
            row.toggled.connect(
                lambda item, checked, i=info: self._toggle_addon(i, item, checked)
            )
            self.addon_layout.insertWidget(self.addon_layout.count() - 1, row)
        self.addon_layout.activate()
        self.addon_holder.setMinimumHeight(self.addon_layout.sizeHint().height())

    def _toggle_addon(
        self, info: ServerInfo, addon: AddonFile, checked: bool
    ) -> None:
        if addon.enabled == checked:
            return
        try:
            toggle_addon(addon)
        except (ServerError, OSError) as exc:
            self.toast(f"无法切换{info.addon_label}状态：{exc}", "error")
            self.refresh_addons()
            return
        state = "启用" if checked else "禁用"
        self.toast(f"{addon.name} 已{state}", "success")
        if info.folder in self._processes:
            self.toast("需要重启服务端后生效", "info")
        self.refresh_addons()

    def open_addon_dialog(self) -> None:
        info = self._current_info()
        if info is None:
            return
        dialog = ServerAddonDialog(self.main, info)
        dialog.installed.connect(lambda _: self.refresh_addons())
        dialog.exec()
        self.refresh_addons()

    def _open_addons_folder(self) -> None:
        info = self._current_info()
        if info is None:
            return
        path = addons_dir(info)
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.toast(f"无法创建目录：{exc}", "error")
            return
        self.main.open_path(path)

    # ------------------------------------------------------------------
    # 设置

    def _refresh_java_sources(self, _installs=None) -> None:
        info = self._current_info()
        if info is not None:
            self._populate_java_combo(info)

    def _populate_java_combo(self, info: ServerInfo) -> None:
        current = self.settings_java.currentData() if self.settings_java.count() else ""
        preferred = current or info.java_path or ""
        self.settings_java.blockSignals(True)
        self.settings_java.clear()
        self.settings_java.addItem("自动选择（推荐）", "")
        paths = {str(item.path) for item in self.main.java_installs}
        for java in self.main.java_installs:
            self.settings_java.addItem(java.display, java.path)
        if preferred and preferred not in paths:
            self.settings_java.addItem(f"已配置：{preferred}", preferred)
        index = self.settings_java.findData(preferred)
        self.settings_java.setCurrentIndex(index if index >= 0 else 0)
        self.settings_java.blockSignals(False)
        self._update_java_hint(info)

    def _update_java_hint_for_current(self) -> None:
        info = self._current_info()
        if info is not None:
            self._update_java_hint(info)

    def _update_java_hint(self, info: ServerInfo) -> None:
        required = required_java_major(info.mc_version)
        chosen = pick_java(
            self.main.java_installs,
            required,
            self.settings_java.currentData() or "",
        )
        if chosen is None:
            self.java_hint.setText(f"需要 Java {required}+")
            self.java_hint.setStyleSheet(
                f"color: {colors()['red']}; font-size: 11px;"
            )
        else:
            self.java_hint.setText(f"Java {chosen.major} · 需要 {required}+")
            color = colors()["green"] if chosen.major >= required else colors()["orange"]
            self.java_hint.setStyleSheet(f"color: {color}; font-size: 11px;")

    def _load_settings(self, info: ServerInfo) -> None:
        self.settings_name.setText(info.name)
        self.settings_memory.setValue(max(1024, min(32768, info.memory_mb)))
        self.settings_port.setValue(max(1024, min(65535, info.port)))
        self.settings_online.setChecked(info.online_mode)
        self._populate_java_combo(info)
        self._update_eula_badge(info)
        self._update_online_hint()
        self._update_port_hint()
        self.reinstall_btn.setVisible(not info.installed or bool(info.install_error))
        if info.install_error and not info.installed:
            self.install_error_label.setText(f"上次安装失败：{info.install_error}")
            self.install_error_label.setStyleSheet(
                f"color: {colors()['red']}; font-size: 11px;"
            )
            self.install_error_label.setVisible(True)
        else:
            self.install_error_label.setVisible(False)

    def save_settings(self) -> None:
        info = self._current_info()
        if info is None:
            return
        name = self.settings_name.text().strip()
        if not name:
            self.toast("名称不能为空", "error")
            self.settings_name.setFocus()
            return
        running = info.folder in self._processes
        info.name = name
        info.memory_mb = int(self.settings_memory.value())
        info.port = int(self.settings_port.value())
        info.online_mode = self.settings_online.isChecked()
        info.java_path = str(self.settings_java.currentData() or "")
        try:
            save_server(info)
            update_server_properties(info)
        except Exception as exc:
            self.toast(f"保存失败：{exc}", "error")
            return
        self.toast("服务端设置已保存", "success")
        if running:
            self.toast("设置会在重启服务端后生效", "info")
        self.refresh_servers(info.folder)
        self._update_online_hint()

    def _update_online_hint(self) -> None:
        if self.settings_online.isChecked():
            self.online_hint.setText("仅正版账户可进入")
        else:
            self.online_hint.setText("允许离线玩家进入")

    def _update_port_hint(self) -> None:
        port = int(self.settings_port.value())
        if check_port(port):
            self.port_hint.setText(f"端口 {port} 可用")
            self.port_hint.setStyleSheet(
                f"color: {colors()['green']}; font-size: 11px;"
            )
        else:
            self.port_hint.setText(f"端口 {port} 已被占用，服务端可能无法启动")
            self.port_hint.setStyleSheet(
                f"color: {colors()['orange']}; font-size: 11px;"
            )

    def _update_eula_badge(self, info: ServerInfo) -> None:
        self.eula_badge.setText("已同意" if info.eula else "未同意")
        self.eula_badge.setProperty("badge", "green" if info.eula else "orange")
        repolish(self.eula_badge)
        self.eula_btn.setEnabled(not info.eula)

    def delete_server(self) -> None:
        info = self._current_info()
        if info is None:
            return
        process = self._processes.get(info.folder)
        if process is not None and process.is_running:
            self.toast("请先停止服务端再删除", "error")
            return
        dialog = ConfirmDialog(
            self,
            "删除服务端",
            f"确定删除 {info.name} 吗？",
            primary_text="删除",
            secondary_text="取消",
            icon_name="trash",
            icon_color=colors()["red"],
            details=f"整个服务端文件夹（包括世界存档、插件和配置）将移动到回收站：\n{info.path}",
        )
        if not dialog.exec():
            return
        ok, message = send_to_recycle_bin(info.path)
        if not ok:
            NoticeDialog(
                self,
                "删除失败",
                f"无法移动服务端文件夹：{message}",
                button_text="知道了",
                icon_name="warning",
            ).exec()
            return
        self._buffers.pop(info.folder, None)
        self._selected_folder = ""
        self.toast(message, "success")
        self.refresh_servers()

    def export_server_pack(self) -> None:
        info = self._current_info()
        if info is None:
            self.toast("先选一个服务端，猫猫才有东西可打包", "info")
            return
        if self._state_of(info) != "stopped":
            self.toast("请先停止服务端再打包，避免存档正在写入", "error")
            return
        if self._install_worker is not None and self._install_folder == info.folder:
            self.toast("服务端还在安装，等装完再打包", "info")
            return
        from ..server_pack_dialog import ServerPackExportDialog

        ServerPackExportDialog(self.main, info).exec()

    def open_import_dialog(self, path: str | Path | None = None) -> None:
        from ..server_pack_dialog import ServerPackImportDialog

        dialog = ServerPackImportDialog(self.main, initial_path=path)
        dialog.exec()
        if dialog.imported_folder:
            self.refresh_servers(dialog.imported_folder)
            self.toast("服务端已经导入，世界和玩家数据都搬过来了喵。", "success")

    def open_config_dialog(self) -> None:
        info = self._current_info()
        if info is None:
            self.toast("先选一个服务端，再编辑它的配置", "info")
            return
        if self._install_worker is not None and self._install_folder == info.folder:
            self.toast("服务端还在安装，安装完成后再编辑配置", "info")
            return
        state = self._state_of(info)
        dialog = ServerConfigDialog(
            self.main, info, running=state != "stopped"
        )
        dialog.exec()
        # 配置里可能改了端口或正版验证，重新扫描后同步到设置页。
        self.refresh_servers(info.folder)

    def _open_server_folder(self) -> None:
        info = self._current_info()
        if info is not None:
            self.main.open_path(info.path)

    # ------------------------------------------------------------------
    # 重新安装

    def _retry_install(self) -> None:
        info = self._current_info()
        if info is None:
            return
        if self._install_worker is not None:
            self.toast("已有服务端安装任务正在进行", "info")
            return
        required = required_java_major(info.mc_version)
        java = pick_java(
            self.main.java_installs,
            required,
            info.java_path or self.main.config.java_path,
        )
        if java is None:
            self.toast("没有可用的 Java，无法安装", "error")
            return
        if info.kind in ("spigot", "forge", "neoforge") and java.major < required:
            self.toast(f"安装需要 Java {required}+", "error")
            return

        worker = ServerInstallWorker(
            info,
            java_path=java.path,
            accept_eula=info.eula,
            source=self.main.config.download_source,
        )
        self._install_worker = worker
        self._install_folder = info.folder
        self.install_box.setVisible(True)
        self.install_progress.setValue(0)
        self.install_note.setText("正在准备重新安装…")
        self.tabs.set_value("console")
        self.tab_stack.setCurrentIndex(0)
        self._append_console(info.folder, "开始重新安装服务端…")
        worker.progress.connect(
            lambda percent, message, f=info.folder: self._on_install_progress(
                f, percent, message
            )
        )
        worker.log_line.connect(
            lambda line, f=info.folder: self._append_console(f, line)
        )
        worker.finished.connect(
            lambda success, message, f=info.folder, w=worker: self._on_install_finished(
                f, success, message, w
            )
        )
        self._update_controls()
        self.main.download_center.start_worker(
            worker,
            title=f"重装服务端 · {info.name}",
            content_type="server-install",
            target=info.path,
            source=self.main.config.download_source,
            metadata={
                "folder": info.folder,
                "name": info.name,
                "kind": info.kind,
                "ui_handled": True,
            },
            forward_log=False,
        )

    def _on_install_progress(self, folder: str, percent: int, message: str) -> None:
        if folder != self._install_folder:
            return
        self.install_progress.setValue(max(0, min(100, int(percent))))
        self.install_note.setText(message)

    def _on_install_finished(
        self,
        folder: str,
        success: bool,
        message: str,
        worker: ServerInstallWorker,
    ) -> None:
        if worker is not self._install_worker:
            return
        self._install_worker = None
        self._install_folder = ""
        self.install_box.setVisible(False)
        self.refresh_servers(folder)
        if success:
            self.toast("服务端重新安装完成", "success")
        else:
            self.toast(f"安装失败：{message}", "error")
            NoticeDialog(
                self,
                "服务端安装失败",
                message,
                button_text="知道了",
                icon_name="warning",
            ).exec()

    # ------------------------------------------------------------------
    # 对话框 / 生命周期

    def open_create_dialog(self) -> None:
        if self._install_worker is not None:
            self.toast("已有服务端安装任务正在进行", "info")
            return
        dialog = ServerCreateDialog(self.main)
        if dialog.exec() and dialog.server is not None:
            self.refresh_servers(dialog.server.folder)

    def _current_info(self) -> ServerInfo | None:
        return next(
            (
                info
                for info in self.servers
                if info.folder == self._selected_folder
            ),
            None,
        )

    def _on_tab_changed(self, value: str) -> None:
        index = {"console": 0, "addons": 1, "settings": 2}.get(value, 0)
        self.tab_stack.setCurrentIndex(index)
        if value == "addons":
            self.refresh_addons()
        elif value == "settings":
            info = self._current_info()
            if info is not None:
                self._load_settings(info)
                self._update_port_hint()

    def refresh_theme(self) -> None:
        self.create_btn.setIcon(icons.icon("plus", "#FFFFFF", 15))
        self.empty_btn.setIcon(icons.icon("plus", "#FFFFFF", 15))
        self.import_btn.setIcon(icons.icon("package", colors()["accent"], 15))
        self.empty_import_btn.setIcon(
            icons.icon("package", colors()["accent"], 15)
        )
        self.empty_icon.setPixmap(icons.pixmap("server", colors()["muted"], 46))
        self.start_btn.setIcon(icons.icon("play", "#FFFFFF", 15))
        self.stop_btn.setIcon(icons.icon("stop", "#FFFFFF", 13))
        self.pack_icon_btn.setIcon(
            icons.icon("package", colors()["text2"], 16)
        )
        self.pack_btn.setIcon(icons.icon("package", colors()["accent"], 14))
        self.addon_open_btn.setIcon(icons.icon("folder", colors()["text2"], 14))
        self.addon_download_btn.setIcon(
            icons.icon("download", colors()["accent"], 14)
        )
        for row in self._rows:
            row.refresh_theme()
        for label in self.findChildren(QLabel):
            if label.property("badge"):
                repolish(label)
        info = self._current_info()
        if info is not None:
            self._apply_detail_icon(info)
            self.detail_icon.setStyleSheet(
                f"background: {colors()['accent_soft']}; border-radius: 11px;"
            )
            self._update_online_hint()
            self._update_java_hint(info)
            self._update_port_hint()
            self._update_controls()

    def on_show(self) -> None:
        self.main.ensure_java_detected()
        self.refresh_servers(self._selected_folder)
        self.refresh_addons()

    def running_server_names(self) -> list[str]:
        names: list[str] = []
        for info in self.servers:
            process = self._processes.get(info.folder)
            if process is not None and process.state != "stopped":
                names.append(info.name)
        return names

    def shutdown(self) -> None:
        worker = self._install_worker
        if worker is not None:
            try:
                worker.cancel()
            except Exception:
                pass
            self._install_worker = None
        processes = list(self._processes.values())
        if not processes:
            return
        for process in processes:
            try:
                process.stop(6)
            except Exception:
                pass
        deadline = time.monotonic() + 6
        for process in processes:
            remaining = max(0.2, deadline - time.monotonic())
            process.wait(remaining)
        for process in processes:
            proc = process.process
            if proc is None or proc.poll() is not None:
                continue
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=1)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._processes.clear()

    def toast(self, text: str, kind: str = "info") -> None:
        self.main.toast(text, kind)
