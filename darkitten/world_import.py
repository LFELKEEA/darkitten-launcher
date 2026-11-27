"""把其他游戏版本的存档导入服务端或启动器存档目录。

可以导入完整世界，也可以只导入主世界、下界或末地中的一个或几个维度：
完整导入只做文件复制，level.dat、region、DIM-1、DIM1、dimensions、playerdata、
stats、advancements、data、datapacks 等都原样搬过去，不重写任何 NBT；
按维度导入时只替换选中的维度，未选中的维度、level.dat 和玩家数据保持原样。
因此玩家数据、物品栏、末影箱、进度和各个维度都会保留；如果目标版本更新，
Minecraft 会在首次打开世界时自己完成跨版本升级。

目标位置已有同名世界或同名维度时不会删除，而是重命名为
``<名字>.bak-时间戳`` 保留。
"""

from __future__ import annotations

import gzip
import os
import re
import shutil
import threading
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Callable, Iterator

from . import nbt
from .packs import human_size
from .worlds import _to_int, world_name, world_seed

ProgressCallback = Callable[[int, str], None]

MAX_TOTAL_FILES = 300_000
MAX_TOTAL_BYTES = 256 * 1024**3

_ILLEGAL_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_SKIP_NAMES = {
    "session.lock",
    ".ds_store",
    "thumbs.db",
    "desktop.ini",
}
_SKIP_DIRS = {"__MACOSX"}

_DIMENSION_LABELS = {
    "minecraft:overworld": "主世界",
    "minecraft:the_nether": "下界",
    "minecraft:the_end": "末地",
}

DIMENSION_KEYS = ("overworld", "nether", "end")
DIMENSION_NAMES = {
    "overworld": "主世界",
    "nether": "下界",
    "end": "末地",
}
_NAMESPACED_PATHS = {
    "overworld": ("dimensions", "minecraft", "overworld"),
    "nether": ("dimensions", "minecraft", "the_nether"),
    "end": ("dimensions", "minecraft", "the_end"),
}
_LEGACY_PATHS = {
    "nether": ("DIM-1",),
    "end": ("DIM1",),
}
_OVERWORLD_DIRS = {"region", "entities", "poi"}


class WorldImportError(ValueError):
    pass


class WorldImportCancelled(Exception):
    pass


@dataclass
class WorldSource:
    path: Path
    is_zip: bool
    root: str = ""
    folder_name: str = ""
    name: str = ""
    version_name: str = ""
    data_version: int = 0
    seed: int | None = None
    files: int = 0
    total_bytes: int = 0
    dimensions: list[str] = field(default_factory=list)
    players: int = 0
    # Paper/Spigot 的下界、末地放在 world_nether、world_the_end 里。
    # 每项是 (实际目录, 映射到存档内的相对前缀)。
    extra_sources: list[tuple[Path, tuple[str, ...]]] = field(default_factory=list)

    @property
    def size_text(self) -> str:
        return human_size(self.total_bytes)

    @property
    def seed_text(self) -> str:
        return str(self.seed) if self.seed is not None else "未知"

    @property
    def version_text(self) -> str:
        if self.version_name:
            return self.version_name
        if self.data_version:
            return f"数据版本 {self.data_version}"
        return "未知版本"

    @property
    def dimension_text(self) -> str:
        return "、".join(self.dimensions) if self.dimensions else "主世界"

    @property
    def player_text(self) -> str:
        return f"{self.players} 个玩家存档" if self.players else "没有玩家数据"


@dataclass
class WorldImportResult:
    folder: Path
    backup: Path | None
    source: WorldSource
    files: int = 0
    total_bytes: int = 0
    level_name: str = ""
    backups: list[Path] = field(default_factory=list)
    dimensions: list[str] = field(default_factory=list)

    @property
    def size_text(self) -> str:
        return human_size(self.total_bytes)

    @property
    def dimension_text(self) -> str:
        return "、".join(
            DIMENSION_NAMES.get(key, key) for key in self.dimensions
        )


# ---------------------------------------------------------------------------
# 通用工具


def _check_cancel(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise WorldImportCancelled()


def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _skip_name(name: str) -> bool:
    return name.lower() in _SKIP_NAMES


def version_key(text: str) -> tuple[int, int, int]:
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", str(text or ""))
    if not match:
        return (0, 0, 0)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))


def is_newer_version(source_version: str, target_version: str) -> bool:
    """来源版本高于目标版本时返回 True；Minecraft 不能降级打开存档。"""
    if not source_version or not target_version:
        return False
    return version_key(source_version) > version_key(target_version)


