from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin

from .config import APP_DIR, DEFAULT_UPDATE_URL
from .downloader import DownloadCancelled, download_to_file
from .pet import is_newer
from .updater import fetch_manifest, sha256_file
from .zip_extract import ExtractJob, extract_zip_entries

AGENT_NAME = "Darkitten Mod Agent"
AGENT_ID = "DarkittenModAgent"
AGENT_EXE = "DarkittenModAgent.exe"
AGENT_MANIFEST_NAME = "mod_agent_release.json"
AGENT_STATE_FILE = "state.json"
MAX_AGENT_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_AGENT_UNPACKED_BYTES = 512 * 1024 * 1024

ProgressCallback = Callable[[int, str], None]


class ModAgentError(Exception):
    pass


@dataclass
class ModAgentRelease:
    version: str
    name: str = AGENT_NAME
    download_url: str = ""
    sha256: str = ""
    size: int = 0
    released: str = ""
    notes: list[str] = field(default_factory=list)


def mod_agent_root() -> Path:
    return Path(APP_DIR) / "mod_agent"


def mod_agent_app_dir(root: Path | None = None) -> Path:
    return (Path(root) if root is not None else mod_agent_root()) / AGENT_ID


def mod_agent_exe_path(root: Path | None = None) -> Path:
    return mod_agent_app_dir(root) / AGENT_EXE


def mod_agent_state_path(root: Path | None = None) -> Path:
    return (Path(root) if root is not None else mod_agent_root()) / AGENT_STATE_FILE


def is_installed(root: Path | None = None) -> bool:
    return mod_agent_exe_path(root).is_file()


