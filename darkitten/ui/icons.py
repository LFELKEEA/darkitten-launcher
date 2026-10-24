from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QIcon,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)

ASSETS_DIR = Path(__file__).resolve().parents[2] / "assets"


def _new_painter(size: int) -> tuple[QPixmap, QPainter, int]:
    scale = 2
    pixmap = QPixmap(size * scale, size * scale)
    pixmap.setDevicePixelRatio(scale)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    return pixmap, painter, size


def _line(painter: QPainter, points: list[tuple[float, float]]) -> None:
    path = QPainterPath(QPointF(*points[0]))
    for point in points[1:]:
        path.lineTo(QPointF(*point))
    painter.drawPath(path)


def pixmap(name: str, color: str = "#59637A", size: int = 18) -> QPixmap:
    pm, p, s = _new_painter(size)
    c = QColor(color)
    pen = QPen(c, max(1.5, size / 11.0))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    u = size / 18.0

    def pt(x: float, y: float) -> QPointF:
        return QPointF(x * u, y * u)

    if name == "play":
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawPolygon(QPolygonF([pt(5.2, 3.2), pt(5.2, 14.8), pt(14.8, 9.0)]))
    elif name == "download":
        _line(p, [(9, 2.6), (9, 11.4)])
        _line(p, [(4.8, 7.4), (9, 11.7), (13.2, 7.4)])
        _line(p, [(3.2, 14.6), (14.8, 14.6)])
    elif name == "cube":
        p.drawPolygon(
            QPolygonF([pt(9, 2.1), pt(15.3, 5.7), pt(9, 9.3), pt(2.7, 5.7)])
        )
        _line(p, [(2.7, 5.7), (2.7, 12.3), (9, 16.0), (15.3, 12.3), (15.3, 5.7)])
        _line(p, [(9, 9.3), (9, 16.0)])
    elif name == "settings":
        center = pt(9, 9)
        p.save()
        p.translate(center)
        for i in range(8):
            p.save()
            p.rotate(i * 45)
            p.drawRoundedRect(QRectF(-1.15 * u, -8.2 * u, 2.3 * u, 3.1 * u), 0.8, 0.8)
            p.restore()
        p.restore()
        p.drawEllipse(center, 5.2 * u, 5.2 * u)
        p.drawEllipse(center, 1.8 * u, 1.8 * u)
    elif name == "folder":
        path = QPainterPath(pt(2.2, 5.0))
        path.lineTo(6.8 * u, 5.0 * u)
        path.lineTo(8.2 * u, 6.8 * u)
        path.lineTo(15.8 * u, 6.8 * u)
        path.lineTo(15.8 * u, 13.9 * u)
        path.lineTo(2.2 * u, 13.9 * u)
        path.closeSubpath()
        p.drawPath(path)
        _line(p, [(2.2, 8.4), (15.8, 8.4)])
    elif name == "refresh":
        p.drawArc(QRectF(2.6 * u, 2.6 * u, 12.8 * u, 12.8 * u), 35 * 16, 285 * 16)
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygonF([pt(14.6, 2.2), pt(15.4, 6.4), pt(11.5, 4.5)]))
    elif name == "log":
        p.drawRoundedRect(QRectF(3.0 * u, 2.2 * u, 12.0 * u, 13.6 * u), 1.4, 1.4)
        _line(p, [(5.8, 6.0), (12.2, 6.0)])
        _line(p, [(5.8, 9.0), (12.2, 9.0)])
        _line(p, [(5.8, 12.0), (10.2, 12.0)])
    elif name == "plus":
        _line(p, [(9, 3.2), (9, 14.8)])
        _line(p, [(3.2, 9), (14.8, 9)])
    elif name == "edit":
        _line(p, [(3.6, 14.4), (4.5, 10.6), (12.4, 2.7), (15.3, 5.6), (7.4, 13.5), (3.6, 14.4)])
        _line(p, [(10.8, 4.3), (13.7, 7.2)])
    elif name == "close":
        _line(p, [(4.2, 4.2), (13.8, 13.8)])
        _line(p, [(13.8, 4.2), (4.2, 13.8)])
    elif name == "minimize":
        _line(p, [(4.2, 10.2), (13.8, 10.2)])
    elif name == "maximize":
        p.drawRoundedRect(QRectF(4.2 * u, 4.2 * u, 9.6 * u, 9.6 * u), 1.0, 1.0)
    elif name == "restore":
        p.drawRoundedRect(QRectF(3.2 * u, 5.6 * u, 8.2 * u, 8.2 * u), 1.0, 1.0)
        _line(p, [(6.2, 3.2), (14.8, 3.2), (14.8, 11.6)])
    elif name == "check":
        _line(p, [(3.6, 9.4), (7.3, 13.0), (14.6, 4.8)])
    elif name == "warning":
        path = QPainterPath(pt(9, 2.2))
        path.lineTo(16.4 * u, 15.2 * u)
        path.lineTo(1.6 * u, 15.2 * u)
        path.closeSubpath()
        p.drawPath(path)
        _line(p, [(9, 6.6), (9, 10.3)])
        p.setBrush(c)
        p.drawEllipse(pt(9, 12.7), 0.9 * u, 0.9 * u)
    elif name == "search":
        p.drawEllipse(QRectF(3.0 * u, 3.0 * u, 9.2 * u, 9.2 * u))
        _line(p, [(11.4, 11.4), (15.4, 15.4)])
    elif name == "sun":
        p.drawEllipse(QRectF(5.8 * u, 5.8 * u, 6.4 * u, 6.4 * u))
        for angle in range(0, 360, 45):
            rad = math.radians(angle)
            x1 = 9 + math.cos(rad) * 6.6
            y1 = 9 + math.sin(rad) * 6.6
            x2 = 9 + math.cos(rad) * 8.1
            y2 = 9 + math.sin(rad) * 8.1
            _line(p, [(x1, y1), (x2, y2)])
    elif name == "moon":
        path = QPainterPath()
        path.addEllipse(QRectF(3.4 * u, 3.0 * u, 11.4 * u, 11.4 * u))
        cut = QPainterPath()
        cut.addEllipse(QRectF(7.6 * u, 1.6 * u, 10.0 * u, 10.0 * u))
        p.drawPath(path.subtracted(cut))
    elif name == "user":
        p.drawEllipse(QRectF(6.2 * u, 2.8 * u, 5.6 * u, 5.6 * u))
        path = QPainterPath(pt(3.4, 15.4))
        path.cubicTo(3.8 * u, 11.0 * u, 14.2 * u, 11.0 * u, 14.6 * u, 15.4 * u)
        p.drawPath(path)
    elif name == "chevron-down":
        _line(p, [(4.4, 6.8), (9, 11.4), (13.6, 6.8)])
    elif name == "chevron-right":
        _line(p, [(6.8, 4.4), (11.4, 9), (6.8, 13.6)])
    elif name == "chevron-left":
        _line(p, [(11.2, 4.4), (6.6, 9), (11.2, 13.6)])
    elif name == "external":
        p.drawRoundedRect(QRectF(3.0 * u, 6.0 * u, 9.0 * u, 9.0 * u), 1.4, 1.4)
        _line(p, [(9.4, 3.0), (15.0, 3.0), (15.0, 8.6)])
        _line(p, [(15.0, 3.0), (8.2, 9.8)])
    elif name == "globe":
        p.drawEllipse(QRectF(2.4 * u, 2.4 * u, 13.2 * u, 13.2 * u))
        p.drawEllipse(QRectF(6.2 * u, 2.6 * u, 5.6 * u, 12.8 * u))
        _line(p, [(2.8, 6.6), (15.2, 6.6)])
        _line(p, [(2.8, 11.4), (15.2, 11.4)])
    elif name == "image":
        p.drawRoundedRect(QRectF(2.6 * u, 3.2 * u, 12.8 * u, 11.6 * u), 1.6, 1.6)
        p.drawEllipse(QPointF(6.5 * u, 6.6 * u), 1.15 * u, 1.15 * u)
        _line(p, [(3.6, 13.0), (7.8, 8.9), (10.2, 11.2), (12.2, 9.2), (14.6, 12.9)])
    elif name == "shirt":
        path = QPainterPath(pt(6.2, 3.2))
        path.lineTo(11.8 * u, 3.2 * u)
        path.lineTo(15.4 * u, 6.1 * u)
        path.lineTo(13.0 * u, 8.8 * u)
        path.lineTo(12.0 * u, 7.9 * u)
        path.lineTo(12.0 * u, 15.2 * u)
        path.lineTo(6.0 * u, 15.2 * u)
        path.lineTo(6.0 * u, 7.9 * u)
        path.lineTo(5.0 * u, 8.8 * u)
        path.lineTo(2.6 * u, 6.1 * u)
        path.closeSubpath()
        p.drawPath(path)
        _line(p, [(6.8, 3.3), (9.0, 5.4), (11.2, 3.3)])
    elif name == "rotate":
        p.drawArc(QRectF(2.8 * u, 2.8 * u, 12.4 * u, 12.4 * u), 40 * 16, 280 * 16)
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygonF([pt(13.7, 2.3), pt(15.6, 6.4), pt(11.4, 5.3)]))
    elif name == "flip":
        _line(p, [(9.0, 3.0), (9.0, 15.0)])
        _line(p, [(6.4, 5.0), (3.0, 9.0), (6.4, 13.0)])
        _line(p, [(11.6, 5.0), (15.0, 9.0), (11.6, 13.0)])
    elif name == "bolt":
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawPolygon(QPolygonF([pt(10.2, 1.8), pt(4.0, 10.0), pt(8.2, 10.0), pt(7.4, 16.2), pt(14.0, 7.4), pt(9.6, 7.4)]))
    elif name == "trash":
        _line(p, [(3.4, 5.0), (14.6, 5.0)])
        _line(p, [(7.0, 2.8), (11.0, 2.8)])
        _line(p, [(4.6, 5.0), (5.4, 15.2), (12.6, 15.2), (13.4, 5.0)])
        _line(p, [(7.4, 7.8), (7.4, 12.6)])
        _line(p, [(10.6, 7.8), (10.6, 12.6)])
    elif name == "package":
        p.drawPolygon(
            QPolygonF([pt(3.0, 5.2), pt(9.0, 2.2), pt(15.0, 5.2), pt(9.0, 8.2)])
        )
        _line(p, [(3.0, 5.2), (3.0, 12.7), (9.0, 15.8), (15.0, 12.7), (15.0, 5.2)])
        _line(p, [(9.0, 8.2), (9.0, 15.8)])
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawPolygon(
            QPolygonF(
                [pt(11.4, 9.0), pt(12.6, 10.2), pt(11.4, 10.9), pt(10.2, 10.2)]
            )
        )
    elif name == "import":
        p.drawRoundedRect(QRectF(2.6 * u, 3.0 * u, 12.8 * u, 4.0 * u), 1.2, 1.2)
        _line(p, [(4.2, 7.0), (4.2, 14.6), (13.8, 14.6), (13.8, 7.0)])
        _line(p, [(9.0, 8.4), (9.0, 12.4)])
        _line(p, [(6.8, 10.5), (9.0, 12.7), (11.2, 10.5)])
    elif name == "paw":
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawEllipse(QPointF(9.0 * u, 11.2 * u), 3.15 * u, 2.55 * u)
        for x, y, rx, ry in (
            (4.5, 7.0, 1.45, 1.85),
            (7.7, 5.6, 1.45, 1.9),
            (10.3, 5.6, 1.45, 1.9),
            (13.5, 7.0, 1.45, 1.85),
        ):
            p.drawEllipse(QPointF(x * u, y * u), rx * u, ry * u)
    elif name == "sparkles":
        for x, y, r in ((5.2, 5.2, 4.2), (12.8, 4.2, 2.6), (13.0, 12.6, 3.3)):
            path = QPainterPath()
            path.moveTo(x * u, (y - r) * u)
            path.quadTo(x * u, y * u, (x + r) * u, y * u)
            path.quadTo(x * u, y * u, x * u, (y + r) * u)
            path.quadTo(x * u, y * u, (x - r) * u, y * u)
            path.quadTo(x * u, y * u, x * u, (y - r) * u)
            p.setPen(Qt.NoPen)
            p.setBrush(c)
            p.drawPath(path)
    elif name == "heart":
        path = QPainterPath(pt(9.0, 15.0))
        path.cubicTo(1.8 * u, 10.2 * u, 2.8 * u, 3.2 * u, 9.0 * u, 6.6 * u)
        path.cubicTo(15.2 * u, 3.2 * u, 16.2 * u, 10.2 * u, 9.0 * u, 15.0 * u)
        p.drawPath(path)
    elif name == "copy":
        p.drawRoundedRect(QRectF(6.0 * u, 6.0 * u, 9.0 * u, 9.0 * u), 1.2, 1.2)
        _line(p, [(3.0, 12.0), (3.0, 3.0), (12.0, 3.0)])
    elif name == "users":
        p.drawEllipse(QRectF(4.7 * u, 2.9 * u, 5.2 * u, 5.2 * u))
        path = QPainterPath(pt(2.0, 15.3))
        path.cubicTo(2.5 * u, 10.8 * u, 12.1 * u, 10.8 * u, 12.6 * u, 15.3 * u)
        p.drawPath(path)
        p.drawEllipse(QRectF(11.4 * u, 4.8 * u, 3.9 * u, 3.9 * u))
        path = QPainterPath(pt(12.8, 10.4))
        path.cubicTo(14.9 * u, 10.8 * u, 16.2 * u, 12.4 * u, 16.4 * u, 15.1 * u)
        p.drawPath(path)
    elif name == "link":
        p.drawArc(QRectF(1.8 * u, 6.2 * u, 8.2 * u, 5.6 * u), 90 * 16, 180 * 16)
        p.drawArc(QRectF(8.0 * u, 6.2 * u, 8.2 * u, 5.6 * u), 270 * 16, 180 * 16)
        _line(p, [(6.4, 9.0), (11.6, 9.0)])
    elif name == "server":
        p.drawRoundedRect(QRectF(2.6 * u, 2.8 * u, 12.8 * u, 5.2 * u), 1.4, 1.4)
        p.drawRoundedRect(QRectF(2.6 * u, 10.0 * u, 12.8 * u, 5.2 * u), 1.4, 1.4)
        p.setBrush(c)
        p.drawEllipse(QPointF(5.5 * u, 5.4 * u), 0.8 * u, 0.8 * u)
        p.drawEllipse(QPointF(5.5 * u, 12.6 * u), 0.8 * u, 0.8 * u)
        _line(p, [(8.2, 5.4), (12.4, 5.4)])
        _line(p, [(8.2, 12.6), (12.4, 12.6)])
    elif name == "terminal":
        p.drawRoundedRect(QRectF(2.4 * u, 3.2 * u, 13.2 * u, 11.6 * u), 1.6, 1.6)
        _line(p, [(5.0, 7.0), (7.6, 9.0), (5.0, 11.0)])
        _line(p, [(9.6, 11.2), (13.2, 11.2)])
    elif name == "stop":
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawRoundedRect(QRectF(5.0 * u, 5.0 * u, 8.0 * u, 8.0 * u), 1.6, 1.6)
    elif name == "plug":
        _line(p, [(6.8, 2.4), (6.8, 5.4)])
        _line(p, [(11.2, 2.4), (11.2, 5.4)])
        p.drawRoundedRect(QRectF(4.8 * u, 5.4 * u, 8.4 * u, 6.0 * u), 1.8, 1.8)
        _line(p, [(9.0, 11.4), (9.0, 15.6)])
        _line(p, [(6.2, 9.0), (11.8, 9.0)])
    elif name == "migrate":
        _line(p, [(2.8, 6.0), (12.6, 6.0)])
        _line(p, [(10.0, 3.4), (12.8, 6.0), (10.0, 8.6)])
        _line(p, [(15.2, 12.0), (5.4, 12.0)])
        _line(p, [(8.0, 9.4), (5.2, 12.0), (8.0, 14.6)])
    elif name == "desktop":
        p.drawRoundedRect(QRectF(2.4 * u, 3.2 * u, 13.2 * u, 9.2 * u), 1.6, 1.6)
        _line(p, [(8.2, 12.4), (8.2, 15.0)])
        _line(p, [(5.4, 15.2), (11.0, 15.2)])
        _line(p, [(5.2, 8.4), (8.2, 5.8), (11.2, 8.4)])
    else:
        p.drawEllipse(QRectF(4 * u, 4 * u, 10 * u, 10 * u))

    p.end()
    return pm