def safe_folder_name(text: str, fallback: str = "world") -> str:
    value = _ILLEGAL_NAME.sub("-", str(text or "")).strip().rstrip(". ")
    if not value or value.split(".")[0].upper() in _WINDOWS_RESERVED:
        value = fallback
    return value[:64] or fallback


def _parse_level_bytes(raw: bytes) -> dict:
    if raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except OSError as error:
            raise WorldImportError(f"无法解压 level.dat：{error}") from error
    try:
        return nbt.parse_nbt(raw)
    except (nbt.NBTError, IndexError, ValueError) as error:
        raise WorldImportError(f"level.dat 不是有效的存档数据：{error}") from error


def _level_fields(root: dict, fallback: str) -> dict:
    data = root.get("Data")
    data = data if isinstance(data, dict) else root
    version = data.get("Version")
    version_name = ""
    if isinstance(version, dict):
        version_name = str(version.get("Name") or "")
    return {
        "name": world_name(data, fallback),
        "seed": world_seed(data),
        "version_name": version_name,
        "data_version": _to_int(data.get("DataVersion"), 0),
    }


def _dimensions_of(relative_files: list[Path]) -> list[str]:
    labels: list[str] = []
    custom: list[str] = []
    for relative in relative_files:
        parts = relative.parts
        if not parts:
            continue
        head = parts[0]
        if head in ("region", "entities", "poi") and "主世界" not in labels:
            labels.append("主世界")
        elif head == "DIM-1" and "下界" not in labels:
            labels.append("下界")
        elif head == "DIM1" and "末地" not in labels:
            labels.append("末地")
        elif head == "dimensions" and len(parts) >= 4:
            key = f"{parts[1]}:{parts[2]}"
            label = _DIMENSION_LABELS.get(key)
            if label:
                if label not in labels:
                    labels.append(label)
            elif key not in custom:
                custom.append(key)
    return labels + custom


def _players_of(relative_files: list[Path]) -> int:
    return sum(
        1
        for relative in relative_files
        if len(relative.parts) == 2
        and relative.parts[0] == "playerdata"
        and relative.suffix.lower() == ".dat"
    )


def available_dimensions(info: WorldSource) -> list[str]:
    labels = set(info.dimensions)
    return [
        key for key in DIMENSION_KEYS
        if DIMENSION_NAMES[key] in labels
    ]


@dataclass
class LocalWorldOption:
    """启动器里可以直接选择的存档来源。"""

    path: Path
    category: str
    source_label: str
    world_name: str
    version_name: str = ""
    dimensions: list[str] = field(default_factory=list)
    last_played: int = 0

    @property
    def label(self) -> str:
        prefix = "游戏存档" if self.category == "game" else "服务端"
        text = f"{prefix} · {self.source_label} · {self.world_name}"
        if self.dimensions:
            text += f"（{'/'.join(self.dimensions)}）"
        return text

    @property
    def detail(self) -> str:
        parts = [str(self.path)]
        if self.version_name:
            parts.append(self.version_name)
        if self.dimensions:
            parts.append("、".join(self.dimensions))
        return " · ".join(parts)


def world_dimension_labels(folder: Path) -> list[str]:
    """快速看目录判断一个世界有哪些维度，不遍历文件。"""
    labels: list[str] = []
    if (folder / "region").is_dir() or (
        folder / "dimensions" / "minecraft" / "overworld"
    ).is_dir():
        labels.append("主世界")
    if (
        (folder / "DIM-1").is_dir()
        or (folder / "dimensions" / "minecraft" / "the_nether").is_dir()
        or (folder.parent / f"{folder.name}_nether").is_dir()
    ):
        labels.append("下界")
    if (
        (folder / "DIM1").is_dir()
        or (folder / "dimensions" / "minecraft" / "the_end").is_dir()
        or (folder.parent / f"{folder.name}_the_end").is_dir()
    ):
        labels.append("末地")
    return labels


