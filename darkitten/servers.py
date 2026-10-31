from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal

from .config import APP_DIR
from .installer import (
    BMCL_MAVEN,
    CENTRAL_MAVEN,
    InstallCancelled,
    _http_open,
    fetch_json,
    get_manifest,
)
from .loaders import (
    FABRIC_META,
    FABRIC_META_BMCL,
    _check_cancel,
    _download,
    _installer_urls,
    _patch_installer,
    _preseed_libraries,
)
from .versions import maven_path

SERVER_NAMES = {
    "paper": "Paper",
    "spigot": "Spigot",
    "fabric": "Fabric",
    "forge": "Forge",
    "neoforge": "NeoForge",
}
PLUGIN_SERVERS = {"paper", "spigot"}
SERVER_BADGE = {
    "paper": "blue",
    "spigot": "orange",
    "fabric": "purple",
    "forge": "orange",
    "neoforge": "purple",
}

SERVERS_DIR = APP_DIR / "servers"
SERVER_CACHE = APP_DIR / "cache" / "servers"
BUILDTOOLS_URL = (
    "https://hub.spigotmc.org/jenkins/job/BuildTools/"
    "lastSuccessfulBuild/artifact/target/BuildTools.jar"
)
PAPER_API = "https://fill.papermc.io/v3/projects/paper"
EULA_URL = "https://aka.ms/MinecraftEULA"
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

ProgressCallback = Callable[[int, str], None]
LogCallback = Callable[[str], None]
ProcessCallback = Callable[[subprocess.Popen | None], None]

DEFAULT_PROPERTIES = {
    "server-port": "25565",
    "online-mode": "false",
    "motd": "Darkitten Server",
    "max-players": "20",
    "view-distance": "10",
    "simulation-distance": "10",
    "enable-command-block": "true",
    "allow-flight": "true",
    "spawn-protection": "0",
    "difficulty": "easy",
    "gamemode": "survival",
    "level-name": "world",
    "pvp": "true",
}


class ServerError(Exception):
    pass


# ---------------------------------------------------------------------------
# 数据模型与目录


@dataclass
class ServerInfo:
    folder: str
    name: str
    kind: str
    mc_version: str
    loader_version: str = ""
    java_path: str = ""
    memory_mb: int = 4096
    port: int = 25565
    online_mode: bool = False
    eula: bool = False
    installed: bool = False
    install_error: str = ""
    launch_kind: str = ""
    launch_target: str = ""
    launch_classpath: list[str] = field(default_factory=list)
    paper_build: int = 0
    created_at: str = ""
    installed_at: str = ""
    last_started: str = ""

    @property
    def type_name(self) -> str:
        return SERVER_NAMES.get(self.kind, self.kind or "未知")

    @property
    def is_plugin_server(self) -> bool:
        return self.kind in PLUGIN_SERVERS

    @property
    def addon_dir_name(self) -> str:
        return "plugins" if self.is_plugin_server else "mods"

    @property
    def addon_label(self) -> str:
        return "插件" if self.is_plugin_server else "Mod"

    @property
    def path(self) -> Path:
        return SERVERS_DIR / self.folder

    @property
    def display_version(self) -> str:
        if self.kind == "paper" and self.paper_build:
            return f"{self.mc_version} · #{self.paper_build}"
        if self.loader_version:
            return f"{self.mc_version} · {self.loader_version}"
        return self.mc_version


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _safe_folder(name: str) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", str(name or "")).strip()
    text = text.rstrip(". ")
    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
    if not text or text.upper() in reserved:
        text = "server"
    return text[:48]


def new_server_folder(name: str, kind: str, mc_version: str) -> str:
    SERVERS_DIR.mkdir(parents=True, exist_ok=True)
    base = _safe_folder(name) or _safe_folder(f"{kind}-{mc_version}")
    folder = base
    index = 2
    while (SERVERS_DIR / folder).exists():
        folder = f"{base}-{index}"
        index += 1
    return folder


def server_json_path(info: ServerInfo) -> Path:
    return info.path / "server.json"


def save_server(info: ServerInfo) -> None:
    path = server_json_path(info)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(info)
    data["kind"] = info.kind
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def load_server(folder: str) -> ServerInfo | None:
    folder = str(folder or "")
    if not folder or Path(folder).name != folder:
        return None
    path = SERVERS_DIR / folder / "server.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    data["folder"] = folder
    known = set(ServerInfo.__dataclass_fields__)
    clean = {k: v for k, v in data.items() if k in known}
    try:
        info = ServerInfo(**clean)
    except Exception:
        return None
    if info.kind not in SERVER_NAMES:
        return None
    try:
        info.memory_mb = max(512, min(65536, int(info.memory_mb or 4096)))
    except Exception:
        info.memory_mb = 4096
    try:
        info.port = max(1, min(65535, int(info.port or 25565)))
    except Exception:
        info.port = 25565
    if not isinstance(info.launch_classpath, list):
        info.launch_classpath = []
    info.launch_classpath = [str(item) for item in info.launch_classpath if item]
    info.eula = bool(info.eula) or eula_path(info).exists()
    return info


def scan_servers() -> list[ServerInfo]:
    if not SERVERS_DIR.exists():
        return []
    result: list[ServerInfo] = []
    try:
        folders = [p for p in SERVERS_DIR.iterdir() if p.is_dir()]
    except OSError:
        return []
    for folder in folders:
        info = load_server(folder.name)
        if info is not None:
            result.append(info)
    result.sort(key=lambda item: (item.installed, item.created_at), reverse=True)
    return result


def eula_path(info: ServerInfo) -> Path:
    return info.path / "eula.txt"


def write_eula(info: ServerInfo) -> None:
    info.path.mkdir(parents=True, exist_ok=True)
    eula_path(info).write_text("eula=true\n", encoding="utf-8")
    info.eula = True


def addons_dir(info: ServerInfo) -> Path:
    return info.path / info.addon_dir_name


def _properties_escape(value: str) -> str:
    text = str(value or "")
    text = text.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r")
    text = text.replace("=", "\\=").replace(":", "\\:")
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if code < 32 or code > 126:
            if code <= 0xFFFF:
                out.append(f"\\u{code:04x}")
            else:
                out.append("?")
        else:
            out.append(ch)
    return "".join(out)


def update_server_properties(
    info: ServerInfo, updates: dict[str, Any] | None = None
) -> None:
    path = info.path / "server.properties"
    info.path.mkdir(parents=True, exist_ok=True)
    values = {k: str(v) for k, v in DEFAULT_PROPERTIES.items()}
    values.update(
        {
            "server-port": str(info.port),
            "online-mode": "true" if info.online_mode else "false",
        }
    )
    for key, value in (updates or {}).items():
        values[str(key)] = str(value)

    lines: list[str] = []
    if path.exists():
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = []
    existing: dict[str, int] = {}
    for index, line in enumerate(lines):
        raw = line.strip()
        if not raw or raw.startswith("#") or raw.startswith("!") or "=" not in raw:
            continue
        key = raw.split("=", 1)[0].strip()
        existing[key] = index
    for key, value in values.items():
        encoded = f"{key}={_properties_escape(value)}"
        if key in existing:
            lines[existing[key]] = encoded
        else:
            lines.append(encoded)
    if not lines:
        lines = ["#Minecraft server properties"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def check_port(port: int, host: str = "") -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, int(port)))
        return True
    except OSError:
        return False
    finally:
        try:
            sock.close()
        except OSError:
            pass


