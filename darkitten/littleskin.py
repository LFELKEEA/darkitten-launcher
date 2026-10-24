from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
import uuid as uuidlib
from dataclasses import dataclass, field
from typing import Any

from .config import APP_VERSION
from .downloader import http_open
from .secret import protect, unprotect
from .skins import SkinFile, SkinError, validate_skin_bytes

API_ROOT = "https://littleskin.cn/api/yggdrasil"
AUTHENTICATE_URL = f"{API_ROOT}/authserver/authenticate"
PROFILE_URL = f"{API_ROOT}/sessionserver/session/minecraft/profile/{{uuid}}"
REGISTER_URL = "https://littleskin.cn/auth/register"
USER_AGENT = f"DarkittenLauncher/{APP_VERSION} (LittleSkin skin sync)"
MAX_TEXTURE_BYTES = 4 * 1024 * 1024


class LittleSkinError(Exception):
    pass


@dataclass
class LittleSkinAccount:
    username: str = ""
    name: str = ""
    uuid: str = ""
    access_token: str = ""
    client_token: str = ""
    variant: str = "classic"
    skin_url: str = ""
    last_sync: float = 0.0
    profiles: list[dict[str, str]] = field(default_factory=list)

    @property
    def is_logged_in(self) -> bool:
        return bool(self.access_token and self.uuid and self.name)

    @property
    def short_uuid(self) -> str:
        value = self.uuid.replace("-", "")
        return f"{value[:8]}…{value[-4:]}" if len(value) >= 12 else value

    @classmethod
    def from_config(cls, data: dict[str, Any] | None) -> "LittleSkinAccount":
        if not isinstance(data, dict):
            return cls()
        profiles: list[dict[str, str]] = []
        raw_profiles = data.get("profiles")
        if isinstance(raw_profiles, list):
            for item in raw_profiles[:16]:
                if not isinstance(item, dict):
                    continue
                profile_id = str(item.get("id") or "").strip()
                profile_name = str(item.get("name") or "").strip()
                if profile_id and profile_name:
                    profiles.append({"id": profile_id, "name": profile_name})
        variant = "slim" if str(data.get("variant") or "").lower() == "slim" else "classic"
        try:
            last_sync = float(data.get("last_sync") or 0)
        except (TypeError, ValueError):
            last_sync = 0.0
        return cls(
            username=str(data.get("username") or "").strip(),
            name=str(data.get("name") or "").strip(),
            uuid=str(data.get("uuid") or "").strip(),
            access_token=unprotect(str(data.get("access_token") or "")),
            client_token=unprotect(str(data.get("client_token") or "")),
            variant=variant,
            skin_url=str(data.get("skin_url") or "").strip(),
            last_sync=max(0.0, last_sync),
            profiles=profiles,
        )

    def to_config(self) -> dict[str, Any]:
        return {
            "username": self.username,
            "name": self.name,
            "uuid": self.uuid,
            "access_token": protect(self.access_token),
            "client_token": protect(self.client_token),
            "variant": self.variant,
            "skin_url": self.skin_url,
            "last_sync": self.last_sync,
            "profiles": self.profiles,
        }


def load_account(cfg) -> LittleSkinAccount:
    return LittleSkinAccount.from_config(
        getattr(cfg, "littleskin_account", {}) or {}
    )


def save_account(cfg, account: LittleSkinAccount) -> None:
    cfg.littleskin_account = account.to_config()
    cfg.save()


def clear_account(cfg) -> None:
    cfg.littleskin_account = {}
    cfg.save()


def _error_message(raw: bytes, status: int) -> str:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        payload = {}
    if isinstance(payload, dict):
        for key in ("errorMessage", "error_message", "message", "error"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:240]
    if status == 401:
        return "LittleSkin 登录已失效，请重新登录。"
    if status == 403:
        return "LittleSkin 用户名或密码错误，或当前操作被拒绝。"
    if status == 429:
        return "LittleSkin 请求过于频繁，请稍后再试。"
    return f"LittleSkin 返回了错误（HTTP {status}）。"


def _request_json(
    url: str,
    payload: dict[str, Any] | None = None,
    *,
    token: str = "",
    timeout: int = 25,
) -> dict[str, Any]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = (
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if payload is not None
        else None
    )
    request = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method="POST" if data is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(1024 * 1024)
    except urllib.error.HTTPError as exc:
        raise LittleSkinError(_error_message(exc.read(4096), exc.code)) from None
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise LittleSkinError(f"无法连接 LittleSkin：{reason}") from None
    except OSError as exc:
        raise LittleSkinError(f"LittleSkin 请求失败：{exc}") from None
    if not raw:
        return {}
    try:
        result = json.loads(raw.decode("utf-8"))
    except Exception:
        raise LittleSkinError("LittleSkin 返回了无法识别的数据。") from None
    if not isinstance(result, dict):
        raise LittleSkinError("LittleSkin 返回了无法识别的数据。")
    return result


