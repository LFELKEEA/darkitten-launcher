from __future__ import annotations

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..accounts import Account
from ..config import APP_VERSION
from ..developer import developer_mode
from ..feedback import (
    FEEDBACK_SITE,
    GITHUB_ISSUES_PAGE,
    KIND_LABELS,
    SOURCE_LABELS,
    STATUS_LABELS,
    FeedbackListWorker,
    FeedbackSubmitWorker,
    FeedbackUpdateWorker,
    build_feedback_payload,
    build_github_issue_url,
    build_mailto_url,
    feedback_as_text,
    feedback_payload_text,
)
from . import icons
from .dialogs import StyledDialog
from .widgets import (
    Card,
    SegmentedControl,
    badge,
    colors,
    icon_button,
    transparent_children,
)

KIND_ORDER = ("bug", "suggestion", "ui", "download", "update", "other")
KIND_BADGES = {
    "bug": "red",
    "suggestion": "green",
    "ui": "blue",
    "download": "orange",
    "update": "purple",
    "other": "gray",
}
STATUS_BADGES = {
    "open": "orange",
    "resolved": "green",
    "archived": "gray",
    "closed": "gray",
}


def _short_time(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "时间未知"
    text = text.replace("T", " ")
    return text[:16]


def _kind_badge(kind: str) -> QLabel:
    return badge(KIND_LABELS.get(kind, "其他"), KIND_BADGES.get(kind, "gray"))


def _status_badge(status: str) -> QLabel:
    return badge(STATUS_LABELS.get(status, status or "待处理"), STATUS_BADGES.get(status, "gray"))


def _source_text(item: dict) -> str:
    source = str(item.get("source") or "")
    label = SOURCE_LABELS.get(source, source or "反馈")
    if item.get("platform") == "github":
        return f"GitHub 公开反馈 · {label}"
    return f"反馈中心 · {label}"


class FeedbackRow(QFrame):
    clicked = Signal(object)

    def __init__(self, item: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.item = item
        self.setObjectName("FeedbackRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(72)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 12, 10)
        layout.setSpacing(10)

        texts = QVBoxLayout()
        texts.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(6)
        top.addWidget(_kind_badge(str(item.get("kind") or "other")))
        top.addWidget(_status_badge(str(item.get("status") or "open")))
        if item.get("platform") == "github":
            top.addWidget(badge("GitHub", "blue"))
        top.addStretch(1)
        texts.addLayout(top)

        title = QLabel(str(item.get("title") or "未命名反馈"))
        title.setStyleSheet("font-weight: 700; font-size: 13px;")
        texts.addWidget(title)

        meta_bits = [
            _source_text(item),
            _short_time(item.get("created_at")),
        ]
        version = str(item.get("version") or "").strip()
        if version:
            meta_bits.append(f"v{version}")
        meta = QLabel(" · ".join(meta_bits))
        meta.setObjectName("Tiny")
        texts.addWidget(meta)

        preview_text = str(item.get("description") or "").replace("\n", " ").strip()
        if preview_text:
            preview = QLabel(preview_text[:120])
            preview.setObjectName("Muted")
            preview.setStyleSheet("font-size: 11px;")
            texts.addWidget(preview)

        layout.addLayout(texts, 1)
        arrow = QLabel()
        arrow.setPixmap(icons.pixmap("chevron-right", colors()["muted"], 16))
        arrow.setFixedWidth(20)
        layout.addWidget(arrow, 0, Qt.AlignVCenter)
        transparent_children(self)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.item)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class FeedbackDetailDialog(StyledDialog):
    status_requested = Signal(object, str)

    def __init__(self, parent: QWidget, item: dict) -> None:
        super().__init__(
            parent,
            "反馈详情",
            "message",
            colors()["accent"],
            width=700,
        )
        self.item = dict(item)
        self._build()

    def _build(self) -> None:
        item = self.item
        top = QHBoxLayout()
        top.setSpacing(6)
        top.addWidget(_kind_badge(str(item.get("kind") or "other")))
        top.addWidget(_status_badge(str(item.get("status") or "open")))
        if item.get("platform") == "github":
            top.addWidget(badge("GitHub", "blue"))
        top.addStretch(1)
        self.body.addLayout(top)

        title = QLabel(str(item.get("title") or "未命名反馈"))
        title.setStyleSheet("font-size: 18px; font-weight: 800;")
        title.setWordWrap(True)
        self.body.addWidget(title)

        meta_bits = [
            f"编号 {item.get('id') or '—'}",
            _source_text(item),
            _short_time(item.get("created_at")),
        ]
        version = str(item.get("version") or "").strip()
        if version:
            meta_bits.append(f"启动器 v{version}")
        meta = QLabel(" · ".join(meta_bits))
        meta.setObjectName("Tiny")
        meta.setWordWrap(True)
        self.body.addWidget(meta)

        contact = str(item.get("contact") or "").strip()
        page = str(item.get("page") or "").strip()
        system = str(item.get("system") or "").strip()
        details = []
        if contact:
            details.append(f"联系方式：{contact}")
        if page:
            details.append(f"页面：{page}")
        if system:
            details.append(f"系统：{system}")
        if details:
            detail_label = QLabel("\n".join(details))
            detail_label.setObjectName("Tiny")
            detail_label.setWordWrap(True)
            self.body.addWidget(detail_label)

        description = QPlainTextEdit()
        description.setReadOnly(True)
        description.setMinimumHeight(230)
        description.setPlainText(str(item.get("description") or "（没有填写描述。）"))
        self.body.addWidget(description, 1)

        if item.get("platform") == "center":
            status_row = QHBoxLayout()
            status_row.setSpacing(8)
            status_label = QLabel("处理状态")
            status_label.setStyleSheet("font-weight: 600;")
            status_row.addWidget(status_label)
            self.status_combo = QComboBox()
            for value in ("open", "resolved", "archived"):
                self.status_combo.addItem(STATUS_LABELS[value], value)
            current = str(item.get("status") or "open")
            index = self.status_combo.findData(current)
            if index >= 0:
                self.status_combo.setCurrentIndex(index)
            status_row.addWidget(self.status_combo)
            status_hint = QLabel("状态会保存到反馈中心，网站和启动器同步显示。")
            status_hint.setObjectName("Tiny")
            status_hint.setWordWrap(True)
            status_row.addWidget(status_hint, 1)
            self.body.addLayout(status_row)

        copy_btn = QPushButton("复制内容")
        copy_btn.setObjectName("Ghost")
        copy_btn.setCursor(Qt.PointingHandCursor)
        copy_btn.clicked.connect(self._copy)
        self.footer.addWidget(copy_btn)

        if item.get("platform") == "github":
            github_btn = QPushButton("在 GitHub 打开")
            github_btn.setObjectName("Secondary")
            github_btn.setCursor(Qt.PointingHandCursor)
            github_btn.clicked.connect(self._open_github)
            self.footer.addWidget(github_btn)
        elif hasattr(self, "status_combo"):
            save_btn = QPushButton("保存状态")
            save_btn.setObjectName("Primary")
            save_btn.setCursor(Qt.PointingHandCursor)
            save_btn.clicked.connect(self._save_status)
            self.footer.addWidget(save_btn)

        self.add_button("关闭", "Secondary", self.accept)

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(feedback_as_text(self.item))

    def _open_github(self) -> None:
        url = str(self.item.get("html_url") or GITHUB_ISSUES_PAGE)
        QDesktopServices.openUrl(QUrl(url))

    def _save_status(self) -> None:
        status = str(self.status_combo.currentData() or "open")
        self.status_requested.emit(self.item, status)
        self.accept()


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
        self._items: list[dict] = []
        self._list_worker: FeedbackListWorker | None = None
        self._submit_worker: FeedbackSubmitWorker | None = None
        self._update_worker: FeedbackUpdateWorker | None = None
        self._pending_update: tuple[str, str] | None = None
        self._fallback_payload: dict | None = None
        self._closed = False
        self._inbox_loaded = False
        self._build()
        self.set_kind(self._kind)

    # ------------------------------------------------------------------
    def _build(self) -> None:
        self.intro = QLabel(
            "遇到 bug，或者想让 DKL 更顺手，都可以写在这里。反馈会进入猫猫的收件箱，"
            "网站和启动器共用同一套反馈。"
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
            title = QLabel("开发者模式 · LFELKEEA")
            title.setStyleSheet("font-weight: 700; font-size: 12px;")
            note = QLabel("正版账户与 Microsoft 邮箱匹配。你可以查看其他人提交的 Bug 和改进建议。")
            note.setObjectName("Tiny")
            note.setWordWrap(True)
            texts.addWidget(title)
            texts.addWidget(note)
            banner_layout.addLayout(texts, 1)
            self.body.addWidget(banner)

        self.tabs = SegmentedControl()
        self.tabs.add_item("写反馈", "compose")
        if developer_mode():
            self.tabs.add_item("收件箱", "inbox")
            self.tabs.changed.connect(self._switch_tab)
        else:
            self.tabs.setVisible(False)
        self.body.addWidget(self.tabs)

        self.stack = QStackedWidget()
        self.stack.setMinimumHeight(440)
        self.compose_page = self._build_compose()
        self.inbox_page = self._build_inbox()
        self.stack.addWidget(self.compose_page)
        self.stack.addWidget(self.inbox_page)
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
            "不会自动收集账号令牌或存档内容。反馈中心暂时离线时，可以改用 GitHub "
            "公开提交或开发者邮箱。"
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
        self.github_btn = QPushButton("公开提交到 GitHub")
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
        self.submit_btn = QPushButton("提交反馈")
        self.submit_btn.setObjectName("Primary")
        self.submit_btn.setCursor(Qt.PointingHandCursor)
        self.submit_btn.setMinimumWidth(108)
        self.submit_btn.clicked.connect(self._submit)
        actions.addWidget(copy_btn)
        actions.addStretch(1)
        actions.addWidget(self.submit_btn)
        layout.addLayout(actions)

        outer.addWidget(card)
        outer.addStretch(1)
        return page

    def _build_inbox(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)

        head = QHBoxLayout()
        head.setSpacing(8)
        title = QLabel("其他人提交的反馈")
        title.setObjectName("H2")
        self.inbox_count = badge("0 条", "blue")
        head.addWidget(title)
        head.addWidget(self.inbox_count)
        head.addStretch(1)
        github_btn = icon_button(
            "external", "打开 GitHub Issues", colors()["muted"], 30, 15
        )
        github_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(GITHUB_ISSUES_PAGE))
        )
        self.refresh_btn = icon_button(
            "refresh", "刷新反馈列表", colors()["muted"], 30, 15
        )
        self.refresh_btn.clicked.connect(self._load_inbox)
        head.addWidget(github_btn)
        head.addWidget(self.refresh_btn)
        layout.addLayout(head)

        filters = QHBoxLayout()
        filters.setSpacing(8)
        self.filter_status = QComboBox()
        self.filter_status.addItem("全部状态", "")
        self.filter_status.addItem("待处理", "open")
        self.filter_status.addItem("已处理", "resolved")
        self.filter_status.addItem("已归档", "archived")
        self.filter_status.currentIndexChanged.connect(
            lambda _index: self._render_inbox()
        )
        self.filter_kind = QComboBox()
        self.filter_kind.addItem("全部类型", "")
        for value in KIND_ORDER:
            self.filter_kind.addItem(KIND_LABELS[value], value)
        self.filter_kind.currentIndexChanged.connect(
            lambda _index: self._render_inbox()
        )
        filters.addWidget(self.filter_status)
        filters.addWidget(self.filter_kind)
        filters.addStretch(1)
        layout.addLayout(filters)

        self.inbox_note = QLabel("打开收件箱后会读取反馈中心，并合并 GitHub 公开反馈。")
        self.inbox_note.setObjectName("Tiny")
        self.inbox_note.setWordWrap(True)
        layout.addWidget(self.inbox_note)

        self.inbox_scroll = QScrollArea()
        self.inbox_scroll.setWidgetResizable(True)
        self.inbox_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.inbox_scroll.setMinimumHeight(350)
        self._reset_inbox_container()
        layout.addWidget(self.inbox_scroll, 1)
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
            self.submit_btn.setText("提交建议")
        else:
            self.title_label.setText("猫猫收到一只虫子")
            self.form_title.setText("报告一个 Bug")
            self.form_hint.setText(
                "描述发生了什么、你期待什么，以及能否稳定复现。"
                "带版本号和报错信息的反馈最有用。"
            )
            self.title_edit.setPlaceholderText("例如：安装 1.21.1 后启动时报错")
            self.submit_btn.setText("提交反馈")

    def _switch_tab(self, value: str) -> None:
        if value == "inbox":
            self.stack.setCurrentIndex(1)
            if not self._inbox_loaded:
                self._load_inbox()
        else:
            self.stack.setCurrentIndex(0)

    # ------------------------------------------------------------------
    def _current_account(self) -> Account:
        try:
            return self.main.current_account()
        except Exception:
            return Account.from_config(getattr(self.main.config, "account", {}))

    def _client_id(self) -> str:
        return str(getattr(self.main.config, "microsoft_client_id", "") or "").strip()

    def _save_account(self, account) -> None:
        if account is None or not getattr(account, "is_msa", False):
            return
        try:
            self.main.config.account = account.to_config()
            self.main.config.save()
            signal = getattr(self.main, "account_changed", None)
            if signal is not None:
                signal.emit()
        except Exception:
            pass

    def _set_status(self, text: str, kind: str = "") -> None:
        if not hasattr(self, "status_label"):
            return
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
        if self._submit_worker is not None:
            return
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
        self.fallback_box.setVisible(False)
        self.submit_btn.setEnabled(False)
        self.submit_btn.setText("正在提交…")
        self._set_status("正在把反馈送进猫猫的收件箱…", "busy")
        worker = FeedbackSubmitWorker(payload, self)
        self._submit_worker = worker
        worker.finished.connect(self._submit_finished)
        worker.start()

    def _submit_finished(self, ok: bool, message: str, feedback_id: str) -> None:
        self._submit_worker = None
        if self._closed:
            return
        self.submit_btn.setEnabled(True)
        self.submit_btn.setText(
            "提交建议" if self._kind == "suggestion" else "提交反馈"
        )
        if ok:
            self._set_status(
                f"已经收到啦，反馈编号 {feedback_id or '已记录'}。猫猫会认真看。",
                "ok",
            )
            self.title_edit.clear()
            self.desc_edit.clear()
            self.fallback_box.setVisible(False)
            if developer_mode():
                self._inbox_loaded = False
            try:
                self.main.toast("反馈已提交，谢谢", "success")
            except Exception:
                pass
            return
        detail = message or "反馈没有成功保存。"
        self._set_status(
            f"{detail}\n反馈中心可能还在部署。可以先公开提交到 GitHub，或改用邮件发送。",
            "err",
        )
        self.fallback_box.setVisible(True)

    def _open_github_fallback(self) -> None:
        payload = self._fallback_payload or self._payload()
        if not payload.get("title") and not payload.get("description"):
            self._set_status("先写一点内容再提交。", "err")
            return
        QGuiApplication.clipboard().setText(feedback_payload_text(payload))
        QDesktopServices.openUrl(QUrl(build_github_issue_url(payload)))
        self._set_status(
            "已打开 GitHub 新建 Issue 页面，完整内容也复制到了剪贴板。"
            "公开提交后，开发者收件箱会读取到这条反馈。",
            "ok",
        )

    def _open_email_fallback(self) -> None:
        payload = self._fallback_payload or self._payload()
        QGuiApplication.clipboard().setText(feedback_payload_text(payload))
        QDesktopServices.openUrl(QUrl(build_mailto_url(payload)))
        self._set_status("已打开邮件草稿，完整内容也复制到了剪贴板。", "ok")

    def _open_site(self) -> None:
        QDesktopServices.openUrl(QUrl(FEEDBACK_SITE))

    # ------------------------------------------------------------------
    def _reset_inbox_container(self) -> QVBoxLayout:
        old = self.inbox_scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(8)
        layout.addStretch(1)
        self.inbox_scroll.setWidget(container)
        return layout

    def _load_inbox(self) -> None:
        if not developer_mode() or self._list_worker is not None:
            return
        self.refresh_btn.setEnabled(False)
        self.inbox_note.setText("正在读取反馈中心与 GitHub 公开反馈…")
        self.inbox_note.setStyleSheet(f"color: {colors()['muted']};")
        account = self._current_account()
        worker = FeedbackListWorker(account, self._client_id(), self)
        self._list_worker = worker
        worker.finished.connect(self._list_finished)
        worker.start()

    def _list_finished(self, items, account, source: str, warning: str) -> None:
        self._list_worker = None
        if self._closed:
            return
        self.refresh_btn.setEnabled(True)
        self._save_account(account)
        self._items = list(items or [])
        self._inbox_loaded = True
        if source == "center+github":
            note = f"已合并反馈中心与 GitHub 公开反馈，共 {len(self._items)} 条。"
            color = colors()["green"]
        elif source == "center":
            note = f"已连接反馈中心，共 {len(self._items)} 条反馈。"
            color = colors()["green"]
        elif source == "github":
            note = (
                f"反馈中心暂时不可用，已显示 GitHub 公开反馈，共 {len(self._items)} 条。"
                + (f"\n{warning}" if warning else "")
            )
            color = colors()["orange"]
        else:
            note = warning or "暂时没有读取到反馈。"
            color = colors()["red"]
        self.inbox_note.setText(note)
        self.inbox_note.setStyleSheet(f"color: {color}; font-size: 11px;")
        self._render_inbox()

    def _filtered_items(self) -> list[dict]:
        status = str(self.filter_status.currentData() or "")
        kind = str(self.filter_kind.currentData() or "")
        result = []
        for item in self._items:
            item_status = str(item.get("status") or "open")
            if status and item_status != status:
                continue
            if kind and str(item.get("kind") or "other") != kind:
                continue
            result.append(item)
        return result

    def _render_inbox(self) -> None:
        if not hasattr(self, "inbox_scroll"):
            return
        layout = self._reset_inbox_container()
        items = self._filtered_items()
        self.inbox_count.setText(f"{len(items)} 条")
        if not items:
            empty = QLabel(
                "没有符合筛选条件的反馈。换一个筛选，或点右上角刷新。"
                if self._items
                else "收件箱还是空的。等第一条反馈进来，猫猫会第一时间告诉你。"
            )
            empty.setObjectName("Muted")
            empty.setWordWrap(True)
            empty.setAlignment(Qt.AlignCenter)
            empty.setMinimumHeight(180)
            layout.insertWidget(0, empty)
            return
        for item in items:
            row = FeedbackRow(item)
            row.clicked.connect(self._open_detail)
            layout.insertWidget(layout.count() - 1, row)

    def _open_detail(self, item: dict) -> None:
        dialog = FeedbackDetailDialog(self, item)
        dialog.status_requested.connect(self._request_status)
        dialog.exec()

    def _request_status(self, item: dict, status: str) -> None:
        if item.get("platform") != "center" or self._update_worker is not None:
            return
        feedback_id = str(item.get("id") or "")
        if not feedback_id:
            return
        self._pending_update = (feedback_id, status)
        self.inbox_note.setText(
            f"正在把 {feedback_id} 标记为 {STATUS_LABELS.get(status, status)}…"
        )
        self.inbox_note.setStyleSheet(f"color: {colors()['accent']}; font-size: 11px;")
        worker = FeedbackUpdateWorker(
            self._current_account(),
            self._client_id(),
            feedback_id,
            status,
            self,
        )
        self._update_worker = worker
        worker.finished.connect(self._update_finished)
        worker.start()

    def _update_finished(self, ok: bool, message: str, account, item) -> None:
        self._update_worker = None
        if self._closed:
            return
        self._save_account(account)
        pending = self._pending_update
        self._pending_update = None
        if not ok:
            self.inbox_note.setText(message or "更新反馈状态失败。")
            self.inbox_note.setStyleSheet(f"color: {colors()['red']}; font-size: 11px;")
            return
        if pending and isinstance(item, dict):
            feedback_id, status = pending
            for index, existing in enumerate(self._items):
                if str(existing.get("id") or "") == feedback_id:
                    merged = dict(existing)
                    merged.update(item)
                    self._items[index] = merged
                    break
            else:
                self._items.insert(0, item)
            self.inbox_note.setText(
                f"反馈状态已更新为 {STATUS_LABELS.get(status, status)}。"
            )
            self.inbox_note.setStyleSheet(
                f"color: {colors()['green']}; font-size: 11px;"
            )
        self._render_inbox()

    # ------------------------------------------------------------------
    def reject(self) -> None:
        self._closed = True
        super().reject()
