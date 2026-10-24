from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..social import Invite, SocialError, SocialStore, encode_invite, parse_invite
from . import icons
from .dialogs import ConfirmDialog, StyledDialog
from .widgets import Avatar, Card, colors, hline, icon_button, repolish


class NicknameDialog(StyledDialog):
    def __init__(self, parent, value: str) -> None:
        super().__init__(parent, "修改 DKL 昵称", "edit", width=420)
        self._value = value
        text = QLabel("这个昵称只用于好友邀请卡，不会改变游戏内玩家名称。")
        text.setObjectName("DialogText")
        text.setWordWrap(True)
        self.body.addWidget(text)

        self.edit = QLineEdit(value)
        self.edit.setMaxLength(24)
        self.edit.setPlaceholderText("1-24 个字符")
        self.edit.returnPressed.connect(self._save)
        self.body.addWidget(self.edit)

        self.error = QLabel("")
        self.error.setStyleSheet(f"color: {colors()['red']}; font-size: 11px;")
        self.error.setVisible(False)
        self.body.addWidget(self.error)

        self.add_button("取消", "Secondary", self.reject)
        self.add_button("保存", "Primary", self._save)
        self.edit.setFocus()
        self.edit.selectAll()

    def _save(self) -> None:
        value = self.edit.text().strip()
        if not 1 <= len(value) <= 24:
            self.error.setText("昵称需要 1-24 个字符。")
            self.error.setVisible(True)
            return
        self._value = value
        self.accept()

    def value(self) -> str:
        return self._value


