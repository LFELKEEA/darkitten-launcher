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
from .updater import UpdateError, fetch_manifest, sha256_file
from .zip_extract import ExtractJob, extract_zip_entries

PET_NAME = "LFELKEEA 桌宠"
PET_ID = "LFELKEEA"
PET_EXE = "LFELKEEA.exe"
PET_MANIFEST_KEY = "pet"
PET_STATE_FILE = "state.json"
MAX_PET_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_PET_UNPACKED_BYTES = 900 * 1024 * 1024

ProgressCallback = Callable[[int, str], None]


class PetError(Exception):
    pass


@dataclass
class PetRelease:
    version: str
    name: str = PET_NAME
    download_url: str = ""
    sha256: str = ""
    size: int = 0
    released: str = ""
    notes: list[str] = field(default_factory=list)


def pet_root() -> Path:
    return Path(APP_DIR) / "pet"


def pet_app_dir(root: Path | None = None) -> Path:
    return (Path(root) if root is not None else pet_root()) / PET_ID


def pet_exe_path(root: Path | None = None) -> Path:
    return pet_app_dir(root) / PET_EXE


def pet_state_path(root: Path | None = None) -> Path:
    return (Path(root) if root is not None else pet_root()) / PET_STATE_FILE


def is_installed(root: Path | None = None) -> bool:
    return pet_exe_path(root).is_file()