def icon(name: str, color: str = "#59637A", size: int = 18) -> QIcon:
    return QIcon(pixmap(name, color, size))


def logo_pixmap(size: int = 40) -> QPixmap:
    """内部品牌图标：与桌面图标一致的低像素恶魔小猫。"""
    return app_icon_pixmap(size)


def chibi_face_pixmap(size: int) -> QPixmap:
    """Q 版 OC 的正面头像，正方形裁切，适合圆形徽章。"""
    image = QImage(str(ASSETS_DIR / "chibi_face.png"))
    if image.isNull():
        return app_icon_pixmap(size)
    side = min(image.width(), image.height())
    left = (image.width() - side) // 2
    top = (image.height() - side) // 2
    square = image.copy(left, top, side, side)
    return QPixmap.fromImage(
        square.scaled(
            QSize(size, size),
            Qt.IgnoreAspectRatio,
            Qt.SmoothTransformation,
        )
    )


def chibi_pixmap(height: int) -> QPixmap:
    """Q 版 OC 头像立绘，保持宽高比缩放到指定高度。"""
    image = QImage(str(ASSETS_DIR / "chibi_oc.png"))
    if image.isNull():
        return app_icon_pixmap(height)
    return QPixmap.fromImage(
        image.scaledToHeight(max(1, height), Qt.SmoothTransformation)
    )


