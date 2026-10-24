from __future__ import annotations

import hashlib
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import curseforge as cf
from . import modrinth
from .mods import ModInfo, forget_mod_meta, load_mod_meta, remember_downloaded_mod

ProgressCallback = Callable[[int, str], None]


class ModUpdateError(Exception):
    pass


@dataclass
class ModUpdate:
    mod: ModInfo
    version: dict[str, Any]
    source: str
    version_label: str
    file_name: str
    size: int = 0
    sha1: str = ""
    page_url: str = ""
    dependencies: list[dict[str, Any]] = field(default_factory=list)

    @property
    def source_label(self) -> str:
        return "CurseForge" if self.source == "curseforge" else "Modrinth"

    @property
    def size_text(self) -> str:
        if self.size >= 1024 * 1024:
            return f"{self.size / 1024 / 1024:.1f} MB"
        if self.size >= 1024:
            return f"{self.size / 1024:.0f} KB"
        return f"{self.size} B" if self.size else "大小未知"


def file_sha1(path: Path) -> str:
    digest = hashlib.sha1()
    try:
        with Path(path).open("rb") as fp:
            while True:
                chunk = fp.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def _hash_of_file(version: dict[str, Any]) -> dict[str, Any]:
    file = modrinth.primary_file(version) or {}
    hashes = file.get("hashes") if isinstance(file.get("hashes"), dict) else {}
    return {
        "filename": Path(str(file.get("filename") or "")).name,
        "size": int(file.get("size") or 0),
        "sha1": str(hashes.get("sha1") or "").lower(),
    }


def _source_for(mod: ModInfo, meta: dict) -> str:
    source = str(meta.get("source") or mod.source or "").strip().lower()
    if source in ("modrinth", "curseforge"):
        return source
    url = str(meta.get("project_url") or mod.project_url or "").lower()
    if "curseforge.com" in url:
        return "curseforge"
    if "modrinth.com" in url:
        return "modrinth"
    return ""


def _version_id(meta: dict, mod: ModInfo) -> str:
    return str(
        meta.get("file_id")
        or meta.get("version_id")
        or mod.file_id
        or mod.version_id
        or ""
    )


def _installed_sha1(mod: ModInfo, meta: dict) -> str:
    return str(meta.get("sha1") or mod.sha1 or "") or file_sha1(mod.path)


def _resolved_loader(mod: ModInfo, loader: str) -> str:
    return str(loader or mod.loader or "").strip()


