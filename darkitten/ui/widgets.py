from __future__ import annotations

import math
import random
import time
from pathlib import Path
from typing import Callable

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
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
    QPolygonF,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QButtonGroup,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..versions import LOADER_COLORS, VersionSummary
from . import icons, theme


def local_archive_paths(mime) -> list[Path]:
    """从拖拽数据里挑出本地 .zip / .dklpack / .mrpack 文件。"""
    if mime is None or not mime.hasUrls():
        return []
    paths: list[Path] = []
    seen: set[str] = set()
    for url in mime.urls():
        if not url.isLocalFile():
            continue
        path = Path(url.toLocalFile())
        try:
            if not path.is_file():
                continue
        except OSError:
            continue
        if path.suffix.lower() not in (".zip", ".dklpack", ".mrpack"):
            continue
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        paths.append(path)
    return paths


TEXT_ROLE = Qt.WA_TransparentForMouseEvents
_THEMED_ICON_BUTTONS: list[QPushButton] = []


def colors() -> dict[str, str]:
    return theme.colors()


def repolish(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def transparent_children(widget: QWidget) -> None:
    for child in widget.findChildren(QWidget):
        child.setAttribute(Qt.WA_TransparentForMouseEvents, True)


def icon_button(
    name: str,
    tooltip: str,
    color: str | None = None,
    size: int = 32,
    icon_size: int = 17,
    parent: QWidget | None = None,
) -> QPushButton:
    button = QPushButton(parent)
    button.setObjectName("IconBtn")
    button.setFixedSize(size, size)
    button.setCursor(Qt.PointingHandCursor)
    button.setToolTip(tooltip)
    c = color or colors()["text2"]
    button.setIcon(icons.icon(name, c, icon_size))
    button.setIconSize(QSize(icon_size, icon_size))
    try:
        button._darkitten_icon_spec = (name, color, icon_size)  # type: ignore[attr-defined]
    except Exception:
        pass
    _THEMED_ICON_BUTTONS.append(button)
    return button


def refresh_themed_icons() -> None:
    c = colors()
    alive: list[QPushButton] = []
    for button in _THEMED_ICON_BUTTONS:
        try:
            spec = getattr(button, "_darkitten_icon_spec", None)
            if not spec:
                continue
            name, color, size = spec
            button.setIcon(icons.icon(name, color or c["text2"], size))
            alive.append(button)
        except (RuntimeError, ReferenceError):
            continue
    _THEMED_ICON_BUTTONS[:] = alive


def badge(text: str, kind: str = "gray") -> QLabel:
    label = QLabel(text)
    label.setProperty("badge", kind)
    label.setAlignment(Qt.AlignCenter)
    label.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    return label


def hline() -> QFrame:
    line = QFrame()
    line.setObjectName("Divider")
    line.setFrameShape(QFrame.NoFrame)
    line.setFixedHeight(1)
    return line


class Card(QFrame):
    def __init__(self, parent: QWidget | None = None, soft: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("SoftCard" if soft else "Card")
        self.setAttribute(Qt.WA_StyledBackground, True)


class Surface(QWidget):
    """带样式的普通容器，避免 QWidget 不绘制背景。"""

    def __init__(self, object_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setAttribute(Qt.WA_StyledBackground, True)


class NavButton(QPushButton):
    def __init__(
        self, text: str, icon_name: str, accent: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(text, parent)
        self.setObjectName("Nav")
        self._icon_name = icon_name
        self._accent = accent
        self._active = False
        self.setCheckable(False)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(34)
        self.setIconSize(QSize(16, 16))
        self.set_active(False)

    def set_active(self, active: bool) -> None:
        self._active = active
        c = colors()
        icon_color = self._accent if active else c["text2"]
        self.setIcon(icons.icon(self._icon_name, icon_color, 16))
        if active:
            tint = QColor(self._accent)
            self.setStyleSheet(
                f"QPushButton#Nav {{ background: rgba({tint.red()},{tint.green()},"
                f"{tint.blue()},30); color: {self._accent}; }}"
                f"QPushButton#Nav:hover {{ background: rgba({tint.red()},"
                f"{tint.green()},{tint.blue()},46); color: {self._accent}; }}"
            )
        else:
            self.setStyleSheet("")


class Switch(QAbstractButton):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(40, 22)
        self._hover = False

    def enterEvent(self, event: QEvent) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:
        c = colors()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        checked = self.isChecked()
        track = QRectF(1, 2, 38, 18)
        p.setPen(Qt.NoPen)
        if checked:
            color = QColor(c["accent"])
            if self._hover:
                color = color.lighter(110)
            p.setBrush(color)
        else:
            p.setBrush(QColor(c["border_strong"] if not self._hover else c["muted"]))
        p.drawRoundedRect(track, 9, 9)

        knob_x = 20 if checked else 3
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(QRectF(knob_x, 4, 14, 14))
        p.end()


class SegmentedControl(QWidget):
    changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._buttons: dict[str, QPushButton] = {}
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._current = ""

        self.box = QFrame()
        self.box.setObjectName("SegmentBox")
        self.box.setAttribute(Qt.WA_StyledBackground, True)
        self._layout = QHBoxLayout(self.box)
        self._layout.setContentsMargins(2, 2, 2, 2)
        self._layout.setSpacing(2)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.box)

    def add_item(self, text: str, value: str) -> None:
        button = QPushButton(text)
        button.setObjectName("Segment")
        button.setCheckable(True)
        button.setCursor(Qt.PointingHandCursor)
        button.clicked.connect(lambda _=False, v=value: self._on_click(v))
        self._group.addButton(button)
        self._layout.addWidget(button)
        self._buttons[value] = button
        if not self._current:
            button.setChecked(True)
            self._current = value

    def _on_click(self, value: str) -> None:
        if value != self._current:
            self._current = value
            self.changed.emit(value)

    def set_value(self, value: str, emit: bool = False) -> None:
        button = self._buttons.get(value)
        if not button:
            return
        button.setChecked(True)
        if value != self._current:
            self._current = value
            if emit:
                self.changed.emit(value)

    def set_item_text(self, value: str, text: str) -> None:
        button = self._buttons.get(value)
        if button is not None:
            button.setText(text)

    def current(self) -> str:
        return self._current


class Avatar(QWidget):
    def __init__(self, size: int = 52, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)
        self._pixmap = icons.skin_face_pixmap(max(24, size - 4))

    def refresh(self) -> None:
        self._pixmap = icons.skin_face_pixmap(max(24, self._size - 4))
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0.5, 0.5, self._size - 1, self._size - 1), 11, 11)
        p.setClipPath(path)

        p.fillRect(self.rect(), QColor(colors()["accent"]).darker(150))
        if self._pixmap.isNull():
            p.setPen(QColor(colors()["text"]))
            p.drawText(self.rect(), Qt.AlignCenter, "?")
        else:
            offset = (self._size - self._pixmap.width()) / 2.0
            p.drawPixmap(QPointF(offset, offset), self._pixmap)
        p.setClipping(False)

        c = colors()
        p.setPen(QPen(QColor(c["border_strong"]), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(
            QRectF(0.5, 0.5, self._size - 1, self._size - 1), 11, 11
        )
        p.end()


class GrassLogo(QWidget):
    def __init__(self, size: int = 36, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)
        self._pixmap = icons.logo_pixmap(size)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.drawPixmap(0, 0, self._pixmap)
        p.end()


class ChibiCompanion(QWidget):
    """页头里的 Q 版 OC 头像：悬停发光，点击弹跳、掉爱心并说台词。"""

    clicked = Signal(str)

    PHRASES = {
        "home": ("欢迎回来", "今天也一起玩吗", "摸头要收费", "猫猫在看着你"),
        "download": ("要哪个版本？本喵去叼", "下载源盯紧了", "别催，喵在排队", "仓库好大…"),
        "mods": ("前置装了吗？", "这个模组会咬人吗", "抽屉还有空位", "又装新玩具？"),
        "skin": ("换件衣服给本喵看", "转一圈看看嘛", "64×64 的像素魔法", "这套很适合你"),
        "room": ("房间号给本喵", "朋友来了就开门", "樱花隧道启动！", "联机要一起玩"),
        "server": ("服务器由本喵看家", "插件别打架", "内存给够了吗", "开服，冲！"),
        "settings": ("这里是猫猫的窝", "别把本喵调坏了", "设置要乖乖保存", "喜好都记下了"),
    }

    def __init__(
        self,
        size: int = 58,
        context: str = "home",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._size = size
        self._context = context
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("戳一下恶魔猫猫")
        self._pixmap = icons.chibi_face_pixmap(size * 3)
        self._hover = False
        self._press = False
        self._click_at = -10.0
        self._phrase = ""
        self._particles: list[dict[str, float | str]] = []
        self._last_tick = time.monotonic()
        self._bubble: QLabel | None = None
        self._bubble_timer = QTimer(self)
        self._bubble_timer.setSingleShot(True)
        self._bubble_timer.timeout.connect(self._hide_bubble)
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def hideEvent(self, event) -> None:
        self._hide_bubble()
        super().hideEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._press = True
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._press:
            self._press = False
            if self.rect().contains(event.position().toPoint()):
                self._poke()
                event.accept()
                return
        super().mouseReleaseEvent(event)

    def _poke(self) -> None:
        now = time.monotonic()
        self._click_at = now
        phrases = self.PHRASES.get(self._context, self.PHRASES["home"])
        self._phrase = random.choice(phrases)
        radius = self._size * 0.42
        for _ in range(7):
            angle = random.uniform(-2.8, -0.34)
            speed = random.uniform(18.0, 52.0)
            self._particles.append(
                {
                    "x": self._size / 2 + random.uniform(-radius * 0.5, radius * 0.5),
                    "y": self._size * 0.55 + random.uniform(-radius * 0.2, radius * 0.3),
                    "vx": math.cos(angle) * speed,
                    "vy": math.sin(angle) * speed - random.uniform(5.0, 16.0),
                    "life": 0.0,
                    "max": random.uniform(0.6, 1.15),
                    "kind": random.choice(("heart", "heart", "paw", "star")),
                    "size": random.uniform(2.0, 3.6),
                }
            )
        self._last_tick = now
        self._timer.start()
        self.clicked.emit(self._phrase)
        self._show_bubble()
        self.update()

    def _tick(self) -> None:
        now = time.monotonic()
        dt = min(0.05, max(0.001, now - self._last_tick))
        self._last_tick = now
        alive: list[dict[str, float | str]] = []
        for particle in self._particles:
            particle["life"] = float(particle["life"]) + dt
            if float(particle["life"]) >= float(particle["max"]):
                continue
            particle["vy"] = float(particle["vy"]) + 70.0 * dt
            particle["x"] = float(particle["x"]) + float(particle["vx"]) * dt
            particle["y"] = float(particle["y"]) + float(particle["vy"]) * dt
            alive.append(particle)
        self._particles = alive
        if not alive and now - self._click_at > 1.4:
            self._timer.stop()
        self.update()

    def _show_bubble(self) -> None:
        if not self._phrase or not self.isVisible():
            return
        window = self.window()
        if self._bubble is None:
            self._bubble = QLabel(window)
            self._bubble.setObjectName("ChibiBubble")
            self._bubble.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            self._bubble.setStyleSheet(
                "background: #FFF6F8; color: #7C1B31;"
                " border: 1px solid #E23A5E; border-radius: 10px;"
                " padding: 6px 10px; font-weight: 700;"
            )
        self._bubble.setText(self._phrase)
        self._bubble.adjustSize()
        origin = self.mapTo(window, self.rect().topLeft())
        width = self._bubble.width()
        height = self._bubble.height()
        x = origin.x() + (self.width() - width) // 2
        y = origin.y() - height - 8
        if y < 4:
            y = origin.y() + self.height() + 6
        x = max(4, min(x, max(4, window.width() - width - 4)))
        self._bubble.move(x, y)
        self._bubble.raise_()
        self._bubble.show()
        self._bubble_timer.start(2400)

    def _hide_bubble(self) -> None:
        if self._bubble is not None:
            self._bubble.hide()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0 + 1.0
        radius = min(w, h) * 0.43
        age = time.monotonic() - self._click_at
        lift = 0.0
        angle = 0.0
        scale = 1.0
        if 0.0 <= age <= 1.0:
            decay = math.exp(-age * 4.2)
            angle = 5.0 * math.sin(age * 22.0) * decay
            scale = 1.0 + 0.06 * math.sin(min(age, 0.35) * math.pi / 0.35) * math.exp(
                -age * 2.5
            )
            lift = -4.0 * math.sin(min(1.0, age / 0.3) * math.pi)
        if self._hover:
            scale *= 1.04

        glow = QRadialGradient(cx, cy, radius * 1.75)
        glow.setColorAt(0, QColor(226, 58, 94, 120 if self._hover else 70))
        glow.setColorAt(1, QColor(226, 58, 94, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(QPointF(cx, cy), radius * 1.75, radius * 1.75)

        p.setBrush(QColor(0, 0, 0, 65))
        p.drawEllipse(
            QPointF(cx, cy + radius * 0.9),
            radius * 0.72,
            radius * 0.18,
        )

        p.save()
        p.translate(cx, cy + lift)
        p.rotate(angle)
        p.scale(scale, scale)
        disc = QRectF(-radius, -radius, radius * 2, radius * 2)
        p.setBrush(QColor("#FFF6F8"))
        p.drawEllipse(disc)
        inner = disc.adjusted(2.5, 2.5, -2.5, -2.5)
        clip = QPainterPath()
        clip.addEllipse(inner)
        p.save()
        p.setClipPath(clip)
        if self._pixmap.isNull():
            p.fillRect(inner, QColor("#3D1226"))
        else:
            p.drawPixmap(inner.toRect(), self._pixmap)
        p.restore()
        p.setBrush(Qt.NoBrush)
        p.setPen(
            QPen(
                QColor(226, 58, 94, 230 if self._hover else 175),
                1.8 if self._hover else 1.5,
            )
        )
        p.drawEllipse(disc)
        hx, hy = radius * 0.72, radius * 0.64
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#E23A5E"))
        p.drawEllipse(QPointF(hx - 3.4, hy), 4.0, 4.0)
        p.drawEllipse(QPointF(hx + 3.4, hy), 4.0, 4.0)
        p.drawPolygon(
            QPolygonF(
                [
                    QPointF(hx - 7.0, hy + 1.6),
                    QPointF(hx, hy + 8.6),
                    QPointF(hx + 7.0, hy + 1.6),
                    QPointF(hx, hy + 3.2),
                ]
            )
        )
        p.restore()

        for particle in self._particles:
            life = float(particle["life"])
            span = float(particle["max"])
            alpha = int(220 * max(0.0, 1.0 - life / span))
            size = float(particle["size"])
            color = QColor(255, 150, 180, alpha)
            kind = str(particle["kind"])
            if kind == "heart":
                HeroArt._heart(
                    p,
                    QPointF(float(particle["x"]), float(particle["y"])),
                    size,
                    color,
                )
            elif kind == "paw":
                HeroArt._paw(
                    p,
                    QPointF(float(particle["x"]), float(particle["y"])),
                    size * 0.8,
                    color,
                )
            else:
                p.setPen(Qt.NoPen)
                p.setBrush(color)
                p.drawEllipse(
                    QPointF(float(particle["x"]), float(particle["y"])),
                    size * 0.4,
                    size * 0.4,
                )
        p.end()


class HeroArt(QWidget):
    """首页横幅右侧的 Q 版 OC 头像贴纸，可以戳一戳。"""

    clicked = Signal(str)
    PHRASES = (
        "喵？",
        "别戳了啦",
        "摸头要收费",
        "抓到你啦",
        "哼，变态",
        "猫爪盖章",
        "再戳就咬你",
        "想我了吗",
        "今天也要一起玩",
        "本喵在看着你哦",
        "不许偷偷装整合包",
        "耳朵不可以揪",
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(300)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("戳一下恶魔猫猫")
        self._pixmap = icons.chibi_pixmap(640)
        self._press = False
        self._hover = False
        self._click_at = -10.0
        self._phrase = ""
        self._particles: list[dict[str, float | str]] = []
        self._last_tick = time.monotonic()
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def _body_rect(self) -> QRectF:
        w, h = self.width(), self.height()
        if self._pixmap.isNull():
            return QRectF(w * 0.55, h * 0.1, 130, 130)
        side = max(120.0, h * 0.9)
        return QRectF(w * 0.63 - side / 2.0, (h - side) / 2.0, side, side)

    def _hit(self, pos: QPointF) -> bool:
        body = self._body_rect()
        dx = pos.x() - body.center().x()
        dy = pos.y() - body.center().y()
        radius = body.width() * 0.52
        return dx * dx + dy * dy <= radius * radius

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._hit(
            event.position()
        ):
            self._press = True
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._press:
            self._press = False
            if self._hit(event.position()):
                self._poke()
                event.accept()
                return
        super().mouseReleaseEvent(event)

    def _poke(self) -> None:
        now = time.monotonic()
        self._click_at = now
        self._phrase = random.choice(self.PHRASES)
        body = self._body_rect()
        for _ in range(10):
            angle = random.uniform(-2.75, -0.39)
            speed = random.uniform(26.0, 86.0)
            self._particles.append(
                {
                    "x": body.center().x()
                    + random.uniform(-body.width() * 0.42, body.width() * 0.42),
                    "y": body.center().y()
                    + random.uniform(-body.height() * 0.2, body.height() * 0.32),
                    "vx": math.cos(angle) * speed,
                    "vy": math.sin(angle) * speed - random.uniform(8.0, 26.0),
                    "life": 0.0,
                    "max": random.uniform(0.7, 1.35),
                    "kind": random.choice(("heart", "heart", "paw", "star")),
                    "size": random.uniform(3.0, 5.4),
                }
            )
        self._last_tick = now
        self._timer.start()
        self.clicked.emit(self._phrase)
        self.update()

    def _tick(self) -> None:
        now = time.monotonic()
        dt = min(0.05, max(0.001, now - self._last_tick))
        self._last_tick = now
        alive: list[dict[str, float | str]] = []
        for particle in self._particles:
            particle["life"] = float(particle["life"]) + dt
            if float(particle["life"]) >= float(particle["max"]):
                continue
            particle["vy"] = float(particle["vy"]) + 120.0 * dt
            particle["x"] = float(particle["x"]) + float(particle["vx"]) * dt
            particle["y"] = float(particle["y"]) + float(particle["vy"]) * dt
            alive.append(particle)
        self._particles = alive
        if not alive and now - self._click_at > 2.7:
            self._phrase = ""
            self._timer.stop()
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        w, h = self.width(), self.height()
        body = self._body_rect()
        cx, cy = body.center().x(), body.center().y()

        glow = QRadialGradient(cx, cy, max(80.0, h * 0.72))
        glow.setColorAt(0, QColor(226, 58, 94, 95))
        glow.setColorAt(0.55, QColor(120, 30, 60, 45))
        glow.setColorAt(1, QColor(30, 8, 20, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(QPointF(cx, cy), h * 0.72, h * 0.72)

        # 装饰圆环
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(255, 210, 225, 26), 1))
        ring = max(body.width(), body.height()) * 0.64
        p.drawEllipse(QPointF(cx, cy), ring, ring)
        p.drawEllipse(QPointF(cx, cy), ring * 1.2, ring * 1.2)

        # 星点与小爪印
        stars = [
            (0.40, 0.24, 2.0), (0.92, 0.20, 1.6), (0.86, 0.80, 1.8),
            (0.44, 0.82, 1.4), (0.52, 0.12, 1.1), (0.97, 0.55, 1.4),
        ]
        p.setPen(Qt.NoPen)
        for sx, sy, r in stars:
            p.setBrush(QColor(255, 225, 235, 130))
            p.drawEllipse(QPointF(w * sx, h * sy), r, r)

        for px, py, s in ((0.34, 0.62, 5.0), (0.94, 0.34, 4.0), (0.80, 0.90, 3.6)):
            self._paw(p, QPointF(w * px, h * py), s, QColor(255, 190, 208, 55))

        # 点击后的弹跳与歪头
        age = time.monotonic() - self._click_at
        lift = 0.0
        angle = 0.0
        scale = 1.0
        if 0.0 <= age <= 1.4:
            decay = math.exp(-age * 3.1)
            angle = 4.2 * math.sin(age * 18.0) * decay
            scale = 1.0 + 0.055 * math.sin(
                min(age, 0.55) * math.pi / 0.55
            ) * math.exp(-age * 2.0)
            lift = -8.0 * math.sin(min(1.0, age / 0.45) * math.pi)
        if self._hover:
            scale *= 1.02
            angle += 1.1

        # 贴纸投影
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 80))
        p.drawEllipse(
            QPointF(body.center().x(), body.bottom() + 2),
            body.width() * 0.4,
            max(6.0, h * 0.028),
        )

        radius = body.width() / 2.0
        p.save()
        p.translate(body.center().x(), body.center().y() + lift)
        p.rotate(angle)
        p.scale(scale, scale)

        disc = QRectF(-radius, -radius, radius * 2, radius * 2)
        p.setBrush(QColor("#FFF6F8"))
        p.drawEllipse(disc)
        inner = disc.adjusted(4, 4, -4, -4)
        clip = QPainterPath()
        clip.addEllipse(inner)
        p.save()
        p.setClipPath(clip)
        if self._pixmap.isNull():
            p.fillRect(inner, QColor("#3D1226"))
        else:
            p.drawPixmap(inner.toRect(), self._pixmap)
        p.restore()

        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(226, 58, 94, 210), 2.2))
        p.drawEllipse(disc)

        # 右下角小爱心
        hx, hy = radius * 0.74, radius * 0.64
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#E23A5E"))
        p.drawEllipse(QPointF(hx - 5, hy), 6.0, 6.0)
        p.drawEllipse(QPointF(hx + 5, hy), 6.0, 6.0)
        p.drawPolygon(
            QPolygonF(
                [
                    QPointF(hx - 11, hy + 2),
                    QPointF(hx, hy + 14),
                    QPointF(hx + 11, hy + 2),
                    QPointF(hx, hy + 4),
                ]
            )
        )
        p.restore()

        # 粒子
        for particle in self._particles:
            life = float(particle["life"])
            span = float(particle["max"])
            alpha = int(220 * max(0.0, 1.0 - life / span))
            size = float(particle["size"])
            color = QColor(255, 170, 195, alpha)
            kind = str(particle["kind"])
            if kind == "heart":
                self._heart(
                    p, QPointF(float(particle["x"]), float(particle["y"])),
                    size, color,
                )
            elif kind == "paw":
                self._paw(
                    p, QPointF(float(particle["x"]), float(particle["y"])),
                    size * 0.85, color,
                )
            else:
                p.setPen(Qt.NoPen)
                p.setBrush(color)
                p.drawEllipse(
                    QPointF(float(particle["x"]), float(particle["y"])),
                    size * 0.45, size * 0.45,
                )

        # 气泡台词
        if self._phrase and age <= 2.6:
            alpha = 255
            if age > 2.0:
                alpha = int(255 * max(0.0, (2.6 - age) / 0.6))
            bubble_w = 116.0
            bubble_h = 36.0
            bx = max(6.0, body.left() - bubble_w - 14.0)
            by = body.center().y() - bubble_h / 2.0 + lift * 0.6
            bubble = QRectF(bx, by, bubble_w, bubble_h)
            p.setOpacity(alpha / 255.0)
            p.setPen(QPen(QColor(226, 58, 94, 150), 1.4))
            p.setBrush(QColor(255, 246, 248, 245))
            p.drawRoundedRect(bubble, 11, 11)
            tail = QPolygonF(
                [
                    QPointF(bubble.right() - 2, by + bubble_h * 0.38),
                    QPointF(bubble.right() + 11, by + bubble_h * 0.5),
                    QPointF(bubble.right() - 2, by + bubble_h * 0.62),
                ]
            )
            p.setBrush(QColor(255, 246, 248, 245))
            p.drawPolygon(tail)
            p.setPen(QPen(QColor("#7C1B31")))
            font = QFont("Microsoft YaHei UI", 8)
            font.setBold(True)
            p.setFont(font)
            p.drawText(bubble, Qt.AlignCenter, self._phrase)
            p.setOpacity(1.0)

        p.end()

    @staticmethod
    def _heart(p: QPainter, center: QPointF, size: float, color: QColor) -> None:
        path = QPainterPath()
        x, y = center.x(), center.y()
        path.moveTo(x, y + size * 0.8)
        path.cubicTo(
            x - size * 1.5, y - size * 0.3,
            x - size * 0.9, y - size * 1.2,
            x, y - size * 0.45,
        )
        path.cubicTo(
            x + size * 0.9, y - size * 1.2,
            x + size * 1.5, y - size * 0.3,
            x, y + size * 0.8,
        )
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawPath(path)

    @staticmethod
    def _paw(p: QPainter, center: QPointF, size: float, color: QColor) -> None:
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawEllipse(center, size * 1.25, size)
        for dx, dy in ((-1.3, -1.5), (-0.45, -2.0), (0.45, -2.0), (1.3, -1.5)):
            p.drawEllipse(
                QPointF(center.x() + dx * size, center.y() + dy * size),
                size * 0.45,
                size * 0.55,
            )


class VersionRow(QFrame):
    clicked = Signal(str)
    double_clicked = Signal(str)
    delete_requested = Signal(str)
    export_requested = Signal(str)
    migrate_requested = Signal(str)
    settings_requested = Signal(str)

    def __init__(
        self,
        summary: VersionSummary,
        selected: bool = False,
        parent: QWidget | None = None,
        bound_loader: str = "",
    ) -> None:
        super().__init__(parent)
        self.summary = summary
        self.bound_loader = bound_loader
        self.setObjectName("SelectRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(60)
        self.setProperty("selected", "true" if selected else "false")

        accent = LOADER_COLORS.get(
            bound_loader or summary.loader, colors()["accent"]
        )
        if summary.error:
            accent = colors()["red"]

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(11)

        mark = QLabel()
        mark.setFixedSize(34, 34)
        mark.setAlignment(Qt.AlignCenter)
        mark.setPixmap(icons.pixmap("cube", accent, 22))

        info = QVBoxLayout()
        info.setSpacing(2)
        title = QHBoxLayout()
        title.setSpacing(6)
        name = QLabel(summary.id)
        name.setStyleSheet("font-weight: 700; font-size: 13px;")
        title.addWidget(name)
        loader_name = bound_loader or summary.loader
        kind = loader_name or summary.type_name
        badge_kind = "purple" if loader_name else ("green" if summary.type == "release" else "orange")
        title.addWidget(badge(kind, badge_kind))
        if bound_loader:
            title.addWidget(badge("已绑定", "blue"))
        if summary.error:
            title.addWidget(badge("损坏", "red"))
        title.addStretch(1)
        info.addLayout(title)

        meta_parts = []
        if bound_loader:
            meta_parts.append(f"加载器 {bound_loader}")
        if summary.release_time:
            meta_parts.append(summary.release_time[:10])
        if summary.java_major:
            meta_parts.append(f"Java {summary.java_major}+")
        if summary.error:
            meta_parts.append(summary.error)
        meta = QLabel(" · ".join(meta_parts) or "本地版本")
        meta.setObjectName("Tiny")
        info.addWidget(meta)

        layout.addWidget(mark)
        layout.addLayout(info, 1)

        check = QLabel()
        if selected:
            check.setPixmap(icons.pixmap("check", colors()["accent"], 18))
        check.setFixedWidth(20)
        layout.addWidget(check)

        settings_btn = icon_button(
            "settings", "版本设置：选择原版或已安装的加载器", colors()["muted"], 28, 15
        )
        settings_btn.clicked.connect(
            lambda _=False: self.settings_requested.emit(self.summary.id)
        )
        layout.addWidget(settings_btn)

        export_btn = icon_button("package", "打包此版本（含 mods / 配置）", colors()["muted"], 28, 15)
        export_btn.clicked.connect(
            lambda _=False: self.export_requested.emit(self.summary.id)
        )
        layout.addWidget(export_btn)

        migrate_btn = icon_button("migrate", "迁移此版本的存档 / Mod", colors()["muted"], 28, 15)
        migrate_btn.clicked.connect(
            lambda _=False: self.migrate_requested.emit(self.summary.id)
        )
        layout.addWidget(migrate_btn)

        delete_btn = icon_button("trash", "删除此版本", colors()["muted"], 28, 15)
        delete_btn.clicked.connect(
            lambda _=False: self.delete_requested.emit(self.summary.id)
        )
        layout.addWidget(delete_btn)

        transparent_children(self)
        settings_btn.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        export_btn.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        migrate_btn.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        delete_btn.setAttribute(Qt.WA_TransparentForMouseEvents, False)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.summary.id)
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.double_clicked.emit(self.summary.id)
        super().mouseDoubleClickEvent(event)


class LogPanel(QFrame):
    closed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("LogPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMaximumHeight(0)
        self.setVisible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 8, 18, 12)
        layout.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(8)
        dot = QLabel("●")
        dot.setStyleSheet(f"color: {colors()['green']}; font-size: 9px;")
        header.addWidget(dot)
        title = QLabel("运行日志")
        title.setStyleSheet("font-weight: 700;")
        header.addWidget(title)
        tip = QLabel("游戏输出与启动过程会显示在这里")
        tip.setObjectName("Tiny")
        header.addWidget(tip)
        header.addStretch(1)

        copy_btn = icon_button("copy", "复制日志", size=28, icon_size=15)
        clear_btn = icon_button("trash", "清空日志", size=28, icon_size=15)
        close_btn = icon_button("close", "收起", size=28, icon_size=15)
        copy_btn.clicked.connect(self.copy_all)
        clear_btn.clicked.connect(self.clear)
        close_btn.clicked.connect(self.close_panel)
        header.addWidget(copy_btn)
        header.addWidget(clear_btn)
        header.addWidget(close_btn)

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(3000)
        self.text.setMinimumHeight(100)
        self.text.setPlaceholderText("暂无日志")

        layout.addLayout(header)
        layout.addWidget(self.text, 1)

        self._animation = QPropertyAnimation(self, b"maximumHeight", self)
        self._animation.setDuration(180)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)

    def append(self, line: str) -> None:
        from datetime import datetime

        stamp = datetime.now().strftime("%H:%M:%S")
        self.text.appendPlainText(f"[{stamp}] {line}")
        bar = self.text.verticalScrollBar()
        bar.setValue(bar.maximum())

    def clear(self) -> None:
        self.text.clear()

    def copy_all(self) -> None:
        QApplication.clipboard().setText(self.text.toPlainText())

    def open_panel(self) -> None:
        if self.isVisible() and self.maximumHeight() > 0:
            return
        self.setVisible(True)
        self._animation.stop()
        self._animation.setStartValue(self.maximumHeight())
        self._animation.setEndValue(230)
        self._animation.start()

    def close_panel(self) -> None:
        if not self.isVisible():
            return
        self._animation.stop()
        self._animation.setStartValue(self.maximumHeight())
        self._animation.setEndValue(0)
        try:
            self._animation.finished.disconnect(self._hide_after)
        except (RuntimeError, TypeError):
            pass
        self._animation.finished.connect(self._hide_after)
        self._animation.start()

    def _hide_after(self) -> None:
        if self.maximumHeight() <= 1:
            self.setVisible(False)
        self.closed.emit()


class Toast(QFrame):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setLayout(QHBoxLayout())
        self.layout().setContentsMargins(14, 9, 14, 9)
        self.layout().setSpacing(8)
        self._icon = QLabel()
        self._label = QLabel()
        self._label.setWordWrap(False)
        self._label.setStyleSheet("color: white; font-weight: 600;")
        self.layout().addWidget(self._icon)
        self.layout().addWidget(self._label)

        self._effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._effect)
        self._anim = QPropertyAnimation(self._effect, b"opacity", self)
        self._anim.setDuration(180)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade_out)
        self.hide()

    def show_message(self, text: str, kind: str = "info") -> None:
        c = colors()
        if kind == "error":
            color = c["red"]
        elif kind == "success":
            color = c["green"]
        else:
            color = c["accent"]
        self._icon.setPixmap(
            icons.pixmap("check" if kind == "success" else ("warning" if kind == "error" else "bolt"), color, 15)
        )
        self._label.setText(text)
        self._label.adjustSize()
        self.adjustSize()
        parent = self.parentWidget()
        if parent:
            width = min(self.width(), parent.width() - 48)
            self.setFixedWidth(max(180, width + 48))
            self.adjustSize()
            x = (parent.width() - self.width()) // 2
            y = parent.height() - self.height() - 58
            self.move(max(16, x), max(16, y))
        self.raise_()
        self.show()
        self._effect.setOpacity(0.0)
        self._anim.stop()
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()
        self._timer.start(2600)

    def _fade_out(self) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._effect.opacity())
        self._anim.setEndValue(0.0)
        self._anim.finished.connect(self._hide_once)
        self._anim.start()

    def _hide_once(self) -> None:
        try:
            self._anim.finished.disconnect(self._hide_once)
        except (RuntimeError, TypeError):
            pass
        self.hide()


