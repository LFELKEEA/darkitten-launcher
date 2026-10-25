from __future__ import annotations

import json
import os
import re
import threading
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from .config import APP_NAME, APP_VERSION
from .installer import Installer
from .paths import version_game_dir
from .versions import resolve_version, version_json_path
from .zip_extract import (
    DEFAULT_EXTRACT_THREADS,
    ExtractJob,
    ZipExtractCancelled,
    ZipExtractError,
    extract_zip_entries,
)

PACK_FORMAT = "dklpack"
PACK_FORMAT_VERSION = 1
PACK_EXTENSION = ".dklpack"
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_TOTAL_FILES = 100_000
MAX_TOTAL_BYTES = 64 * 1024**3

CONTENT_LABELS = {
    "mods": "Mod",
    "config": "配置",
    "defaultconfigs": "默认配置",
    "resourcepacks": "资源包",
    "shaderpacks": "光影包",
    "saves": "存档",
}
CONTENT_DIRS = tuple(CONTENT_LABELS)
ALWAYS_INCLUDED = ("mods",)
DEFAULT_OPTIONS = {
    "config": True,
    "defaultconfigs": True,
    "resourcepacks": False,
    "shaderpacks": False,
    "saves": False,
}
_SKIP_SUFFIXES = (".part", ".tmp", ".download")
_SKIP_NAMES = {"desktop.ini", "thumbs.db", ".ds_store"}
_STORE_SUFFIXES = {
    ".jar",
    ".zip",
    ".png",
    ".jpg",
    ".jpeg",
    ".ogg",
    ".wav",
    ".mp3",
    ".7z",
}

ProgressCallback = Callable[[int, str], None]
LogCallback = Callable[[str], None]


class PackError(Exception):
    pass


@dataclass
class PackOptions:
    config: bool = DEFAULT_OPTIONS["config"]
    defaultconfigs: bool = DEFAULT_OPTIONS["defaultconfigs"]
    resourcepacks: bool = DEFAULT_OPTIONS["resourcepacks"]
    shaderpacks: bool = DEFAULT_OPTIONS["shaderpacks"]
    saves: bool = DEFAULT_OPTIONS["saves"]

    def enabled_dirs(self) -> list[str]:
        dirs = list(ALWAYS_INCLUDED)
        for key in ("config", "defaultconfigs", "resourcepacks", "shaderpacks", "saves"):
            if bool(getattr(self, key)):
                dirs.append(key)
        return dirs


@dataclass
class PackInfo:
    path: Path
    manifest: dict[str, Any]
    version_json: dict[str, Any]
    counts: dict[str, int]
    total_files: int
    total_bytes: int
    archive_bytes: int

    @property
    def name(self) -> str:
        return str(self.manifest.get("name") or self.version_id or "DKL 整合包")

    @property
    def version_id(self) -> str:
        return str(self.manifest.get("version_id") or "")

    @property
    def minecraft_version(self) -> str:
        return str(self.manifest.get("minecraft_version") or "")

    @property
    def loader(self) -> str:
        return str(self.manifest.get("loader") or "")

    @property
    def loader_version(self) -> str:
        return str(self.manifest.get("loader_version") or "")

    @property
    def description(self) -> str:
        return str(self.manifest.get("description") or "")

    @property
    def created_at(self) -> str:
        return str(self.manifest.get("created_at") or "")

    @property
    def created_with(self) -> str:
        return str(self.manifest.get("created_with") or "")



@dataclass
class PackImportResult:
    version_id: str
    imported_files: int
    skipped_files: int
    imported_bytes: int


