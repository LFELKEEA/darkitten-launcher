from __future__ import annotations

import re

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
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
    QVBoxLayout,
    QWidget,
)

from ...sakura import (
    SAKURA_MC_DOCS,
    SAKURA_PANEL,
    ROOM_TUNNEL_NAME,
    SakuraError,
    SakuraNode,
    SakuraTunnel,
    SakuraUser,
    SakuraTunnelProcess,
    cleanup_room_configs,
    decode_room_input,
    detect_game_lan_ports,
    normalize_room_code,
    preferred_lan_port,
    remove_tunnel_config,
)
from ...social import encode_invite
from ...workers import SakuraLoginWorker, SakuraRoomWorker
from .. import icons
from ..dialogs import ConfirmDialog
from ..widgets import (
    Card,
    ChibiCompanion,
    badge,
    colors,
    hline,
    icon_button,
    repolish,
)

STATUS_COLORS = {
    "muted": ("muted", ""),
    "busy": ("accent", ""),
    "success": ("green", ""),
    "warning": ("orange", ""),
    "error": ("red", ""),
}


class RoomPage(QWidget):
    def __init__(self, main, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.main = main
        self._token = ""
        self._user: SakuraUser | None = None
        self._nodes: dict[int, SakuraNode] = {}
        self._tunnels: list[SakuraTunnel] = []
        self._login_worker: SakuraLoginWorker | None = None
        self._room_worker: SakuraRoomWorker | None = None
        self._pending_token = ""
        self._tunnel_process: SakuraTunnelProcess | None = None
        self._room_payload: dict | None = None
        self._closing = False
        self._busy = False
        self._auto_login_attempted = False
        self._notice_shown = False
        self._decoded_address = ""
        self._host_status_kind = "muted"
        self._guest_status_kind = "muted"
        cleanup_room_configs()
        self._build()
        self._restore_token()
        self._sync_versions()
        self.main.versions_changed.connect(self._sync_versions)
        self.main.selection_changed.connect(self._sync_selection)
        self.main.game_state_changed.connect(self._on_game_state)
        self._on_game_state(self.main.game_running)

    # ------------------------------------------------------------------
    # 界面构建

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
        title = QLabel("猫猫联机窝")
        title.setObjectName("PageTitle")
        sub = QLabel(
            "内置樱花穿透：房主把单人世界开放到局域网，朋友输入房间号就能直接进入。"
        )
        sub.setObjectName("Muted")
        sub.setWordWrap(True)
        titles.addWidget(title)
        titles.addWidget(sub)
        header.addLayout(titles, 1)
        header.addWidget(ChibiCompanion(60, "room"), 0, Qt.AlignTop)

        builtin_badge = badge("樱花穿透 · 内置", "blue")
        header.addWidget(builtin_badge, 0, Qt.AlignTop)
        docs_btn = QPushButton("联机文档")
        docs_btn.setObjectName("Ghost")
        docs_btn.setCursor(Qt.PointingHandCursor)
        docs_btn.setIcon(icons.icon("external", colors()["text2"], 15))
        docs_btn.clicked.connect(lambda: self._open_url(SAKURA_MC_DOCS))
        header.addWidget(docs_btn, 0, Qt.AlignTop)
        layout.addLayout(header)

        columns = QHBoxLayout()
        columns.setSpacing(14)
        self.host_card = self._build_host_card()
        self.guest_card = self._build_guest_card()
        columns.addWidget(self.host_card, 3)
        columns.addWidget(self.guest_card, 2)
        layout.addLayout(columns, 1)

        layout.addWidget(self._build_info_card())
        layout.addStretch(1)

    def _build_host_card(self) -> QWidget:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 15, 18, 15)
        layout.setSpacing(9)

        header = QHBoxLayout()
        title = QLabel("开房间")
        title.setObjectName("H2")
        header.addWidget(title)
        header.addStretch(1)
        self.host_badge = badge("未连接", "gray")
        header.addWidget(self.host_badge)
        layout.addLayout(header)

        sub = QLabel(
            "填写樱花穿透访问密钥，创建一条 TCP 隧道指向游戏的局域网端口。"
        )
        sub.setObjectName("Tiny")
        sub.setWordWrap(True)
        layout.addWidget(sub)

        token_row = QHBoxLayout()
        token_row.setSpacing(6)
        self.token_edit = QLineEdit()
        self.token_edit.setPlaceholderText("樱花穿透访问密钥")
        self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_edit.setClearButtonEnabled(True)
        self.token_edit.returnPressed.connect(lambda: self.login_sakura())
        token_row.addWidget(self.token_edit, 1)

        self.show_token_btn = QPushButton("显示")
        self.show_token_btn.setObjectName("Ghost")
        self.show_token_btn.setFixedWidth(48)
        self.show_token_btn.setCursor(Qt.PointingHandCursor)
        self.show_token_btn.clicked.connect(self._toggle_token_visibility)
        token_row.addWidget(self.show_token_btn)

        self.connect_btn = QPushButton("验证并连接")
        self.connect_btn.setObjectName("Soft")
        self.connect_btn.setCursor(Qt.PointingHandCursor)
        self.connect_btn.clicked.connect(lambda: self.login_sakura())
        token_row.addWidget(self.connect_btn)
        layout.addLayout(token_row)

        links = QHBoxLayout()
        links.setSpacing(4)
        hint = QLabel("密钥使用 Windows DPAPI 加密保存，只发送给樱花穿透官方接口。")
        hint.setObjectName("Tiny")
        hint.setWordWrap(True)
        links.addWidget(hint, 1)
        panel_btn = QPushButton("获取密钥")
        panel_btn.setObjectName("Ghost")
        panel_btn.setCursor(Qt.PointingHandCursor)
        panel_btn.setIcon(icons.icon("external", colors()["text2"], 13))
        panel_btn.clicked.connect(lambda: self._open_url(SAKURA_PANEL))
        links.addWidget(panel_btn, 0, Qt.AlignTop)
        layout.addLayout(links)

        self.account_line = QLabel("尚未连接樱花穿透账户。")
        self.account_line.setObjectName("Tiny")
        self.account_line.setWordWrap(True)
        layout.addWidget(self.account_line)
        layout.addWidget(hline())

        port_row = QHBoxLayout()
        port_row.setSpacing(8)
        port_label = QLabel("局域网端口")
        port_label.setStyleSheet("font-weight: 600;")
        port_row.addWidget(port_label)
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(25565)
        self.port_spin.setFixedWidth(108)
        self.port_spin.setToolTip("游戏内“对局域网开放”后显示的端口")
        port_row.addWidget(self.port_spin)
        self.detect_btn = QPushButton("检测游戏端口")
        self.detect_btn.setObjectName("Soft")
        self.detect_btn.setCursor(Qt.PointingHandCursor)
        self.detect_btn.clicked.connect(self.detect_port)
        port_row.addWidget(self.detect_btn)
        port_row.addStretch(1)
        layout.addLayout(port_row)

        port_hint = QLabel(
            "进入单人游戏后按 Esc → 对局域网开放。只有房主需要保持游戏和房间开启。"
        )
        port_hint.setObjectName("Tiny")
        port_hint.setWordWrap(True)
        layout.addWidget(port_hint)

        node_row = QHBoxLayout()
        node_row.setSpacing(8)
        node_label = QLabel("穿透节点")
        node_label.setStyleSheet("font-weight: 600;")
        node_row.addWidget(node_label)
        self.node_combo = QComboBox()
        self.node_combo.setMinimumWidth(240)
        self.node_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        node_row.addWidget(self.node_combo, 1)
        self.node_refresh = icon_button("refresh", "刷新节点列表")
        self.node_refresh.clicked.connect(lambda: self.login_sakura(silent=True))
        node_row.addWidget(self.node_refresh)
        layout.addLayout(node_row)

        node_hint = QLabel(
            "熟人联机可选普通节点；如果要公开宣传服务器，必须选择带“多线”标记的专用节点。"
        )
        node_hint.setObjectName("Tiny")
        node_hint.setWordWrap(True)
        layout.addWidget(node_hint)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.create_btn = QPushButton("创建房间")
        self.create_btn.setObjectName("Primary")
        self.create_btn.setCursor(Qt.PointingHandCursor)
        self.create_btn.setIcon(icons.icon("bolt", "#FFFFFF", 16))
        self.create_btn.clicked.connect(self.create_room)
        buttons.addWidget(self.create_btn)

        self.close_btn = QPushButton("关闭房间")
        self.close_btn.setObjectName("Danger")
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.clicked.connect(self.close_room)
        self.close_btn.setVisible(False)
        buttons.addWidget(self.close_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.host_status = QLabel("填写访问密钥后即可创建房间。")
        self.host_status.setObjectName("Tiny")
        self.host_status.setWordWrap(True)
        layout.addWidget(self.host_status)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.result_box = self._build_result_box()
        layout.addWidget(self.result_box)
        layout.addWidget(self._build_log_box())
        return card

    def _build_result_box(self) -> QWidget:
        box = QFrame()
        box.setObjectName("SoftCard")
        box.setAttribute(Qt.WA_StyledBackground, True)
        box.setVisible(False)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(14, 11, 14, 11)
        layout.setSpacing(4)

        caption = QLabel("房间号")
        caption.setObjectName("Tiny")
        layout.addWidget(caption)

        code_row = QHBoxLayout()
        code_row.setSpacing(8)
        self.room_code_label = QLabel("—")
        self.room_code_label.setStyleSheet(
            "font-size: 24px; font-weight: 800; letter-spacing: 2px;"
        )
        self.room_code_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        code_row.addWidget(self.room_code_label, 1)
        self.copy_code_btn = QPushButton("复制房间号")
        self.copy_code_btn.setObjectName("Primary")
        self.copy_code_btn.setCursor(Qt.PointingHandCursor)
        self.copy_code_btn.setIcon(icons.icon("copy", "#FFFFFF", 14))
        self.copy_code_btn.clicked.connect(self._copy_room_code)
        code_row.addWidget(self.copy_code_btn)

        self.copy_invite_btn = QPushButton("好友邀请卡")
        self.copy_invite_btn.setObjectName("Soft")
        self.copy_invite_btn.setCursor(Qt.PointingHandCursor)
        self.copy_invite_btn.setIcon(icons.icon("users", colors()["accent"], 14))
        self.copy_invite_btn.setToolTip(
            "复制带房间号的邀请卡，好友导入后可以直接进入房间"
        )
        self.copy_invite_btn.clicked.connect(self._copy_invite_card)
        code_row.addWidget(self.copy_invite_btn)
        layout.addLayout(code_row)

        self.room_address_label = QLabel("")
        self.room_address_label.setObjectName("Tiny")
        self.room_address_label.setWordWrap(True)
        self.room_address_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.room_address_label)

        addr_row = QHBoxLayout()
        addr_row.setSpacing(6)
        self.room_detail_label = QLabel("")
        self.room_detail_label.setObjectName("Tiny")
        self.room_detail_label.setWordWrap(True)
        addr_row.addWidget(self.room_detail_label, 1)
        self.copy_address_btn = QPushButton("复制地址")
        self.copy_address_btn.setObjectName("Ghost")
        self.copy_address_btn.setCursor(Qt.PointingHandCursor)
        self.copy_address_btn.setIcon(icons.icon("copy", colors()["text2"], 13))
        self.copy_address_btn.clicked.connect(self._copy_host_address)
        addr_row.addWidget(self.copy_address_btn)
        layout.addLayout(addr_row)
        return box

    def _build_log_box(self) -> QWidget:
        self.log_box = QPlainTextEdit()
        self.log_box.setObjectName("SoftLog")
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumBlockCount(400)
        self.log_box.setFixedHeight(92)
        self.log_box.setPlaceholderText("内置 frpc 的连接日志会显示在这里")
        self.log_box.setVisible(False)
        return self.log_box

    def _build_guest_card(self) -> QWidget:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 15, 18, 15)
        layout.setSpacing(9)

        header = QHBoxLayout()
        title = QLabel("加入房间")
        title.setObjectName("H2")
        header.addWidget(title)
        header.addStretch(1)
        no_account = badge("无需樱花账号", "green")
        header.addWidget(no_account)
        layout.addLayout(header)

        sub = QLabel("粘贴房主发来的房间号，选择相同版本后直接启动游戏。")
        sub.setObjectName("Tiny")
        sub.setWordWrap(True)
        layout.addWidget(sub)

        code_caption = QLabel("房间号")
        code_caption.setStyleSheet("font-weight: 600;")
        layout.addWidget(code_caption)

        code_row = QHBoxLayout()
        code_row.setSpacing(6)
        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText("XXXXX-XXXX-XXXXX")
        self.code_edit.setClearButtonEnabled(True)
        self.code_edit.setMaxLength(40)
        self.code_edit.textChanged.connect(self._update_code_preview)
        self.code_edit.editingFinished.connect(self._format_code_field)
        self.code_edit.returnPressed.connect(self.join_room)
        code_row.addWidget(self.code_edit, 1)
        paste_btn = icon_button("copy", "从剪贴板粘贴房间号")
        paste_btn.clicked.connect(self._paste_code)
        code_row.addWidget(paste_btn)
        layout.addLayout(code_row)

        self.code_hint = QLabel("也可以直接粘贴 host:port 形式的服务器地址。")
        self.code_hint.setObjectName("Tiny")
        self.code_hint.setWordWrap(True)
        layout.addWidget(self.code_hint)
        layout.addWidget(hline())

        version_caption = QLabel("游戏版本")
        version_caption.setStyleSheet("font-weight: 600;")
        layout.addWidget(version_caption)
        self.version_combo = QComboBox()
        self.version_combo.setMinimumWidth(220)
        layout.addWidget(self.version_combo)

        version_hint = QLabel("必须和房主使用相同版本；模组、整合包也需要一致。")
        version_hint.setObjectName("Tiny")
        version_hint.setWordWrap(True)
        layout.addWidget(version_hint)

        self.join_btn = QPushButton("启动游戏并进入")
        self.join_btn.setObjectName("Primary")
        self.join_btn.setCursor(Qt.PointingHandCursor)
        self.join_btn.setIcon(icons.icon("play", "#FFFFFF", 16))
        self.join_btn.clicked.connect(self.join_room)
        layout.addWidget(self.join_btn)

        self.copy_decode_btn = QPushButton("仅复制服务器地址")
        self.copy_decode_btn.setObjectName("Ghost")
        self.copy_decode_btn.setCursor(Qt.PointingHandCursor)
        self.copy_decode_btn.setIcon(icons.icon("copy", colors()["text2"], 14))
        self.copy_decode_btn.clicked.connect(self._copy_guest_address)
        layout.addWidget(self.copy_decode_btn)

        self.guest_status = QLabel("房主创建房间后，把房间号发给你即可。")
        self.guest_status.setObjectName("Tiny")
        self.guest_status.setWordWrap(True)
        layout.addWidget(self.guest_status)

        layout.addStretch(1)
        note = QLabel(
            "正版/离线账户都可以加入。若服务器开启了正版验证，请先在设置里登录 Microsoft 账户。"
        )
        note.setObjectName("Tiny")
        note.setWordWrap(True)
        layout.addWidget(note)
        return card

    def _build_info_card(self) -> QWidget:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("联机须知")
        title.setObjectName("H2")
        header.addWidget(title)
        header.addWidget(badge("仅限熟人联机", "orange"))
        header.addStretch(1)
        tunnel_btn = QPushButton("隧道规则")
        tunnel_btn.setObjectName("Ghost")
        tunnel_btn.setCursor(Qt.PointingHandCursor)
        tunnel_btn.clicked.connect(lambda: self._open_url(SAKURA_MC_DOCS))
        header.addWidget(tunnel_btn)
        layout.addLayout(header)

        self._step_numbers: list[QLabel] = []
        steps = QHBoxLayout()
        steps.setSpacing(10)
        step_data = [
            ("1", "房主开放世界", "进入单人游戏，按 Esc → 对局域网开放，记下端口。"),
            ("2", "创建房间", "连接樱花穿透，检测端口并创建，把房间号发给朋友。"),
            ("3", "朋友直接进入", "粘贴房间号，选择相同版本，启动后自动连接。"),
        ]
        for number, step_title, step_desc in step_data:
            box = QFrame()
            box.setObjectName("SoftCard")
            box.setAttribute(Qt.WA_StyledBackground, True)
            box_layout = QVBoxLayout(box)
            box_layout.setContentsMargins(12, 10, 12, 10)
            box_layout.setSpacing(3)
            top = QHBoxLayout()
            top.setSpacing(7)
            number_label = QLabel(number)
            number_label.setAlignment(Qt.AlignCenter)
            number_label.setFixedSize(20, 20)
            number_label.setStyleSheet(
                f"background: {colors()['accent_soft']}; color: {colors()['accent']};"
                "border-radius: 10px; font-size: 10px; font-weight: 800;"
            )
            self._step_numbers.append(number_label)
            top.addWidget(number_label)
            step_name = QLabel(step_title)
            step_name.setStyleSheet("font-weight: 700; font-size: 12px;")
            top.addWidget(step_name)
            top.addStretch(1)
            box_layout.addLayout(top)
            desc = QLabel(step_desc)
            desc.setObjectName("Tiny")
            desc.setWordWrap(True)
            box_layout.addWidget(desc)
            steps.addWidget(box, 1)
        layout.addLayout(steps)

        warning_box = QFrame()
        warning_box.setObjectName("SoftCard")
        warning_box.setAttribute(Qt.WA_StyledBackground, True)
        warning_layout = QHBoxLayout(warning_box)
        warning_layout.setContentsMargins(11, 8, 11, 8)
        warning_layout.setSpacing(8)
        warn_icon = QLabel()
        warn_icon.setPixmap(icons.pixmap("warning", colors()["orange"], 17))
        warning_layout.addWidget(warn_icon, 0, Qt.AlignTop)
        warning = QLabel(
            "房间号只适合朋友之间联机。若在论坛、群聊或视频平台公开宣传，"
            "必须使用带“多线”标记的公开游戏节点，否则可能被樱花穿透封号；"
            "免费账户还有流量、隧道数和实名认证限制。"
        )
        warning.setObjectName("Tiny")
        warning.setWordWrap(True)
        warning_layout.addWidget(warning, 1)
        layout.addWidget(warning_box)
        return card

    # ------------------------------------------------------------------
    # 状态与主题

    def _set_status(self, label: QLabel, text: str, kind: str = "muted") -> None:
        c = colors()
        key = STATUS_COLORS.get(kind, STATUS_COLORS["muted"])[0]
        label.setStyleSheet(
            f"color: {c[key]}; font-size: 11px; background: transparent;"
        )
        label.setText(text)
        if label is self.host_status:
            self._host_status_kind = kind
        elif label is self.guest_status:
            self._guest_status_kind = kind

    def _set_badge(self, label: QLabel, text: str, kind: str) -> None:
        label.setText(text)
        label.setProperty("badge", kind)
        repolish(label)

    def refresh_theme(self) -> None:
        self._set_status(
            self.host_status, self.host_status.text(), self._host_status_kind
        )
        self._set_status(
            self.guest_status, self.guest_status.text(), self._guest_status_kind
        )
        self.create_btn.setIcon(icons.icon("bolt", "#FFFFFF", 16))
        self.join_btn.setIcon(icons.icon("play", "#FFFFFF", 16))
        self.copy_code_btn.setIcon(icons.icon("copy", "#FFFFFF", 14))
        for button in (self.copy_address_btn, self.copy_decode_btn):
            button.setIcon(icons.icon("copy", colors()["text2"], 13))
        for label in getattr(self, "_step_numbers", []):
            label.setStyleSheet(
                f"background: {colors()['accent_soft']}; color: {colors()['accent']};"
                "border-radius: 10px; font-size: 10px; font-weight: 800;"
            )
        for label in self.findChildren(QLabel):
            if label.property("badge"):
                repolish(label)

    def _restore_token(self) -> None:
        token = self.main.config.sakura_access_token
        if not token:
            return
        self._token = token
        self.token_edit.setText(token)
        self.token_edit.setCursorPosition(len(token))
        self.token_edit.setPlaceholderText("已保存访问密钥（已加密）")
        self._set_status(
            self.host_status, "已保存访问密钥，切换到本页时自动连接。", "muted"
        )
        self.connect_btn.setText("重新验证")

    def _toggle_token_visibility(self) -> None:
        if self.token_edit.echoMode() == QLineEdit.EchoMode.Password:
            self.token_edit.setEchoMode(QLineEdit.EchoMode.Normal)
            self.show_token_btn.setText("隐藏")
        else:
            self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self.show_token_btn.setText("显示")

    def on_show(self) -> None:
        self._sync_versions()
        if (
            not self._auto_login_attempted
            and self._token
            and self._user is None
            and not self._busy
        ):
            self._auto_login_attempted = True
            QTimer.singleShot(120, lambda: self.login_sakura(silent=True))

    # ------------------------------------------------------------------
    # 樱花账户

    def login_sakura(self, silent: bool = False) -> None:
        if self._busy:
            return
        if self._tunnel_process is not None:
            self.toast("请先关闭当前房间，再更换访问密钥。", "info")
            return
        token = self.token_edit.text().strip() or self._token
        if not token:
            self._set_status(
                self.host_status, "请先填写樱花穿透访问密钥。", "error"
            )
            return
        self._pending_token = token
        self._busy = True
        self.connect_btn.setEnabled(False)
        self.connect_btn.setText("正在验证…")
        self.create_btn.setEnabled(False)
        self.node_refresh.setEnabled(False)
        self._set_status(self.host_status, "正在验证访问密钥并读取节点…", "busy")
        worker = SakuraLoginWorker(token, self)
        self._login_worker = worker
        worker.ready.connect(
            lambda payload, error, w=worker, s=silent: self._login_ready(
                w, payload, error, s
            )
        )
        worker.start()

    def _login_ready(
        self,
        worker: SakuraLoginWorker,
        payload,
        error: str,
        silent: bool = False,
    ) -> None:
        if self._login_worker is not worker:
            return
        self._login_worker = None
        self._busy = False
        self.connect_btn.setEnabled(True)
        self.node_refresh.setEnabled(True)
        if error or not isinstance(payload, dict):
            self.connect_btn.setText("验证并连接")
            self._set_status(
                self.host_status, error or "无法读取樱花穿透账户。", "error"
            )
            self.toast(error or "樱花穿透连接失败", "error")
            self._sync_controls()
            return

        user: SakuraUser = payload["user"]
        self._user = user
        self._nodes = payload["nodes"]
        self._tunnels = payload["tunnels"]
        self._token = self._pending_token
        old_token = self.main.config.sakura_access_token
        if old_token and old_token != self._token:
            self.main.config.sakura_tunnel_id = 0
        self.main.config.set_sakura_token(self._token)
        self.main.config.save()
        self.token_edit.setText(self._token)
        self.token_edit.setCursorPosition(len(self._token))
        self.token_edit.setPlaceholderText("已保存访问密钥（已加密）")
        self.connect_btn.setText("重新验证")
        self._set_badge(self.host_badge, "已连接", "green")
        self._populate_nodes()

        details = [
            f"已连接 · {user.name or '樱花账户'}",
            user.realname_text,
            user.traffic_text,
        ]
        if user.speed:
            details.append(f"限速 {user.speed}")
        if user.tunnel_limit:
            details.append(f"隧道 {len(self._tunnels)}/{user.tunnel_limit}")
        self.account_line.setText(" · ".join(details))
        if not user.realname:
            self._set_status(
                self.host_status,
                "账户尚未实名认证，创建隧道可能被拒绝，请到樱花穿透面板完成认证。",
                "warning",
            )
        else:
            self._set_status(
                self.host_status, "账户已连接，可以创建房间。", "success"
            )
        self._sync_controls()
        if not silent:
            self.toast("樱花穿透账户已连接", "success")

    def _populate_nodes(self) -> None:
        current_id = self._selected_node_id()
        if not current_id:
            current_id = self.main.config.sakura_node_id
        preferred_tunnel = self._existing_room_tunnel()
        if preferred_tunnel and preferred_tunnel.node in self._nodes:
            current_id = preferred_tunnel.node

        self.node_combo.blockSignals(True)
        self.node_combo.clear()
        self.node_combo.addItem("自动选择节点", 0)
        nodes = [
            node
            for node in self._nodes.values()
            if node.selectable and node.host
        ]
        nodes.sort(key=lambda node: (node.public_game, node.vip, not node.mainland, node.name))
        for node in nodes[:200]:
            self.node_combo.addItem(node.label, node.id)
            index = self.node_combo.count() - 1
            tip = node.description or ""
            if node.host:
                tip = (tip + "\n" if tip else "") + f"节点地址：{node.host}"
            self.node_combo.setItemData(index, tip, Qt.ToolTipRole)
        if current_id:
            index = self.node_combo.findData(current_id)
            if index >= 0:
                self.node_combo.setCurrentIndex(index)
        self.node_combo.blockSignals(False)
        if not nodes:
            self._set_status(
                self.host_status,
                "没有可用节点，请稍后刷新，或到樱花穿透面板检查节点状态。",
                "error",
            )

    def _selected_node_id(self) -> int:
        data = self.node_combo.currentData()
        try:
            return int(data or 0)
        except (TypeError, ValueError):
            return 0

    def _existing_room_tunnel(self) -> SakuraTunnel | None:
        preferred = self.main.config.sakura_tunnel_id
        if preferred:
            for tunnel in self._tunnels:
                if tunnel.id == preferred and tunnel.type == "tcp":
                    return tunnel
        candidates = [
            tunnel
            for tunnel in self._tunnels
            if tunnel.name.startswith(ROOM_TUNNEL_NAME) and tunnel.type == "tcp"
        ]
        return max(candidates, key=lambda item: item.id) if candidates else None

    # ------------------------------------------------------------------
    # 主机流程

    def detect_port(self) -> None:
        process = self.main.game_process
        pid = process.pid if process and self.main.game_running else 0
        if not pid:
            self._set_status(
                self.host_status,
                "游戏还没有运行。请先启动游戏，并在单人游戏里“对局域网开放”。",
                "warning",
            )
            return
        ports = detect_game_lan_ports(pid)
        if not ports:
            self._set_status(
                self.host_status,
                "没有检测到游戏监听端口。请先在游戏里对局域网开放，再点一次检测。",
                "warning",
            )
            return
        port = preferred_lan_port(ports)
        self.port_spin.setValue(port)
        others = [str(item) for item in ports if item != port]
        text = f"已检测到游戏端口 {port}，可以创建房间。"
        if others:
            text += f"（其他监听端口：{', '.join(others[:4])}）"
        self._set_status(self.host_status, text, "success")

    def create_room(self) -> None:
        if self._busy:
            return
        if self._user is None:
            self._set_status(
                self.host_status, "请先验证并连接樱花穿透访问密钥。", "error"
            )
            return
        if self.token_edit.text().strip() != self._token:
            self._set_status(
                self.host_status,
                "访问密钥已修改，请先点击“验证并连接”再创建房间。",
                "warning",
            )
            return
        if self._tunnel_process is not None or self._room_payload is not None:
            self.toast("房间已经开启，请先关闭当前房间。", "info")
            return
        port = int(self.port_spin.value())
        node_id = self._selected_node_id()
        if not self._notice_shown:
            dialog = ConfirmDialog(
                self,
                "创建联机房间",
                "房间号适合朋友之间私下联机。创建后会占用一条樱花穿透隧道。",
                primary_text="仅熟人联机，继续",
                secondary_text="取消",
                icon_name="users",
                icon_color=colors()["green"],
                details=(
                    "如果你的服务器会发布到论坛、群聊、服务器列表或视频平台，"
                    "必须使用带“多线”标记的公开游戏节点，否则可能违反樱花穿透规则并被封号。"
                    "房主需要保持游戏、启动器和房间全程开启。"
                ),
            )
            if not dialog.exec():
                return
            self._notice_shown = True

        self._busy = True
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.log_box.clear()
        self.log_box.setVisible(True)
        self._set_status(
            self.host_status, "正在准备隧道与内置 frpc…", "busy"
        )
        self._set_badge(self.host_badge, "准备中", "blue")
        self._sync_controls()

        worker = SakuraRoomWorker(
            self._token,
            port,
            node_id=node_id,
            preferred_tunnel_id=self.main.config.sakura_tunnel_id,
            frpc_path=self.main.config.sakura_frpc_path,
            parent=self,
        )
        self._room_worker = worker
        worker.progress.connect(
            lambda value, message, w=worker: self._room_progress(w, value, message)
        )
        worker.log_line.connect(
            lambda line, w=worker: self._room_log(w, line)
        )
        worker.ready.connect(
            lambda payload, error, w=worker: self._room_ready(w, payload, error)
        )
        worker.failed.connect(
            lambda error, w=worker: self._room_failed(w, error)
        )
        worker.start()

    def _room_progress(
        self, worker: SakuraRoomWorker, value: int, message: str
    ) -> None:
        if self._room_worker is not worker:
            return
        self.progress.setValue(value)
        self._set_status(self.host_status, message, "busy")

    def _room_log(self, worker: SakuraRoomWorker, line: str) -> None:
        if self._room_worker is not worker:
            return
        self._append_log(line)

    def _append_log(self, line: str) -> None:
        text = str(line or "")
        if not text:
            return
        if self._token:
            text = text.replace(self._token, "***")
        text = re.sub(
            r"(?i)((?:access[_ ]?)?token\s*[=:]\s*)\S+", r"\1***", text
        )
        if not self.log_box.isVisible():
            self.log_box.setVisible(True)
        self.log_box.appendPlainText(text)
        bar = self.log_box.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _room_ready(
        self, worker: SakuraRoomWorker, payload, error: str
    ) -> None:
        if self._room_worker is not worker:
            return
        self._room_worker = None
        self._busy = False
        if error or not isinstance(payload, dict):
            self.progress.setVisible(False)
            self._set_status(
                self.host_status, error or "创建房间失败。", "error"
            )
            self._set_badge(self.host_badge, "已连接", "green")
            self.toast(error or "创建房间失败", "error")
            self._sync_controls()
            return

        self._room_payload = payload
        self.main.config.sakura_tunnel_id = int(payload.get("tunnel_id") or 0)
        self.main.config.sakura_node_id = int(payload.get("node_id") or 0)
        self.main.config.save()
        self.progress.setValue(100)
        self.progress.setVisible(False)
        self._set_status(
            self.host_status, "隧道已准备，正在启动内置 frpc…", "busy"
        )
        self._set_badge(self.host_badge, "连接中", "blue")
        self._start_tunnel(payload)

    def _start_tunnel(self, payload: dict) -> None:
        process = SakuraTunnelProcess(
            payload.get("frpc_path", ""),
            payload.get("config_path", ""),
            self,
        )
        self._tunnel_process = process
        process.log_line.connect(self._append_log)
        process.ready.connect(self._tunnel_ready)
        process.failed.connect(self._tunnel_failed)
        process.finished.connect(self._tunnel_finished)
        process.start()
        self._sync_controls()

    def _tunnel_ready(self, message: str) -> None:
        if self._closing or self._room_payload is None:
            return
        payload = self._room_payload
        self._set_badge(self.host_badge, "房间开启", "green")
        code = str(payload.get("room_code") or "")
        if code:
            self._set_status(
                self.host_status,
                f"房间已开启（{message}）。把房间号发给朋友即可。",
                "success",
            )
        else:
            self._set_status(
                self.host_status,
                "房间已开启，但房间号生成失败，请直接分享服务器地址。",
                "warning",
            )
        self._show_room_result(payload)
        self.progress.setVisible(False)
        self._sync_controls()

    def _show_room_result(self, payload: dict) -> None:
        code = str(payload.get("room_code") or "")
        address = str(payload.get("address") or "")
        share = str(payload.get("share_address") or address)
        self.room_code_label.setText(code or "房间号不可用")
        if code:
            self.room_code_label.setStyleSheet(
                "font-size: 24px; font-weight: 800; letter-spacing: 2px;"
            )
            self.copy_code_btn.setEnabled(True)
        else:
            self.room_code_label.setStyleSheet(
                "font-size: 14px; font-weight: 700;"
            )
            self.copy_code_btn.setEnabled(False)
        self.room_address_label.setText(f"服务器地址：{address}")
        detail = (
            f"隧道 #{payload.get('tunnel_id', '?')} · "
            f"{payload.get('node_name', '')} · "
            f"本地端口 {payload.get('local_port', '')} · "
            f"frpc {payload.get('frpc_version', '')}"
        )
        code_error = str(payload.get("code_error") or "")
        if code_error:
            detail += f"\n{code_error}"
        if share and share != address:
            detail += f"\n节点地址：{share}"
        self.room_detail_label.setText(detail)
        self.result_box.setVisible(True)
        self._sync_social_room(code or address)
        self._sync_controls()

    def _tunnel_failed(self, message: str) -> None:
        if self._closing:
            return
        process = self._tunnel_process
        if process is not None:
            try:
                process.blockSignals(True)
                process.stop()
            except Exception:
                pass
        self._fail_room(message)

    def _tunnel_finished(self, code: int) -> None:
        if self._closing or self._tunnel_process is None:
            return
        self._tunnel_process = None
        if self._room_payload is not None:
            self._fail_room(f"内置 frpc 已退出（代码 {code}），房间已关闭。")

    def _fail_room(self, message: str) -> None:
        payload = self._room_payload
        if payload:
            remove_tunnel_config(payload.get("config_path"))
            self._clear_social_room()
        self._room_payload = None
        self._tunnel_process = None
        self._busy = False
        self.progress.setVisible(False)
        self._set_badge(
            self.host_badge, "已连接" if self._user else "未连接",
            "green" if self._user else "gray",
        )
        self._set_status(self.host_status, message, "error")
        self.result_box.setVisible(False)
        self.toast(message, "error")
        self._sync_controls()

    def close_room(self) -> None:
        if self._busy:
            worker = self._room_worker
            if worker:
                worker.cancel()
            self._room_worker = None
            self._busy = False
        process = self._tunnel_process
        self._closing = True
        self._tunnel_process = None
        if process is not None:
            process.blockSignals(True)
            process.stop()
        payload = self._room_payload
        if payload:
            remove_tunnel_config(payload.get("config_path"))
            self._clear_social_room()
        self._room_payload = None
        self.progress.setVisible(False)
        self._set_badge(
            self.host_badge, "已连接" if self._user else "未连接",
            "green" if self._user else "gray",
        )
        self._set_status(self.host_status, "房间已关闭。", "muted")
        self.result_box.setVisible(False)
        self._closing = False
        self._sync_controls()

    def _copy_room_code(self) -> None:
        code = self.room_code_label.text()
        if not code or code == "房间号不可用":
            self.toast("当前没有可复制的房间号", "info")
            return
        self._copy_text(code, "房间号")

    def _copy_host_address(self) -> None:
        if not self._room_payload:
            return
        self._copy_text(
            str(self._room_payload.get("address") or ""), "服务器地址"
        )

    def _sync_social_room(self, room_code: str) -> None:
        if not room_code:
            return
        try:
            self.main.social_store.set_room(
                room_code, str(self.main.selected_version or "")
            )
        except Exception:
            pass

    def _clear_social_room(self) -> None:
        try:
            self.main.social_store.set_room("", "")
        except Exception:
            pass

    def _copy_invite_card(self) -> None:
        payload = self._room_payload or {}
        code = str(payload.get("room_code") or payload.get("address") or "")
        if not code:
            self.toast("房间还没有准备好，稍等一下喵", "info")
            return
        try:
            profile = self.main.social_store.ensure_profile(
                self.main.display_name()
            )
            text = encode_invite(
                profile, code, str(self.main.selected_version or "")
            )
        except Exception as exc:
            self.toast(f"生成邀请卡失败：{exc}", "error")
            return
        self._copy_text(text, "好友邀请卡")

    def join_friend_room(self, room_code: str, version_id: str = "") -> None:
        """从好友簿进入房间：填好房间号和版本，直接走客人启动流程。"""
        if self.main.game_running:
            self._copy_text(room_code, "好友房间地址")
            return
        text = str(room_code or "").strip()
        if not text:
            self.toast("好友还没有分享房间号", "info")
            return
        self.code_edit.setText(text)
        self._format_code_field()
        self._update_code_preview()
        if version_id:
            index = self.version_combo.findData(version_id)
            if index < 0:
                self._set_status(
                    self.guest_status,
                    f"好友房间使用 {version_id}，你还没有安装这个版本。"
                    "请先安装相同版本再加入。",
                    "error",
                )
                self.toast(f"缺少版本 {version_id}", "error")
                return
            self.version_combo.setCurrentIndex(index)
        if not self._decoded_address:
            self._set_status(
                self.guest_status, "好友房间号无效，请让对方重新发一张邀请卡。", "error"
            )
            return
        self.join_room()

    # ------------------------------------------------------------------
    # 客机流程

    def _sync_versions(self) -> None:
        versions = list(self.main.game_version_summaries())
        current = self.version_combo.currentData() if self.version_combo.count() else ""
        preferred = str(current or self.main.selected_version or "")
        self.version_combo.blockSignals(True)
        self.version_combo.clear()
        for summary in versions:
            kind = summary.loader or summary.type_name or ""
            label = f"{summary.id} · {kind}" if kind else summary.id
            if summary.error:
                label += "（文件异常）"
            self.version_combo.addItem(label, summary.id)
        if versions:
            index = self.version_combo.findData(preferred)
            if index < 0:
                index = 0
            self.version_combo.setCurrentIndex(index)
        else:
            self.version_combo.addItem("未安装游戏版本", "")
        self.version_combo.blockSignals(False)
        self._sync_controls()

    def _sync_selection(self, version_id: str) -> None:
        if not version_id:
            return
        index = self.version_combo.findData(version_id)
        if index >= 0 and index != self.version_combo.currentIndex():
            self.version_combo.blockSignals(True)
            self.version_combo.setCurrentIndex(index)
            self.version_combo.blockSignals(False)

    def _update_code_preview(self) -> None:
        text = self.code_edit.text().strip()
        if not text:
            self._decoded_address = ""
            self.code_hint.setText(
                "也可以直接粘贴 host:port 形式的服务器地址。"
            )
            self._set_status(
                self.guest_status, "房主创建房间后，把房间号发给你即可。", "muted"
            )
            self._sync_controls()
            return
        try:
            host, port = decode_room_input(text)
        except SakuraError as exc:
            self._decoded_address = ""
            if len(re.sub(r"[\s\-_]", "", text)) >= 8:
                self.code_hint.setText(str(exc))
                self._set_status(self.guest_status, str(exc), "error")
            else:
                self.code_hint.setText("继续输入完整的 13 位房间号…")
                self._set_status(self.guest_status, "正在读取房间号…", "muted")
            self._sync_controls()
            return
        self._decoded_address = f"{host}:{port}"
        self.code_hint.setText(f"将连接 {self._decoded_address}")
        self._set_status(
            self.guest_status, "房间号有效，选择版本后即可启动。", "success"
        )
        self._sync_controls()

    def _format_code_field(self) -> None:
        text = self.code_edit.text().strip()
        if not text or ":" in text:
            return
        try:
            raw = normalize_room_code(text)
        except SakuraError:
            return
        formatted = f"{raw[:5]}-{raw[5:9]}-{raw[9:]}"
        if formatted != text:
            self.code_edit.setText(formatted)

    def _paste_code(self) -> None:
        text = QApplication.clipboard().text().strip()
        if not text:
            self.toast("剪贴板为空", "info")
            return
        self.code_edit.setText(text)
        self._format_code_field()
        self._update_code_preview()

    def _copy_guest_address(self) -> None:
        if not self._decoded_address:
            self._update_code_preview()
        if not self._decoded_address:
            self.toast("请先输入有效的房间号", "info")
            return
        self._copy_text(self._decoded_address, "服务器地址")

    def join_room(self) -> None:
        self._update_code_preview()
        if not self._decoded_address:
            self._set_status(
                self.guest_status, "请先输入有效的房间号。", "error"
            )
            return
        address = self._decoded_address
        if self.main.game_running:
            self._copy_text(address, "服务器地址")
            self._set_status(
                self.guest_status,
                "游戏已经在运行，地址已复制。请在多人游戏中添加服务器。",
                "warning",
            )
            return
        if self.main.install_active:
            self.toast("正在安装游戏文件，请稍候", "info")
            return
        version_id = str(self.version_combo.currentData() or "")
        if not version_id:
            self.toast("请先安装一个游戏版本", "info")
            return
        if version_id != self.main.selected_version:
            self.main.home_page.select_version(version_id)
        self._set_status(
            self.guest_status,
            f"正在启动 {version_id} 并连接 {address}…",
            "busy",
        )
        self.main.launch_selected(connect_address=address)
        if not self.main.game_running and not self.main.launch_active:
            self._set_status(
                self.guest_status,
                "启动没有完成，请查看启动器提示或运行日志。",
                "error",
            )

    def _copy_text(self, text: str, label: str) -> None:
        if not text:
            self.toast(f"没有可复制的{label}", "info")
            return
        QApplication.clipboard().setText(text)
        self.toast(f"{label}已复制", "success")

    # ------------------------------------------------------------------
    # 其他

    def _on_game_state(self, running: bool) -> None:
        if running:
            self.join_btn.setText("游戏运行中 · 复制地址")
        else:
            self.join_btn.setText("启动游戏并进入")
        self.detect_btn.setEnabled(running)
        if hasattr(self, "join_btn"):
            self.join_btn.setIcon(
                icons.icon("copy" if running else "play", "#FFFFFF", 16)
            )
        self._sync_controls()

    def _sync_controls(self) -> None:
        if not hasattr(self, "create_btn"):
            return
        room_active = self._tunnel_process is not None or self._room_payload is not None
        self.create_btn.setEnabled(
            bool(self._user) and not self._busy and not room_active
        )
        self.close_btn.setVisible(
            room_active or self._room_worker is not None
        )
        self.connect_btn.setEnabled(not self._busy and not room_active)
        self.token_edit.setEnabled(not room_active)
        self.node_combo.setEnabled(not room_active and bool(self._nodes))
        self.node_refresh.setEnabled(not self._busy and not room_active)
        self.port_spin.setEnabled(not room_active)
        self.detect_btn.setEnabled(not room_active and self.main.game_running)
        has_version = bool(self.version_combo.currentData())
        self.join_btn.setEnabled(has_version and not self._busy)
        self.copy_decode_btn.setEnabled(has_version)

    def toast(self, text: str, kind: str = "info") -> None:
        self.main.toast(text, kind)

    def _open_url(self, url: str) -> None:
        QDesktopServices.openUrl(QUrl(url))

    def shutdown(self) -> None:
        self._closing = True
        worker = self._room_worker
        if worker:
            worker.cancel()
            self._room_worker = None
        login = self._login_worker
        if login:
            self._login_worker = None
        process = self._tunnel_process
        self._tunnel_process = None
        if process is not None:
            process.blockSignals(True)
            process.stop()
        payload = self._room_payload
        if payload:
            remove_tunnel_config(payload.get("config_path"))
            self._clear_social_room()
        self._room_payload = None