def _download_texture(url: str) -> bytes:
    if not url:
        raise LittleSkinError("LittleSkin 角色没有可下载的皮肤纹理。")
    try:
        with http_open(
            url,
            timeout=30,
            headers={"Accept": "image/png,image/*;q=0.8"},
        ) as response:
            data = response.read(MAX_TEXTURE_BYTES + 1)
    except Exception as exc:
        raise LittleSkinError(f"下载 LittleSkin 皮肤失败：{exc}") from None
    if len(data) > MAX_TEXTURE_BYTES:
        raise LittleSkinError("LittleSkin 皮肤文件过大，已停止下载。")
    return data


def _decode_textures(profile: dict[str, Any]) -> dict[str, Any]:
    properties = profile.get("properties")
    if not isinstance(properties, list):
        return {}
    for item in properties:
        if not isinstance(item, dict) or item.get("name") != "textures":
            continue
        value = str(item.get("value") or "").strip()
        if not value:
            continue
        try:
            padded = value + "=" * (-len(value) % 4)
            raw = base64.urlsafe_b64decode(padded.encode("ascii"))
            decoded = json.loads(raw.decode("utf-8"))
        except Exception:
            continue
        if isinstance(decoded, dict):
            return decoded
    return {}


def _profile_from_response(data: dict[str, Any]) -> dict[str, str] | None:
    selected = data.get("selectedProfile")
    if isinstance(selected, dict):
        profile_id = str(selected.get("id") or "").strip()
        profile_name = str(selected.get("name") or "").strip()
        if profile_id and profile_name:
            return {"id": profile_id, "name": profile_name}
    available = data.get("availableProfiles")
    if isinstance(available, list):
        for item in available:
            if not isinstance(item, dict):
                continue
            profile_id = str(item.get("id") or "").strip()
            profile_name = str(item.get("name") or "").strip()
            if profile_id and profile_name:
                return {"id": profile_id, "name": profile_name}
    return None


def _all_profiles(data: dict[str, Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    available = data.get("availableProfiles")
    if not isinstance(available, list):
        return result
    for item in available[:16]:
        if not isinstance(item, dict):
            continue
        profile_id = str(item.get("id") or "").strip()
        profile_name = str(item.get("name") or "").strip()
        if profile_id and profile_name:
            result.append({"id": profile_id, "name": profile_name})
    return result


def authenticate(
    username: str,
    password: str,
    *,
    client_token: str = "",
) -> LittleSkinAccount:
    """用 LittleSkin 账号密码换 Yggdrasil 令牌，密码只在本次请求中使用。"""
    username = str(username or "").strip()
    if not username:
        raise LittleSkinError("请输入 LittleSkin 邮箱或用户名。")
    if not password:
        raise LittleSkinError("请输入 LittleSkin 密码。")
    token = str(client_token or "").strip() or uuidlib.uuid4().hex
    data = _request_json(
        AUTHENTICATE_URL,
        {
            "agent": {"name": "Minecraft", "version": 1},
            "username": username,
            "password": password,
            "clientToken": token,
            "requestUser": True,
        },
    )
    access_token = str(data.get("accessToken") or "").strip()
    if not access_token:
        raise LittleSkinError("LittleSkin 没有返回登录令牌，请稍后重试。")
    profile = _profile_from_response(data)
    if profile is None:
        raise LittleSkinError(
            "这个 LittleSkin 账号还没有角色。请先到 LittleSkin 创建一个角色，再回来登录。"
        )
    return LittleSkinAccount(
        username=username,
        name=profile["name"],
        uuid=profile["id"],
        access_token=access_token,
        client_token=str(data.get("clientToken") or token),
        variant="classic",
        profiles=_all_profiles(data),
    )


def fetch_skin(account: LittleSkinAccount) -> SkinFile | None:
    """读取 LittleSkin 角色当前皮肤；角色没有自定义皮肤时返回 None。"""
    if not account.uuid:
        raise LittleSkinError("LittleSkin 角色 UUID 为空，请重新登录。")
    profile = _request_json(PROFILE_URL.format(uuid=account.uuid))
    if not profile:
        raise LittleSkinError("LittleSkin 上找不到这个角色，请重新登录或检查角色状态。")
    account.name = str(profile.get("name") or account.name)
    account.uuid = str(profile.get("id") or account.uuid)
    decoded = _decode_textures(profile)
    textures = decoded.get("textures")
    skin_info = textures.get("SKIN") if isinstance(textures, dict) else None
    if not isinstance(skin_info, dict) or not skin_info.get("url"):
        account.skin_url = ""
        account.last_sync = time.time()
        return None
    url = str(skin_info.get("url") or "").strip()
    metadata = skin_info.get("metadata")
    model = ""
    if isinstance(metadata, dict):
        model = str(metadata.get("model") or "").strip().lower()
    data = _download_texture(url)
    try:
        skin = validate_skin_bytes(data, source=url)
    except SkinError as exc:
        raise LittleSkinError(f"LittleSkin 皮肤无法使用：{exc}") from None
    account.variant = "slim" if model == "slim" else "classic"
    if skin.legacy:
        account.variant = "classic"
    account.skin_url = url
    account.last_sync = time.time()
    return skin


def login(
    username: str,
    password: str,
    *,
    client_token: str = "",
) -> tuple[LittleSkinAccount, SkinFile | None]:
    account = authenticate(username, password, client_token=client_token)
    skin = fetch_skin(account)
    return account, skin
