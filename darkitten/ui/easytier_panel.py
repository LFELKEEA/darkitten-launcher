"""EasyTier P2P 联机面板（Campfire 风格，无需樱花账户）。"""

from __future__ import annotations

import re
import threading

from PySide6.QtCore import Qt, QTimer
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
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..easytier import (
    PUBLIC_NODES,
    RoomCode,
    parse_room_code,
)
from ..sakura import detect_game_lan_ports, preferred_lan_port
from ..workers import EasyTierHostWorker, EasyTierJoinWorker
from . import icons
from .widgets import Card, badge, colors, hline, icon_button, repolish, Switch

STATUS_COLORS = {
    "muted": "muted",
    "busy": "accent",
    "success": "green",
    "warning": "orange",
    "error": "red",
}


class EasyTierPanel(QWidget):
    """P2P 联机主界面：左边开房间，右边加入房间。"""

    def __init__(self, main, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.main = main
        self._host_worker: EasyTierHostWorker | None = None
        self._join_worker: EasyTierJoinWorker | None = None
        self._host_log_worker: EasyTierHostWorker | None = None
        self._join_log_worker: EasyTierJoinWorker | None = None
        self._host_session = None
        self._guest_session = None
        self._busy = False
        self._host_status_kind = "muted"
        self._guest_status_kind = "muted"
        self._pending_version = ""
        self._closing = False
        self._build()
        self._sync_versions()
        self.main.versions_changed.connect(self._sync_versions)
        self.main.selection_changed.connect(self._sync_selection)
        self.main.game_state_changed.connect(self._on_game_state)
        self._host_timer = QTimer(self)
        self._host_timer.setInterval(1500)
        self._host_timer.timeout.connect(self._host_tick)
        self._guest_timer = QTimer(self)
        self._guest_timer.setInterval(2000)
        self._guest_timer.timeout.connect(self._join_tick)
        self._on_game_state(self.main.game_running)

    # ------------------------------------------------------------------
    # 界面

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(14)

        columns = QHBoxLayout()
        columns.setSpacing(14)
        self.host_card = self._build_host_card()
        self.guest_card = self._build_guest_card()
        columns.addWidget(self.host_card, 3)
        columns.addWidget(self.guest_card, 2)
        outer.addLayout(columns, 1)
        outer.addWidget(self._build_info_card())
        outer.addStretch(1)

    def _build_host_card(self) -> QWidget:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 15, 18, 15)
        layout.setSpacing(9)

        header = QHBoxLayout()
        title = QLabel("P2P 开房间")
        title.setObjectName("H2")
        header.addWidget(title)
        header.addStretch(1)
        self.host_badge = badge("未开启", "gray")
        header.addWidget(self.host_badge)
        layout.addLayout(header)

        sub = QLabel(
            "用 EasyTier 组建点对点虚拟网络，不需要樱花账号。"
            "房主保持游戏和启动器开启即可。"
        )
        sub.setObjectName("Tiny")
        sub.setWordWrap(True)
        layout.addWidget(sub)

        port_row = QHBoxLayout()
        port_row.setSpacing(8)
        port_label = QLabel("局域网端口")
        port_label.setStyleSheet("font-weight: 600;")
        port_row.addWidget(port_label)
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(
            int(getattr(self.main.config, "easytier_last_port", 25565) or 25565)
        )
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
            "进入单人游戏后按 Esc → 对局域网开放，再点“检测游戏端口”。"
        )
        port_hint.setObjectName("Tiny")
        port_hint.setWordWrap(True)
        layout.addWidget(port_hint)

        option_row = QHBoxLayout()
        option_row.setSpacing(8)
        self.direct_switch = Switch()
        self.direct_switch.setChecked(
            bool(getattr(self.main.config, "easytier_direct_only", False))
        )
        self.direct_switch.setToolTip(
            "打开后只允许玩家之间直接打洞，不走公共节点中继；"
            "NAT 严格时可能连不上。"
        )
        option_row.addWidget(self.direct_switch)
        direct_label = QLabel("只允许直连")
        direct_label.setObjectName("Tiny")
        option_row.addWidget(direct_label)
        option_row.addSpacing(10)
        self.hide_switch = Switch()
        self.hide_switch.setChecked(
            bool(getattr(self.main.config, "easytier_hide_address", False))
        )
        self.hide_switch.setToolTip(
            "只通过公共节点中继，不向其他玩家暴露本机地址；延迟会更高。"
        )
        option_row.addWidget(self.hide_switch)
        hide_label = QLabel("隐藏本机地址")
        hide_label.setObjectName("Tiny")
        option_row.addWidget(hide_label)
        option_row.addStretch(1)
        node_label = QLabel(f"公共节点 {len(PUBLIC_NODES)} 个")
        node_label.setObjectName("Tiny")
        node_label.setToolTip(
            "由社区维护的中继节点，用于帮助玩家互相发现和穿透 NAT。"
        )
        option_row.addWidget(node_label)
        layout.addLayout(option_row)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.create_btn = QPushButton("创建 P2P 房间")
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

        self.host_status = QLabel("打开游戏局域网后，点“创建 P2P 房间”。")
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

        self.log_box = QPlainTextEdit()
        self.log_box.setObjectName("SoftLog")
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumBlockCount(300)
        self.log_box.setFixedHeight(86)
        self.log_box.setPlaceholderText("EasyTier 的连接日志会显示在这里")
        self.log_box.setVisible(False)
        layout.addWidget(self.log_box)
        return card

    def _build_result_box(self) -> QWidget:
        box = QFrame()
        box.setObjectName("SoftCard")
        box.setAttribute(Qt.WA_StyledBackground, True)
        box.setVisible(False)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(14, 11, 14, 11)
        layout.setSpacing(4)

        caption = QLabel("房间号（发给朋友即可）")
        caption.setObjectName("Tiny")
        layout.addWidget(caption)

        code_row = QHBoxLayout()
        code_row.setSpacing(8)
        self.room_code_label = QLabel("—")
        self.room_code_label.setStyleSheet(
            "font-size: 22px; font-weight: 800; letter-spacing: 1px;"
        )
        self.room_code_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        code_row.addWidget(self.room_code_label, 1)
        self.copy_code_btn = QPushButton("复制房间号")
        self.copy_code_btn.setObjectName("Primary")
        self.copy_code_btn.setCursor(Qt.PointingHandCursor)
        self.copy_code_btn.setIcon(icons.icon("copy", "#FFFFFF", 14))
        self.copy_code_btn.clicked.connect(self._copy_room_code)
        code_row.addWidget(self.copy_code_btn)
        layout.addLayout(code_row)

        self.code_detail = QLabel("")
        self.code_detail.setObjectName("Tiny")
        self.code_detail.setWordWrap(True)
        self.code_detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.code_detail)

        self.player_line = QLabel("")
        self.player_line.setObjectName("Tiny")
        self.player_line.setWordWrap(True)
        layout.addWidget(self.player_line)
        return box

    def _build_guest_card(self) -> QWidget:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 15, 18, 15)
        layout.setSpacing(9)

        header = QHBoxLayout()
        title = QLabel("加入 P2P 房间")
        title.setObjectName("H2")
        header.addWidget(title)
        header.addStretch(1)
        self.guest_badge = badge("空闲", "gray")
        header.addWidget(self.guest_badge)
        layout.addLayout(header)

        sub = QLabel("粘贴房主发来的房间号，选择相同版本后自动组网并启动游戏。")
        sub.setObjectName("Tiny")
        sub.setWordWrap(True)
        layout.addWidget(sub)

        code_caption = QLabel("房间号")
        code_caption.setStyleSheet("font-weight: 600;")
        layout.addWidget(code_caption)

        code_row = QHBoxLayout()
        code_row.setSpacing(6)
        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText("U/XXXX-XXXX-XXXX-XXXX")
        self.code_edit.setClearButtonEnabled(True)
        self.code_edit.textChanged.connect(self._update_code_hint)
        self.code_edit.returnPressed.connect(self.join_room)
        code_row.addWidget(self.code_edit, 1)
        paste_btn = icon_button("copy", "从剪贴板粘贴房间号")
        paste_btn.clicked.connect(self._paste_code)
        code_row.addWidget(paste_btn)
        layout.addLayout(code_row)

        self.code_hint = QLabel("也可以直接粘贴一整段聊天消息，会自动找出房间号。")
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

        self.join_btn = QPushButton("组网并启动游戏")
        self.join_btn.setObjectName("Primary")
        self.join_btn.setCursor(Qt.PointingHandCursor)
        self.join_btn.setIcon(icons.icon("play", "#FFFFFF", 16))
        self.join_btn.clicked.connect(self.join_room)
        layout.addWidget(self.join_btn)

        self.copy_address_btn = QPushButton("复制本机连接地址")
        self.copy_address_btn.setObjectName("Ghost")
        self.copy_address_btn.setCursor(Qt.PointingHandCursor)
        self.copy_address_btn.setIcon(icons.icon("copy", colors()["text2"], 13))
        self.copy_address_btn.clicked.connect(self._copy_address)
        layout.addWidget(self.copy_address_btn)

        self.leave_btn = QPushButton("断开 P2P 连接")
        self.leave_btn.setObjectName("Danger")
        self.leave_btn.setCursor(Qt.PointingHandCursor)
        self.leave_btn.clicked.connect(self.close_guest)
        self.leave_btn.setVisible(False)
        layout.addWidget(self.leave_btn)

        self.guest_status = QLabel("房主创建房间后，把房间号发给你即可。")
        self.guest_status.setObjectName("Tiny")
        self.guest_status.setWordWrap(True)
        layout.addWidget(self.guest_status)

        layout.addStretch(1)
        note = QLabel(
            "首次使用会自动下载约 32 MB 的 EasyTier 组件。"
            "房间号相当于房间密码，只发给认识的朋友。"
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
        title = QLabel("P2P 联机怎么用")
        title.setObjectName("H2")
        header.addWidget(title)
        header.addWidget(badge("点对点 · 公共节点辅助", "blue"))
        header.addStretch(1)
        layout.addLayout(header)

        steps = QHBoxLayout()
        steps.setSpacing(10)
        step_data = [
            ("1", "房主开放世界", "进入单人游戏，按 Esc → 对局域网开放，记下端口。"),
            ("2", "创建 P2P 房间", "填写端口并创建，把生成的 U/ 房间号发给朋友。"),
            ("3", "朋友输入房间号", "选择相同版本后加入，启动器会自动组网并进入游戏。"),
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

        note = QLabel(
            "P2P 模式不需要樱花账户，但需要能连上至少一个公共节点。"
            "如果双方都在严格 NAT 后面，可能要等待几秒钟打洞，"
            "或允许通过公共节点中继。公共节点由社区维护，"
            "仅适合朋友之间私下联机。"
        )
        note.setObjectName("Tiny")
        note.setWordWrap(True)
        layout.addWidget(note)
        return card

    # ------------------------------------------------------------------
    # 状态与主题

    def _set_status(self, label: QLabel, text: str, kind: str = "muted") -> None:
        c = colors()
        label.setStyleSheet(
            f"color: {c[STATUS_COLORS.get(kind, 'muted')]};"
            "font-size: 11px; background: transparent;"
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
        self.copy_address_btn.setIcon(
            icons.icon("copy", colors()["text2"], 13)
        )
        for label in self.findChildren(QLabel):
            if label.property("badge"):
                repolish(label)

    # ------------------------------------------------------------------
    # 版本

    def _sync_versions(self) -> None:
        versions = list(self.main.game_version_summaries())
        current = (
            self.version_combo.currentData() if self.version_combo.count() else ""
        )
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

    # ------------------------------------------------------------------
    # 房主流程

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
        text = f"已检测到游戏端口 {port}，可以创建 P2P 房间。"
        if others:
            text += f"（其他监听端口：{', '.join(others[:4])}）"
        self._set_status(self.host_status, text, "success")

    def create_room(self) -> None:
        if self._busy:
            return
        if self._host_session is not None:
            self.toast("P2P 房间已经开启，请先关闭当前房间。", "info")
            return
        port = int(self.port_spin.value())
        config = self.main.config
        config.easytier_last_port = port
        config.easytier_direct_only = self.direct_switch.isChecked()
        config.easytier_hide_address = self.hide_switch.isChecked()
        config.save()

        self._busy = True
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.log_box.clear()
        self.log_box.setVisible(True)
        self._set_status(self.host_status, "正在准备 EasyTier 联机组件…", "busy")
        self._set_badge(self.host_badge, "准备中", "blue")
        self.result_box.setVisible(False)
        self._sync_controls()

        worker = EasyTierHostWorker(
            port,
            self.main.display_name(),
            machine_id=str(config.easytier_machine_id or ""),
            node_ids=list(config.easytier_node_ids or []),
            direct_only=config.easytier_direct_only,
            hide_address=config.easytier_hide_address,
            parent=self,
        )
        self._host_worker = worker
        self._host_log_worker = worker
        worker.progress.connect(
            lambda value, message, w=worker: self._host_progress(w, value, message)
        )
        worker.log_line.connect(
            lambda line, w=worker: self._host_log(w, line)
        )
        worker.ready.connect(
            lambda session, error, w=worker: self._host_ready(w, session, error)
        )
        worker.failed.connect(
            lambda error, w=worker: self._host_failed(w, error)
        )
        worker.cancelled.connect(
            lambda w=worker: self._host_cancelled(w)
        )
        worker.start()

    def _host_progress(
        self, worker: EasyTierHostWorker, value: int, message: str
    ) -> None:
        if self._host_worker is not worker:
            return
        self.progress.setValue(int(value))
        self._set_status(self.host_status, message, "busy")

    def _host_log(self, worker: EasyTierHostWorker, line: str) -> None:
        if self._host_log_worker is not worker:
            return
        self._append_log(line)

    @staticmethod
    def _dispose_session(session, wait: bool = False) -> None:
        if session is None:
            return
        if wait:
            try:
                session.stop()
            except Exception:
                pass
            return
        threading.Thread(target=session.stop, daemon=True).start()

    def _host_ready(
        self, worker: EasyTierHostWorker, session, error: str
    ) -> None:
        if self._host_worker is not worker:
            # 用户已经关闭房间或退出启动器，迟到的会话要立即回收，
            # 否则 EasyTier 进程和房间服务会一直留在后台。
            self._dispose_session(session)
            return
        self._host_worker = None
        self._busy = False
        if error or session is None:
            self.progress.setVisible(False)
            self._set_badge(self.host_badge, "未开启", "gray")
            self._set_status(
                self.host_status, error or "创建 P2P 房间失败。", "error"
            )
            self.toast(error or "创建 P2P 房间失败", "error")
            self._sync_controls()
            return

        self._host_session = session
        self.progress.setValue(100)
        self.progress.setVisible(False)
        self._set_badge(self.host_badge, "房间开启", "green")
        self._set_status(
            self.host_status,
            "P2P 房间已开启。把房间号发给朋友，保持游戏和启动器运行。",
            "success",
        )
        self.room_code_label.setText(session.code.code)
        self.code_detail.setText(
            f"房间服务端口 {session.service_port} · "
            f"游戏端口 {self.port_spin.value()} · "
            f"公共节点 {max(0, session.connected_nodes)} 个"
        )
        self.player_line.setText("房间玩家：你自己")
        self.result_box.setVisible(True)
        self._host_timer.start()
        self._sync_controls()
        self.toast("P2P 房间已创建", "success")

    def _host_failed(self, worker: EasyTierHostWorker, error: str) -> None:
        if self._host_worker is not worker:
            return
        self._host_worker = None
        self._busy = False
        self.progress.setVisible(False)
        self._set_badge(self.host_badge, "未开启", "gray")
        self._set_status(self.host_status, error or "创建 P2P 房间失败。", "error")
        self.toast(error or "创建 P2P 房间失败", "error")
        self._sync_controls()

    def _host_cancelled(self, worker: EasyTierHostWorker) -> None:
        if self._host_worker is not worker:
            return
        self._host_worker = None
        self._busy = False
        self.progress.setVisible(False)
        self._set_badge(self.host_badge, "未开启", "gray")
        self._set_status(self.host_status, "已取消创建房间。", "muted")
        self._sync_controls()

    def _host_tick(self) -> None:
        session = self._host_session
        if session is None:
            self._host_timer.stop()
            return
        if session.lost:
            self._host_timer.stop()
            message = session.error or "EasyTier 网络已断开。"
            self._host_session = None
            self._dispose_session(session)
            self._busy = False
            self.progress.setVisible(False)
            self._set_badge(self.host_badge, "未开启", "gray")
            self._set_status(self.host_status, message, "error")
            self.result_box.setVisible(False)
            self.toast(message, "error")
            self._sync_controls()
            return
        players = list(session.players or [])
        names = "、".join(
            (profile.name or "玩家")
            + ("（房主）" if profile.kind == "HOST" else "")
            for profile in players[:6]
        )
        if len(players) > 6:
            names += f" 等 {len(players)} 人"
        self.player_line.setText(
            f"房间玩家：{names or '你自己'} · "
            f"直连 {session.direct_peers} · 中继 {session.relayed_peers} · "
            f"公共节点 {max(0, session.connected_nodes)}"
        )
        self.code_detail.setText(
            f"房间服务端口 {session.service_port} · "
            f"游戏端口 {self.port_spin.value()} · "
            f"公共节点 {max(0, session.connected_nodes)} 个"
        )

    def close_room(self, checked: bool = False, message: str = "") -> None:
        worker = self._host_worker
        if worker is not None:
            worker.cancel()
            self._host_worker = None
        session = self._host_session
        self._host_session = None
        self._host_log_worker = None
        self._dispose_session(session)
        self._host_timer.stop()
        self._busy = False
        self.progress.setVisible(False)
        self.result_box.setVisible(False)
        self.log_box.setVisible(False)
        self._set_badge(self.host_badge, "未开启", "gray")
        self._set_status(
            self.host_status, message or "P2P 房间已关闭。", "muted"
        )
        self._sync_controls()

    # ------------------------------------------------------------------
    # 加入流程

    def _parse_code(self) -> RoomCode | None:
        text = self.code_edit.text().strip()
        if not text:
            return None
        try:
            return parse_room_code(text)
        except ValueError:
            return None

    def _update_code_hint(self) -> None:
        text = self.code_edit.text().strip()
        if not text:
            self.code_hint.setText(
                "也可以直接粘贴一整段聊天消息，会自动找出房间号。"
            )
            return
        try:
            code = parse_room_code(text)
        except ValueError:
            self.code_hint.setText("继续输入完整的 U/XXXX-XXXX-XXXX-XXXX 房间号…")
            return
        self.code_hint.setText(f"将加入房间 {code.code}")
        if text != code.code:
            # 粘贴整段聊天消息时，把输入框整理成标准房间号。
            self.code_edit.blockSignals(True)
            self.code_edit.setText(code.code)
            self.code_edit.blockSignals(False)

    def _paste_code(self) -> None:
        text = QApplication.clipboard().text().strip()
        if not text:
            self.toast("剪贴板为空", "info")
            return
        self.code_edit.setText(text)
        self._update_code_hint()

    def join_room(self) -> None:
        if self._busy:
            return
        if self._guest_session is not None:
            self.toast("已经连接到 P2P 房间。", "info")
            return
        code = self._parse_code()
        if code is None:
            self._set_status(
                self.guest_status,
                "请先输入有效的 P2P 房间号（U/ 开头）。",
                "error",
            )
            return
        version_id = str(self.version_combo.currentData() or "")
        if not version_id:
            self.toast("请先安装一个游戏版本", "info")
            return
        if self.main.game_running:
            self._set_status(
                self.guest_status,
                "游戏已经在运行。请先退出游戏，再加入 P2P 房间。",
                "warning",
            )
            return
        if self.main.install_active:
            self.toast("正在安装游戏文件，请稍候", "info")
            return

        config = self.main.config
        self._pending_version = version_id
        self._busy = True
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.log_box.clear()
        self.log_box.setVisible(True)
        self._set_status(self.guest_status, "正在准备 EasyTier 联机组件…", "busy")
        self._set_badge(self.guest_badge, "连接中", "blue")
        self._sync_controls()

        worker = EasyTierJoinWorker(
            code,
            self.main.display_name(),
            machine_id=str(config.easytier_machine_id or ""),
            node_ids=list(config.easytier_node_ids or []),
            allow_relay=not bool(config.easytier_direct_only),
            hide_address=bool(config.easytier_hide_address),
            search_timeout=120.0,
            parent=self,
        )
        self._join_worker = worker
        self._join_log_worker = worker
        worker.progress.connect(
            lambda value, message, w=worker: self._join_progress(w, value, message)
        )
        worker.log_line.connect(
            lambda line, w=worker: self._join_log(w, line)
        )
        worker.ready.connect(
            lambda session, error, w=worker: self._join_ready(w, session, error)
        )
        worker.failed.connect(
            lambda error, w=worker: self._join_failed(w, error)
        )
        worker.cancelled.connect(
            lambda w=worker: self._join_cancelled(w)
        )
        worker.start()

    def _join_progress(
        self, worker: EasyTierJoinWorker, value: int, message: str
    ) -> None:
        if self._join_worker is not worker:
            return
        self.progress.setValue(int(value))
        self._set_status(self.guest_status, message, "busy")

    def _join_log(self, worker: EasyTierJoinWorker, line: str) -> None:
        if self._join_log_worker is not worker:
            return
        self._append_log(line)

    def _join_ready(
        self, worker: EasyTierJoinWorker, session, error: str
    ) -> None:
        if self._join_worker is not worker:
            self._dispose_session(session)
            return
        self._join_worker = None
        self._busy = False
        if error or session is None:
            self.progress.setVisible(False)
            self._set_badge(self.guest_badge, "空闲", "gray")
            self._set_status(
                self.guest_status, error or "加入 P2P 房间失败。", "error"
            )
            self.toast(error or "加入 P2P 房间失败", "error")
            self._sync_controls()
            return

        self._guest_session = session
        self.progress.setValue(100)
        self.progress.setVisible(False)
        self._set_badge(self.guest_badge, "已连接", "green")
        self._set_status(
            self.guest_status,
            f"已连接房主，正在启动游戏并连接 {session.address}…",
            "success",
        )
        self._guest_timer.start()
        self._sync_controls()

        version_id = self._pending_version or str(
            self.version_combo.currentData() or ""
        )
        if version_id != self.main.selected_version:
            self.main.home_page.select_version(version_id)
        self.main.launch_selected(connect_address=session.address)
        if not self.main.game_running and not self.main.launch_active:
            self._set_status(
                self.guest_status,
                "组网成功，但游戏启动没有完成。请查看启动器提示，"
                f"或手动连接 {session.address}。",
                "warning",
            )

    def _join_failed(self, worker: EasyTierJoinWorker, error: str) -> None:
        if self._join_worker is not worker:
            return
        self._join_worker = None
        self._busy = False
        self.progress.setVisible(False)
        self._set_badge(self.guest_badge, "未开启", "gray")
        self._set_status(
            self.guest_status, error or "加入 P2P 房间失败。", "error"
        )
        self.toast(error or "加入 P2P 房间失败", "error")
        self._sync_controls()

    def _join_cancelled(self, worker: EasyTierJoinWorker) -> None:
        if self._join_worker is not worker:
            return
        self._join_worker = None
        self._busy = False
        self.progress.setVisible(False)
        self._set_badge(self.guest_badge, "未开启", "gray")
        self._set_status(self.guest_status, "已取消加入房间。", "muted")
        self._sync_controls()

    def _join_tick(self) -> None:
        session = self._guest_session
        if session is None:
            self._guest_timer.stop()
            return
        if session.lost:
            self._guest_timer.stop()
            message = session.error or "与房主的连接已断开。"
            self._guest_session = None
            self._dispose_session(session)
            self.progress.setVisible(False)
            self._set_badge(self.guest_badge, "未开启", "gray")
            self._set_status(self.guest_status, message, "error")
            self.toast(message, "error")
            self._sync_controls()
            return
        players = list(session.players or [])
        names = "、".join(profile.name or "玩家" for profile in players[:6])
        latency = (
            f" · 延迟 {session.host_latency_ms:.0f} ms"
            if session.host_latency_ms is not None
            else ""
        )
        self._set_status(
            self.guest_status,
            f"已连接 {session.address}{latency}"
            + (f" · 房间玩家：{names}" if names else ""),
            "success",
        )

    def _copy_room_code(self) -> None:
        session = self._host_session
        if session is None:
            self.toast("当前没有可复制的房间号", "info")
            return
        QApplication.clipboard().setText(session.code.code)
        self.toast("房间号已复制", "success")

    def close_guest(self, checked: bool = False) -> None:
        worker = self._join_worker
        if worker is not None:
            worker.cancel()
            self._join_worker = None
        session = self._guest_session
        self._guest_session = None
        self._join_log_worker = None
        self._dispose_session(session)
        self._guest_timer.stop()
        self._busy = False
        self.progress.setVisible(False)
        self._set_badge(self.guest_badge, "空闲", "gray")
        self._set_status(
            self.guest_status, "已断开 P2P 连接。", "muted"
        )
        self._sync_controls()

    def _copy_address(self) -> None:
        session = self._guest_session
        if session is None:
            self.toast("加入房间后才能复制连接地址", "info")
            return
        QApplication.clipboard().setText(session.address)
        self.toast("连接地址已复制", "success")

    # ------------------------------------------------------------------
    # 其他

    def _append_log(self, line: str) -> None:
        text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", str(line or "")).strip()
        if not text:
            return
        if not self.log_box.isVisible():
            self.log_box.setVisible(True)
        self.log_box.appendPlainText(text)
        bar = self.log_box.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _on_game_state(self, running: bool) -> None:
        self.detect_btn.setEnabled(running)
        if not running and self._host_session is not None:
            self._append_log("游戏已退出，正在关闭 P2P 房间。")
            self.close_room(message="游戏已退出，P2P 房间已关闭。")
        self._sync_controls()

    def _sync_controls(self) -> None:
        if not hasattr(self, "create_btn"):
            return
        room_active = (
            self._host_session is not None or self._host_worker is not None
        )
        guest_active = (
            self._guest_session is not None or self._join_worker is not None
        )
        self.create_btn.setEnabled(not self._busy and not room_active)
        self.close_btn.setVisible(room_active)
        self.direct_switch.setEnabled(not room_active and not self._busy)
        self.hide_switch.setEnabled(not room_active and not self._busy)
        self.port_spin.setEnabled(not room_active and not self._busy)
        self.detect_btn.setEnabled(
            not room_active and not self._busy and self.main.game_running
        )
        has_version = bool(self.version_combo.currentData())
        self.join_btn.setEnabled(has_version and not self._busy and not guest_active)
        self.join_btn.setText(
            "已连接房主" if self._guest_session is not None else "组网并启动游戏"
        )
        self.code_edit.setEnabled(not guest_active and not self._busy)
        self.copy_address_btn.setEnabled(self._guest_session is not None)
        self.leave_btn.setVisible(guest_active)

    def on_show(self) -> None:
        self._sync_versions()

    def toast(self, text: str, kind: str = "info") -> None:
        self.main.toast(text, kind)

    def shutdown(self) -> None:
        self._closing = True
        self._host_timer.stop()
        self._guest_timer.stop()
        host_worker = self._host_worker
        if host_worker is not None:
            host_worker.cancel()
            self._host_worker = None
        self._host_log_worker = None
        join_worker = self._join_worker
        if join_worker is not None:
            join_worker.cancel()
            self._join_worker = None
        self._join_log_worker = None
        host_session = self._host_session
        self._host_session = None
        # 退出时同步收尾，避免启动器进程结束后还留下 easytier-core。
        self._dispose_session(host_session, wait=True)
        guest_session = self._guest_session
        self._guest_session = None
        self._dispose_session(guest_session, wait=True)
