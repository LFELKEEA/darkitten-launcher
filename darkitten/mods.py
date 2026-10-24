from __future__ import annotations

import hashlib
import io
import json
import re
import tomllib
import zipfile
from dataclasses import dataclass, field, replace
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
    project_id: str = ""
    source: str = ""
    version_id: str = ""
    file_id: str = ""
    sha1: str = ""

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


NEOFORGE_SERVICE_MARKERS = (
    "META-INF/services/net.neoforged.neoforgespi.locating.IModLocator",
    "META-INF/services/net.neoforged.neoforgespi.locating.IModFileCandidateLocator",
    "META-INF/services/net.neoforged.neoforgespi.transformation.ClassProcessorProvider",
)
FORGE_SERVICE_MARKERS = (
    "META-INF/services/cpw.mods.modlauncher.api.ITransformationService",
    "META-INF/services/net.minecraftforge.fml.loading.moddiscovery.IModLocator",
    "META-INF/services/net.minecraftforge.forgespi.locating.IModLocator",
)
LOADER_ORDER = ("NeoForge", "Forge", "Quilt", "Fabric")
_NESTED_PREFIXES = ("META-INF/jarjar/", "META-INF/jar-jar/", "META-INF/jars/")
_MAX_NESTED_JARS = 8
_MAX_NESTED_BYTES = 32 * 1024 * 1024


@dataclass
class JarDeclarations:
    """一个 JAR 内声明的加载器与原始元数据。"""

    loaders: list[str] = field(default_factory=list)
    fabric: list[dict] = field(default_factory=list)
    quilt: list[dict] = field(default_factory=list)
    forge: list[dict] = field(default_factory=list)
    neoforge: list[dict] = field(default_factory=list)
    icon_bytes: bytes = b""
    nested: bool = False
    nested_provides: dict[str, str] = field(default_factory=dict)

    @property
    def has_metadata(self) -> bool:
        return bool(self.fabric or self.quilt or self.forge or self.neoforge)


