from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from PySide6.QtGui import QImage

from .config import APP_DIR
from .fsops import recycle_path
from .skins import (
    SkinError,
    SkinFile,
    load_skin_file,
    local_skin_path,
    save_local_skin,
)

LIBRARY_DIR = APP_DIR / "skins" / "library"
INDEX_PATH = LIBRARY_DIR / "index.json"
INDEX_VERSION = 1
VALID_VARIANTS = ("classic", "slim")
_ID_RE = re.compile(r"[a-f0-9]{8,64}")
_BAD_NAME_RE = re.compile(r"[\x00-\x1f\x7f/\\:*?\"<>|]")


class SkinLibraryError(Exception):
    pass


@dataclass
class LibrarySkin:
    id: str
    name: str
    filename: str
    hash: str
    variant: str = "classic"
    added: int = 0
    source: str = ""
    legacy: bool = False

    @property
    def path(self) -> Path:
        return LIBRARY_DIR / self.filename

    @property
    def exists(self) -> bool:
        return self.path.is_file()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["file"] = data.pop("filename")
        return data


def library_dir() -> Path:
    return LIBRARY_DIR


def _clean_name(value: str, fallback: str = "未命名皮肤") -> str:
    name = _BAD_NAME_RE.sub(" ", str(value or "")).strip()
    name = re.sub(r"\s+", " ", name)
    return name[:32] or fallback


def _safe_path(filename: str) -> Path | None:
    name = Path(str(filename or "")).name
    if not name or name != str(filename) or name in (".", ".."):
        return None
    path = (LIBRARY_DIR / name).resolve()
    try:
        path.relative_to(LIBRARY_DIR.resolve())
    except ValueError:
        return None
    return path


def _read_entries() -> list[LibrarySkin]:
    try:
        raw = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(raw, dict) or not isinstance(raw.get("entries"), list):
        return []
    entries: list[LibrarySkin] = []
    seen: set[str] = set()
    for item in raw["entries"]:
        if not isinstance(item, dict):
            continue
        entry_id = str(item.get("id") or "").strip().lower()
        filename = str(item.get("file") or item.get("filename") or "").strip()
        digest = str(item.get("hash") or "").strip().lower()
        if not _ID_RE.fullmatch(entry_id) or entry_id in seen:
            continue
        path = _safe_path(filename)
        if path is None or not path.is_file():
            continue
        variant = str(item.get("variant") or "classic").lower()
        if variant not in VALID_VARIANTS:
            variant = "classic"
        try:
            added = int(item.get("added") or 0)
        except Exception:
            added = 0
        entries.append(
            LibrarySkin(
                id=entry_id,
                name=_clean_name(str(item.get("name") or path.stem)),
                filename=path.name,
                hash=digest or _hash_file(path),
                variant=variant,
                added=added,
                source=str(item.get("source") or ""),
                legacy=bool(item.get("legacy")),
            )
        )
        seen.add(entry_id)
    entries.sort(key=lambda entry: entry.added, reverse=True)
    return entries


