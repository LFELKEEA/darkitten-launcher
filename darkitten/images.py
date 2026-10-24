from __future__ import annotations

from PySide6.QtGui import QImage

from .installer import _http_open

MAX_IMAGE_BYTES = 16 * 1024 * 1024


class ImageFetchError(Exception):
    pass


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
