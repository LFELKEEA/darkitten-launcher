from __future__ import annotations

import hashlib
import json
import tomllib
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .descriptions import has_chinese, local_mod_zh_intro, project_zh_intro

META_DIR_NAME = ".dkl-meta"
_META_ICON_DIR = "icons"
_MAX_ICON_BYTES = 512 * 1024


@dataclass
class ModInfo:
    path: Path
    name: str
    version: str = ""
    loader: str = ""
    description: str = ""
    description_zh: str = ""
    enabled: bool = True
    size: int = 0
    icon_bytes: bytes = b""
    project_url: str = ""
    source_label: str = ""

    @property
    def filename(self) -> str:
        return self.path.name

    @property
    def size_text(self) -> str:
        size = self.size
        if size >= 1024 * 1024:
            return f"{size / 1024 / 1024:.1f} MB"
        if size >= 1024:
            return f"{size / 1024:.0f} KB"
        return f"{size} B"

    @property
    def intro(self) -> str:
        """优先展示持久化的中文介绍，其次使用 JAR 内的中文简介。"""
        if self.description_zh:
            return self.description_zh
        if has_chinese(self.description):
            return self.description
        return local_mod_zh_intro(
            self.name,
            loader=self.loader,
            version=self.version,
            description=self.description,
        )


def _meta_key(filename: str) -> str:
    return hashlib.sha1(str(filename).encode("utf-8")).hexdigest()[:20]


def _meta_candidates(filename: str) -> list[str]:
    names = [str(filename)]
    if filename.lower().endswith(".disabled"):
        names.append(filename[: -len(".disabled")])
    return names


def mod_meta_json_path(mods_dir: Path, filename: str) -> Path:
    return Path(mods_dir) / META_DIR_NAME / f"{_meta_key(filename)}.json"


def mod_meta_icon_path(mods_dir: Path, filename: str) -> Path:
    return (
        Path(mods_dir)
        / META_DIR_NAME
        / _META_ICON_DIR
        / f"{_meta_key(filename)}.png"
    )


def load_mod_meta(mods_dir: Path, filename: str) -> dict:
    for name in _meta_candidates(filename):
        path = mod_meta_json_path(mods_dir, name)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        if isinstance(data, dict):
            return data
    return {}


def _find_meta_icon(mods_dir: Path, filename: str) -> bytes:
    for name in _meta_candidates(filename):
        path = mod_meta_icon_path(mods_dir, name)
        try:
            if path.is_file() and path.stat().st_size <= _MAX_ICON_BYTES:
                return path.read_bytes()
        except OSError:
            continue
    return b""


