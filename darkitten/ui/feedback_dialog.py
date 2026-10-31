from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import APP_VERSION
from ..developer import developer_mode, feedback_table_url
from ..feedback import (
    FEEDBACK_SITE,
    GITHUB_ISSUES_PAGE,
    KIND_LABELS,
    build_feedback_payload,
    build_github_issue_url,
    build_mailto_url,
    build_site_feedback_url,
    feedback_payload_text,
)
from .dialogs import StyledDialog
from .widgets import Card, SegmentedControl, badge, colors

KIND_ORDER = ("bug", "suggestion", "ui", "download", "update", "other")


class FeedbackDialog(StyledDialog):
    def __init__(self, parent, kind: str = "bug") -> None:
        super().__init__(
            parent,
            "猫猫的意见箱",
            "message",
            colors()["accent"],
            width=840,
        )
        self.main = parent
        self._kind = kind if kind in KIND_LABELS else "bug"
        self._fallback_payload: dict | None = None
        self._build()
        self.set_kind(self._kind)

    # ------------------------------------------------------------------
    def _build(self) -> None:
        self.intro = QLabel(
            "遇到 bug，或者想让 DKL 更顺手，都可以写在这里。提交时会带上内容打开"
            "下载站反馈表单，再由网站统一收进反馈表。"
        )
        self.intro.setObjectName("Muted")
        self.intro.setWordWrap(True)
        self.body.addWidget(self.intro)

        if developer_mode():
            banner = Card(soft=True)
            banner_layout = QHBoxLayout(banner)
            banner_layout.setContentsMargins(12, 9, 12, 9)
            banner_layout.setSpacing(9)
            banner_layout.addWidget(badge("DEV", "red"))
            texts = QVBoxLayout()
            texts.setSpacing(1)
            title = QLabel("开发者模式已解锁")
            title.setStyleSheet("font-weight: 700; font-size: 12px;")
            note = QLabel(
                "反馈表在下载站内，只有从启动器开发者入口才能进入。"
                "反馈不会再经过第三方反馈服务。"
            )
            note.setObjectName("Tiny")
            note.setWordWrap(True)
            texts.addWidget(title)
            texts.addWidget(note)
            banner_layout.addLayout(texts, 1)
            self.body.addWidget(banner)

        self.tabs = SegmentedControl()
        self.tabs.add_item("写反馈", "compose")
        if developer_mode():
            self.tabs.add_item("反馈表", "table")
            self.tabs.changed.connect(self._switch_tab)
        else:
            self.tabs.setVisible(False)
        self.body.addWidget(self.tabs)

        self.stack = QStackedWidget()
        self.stack.setMinimumHeight(440)
        self.compose_page = self._build_compose()
        self.table_page = self._build_table()
        self.stack.addWidget(self.compose_page)
        self.stack.addWidget(self.table_page)
        self.body.addWidget(self.stack, 1)

        self.add_button("打开网站反馈", "Ghost", self._open_site)
        self.add_button("关闭", "Secondary", self.reject)

    def _build_compose(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)

        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        self.form_title = QLabel("报告一个 Bug")
        self.form_title.setObjectName("H2")
        layout.addWidget(self.form_title)

        self.form_hint = QLabel("")
        self.form_hint.setObjectName("Tiny")
        self.form_hint.setWordWrap(True)
        layout.addWidget(self.form_hint)

        row = QHBoxLayout()
        row.setSpacing(10)
        type_box = QVBoxLayout()
        type_box.setSpacing(4)
        type_label = QLabel("反馈类型")
        type_label.setObjectName("Tiny")
        self.type_combo = QComboBox()
        for value in KIND_ORDER:
            self.type_combo.addItem(KIND_LABELS[value], value)
        type_box.addWidget(type_label)
        type_box.addWidget(self.type_combo)
        row.addLayout(type_box, 1)

        version_box = QVBoxLayout()
        version_box.setSpacing(4)
        version_label = QLabel("启动器版本")
        version_label.setObjectName("Tiny")
        self.version_edit = QLineEdit(APP_VERSION)
        self.version_edit.setReadOnly(True)
        version_box.addWidget(version_label)
        version_box.addWidget(self.version_edit)
        row.addLayout(version_box, 0)
        layout.addLayout(row)

        title_label = QLabel("一句话标题")
        title_label.setObjectName("Tiny")
        self.title_label = title_label
        self.title_edit = QLineEdit()
        self.title_edit.setMaxLength(80)
        self.title_edit.setPlaceholderText("例如：安装 1.21.1 后启动时提示缺少 Java")
        layout.addWidget(title_label)
        layout.addWidget(self.title_edit)

        desc_label = QLabel("详细描述")
        desc_label.setObjectName("Tiny")
        self.desc_edit = QPlainTextEdit()
        self.desc_edit.setPlaceholderText(
            "操作步骤、看到的报错、复现频率。请勿填写账号密码、访问令牌或存档内容。"
        )
        self.desc_edit.setFixedHeight(142)
        layout.addWidget(desc_label)
        layout.addWidget(self.desc_edit)

        contact_label = QLabel("联系方式（可选）")
        contact_label.setObjectName("Tiny")
        self.contact_edit = QLineEdit()
        self.contact_edit.setMaxLength(120)
        self.contact_edit.setPlaceholderText("邮箱、QQ 或论坛 ID")
        layout.addWidget(contact_label)
        layout.addWidget(self.contact_edit)
        try:
            email = str(getattr(self.main.config, "microsoft_email", "") or "").strip()
            if email:
                self.contact_edit.setText(email)
        except Exception:
            pass

        note = QLabel(
            "提交时会打开 DKL 下载站的反馈表单，内容会自动填好。网站是纯静态页面，"
            "最终通过 GitHub Issue 保存反馈，不再经过第三方反馈服务。"
        )
        note.setObjectName("Tiny")
        note.setWordWrap(True)
        layout.addWidget(note)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("Tiny")
        layout.addWidget(self.status_label)

        self.fallback_box = QWidget()
        fallback_layout = QHBoxLayout(self.fallback_box)
        fallback_layout.setContentsMargins(0, 0, 0, 0)
        fallback_layout.setSpacing(8)
        self.github_btn = QPushButton("直接打开 GitHub")
        self.github_btn.setObjectName("Secondary")
        self.github_btn.setCursor(Qt.PointingHandCursor)
        self.github_btn.clicked.connect(self._open_github_fallback)
        self.email_btn = QPushButton("用邮件发送")
        self.email_btn.setObjectName("Ghost")
        self.email_btn.setCursor(Qt.PointingHandCursor)
        self.email_btn.clicked.connect(self._open_email_fallback)
        fallback_layout.addWidget(self.github_btn)
        fallback_layout.addWidget(self.email_btn)
        fallback_layout.addStretch(1)
        self.fallback_box.setVisible(False)
        layout.addWidget(self.fallback_box)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        copy_btn = QPushButton("复制内容")
        copy_btn.setObjectName("Ghost")
        copy_btn.setCursor(Qt.PointingHandCursor)
        copy_btn.clicked.connect(self._copy_compose)
        self.submit_btn = QPushButton("在下载站提交")
        self.submit_btn.setObjectName("Primary")
        self.submit_btn.setCursor(Qt.PointingHandCursor)
        self.submit_btn.setMinimumWidth(126)
        self.submit_btn.clicked.connect(self._submit)
        actions.addWidget(copy_btn)
        actions.addStretch(1)
        actions.addWidget(self.submit_btn)
        layout.addLayout(actions)

        outer.addWidget(card)
        outer.addStretch(1)
        return page

    def _build_table(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        card = Card()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 18, 20, 18)
        card_layout.setSpacing(9)

        title = QLabel("网站反馈表")
        title.setObjectName("H2")
        card_layout.addWidget(title)

        note = QLabel(
            "反馈表放在 DKL 下载站里，由网站直接读取 GitHub Issues。"
            "启动器只在开发者模式下生成入口链接，普通访客无法从网站导航进入。"
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)
        card_layout.addWidget(note)

        detail = QLabel(
            "表格里会显示类型、状态、版本、联系方式、页面和系统信息。"
            "回复、关闭或标记处理状态请直接打开对应的 GitHub Issue。"
        )
        detail.setObjectName("Tiny")
        detail.setWordWrap(True)
        card_layout.addWidget(detail)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        open_table = QPushButton("打开网站反馈表")
        open_table.setObjectName("Primary")
        open_table.setCursor(Qt.PointingHandCursor)
        open_table.clicked.connect(self._open_table)
        open_github = QPushButton("打开 GitHub Issues")
        open_github.setObjectName("Ghost")
        open_github.setCursor(Qt.PointingHandCursor)
        open_github.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(GITHUB_ISSUES_PAGE))
        )
        actions.addWidget(open_table)
        actions.addWidget(open_github)
        actions.addStretch(1)
        card_layout.addLayout(actions)

        layout.addWidget(card)
        layout.addStretch(1)
        return page

    # ------------------------------------------------------------------
    def set_kind(self, kind: str) -> None:
        kind = kind if kind in KIND_LABELS else "bug"
        self._kind = kind
        index = self.type_combo.findData(kind)
        if index >= 0:
            self.type_combo.setCurrentIndex(index)
        if kind == "suggestion":
            self.title_label.setText("给猫猫提个改进建议")
            self.form_title.setText("提一个改进建议")
            self.form_hint.setText(
                "希望启动器多做什么、少做什么，或者哪里用起来不顺手？"
                "具体的使用场景比一句“优化一下”更有帮助。"
            )
            self.title_edit.setPlaceholderText("例如：希望下载中心支持按完成时间排序")
            self.submit_btn.setText("在下载站提建议")
        else:
            self.title_label.setText("猫猫收到一只虫子")
            self.form_title.setText("报告一个 Bug")
            self.form_hint.setText(
                "描述发生了什么、你期待什么，以及能否稳定复现。"
                "带版本号和报错信息的反馈最有用。"
            )
            self.title_edit.setPlaceholderText("例如：安装 1.21.1 后启动时报错")
            self.submit_btn.setText("在下载站提交")

    def _switch_tab(self, value: str) -> None:
        self.stack.setCurrentIndex(1 if value == "table" else 0)

    # ------------------------------------------------------------------
    def _set_status(self, text: str, kind: str = "") -> None:
        self.status_label.setText(text)
        color = colors()["muted"]
        if kind == "ok":
            color = colors()["green"]
        elif kind == "err":
            color = colors()["red"]
        elif kind == "busy":
            color = colors()["accent"]
        self.status_label.setStyleSheet(f"color: {color}; font-size: 11px;")

    def _copy_compose(self) -> None:
        payload = self._payload()
        if not payload.get("title") and not payload.get("description"):
            self._set_status("先写一点内容再复制。", "err")
            return
        QGuiApplication.clipboard().setText(feedback_payload_text(payload))
        self._set_status("反馈内容已复制到剪贴板。", "ok")

    def _payload(self) -> dict:
        return build_feedback_payload(
            kind=str(self.type_combo.currentData() or "other"),
            title=self.title_edit.text(),
            description=self.desc_edit.toPlainText(),
            version=self.version_edit.text() or APP_VERSION,
            contact=self.contact_edit.text(),
        )

    def _submit(self) -> None:
        payload = self._payload()
        if len(payload["title"]) < 2:
            self._set_status("标题至少需要 2 个字。", "err")
            self.title_edit.setFocus()
            return
        if len(payload["description"]) < 10:
            self._set_status("描述至少需要 10 个字，写清楚操作步骤会更容易定位。", "err")
            self.desc_edit.setFocus()
            return
        self._fallback_payload = payload
        QGuiApplication.clipboard().setText(feedback_payload_text(payload))
        QDesktopServices.openUrl(QUrl(build_site_feedback_url(payload)))
        self._set_status(
            "已打开下载站反馈表单，内容已自动填好。在网页里确认提交后会转到 GitHub Issue。"
            "完整内容也复制到了剪贴板。",
            "ok",
        )
        self.fallback_box.setVisible(True)
        try:
            self.main.toast("已打开下载站反馈表单", "success")
        except Exception:
            pass

    def _open_github_fallback(self) -> None:
        payload = self._fallback_payload or self._payload()
        if not payload.get("title") and not payload.get("description"):
            self._set_status("先写一点内容再提交。", "err")
            return
        QGuiApplication.clipboard().setText(feedback_payload_text(payload))
        QDesktopServices.openUrl(QUrl(build_github_issue_url(payload)))
        self._set_status(
            "已打开 GitHub 新建 Issue 页面，完整内容也复制到了剪贴板。",
            "ok",
        )

    def _open_email_fallback(self) -> None:
        payload = self._fallback_payload or self._payload()
        QGuiApplication.clipboard().setText(feedback_payload_text(payload))
        QDesktopServices.openUrl(QUrl(build_mailto_url(payload)))
        self._set_status("已打开邮件草稿，完整内容也复制到了剪贴板。", "ok")

    def _open_site(self) -> None:
        QDesktopServices.openUrl(QUrl(FEEDBACK_SITE))

    def _open_table(self) -> None:
        QDesktopServices.openUrl(QUrl(feedback_table_url(self.main.config)))

    # ------------------------------------------------------------------
    def reject(self) -> None:
        super().reject()
