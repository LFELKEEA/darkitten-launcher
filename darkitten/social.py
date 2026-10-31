from __future__ import annotations

import base64
import json
import re
import secrets
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import APP_DIR

SOCIAL_PATH = APP_DIR / "social.json"
INVITE_PREFIX = "DKLF1:"
ID_RE = re.compile(r"^DKL-[A-Z0-9]{4,16}$")
ROOM_RE = re.compile(r"^[A-Za-z0-9_.:\-]{3,80}$")


class SocialError(Exception):
    pass


def new_profile_id() -> str:
    return "DKL-" + secrets.token_hex(4).upper()


@dataclass
class SocialProfile:
    id: str = ""
    name: str = ""
    created_at: float = 0.0
    room_code: str = ""
    room_version: str = ""

    def ensure(self, fallback_name: str = "Steve") -> "SocialProfile":
        if not ID_RE.fullmatch(self.id or ""):
            self.id = new_profile_id()
        self.name = str(self.name or fallback_name or "Steve").strip()[:24]
        if not self.created_at:
            self.created_at = time.time()
        return self


@dataclass
class Friend:
    id: str
    name: str
    room_code: str = ""
    room_version: str = ""
    note: str = ""
    added_at: float = 0.0
    last_room_at: float = 0.0

    def ensure(self) -> "Friend":
        self.id = str(self.id or "").strip().upper()
        if not ID_RE.fullmatch(self.id):
            raise SocialError("好友 ID 格式不正确，应以 DKL- 开头。")
        self.name = str(self.name or "未命名好友").strip()[:24]
        self.room_code = str(self.room_code or "").strip()[:80]
        self.room_version = str(self.room_version or "").strip()[:64]
        self.note = str(self.note or "").strip()[:120]
        if not self.added_at:
            self.added_at = time.time()
        return self


@dataclass
class Invite:
    id: str
    name: str
    room_code: str = ""
    room_version: str = ""
    note: str = ""
    kind: str = "friend"
    mod_manifest: str = ""


def encode_invite(
    profile: SocialProfile,
    room_code: str = "",
    room_version: str = "",
    note: str = "",
    kind: str = "friend",
    mod_manifest: str = "",
) -> str:
    payload = {
        "v": 1,
        "kind": kind,
        "id": profile.id,
        "name": profile.name,
        "room": str(room_code or profile.room_code or "").strip(),
        "mc": str(room_version or profile.room_version or "").strip(),
        "note": str(note or "").strip()[:120],
        "ts": int(time.time()),
    }
    manifest = str(mod_manifest or "").strip()
    if manifest:
        payload["mods"] = manifest
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    token = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return INVITE_PREFIX + token


def parse_invite(text: str) -> Invite:
    raw = str(text or "").strip()
    if not raw:
        raise SocialError("请先粘贴好友发来的 DKL 邀请卡。")
    if not raw.startswith(INVITE_PREFIX):
        raise SocialError(
            "邀请卡格式不正确。请让好友在“个人资料与好友”里复制 DKL 邀请卡。"
        )
    token = raw[len(INVITE_PREFIX) :].strip()
    try:
        padding = "=" * (-len(token) % 4)
        data = json.loads(
            base64.urlsafe_b64decode(token + padding).decode("utf-8")
        )
    except Exception as exc:
        raise SocialError("邀请卡已损坏，无法解析。") from exc
    if not isinstance(data, dict):
        raise SocialError("邀请卡内容无法识别。")
    invite = Invite(
        id=str(data.get("id") or "").strip().upper(),
        name=str(data.get("name") or "").strip()[:24],
        room_code=str(data.get("room") or "").strip()[:80],
        room_version=str(data.get("mc") or "").strip()[:64],
        note=str(data.get("note") or "").strip()[:120],
        kind=str(data.get("kind") or "friend"),
        mod_manifest=str(data.get("mods") or "").strip(),
    )
    if not ID_RE.fullmatch(invite.id):
        raise SocialError("邀请卡里的 DKL ID 无效。")
    if not invite.name:
        invite.name = "未命名好友"
    return invite


