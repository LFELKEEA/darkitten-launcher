from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..accounts import valid_player_name
from . import icons, theme
from .widgets import icon_button


class StyledDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        icon_name: str = "bolt",
        icon_color: str | None = None,
        width: int = 440,
    ) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)
        self._width = width

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.setSpacing(0)

        self.card = QFrame()
        self.card.setObjectName("DialogCard")
        self.card.setAttribute(Qt.WA_StyledBackground, True)
        shadow = QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(38)
        shadow.setOffset(0, 10)
        shadow.setColor(QColor(10, 20, 40, 80))
        self.card.setGraphicsEffect(shadow)
        outer.addWidget(self.card)

        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(10)
        icon_badge = QLabel()
        icon_badge.setFixedSize(36, 36)
        icon_badge.setAlignment(Qt.AlignCenter)
        color = icon_color or theme.colors()["accent"]
        icon_badge.setPixmap(icons.pixmap(icon_name, color, 22))
        icon_badge.setStyleSheet(
            f"background: {theme.colors()['accent_soft']}; border-radius: 10px;"
        )
        self.title_label = QLabel(title)
        self.title_label.setObjectName("DialogTitle")
        close_btn = icon_button("close", "关闭", size=28, icon_size=15)
        close_btn.clicked.connect(self.reject)
        header.addWidget(icon_badge)
        header.addWidget(self.title_label, 1)
        header.addWidget(close_btn, 0, Qt.AlignTop)
        layout.addLayout(header)

        self.body = QVBoxLayout()
        self.body.setSpacing(10)
        layout.addLayout(self.body, 1)

        self.footer = QHBoxLayout()
        self.footer.setSpacing(8)
        self.footer.addStretch(1)
        layout.addLayout(self.footer)

        self.setFixedWidth(width)
        self.adjustSize()

    def add_button(
        self, text: str, style: str = "Secondary", callback=None
    ) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName(style)
        button.setCursor(Qt.PointingHandCursor)
        button.setMinimumWidth(78)
        if callback:
            button.clicked.connect(callback)
        self.footer.addWidget(button)
        return button

    def showEvent(self, event) -> None:
        super().showEvent(event)
        parent = self.parentWidget()
        if parent:
            geo = parent.window().frameGeometry()
            self.adjustSize()
            x = geo.x() + (geo.width() - self.width()) // 2
            y = geo.y() + (geo.height() - self.height()) // 2
            self.move(x, y)


class ConfirmDialog(StyledDialog):
    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        message: str,
        primary_text: str = "确定",
        secondary_text: str = "取消",
        icon_name: str = "warning",
        icon_color: str | None = None,
        details: str = "",
    ) -> None:
        super().__init__(parent, title, icon_name, icon_color, width=460)
        text = QLabel(message)
        text.setObjectName("DialogText")
        text.setWordWrap(True)
        self.body.addWidget(text)
        if details:
            detail_box = QFrame()
            detail_box.setObjectName("SoftCard")
            detail_box.setAttribute(Qt.WA_StyledBackground, True)
            box_layout = QVBoxLayout(detail_box)
            box_layout.setContentsMargins(12, 10, 12, 10)
            detail_label = QLabel(details)
            detail_label.setObjectName("Tiny")
            detail_label.setWordWrap(True)
            box_layout.addWidget(detail_label)
            self.body.addWidget(detail_box)

        self.add_button(secondary_text, "Secondary", self.reject)
        self.add_button(primary_text, "Primary", self.accept)


class NoticeDialog(StyledDialog):
    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        message: str,
        button_text: str = "知道了",
        icon_name: str = "bolt",
        secondary_text: str = "",
    ) -> None:
        super().__init__(parent, title, icon_name, width=440)
        self._action = False
        text = QLabel(message)
        text.setObjectName("DialogText")
        text.setWordWrap(True)
        text.setMinimumWidth(380)
        self.body.addWidget(text)

        if secondary_text:
            self.add_button(secondary_text, "Secondary", self.reject)

        def primary() -> None:
            self._action = True
            self.accept()

        self.add_button(button_text, "Primary", primary)

    @property
    def action_clicked(self) -> bool:
        return self._action


class TextInputDialog(StyledDialog):
    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        message: str,
        value: str,
        button_text: str = "保存",
    ) -> None:
        super().__init__(parent, title, "user", width=440)
        self._result = value

        text = QLabel(message)
        text.setObjectName("DialogText")
        text.setWordWrap(True)
        self.body.addWidget(text)

        self.edit = QLineEdit(value)
        self.edit.setMaxLength(16)
        self.edit.setPlaceholderText("3-16 位字母、数字或下划线")
        self.edit.returnPressed.connect(self._save)
        self.body.addWidget(self.edit)

        self.error = QLabel("")
        self.error.setStyleSheet(f"color: {theme.colors()['red']}; font-size: 11px;")
        self.error.setVisible(False)
        self.body.addWidget(self.error)

        self.add_button("取消", "Secondary", self.reject)
        self.add_button(button_text, "Primary", self._save)
        self.edit.setFocus()

    def _save(self) -> None:
        value = self.edit.text().strip()
        if not valid_player_name(value):
            self.error.setText("名称需为 3-16 位字母、数字或下划线。")
            self.error.setVisible(True)
            return
        self._result = value
        self.accept()

    def value(self) -> str:
        return self._result


class IconGlow(QWidget):
    """给关于页面用的小装饰。"""

    def __init__(self, size: int = 64, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._pixmap = icons.logo_pixmap(size)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.drawPixmap(0, 0, self._pixmap)
        p.end()
