"""本机联动桥：把启动器已经拥有的版本、下载源和 Mod 能力暴露给工坊。

安全边界：
- 只监听 127.0.0.1，端口随机；
- 每次启动生成随机 token，写在 APP_DIR/bridge.json，只接受带 token 的请求；
- 不返回 API Key、账号令牌等敏感配置；
- 下载和反编译只写入启动器自己的目录，不执行任意命令。
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from . import decompiler, modrinth, sources
from .config import APP_DIR, APP_VERSION, DEFAULT_UPDATE_URL
from .downloader import download_to_file
from .updater import sha256_file

BRIDGE_FILE = APP_DIR / "bridge.json"
LEARN_DIR = APP_DIR / "learn"
USER_AGENT = f"DarkittenLauncher/{APP_VERSION} (bridge)"
SITE = "https://lfelkeea.github.io/darkitten-launcher/"
MAX_BODY_BYTES = 1024 * 1024
ALLOWED_EXTRA_ROOTS = (APP_DIR,)

# 这些域名可以直接换成 BMCLAPI，工坊拉原版文件时优先走镜像。
MIRROR_HOSTS = {
    "piston-data.mojang.com": "bmclapi2.bangbang93.com",
    "launchermeta.mojang.com": "bmclapi2.bangbang93.com",
    "launcher.mojang.com": "bmclapi2.bangbang93.com",
}
MIRROR_PREFIXES = {
    "libraries.minecraft.net": ("bmclapi2.bangbang93.com", "/maven"),
    "resources.download.minecraft.net": ("bmclapi2.bangbang93.com", "/assets"),
}


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _json_bytes(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


def mirror_url(url: str) -> str:
    """把 Mojang 官方地址换成 BMCLAPI 镜像，换不了就原样返回。"""
    try:
        parts = urllib.parse.urlsplit(str(url or ""))
    except ValueError:
        return str(url or "")
    host = parts.netloc.lower()
    if host in MIRROR_HOSTS:
        return urllib.parse.urlunsplit(
            (parts.scheme, MIRROR_HOSTS[host], parts.path, parts.query, parts.fragment)
        )
    if host in MIRROR_PREFIXES:
        mirror_host, prefix = MIRROR_PREFIXES[host]
        return urllib.parse.urlunsplit(
            (parts.scheme, mirror_host, prefix + parts.path, parts.query, parts.fragment)
        )
    return str(url or "")


def _http_json(url: str, timeout: float = 15.0) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


class BridgeSignals(QObject):
    activate = Signal()
    launch = Signal(str)


class BridgeServer:
    """HTTP 服务的生命周期管理，供 app.py 启动/停止。"""

    def __init__(self, config, port: int = 0, host: str = "127.0.0.1") -> None:
        self.config = config
        self.host = host
        self.port = int(port or 0)
        self.token = secrets.token_urlsafe(24)
        self.signals = BridgeSignals()
        self.started_at = time.time()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return bool(self._server is not None)

    @property
    def base_url(self) -> str:
        if self._server is None:
            return ""
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def start(self, port: int = 0) -> dict:
        if self.running:
            return self.descriptor()
        handler = _make_handler(self)
        last_error: Exception | None = None
        candidates = [int(port or self.port or 0)]
        if candidates[0]:
            candidates.append(0)
        else:
            candidates = [0]
        for candidate in candidates:
            try:
                server = ThreadingHTTPServer((self.host, candidate), handler)
                server.daemon_threads = True
                break
            except OSError as exc:
                last_error = exc
                server = None
        if server is None:
            raise RuntimeError(f"联动桥启动失败：{last_error}")
        self._server = server
        self.port = int(server.server_address[1])
        self._thread = threading.Thread(
            target=server.serve_forever,
            name="darkitten-bridge",
            daemon=True,
        )
        self._thread.start()
        self._write_descriptor()
        return self.descriptor()

    def stop(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            try:
                server.shutdown()
                server.server_close()
            except Exception:
                pass
        try:
            BRIDGE_FILE.unlink(missing_ok=True)
        except OSError:
            pass

    def descriptor(self) -> dict:
        return {
            "ok": True,
            "name": "Darkitten Launcher",
            "version": APP_VERSION,
            "host": self.host,
            "port": self._server.server_address[1] if self._server else 0,
            "url": self.base_url,
            "token": self.token,
            "pid": os.getpid(),
            "game_dir": str(self.config.game_path),
            "app_dir": str(APP_DIR),
            "updated": _now_text(),
        }

    def _write_descriptor(self) -> None:
        try:
            BRIDGE_FILE.write_text(
                json.dumps(self.descriptor(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    # ------------------------------------------------------------------
    # 业务实现（都在 HTTP 线程里执行，不碰 Qt 控件）
    # ------------------------------------------------------------------
    def game_dir(self) -> Path:
        return Path(self.config.game_path)

    def allowed_roots(self) -> list[Path]:
        return [self.game_dir(), LEARN_DIR, *ALLOWED_EXTRA_ROOTS]

    def versions(self) -> dict:
        from . import versions as versions_mod

        game_dir = self.game_dir()
        items = []
        selected = ""
        try:
            found = versions_mod.scan_versions(game_dir)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "game_dir": str(game_dir), "versions": []}
        for item in found:
            items.append(
                {
                    "id": item.id,
                    "type": item.type,
                    "loader": item.loader,
                    "type_name": item.type_name,
                    "loader_version": item.loader_version,
                    "release_time": item.release_time,
                    "java_major": item.java_major,
                    "parent_id": item.parent_id,
                    "base_id": item.base_id,
                    "is_loader_profile": item.is_loader_profile,
                    "json_path": item.json_path,
                    "error": item.error,
                }
            )
        return {
            "ok": True,
            "game_dir": str(game_dir),
            "versions": items,
            "selected": selected,
        }

    def downloads(self) -> dict:
        base = SITE.rstrip("/")
        result: dict[str, Any] = {
            "ok": True,
            "launcher": {
                "name": "Darkitten Launcher",
                "version": APP_VERSION,
                "update_url": DEFAULT_UPDATE_URL,
                "site": SITE,
                "source_repo": "https://github.com/LFELKEEA/darkitten-launcher",
            },
            "manifests": {
                "update": DEFAULT_UPDATE_URL,
                "mod_agent": f"{base}/mod_agent_release.json",
                "pet": f"{base}/pet_release.json",
                "plugins": f"{base}/plugins.json",
            },
            "minecraft_manifest": dict(sources.MANIFEST_URLS),
            "modrinth": "https://api.modrinth.com/v2",
            "sources": list(sources.SOURCES),
        }
        cached: dict[str, Any] = {}
        for name, url in result["manifests"].items():
            try:
                cached[name] = _http_json(url, timeout=8.0)
            except Exception as exc:  # noqa: BLE001 - 离线时只返回地址
                cached[name] = {"error": str(exc), "url": url}
        result["latest"] = cached
        return result

    def vanilla(self, version_id: str) -> dict:
        version_id = str(version_id or "").strip()
        if not version_id:
            raise ValueError("缺少版本 ID")
        local = self.game_dir() / "versions" / version_id / f"{version_id}.json"
        raw: dict[str, Any] | None = None
        source_url = ""
        if local.is_file():
            try:
                raw = json.loads(local.read_text(encoding="utf-8", errors="replace"))
            except (OSError, ValueError):
                raw = None
        if raw is None:
            manifest_url = ""
            for name in (sources.OFFICIAL, sources.BMCLAPI):
                try:
                    manifest = _http_json(sources.MANIFEST_URLS[name], timeout=12.0)
                except Exception:
                    continue
                for item in manifest.get("versions") or []:
                    if item.get("id") == version_id:
                        source_url = str(item.get("url") or "")
                        break
                if source_url:
                    break
            if not source_url:
                raise ValueError(f"找不到 Minecraft {version_id} 的版本清单")
            raw = _http_json(source_url, timeout=20.0)
        downloads = raw.get("downloads") or {}
        libraries = []
        for lib in raw.get("libraries") or []:
            if not isinstance(lib, dict):
                continue
            artifact = (lib.get("downloads") or {}).get("artifact") or {}
            if not artifact:
                continue
            libraries.append(
                {
                    "name": lib.get("name", ""),
                    "path": artifact.get("path", ""),
                    "url": artifact.get("url", ""),
                    "mirror": mirror_url(artifact.get("url", "")),
                    "sha1": artifact.get("sha1", ""),
                    "size": artifact.get("size", 0),
                }
            )
        client = downloads.get("client") or {}
        server = downloads.get("server") or {}
        return {
            "ok": True,
            "id": raw.get("id", version_id),
            "type": raw.get("type", ""),
            "release_time": raw.get("releaseTime", ""),
            "local": str(local) if local.is_file() else "",
            "source": source_url,
            "java_major": (raw.get("javaVersion") or {}).get("majorVersion", 0),
            "asset_index": raw.get("assetIndex") or {},
            "downloads": {
                "client": {
                    "url": client.get("url", ""),
                    "mirror": mirror_url(client.get("url", "")),
                    "sha1": client.get("sha1", ""),
                    "size": client.get("size", 0),
                },
                "server": {
                    "url": server.get("url", ""),
                    "mirror": mirror_url(server.get("url", "")),
                    "sha1": server.get("sha1", ""),
                    "size": server.get("size", 0),
                },
            },
            "libraries": libraries,
        }

    def search_mods(self, params: dict) -> dict:
        data = modrinth.search_mods(
            str(params.get("query") or params.get("q") or ""),
            loader=str(params.get("loader") or ""),
            game_version=str(params.get("mc") or params.get("game_version") or ""),
            limit=int(params.get("limit") or 20),
            offset=int(params.get("offset") or 0),
            project_type=str(params.get("project_type") or "mod"),
        )
        return {"ok": True, **data}

    def mod_versions(self, params: dict) -> dict:
        project = str(params.get("project") or params.get("project_id") or "").strip()
        if not project:
            raise ValueError("缺少 project")
        items = modrinth.fetch_mod_versions(
            project,
            loader=str(params.get("loader") or ""),
            game_version=str(params.get("mc") or params.get("game_version") or ""),
            content_type=str(params.get("project_type") or "mod"),
        )
        return {"ok": True, "project": project, "versions": items}

    def _pick_version(self, project: str, params: dict) -> dict:
        version_id = str(params.get("version") or params.get("version_id") or "").strip()
        items = modrinth.fetch_mod_versions(
            project,
            loader=str(params.get("loader") or ""),
            game_version=str(params.get("mc") or params.get("game_version") or ""),
            content_type=str(params.get("project_type") or "mod"),
        )
        if version_id:
            for item in items:
                if str(item.get("id") or "") == version_id:
                    return item
            raise ValueError(f"项目 {project} 没有版本 {version_id}")
        for item in items:
            if item.get("version_type") == "release":
                return item
        if not items:
            raise ValueError("这个项目没有匹配当前加载器/游戏版本的版本")
        return items[0]

    def download_mod(self, params: dict) -> dict:
        project = str(params.get("project") or params.get("project_id") or "").strip()
        if not project:
            raise ValueError("缺少 project")
        version = self._pick_version(project, params)
        instance = str(params.get("instance") or params.get("version_id") or "").strip()
        use_mods = str(params.get("target") or "learn").lower() == "mods"
        if use_mods:
            dest = Path(self.config.mods_path(instance))
        else:
            dest = LEARN_DIR / "mods"
        path = modrinth.download_mod_file(version, dest, threads=8)
        details = modrinth.fetch_project(project)
        return {
            "ok": True,
            "path": str(path),
            "size": path.stat().st_size if path.is_file() else 0,
            "sha256": sha256_file(path) if path.is_file() else "",
            "project": {
                "id": details.get("id", ""),
                "slug": details.get("slug", ""),
                "title": details.get("title", ""),
                "license": (details.get("license") or {}).get("id", ""),
                "icon_url": details.get("icon_url", ""),
            },
            "version": {
                "id": version.get("id", ""),
                "name": version.get("name", ""),
                "version_number": version.get("version_number", ""),
                "game_versions": version.get("game_versions") or [],
                "loaders": version.get("loaders") or [],
            },
        }

    def decompile_mod(self, params: dict) -> dict:
        downloaded = self.download_mod({**params, "target": "learn"})
        project = downloaded["project"]
        version = downloaded["version"]
        slug = decompiler.safe_name(project.get("slug") or project.get("id") or "mod")
        label = decompiler.safe_name(
            version.get("version_number") or version.get("id") or "version"
        )
        out_dir = LEARN_DIR / "src" / slug / label
        info = decompiler.decompile_jar(downloaded["path"], out_dir, config=self.config)
        entry = {
            "project": project.get("id", ""),
            "slug": project.get("slug", ""),
            "title": project.get("title", ""),
            "license": project.get("license", ""),
            "icon_url": project.get("icon_url", ""),
            "version": version.get("id", ""),
            "version_name": version.get("version_number", ""),
            "jar": downloaded["path"],
            "source_dir": info["source_dir"],
            "index": info["index"],
            "classes": info["classes"],
            "files": info["files"],
            "updated": _now_text(),
        }
        decompiler.write_learn_index(LEARN_DIR, entry)
        return {"ok": True, **entry, "nested": info.get("nested") or []}

    def learned(self) -> dict:
        path = LEARN_DIR / "index.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = []
        return {"ok": True, "items": data if isinstance(data, list) else []}

    def open_path(self, params: dict) -> dict:
        raw = str(params.get("path") or "").strip()
        if not raw:
            raise ValueError("缺少 path")
        target = Path(raw).expanduser().resolve()
        roots = [root.resolve() for root in self.allowed_roots() if root]
        if not any(target == root or root in target.parents for root in roots):
            raise PermissionError("只能打开启动器游戏目录或学习库里的文件")
        if not target.exists():
            raise FileNotFoundError(str(target))
        if os.name == "nt":
            os.startfile(str(target))  # noqa: S606 - 本地用户主动请求打开
        else:
            import subprocess

            subprocess.Popen(["xdg-open", str(target)])
        return {"ok": True, "path": str(target)}

    def launch(self, params: dict) -> dict:
        version_id = str(params.get("version") or params.get("version_id") or "").strip()
        if not version_id:
            raise ValueError("缺少 version")
        known = {item["id"] for item in self.versions().get("versions") or []}
        if version_id not in known:
            raise ValueError(f"启动器里没有版本 {version_id}")
        self.signals.launch.emit(version_id)
        return {"ok": True, "queued": True, "version": version_id}

    # ------------------------------------------------------------------
    def handle(self, method: str, path: str, query: dict, body: dict) -> tuple[int, dict]:
        if method == "GET" and path == "/health":
            return 200, {
                "ok": True,
                "name": "Darkitten Launcher",
                "version": APP_VERSION,
                "pid": os.getpid(),
                "uptime": int(time.time() - self.started_at),
            }
        if path == "/versions" and method == "GET":
            return 200, self.versions()
        if path == "/downloads" and method == "GET":
            return 200, self.downloads()
        if path.startswith("/vanilla/") and method == "GET":
            return 200, self.vanilla(path[len("/vanilla/") :])
        if path == "/mods/search" and method == "GET":
            return 200, self.search_mods(query)
        if path == "/mods/versions" and method == "GET":
            return 200, self.mod_versions(query)
        if path == "/mods/download" and method == "POST":
            return 200, self.download_mod(body)
        if path == "/mods/decompile" and method == "POST":
            return 200, self.decompile_mod(body)
        if path == "/learn" and method == "GET":
            return 200, self.learned()
        if path == "/open" and method == "POST":
            return 200, self.open_path(body)
        if path == "/launch" and method == "POST":
            return 200, self.launch(body)
        if path == "/activate" and method == "POST":
            self.signals.activate.emit()
            return 200, {"ok": True}
        return 404, {"ok": False, "error": f"未知接口：{method} {path}"}


def _make_handler(bridge: BridgeServer):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"DarkittenBridge/{APP_VERSION}"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
            return

        # --------------------------------------------------------------
        def _send(self, status: int, payload: dict) -> None:
            raw = _json_bytes(payload)
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _authorized(self) -> bool:
            if self.path.split("?", 1)[0] == "/health":
                return True
            token = self.headers.get("X-Darkitten-Token", "")
            if not token:
                auth = self.headers.get("Authorization", "")
                if auth.lower().startswith("bearer "):
                    token = auth[7:].strip()
            return secrets.compare_digest(str(token), bridge.token)

        def _read_body(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length <= 0:
                return {}
            if length > MAX_BODY_BYTES:
                raise ValueError("请求体过大")
            raw = self.rfile.read(length)
            if not raw:
                return {}
            data = json.loads(raw.decode("utf-8", errors="replace"))
            return data if isinstance(data, dict) else {}

        def _handle(self, method: str) -> None:
            parts = urllib.parse.urlsplit(self.path)
            path = urllib.parse.unquote(parts.path)
            query = {
                key: values[-1]
                for key, values in urllib.parse.parse_qs(parts.query).items()
            }
            if not self._authorized():
                self._send(401, {"ok": False, "error": "token 无效"})
                return
            try:
                body = self._read_body() if method == "POST" else {}
            except (ValueError, json.JSONDecodeError) as exc:
                self._send(400, {"ok": False, "error": str(exc)})
                return
            try:
                status, payload = bridge.handle(method, path, query, body)
            except PermissionError as exc:
                status, payload = 403, {"ok": False, "error": str(exc)}
            except FileNotFoundError as exc:
                status, payload = 404, {"ok": False, "error": str(exc)}
            except (ValueError, RuntimeError) as exc:
                status, payload = 400, {"ok": False, "error": str(exc)}
            except Exception as exc:  # noqa: BLE001 - 返回给调用方，不直接崩溃
                status, payload = 500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            self._send(status, payload)

        def do_GET(self) -> None:  # noqa: N802
            self._handle("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._handle("POST")

    return Handler