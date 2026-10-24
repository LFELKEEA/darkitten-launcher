from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QImage,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QRadialGradient,
    QTransform,
    QWheelEvent,
)
from PySide6.QtWidgets import QWidget

from ..skins import default_skin
from .widgets import colors


@dataclass
class _Part:
    name: str
    center: tuple[float, float, float]
    width: float
    height: float
    depth: float
    uv: tuple[int, int]
    overlay: tuple[int, int] | None = None
    mirror: bool = False
    use_overlay: bool = True


_FACES = (
    ("front", (0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
    ("back", (0.0, 0.0, -1.0), (-1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
    ("left", (1.0, 0.0, 0.0), (0.0, 0.0, -1.0), (0.0, 1.0, 0.0)),
    ("right", (-1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),
    ("top", (0.0, 1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, -1.0)),
    ("bottom", (0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
)


def _vec_scale(vector: tuple[float, float, float], value: float):
    return tuple(component * value for component in vector)


def _vec_add(a: tuple[float, float, float], b: tuple[float, float, float]):
    return tuple(x + y for x, y in zip(a, b))


def _face_rect(uv: tuple[int, int], width: float, height: float, depth: float, face: str):
    u, v = uv
    w, h, d = int(width), int(height), int(depth)
    if face == "right":
        return QRect(u, v + d, d, h)
    if face == "front":
        return QRect(u + d, v + d, w, h)
    if face == "left":
        return QRect(u + d + w, v + d, d, h)
    if face == "back":
        return QRect(u + 2 * d + w, v + d, w, h)
    if face == "top":
        return QRect(u + d, v, w, d)
    return QRect(u + d + w, v, w, d)


def rotate_model_point(
    point: tuple[float, float, float],
    yaw_degrees: float,
    pitch_degrees: float,
    pivot: tuple[float, float, float] = (0.0, 16.0, 0.0),
) -> tuple[float, float, float]:
    """绕模型中心旋转，返回相对旋转中心的坐标。

    Minecraft 人物模型从脚底 y=0 延伸到头顶 y=32，视觉中心在 y=16。
    如果直接绕脚底旋转，俯仰视角会让模型像被钉在地上一样被拉长。
    """
    x, y, z = point
    x -= pivot[0]
    y -= pivot[1]
    z -= pivot[2]
    yaw = math.radians(yaw_degrees)
    pitch = math.radians(pitch_degrees)
    sin_y, cos_y = math.sin(yaw), math.cos(yaw)
    sin_x, cos_x = math.sin(pitch), math.cos(pitch)
    x1 = x * cos_y + z * sin_y
    z1 = -x * sin_y + z * cos_y
    y2 = y * cos_x - z1 * sin_x
    z2 = y * sin_x + z1 * cos_x
    return x1, y2, z2


def _region_has_alpha(image: QImage, u: int, v: int, w: int, h: int) -> bool:
    if image.isNull():
        return False
    left = max(0, u)
    top = max(0, v)
    right = min(image.width(), u + w)
    bottom = min(image.height(), v + h)
    if right <= left or bottom <= top:
        return False
    step = 1 if (right - left) * (bottom - top) <= 900 else 2
    for y in range(top, bottom, step):
        for x in range(left, right, step):
            if image.pixelColor(x, y).alpha() > 8:
                return True
    return False


class SkinPreviewWidget(QWidget):
    """用 QPainter 软件渲染的 Minecraft 皮肤 3D 预览。

    不依赖 OpenGL / numpy，支持拖动旋转、滚轮缩放、双击复位和翻面动画。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(300, 360)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self._image = default_skin()
        self._legacy = False
        self._variant = "classic"
        self._yaw = 26.0
        self._pitch = 7.0
        self._zoom = 1.0
        self._drag_pos: QPointF | None = None
        self._drag_yaw = 0.0
        self._drag_pitch = 0.0
        self._animation = QTimer(self)
        self._animation.setInterval(16)
        self._animation.timeout.connect(self._animate_step)
        self._anim_from = 0.0
        self._anim_to = 0.0
        self._anim_progress = 1.0
        self._mirror_left_arm = False
        self._mirror_left_leg = False
        self._arm_width = 4
        self.setCursor(Qt.OpenHandCursor)

    # ------------------------------------------------------------------
    def sizeHint(self) -> QSize:
        return QSize(420, 470)

    def set_skin(self, image: QImage, legacy: bool = False) -> None:
        self._image = image.convertToFormat(QImage.Format_ARGB32) if not image.isNull() else default_skin()
        self._legacy = bool(legacy or self._image.height() == 32)
        self._refresh_limb_fallback()
        self.update()

    def set_variant(self, variant: str) -> None:
        variant = "slim" if str(variant).lower() == "slim" else "classic"
        if variant == self._variant:
            return
        self._variant = variant
        self._arm_width = 3 if variant == "slim" else 4
        self._refresh_limb_fallback()
        self.update()

    def variant(self) -> str:
        return self._variant

    def is_legacy(self) -> bool:
        return self._legacy

    def _refresh_limb_fallback(self) -> None:
        self._arm_width = 3 if self._variant == "slim" else 4
        if self._legacy:
            self._mirror_left_arm = True
            self._mirror_left_leg = True
            return
        arm_rect_w = 2 * 4 + self._arm_width
        self._mirror_left_arm = not _region_has_alpha(
            self._image, 32, 48, arm_rect_w, 16
        )
        self._mirror_left_leg = not _region_has_alpha(
            self._image, 16, 48, 16, 16
        )

    # ------------------------------------------------------------------
    def flip(self) -> None:
        self._animation.stop()
        self._anim_from = self._yaw
        self._anim_to = self._yaw + 180.0
        self._anim_progress = 0.0
        self._animation.start()

    def reset_view(self) -> None:
        self._animation.stop()
        self._yaw = 26.0
        self._pitch = 7.0
        self._zoom = 1.0
        self.update()

    def _animate_step(self) -> None:
        self._anim_progress = min(1.0, self._anim_progress + 0.055)
        eased = 1.0 - (1.0 - self._anim_progress) ** 3
        self._yaw = self._anim_from + (self._anim_to - self._anim_from) * eased
        if self._anim_progress >= 1.0:
            self._animation.stop()
            self._yaw = ((self._yaw + 180.0) % 360.0) - 180.0
        self.update()

    # ------------------------------------------------------------------
    def _parts(self) -> list[_Part]:
        arm = float(self._arm_width)
        parts = [
            _Part("head", (0.0, 28.0, 0.0), 8, 8, 8, (0, 0), (32, 0)),
            _Part("body", (0.0, 18.0, 0.0), 8, 12, 4, (16, 16), (16, 32)),
            _Part(
                "right_arm",
                (-(arm / 2 + 4), 18.0, 0.0),
                arm,
                12,
                4,
                (40, 16),
                (40, 32),
                False,
                not self._legacy,
            ),
            _Part(
                "left_arm",
                (arm / 2 + 4, 18.0, 0.0),
                arm,
                12,
                4,
                (40, 16) if self._legacy else (32, 48),
                (40, 32) if self._legacy else (48, 48),
                self._mirror_left_arm,
                not self._legacy,
            ),
            _Part("right_leg", (-2.0, 6.0, 0.0), 4, 12, 4, (0, 16), (0, 32)),
            _Part(
                "left_leg",
                (2.0, 6.0, 0.0),
                4,
                12,
                4,
                (0, 16) if self._legacy else (16, 48),
                (0, 32) if self._legacy else (0, 48),
                self._mirror_left_leg,
                not self._legacy,
            ),
        ]
        return parts

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, False)
        c = colors()
        width, height = self.width(), self.height()

        rect = QRectF(0.5, 0.5, width - 1, height - 1)
        clip = QPainterPath()
        clip.addRoundedRect(rect, 13, 13)
        p.setClipPath(clip)

        bg = QLinearGradient(0, 0, 0, height)
        bg.setColorAt(0.0, QColor(c["card2"]))
        bg.setColorAt(1.0, QColor(c["card"]))
        p.fillRect(rect, bg)

        glow = QRadialGradient(width * 0.5, height * 0.46, max(width, height) * 0.58)
        tint = QColor(c["accent"])
        tint.setAlpha(30 if c is colors() else 30)
        glow.setColorAt(0.0, tint)
        glow.setColorAt(1.0, QColor(tint.red(), tint.green(), tint.blue(), 0))
        p.fillRect(rect, glow)

        # 细网格，增强“三维展示台”的感觉
        grid_pen = QPen(QColor(c["border"]))
        grid_pen.setWidth(1)
        p.setPen(grid_pen)
        step = 28
        for x in range(0, width, step):
            p.drawLine(x, 0, x, height)
        for y in range(0, height, step):
            p.drawLine(0, y, width, y)

        # 以模型整体高 32 个单位计算缩放
        base = min(width / 22.0, height / 38.0)
        scale = base * self._zoom
        cx = width * 0.5
        cy = height * 0.51
        floor_y = cy + 16.2 * scale

        shadow = QRadialGradient(cx, floor_y, max(30.0, 8.4 * scale))
        shadow.setColorAt(0.0, QColor(15, 23, 42, 70))
        shadow.setColorAt(1.0, QColor(15, 23, 42, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(shadow)
        p.drawEllipse(QPointF(cx, floor_y), max(30.0, 8.4 * scale), max(8.0, 2.4 * scale))

        p.setBrush(Qt.NoBrush)

        def rotate(point: tuple[float, float, float]):
            return rotate_model_point(point, self._yaw, self._pitch)

        def project(point: tuple[float, float, float]):
            x1, y2, depth = rotate(point)
            # rotate_model_point 已把原点移到人物中心，因此这里直接以 cy 投影。
            return QPointF(cx + x1 * scale, cy - y2 * scale), depth

        faces: list[tuple[float, QPolygonF, QImage]] = []
        for part in self._parts():
            layers: list[tuple[float, tuple[int, int] | None]] = [(0.0, None)]
            if part.overlay and part.use_overlay and not self._legacy:
                if self._has_overlay(part):
                    layers.append((0.5, part.overlay))
            for inflate, overlay_uv in layers:
                box_w = part.width + inflate * 2
                box_h = part.height + inflate * 2
                box_d = part.depth + inflate * 2
                uv = overlay_uv or part.uv
                for face, normal, u_axis, v_axis in _FACES:
                    length = {"front": box_w, "back": box_w, "left": box_d,
                              "right": box_d, "top": box_w, "bottom": box_w}[face]
                    v_length = {"front": box_h, "back": box_h, "left": box_h,
                                "right": box_h, "top": box_d, "bottom": box_d}[face]
                    normal_size = {"front": box_d, "back": box_d, "left": box_w,
                                   "right": box_w, "top": box_h, "bottom": box_h}[face]
                    fc = _vec_add(part.center, _vec_scale(normal, normal_size / 2.0))
                    u_half = _vec_scale(u_axis, length / 2.0)
                    v_half = _vec_scale(v_axis, v_length / 2.0)
                    corners3d = (
                        _vec_add(_vec_add(fc, _vec_scale(u_half, -1)), v_half),
                        _vec_add(_vec_add(fc, u_half), v_half),
                        _vec_add(_vec_add(fc, u_half), _vec_scale(v_half, -1)),
                        _vec_add(_vec_add(fc, _vec_scale(u_half, -1)), _vec_scale(v_half, -1)),
                    )
                    projected = [project(point) for point in corners3d]
                    depth = sum(item[1] for item in projected) / 4.0
                    polygon = QPolygonF([item[0] for item in projected])
                    row = _face_rect(
                        uv,
                        part.width,
                        part.height,
                        part.depth,
                        face,
                    )
                    if row.width() <= 0 or row.height() <= 0:
                        continue
                    crop = self._image.copy(row)
                    if crop.isNull() or crop.width() == 0:
                        continue
                    if part.mirror:
                        crop = crop.mirrored(True, False)
                    faces.append((depth, polygon, crop))

        faces.sort(key=lambda item: item[0])
        for _depth, polygon, crop in faces:
            self._draw_face(p, polygon, crop)
        p.end()

    def _has_overlay(self, part: _Part) -> bool:
        if not part.overlay:
            return False
        u, v = part.overlay
        w, h, d = int(part.width), int(part.height), int(part.depth)
        return _region_has_alpha(self._image, u, v, 2 * d + w, d + h)

    def _draw_face(self, painter: QPainter, polygon: QPolygonF, image: QImage) -> None:
        if polygon.count() != 4:
            return
        source = QPolygonF(
            [
                QPointF(0, 0),
                QPointF(image.width(), 0),
                QPointF(image.width(), image.height()),
                QPointF(0, image.height()),
            ]
        )
        transform = QTransform()
        if not QTransform.quadToQuad(source, polygon, transform):
            return
        painter.save()
        painter.setTransform(transform, True)
        painter.drawImage(QPointF(0, 0), image)
        painter.restore()

    # ------------------------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.position()
            self._drag_yaw = self._yaw
            self._drag_pitch = self._pitch
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_pos is not None and event.buttons() & Qt.LeftButton:
            delta = event.position() - self._drag_pos
            self._yaw = self._drag_yaw + delta.x() * 0.55
            self._pitch = max(-75.0, min(75.0, self._drag_pitch - delta.y() * 0.45))
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_pos = None
            self.setCursor(Qt.OpenHandCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self.reset_view()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        if delta:
            self._zoom = max(0.55, min(2.6, self._zoom * (1.0016 ** delta)))
            self.update()
            event.accept()
            return
        super().wheelEvent(event)
