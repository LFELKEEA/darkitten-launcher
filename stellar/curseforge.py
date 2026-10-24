"""CurseForge 模组数据适配层。

官方 CurseForge API 需要申请 API Key。为了开箱即用，这里默认通过
MCIM 镜像访问 CurseForge API；如果设置了环境变量 CURSEFORGE_API_KEY
（或 DKL_CURSEFORGE_API_KEY），则优先直连官方 API。

下载文件时优先走 MCIM 文件镜像，失败后回退到 CurseForge CDN。
所有返回结构都尽量贴近 Modrinth 的字段，便于现有界面复用。
"""

from __future__ import annotations

import json
import os
import re
import threading
import urllib.error
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from .downloader import DownloadCancelled, download_to_file, http_open

CURSEFORGE_API = "https://api.curseforge.com/v1"
MCIM_CURSEFORGE_API = "https://mod.mcimirror.top/curseforge/v1"
MCIM_CDN_HOST = "mod.mcimirror.top"
FORGE_CDN_HOSTS = ("mediafilez.forgecdn.net", "media.forgecdn.net")
GAME_ID = 432
CLASS_ID_MODS = 6
CLASS_IDS = {
    "mod": CLASS_ID_MODS,
    "modpack": 4471,
    "resourcepack": 12,
    "shader": 6552,
}
CONTENT_PATHS = {
    CLASS_ID_MODS: "mc-mods",
    4471: "modpacks",
    12: "texture-packs",
    6552: "shaders",
}
PAGE_SIZE_MAX = 50

LOADER_TYPES = {
    "fabric": 4,
    "forge": 1,
    "neoforge": 6,
    "quilt": 5,
}
LOADER_NAMES = {
    1: "Forge",
    2: "LiteLoader",
    3: "CraftBukkit",
    4: "Fabric",
    5: "Quilt",
    6: "NeoForge",
}
_RELEASE_TYPES = {1: "release", 2: "beta", 3: "alpha"}
_MC_VERSION_RE = re.compile(r"^\d+(?:\.\d+)*(?:[-+].*)?$")
ProgressCallback = Callable[[int, str], None]


class CurseForgeError(Exception):
    pass


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _loader_key(loader: str) -> str:
    text = str(loader or "").strip().lower()
    return text if text in LOADER_TYPES else ""


def _loader_type(loader: str) -> int | None:
    return LOADER_TYPES.get(_loader_key(loader))


def _class_id(content_type: str) -> int:
    kind = str(content_type or "mod").strip().lower()
    return CLASS_IDS.get(kind, CLASS_ID_MODS)


def _content_type(class_id: Any) -> str:
    value = _as_int(class_id, CLASS_ID_MODS)
    for key, known in CLASS_IDS.items():
        if known == value:
            return key
    return "mod"


def _api_sources() -> list[tuple[str, dict[str, str]]]:
    key = (
        os.environ.get("CURSEFORGE_API_KEY")
        or os.environ.get("DKL_CURSEFORGE_API_KEY")
        or ""
    ).strip()
    sources: list[tuple[str, dict[str, str]]] = []
    if key:
        sources.append((CURSEFORGE_API, {"x-api-key": key}))
    sources.append((MCIM_CURSEFORGE_API, {}))
    return sources


