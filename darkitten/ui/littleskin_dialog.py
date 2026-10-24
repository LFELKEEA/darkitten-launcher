from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QWidget

from ..littleskin import REGISTER_URL
from ..workers import LittleSkinLoginWorker
from . import icons
from .dialogs import StyledDialog
from .widgets import colors


class LittleSkinLoginDialog(StyledDialog):
    """LittleSkin 账号登录：密码只用于本次请求，不写入配置。"""

    def __init__(
        self,
        parent: QWidget | None,
        username: str = "",
        client_token: str = "",
    ) -> None:
        super().__init__(parent, "登录 LittleSkin", "globe", width=470)
        self.account = None
        self.skin = None
        self._worker: LittleSkinLoginWorker | None = None
        self._busy = False
        self._client_token = str(client_token or "")

        intro = QLabel(
            "登录后会自动读取 LittleSkin 角色，并把角色当前皮肤同步到皮肤库。"
            "这里只负责皮肤；启动游戏仍使用启动器当前选择的账户。"
        )
        intro.setObjectName("Muted")
        intro.setWordWrap(True)
        self.body.addWidget(intro)

        self.username_edit = QLineEdit()
        self.username_edit.setPlaceholderText("LittleSkin 邮箱或用户名")
        self.username_edit.setText(str(username or ""))
        self.username_edit.setClearButtonEnabled(True)
        self.body.addWidget(self.username_edit)

        self.password_edit = QLineEdit()
        self.password_edit.setPlaceholderText("LittleSkin 密码")
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.password_edit.returnPressed.connect(self._submit)
        self.body.addWidget(self.password_edit)

        hint = QHBoxLayout()
        hint.setSpacing(6)
        note = QLabel("密码只用于本次登录，不会保存在启动器里。")
        note.setObjectName("Tiny")
        note.setWordWrap(True)
        hint.addWidget(note, 1)
        register = QPushButton("注册 LittleSkin")
        register.setObjectName("Ghost")
        register.setCursor(Qt.PointingHandCursor)
        register.setIcon(icons.icon("external", colors()["text2"], 14))
        register.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(REGISTER_URL))
        )
        hint.addWidget(register)
        self.body.addLayout(hint)

        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(f"color: {colors()['red']}; font-size: 11px;")
        self.error_label.setVisible(False)
        self.body.addWidget(self.error_label)

        self.add_button("取消", "Secondary", self.reject)
        self.login_btn = self.add_button("登录", "Primary", self._submit)
        self.login_btn.setDefault(True)
        if not self.username_edit.text().strip():
            self.username_edit.setFocus()
        else:
            self.password_edit.setFocus()

    # ------------------------------------------------------------------
    def reject(self) -> None:
        if self._busy:
            return
        super().reject()

    def _set_busy(self, busy: bool, text: str = "") -> None:
        self._busy = busy
        self.username_edit.setEnabled(not busy)
        self.password_edit.setEnabled(not busy)
        self.login_btn.setEnabled(not busy)
        self.login_btn.setText("正在登录…" if busy else "登录")
        if text:
            self._show_error(text, error=False)

    def _show_error(self, text: str, *, error: bool = True) -> None:
        self.error_label.setText(str(text or ""))
        self.error_label.setStyleSheet(
            f"color: {colors()['red' if error else 'muted']}; font-size: 11px;"
        )
        self.error_label.setVisible(bool(str(text or "").strip()))

    def _submit(self) -> None:
        if self._busy:
            return
        username = self.username_edit.text().strip()
        password = self.password_edit.text()
        if not username:
            self._show_error("请输入 LittleSkin 邮箱或用户名。")
            self.username_edit.setFocus()
            return
        if not password:
            self._show_error("请输入 LittleSkin 密码。")
            self.password_edit.setFocus()
            return
        self._show_error("")
        self._set_busy(True, "正在连接 LittleSkin…")
        worker = LittleSkinLoginWorker(
            username, password, self._client_token, self
        )
        self._worker = worker
        worker.finished.connect(self._on_finished)
        worker.start()
        password = ""

    def _on_finished(self, account, skin, error: str) -> None:
        self._worker = None
        self._set_busy(False)
        if error:
            self._show_error(error)
            self.password_edit.setFocus()
            self.password_edit.selectAll()
            return
        self.account = account
        self.skin = skin
        self.accept()
