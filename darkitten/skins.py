from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter

from .config import APP_DIR
from .installer import _http_open
from .microsoft_auth import USER_AGENT, fetch_profile

SKIN_API_URL = "https://api.minecraftservices.com/minecraft/profile/skins"
SKIN_DIR = APP_DIR / "skins"
LOCAL_SKIN_NAME = "local-skin.png"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
VALID_VARIANTS = ("classic", "slim")


class SkinError(Exception):
    pass


@dataclass
class SkinFile:
    image: QImage
    data: bytes
    legacy: bool = False
    source: str = ""


@dataclass
class CurrentSkin:
    image: QImage
    variant: str = "classic"
    url: str = ""
    skin_id: str = ""
    alias: str = ""


def _friendly_size(width: int, height: int) -> str:
    return f"{width}×{height}"


def validate_skin_bytes(data: bytes, *, source: str = "") -> SkinFile:
    if not data:
        raise SkinError("皮肤文件是空的，请选择有效的 PNG 图片。")
    if not data.startswith(PNG_SIGNATURE):
        raise SkinError("这不是 PNG 图片。Minecraft 皮肤必须使用 PNG 格式。")
    if len(data) > 4 * 1024 * 1024:
        raise SkinError("皮肤文件过大，请选择 64×64 或 64×32 的 PNG 图片。")
    image = QImage.fromData(data, "PNG")
    if image.isNull():
        raise SkinError("无法读取这张 PNG 图片，文件可能已损坏。")
    width, height = image.width(), image.height()
    if (width, height) not in ((64, 64), (64, 32)):
        raise SkinError(
            f"皮肤尺寸为 {_friendly_size(width, height)}，"
            "Minecraft 仅支持 64×64（现代）或 64×32（旧版）皮肤。"
        )
    image = image.convertToFormat(QImage.Format_ARGB32)
    return SkinFile(
        image=image,
        data=data,
        legacy=height == 32,
        source=source,
    )


def load_skin_file(path: str | Path) -> SkinFile:
    file_path = Path(path)
    try:
        data = file_path.read_bytes()
    except OSError as exc:
        raise SkinError(f"无法读取皮肤文件：{exc}") from exc
    return validate_skin_bytes(data, source=str(file_path))


def save_local_skin(skin: SkinFile) -> Path:
    SKIN_DIR.mkdir(parents=True, exist_ok=True)
    dest = SKIN_DIR / LOCAL_SKIN_NAME
    try:
        dest.write_bytes(skin.data)
    except OSError as exc:
        raise SkinError(f"无法保存皮肤预览副本：{exc}") from exc
    return dest


def local_skin_path() -> Path:
    return SKIN_DIR / LOCAL_SKIN_NAME


def active_skin(profile: dict[str, Any] | None) -> CurrentSkin | None:
    if not isinstance(profile, dict):
        return None
    skins = profile.get("skins")
    if not isinstance(skins, list):
        return None
    chosen: dict[str, Any] | None = None
    for entry in skins:
        if isinstance(entry, dict) and entry.get("url"):
            if str(entry.get("state") or "").upper() == "ACTIVE":
                chosen = entry
                break
    if chosen is None:
        chosen = next(
            (entry for entry in skins if isinstance(entry, dict) and entry.get("url")),
            None,
        )
    if chosen is None:
        return None
    return CurrentSkin(
        image=QImage(),
        variant=str(chosen.get("variant") or "classic").lower(),
        url=str(chosen.get("url") or ""),
        skin_id=str(chosen.get("id") or ""),
        alias=str(chosen.get("alias") or ""),
    )


def download_skin_image(url: str, timeout: int = 30) -> QImage:
    if not url:
        raise SkinError("没有可下载的皮肤地址。")
    try:
        with _http_open(
            url,
            timeout=timeout,
            headers={"Accept": "image/png,image/*;q=0.8"},
        ) as response:
            raw = response.read()
    except Exception as exc:
        raise SkinError(f"下载皮肤纹理失败：{exc}") from exc
    skin = validate_skin_bytes(raw, source=url)
    return skin.image


def fetch_account_skin(
    account, client_id: str
) -> tuple[CurrentSkin | None, Any]:
    """刷新正版令牌并读取当前皮肤，返回 (皮肤, 更新后的账户)。"""
    from .microsoft_auth import ensure_minecraft_token

    account = ensure_minecraft_token(account, client_id)
    profile = fetch_profile(account.mc_access_token)
    skin = active_skin(profile)
    if skin is not None:
        skin.image = download_skin_image(skin.url)
        if skin.variant not in VALID_VARIANTS:
            skin.variant = "classic"
    return skin, account


def _api_message(raw: bytes, status: int) -> str:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        payload = {}
    if isinstance(payload, dict):
        for key in ("errorMessage", "error_message", "message", "error", "details"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:240]
    if status == 401:
        return "Minecraft 登录已失效，请重新登录正版账户。"
    if status == 403:
        return "该账户没有修改皮肤的权限。"
    if status == 429:
        return "皮肤服务请求过于频繁，请稍后再试。"
    return f"皮肤服务返回了错误（HTTP {status}）。"


