from __future__ import annotations

import ctypes
import sys
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QDesktopServices,
    QImage,
    QKeyEvent,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
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
from ...mods import list_mods
from ...server_config import read_server_properties
from ...servers import (
    EULA_URL,
    SERVER_BADGE,
    AddonFile,
    ServerError,
    ServerInfo,
    ServerProcess,
    addon_import_conflicts,
    addons_dir,
    check_port,
    delete_addon,
    import_addon_files,
    list_addons,
    scan_addon_paths,
    required_java_major,
    save_server,
    scan_servers,
    send_to_recycle_bin,
    toggle_addon,
    update_server_properties,
    write_eula,
)
from ...sakura import (
    SAKURA_PANEL,
    SakuraNode,
    SakuraTunnel,
    SakuraTunnelProcess,
    SakuraUser,
    remove_tunnel_config,
)
from ...workers import (
    ModUpdateApplyWorker,
    ModUpdateCheckWorker,
    SakuraLoginWorker,
    SakuraRoomWorker,
    ServerInstallWorker,
)
from .. import icons
from ..dialogs import ConfirmDialog, NoticeDialog, TextInputDialog
from ..mod_update_dialog import ModUpdatesDialog
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
    local_transfer_paths,
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


def _caps_lock_on() -> bool:
    if sys.platform != "win32":
        return False
    try:
        return bool(ctypes.windll.user32.GetKeyState(0x14) & 1)
    except Exception:
        return False


def _shifted_key_event(event: QKeyEvent) -> QKeyEvent | None:
    """按住 Shift 或开启 CapsLock 时，把投递成小写的单个字母改回大写。

    Windows 上部分键盘布局或输入法会把 Shift+字母作为小写 text 交给
    QLineEdit，导致服务端指令里的大写（玩家名、say 内容等）输不进去。
    这里只在大小写与修饰键不一致时修正，中文输入法组合不受影响。
    """
    if event.type() != QEvent.KeyPress:
        return None
    text = event.text()
    if (
        len(text) != 1
        or not text.isascii()
        or not text.isalpha()
        or not text.islower()
    ):
        return None
    shift = bool(event.modifiers() & Qt.ShiftModifier)
    if not (shift ^ _caps_lock_on()):
        return None
    return QKeyEvent(
        event.type(),
        event.key(),
        event.modifiers(),
        text.upper(),
        event.isAutoRepeat(),
        event.count(),
    )


class ConsoleCommandEdit(QLineEdit):
    """服务端控制台指令输入框，保证 Shift/CapsLock 能输入大写。"""

    def keyPressEvent(self, event: QKeyEvent) -> None:
        super().keyPressEvent(_shifted_key_event(event) or event)


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
SERVER_ICON_SIZE = 64