def _request_json(
    path: str, params: dict[str, Any] | None = None, timeout: int = 35
) -> Any:
    query = urllib.parse.urlencode(
        {
            key: value
            for key, value in (params or {}).items()
            if value not in (None, "")
        }
    )
    suffix = f"?{query}" if query else ""
    errors: list[str] = []
    for base, headers in _api_sources():
        url = f"{base}{path}{suffix}"
        request_headers = {
            "Accept": "application/json",
            "User-Agent": "DarkittenLauncher CurseForge/1.9",
        }
        request_headers.update(headers)
        for attempt in range(2):
            try:
                with http_open(url, timeout=timeout, headers=request_headers) as resp:
                    raw = resp.read()
                return json.loads(raw.decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = ""
                try:
                    detail = exc.read(300).decode("utf-8", "replace")
                except Exception:
                    pass
                errors.append(f"HTTP {exc.code}{(' ' + detail) if detail else ''}")
                # 4xx 通常重试也无用，5xx / 429 再试一次。
                if exc.code < 500 and exc.code != 429:
                    break
            except Exception as exc:
                errors.append(str(exc))
                if attempt == 0:
                    continue
                break
    message = "；".join(item for item in errors if item) or "未知错误"
    raise CurseForgeError(f"无法连接 CurseForge：{message}")


def _extract_data(payload: Any) -> Any:
    if not isinstance(payload, dict):
        raise CurseForgeError("CurseForge 返回了无法识别的数据")
    return payload.get("data")


def _logo_url(data: dict) -> str:
    logo = data.get("logo") or {}
    if isinstance(logo, dict):
        return str(logo.get("thumbnailUrl") or logo.get("url") or "")
    return str(logo or "")


def _project_url(data: dict) -> str:
    links = data.get("links") or {}
    url = ""
    if isinstance(links, dict):
        url = str(links.get("websiteUrl") or "")
    if not url and data.get("slug"):
        path = CONTENT_PATHS.get(_as_int(data.get("classId"), CLASS_ID_MODS), "mc-mods")
        url = (
            f"https://www.curseforge.com/minecraft/{path}/"
            + urllib.parse.quote(str(data.get("slug")), safe="")
        )
    return url


def _author_name(data: dict) -> str:
    authors = data.get("authors") or []
    if isinstance(authors, list) and authors:
        first = authors[0]
        if isinstance(first, dict):
            return str(first.get("name") or "")
        return str(first or "")
    return ""


def _category_names(data: dict) -> list[str]:
    names: list[str] = []
    for item in data.get("categories") or []:
        if isinstance(item, dict) and item.get("name"):
            names.append(str(item["name"]))
    return names


def _game_versions(data: dict) -> list[str]:
    versions: list[str] = []
    for item in data.get("latestFilesIndexes") or []:
        if not isinstance(item, dict):
            continue
        version = str(item.get("gameVersion") or "").strip()
        if version and version not in versions:
            versions.append(version)
    if not versions:
        for file in data.get("latestFiles") or []:
            if not isinstance(file, dict):
                continue
            for version in file.get("gameVersions") or []:
                text = str(version or "").strip()
                if _MC_VERSION_RE.match(text) and text not in versions:
                    versions.append(text)
    return versions


def _project_loaders(data: dict) -> list[str]:
    names: list[str] = []
    for item in data.get("latestFilesIndexes") or []:
        if not isinstance(item, dict):
            continue
        name = LOADER_NAMES.get(_as_int(item.get("modLoader")), "")
        if name and name not in names:
            names.append(name)
    if not names:
        for file in data.get("latestFiles") or []:
            if not isinstance(file, dict):
                continue
            for version in file.get("gameVersions") or []:
                name = str(version or "")
                if name in LOADER_NAMES.values() and name not in names:
                    names.append(name)
    return names


def _gallery(data: dict) -> list[dict[str, str]]:
    gallery: list[dict[str, str]] = []
    for item in data.get("screenshots") or []:
        if not isinstance(item, dict):
            continue
        full = str(item.get("url") or "")
        preview = str(item.get("thumbnailUrl") or full)
        if not preview:
            continue
        gallery.append(
            {
                "url": preview,
                "full_url": full or preview,
                "title": str(item.get("title") or ""),
                "description": str(item.get("description") or ""),
            }
        )
    return gallery


def normalize_hit(data: dict) -> dict[str, Any]:
    """把 CurseForge 搜索结果转成界面使用的摘要结构。"""
    kind = _content_type(data.get("classId"))
    return {
        "project_id": str(data.get("id") or ""),
        "slug": str(data.get("slug") or ""),
        "title": str(data.get("name") or data.get("slug") or "未知项目"),
        "description": str(data.get("summary") or ""),
        "author": _author_name(data),
        "downloads": data.get("downloadCount") or 0,
        "categories": _category_names(data),
        "icon_url": _logo_url(data),
        "project_url": _project_url(data),
        "date_modified": str(data.get("dateModified") or ""),
        "content_type": kind,
        "source": "curseforge",
        "source_label": "CurseForge",
    }


def normalize_project(data: dict) -> dict[str, Any]:
    """把 CurseForge 项目详情转成界面使用的结构。"""
    links = data.get("links") if isinstance(data.get("links"), dict) else {}
    project = normalize_hit(data)
    project.update(
        {
            "id": str(data.get("id") or ""),
            "body": str(data.get("description") or data.get("summary") or ""),
            "followers": data.get("favoriteCount") or 0,
            "updated": str(data.get("dateModified") or data.get("dateReleased") or ""),
            "loaders": _project_loaders(data),
            "game_versions": _game_versions(data),
            "license": "",
            "gallery": _gallery(data),
            "source_url": str(links.get("sourceUrl") or ""),
            "issues_url": str(links.get("issuesUrl") or ""),
            "wiki_url": str(links.get("wikiUrl") or ""),
            "discord_url": str(links.get("discordUrl") or ""),
            "allow_mod_distribution": bool(data.get("allowModDistribution", True)),
        }
    )
    return project


def _file_hashes(file: dict) -> dict[str, str]:
    hashes: dict[str, str] = {}
    raw = file.get("hashes")
    if isinstance(raw, dict):
        for key, value in raw.items():
            if value:
                hashes[str(key).lower()] = str(value)
    elif isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            algo = _as_int(item.get("algo"))
            value = str(item.get("value") or "")
            if not value:
                continue
            if algo == 1:
                hashes["sha1"] = value
            elif algo == 2:
                hashes["md5"] = value
    return hashes


def _file_game_versions(file: dict) -> list[str]:
    versions: list[str] = []
    for raw in file.get("gameVersions") or []:
        text = str(raw or "").strip()
        if _MC_VERSION_RE.match(text) and text not in versions:
            versions.append(text)
    return versions


def _file_loaders(file: dict) -> list[str]:
    loaders: list[str] = []
    for raw in file.get("gameVersions") or []:
        text = str(raw or "").strip()
        key = text.lower()
        if key in LOADER_TYPES and key not in loaders:
            loaders.append(key)
    return loaders


def _required_dependencies(file: dict) -> list[dict[str, Any]]:
    deps: list[dict[str, Any]] = []
    for item in file.get("dependencies") or []:
        if not isinstance(item, dict):
            continue
        if _as_int(item.get("relationType")) != 3:
            continue
        mod_id = str(item.get("modId") or "").strip()
        if not mod_id or mod_id == "0":
            continue
        deps.append(
            {
                "project_id": mod_id,
                "version_id": "",
                "dependency_type": "required",
            }
        )
    return deps


def normalize_file(
    file: dict,
    project_slug: str = "",
    project_url: str = "",
    content_type: str = "mod",
) -> dict[str, Any]:
    """把 CurseForge 文件转成 Modrinth 风格的版本字典。"""
    filename = Path(str(file.get("fileName") or "").replace("\\", "/")).name
    file_id = str(file.get("id") or "")
    display_name = str(
        file.get("displayName") or (filename[:-4] if filename.endswith(".jar") else filename)
    )
    download_url = str(file.get("downloadUrl") or "")
    page_url = project_url
    if project_slug and file_id:
        path = CONTENT_PATHS.get(_class_id(content_type), "mc-mods")
        page_url = (
            f"https://www.curseforge.com/minecraft/{path}/"
            f"{urllib.parse.quote(project_slug, safe='')}/files/{file_id}"
        )
    release_type = _as_int(file.get("releaseType"), 1)
    return {
        "id": file_id,
        "version_number": display_name,
        "version_type": _RELEASE_TYPES.get(release_type, "release"),
        "files": [
            {
                "primary": True,
                "url": download_url,
                "filename": filename,
                "size": _as_int(file.get("fileLength")),
                "hashes": _file_hashes(file),
            }
        ],
        "dependencies": _required_dependencies(file),
        "game_versions": _file_game_versions(file),
        "loaders": _file_loaders(file),
        "source": "curseforge",
        "source_label": "CurseForge",
        "content_type": str(content_type or "mod"),
        "page_url": page_url,
        "file_date": str(file.get("fileDate") or ""),
        "download_count": file.get("downloadCount") or 0,
    }


def search_mods(
    query: str,
    *,
    loader: str = "",
    game_version: str = "",
    limit: int = 20,
    offset: int = 0,
    content_type: str = "mod",
) -> dict[str, Any]:
    text = str(query or "").strip()
    kind = str(content_type or "mod").strip().lower()
    if kind not in CLASS_IDS:
        kind = "mod"
    page_size = max(1, min(PAGE_SIZE_MAX, int(limit)))
    index = max(0, int(offset))
    params: dict[str, Any] = {
        "gameId": GAME_ID,
        "classId": CLASS_IDS[kind],
        "index": index,
        "pageSize": page_size,
        "sortField": 11 if text else 2,
    }
    if text:
        params["searchFilter"] = text
    else:
        # 空搜索时按总下载量展示热门 Mod；有搜索词时由 sortField=11
        # 交给 CurseForge 内部的相关度排序（不要传 sortOrder）。
        params["sortOrder"] = "desc"
    if game_version:
        params["gameVersion"] = str(game_version).strip()
    loader_type = _loader_type(loader) if kind == "mod" else None
    if loader_type is not None:
        params["modLoaderType"] = loader_type
    payload = _request_json("/mods/search", params)
    data = _extract_data(payload)
    if not isinstance(data, list):
        raise CurseForgeError("CurseForge 搜索结果格式不正确")
    pagination = payload.get("pagination") if isinstance(payload, dict) else {}
    total = 0
    if isinstance(pagination, dict):
        try:
            total = int(pagination.get("totalCount") or 0)
        except Exception:
            total = 0
    hits = [normalize_hit(item) for item in data if isinstance(item, dict)]
    return {
        "hits": hits,
        "total_hits": total or len(hits),
        "offset": index,
        "limit": page_size,
    }


def fetch_project(project_id: str) -> dict[str, Any]:
    project_id = str(project_id or "").strip()
    if not project_id:
        raise CurseForgeError("缺少 CurseForge 项目 ID")
    payload = _request_json(
        f"/mods/{urllib.parse.quote(project_id, safe='')}"
    )
    data = _extract_data(payload)
    if not isinstance(data, dict) or not data.get("id"):
        raise CurseForgeError("CurseForge 项目详情无法识别")
    return normalize_project(data)


def fetch_file(
    project_id: str, file_id: str, content_type: str = "mod"
) -> dict[str, Any]:
    """读取单个文件的下载地址与校验信息，供整合包导入使用。"""
    project_id = str(project_id or "").strip()
    file_id = str(file_id or "").strip()
    if not project_id or not file_id:
        raise CurseForgeError("缺少 CurseForge 文件 ID")
    payload = _request_json(
        f"/mods/{urllib.parse.quote(project_id, safe='')}/files/"
        f"{urllib.parse.quote(file_id, safe='')}"
    )
    data = _extract_data(payload)
    if not isinstance(data, dict) or not data.get("id"):
        raise CurseForgeError("CurseForge 文件详情无法识别")
    return normalize_file(data, content_type=content_type)


def fetch_projects(project_ids: list[str]) -> dict[str, dict[str, Any]]:
    """批量读取前置项目；单个失败不影响其他项目。"""
    ids = list(
        dict.fromkeys(
            str(item).strip() for item in project_ids if str(item).strip()
        )
    )
    if not ids:
        return {}

    def load(project_id: str) -> dict[str, Any] | None:
        try:
            return fetch_project(project_id)
        except Exception:
            return None

    result: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=min(4, len(ids))) as pool:
        for project in pool.map(load, ids):
            if not project:
                continue
            for key in (project.get("id"), project.get("slug")):
                if key:
                    result[str(key)] = project
    return result