def installed_info(root: Path | None = None) -> dict:
    try:
        data = json.loads(mod_agent_state_path(root).read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def installed_version(root: Path | None = None) -> str:
    return str(installed_info(root).get("version") or "")


def manifest_url(update_url: str = DEFAULT_UPDATE_URL) -> str:
    base = str(update_url or "").strip() or DEFAULT_UPDATE_URL
    if base.rsplit("/", 1)[-1].lower().endswith(".json"):
        return urljoin(base, AGENT_MANIFEST_NAME)
    return base.rstrip("/") + "/" + AGENT_MANIFEST_NAME


def _normalize_notes(value) -> list[str]:
    if isinstance(value, str):
        raw = str(value).splitlines()
    elif isinstance(value, (list, tuple)):
        raw = [str(item) for item in value]
    else:
        return []
    notes: list[str] = []
    for line in raw:
        text = str(line).strip().lstrip("-*•").strip()
        if text:
            notes.append(text)
    return notes[:20]


def parse_mod_agent_release(
    manifest: dict, base_url: str = DEFAULT_UPDATE_URL
) -> ModAgentRelease | None:
    data = manifest if isinstance(manifest, dict) else None
    if not data:
        return None
    version = str(data.get("version") or "").strip().lstrip("vV")
    if not version:
        return None
    download_url = str(data.get("download_url") or data.get("url") or "").strip()
    if download_url:
        download_url = urljoin(base_url, download_url)
    digest = str(data.get("sha256") or "").strip().lower()
    try:
        size = int(data.get("size") or 0)
    except Exception:
        size = 0
    return ModAgentRelease(
        version=version,
        name=str(data.get("name") or AGENT_NAME),
        download_url=download_url,
        sha256=digest,
        size=max(0, size),
        released=str(data.get("released") or ""),
        notes=_normalize_notes(data.get("notes")),
    )


def fetch_mod_agent_release(url: str = DEFAULT_UPDATE_URL) -> ModAgentRelease | None:
    target = manifest_url(url)
    return parse_mod_agent_release(fetch_manifest(target), target)


def archive_path(
    release: ModAgentRelease, root: Path | None = None
) -> Path:
    folder = (Path(root) if root is not None else mod_agent_root()) / "downloads"
    return folder / f"{AGENT_ID}-{release.version}.zip"


def _emit(progress: ProgressCallback | None, percent: int, message: str) -> None:
    if progress is not None:
        progress(max(0, min(100, int(percent))), str(message))


def download_mod_agent(
    release: ModAgentRelease,
    root: Path | None = None,
    *,
    progress_cb: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
    threads: int = 4,
) -> Path:
    if not release.download_url:
        raise ModAgentError("发布清单里没有模组工坊下载地址")
    if not release.sha256:
        raise ModAgentError("发布清单里没有模组工坊 SHA-256，已停止下载以保护安全")
    cancel_event = cancel_event or threading.Event()
    dest = archive_path(release, root)
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.exists():
        try:
            if sha256_file(dest, cancel_event).lower() == release.sha256.lower():
                _emit(progress_cb, 100, "模组工坊安装包已下载")
                return dest
        except DownloadCancelled:
            raise
        except Exception:
            pass
        try:
            dest.unlink()
        except OSError:
            pass

    def on_bytes(done: int, total: int) -> None:
        total = int(total or release.size or 0)
        if total:
            percent = max(1, min(99, int(done * 100 / total)))
            _emit(
                progress_cb,
                percent,
                f"{done / 1024 / 1024:.1f}/{total / 1024 / 1024:.1f} MB",
            )
        else:
            _emit(progress_cb, 50, f"已下载 {done / 1024 / 1024:.1f} MB")

    _emit(progress_cb, 1, f"正在下载模组工坊 {release.version}…")
    try:
        download_to_file(
            release.download_url,
            dest,
            size=release.size,
            cancel_event=cancel_event,
            progress_cb=on_bytes,
            connections=max(2, min(6, int(threads))),
        )
    except DownloadCancelled:
        raise
    except Exception as exc:
        raise ModAgentError(f"模组工坊下载失败：{exc}") from exc

    if dest.stat().st_size > MAX_AGENT_ARCHIVE_BYTES:
        try:
            dest.unlink()
        except OSError:
            pass
        raise ModAgentError("模组工坊安装包体积异常，已删除")
    digest = sha256_file(dest, cancel_event).lower()
    if digest != release.sha256.lower():
        try:
            dest.unlink()
        except OSError:
            pass
        raise ModAgentError("模组工坊安装包 SHA-256 校验失败，已删除损坏文件")
    _emit(progress_cb, 100, "模组工坊安装包下载完成")
    return dest


def _plan_jobs(
    archive: Path, staging: Path, cancel_event: threading.Event | None
) -> tuple[list[ExtractJob], int]:
    jobs: list[ExtractJob] = []
    total = 0
    root = staging.resolve()
    try:
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                if cancel_event is not None and cancel_event.is_set():
                    raise DownloadCancelled()
                name = info.filename.replace("\\", "/")
                if not name or name.endswith("/"):
                    continue
                parts = [part for part in name.split("/") if part]
                if parts and parts[0].lower() == AGENT_ID.lower():
                    parts = parts[1:]
                if not parts:
                    continue
                relative = Path(*parts)
                if relative.is_absolute() or ".." in relative.parts:
                    raise ModAgentError("模组工坊安装包包含不安全的路径")
                destination = (staging / relative).resolve()
                if destination != root and root not in destination.parents:
                    raise ModAgentError("模组工坊安装包包含不安全的路径")
                total += max(0, int(info.file_size))
                if total > MAX_AGENT_UNPACKED_BYTES:
                    raise ModAgentError("模组工坊安装包解压后体积异常，已停止")
                jobs.append(
                    ExtractJob(
                        info=info,
                        destination=destination,
                        label=relative.name,
                        check_exists=False,
                    )
                )
    except (ModAgentError, DownloadCancelled):
        raise
    except zipfile.BadZipFile as exc:
        raise ModAgentError("模组工坊安装包不是有效的 ZIP 文件") from exc
    except Exception as exc:
        raise ModAgentError(f"读取模组工坊安装包失败：{exc}") from exc
    if not jobs:
        raise ModAgentError("模组工坊安装包是空的")
    return jobs, total


def _exe_locked(exe: Path) -> bool:
    """目标目录里的模组工坊正在运行时，Windows 会锁住 exe，无法替换。"""
    if not exe.is_file():
        return False
    try:
        with exe.open("r+b"):
            pass
    except OSError:
        return True
    return False


def install_mod_agent(
    archive: Path,
    version: str,
    root: Path | None = None,
    *,
    progress_cb: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    archive = Path(archive)
    if not archive.is_file():
        raise ModAgentError("模组工坊安装包不存在，请重新下载")
    base = Path(root) if root is not None else mod_agent_root()
    target = base / AGENT_ID
    staging = base / f".staging-{AGENT_ID}"
    backup = base / f".backup-{AGENT_ID}"
    if target.exists() and _exe_locked(target / AGENT_EXE):
        raise ModAgentError("模组工坊正在运行，请先关闭再更新")
    base.mkdir(parents=True, exist_ok=True)
    for leftover in (staging, backup):
        if leftover.exists():
            shutil.rmtree(leftover, ignore_errors=True)

    jobs, total = _plan_jobs(archive, staging, cancel_event)

    def on_extract(done_bytes: int, _done_files: int, label: str) -> None:
        percent = max(1, min(99, int(done_bytes * 100 / total))) if total else 50
        _emit(progress_cb, percent, f"正在解压 {label}…")

    _emit(progress_cb, 1, "正在安装模组工坊…")
    try:
        extract_zip_entries(
            archive,
            jobs,
            threads=4,
            overwrite=True,
            cancel_event=cancel_event,
            progress_cb=on_extract,
        )
    except DownloadCancelled:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise ModAgentError(f"解压模组工坊失败：{exc}") from exc

    if not (staging / AGENT_EXE).is_file():
        shutil.rmtree(staging, ignore_errors=True)
        raise ModAgentError("模组工坊安装包里没有找到 DarkittenModAgent.exe")

    moved_old = False
    if target.exists():
        old_data = target / "data"
        if old_data.is_dir():
            new_data = staging / "data"
            if new_data.exists():
                shutil.rmtree(new_data, ignore_errors=True)
            shutil.move(str(old_data), str(new_data))
        try:
            target.rename(backup)
            moved_old = True
        except Exception as exc:
            shutil.rmtree(staging, ignore_errors=True)
            raise ModAgentError(f"无法替换旧版模组工坊，可能仍有文件被占用：{exc}") from exc

    try:
        staging.rename(target)
    except Exception as exc:
        if moved_old and backup.exists() and not target.exists():
            try:
                backup.rename(target)
            except Exception:
                pass
        shutil.rmtree(staging, ignore_errors=True)
        raise ModAgentError(f"安装模组工坊失败：{exc}") from exc

    if moved_old:
        shutil.rmtree(backup, ignore_errors=True)

    state = {
        "version": str(version or ""),
        "installed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "archive": archive.name,
        "size": archive.stat().st_size,
    }
    try:
        (base / AGENT_STATE_FILE).write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass
    _emit(progress_cb, 100, "模组工坊安装完成")
    return target


def launch_mod_agent(root: Path | None = None) -> subprocess.Popen:
    exe = mod_agent_exe_path(root)
    if not exe.is_file():
        raise ModAgentError("模组工坊还没有安装")
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
    try:
        return subprocess.Popen(
            [str(exe)],
            cwd=str(exe.parent),
            creationflags=flags,
            close_fds=True,
        )
    except Exception as exc:
        raise ModAgentError(f"无法启动模组工坊：{exc}") from exc


def open_mod_agent_folder(root: Path | None = None) -> None:
    folder = mod_agent_app_dir(root)
    if not folder.is_dir():
        raise ModAgentError("模组工坊还没有安装")
    if os.name == "nt":
        os.startfile(str(folder))
        return
    subprocess.Popen(["xdg-open", str(folder)])


__all__ = [
    "AGENT_EXE",
    "AGENT_ID",
    "AGENT_NAME",
    "ModAgentError",
    "ModAgentRelease",
    "fetch_mod_agent_release",
    "install_mod_agent",
    "installed_version",
    "is_installed",
    "is_newer",
    "launch_mod_agent",
    "mod_agent_app_dir",
    "mod_agent_exe_path",
    "open_mod_agent_folder",
]