class DependencyBar(QWidget):
    """必需前置 Mod 的导航条：显示可点击的前置项目与返回按钮。"""

    dependency_clicked = Signal(str, str)
    back_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)
        self._deps: list[dict] = []
        self._projects: dict[str, dict] = {}
        self._show_back = False
        self._loading = False
        self.setVisible(False)

    @staticmethod
    def dependency_name(dep: dict, projects: dict[str, dict]) -> str:
        project_id = str(dep.get("project_id") or "")
        project = projects.get(project_id) or {}
        title = str(project.get("title") or project.get("slug") or "").strip()
        if title:
            return title
        filename = str(dep.get("file_name") or "").rsplit("/", 1)[-1]
        if filename.lower().endswith(".jar"):
            filename = filename[:-4]
        return filename or project_id or "未知前置"

    def set_state(
        self,
        deps: list[dict] | None = None,
        *,
        show_back: bool = False,
        projects: dict[str, dict] | None = None,
        loading: bool = False,
    ) -> None:
        self._deps = [
            dep
            for dep in deps or []
            if isinstance(dep, dict) and str(dep.get("project_id") or "")
        ]
        self._show_back = bool(show_back)
        self._loading = bool(loading)
        if projects:
            self._projects.update(projects)
        self._rebuild()

    def update_projects(self, projects: dict[str, dict]) -> None:
        if not projects:
            return
        self._projects.update(projects)
        if self._deps:
            self._rebuild()

    def clear(self) -> None:
        self._deps = []
        self._loading = False
        self._show_back = False
        self.setVisible(False)
        self._clear_widgets()

    def _clear_widgets(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()

    def _rebuild(self) -> None:
        self._clear_widgets()
        if self._show_back:
            back = QPushButton("返回")
            back.setObjectName("Ghost")
            back.setCursor(Qt.PointingHandCursor)
            back.setIcon(icons.icon("chevron-left", colors()["text2"], 12))
            back.setStyleSheet("font-size: 11px; padding: 3px 8px;")
            back.setToolTip("返回上一个 Mod")
            back.clicked.connect(self.back_clicked.emit)
            self._layout.addWidget(back)

        if self._deps:
            for dep in self._deps:
                project_id = str(dep.get("project_id") or "")
                version_id = str(dep.get("version_id") or "")
                name = self.dependency_name(dep, self._projects)
                label = name if len(name) <= 22 else name[:21] + "…"
                button = QPushButton(f"前置：{label}")
                button.setObjectName("Ghost")
                button.setCursor(Qt.PointingHandCursor)
                button.setIcon(icons.icon("download", colors()["text2"], 12))
                button.setStyleSheet("font-size: 11px; padding: 3px 8px;")
                tip = f"点击查看并下载前置 Mod：{name}"
                if dep.get("file_name"):
                    tip += f"\n当前文件要求：{dep.get('file_name')}"
                button.setToolTip(tip)
                button.clicked.connect(
                    lambda _=False, pid=project_id, vid=version_id: self.dependency_clicked.emit(pid, vid)
                )
                self._layout.addWidget(button)
        elif self._loading:
            hint = QLabel("正在读取前置 Mod…")
            hint.setObjectName("Tiny")
            self._layout.addWidget(hint)

        self._layout.addStretch(1)
        self.setVisible(self._show_back or bool(self._deps) or self._loading)