def _norm_modid(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _has_version_number(value: object) -> bool:
    return bool(re.search(r"\d", str(value or "")))


def _zip_json(zf: zipfile.ZipFile, name: str) -> dict | None:
    try:
        raw = zf.read(name)
    except (KeyError, OSError, RuntimeError):
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _zip_toml(zf: zipfile.ZipFile, name: str) -> dict | None:
    try:
        raw = zf.read(name)
    except (KeyError, OSError, RuntimeError):
        return None
    try:
        data = tomllib.loads(raw.decode("utf-8", errors="replace"))
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _forge_modids(data: dict | None) -> set[str]:
    mods = (data or {}).get("mods") or []
    if not isinstance(mods, list):
        return set()
    return {
        _norm_modid(mod.get("modId"))
        for mod in mods
        if isinstance(mod, dict) and _norm_modid(mod.get("modId"))
    }


def _loaders_from(names: set[str], decl: JarDeclarations) -> list[str]:
    loaders: list[str] = []
    if decl.neoforge:
        loaders.append("NeoForge")
    if decl.forge:
        loaders.append("Forge")
    if decl.quilt:
        loaders.append("Quilt")
    if decl.fabric:
        loaders.append("Fabric")
    if not decl.forge and not decl.neoforge:
        if any(marker in names for marker in NEOFORGE_SERVICE_MARKERS):
            loaders.append("NeoForge")
        if any(marker in names for marker in FORGE_SERVICE_MARKERS):
            loaders.append("Forge")
    return [loader for loader in LOADER_ORDER if loader in loaders]


def _declarations_from_zip(
    zf: zipfile.ZipFile, *, with_icon: bool = False
) -> JarDeclarations:
    names = set(zf.namelist())
    decl = JarDeclarations()

    fabric = _zip_json(zf, "fabric.mod.json")
    quilt_raw = _zip_json(zf, "quilt.mod.json")
    quilt = None
    if quilt_raw:
        value = quilt_raw.get("quilt_loader") or {}
        quilt = value if isinstance(value, dict) else None
    forge = _zip_toml(zf, "META-INF/mods.toml")
    neoforge = _zip_toml(zf, "META-INF/neoforge.mods.toml")

    forge_ids = _forge_modids(forge) | _forge_modids(neoforge)
    if (
        fabric
        and forge_ids
        and _norm_modid(fabric.get("id")) not in forge_ids
        and not _has_version_number(fabric.get("version"))
    ):
        # JourneyMap 等 Forge/NeoForge 模组会故意放一个“not a fabric mod”
        # 的 fabric.mod.json，用来在 Fabric 环境里报错。它不能当作加载器依据。
        fabric = None

    if fabric:
        decl.fabric.append(fabric)
    if quilt:
        decl.quilt.append(quilt)
    if forge:
        decl.forge.append(forge)
    if neoforge:
        decl.neoforge.append(neoforge)
    decl.loaders = _loaders_from(names, decl)
    if with_icon:
        _icon = jar_identity(decl)[3]
        if _icon:
            decl.icon_bytes = _read_icon_from_jar(zf, _icon)
    return decl


def jar_identity(decl: JarDeclarations) -> tuple[str, str, str, str]:
    """返回 JAR 的名称、版本、简介和图标路径。"""
    for data in decl.neoforge + decl.forge:
        mods = data.get("mods") or []
        if isinstance(mods, list) and mods and isinstance(mods[0], dict):
            mod = mods[0]
            return (
                _clean(mod.get("displayName") or mod.get("modId")),
                _clean(mod.get("version") or data.get("version")),
                _clean(mod.get("description") or data.get("description")),
                _clean(mod.get("logoFile") or mod.get("iconFile")),
            )
    for data in decl.quilt + decl.fabric:
        description = data.get("description", "")
        if isinstance(description, dict):
            description = description.get("text", "")
        icon = data.get("icon")
        if not icon and isinstance(data.get("metadata"), dict):
            icon = data["metadata"].get("icon")
        return (
            _clean(data.get("name") or data.get("id")),
            _clean(data.get("version")),
            _clean(description),
            _icon_field(icon),
        )
    return "", "", "", ""


def _nested_jar_entries(
    zf: zipfile.ZipFile, names: set[str]
) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for meta_name in (
        "META-INF/jarjar/metadata.json",
        "META-INF/jar-jar/metadata.json",
    ):
        data = _zip_json(zf, meta_name)
        if not data:
            continue
        jars = data.get("jars") or []
        if not isinstance(jars, list):
            continue
        for item in jars:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "").strip()
            if not path:
                continue
            version = ""
            version_info = item.get("version")
            if isinstance(version_info, dict):
                version = _clean(version_info.get("artifactVersion"))
            entries.append((path, version))
    listed = {path for path, _version in entries}
    for name in sorted(names):
        if (
            name.startswith(_NESTED_PREFIXES)
            and name.lower().endswith(".jar")
            and name not in listed
        ):
            entries.append((name, ""))

    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for path, version in entries:
        if path in seen or path not in names:
            continue
        seen.add(path)
        result.append((path, version))
    return result


def _declaration_modids(decl: JarDeclarations) -> list[str]:
    modids: list[str] = []
    for data in decl.fabric + decl.quilt:
        modid = _clean(data.get("id"))
        if modid:
            modids.append(modid)
        provides = data.get("provides")
        if isinstance(provides, list):
            modids.extend(_clean(item) for item in provides if _clean(item))
    for data in decl.forge + decl.neoforge:
        mods = data.get("mods") or []
        if not isinstance(mods, list):
            continue
        for mod in mods:
            if isinstance(mod, dict) and _clean(mod.get("modId")):
                modids.append(_clean(mod.get("modId")))
    return list(dict.fromkeys(modids))


def _merge_declarations(target: JarDeclarations, part: JarDeclarations) -> None:
    target.fabric.extend(part.fabric)
    target.quilt.extend(part.quilt)
    target.forge.extend(part.forge)
    target.neoforge.extend(part.neoforge)
    loaders = set(target.loaders) | set(part.loaders)
    target.loaders = [loader for loader in LOADER_ORDER if loader in loaders]
    for modid, version in part.nested_provides.items():
        target.nested_provides.setdefault(modid, version)
    if part.icon_bytes and not target.icon_bytes:
        target.icon_bytes = part.icon_bytes


