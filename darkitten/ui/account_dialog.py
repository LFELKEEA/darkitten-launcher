from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from ..microsoft_auth import DeviceCode
from ..workers import BrowserLoginWorker, DeviceLoginWorker
from .dialogs import StyledDialog
from .widgets import colors


class BrowserLoginDialog(StyledDialog):
    """用系统浏览器完成 Microsoft 登录，邮箱会预填到官方登录页。"""

    def __init__(self, main, client_id: str, email: str = "") -> None:
        super().__init__(
            main,
            "登录 Microsoft 账户",
            "user",
            icon_color="#3B6FF5",
            width=560,
        )
        self.main = main
        self.client_id = client_id
        self._worker: BrowserLoginWorker | None = None
        self._account = None
        self._url = ""
        self._closed = False

        intro = QLabel(
            "启动器会打开系统浏览器，并把邮箱预填到 Microsoft 官方登录页。"
            "密码只在浏览器中输入，授权完成后会自动返回启动器，无需复制验证码。"
        )
        intro.setObjectName("DialogText")
        intro.setWordWrap(True)
        self.body.addWidget(intro)

        email_box = QFrame()
        email_box.setObjectName("SoftCard")
        email_box.setAttribute(Qt.WA_StyledBackground, True)
        email_layout = QVBoxLayout(email_box)
        email_layout.setContentsMargins(13, 11, 13, 11)
        email_layout.setSpacing(6)
        email_title = QLabel("Microsoft 邮箱")
        email_title.setStyleSheet("font-weight: 600;")
        email_layout.addWidget(email_title)
        self.email_edit = QLineEdit(email)
        self.email_edit.setPlaceholderText("name@example.com（可留空）")
        self.email_edit.returnPressed.connect(self._start)
        email_layout.addWidget(self.email_edit)
        email_hint = QLabel(
            "填写后会自动预填到登录页，减少一次输入；不会保存密码。"
        )
        email_hint.setObjectName("Tiny")
        email_hint.setWordWrap(True)
        email_layout.addWidget(email_hint)
        self.body.addWidget(email_box)

        steps = QFrame()
        steps.setObjectName("SoftCard")
        steps.setAttribute(Qt.WA_StyledBackground, True)
        steps_layout = QVBoxLayout(steps)
        steps_layout.setContentsMargins(13, 11, 13, 11)
        steps_layout.setSpacing(4)
        for text in (
            "1. 点击“打开浏览器登录”，启动器会自动打开 Microsoft 登录页。",
            "2. 在浏览器中输入密码并同意授权；密码不会经过启动器。",
            "3. 页面显示“登录完成”后回到启动器，账户会自动完成验证。",
        ):
            label = QLabel(text)
            label.setObjectName("Tiny")
            label.setWordWrap(True)
            steps_layout.addWidget(label)
        self.body.addWidget(steps)

        self.status = QLabel("")
        self.status.setObjectName("Tiny")
        self.status.setWordWrap(True)
        self.body.addWidget(self.status)

        self.open_btn = QPushButton("重新打开登录页")
        self.open_btn.setObjectName("Secondary")
        self.open_btn.setCursor(Qt.PointingHandCursor)
        self.open_btn.clicked.connect(self._open_page)
        self.open_btn.setVisible(False)
        self.body.addWidget(self.open_btn, 0, Qt.AlignLeft)

        self.error = QLabel("")
        self.error.setWordWrap(True)
        self.error.setStyleSheet(f"color: {colors()['red']}; font-size: 11px;")
        self.error.setVisible(False)
        self.body.addWidget(self.error)

        self.cancel_btn = self.add_button("取消", "Secondary", self.reject)
        self.start_btn = self.add_button(
            "打开浏览器登录", "Primary", self._start
        )
        self.start_btn.setMinimumWidth(132)

    @property
    def account(self):
        return self._account

    def _start(self) -> None:
        if self._worker is not None:
            return
        email = self.email_edit.text().strip()
        if email and ("@" not in email or any(ch.isspace() for ch in email)):
            self.error.setText("请输入完整的 Microsoft 邮箱地址，或留空。")
            self.error.setVisible(True)
            self.email_edit.setFocus()
            return
        self.error.setVisible(False)
        self.status.setText("正在启动本地登录服务并打开浏览器…")
        self._url = ""
        self.open_btn.setVisible(False)
        self.email_edit.setEnabled(False)
        self.start_btn.setEnabled(False)
        self.start_btn.setText("等待浏览器登录…")
        self.cancel_btn.setText("取消登录")
        self.main.config.microsoft_email = email
        self.main.config.save()
        worker = BrowserLoginWorker(self.client_id, email, self)
        self._worker = worker
        worker.url_ready.connect(self._url_ready)
        worker.status.connect(self._set_status)
        worker.finished.connect(self._finished)
        worker.start()

    def _url_ready(self, url: str) -> None:
        if self._closed:
            return
        self._url = url
        self.open_btn.setVisible(True)
        self.adjustSize()

    def _set_status(self, text: str) -> None:
        if not self._closed:
            self.status.setText(text)

    def _open_page(self) -> None:
        if self._url:
            QDesktopServices.openUrl(QUrl(self._url))

    def _finished(self, account, error: str) -> None:
        self._worker = None
        if self._closed:
            return
        if account is not None:
            self._account = account
            self.accept()
            return
        self.error.setText(error or "Microsoft 登录失败，请重试。")
        self.error.setVisible(True)
        self.status.setText("")
        self._url = ""
        self.open_btn.setVisible(False)
        self.email_edit.setEnabled(True)
        self.start_btn.setEnabled(True)
        self.start_btn.setText("重新尝试")
        self.cancel_btn.setText("关闭")
        self.adjustSize()

    def reject(self) -> None:
        self._closed = True
        if self._worker is not None:
            self._worker.cancel()
            self._worker = None
        super().reject()


