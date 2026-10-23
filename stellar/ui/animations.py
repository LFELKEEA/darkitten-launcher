from __future__ import annotations

import math

from PySide6.QtCore import (
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QRadialGradient,
)
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from ..config import APP_NAME, APP_SHORT_NAME, APP_TAGLINE, APP_VERSION
from . import icons

SPLASH_NOTES = (
    "正在磨爪子…",
    "正在偷看你的存档…",
    "正在把苦力怕哄睡…",
    "正在数箱子里的曲奇…",
)


def _center_on_screen(widget: QWidget) -> None:
    screen = QApplication.primaryScreen()
    if not screen:
        return
    area = screen.availableGeometry()
    widget.move(
        area.x() + (area.width() - widget.width()) // 2,
        area.y() + (area.height() - widget.height()) // 2,
    )


def _draw_paw(
    p: QPainter, center: QPointF, size: float, color: QColor, rotation: float = 0.0
) -> None:
    p.save()
    p.translate(center)
    p.rotate(rotation)
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawEllipse(QPointF(0, size * 0.22), size * 1.05, size * 0.86)
    for dx, dy in ((-1.10, -1.00), (-0.38, -1.48), (0.38, -1.48), (1.10, -1.00)):
        p.drawEllipse(
            QPointF(dx * size, dy * size), size * 0.42, size * 0.52
        )
    p.restore()


def _draw_cat_ears(p: QPainter, rect: QRectF, color: QColor) -> None:
    """在圆角卡片顶部画一对猫耳。"""
    left = QPolygonF(
        [
            QPointF(rect.left() + rect.width() * 0.16, rect.top() + 10),
            QPointF(rect.left() + rect.width() * 0.24, rect.top() - 30),
            QPointF(rect.left() + rect.width() * 0.36, rect.top() + 6),
        ]
    )
    right = QPolygonF(
        [
            QPointF(rect.left() + rect.width() * 0.64, rect.top() + 6),
            QPointF(rect.left() + rect.width() * 0.76, rect.top() - 30),
            QPointF(rect.left() + rect.width() * 0.84, rect.top() + 10),
        ]
    )
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawPolygon(left)
    p.drawPolygon(right)
    inner = QColor("#E23A5E")
    inner.setAlpha(90)
    p.setBrush(inner)
    p.drawPolygon(
        QPolygonF(
            [
                QPointF(rect.left() + rect.width() * 0.205, rect.top() - 2),
                QPointF(rect.left() + rect.width() * 0.247, rect.top() - 19),
                QPointF(rect.left() + rect.width() * 0.30, rect.top() + 0),
            ]
        )
    )
    p.drawPolygon(
        QPolygonF(
            [
                QPointF(rect.left() + rect.width() * 0.70, rect.top() + 0),
                QPointF(rect.left() + rect.width() * 0.753, rect.top() - 19),
                QPointF(rect.left() + rect.width() * 0.795, rect.top() - 2),
            ]
        )
    )


