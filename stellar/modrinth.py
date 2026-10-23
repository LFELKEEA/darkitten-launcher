from __future__ import annotations

import json
import threading
import urllib.parse
from pathlib import Path
from typing import Any, Callable

from .downloader import DownloadCancelled, download_to_file, http_open as _http_open

MODRINTH_API = "https://api.modrinth.com/v2"
ProgressCallback = Callable[[int, str], None]

LOADER_KEYS = {"Fabric": "fabric", "Forge": "forge", "NeoForge": "neoforge"}


class ModrinthError(Exception):
    pass


def _get_json(url: str, timeout: int = 30) -> Any:
    try:
        with _http_open(url, timeout=timeout) as resp:
            raw = resp.read()
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise ModrinthError(f"无法连接 Modrinth：{exc}") from exc


def _loader_key(loader: str) -> str:
    if not loader:
        return ""
    if loader in LOADER_KEYS:
        return LOADER_KEYS[loader]
    return loader.strip().lower()


def search_mods(
    query: str,
    *,
    loader: str = "",
    game_version: str = "",
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    facets: list[list[str]] = [["project_type:mod"]]
    key = _loader_key(loader)
    if key:
        facets.append([f"categories:{key}"])
    if game_version:
        facets.append([f"versions:{game_version}"])
    params = {
        "query": query.strip(),
        "limit": max(1, min(50, int(limit))),
        "offset": max(0, int(offset)),
        "index": "relevance" if query.strip() else "downloads",
        "facets": json.dumps(facets, separators=(",", ":")),
    }
    url = f"{MODRINTH_API}/search?" + urllib.parse.urlencode(params)
    data = _get_json(url)
    if not isinstance(data, dict):
        raise ModrinthError("Modrinth 返回了无法识别的数据")
    return data


def search_server_addons(
    query: str,
    *,
    server_type: str,
    game_version: str = "",
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    """搜索服务端插件（Paper/Spigot）或服务端 Mod。"""
    server = str(server_type or "").lower()
    if server in ("paper", "spigot"):
        facets: list[list[str]] = [
            ["project_type:plugin"],
            ["categories:paper", "categories:spigot", "categories:bukkit"],
        ]
    elif server in ("fabric", "forge", "neoforge"):
        facets = [
            ["project_type:mod"],
            [f"categories:{server}"],
            ["server_side:required", "server_side:optional"],
        ]
    else:
        raise ModrinthError("未知的服务端类型")
    if game_version:
        facets.append([f"versions:{game_version}"])
    params = {
        "query": query.strip(),
        "limit": max(1, min(50, int(limit))),
        "offset": max(0, int(offset)),
        "index": "relevance" if query.strip() else "downloads",
        "facets": json.dumps(facets, separators=(",", ":")),
    }
    url = f"{MODRINTH_API}/search?" + urllib.parse.urlencode(params)
    data = _get_json(url)
    if not isinstance(data, dict):
        raise ModrinthError("Modrinth 返回了无法识别的数据")
    return data


def fetch_server_addon_versions(
    project_id: str,
    server_type: str,
    game_version: str = "",
) -> list[dict[str, Any]]:
    server = str(server_type or "").lower()
    if server in ("paper", "spigot"):
        loaders = ["paper", "spigot", "bukkit"]
    elif server in ("fabric", "forge", "neoforge"):
        loaders = [server]
    else:
        raise ModrinthError("未知的服务端类型")
    params: dict[str, str] = {"loaders": json.dumps(loaders)}
    if game_version:
        params["game_versions"] = json.dumps([game_version])
    url = f"{MODRINTH_API}/project/{project_id}/version?" + urllib.parse.urlencode(
        params
    )
    data = _get_json(url)
    if not isinstance(data, list):
        raise ModrinthError("Modrinth 版本列表格式不正确")
    result = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        files = entry.get("files") or []
        if not isinstance(files, list) or not files:
            continue
        if not any(isinstance(f, dict) and f.get("url") for f in files):
            continue
        result.append(entry)
    return result


def fetch_projects(project_ids: list[str]) -> dict[str, dict[str, Any]]:
    """批量读取项目摘要，用于前置 Mod 按钮显示名称和图标。"""
    ids = list(dict.fromkeys(str(item).strip() for item in project_ids if str(item).strip()))
    if not ids:
        return {}
    params = {"ids": json.dumps(ids, separators=(",", ":"))}
    url = f"{MODRINTH_API}/projects?" + urllib.parse.urlencode(params)
    data = _get_json(url)
    if not isinstance(data, list):
        raise ModrinthError("Modrinth 返回的项目列表无法识别")
    result: dict[str, dict[str, Any]] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        for key in (item.get("id"), item.get("slug")):
            if key:
                result[str(key)] = item
    return result


def fetch_project(project_id: str) -> dict[str, Any]:
    """读取 Modrinth 项目详情（图标、画廊、统计、链接和正文）。"""
    project_id = (project_id or "").strip()
    if not project_id:
        raise ModrinthError("缺少 Mod 项目 ID")
    url = f"{MODRINTH_API}/project/{urllib.parse.quote(project_id, safe='')}"
    data = _get_json(url)
    if not isinstance(data, dict) or not data.get("id"):
        raise ModrinthError("Modrinth 返回的项目详情无法识别")
    gallery = []
    for item in data.get("gallery") or []:
        if isinstance(item, dict) and item.get("url"):
            gallery.append(item)
        elif isinstance(item, str) and item:
            gallery.append({"url": item})
    gallery.sort(key=lambda item: 0 if item.get("featured") else 1)
    data["gallery"] = gallery
    return data


def fetch_mod_versions(
    project_id: str, loader: str = "", game_version: str = ""
) -> list[dict[str, Any]]:
    params: dict[str, str] = {}
    key = _loader_key(loader)
    if key:
        params["loaders"] = json.dumps([key])
    if game_version:
        params["game_versions"] = json.dumps([game_version])
    url = f"{MODRINTH_API}/project/{project_id}/version"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = _get_json(url)
    if not isinstance(data, list):
        raise ModrinthError("Modrinth 版本列表格式不正确")
    result = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        files = entry.get("files") or []
        if not isinstance(files, list) or not files:
            continue
        if not any(isinstance(f, dict) and f.get("url") for f in files):
            continue
        result.append(entry)
    return result


def lookup_version_by_hash(sha1: str) -> dict[str, Any] | None:
    """按文件 SHA-1 反查 Modrinth 版本；未收录时返回 None。"""
    digest = str(sha1 or "").strip().lower()
    if not digest:
        return None
    url = f"{MODRINTH_API}/version_file/{digest}?algorithm=sha1"
    try:
        data = _get_json(url)
    except ModrinthError:
        return None
    return data if isinstance(data, dict) and data.get("id") else None


def version_supports(
    version: dict[str, Any], loader: str = "", game_version: str = ""
) -> bool:
    """判断 Modrinth 版本是否支持目标加载器与 MC 版本。"""
    if not isinstance(version, dict):
        return False
    key = _loader_key(loader)
    loaders = {str(item).lower() for item in version.get("loaders") or []}
    games = {str(item) for item in version.get("game_versions") or []}
    if key and key not in loaders:
        return False
    if game_version and game_version not in games:
        return False
    return True


def primary_file(version: dict[str, Any]) -> dict[str, Any] | None:
    files = [f for f in version.get("files") or [] if isinstance(f, dict)]
    for item in files:
        if item.get("primary") and item.get("url"):
            return item
    for item in files:
        if item.get("url"):
            return item
    return None


def required_dependencies(version: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dep
        for dep in version.get("dependencies") or []
        if isinstance(dep, dict) and dep.get("dependency_type") == "required"
    ]


def download_mod_file(
    version: dict[str, Any],
    mods_dir: Path,
    *,
    progress_cb: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
    threads: int = 8,
) -> Path:
    progress = progress_cb or (lambda p, m: None)
    file = primary_file(version)
    if not file:
        raise ModrinthError("这个版本没有可下载的文件")
    filename = Path(str(file.get("filename") or "")).name
    if not filename:
        filename = f"{version.get('id', 'mod')}.jar"
    mods_dir = Path(mods_dir)
    mods_dir.mkdir(parents=True, exist_ok=True)
    dest = mods_dir / filename
    hashes = file.get("hashes") or {}
    sha1 = str(hashes.get("sha1") or "")
    size = int(file.get("size") or 0)

    if dest.exists():
        if size and dest.stat().st_size == size:
            progress(100, f"{filename} 已存在")
            return dest
        if not size:
            progress(100, f"{filename} 已存在")
            return dest

    progress(1, f"正在下载 {filename}")

    def on_bytes(done: int, total: int) -> None:
        if total:
            percent = max(1, min(99, int(done * 100 / total)))
            progress(
                percent,
                f"{filename} · {done / 1024 / 1024:.1f}/{total / 1024 / 1024:.1f} MB",
            )
        else:
            progress(50, f"正在下载 {filename}")

    try:
        download_to_file(
            str(file["url"]),
            dest,
            sha1=sha1,
            size=size,
            cancel_event=cancel_event,
            progress_cb=on_bytes,
            connections=min(4, max(2, int(threads) // 2)),
        )
    except DownloadCancelled:
        raise
    except Exception as exc:
        raise ModrinthError(str(exc)) from exc
    progress(100, "下载完成")
    return dest