def mascot_pixmap(size: int) -> QPixmap:
    """兼容旧调用：返回 Q 版 OC 的圆形头像。"""
    return chibi_face_pixmap(size)


def skin_face_pixmap(size: int) -> QPixmap:
    """把玩家当前皮肤的脸部（含帽子层）渲染成小头像。"""
    image = active_skin_image()
    if image.isNull():
        return logo_pixmap(size)
    face = QImage(8, 8, QImage.Format_ARGB32)
    face.fill(Qt.transparent)
    painter = QPainter(face)
    painter.drawImage(0, 0, image.copy(8, 8, 8, 8))
    painter.drawImage(0, 0, image.copy(40, 8, 8, 8))
    painter.end()
    scaled = face.scaled(
        QSize(size, size), Qt.IgnoreAspectRatio, Qt.FastTransformation
    ).convertToFormat(QImage.Format_ARGB32)
    return QPixmap.fromImage(scaled)


_SKIN_IMAGE_CACHE: dict[str, tuple[float, QImage]] = {}


def active_skin_image() -> QImage:
    """读取启动器当前使用的皮肤：优先 config.skin_path，其次本地预览副本。"""
    try:
        from ..config import Config

        configured = str(Config.load().skin_path or "").strip()
    except Exception:
        configured = ""
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured))
    try:
        from ..skins import local_skin_path

        local = local_skin_path()
        if local not in candidates:
            candidates.append(local)
    except Exception:
        pass

    for path in candidates:
        try:
            if not path.is_file():
                continue
            mtime = path.stat().st_mtime
            cached = _SKIN_IMAGE_CACHE.get(str(path))
            if cached and cached[0] == mtime:
                return cached[1]
            from ..skins import load_skin_file

            image = load_skin_file(path).image
            if not image.isNull():
                _SKIN_IMAGE_CACHE[str(path)] = (mtime, image)
                return image
        except Exception:
            continue
    try:
        from ..skins import default_skin

        return default_skin()
    except Exception:
        return QImage()