@dataclass
class SocialStore:
    path: Path = field(default_factory=lambda: SOCIAL_PATH)
    profile: SocialProfile = field(default_factory=SocialProfile)
    friends: list[Friend] = field(default_factory=list)

    def load(self) -> "SocialStore":
        try:
            if self.path.is_file():
                data = json.loads(self.path.read_text(encoding="utf-8"))
            else:
                data = {}
        except Exception:
            data = {}
        if isinstance(data.get("profile"), dict):
            allowed = {
                key: data["profile"].get(key)
                for key in ("id", "name", "created_at", "room_code", "room_version")
            }
            self.profile = SocialProfile(**allowed)
        self.friends = []
        for item in data.get("friends") or []:
            if not isinstance(item, dict):
                continue
            allowed = {
                key: item.get(key)
                for key in (
                    "id",
                    "name",
                    "room_code",
                    "room_version",
                    "note",
                    "added_at",
                    "last_room_at",
                )
            }
            try:
                self.friends.append(Friend(**allowed).ensure())
            except (SocialError, TypeError):
                continue
        self.friends.sort(
            key=lambda friend: (friend.last_room_at, friend.added_at), reverse=True
        )
        return self

    def ensure_profile(self, fallback_name: str = "Steve") -> SocialProfile:
        before = asdict(self.profile)
        self.profile.ensure(fallback_name)
        if asdict(self.profile) != before:
            self.save()
        return self.profile

    def rename_profile(self, name: str) -> SocialProfile:
        clean = str(name or "").strip()
        if not 1 <= len(clean) <= 24:
            raise SocialError("昵称需要 1-24 个字符。")
        self.profile.name = clean
        self.save()
        return self.profile

    def set_room(self, room_code: str, room_version: str = "") -> None:
        self.profile.room_code = str(room_code or "").strip()[:80]
        self.profile.room_version = str(room_version or "").strip()[:64]
        self.save()

    def add_invite(self, invite: Invite) -> Friend:
        if invite.id == self.profile.id:
            raise SocialError("这是你自己的邀请卡，换一张好友的吧。")
        friend = Friend(
            id=invite.id,
            name=invite.name,
            room_code=invite.room_code,
            room_version=invite.room_version,
            note=invite.note,
        ).ensure()
        existing = self.find(friend.id)
        if existing is not None:
            existing.name = friend.name or existing.name
            existing.note = friend.note or existing.note
            if friend.room_code:
                existing.room_code = friend.room_code
                existing.room_version = friend.room_version
                existing.last_room_at = time.time()
            friend = existing
        else:
            if friend.room_code:
                friend.last_room_at = time.time()
            self.friends.insert(0, friend)
        self.save()
        return friend

    def add_friend(
        self, name: str, friend_id: str, room_code: str = "", room_version: str = ""
    ) -> Friend:
        friend = Friend(
            id=str(friend_id or "").strip().upper(),
            name=str(name or "").strip(),
            room_code=room_code,
            room_version=room_version,
        ).ensure()
        if friend.id == self.profile.id:
            raise SocialError("不能把自己添加为好友。")
        existing = self.find(friend.id)
        if existing is not None:
            existing.name = friend.name
            if friend.room_code:
                existing.room_code = friend.room_code
                existing.room_version = friend.room_version
                existing.last_room_at = time.time()
            self.save()
            return existing
        if friend.room_code:
            friend.last_room_at = time.time()
        self.friends.insert(0, friend)
        self.save()
        return friend

    def find(self, friend_id: str) -> Friend | None:
        key = str(friend_id or "").strip().upper()
        return next((friend for friend in self.friends if friend.id == key), None)

    def update_room(
        self, friend_id: str, room_code: str, room_version: str = ""
    ) -> Friend | None:
        friend = self.find(friend_id)
        if friend is None:
            return None
        friend.room_code = str(room_code or "").strip()[:80]
        friend.room_version = str(room_version or "").strip()[:64]
        friend.last_room_at = time.time() if friend.room_code else 0.0
        self.save()
        return friend

    def remove_friend(self, friend_id: str) -> bool:
        friend = self.find(friend_id)
        if friend is None:
            return False
        self.friends.remove(friend)
        self.save()
        return True

    def save(self) -> None:
        data = {
            "version": 1,
            "profile": asdict(self.profile),
            "friends": [asdict(friend) for friend in self.friends],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(self.path)


def load_store(path: Path | None = None) -> SocialStore:
    return SocialStore(path=Path(path or SOCIAL_PATH)).load()