def scan_local_world_options(
    game_path: Path | str,
    *,
    version_names: dict[str, str] | None = None,
    exclude_server: str = "",
) -> list[LocalWorldOption]:
    """扫描启动器已下载版本的 saves，以及其他服务端的世界。"""
    from .server_config import read_server_properties
    from .servers import scan_servers
    from .versions import scan_versions
    from .worlds import load_world, scan_worlds

    root = Path(game_path)
    names = version_names or {}
    options: list[LocalWorldOption] = []
    seen: set[str] = set()

    def add(
        category: str,
        source_label: str,
        folder: Path,
        name: str = "",
        version_name: str = "",
        last_played: int = 0,
    ) -> None:
        if not (folder / "level.dat").is_file():
            return
        try:
            key = str(folder.resolve())
        except OSError:
            key = str(folder)
        if key in seen:
            return
        seen.add(key)
        try:
            info, _ = load_world(folder)
            name = name or info.name
            version_name = version_name or info.version_name
            last_played = last_played or info.last_played
        except Exception:
            name = name or folder.name
        options.append(
            LocalWorldOption(
                path=folder,
                category=category,
                source_label=source_label,
                world_name=name,
                version_name=version_name,
                dimensions=world_dimension_labels(folder),
                last_played=last_played,
            )
        )

    # 未开启版本隔离时的共享 saves，以及每个版本自己的工作目录。
    for world in scan_worlds(root):
        add(
            "game",
            "共享游戏目录",
            world.folder,
            world.name,
            world.version_name,
            world.last_played,
        )
    for summary in scan_versions(root):
        version_dir = root / "versions" / summary.id
        label = names.get(summary.id) or summary.id
        for world in scan_worlds(version_dir):
            add(
                "game",
                label,
                world.folder,
                world.name,
                world.version_name,
                world.last_played,
            )

    for info in scan_servers():
        if exclude_server and info.folder == exclude_server:
            continue
        properties = read_server_properties(info.path / "server.properties")
        level = safe_folder_name(properties.get("level-name") or "world")
        folder = info.path / level
        if not (folder / "level.dat").is_file():
            continue
        source_label = f"{info.name}（{info.type_name} {info.mc_version}）"
        add("server", source_label, folder)

    options.sort(
        key=lambda item: (
            0 if item.category == "game" else 1,
            -int(item.last_played or 0),
            item.label,
        )
    )
    return options


def _source_dimension_entry(
    relative: Path,
) -> tuple[str, tuple[str, ...]] | None:
    """Return ``(dimension, path_inside_dimension)`` for a source entry."""
    parts = relative.parts
    if not parts:
        return None
    if len(parts) >= 4 and parts[0] == "dimensions" and parts[1] == "minecraft":
        key = {
            "overworld": "overworld",
            "the_nether": "nether",
            "the_end": "end",
        }.get(parts[2])
        if key is not None:
            return key, parts[3:]
        return None
    if parts[0] == "DIM-1":
        return "nether", parts[1:]
    if parts[0] == "DIM1":
        return "end", parts[1:]
    if parts[0] in _OVERWORLD_DIRS:
        return "overworld", parts
    return None


def _is_common_relative(relative: Path) -> bool:
    parts = relative.parts
    if not parts or parts[0] == "dimensions":
        return False
    return _source_dimension_entry(relative) is None


def _target_layout(
    world: Path,
    level_name: str,
    target_version: str,
    server_kind: str,
) -> str:
    if (world / "dimensions" / "minecraft").is_dir():
        return "namespaced"
    if (
        (world.parent / f"{level_name}_nether").exists()
        or (world.parent / f"{level_name}_the_end").exists()
    ):
        return "split"
    if (world / "DIM-1").exists() or (world / "DIM1").exists():
        return "nested"
    if version_key(target_version) >= (26, 1, 0):
        return "namespaced"
    if server_kind in {"paper", "spigot"}:
        return "split"
    return "nested"


def _target_dimension_root(
    world: Path,
    level_name: str,
    layout: str,
    key: str,
) -> Path:
    if layout == "namespaced":
        return world.joinpath(*_NAMESPACED_PATHS[key])
    if layout == "split":
        if key == "overworld":
            return world
        suffix = "nether" if key == "nether" else "the_end"
        return (
            world.parent
            / f"{level_name}_{suffix}"
            / ("DIM-1" if key == "nether" else "DIM1")
        )
    if key == "overworld":
        return world
    return world / ("DIM-1" if key == "nether" else "DIM1")


def _target_backup_paths(
    world: Path,
    level_name: str,
    layout: str,
    key: str,
    source_heads: set[tuple[str, str]] | None = None,
) -> list[Path]:
    root = _target_dimension_root(world, level_name, layout, key)
    if source_heads is None:
        return [root]
    heads = [head for dim, head in source_heads if dim == key]
    # 只备份真正会被覆盖的子目录，避免把来源里没有的 entities/poi 挪走。
    return [root / head for head in dict.fromkeys(heads)]