def installed_info(root: Path | None = None) -> dict:
    try:
        data = json.loads(pet_state_path(root).read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def installed_version(root: Path | None = None) -> str:
    return str(installed_info(root).get("version") or "")


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


def parse_pet_release(
    manifest: dict, manifest_url: str = DEFAULT_UPDATE_URL
) -> PetRelease | None:
    data = manifest.get(PET_MANIFEST_KEY) if isinstance(manifest, dict) else None
    if not isinstance(data, dict):
        return None
    version = str(data.get("version") or "").strip().lstrip("vV")
    if not version:
        return None
    download_url = str(data.get("download_url") or data.get("url") or "").strip()
    if download_url:
        download_url = urljoin(manifest_url, download_url)
    digest = str(data.get("sha256") or "").strip().lower()
    try:
        size = int(data.get("size") or 0)
    except Exception:
        size = 0
    return PetRelease(
        version=version,
        name=str(data.get("name") or PET_NAME),
        download_url=download_url,
        sha256=digest,
        size=max(0, size),
        released=str(data.get("released") or ""),
        notes=_normalize_notes(data.get("notes")),
    )


def fetch_pet_release(url: str = DEFAULT_UPDATE_URL) -> PetRelease | None:
    manifest_url = str(url or "").strip() or DEFAULT_UPDATE_URL
    return parse_pet_release(fetch_manifest(manifest_url), manifest_url)


def archive_path(release: PetRelease, root: Path | None = None) -> Path:
    folder = (Path(root) if root is not None else pet_root()) / "downloads"
    return folder / f"LFELKEEA-{release.version}.zip"


def _emit(progress: ProgressCallback | None, percent: int, message: str) -> None:
    if progress is not None:
        progress(max(0, min(100, int(percent))), str(message))


def download_pet(
    release: PetRelease,
    root: Path | None = None,
    *,
    progress_cb: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
    threads: int = 4,
) -> Path:
    if not release.download_url:
        raise PetError("更新清单里没有桌宠下载地址")
    if not release.sha256:
        raise PetError("更新清单里没有桌宠 SHA-256，已停止下载以保护安全")
    cancel_event = cancel_event or threading.Event()
    dest = archive_path(release, root)
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.exists():
        try:
            if sha256_file(dest, cancel_event).lower() == release.sha256.lower():
                _emit(progress_cb, 100, "桌宠安装包已下载")
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

    _emit(progress_cb, 1, f"正在下载桌宠 {release.version}…")
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
        raise PetError(f"桌宠下载失败：{exc}") from exc

    if dest.stat().st_size > MAX_PET_ARCHIVE_BYTES:
        try:
            dest.unlink()
        except OSError:
            pass
        raise PetError("桌宠安装包体积异常，已删除")
    digest = sha256_file(dest, cancel_event).lower()
    if digest != release.sha256.lower():
        try:
            dest.unlink()
        except OSError:
            pass
        raise PetError("桌宠安装包 SHA-256 校验失败，已删除损坏文件")
    _emit(progress_cb, 100, "桌宠安装包下载完成")
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
                if parts and parts[0].lower() == PET_ID.lower():
                    parts = parts[1:]
                if not parts:
                    continue
                relative = Path(*parts)
                if relative.is_absolute() or ".." in relative.parts:
                    raise PetError("桌宠安装包包含不安全的路径")
                destination = (staging / relative).resolve()
                if destination != root and root not in destination.parents:
                    raise PetError("桌宠安装包包含不安全的路径")
                total += max(0, int(info.file_size))
                if total > MAX_PET_UNPACKED_BYTES:
                    raise PetError("桌宠安装包解压后体积异常，已停止")
                jobs.append(
                    ExtractJob(
                        info=info,
                        destination=destination,
                        label=relative.name,
                        check_exists=False,
                    )
                )
    except PetError:
        raise
    except DownloadCancelled:
        raise
    except zipfile.BadZipFile as exc:
        raise PetError("桌宠安装包不是有效的 ZIP 文件") from exc
    except Exception as exc:
        raise PetError(f"读取桌宠安装包失败：{exc}") from exc
    if not jobs:
        raise PetError("桌宠安装包是空的")
    return jobs, total


def _exe_locked(exe: Path) -> bool:
    """目标目录里的桌宠正在运行时，Windows 会锁住 exe，无法替换。"""
    if not exe.is_file():
        return False
    try:
        with exe.open("r+b"):
            pass
    except OSError:
        return True
    return False


def install_pet(
    archive: Path,
    version: str,
    root: Path | None = None,
    *,
    progress_cb: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    archive = Path(archive)
    if not archive.is_file():
        raise PetError("桌宠安装包不存在，请重新下载")
    base = Path(root) if root is not None else pet_root()
    target = base / PET_ID
    staging = base / f".staging-{PET_ID}"
    backup = base / f".backup-{PET_ID}"
    if target.exists() and _exe_locked(target / PET_EXE):
        raise PetError("桌宠正在运行，请先关闭桌宠再更新")
    base.mkdir(parents=True, exist_ok=True)
    for leftover in (staging, backup):
        if leftover.exists():
            shutil.rmtree(leftover, ignore_errors=True)

    jobs, total = _plan_jobs(archive, staging, cancel_event)

    def on_extract(done_bytes: int, _done_files: int, label: str) -> None:
        if total:
            percent = max(1, min(99, int(done_bytes * 100 / total)))
        else:
            percent = 50
        _emit(progress_cb, percent, f"正在解压 {label}…")

    _emit(progress_cb, 1, "正在安装桌宠…")
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
        raise PetError(f"解压桌宠失败：{exc}") from exc

    if not (staging / PET_EXE).is_file():
        shutil.rmtree(staging, ignore_errors=True)
        raise PetError("桌宠安装包里没有找到 LFELKEEA.exe")

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
            raise PetError(f"无法替换旧版桌宠，可能仍有文件被占用：{exc}") from exc

    try:
        staging.rename(target)
    except Exception as exc:
        if moved_old and backup.exists() and not target.exists():
            try:
                backup.rename(target)
            except Exception:
                pass
        shutil.rmtree(staging, ignore_errors=True)
        raise PetError(f"安装桌宠失败：{exc}") from exc

    if moved_old:
        shutil.rmtree(backup, ignore_errors=True)

    state = {
        "version": str(version or ""),
        "installed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "archive": archive.name,
        "size": archive.stat().st_size,
    }
    try:
        (base / PET_STATE_FILE).write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass
    _emit(progress_cb, 100, "桌宠安装完成")
    return target


def launch_pet(root: Path | None = None) -> subprocess.Popen:
    exe = pet_exe_path(root)
    if not exe.is_file():
        raise PetError("桌宠还没有安装")
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
        raise PetError(f"无法启动桌宠：{exc}") from exc


def open_pet_folder(root: Path | None = None) -> None:
    folder = pet_app_dir(root)
    if not folder.is_dir():
        raise PetError("桌宠还没有安装")
    if os.name == "nt":
        os.startfile(str(folder))
        return
    subprocess.Popen(["xdg-open", str(folder)])


def is_newer(remote: str, current: str) -> bool:
    def parse(value: str) -> tuple[int, ...]:
        parts: list[int] = []
        for item in str(value or "").strip().lstrip("vV").split("."):
            digits = "".join(ch for ch in item if ch.isdigit())
            parts.append(int(digits) if digits else 0)
        while len(parts) < 3:
            parts.append(0)
        return tuple(parts)

    return parse(remote) > parse(current)