def _skin_request(
    method: str, access_token: str, data: bytes | None = None, content_type: str = ""
) -> None:
    if not access_token:
        raise SkinError("Minecraft 登录令牌为空，请重新登录正版账户。")
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}",
    }
    if content_type:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(
        SKIN_API_URL, data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            response.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        raise SkinError(_api_message(raw, exc.code)) from None
    except urllib.error.URLError as exc:
        raise SkinError(f"无法连接 Minecraft 皮肤服务：{exc.reason}") from None
    except OSError as exc:
        raise SkinError(f"皮肤请求失败：{exc}") from None


def change_skin(access_token: str, png_data: bytes, variant: str = "classic") -> None:
    variant = (variant or "classic").lower()
    if variant not in VALID_VARIANTS:
        variant = "classic"
    if not png_data:
        raise SkinError("没有可上传的皮肤文件。")
    boundary = "----DarkittenSkin" + uuid.uuid4().hex
    chunks: list[bytes] = [
        f"--{boundary}\r\n".encode("ascii"),
        b'Content-Disposition: form-data; name="variant"\r\n\r\n',
        variant.encode("ascii"),
        b"\r\n",
        f"--{boundary}\r\n".encode("ascii"),
        b'Content-Disposition: form-data; name="file"; filename="skin.png"\r\n',
        b"Content-Type: image/png\r\n\r\n",
        png_data,
        b"\r\n",
        f"--{boundary}--\r\n".encode("ascii"),
    ]
    _skin_request(
        "POST",
        access_token,
        b"".join(chunks),
        f"multipart/form-data; boundary={boundary}",
    )


def reset_skin(access_token: str) -> None:
    _skin_request("DELETE", access_token)


def variant_text(variant: str) -> str:
    return "Slim（Alex 细手臂）" if str(variant).lower() == "slim" else "Classic（Steve 标准手臂）"


def _fill(painter: QPainter, x: int, y: int, w: int, h: int, color: str) -> None:
    painter.fillRect(QRect(x, y, w, h), QColor(color))


def bundled_skin_path() -> Path:
    return APP_DIR / "assets" / "dkl_skin.png"


def default_skin() -> QImage:
    """优先使用内置的官方 Steve 皮肤，缺失时回退到程序绘制的 Steve 占位皮肤。"""
    bundled = bundled_skin_path()
    if bundled.is_file():
        image = QImage(str(bundled), "PNG")
        if not image.isNull() and (image.width(), image.height()) in ((64, 64), (64, 32)):
            return image.convertToFormat(QImage.Format_ARGB32)

    image = QImage(64, 64, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setPen(Qt.NoPen)

    skin = "#C99C6E"
    skin_dark = "#B5875C"
    hair = "#3B2B20"
    shirt = "#2E8E9C"
    shirt_dark = "#257680"
    pants = "#3C4F9E"
    pants_dark = "#314181"
    shoe = "#57534E"
    eye = "#2F4F73"
    mouth = "#8A5A45"

    # 头部
    _fill(painter, 8, 0, 8, 8, hair)
    _fill(painter, 16, 0, 8, 8, skin_dark)
    _fill(painter, 0, 8, 8, 8, hair)
    _fill(painter, 8, 8, 8, 8, skin)
    _fill(painter, 16, 8, 8, 8, hair)
    _fill(painter, 24, 8, 8, 8, hair)
    _fill(painter, 8, 8, 8, 2, hair)
    _fill(painter, 8, 10, 1, 3, hair)
    _fill(painter, 15, 10, 1, 3, hair)
    _fill(painter, 9, 12, 2, 1, "#F2F2F2")
    _fill(painter, 13, 12, 2, 1, "#F2F2F2")
    _fill(painter, 10, 12, 1, 1, eye)
    _fill(painter, 13, 12, 1, 1, eye)
    _fill(painter, 11, 13, 2, 1, skin_dark)
    _fill(painter, 11, 14, 2, 1, mouth)

    # 身体
    for rect in ((20, 16, 8, 4), (28, 16, 8, 4), (16, 20, 4, 12),
                 (20, 20, 8, 12), (24, 20, 4, 12), (32, 20, 8, 12)):
        _fill(painter, *rect, shirt)
    _fill(painter, 20, 28, 8, 4, shirt_dark)

    # 右臂 / 左臂
    for ox, oy in ((40, 16), (32, 48)):
        for rect in ((ox + 4, oy, 4, 4), (ox + 8, oy, 4, 4),
                     (ox, oy + 4, 4, 12), (ox + 4, oy + 4, 4, 12),
                     (ox + 8, oy + 4, 4, 12), (ox + 12, oy + 4, 4, 12)):
            _fill(painter, *rect, skin)
        _fill(painter, ox + 4, oy, 4, 4, shirt)
        _fill(painter, ox, oy + 4, 16, 4, shirt)

    # 右腿 / 左腿
    for ox, oy in ((0, 16), (16, 48)):
        for rect in ((ox + 4, oy, 4, 4), (ox + 8, oy, 4, 4),
                     (ox, oy + 4, 4, 12), (ox + 4, oy + 4, 4, 12),
                     (ox + 8, oy + 4, 4, 12), (ox + 12, oy + 4, 4, 12)):
            _fill(painter, *rect, pants)
        _fill(painter, ox, oy + 13, 16, 3, shoe)
        _fill(painter, ox + 12, oy + 4, 4, 12, pants_dark)

    painter.end()
    return image
