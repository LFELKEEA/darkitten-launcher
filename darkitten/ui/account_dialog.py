from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from ..microsoft_auth import (
    DeviceCode,
    MicrosoftAuthError,
    is_legacy_client_id,
    normalize_client_id,
)
from ..workers import BrowserLoginWorker, DeviceLoginWorker
from .dialogs import StyledDialog
from .widgets import colors


LEGACY_WARNING = (
    "你填写的是 16 位旧版应用 ID。该登录方式属于微软旧版 Live 服务，"
    "是否使用完全由你自行决定。启动器无法保证它的可用性、稳定性与安全性，"
    "也不对使用该应用 ID 产生的任何后果承担责任。"
)


def _apply_client_id(dialog, edit: QLineEdit, error: QLabel) -> str | None:
    """校验登录窗口里的客户端 ID，并把它保存为默认值。"""
    try:
        client_id = normalize_client_id(edit.text())
    except MicrosoftAuthError as exc:
        error.setText(str(exc))
        error.setVisible(True)
        edit.setFocus()
        return None
    edit.setText(client_id)
    dialog.client_id = client_id
    dialog.main.config.microsoft_client_id = client_id
    dialog.main.config.save()
    return client_id


def _legacy_warning_box() -> tuple[QFrame, QCheckBox]:
    """16 位应用 ID 的风险提示与确认勾选。"""
    box = QFrame()
    box.setObjectName("SoftCard")
    box.setAttribute(Qt.WA_StyledBackground, True)
    layout = QVBoxLayout(box)
    layout.setContentsMargins(13, 11, 13, 11)
    layout.setSpacing(6)
    title = QLabel("旧版应用 ID 风险提示")
    title.setStyleSheet("font-weight: 600;")
    layout.addWidget(title)
    text = QLabel(LEGACY_WARNING)
    text.setObjectName("Tiny")
    text.setWordWrap(True)
    layout.addWidget(text)
    check = QCheckBox("我已了解并自行承担使用该应用 ID 的风险")
    layout.addWidget(check)
    box.setVisible(False)
    return box, check


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
        self._worker: BrowserLoginWorker | DeviceLoginWorker | None = None
        self._account = None
        self._url = ""
        self._device: DeviceCode | None = None
        self._closed = False

        self.intro = QLabel(
            "启动器会打开系统浏览器，并把邮箱预填到 Microsoft 官方登录页。"
            "密码只在浏览器中输入，授权完成后会自动返回启动器。"
            "16 位旧版应用 ID 会自动改用验证码登录。"
        )
        self.intro.setObjectName("DialogText")
        self.intro.setWordWrap(True)
        self.body.addWidget(self.intro)

        client_box = QFrame()
        client_box.setObjectName("SoftCard")
        client_box.setAttribute(Qt.WA_StyledBackground, True)
        client_layout = QVBoxLayout(client_box)
        client_layout.setContentsMargins(13, 11, 13, 11)
        client_layout.setSpacing(6)
        client_title = QLabel("客户端 ID")
        client_title.setStyleSheet("font-weight: 600;")
        client_layout.addWidget(client_title)
        self.client_edit = QLineEdit(client_id)
        self.client_edit.setPlaceholderText("GUID 或 16 位应用 ID")
        self.client_edit.returnPressed.connect(self._start)
        self.client_edit.textChanged.connect(self._client_id_changed)
        client_layout.addWidget(self.client_edit)
        client_hint = QLabel(
            "支持 GUID 或老式 16 位应用 ID；登录和之后的令牌刷新都会用这个应用。"
        )
        client_hint.setObjectName("Tiny")
        client_hint.setWordWrap(True)
        client_layout.addWidget(client_hint)
        self.body.addWidget(client_box)

        self.legacy_box, self.risk_check = _legacy_warning_box()
        self.risk_check.toggled.connect(self._client_id_changed)
        self.body.addWidget(self.legacy_box)

        self.email_box = QFrame()
        self.email_box.setObjectName("SoftCard")
        self.email_box.setAttribute(Qt.WA_StyledBackground, True)
        email_layout = QVBoxLayout(self.email_box)
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
        self.body.addWidget(self.email_box)

        self.steps_box = QFrame()
        self.steps_box.setObjectName("SoftCard")
        self.steps_box.setAttribute(Qt.WA_StyledBackground, True)
        self.steps_layout = QVBoxLayout(self.steps_box)
        self.steps_layout.setContentsMargins(13, 11, 13, 11)
        self.steps_layout.setSpacing(4)
        for text in (
            "1. 点击“打开浏览器登录”，启动器会自动打开 Microsoft 登录页。",
            "2. 在浏览器中输入密码并同意授权；密码不会经过启动器。",
            "3. 页面显示“登录完成”后回到启动器，账户会自动完成验证。",
        ):
            label = QLabel(text)
            label.setObjectName("Tiny")
            label.setWordWrap(True)
            self.steps_layout.addWidget(label)
        self.body.addWidget(self.steps_box)

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

        self.reopen_btn = QPushButton("重新打开登录页")
        self.reopen_btn.setObjectName("Secondary")
        self.reopen_btn.setCursor(Qt.PointingHandCursor)
        self.reopen_btn.clicked.connect(self._open_page)
        self.reopen_btn.setVisible(False)
        self.body.addWidget(self.reopen_btn, 0, Qt.AlignLeft)

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
        self._client_id_changed()

    @property
    def account(self):
        return self._account

    def _client_id_changed(self, *_args) -> None:
        if self._worker is not None:
            return
        legacy = is_legacy_client_id(self.client_edit.text())
        self.legacy_box.setVisible(legacy)
        self.email_box.setVisible(not legacy)
        self.steps_box.setVisible(not legacy)
        self.intro.setText(
            "16 位旧版应用 ID 将使用 Microsoft 验证码登录。"
            "登录在 Microsoft 官方页面完成，启动器不会接触你的密码。"
            if legacy
            else "启动器会打开系统浏览器，并把邮箱预填到 Microsoft 官方登录页。"
            "密码只在浏览器中输入，授权完成后会自动返回启动器。"
        )
        self.start_btn.setText(
            "使用验证码登录" if legacy else "打开浏览器登录"
        )
        self.start_btn.setEnabled(
            not legacy or self.risk_check.isChecked()
        )
        self.adjustSize()

    def _start(self) -> None:
        if self._worker is not None:
            return
        client_id = _apply_client_id(self, self.client_edit, self.error)
        if client_id is None:
            return
        if is_legacy_client_id(client_id):
            self.legacy_box.setVisible(True)
            if not self.risk_check.isChecked():
                self.error.setText(
                    "请先勾选风险确认，再继续使用 16 位应用 ID 登录。"
                )
                self.error.setVisible(True)
                return
            self.error.setVisible(False)
            self.status.setText("正在向 Microsoft 请求验证码…")
            self._device = None
            self.code_box.setVisible(False)
            self.reopen_btn.setVisible(False)
            self.client_edit.setEnabled(False)
            self.email_box.setEnabled(False)
            self.start_btn.setEnabled(False)
            self.start_btn.setText("等待验证码登录…")
            self.cancel_btn.setText("取消登录")
            worker = DeviceLoginWorker(client_id, self)
            self._worker = worker
            worker.code_ready.connect(self._code_ready)
            worker.status.connect(self._set_status)
            worker.finished.connect(self._finished)
            worker.start()
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
        self._device = None
        self.code_box.setVisible(False)
        self.reopen_btn.setVisible(False)
        self.client_edit.setEnabled(False)
        self.email_edit.setEnabled(False)
        self.start_btn.setEnabled(False)
        self.start_btn.setText("等待浏览器登录…")
        self.cancel_btn.setText("取消登录")
        self.main.config.microsoft_email = email
        self.main.config.save()
        worker = BrowserLoginWorker(client_id, email, self)
        self._worker = worker
        worker.url_ready.connect(self._url_ready)
        worker.status.connect(self._set_status)
        worker.finished.connect(self._finished)
        worker.start()

    def _url_ready(self, url: str) -> None:
        if self._closed:
            return
        self._url = url
        self.reopen_btn.setVisible(True)
        self.adjustSize()

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
        if self._device:
            url = (
                self._device.verification_uri_complete
                or self._device.verification_uri
                or "https://www.microsoft.com/link"
            )
            QDesktopServices.openUrl(QUrl(url))
        elif self._url:
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
        self._device = None
        self.code_box.setVisible(False)
        self.reopen_btn.setVisible(False)
        self.client_edit.setEnabled(True)
        self.email_box.setEnabled(True)
        legacy = is_legacy_client_id(self.client_edit.text())
        self.legacy_box.setVisible(legacy)
        self.email_box.setVisible(not legacy)
        self.steps_box.setVisible(not legacy)
        self.start_btn.setEnabled(
            not legacy or self.risk_check.isChecked()
        )
        self.start_btn.setText(
            "使用验证码登录" if legacy else "重新尝试"
        )
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

        client_box = QFrame()
        client_box.setObjectName("SoftCard")
        client_box.setAttribute(Qt.WA_StyledBackground, True)
        client_layout = QVBoxLayout(client_box)
        client_layout.setContentsMargins(13, 11, 13, 11)
        client_layout.setSpacing(6)
        client_title = QLabel("客户端 ID")
        client_title.setStyleSheet("font-weight: 600;")
        client_layout.addWidget(client_title)
        self.client_edit = QLineEdit(client_id)
        self.client_edit.setPlaceholderText("GUID 或 16 位应用 ID")
        self.client_edit.returnPressed.connect(self._start)
        self.client_edit.textChanged.connect(self._client_id_changed)
        client_layout.addWidget(self.client_edit)
        client_hint = QLabel(
            "支持 GUID 或老式 16 位应用 ID；验证码和之后的令牌刷新都会用这个应用。"
        )
        client_hint.setObjectName("Tiny")
        client_hint.setWordWrap(True)
        client_layout.addWidget(client_hint)
        self.body.addWidget(client_box)

        self.legacy_box, self.risk_check = _legacy_warning_box()
        self.risk_check.toggled.connect(self._client_id_changed)
        self.body.addWidget(self.legacy_box)

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
        self._client_id_changed()

    @property
    def account(self):
        return self._account

    def _client_id_changed(self, *_args) -> None:
        if self._worker is not None:
            return
        legacy = is_legacy_client_id(self.client_edit.text())
        self.legacy_box.setVisible(legacy)
        self.login_btn.setEnabled(
            not legacy or self.risk_check.isChecked()
        )
        self.adjustSize()

    def _start(self) -> None:
        if self._worker is not None:
            return
        client_id = _apply_client_id(self, self.client_edit, self.error)
        if client_id is None:
            return
        if is_legacy_client_id(client_id):
            self.legacy_box.setVisible(True)
            if not self.risk_check.isChecked():
                self.error.setText(
                    "请先勾选风险确认，再继续使用 16 位应用 ID 登录。"
                )
                self.error.setVisible(True)
                return
        self.error.setVisible(False)
        self.code_box.setVisible(False)
        self.status.setText("正在向 Microsoft 请求验证码…")
        self.client_edit.setEnabled(False)
        self.login_btn.setEnabled(False)
        self.cancel_btn.setText("取消登录")
        worker = DeviceLoginWorker(client_id, self)
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
        self.client_edit.setEnabled(True)
        legacy = is_legacy_client_id(self.client_edit.text())
        self.legacy_box.setVisible(legacy)
        self.login_btn.setEnabled(
            not legacy or self.risk_check.isChecked()
        )
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