def _write_entries(entries: list[LibrarySkin]) -> None:
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": INDEX_VERSION,
        "entries": [entry.to_dict() for entry in entries],
    }
    tmp = INDEX_PATH.with_name(INDEX_PATH.name + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(tmp, INDEX_PATH)


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash_file(path: Path) -> str:
    try:
        return _hash_bytes(path.read_bytes())
    except OSError:
        return ""


def list_skins() -> list[LibrarySkin]:
    return _read_entries()


def get_skin(entry_id: str) -> LibrarySkin | None:
    key = str(entry_id or "").strip()
    return next((entry for entry in _read_entries() if entry.id == key), None)


def add_skin(
    skin: SkinFile,
    *,
    name: str = "",
    variant: str = "classic",
    source: str = "",
) -> LibrarySkin:
    if not skin.data:
        raise SkinLibraryError("皮肤数据为空，无法加入皮肤库。")
    digest = _hash_bytes(skin.data)
    entries = _read_entries()
    existing = next((entry for entry in entries if entry.hash == digest), None)
    if existing is not None:
        changed = False
        clean = _clean_name(name, existing.name) if name else ""
        if clean and clean != existing.name:
            existing.name = clean
            changed = True
        if changed:
            _write_entries(entries)
        return existing

    entry_id = digest[:16]
    while any(entry.id == entry_id for entry in entries):
        entry_id = digest[: min(len(digest), len(entry_id) + 2)]
    filename = f"{entry_id}.png"
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    target = LIBRARY_DIR / filename
    tmp = target.with_name(filename + ".tmp")
    try:
        tmp.write_bytes(skin.data)
        os.replace(tmp, target)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise SkinLibraryError(f"无法写入皮肤库：{exc}") from exc

    chosen_variant = "slim" if str(variant).lower() == "slim" else "classic"
    if skin.legacy:
        chosen_variant = "classic"
    entry = LibrarySkin(
        id=entry_id,
        name=_clean_name(name, Path(source).stem if source else "未命名皮肤"),
        filename=filename,
        hash=digest,
        variant=chosen_variant,
        added=int(time.time()),
        source=str(source or ""),
        legacy=bool(skin.legacy),
    )
    entries.append(entry)
    _write_entries(entries)
    return entry


def import_path(
    path: str | Path,
    *,
    name: str = "",
    variant: str = "classic",
) -> LibrarySkin:
    file_path = Path(path)
    skin = load_skin_file(file_path)
    return add_skin(
        skin,
        name=name or file_path.stem,
        variant=variant,
        source=str(file_path),
    )


def set_variant(entry_id: str, variant: str) -> LibrarySkin | None:
    entries = _read_entries()
    entry = next((item for item in entries if item.id == entry_id), None)
    if entry is None:
        return None
    chosen = "slim" if str(variant).lower() == "slim" else "classic"
    if entry.legacy:
        chosen = "classic"
    if chosen != entry.variant:
        entry.variant = chosen
        _write_entries(entries)
    return entry


def rename_skin(entry_id: str, name: str) -> LibrarySkin | None:
    entries = _read_entries()
    entry = next((item for item in entries if item.id == entry_id), None)
    if entry is None:
        return None
    clean = _clean_name(name, entry.name)
    if clean != entry.name:
        entry.name = clean
        _write_entries(entries)
    return entry


def delete_skin(
    entry_id: str, *, to_recycle_bin: bool = True
) -> tuple[bool, str]:
    entries = _read_entries()
    entry = next((item for item in entries if item.id == entry_id), None)
    if entry is None:
        return False, "皮肤库中找不到这个皮肤。"
    remaining = [item for item in entries if item.id != entry_id]
    try:
        _write_entries(remaining)
    except Exception as exc:
        return False, f"无法更新皮肤库索引：{exc}"

    path = entry.path
    if not path.is_file():
        return True, "已从皮肤库移除（文件已不存在）。"
    if not to_recycle_bin:
        try:
            path.unlink()
        except OSError as exc:
            return True, f"已从皮肤库移除，但文件删除失败：{exc}"
        return True, "已从皮肤库移除。"
    try:
        recycle_path(path)
    except Exception as exc:
        return True, f"已从皮肤库移除，但文件未能移入回收站：{exc}"
    return True, "已从皮肤库移除。"


def sync_preview_copy(entry: LibrarySkin) -> Path:
    """把皮肤库里的皮肤同步为 local-skin.png，兼容旧版头像读取逻辑。"""
    try:
        data = entry.path.read_bytes()
    except OSError as exc:
        raise SkinLibraryError(f"无法读取皮肤文件：{exc}") from exc
    try:
        save_local_skin(
            SkinFile(
                image=QImage(),
                data=data,
                legacy=bool(entry.legacy),
                source=str(entry.path),
            )
        )
    except SkinError as exc:
        raise SkinLibraryError(str(exc)) from exc
    return local_skin_path()


def load_entry_image(entry: LibrarySkin) -> QImage:
    try:
        return load_skin_file(entry.path).image
    except SkinError:
        return QImage()


def path_matches(entry: LibrarySkin, configured: str | Path | None) -> bool:
    if not configured:
        return False
    try:
        return entry.path.resolve() == Path(configured).resolve()
    except OSError:
        return False


def ensure_library(cfg) -> list[LibrarySkin]:
    """首次打开皮肤页时，把旧版单文件皮肤迁移进皮肤库。"""
    entries = _read_entries()
    configured = str(getattr(cfg, "skin_path", "") or "").strip()
    candidate: Path | None = None
    if configured:
        path = Path(configured)
        if path.is_file():
            candidate = path
    if candidate is None and not entries:
        local = local_skin_path()
        if local.is_file():
            candidate = local
    if candidate is None:
        return entries

    try:
        skin = load_skin_file(candidate)
    except SkinError:
        return entries

    entry: LibrarySkin | None = None
    try:
        entry = add_skin(
            skin,
            name="当前皮肤" if candidate == local_skin_path() else candidate.stem,
            variant=str(getattr(cfg, "skin_variant", "classic") or "classic"),
            source=str(candidate),
        )
    except SkinLibraryError:
        return entries

    entries = _read_entries()
    if entry is None:
        return entries
    variant = entry.variant
    changed = False
    if not path_matches(entry, configured):
        cfg.skin_path = str(entry.path)
        changed = True
    if getattr(cfg, "skin_variant", "") != variant:
        cfg.skin_variant = variant
        changed = True
    if changed:
        cfg.save()
    return entries
