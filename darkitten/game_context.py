from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from .mods import load_mod_meta

if TYPE_CHECKING:
    from .config import Config
    from .versions import VersionSummary

LOADER_KEYS = {
    "Fabric": "fabric",
    "Forge": "forge",
    "NeoForge": "neoforge",
    "Quilt": "quilt",
}
LOADER_NAMES = {value: key for key, value in LOADER_KEYS.items()}


def loader_key(loader: str) -> str:
    text = str(loader or "").strip()
    if text in LOADER_KEYS:
        return LOADER_KEYS[text]
    low = text.lower()
    return low if low in LOADER_KEYS.values() else ""


def loader_candidates(loader: str) -> list[str]:
    """Quilt 可以直接加载 Fabric 的 Mod，其余加载器只用自己的键。"""
    key = loader_key(loader)
    if key == "quilt":
        return ["quilt", "fabric"]
    return [key] if key else []


@dataclass(frozen=True)
class LoaderContext:
    """一个游戏实例实际使用的加载器、MC 版本与目录。"""

    version_id: str
    launch_id: str
    mc_version: str
    loader: str
    loader_key: str
    game_dir: Path
    mods_dir: Path
    resourcepacks_dir: Path
    shaderpacks_dir: Path
    options_path: Path

    @property
    def has_loader(self) -> bool:
        return bool(self.loader_key)

    @property
    def loader_label(self) -> str:
        return self.loader or "原版"

    @property
    def display_version(self) -> str:
        return self.mc_version or self.version_id


def resolve_context(
    version_id: str,
    versions: Iterable[VersionSummary],
    config: Config,
) -> LoaderContext:
    """按启动器的绑定关系解析实例真正使用的加载器。"""
    version_id = str(version_id or "").strip()
    by_id = {item.id: item for item in versions}
    summary = by_id.get(version_id)
    if summary is None:
        raise ValueError(f"找不到游戏版本 {version_id or '（未选择）'}")

    launch_id = version_id
    profile = None
    if not summary.is_loader_profile and not summary.loader:
        bound = config.loader_for(version_id)
        candidate = by_id.get(bound)
        if (
            candidate is not None
            and candidate.is_loader_profile
            and candidate.base_id == summary.base_id
        ):
            profile = candidate
            launch_id = candidate.id

    if summary.loader:
        loader_name = summary.loader
        mc_version = summary.base_id or summary.id
    elif profile is not None:
        loader_name = profile.loader
        mc_version = profile.base_id or profile.id
    else:
        loader_name = ""
        mc_version = summary.base_id or summary.id

    game_dir = Path(config.version_game_path(version_id))
    return LoaderContext(
        version_id=version_id,
        launch_id=launch_id,
        mc_version=str(mc_version or ""),
        loader=str(loader_name or ""),
        loader_key=loader_key(loader_name),
        game_dir=game_dir,
        mods_dir=game_dir / "mods",
        resourcepacks_dir=game_dir / "resourcepacks",
        shaderpacks_dir=game_dir / "shaderpacks",
        options_path=game_dir / "options.txt",
    )


def file_sha1(path: Path) -> str:
    digest = hashlib.sha1()
    try:
        with Path(path).open("rb") as fp:
            while True:
                chunk = fp.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def normalize_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def filename_matches(filename: str, *tokens: str) -> bool:
    """用文件名里的关键字识别手动放进 mods 的同名 Mod。"""
    normalized = normalize_token(filename)
    for token in tokens:
        key = normalize_token(token)
        if len(key) >= 4 and key in normalized:
            return True
    return False


def enabled_mod_files(mods_dir: Path) -> list[Path]:
    try:
        return sorted(
            (
                path
                for path in Path(mods_dir).iterdir()
                if path.is_file() and path.name.lower().endswith(".jar")
            ),
            key=lambda path: path.name.lower(),
        )
    except OSError:
        return []


def all_mod_files(mods_dir: Path) -> list[Path]:
    try:
        return sorted(
            (
                path
                for path in Path(mods_dir).iterdir()
                if path.is_file()
                and path.name.lower().endswith((".jar", ".jar.disabled"))
            ),
            key=lambda path: path.name.lower(),
        )
    except OSError:
        return []


def installed_meta_keys(mods_dir: Path) -> set[str]:
    """收集已安装 Mod 的 project/slug 标识，供去重使用。"""
    keys: set[str] = set()
    for path in all_mod_files(mods_dir):
        meta = load_mod_meta(Path(mods_dir), path.name)
        for field in ("project_id", "slug", "version_id", "file_id"):
            value = str(meta.get(field) or "").strip().lower()
            if value:
                keys.add(value)
        keys.add(path.name.lower())
    return keys