class SplashScreen(QWidget):
    """启动器开场：恶魔小猫贴纸、猫耳卡片和磨爪子进度。"""

    finished = Signal()

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.SplashScreen
            | Qt.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        # 不抢焦点，也不接收键盘输入：启动阶段按键不会影响窗口生命周期。
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedSize(560, 340)
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(icons.app_icon())
        self._pixmap: QPixmap = icons.chibi_pixmap(460)
        self._progress = 0.0
        self._elapsed = 0
        self._done = False
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._fade: QPropertyAnimation | None = None
        _center_on_screen(self)

    def start(self) -> None:
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(260)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.setEasingCurve(QEasingCurve.OutCubic)
        self._fade.start()
        self._timer.start()

    def _tick(self) -> None:
        if self._done:
            return
        self._elapsed += 16
        self._progress = min(1.0, self._elapsed / 1900.0)
        self.update()
        if self._progress >= 1.0 and not self._done:
            self._finish()

    def _finish(self) -> None:
        if self._done:
            return
        self._done = True
        self._timer.stop()
        if self._fade:
            self._fade.stop()
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(300)
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(0.0)
        self._fade.setEasingCurve(QEasingCurve.InCubic)
        self._fade.finished.connect(self.finished.emit)
        self._fade.start()

    def mousePressEvent(self, event) -> None:
        self._finish()
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        # 启动画面不响应键盘，按键一律吞掉。
        event.accept()

    def keyReleaseEvent(self, event) -> None:
        event.accept()

    def closeEvent(self, event) -> None:
        if not self._done:
            self._done = True
            self._timer.stop()
            self.finished.emit()
        super().closeEvent(event)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)

        card = QRectF(18, 32, self.width() - 36, self.height() - 48)
        _draw_cat_ears(p, card, QColor("#1C1019"))

        shadow = QColor(0, 0, 0, 120)
        p.setPen(Qt.NoPen)
        p.setBrush(shadow)
        p.drawRoundedRect(card.translated(0, 8), 24, 24)

        gradient = QLinearGradient(card.topLeft(), card.bottomRight())
        gradient.setColorAt(0, QColor("#24101C"))
        gradient.setColorAt(0.55, QColor("#160D15"))
        gradient.setColorAt(1, QColor("#2A0C1A"))
        p.setBrush(gradient)
        p.drawRoundedRect(card, 24, 24)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(226, 58, 94, 75), 1))
        p.drawRoundedRect(card, 24, 24)

        glow = QRadialGradient(card.center(), card.height() * 0.75)
        glow.setColorAt(0, QColor(226, 58, 94, 46))
        glow.setColorAt(1, QColor(226, 58, 94, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(card.center(), card.height() * 0.75, card.height() * 0.75)

        # 左侧圆形肖像
        radius = 58.0
        center = QPointF(card.left() + 88, card.top() + 112)
        disc = QRectF(
            center.x() - radius, center.y() - radius, radius * 2, radius * 2
        )
        p.setBrush(QColor("#FFF4F7"))
        p.drawEllipse(disc)
        clip = QPainterPath()
        clip.addEllipse(disc.adjusted(5, 5, -5, -5))
        p.save()
        p.setClipPath(clip)
        if self._pixmap.isNull():
            p.fillRect(disc, QColor("#3D1226"))
        else:
            p.drawPixmap(disc.adjusted(5, 5, -5, -5).toRect(), self._pixmap)
        p.restore()
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor("#E23A5E"), 2))
        p.drawEllipse(disc)

        text_left = card.left() + 170
        p.setPen(QColor("#F8E9EF"))
        title_font = QFont("Microsoft YaHei UI", 21)
        title_font.setBold(True)
        p.setFont(title_font)
        p.drawText(
            QRectF(text_left, card.top() + 42, card.width() - 220, 36),
            Qt.AlignLeft | Qt.AlignVCenter,
            APP_NAME,
        )

        p.setPen(QColor("#E23A5E"))
        tag_font = QFont("Microsoft YaHei UI", 8)
        tag_font.setBold(True)
        tag_font.setLetterSpacing(QFont.AbsoluteSpacing, 2.0)
        p.setFont(tag_font)
        p.drawText(
            QRectF(text_left + 1, card.top() + 77, card.width() - 220, 20),
            Qt.AlignLeft | Qt.AlignVCenter,
            f"{APP_SHORT_NAME} · {APP_TAGLINE}",
        )

        p.setPen(QColor(240, 220, 229, 210))
        note_font = QFont("Microsoft YaHei UI", 10)
        p.setFont(note_font)
        index = min(len(SPLASH_NOTES) - 1, int(self._progress * len(SPLASH_NOTES)))
        p.drawText(
            QRectF(text_left, card.top() + 112, card.width() - 220, 24),
            Qt.AlignLeft | Qt.AlignVCenter,
            SPLASH_NOTES[index],
        )

        # 爪印进度
        base_y = card.bottom() - 66
        total = 5
        filled = self._progress * total
        for i in range(total):
            x = text_left + 16 + i * 38
            active = i < int(filled)
            pulse = 1.0
            if active and i == int(filled) - 1:
                pulse = 1.0 + 0.16 * math.sin(self._elapsed / 90.0)
            color = QColor("#E23A5E") if active else QColor(255, 255, 255, 30)
            _draw_paw(p, QPointF(x, base_y), 7.2 * pulse, color)

        track = QRectF(text_left, card.bottom() - 40, card.width() - 250, 4)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 22))
        p.drawRoundedRect(track, 2, 2)
        p.setBrush(QColor("#E23A5E"))
        p.drawRoundedRect(
            QRectF(track.left(), track.top(), track.width() * self._progress, 4), 2, 2
        )

        p.setPen(QColor(255, 255, 255, 70))
        foot_font = QFont("Microsoft YaHei UI", 8)
        p.setFont(foot_font)
        p.drawText(
            QRectF(card.left() + 32, card.bottom() - 28, card.width() - 64, 18),
            Qt.AlignLeft | Qt.AlignVCenter,
            f"v{APP_VERSION} · 点击任意处跳过",
        )
        p.end()


