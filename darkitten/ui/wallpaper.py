"""个性化壁纸：把用户选的背景图按显示方式铺到启动器根窗口上。

背景图只画在 #Root 的绘制层，再叠一层半透明底色保证文字对比度；
原图不会被修改，尺寸和缩放都在内存里完成。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap

SUPPORTED_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif")

MODES = {
    "cover": "铺满裁切",
    "contain": "完整显示",
    "tile": "平铺",
}

_CACHE: dict[tuple[str, float], QImage] = {}
_CACHE_MAX = 3


def is_supported(path: str | Path) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_SUFFIXES


def load_image(path: str | Path) -> QImage | None:
    """读取背景图并做缓存；找不到或格式不支持时返回 None。"""
    text = str(path or "").strip()
    if not text or not is_supported(text):
        return None
    target = Path(text)
    try:
        stamp = target.stat().st_mtime
    except OSError:
        return None
    key = (str(target), stamp)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    image = QImage(str(target))
    if image.isNull():
        return None
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = image
    return image


def clear_cache() -> None:
    _CACHE.clear()


def paint_background(
    widget,
    image: QImage,
    mode: str,
    opacity: int,
    veil: QColor,
) -> None:
    """把背景图画到控件上，再盖一层底色压住对比度。"""
    if image is None or image.isNull():
        return
    painter = QPainter(widget)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    rect = QRect(0, 0, widget.width(), widget.height())
    mode = mode if mode in MODES else "cover"
    if mode == "tile":
        pixmap = QPixmap.fromImage(image)
        painter.drawTiledPixmap(rect, pixmap)
    else:
        scaled = image.scaled(
            widget.size(),
            Qt.KeepAspectRatioByExpanding if mode == "cover" else Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        x = (widget.width() - scaled.width()) // 2
        y = (widget.height() - scaled.height()) // 2
        painter.drawImage(x, y, scaled)
    painter.end()

    # opacity 是“看得见多少原图”，所以遮罩浓度取反。
    strength = max(0, min(100, int(opacity)))
    alpha = int(255 * (1 - strength / 100.0))
    if alpha > 0:
        overlay = QColor(veil)
        overlay.setAlpha(alpha)
        painter = QPainter(widget)
        painter.fillRect(rect, overlay)
        painter.end()