_APP_ICON_PALETTE = {
    "W": "#FFF6EE",
    "w": "#F7DFE1",
    "E": "#D81B4A",
}

# 30 one-row bands keep the rose gradient smooth while every edge stays hard.
_APP_ICON_BANDS = (
    (1, 1, "#E92E64"),
    (2, 2, "#E52D62"),
    (3, 3, "#E02C60"),
    (4, 4, "#DC2A5D"),
    (5, 5, "#D7295B"),
    (6, 6, "#D32859"),
    (7, 7, "#CF2757"),
    (8, 8, "#CA2654"),
    (9, 9, "#C62452"),
    (10, 10, "#C12350"),
    (11, 11, "#BD224E"),
    (12, 12, "#B8214B"),
    (13, 13, "#B42049"),
    (14, 14, "#B01E47"),
    (15, 15, "#AB1D45"),
    (16, 16, "#A71C42"),
    (17, 17, "#A21B40"),
    (18, 18, "#9E193E"),
    (19, 19, "#9A183C"),
    (20, 20, "#951739"),
    (21, 21, "#911637"),
    (22, 22, "#8C1535"),
    (23, 23, "#881333"),
    (24, 24, "#831230"),
    (25, 25, "#7F112E"),
    (26, 26, "#7B102C"),
    (27, 27, "#760F2A"),
    (28, 28, "#720D27"),
    (29, 29, "#6D0C25"),
    (30, 30, "#690B23"),
)

