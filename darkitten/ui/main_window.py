from __future__ import annotations

import ctypes
import os
import sys
import urllib.parse
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

from shiboken6 import isValid
from PySide6.QtCore import QEvent, QPoint, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QCloseEvent, QDesktopServices, QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..accounts import Account
from ..config import (
    APP_DIR,
    APP_NAME,
    APP_SHORT_NAME,
    APP_TAGLINE,
    APP_VERSION,
    DEFAULT_UPDATE_URL,
    LOG_DIR,
    Config,
)
from ..updater import restart_executable, schedule_apply
from ..java import pick_java
from ..launch import (
    GameProcess,
    MissingFilesError,
    redacted_command_line,
)
from ..social import load_store
from ..versions import (
    check_installation,
    dependent_versions,
    game_versions as list_game_versions,
    loader_profiles as list_loader_profiles,
    resolve_version,
    scan_versions,
    version_json_path,
)
from ..workers import (
    DeleteWorker,
    InstallWorker,
    JavaDetectWorker,
    LaunchWorker,
    UpdateCheckWorker,
)
from . import icons, theme
from .animations import GameLoadingOverlay
from .dialogs import ConfirmDialog, NoticeDialog
from .download_center import DownloadCenter, DownloadTasksDialog, TopDownloadChip
from .update_dialog import UpdateDialog
from .pages.download import DownloadPage
from .pages.home import HomePage
from .pages.loaders import LoaderPage
from .pages.mods import ModsPage
from .pages.room import RoomPage
from .pages.server import ServerPage
from .pages.settings import SettingsPage
from .pages.skin import SkinPage
from .widgets import (
    Avatar,
    GrassLogo,
    LogPanel,
    NavButton,
    Surface,
    Toast,
    colors,
    icon_button,
    local_transfer_paths,
    refresh_themed_icons,
    transparent_children,
)

PAGE_INDEX = {
    "home": 0,
    "download": 1,
    "loaders": 2,
    "mods": 3,
    "skin": 4,
    "room": 5,
    "server": 6,
    "settings": 7,
}


