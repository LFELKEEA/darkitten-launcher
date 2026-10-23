from __future__ import annotations

import json
import tomllib
import zipfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ModInfo:
    path: Path
    name: str
    version: str = ""
    loader: str = ""
    description: str = ""
    enabled: bool = True
    size: int = 0

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


def _clean(value: object, fallback: str = "") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    if not text:
        return fallback
    if text.startswith("${") or text.startswith("%"):
        return fallback
    return text


def _fabric_meta(zf: zipfile.ZipFile) -> tuple[str, str, str] | None:
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
        return name, version, description
    return None


def _forge_meta(zf: zipfile.ZipFile) -> tuple[str, str, str] | None:
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
        return name, version, description
    return None


def read_mod(path: Path) -> ModInfo:
    enabled = path.name.lower().endswith(".jar")
    size = 0
    try:
        size = path.stat().st_size
    except OSError:
        pass

    name = path.name
    if name.lower().endswith(".disabled"):
        name = name[: -len(".disabled")]
    if name.lower().endswith(".jar"):
        name = name[:-4]
    version = ""
    description = ""
    loader = ""

    try:
        with zipfile.ZipFile(path) as zf:
            fabric = _fabric_meta(zf)
            if fabric:
                loader = "Fabric"
                name = fabric[0] or name
                version = fabric[1]
                description = fabric[2]
            else:
                forge = _forge_meta(zf)
                if forge:
                    loader = "Forge"
                    name = forge[0] or name
                    version = forge[1]
                    description = forge[2]
    except (zipfile.BadZipFile, OSError):
        pass

    return ModInfo(
        path=path,
        name=name,
        version=version,
        loader=loader,
        description=description,
        enabled=enabled,
        size=size,
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
