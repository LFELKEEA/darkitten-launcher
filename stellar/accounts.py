from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from typing import Any

from .secret import protect, unprotect

NAME_RE = re.compile(r"^[A-Za-z0-9_]{3,16}$")


def valid_player_name(name: str) -> bool:
    return bool(NAME_RE.fullmatch(name.strip()))


def offline_uuid(name: str) -> str:
    """生成与 Minecraft 离线模式一致的 UUID（MD5 version 3）。"""
    digest = bytearray(hashlib.md5(f"OfflinePlayer:{name}".encode("utf-8")).digest())
    digest[6] = (digest[6] & 0x0F) | 0x30
    digest[8] = (digest[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(digest)))


@dataclass
class Account:
    kind: str = "offline"
    name: str = ""
    uuid: str = ""
    access_token: str = ""
    refresh_token: str = ""
    expires_at: float = 0.0
    mc_access_token: str = ""
    mc_expires_at: float = 0.0
    xuid: str = ""

    @property
    def is_msa(self) -> bool:
        return self.kind == "msa"

    @property
    def display_name(self) -> str:
        return self.name or "Steve"

    @classmethod
    def offline(cls, name: str) -> "Account":
        return cls(kind="offline", name=name, uuid=offline_uuid(name))

    @classmethod
    def from_config(cls, data: dict[str, Any] | None) -> "Account":
        if not isinstance(data, dict):
            return cls()
        kind = str(data.get("kind") or "offline")
        if kind != "msa":
            return cls()
        return cls(
            kind="msa",
            name=str(data.get("name") or ""),
            uuid=str(data.get("uuid") or ""),
            access_token=unprotect(str(data.get("access_token") or "")),
            refresh_token=unprotect(str(data.get("refresh_token") or "")),
            expires_at=float(data.get("expires_at") or 0),
            mc_access_token=unprotect(str(data.get("mc_access_token") or "")),
            mc_expires_at=float(data.get("mc_expires_at") or 0),
            xuid=str(data.get("xuid") or ""),
        )

    def to_config(self) -> dict[str, Any]:
        if not self.is_msa:
            return {}
        return {
            "kind": "msa",
            "name": self.name,
            "uuid": self.uuid,
            "access_token": protect(self.access_token),
            "refresh_token": protect(self.refresh_token),
            "expires_at": self.expires_at,
            "mc_access_token": protect(self.mc_access_token),
            "mc_expires_at": self.mc_expires_at,
            "xuid": self.xuid,
        }