def required_java_major(mc_version: str) -> int:
    pieces = [int(x) for x in re.findall(r"\d+", str(mc_version))[:3]]
    if len(pieces) < 2:
        return 21
    major, minor = pieces[0], pieces[1]
    patch = pieces[2] if len(pieces) > 2 else 0
    if major > 1 or (major == 1 and minor >= 21):
        return 21
    if (major, minor, patch) >= (1, 20, 5):
        return 21
    if (major, minor) >= (1, 18):
        return 17
    if (major, minor) == (1, 17):
        return 16
    return 8


def send_to_recycle_bin(path: Path) -> tuple[bool, str]:
    """优先移动到 Windows 回收站，失败时移入启动器内 .trash 目录。"""
    target = Path(path)
    if not target.exists():
        return False, "文件夹不存在"
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class _SHFileOpStructW(ctypes.Structure):
                _fields_ = [
                    ("hwnd", wintypes.HWND),
                    ("wFunc", wintypes.UINT),
                    ("pFrom", ctypes.c_wchar_p),
                    ("pTo", ctypes.c_wchar_p),
                    ("fFlags", wintypes.USHORT),
                    ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p),
                    ("lpszProgressTitle", ctypes.c_wchar_p),
                ]

            buffer = ctypes.create_unicode_buffer(str(target.resolve()) + "\0")
            op = _SHFileOpStructW()
            op.hwnd = None
            op.wFunc = 3  # FO_DELETE
            op.pFrom = ctypes.cast(buffer, ctypes.c_wchar_p)
            op.pTo = None
            op.fFlags = 0x0040 | 0x0010 | 0x0004 | 0x0400  # ALLOWUNDO | NOCONFIRM | SILENT | NOERRORUI
            op.fAnyOperationsAborted = False
            op.hNameMappings = None
            op.lpszProgressTitle = None
            shell = ctypes.windll.shell32
            shell.SHFileOperationW.argtypes = [ctypes.POINTER(_SHFileOpStructW)]
            shell.SHFileOperationW.restype = ctypes.c_int
            code = shell.SHFileOperationW(ctypes.byref(op))
            if code == 0 and not op.fAnyOperationsAborted:
                return True, "已移动到回收站"
        except Exception:
            pass
    try:
        trash = APP_DIR / ".trash" / "servers"
        trash.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        destination = trash / f"{stamp}-{target.name}"
        index = 2
        while destination.exists():
            destination = trash / f"{stamp}-{target.name}-{index}"
            index += 1
        shutil.move(str(target), str(destination))
        return True, f"已移入启动器回收目录：{destination}"
    except Exception as exc:
        return False, str(exc)


# ---------------------------------------------------------------------------
# 基础工具


def _get_json(url: str, timeout: int = 45) -> Any:
    with _http_open(
        url,
        timeout=timeout,
        headers={"Accept": "application/json"},
    ) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fp:
        while True:
            chunk = fp.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _find_git() -> str:
    found = shutil.which("git")
    if found:
        return found
    candidates = [
        Path("C:/Program Files/Git/cmd/git.exe"),
        Path("C:/Program Files/Git/bin/git.exe"),
        Path("C:/Program Files (x86)/Git/cmd/git.exe"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Git/cmd/git.exe",
    ]
    for item in candidates:
        try:
            if item.exists():
                return str(item)
        except OSError:
            continue
    raise ServerError("没有找到 Git。Spigot 的 BuildTools 需要 Git，请先安装 Git 后重试。")


def _run_process(
    cmd: list[str],
    cwd: Path,
    *,
    log_cb: LogCallback,
    cancel_event: threading.Event | None,
    process_cb: ProcessCallback | None,
    env: dict[str, str] | None = None,
) -> int:
    log_cb("运行：" + subprocess.list2cmdline(cmd))
    startupinfo = None
    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=CREATE_NO_WINDOW,
            startupinfo=startupinfo,
            env=env,
        )
    except Exception as exc:
        raise ServerError(f"无法启动安装进程：{exc}") from exc

    if process_cb:
        process_cb(proc)
    cancelled = False
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                try:
                    proc.terminate()
                except Exception:
                    pass
                break
            text = line.rstrip("\r\n")
            if text:
                log_cb(text)
        try:
            code = proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            try:
                proc.terminate()
            except Exception:
                pass
            code = proc.wait(timeout=10)
    finally:
        if process_cb:
            process_cb(None)

    if cancelled or (cancel_event is not None and cancel_event.is_set()):
        raise InstallCancelled()
    return int(code)


# ---------------------------------------------------------------------------
# 安装服务端


def _vanilla_server_jar(
    info: ServerInfo,
    *,
    source: str,
    progress_cb: ProgressCallback,
    log_cb: LogCallback,
    cancel_event: threading.Event | None,
) -> Path:
    dest = info.path / "server.jar"
    progress_cb(60, "获取 Minecraft 服务端文件…")
    manifest = get_manifest(source, log=log_cb)
    entry = next(
        (
            item
            for item in manifest.get("versions") or []
            if isinstance(item, dict) and item.get("id") == info.mc_version
        ),
        None,
    )
    if not entry or not entry.get("url"):
        raise ServerError(f"版本清单里没有 Minecraft {info.mc_version}")

    version_data: dict[str, Any] | None = None
    errors: list[str] = []
    for version_url in (
        str(entry.get("url") or ""),
        f"https://bmclapi2.bangbang93.com/version/{urllib.parse.quote(info.mc_version)}/json",
    ):
        if not version_url:
            continue
        try:
            data = fetch_json(version_url)
            if isinstance(data.get("downloads"), dict):
                version_data = data
                break
        except Exception as exc:
            errors.append(str(exc))
    if version_data is None:
        raise ServerError(f"无法读取 Minecraft {info.mc_version} 的版本信息：{errors[-1] if errors else '未知错误'}")

    server = (version_data.get("downloads") or {}).get("server") or {}
    if not server.get("url"):
        raise ServerError(f"Minecraft {info.mc_version} 没有可用的服务端下载地址")
    urls = [
        f"https://bmclapi2.bangbang93.com/version/{urllib.parse.quote(info.mc_version)}/server",
        str(server["url"]),
    ]
    log_cb(f"下载 Minecraft {info.mc_version} 服务端")
    _download(
        urls,
        dest,
        sha1=str(server.get("sha1") or ""),
        size=int(server.get("size") or 0),
        label="Minecraft 服务端",
        cancel_event=cancel_event,
    )
    if not zipfile.is_zipfile(dest):
        raise ServerError("下载到的 Minecraft 服务端不是有效的 jar 文件")
    return dest


