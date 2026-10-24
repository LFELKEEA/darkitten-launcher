from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import urllib.parse
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from .config import APP_VERSION, DEFAULT_UPDATE_URL
from .downloader import (
    DownloadCancelled,
    download_to_file,
    http_open,
)

MAX_UPDATE_BYTES = 200 * 1024 * 1024
CODE_PATHS = (
    "app.py",
    "darkitten",
    "assets",
    "requirements.txt",
    "launch.bat",
    "启动.bat",
)
ProgressCallback = Callable[[int, str], None]

_APPLY_SCRIPT = r'''from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path


def _log(path: Path, text: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fp:
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            fp.write(f"[{stamp}] {text}\n")
    except Exception:
        pass


def _wait_for_process(pid: int, timeout: float = 180.0) -> None:
    if pid <= 0:
        return
    if os.name == "nt":
        kernel32 = ctypes.windll.kernel32
        synchronize = 0x00100000
        handle = kernel32.OpenProcess(synchronize, False, int(pid))
        if handle:
            try:
                kernel32.WaitForSingleObject(handle, int(timeout * 1000))
            finally:
                kernel32.CloseHandle(handle)
        return
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.4)


def main() -> int:
    if len(sys.argv) < 6:
        return 2
    payload = Path(sys.argv[1]).resolve()
    install_dir = Path(sys.argv[2]).resolve()
    pid = int(sys.argv[3])
    restart = sys.argv[4] == "1"
    python_exe = sys.argv[5]
    log_path = install_dir / "updates" / "apply.log"
    _log(log_path, f"等待启动器退出：pid={pid}")
    _wait_for_process(pid)
    _log(log_path, f"开始应用更新：{payload}")
    try:
        sys.path.insert(0, str(payload))
        from darkitten.updater import apply_update_code

        backup = apply_update_code(payload, install_dir)
        _log(log_path, f"更新完成，原代码备份：{backup}")
        try:
            shutil.rmtree(payload, ignore_errors=True)
        except Exception:
            pass
        if restart:
            flags = 0
            if os.name == "nt":
                flags = (
                    getattr(subprocess, "DETACHED_PROCESS", 0)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                )
            subprocess.Popen(
                [python_exe, str(install_dir / "app.py")],
                cwd=str(install_dir),
                creationflags=flags,
                close_fds=True,
            )
            _log(log_path, "启动器已重新启动")
        return 0
    except Exception:
        _log(log_path, traceback.format_exc())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
'''


class UpdateError(Exception):
    pass


@dataclass
class ReleaseInfo:
    version: str
    notes: list[str]
    download_url: str
    page_url: str = ""
    sha256: str = ""
    size: int = 0
    released: str = ""
    mandatory: bool = False
    manifest_url: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def size_text(self) -> str:
        return format_size(self.size)


def format_size(size: int) -> str:
    size = max(0, int(size or 0))
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size} B"


def parse_version(value: str) -> tuple[int, ...]:
    text = str(value or "").strip().lstrip("vV")
    match = re.match(r"(\d+(?:\.\d+)*)", text)
    if not match:
        raise UpdateError(f"无法识别版本号：{value!r}")
    parts = [int(item) for item in match.group(1).split(".")]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_newer(remote: str, current: str) -> bool:
    return parse_version(remote) > parse_version(current)


