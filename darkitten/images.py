from __future__ import annotations

from PySide6.QtGui import QImage

from .installer import _http_open

MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_ICON_BYTES = 1024 * 1024


class ImageFetchError(Exception):
    pass


def fetch_image_bytes(
    url: str, timeout: int = 30, limit: int = MAX_ICON_BYTES
) -> bytes:
    """下载并校验图片，返回原始字节，供图标等需要本地缓存的场景使用。"""
    if not url:
        raise ImageFetchError("图片地址为空。")
    try:
        with _http_open(
            url,
            timeout=timeout,
            headers={"Accept": "image/*"},
        ) as response:
            raw = response.read(max(1, int(limit)) + 1)
    except Exception as exc:
        raise ImageFetchError(f"图片下载失败：{exc}") from exc
    if not raw:
        raise ImageFetchError("图片内容为空。")
    if len(raw) > int(limit):
        raise ImageFetchError("图片过大，已跳过。")
    if QImage.fromData(raw).isNull():
        raise ImageFetchError("无法解析图片格式。")
    return raw


def fetch_image(url: str, timeout: int = 30) -> QImage:
    if not url:
        raise ImageFetchError("图片地址为空。")
    try:
        with _http_open(
            url,
            timeout=timeout,
            headers={"Accept": "image/*"},
        ) as response:
            raw = response.read(MAX_IMAGE_BYTES + 1)
    except Exception as exc:
        raise ImageFetchError(f"图片下载失败：{exc}") from exc
    if not raw:
        raise ImageFetchError("图片内容为空。")
    if len(raw) > MAX_IMAGE_BYTES:
        raise ImageFetchError("图片过大，已跳过。")
    image = QImage.fromData(raw)
    if image.isNull():
        raise ImageFetchError("无法解析图片格式。")
    return image