def _install_paper(
    info: ServerInfo,
    *,
    progress_cb: ProgressCallback,
    log_cb: LogCallback,
    cancel_event: threading.Event | None,
) -> None:
    progress_cb(3, f"查询 Paper {info.mc_version} 构建…")
    mc = urllib.parse.quote(info.mc_version, safe="")
    url = f"{PAPER_API}/versions/{mc}/builds"
    try:
        data = _get_json(url, timeout=60)
    except Exception as exc:
        raise ServerError(f"无法获取 Paper 构建列表：{exc}") from exc
    builds = [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []
    if not builds:
        raise ServerError(
            f"Paper 没有 Minecraft {info.mc_version} 的构建，"
            "请换一个版本，或改用 Spigot。"
        )
    stable = [item for item in builds if str(item.get("channel")).upper() == "STABLE"]
    candidates = stable or builds
    try:
        build = max(candidates, key=lambda item: int(item.get("id") or 0))
    except Exception:
        build = candidates[0]
    downloads = build.get("downloads") or {}
    download = downloads.get("server:default")
    if not isinstance(download, dict):
        download = next(
            (
                item
                for item in downloads.values()
                if isinstance(item, dict) and item.get("url")
            ),
            None,
        )
    if not isinstance(download, dict) or not download.get("url"):
        raise ServerError("Paper 构建里没有服务端下载地址")

    build_id = int(build.get("id") or 0)
    filename = str(download.get("name") or f"paper-{info.mc_version}-{build_id}.jar")
    size = int(download.get("size") or 0)
    sha256 = str((download.get("checksums") or {}).get("sha256") or "")
    log_cb(f"Paper 构建 #{build_id} · {filename} · {size / 1024 / 1024:.1f} MB")
    progress_cb(8, f"下载 Paper #{build_id}…")

    dest = info.path / "server.jar"
    last_error = ""
    for attempt in range(2):
        _download(
            [str(download["url"])],
            dest,
            size=size,
            label=f"Paper #{build_id}",
            cancel_event=cancel_event,
        )
        if not sha256 or _sha256_file(dest).lower() == sha256.lower():
            break
        last_error = "SHA256 校验失败"
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass
    else:
        raise ServerError(f"Paper 服务端下载失败：{last_error}")
    if not zipfile.is_zipfile(dest):
        raise ServerError("Paper 服务端不是有效的 jar 文件")

    info.paper_build = build_id
    info.launch_kind = "jar"
    info.launch_target = "server.jar"
    progress_cb(92, "Paper 服务端已就绪")
    _preseed_paper_cache(
        info,
        dest,
        progress_cb=progress_cb,
        log_cb=log_cb,
        cancel_event=cancel_event,
    )


def _paper_download_contexts(jar_path: Path) -> list[tuple[str, str, str]]:
    try:
        with zipfile.ZipFile(jar_path) as jar:
            try:
                raw = jar.read("META-INF/download-context").decode("utf-8", "replace")
            except KeyError:
                return []
    except (OSError, zipfile.BadZipFile):
        return []
    contexts: list[tuple[str, str, str]] = []
    for line in raw.splitlines():
        parts = line.strip().split("\t")
        if len(parts) != 3:
            continue
        digest, url, filename = (part.strip() for part in parts)
        if digest and url and filename and "/" not in filename and "\\" not in filename:
            contexts.append((digest.lower(), url, filename))
    return contexts


def _preseed_paper_cache(
    info: ServerInfo,
    jar_path: Path,
    *,
    progress_cb: ProgressCallback,
    log_cb: LogCallback,
    cancel_event: threading.Event | None = None,
) -> None:
    """预先下载 Paperclip 需要的 Mojang 服务端缓存，避免首次启动卡在官方源。"""
    contexts = _paper_download_contexts(jar_path)
    if not contexts:
        return
    cache_dir = info.path / "cache"
    for index, (digest, url, filename) in enumerate(contexts):
        cache_file = cache_dir / filename
        if cache_file.exists() and _sha256_file(cache_file).lower() == digest:
            continue
        if cache_file.exists():
            try:
                cache_file.unlink()
            except OSError:
                pass
        candidates = []
        if filename.startswith("mojang_"):
            candidates.append(
                "https://bmclapi2.bangbang93.com/version/"
                + urllib.parse.quote(info.mc_version, safe="")
                + "/server"
            )
        candidates.append(url)
        progress_cb(
            min(96, 94 + index),
            f"预下载 Paper 启动缓存 {filename}…",
        )
        log_cb(f"预下载 Paper 启动缓存：{filename}")
        seeded = False
        for candidate in dict.fromkeys(candidates):
            _check_cancel(cancel_event)
            try:
                _download(
                    [candidate],
                    cache_file,
                    label=filename,
                    cancel_event=cancel_event,
                )
            except InstallCancelled:
                raise
            except Exception:
                continue
            if cache_file.exists() and _sha256_file(cache_file).lower() == digest:
                seeded = True
                break
            try:
                cache_file.unlink()
            except OSError:
                pass
        if seeded:
            log_cb(f"Paper 启动缓存已准备：{filename}")
        else:
            log_cb(
                "无法预下载 Paper 启动缓存，首次启动时 Paperclip 会自行下载；"
                "如果官方源不可用，启动可能失败。"
            )
    progress_cb(97, "Paper 启动缓存已准备")


def _install_spigot(
    info: ServerInfo,
    *,
    java_path: str,
    progress_cb: ProgressCallback,
    log_cb: LogCallback,
    cancel_event: threading.Event | None,
    process_cb: ProcessCallback | None,
) -> None:
    git = _find_git()
    build_dir = SERVER_CACHE / "spigot" / _safe_folder(info.mc_version)
    build_dir.mkdir(parents=True, exist_ok=True)
    buildtools = build_dir / "BuildTools.jar"

    progress_cb(4, "下载 Spigot BuildTools…")
    _download(
        [BUILDTOOLS_URL],
        buildtools,
        label="Spigot BuildTools",
        cancel_event=cancel_event,
    )

    def find_jar() -> Path | None:
        candidates = []
        for item in build_dir.glob("spigot-*.jar"):
            name = item.name.lower()
            if any(
                part in name
                for part in ("-sources", "-javadoc", "-original", "-deprecated")
            ):
                continue
            candidates.append(item)
        if not candidates:
            return None
        return max(candidates, key=lambda item: item.stat().st_mtime)

    existing = find_jar()
    if existing:
        log_cb(f"复用缓存中的 {existing.name}")
    else:
        log_cb("Spigot 首次构建需要 Git 和较长时间（通常 10-30 分钟），请耐心等待。")
        env = os.environ.copy()
        env["PATH"] = str(Path(git).parent) + os.pathsep + env.get("PATH", "")
        progress_cb(10, "BuildTools 正在编译 Spigot（耗时较长）…")
        network_error = False

        def build_log(message: str) -> None:
            nonlocal network_error
            if any(
                token in message
                for token in (
                    "ConnectException",
                    "UnknownHostException",
                    "Connection timed out",
                    "Network is unreachable",
                )
            ):
                network_error = True
            log_cb(message)

        code = _run_process(
            [java_path, "-jar", "BuildTools.jar", "--rev", info.mc_version, "--compile", "spigot"],
            build_dir,
            log_cb=build_log,
            cancel_event=cancel_event,
            process_cb=process_cb,
            env=env,
        )
        if code != 0:
            if network_error:
                raise ServerError(
                    "Spigot BuildTools 无法访问 hub.spigotmc.org（Java 网络受限）。"
                    "请检查网络或代理后重试；也可以改用 Paper 服务端，它兼容 Spigot 插件。"
                )
            raise ServerError(f"Spigot BuildTools 退出代码 {code}，请查看日志")
        existing = find_jar()
    if not existing:
        raise ServerError("BuildTools 已结束，但没有找到生成的 Spigot jar")

    progress_cb(90, "复制 Spigot 服务端…")
    dest = info.path / "server.jar"
    shutil.copy2(existing, dest)
    info.launch_kind = "jar"
    info.launch_target = "server.jar"
    log_cb(f"Spigot 服务端：{existing.name}")


def _install_fabric(
    info: ServerInfo,
    *,
    source: str,
    progress_cb: ProgressCallback,
    log_cb: LogCallback,
    cancel_event: threading.Event | None,
) -> None:
    progress_cb(3, "读取 Fabric 服务端配置…")
    meta: dict[str, Any] | None = None
    errors: list[str] = []
    for url in (
        f"{FABRIC_META_BMCL}/versions/loader/{info.mc_version}/{info.loader_version}/server/json",
        f"{FABRIC_META}/versions/loader/{info.mc_version}/{info.loader_version}/server/json",
    ):
        try:
            data = _get_json(url, timeout=45)
            if isinstance(data, dict) and data.get("libraries"):
                meta = data
                break
        except Exception as exc:
            errors.append(str(exc))
    if meta is None:
        raise ServerError(
            "无法获取 Fabric 服务端配置："
            + (errors[-1] if errors else "Fabric 返回内容为空")
        )

    libraries = [lib for lib in meta.get("libraries") or [] if isinstance(lib, dict)]
    if not libraries:
        raise ServerError("Fabric 服务端配置缺少依赖库")
    log_cb(f"Fabric 服务端依赖 {len(libraries)} 个")

    classpath: list[str] = []
    total = len(libraries)
    for index, lib in enumerate(libraries, start=1):
        _check_cancel(cancel_event)
        path = maven_path(str(lib.get("name") or ""))
        if not path:
            continue
        dest = info.path / "libraries" / Path(path)
        base = str(lib.get("url") or "https://maven.fabricmc.net/")
        if not base.endswith("/"):
            base += "/"
        encoded = urllib.parse.quote(path, safe="/")
        urls = [
            BMCL_MAVEN + encoded,
            BMCL_MAVEN + path,
            CENTRAL_MAVEN + path,
            base + path,
        ]
        _download(
            urls,
            dest,
            sha1=str(lib.get("sha1") or ""),
            size=int(lib.get("size") or 0),
            label=str(lib.get("name") or path),
            cancel_event=cancel_event,
        )
        classpath.append(str(Path("libraries") / Path(path)))
        progress_cb(
            6 + int(45 * index / max(1, total)),
            f"下载 Fabric 依赖 {index}/{total}",
        )

    _vanilla_server_jar(
        info,
        source=source,
        progress_cb=progress_cb,
        log_cb=log_cb,
        cancel_event=cancel_event,
    )
    classpath.append("server.jar")

    info.launch_kind = "classpath"
    info.launch_target = str(
        meta.get("mainClass")
        or "net.fabricmc.loader.impl.launch.knot.KnotServer"
    )
    info.launch_classpath = classpath
    progress_cb(94, "Fabric 服务端已就绪")


def _read_install_profile(jar_path: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(jar_path) as jar:
            try:
                raw = jar.read("install_profile.json")
            except KeyError:
                return {}
    except (OSError, zipfile.BadZipFile):
        return {}
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _preseed_forge_server_jar(
    info: ServerInfo,
    installer: Path,
    *,
    source: str,
    progress_cb: ProgressCallback,
    log_cb: LogCallback,
    cancel_event: threading.Event | None,
) -> None:
    """Forge/NeoForge 安装器会从 Mojang 官网下载原版服务端，这里提前放到它期望的路径。"""
    profile = _read_install_profile(installer)
    template = str(profile.get("serverJarPath") or "")
    if not template:
        return
    relative = (
        template.replace("{LIBRARY_DIR}", "libraries")
        .replace("{MINECRAFT_VERSION}", info.mc_version)
        .replace("\\", "/")
        .lstrip("/")
    )
    target = (info.path / relative).resolve()
    try:
        target.relative_to(info.path.resolve())
    except ValueError:
        return
    if target.exists() and zipfile.is_zipfile(target):
        log_cb(f"原版服务端已存在：{target.name}")
        return
    target.parent.mkdir(parents=True, exist_ok=True)

    server: dict[str, Any] = {}
    try:
        manifest = get_manifest(source, log=log_cb)
        entry = next(
            (
                item
                for item in manifest.get("versions") or []
                if isinstance(item, dict) and item.get("id") == info.mc_version
            ),
            None,
        )
        if entry and entry.get("url"):
            data = fetch_json(str(entry["url"]))
            server = (data.get("downloads") or {}).get("server") or {}
    except Exception as exc:
        log_cb(f"读取 Mojang 服务端信息失败：{exc}")
    if not server.get("url"):
        try:
            data = _get_json(
                "https://bmclapi2.bangbang93.com/version/"
                + urllib.parse.quote(info.mc_version, safe="")
                + "/json",
                timeout=30,
            )
            if isinstance(data, dict):
                server = (data.get("downloads") or {}).get("server") or {}
        except Exception as exc:
            log_cb(f"读取 BMCL 服务端信息失败：{exc}")

    urls = [
        "https://bmclapi2.bangbang93.com/version/"
        + urllib.parse.quote(info.mc_version, safe="")
        + "/server"
    ]
    if server.get("url"):
        urls.append(str(server["url"]))
    progress_cb(44, f"预下载 {SERVER_NAMES[info.kind]} 需要的原版服务端…")
    log_cb(f"预下载 Minecraft {info.mc_version} 原版服务端：{relative}")
    _download(
        urls,
        target,
        sha1=str(server.get("sha1") or ""),
        size=int(server.get("size") or 0),
        label="Minecraft 服务端",
        cancel_event=cancel_event,
    )
    if not zipfile.is_zipfile(target):
        try:
            target.unlink()
        except OSError:
            pass
        raise ServerError("预下载的原版服务端不是有效的 jar 文件")


_PROGUARD_CLASS = re.compile(r"^(\S+) -> (\S+):$")
_PROGUARD_FIELD = re.compile(r"^\s+(\S+) ([^\s(]+) -> (\S+)$")
_PROGUARD_METHOD = re.compile(
    r"^\s+(?:\d+:\d+:)?(\S+) ([^\s(]+)\((.*)\) -> (\S+)$"
)
_JAVA_PRIMITIVES = {
    "void": "V",
    "boolean": "Z",
    "byte": "B",
    "char": "C",
    "short": "S",
    "int": "I",
    "long": "J",
    "float": "F",
    "double": "D",
}


def _proguard_type(value: str) -> str:
    dims = ""
    while value.endswith("[]"):
        dims += "["
        value = value[:-2]
    if value in _JAVA_PRIMITIVES:
        return dims + _JAVA_PRIMITIVES[value]
    return dims + "L" + value.replace(".", "/") + ";"


def _proguard_descriptor(return_type: str, args: str) -> str:
    parts: list[str] = []
    depth = 0
    current = ""
    for char in args:
        if char == "<":
            depth += 1
        elif char == ">":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += char
    if current.strip():
        parts.append(current)
    params = "".join(_proguard_type(part.strip()) for part in parts if part.strip())
    return f"({params}){_proguard_type(return_type)}"


def _java_string_hash(value: str) -> int:
    result = 0
    for char in value:
        result = (31 * result + ord(char)) & 0xFFFFFFFF
    return result


def _hashmap_iteration_order(keys: list[str]) -> dict[str, int]:
    """复刻 Java HashMap 的桶遍历顺序，srgutils 输出同名字方法时依赖这个顺序。"""
    unique = list(dict.fromkeys(keys))
    capacity = 16
    while len(unique) > capacity * 0.75:
        capacity *= 2
    buckets: dict[int, list[str]] = {}
    for key in unique:
        h = _java_string_hash(key)
        index = ((h ^ (h >> 16)) & 0xFFFFFFFF) & (capacity - 1)
        buckets.setdefault(index, []).append(key)
    order: dict[str, int] = {}
    for index in range(capacity):
        for key in buckets.get(index, []):
            order[key] = len(order)
    return order


def _proguard_to_tsrg(raw: bytes) -> bytes:
    """把 Mojang ProGuard 映射转换成 srgutils 生成的 TSRG 文本。"""
    classes: list[tuple[str, str, list[tuple[int, str, str, str]]]] = []
    current: list[tuple[str, str, list[tuple[int, str, str, str]]]] | None = None
    for line in raw.decode("utf-8", "replace").splitlines():
        if not line or line.startswith("#"):
            continue
        if not line[0].isspace():
            match = _PROGUARD_CLASS.match(line)
            if match:
                current = (match.group(1), match.group(2), [])
                classes.append(current)
            continue
        if current is None:
            continue
        match = _PROGUARD_METHOD.match(line)
        if match:
            return_type, name, args, mapped = match.groups()
            current[2].append(
                (1, name, mapped, _proguard_descriptor(return_type, args))
            )
            continue
        match = _PROGUARD_FIELD.match(line)
        if match:
            field_type, name, mapped = match.groups()
            current[2].append((0, name, mapped, _proguard_type(field_type)))

    classes.sort(key=lambda item: item[0])
    lines: list[str] = []
    for original, mapped, members in classes:
        lines.append(original.replace(".", "/") + " " + mapped.replace(".", "/"))
        fields = sorted((item for item in members if item[0] == 0), key=lambda x: x[1])
        methods = [item for item in members if item[0] == 1]
        order = _hashmap_iteration_order([item[1] + item[3] for item in methods])
        methods.sort(key=lambda item: (item[1], order.get(item[1] + item[3], 0)))
        for _, name, mapped_name, _ in fields:
            lines.append(f"\t{name} {mapped_name}")
        for _, name, mapped_name, descriptor in methods:
            lines.append(f"\t{name} {descriptor} {mapped_name}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _preseed_forge_mappings(
    info: ServerInfo,
    installer: Path,
    *,
    source: str,
    progress_cb: ProgressCallback,
    log_cb: LogCallback,
    cancel_event: threading.Event | None,
) -> None:
    """提前生成 Forge/NeoForge 安装器需要的 Mojang 映射，避开它写死的官网地址。"""
    profile = _read_install_profile(installer)
    data = profile.get("data") or {}
    coordinate = data.get("MOJMAPS")
    expected = data.get("MOJMAPS_SHA")
    if isinstance(coordinate, dict):
        coordinate = coordinate.get("server")
    if isinstance(expected, dict):
        expected = expected.get("server")
    coordinate = str(coordinate or "").strip().strip("[]")
    expected = str(expected or "").strip().strip("'\"")
    if not coordinate or not expected:
        return
    name = coordinate.split("@", 1)[0]
    parts = name.split(":")
    if len(parts) < 3:
        return
    classifier = parts[3] if len(parts) > 3 else ""
    filename = f"{parts[1]}-{parts[2]}"
    if classifier:
        filename += f"-{classifier}"
    filename += ".tsrg"
    relative = "/".join(parts[0].split(".") + [parts[1], parts[2], filename])
    target = (info.path / "libraries" / relative).resolve()
    try:
        target.relative_to(info.path.resolve())
    except ValueError:
        return
    if target.exists() and _sha1_file_bytes(target.read_bytes()) == expected.lower():
        return

    server_mappings: dict[str, Any] = {}
    try:
        manifest = get_manifest(source, log=log_cb)
        entry = next(
            (
                item
                for item in manifest.get("versions") or []
                if isinstance(item, dict) and item.get("id") == info.mc_version
            ),
            None,
        )
        if entry and entry.get("url"):
            version_data = fetch_json(str(entry["url"]))
            server_mappings = (version_data.get("downloads") or {}).get(
                "server_mappings"
            ) or {}
    except Exception as exc:
        log_cb(f"读取 Mojang 映射信息失败：{exc}")
    if not server_mappings.get("url"):
        log_cb("没有找到 Mojang 映射下载地址，安装器会尝试自行下载")
        return

    cache_file = SERVER_CACHE / "mappings" / f"server-{info.mc_version}.txt"
    progress_cb(45, "准备 Mojang 映射文件…")
    log_cb(f"预生成 Mojang 映射：{filename}")
    _download(
        [str(server_mappings["url"])],
        cache_file,
        sha1=str(server_mappings.get("sha1") or ""),
        size=int(server_mappings.get("size") or 0),
        label="Mojang 映射",
        cancel_event=cancel_event,
    )
    try:
        converted = _proguard_to_tsrg(cache_file.read_bytes())
    except Exception as exc:
        log_cb(f"转换 Mojang 映射失败：{exc}")
        return
    actual = _sha1_file_bytes(converted)
    if actual != expected.lower():
        log_cb(
            f"Mojang 映射校验不一致（期望 {expected[:8]}，实际 {actual[:8]}），"
            "仍写入文件供安装器尝试"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(converted)


def _sha1_file_bytes(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def _patch_class_string(data: bytes, old: bytes, new: bytes) -> bytes:
    """替换 class 常量池里的 UTF-8 字符串，允许新字符串长度不同。"""
    if data[:4] != b"\xca\xfe\xba\xbe":
        raise ValueError("不是有效的 class 文件")
    count = int.from_bytes(data[8:10], "big")
    pos = 10
    output = bytearray(data[:10])
    patched = 0
    index = 1
    while index < count:
        tag = data[pos]
        start = pos
        pos += 1
        if tag == 1:
            length = int.from_bytes(data[pos : pos + 2], "big")
            pos += 2
            raw = data[pos : pos + length]
            pos += length
            if raw == old:
                raw = new
                patched += 1
            output.append(tag)
            output += len(raw).to_bytes(2, "big")
            output += raw
        elif tag in (7, 8, 16, 19, 20):
            output.append(tag)
            output += data[pos : pos + 2]
            pos += 2
        elif tag == 15:
            output.append(tag)
            output += data[pos : pos + 3]
            pos += 3
        elif tag in (3, 4, 9, 10, 11, 12, 17, 18):
            output.append(tag)
            output += data[pos : pos + 4]
            pos += 4
        elif tag in (5, 6):
            output.append(tag)
            output += data[pos : pos + 8]
            pos += 8
            index += 1
        else:
            raise ValueError(f"未知常量池标签：{tag}（偏移 {start}）")
        index += 1
    output += data[pos:]
    if not patched:
        raise ValueError("class 里没有找到目标字符串")
    return bytes(output)


def _class_utf8_strings(data: bytes) -> list[bytes]:
    if data[:4] != b"\xca\xfe\xba\xbe":
        raise ValueError("不是有效的 class 文件")
    count = int.from_bytes(data[8:10], "big")
    pos = 10
    index = 1
    strings: list[bytes] = []
    while index < count:
        tag = data[pos]
        pos += 1
        if tag == 1:
            length = int.from_bytes(data[pos : pos + 2], "big")
            pos += 2
            strings.append(data[pos : pos + length])
            pos += length
        elif tag in (7, 8, 16, 19, 20):
            pos += 2
        elif tag == 15:
            pos += 3
        elif tag in (3, 4, 9, 10, 11, 12, 17, 18):
            pos += 4
        elif tag in (5, 6):
            pos += 8
            index += 1
        else:
            raise ValueError(f"未知常量池标签：{tag}")
        index += 1
    return strings


def _patch_jar_manifest_url(
    jar_path: Path,
    class_suffix: str,
    new_url: bytes,
) -> bool:
    """把 class 里任意指向版本清单的 URL 常量替换成 new_url。"""
    changed = False
    temp = jar_path.with_name(jar_path.name + ".patchtmp")
    with zipfile.ZipFile(jar_path) as src, zipfile.ZipFile(
        temp, "w", zipfile.ZIP_DEFLATED
    ) as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename.endswith(class_suffix):
                try:
                    strings = _class_utf8_strings(data)
                except ValueError:
                    strings = []
                old = next(
                    (
                        value
                        for value in strings
                        if b"version_manifest" in value
                        and value.startswith((b"http://", b"https://", b"file:"))
                    ),
                    None,
                )
                if old is not None and old != new_url:
                    data = _patch_class_string(data, old, new_url)
                    changed = True
            dst.writestr(item, data)
    if changed:
        os.replace(temp, jar_path)
    else:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
    return changed


def _rewrite_installer_profile(jar_path: Path, profile: dict[str, Any]) -> None:
    temp = jar_path.with_name(jar_path.name + ".profiletmp")
    with zipfile.ZipFile(jar_path) as src, zipfile.ZipFile(
        temp, "w", zipfile.ZIP_DEFLATED
    ) as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "install_profile.json":
                data = json.dumps(profile, ensure_ascii=False).encode("utf-8")
            dst.writestr(item, data)
    os.replace(temp, jar_path)


def _prepare_neoforge_local_manifest(
    info: ServerInfo,
    *,
    source: str,
    log_cb: LogCallback,
    cancel_event: threading.Event | None,
) -> Path | None:
    """生成本地版本清单与版本 JSON，让 NeoForge 映射下载器完全离线读取。"""
    version_data: dict[str, Any] = {}
    try:
        manifest = get_manifest(source, log=log_cb)
        entry = next(
            (
                item
                for item in manifest.get("versions") or []
                if isinstance(item, dict) and item.get("id") == info.mc_version
            ),
            None,
        )
        if entry and entry.get("url"):
            version_data = fetch_json(str(entry["url"]))
    except Exception as exc:
        log_cb(f"读取 Mojang 版本信息失败：{exc}")
        return None

    mappings = (version_data.get("downloads") or {}).get("server_mappings") or {}
    if not mappings.get("url"):
        log_cb("没有找到 Mojang 服务端映射地址，安装器会尝试自行下载")
        return None

    raw_file = SERVER_CACHE / "mappings" / f"server-{info.mc_version}.txt"
    try:
        _download(
            [str(mappings["url"])],
            raw_file,
            sha1=str(mappings.get("sha1") or ""),
            size=int(mappings.get("size") or 0),
            label="Mojang 服务端映射",
            cancel_event=cancel_event,
        )
    except Exception as exc:
        log_cb(f"下载 Mojang 服务端映射失败：{exc}")
        return None

    folder = SERVER_CACHE / "neoforge-local" / _safe_folder(info.mc_version)
    folder.mkdir(parents=True, exist_ok=True)
    version_file = folder / "version.json"
    manifest_file = folder / "version_manifest_v2.json"
    raw_bytes = raw_file.read_bytes()
    version_payload = {
        "id": info.mc_version,
        "downloads": {
            "server_mappings": {
                "sha1": _sha1_file_bytes(raw_bytes),
                "size": len(raw_bytes),
                "url": raw_file.resolve().as_uri(),
            }
        },
    }
    version_file.write_text(
        json.dumps(version_payload, ensure_ascii=False), encoding="utf-8"
    )
    manifest_payload = {
        "latest": {"release": info.mc_version, "snapshot": info.mc_version},
        "versions": [
            {
                "id": info.mc_version,
                "type": "release",
                "url": version_file.resolve().as_uri(),
            }
        ],
    }
    manifest_file.write_text(
        json.dumps(manifest_payload, ensure_ascii=False), encoding="utf-8"
    )
    log_cb(f"已生成离线映射清单：{manifest_file.name}")
    return manifest_file


def _patch_neoforge_mojmaps(
    info: ServerInfo,
    installer: Path,
    manifest_file: Path,
    *,
    log_cb: LogCallback,
) -> None:
    """把 NeoForge 映射下载器的清单地址改成刚生成的本地 file: 地址。"""
    profile = _read_install_profile(installer)
    libraries = [item for item in profile.get("libraries") or [] if isinstance(item, dict)]
    target_lib = None
    for item in libraries:
        if str(item.get("name") or "").startswith(
            "net.neoforged.installertools:installertools:"
        ):
            target_lib = item
            break
    if target_lib is None:
        log_cb("没有找到 NeoForge installertools 依赖")
        return
    artifact = (target_lib.get("downloads") or {}).get("artifact") or {}
    relative = str(artifact.get("path") or "")
    if not relative:
        return
    jar_path = info.path / "libraries" / relative
    if not jar_path.exists():
        log_cb("NeoForge installertools 尚未下载，跳过映射下载器补丁")
        return
    new_url = manifest_file.resolve().as_uri().encode("utf-8")
    try:
        changed = _patch_jar_manifest_url(
            jar_path,
            "net/neoforged/installertools/DownloadMojmaps.class",
            new_url,
        )
    except Exception as exc:
        log_cb(f"修补 NeoForge 映射下载器失败：{exc}")
        return
    actual_sha = _sha1_file_bytes(jar_path.read_bytes())
    if not changed and str(artifact.get("sha1") or "").lower() == actual_sha:
        log_cb("NeoForge 映射下载器已使用本地清单")
        return
    artifact["sha1"] = actual_sha
    artifact["size"] = jar_path.stat().st_size
    _rewrite_installer_profile(installer, profile)
    log_cb("已将 NeoForge 映射下载器切换到本地清单")


def _find_forge_args(info: ServerInfo) -> Path | None:
    files = list(info.path.glob("libraries/**/win_args.txt"))
    if sys.platform != "win32":
        files.extend(info.path.glob("libraries/**/unix_args.txt"))
    if not files:
        return None
    keywords = ("forge", "neoforge")
    preferred = [
        item
        for item in files
        if any(word in str(item).lower() for word in keywords)
    ]
    pool = preferred or files
    return max(pool, key=lambda item: item.stat().st_mtime)


def _find_forge_jar(info: ServerInfo) -> Path | None:
    prefixes = ("forge-", "neoforge-")
    candidates = []
    for item in info.path.glob("*.jar"):
        name = item.name.lower()
        if not name.startswith(prefixes):
            continue
        if any(part in name for part in ("installer", "sources", "javadoc", "shim")):
            continue
        candidates.append(item)
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_mtime)


def _install_forge_family(
    info: ServerInfo,
    *,
    java_path: str,
    source: str = "official",
    progress_cb: ProgressCallback,
    log_cb: LogCallback,
    cancel_event: threading.Event | None,
    process_cb: ProcessCallback | None,
) -> None:
    kind = info.kind
    name = SERVER_NAMES[kind]
    SERVER_CACHE.mkdir(parents=True, exist_ok=True)
    urls, filename = _installer_urls(kind, info.mc_version, info.loader_version)
    raw = SERVER_CACHE / f"{kind}-{info.mc_version}-{info.loader_version}-installer.jar"
    patched = SERVER_CACHE / f"{kind}-{info.mc_version}-{info.loader_version}-bmcl.jar"

    progress_cb(3, f"下载 {name} 安装器…")
    log_cb(f"下载 {name} 安装器 {info.loader_version}")
    _download(
        urls,
        raw,
        label=f"{name} 安装器",
        cancel_event=cancel_event,
    )
    _patch_installer(raw, patched, kind)
    log_cb(f"已将 {name} 安装器切换到 BMCLAPI 镜像")

    def preseed_progress(percent: int, message: str) -> None:
        progress_cb(8 + int(max(0, min(100, percent)) * 0.35), message)

    _preseed_libraries(
        info.path,
        patched,
        progress_cb=preseed_progress,
        log_cb=log_cb,
        cancel_event=cancel_event,
    )
    if kind == "neoforge":
        local_manifest = _prepare_neoforge_local_manifest(
            info,
            source=source,
            log_cb=log_cb,
            cancel_event=cancel_event,
        )
        if local_manifest is not None:
            _patch_neoforge_mojmaps(
                info, patched, local_manifest, log_cb=log_cb
            )
    _preseed_forge_server_jar(
        info,
        patched,
        source=source,
        progress_cb=progress_cb,
        log_cb=log_cb,
        cancel_event=cancel_event,
    )
    _preseed_forge_mappings(
        info,
        patched,
        source=source,
        progress_cb=progress_cb,
        log_cb=log_cb,
        cancel_event=cancel_event,
    )

    progress_cb(48, f"运行 {name} 服务端安装器…")
    code = _run_process(
        [
            java_path,
            "-jar",
            str(patched),
            "--installServer",
            str(info.path),
        ],
        info.path,
        log_cb=log_cb,
        cancel_event=cancel_event,
        process_cb=process_cb,
    )
    if code != 0:
        raise ServerError(f"{name} 安装器退出代码 {code}，请查看日志")

    progress_cb(90, "检查服务端启动文件…")
    args_file = _find_forge_args(info)
    if args_file is not None:
        info.launch_kind = "args"
        info.launch_target = str(args_file.relative_to(info.path))
        log_cb(f"服务端启动参数：{info.launch_target}")
    else:
        jar = _find_forge_jar(info)
        if jar is None:
            raise ServerError(
                f"{name} 安装器已结束，但没有找到 win_args.txt 或服务端 jar，"
                "请查看日志了解安装器失败原因。"
            )
        info.launch_kind = "jar"
        info.launch_target = jar.name
        log_cb(f"服务端启动文件：{jar.name}")


def install_server(
    info: ServerInfo,
    *,
    java_path: str = "",
    accept_eula: bool = False,
    source: str = "official",
    progress_cb: ProgressCallback | None = None,
    log_cb: LogCallback | None = None,
    cancel_event: threading.Event | None = None,
    process_cb: ProcessCallback | None = None,
) -> None:
    progress = progress_cb or (lambda p, m: None)
    log = log_cb or (lambda m: None)
    info.kind = info.kind if info.kind in SERVER_NAMES else "paper"
    info.installed = False
    info.install_error = ""
    info.path.mkdir(parents=True, exist_ok=True)
    save_server(info)
    update_server_properties(info)
    if accept_eula:
        write_eula(info)

    try:
        if info.kind == "paper":
            _install_paper(
                info,
                progress_cb=progress,
                log_cb=log,
                cancel_event=cancel_event,
            )
        elif info.kind == "spigot":
            if not java_path:
                raise ServerError("编译 Spigot 需要 Java")
            _install_spigot(
                info,
                java_path=java_path,
                progress_cb=progress,
                log_cb=log,
                cancel_event=cancel_event,
                process_cb=process_cb,
            )
        elif info.kind == "fabric":
            _install_fabric(
                info,
                source=source,
                progress_cb=progress,
                log_cb=log,
                cancel_event=cancel_event,
            )
        else:
            if not java_path:
                raise ServerError(f"安装 {SERVER_NAMES[info.kind]} 需要 Java")
            _install_forge_family(
                info,
                java_path=java_path,
                source=source,
                progress_cb=progress,
                log_cb=log,
                cancel_event=cancel_event,
                process_cb=process_cb,
            )
    except InstallCancelled:
        info.install_error = "安装已取消"
        save_server(info)
        raise
    except Exception as exc:
        info.install_error = str(exc)
        save_server(info)
        raise

    info.installed = True
    info.install_error = ""
    info.installed_at = _now()
    save_server(info)
    update_server_properties(info)
    if accept_eula:
        write_eula(info)
    progress(100, "安装完成")
    log(f"{info.type_name} 服务端安装完成")


# ---------------------------------------------------------------------------
# 目录 / 插件文件


@dataclass
class AddonFile:
    path: Path
    name: str
    filename: str
    size: int
    enabled: bool

    @property
    def size_text(self) -> str:
        if self.size >= 1024 * 1024:
            return f"{self.size / 1024 / 1024:.1f} MB"
        if self.size >= 1024:
            return f"{self.size / 1024:.0f} KB"
        return f"{self.size} B"


def list_addons(info: ServerInfo) -> list[AddonFile]:
    directory = addons_dir(info)
    if not directory.exists():
        return []
    result: list[AddonFile] = []
    try:
        files = [p for p in directory.iterdir() if p.is_file()]
    except OSError:
        return []
    for path in files:
        name = path.name
        lower = name.lower()
        if lower.endswith(".jar"):
            enabled = True
            display = name[:-4]
        elif lower.endswith(".jar.disabled"):
            enabled = False
            display = name[: -len(".jar.disabled")]
        else:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        result.append(
            AddonFile(
                path=path,
                name=display,
                filename=name,
                size=size,
                enabled=enabled,
            )
        )
    result.sort(key=lambda item: item.name.lower())
    return result


def toggle_addon(addon: AddonFile) -> Path:
    if addon.enabled:
        dest = addon.path.with_name(addon.path.name + ".disabled")
    else:
        if addon.path.name.lower().endswith(".jar.disabled"):
            dest = addon.path.with_name(addon.path.name[: -len(".disabled")])
        else:
            dest = addon.path.with_suffix(".jar")
    if dest.exists():
        raise ServerError(f"目标文件已存在：{dest.name}")
    os.replace(addon.path, dest)
    return dest


# ---------------------------------------------------------------------------
# 启动


def build_server_command(info: ServerInfo, java_path: str = "") -> list[str]:
    java = str(java_path or info.java_path or "").strip()
    if not java:
        raise ServerError("没有可用的 Java，请先在设置中选择 Java。")
    info.path.mkdir(parents=True, exist_ok=True)
    memory = max(512, min(65536, int(info.memory_mb or 4096)))
    initial = min(memory, 1024)
    command = [
        java,
        f"-Xms{initial}M",
        f"-Xmx{memory}M",
        "-Dfile.encoding=UTF-8",
        "-Dterminal.jline=false",
    ]
    if info.launch_kind == "args":
        target = info.launch_target
        if not target or not (info.path / target).exists():
            raise ServerError("服务端启动参数文件缺失，请重新安装服务端。")
        command.append("@" + target)
        try:
            text = (info.path / target).read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        if "nogui" not in text.lower():
            command.append("nogui")
    elif info.launch_kind == "classpath":
        if not info.launch_classpath or not info.launch_target:
            raise ServerError("Fabric 启动信息不完整，请重新安装服务端。")
        command.extend(
            [
                "-cp",
                os.pathsep.join(info.launch_classpath),
                info.launch_target,
                "nogui",
            ]
        )
    else:
        target = info.launch_target or "server.jar"
        if not (info.path / target).exists():
            raise ServerError(f"服务端文件不存在：{target}")
        command.extend(["-jar", target, "nogui"])
    return command


_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


class ServerProcess(QObject):
    """仅管理本启动器创建的服务端进程。"""

    output = Signal(str)
    started = Signal(int)
    ready = Signal()
    stopped = Signal(int)
    state_changed = Signal(str)

    def __init__(
        self,
        info: ServerInfo,
        java_path: str = "",
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.info = info
        self.java_path = java_path
        self.process: subprocess.Popen | None = None
        self.state = "stopped"
        self.buffer: list[str] = []
        self._thread: threading.Thread | None = None
        self._ready = False
        self._stopping = False
        self._force_timer: threading.Timer | None = None

    def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            raise ServerError("服务端已经在运行")
        if not eula_path(self.info).exists():
            raise ServerError("还没有同意 Minecraft EULA")
        command = build_server_command(self.info, self.java_path)
        startupinfo = None
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
        env = os.environ.copy()
        env.pop("JAVA_TOOL_OPTIONS", None)
        try:
            self.process = subprocess.Popen(
                command,
                cwd=str(self.info.path),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=CREATE_NO_WINDOW,
                startupinfo=startupinfo,
                env=env,
            )
        except Exception as exc:
            self.process = None
            raise ServerError(f"无法启动服务端：{exc}") from exc
        self._ready = False
        self._stopping = False
        self.state = "starting"
        self.buffer.clear()
        self.state_changed.emit(self.state)
        self.started.emit(int(self.process.pid))
        self._thread = threading.Thread(target=self._read_output, daemon=True)
        self._thread.start()

    def _read_output(self) -> None:
        proc = self.process
        if proc is None or proc.stdout is None:
            return
        try:
            for raw in proc.stdout:
                line = _ANSI_RE.sub("", raw.rstrip("\r\n"))
                if not line:
                    continue
                self.buffer.append(line)
                if len(self.buffer) > 3000:
                    del self.buffer[:500]
                self.output.emit(line)
                if not self._ready and (
                    "Done (" in line or 'For help, type "help"' in line
                ):
                    self._ready = True
                    self.state = "running"
                    self.state_changed.emit(self.state)
                    self.ready.emit()
        except Exception:
            pass
        try:
            code = proc.wait(timeout=10)
        except Exception:
            code = -1
        self.state = "stopped"
        self.process = None
        self._cancel_force_timer()
        self.state_changed.emit(self.state)
        self.stopped.emit(int(code))

    @property
    def is_running(self) -> bool:
        proc = self.process
        return proc is not None and proc.poll() is None

    def send(self, command: str) -> None:
        text = str(command or "").rstrip("\r\n")
        if not text:
            return
        proc = self.process
        if proc is None or proc.poll() is not None or proc.stdin is None:
            raise ServerError("服务端未在运行")
        try:
            proc.stdin.write(text + "\n")
            proc.stdin.flush()
        except Exception as exc:
            raise ServerError(f"发送命令失败：{exc}") from exc

    def stop(self, wait_seconds: int = 20) -> None:
        proc = self.process
        if proc is None or proc.poll() is not None:
            return
        self._stopping = True
        self.state = "stopping"
        self.state_changed.emit(self.state)
        try:
            self.send("stop")
        except ServerError:
            pass
        self._cancel_force_timer()
        timer = threading.Timer(max(3, wait_seconds), self._force_stop)
        timer.daemon = True
        self._force_timer = timer
        timer.start()

    def force_stop(self) -> None:
        self._force_stop()

    def _force_stop(self) -> None:
        proc = self.process
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
        except Exception:
            pass
        time.sleep(3)
        proc = self.process
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass

    def _cancel_force_timer(self) -> None:
        timer = self._force_timer
        self._force_timer = None
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass

    def wait(self, timeout: float = 10) -> int | None:
        proc = self.process
        if proc is None:
            return 0
        try:
            return proc.wait(timeout=timeout)
        except Exception:
            return None