def _url_join(base: str, value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    return urllib.parse.urljoin(base, value)


def _normalize_notes(value) -> list[str]:
    if isinstance(value, str):
        raw = value.splitlines()
    elif isinstance(value, (list, tuple)):
        raw = [str(item) for item in value]
    else:
        return []
    notes: list[str] = []
    for line in raw:
        text = str(line).strip().lstrip("-*•").strip()
        if text:
            notes.append(text)
    return notes[:60]


def _release_from_manifest(data: dict, manifest_url: str) -> ReleaseInfo:
    if not isinstance(data, dict):
        raise UpdateError("更新清单格式不正确")
    payload = data.get("latest") if isinstance(data.get("latest"), dict) else data
    version = str(payload.get("version") or data.get("latest_version") or "").strip()
    if not version:
        raise UpdateError("更新清单缺少版本号")
    download = _url_join(
        manifest_url,
        payload.get("download_url")
        or payload.get("url")
        or data.get("download_url")
        or "",
    )
    page = _url_join(
        manifest_url,
        payload.get("page_url")
        or payload.get("release_url")
        or data.get("page_url")
        or "",
    )
    digest = str(payload.get("sha256") or data.get("sha256") or "").strip().lower()
    if digest and not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise UpdateError("更新清单里的 SHA-256 格式不正确")
    try:
        size = int(payload.get("size") or data.get("size") or 0)
    except Exception:
        size = 0
    return ReleaseInfo(
        version=version.lstrip("vV"),
        notes=_normalize_notes(payload.get("notes") or data.get("notes")),
        download_url=download,
        page_url=page,
        sha256=digest,
        size=max(0, size),
        released=str(payload.get("released") or data.get("released") or ""),
        mandatory=bool(payload.get("mandatory") or data.get("mandatory")),
        manifest_url=manifest_url,
        raw=payload if isinstance(payload, dict) else {},
    )


def fetch_manifest(url: str, timeout: int = 25) -> dict:
    url = str(url or "").strip() or DEFAULT_UPDATE_URL
    scheme = urllib.parse.urlparse(url).scheme.lower()
    if scheme not in ("http", "https", "file"):
        raise UpdateError("更新源地址必须以 http://、https:// 或 file:// 开头")
    try:
        with http_open(url, timeout=timeout) as resp:
            raw = resp.read(4 * 1024 * 1024 + 1)
    except Exception as exc:
        raise UpdateError(f"无法连接更新源：{exc}") from exc
    if len(raw) > 4 * 1024 * 1024:
        raise UpdateError("更新清单过大")
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except Exception as exc:
        raise UpdateError(f"更新清单不是有效的 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise UpdateError("更新清单格式不正确")
    return data


def check_for_update(
    current: str = APP_VERSION,
    url: str = DEFAULT_UPDATE_URL,
    timeout: int = 25,
) -> ReleaseInfo | None:
    manifest_url = str(url or "").strip() or DEFAULT_UPDATE_URL
    data = fetch_manifest(manifest_url, timeout=timeout)
    release = _release_from_manifest(data, manifest_url)
    if not is_newer(release.version, current):
        return None
    if not release.download_url:
        raise UpdateError("更新清单缺少下载地址")
    return release


def sha256_file(path: Path, cancel_event: threading.Event | None = None) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fp:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise DownloadCancelled()
            chunk = fp.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def download_update(
    release: ReleaseInfo,
    dest_dir: Path,
    *,
    progress_cb: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
    threads: int = 4,
) -> Path:
    if not release.download_url:
        raise UpdateError("更新清单缺少下载地址")
    if not release.sha256:
        raise UpdateError("更新清单缺少 SHA-256，已停止下载以保护安全")
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"DKL-{release.version}-update.zip"
    progress = progress_cb or (lambda _p, _m: None)
    cancel_event = cancel_event or threading.Event()

    if dest.exists():
        try:
            if sha256_file(dest, cancel_event).lower() == release.sha256:
                progress(100, "更新包已下载")
                return dest
        except DownloadCancelled:
            raise
        except Exception:
            pass
        try:
            dest.unlink()
        except OSError:
            pass

    progress(1, f"正在下载 DKL {release.version}…")

    def on_bytes(done: int, total: int) -> None:
        total = int(total or release.size or 0)
        if total:
            percent = max(1, min(99, int(done * 100 / total)))
            progress(
                percent,
                f"{done / 1024 / 1024:.1f}/{total / 1024 / 1024:.1f} MB",
            )
        else:
            progress(50, f"已下载 {done / 1024 / 1024:.1f} MB")

    try:
        download_to_file(
            release.download_url,
            dest,
            size=release.size,
            cancel_event=cancel_event,
            progress_cb=on_bytes,
            connections=max(2, min(6, int(threads))),
        )
        digest = sha256_file(dest, cancel_event)
    except DownloadCancelled:
        raise
    except Exception as exc:
        raise UpdateError(f"更新包下载失败：{exc}") from exc
    if digest.lower() != release.sha256.lower():
        try:
            dest.unlink()
        except OSError:
            pass
        raise UpdateError("更新包 SHA-256 校验失败，已删除损坏文件")
    progress(100, "更新包下载完成")
    return dest


def _safe_extract(archive: Path, target: Path) -> None:
    target = Path(target)
    try:
        with zipfile.ZipFile(archive) as zf:
            total = sum(int(info.file_size) for info in zf.infolist())
            if total > MAX_UPDATE_BYTES:
                raise UpdateError("更新包解压后体积异常，已停止")
            root = target.resolve()
            for info in zf.infolist():
                name = info.filename.replace("\\", "/")
                if not name or name.endswith("/"):
                    continue
                pure = Path(name)
                if pure.is_absolute() or ".." in pure.parts:
                    raise UpdateError("更新包包含不安全的路径")
                destination = (target / pure).resolve()
                if destination != root and root not in destination.parents:
                    raise UpdateError("更新包包含不安全的路径")
            zf.extractall(target)
    except UpdateError:
        raise
    except zipfile.BadZipFile as exc:
        raise UpdateError("更新包不是有效的 ZIP 文件") from exc
    except Exception as exc:
        raise UpdateError(f"解压更新包失败：{exc}") from exc


def _payload_root(staging: Path) -> Path:
    entries = [item for item in Path(staging).iterdir()]
    dirs = [item for item in entries if item.is_dir()]
    if len(dirs) == 1 and not any(item.is_file() for item in entries):
        return dirs[0]
    return Path(staging)


def read_version(root: Path) -> str:
    config_path = Path(root) / "darkitten" / "config.py"
    try:
        text = config_path.read_text(encoding="utf-8")
    except Exception:
        return ""
    match = re.search(r'APP_VERSION\s*=\s*["\']([^"\']+)["\']', text)
    return match.group(1) if match else ""


def prepare_update(
    archive: Path, dest_dir: Path, expected_version: str = ""
) -> Path:
    archive = Path(archive)
    dest_dir = Path(dest_dir)
    if not archive.is_file():
        raise UpdateError("更新包不存在")
    staging = dest_dir / f"staging-{expected_version or 'latest'}"
    _remove_path(staging)
    staging.mkdir(parents=True, exist_ok=True)
    _safe_extract(archive, staging)
    payload = _payload_root(staging)
    if not (payload / "app.py").is_file() or not (
        payload / "darkitten" / "config.py"
    ).is_file():
        raise UpdateError("更新包缺少启动器核心文件")
    version = read_version(payload)
    if not version:
        raise UpdateError("更新包里的版本号无法识别")
    if expected_version:
        try:
            if parse_version(version) != parse_version(expected_version):
                raise UpdateError(
                    f"更新包版本不匹配（期望 {expected_version}，实际 {version}）"
                )
        except UpdateError:
            raise
        except Exception as exc:
            raise UpdateError(f"更新包版本号无效：{exc}") from exc
    return payload


def _remove_path(path: Path) -> None:
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or path.is_file():
        try:
            path.unlink()
        except OSError:
            pass
        return
    try:
        shutil.rmtree(path)
    except OSError:
        pass


def _copy_tree(source: Path, destination: Path) -> None:
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )


def apply_update_code(payload_root: Path, install_dir: Path) -> Path:
    """只替换启动器代码，绝不改动游戏、Mod、账户、好友和设置文件。"""
    payload_root = Path(payload_root)
    install_dir = Path(install_dir)
    if not (payload_root / "app.py").is_file() or not (
        payload_root / "darkitten" / "config.py"
    ).is_file():
        raise UpdateError("更新包内容不完整")
    old_version = read_version(install_dir) or "unknown"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = install_dir / "updates" / f"backup-{old_version}-{stamp}"
    backup.mkdir(parents=True, exist_ok=True)

    for relative in CODE_PATHS:
        source = install_dir / relative
        if not source.exists():
            continue
        destination = backup / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            _copy_tree(source, destination)
        else:
            shutil.copy2(source, destination)

    temporary: dict[str, Path] = {}
    swapped: list[tuple[Path, Path, bool]] = []
    try:
        for relative in CODE_PATHS:
            source = payload_root / relative
            if not source.exists():
                continue
            destination = install_dir / relative
            temp = destination.with_name(destination.name + ".dkl-new")
            _remove_path(temp)
            if source.is_dir():
                _copy_tree(source, temp)
            else:
                shutil.copy2(source, temp)
            temporary[relative] = temp

        for relative, temp in temporary.items():
            destination = install_dir / relative
            old = destination.with_name(destination.name + ".dkl-old")
            _remove_path(old)
            had_old = destination.exists()
            if had_old:
                os.replace(destination, old)
            os.replace(temp, destination)
            swapped.append((destination, old, had_old))

        for _destination, old, _had_old in swapped:
            _remove_path(old)
    except Exception as exc:
        for relative in CODE_PATHS:
            destination = install_dir / relative
            old = destination.with_name(destination.name + ".dkl-old")
            temp = destination.with_name(destination.name + ".dkl-new")
            if old.exists():
                _remove_path(destination)
                try:
                    os.replace(old, destination)
                except Exception:
                    pass
            elif any(item[0] == destination and not item[2] for item in swapped):
                _remove_path(destination)
            _remove_path(temp)
        raise UpdateError(f"替换启动器文件失败：{exc}") from exc
    return backup


def restart_executable() -> str:
    executable = Path(sys.executable)
    if executable.name.lower() == "python.exe":
        candidate = executable.with_name("pythonw.exe")
        if candidate.exists():
            return str(candidate)
    return str(executable)


def write_apply_script(updates_dir: Path) -> Path:
    updates_dir = Path(updates_dir)
    updates_dir.mkdir(parents=True, exist_ok=True)
    script = updates_dir / "dkl_apply_update.py"
    script.write_text(_APPLY_SCRIPT, encoding="utf-8")
    return script


def schedule_apply(
    payload_root: Path,
    install_dir: Path,
    *,
    python_exe: str = "",
    restart: bool = True,
) -> Path:
    install_dir = Path(install_dir)
    updates_dir = install_dir / "updates"
    script = write_apply_script(updates_dir)
    log_path = updates_dir / "apply.log"
    python_exe = python_exe or restart_executable()
    flags = 0
    if os.name == "nt":
        flags = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    with log_path.open("a", encoding="utf-8") as log:
        subprocess.Popen(
            [
                python_exe,
                str(script),
                str(Path(payload_root).resolve()),
                str(install_dir.resolve()),
                str(os.getpid()),
                "1" if restart else "0",
                python_exe,
            ],
            cwd=str(install_dir),
            creationflags=flags,
            close_fds=True,
            stdout=log,
            stderr=log,
        )
    return script