def _source_entries(
    source: WorldSource,
) -> Iterator[tuple[Path, int, Path | str]]:
    if source.is_zip:
        with zipfile.ZipFile(source.path) as zf:
            for relative, info in _zip_entries(zf, source.root):
                yield relative, max(0, int(info.file_size)), info.filename
    else:
        for relative, path, size in _source_folder_entries(
            source.path, source.extra_sources
        ):
            yield relative, size, path


def _split_dimension_sources(
    root: Path,
) -> list[tuple[Path, tuple[str, ...]]]:
    """找出 Paper/Spigot 放在 world_nether、world_the_end 的下界和末地。"""
    result: list[tuple[Path, tuple[str, ...]]] = []
    for suffix, inner in (("_nether", "DIM-1"), ("_the_end", "DIM1")):
        sibling = root.parent / f"{root.name}{suffix}"
        if not sibling.is_dir():
            continue
        nested = sibling / inner
        actual = nested if nested.is_dir() else sibling
        if not any(
            (actual / name).is_dir() for name in ("region", "entities", "poi")
        ):
            continue
        result.append((actual, (inner,)))
    return result


def _source_folder_entries(
    root: Path,
    extra_sources: list[tuple[Path, tuple[str, ...]]],
) -> Iterator[tuple[Path, Path, int]]:
    for relative, path, size in _folder_entries(root):
        yield relative, path, size
    for actual, prefix in extra_sources:
        for relative, path, size in _folder_entries(actual):
            if not relative.parts:
                continue
            if relative.parts[0].lower() in {
                "level.dat",
                "session.lock",
                "paper-world.yml",
                "paper-world-defaults.yml",
            }:
                continue
            yield Path(*prefix, *relative.parts), path, size


def _backup_paths(
    paths: list[Path],
    stamp: str,
) -> list[tuple[Path, Path]]:
    moved: list[tuple[Path, Path]] = []
    try:
        for path in paths:
            if not path.exists():
                continue
            base = path.name[:48]
            candidate = path.with_name(f"{base}.bak-{stamp}")
            index = 2
            while candidate.exists():
                candidate = path.with_name(f"{base}.bak-{stamp}-{index}")
                index += 1
            path.rename(candidate)
            moved.append((path, candidate))
    except OSError as error:
        _restore_backups(moved)
        raise WorldImportError(
            f"目标维度正在被占用，无法备份：{error}。"
            "请先关闭正在使用这个存档的游戏或服务端。"
        ) from error
    return moved


def _restore_backups(moved: list[tuple[Path, Path]]) -> None:
    for original, backup in reversed(moved):
        if not backup.exists():
            continue
        if original.is_dir():
            shutil.rmtree(original, ignore_errors=True)
        elif original.exists():
            try:
                original.unlink()
            except OSError:
                pass
        try:
            backup.rename(original)
        except OSError:
            pass


def _remove_paths(paths: list[Path]) -> None:
    for path in paths:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists():
            try:
                path.unlink()
            except OSError:
                pass


def _copy_selected_dimensions(
    source: WorldSource,
    target_world: Path,
    roots: dict[str, Path],
    selected: list[str],
    include_common: bool,
    include_custom: bool,
    progress: ProgressCallback,
    cancel_event: threading.Event | None,
) -> tuple[int, int, int]:
    planned: list[tuple[str, tuple[str, ...], int, Path | str]] = []
    total = 0
    for relative, size, ref in _source_entries(source):
        classified = _source_dimension_entry(relative)
        if classified is not None:
            key, inner = classified
            if not inner:
                continue
            if key not in selected:
                continue
            if relative.name.lower() in {
                "paper-world.yml",
                "paper-world-defaults.yml",
            }:
                continue
            planned.append((key, inner, size, ref))
            total += size
        elif relative.parts and relative.parts[0] == "dimensions":
            # 自定义维度：三个维度全选时一并带走。
            if include_custom:
                planned.append(("custom", relative.parts, size, ref))
                total += size
        elif include_common and _is_common_relative(relative):
            planned.append(("common", relative.parts, size, ref))
            total += size
    if not planned:
        raise WorldImportError("没有找到可导入的维度数据")
    if total > MAX_TOTAL_BYTES:
        raise WorldImportError("选中的维度超过 256 GB，已停止导入")

    total = max(1, total)
    copied = 0
    written = 0
    last_report = 0.0
    zf = zipfile.ZipFile(source.path) if source.is_zip else None
    try:
        for key, inner, size, ref in planned:
            _check_cancel(cancel_event)
            root = roots.get(key, target_world)
            target = root.joinpath(*inner)
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                if zf is not None:
                    with zf.open(str(ref)) as reader, open(target, "wb") as writer:
                        while True:
                            chunk = reader.read(1024 * 1024)
                            if not chunk:
                                break
                            written += len(chunk)
                            if written > MAX_TOTAL_BYTES:
                                raise WorldImportError(
                                    "解压后超过 256 GB，已停止导入"
                                )
                            writer.write(chunk)
                else:
                    shutil.copy2(Path(ref), target)
            except (OSError, zipfile.BadZipFile) as error:
                raise WorldImportError(f"复制 {Path(*inner)} 失败：{error}") from error
            copied += 1
            if zf is None:
                written += size
            now = time.monotonic()
            if now - last_report > 0.12:
                last_report = now
                percent = 5 + int(90 * min(written, total) / total)
                label = DIMENSION_NAMES.get(key, "公共数据")
                progress(percent, f"正在导入{label} {Path(*inner)}")
    finally:
        if zf is not None:
            zf.close()
    return copied, written, total