class MicrosoftLoginDialog(StyledDialog):
    """通过 Microsoft 设备码流程登录正版账户。"""

    def __init__(self, main, client_id: str) -> None:
        super().__init__(
            main,
            "登录 Microsoft 账户",
            "user",
            icon_color="#3B6FF5",
            width=560,
        )
        self.main = main
        self.client_id = client_id
        self._worker: DeviceLoginWorker | None = None
        self._device: DeviceCode | None = None
        self._account = None
        self._closed = False

        intro = QLabel(
            "将使用 Microsoft 设备码登录。登录过程在 Microsoft 官方页面完成，"
            "启动器不会接触你的密码。"
        )
        intro.setObjectName("DialogText")
        intro.setWordWrap(True)
        self.body.addWidget(intro)

        steps = QFrame()
        steps.setObjectName("SoftCard")
        steps.setAttribute(Qt.WA_StyledBackground, True)
        steps_layout = QVBoxLayout(steps)
        steps_layout.setContentsMargins(13, 11, 13, 11)
        steps_layout.setSpacing(4)
        for text in (
            "1. 点击“获取验证码”，浏览器会打开 Microsoft 登录页。",
            "2. 输入窗口中显示的验证码，并登录要使用的 Microsoft 账户。",
            "3. 同意授权后回到启动器，账户会自动完成验证。",
        ):
            label = QLabel(text)
            label.setObjectName("Tiny")
            label.setWordWrap(True)
            steps_layout.addWidget(label)
        self.body.addWidget(steps)

        self.code_box = QFrame()
        self.code_box.setObjectName("SoftCard")
        self.code_box.setAttribute(Qt.WA_StyledBackground, True)
        code_layout = QVBoxLayout(self.code_box)
        code_layout.setContentsMargins(14, 12, 14, 12)
        code_layout.setSpacing(7)
        code_title = QLabel("在 Microsoft 页面输入此验证码")
        code_title.setObjectName("Tiny")
        code_title.setAlignment(Qt.AlignCenter)
        code_layout.addWidget(code_title)

        self.code_label = QLabel("------")
        self.code_label.setAlignment(Qt.AlignCenter)
        self.code_label.setStyleSheet(
            "font-size: 30px; font-weight: 800; letter-spacing: 6px;"
        )
        code_layout.addWidget(self.code_label)

        code_actions = QHBoxLayout()
        code_actions.setSpacing(8)
        code_actions.addStretch(1)
        self.copy_btn = QPushButton("复制验证码")
        self.copy_btn.setObjectName("Secondary")
        self.copy_btn.setCursor(Qt.PointingHandCursor)
        self.copy_btn.clicked.connect(self._copy_code)
        self.open_btn = QPushButton("打开登录页")
        self.open_btn.setObjectName("Primary")
        self.open_btn.setCursor(Qt.PointingHandCursor)
        self.open_btn.clicked.connect(self._open_page)
        code_actions.addWidget(self.copy_btn)
        code_actions.addWidget(self.open_btn)
        code_actions.addStretch(1)
        code_layout.addLayout(code_actions)
        self.code_box.setVisible(False)
        self.body.addWidget(self.code_box)

        self.status = QLabel("")
        self.status.setObjectName("Tiny")
        self.status.setWordWrap(True)
        self.body.addWidget(self.status)

        self.error = QLabel("")
        self.error.setWordWrap(True)
        self.error.setStyleSheet(f"color: {colors()['red']}; font-size: 11px;")
        self.error.setVisible(False)
        self.body.addWidget(self.error)

        self.cancel_btn = self.add_button("关闭", "Secondary", self.reject)
        self.login_btn = self.add_button("获取验证码", "Primary", self._start)
        self.login_btn.setMinimumWidth(112)

    @property
    def account(self):
        return self._account

    def _start(self) -> None:
        if self._worker is not None:
            return
        self.error.setVisible(False)
        self.code_box.setVisible(False)
        self.status.setText("正在向 Microsoft 请求验证码…")
        self.login_btn.setEnabled(False)
        self.cancel_btn.setText("取消登录")
        worker = DeviceLoginWorker(self.client_id, self)
        self._worker = worker
        worker.code_ready.connect(self._code_ready)
        worker.status.connect(self._set_status)
        worker.finished.connect(self._finished)
        worker.start()

    def _code_ready(self, device: DeviceCode) -> None:
        if self._closed:
            return
        self._device = device
        self.code_label.setText(device.user_code)
        self.code_box.setVisible(True)
        self._copy_code()
        self.status.setText(
            "验证码已复制到剪贴板。请在浏览器中输入验证码并登录。"
        )
        self._open_page()
        self.adjustSize()

    def _set_status(self, text: str) -> None:
        if not self._closed:
            self.status.setText(text)

    def _copy_code(self) -> None:
        if self._device:
            QApplication.clipboard().setText(self._device.user_code)
            self.status.setText("验证码已复制到剪贴板。")

    def _open_page(self) -> None:
        if not self._device:
            return
        url = (
            self._device.verification_uri_complete
            or self._device.verification_uri
            or "https://microsoft.com/devicelogin"
        )
        QDesktopServices.openUrl(QUrl(url))

    def _finished(self, account, error: str) -> None:
        self._worker = None
        if self._closed:
            return
        self.login_btn.setEnabled(True)
        self.login_btn.setText("重新登录")
        self.cancel_btn.setText("关闭")
        if account is not None:
            self._account = account
            self.accept()
            return
        self.error.setText(error or "Microsoft 登录失败，请重试。")
        self.error.setVisible(True)
        self.status.setText("")
        self.code_box.setVisible(False)
        self.adjustSize()

    def reject(self) -> None:
        self._closed = True
        if self._worker is not None:
            self._worker.cancel()
            self._worker = None
        super().reject()
