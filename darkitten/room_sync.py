from __future__ import annotations

import base64
import json
import re
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from . import curseforge as cf
from . import modrinth
from .game_context import (
    LOADER_NAMES,
    LoaderContext,
    all_mod_files,
    enabled_mod_files,
    file_sha1,
    loader_candidates,
    resolve_context,
)
from .mods import load_mod_meta, remember_downloaded_mod
from .social import INVITE_PREFIX, SocialError, parse_invite

MANIFEST_PREFIX = "DKLM1:"
MANIFEST_VERSION = 1
MAX_MANIFEST_CHARS = 100_000
MAX_MANIFEST_MODS = 500
_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")

ProgressCallback = Callable[[int, str], None]


class RoomSyncError(Exception):
    pass


@dataclass
class ManifestMod:
    filename: str
    sha1: str = ""
    size: int = 0
    title: str = ""
    project_id: str = ""
    version_id: str = ""
    file_id: str = ""
    source: str = ""
    slug: str = ""

    @property
    def display_name(self) -> str:
        return self.title or Path(self.filename).stem or "未命名 Mod"

    @property
    def source_label(self) -> str:
        if self.source == "curseforge":
            return "CurseForge"
        if self.source == "modrinth":
            return "Modrinth"
        return "未知来源"

    @property
    def resolvable(self) -> bool:
        return bool(
            (self.sha1 and _SHA1_RE.fullmatch(self.sha1))
            or (self.project_id and self.source)
            or self.project_id
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "f": self.filename,
            "h": self.sha1,
            "s": int(self.size),
            "t": self.title,
            "p": self.project_id,
            "v": self.version_id,
            "i": self.file_id,
            "d": self.source,
            "g": self.slug,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "ManifestMod | None":
        if not isinstance(data, dict):
            return None
        raw_name = str(data.get("f") or "").replace("\\", "/").strip()
        filename = Path(raw_name).name
        if not filename or not filename.lower().endswith(".jar"):
            return None
        sha1 = str(data.get("h") or "").strip().lower()
        if sha1 and not _SHA1_RE.fullmatch(sha1):
            sha1 = ""
        try:
            size = max(0, int(data.get("s") or 0))
        except Exception:
            size = 0
        source = str(data.get("d") or "").strip().lower()
        if source not in ("modrinth", "curseforge"):
            source = ""
        project_id = _clean_id(data.get("p"))
        version_id = _clean_id(data.get("v"))
        file_id = _clean_id(data.get("i"))
        slug = _clean_id(data.get("g")).lower()
        return cls(
            filename=filename[:180],
            sha1=sha1,
            size=size,
            title=str(data.get("t") or "")[:80],
            project_id=project_id,
            version_id=version_id,
            file_id=file_id,
            source=source,
            slug=slug[:80],
        )


def _clean_id(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "", str(value or "").strip())[:80]


@dataclass
class RoomManifest:
    name: str = ""
    version_id: str = ""
    mc_version: str = ""
    loader: str = ""
    created_at: float = 0.0
    mods: list[ManifestMod] = field(default_factory=list)

    @property
    def mod_count(self) -> int:
        return len(self.mods)

    def to_dict(self) -> dict[str, Any]:
        return {
            "v": MANIFEST_VERSION,
            "n": self.name,
            "mv": self.version_id,
            "mc": self.mc_version,
            "ld": self.loader,
            "ts": int(self.created_at or time.time()),
            "mods": [item.to_dict() for item in self.mods],
        }

    @classmethod
    def from_dict(cls, data: Any) -> "RoomManifest":
        if not isinstance(data, dict):
            raise RoomSyncError("Mod 清单格式无法识别。")
        raw_mods = data.get("mods")
        if not isinstance(raw_mods, list):
            raw_mods = []
        if len(raw_mods) > MAX_MANIFEST_MODS:
            raise RoomSyncError("Mod 清单数量异常，已拒绝导入。")
        mods: list[ManifestMod] = []
        seen: set[tuple[str, str]] = set()
        for item in raw_mods:
            mod = ManifestMod.from_dict(item)
            if mod is None:
                continue
            key = (mod.filename.lower(), mod.sha1)
            if key in seen:
                continue
            seen.add(key)
            mods.append(mod)
        return cls(
            name=str(data.get("n") or "")[:24],
            version_id=str(data.get("mv") or "")[:80],
            mc_version=str(data.get("mc") or "")[:40],
            loader=str(data.get("ld") or "")[:24],
            created_at=float(data.get("ts") or 0),
            mods=mods,
        )


def encode_manifest(manifest: RoomManifest) -> str:
    raw = json.dumps(
        manifest.to_dict(), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    token = base64.urlsafe_b64encode(zlib.compress(raw, 9)).decode("ascii").rstrip("=")
    text = MANIFEST_PREFIX + token
    if len(text) > MAX_MANIFEST_CHARS:
        raise RoomSyncError(
            "整合包 Mod 数量过多，邀请卡会超出可复制长度。"
            "可以改用“同步房主 Mod”手动粘贴清单。"
        )
    return text


def decode_manifest(text: str) -> RoomManifest:
    raw = str(text or "").strip()
    if not raw:
        raise RoomSyncError("请先粘贴房主的邀请卡或 Mod 清单。")
    if raw.startswith(INVITE_PREFIX):
        try:
            invite = parse_invite(raw)
        except SocialError as exc:
            raise RoomSyncError(str(exc)) from exc
        if not invite.mod_manifest:
            raise RoomSyncError("这张邀请卡没有携带 Mod 清单，房主可能使用的是旧版本。")
        manifest = decode_manifest(invite.mod_manifest)
        if not manifest.name:
            manifest.name = invite.name
        if not manifest.version_id:
            manifest.version_id = invite.room_version
        return manifest
    if not raw.startswith(MANIFEST_PREFIX):
        raise RoomSyncError("Mod 清单格式不正确，应以 DKLM1: 或 DKLF1: 开头。")
    token = raw[len(MANIFEST_PREFIX) :].strip()
    try:
        padding = "=" * (-len(token) % 4)
        payload = zlib.decompress(
            base64.urlsafe_b64decode(token + padding)
        ).decode("utf-8")
        data = json.loads(payload)
    except Exception as exc:
        raise RoomSyncError("Mod 清单已损坏，无法解析。") from exc
    return RoomManifest.from_dict(data)


def build_room_manifest(
    version_id: str,
    config,
    versions: Iterable,
    *,
    host_name: str = "",
    lookup_unknown: bool = True,
    max_workers: int = 4,
    progress_cb: Callable[[int, str], None] | None = None,
    cancel_event=None,
) -> RoomManifest:
    """读取实例里启用的 Mod，并尽量补全 Modrinth 来源。"""
    progress = progress_cb or (lambda p, m: None)
    context = resolve_context(version_id, versions, config)
    files = enabled_mod_files(context.mods_dir)
    total = len(files)
    mods: list[ManifestMod] = []
    unknown: list[tuple[ManifestMod, Path]] = []
    for index, path in enumerate(files):
        if cancel_event is not None and cancel_event.is_set():
            break
        meta = load_mod_meta(context.mods_dir, path.name)
        sha1 = str(meta.get("sha1") or "").strip().lower()
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        if not sha1:
            sha1 = file_sha1(path)
        source = str(meta.get("source") or "").strip().lower()
        if source not in ("modrinth", "curseforge"):
            source = ""
        mod = ManifestMod(
            filename=path.name,
            sha1=sha1 if _SHA1_RE.fullmatch(sha1) else "",
            size=size,
            title=str(meta.get("title") or path.stem)[:80],
            project_id=_clean_id(meta.get("project_id")),
            version_id=_clean_id(meta.get("version_id")),
            file_id=_clean_id(meta.get("file_id")),
            source=source,
            slug=_clean_id(meta.get("slug")).lower(),
        )
        mods.append(mod)
        if lookup_unknown and not mod.project_id and mod.sha1:
            unknown.append((mod, path))
        progress(
            int((index + 1) / max(1, total) * 60),
            f"正在整理 Mod 清单 {index + 1}/{total}",
        )

    if unknown and lookup_unknown and not (cancel_event is not None and cancel_event.is_set()):
        done = 0
        with ThreadPoolExecutor(max_workers=max(1, min(8, max_workers))) as pool:
            futures = {
                pool.submit(modrinth.lookup_version_by_hash, mod.sha1): mod
                for mod, _path in unknown
            }
            for future in as_completed(futures):
                if cancel_event is not None and cancel_event.is_set():
                    break
                mod = futures[future]
                done += 1
                try:
                    version = future.result()
                except Exception:
                    version = None
                if isinstance(version, dict):
                    mod.source = "modrinth"
                    mod.project_id = _clean_id(version.get("project_id")) or mod.project_id
                    mod.version_id = _clean_id(version.get("id")) or mod.version_id
                    mod.file_id = mod.version_id
                progress(
                    60 + int(done / max(1, len(unknown)) * 35),
                    f"正在识别来源 {done}/{len(unknown)}",
                )

    mods.sort(key=lambda item: item.filename.lower())
    progress(100, f"Mod 清单整理完成，共 {len(mods)} 个")
    return RoomManifest(
        name=str(host_name or "")[:24],
        version_id=context.version_id,
        mc_version=context.mc_version,
        loader=context.loader,
        created_at=time.time(),
        mods=mods,
    )


# ---------------------------------------------------------------------------
# 差异检查与同步


@dataclass
class RoomSyncPlan:
    manifest: RoomManifest
    context: LoaderContext
    present: int
    missing: list[ManifestMod]
    unknown: list[ManifestMod]
    warnings: list[str]

    @property
    def missing_count(self) -> int:
        return len(self.missing)

    @property
    def total(self) -> int:
        return self.manifest.mod_count


@dataclass
class RoomSyncResult:
    downloaded: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False


def _installed_index(mods_dir: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in all_mod_files(mods_dir):
        meta = load_mod_meta(mods_dir, path.name)
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        items.append(
            {
                "filename": path.name,
                "lower": path.name.lower(),
                "size": size,
                "sha1": str(meta.get("sha1") or "").strip().lower(),
            }
        )
    return items


def plan_room_sync(
    manifest: RoomManifest,
    version_id: str,
    config,
    versions: Iterable,
) -> RoomSyncPlan:
    context = resolve_context(version_id, versions, config)
    installed = _installed_index(context.mods_dir)
    present = 0
    missing: list[ManifestMod] = []
    unknown: list[ManifestMod] = []
    for mod in manifest.mods:
        match = any(
            item["lower"] == mod.filename.lower()
            and (not mod.size or not item["size"] or item["size"] == mod.size)
            for item in installed
        )
        if not match and mod.sha1:
            match = any(item["sha1"] == mod.sha1 for item in installed)
        if match:
            present += 1
        elif mod.resolvable:
            missing.append(mod)
        else:
            unknown.append(mod)

    warnings: list[str] = []
    if manifest.loader and context.loader and manifest.loader.lower() != context.loader.lower():
        warnings.append(
            f"房主使用 {manifest.loader}，你选择的是 {context.loader}，"
            "加载器不一致时 Mod 无法通用。"
        )
    if manifest.mc_version and context.mc_version and manifest.mc_version != context.mc_version:
        warnings.append(
            f"房主是 Minecraft {manifest.mc_version}，你选择的是 "
            f"{context.mc_version}，请确认版本一致。"
        )
    if not context.has_loader and manifest.mods:
        warnings.append("当前实例是原版启动，没有加载器，同步的 Mod 不会生效。")
    if unknown:
        warnings.append(f"有 {len(unknown)} 个 Mod 缺少来源信息，无法自动下载。")

    return RoomSyncPlan(
        manifest=manifest,
        context=context,
        present=present,
        missing=missing,
        unknown=unknown,
        warnings=list(dict.fromkeys(warnings)),
    )


def _file_entry(version: dict[str, Any]) -> dict[str, Any]:
    files = [item for item in version.get("files") or [] if isinstance(item, dict)]
    for item in files:
        if item.get("primary") and item.get("url"):
            return item
    for item in files:
        if item.get("url"):
            return item
    return {}


def _version_sha1(version: dict[str, Any]) -> str:
    file = _file_entry(version)
    hashes = file.get("hashes") if isinstance(file.get("hashes"), dict) else {}
    return str(hashes.get("sha1") or "").strip().lower()


def _pick_versions(
    mod: ManifestMod, context: LoaderContext
) -> list[dict[str, Any]]:
    if mod.source == "curseforge" and mod.project_id:
        versions = cf.fetch_mod_versions(
            mod.project_id, context.loader, context.mc_version, "mod"
        )
        if not versions:
            versions = cf.fetch_mod_versions(mod.project_id, "", "", "mod")
        return _prefer_version(versions, mod)
    if mod.project_id:
        versions: list[dict[str, Any]] = []
        for key in loader_candidates(context.loader):
            versions = modrinth.fetch_mod_versions(
                mod.project_id,
                LOADER_NAMES.get(key, key),
                context.mc_version,
                "mod",
            )
            if versions:
                break
        if not versions:
            versions = modrinth.fetch_mod_versions(
                mod.project_id, "", "", "mod"
            )
        return _prefer_version(versions, mod)
    return []


def _prefer_version(
    versions: list[dict[str, Any]], mod: ManifestMod
) -> list[dict[str, Any]]:
    target = mod.version_id or mod.file_id
    if target:
        exact = [
            item
            for item in versions
            if str(item.get("id") or "") == target
        ]
        if exact:
            return exact + [item for item in versions if item not in exact]
    return versions


def _metadata_from_version(
    mod: ManifestMod,
    version: dict[str, Any],
    path: Path,
    context: LoaderContext,
    source: str,
) -> dict[str, Any]:
    file = _file_entry(version)
    hashes = file.get("hashes") if isinstance(file.get("hashes"), dict) else {}
    project_id = mod.project_id or _clean_id(version.get("project_id"))
    slug = mod.slug
    page_url = str(version.get("page_url") or "")
    if source == "modrinth" and not page_url:
        page_url = f"https://modrinth.com/mod/{slug or project_id}"
    return {
        "title": mod.title or Path(path.name).stem,
        "project_id": project_id,
        "slug": slug,
        "source": source,
        "source_label": "CurseForge" if source == "curseforge" else "Modrinth",
        "version_id": str(version.get("id") or ""),
        "file_id": str(version.get("id") or ""),
        "file_name": path.name,
        "sha1": str(hashes.get("sha1") or "") or mod.sha1,
        "content_type": "mod",
        "icon_url": "",
        "author": "",
        "categories": [],
        "loaders": version.get("loaders") or [],
        "game_versions": version.get("game_versions") or [],
        "downloads": 0,
        "description": "",
        "description_zh": "",
        "project_url": page_url,
        "game_version": context.mc_version,
        "loader": context.loader,
    }


def _download_one(
    mod: ManifestMod,
    context: LoaderContext,
    *,
    threads: int,
    progress_cb: ProgressCallback | None = None,
    cancel_event=None,
) -> tuple[ManifestMod, Path | None, str]:
    progress = progress_cb or (lambda p, m: None)
    mods_dir = context.mods_dir
    mods_dir.mkdir(parents=True, exist_ok=True)
    version: dict[str, Any] | None = None
    source = mod.source

    if mod.sha1:
        try:
            looked_up = modrinth.lookup_version_by_hash(mod.sha1)
        except Exception:
            looked_up = None
        if isinstance(looked_up, dict) and looked_up.get("id"):
            version = looked_up
            source = "modrinth"

    if version is None and source == "curseforge" and mod.project_id:
        if mod.file_id:
            try:
                version = cf.fetch_file(mod.project_id, mod.file_id, "mod")
            except Exception:
                version = None
        if version is None:
            versions = _pick_versions(mod, context)
            version = versions[0] if versions else None
        if version is not None:
            path = cf.download_mod_file(
                version,
                mods_dir,
                progress_cb=progress,
                cancel_event=cancel_event,
                threads=threads,
            )
            _verify_download(path, mod)
            metadata = _metadata_from_version(
                mod, version, path, context, "curseforge"
            )
            remember_downloaded_mod(mods_dir, path, metadata)
            return mod, path, ""

    if version is None:
        try:
            versions = _pick_versions(mod, context)
        except Exception as exc:
            return mod, None, str(exc)
        version = versions[0] if versions else None

    if version is None:
        return mod, None, "找不到可下载的文件"

    path = modrinth.download_mod_file(
        version,
        mods_dir,
        progress_cb=progress,
        cancel_event=cancel_event,
        threads=threads,
    )
    _verify_download(path, mod)
    metadata = _metadata_from_version(mod, version, path, context, "modrinth")
    remember_downloaded_mod(mods_dir, path, metadata)
    return mod, path, ""


def _verify_download(path: Path, mod: ManifestMod) -> None:
    if not path.is_file() or path.stat().st_size <= 0:
        raise RoomSyncError(f"{mod.display_name} 下载后没有有效文件")
    if mod.sha1:
        actual = file_sha1(path).lower()
        if actual != mod.sha1:
            raise RoomSyncError(f"{mod.display_name} 文件校验失败")


def sync_missing_mods(
    plan: RoomSyncPlan,
    *,
    threads: int = 8,
    max_workers: int = 4,
    progress_cb: Callable[[int, str], None] | None = None,
    cancel_event=None,
) -> RoomSyncResult:
    progress = progress_cb or (lambda p, m: None)
    result = RoomSyncResult()
    missing = list(plan.missing)
    if not missing:
        progress(100, "没有缺失的 Mod")
        return result
    total = len(missing)
    lock = threading.Lock()
    state = {"done": 0}

    def on_progress(percent: int, message: str, label: str) -> None:
        with lock:
            done = state["done"]
        overall = int((done + max(0, min(100, percent)) / 100) / total * 100)
        progress(overall, f"[{done + 1}/{total}] {message}")

    def worker(mod: ManifestMod) -> tuple[ManifestMod, Path | None, str]:
        if cancel_event is not None and cancel_event.is_set():
            return mod, None, ""
        try:
            return _download_one(
                mod,
                plan.context,
                threads=threads,
                progress_cb=lambda p, m: on_progress(p, m, mod.display_name),
                cancel_event=cancel_event,
            )
        except Exception as exc:
            return mod, None, str(exc)

    with ThreadPoolExecutor(max_workers=max(1, min(8, max_workers))) as pool:
        futures = [pool.submit(worker, mod) for mod in missing]
        for future in as_completed(futures):
            if cancel_event is not None and cancel_event.is_set():
                result.cancelled = True
            mod, path, error = future.result()
            with lock:
                state["done"] += 1
            if path is not None:
                result.downloaded.append(path.name)
            elif error:
                result.errors.append(f"{mod.display_name}：{error}")
            if cancel_event is not None and cancel_event.is_set():
                result.cancelled = True
                break
            progress(
                int(state["done"] / total * 100),
                f"已同步 {state['done']}/{total} 个 Mod",
            )
    progress(100, "Mod 同步完成" if not result.cancelled else "Mod 同步已取消")
    return result