def _fit_server_icon(image: QImage) -> QImage:
    """居中裁剪成正方形并缩放到 64×64，Minecraft 服务端只认这个尺寸。"""
    if image.isNull():
        return image
    side = min(image.width(), image.height())
    cropped = image.copy(
        max(0, (image.width() - side) // 2),
        max(0, (image.height() - side) // 2),
        side,
        side,
    )
    return cropped.scaled(
        SERVER_ICON_SIZE,
        SERVER_ICON_SIZE,
        Qt.IgnoreAspectRatio,
        Qt.SmoothTransformation,
    )


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
    details_requested = Signal(object)
    delete_requested = Signal(object)

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
        pixmap = QPixmap()
        if addon.icon_bytes and pixmap.loadFromData(addon.icon_bytes):
            icon.setPixmap(
                pixmap.scaled(
                    30, 30, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
            )
        else:
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
        meta_bits = [addon.filename, addon.size_text]
        if addon.version:
            meta_bits.insert(0, f"v{addon.version}")
        if addon.loader:
            meta_bits.insert(
                1 if addon.version else 0, addon.loader
            )
        meta = _ElidedLabel(" · ".join(meta_bits))
        meta.setObjectName("Tiny")
        info_layout.addWidget(meta)
        layout.addLayout(info_layout, 1)
        tips = [addon.filename]
        if addon.version:
            tips.append(f"版本 {addon.version}")
        if addon.loader:
            tips.append(addon.loader)
        self.setToolTip(" · ".join(tips))

        info_btn = icon_button("message", "查看介绍", colors()["accent"], 28, 14)
        info_btn.clicked.connect(lambda _=False: self.details_requested.emit(self.addon))
        layout.addWidget(info_btn)

        delete_btn = icon_button("trash", "卸载并移入回收站", colors()["muted"], 28, 14)
        delete_btn.clicked.connect(
            lambda _=False: self.delete_requested.emit(self.addon)
        )
        layout.addWidget(delete_btn)

        self.switch = Switch()
        self.switch.setChecked(addon.enabled)
        self.switch.setToolTip("关闭后会在下次启动时禁用")
        self.switch.toggled.connect(
            lambda checked: self.toggled.emit(self.addon, checked)
        )
        layout.addWidget(self.switch)


class AddonDropArea(QScrollArea):
    """支持把 .jar 或包含 .jar 的文件夹拖进服务端列表。"""

    files_dropped = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setProperty("dklAddonDrop", True)
        self._hint = QLabel("松开鼠标，把 jar 放进这个服务端", self.viewport())
        self._hint.setAlignment(Qt.AlignCenter)
        self._hint.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._hint.setStyleSheet(
            "background: rgba(20, 12, 18, 225);"
            f" border: 2px dashed {colors()['accent']};"
            " border-radius: 10px; color: #F8E9EF;"
            " font-size: 12px; font-weight: 700;"
        )
        self._hint.hide()

    @staticmethod
    def _local_paths(mime) -> list[Path]:
        result: list[Path] = []
        try:
            candidates = local_transfer_paths(mime)
        except Exception:
            return result
        for path in candidates:
            try:
                if path.is_dir() or path.name.lower().endswith(
                    (".jar", ".jar.disabled")
                ):
                    result.append(path)
            except OSError:
                continue
        return result

    def _set_drag_active(self, active: bool) -> None:
        if active:
            self._position_hint()
            self._hint.show()
            self._hint.raise_()
        else:
            self._hint.hide()

    def _position_hint(self) -> None:
        width = max(80, self.viewport().width() - 24)
        self._hint.setGeometry(12, 10, width, 34)

    def viewportEvent(self, event) -> bool:
        # QAbstractScrollArea 的 viewport 默认接收拖放，事件不会直接到
        # 滚动区域本身，这里转给下面的处理函数。
        etype = event.type()
        if etype == QEvent.DragEnter:
            self.dragEnterEvent(event)
            return True
        if etype == QEvent.DragMove:
            self.dragMoveEvent(event)
            return True
        if etype == QEvent.DragLeave:
            self.dragLeaveEvent(event)
            return True
        if etype == QEvent.Drop:
            self.dropEvent(event)
            return True
        return super().viewportEvent(event)

    def dragEnterEvent(self, event) -> None:
        if self._local_paths(event.mimeData()):
            event.setDropAction(Qt.CopyAction)
            event.accept()
            self._set_drag_active(True)
            return
        event.ignore()

    def dragMoveEvent(self, event) -> None:
        if self._local_paths(event.mimeData()):
            event.setDropAction(Qt.CopyAction)
            event.accept()
            return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._set_drag_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        paths = self._local_paths(event.mimeData())
        self._set_drag_active(False)
        if not paths:
            event.ignore()
            return
        event.setDropAction(Qt.CopyAction)
        event.accept()
        self.files_dropped.emit(paths)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._hint.isVisible():
            self._position_hint()


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
        self._addon_update_worker: ModUpdateCheckWorker | None = None
        self._sakura_login_worker: SakuraLoginWorker | None = None
        self._sakura_worker: SakuraRoomWorker | None = None
        self._sakura_tunnel: SakuraTunnelProcess | None = None
        self._sakura_payload: dict | None = None
        self._sakura_user: SakuraUser | None = None
        self._sakura_nodes: dict[int, SakuraNode] = {}
        self._sakura_tunnels: list[SakuraTunnel] = []
        self._sakura_busy = False
        self._online_closing = False
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

        self.icon_edit_btn = icon_button(
            "image",
            "修改服务器图标（自动生成 64×64 的 server-icon.png）",
            size=32,
            icon_size=16,
        )
        self.icon_edit_btn.clicked.connect(self.change_server_icon)
        header.addWidget(self.icon_edit_btn)

        self.rename_icon_btn = icon_button(
            "edit", "重命名服务端", size=32, icon_size=16
        )
        self.rename_icon_btn.clicked.connect(self.rename_server)
        header.addWidget(self.rename_icon_btn)

        self.public_btn = icon_button(
            "globe",
            "公网联机：用樱花穿透把服务端分享给朋友（无需房间号）",
            size=32,
            icon_size=16,
        )
        self.public_btn.clicked.connect(self.open_online_tab)
        header.addWidget(self.public_btn)

        self.pack_icon_btn = icon_button("package", "打包服务端", size=32, icon_size=16)
        self.pack_icon_btn.clicked.connect(self.export_server_pack)
        header.addWidget(self.pack_icon_btn)

        self.uninstall_icon_btn = icon_button(
            "trash", "卸载服务端（整个文件夹移入回收站）", size=32, icon_size=16
        )
        self.uninstall_icon_btn.clicked.connect(self.delete_server)
        header.addWidget(self.uninstall_icon_btn)

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
        self.tabs.add_item("公网联机", "online")
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
        self.tab_stack.addWidget(self._build_online_tab())
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

        self.console_banner = QFrame()
        self.console_banner.setObjectName("SoftCard")
        self.console_banner.setAttribute(Qt.WA_StyledBackground, True)
        banner_layout = QHBoxLayout(self.console_banner)
        banner_layout.setContentsMargins(10, 7, 10, 7)
        banner_layout.setSpacing(7)
        banner_icon = QLabel()
        banner_icon.setPixmap(icons.pixmap("globe", colors()["accent"], 16))
        banner_layout.addWidget(banner_icon, 0, Qt.AlignTop)
        self.console_banner_label = QLabel("")
        self.console_banner_label.setObjectName("Tiny")
        self.console_banner_label.setWordWrap(True)
        banner_layout.addWidget(self.console_banner_label, 1)
        self.console_banner_btn = QPushButton("打开公网联机")
        self.console_banner_btn.setObjectName("Soft")
        self.console_banner_btn.setCursor(Qt.PointingHandCursor)
        self.console_banner_btn.clicked.connect(self.open_online_tab)
        banner_layout.addWidget(self.console_banner_btn)
        self.console_banner.setVisible(False)
        layout.addWidget(self.console_banner)

        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(4000)
        self.console.setPlaceholderText("服务端启动后，控制台输出会显示在这里…")
        layout.addWidget(self.console, 1)

        command_row = QHBoxLayout()
        command_row.setSpacing(6)
        self.command_edit = ConsoleCommandEdit()
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
        layout.addLayout(top)

        actions = QHBoxLayout()
        actions.setSpacing(6)
        drop_tip = QLabel("把 .jar 或文件夹拖进列表即可导入")
        drop_tip.setObjectName("Tiny")
        actions.addWidget(drop_tip)
        actions.addStretch(1)
        self.addon_check_btn = QPushButton("检查更新")
        self.addon_check_btn.setObjectName("Ghost")
        self.addon_check_btn.setCursor(Qt.PointingHandCursor)
        self.addon_check_btn.setIcon(icons.icon("refresh", colors()["text2"], 14))
        self.addon_check_btn.setToolTip("检查已安装插件/Mod 是否有新版本")
        self.addon_check_btn.clicked.connect(self._check_addon_updates)
        actions.addWidget(self.addon_check_btn)
        self.addon_import_btn = QPushButton("从文件夹导入")
        self.addon_import_btn.setObjectName("Secondary")
        self.addon_import_btn.setCursor(Qt.PointingHandCursor)
        self.addon_import_btn.setIcon(icons.icon("plus", colors()["text2"], 14))
        self.addon_import_btn.setToolTip("选择一个文件夹，导入里面的 .jar")
        self.addon_import_btn.clicked.connect(self._import_addon_folder)
        actions.addWidget(self.addon_import_btn)
        self.addon_download_btn = QPushButton("下载插件")
        self.addon_download_btn.setObjectName("Soft")
        self.addon_download_btn.setCursor(Qt.PointingHandCursor)
        self.addon_download_btn.setIcon(icons.icon("download", colors()["accent"], 14))
        self.addon_download_btn.clicked.connect(self.open_addon_dialog)
        actions.addWidget(self.addon_download_btn)
        layout.addLayout(actions)

        self.addon_scroll = AddonDropArea()
        self.addon_scroll.setWidgetResizable(True)
        self.addon_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.addon_scroll.files_dropped.connect(self._import_addon_paths)
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
        self.settings_online.toggled.connect(self._settings_online_toggled)
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

        self.import_world_btn = QPushButton("导入存档")
        self.import_world_btn.setObjectName("Soft")
        self.import_world_btn.setCursor(Qt.PointingHandCursor)
        self.import_world_btn.setIcon(icons.icon("globe", colors()["accent"], 14))
        self.import_world_btn.setToolTip(
            "把其他游戏版本的存档导入这个服务端，可以只导入主世界、下界或末地；"
            "只选部分维度时合并，未选维度和玩家数据保持原样"
        )
        self.import_world_btn.clicked.connect(self.import_world_dialog)
        actions.addWidget(self.import_world_btn)

        self.reinstall_btn = QPushButton("重新安装")
        self.reinstall_btn.setObjectName("Soft")
        self.reinstall_btn.setCursor(Qt.PointingHandCursor)
        self.reinstall_btn.clicked.connect(self._retry_install)
        self.reinstall_btn.setVisible(False)
        actions.addWidget(self.reinstall_btn)
        actions.addStretch(1)

        self.delete_btn = QPushButton("卸载服务端")
        self.delete_btn.setObjectName("Danger")
        self.delete_btn.setCursor(Qt.PointingHandCursor)
        self.delete_btn.setToolTip("把整个服务端文件夹移入回收站")
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

    def _build_online_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        page = QWidget()
        scroll.setWidget(page)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(2, 2, 8, 8)
        layout.setSpacing(9)

        intro = QLabel(
            "把当前服务端的端口通过樱花穿透暴露到公网。这里不需要房间号："
            "朋友在多人游戏里添加服务器，填写下面的公网地址即可进入。"
        )
        intro.setObjectName("Muted")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        token_box = QFrame()
        token_box.setObjectName("SoftCard")
        token_box.setAttribute(Qt.WA_StyledBackground, True)
        token_layout = QVBoxLayout(token_box)
        token_layout.setContentsMargins(12, 10, 12, 10)
        token_layout.setSpacing(6)
        token_head = QHBoxLayout()
        token_title = QLabel("樱花穿透账户")
        token_title.setStyleSheet("font-weight: 700; font-size: 12px;")
        token_head.addWidget(token_title)
        token_head.addStretch(1)
        self.online_account_badge = badge("未连接", "gray")
        token_head.addWidget(self.online_account_badge)
        token_layout.addLayout(token_head)

        token_row = QHBoxLayout()
        token_row.setSpacing(6)
        self.online_token_edit = QLineEdit()
        self.online_token_edit.setPlaceholderText("樱花穿透访问密钥")
        self.online_token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.online_token_edit.setClearButtonEnabled(True)
        self.online_token_edit.returnPressed.connect(self.login_sakura)
        token_row.addWidget(self.online_token_edit, 1)
        self.online_show_btn = QPushButton("显示")
        self.online_show_btn.setObjectName("Ghost")
        self.online_show_btn.setFixedWidth(48)
        self.online_show_btn.setCursor(Qt.PointingHandCursor)
        self.online_show_btn.clicked.connect(self._toggle_online_token)
        token_row.addWidget(self.online_show_btn)
        self.online_login_btn = QPushButton("验证并连接")
        self.online_login_btn.setObjectName("Soft")
        self.online_login_btn.setCursor(Qt.PointingHandCursor)
        self.online_login_btn.clicked.connect(self.login_sakura)
        token_row.addWidget(self.online_login_btn)
        token_layout.addLayout(token_row)
        token = self.main.config.sakura_access_token
        if token:
            self.online_token_edit.setText(token)
            self.online_token_edit.setPlaceholderText("已保存访问密钥（已加密）")
        self.online_account_line = QLabel("尚未连接樱花穿透账户。")
        self.online_account_line.setObjectName("Tiny")
        self.online_account_line.setWordWrap(True)
        token_layout.addWidget(self.online_account_line)
        get_key_btn = QPushButton("获取访问密钥")
        get_key_btn.setObjectName("Ghost")
        get_key_btn.setCursor(Qt.PointingHandCursor)
        get_key_btn.setIcon(icons.icon("external", colors()["text2"], 13))
        get_key_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(SAKURA_PANEL))
        )
        token_layout.addWidget(get_key_btn, 0, Qt.AlignLeft)
        layout.addWidget(token_box)

        node_box = QFrame()
        node_box.setObjectName("SoftCard")
        node_box.setAttribute(Qt.WA_StyledBackground, True)
        node_layout = QVBoxLayout(node_box)
        node_layout.setContentsMargins(12, 10, 12, 10)
        node_layout.setSpacing(6)
        node_row = QHBoxLayout()
        node_row.setSpacing(7)
        node_title = QLabel("穿透节点")
        node_title.setStyleSheet("font-weight: 700; font-size: 12px;")
        node_row.addWidget(node_title)
        self.online_node_combo = QComboBox()
        self.online_node_combo.setMinimumWidth(260)
        node_row.addWidget(self.online_node_combo, 1)
        self.online_node_refresh = icon_button("refresh", "刷新节点列表")
        self.online_node_refresh.clicked.connect(
            lambda: self.login_sakura(silent=True)
        )
        node_row.addWidget(self.online_node_refresh)
        node_layout.addLayout(node_row)
        node_hint = QLabel(
            "公开宣传服务器时必须选择带“多线”标记的专用节点；"
            "普通节点只适合朋友之间私下使用。"
        )
        node_hint.setObjectName("Tiny")
        node_hint.setWordWrap(True)
        node_layout.addWidget(node_hint)
        layout.addWidget(node_box)

        access_box = QFrame()
        access_box.setObjectName("SoftCard")
        access_box.setAttribute(Qt.WA_StyledBackground, True)
        access_layout = QVBoxLayout(access_box)
        access_layout.setContentsMargins(12, 10, 12, 10)
        access_layout.setSpacing(6)
        offline_row = QHBoxLayout()
        offline_row.setSpacing(8)
        offline_title = QLabel("正版验证")
        offline_title.setStyleSheet("font-weight: 700; font-size: 12px;")
        offline_row.addWidget(offline_title)
        self.online_offline_badge = badge("仅正版账户", "gray")
        offline_row.addWidget(self.online_offline_badge)
        self.online_offline_hint = QLabel("")
        self.online_offline_hint.setObjectName("Tiny")
        self.online_offline_hint.setWordWrap(True)
        offline_row.addWidget(self.online_offline_hint, 1)
        self.online_settings_btn = QPushButton("去设置修改")
        self.online_settings_btn.setObjectName("Ghost")
        self.online_settings_btn.setCursor(Qt.PointingHandCursor)
        self.online_settings_btn.clicked.connect(self._show_online_settings_tab)
        offline_row.addWidget(self.online_settings_btn)
        access_layout.addLayout(offline_row)
        note = QLabel(
            "这里的值和“设置”页的正版验证开关是同一个配置，修改请到“设置”页操作；"
            "保存后需要重启服务端才会生效。"
        )
        note.setObjectName("Tiny")
        note.setWordWrap(True)
        access_layout.addWidget(note)
        layout.addWidget(access_box)

        action_row = QHBoxLayout()
        action_row.setSpacing(7)
        self.online_start_btn = QPushButton("启动公网隧道")
        self.online_start_btn.setObjectName("Primary")
        self.online_start_btn.setCursor(Qt.PointingHandCursor)
        self.online_start_btn.setIcon(icons.icon("globe", "#FFFFFF", 15))
        self.online_start_btn.clicked.connect(self.start_online_tunnel)
        action_row.addWidget(self.online_start_btn)
        self.online_close_btn = QPushButton("关闭隧道")
        self.online_close_btn.setObjectName("Danger")
        self.online_close_btn.setCursor(Qt.PointingHandCursor)
        self.online_close_btn.clicked.connect(self.close_online_tunnel)
        self.online_close_btn.setVisible(False)
        action_row.addWidget(self.online_close_btn)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        self.online_status = QLabel("连接樱花账户后即可启动公网隧道。")
        self.online_status.setObjectName("Tiny")
        self.online_status.setWordWrap(True)
        layout.addWidget(self.online_status)

        self.online_progress = QProgressBar()
        self.online_progress.setRange(0, 100)
        self.online_progress.setTextVisible(False)
        self.online_progress.setFixedHeight(6)
        self.online_progress.setVisible(False)
        layout.addWidget(self.online_progress)

        address_box = QFrame()
        address_box.setObjectName("SoftCard")
        address_box.setAttribute(Qt.WA_StyledBackground, True)
        address_layout = QVBoxLayout(address_box)
        address_layout.setContentsMargins(12, 9, 12, 9)
        address_layout.setSpacing(4)
        address_caption = QLabel("公网服务器地址")
        address_caption.setObjectName("Tiny")
        address_layout.addWidget(address_caption)
        address_row = QHBoxLayout()
        address_row.setSpacing(7)
        self.online_address = QLabel("隧道启动后显示")
        self.online_address.setStyleSheet(
            "font-size: 16px; font-weight: 800; letter-spacing: 0.5px;"
        )
        self.online_address.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.online_address.setWordWrap(True)
        address_row.addWidget(self.online_address, 1)
        self.online_copy_btn = QPushButton("复制地址")
        self.online_copy_btn.setObjectName("Soft")
        self.online_copy_btn.setCursor(Qt.PointingHandCursor)
        self.online_copy_btn.setIcon(icons.icon("copy", colors()["accent"], 14))
        self.online_copy_btn.clicked.connect(self._copy_public_address)
        address_row.addWidget(self.online_copy_btn)
        address_layout.addLayout(address_row)
        address_hint = QLabel(
            "朋友打开 Minecraft → 多人游戏 → 添加服务器，把这个地址填进去即可。"
        )
        address_hint.setObjectName("Tiny")
        address_hint.setWordWrap(True)
        address_layout.addWidget(address_hint)
        self.online_address_box = address_box
        address_box.setVisible(False)
        layout.addWidget(address_box)

        self.online_log = QPlainTextEdit()
        self.online_log.setObjectName("SoftLog")
        self.online_log.setReadOnly(True)
        self.online_log.setMaximumBlockCount(300)
        self.online_log.setFixedHeight(110)
        self.online_log.setPlaceholderText("内置 frpc 的连接日志会显示在这里")
        self.online_log.setVisible(False)
        layout.addWidget(self.online_log)

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
        self.addon_check_btn.setToolTip(
            f"检查已安装{info.addon_label}是否有新版本"
        )
        self.addon_import_btn.setToolTip(
            f"选择包含{info.addon_label}的文件夹，自动导入里面的 .jar"
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
        if self._sakura_payload is None:
            self.toast(
                "想让朋友从公网进入，请到“公网联机”启动樱花穿透"
                "（无需房间号，直接分享服务器地址）",
                "info",
            )
        self._update_controls()
        self._refresh_rows_state()
        self._update_console_banner()

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
        self.uninstall_icon_btn.setEnabled(not running and not installing)
        self.uninstall_icon_btn.setToolTip(
            "服务端运行中，先停止再卸载"
            if running
            else "卸载服务端（整个文件夹移入回收站）"
        )
        self._sync_online_controls()

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
                f"还没有{info.addon_label}。点击“下载{info.addon_label}”从 "
                f"Modrinth 或 CurseForge 获取适配 {info.mc_version} 的项目，"
                "也可以把 .jar 或包含 .jar 的文件夹直接拖进这个列表。"
            )
            self.addon_empty.setVisible(True)
            return
        self.addon_empty.setVisible(False)
        for addon in addons:
            row = AddonRow(addon, info.is_plugin_server)
            row.toggled.connect(
                lambda item, checked, i=info: self._toggle_addon(i, item, checked)
            )
            row.details_requested.connect(
                lambda item, i=info: self._show_addon_info(i, item)
            )
            row.delete_requested.connect(
                lambda item, i=info: self._delete_addon(i, item)
            )
            self.addon_layout.insertWidget(self.addon_layout.count() - 1, row)
        self.addon_layout.activate()
        self.addon_holder.setMinimumHeight(self.addon_layout.sizeHint().height())

    def _show_addon_info(self, info: ServerInfo, addon: AddonFile) -> None:
        if addon.project_id:
            hit = {
                "project_id": addon.project_id,
                "title": addon.name,
                "description": addon.description,
                "project_url": addon.project_url,
                "source": addon.source or "modrinth",
                "source_label": addon.source_label,
                "content_type": "mod",
                "categories": [],
                "downloads": 0,
                "icon_url": "",
            }
            ServerAddonDialog(self.main, info, hit).exec()
            return
        text = addon.intro
        if not text:
            details = [addon.filename]
            if addon.version:
                details.append(f"版本 {addon.version}")
            if addon.loader:
                details.append(addon.loader)
            text = (
                f"{addon.name}\n\n"
                + " · ".join(details)
                + "\n\nJAR 内没有可读取的简介，也没有记录在线来源。"
                "文件仍可正常启用、禁用或卸载。"
            )
        else:
            header = [addon.name]
            if addon.version:
                header.append(f"v{addon.version}")
            if addon.loader:
                header.append(addon.loader)
            text = f"{' · '.join(header)}\n\n{text}"
        NoticeDialog(self, addon.name, text, icon_name="cube").exec()

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

    def _delete_addon(self, info: ServerInfo, addon: AddonFile) -> None:
        running = info.folder in self._processes
        dialog = ConfirmDialog(
            self,
            f"卸载{info.addon_label}",
            f"确定卸载 {addon.name} 吗？",
            primary_text="移入回收站",
            secondary_text="取消",
            icon_name="trash",
            icon_color=colors()["red"],
            details=(
                f"文件会移动到回收站，下载记录和图标也会一并清理：\n"
                f"{addon.path}"
                + (
                    "\n\n服务端正在运行，卸载后需要重启才能生效。"
                    if running
                    else ""
                )
            ),
        )
        if not dialog.exec():
            return
        ok, message = delete_addon(addon)
        if not ok:
            NoticeDialog(
                self,
                "卸载失败",
                f"无法移动{info.addon_label}文件：{message}",
                button_text="知道了",
                icon_name="warning",
            ).exec()
            return
        self.toast(f"{addon.name} 已移入回收站", "success")
        if running:
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

    # ------------------------------------------------------------------
    # 插件检查更新与本地导入

    def _check_addon_updates(self) -> None:
        info = self._current_info()
        if info is None:
            return
        if self._addon_update_worker is not None:
            self.toast("正在检查更新，请稍候", "info")
            return
        mods = list_mods(addons_dir(info))
        if not mods:
            self.toast(f"当前服务端还没有安装{info.addon_label}", "info")
            return
        content_type = "plugin" if info.is_plugin_server else "mod"
        self.addon_check_btn.setEnabled(False)
        self.addon_check_btn.setText("检查中…")
        worker = ModUpdateCheckWorker(
            mods,
            info.mc_version,
            info.kind,
            self,
            content_type=content_type,
        )
        self._addon_update_worker = worker
        worker.progress.connect(self._on_addon_update_progress)
        worker.finished.connect(
            lambda updates, errors, folder=info.folder, total=len(mods): (
                self._on_addon_updates_checked(folder, updates, errors, total)
            )
        )
        worker.start()

    def _on_addon_update_progress(self, done: int, total: int, _name: str) -> None:
        self.addon_check_btn.setText(f"检查中 {done}/{total}")

    def _on_addon_updates_checked(
        self, folder: str, updates, errors, total: int = 0
    ) -> None:
        self._addon_update_worker = None
        self.addon_check_btn.setEnabled(True)
        self.addon_check_btn.setText("检查更新")
        info = self._current_info()
        if info is None or info.folder != folder:
            return
        found = list(updates or [])
        checked = f"已检查 {total or len(found)} 个{info.addon_label}"
        detail = ""
        if errors:
            detail = "；".join(str(item) for item in list(errors)[:2])
        if not found:
            if detail:
                self.toast(f"{checked}，检查未完成：{detail}", "info")
            else:
                self.toast(
                    f"{checked}，都是最新版本或没有找到在线来源",
                    "success",
                )
            return
        if detail:
            self.toast(f"{checked}，{len(found)} 个可更新，部分检查失败：{detail}", "info")
        dialog = ModUpdatesDialog(
            self,
            found,
            label=info.addon_label,
            folder_name=info.addon_dir_name,
        )
        if dialog.exec():
            self._apply_addon_updates(info, dialog.selected)

    def _apply_addon_updates(self, info: ServerInfo, updates) -> None:
        updates = list(updates or [])
        if not updates:
            return
        if info.folder in self._processes:
            self.toast("服务端正在运行，请先停止再更新", "error")
            return
        target = addons_dir(info)
        worker = ModUpdateApplyWorker(
            updates,
            target,
            threads=self.main.config.download_threads,
            parent=self,
            label=info.addon_label,
        )
        worker.finished.connect(
            lambda success, message, filename, i=info: self._on_addon_updates_applied(
                i, success, message, filename
            )
        )
        title = (
            f"更新 {len(updates)} 个{info.addon_label}"
            if len(updates) > 1
            else f"更新{info.addon_label}：{updates[0].mod.name}"
        )
        self.main.download_center.start_worker(
            worker,
            title=title,
            content_type="mod-update",
            target=target,
            source="mixed",
        )
        self.toast("更新已加入下载中心，关闭页面也会继续", "info")

    def _on_addon_updates_applied(
        self, info: ServerInfo, success: bool, message: str, _filename: str
    ) -> None:
        self.refresh_addons()
        self.main.toast(message, "success" if success else "error")
        if success and info.folder in self._processes:
            self.toast("需要重启服务端后生效", "info")

    def _import_addon_folder(self) -> None:
        info = self._current_info()
        if info is None:
            return
        start = addons_dir(info)
        if not start.is_dir():
            start = info.path
        folder = QFileDialog.getExistingDirectory(
            self,
            f"选择包含{info.addon_label}的文件夹",
            str(start),
        )
        if not folder:
            return
        self._import_addon_paths([Path(folder)], info)

    def _import_addon_paths(self, paths, info: ServerInfo | None = None) -> None:
        info = info or self._current_info()
        if info is None:
            self.toast("请先选择一个服务端", "info")
            return
        sources, _ignored = scan_addon_paths([Path(item) for item in paths])
        label = info.addon_label
        if not sources:
            NoticeDialog(
                self,
                "没有可导入的文件",
                f"没有找到 .jar 或 .jar.disabled 文件。\n\n"
                f"可以直接把 jar 拖进列表，或选择包含{label}的文件夹。",
                button_text="知道了",
                icon_name="warning",
            ).exec()
            return
        target = addons_dir(info)
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.toast(f"无法创建目录：{exc}", "error")
            return

        conflicts = addon_import_conflicts(sources, target)
        overwrite = False
        if conflicts:
            names = "\n".join(f"· {Path(item).name}" for item in conflicts[:8])
            if len(conflicts) > 8:
                names += f"\n…另有 {len(conflicts) - 8} 个"
            dialog = ConfirmDialog(
                self,
                f"发现同名{label}",
                f"{len(conflicts)} 个文件与现有文件同名，要覆盖吗？",
                primary_text="覆盖旧文件",
                secondary_text="跳过同名",
                icon_name="warning",
                details=(
                    names + "\n\n覆盖前会把旧文件备份到 "
                    ".dkl-meta/backups/addons，不会直接删除。"
                ),
            )
            overwrite = bool(dialog.exec())

        result = import_addon_files(sources, target, overwrite=overwrite)
        if result.imported:
            self.toast(f"已导入 {len(result.imported)} 个{label}", "success")
        if result.replaced:
            self.toast(f"已覆盖 {len(result.replaced)} 个旧文件并完成备份", "info")
        if result.skipped:
            self.toast(
                f"跳过 {len(result.skipped)} 个文件（内容相同或同名）", "info"
            )
        if result.errors:
            NoticeDialog(
                self,
                "部分文件导入失败",
                "\n".join(result.errors[:8]),
                button_text="知道了",
                icon_name="warning",
            ).exec()
        if result.imported and info.folder in self._processes:
            self.toast("需要重启服务端后生效", "info")
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
        self.settings_online.blockSignals(True)
        self.settings_online.setChecked(info.online_mode)
        self.settings_online.blockSignals(False)
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
        self._load_online_settings(info)

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
            self.toast("先选一个服务端，再点卸载", "info")
            return
        process = self._processes.get(info.folder)
        if process is not None and process.is_running:
            self.toast("请先停止服务端再删除", "error")
            return
        dialog = ConfirmDialog(
            self,
            "卸载服务端",
            f"确定卸载 {info.name} 吗？",
            primary_text="移入回收站",
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
                "卸载失败",
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

    def import_world_dialog(self) -> None:
        info = self._current_info()
        if info is None:
            self.toast("先选一个服务端，再导入存档", "info")
            return
        if self._state_of(info) != "stopped":
            self.toast("请先停止服务端再导入存档，避免世界正在写入", "error")
            return
        if self._install_worker is not None and self._install_folder == info.folder:
            self.toast("服务端还在安装，等装完再导入存档", "info")
            return
        properties = read_server_properties(info.path / "server.properties")
        level_name = (properties.get("level-name") or "world").strip() or "world"
        from ..world_import_dialog import WorldImportDialog

        dialog = WorldImportDialog(
            self.main,
            target_text=(
                f"把其他游戏版本的存档导入服务端「{info.name}」，作为它的主世界。"
                "可以选择只导入主世界、下界或末地；只选部分维度时会合并到现有世界，"
                "level.dat、玩家数据和未选维度保持原样。"
                "导入后 server.properties 的 level-name 会同步更新。"
            ),
            default_name=level_name,
            server_info=info,
            target_version=info.mc_version,
        )
        dialog.exec()
        if dialog.import_result is not None:
            self.refresh_servers(info.folder)
            result = dialog.import_result
            dimensions = getattr(result, "dimension_text", "")
            backups = getattr(result, "backups", None) or []
            if not backups and getattr(result, "backup", None) is not None:
                backups = [result.backup]
            prefix = f"{dimensions}已导入服务端" if dimensions else "存档已导入服务端"
            if backups:
                names = "、".join(item.name for item in backups[:3])
                more = f" 等 {len(backups)} 个" if len(backups) > 3 else ""
                suffix = f"，旧数据已备份为 {names}{more}"
            else:
                suffix = ""
            self.toast(f"{prefix}{suffix}", "success")

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

    def rename_server(self) -> None:
        info = self._current_info()
        if info is None:
            self.toast("先选一个服务端，再重命名", "info")
            return
        dialog = TextInputDialog(
            self,
            "重命名服务端",
            "只修改启动器里显示的名称，服务端文件夹和存档位置保持不变。",
            info.name,
            max_length=48,
            placeholder="例如：周末生存服",
            player_name=False,
        )
        if not dialog.exec():
            return
        name = dialog.value().strip()
        if not name:
            return
        info.name = name
        try:
            save_server(info)
        except Exception as exc:
            self.toast(f"重命名失败：{exc}", "error")
            return
        self.refresh_servers(info.folder)
        self.toast("服务端名称已更新", "success")

    def change_server_icon(self) -> None:
        info = self._current_info()
        if info is None:
            self.toast("先选一个服务端，再修改图标", "info")
            return
        if not info.path.is_dir():
            self.toast("服务端文件夹不存在", "error")
            return
        pictures = Path.home() / "Pictures"
        start = pictures if pictures.is_dir() else Path.home()
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择服务器图标",
            str(start),
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.gif *.webp)",
        )
        if not path:
            return
        image = QImage(path)
        if image.isNull():
            self.toast("这个文件无法作为图片读取", "error")
            return
        icon = _fit_server_icon(image)
        target = info.path / "server-icon.png"
        if not icon.save(str(target), "PNG"):
            self.toast("图标写入失败，请检查服务端文件夹权限", "error")
            return
        self.refresh_servers(info.folder)
        self.toast("服务器图标已更新，重启服务端后生效", "success")

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
        index = {"console": 0, "addons": 1, "settings": 2, "online": 3}.get(
            value, 0
        )
        self.tab_stack.setCurrentIndex(index)
        if value == "addons":
            self.refresh_addons()
        elif value == "settings":
            info = self._current_info()
            if info is not None:
                self._load_settings(info)
                self._update_port_hint()
        elif value == "online":
            info = self._current_info()
            if info is not None:
                self._load_online_settings(info)
            self._sync_online_controls()
            token = self.online_token_edit.text().strip() or self.main.config.sakura_access_token
            if token and self._sakura_user is None and not self._sakura_busy:
                QTimer.singleShot(120, lambda: self.login_sakura(silent=True))

    def open_online_tab(self) -> None:
        if self._current_info() is None:
            self.toast("先选一个服务端，再打开公网联机", "info")
            return
        self.tabs.set_value("online")
        self.tab_stack.setCurrentIndex(3)
        self._on_tab_changed("online")

    # ------------------------------------------------------------------
    # 公网联机（樱花穿透，无房间号）

    def _set_online_status(self, text: str, kind: str = "muted") -> None:
        mapping = {
            "muted": "muted",
            "busy": "accent",
            "success": "green",
            "warning": "orange",
            "error": "red",
        }
        color = colors().get(mapping.get(kind, "muted"), colors()["muted"])
        self.online_status.setStyleSheet(
            f"color: {color}; font-size: 11px; background: transparent;"
        )
        self.online_status.setText(text)

    def _toggle_online_token(self) -> None:
        if self.online_token_edit.echoMode() == QLineEdit.EchoMode.Password:
            self.online_token_edit.setEchoMode(QLineEdit.EchoMode.Normal)
            self.online_show_btn.setText("隐藏")
        else:
            self.online_token_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self.online_show_btn.setText("显示")

    def login_sakura(self, silent: bool = False) -> None:
        if self._sakura_busy:
            return
        if self._sakura_payload is not None or self._sakura_tunnel is not None:
            self.toast("请先关闭公网隧道，再更换访问密钥。", "info")
            return
        token = (
            self.online_token_edit.text().strip()
            or self.main.config.sakura_access_token
        )
        if not token:
            self._set_online_status("请先填写樱花穿透访问密钥。", "error")
            return
        self._sakura_busy = True
        self.online_login_btn.setEnabled(False)
        self.online_login_btn.setText("正在验证…")
        self._set_online_status("正在验证访问密钥并读取节点…", "busy")
        self.online_account_badge.setText("验证中")
        self.online_account_badge.setProperty("badge", "blue")
        repolish(self.online_account_badge)
        self._sync_online_controls()
        worker = SakuraLoginWorker(token, self)
        self._sakura_login_worker = worker
        worker.ready.connect(
            lambda payload, error, w=worker, s=silent: self._online_login_ready(
                w, payload, error, s
            )
        )
        worker.start()

    def _online_login_ready(
        self,
        worker: SakuraLoginWorker,
        payload,
        error: str,
        silent: bool = False,
    ) -> None:
        if worker is not self._sakura_login_worker:
            return
        self._sakura_login_worker = None
        self._sakura_busy = False
        self.online_login_btn.setEnabled(True)
        self.online_login_btn.setText("重新验证")
        if error or not isinstance(payload, dict):
            self._set_online_status(
                error or "无法读取樱花穿透账户。", "error"
            )
            self.online_account_badge.setText("未连接")
            self.online_account_badge.setProperty("badge", "gray")
            repolish(self.online_account_badge)
            self._sync_online_controls()
            if not silent:
                self.toast(error or "樱花穿透连接失败", "error")
            return

        user: SakuraUser = payload["user"]
        self._sakura_user = user
        self._sakura_nodes = payload["nodes"]
        self._sakura_tunnels = payload["tunnels"]
        token = self.online_token_edit.text().strip()
        if token:
            self.main.config.set_sakura_token(token)
            self.main.config.save()
            self.online_token_edit.setPlaceholderText("已保存访问密钥（已加密）")
        self.online_account_badge.setText("已连接")
        self.online_account_badge.setProperty("badge", "green")
        repolish(self.online_account_badge)
        details = [
            f"已连接 · {user.name or '樱花账户'}",
            user.realname_text,
            user.traffic_text,
        ]
        if user.tunnel_limit:
            details.append(f"隧道 {len(self._sakura_tunnels)}/{user.tunnel_limit}")
        self.online_account_line.setText(" · ".join(details))
        self._populate_online_nodes()
        info = self._current_info()
        if info is not None:
            self._set_online_status(
                f"账户已连接，可以启动 {info.port} 端口的公网隧道。", "success"
            )
        else:
            self._set_online_status("账户已连接。", "success")
        self._sync_online_controls()
        if not silent:
            self.toast("樱花穿透账户已连接", "success")

    def _populate_online_nodes(self) -> None:
        current_id = self._selected_online_node_id()
        if not current_id:
            current_id = int(self.main.config.sakura_server_node_id or 0)
        self.online_node_combo.blockSignals(True)
        self.online_node_combo.clear()
        self.online_node_combo.addItem("自动选择节点", 0)
        nodes = [
            node
            for node in self._sakura_nodes.values()
            if node.selectable and node.host
        ]
        nodes.sort(
            key=lambda node: (
                node.public_game,
                node.vip,
                not node.mainland,
                node.name,
            )
        )
        for node in nodes[:200]:
            self.online_node_combo.addItem(node.label, node.id)
            index = self.online_node_combo.count() - 1
            tip = node.description or ""
            if node.host:
                tip = (tip + "\n" if tip else "") + f"节点地址：{node.host}"
            self.online_node_combo.setItemData(index, tip, Qt.ToolTipRole)
        if current_id:
            index = self.online_node_combo.findData(current_id)
            if index >= 0:
                self.online_node_combo.setCurrentIndex(index)
        self.online_node_combo.blockSignals(False)
        if not nodes:
            self._set_online_status(
                "没有可用节点，请稍后刷新，或到樱花穿透面板检查节点状态。",
                "error",
            )

    def _selected_online_node_id(self) -> int:
        try:
            return int(self.online_node_combo.currentData() or 0)
        except (TypeError, ValueError):
            return 0

    def _load_online_settings(self, info: ServerInfo) -> None:
        """公网联机页只读展示设置页的正版验证状态，避免两个开关各保存一份。"""
        self.online_offline_badge.setText(
            "仅正版账户" if info.online_mode else "允许离线玩家"
        )
        self.online_offline_badge.setProperty(
            "badge", "green" if info.online_mode else "orange"
        )
        repolish(self.online_offline_badge)
        self.online_offline_hint.setText(
            "服务器会拒绝未登录正版账户的玩家" if info.online_mode
            else "服务器允许非正版账户进入"
        )
        self._sync_online_controls()

    def _show_online_settings_tab(self) -> None:
        self.tabs.set_value("settings", emit=True)

    def _settings_online_toggled(self, checked: bool) -> None:
        """设置页的正版验证开关立即写盘，切换页面后不会回退。"""
        info = self._current_info()
        if info is None:
            self._update_online_hint()
            return
        if bool(info.online_mode) == bool(checked):
            self._update_online_hint()
            return
        info.online_mode = bool(checked)
        try:
            save_server(info)
            update_server_properties(info)
        except Exception as exc:
            self.settings_online.blockSignals(True)
            self.settings_online.setChecked(not bool(checked))
            self.settings_online.blockSignals(False)
            self._update_online_hint()
            self.toast(f"保存正版验证设置失败：{exc}", "error")
            return
        self._update_online_hint()
        self._load_online_settings(info)
        process = self._processes.get(info.folder)
        if process is not None and process.state != "stopped":
            self.toast("正版验证设置已保存，重启服务端后生效", "info")
        else:
            self.toast("正版验证设置已保存", "success")

    def start_online_tunnel(self) -> None:
        info = self._current_info()
        if info is None:
            return
        if self._sakura_busy:
            return
        if self._sakura_payload is not None or self._sakura_tunnel is not None:
            self.toast("公网隧道已经开启", "info")
            return
        if not info.installed:
            self.toast("服务端尚未安装完成", "error")
            return
        if self._sakura_user is None:
            self._set_online_status("请先验证并连接樱花穿透账户。", "error")
            return
        self._sakura_busy = True
        self.online_progress.setRange(0, 100)
        self.online_progress.setValue(0)
        self.online_progress.setVisible(True)
        self.online_log.clear()
        self.online_log.setVisible(True)
        self.online_start_btn.setEnabled(False)
        self._set_online_status(
            f"正在为端口 {info.port} 准备隧道与内置 frpc…", "busy"
        )
        self._sync_online_controls()
        worker = SakuraRoomWorker(
            self.online_token_edit.text().strip()
            or self.main.config.sakura_access_token,
            info.port,
            node_id=self._selected_online_node_id(),
            preferred_tunnel_id=self.main.config.sakura_server_tunnel_id,
            frpc_path=self.main.config.sakura_frpc_path,
            parent=self,
            tunnel_name="DarkittenServer",
            make_room_code=False,
            avoid_tunnel_id=self.main.config.sakura_tunnel_id,
        )
        self._sakura_worker = worker
        worker.progress.connect(
            lambda value, message, w=worker: self._online_progress(
                w, value, message
            )
        )
        worker.log_line.connect(
            lambda line, w=worker: self._online_log_line(w, line)
        )
        worker.ready.connect(
            lambda payload, error, w=worker: self._online_ready(w, payload, error)
        )
        worker.failed.connect(
            lambda error, w=worker: self._online_failed(w, error)
        )
        worker.start()

    def _online_progress(
        self, worker: SakuraRoomWorker, value: int, message: str
    ) -> None:
        if worker is not self._sakura_worker:
            return
        self.online_progress.setValue(value)
        self._set_online_status(message, "busy")

    def _online_log_line(self, worker: SakuraRoomWorker, line: str) -> None:
        if worker is not self._sakura_worker:
            return
        self._append_online_log(line)

    def _append_online_log(self, line: str) -> None:
        text = str(line or "")
        if not text:
            return
        token = self.main.config.sakura_access_token
        if token:
            text = text.replace(token, "***")
        if not self.online_log.isVisible():
            self.online_log.setVisible(True)
        self.online_log.appendPlainText(text)
        bar = self.online_log.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _online_ready(
        self, worker: SakuraRoomWorker, payload, error: str
    ) -> None:
        if worker is not self._sakura_worker:
            return
        self._sakura_worker = None
        self._sakura_busy = False
        if error or not isinstance(payload, dict):
            self.online_progress.setVisible(False)
            self._set_online_status(error or "创建隧道失败。", "error")
            self.toast(error or "创建隧道失败", "error")
            self._sync_online_controls()
            return
        self._sakura_payload = payload
        self.main.config.sakura_server_tunnel_id = int(
            payload.get("tunnel_id") or 0
        )
        self.main.config.sakura_server_node_id = int(payload.get("node_id") or 0)
        self.main.config.save()
        self.online_progress.setValue(100)
        self.online_progress.setVisible(False)
        self._set_online_status("隧道已准备，正在启动内置 frpc…", "busy")
        self._start_online_tunnel_process(payload)

    def _online_failed(self, worker: SakuraRoomWorker, error: str) -> None:
        if worker is not self._sakura_worker:
            return
        self._sakura_worker = None
        self._sakura_busy = False
        self.online_progress.setVisible(False)
        self._set_online_status(error or "创建隧道失败。", "error")
        self.toast(error or "创建隧道失败", "error")
        self._sync_online_controls()

    def _start_online_tunnel_process(self, payload: dict) -> None:
        process = SakuraTunnelProcess(
            payload.get("frpc_path", ""),
            payload.get("config_path", ""),
            self,
        )
        self._sakura_tunnel = process
        process.log_line.connect(self._append_online_log)
        process.ready.connect(self._online_tunnel_ready)
        process.failed.connect(self._online_tunnel_failed)
        process.finished.connect(self._online_tunnel_finished)
        process.start()
        self._sync_online_controls()

    def _online_tunnel_ready(self, message: str) -> None:
        if self._online_closing or self._sakura_payload is None:
            return
        payload = self._sakura_payload
        address = str(payload.get("address") or "")
        self.online_address.setText(address or "地址获取失败")
        self.online_address_box.setVisible(True)
        self._set_online_status(
            f"公网隧道已开启（{message}）。把地址发给朋友即可。", "success"
        )
        self._update_console_banner()
        self._sync_online_controls()
        self.toast("公网隧道已开启，可以分享服务器地址了", "success")

    def _online_tunnel_failed(self, message: str) -> None:
        if self._online_closing:
            return
        process = self._sakura_tunnel
        if process is not None:
            try:
                process.blockSignals(True)
                process.stop()
            except Exception:
                pass
        self._fail_online_tunnel(message)

    def _online_tunnel_finished(self, code: int) -> None:
        if self._online_closing or self._sakura_tunnel is None:
            return
        self._sakura_tunnel = None
        if self._sakura_payload is not None:
            self._fail_online_tunnel(
                f"内置 frpc 已退出（代码 {code}），公网隧道已关闭。"
            )

    def _fail_online_tunnel(self, message: str) -> None:
        payload = self._sakura_payload
        if payload:
            remove_tunnel_config(payload.get("config_path"))
        self._sakura_payload = None
        self._sakura_tunnel = None
        self._sakura_busy = False
        self.online_progress.setVisible(False)
        self.online_address_box.setVisible(False)
        self._set_online_status(message, "error")
        self._update_console_banner()
        self._sync_online_controls()
        self.toast(message, "error")

    def close_online_tunnel(self) -> None:
        self._online_closing = True
        worker = self._sakura_worker
        if worker is not None:
            worker.cancel()
            self._sakura_worker = None
        self._sakura_busy = False
        process = self._sakura_tunnel
        self._sakura_tunnel = None
        if process is not None:
            process.blockSignals(True)
            process.stop()
        payload = self._sakura_payload
        if payload:
            remove_tunnel_config(payload.get("config_path"))
            self._append_console(
                self._selected_folder, "已关闭公网隧道"
            )
        self._sakura_payload = None
        self.online_progress.setVisible(False)
        self.online_address_box.setVisible(False)
        self._set_online_status("公网隧道已关闭。", "muted")
        self._online_closing = False
        self._update_console_banner()
        self._sync_online_controls()

    def _copy_public_address(self) -> None:
        payload = self._sakura_payload or {}
        address = str(payload.get("address") or "")
        if not address:
            self.toast("隧道还没有准备好", "info")
            return
        QApplication.clipboard().setText(address)
        self.toast("公网服务器地址已复制", "success")

    def _sync_online_controls(self) -> None:
        if not hasattr(self, "online_start_btn"):
            return
        info = self._current_info()
        active = (
            self._sakura_payload is not None or self._sakura_tunnel is not None
        )
        self.online_start_btn.setEnabled(
            bool(info)
            and bool(info.installed)
            and bool(self._sakura_user)
            and not self._sakura_busy
            and not active
        )
        self.online_close_btn.setVisible(active or self._sakura_worker is not None)
        self.online_close_btn.setEnabled(not self._sakura_busy or active)
        self.online_login_btn.setEnabled(not self._sakura_busy and not active)
        self.online_token_edit.setEnabled(not active)
        self.online_node_combo.setEnabled(
            bool(self._sakura_user) and not active
        )
        self.online_node_refresh.setEnabled(
            not self._sakura_busy and not active
        )
        self.online_settings_btn.setEnabled(bool(info))
        if active and self._sakura_payload is not None:
            address = str(self._sakura_payload.get("address") or "")
            if address:
                self.online_address.setText(address)
        self._update_console_banner()

    def _update_console_banner(self) -> None:
        if not hasattr(self, "console_banner"):
            return
        info = self._current_info()
        address = str((self._sakura_payload or {}).get("address") or "")
        if address:
            self.console_banner_label.setText(
                f"公网隧道已开启：{address}。朋友在多人游戏里添加这个地址即可。"
            )
            self.console_banner.setVisible(True)
            return
        process = self._processes.get(info.folder) if info else None
        state = process.state if process is not None else "stopped"
        if state != "stopped":
            self.console_banner_label.setText(
                "服务端正在运行。想让朋友从公网进入，请打开“公网联机”"
                "启动樱花穿透（无需房间号，直接分享服务器地址）。"
            )
            self.console_banner.setVisible(True)
        else:
            self.console_banner.setVisible(False)

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
        self.rename_icon_btn.setIcon(icons.icon("edit", colors()["text2"], 16))
        self.public_btn.setIcon(icons.icon("globe", colors()["text2"], 16))
        self.online_start_btn.setIcon(icons.icon("globe", "#FFFFFF", 15))
        self.online_copy_btn.setIcon(icons.icon("copy", colors()["accent"], 14))
        self.pack_btn.setIcon(icons.icon("package", colors()["accent"], 14))
        self.addon_open_btn.setIcon(icons.icon("folder", colors()["text2"], 14))
        self.addon_check_btn.setIcon(icons.icon("refresh", colors()["text2"], 14))
        self.addon_import_btn.setIcon(icons.icon("plus", colors()["text2"], 14))
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
        login_worker = self._sakura_login_worker
        if login_worker is not None:
            try:
                login_worker.ready.disconnect()
            except Exception:
                pass
            self._sakura_login_worker = None
        try:
            self.close_online_tunnel()
        except Exception:
            pass
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
