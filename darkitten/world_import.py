"""把其他游戏版本的完整存档导入服务端或启动器存档目录。

导入只做文件复制：level.dat、region、DIM-1、DIM1、dimensions、playerdata、
stats、advancements、data、datapacks 等都原样搬过去，不重写任何 NBT。
因此玩家数据、物品栏、末影箱、进度和各个维度都会保留；如果目标版本更新，
Minecraft 会在首次打开世界时自己完成跨版本升级。

目标位置已有同名世界时不会删除，而是重命名为 ``<名字>.bak-时间戳`` 保留。
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

    @property
    def size_text(self) -> str:
        return human_size(self.total_bytes)


# ---------------------------------------------------------------------------
# 通用工具


def _check_cancel(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise WorldImportCancelled()


def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _skip_name(name: str) -> bool:
    return name.lower() in _SKIP_NAMES


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
        for relative, _file, size in _folder_entries(root):
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
    for relative, path, size in _folder_entries(source.path):
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
