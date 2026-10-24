from __future__ import annotations

import ctypes
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ...accounts import valid_player_name
from ...developer import (
    feedback_table_url,
    lock_developer,
    unlock_developer,
    verify_developer_key,
)
from ...config import (
    APP_AUTHOR,
    APP_DIR,
    APP_EMAIL,
    APP_NAME,
    APP_VERSION,
    DEFAULT_UPDATE_URL,
)
from ...java import JavaInfo, probe_java
from ...microsoft_auth import MicrosoftAuthError, normalize_client_id
from ...snapshots import list_snapshots, snapshot_dir
from ...workers import (
    CleanupWorker,
    DiagnosticsWorker,
    SnapshotCreateWorker,
    SnapshotRestoreWorker,
)
from .. import icons
from ..account_dialog import BrowserLoginDialog, MicrosoftLoginDialog
from ..dialogs import ConfirmDialog
from ..snapshot_dialog import SnapshotDialog
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
)


def _total_ram_mb() -> int:
    try:
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return int(stat.ullTotalPhys / 1024 / 1024)
    except Exception:
        pass
    return 16384


class SettingsPage(QWidget):
    def __init__(self, main, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.main = main
        self._cleanup_worker: CleanupWorker | None = None
        self._snapshot_create_worker: SnapshotCreateWorker | None = None
        self._snapshot_restore_worker: SnapshotRestoreWorker | None = None
        self._diagnostics_worker: DiagnosticsWorker | None = None
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self.main.config.save)
        self._build()
        self.main.java_installs_changed.connect(self._populate_java)
        self.main.versions_changed.connect(self._update_java_requirement)
        self._populate_java(self.main.java_installs)

    # ------------------------------------------------------------------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        container = QWidget()
        scroll.setWidget(container)
        outer.addWidget(scroll)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(22, 18, 22, 22)
        layout.setSpacing(14)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = QLabel("猫猫的窝")
        title.setObjectName("PageTitle")
        sub = QLabel("Java、内存和下载偏好都收在这里，改完自动保存，不用按保存键。")
        sub.setObjectName("Muted")
        titles.addWidget(title)
        titles.addWidget(sub)
        header.addLayout(titles, 1)
        header.addWidget(ChibiCompanion(60, "settings"), 0, Qt.AlignTop)
        layout.addLayout(header)

        layout.addWidget(self._build_account_card())
        layout.addWidget(self._build_game_card())
        layout.addWidget(self._build_launcher_card())
        layout.addWidget(self._build_snapshot_card())
        layout.addWidget(self._build_diagnostics_card())
        layout.addWidget(self._build_about_card())
        layout.addWidget(self._build_developer_card())
        layout.addStretch(1)
        self.refresh_account()
        self.refresh_shortcut_state()
        self.refresh_update_state()

    def _card_header(self, title: str, subtitle: str = "") -> QVBoxLayout:
        layout = QVBoxLayout()
        layout.setSpacing(2)
        title_label = QLabel(title)
        title_label.setObjectName("H2")
        layout.addWidget(title_label)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("Tiny")
            sub.setWordWrap(True)
            layout.addWidget(sub)
        return layout

    @staticmethod
    def _row(title: str, subtitle: str, control: QWidget) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 10, 0, 10)
        layout.setSpacing(18)

        text = QVBoxLayout()
        text.setSpacing(2)
        name = QLabel(title)
        name.setStyleSheet("font-weight: 600;")
        text.addWidget(name)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("Tiny")
            sub.setWordWrap(True)
            text.addWidget(sub)
        layout.addLayout(text, 1)
        layout.addWidget(control, 0, Qt.AlignRight | Qt.AlignVCenter)
        return row

    def _build_account_card(self) -> QWidget:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 12)
        layout.setSpacing(0)

        header = QHBoxLayout()
        header.addLayout(
            self._card_header(
                "正版账户",
                "使用 Microsoft 账户登录，可进入需要正版验证的服务器。",
            ),
            1,
        )
        self.account_badge = badge("离线模式", "gray")
        header.addWidget(self.account_badge, 0, Qt.AlignTop)
        layout.addLayout(header)
        layout.addSpacing(8)

        info = QWidget()
        info_layout = QHBoxLayout(info)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(10)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        self.account_name = QLabel("离线账户")
        self.account_name.setStyleSheet("font-size: 14px; font-weight: 700;")
        self.account_uuid = QLabel("")
        self.account_uuid.setObjectName("Tiny")
        self.account_uuid.setWordWrap(True)
        texts.addWidget(self.account_name)
        texts.addWidget(self.account_uuid)
        info_layout.addLayout(texts, 1)
        layout.addWidget(info)
        layout.addWidget(hline())

        self.email_edit = QLineEdit(self.main.config.microsoft_email)
        self.email_edit.setMinimumWidth(300)
        self.email_edit.setPlaceholderText("name@example.com（可选）")
        self.email_edit.editingFinished.connect(self._save_email)
        layout.addWidget(
            self._row(
                "Microsoft 邮箱",
                "填写后会自动预填到 Microsoft 官方登录页，密码只在浏览器中输入。",
                self.email_edit,
            )
        )
        layout.addWidget(hline())

        client_box = QWidget()
        client_layout = QHBoxLayout(client_box)
        client_layout.setContentsMargins(0, 0, 0, 0)
        client_layout.setSpacing(6)
        self.client_edit = QLineEdit(self.main.config.microsoft_client_id)
        self.client_edit.setMinimumWidth(300)
        self.client_edit.setPlaceholderText(
            "Azure 应用的客户端 ID（GUID）"
        )
        self.client_edit.editingFinished.connect(self._save_client_id)
        portal_btn = QPushButton("Azure 门户")
        portal_btn.setObjectName("Ghost")
        portal_btn.setCursor(Qt.PointingHandCursor)
        portal_btn.clicked.connect(self._open_azure_portal)
        client_layout.addWidget(self.client_edit)
        client_layout.addWidget(portal_btn)
        layout.addWidget(
            self._row(
                "Azure 客户端 ID",
                "正版登录和令牌刷新需要你自己的 Azure 公共客户端应用。",
                client_box,
            )
        )
        layout.addWidget(hline())

        buttons = QWidget()
        button_layout = QHBoxLayout(buttons)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(8)
        self.login_btn = QPushButton("邮箱登录")
        self.login_btn.setObjectName("Primary")
        self.login_btn.setCursor(Qt.PointingHandCursor)
        self.login_btn.setIcon(icons.icon("user", "#FFFFFF", 15))
        self.login_btn.setToolTip(
            "打开系统浏览器，用 Microsoft 邮箱登录，授权完成后自动返回"
        )
        self.login_btn.clicked.connect(self._login_microsoft)
        self.verify_btn = QPushButton("验证码登录")
        self.verify_btn.setObjectName("Ghost")
        self.verify_btn.setCursor(Qt.PointingHandCursor)
        self.verify_btn.setToolTip("浏览器无法跳回启动器时，可改用设备验证码登录")
        self.verify_btn.clicked.connect(self._login_device_code)
        self.logout_btn = QPushButton("退出登录")
        self.logout_btn.setObjectName("Ghost")
        self.logout_btn.setCursor(Qt.PointingHandCursor)
        self.logout_btn.clicked.connect(self._logout_microsoft)
        skin_btn = QPushButton("管理皮肤")
        skin_btn.setObjectName("Ghost")
        skin_btn.setCursor(Qt.PointingHandCursor)
        skin_btn.setIcon(icons.icon("shirt", colors()["text2"], 15))
        skin_btn.clicked.connect(lambda: self.main.switch_page("skin"))
        button_layout.addWidget(self.login_btn)
        button_layout.addWidget(self.verify_btn)
        button_layout.addWidget(self.logout_btn)
        button_layout.addWidget(skin_btn)
        layout.addWidget(
            self._row(
                "账户操作",
                "邮箱登录会在系统浏览器中完成，授权后自动返回；离线名称仅在未登录时生效。",
                buttons,
            )
        )

        help_label = QLabel(
            "首次使用：在 Azure 门户免费注册一个应用，添加平台“移动和桌面应用程序”，"
            "重定向 URI 填写 http://localhost，并启用“允许公共客户端流”，"
            "然后把“应用程序(客户端) ID”填到上方。客户端 ID 和邮箱只保存在本机，"
            "密码不会经过启动器，登录令牌使用 Windows DPAPI 加密。"
        )
        help_label.setObjectName("Tiny")
        help_label.setWordWrap(True)
        help_label.setContentsMargins(0, 4, 0, 8)
        layout.addWidget(help_label)
        return card

    def _build_game_card(self) -> QWidget:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 10)
        layout.setSpacing(0)
        layout.addLayout(
            self._card_header("游戏设置", "这些设置会应用到下一次启动。")
        )
        layout.addSpacing(4)

        game_dir_box = QWidget()
        game_dir_layout = QHBoxLayout(game_dir_box)
        game_dir_layout.setContentsMargins(0, 0, 0, 0)
        game_dir_layout.setSpacing(6)
        self.game_dir = QLineEdit(str(self.main.config.game_path))
        self.game_dir.setReadOnly(True)
        self.game_dir.setMinimumWidth(320)
        browse_dir = icon_button("folder", "选择游戏目录")
        browse_dir.clicked.connect(self._browse_game_dir)
        open_dir = icon_button("chevron-right", "打开游戏目录")
        open_dir.clicked.connect(self.main.open_game_dir)
        game_dir_layout.addWidget(self.game_dir)
        game_dir_layout.addWidget(browse_dir)
        game_dir_layout.addWidget(open_dir)
        layout.addWidget(
            self._row(
                "游戏目录",
                "存放 versions、libraries 和 assets；默认在启动器目录下的 minecraft 文件夹。",
                game_dir_box,
            )
        )
        layout.addWidget(hline())

        isolation_box = QWidget()
        isolation_layout = QHBoxLayout(isolation_box)
        isolation_layout.setContentsMargins(0, 0, 0, 0)
        self.isolation_switch = Switch()
        self.isolation_switch.setChecked(bool(self.main.config.version_isolation))
        self.isolation_switch.toggled.connect(self._save_isolation)
        isolation_layout.addWidget(self.isolation_switch)
        layout.addWidget(
            self._row(
                "版本隔离",
                "每个版本以 versions/<版本名> 为独立游戏目录，Mod、存档和配置互不影响。"
                "切换后已有的公共 mods 不会被自动移动。",
                isolation_box,
            )
        )
        layout.addWidget(hline())

        self.name_edit = QLineEdit(self.main.config.player_name)
        self.name_edit.setFixedWidth(220)
        self.name_edit.setMaxLength(16)
        self.name_edit.setPlaceholderText("3-16 位字母、数字或下划线")
        self.name_edit.editingFinished.connect(self._save_name)
        layout.addWidget(
            self._row(
                "玩家名称",
                "离线模式使用的游戏内名称；登录正版后以正版账户为准。",
                self.name_edit,
            )
        )
        layout.addWidget(hline())

        memory_box = QWidget()
        memory_layout = QHBoxLayout(memory_box)
        memory_layout.setContentsMargins(0, 0, 0, 0)
        memory_layout.setSpacing(12)
        self.memory_slider = QSlider(Qt.Horizontal)
        self.memory_slider.setFixedWidth(250)
        self.memory_slider.setSingleStep(512)
        self.memory_slider.setPageStep(1024)
        max_mem = max(4096, min(32768, _total_ram_mb() - 2048))
        self.memory_slider.setRange(1024, max_mem)
        value = max(1024, min(max_mem, int(self.main.config.memory_mb)))
        self.memory_slider.setValue(value)
        self.memory_slider.valueChanged.connect(self._on_memory)
        self.memory_value = QLabel("")
        self.memory_value.setFixedWidth(132)
        self.memory_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.memory_value.setStyleSheet("font-weight: 700;")
        memory_layout.addWidget(self.memory_slider)
        memory_layout.addWidget(self.memory_value)
        self._on_memory(value)
        layout.addWidget(
            self._row(
                "游戏内存",
                "分配过多内存反而可能增加回收停顿，推荐 4-8 GB。",
                memory_box,
            )
        )
        layout.addWidget(hline())

        java_box = QWidget()
        java_layout = QHBoxLayout(java_box)
        java_layout.setContentsMargins(0, 0, 0, 0)
        java_layout.setSpacing(6)
        self.java_combo = QComboBox()
        self.java_combo.setMinimumWidth(360)
        self.java_combo.currentIndexChanged.connect(self._on_java_changed)
        browse_java = icon_button("folder", "手动选择 java.exe")
        browse_java.clicked.connect(self._browse_java)
        refresh_java = icon_button("refresh", "重新检测 Java")
        refresh_java.clicked.connect(self._rescan_java)
        java_layout.addWidget(self.java_combo)
        java_layout.addWidget(browse_java)
        java_layout.addWidget(refresh_java)
        self.java_row = self._row(
            "Java 运行环境",
            "自动选择会匹配版本所需的最低 Java 版本。",
            java_box,
        )
        layout.addWidget(self.java_row)
        self.java_hint = QLabel("尚未检测 Java。")
        self.java_hint.setObjectName("Tiny")
        self.java_hint.setWordWrap(True)
        self.java_hint.setContentsMargins(0, 0, 0, 8)
        layout.addWidget(self.java_hint)

        layout.addWidget(hline())
        self.jvm_edit = QLineEdit(self.main.config.jvm_args)
        self.jvm_edit.setFixedWidth(360)
        self.jvm_edit.setPlaceholderText("留空即可，例如 -XX:+UseG1GC")
        self.jvm_edit.editingFinished.connect(self._save_jvm_args)
        layout.addWidget(
            self._row("额外 JVM 参数", "启动游戏时附加到 Java 命令行的参数。", self.jvm_edit)
        )
        return card

    def _build_launcher_card(self) -> QWidget:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 10)
        layout.setSpacing(0)
        layout.addLayout(self._card_header("启动器设置"))

        self.source_combo = QComboBox()
        self.source_combo.setFixedWidth(280)
        self.source_combo.addItem("智能加速（自动测速，失败熔断）", "auto")
        self.source_combo.addItem("Mojang 官方源", "official")
        self.source_combo.addItem("BMCLAPI 镜像（国内加速）", "bmclapi")
        index = self.source_combo.findData(self.main.config.download_source)
        self.source_combo.setCurrentIndex(max(0, index))
        self.source_combo.currentIndexChanged.connect(self._save_source)
        layout.addWidget(
            self._row(
                "下载源",
                "智能加速会自动测速选择更快的线路；连续失败会熔断 5 分钟并切换另一条，恢复后自动重新启用。",
                self.source_combo,
            )
        )
        layout.addWidget(hline())

        self.thread_combo = QComboBox()
        self.thread_combo.setFixedWidth(150)
        thread_values = [4, 8, 12, 16]
        current_threads = int(self.main.config.download_threads)
        if current_threads not in thread_values:
            thread_values.append(current_threads)
            thread_values.sort()
        for value in thread_values:
            self.thread_combo.addItem(f"{value} 线程", value)
        thread_index = self.thread_combo.findData(current_threads)
        self.thread_combo.setCurrentIndex(max(0, thread_index))
        self.thread_combo.currentIndexChanged.connect(self._save_threads)
        layout.addWidget(
            self._row(
                "下载线程",
                "多线程分段下载可明显加快 1.21.1 等大版本和 Mod 的下载速度。",
                self.thread_combo,
            )
        )
        layout.addWidget(hline())

        self.cleanup_btn = QPushButton("清理下载残留")
        self.cleanup_btn.setObjectName("Secondary")
        self.cleanup_btn.setCursor(Qt.PointingHandCursor)
        self.cleanup_btn.setIcon(icons.icon("trash", colors()["text2"], 14))
        self.cleanup_btn.clicked.connect(self._cleanup_partials)
        layout.addWidget(
            self._row(
                "下载残留",
                "安装或 Mod 下载被取消后，可能会留下 .part 等临时分片文件。",
                self.cleanup_btn,
            )
        )
        layout.addWidget(hline())

        update_box = QWidget()
        update_layout = QHBoxLayout(update_box)
        update_layout.setContentsMargins(0, 0, 0, 0)
        update_layout.setSpacing(8)
        self.update_status = QLabel(self.main.update_state_text)
        self.update_status.setObjectName("Tiny")
        self.update_status.setMinimumWidth(150)
        self.update_status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.update_btn = QPushButton("检查更新")
        self.update_btn.setObjectName("Secondary")
        self.update_btn.setCursor(Qt.PointingHandCursor)
        self.update_btn.setIcon(icons.icon("refresh", colors()["text2"], 14))
        self.update_btn.clicked.connect(self._check_updates)
        self.site_btn = QPushButton("下载站")
        self.site_btn.setObjectName("Ghost")
        self.site_btn.setCursor(Qt.PointingHandCursor)
        self.site_btn.setIcon(icons.icon("download", colors()["text2"], 14))
        self.site_btn.clicked.connect(self._open_update_site)
        update_layout.addWidget(self.update_status)
        update_layout.addWidget(self.update_btn)
        update_layout.addWidget(self.site_btn)
        layout.addWidget(
            self._row(
                "启动器更新",
                f"当前 v{APP_VERSION}。更新只替换启动器代码，游戏版本、Mod、"
                "账户、好友和设置都会原样保留。",
                update_box,
            )
        )
        layout.addWidget(hline())

        auto_update_box = QWidget()
        auto_update_layout = QHBoxLayout(auto_update_box)
        auto_update_layout.setContentsMargins(0, 0, 0, 0)
        self.update_auto_switch = Switch()
        self.update_auto_switch.setChecked(
            bool(self.main.config.update_auto_check)
        )
        self.update_auto_switch.toggled.connect(self._save_update_auto)
        auto_update_layout.addWidget(self.update_auto_switch)
        layout.addWidget(
            self._row(
                "启动时检查更新",
                "打开启动器后自动读取一次更新清单，有新版时再询问你。",
                auto_update_box,
            )
        )
        layout.addWidget(hline())

        update_url_box = QWidget()
        update_url_layout = QHBoxLayout(update_url_box)
        update_url_layout.setContentsMargins(0, 0, 0, 0)
        update_url_layout.setSpacing(6)
        self.update_url_edit = QLineEdit(DEFAULT_UPDATE_URL)
        self.update_url_edit.setMinimumWidth(330)
        self.update_url_edit.setReadOnly(True)
        self.update_url_edit.setToolTip("更新源固定为 Darkitten Launcher 官方 GitHub Pages 地址")
        update_url_layout.addWidget(self.update_url_edit)
        layout.addWidget(
            self._row(
                "更新源",
                "固定使用 GitHub Pages 官方更新清单，不再请求旧的本机或临时托管地址。",
                update_url_box,
            )
        )
        layout.addWidget(hline())

        feedback_box = QWidget()
        feedback_layout = QHBoxLayout(feedback_box)
        feedback_layout.setContentsMargins(0, 0, 0, 0)
        feedback_layout.setSpacing(8)
        bug_btn = QPushButton("Bug 反馈")
        bug_btn.setObjectName("Secondary")
        bug_btn.setCursor(Qt.PointingHandCursor)
        bug_btn.setIcon(icons.icon("message", colors()["text2"], 14))
        bug_btn.clicked.connect(lambda: self.main.open_feedback_dialog("bug"))
        idea_btn = QPushButton("改进建议")
        idea_btn.setObjectName("Ghost")
        idea_btn.setCursor(Qt.PointingHandCursor)
        idea_btn.setIcon(icons.icon("message", colors()["text2"], 14))
        idea_btn.clicked.connect(
            lambda: self.main.open_feedback_dialog("suggestion")
        )
        feedback_layout.addWidget(bug_btn)
        feedback_layout.addWidget(idea_btn)
        layout.addWidget(
            self._row(
                "猫猫意见箱",
                "反馈会进入启动器收件箱和网站反馈中心；反馈中心离线时可改用 GitHub 或邮件。",
                feedback_box,
            )
        )
        layout.addWidget(hline())

        theme_box = QWidget()
        theme_layout = QHBoxLayout(theme_box)
        theme_layout.setContentsMargins(0, 0, 0, 0)
        self.theme_seg = SegmentedControl()
        self.theme_seg.add_item("浅色", "light")
        self.theme_seg.add_item("深色", "dark")
        self.theme_seg.set_value(self.main.config.theme)
        self.theme_seg.changed.connect(self._save_theme)
        theme_layout.addWidget(self.theme_seg)
        layout.addWidget(
            self._row("界面主题", "切换启动器的浅色或深色外观。", theme_box)
        )
        layout.addWidget(hline())

        self.shortcut_btn = QPushButton("创建桌面快捷方式")
        self.shortcut_btn.setObjectName("Secondary")
        self.shortcut_btn.setCursor(Qt.PointingHandCursor)
        self.shortcut_btn.setIcon(icons.icon("desktop", colors()["text2"], 14))
        self.shortcut_btn.clicked.connect(self._create_shortcut)
        layout.addWidget(
            self._row(
                "桌面快捷方式",
                "在桌面放一个带 DKL 图标的启动入口；第一次启动时也会询问一次。",
                self.shortcut_btn,
            )
        )
        layout.addWidget(hline())

        auto_box = QWidget()
        auto_layout = QHBoxLayout(auto_box)
        auto_layout.setContentsMargins(0, 0, 0, 0)
        self.auto_switch = Switch()
        self.auto_switch.setChecked(bool(self.main.config.auto_complete))
        self.auto_switch.toggled.connect(self._save_auto)
        auto_layout.addWidget(self.auto_switch)
        layout.addWidget(
            self._row(
                "启动前自动补全",
                "文件缺失时自动下载，完成后继续启动，不再弹窗询问。",
                auto_box,
            )
        )
        return card

    def _build_snapshot_card(self) -> QWidget:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 14)
        layout.setSpacing(10)
        layout.addLayout(
            self._card_header(
                "世界与配置快照",
                "把当前版本的世界、Mod 配置和选项打包备份，改崩了可以一键回到之前的状态。",
            )
        )

        buttons = QWidget()
        row = QHBoxLayout(buttons)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.snapshot_create_btn = QPushButton("创建快照")
        self.snapshot_create_btn.setObjectName("Primary")
        self.snapshot_create_btn.setCursor(Qt.PointingHandCursor)
        self.snapshot_create_btn.clicked.connect(self._create_snapshot)
        self.snapshot_manage_btn = QPushButton("查看 / 恢复")
        self.snapshot_manage_btn.setObjectName("Secondary")
        self.snapshot_manage_btn.setCursor(Qt.PointingHandCursor)
        self.snapshot_manage_btn.clicked.connect(self._open_snapshot_dialog)
        self.snapshot_open_btn = QPushButton("打开备份目录")
        self.snapshot_open_btn.setObjectName("Ghost")
        self.snapshot_open_btn.setCursor(Qt.PointingHandCursor)
        self.snapshot_open_btn.clicked.connect(self._open_snapshot_folder)
        row.addWidget(self.snapshot_create_btn)
        row.addWidget(self.snapshot_manage_btn)
        row.addWidget(self.snapshot_open_btn)
        row.addStretch(1)
        layout.addWidget(buttons)

        self.snapshot_hint = QLabel("")
        self.snapshot_hint.setObjectName("Tiny")
        self.snapshot_hint.setWordWrap(True)
        layout.addWidget(self.snapshot_hint)
        self._refresh_snapshot_hint()
        return card

    def _snapshot_target(self, notify: bool = False) -> tuple[str, Path] | None:
        version = str(
            getattr(self.main, "selected_version", "")
            or self.main.config.last_version
            or ""
        )
        if not version:
            if notify:
                self.main.toast("请先选择一个游戏版本", "info")
            return None
        game_path = Path(self.main.config.version_game_path(version))
        if not game_path.is_dir():
            if notify:
                self.main.toast(f"版本目录不存在：{version}", "error")
            return None
        return version, game_path

    def _refresh_snapshot_hint(self) -> None:
        if not hasattr(self, "snapshot_hint"):
            return
        target = self._snapshot_target()
        if target is None:
            self.snapshot_hint.setText("选择游戏版本后即可创建快照。")
            return
        version, game_path = target
        snapshots = list_snapshots(game_path)
        if not snapshots:
            self.snapshot_hint.setText(
                f"{version} · 还没有快照。安装新 Mod 或改配置前建议先备份一次。"
            )
            return
        latest = snapshots[0]
        self.snapshot_hint.setText(
            f"{version} · 共 {len(snapshots)} 个快照 · 最近 {latest.created_text}"
            f" · {latest.size_text}"
        )

    def _create_snapshot(self) -> None:
        if self._snapshot_create_worker is not None:
            self.main.toast("快照正在创建，请稍候", "info")
            return
        target = self._snapshot_target(notify=True)
        if target is None:
            return
        version, game_path = target
        worker = SnapshotCreateWorker(game_path, version, parent=self)
        self._snapshot_create_worker = worker
        worker.finished.connect(self._on_snapshot_created)
        self.snapshot_create_btn.setEnabled(False)
        self.snapshot_create_btn.setText("正在打包…")
        self.main.set_status("正在创建世界与配置快照…", "busy")
        self.main.log(f"开始创建 {version} 的世界与配置快照")
        worker.start()

    def _on_snapshot_created(self, success: bool, message: str, info) -> None:
        self._snapshot_create_worker = None
        self.snapshot_create_btn.setEnabled(True)
        self.snapshot_create_btn.setText("创建快照")
        if not success:
            self.main.set_status(f"快照创建失败：{message}", "error")
            self.main.toast(f"快照创建失败：{message}", "error")
            return
        self.main.set_status("世界与配置快照已创建", "success")
        self.main.toast(f"快照已保存 · {info.size_text}", "success")
        self.main.log(
            f"快照已创建：{info.path.name}（{info.files} 个文件，"
            f"{info.size_text}）"
        )
        self._refresh_snapshot_hint()

    def _open_snapshot_dialog(self) -> None:
        target = self._snapshot_target(notify=True)
        if target is None:
            return
        version, game_path = target
        dialog = SnapshotDialog(self, game_path, version)
        dialog.restore_requested.connect(
            lambda path: self._restore_snapshot(Path(path), game_path, version)
        )
        dialog.exec()

    def _restore_snapshot(
        self, archive_path: Path, game_path: Path, version: str
    ) -> None:
        if self._snapshot_restore_worker is not None:
            self.main.toast("正在恢复快照，请稍候", "info")
            return
        worker = SnapshotRestoreWorker(archive_path, game_path, parent=self)
        self._snapshot_restore_worker = worker
        worker.finished.connect(self._on_snapshot_restored)
        self.snapshot_create_btn.setEnabled(False)
        self.snapshot_manage_btn.setEnabled(False)
        self.main.set_status("正在恢复世界与配置…", "busy")
        self.main.log(f"开始恢复 {version} 的快照：{archive_path.name}")
        worker.start()

    def _on_snapshot_restored(self, success: bool, message: str, count: int) -> None:
        self._snapshot_restore_worker = None
        self.snapshot_create_btn.setEnabled(True)
        self.snapshot_manage_btn.setEnabled(True)
        if not success:
            self.main.set_status(f"快照恢复失败：{message}", "error")
            self.main.toast(f"快照恢复失败：{message}", "error")
            return
        self.main.set_status(f"已恢复 {count} 个文件", "success")
        self.main.toast(f"快照已恢复 · {count} 个文件", "success")
        self.main.log(f"快照恢复完成，共 {count} 个文件")
        self._refresh_snapshot_hint()

    def _open_snapshot_folder(self) -> None:
        target = self._snapshot_target(notify=True)
        if target is None:
            return
        _version, game_path = target
        folder = snapshot_dir(game_path)
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _build_diagnostics_card(self) -> QWidget:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 14)
        layout.setSpacing(10)
        layout.addLayout(
            self._card_header(
                "诊断与安全",
                "遇到启动失败、Mod 冲突或游戏闪退时，把诊断包发给开发者；"
                "安全模式会临时禁用当前版本的 Mod，退出游戏后自动恢复。",
            )
        )

        buttons = QWidget()
        row = QHBoxLayout(buttons)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.diagnostics_copy_btn = QPushButton("复制诊断信息")
        self.diagnostics_copy_btn.setObjectName("Secondary")
        self.diagnostics_copy_btn.setCursor(Qt.PointingHandCursor)
        self.diagnostics_copy_btn.clicked.connect(self._copy_diagnostics)
        self.diagnostics_bundle_btn = QPushButton("生成诊断包")
        self.diagnostics_bundle_btn.setObjectName("Primary")
        self.diagnostics_bundle_btn.setCursor(Qt.PointingHandCursor)
        self.diagnostics_bundle_btn.clicked.connect(self._create_diagnostics)
        self.safe_mode_btn = QPushButton("安全模式启动")
        self.safe_mode_btn.setObjectName("Ghost")
        self.safe_mode_btn.setCursor(Qt.PointingHandCursor)
        self.safe_mode_btn.clicked.connect(self._confirm_safe_mode)
        self.diagnostics_open_btn = QPushButton("打开诊断目录")
        self.diagnostics_open_btn.setObjectName("Ghost")
        self.diagnostics_open_btn.setCursor(Qt.PointingHandCursor)
        self.diagnostics_open_btn.clicked.connect(self._open_diagnostics_folder)
        row.addWidget(self.diagnostics_bundle_btn)
        row.addWidget(self.diagnostics_copy_btn)
        row.addWidget(self.safe_mode_btn)
        row.addWidget(self.diagnostics_open_btn)
        row.addStretch(1)
        layout.addWidget(buttons)

        self.diagnostics_hint = QLabel(
            "诊断包已隐藏访问令牌、刷新令牌和樱花 Token；"
            "不包含账户密码、世界存档、皮肤文件与 Mod 本体。"
        )
        self.diagnostics_hint.setObjectName("Tiny")
        self.diagnostics_hint.setWordWrap(True)
        layout.addWidget(self.diagnostics_hint)
        return card

    def _diagnostics_context(self) -> tuple[str, str]:
        version = str(
            getattr(self.main, "selected_version", "")
            or self.main.config.last_version
            or ""
        )
        loader = self.main.config.loader_for(version) if version else ""
        return version, loader

    def _start_diagnostics(self, bundle: bool) -> None:
        if self._diagnostics_worker is not None:
            self.main.toast("诊断任务正在进行，请稍候", "info")
            return
        version, loader = self._diagnostics_context()
        worker = DiagnosticsWorker(
            self.main.config,
            self.main.java_installs,
            version,
            loader,
            bundle=bundle,
            parent=self,
        )
        self._diagnostics_worker = worker
        worker.finished.connect(self._on_diagnostics_finished)
        self.diagnostics_copy_btn.setEnabled(False)
        self.diagnostics_bundle_btn.setEnabled(False)
        self.diagnostics_bundle_btn.setText("正在收集…" if bundle else "生成诊断包")
        self.main.set_status("正在收集诊断信息…", "busy")
        worker.start()

    def _copy_diagnostics(self) -> None:
        self._start_diagnostics(bundle=False)

    def _create_diagnostics(self) -> None:
        self._start_diagnostics(bundle=True)

    def _on_diagnostics_finished(
        self, success: bool, message: str, payload
    ) -> None:
        self._diagnostics_worker = None
        self.diagnostics_copy_btn.setEnabled(True)
        self.diagnostics_bundle_btn.setEnabled(True)
        self.diagnostics_bundle_btn.setText("生成诊断包")
        if not success or not isinstance(payload, dict):
            self.main.set_status(f"诊断信息生成失败：{message}", "error")
            self.main.toast(f"诊断信息生成失败：{message}", "error")
            return
        summary = str(payload.get("summary") or "")
        path = payload.get("path")
        if path is None:
            QApplication.clipboard().setText(summary)
            self.main.set_status("诊断信息已复制", "success")
            self.main.toast("诊断信息已复制到剪贴板", "success")
            self.main.log("诊断信息已复制到剪贴板")
            return
        self.main.set_status("诊断包已生成", "success")
        self.main.log(f"诊断包已生成：{path}")
        self.main.toast("诊断包已生成", "success")
        self.diagnostics_hint.setText(
            f"最近诊断包：{path}\n"
            "诊断包已隐藏访问令牌、刷新令牌和樱花 Token；"
            "不包含账户密码、世界存档、皮肤文件与 Mod 本体。"
        )

    def _open_diagnostics_folder(self) -> None:
        folder = APP_DIR / "diagnostics"
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _confirm_safe_mode(self) -> None:
        if self.main.game_running or self.main.launch_active:
            self.main.toast("游戏正在运行或启动中", "info")
            return
        target = self._snapshot_target(notify=True)
        if target is None:
            return
        version, _game_path = target
        dialog = ConfirmDialog(
            self,
            "安全模式启动",
            "将临时禁用当前版本的全部 Mod 后启动游戏，"
            "用来判断闪退是否由 Mod 引起。游戏退出或启动失败后会自动恢复。",
            primary_text="安全模式启动",
            secondary_text="取消",
            icon_name="warning",
            icon_color=colors()["orange"],
            details=f"版本：{version}\nMod 目录：{self.main.config.mods_path(version)}",
        )
        if dialog.exec():
            self.main.launch_safe_mode()

    def _build_about_card(self) -> QWidget:
        card = Card()
        layout = QHBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(14)

        logo = QLabel()
        logo.setPixmap(icons.chibi_face_pixmap(56))
        logo.setFixedSize(56, 56)
        logo.setStyleSheet("border-radius: 14px;")
        logo.setScaledContents(True)
        layout.addWidget(logo)

        texts = QVBoxLayout()
        texts.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        name = QLabel(APP_NAME)
        name.setStyleSheet("font-size: 15px; font-weight: 700;")
        version = badge(f"v{APP_VERSION}", "blue")
        title_row.addWidget(name)
        title_row.addWidget(version)
        title_row.addStretch(1)
        texts.addLayout(title_row)
        desc = QLabel("恶魔小猫的秘密基地 · 叼回版本、藏好模组、顺手打包")
        desc.setObjectName("Tiny")
        texts.addWidget(desc)
        contact = QLabel(
            f"开发者：{APP_AUTHOR} · 邮箱：{APP_EMAIL} · 非 Mojang 或 Microsoft 官方产品。"
        )
        contact.setObjectName("Tiny")
        contact.setTextInteractionFlags(Qt.TextSelectableByMouse)
        texts.addWidget(contact)
        layout.addLayout(texts, 1)

        config_btn = QPushButton("打开配置目录")
        config_btn.setObjectName("Secondary")
        config_btn.setCursor(Qt.PointingHandCursor)
        config_btn.clicked.connect(lambda: self.main.open_path(APP_DIR))
        layout.addWidget(config_btn)

        log_btn = QPushButton("运行日志")
        log_btn.setObjectName("Ghost")
        log_btn.setCursor(Qt.PointingHandCursor)
        log_btn.clicked.connect(self.main.toggle_log)
        layout.addWidget(log_btn)
        return card

    def _build_developer_card(self) -> QWidget:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel("开发者模式")
        title.setStyleSheet("font-size: 15px; font-weight: 700;")
        self.developer_badge = badge("未解锁", "gray")
        header.addWidget(title)
        header.addWidget(self.developer_badge)
        header.addStretch(1)
        layout.addLayout(header)

        self.developer_hint = QLabel(
            "开发者模式会显示 DEV 标记、解锁开发者台词，并允许从启动器打开网站反馈表。"
            "输入开发者口令后开启，口令只在本机校验，不会上传。"
        )
        self.developer_hint.setObjectName("Tiny")
        self.developer_hint.setWordWrap(True)
        layout.addWidget(self.developer_hint)

        self.developer_unlock_row = QWidget()
        unlock_layout = QHBoxLayout(self.developer_unlock_row)
        unlock_layout.setContentsMargins(0, 0, 0, 0)
        unlock_layout.setSpacing(8)
        self.developer_key_edit = QLineEdit()
        self.developer_key_edit.setEchoMode(QLineEdit.Password)
        self.developer_key_edit.setPlaceholderText("输入开发者口令")
        self.developer_key_edit.setMaxLength(120)
        self.developer_key_edit.returnPressed.connect(self._unlock_developer)
        self.developer_unlock_btn = QPushButton("解锁")
        self.developer_unlock_btn.setObjectName("Primary")
        self.developer_unlock_btn.setCursor(Qt.PointingHandCursor)
        self.developer_unlock_btn.clicked.connect(self._unlock_developer)
        unlock_layout.addWidget(self.developer_key_edit, 1)
        unlock_layout.addWidget(self.developer_unlock_btn)
        layout.addWidget(self.developer_unlock_row)

        self.developer_actions = QWidget()
        action_layout = QHBoxLayout(self.developer_actions)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(8)
        self.developer_table_btn = QPushButton("打开网站反馈表")
        self.developer_table_btn.setObjectName("Primary")
        self.developer_table_btn.setCursor(Qt.PointingHandCursor)
        self.developer_table_btn.clicked.connect(self._open_feedback_table)
        self.developer_lock_btn = QPushButton("退出开发者模式")
        self.developer_lock_btn.setObjectName("Ghost")
        self.developer_lock_btn.setCursor(Qt.PointingHandCursor)
        self.developer_lock_btn.clicked.connect(self._lock_developer)
        action_layout.addWidget(self.developer_table_btn)
        action_layout.addWidget(self.developer_lock_btn)
        action_layout.addStretch(1)
        layout.addWidget(self.developer_actions)

        self.developer_status = QLabel("")
        self.developer_status.setObjectName("Tiny")
        self.developer_status.setWordWrap(True)
        layout.addWidget(self.developer_status)

        self.refresh_developer_state()
        return card

    def refresh_developer_state(self) -> None:
        if not hasattr(self, "developer_badge"):
            return
        unlocked = bool(getattr(self.main.config, "developer_unlocked", False))
        self.developer_badge.setText("已解锁" if unlocked else "未解锁")
        self.developer_badge.setProperty("badge", "green" if unlocked else "gray")
        repolish(self.developer_badge)
        self.developer_unlock_row.setVisible(not unlocked)
        self.developer_actions.setVisible(unlocked)
        if unlocked:
            self.developer_hint.setText(
                "开发者模式已开启。网站反馈表只能从启动器这里的入口打开；"
                "反馈内容由下载站读取 GitHub Issues 展示。"
            )
            self.developer_status.setText("")
        else:
            self.developer_hint.setText(
                "开发者模式会显示 DEV 标记、解锁开发者台词，并允许从启动器打开网站反馈表。"
                "输入开发者口令后开启，口令只在本机校验，不会上传。"
            )

    def _unlock_developer(self) -> None:
        key = self.developer_key_edit.text().strip()
        if not key:
            self.developer_status.setText("先输入开发者口令。")
            self.developer_status.setStyleSheet(f"color: {colors()['red']}; font-size: 11px;")
            return
        if not verify_developer_key(key):
            self.developer_status.setText("口令不正确，开发者模式没有开启。")
            self.developer_status.setStyleSheet(f"color: {colors()['red']}; font-size: 11px;")
            self.developer_key_edit.selectAll()
            self.developer_key_edit.setFocus()
            return
        unlock_developer(self.main.config, key)
        self.main.config.save()
        self.developer_key_edit.clear()
        self.refresh_developer_state()
        self.developer_status.setText("开发者模式已解锁。")
        self.developer_status.setStyleSheet(f"color: {colors()['green']}; font-size: 11px;")
        if hasattr(self.main, "_update_account"):
            self.main._update_account()
        try:
            self.main.toast("开发者模式已解锁", "success")
        except Exception:
            pass

    def _lock_developer(self) -> None:
        lock_developer(self.main.config)
        self.main.config.save()
        self.refresh_developer_state()
        self.developer_status.setText("已退出开发者模式。")
        self.developer_status.setStyleSheet(f"color: {colors()['muted']}; font-size: 11px;")
        if hasattr(self.main, "_update_account"):
            self.main._update_account()
        try:
            self.main.toast("已退出开发者模式", "info")
        except Exception:
            pass

    def _open_feedback_table(self) -> None:
        if not bool(getattr(self.main.config, "developer_unlocked", False)):
            return
        QDesktopServices.openUrl(QUrl(feedback_table_url(self.main.config)))

    # ------------------------------------------------------------------
    def refresh_account(self) -> None:
        account = self.main.current_account()
        self.client_edit.setText(self.main.config.microsoft_client_id)
        self.email_edit.setText(self.main.config.microsoft_email)
        if account.is_msa:
            self.account_badge.setText("微软正版")
            self.account_badge.setProperty("badge", "green")
            self.account_name.setText(account.name or "正版账户")
            uid = account.uuid or ""
            self.account_uuid.setText(
                f"UUID {uid[:8]}…{uid[-4:]}" if uid else "已登录 Microsoft 账户"
            )
            self.login_btn.setText("重新登录")
            self.verify_btn.setVisible(False)
            self.logout_btn.setVisible(True)
        else:
            self.account_badge.setText("离线模式")
            self.account_badge.setProperty("badge", "gray")
            self.account_name.setText(f"离线账户 · {self.main.config.player_name}")
            self.account_uuid.setText(
                "未登录 Microsoft 账户，启动时使用上面的离线名称。"
            )
            self.login_btn.setText("邮箱登录")
            self.verify_btn.setVisible(True)
            self.logout_btn.setVisible(False)
        repolish(self.account_badge)

    def _save_client_id(self) -> None:
        value = self.client_edit.text().strip()
        if value == self.main.config.microsoft_client_id:
            return
        self.main.config.microsoft_client_id = value
        self.main.config.save()

    def _save_email(self) -> None:
        value = self.email_edit.text().strip()
        if value == self.main.config.microsoft_email:
            return
        self.main.config.microsoft_email = value
        self.main.config.save()

    def _open_azure_portal(self) -> None:
        QDesktopServices.openUrl(
            QUrl(
                "https://portal.azure.com/#view/"
                "Microsoft_AAD_RegisteredApps/ApplicationsListBlade"
            )
        )

    def _login_microsoft(self) -> None:
        client_id = self.client_edit.text().strip()
        try:
            client_id = normalize_client_id(client_id)
        except MicrosoftAuthError as exc:
            self.main.toast(str(exc), "error")
            self.client_edit.setFocus()
            return
        self._save_client_id()
        self._save_email()
        dialog = BrowserLoginDialog(
            self.main, client_id, self.main.config.microsoft_email
        )
        if dialog.exec() and dialog.account is not None:
            self._apply_account(dialog.account)

    def _login_device_code(self) -> None:
        client_id = self.client_edit.text().strip()
        try:
            client_id = normalize_client_id(client_id)
        except MicrosoftAuthError as exc:
            self.main.toast(str(exc), "error")
            self.client_edit.setFocus()
            return
        self._save_client_id()
        dialog = MicrosoftLoginDialog(self.main, client_id)
        if dialog.exec() and dialog.account is not None:
            self._apply_account(dialog.account)

    def _apply_account(self, account) -> None:
        self.main.config.account = account.to_config()
        self.main.config.save()
        self.main.account_changed.emit()
        self.main.toast(f"已登录 Microsoft 账户：{account.name}", "success")

    def _logout_microsoft(self) -> None:
        if not self.main.current_account().is_msa:
            return
        dialog = ConfirmDialog(
            self.main,
            "退出正版账户",
            "退出后将以离线账户启动游戏，正版服务器将无法进入。确定退出吗？",
            primary_text="退出登录",
            secondary_text="取消",
            icon_name="warning",
        )
        if not dialog.exec():
            return
        self.main.config.account = {}
        self.main.config.save()
        self.main.account_changed.emit()
        self.main.toast("已退出正版账户", "info")

    def _save_isolation(self, checked: bool) -> None:
        checked = bool(checked)
        if checked == bool(self.main.config.version_isolation):
            return
        self.main.config.version_isolation = checked
        self.main.config.save()
        if hasattr(self.main, "mods_page"):
            self.main.mods_page.on_show()
        self.main.toast(
            "版本隔离已开启，新的 Mod 会进入版本目录"
            if checked
            else "版本隔离已关闭，将使用游戏目录下的公共 mods",
            "success",
        )

    def _browse_game_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "选择 .minecraft 文件夹", str(self.main.config.game_path)
        )
        if not folder:
            return
        self.main.config.game_dir = folder
        self.main.config.save()
        self.game_dir.setText(folder)
        self.main.refresh_versions()
        self.main.toast("游戏目录已更新", "success")

    def _save_name(self) -> None:
        name = self.name_edit.text().strip()
        if name == self.main.config.player_name:
            return
        if not valid_player_name(name):
            self.main.toast("名称需为 3-16 位字母、数字或下划线", "error")
            self.name_edit.setText(self.main.config.player_name)
            return
        self.main.config.player_name = name
        self.main.config.save()
        self.main.account_changed.emit()
        self.main.toast("玩家名称已更新", "success")

    def _on_memory(self, value: int) -> None:
        value = int(round(value / 512) * 512)
        self.memory_value.setText(f"{value} MB · {value / 1024:.1f} GB")
        if value != self.main.config.memory_mb:
            self.main.config.memory_mb = value
            if hasattr(self, "_save_timer"):
                self._save_timer.start(350)

    def _populate_java(self, installs: list[JavaInfo]) -> None:
        self.java_combo.blockSignals(True)
        self.java_combo.clear()
        self.java_combo.addItem("自动选择（推荐）", "")
        paths: set[str] = set()
        for info in installs:
            self.java_combo.addItem(info.display, info.path)
            paths.add(info.path.lower())
        config_path = self.main.config.java_path
        if config_path and config_path.lower() not in paths:
            self.java_combo.addItem(
                f"手动指定 · {config_path}", config_path
            )
        if config_path:
            index = self.java_combo.findData(config_path)
            if index >= 0:
                self.java_combo.setCurrentIndex(index)
        self.java_combo.blockSignals(False)

        if installs:
            self.java_hint.setText(
                f"已检测到 {len(installs)} 个 Java。"
                "启动时会自动选择满足版本要求的最低版本。"
            )
        else:
            self.java_hint.setText(
                "未检测到 Java。请安装 Java 运行环境，或手动选择 java.exe。"
            )
        self._update_java_requirement()

    def _update_java_requirement(self) -> None:
        profile_id, _instance_id = self.main.launch_profile(
            self.main.selected_version
        )
        summary = next(
            (v for v in self.main.versions if v.id == profile_id),
            None,
        )
        if summary and summary.java_major and self.main.java_installs:
            self.java_hint.setText(
                f"当前版本 {self.main.selected_version or summary.id} 需要 Java "
                f"{summary.java_major}+，"
                f"已检测到 {len(self.main.java_installs)} 个 Java。"
            )

    def _on_java_changed(self, _index: int) -> None:
        path = self.java_combo.currentData() or ""
        if path == self.main.config.java_path:
            return
        self.main.config.java_path = path
        self.main.config.save()
        self.main.toast("Java 设置已保存", "success")

    def _browse_java(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 java.exe", "", "Java 可执行文件 (java.exe);;所有文件 (*)"
        )
        if not path:
            return
        info = probe_java(path)
        if info is None:
            self.main.toast("无法运行该 Java，请确认路径是否正确", "error")
            return
        self.main.config.java_path = path
        self.main.config.save()
        self._populate_java(self.main.java_installs)
        self.main.toast(f"已选择 Java {info.version}", "success")

    def _rescan_java(self) -> None:
        self.java_hint.setText("正在检测 Java…")
        self.main.ensure_java_detected(force=True)

    def _save_jvm_args(self) -> None:
        text = self.jvm_edit.text().strip()
        if text != self.main.config.jvm_args:
            self.main.config.jvm_args = text
            self.main.config.save()
            self.main.toast("JVM 参数已保存", "success")

    def _save_source(self, _index: int) -> None:
        source = self.source_combo.currentData() or "official"
        if source == self.main.config.download_source:
            return
        self.main.config.download_source = source
        self.main.config.save()
        self.main.toast("下载源已更新，下次安装生效", "success")

    def _save_threads(self, _index: int) -> None:
        value = int(self.thread_combo.currentData() or 8)
        value = max(1, min(16, value))
        if value == self.main.config.download_threads:
            return
        self.main.config.download_threads = value
        self.main.config.save()
        self.main.toast(f"下载线程已设为 {value}，下次下载生效", "success")

    def refresh_shortcut_state(self) -> None:
        from ...shortcut import shortcut_exists

        exists = shortcut_exists()
        self.shortcut_btn.setText(
            "重新创建快捷方式" if exists else "创建桌面快捷方式"
        )
        self.shortcut_btn.setToolTip(
            "覆盖桌面上已有的同名快捷方式"
            if exists
            else "在桌面创建 Darkitten Launcher 快捷方式"
        )

    def _create_shortcut(self) -> None:
        self.main.create_desktop_shortcut()

    def _cleanup_partials(self) -> None:
        if self._cleanup_worker is not None:
            return
        if self.main.install_active:
            self.main.toast("有安装任务正在进行，请先完成或取消安装", "info")
            return
        root = self.main.config.game_path
        dialog = ConfirmDialog(
            self,
            "清理下载残留",
            "将扫描游戏目录，删除未完成的 .part 分片临时文件。"
            "已下载完成的文件不会受影响。",
            primary_text="开始清理",
            secondary_text="取消",
            icon_name="trash",
            icon_color=colors()["orange"],
            details=f"扫描目录：{root}",
        )
        if not dialog.exec():
            return
        self.cleanup_btn.setEnabled(False)
        self.cleanup_btn.setText("正在清理…")
        self.main.set_status("正在清理下载残留…", "busy")
        worker = CleanupWorker(root, self)
        self._cleanup_worker = worker
        worker.finished.connect(self._on_cleanup_finished)
        worker.start()

    def _on_cleanup_finished(self, removed: int, freed: int) -> None:
        self._cleanup_worker = None
        self.cleanup_btn.setEnabled(True)
        self.cleanup_btn.setText("清理下载残留")
        if removed:
            size = (
                f"{freed / 1024 / 1024:.1f} MB"
                if freed >= 1024 * 1024
                else f"{max(0, freed) / 1024:.0f} KB"
            )
            self.main.set_status(f"已清理 {removed} 个下载残留", "success")
            self.main.toast(f"已清理 {removed} 个临时文件（{size}）", "success")
        else:
            self.main.set_status("没有发现下载残留", "idle")
            self.main.toast("没有发现未完成的下载文件", "info")

    def _save_theme(self, theme_value: str) -> None:
        if theme_value == self.main.config.theme:
            return
        self.main.config.theme = theme_value
        self.main.config.save()
        self.main.apply_theme(theme_value)

    def _save_auto(self, checked: bool) -> None:
        if bool(self.main.config.auto_complete) == bool(checked):
            return
        self.main.config.auto_complete = bool(checked)
        self.main.config.save()

    def refresh_update_state(self) -> None:
        if not hasattr(self, "update_status"):
            return
        palette = {
            "idle": colors()["muted"],
            "busy": colors()["accent"],
            "accent": colors()["accent"],
            "success": colors()["green"],
            "error": colors()["red"],
        }
        color = palette.get(self.main.update_state_kind, colors()["muted"])
        self.update_status.setText(self.main.update_state_text)
        self.update_status.setStyleSheet(
            f"color: {color}; font-size: 11px; font-weight: 600;"
        )
        last = str(self.main.config.update_last_check or "").strip()
        self.update_status.setToolTip(
            f"上次检查：{last}" if last else "还没有检查过更新"
        )
        busy = bool(getattr(self.main, "_update_busy", False))
        self.update_btn.setEnabled(not busy)
        self.update_btn.setText("正在检查…" if busy else "检查更新")
        self.update_auto_switch.setChecked(
            bool(self.main.config.update_auto_check)
        )
        self.update_url_edit.setText(DEFAULT_UPDATE_URL)

    def _check_updates(self) -> None:
        self.main.check_for_updates(manual=True)

    def _open_update_site(self) -> None:
        self.main.open_update_site()

    def _save_update_auto(self, checked: bool) -> None:
        if bool(self.main.config.update_auto_check) == bool(checked):
            return
        self.main.config.update_auto_check = bool(checked)
        self.main.config.save()
        self.main.toast(
            "启动时会自动检查更新" if checked else "已关闭启动时自动检查更新",
            "success",
        )

    def on_show(self) -> None:
        self.refresh_account()
        self.isolation_switch.setChecked(bool(self.main.config.version_isolation))
        source_index = self.source_combo.findData(self.main.config.download_source)
        self.source_combo.setCurrentIndex(max(0, source_index))
        thread_index = self.thread_combo.findData(self.main.config.download_threads)
        if thread_index >= 0:
            self.thread_combo.setCurrentIndex(thread_index)
        self._populate_java(self.main.java_installs)
        self.refresh_shortcut_state()
        self.refresh_update_state()
        self._refresh_snapshot_hint()
        self.refresh_developer_state()
        self.main.ensure_java_detected()