class FriendRow(QFrame):
    join_requested = Signal(str, str)
    delete_requested = Signal(str)
    copy_requested = Signal(str)

    def __init__(self, friend, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.friend = friend
        self.setObjectName("SelectRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(62)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 7, 8, 7)
        layout.setSpacing(10)

        initial = QLabel((friend.name or "?")[:1].upper())
        initial.setAlignment(Qt.AlignCenter)
        initial.setFixedSize(38, 38)
        initial.setStyleSheet(
            f"background: {colors()['accent_soft']}; color: {colors()['accent']};"
            "border-radius: 12px; font-size: 16px; font-weight: 800;"
        )
        layout.addWidget(initial)

        info = QVBoxLayout()
        info.setSpacing(1)
        title = QHBoxLayout()
        title.setSpacing(6)
        name = QLabel(friend.name)
        name.setStyleSheet("font-weight: 700; font-size: 12px;")
        title.addWidget(name)
        id_label = QLabel(friend.id)
        id_label.setObjectName("Tiny")
        title.addWidget(id_label)
        title.addStretch(1)
        info.addLayout(title)

        if friend.room_code:
            room = QLabel(
                f"房间 {friend.room_code}"
                + (f" · {friend.room_version}" if friend.room_version else "")
            )
            room.setStyleSheet(f"color: {colors()['green']}; font-size: 10px;")
            room.setToolTip("点击“进入房间”会启动游戏并直接连接这个地址")
        else:
            room = QLabel("还没有房间号，让好友再发一张带房间的邀请卡")
            room.setObjectName("Tiny")
        info.addWidget(room)
        layout.addLayout(info, 1)

        copy_btn = icon_button("copy", "复制好友 ID", size=28, icon_size=14)
        copy_btn.clicked.connect(
            lambda _=False: self.copy_requested.emit(self.friend.id)
        )
        layout.addWidget(copy_btn)

        self.join_btn = QPushButton("进入房间")
        self.join_btn.setObjectName("Soft")
        self.join_btn.setCursor(Qt.PointingHandCursor)
        self.join_btn.setFixedHeight(28)
        self.join_btn.setIcon(icons.icon("play", colors()["green"], 13))
        self.join_btn.setEnabled(bool(friend.room_code))
        self.join_btn.setToolTip(
            "启动游戏并直接进入好友房间"
            if friend.room_code
            else "好友还没有分享房间号"
        )
        self.join_btn.clicked.connect(
            lambda _=False: self.join_requested.emit(
                self.friend.room_code, self.friend.room_version
            )
        )
        layout.addWidget(self.join_btn)

        delete_btn = icon_button("trash", "删除好友", colors()["muted"], 28, 14)
        delete_btn.clicked.connect(
            lambda _=False: self.delete_requested.emit(self.friend.id)
        )
        layout.addWidget(delete_btn)


class SocialDialog(StyledDialog):
    join_requested = Signal(str, str)

    def __init__(self, main) -> None:
        super().__init__(main, "个人资料与好友", "users", width=680)
        self.main = main
        self.store: SocialStore = main.social_store
        self._manual_visible = False

        intro = QLabel(
            "每个人都有一个本地生成的 DKL 虚拟身份。把邀请卡发给朋友，"
            "对方导入后就能从好友簿一键启动并进入你的联机房间。"
        )
        intro.setObjectName("DialogText")
        intro.setWordWrap(True)
        self.body.addWidget(intro)

        self.body.addWidget(self._build_profile_card())
        self.body.addWidget(self._build_add_card())
        self.body.addWidget(self._build_friends_card())

        note = QLabel(
            "好友和邀请卡只保存在本机，不会上传到服务器；请通过聊天工具自行交换邀请卡。"
            "房间号会过期，房主重新开房后请再发一张新的邀请卡。"
        )
        note.setObjectName("Tiny")
        note.setWordWrap(True)
        self.body.addWidget(note)

        self.add_button("关闭", "Secondary", self.reject)
        self._refresh_profile()
        self._refresh_friends()
        self.adjustSize()

    # ------------------------------------------------------------------
    def _build_profile_card(self) -> QWidget:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 13, 16, 13)
        layout.setSpacing(9)

        header = QHBoxLayout()
        title = QLabel("我的 DKL 身份")
        title.setObjectName("H2")
        header.addWidget(title)
        header.addStretch(1)
        self.room_badge = QLabel("未开房")
        self.room_badge.setProperty("badge", "gray")
        header.addWidget(self.room_badge)
        layout.addLayout(header)

        row = QHBoxLayout()
        row.setSpacing(12)
        self.profile_avatar = Avatar(54)
        row.addWidget(self.profile_avatar)

        info = QVBoxLayout()
        info.setSpacing(2)
        self.profile_name = QLabel("")
        self.profile_name.setStyleSheet("font-size: 17px; font-weight: 800;")
        self.profile_id = QLabel("")
        self.profile_id.setObjectName("Tiny")
        self.profile_id.setTextInteractionFlags(Qt.TextSelectableByMouse)
        info.addWidget(self.profile_name)
        info.addWidget(self.profile_id)
        self.room_line = QLabel("")
        self.room_line.setObjectName("Tiny")
        self.room_line.setWordWrap(True)
        info.addWidget(self.room_line)
        row.addLayout(info, 1)

        edit_btn = icon_button("edit", "修改 DKL 昵称")
        edit_btn.clicked.connect(self._edit_name)
        row.addWidget(edit_btn, 0, Qt.AlignTop)
        layout.addLayout(row)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.copy_invite_btn = QPushButton("复制我的好友邀请卡")
        self.copy_invite_btn.setObjectName("Primary")
        self.copy_invite_btn.setCursor(Qt.PointingHandCursor)
        self.copy_invite_btn.setIcon(icons.icon("users", "#FFFFFF", 15))
        self.copy_invite_btn.clicked.connect(self._copy_invite)
        actions.addWidget(self.copy_invite_btn)

        copy_id_btn = QPushButton("复制 DKL ID")
        copy_id_btn.setObjectName("Ghost")
        copy_id_btn.setCursor(Qt.PointingHandCursor)
        copy_id_btn.setIcon(icons.icon("copy", colors()["text2"], 14))
        copy_id_btn.clicked.connect(
            lambda: self._copy_text(self.store.profile.id, "DKL ID")
        )
        actions.addWidget(copy_id_btn)
        actions.addStretch(1)
        layout.addLayout(actions)
        return card

    def _build_add_card(self) -> QWidget:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 13, 16, 13)
        layout.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("添加好友")
        title.setObjectName("H2")
        header.addWidget(title)
        header.addStretch(1)
        self.manual_btn = QPushButton("手动填写")
        self.manual_btn.setObjectName("Ghost")
        self.manual_btn.setCursor(Qt.PointingHandCursor)
        self.manual_btn.clicked.connect(self._toggle_manual)
        header.addWidget(self.manual_btn)
        layout.addLayout(header)

        self.invite_edit = QLineEdit()
        self.invite_edit.setPlaceholderText("粘贴好友发来的 DKLF1:... 邀请卡")
        self.invite_edit.returnPressed.connect(self._add_from_invite)
        self.invite_edit.textChanged.connect(self._update_add_state)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.invite_edit, 1)
        paste_btn = QPushButton("粘贴")
        paste_btn.setObjectName("Soft")
        paste_btn.setCursor(Qt.PointingHandCursor)
        paste_btn.clicked.connect(self._paste_invite)
        row.addWidget(paste_btn)
        self.add_btn = QPushButton("添加好友")
        self.add_btn.setObjectName("Primary")
        self.add_btn.setCursor(Qt.PointingHandCursor)
        self.add_btn.clicked.connect(self._add_from_invite)
        row.addWidget(self.add_btn)
        layout.addLayout(row)

        self.manual_box = QWidget()
        manual = QVBoxLayout(self.manual_box)
        manual.setContentsMargins(0, 2, 0, 0)
        manual.setSpacing(6)
        manual_row = QHBoxLayout()
        manual_row.setSpacing(6)
        self.manual_name = QLineEdit()
        self.manual_name.setPlaceholderText("好友昵称")
        self.manual_id = QLineEdit()
        self.manual_id.setPlaceholderText("DKL-XXXXXXXX")
        self.manual_room = QLineEdit()
        self.manual_room.setPlaceholderText("房间号或 host:port（可留空）")
        manual_row.addWidget(self.manual_name, 1)
        manual_row.addWidget(self.manual_id, 1)
        manual_row.addWidget(self.manual_room, 1)
        manual.addLayout(manual_row)
        manual_actions = QHBoxLayout()
        manual_actions.addStretch(1)
        manual_add = QPushButton("按以上信息添加")
        manual_add.setObjectName("Soft")
        manual_add.setCursor(Qt.PointingHandCursor)
        manual_add.clicked.connect(self._add_manual)
        manual_actions.addWidget(manual_add)
        manual.addLayout(manual_actions)
        self.manual_box.setVisible(False)
        layout.addWidget(self.manual_box)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet(
            f"color: {colors()['red']}; font-size: 11px;"
        )
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)
        return card

    def _build_friends_card(self) -> QWidget:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 13, 16, 13)
        layout.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("好友簿")
        title.setObjectName("H2")
        header.addWidget(title)
        self.friend_count = QLabel("0 位好友")
        self.friend_count.setProperty("badge", "blue")
        header.addWidget(self.friend_count)
        header.addStretch(1)
        layout.addLayout(header)
        layout.addWidget(hline())

        self.friend_scroll = QScrollArea()
        self.friend_scroll.setWidgetResizable(True)
        self.friend_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.friend_scroll.setFixedHeight(190)
        self.friend_holder = QWidget()
        self.friend_layout = QVBoxLayout(self.friend_holder)
        self.friend_layout.setContentsMargins(0, 0, 4, 0)
        self.friend_layout.setSpacing(5)
        self.friend_layout.addStretch(1)
        self.friend_scroll.setWidget(self.friend_holder)
        layout.addWidget(self.friend_scroll)

        self.empty_label = QLabel(
            "好友簿还是空的。开一间房，复制邀请卡发给朋友；"
            "或者把朋友发来的邀请卡粘贴到上面。"
        )
        self.empty_label.setObjectName("Tiny")
        self.empty_label.setWordWrap(True)
        self.empty_label.setVisible(False)
        layout.addWidget(self.empty_label)
        return card

    # ------------------------------------------------------------------
    def _refresh_profile(self) -> None:
        profile = self.store.profile
        self.profile_name.setText(profile.name)
        self.profile_id.setText(f"DKL ID · {profile.id}")
        if profile.room_code:
            self.room_badge.setText("房间已分享")
            self.room_badge.setProperty("badge", "green")
            self.room_line.setText(
                f"当前房间：{profile.room_code}"
                + (f" · {profile.room_version}" if profile.room_version else "")
                + "（邀请卡会自动带上）"
            )
        else:
            self.room_badge.setText("未开房")
            self.room_badge.setProperty("badge", "gray")
            self.room_line.setText(
                "还没有房间号。在“联机”页创建房间后，邀请卡会自动带上房间号。"
            )
        repolish(self.room_badge)
        self.profile_avatar.refresh()

    def _refresh_friends(self) -> None:
        while self.friend_layout.count() > 1:
            item = self.friend_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        friends = list(self.store.friends)
        self.friend_count.setText(f"{len(friends)} 位好友")
        self.empty_label.setVisible(not friends)
        self.friend_scroll.setVisible(bool(friends))
        for friend in friends:
            row = FriendRow(friend)
            row.join_requested.connect(self._join_friend)
            row.delete_requested.connect(self._delete_friend)
            row.copy_requested.connect(
                lambda friend_id: self._copy_text(friend_id, "好友 ID")
            )
            self.friend_layout.insertWidget(self.friend_layout.count() - 1, row)

    def _update_add_state(self) -> None:
        text = self.invite_edit.text().strip()
        self.add_btn.setEnabled(text.startswith("DKLF1:"))
        if text:
            self.error_label.setVisible(False)

    def _toggle_manual(self) -> None:
        self._manual_visible = not self._manual_visible
        self.manual_box.setVisible(self._manual_visible)
        self.manual_btn.setText("收起手动填写" if self._manual_visible else "手动填写")
        self.adjustSize()

    def _paste_invite(self) -> None:
        text = QApplication.clipboard().text().strip()
        if not text:
            self._show_error("剪贴板为空。")
            return
        self.invite_edit.setText(text)

    def _add_from_invite(self) -> None:
        try:
            invite: Invite = parse_invite(self.invite_edit.text())
            friend = self.store.add_invite(invite)
        except SocialError as exc:
            self._show_error(str(exc))
            return
        self.invite_edit.clear()
        self.error_label.setVisible(False)
        self._refresh_friends()
        self.main.toast(f"已添加好友 {friend.name}", "success")
        if friend.room_code:
            self.main.toast(f"{friend.name} 分享了房间，点“进入房间”即可", "info")

    def _add_manual(self) -> None:
        try:
            friend = self.store.add_friend(
                self.manual_name.text(),
                self.manual_id.text(),
                self.manual_room.text(),
            )
        except SocialError as exc:
            self._show_error(str(exc))
            return
        self.manual_name.clear()
        self.manual_id.clear()
        self.manual_room.clear()
        self.error_label.setVisible(False)
        self._refresh_friends()
        self.main.toast(f"已添加好友 {friend.name}", "success")

    def _show_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.setVisible(True)
        self.adjustSize()

    def _edit_name(self) -> None:
        dialog = NicknameDialog(self, self.store.profile.name)
        if not dialog.exec():
            return
        try:
            self.store.rename_profile(dialog.value())
        except SocialError as exc:
            self._show_error(str(exc))
            return
        self._refresh_profile()
        self.main.toast("DKL 昵称已更新", "success")

    def _copy_invite(self) -> None:
        text = encode_invite(
            self.store.profile,
            self.store.profile.room_code,
            self.store.profile.room_version,
        )
        self._copy_text(text, "好友邀请卡")

    def _copy_text(self, text: str, label: str) -> None:
        if not text:
            self.main.toast(f"没有可复制的{label}", "info")
            return
        QApplication.clipboard().setText(text)
        self.main.toast(f"{label}已复制", "success")

    def _join_friend(self, room_code: str, room_version: str) -> None:
        if not room_code:
            self.main.toast("好友还没有分享房间号", "info")
            return
        self.join_requested.emit(room_code, room_version)
        self.accept()

    def _delete_friend(self, friend_id: str) -> None:
        friend = self.store.find(friend_id)
        if friend is None:
            return
        dialog = ConfirmDialog(
            self,
            "删除好友",
            f"从好友簿中移除 {friend.name}？不会删除对方的任何文件。",
            primary_text="移除",
            secondary_text="取消",
            icon_name="trash",
            icon_color=colors()["red"],
        )
        if not dialog.exec():
            return
        self.store.remove_friend(friend_id)
        self._refresh_friends()
        self.main.toast("好友已移除", "info")

    def refresh(self) -> None:
        self._refresh_profile()
        self._refresh_friends()