def import_world_dimensions(
    source: WorldSource | Path | str,
    target_world: Path | str,
    *,
    dimensions: list[str],
    level_name: str = "",
    target_version: str = "",
    server_kind: str = "",
    progress_cb: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> WorldImportResult:
    """Import selected dimensions into an existing or new server world.

    Existing dimension folders are moved to ``.bak-时间戳`` before replacement.
    Common files are only copied when the target world does not exist yet.
    """
    progress = progress_cb or (lambda _p, _m: None)
    info = source if isinstance(source, WorldSource) else inspect_world_source(source)
    selected = [key for key in DIMENSION_KEYS if key in set(dimensions)]
    if not selected:
        raise WorldImportError("至少选择一个要导入的维度")
    invalid = [key for key in dimensions if key not in DIMENSION_KEYS]
    if invalid:
        raise WorldImportError(f"未知维度：{', '.join(invalid)}")
    missing = [key for key in selected if key not in available_dimensions(info)]
    if missing:
        names = "、".join(DIMENSION_NAMES[key] for key in missing)
        raise WorldImportError(f"来源存档里没有{names}数据")

    world = Path(target_world)
    name = safe_folder_name(level_name or world.name)
    include_custom = set(selected) == set(DIMENSION_KEYS)

    if not info.is_zip:
        source_root = info.path.resolve()
        target_root = world.resolve()
        if (
            target_root == source_root
            or source_root in target_root.parents
            or target_root in source_root.parents
        ):
            raise WorldImportError("不能把维度导入到它自己或它的子目录里")

    has_world = world.is_dir() and any(world.iterdir())
    if has_world and not (world / "level.dat").is_file():
        raise WorldImportError("目标世界缺少 level.dat，无法安全合并维度")
    new_world = not has_world

    progress(2, "检查目标世界布局…")
    layout = _target_layout(world, name, target_version, server_kind)
    roots = {
        key: _target_dimension_root(world, name, layout, key)
        for key in selected
    }
    roots["common"] = world

    backup_paths: list[Path] = []
    source_heads: set[tuple[str, str]] = set()
    for relative, _size, _ref in _source_entries(info):
        classified = _source_dimension_entry(relative)
        if classified is None:
            continue
        key, inner = classified
        if key in selected and inner:
            source_heads.add((key, inner[0]))
    for key in selected:
        backup_paths.extend(
            _target_backup_paths(world, name, layout, key, source_heads)
        )
    if include_custom:
        # 自定义维度整体放在 dimensions/<命名空间> 下，minecraft 子目录属于原版三维度，已单独处理。
        custom_root = world / "dimensions"
        if custom_root.is_dir():
            for child in sorted(custom_root.iterdir()):
                if child.is_dir() and child.name != "minecraft":
                    backup_paths.append(child)
    # Remove duplicates while preserving order.
    backup_paths = list(dict.fromkeys(backup_paths))

    moved: list[tuple[Path, Path]] = []
    created_world = False
    try:
        if new_world:
            world.mkdir(parents=True, exist_ok=True)
            created_world = True
            progress(4, "创建目标世界…")
        moved = _backup_paths(backup_paths, _now_stamp())
        if moved:
            progress(6, f"已备份 {len(moved)} 个旧维度")
        files, total, _ = _copy_selected_dimensions(
            info,
            world,
            roots,
            selected,
            include_common=new_world,
            include_custom=include_custom,
            progress=progress,
            cancel_event=cancel_event,
        )
        _check_cancel(cancel_event)
        if not (world / "level.dat").is_file():
            raise WorldImportError("导入完成后目标世界没有 level.dat")
    except BaseException as error:
        _remove_paths(backup_paths)
        if created_world and world.exists():
            shutil.rmtree(world, ignore_errors=True)
        _restore_backups(moved)
        if isinstance(error, WorldImportCancelled):
            raise
        if isinstance(error, WorldImportError):
            raise
        raise WorldImportError(f"导入维度失败：{error}") from error

    backups = [backup for _original, backup in moved]
    progress(100, f"已导入{'、'.join(DIMENSION_NAMES[key] for key in selected)}")
    return WorldImportResult(
        folder=world,
        backup=backups[0] if backups else None,
        source=info,
        files=files,
        total_bytes=total,
        level_name=name,
        backups=backups,
        dimensions=selected,
    )


def import_dimensions_into_server(
    info,
    source: WorldSource | Path | str,
    *,
    dimensions: list[str],
    level_name: str = "",
    progress_cb: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> WorldImportResult:
    from .servers import update_server_properties

    level = safe_folder_name(level_name or "world")
    result = import_world_dimensions(
        source,
        info.path / level,
        dimensions=dimensions,
        level_name=level,
        target_version=info.mc_version,
        server_kind=info.kind,
        progress_cb=progress_cb,
        cancel_event=cancel_event,
    )
    update_server_properties(info, {"level-name": level})
    return result


def _folder_root(path: Path) -> Path:
    if (path / "level.dat").is_file():
        return path
    candidates: list[Path] = []
    try:
        for child in path.iterdir():
            if (
                child.is_dir()
                and (child / "level.dat").is_file()
                and ".bak-" not in child.name
            ):
                candidates.append(child)
    except OSError as error:
        raise WorldImportError(f"无法读取文件夹：{error}") from error
    if not candidates:
        raise WorldImportError("这个文件夹里没有 level.dat，不是有效的世界存档")
    if len(candidates) > 1:
        names = "、".join(sorted(item.name for item in candidates)[:4])
        raise WorldImportError(
            f"这个文件夹里有多个存档（{names}），请直接选择其中一个世界文件夹"
        )
    return candidates[0]


def _zip_level_roots(zf: zipfile.ZipFile) -> list[str]:
    roots: list[str] = []
    for name in zf.namelist():
        text = name.replace("\\", "/").lstrip("/")
        lower = text.lower()
        if lower == "level.dat":
            roots.append("")
        elif lower.endswith("/level.dat"):
            roots.append(text[: -len("/level.dat")])
    return sorted(set(roots), key=lambda item: (len(PurePosixPath(item).parts), item))


def _zip_root(zf: zipfile.ZipFile) -> str:
    roots = _zip_level_roots(zf)
    if not roots:
        raise WorldImportError("压缩包里没有 level.dat，不是有效的世界存档")
    shortest = len(PurePosixPath(roots[0]).parts)
    tied = [item for item in roots if len(PurePosixPath(item).parts) == shortest]
    if len(tied) > 1:
        names = "、".join(
            (PurePosixPath(item).name or "根目录") for item in tied[:4]
        )
        raise WorldImportError(
            f"压缩包里有多个存档（{names}），请解压后直接选择其中一个世界文件夹"
        )
    return roots[0]


def _zip_relative(name: str, root: str) -> Path | None:
    text = name.replace("\\", "/").lstrip("/")
    if root:
        prefix = root.rstrip("/") + "/"
        if not text.startswith(prefix):
            return None
        text = text[len(prefix):]
    if not text or text.endswith("/"):
        return None
    if re.match(r"^[A-Za-z]:", text):
        return None
    relative = PurePosixPath(text)
    if relative.is_absolute():
        return None
    parts = [part for part in relative.parts if part not in ("", ".")]
    if not parts or any(part in ("..",) or ":" in part for part in parts):
        return None
    if parts[0] in _SKIP_DIRS:
        return None
    if _skip_name(parts[-1]):
        return None
    return Path(*parts)


def _folder_entries(root: Path) -> Iterator[tuple[Path, Path, int]]:
    for current, dirs, names in os.walk(root, followlinks=False):
        current_path = Path(current)
        dirs[:] = sorted(
            name
            for name in dirs
            if name not in _SKIP_DIRS
            and not (current_path / name).is_symlink()
        )
        for name in sorted(names):
            path = current_path / name
            if path.is_symlink() or not path.is_file() or _skip_name(name):
                continue
            try:
                relative = path.relative_to(root)
                size = path.stat().st_size
            except OSError:
                continue
            yield relative, path, max(0, int(size))


def _zip_entries(
    zf: zipfile.ZipFile, root: str
) -> Iterator[tuple[Path, zipfile.ZipInfo]]:
    for info in zf.infolist():
        if info.is_dir():
            continue
        relative = _zip_relative(info.filename, root)
        if relative is None:
            continue
        yield relative, info


# ---------------------------------------------------------------------------
# 检查来源


def inspect_world_source(path: Path | str) -> WorldSource:
    source_path = Path(path)
    if not source_path.exists():
        raise WorldImportError("选择的路径不存在")
    if source_path.is_dir():
        root = _folder_root(source_path)
        try:
            raw = (root / "level.dat").read_bytes()
        except OSError as error:
            raise WorldImportError(f"无法读取 level.dat：{error}") from error
        fields = _level_fields(_parse_level_bytes(raw), root.name)
        files = 0
        total = 0
        relatives: list[Path] = []
        extra_sources = _split_dimension_sources(root)
        for relative, _file, size in _source_folder_entries(root, extra_sources):
            files += 1
            total += size
            relatives.append(relative)
        _guard_totals(files, total)
        return WorldSource(
            path=root,
            is_zip=False,
            folder_name=root.name,
            name=fields["name"],
            version_name=fields["version_name"],
            data_version=fields["data_version"],
            seed=fields["seed"],
            files=files,
            total_bytes=total,
            dimensions=_dimensions_of(relatives),
            players=_players_of(relatives),
            extra_sources=extra_sources,
        )

    if not zipfile.is_zipfile(source_path):
        raise WorldImportError("请选择世界文件夹或 .zip 存档压缩包")
    try:
        with zipfile.ZipFile(source_path) as zf:
            root = _zip_root(zf)
            level_name = f"{root}/level.dat" if root else "level.dat"
            try:
                raw = zf.read(level_name)
            except KeyError as error:
                raise WorldImportError("压缩包里缺少 level.dat") from error
            fields = _level_fields(_parse_level_bytes(raw), PurePosixPath(root).name or source_path.stem)
            files = 0
            total = 0
            relatives = []
            for relative, info in _zip_entries(zf, root):
                files += 1
                total += max(0, int(info.file_size))
                relatives.append(relative)
            _guard_totals(files, total)
    except zipfile.BadZipFile as error:
        raise WorldImportError(f"压缩包已损坏：{error}") from error
    return WorldSource(
        path=source_path,
        is_zip=True,
        root=root,
        folder_name=safe_folder_name(
            PurePosixPath(root).name if root else source_path.stem
        ),
        name=fields["name"],
        version_name=fields["version_name"],
        data_version=fields["data_version"],
        seed=fields["seed"],
        files=files,
        total_bytes=total,
        dimensions=_dimensions_of(relatives),
        players=_players_of(relatives),
    )


def _guard_totals(files: int, total_bytes: int) -> None:
    if files > MAX_TOTAL_FILES:
        raise WorldImportError(f"存档文件数量过多（{files} 个），已停止导入")
    if total_bytes > MAX_TOTAL_BYTES:
        raise WorldImportError("存档体积超过 256 GB，已停止导入")


# ---------------------------------------------------------------------------
# 导入


def _backup_existing(folder: Path) -> Path:
    stamp = _now_stamp()
    base = folder.name[:48]
    candidate = folder.with_name(f"{base}.bak-{stamp}")
    index = 2
    while candidate.exists():
        candidate = folder.with_name(f"{base}.bak-{stamp}-{index}")
        index += 1
    try:
        folder.rename(candidate)
    except OSError as error:
        raise WorldImportError(
            f"目标同名世界正在被占用，无法备份：{error}。"
            "请先关闭正在使用这个存档的游戏或服务端。"
        ) from error
    return candidate


def _copy_folder(
    source: WorldSource,
    dest: Path,
    progress: ProgressCallback,
    cancel_event: threading.Event | None,
) -> tuple[int, int]:
    done = 0
    copied = 0
    last_report = 0.0
    total = max(1, source.total_bytes)
    for relative, path, size in _source_folder_entries(
        source.path, source.extra_sources
    ):
        _check_cancel(cancel_event)
        target = dest / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(path, target)
        except OSError as error:
            raise WorldImportError(f"复制 {relative} 失败：{error}") from error
        done += size
        copied += 1
        now = time.monotonic()
        if now - last_report > 0.12:
            last_report = now
            percent = 5 + int(93 * min(done, total) / total)
            progress(percent, f"正在复制 {relative}")
    return copied, done


def _copy_zip(
    source: WorldSource,
    dest: Path,
    progress: ProgressCallback,
    cancel_event: threading.Event | None,
) -> tuple[int, int]:
    copied = 0
    written = 0
    last_report = 0.0
    total = max(1, source.total_bytes)
    try:
        with zipfile.ZipFile(source.path) as zf:
            for relative, info in _zip_entries(zf, source.root):
                _check_cancel(cancel_event)
                target = dest / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with zf.open(info) as reader, open(target, "wb") as writer:
                        while True:
                            chunk = reader.read(1024 * 1024)
                            if not chunk:
                                break
                            written += len(chunk)
                            if written > MAX_TOTAL_BYTES:
                                raise WorldImportError("压缩包解压后超过 256 GB，已停止导入")
                            writer.write(chunk)
                except (OSError, zipfile.BadZipFile) as error:
                    raise WorldImportError(f"解压 {relative} 失败：{error}") from error
                copied += 1
                try:
                    actual = target.stat().st_size
                except OSError:
                    actual = 0
                now = time.monotonic()
                if now - last_report > 0.12:
                    last_report = now
                    percent = 5 + int(93 * min(written, total) / total)
                    progress(percent, f"正在解压 {relative}")
    except zipfile.BadZipFile as error:
        raise WorldImportError(f"压缩包已损坏：{error}") from error
    return copied, written


def import_world(
    source: WorldSource | Path | str,
    dest_parent: Path | str,
    *,
    folder_name: str = "",
    replace: bool = True,
    progress_cb: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> WorldImportResult:
    """把完整世界复制到 ``dest_parent/<folder_name>``。

    已存在的同名世界会先重命名为 ``.bak-时间戳``，不会被删除。
    """
    progress = progress_cb or (lambda _p, _m: None)
    info = source if isinstance(source, WorldSource) else inspect_world_source(source)
    parent = Path(dest_parent)
    parent.mkdir(parents=True, exist_ok=True)
    name = safe_folder_name(folder_name or info.folder_name or info.name)
    dest = parent / name
    backup: Path | None = None

    if not info.is_zip:
        source_root = info.path.resolve()
        dest_root = dest.resolve()
        if (
            dest_root == source_root
            or source_root in dest_root.parents
            or dest_root in source_root.parents
        ):
            raise WorldImportError("不能把存档导入到它自己或它的子目录里")

    progress(2, "检查目标位置…")
    if dest.exists():
        if not replace:
            raise WorldImportError(f"目标位置已有同名世界：{dest}")
        backup = _backup_existing(dest)
        progress(4, f"原世界已备份为 {backup.name}")

    try:
        dest.mkdir(parents=True)
        if info.is_zip:
            files, total = _copy_zip(info, dest, progress, cancel_event)
        else:
            files, total = _copy_folder(info, dest, progress, cancel_event)
        _check_cancel(cancel_event)
        if not (dest / "level.dat").is_file():
            raise WorldImportError("复制完成后没有找到 level.dat，导入未完成")
    except BaseException as error:
        shutil.rmtree(dest, ignore_errors=True)
        # 取消或失败时把原世界改回原名，避免用户突然找不到原来的世界。
        if backup is not None and backup.exists() and not dest.exists():
            try:
                backup.rename(dest)
                backup = None
            except OSError:
                pass
        if isinstance(error, WorldImportCancelled):
            raise
        message = str(error)
        if backup is not None:
            message = f"{message}。原世界已备份为 {backup.name}，可以手动改回原名。"
        if isinstance(error, WorldImportError):
            raise WorldImportError(message) from error
        raise WorldImportError(f"导入失败：{message}") from error

    progress(100, f"已导入「{info.name}」")
    return WorldImportResult(
        folder=dest,
        backup=backup,
        source=info,
        files=files,
        total_bytes=total,
        level_name=name,
    )


def import_into_server(
    info,
    source: WorldSource | Path | str,
    *,
    level_name: str = "",
    folder_name: str = "",
    progress_cb: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> WorldImportResult:
    from .servers import update_server_properties

    level = safe_folder_name(level_name or folder_name or "world")
    result = import_world(
        source,
        info.path,
        folder_name=level,
        progress_cb=progress_cb,
        cancel_event=cancel_event,
    )
    update_server_properties(info, {"level-name": level})
    return result


def import_into_game(
    game_dir: Path | str,
    source: WorldSource | Path | str,
    *,
    world_name: str = "",
    progress_cb: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> WorldImportResult:
    saves = Path(game_dir) / "saves"
    return import_world(
        source,
        saves,
        folder_name=world_name,
        progress_cb=progress_cb,
        cancel_event=cancel_event,
    )