def read_jar_declarations(
    zf: zipfile.ZipFile,
    *,
    with_icon: bool = False,
    with_nested_provides: bool = False,
) -> JarDeclarations:
    """读取 JAR 元数据；仅有 jarjar 包装层时继续读取内嵌 JAR。"""
    decl = _declarations_from_zip(zf, with_icon=with_icon)
    root_has_metadata = decl.has_metadata
    if root_has_metadata and not with_nested_provides:
        return decl

    names = set(zf.namelist())
    total = 0
    for path, artifact_version in _nested_jar_entries(zf, names)[
        :_MAX_NESTED_JARS
    ]:
        try:
            info = zf.getinfo(path)
            if info.file_size <= 0 or info.file_size > _MAX_NESTED_BYTES:
                continue
            if total + info.file_size > _MAX_NESTED_BYTES:
                break
            data = zf.read(path)
        except (KeyError, OSError, RuntimeError):
            continue
        total += len(data)
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as inner:
                part = _declarations_from_zip(
                    inner, with_icon=with_icon and not root_has_metadata
                )
        except (zipfile.BadZipFile, OSError):
            continue
        if not part.has_metadata:
            continue
        version = jar_identity(part)[1]
        if not version or version.startswith("${"):
            version = artifact_version
        for modid in _declaration_modids(part):
            decl.nested_provides.setdefault(modid, version)
        if not root_has_metadata:
            _merge_declarations(decl, part)

    if not root_has_metadata and decl.has_metadata:
        decl.nested = True
    return decl


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
        "version_id": str(info.get("version_id") or ""),
        "file_id": str(info.get("file_id") or ""),
        "file_name": str(info.get("file_name") or jar_path.name),
        "sha1": str(info.get("sha1") or ""),
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
        "game_version": str(info.get("game_version") or ""),
        "loader": str(info.get("loader") or ""),
    }
    write_mod_meta(Path(mods_dir), jar_path.name, payload, icon)


_MOD_CACHE: dict[tuple[str, bool], tuple[tuple, ModInfo]] = {}
_MOD_CACHE_LIMIT = 512


def _file_signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _mod_signature(path: Path, with_meta: bool) -> tuple:
    """用文件修改时间与大小判断缓存是否仍然有效。"""
    signature: list[object] = [_file_signature(path)]
    if with_meta:
        meta_dir = path.parent / META_DIR_NAME
        try:
            has_meta = meta_dir.is_dir()
        except OSError:
            has_meta = False
        if has_meta:
            for name in _meta_candidates(path.name):
                signature.append(
                    _file_signature(mod_meta_json_path(path.parent, name))
                )
                signature.append(
                    _file_signature(mod_meta_icon_path(path.parent, name))
                )
    return tuple(signature)


def read_mod(path: Path, with_meta: bool = True) -> ModInfo:
    """读取 Mod 信息；同一个文件未变化时复用上一次的解析结果。"""
    path = Path(path)
    key = (str(path), bool(with_meta))
    signature = _mod_signature(path, with_meta)
    cached = _MOD_CACHE.get(key)
    if cached is not None and cached[0] == signature:
        return replace(cached[1])

    info = _read_mod_uncached(path, with_meta)
    if len(_MOD_CACHE) >= _MOD_CACHE_LIMIT:
        _MOD_CACHE.clear()
    _MOD_CACHE[key] = (signature, replace(info))
    return info


def _read_mod_uncached(path: Path, with_meta: bool = True) -> ModInfo:
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
            decl = read_jar_declarations(zf, with_icon=True)
            if decl.has_metadata:
                meta_name, meta_version, meta_description, icon_path = jar_identity(
                    decl
                )
                name = meta_name or name
                version = meta_version
                description = meta_description
                if len(decl.loaders) > 1:
                    loader = "多加载器"
                elif decl.loaders:
                    loader = decl.loaders[0]
                icon_bytes = decl.icon_bytes or _read_icon_from_jar(zf, icon_path)
            else:
                icon_bytes = _read_icon_from_jar(zf, "")
    except (zipfile.BadZipFile, OSError):
        pass

    description_zh = ""
    project_url = ""
    source_label = ""
    project_id = ""
    source = ""
    version_id = ""
    file_id = ""
    sha1 = ""
    if with_meta:
        meta = load_mod_meta(path.parent, path.name)
        if meta:
            description_zh = str(meta.get("description_zh") or "")
            project_url = str(meta.get("project_url") or "")
            source_label = str(meta.get("source_label") or meta.get("source") or "")
            project_id = str(meta.get("project_id") or "")
            source = str(meta.get("source") or "")
            version_id = str(meta.get("version_id") or "")
            file_id = str(meta.get("file_id") or "")
            sha1 = str(meta.get("sha1") or "")
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
        project_id=project_id,
        source=source,
        version_id=version_id,
        file_id=file_id,
        sha1=sha1,
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