def write_mod_meta(
    mods_dir: Path, filename: str, payload: dict, icon_bytes: bytes = b""
) -> None:
    """把下载来源、中文介绍与图标保存到 mods/.dkl-meta，供列表离线读取。"""
    mods_dir = Path(mods_dir)
    meta_path = mod_meta_json_path(mods_dir, filename)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    clean = {
        key: value
        for key, value in (payload or {}).items()
        if isinstance(value, (str, int, float, bool, list, dict)) or value is None
    }
    clean["filename"] = str(filename)
    temp = meta_path.with_name(meta_path.name + ".tmp")
    temp.write_text(
        json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temp.replace(meta_path)
    if icon_bytes:
        icon_path = mod_meta_icon_path(mods_dir, filename)
        icon_path.parent.mkdir(parents=True, exist_ok=True)
        icon_temp = icon_path.with_name(icon_path.name + ".tmp")
        icon_temp.write_bytes(icon_bytes)
        icon_temp.replace(icon_path)


def forget_mod_meta(mods_dir: Path, filename: str) -> None:
    for name in _meta_candidates(filename):
        for path in (
            mod_meta_json_path(mods_dir, name),
            mod_meta_icon_path(mods_dir, name),
        ):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def _clean(value: object, fallback: str = "") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    if not text:
        return fallback
    if text.startswith("${") or text.startswith("%"):
        return fallback
    return text


def _fabric_meta(
    zf: zipfile.ZipFile,
) -> tuple[str, str, str, str] | None:
    for meta_name in ("fabric.mod.json", "quilt.mod.json"):
        try:
            raw = zf.read(meta_name)
        except KeyError:
            continue
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            continue
        if meta_name == "quilt.mod.json":
            data = data.get("quilt_loader") or {}
        name = _clean(data.get("name") or data.get("id"))
        version = _clean(data.get("version"))
        description = data.get("description", "")
        if isinstance(description, dict):
            description = description.get("text", "")
        description = _clean(description)
        icon = data.get("icon")
        if not icon and isinstance(data.get("metadata"), dict):
            icon = data["metadata"].get("icon")
        return name, version, description, _icon_field(icon)
    return None


def _forge_meta(zf: zipfile.ZipFile) -> tuple[str, str, str, str] | None:
    for meta_name in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml"):
        try:
            raw = zf.read(meta_name)
        except KeyError:
            continue
        try:
            data = tomllib.loads(raw.decode("utf-8", errors="replace"))
        except Exception:
            continue
        mods = data.get("mods") or []
        if not isinstance(mods, list) or not mods:
            continue
        mod = mods[0] if isinstance(mods[0], dict) else {}
        name = _clean(
            mod.get("displayName") or mod.get("modId") or data.get("modId")
        )
        version = _clean(mod.get("version") or data.get("version"))
        description = _clean(mod.get("description") or data.get("description"))
        return name, version, description, _clean(mod.get("logoFile"))
    return None


def _icon_field(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        candidates = [
            (str(key), str(path))
            for key, path in value.items()
            if str(path).strip()
        ]
        if not candidates:
            return ""
        numeric = []
        for key, path in candidates:
            try:
                numeric.append((int(key), path))
            except ValueError:
                continue
        if numeric:
            return max(numeric, key=lambda item: item[0])[1]
        return candidates[-1][1]
    return ""


def _read_icon_from_jar(zf: zipfile.ZipFile, preferred: str) -> bytes:
    names = set(zf.namelist())
    candidates: list[str] = []
    preferred = str(preferred or "").strip().lstrip("/\\").replace("\\", "/")
    if preferred:
        candidates.append(preferred)
    candidates.extend(
        (
            "icon.png",
            "logo.png",
            "assets/icon.png",
            "pack.png",
        )
    )
    seen: set[str] = set()
    for name in candidates:
        if not name or name in seen or name not in names:
            continue
        seen.add(name)
        try:
            info = zf.getinfo(name)
            if info.file_size <= 0 or info.file_size > _MAX_ICON_BYTES:
                continue
            data = zf.read(name)
        except (KeyError, OSError, RuntimeError):
            continue
        if data:
            return data
    return b""


def _download_icon(url: str) -> bytes:
    if not url:
        return b""
    try:
        from .images import fetch_image_bytes

        return fetch_image_bytes(url, timeout=20, limit=_MAX_ICON_BYTES)
    except Exception:
        return b""


def remember_downloaded_mod(
    mods_dir: Path, jar_path: Path, metadata: dict
) -> None:
    """下载完成后把项目图标与中文介绍写入隐藏元数据目录。"""
    jar_path = Path(jar_path)
    if not jar_path.is_file():
        return
    info = metadata if isinstance(metadata, dict) else {}
    icon = _find_meta_icon(Path(mods_dir), jar_path.name)
    jar_info = read_mod(jar_path, with_meta=False)
    if not icon:
        icon = jar_info.icon_bytes
    if not icon:
        icon = _download_icon(str(info.get("icon_url") or ""))
    description_zh = str(
        info.get("description_zh")
        or project_zh_intro(
            info,
            str(info.get("content_type") or "mod"),
            game_version=str(info.get("game_version") or ""),
        )
    )
    payload = {
        "title": str(info.get("title") or jar_info.name or ""),
        "project_id": str(info.get("project_id") or ""),
        "slug": str(info.get("slug") or ""),
        "source": str(info.get("source") or ""),
        "source_label": str(info.get("source_label") or ""),
        "content_type": str(info.get("content_type") or "mod"),
        "icon_url": str(info.get("icon_url") or ""),
        "author": str(info.get("author") or ""),
        "categories": info.get("categories") or [],
        "loaders": info.get("loaders") or [],
        "game_versions": info.get("game_versions") or [],
        "downloads": info.get("downloads") or 0,
        "description": str(info.get("description") or jar_info.description or ""),
        "description_zh": description_zh,
        "project_url": str(info.get("project_url") or ""),
    }
    write_mod_meta(Path(mods_dir), jar_path.name, payload, icon)


def read_mod(path: Path, with_meta: bool = True) -> ModInfo:
    path = Path(path)
    enabled = path.name.lower().endswith(".jar")
    size = 0
    try:
        size = path.stat().st_size
    except OSError:
        pass

    fallback_name = path.name
    if fallback_name.lower().endswith(".disabled"):
        fallback_name = fallback_name[: -len(".disabled")]
    if fallback_name.lower().endswith(".jar"):
        fallback_name = fallback_name[:-4]
    name = fallback_name
    version = ""
    description = ""
    loader = ""
    icon_bytes = b""

    try:
        with zipfile.ZipFile(path) as zf:
            fabric = _fabric_meta(zf)
            if fabric:
                loader = "Fabric"
                name = fabric[0] or name
                version = fabric[1]
                description = fabric[2]
                icon_bytes = _read_icon_from_jar(zf, fabric[3])
            else:
                forge = _forge_meta(zf)
                if forge:
                    loader = "Forge"
                    name = forge[0] or name
                    version = forge[1]
                    description = forge[2]
                    icon_bytes = _read_icon_from_jar(zf, forge[3])
                else:
                    icon_bytes = _read_icon_from_jar(zf, "")
    except (zipfile.BadZipFile, OSError):
        pass

    description_zh = ""
    project_url = ""
    source_label = ""
    if with_meta:
        meta = load_mod_meta(path.parent, path.name)
        if meta:
            description_zh = str(meta.get("description_zh") or "")
            project_url = str(meta.get("project_url") or "")
            source_label = str(meta.get("source_label") or meta.get("source") or "")
            if not description:
                description = str(meta.get("description") or "")
            if name == fallback_name:
                name = str(meta.get("title") or name)
            saved_icon = _find_meta_icon(path.parent, path.name)
            if saved_icon:
                icon_bytes = saved_icon

    return ModInfo(
        path=path,
        name=name,
        version=version,
        loader=loader,
        description=description,
        description_zh=description_zh,
        enabled=enabled,
        size=size,
        icon_bytes=icon_bytes,
        project_url=project_url,
        source_label=source_label,
    )


def list_mods(mods_dir: Path) -> list[ModInfo]:
    """读取指定 mods 文件夹中的 Mod。"""
    mods_dir = Path(mods_dir)
    if not mods_dir.exists():
        return []
    result: list[ModInfo] = []
    try:
        entries = list(mods_dir.iterdir())
    except OSError:
        return result
    for path in entries:
        if not path.is_file():
            continue
        lower = path.name.lower()
        if lower.endswith(".jar") or lower.endswith(".jar.disabled"):
            result.append(read_mod(path))
    result.sort(key=lambda m: (not m.enabled, m.name.lower()))
    return result


def toggle_mod(mod: ModInfo) -> Path:
    path = mod.path
    if path.name.lower().endswith(".jar.disabled"):
        new_path = path.with_name(path.name[: -len(".disabled")])
    elif path.name.lower().endswith(".jar"):
        new_path = path.with_name(path.name + ".disabled")
    else:
        raise ValueError("不支持的文件类型")
    if new_path.exists():
        raise FileExistsError(f"目标文件已存在：{new_path.name}")
    path.rename(new_path)
    return new_path