def check_mod_update(
    mod: ModInfo, game_version: str = "", loader: str = ""
) -> ModUpdate | None:
    """检查单个 Mod 是否有适配当前游戏版本与加载器的新文件。"""
    meta = load_mod_meta(mod.path.parent, mod.path.name)
    source = _source_for(mod, meta)
    project_id = str(meta.get("project_id") or mod.project_id or "").strip()
    installed_sha1 = _installed_sha1(mod, meta)
    current_id = _version_id(meta, mod)
    target_loader = _resolved_loader(mod, loader)

    if not source and installed_sha1:
        looked_up = modrinth.lookup_version_by_hash(installed_sha1)
        if looked_up:
            source = "modrinth"
            project_id = str(looked_up.get("project_id") or "")
            current_id = current_id or str(looked_up.get("id") or "")

    if not project_id:
        return None

    if source == "curseforge":
        versions = cf.fetch_mod_versions(
            project_id,
            target_loader,
            game_version,
            content_type="mod",
        )
        if not versions:
            return None
        latest = versions[0]
        latest_file = _hash_of_file(latest)
        latest_id = str(latest.get("id") or "")
        if current_id and latest_id and current_id == latest_id:
            return None
        if installed_sha1 and latest_file["sha1"]:
            if installed_sha1.lower() == latest_file["sha1"]:
                return None
        if (
            not current_id
            and latest_file["filename"] == mod.filename
            and latest_file["size"] == mod.size
        ):
            return None
        return ModUpdate(
            mod=mod,
            version=latest,
            source="curseforge",
            version_label=str(latest.get("version_number") or latest_id or "新版本"),
            file_name=latest_file["filename"],
            size=latest_file["size"],
            sha1=latest_file["sha1"],
            page_url=str(latest.get("page_url") or mod.project_url),
            dependencies=modrinth.required_dependencies(latest),
        )

    versions = modrinth.fetch_mod_versions(
        project_id,
        target_loader,
        game_version,
        content_type="mod",
    )
    if not versions:
        return None
    latest = versions[0]
    latest_file = _hash_of_file(latest)
    latest_id = str(latest.get("id") or "")
    if current_id and latest_id and current_id == latest_id:
        return None
    if installed_sha1 and latest_file["sha1"]:
        if installed_sha1.lower() == latest_file["sha1"]:
            return None
    if (
        not current_id
        and latest_file["filename"] == mod.filename
        and latest_file["size"] == mod.size
    ):
        return None
    slug = str(meta.get("slug") or "")
    page_url = str(latest.get("page_url") or "")
    if not page_url and latest_id:
        page_url = f"https://modrinth.com/mod/{slug or project_id}/version/{latest_id}"
    return ModUpdate(
        mod=mod,
        version=latest,
        source="modrinth",
        version_label=str(
            latest.get("version_number")
            or latest.get("name")
            or latest_id
            or "新版本"
        ),
        file_name=latest_file["filename"],
        size=latest_file["size"],
        sha1=latest_file["sha1"],
        page_url=page_url or mod.project_url,
        dependencies=modrinth.required_dependencies(latest),
    )


def check_mod_updates(
    mods: list[ModInfo],
    game_version: str = "",
    loader: str = "",
    *,
    max_workers: int = 4,
    progress_cb: Callable[[int, int, str], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[list[ModUpdate], list[str]]:
    """并发检查一组 Mod；单个失败不会影响其他结果。"""
    cancel = cancel_event or threading.Event()
    updates: list[ModUpdate] = []
    errors: list[str] = []
    total = len(mods)
    if not total:
        return updates, errors
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, min(8, int(max_workers)))) as pool:
        futures = {
            pool.submit(check_mod_update, mod, game_version, loader): mod
            for mod in mods
        }
        for future in as_completed(futures):
            if cancel.is_set():
                break
            mod = futures[future]
            done += 1
            try:
                update = future.result()
            except Exception as exc:
                errors.append(f"{mod.name}：{exc}")
            else:
                if update is not None:
                    updates.append(update)
            if progress_cb:
                progress_cb(done, total, mod.name)
    updates.sort(key=lambda item: item.mod.name.lower())
    return updates, errors


def _backup_file(path: Path, backup_root: Path) -> Path:
    backup_root.mkdir(parents=True, exist_ok=True)
    target = backup_root / path.name
    index = 2
    while target.exists():
        target = backup_root / f"{path.stem}-{index}{path.suffix}"
        index += 1
    shutil.move(str(path), str(target))
    return target