def fetch_mod_versions(
    project_id: str,
    loader: str = "",
    game_version: str = "",
    content_type: str = "mod",
) -> list[dict[str, Any]]:
    project_id = str(project_id or "").strip()
    kind = str(content_type or "mod").strip().lower()
    if kind not in CLASS_IDS:
        kind = "mod"
    if not project_id:
        raise CurseForgeError("缺少 CurseForge 项目 ID")
    params: dict[str, Any] = {"index": 0, "pageSize": PAGE_SIZE_MAX}
    if game_version:
        params["gameVersion"] = str(game_version).strip()
    loader_type = _loader_type(loader) if kind == "mod" else None
    if loader_type is not None:
        params["modLoaderType"] = loader_type
    payload = _request_json(
        f"/mods/{urllib.parse.quote(project_id, safe='')}/files", params
    )
    data = _extract_data(payload)
    if not isinstance(data, list):
        raise CurseForgeError("CurseForge 文件列表格式不正确")

    key = _loader_key(loader)
    versions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for file in data:
        if not isinstance(file, dict) or file.get("isAvailable") is False:
            continue
        file_id = str(file.get("id") or "")
        if not file_id or file_id in seen:
            continue
        version = normalize_file(file, content_type=kind)
        if not version["files"][0]["filename"]:
            continue
        if game_version and game_version not in version["game_versions"]:
            continue
        if kind == "mod" and key and key not in version["loaders"]:
            continue
        seen.add(file_id)
        versions.append(version)
    return versions