# 32x32 pixel map: low loaf-shaped cat facing left on a rose badge.
# "." is the badge background, " " is transparent (rounded corners).
_APP_ICON_ROWS = (
    "                                ",
    "    ........................    ",
    "  ............................  ",
    " .............................. ",
    " .............................. ",
    " .............................. ",
    " .............................. ",
    " .............................. ",
    " .............................. ",
    " .............................. ",
    " .............................. ",
    " .............................. ",
    " ....W...W..................... ",
    " ...WW...WW.....WWWWWW......... ",
    " ..WWWW..WWWW.WWWWWWWWW........ ",
    " ..WWWWWWWWWWWWWWWWWWWWWWWW.... ",
    " ..WWWWWWWWWWWWWWWWWWWWWWWWWW.. ",
    " ..WWWWWWWWWWWWWWWWWWWwwW.WWW.. ",
    " ..WWWEEWWWWWWWWWWWWWwww..WWW.. ",
    " ..WWWEEWWWWWWWWWWWWwwww..WWW.. ",
    " ..WWWEEWwwWWWWWWWWWwwwW..WWW.. ",
    " ...WWWWWwwWWWWWWWWwwwwW.WWW... ",
    " ....WWWWWWWWWWWWWWwwwwW.WWW... ",
    " .....WWWWWWWWWWWWWWwwwW.WWW... ",
    " .................WWWWWW..WWW.. ",
    " .........................WWW.. ",
    " ..........................WW.. ",
    " .............................. ",
    " .............................. ",
    "  ............................  ",
    "    ........................    ",
    "                                ",
)