def apply_mod_update(
    update: ModUpdate,
    mods_dir: Path,
    *,
    threads: int = 8,
    progress_cb: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    """下载新版本并替换旧文件；旧文件保存在 .dkl-meta/backups 中。"""
    mods_dir = Path(mods_dir)
    mods_dir.mkdir(parents=True, exist_ok=True)
    progress = progress_cb or (lambda p, m: None)
    cancel = cancel_event or threading.Event()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    temp_dir = mods_dir / ".dkl-updates" / f"{stamp}-{time.time_ns() % 1000000}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    downloaded: Path | None = None
    old_path = Path(update.mod.path)
    target_path: Path | None = None
    backup_old: Path | None = None
    backup_target: Path | None = None
    try:
        downloader = (
            cf.download_mod_file
            if update.source == "curseforge"
            else modrinth.download_mod_file
        )
        downloaded = downloader(
            update.version,
            temp_dir,
            progress_cb=progress,
            cancel_event=cancel,
            threads=threads,
        )
        if not downloaded.is_file() or downloaded.stat().st_size <= 0:
            raise ModUpdateError("新版本下载失败，没有拿到有效文件")
        expected_sha1 = str(update.sha1 or "").lower()
        if expected_sha1 and file_sha1(downloaded).lower() != expected_sha1:
            raise ModUpdateError("新版本文件校验失败，已保留旧文件")
        if cancel.is_set():
            raise ModUpdateError("更新已取消")

        suffix = ".disabled" if not update.mod.enabled else ""
        target_path = mods_dir / (downloaded.name + suffix)
        backup_root = mods_dir / ".dkl-meta" / "backups" / "mods" / stamp
        if old_path.exists():
            backup_old = _backup_file(old_path, backup_root)
        if target_path.exists() and target_path != old_path:
            backup_target = _backup_file(target_path, backup_root)
        if target_path.exists():
            # 旧文件与目标同名且已被备份时，理论上不会走到这里；兜底清理空壳。
            target_path.unlink()

        shutil.move(str(downloaded), str(target_path))
        meta = load_mod_meta(mods_dir, update.mod.filename)
        meta.update(
            {
                "title": update.mod.name,
                "project_id": str(
                    meta.get("project_id")
                    or update.mod.project_id
                    or update.version.get("project_id")
                    or ""
                ),
                "source": update.source,
                "source_label": update.source_label,
                "version_id": str(update.version.get("id") or ""),
                "file_id": str(update.version.get("id") or ""),
                "file_name": downloaded.name,
                "sha1": update.sha1,
                "project_url": str(
                    update.page_url or meta.get("project_url") or update.mod.project_url
                ),
            }
        )
        if target_path.name != update.mod.filename:
            forget_mod_meta(mods_dir, update.mod.filename)
        remember_downloaded_mod(mods_dir, target_path, meta)
        progress(100, f"{update.mod.name} 已更新")
        return target_path
    except Exception:
        if target_path is not None and target_path.exists() and backup_old is not None:
            try:
                target_path.unlink()
            except OSError:
                pass
        if backup_old is not None and backup_old.exists() and not old_path.exists():
            try:
                shutil.move(str(backup_old), str(old_path))
            except OSError:
                pass
        if (
            backup_target is not None
            and backup_target.exists()
            and target_path is not None
            and not target_path.exists()
        ):
            try:
                shutil.move(str(backup_target), str(target_path))
            except OSError:
                pass
        raise
    finally:
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass


def apply_mod_updates(
    updates: list[ModUpdate],
    mods_dir: Path,
    *,
    threads: int = 8,
    progress_cb: Callable[[int, str], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[list[Path], list[str]]:
    """按顺序更新多个 Mod，返回成功文件与错误信息。"""
    cancel = cancel_event or threading.Event()
    done_paths: list[Path] = []
    errors: list[str] = []
    total = len(updates)
    for index, update in enumerate(updates):
        if cancel.is_set():
            errors.append("更新已取消")
            break
        def on_progress(percent: int, message: str, i=index) -> None:
            if progress_cb:
                overall = int((i + max(0, min(100, percent)) / 100) / max(1, total) * 100)
                progress_cb(
                    overall,
                    f"[{i + 1}/{total}] {message}",
                )

        try:
            path = apply_mod_update(
                update,
                mods_dir,
                threads=threads,
                progress_cb=on_progress,
                cancel_event=cancel,
            )
        except Exception as exc:
            errors.append(f"{update.mod.name}：{exc}")
        else:
            done_paths.append(path)
        if progress_cb:
            progress_cb(
                int((index + 1) * 100 / max(1, total)),
                f"已处理 {index + 1}/{total} 个 Mod",
            )
    return done_paths, errors