class TitleBar(QFrame):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window)
        self._window = window
        self.setObjectName("TitleBar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(56)
        self._drag_offset: QPoint | None = None

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton and not self._window.isMaximized():
            self._drag_offset = event.globalPosition().toPoint() - self._window.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            if self._window.isMaximized():
                return
            self._window.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._window.toggle_maximize()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class AccountChip(QFrame):
    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AccountChip")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(40)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 3, 11, 3)
        layout.setSpacing(7)
        self.avatar = Avatar(30)
        self.name = QLabel("Steve")
        self.name.setStyleSheet("font-weight: 700; font-size: 12px;")
        self.name.setMinimumWidth(74)
        self.mode = QLabel("离线")
        self.mode.setObjectName("Tiny")
        layout.addWidget(self.avatar)
        layout.addWidget(self.name)
        layout.addWidget(self.mode)
        transparent_children(self)

    def set_account(self, name: str, mode: str, online: bool = False) -> None:
        self.name.setText(name)
        self.name.setToolTip(name)
        self.mode.setText(mode)
        self.mode.setStyleSheet(
            f"color: {colors()['green']}; font-weight: 700;" if online else ""
        )

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class MainWindow(QWidget):
    versions_changed = Signal()
    selection_changed = Signal(str)
    java_installs_changed = Signal(object)
    account_changed = Signal()
    install_started = Signal(str)
    install_progress = Signal(int, str)
    install_finished = Signal(bool, str, str)
    task_log = Signal(str)
    game_state_changed = Signal(bool)

    def __init__(self, config: Config | None = None) -> None:
        super().__init__()
        self.config = config or Config.load()
        account = Account.from_config(self.config.account)
        self.social_store = load_store()
        self.social_store.ensure_profile(
            (account.display_name if account.is_msa else self.config.player_name)
            or "Steve"
        )
        self.versions = scan_versions(self.config.game_path)
        self._repair_loader_bindings()
        self.selected_version = ""
        games = list_game_versions(self.versions)
        if games:
            ids = {v.id for v in games}
            self.selected_version = (
                self.config.last_version
                if self.config.last_version in ids
                else games[0].id
            )

        self.java_installs = []
        self._java_worker: JavaDetectWorker | None = None
        self._java_busy = False
        self.install_worker: InstallWorker | None = None
        self.install_active = False
        self._install_version = ""
        self._install_success_callback = None
        self._install_cancelling = False
        self.launch_worker: LaunchWorker | None = None
        self.launch_active = False
        self._launch_cancel_requested = False
        self._launch_closing = False
        self._launch_connect_address = ""
        self._launch_auto_retry = True
        self._launch_profile_id = ""
        self._running_version = ""
        self._running_profile_id = ""
        self.game_process: GameProcess | None = None
        self.game_running = False
        self._delete_worker: DeleteWorker | None = None
        self._game_log_file = None
        self.current_page = "home"
        self._update_check_worker: UpdateCheckWorker | None = None
        self._update_busy = False
        self._update_dialog: UpdateDialog | None = None
        self.last_update_release = None
        self.update_state_text = "尚未检查更新"
        self.update_state_kind = "idle"
        self.download_center = DownloadCenter(self)
        self._download_tasks_dialog: DownloadTasksDialog | None = None

        self.setObjectName("Root")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(icons.app_icon())
        self.setMinimumSize(980, 640)
        self.setAcceptDrops(True)

        self._build_ui()
        QApplication.instance().installEventFilter(self)
        self._restore_geometry()
        self.ensure_java_detected()
        self._update_account()
        self._update_status_version()
        QTimer.singleShot(300, self._apply_dwm)
        if self._should_auto_check_updates():
            QTimer.singleShot(2800, lambda: self.check_for_updates(manual=False))

    # ------------------------------------------------------------------
    # 构建界面

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.titlebar = TitleBar(self)
        self._build_titlebar()
        root.addWidget(self.titlebar)

        self.stack = QStackedWidget()
        self.stack.setObjectName("Pages")
        self.home_page = HomePage(self)
        self.download_page = DownloadPage(self)
        self.loader_page = LoaderPage(self)
        self.mods_page = ModsPage(self)
        self.skin_page = SkinPage(self)
        self.room_page = RoomPage(self)
        self.server_page = ServerPage(self)
        self.settings_page = SettingsPage(self)
        self.stack.addWidget(self.home_page)
        self.stack.addWidget(self.download_page)
        self.stack.addWidget(self.loader_page)
        self.stack.addWidget(self.mods_page)
        self.stack.addWidget(self.skin_page)
        self.stack.addWidget(self.room_page)
        self.stack.addWidget(self.server_page)
        self.stack.addWidget(self.settings_page)
        root.addWidget(self.stack, 1)

        self.log_panel = LogPanel()
        self.log_panel.closed.connect(lambda: self.log_btn.setText("运行日志"))
        root.addWidget(self.log_panel)
        root.addWidget(self._build_statusbar())

        self.loading_overlay = GameLoadingOverlay(self)
        self.loading_overlay.cancel_requested.connect(self.cancel_launch)
        self.loading_overlay.hide()

        self.toast_widget = Toast(self)
        self.drop_hint = QLabel(
            "松开猫爪，把文件放进猫窝\n"
            "版本 ZIP / 版本文件夹 · 整合包 · 服务端\n"
            "Mod .jar · 材质 / 光影 ZIP（可多选）", self
        )
        self.drop_hint.setAlignment(Qt.AlignCenter)
        self.drop_hint.setWordWrap(True)
        self.drop_hint.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.drop_hint.setFixedSize(420, 108)
        self.drop_hint.setStyleSheet(
            "background: rgba(20, 12, 18, 238);"
            f" border: 2px dashed {colors()['accent']};"
            " border-radius: 16px; color: #F8E9EF;"
            " font-size: 14px; font-weight: 700;"
        )
        self.drop_hint.hide()
        self.account_changed.connect(self._update_account)
        self.account_changed.connect(self.home_page._refresh_status)
        self.download_center.tasks_changed.connect(self._refresh_download_chip)
        self.download_center.task_finished.connect(self._on_download_task_finished)

        self.speed_timer = QTimer(self)
        self.speed_timer.setInterval(600)
        self.speed_timer.timeout.connect(self._tick_speed)

        self._update_nav("home")
        self._update_window_icons()

    def _build_titlebar(self) -> None:
        layout = QHBoxLayout(self.titlebar)
        layout.setContentsMargins(14, 0, 10, 0)
        layout.setSpacing(0)

        logo = GrassLogo(32)
        layout.addWidget(logo)
        layout.addSpacing(7)

        brand = QVBoxLayout()
        brand.setSpacing(0)
        name = QLabel(APP_NAME)
        name.setObjectName("AppName")
        name.setMinimumWidth(128)
        name.setToolTip(f"{APP_NAME} · {APP_TAGLINE}")
        tag = QLabel(f"LAUNCHER · {APP_SHORT_NAME}")
        tag.setObjectName("AppTag")
        brand.addWidget(name)
        brand.addWidget(tag)
        layout.addLayout(brand)
        layout.addSpacing(18)

        self.nav_buttons: dict[str, NavButton] = {}
        nav_specs = [
            ("home", "启动", "play", "#E23A5E"),
            ("download", "游戏版本", "download", "#E8683F"),
            ("loaders", "加载器", "package", "#D99A3D"),
            ("mods", "模组", "cube", "#B15BE0"),
            ("skin", "皮肤", "shirt", "#F06292"),
            ("room", "联机", "users", "#3FB3AA"),
            ("server", "服务端", "server", "#3FB3AA"),
            ("settings", "设置", "settings", "#C98A4B"),
        ]
        for key, text, icon_name, accent in nav_specs:
            button = NavButton(text, icon_name, accent)
            button.clicked.connect(lambda _=False, k=key: self.switch_page(k))
            self.nav_buttons[key] = button
            layout.addWidget(button)
            layout.addSpacing(2)

        layout.addStretch(1)
        self.download_chip = TopDownloadChip()
        self.download_chip.clicked.connect(self.open_download_tasks)
        layout.addWidget(self.download_chip)
        layout.addSpacing(8)
        self.account_chip = AccountChip()
        self.account_chip.clicked.connect(self.open_social_dialog)
        layout.addWidget(self.account_chip)
        layout.addSpacing(10)

        self.min_btn = QPushButton()
        self.max_btn = QPushButton()
        self.close_btn = QPushButton()
        for button, obj, tip, callback in (
            (self.min_btn, "WinBtn", "最小化", self.showMinimized),
            (self.max_btn, "WinBtn", "最大化", self.toggle_maximize),
            (self.close_btn, "CloseBtn", "关闭", self.close),
        ):
            button.setObjectName(obj)
            button.setFixedSize(34, 30)
            button.setCursor(Qt.PointingHandCursor)
            button.setToolTip(tip)
            button.setIconSize(QSize(14, 14))
            button.clicked.connect(callback)
            layout.addWidget(button)

        account = self.current_account()
        self.account_chip.set_account(
            account.display_name if account.is_msa else self.config.player_name,
            "正版" if account.is_msa else "离线",
            account.is_msa,
        )

    def _build_statusbar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("StatusBar")
        bar.setAttribute(Qt.WA_StyledBackground, True)
        bar.setFixedHeight(34)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 0, 10, 0)
        layout.setSpacing(8)

        self.status_dot = QLabel("●")
        self.status_dot.setStyleSheet(f"color: {colors()['green']}; font-size: 9px;")
        self.status_text = QLabel("就绪")
        self.status_text.setObjectName("StatusText")
        layout.addWidget(self.status_dot)
        layout.addWidget(self.status_text)
        layout.addSpacing(8)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedSize(150, 6)
        self.progress_bar.setVisible(False)
        self.speed_label = QLabel("")
        self.speed_label.setObjectName("Tiny")
        self.speed_label.setVisible(False)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.speed_label)
        self.action_cancel_btn = QPushButton("")
        self.action_cancel_btn.setObjectName("Ghost")
        self.action_cancel_btn.setCursor(Qt.PointingHandCursor)
        self.action_cancel_btn.setFixedHeight(24)
        self.action_cancel_btn.setVisible(False)
        self.action_cancel_btn.clicked.connect(self.cancel_current_action)
        layout.addWidget(self.action_cancel_btn)
        layout.addStretch(1)

        self.mc_label = QLabel("")
        self.mc_label.setObjectName("Tiny")
        self.java_label = QLabel("Java 未检测")
        self.java_label.setObjectName("Tiny")
        layout.addWidget(self.mc_label)
        layout.addWidget(self._status_separator())
        layout.addWidget(self.java_label)
        layout.addSpacing(6)

        self.log_btn = QPushButton("运行日志")
        self.log_btn.setObjectName("Ghost")
        self.log_btn.setCursor(Qt.PointingHandCursor)
        self.log_btn.clicked.connect(self.toggle_log)
        layout.addWidget(self.log_btn)
        return bar

    @staticmethod
    def _status_separator() -> QLabel:
        label = QLabel("|")
        label.setObjectName("Tiny")
        return label

    def _restore_geometry(self) -> None:
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry() if screen else None
        width = max(980, int(self.config.window_width or 1180))
        height = max(640, int(self.config.window_height or 760))
        if available:
            width = min(width, available.width() - 40)
            height = min(height, available.height() - 40)
        self.resize(width, height)
        if available:
            self.move(
                available.x() + (available.width() - width) // 2,
                available.y() + (available.height() - height) // 2,
            )

    # ------------------------------------------------------------------
    # 主题 / 窗口

    def apply_theme(self, theme_name: str) -> None:
        theme.set_current(theme_name)
        app = QApplication.instance()
        if app:
            app.setStyleSheet(theme.stylesheet(theme_name))
        refresh_themed_icons()
        self._update_nav(self.current_page)
        self._update_window_icons()
        if hasattr(self, "home_page"):
            self.home_page.refresh_versions()
        if hasattr(self, "download_page"):
            self.download_page.refresh_theme()
        if hasattr(self, "loader_page"):
            self.loader_page.refresh_theme()
        if hasattr(self, "mods_page"):
            self.mods_page.refresh_mods()
        if hasattr(self, "skin_page"):
            self.skin_page.refresh_theme()
        if hasattr(self, "room_page"):
            self.room_page.refresh_theme()
        if hasattr(self, "server_page"):
            self.server_page.refresh_theme()
        if hasattr(self, "account_chip"):
            self.account_chip.avatar.refresh()
        if hasattr(self, "download_chip"):
            self.download_chip.sync(self.download_center)
        for widget in QApplication.allWidgets():
            widget.update()
        self._apply_dwm()

    def _update_nav(self, current: str) -> None:
        self.current_page = current
        for key, button in self.nav_buttons.items():
            button.set_active(key == current)

    def _update_window_icons(self) -> None:
        c = colors()["text2"]
        self.min_btn.setIcon(icons.icon("minimize", c, 14))
        self.max_btn.setIcon(
            icons.icon("restore" if self.isMaximized() else "maximize", c, 13)
        )
        self.close_btn.setIcon(icons.icon("close", c, 14))

    def _apply_dwm(self) -> None:
        if sys.platform != "win32":
            return
        try:
            hwnd = int(self.winId())
            dwm = ctypes.windll.dwmapi
            dwm.DwmSetWindowAttribute.argtypes = [
                wintypes.HWND,
                wintypes.DWORD,
                ctypes.c_void_p,
                wintypes.DWORD,
            ]
            dark = ctypes.c_int(1 if self.config.theme == "dark" else 0)
            dwm.DwmSetWindowAttribute(
                hwnd, 20, ctypes.byref(dark), ctypes.sizeof(dark)
            )
            corner = ctypes.c_int(2)
            dwm.DwmSetWindowAttribute(
                hwnd, 33, ctypes.byref(corner), ctypes.sizeof(corner)
            )
        except Exception:
            pass

    def toggle_maximize(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        QTimer.singleShot(0, self._update_window_icons)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange and hasattr(self, "max_btn"):
            self._update_window_icons()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "loading_overlay") and self.loading_overlay.isVisible():
            self.loading_overlay.setGeometry(self.rect())
        if hasattr(self, "drop_hint") and self.drop_hint.isVisible():
            self._position_drop_hint()
        if hasattr(self, "toast_widget") and self.toast_widget.isVisible():
            parent = self.toast_widget.parentWidget()
            if parent:
                self.toast_widget.move(
                    (parent.width() - self.toast_widget.width()) // 2,
                    parent.height() - self.toast_widget.height() - 58,
                )

    # ------------------------------------------------------------------
    # 拖拽导入

    @staticmethod
    def _archive_paths(mime) -> list[Path]:
        return local_transfer_paths(mime)

    def _position_drop_hint(self) -> None:
        self.drop_hint.move(
            (self.width() - self.drop_hint.width()) // 2,
            max(80, int(self.height() * 0.38)),
        )

    def _show_drop_hint(self) -> None:
        if not hasattr(self, "drop_hint"):
            return
        self._position_drop_hint()
        self.drop_hint.show()
        self.drop_hint.raise_()

    def _hide_drop_hint(self) -> None:
        if hasattr(self, "drop_hint"):
            self.drop_hint.hide()

    def eventFilter(self, obj, event) -> bool:
        try:
            etype = event.type()
        except (RuntimeError, AttributeError):
            return False
        if etype in (
            QEvent.DragEnter,
            QEvent.DragMove,
            QEvent.DragLeave,
            QEvent.Drop,
        ):
            if (
                isinstance(obj, QWidget)
                and isValid(obj)
                and obj.window() is self
                and isValid(self)
                and QApplication.activeModalWidget() is None
            ):
                if etype == QEvent.DragLeave:
                    self._hide_drop_hint()
                    return False
                try:
                    paths = self._archive_paths(event.mimeData())
                except RuntimeError:
                    return False
                if paths:
                    if etype == QEvent.Drop:
                        self._hide_drop_hint()
                        event.setDropAction(Qt.CopyAction)
                        event.accept()
                        self._handle_dropped_archives(paths)
                    else:
                        event.setDropAction(Qt.CopyAction)
                        event.accept()
                        self._show_drop_hint()
                    return True
        return False

    def _handle_dropped_archives(self, paths: list[Path]) -> None:
        if not paths:
            return
        if self.install_active or self.launch_active:
            self.toast("有任务正在进行，稍等一下喵", "info")
            return
        self.open_local_import(paths)

    def nativeEvent(self, eventType, message):
        if sys.platform == "win32" and eventType == b"windows_generic_MSG":
            try:
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == 0x0084 and not self.isMaximized():
                    hwnd = msg.hwnd
                    rect = wintypes.RECT()
                    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
                    border = max(5, int(6 * self.devicePixelRatioF()))
                    x = ctypes.c_short(msg.lParam & 0xFFFF).value
                    y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
                    left = x < rect.left + border
                    right = x >= rect.right - border
                    top = y < rect.top + border
                    bottom = y >= rect.bottom - border
                    if top and left:
                        return True, 13
                    if top and right:
                        return True, 14
                    if bottom and left:
                        return True, 16
                    if bottom and right:
                        return True, 17
                    if left:
                        return True, 10
                    if right:
                        return True, 11
                    if top:
                        return True, 12
                    if bottom:
                        return True, 15
            except Exception:
                pass
        return super().nativeEvent(eventType, message)

    # ------------------------------------------------------------------
    # 启动器更新

    def _should_auto_check_updates(self) -> bool:
        if not self.config.update_auto_check:
            return False
        if "--no-update-check" in sys.argv or "--screenshot" in sys.argv:
            return False
        if os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen":
            return False
        return bool(str(self.config.update_url or "").strip())

    def check_for_updates(self, manual: bool = False) -> None:
        if self._update_busy:
            if manual:
                self.toast("正在检查更新，稍等一下喵", "info")
            return
        url = str(self.config.update_url or DEFAULT_UPDATE_URL).strip()
        if not url:
            if manual:
                self.toast("请先在设置里填写更新源地址", "error")
            return
        self._update_busy = True
        self.update_state_text = "正在检查更新…"
        self.update_state_kind = "busy"
        self.refresh_update_state()
        if manual:
            self.set_status("正在检查启动器更新…", "busy")
        worker = UpdateCheckWorker(APP_VERSION, url, self)
        self._update_check_worker = worker
        worker.ready.connect(
            lambda release, error: self._update_check_ready(
                release, error, manual
            )
        )
        worker.start()

    def _update_check_ready(self, release, error: str, manual: bool) -> None:
        self._update_busy = False
        self._update_check_worker = None
        self.config.update_last_check = datetime.now().strftime("%Y-%m-%d %H:%M")
        self.config.save()
        if error:
            self.update_state_text = "检查更新失败"
            self.update_state_kind = "error"
            self.refresh_update_state()
            self.log(f"检查更新失败：{error}")
            if manual:
                self.set_status("检查更新失败", "error")
                NoticeDialog(
                    self,
                    "检查更新失败",
                    "暂时无法读取更新清单。下载站可能没有启动，或者更新源地址不可用。"
                    f"\n\n{error}\n更新源：{self.config.update_url}",
                    icon_name="warning",
                ).exec()
            return

        if release is None:
            self.last_update_release = None
            self.update_state_text = f"v{APP_VERSION} 已是最新版本"
            self.update_state_kind = "success"
            self.refresh_update_state()
            self.set_status(f"已是最新版本 v{APP_VERSION}", "success")
            self.log(f"检查更新：当前 v{APP_VERSION} 已是最新版本")
            if manual:
                self.toast("已经是最新版本啦喵", "success")
            return

        self.last_update_release = release
        self.update_state_text = f"发现新版本 v{release.version}"
        self.update_state_kind = "accent"
        self.refresh_update_state()
        self.set_status(f"发现新版本 v{release.version}", "success")
        self.log(
            f"检查更新：发现 v{release.version}（当前 v{APP_VERSION}）"
        )
        if not manual:
            skipped = str(self.config.update_skipped_version or "").strip()
            if release.version == skipped and not release.mandatory:
                self.log(f"已跳过 v{release.version}，等待用户手动检查")
                return
        self.open_update_dialog(release)

    def refresh_update_state(self) -> None:
        if hasattr(self, "settings_page"):
            self.settings_page.refresh_update_state()

    def open_update_dialog(self, release) -> None:
        if self._update_dialog is not None:
            try:
                self._update_dialog.raise_()
                self._update_dialog.activateWindow()
            except Exception:
                pass
            return
        dialog = UpdateDialog(self, release)
        self._update_dialog = dialog
        try:
            dialog.exec()
        finally:
            self._update_dialog = None
            self.refresh_update_state()
            self.raise_()
            self.activateWindow()

    def apply_update(self, payload: str, version: str = "") -> bool:
        if self.install_active or self.launch_active:
            self.toast("还有任务正在进行，完成或取消后再更新", "info")
            return False
        if self.game_running:
            self.toast("游戏还在运行，先关掉游戏再更新", "info")
            return False
        if hasattr(self, "server_page"):
            running = self.server_page.running_server_names()
            if running:
                self.toast("服务端还在运行，先停止服务端再更新", "info")
                return False
        try:
            schedule_apply(
                Path(payload),
                APP_DIR,
                python_exe=restart_executable(),
                restart=True,
            )
        except Exception as exc:
            self.toast(f"无法安排更新：{exc}", "error")
            return False
        self.config.update_skipped_version = ""
        self.config.save()
        self.log(
            f"准备更新到 v{version or '新版本'}，启动器即将退出并自动替换代码"
        )
        self.update_state_text = f"正在更新到 v{version}"
        self.update_state_kind = "busy"
        self.set_status("正在应用更新，启动器即将重启…", "busy")
        self.refresh_update_state()
        QTimer.singleShot(250, self.close)
        return True

    def open_update_site(self) -> None:
        url = ""
        release = self.last_update_release
        if release is not None and getattr(release, "page_url", ""):
            url = str(release.page_url)
        else:
            parsed = urllib.parse.urlsplit(
                str(self.config.update_url or DEFAULT_UPDATE_URL)
            )
            if parsed.scheme in ("http", "https") and parsed.netloc:
                url = f"{parsed.scheme}://{parsed.netloc}/"
        if not url:
            self.toast("这个更新源没有提供下载站地址", "info")
            return
        QDesktopServices.openUrl(QUrl(url))

    def closeEvent(self, event: QCloseEvent) -> None:
        if hasattr(self, "loading_overlay"):
            self.loading_overlay.abort()
        if self.install_active and self.install_worker:
            dialog = ConfirmDialog(
                self,
                "安装仍在进行",
                "现在退出会中断下载，已经下载完成的文件会保留。确定要退出吗？",
                primary_text="退出",
                secondary_text="继续安装",
                icon_name="warning",
            )
            if not dialog.exec():
                event.ignore()
                return
            self.install_worker.cancel()
        if self.download_center.has_running():
            dialog = ConfirmDialog(
                self,
                "下载仍在进行",
                "现在退出会中断正在下载的文件，已经完成的部分会保留。确定要退出吗？",
                primary_text="退出",
                secondary_text="继续下载",
                icon_name="download",
                icon_color=colors()["orange"],
            )
            if not dialog.exec():
                event.ignore()
                return
            for task in self.download_center.active_tasks():
                self.download_center.cancel(task.id)
        if hasattr(self, "server_page"):
            running = self.server_page.running_server_names()
            if running:
                dialog = ConfirmDialog(
                    self,
                    "服务端仍在运行",
                    "以下服务端仍在运行，退出启动器会先尝试安全关闭它们：",
                    primary_text="停止并退出",
                    secondary_text="取消",
                    icon_name="server",
                    icon_color=colors()["red"],
                    details="、".join(running),
                )
                if not dialog.exec():
                    event.ignore()
                    return
            self.server_page.shutdown()
        if hasattr(self, "room_page"):
            self.room_page.shutdown()
        if not self.isMaximized():
            self.config.window_width = self.width()
            self.config.window_height = self.height()
        self.config.save()
        if self._game_log_file:
            try:
                self._game_log_file.close()
            except Exception:
                pass
        event.accept()

    # ------------------------------------------------------------------
    # 导航 / 日志

    def switch_page(self, name: str) -> None:
        index = PAGE_INDEX.get(name, 0)
        self.stack.setCurrentIndex(index)
        self._update_nav(name)
        page = self.stack.widget(index)
        if hasattr(page, "on_show"):
            page.on_show()

    def log(self, text: str) -> None:
        if not text:
            return
        self.log_panel.append(text)

    def toggle_log(self) -> None:
        if self.log_panel.isVisible() and self.log_panel.maximumHeight() > 0:
            self.log_panel.close_panel()
            self.log_btn.setText("运行日志")
        else:
            self.log_panel.open_panel()
            self.log_btn.setText("收起日志")

    def toast(self, text: str, kind: str = "info") -> None:
        if hasattr(self, "toast_widget"):
            self.toast_widget.show_message(text, kind)

    def set_status(self, text: str, kind: str = "idle") -> None:
        palette = {
            "idle": colors()["muted"],
            "busy": colors()["accent"],
            "success": colors()["green"],
            "error": colors()["red"],
        }
        color = palette.get(kind, palette["idle"])
        self.status_dot.setStyleSheet(f"color: {color}; font-size: 9px;")
        self.status_text.setText(text)

    def open_path(self, path: Path | str) -> None:
        try:
            p = Path(path)
            if p.suffix == "":
                p.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))
        except Exception as exc:
            self.toast(f"无法打开路径：{exc}", "error")

    def open_game_dir(self) -> None:
        try:
            self.config.game_path.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            self.toast(f"无法创建游戏目录：{exc}", "error")
            return
        self.open_path(self.config.game_path)

    # ------------------------------------------------------------------
    # 版本 / Java

    def refresh_versions(self) -> None:
        self.versions = scan_versions(self.config.game_path)
        self._repair_loader_bindings()
        games = list_game_versions(self.versions)
        ids = {v.id for v in games}
        if self.selected_version not in ids:
            if self.config.last_version in ids:
                self.selected_version = self.config.last_version
            elif games:
                self.selected_version = games[0].id
            else:
                self.selected_version = ""
        self.versions_changed.emit()
        self.selection_changed.emit(self.selected_version)
        self._update_status_version()

    # ------------------------------------------------------------------
    # 加载器 / 游戏版本绑定

    def loader_profiles(self):
        return list_loader_profiles(self.versions)

    def game_version_summaries(self):
        return list_game_versions(self.versions)

    def loader_profile_for(self, version_id: str):
        """返回游戏版本当前绑定的纯加载器配置；无效时返回 None。"""
        summary = next((v for v in self.versions if v.id == version_id), None)
        if not summary or summary.is_loader_profile or summary.loader:
            return None
        profile_id = self.config.loader_for(version_id)
        if not profile_id:
            return None
        profile = next(
            (
                v
                for v in self.versions
                if v.id == profile_id and v.is_loader_profile
            ),
            None,
        )
        if not profile or profile.base_id != summary.base_id:
            return None
        return profile

    def compatible_loader_profiles(self, version_id: str):
        summary = next((v for v in self.versions if v.id == version_id), None)
        if not summary or summary.is_loader_profile or summary.loader:
            return []
        return [
            profile
            for profile in self.loader_profiles()
            if profile.base_id == summary.base_id
        ]

    def compatible_game_versions(self, loader_id: str):
        profile = next(
            (v for v in self.versions if v.id == loader_id),
            None,
        )
        if not profile or not profile.is_loader_profile:
            return []
        return [
            game
            for game in self.game_version_summaries()
            if not game.loader and game.base_id == profile.base_id
        ]

    def bind_loader(self, version_id: str, loader_id: str) -> None:
        self.config.bind_loader(version_id, loader_id)
        self.config.save()
        self.versions_changed.emit()
        self.selection_changed.emit(self.selected_version)
        self._update_status_version()

    def launch_profile(self, version_id: str) -> tuple[str, str]:
        """返回（启动配置 ID，游戏实例 ID）。"""
        summary = next((v for v in self.versions if v.id == version_id), None)
        if not summary:
            return version_id, version_id
        if summary.is_loader_profile:
            return summary.id, summary.base_id or summary.id
        profile = self.loader_profile_for(version_id)
        if profile:
            return profile.id, version_id
        return version_id, version_id

    def _launch_mode_note(self, profile_id: str, instance_id: str) -> str:
        summary = next(
            (v for v in self.versions if v.id == instance_id), None
        )
        if profile_id and profile_id != instance_id:
            return f" · 加载器 {profile_id}"
        if summary and summary.loader:
            return f" · {summary.loader}"
        return " · 原版"

    def _repair_loader_bindings(self) -> None:
        profiles = {
            item.id: item
            for item in self.versions
            if item.is_loader_profile
        }
        games = [
            item
            for item in self.versions
            if not item.is_loader_profile
        ]
        game_ids = {item.id for item in games}
        valid: dict[str, str] = {}
        changed = False
        for game in games:
            if game.loader:
                continue
            profile = profiles.get(self.config.loader_for(game.id))
            if profile and profile.base_id == game.base_id:
                valid[game.id] = profile.id

        # 旧版本里用户是直接选中加载器版本启动的。升级后把这类选择迁移到
        # 它继承的原版游戏版本上，避免打开首页时选中项凭空消失。
        last = next(
            (item for item in self.versions if item.id == self.config.last_version),
            None,
        )
        if last and last.is_loader_profile and last.base_id in game_ids:
            valid[last.base_id] = last.id
            self.config.last_version = last.base_id
            changed = True

        # 只有一个加载器候选时，升级后首次运行给它的基础版本补上绑定；
        # 其他同基础版本仍保持原版，等用户在加载器页面明确勾选。
        # 迁移完成后不再自动绑定，避免用户手动选择原版后被改回去。
        if not self.config.loaders_migrated:
            for game in games:
                if game.loader or game.id in valid:
                    continue
                candidates = [
                    item
                    for item in profiles.values()
                    if item.base_id == game.base_id
                ]
                if len(candidates) == 1 and game.id == candidates[0].base_id:
                    valid[game.id] = candidates[0].id

        # 旧绑定指向已删除或已不兼容的版本时一并清理。
        self.config.version_loaders = {
            key: value
            for key, value in self.config.version_loaders.items()
            if key in game_ids
        }
        if self.config.version_loaders != valid:
            self.config.version_loaders = valid
            changed = True
        if not self.config.loaders_migrated:
            self.config.loaders_migrated = True
            changed = True
        if changed:
            self.config.save()

    def _update_status_version(self) -> None:
        if hasattr(self, "mc_label"):
            self.mc_label.setText(
                f"MC: {self.selected_version}" if self.selected_version else "MC: —"
            )
        self._update_java_label()

    def ensure_java_detected(self, force: bool = False) -> None:
        if self._java_busy:
            return
        if self.java_installs and not force:
            return
        self._java_busy = True
        worker = JavaDetectWorker(self.config.java_path)
        self._java_worker = worker
        worker.ready.connect(self._java_ready)
        worker.start()

    def _java_ready(self, installs) -> None:
        self._java_busy = False
        self.java_installs = list(installs or [])
        self.java_installs_changed.emit(self.java_installs)
        self._update_java_label()

    def _update_java_label(self) -> None:
        if not hasattr(self, "java_label"):
            return
        required = 0
        profile_id, _instance_id = self.launch_profile(self.selected_version)
        summary = next(
            (v for v in self.versions if v.id == profile_id), None
        )
        if summary:
            required = summary.java_major
        chosen = pick_java(self.java_installs, required, self.config.java_path)
        if chosen:
            self.java_label.setText(f"Java {chosen.major or '?'}")
            self.java_label.setToolTip(chosen.path)
        else:
            self.java_label.setText("Java 未检测")
            self.java_label.setToolTip("")

    def current_account(self) -> Account:
        return Account.from_config(self.config.account)

    def display_name(self) -> str:
        account = self.current_account()
        return account.name if account.is_msa else self.config.player_name

    def _update_account(self) -> None:
        account = self.current_account()
        if hasattr(self, "account_chip"):
            self.account_chip.set_account(
                account.display_name if account.is_msa else self.config.player_name,
                "正版" if account.is_msa else "离线",
                account.is_msa,
            )
            self.account_chip.avatar.refresh()
        if hasattr(self, "home_page"):
            self.home_page._refresh_status()
        if hasattr(self, "settings_page"):
            self.settings_page.refresh_account()

    # ------------------------------------------------------------------
    # 安装

    def begin_install(
        self,
        version_id: str,
        version_url: str = "",
        verify: bool = False,
        success_callback=None,
    ) -> None:
        if self.install_active:
            self.toast("已有安装任务正在进行", "info")
            return
        if self.launch_active:
            self.toast("游戏正在启动，请稍候", "info")
            return
        if not version_id:
            self.toast("请先选择一个版本", "error")
            return

        worker = InstallWorker(
            self.config.game_path,
            version_id,
            version_url=version_url,
            source=self.config.download_source,
            verify=verify,
            threads=self.config.download_threads,
        )
        self.install_worker = worker
        self.install_active = True
        self._install_cancelling = False
        self._install_version = version_id
        self._install_success_callback = success_callback
        worker.progress.connect(self.install_progress.emit)
        worker.progress.connect(self._on_install_progress)
        worker.log_line.connect(self._on_task_log)
        worker.finished.connect(self._on_install_finished)
        self.install_started.emit(version_id)
        self.set_status(f"正在安装 {version_id}…", "busy")
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.speed_label.setText("")
        self.speed_label.setVisible(True)
        self.speed_timer.start()
        self._update_action_button()
        self.log(f"开始安装 {version_id}（{self.config.download_source}）")
        worker.start()

    def _on_install_progress(self, percent: int, message: str) -> None:
        self.progress_bar.setValue(percent)
        self.set_status(f"{self._install_version} · {message}", "busy")

    def _on_task_log(self, line: str) -> None:
        self.log(line)
        self.task_log.emit(line)

    def _tick_speed(self) -> None:
        worker = self.install_worker
        if worker and worker.installer:
            self.speed_label.setText(worker.installer.speed_text)

    def _on_install_finished(self, success: bool, message: str) -> None:
        version_id = self._install_version
        was_cancelling = self._install_cancelling
        cancelled = (not success) and (
            was_cancelling or "取消" in str(message)
        )
        self.install_active = False
        self._install_cancelling = False
        self.speed_timer.stop()
        self.speed_label.setText("")
        self.speed_label.setVisible(False)
        self.progress_bar.setVisible(False)
        self.progress_bar.setValue(100 if success else 0)
        if success:
            self.set_status(f"{version_id} 安装完成", "success")
            self.log(f"{version_id} 安装完成")
            self.toast(f"{version_id} 已准备就绪", "success")
        elif cancelled:
            self.set_status(f"已取消安装 {version_id}", "idle")
            self.log(f"已取消安装 {version_id}")
            self.toast("安装已取消，临时下载文件已清理", "info")
        else:
            self.set_status(f"安装失败：{message}", "error")
            self.log(f"安装失败：{message}")
            self.toast(f"安装失败：{message}", "error")

        self.refresh_versions()
        self._update_action_button()
        self.install_finished.emit(success, message, version_id)
        callback = self._install_success_callback
        self._install_success_callback = None
        if success and callback:
            QTimer.singleShot(150, callback)

    def cancel_install(self) -> None:
        if not self.install_active or not self.install_worker:
            return
        if self._install_cancelling:
            return
        self._install_cancelling = True
        self.install_worker.cancel()
        self.set_status("正在取消安装…", "busy")
        self.log("正在取消安装…")
        self.action_cancel_btn.setText("正在取消…")
        self.action_cancel_btn.setEnabled(False)

    def _update_action_button(self) -> None:
        if not hasattr(self, "action_cancel_btn"):
            return
        self.action_cancel_btn.setEnabled(True)
        if self.install_active:
            self.action_cancel_btn.setText(
                "正在取消…" if self._install_cancelling else "取消安装"
            )
            self.action_cancel_btn.setEnabled(not self._install_cancelling)
            self.action_cancel_btn.setVisible(True)
        elif self.launch_active:
            self.action_cancel_btn.setText(
                "正在取消…" if self._launch_cancel_requested else "取消启动"
            )
            self.action_cancel_btn.setEnabled(not self._launch_cancel_requested)
            self.action_cancel_btn.setVisible(True)
        elif self.game_running:
            self.action_cancel_btn.setText(
                "正在关闭…" if self._launch_closing else "关闭游戏"
            )
            self.action_cancel_btn.setEnabled(not self._launch_closing)
            self.action_cancel_btn.setVisible(True)
        else:
            self.action_cancel_btn.setVisible(False)

    def cancel_current_action(self) -> None:
        if self.install_active:
            self.cancel_install()
        elif self.launch_active:
            self.cancel_launch()
        elif self.game_running:
            self.close_game()

    def complete_selected(self) -> None:
        if not self.selected_version:
            self.toast("请先选择一个版本", "info")
            return
        profile_id, _instance_id = self.launch_profile(self.selected_version)
        try:
            resolved = resolve_version(self.config.game_path, profile_id)
            missing = check_installation(self.config.game_path, resolved, check_assets=False)
        except Exception as exc:
            self.toast(f"无法读取版本：{exc}", "error")
            return
        if not missing:
            self.toast("当前版本文件已完整", "success")
            return
        self.begin_install(profile_id, verify=True)
        self.log_panel.open_panel()
        self.log_btn.setText("收起日志")

    def open_version_settings(self, version_id: str = "") -> None:
        version_id = version_id or self.selected_version
        if not version_id:
            self.toast("先选一个游戏版本，再打开版本设置", "info")
            return
        from .version_settings_dialog import VersionSettingsDialog

        VersionSettingsDialog(self, version_id).exec()
        self.refresh_versions()

    def open_loader_dialog(self) -> None:
        if self.install_active:
            self.toast("已有安装任务正在进行，请稍候", "info")
            return
        from .loader_dialog import LoaderDialog

        self.ensure_java_detected()
        LoaderDialog(self).exec()

    def open_mod_download(self) -> None:
        from .mod_dialog import ModBrowserDialog

        ModBrowserDialog(self).exec()
        self._refresh_download_chip()

    def _refresh_download_chip(self) -> None:
        if hasattr(self, "download_chip"):
            self.download_chip.sync(self.download_center)

    def _on_download_task_finished(self, task) -> None:
        filename = str(getattr(task, "filename", "") or task.title)
        content_type = str(getattr(task, "content_type", ""))
        status = str(getattr(task, "status", ""))
        if status == "success":
            if content_type == "mod" and hasattr(self, "mods_page"):
                self.mods_page.refresh_mods()
            self.set_status(f"下载完成：{filename}", "success")
            if content_type == "modpack":
                self.toast(
                    "整合包下载完成，可在顶栏下载任务中导入",
                    "success",
                )
            else:
                self.toast(f"已下载 {filename}", "success")
        elif status == "cancelled":
            self.set_status("下载已取消", "idle")
            self.toast("下载已取消，临时文件已清理", "info")
        else:
            message = str(getattr(task, "error", "") or getattr(task, "message", ""))
            self.set_status(f"下载失败：{message}", "error")
            self.toast(f"下载失败：{message}", "error")

    def open_download_tasks(self) -> None:
        if self._download_tasks_dialog is not None:
            self._download_tasks_dialog.refresh()
            self._download_tasks_dialog.raise_()
            self._download_tasks_dialog.activateWindow()
            return
        dialog = DownloadTasksDialog(self)
        self._download_tasks_dialog = dialog
        dialog.finished.connect(self._on_download_tasks_closed)
        dialog.show()

    def _on_download_tasks_closed(self, _result: int = 0) -> None:
        dialog = self._download_tasks_dialog
        self._download_tasks_dialog = None
        if dialog is not None:
            dialog.deleteLater()

    def open_migration_dialog(self, source_id: str = "") -> None:
        if self.install_active or self.launch_active:
            self.toast("有安装或启动任务正在进行，请稍候", "info")
            return
        if self.game_running:
            self.toast("游戏正在运行，先关掉游戏再迁移更安全", "info")
            return
        from .migration_dialog import MigrationDialog

        self.ensure_java_detected()
        MigrationDialog(self, source_id or self.selected_version).exec()

    def open_social_dialog(self) -> None:
        from .social_dialog import SocialDialog

        dialog = SocialDialog(self)
        dialog.join_requested.connect(self.join_friend_room)
        dialog.exec()

    def offer_shortcut_once(self) -> None:
        """第一次启动时询问是否创建桌面快捷方式，之后不再打扰。"""
        if bool(self.config.shortcut_offered):
            return
        from ..shortcut import shortcut_exists

        if shortcut_exists():
            self.config.shortcut_offered = True
            self.config.save()
            if hasattr(self, "settings_page"):
                self.settings_page.refresh_shortcut_state()
            return
        dialog = ConfirmDialog(
            self,
            "创建桌面快捷方式",
            "要把 Darkitten Launcher 放到桌面吗？以后双击桌面上的猫猫图标就能启动，"
            "不用每次翻文件夹。",
            primary_text="创建快捷方式",
            secondary_text="暂不创建",
            icon_name="desktop",
            icon_color=colors()["accent"],
            details="之后也可以在“设置 → 启动器设置”里重新创建，快捷方式只保存在本机。",
        )
        create = dialog.exec()
        self.config.shortcut_offered = True
        self.config.save()
        if create:
            self.create_desktop_shortcut()

    def create_desktop_shortcut(self) -> bool:
        from ..shortcut import ShortcutError
        from ..shortcut import create_desktop_shortcut as build_shortcut

        try:
            path = build_shortcut()
        except ShortcutError as exc:
            self.toast(f"创建快捷方式失败：{exc}", "error")
            return False
        self.toast(f"桌面快捷方式已创建：{path.name}", "success")
        if hasattr(self, "settings_page"):
            self.settings_page.refresh_shortcut_state()
        return True

    def join_friend_room(self, room_code: str, room_version: str = "") -> None:
        if not room_code:
            self.toast("好友还没有分享房间号", "info")
            return
        if self.game_running:
            self.toast("游戏已经在运行，先把当前游戏关掉", "info")
            return
        if self.launch_active or self.install_active:
            self.toast("有任务正在进行，请稍候", "info")
            return
        self.switch_page("room")
        QTimer.singleShot(
            0, lambda: self.room_page.join_friend_room(room_code, room_version)
        )

    def export_version(self, version_id: str = "") -> None:
        version_id = version_id or self.selected_version
        if not version_id:
            self.toast("先选一个版本，猫猫才有东西可打包", "info")
            return
        if self.install_active and self._install_version == version_id:
            self.toast("这个版本还在安装，等装完再打包", "info")
            return
        from .pack_dialog import PackExportDialog

        PackExportDialog(self, version_id).exec()

    def open_pack_import(self, path: str | Path | None = None) -> None:
        if self.install_active or self.launch_active:
            self.toast("有任务正在进行，稍等一下喵", "info")
            return
        from .pack_dialog import PackImportDialog

        dialog = PackImportDialog(self, initial_path=path)
        dialog.exec()
        if dialog.imported_version:
            self.select_version(dialog.imported_version)

    def open_local_import(
        self,
        path: str | Path | list[str | Path] | None = None,
        *,
        initial_paths: list[str | Path] | None = None,
    ) -> None:
        """统一识别本地文件，再转到版本、整合包、服务端或内容导入。"""
        if self.install_active or self.launch_active:
            self.toast("有任务正在进行，稍等一下喵", "info")
            return
        from ..local_import import LocalImportError, detect_local_import
        from .local_import_dialog import LocalImportDialog

        raw: list[str | Path] = []
        if isinstance(path, (list, tuple)):
            raw.extend(path)
        elif path is not None:
            raw.append(path)
        if initial_paths:
            raw.extend(initial_paths)
        sources = [Path(item) for item in raw if str(item or "").strip()]
        if not sources:
            dialog = LocalImportDialog(self)
            dialog.exec()
            return
        try:
            infos = [detect_local_import(item) for item in sources]
        except LocalImportError as exc:
            self.toast(str(exc), "error")
            return

        if len(infos) == 1:
            info = infos[0]
            if info.kind == "server":
                self.open_server_import(info.path)
                return
            if info.kind == "modpack":
                self.open_pack_import(info.path)
                return
            if info.kind == "version_zip":
                self.open_version_import(info.path)
                return
        elif any(info.kind != "content" for info in infos):
            self.toast("一次可以导入多个 Mod / 材质 / 光影，但版本包和服务端要单独导入", "info")
            return

        dialog = LocalImportDialog(self, initial_paths=sources)
        dialog.exec()
        if dialog.imported_version:
            self.select_version(dialog.imported_version)

    def open_version_import(self, path: str | Path | None = None) -> None:
        if self.install_active or self.launch_active:
            self.toast("有任务正在进行，稍等一下喵", "info")
            return
        from ..curseforge_pack import (
            inspect_curseforge_pack,
            looks_like_curseforge_pack,
        )
        from ..modrinth_pack import inspect_modrinth_pack, looks_like_modrinth_pack
        from ..packs import inspect_pack
        from ..version_zip import inspect_version_zip
        from .version_zip_dialog import VersionZipDialog

        if path:
            source = Path(path)
            if not source.is_file():
                self.toast(f"找不到文件：{source.name}", "error")
                return
            suffix = source.suffix.lower()
            if suffix not in (".zip", ".dklpack", ".mrpack"):
                self.toast(
                    "只认识 .zip、.dklpack 和 .mrpack 三种压缩包",
                    "error",
                )
                return
            try:
                inspect_pack(source)
            except Exception:
                pass
            else:
                self.open_pack_import(source)
                return
            if looks_like_modrinth_pack(source):
                try:
                    inspect_modrinth_pack(source)
                except Exception as exc:
                    self.toast(f"这个 Modrinth 整合包不能导入：{exc}", "error")
                    return
                self.open_pack_import(source)
                return
            if looks_like_curseforge_pack(source):
                try:
                    inspect_curseforge_pack(source)
                except Exception as exc:
                    self.toast(f"这个 CurseForge 整合包不能导入：{exc}", "error")
                    return
                self.open_pack_import(source)
                return
            try:
                inspect_version_zip(source)
            except Exception as exc:
                self.toast(f"这个压缩包不能当版本用：{exc}", "error")
                return

        dialog = VersionZipDialog(self, initial_path=path)
        dialog.exec()
        if dialog.imported_version:
            self.select_version(dialog.imported_version)

    def open_server_import(self, path: str | Path | None = None) -> None:
        self.switch_page("server")
        self.server_page.open_import_dialog(path)

    def select_version(self, version_id: str) -> None:
        if not version_id:
            return
        if hasattr(self, "home_page"):
            self.home_page.select_version(version_id)
        self.selected_version = version_id
        self._update_status_version()

    # ------------------------------------------------------------------
    # 删除版本

    def delete_version(self, version_id: str) -> None:
        if not version_id:
            return
        if self.install_active and self._install_version == version_id:
            self.toast("该版本正在安装，请先取消安装", "info")
            return
        if self.game_running and (
            self._running_version == version_id
            or self._running_profile_id == version_id
        ):
            self.toast("该版本正在运行，请先关闭游戏", "error")
            return
        if self._delete_worker is not None:
            self.toast("已有删除任务正在进行", "info")
            return

        summary = next((v for v in self.versions if v.id == version_id), None)
        is_profile = bool(summary and summary.is_loader_profile)

        dependents = dependent_versions(self.config.game_path, version_id)
        if dependents:
            NoticeDialog(
                self,
                "无法删除此版本",
                "以下已安装版本依赖它，直接删除会导致这些版本无法启动。"
                "请先删除依赖它的版本。",
                button_text="知道了",
                icon_name="warning",
                secondary_text="",
            ).exec()
            self.toast(
                "存在依赖版本：" + "、".join(dependents[:4]), "error"
            )
            return

        version_dir = version_json_path(
            self.config.game_path, version_id
        ).parent
        if not version_dir.exists():
            self.toast("版本目录不存在，已刷新列表", "info")
            self.refresh_versions()
            return

        if is_profile:
            bound_games = sorted(
                gid
                for gid, pid in self.config.version_loaders.items()
                if pid == version_id
            )
            note = f"目录：{version_dir}\n"
            if bound_games:
                note += (
                    "以下游戏版本会切回原版启动："
                    + "、".join(bound_games)
                    + "。存档、Mod、材质和光影不会被删除。"
                )
            else:
                note += "当前没有游戏版本使用这个加载器。"
            dialog = ConfirmDialog(
                self,
                "删除加载器配置",
                f"确定删除加载器 {version_id} 吗？配置文件会移入系统回收站。",
                primary_text="移入回收站",
                secondary_text="取消",
                icon_name="trash",
                icon_color=colors()["red"],
                details=note,
            )
        elif self.config.version_isolation:
            note = (
                f"目录：{version_dir}\n"
                "版本隔离已开启，此目录内的 mods、saves 和 config 也会一并移入回收站。"
            )
            dialog = ConfirmDialog(
                self,
                "删除游戏版本",
                f"确定删除版本 {version_id} 吗？文件会移入系统回收站，可在回收站恢复。",
                primary_text="移入回收站",
                secondary_text="取消",
                icon_name="trash",
                icon_color=colors()["red"],
                details=note,
            )
        else:
            note = (
                f"目录：{version_dir}\n"
                "版本隔离已关闭，只会删除版本文件，公共 mods 和存档不受影响。"
            )
            dialog = ConfirmDialog(
                self,
                "删除游戏版本",
                f"确定删除版本 {version_id} 吗？文件会移入系统回收站，可在回收站恢复。",
                primary_text="移入回收站",
                secondary_text="取消",
                icon_name="trash",
                icon_color=colors()["red"],
                details=note,
            )
        if not dialog.exec():
            return

        self.set_status(f"正在删除 {version_id}…", "busy")
        worker = DeleteWorker(version_dir, self)
        self._delete_worker = worker
        worker.finished.connect(
            lambda ok, message, vid=version_id: self._on_version_deleted(
                vid, ok, message
            )
        )
        worker.start()

    def _on_version_deleted(
        self, version_id: str, success: bool, message: str
    ) -> None:
        self._delete_worker = None
        if not success:
            self.set_status(f"删除失败：{message}", "error")
            self.toast(f"删除失败：{message}", "error")
            return
        changed = False
        if self.config.last_version == version_id:
            self.config.last_version = ""
            changed = True
        if self.config.version_loaders.pop(version_id, None) is not None:
            changed = True
        for game_id in list(self.config.version_loaders):
            if self.config.version_loaders[game_id] == version_id:
                del self.config.version_loaders[game_id]
                changed = True
        if changed:
            self.config.save()
        self.refresh_versions()
        self.set_status(f"已删除版本 {version_id}", "success")
        self.toast(f"{version_id} 已移入回收站", "success")

    # ------------------------------------------------------------------
    # 启动

    def launch_selected(
        self,
        checked: bool = False,
        auto_retry: bool = True,
        connect_address: str = "",
    ) -> None:
        if self.game_running:
            self.toast("游戏已经在运行了", "info")
            return
        if self.launch_active:
            self.toast("游戏正在启动，请稍候", "info")
            return
        if self.install_active:
            self.toast("文件仍在安装中，请稍候", "info")
            return
        if not self.selected_version:
            self.toast("请先安装一个游戏版本", "info")
            return

        self.launch_active = True
        self._launch_cancel_requested = False
        self._launch_closing = False
        self._launch_connect_address = connect_address
        self._launch_auto_retry = auto_retry
        profile_id, instance_id = self.launch_profile(self.selected_version)
        self._launch_profile_id = profile_id
        worker = LaunchWorker(
            self.config.game_path,
            profile_id,
            self.config,
            self.java_installs,
            self.current_account(),
            server_address=connect_address,
            parent=self,
            instance_id=instance_id,
        )
        self.launch_worker = worker
        worker.status.connect(self._on_launch_status)
        worker.ready.connect(self._on_launch_ready)
        worker.missing.connect(self._on_launch_missing)
        worker.failed.connect(self._on_launch_failed)
        worker.cancelled.connect(self._on_launch_cancelled)
        self.home_page.set_launching(True)
        self._update_action_button()
        self.set_status("正在准备启动…", "busy")
        self.log(
            f"准备启动 {self.selected_version}"
            f"{self._launch_mode_note(profile_id, instance_id)}"
            + (f" · 联机 {connect_address}" if connect_address else "")
        )
        self.loading_overlay.show_loading(self.selected_version)
        worker.start()

    def _on_launch_status(self, message: str) -> None:
        if self.launch_active:
            self.set_status(message, "busy")
            if hasattr(self, "loading_overlay"):
                self.loading_overlay.set_status(message)

    def _on_launch_ready(self, plan, account) -> None:
        if self.sender() is not self.launch_worker or not self.launch_active:
            return
        if self._launch_cancel_requested:
            self.launch_active = False
            self.launch_worker = None
            self._launch_cancel_requested = False
            self.home_page.set_launching(False)
            self._update_action_button()
            self.loading_overlay.abort()
            self.set_status("已取消启动", "idle")
            self.log("已取消启动")
            self.toast("已取消启动", "info")
            return
        self.launch_active = False
        self.launch_worker = None
        self.loading_overlay.set_status("正在注入猫猫能量…")
        if account.is_msa:
            self.config.account = account.to_config()
            self.config.save()
            self.account_changed.emit()

        for warning in plan.warnings:
            self.log(warning)

        self.config.last_version = self.selected_version
        self.config.save()
        self._running_version = self.selected_version
        self._running_profile_id = self._launch_profile_id
        self.game_running = True
        self.home_page.set_game_running(True)
        self.set_status("正在启动游戏…", "busy")
        self._update_action_button()
        self.log(
            f"启动 {self.selected_version}"
            f"{self._launch_mode_note(self._running_profile_id, self.selected_version)}"
            + f" · Java {plan.java.version or plan.java.major}"
            f" · 内存 {self.config.memory_mb} MB"
            + (f" · 联机 {self._launch_connect_address}" if self._launch_connect_address else "")
        )
        try:
            from ..config import append_log

            append_log("命令：" + redacted_command_line(plan.command))
        except Exception:
            pass

        self._open_game_log()

        process = GameProcess(plan, self)
        self.game_process = process
        process.started_ok.connect(self._game_started)
        process.output.connect(self._game_output)
        process.game_finished.connect(self._game_finished)
        process.start()

    def _on_launch_missing(self, missing) -> None:
        self.launch_active = False
        self.launch_worker = None
        self.home_page.set_launching(False)
        self._update_action_button()
        self.loading_overlay.abort()
        exc = MissingFilesError(list(missing or []))
        self._handle_missing_files(
            exc,
            self._launch_auto_retry,
            self._launch_connect_address,
        )

    def _on_launch_failed(self, message: str) -> None:
        self.launch_active = False
        self.launch_worker = None
        self.home_page.set_launching(False)
        self._update_action_button()
        self.loading_overlay.abort()
        self._handle_launch_error(message)

    def _on_launch_cancelled(self) -> None:
        if self.sender() is not self.launch_worker or not self.launch_active:
            return
        self.launch_active = False
        self.launch_worker = None
        self._launch_cancel_requested = False
        self.home_page.set_launching(False)
        self._update_action_button()
        self.loading_overlay.abort()
        self.set_status("已取消启动", "idle")
        self.log("已取消启动")
        self.toast("已取消启动", "info")

    def cancel_launch(self) -> None:
        if not self.launch_active or not self.launch_worker:
            return
        if self._launch_cancel_requested:
            return
        self._launch_cancel_requested = True
        self.launch_worker.cancel()
        self.set_status("正在取消启动…", "busy")
        self.log("正在取消启动…")
        self.action_cancel_btn.setText("正在取消…")
        self.action_cancel_btn.setEnabled(False)

    def close_game(self) -> None:
        if self.launch_active:
            self.cancel_launch()
            return
        if not self.game_running or not self.game_process:
            self.toast("游戏没有在运行", "info")
            return
        if self._launch_closing:
            return
        dialog = ConfirmDialog(
            self,
            "关闭游戏",
            "将请求游戏窗口正常关闭；如果游戏没有响应，会在等待后强制结束进程。",
            primary_text="关闭游戏",
            secondary_text="取消",
            icon_name="stop",
            icon_color=colors()["red"],
            details=f"当前版本：{self._running_version or self.selected_version}",
        )
        if not dialog.exec():
            return
        self._launch_closing = True
        self.home_page.set_game_closing()
        self.set_status("正在关闭游戏…", "busy")
        self._update_action_button()
        self.game_process.stop(graceful=True)

    def _handle_missing_files(
        self,
        exc: MissingFilesError,
        auto_retry: bool,
        connect_address: str = "",
    ) -> None:
        missing = exc.missing
        detail = "、".join(missing[:5])
        if len(missing) > 5:
            detail += f" 等 {len(missing)} 项"
        install_id = self._launch_profile_id or self.selected_version
        if self.config.auto_complete and auto_retry:
            self.toast("正在自动补全缺失文件，完成后启动", "info")
            self.begin_install(
                install_id,
                success_callback=lambda: self.launch_selected(
                    auto_retry=False, connect_address=connect_address
                ),
            )
            self.toggle_log_if_closed()
            return

        dialog = ConfirmDialog(
            self,
            "游戏文件不完整",
            "启动前需要补全以下文件，补全完成后可以继续启动。",
            primary_text="自动补全",
            secondary_text="取消",
            icon_name="download",
            icon_color=colors()["accent"],
            details=detail,
        )
        if dialog.exec():
            self.begin_install(
                self._launch_profile_id or self.selected_version,
                success_callback=lambda: self.launch_selected(
                    auto_retry=False, connect_address=connect_address
                ),
            )
            self.toggle_log_if_closed()

    def toggle_log_if_closed(self) -> None:
        if not (self.log_panel.isVisible() and self.log_panel.maximumHeight() > 0):
            self.log_panel.open_panel()
            self.log_btn.setText("收起日志")

    def _handle_launch_error(self, message: str) -> None:
        self.set_status(message, "error")
        self.toast(message, "error")
        if "Java" in message:
            dialog = NoticeDialog(
                self,
                "无法启动游戏",
                message,
                button_text="打开设置",
                icon_name="warning",
                secondary_text="取消",
            )
            if dialog.exec() and dialog.action_clicked:
                self.switch_page("settings")
        else:
            NoticeDialog(
                self, "无法启动游戏", message, button_text="知道了", icon_name="warning"
            ).exec()

    def _open_game_log(self) -> None:
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            path = LOG_DIR / f"game-{stamp}.log"
            self._game_log_file = path.open("w", encoding="utf-8")
            self._current_game_log = str(path)
        except Exception:
            self._game_log_file = None

    def _game_started(self, pid: int) -> None:
        self.set_status(f"游戏运行中 · PID {pid}", "success")
        self.toast("游戏已启动", "success")
        self.game_state_changed.emit(True)
        self.home_page.set_game_running(True)
        self._update_action_button()
        self.loading_overlay.finish("世界已加载，去玩吧喵。", 700)
        self.log(f"游戏进程已启动（PID {pid}）")

    def _game_output(self, line: str) -> None:
        self.log_panel.append(line)
        if self._game_log_file:
            try:
                self._game_log_file.write(line + "\n")
                self._game_log_file.flush()
            except Exception:
                pass

    def _game_finished(self, code: int) -> None:
        was_closing = self._launch_closing
        self.game_running = False
        self._launch_closing = False
        self._running_version = ""
        self.game_process = None
        self.home_page.set_game_running(False)
        self.game_state_changed.emit(False)
        self._update_action_button()
        if was_closing:
            self.set_status("游戏已关闭", "success")
            self.log("游戏已按请求关闭")
            self.toast("游戏已关闭", "info")
        else:
            self.set_status(
                f"游戏已退出（代码 {code}）" if code else "游戏已正常退出",
                "success" if code == 0 else "error",
            )
            self.log(f"游戏进程结束，退出代码 {code}")
            if code == 0:
                self.toast("游戏已退出", "info")
            else:
                self.toast(f"游戏异常退出（代码 {code}），请查看日志", "error")
                self.toggle_log_if_closed()
        if self._game_log_file:
            try:
                self._game_log_file.close()
            except Exception:
                pass
            self._game_log_file = None