_APP_ICON_BASE: QImage | None = None


def _app_icon_base() -> QImage:
    global _APP_ICON_BASE
    if _APP_ICON_BASE is not None:
        return _APP_ICON_BASE

    band_colors: list[QColor | None] = [None] * 32
    for lo, hi, value in _APP_ICON_BANDS:
        for y in range(lo, hi + 1):
            band_colors[y] = QColor(value)

    image = QImage(32, 32, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    for y, row in enumerate(_APP_ICON_ROWS):
        for x, ch in enumerate(row):
            if ch == " ":
                continue
            if ch == ".":
                color = band_colors[y]
            else:
                color = QColor(_APP_ICON_PALETTE[ch])
            if color is not None:
                image.setPixelColor(x, y, color)
    _APP_ICON_BASE = image
    return image


def app_icon_image(size: int) -> QImage:
    """低像素恶魔小猫：玫瑰渐变底，奶白侧影，头朝左，尾巴卷在右侧。"""
    base = _app_icon_base()
    if size == base.width():
        return base.copy()
    return base.scaled(size, size, Qt.IgnoreAspectRatio, Qt.FastTransformation)


def app_icon_pixmap(size: int) -> QPixmap:
    return QPixmap.fromImage(app_icon_image(size))


def app_icon() -> QIcon:
    icon = QIcon()
    ico = ASSETS_DIR / "dkl.ico"
    if ico.is_file():
        icon.addFile(str(ico))
        return icon
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(app_icon_pixmap(size))
    return icon