class GameLoadingOverlay(QWidget):
    """启动游戏时覆盖主窗口的加载动画。"""

    cancel_requested = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.setVisible(False)
        self._pixmap = icons.chibi_pixmap(420)
        self._angle = 0.0
        self._elapsed = 0
        self._status = "正在准备启动…"
        self._version = ""
        self._closing = False
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

        self.cancel_btn = QPushButton("收回猫爪（取消启动）", self)
        self.cancel_btn.setObjectName("Secondary")
        self.cancel_btn.setCursor(Qt.PointingHandCursor)
        self.cancel_btn.setFixedSize(184, 34)
        self.cancel_btn.clicked.connect(self._request_cancel)
        self.cancel_btn.hide()

    def show_loading(self, version: str, status: str = "") -> None:
        self._version = version
        self._status = status or "正在准备启动…"
        self._elapsed = 0
        self._closing = False
        self.cancel_btn.setEnabled(True)
        self.cancel_btn.setText("收回猫爪（取消启动）")
        self.cancel_btn.show()
        self.setGeometry(self.parentWidget().rect())
        self.raise_()
        self.show()
        self._timer.start()

    def set_status(self, text: str) -> None:
        if text:
            self._status = text
            self.update()

    def finish(self, message: str = "世界已加载，去玩吧喵。", delay_ms: int = 650) -> None:
        if not self.isVisible():
            return
        self._closing = True
        self._status = message
        self.cancel_btn.hide()
        self.update()
        QTimer.singleShot(max(0, delay_ms), self._hide_now)

    def abort(self) -> None:
        self._closing = True
        self._timer.stop()
        self.hide()
        self.cancel_btn.hide()

    def _hide_now(self) -> None:
        self._timer.stop()
        self.hide()
        self.cancel_btn.hide()

    def _request_cancel(self) -> None:
        if not self._closing:
            self.cancel_btn.setEnabled(False)
            self.cancel_btn.setText("正在收回猫爪…")
            self.cancel_requested.emit()

    def _tick(self) -> None:
        self._elapsed += 16
        self._angle = (self._angle + 2.2) % 360.0
        self.update()

    def resizeEvent(self, event) -> None:
        self.cancel_btn.move(
            (self.width() - self.cancel_btn.width()) // 2,
            int(self.height() * 0.5) + 176,
        )
        super().resizeEvent(event)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        w, h = self.width(), self.height()

        p.fillRect(self.rect(), QColor(12, 7, 11, 214))
        glow = QRadialGradient(w / 2, h / 2 - 40, max(w, h) * 0.42)
        glow.setColorAt(0, QColor(226, 58, 94, 64))
        glow.setColorAt(1, QColor(226, 58, 94, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(QPointF(w / 2, h / 2 - 40), max(w, h) * 0.42, max(w, h) * 0.42)

        cx, cy = w / 2, h / 2 - 54
        radius = 62.0
        disc = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)
        p.setBrush(QColor(0, 0, 0, 80))
        p.drawEllipse(disc.translated(0, 7))
        p.setBrush(QColor("#FFF4F7"))
        p.drawEllipse(disc)
        clip = QPainterPath()
        clip.addEllipse(disc.adjusted(5, 5, -5, -5))
        p.save()
        p.setClipPath(clip)
        if self._pixmap.isNull():
            p.fillRect(disc, QColor("#3D1226"))
        else:
            p.drawPixmap(disc.adjusted(5, 5, -5, -5).toRect(), self._pixmap)
        p.restore()
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor("#E23A5E"), 2))
        p.drawEllipse(disc)

        # 环绕的爪印
        for i in range(6):
            angle = math.radians(self._angle + i * 60)
            px = cx + math.cos(angle) * (radius + 26)
            py = cy + math.sin(angle) * (radius + 26)
            alpha = 90 + int(120 * (0.5 + 0.5 * math.sin(angle)))
            _draw_paw(
                p,
                QPointF(px, py),
                5.5,
                QColor(255, 190, 208, alpha),
                math.degrees(angle) + 90,
            )

        # 旋转弧线
        arc = QRectF(cx - radius - 11, cy - radius - 11, (radius + 11) * 2, (radius + 11) * 2)
        pen = QPen(QColor("#E23A5E"), 2.5)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        start = int(-self._angle * 16)
        p.drawArc(arc, start, 70 * 16)

        p.setPen(QColor("#F8E9EF"))
        title_font = QFont("Microsoft YaHei UI", 17)
        title_font.setBold(True)
        p.setFont(title_font)
        p.drawText(
            QRectF(0, cy + radius + 32, w, 32),
            Qt.AlignCenter,
            "正在把猫猫送进世界…",
        )

        p.setPen(QColor(236, 210, 220, 210))
        status_font = QFont("Microsoft YaHei UI", 10)
        p.setFont(status_font)
        p.drawText(
            QRectF(w * 0.1, cy + radius + 68, w * 0.8, 26),
            Qt.AlignCenter,
            self._status,
        )

        if self._version:
            badge_w, badge_h = 210.0, 26.0
            badge = QRectF(
                cx - badge_w / 2, cy + radius + 104, badge_w, badge_h
            )
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 26))
            p.drawRoundedRect(badge, 13, 13)
            p.setPen(QColor(255, 220, 230, 210))
            small = QFont("Microsoft YaHei UI", 9)
            p.setFont(small)
            p.drawText(badge, Qt.AlignCenter, self._version)

        if not self._closing:
            p.setPen(QColor(255, 255, 255, 55))
            foot = QFont("Microsoft YaHei UI", 8)
            p.setFont(foot)
            p.drawText(
                QRectF(0, h - 38, w, 20),
                Qt.AlignCenter,
                "如果猫猫卡住了，可以随时收回猫爪",
            )
        p.end()