def candidate_download_urls(url: str) -> list[str]:
    """为 CurseForge CDN 地址生成镜像与回退地址。"""
    url = str(url or "").strip()
    if not url:
        return []
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    candidates: list[str] = []
    if parts.path.startswith("/files/"):
        candidates.append(
            urllib.parse.urlunsplit(
                ("https", MCIM_CDN_HOST, parts.path, parts.query, "")
            )
        )
        for cdn_host in FORGE_CDN_HOSTS:
            if cdn_host != host:
                candidates.append(
                    urllib.parse.urlunsplit(
                        ("https", cdn_host, parts.path, parts.query, "")
                    )
                )
    candidates.append(url)
    result: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in result:
            result.append(candidate)
    return result


def _primary_file(version: dict[str, Any]) -> dict[str, Any] | None:
    files = [f for f in version.get("files") or [] if isinstance(f, dict)]
    for item in files:
        if item.get("primary") and item.get("url"):
            return item
    for item in files:
        if item.get("url"):
            return item
    for item in files:
        if item.get("filename"):
            return item
    return None


def download_mod_file(
    version: dict[str, Any],
    mods_dir: Path,
    *,
    progress_cb: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
    threads: int = 8,
) -> Path:
    progress = progress_cb or (lambda p, m: None)
    file = _primary_file(version)
    if not file:
        raise CurseForgeError("这个版本没有可下载的文件")
    filename = Path(str(file.get("filename") or "")).name
    if not filename:
        raise CurseForgeError("这个版本的文件名无效")
    url = str(file.get("url") or "")
    if not url:
        raise CurseForgeError(
            "作者未开放第三方下载，请点击 CurseForge 链接手动下载"
        )

    mods_dir = Path(mods_dir)
    mods_dir.mkdir(parents=True, exist_ok=True)
    dest = mods_dir / filename
    hashes = file.get("hashes") or {}
    sha1 = str(hashes.get("sha1") or "")
    size = _as_int(file.get("size"))

    if dest.exists():
        if not size or dest.stat().st_size == size:
            progress(100, f"{filename} 已存在")
            return dest

    progress(1, f"正在下载 {filename}")
    candidates = candidate_download_urls(url)

    def on_bytes(done: int, total: int) -> None:
        if total:
            percent = max(1, min(99, int(done * 100 / total)))
            progress(
                percent,
                f"{filename} · {done / 1024 / 1024:.1f}/{total / 1024 / 1024:.1f} MB",
            )
        else:
            progress(50, f"正在下载 {filename}")

    last_error: Exception | None = None
    for candidate in candidates:
        try:
            download_to_file(
                candidate,
                dest,
                sha1=sha1,
                size=size,
                cancel_event=cancel_event,
                progress_cb=on_bytes,
                connections=min(4, max(2, int(threads) // 2)),
            )
            last_error = None
            break
        except DownloadCancelled:
            raise
        except Exception as exc:
            last_error = exc
            if cancel_event is not None and cancel_event.is_set():
                raise DownloadCancelled() from exc
    if last_error is not None:
        raise CurseForgeError(f"下载失败：{last_error}") from last_error
    progress(100, "下载完成")
    return dest