def human_size(value: int) -> str:
    size = float(max(0, int(value)))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def normalize_version_id(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    if not text or text in (".", ".."):
        text = "DKL-Pack"
    return text[:64].rstrip(" .") or "DKL-Pack"


def unique_version_id(game_dir: Path | str, base: str) -> str:
    versions = Path(game_dir) / "versions"
    candidate = normalize_version_id(base)
    if not (versions / candidate).exists() and not version_json_path(
        Path(game_dir), candidate
    ).exists():
        return candidate
    index = 2
    while True:
        suffix = f"-{index}"
        trimmed = candidate[: 64 - len(suffix)].rstrip(" .") or "DKL-Pack"
        value = trimmed + suffix
        if not (versions / value).exists() and not version_json_path(
            Path(game_dir), value
        ).exists():
            return value
        index += 1
        if index > 9999:
            raise PackError("无法生成不重复的版本名称")


def _loader_version(version_id: str, loader: str) -> str:
    text = str(version_id)
    lowered = text.lower()
    if loader == "NeoForge":
        for marker in ("-neoforge-", "-forge-"):
            if marker in lowered:
                return text[lowered.index(marker) + len(marker):]
    if loader == "Forge" and "-forge-" in lowered:
        return text[lowered.index("-forge-") + len("-forge-"):]
    if loader in ("Fabric", "Quilt"):
        match = re.search(r"(?:fabric|quilt)-loader-([^/]+)", text, re.I)
        if match:
            value = match.group(1)
            return re.sub(r"-\d+\.\d+(?:\.\d+)?$", "", value) or value
    return ""


def _skip_name(name: str) -> bool:
    lowered = name.lower()
    return (
        lowered in _SKIP_NAMES
        or lowered.endswith(_SKIP_SUFFIXES)
        or lowered.startswith("~")
    )


def _iter_tree(root: Path, relative: str) -> list[tuple[Path, str]]:
    base = root / relative
    if not base.is_dir():
        return []
    files: list[tuple[Path, str]] = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(
            name for name in dirnames if not (Path(dirpath) / name).is_symlink()
        )
        for filename in sorted(filenames):
            if _skip_name(filename):
                continue
            path = Path(dirpath) / filename
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                resolved = path.resolve()
                resolved.relative_to(base.resolve())
            except (OSError, ValueError):
                continue
            files.append((path, path.relative_to(root).as_posix()))
    return files


def _read_json_member(zf: zipfile.ZipFile, name: str, label: str) -> dict[str, Any]:
    try:
        info = zf.getinfo(name)
    except KeyError as exc:
        raise PackError(f"整合包缺少 {label}") from exc
    if info.file_size > MAX_MANIFEST_BYTES:
        raise PackError(f"{label} 过大，已拒绝读取")
    if info.flag_bits & 0x1:
        raise PackError("整合包中的文件被加密，无法读取")
    try:
        raw = zf.read(info)
    except Exception as exc:
        raise PackError(f"读取 {label} 失败：{exc}") from exc
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except Exception as exc:
        raise PackError(f"{label} 不是有效的 JSON") from exc
    if not isinstance(data, dict):
        raise PackError(f"{label} 格式不正确")
    return data


def _safe_archive_name(name: str) -> str:
    value = str(name or "").replace("\\", "/")
    if not value or value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise PackError(f"整合包内包含非法路径：{name}")
    parts = PurePosixPath(value).parts
    if any(part in ("", ".", "..") for part in parts):
        raise PackError(f"整合包内包含越界路径：{name}")
    return "/".join(parts)


def _allowed_content_name(name: str) -> bool:
    parts = PurePosixPath(name).parts
    return bool(parts) and parts[0] in CONTENT_DIRS and len(parts) >= 2


def _validate_archive(zf: zipfile.ZipFile) -> dict[str, Any]:
    if zf.infolist() and len(zf.infolist()) > MAX_TOTAL_FILES + 16:
        raise PackError("整合包内文件数量过多，已拒绝读取")
    seen: set[str] = set()
    total = 0
    counts = {name: 0 for name in CONTENT_DIRS}
    for info in zf.infolist():
        name = _safe_archive_name(info.filename)
        if name in seen:
            raise PackError(f"整合包内存在重复文件：{name}")
        seen.add(name)
        mode = (info.external_attr >> 16) & 0o170000
        if mode == 0o120000:
            raise PackError(f"整合包内不允许符号链接：{name}")
        if info.is_dir():
            continue
        if info.flag_bits & 0x1:
            raise PackError("整合包中的文件被加密，无法导入")
        total += max(0, int(info.file_size))
        if total > MAX_TOTAL_BYTES:
            raise PackError("整合包解压后体积超过 64 GB，已拒绝导入")
        if _allowed_content_name(name):
            counts[name.split("/", 1)[0]] += 1
    if "manifest.json" not in seen:
        raise PackError("这不是 DKL 整合包：缺少 manifest.json")
    if "version/version.json" not in seen:
        raise PackError("整合包缺少版本信息 version/version.json")
    return {"counts": counts, "total": total}


def inspect_pack(path: Path | str) -> PackInfo:
    pack_path = Path(path)
    if not pack_path.is_file():
        raise PackError("整合包文件不存在")
    try:
        with zipfile.ZipFile(pack_path) as zf:
            validation = _validate_archive(zf)
            manifest = _read_json_member(zf, "manifest.json", "manifest.json")
            version_json = _read_json_member(
                zf, "version/version.json", "version/version.json"
            )
    except PackError:
        raise
    except zipfile.BadZipFile as exc:
        raise PackError("文件不是有效的 DKL 整合包（ZIP 结构损坏）") from exc
    except OSError as exc:
        raise PackError(f"无法打开整合包：{exc}") from exc

    if str(manifest.get("format") or "") != PACK_FORMAT:
        raise PackError(
            "这不是 Darkitten Launcher 整合包。请选择由 DKL 导出的 .dklpack 文件。"
        )
    try:
        format_version = int(manifest.get("format_version") or 0)
    except Exception:
        format_version = 0
    if format_version <= 0 or format_version > PACK_FORMAT_VERSION:
        raise PackError(
            f"整合包格式版本 {format_version or '未知'} 不受支持，请更新启动器。"
        )

    version_id = str(manifest.get("version_id") or "").strip()
    if not version_id:
        raise PackError("整合包没有记录原版本名称")
    if "id" not in version_json:
        version_json["id"] = version_id
    return PackInfo(
        path=pack_path,
        manifest=manifest,
        version_json=version_json,
        counts=validation["counts"],
        total_files=int(manifest.get("total_files") or 0)
        or sum(validation["counts"].values()) + 1,
        total_bytes=int(manifest.get("total_bytes") or 0) or validation["total"],
        archive_bytes=pack_path.stat().st_size,
    )


def export_pack(
    game_dir: Path | str,
    version_id: str,
    output_path: Path | str,
    *,
    name: str = "",
    description: str = "",
    options: PackOptions | None = None,
    isolated: bool = True,
    progress_cb: ProgressCallback | None = None,
    log_cb: LogCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    game_dir = Path(game_dir)
    output_path = Path(output_path)
    if output_path.suffix.lower() != PACK_EXTENSION:
        output_path = output_path.with_suffix(PACK_EXTENSION)
    progress = progress_cb or (lambda _p, _m: None)
    log = log_cb or (lambda _m: None)
    cancel = cancel_event or threading.Event()
    options = options or PackOptions()

    json_path = version_json_path(game_dir, version_id)
    if not json_path.is_file():
        raise PackError(f"找不到版本 {version_id} 的 JSON 文件")
    try:
        version_json = json.loads(json_path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise PackError(f"版本 JSON 无法读取：{exc}") from exc
    if not isinstance(version_json, dict):
        raise PackError("版本 JSON 格式不正确")

    try:
        resolved = resolve_version(game_dir, version_id)
    except Exception as exc:
        raise PackError(f"版本无法解析：{exc}") from exc

    run_dir = version_game_dir(game_dir, version_id, isolated)
    enabled = options.enabled_dirs()
    collected: list[tuple[str, list[tuple[Path, str]]]] = []
    counts: dict[str, int] = {}
    total_bytes = 0
    for content in enabled:
        files = _iter_tree(run_dir, content)
        collected.append((content, files))
        counts[content] = len(files)
        total_bytes += sum(path.stat().st_size for path, _arc in files)
        if files:
            log(f"{content}：{len(files)} 个文件")

    json_bytes = json.dumps(version_json, ensure_ascii=False, indent=2).encode("utf-8")
    total_files = sum(counts.values()) + 1
    total_bytes += len(json_bytes)

    manifest = {
        "format": PACK_FORMAT,
        "format_version": PACK_FORMAT_VERSION,
        "name": str(name or f"{version_id} 的猫猫整合包").strip()[:80],
        "description": str(description or "").strip()[:500],
        "version_id": version_id,
        "minecraft_version": resolved.jar_id,
        "loader": resolved.loader,
        "loader_version": _loader_version(version_id, resolved.loader),
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "created_with": f"{APP_NAME} {APP_VERSION}",
        "contents": enabled,
        "counts": counts,
        "total_files": total_files,
        "total_bytes": total_bytes,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(
        f".{output_path.name}.{uuid.uuid4().hex[:8]}.part"
    )
    done = 1
    try:
        with zipfile.ZipFile(
            temp_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as zf:
            progress(1, "写入整合包清单…")
            zf.writestr(
                "manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2),
            )
            zf.writestr(
                "version/version.json",
                json_bytes,
                compress_type=zipfile.ZIP_DEFLATED,
            )
            for content, files in collected:
                for path, arcname in files:
                    if cancel.is_set():
                        raise PackError("打包已取消")
                    progress(
                        min(99, int(2 + 97 * done / max(1, total_files))),
                        f"打包 {arcname}",
                    )
                    compress = (
                        zipfile.ZIP_STORED
                        if path.suffix.lower() in _STORE_SUFFIXES
                        else zipfile.ZIP_DEFLATED
                    )
                    zf.write(path, arcname, compress_type=compress)
                    done += 1
        if cancel.is_set():
            raise PackError("打包已取消")
        os.replace(temp_path, output_path)
    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise
    progress(100, f"整合包已保存（{human_size(output_path.stat().st_size)}）")
    log(
        f"整合包已保存：{output_path} · {total_files} 个文件 · "
        f"{human_size(total_bytes)}"
    )
    return output_path


def _write_version_json(
    game_dir: Path, version_id: str, version_json: dict[str, Any]
) -> Path:
    path = version_json_path(game_dir, version_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = dict(version_json)
    data["id"] = version_id
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def import_pack(
    pack_path: Path | str,
    game_dir: Path | str,
    version_id: str,
    *,
    overwrite: bool = False,
    resume: bool = False,
    isolated: bool = True,
    source: str = "official",
    threads: int = 8,
    installer: Any | None = None,
    progress_cb: ProgressCallback | None = None,
    log_cb: LogCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> PackImportResult:
    game_dir = Path(game_dir)
    info = inspect_pack(pack_path)
    version_id = normalize_version_id(version_id)
    version_dir = game_dir / "versions" / version_id
    if version_dir.exists() and not resume:
        raise PackError(f"版本 {version_id} 已经存在，请换一个导入名称")
    if resume and not version_json_path(game_dir, version_id).is_file():
        raise PackError("上次导入的版本骨架不存在，请换一个导入名称重新开始")
    progress = progress_cb or (lambda _p, _m: None)
    log = log_cb or (lambda _m: None)
    cancel = cancel_event or threading.Event()

    progress(2, "解包版本信息…")
    fresh_version_dir = not version_dir.exists()
    _write_version_json(game_dir, version_id, info.version_json)
    log(f"已写入版本骨架：{version_id}")

    run_dir = version_game_dir(game_dir, version_id, isolated)
    run_dir.mkdir(parents=True, exist_ok=True)
    jobs: list[ExtractJob] = []
    seen_destinations: dict[str, int] = {}
    total = 1
    with zipfile.ZipFile(Path(pack_path)) as zf:
        for entry in zf.infolist():
            if entry.is_dir():
                continue
            name = entry.filename.replace("\\", "/")
            if not _allowed_content_name(name):
                continue
            safe_name = _safe_archive_name(entry.filename)
            destination = run_dir / safe_name
            try:
                destination.resolve().relative_to(run_dir.resolve())
            except ValueError as exc:
                raise PackError(f"整合包路径越界：{safe_name}") from exc
            job = ExtractJob(
                entry,
                destination,
                safe_name,
                check_exists=not (
                    fresh_version_dir
                    and isolated
                    and version_dir in destination.parents
                ),
            )
            key = str(destination).lower()
            if key in seen_destinations:
                jobs[seen_destinations[key]] = job
            else:
                seen_destinations[key] = len(jobs)
                jobs.append(job)
        total = max(
            1, sum(max(0, int(entry.info.file_size)) for entry in jobs)
        )

    def content_progress(done_bytes: int, _files: int, label: str) -> None:
        if cancel.is_set():
            return
        progress(
            min(66, 5 + int(60 * done_bytes / total)),
            f"放入 {label}" if label else "放入整合包内容…",
        )

    try:
        stats = extract_zip_entries(
            pack_path,
            jobs,
            threads=max(
                1,
                min(DEFAULT_EXTRACT_THREADS, int(threads or DEFAULT_EXTRACT_THREADS)),
            ),
            overwrite=overwrite,
            cancel_event=cancel,
            progress_cb=content_progress,
        )
    except ZipExtractCancelled as exc:
        raise PackError("导入已取消") from exc
    except ZipExtractError as exc:
        raise PackError(f"解压失败：{exc}") from exc
    if cancel.is_set():
        raise PackError("导入已取消")
    imported = stats.added
    skipped = stats.skipped
    imported_bytes = stats.bytes_written
    log(
        f"整合包内容已放入版本文件夹：新增 {imported} 个文件"
        + (f"，跳过 {skipped} 个已存在文件" if skipped else "")
    )

    def mapped_progress(percent: int, message: str) -> None:
        if cancel.is_set():
            raise PackError("导入已取消")
        progress(67 + int(max(0, min(100, percent)) * 0.32), message)

    if installer is None:
        installer = Installer(
            game_dir,
            source=source,
            progress_cb=mapped_progress,
            log_cb=log,
            cancel_event=cancel,
            threads=threads,
        )
    progress(67, "补齐原版、依赖库与资源文件…")
    installer.install(version_id)
    if cancel.is_set():
        raise PackError("导入已取消")

    progress(100, "猫猫已经把整合包搬进猫窝")
    log(
        f"导入完成：{version_id} · 新增 {imported} 个文件"
        + (f" · 跳过 {skipped} 个已存在文件" if skipped else "")
    )
    return PackImportResult(
        version_id=version_id,
        imported_files=imported,
        skipped_files=skipped,
        imported_bytes=imported_bytes,
    )
