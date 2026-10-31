from __future__ import annotations

import json
import shutil
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

SNAPSHOT_FORMAT = 1
SNAPSHOT_DIR_NAME = ".dkl-backups"
SNAPSHOT_OPTION_FILES = (
    "options.txt",
    "optionsof.txt",
    "optionsshaders.txt",
    "servers.dat",
    "servers.dat_old",
)
SNAPSHOT_DIRS = ("saves", "config", "defaultconfigs")
SKIP_NAMES = {"session.lock"}
MAX_ENTRY_BYTES = 4 * 1024 * 1024 * 1024

LogCallback = Callable[[str], None]


class SnapshotError(Exception):
    pass


@dataclass
class SnapshotInfo:
    path: Path
    created_at: float
    version_id: str
    files: int
    bytes: int
    note: str = ""
    skipped: int = 0

    @property
    def created_text(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.created_at))

    @property
    def size_text(self) -> str:
        size = float(self.bytes)
        if size >= 1024 * 1024 * 1024:
            return f"{size / 1024 / 1024 / 1024:.2f} GB"
        if size >= 1024 * 1024:
            return f"{size / 1024 / 1024:.1f} MB"
        if size >= 1024:
            return f"{size / 1024:.0f} KB"
        return f"{int(size)} B"


def snapshot_dir(game_path: Path) -> Path:
    return Path(game_path) / SNAPSHOT_DIR_NAME / "snapshots"


def _iter_sources(game_path: Path):
    game_path = Path(game_path)
    for name in SNAPSHOT_OPTION_FILES:
        path = game_path / name
        if path.is_file():
            yield path
    for name in SNAPSHOT_DIRS:
        root = game_path / name
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.is_file():
                yield path


def _relative(path: Path, game_path: Path) -> str:
    return path.relative_to(game_path).as_posix()


def _read_meta(archive: zipfile.ZipFile) -> dict:
    try:
        raw = archive.read("snapshot.json")
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except (KeyError, OSError, ValueError, UnicodeDecodeError):
        return {}


def create_snapshot(
    game_path: Path,
    version_id: str = "",
    note: str = "",
    log: LogCallback | None = None,
) -> SnapshotInfo:
    """把当前版本的世界、配置和选项文件打包成 ZIP 快照。"""
    game_path = Path(game_path)
    if not game_path.is_dir():
        raise SnapshotError(f"游戏目录不存在：{game_path}")

    files = list(_iter_sources(game_path))
    if not files:
        raise SnapshotError("没有找到可备份的世界或配置文件")

    root = snapshot_dir(game_path)
    root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    safe_version = "".join(
        char if char.isalnum() or char in "-_." else "_" for char in version_id
    )[:48]
    base = f"{stamp}-{safe_version}" if safe_version else stamp
    path = root / f"{base}.zip"
    index = 1
    while path.exists():
        path = root / f"{base}-{index}.zip"
        index += 1

    created_at = time.time()
    total_bytes = 0
    written = 0
    skipped: list[str] = []
    temp = path.with_name(path.name + ".tmp")
    try:
        with zipfile.ZipFile(
            temp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as archive:
            for file_path in files:
                name = _relative(file_path, game_path)
                if file_path.name in SKIP_NAMES:
                    continue
                try:
                    archive.write(file_path, name)
                    total_bytes += file_path.stat().st_size
                    written += 1
                except OSError:
                    skipped.append(name)
                    if log:
                        log(f"跳过被占用的文件：{name}")
            meta = {
                "format": SNAPSHOT_FORMAT,
                "version_id": version_id,
                "created_at": created_at,
                "game_path": str(game_path),
                "files": written,
                "bytes": total_bytes,
                "note": note,
                "skipped": skipped[:50],
            }
            archive.writestr(
                "snapshot.json",
                json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"),
            )
        temp.replace(path)
    except Exception:
        try:
            temp.unlink()
        except OSError:
            pass
        raise

    if log:
        log(f"快照已创建：{path.name}（{written} 个文件，{total_bytes} 字节）")
    return SnapshotInfo(
        path=path,
        created_at=created_at,
        version_id=version_id,
        files=written,
        bytes=total_bytes,
        note=note,
        skipped=len(skipped),
    )


def read_snapshot_info(path: Path) -> SnapshotInfo:
    path = Path(path)
    try:
        with zipfile.ZipFile(path) as archive:
            meta = _read_meta(archive)
    except (OSError, zipfile.BadZipFile):
        meta = {}
    try:
        stat = path.stat()
        fallback_time = stat.st_mtime
        fallback_size = stat.st_size
    except OSError:
        fallback_time = 0.0
        fallback_size = 0
    return SnapshotInfo(
        path=path,
        created_at=float(meta.get("created_at") or fallback_time),
        version_id=str(meta.get("version_id") or ""),
        files=int(meta.get("files") or 0),
        bytes=int(meta.get("bytes") or fallback_size),
        note=str(meta.get("note") or ""),
        skipped=len(meta.get("skipped") or []),
    )


def list_snapshots(game_path: Path) -> list[SnapshotInfo]:
    root = snapshot_dir(game_path)
    if not root.is_dir():
        return []
    result = [read_snapshot_info(path) for path in root.glob("*.zip")]
    result.sort(key=lambda item: item.created_at, reverse=True)
    return result


def _safe_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    safe: list[zipfile.ZipInfo] = []
    for info in archive.infolist():
        name = info.filename
        if not name or name.endswith("/") or name == "snapshot.json":
            continue
        pure = PurePosixPath(name)
        if pure.is_absolute() or ".." in pure.parts or ":" in pure.parts[0]:
            raise SnapshotError(f"快照包含不安全路径：{name}")
        if info.file_size > MAX_ENTRY_BYTES:
            raise SnapshotError(f"快照文件过大，已中止：{name}")
        safe.append(info)
    return safe


def restore_snapshot(
    archive_path: Path,
    game_path: Path,
    log: LogCallback | None = None,
) -> int:
    """恢复快照；覆盖同名文件，但不会删除快照之外的新文件。"""
    archive_path = Path(archive_path)
    game_path = Path(game_path)
    if not archive_path.is_file():
        raise SnapshotError("快照文件不存在")
    if not game_path.is_dir():
        raise SnapshotError(f"游戏目录不存在：{game_path}")

    restored = 0
    with zipfile.ZipFile(archive_path) as archive:
        members = _safe_members(archive)
        for info in members:
            target = game_path / PurePosixPath(info.filename)
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with archive.open(info) as source, target.open("wb") as dest:
                    shutil.copyfileobj(source, dest)
                restored += 1
            except OSError as exc:
                raise SnapshotError(f"恢复 {info.filename} 失败：{exc}") from exc
    if log:
        log(f"已从 {archive_path.name} 恢复 {restored} 个文件")
    return restored
