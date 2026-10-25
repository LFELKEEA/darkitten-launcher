from __future__ import annotations

import json
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from .installer import Installer
from .packs import human_size, normalize_version_id
from .paths import version_game_dir
from .versions import detect_loader, version_json_path
from .zip_extract import (
    DEFAULT_EXTRACT_THREADS,
    ExtractJob,
    ZipExtractCancelled,
    ZipExtractError,
    extract_zip_entries,
)

MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_TOTAL_FILES = 100_000
MAX_TOTAL_BYTES = 64 * 1024**3
CONTENT_DIRS = (
    "mods",
    "config",
    "defaultconfigs",
    "resourcepacks",
    "shaderpacks",
    "saves",
)
COMMON_DIRS = ("libraries", "assets")
_SKIP_SUFFIXES = (".part", ".tmp", ".download")
_SKIP_NAMES = {"desktop.ini", "thumbs.db", ".ds_store"}

ProgressCallback = Callable[[int, str], None]
LogCallback = Callable[[str], None]


class VersionZipError(Exception):
    pass


@dataclass
class VersionZipCandidate:
    version_id: str
    json_member: str
    json_data: dict[str, Any]
    prefix: str
    game_prefix: str
    layout: str
    json_rel: str
    total_files: int = 0
    total_bytes: int = 0
    mods: int = 0
    config: int = 0
    libraries: int = 0
    assets: int = 0
    jar_rel: str = ""

    @property
    def has_jar(self) -> bool:
        return bool(self.jar_rel)

    @property
    def minecraft_version(self) -> str:
        inherits = str(self.json_data.get("inheritsFrom") or "").strip()
        if inherits:
            return inherits
        jar = str(self.json_data.get("jar") or "").strip()
        return jar or self.version_id

    @property
    def loader(self) -> str:
        main_class = str(self.json_data.get("mainClass") or "")
        libraries = self.json_data.get("libraries") or []
        if not isinstance(libraries, list):
            libraries = []
        return detect_loader([self.version_id], main_class, libraries)

    @property
    def layout_label(self) -> str:
        return {
            "game_dir": "完整游戏目录",
            "folder": "版本文件夹",
            "flat": "版本文件平铺",
            "nested": "嵌套目录",
        }.get(self.layout, self.layout)


@dataclass
class VersionZipInfo:
    path: Path
    candidates: list[VersionZipCandidate]
    archive_bytes: int

    @property
    def primary(self) -> VersionZipCandidate:
        if not self.candidates:
            raise VersionZipError("压缩包里没有可导入的版本")
        return self.candidates[0]

    def find(self, json_member: str = "", version_id: str = "") -> VersionZipCandidate:
        if not json_member and not version_id:
            return self.primary
        for candidate in self.candidates:
            if json_member and candidate.json_member == json_member:
                return candidate
        for candidate in self.candidates:
            if version_id and candidate.version_id == version_id:
                return candidate
        raise VersionZipError("压缩包里找不到选中的版本")


@dataclass
class VersionZipImportResult:
    version_id: str
    imported_files: int
    skipped_files: int
    imported_bytes: int


def _safe_member(name: str) -> str:
    value = str(name or "").replace("\\", "/")
    if not value or value.startswith("/") or (len(value) >= 2 and value[1] == ":"):
        raise VersionZipError(f"压缩包内包含非法路径：{name}")
    parts = PurePosixPath(value).parts
    if any(part in ("", ".", "..") for part in parts):
        raise VersionZipError(f"压缩包内包含越界路径：{name}")
    return "/".join(parts)


def _skip_name(name: str) -> bool:
    lowered = name.lower()
    return (
        lowered in _SKIP_NAMES
        or lowered.endswith(_SKIP_SUFFIXES)
        or lowered.startswith("~")
    )


def _looks_like_version_json(data: dict[str, Any]) -> bool:
    if not isinstance(data.get("id"), str) or not data["id"].strip():
        return False
    markers = {
        "mainClass",
        "inheritsFrom",
        "libraries",
        "downloads",
        "arguments",
        "minecraftArguments",
        "assetIndex",
        "assets",
        "javaVersion",
    }
    return len(markers.intersection(data)) >= 3


def _json_in_content_folder(member: str) -> bool:
    """跳过 mods/config 等目录里的 JSON；版本目录本身的 JSON 不算。"""
    dirs = PurePosixPath(member).parts[:-1]
    if len(dirs) >= 2 and dirs[-2] == "versions":
        return False
    return any(
        part.lower() in CONTENT_DIRS + COMMON_DIRS for part in dirs
    )


def _read_json_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> dict[str, Any] | None:
    if info.file_size > MAX_JSON_BYTES:
        return None
    try:
        raw = zf.read(info)
        data = json.loads(raw.decode("utf-8-sig"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _candidate_from_json(
    member: str, data: dict[str, Any]
) -> VersionZipCandidate | None:
    parts = PurePosixPath(member).parts
    if not parts:
        return None
    if _json_in_content_folder(member):
        return None
    stem = parts[-1][:-5] if parts[-1].lower().endswith(".json") else parts[-1]
    parent = parts[-2] if len(parts) >= 2 else ""
    prefix = "/".join(parts[:-1])
    if prefix:
        prefix += "/"
    game_prefix = ""
    layout = "folder"
    if len(parts) >= 3 and parts[-3] == "versions":
        game_prefix = "/".join(parts[:-3])
        if game_prefix:
            game_prefix += "/"
        layout = "game_dir"
    elif len(parts) == 1:
        layout = "flat"
        prefix = ""
    elif len(parts) > 2:
        layout = "nested"
    version_id = str(data.get("id") or parent or stem).strip()
    if not version_id:
        return None
    return VersionZipCandidate(
        version_id=version_id,
        json_member=member,
        json_data=data,
        prefix=prefix,
        game_prefix=game_prefix,
        layout=layout,
        json_rel=member[len(prefix) :] if prefix else member,
    )


def _classify_member(
    member: str, candidate: VersionZipCandidate
) -> tuple[str, str] | None:
    """返回 (类别, 相对路径)。类别为 version / content / game。"""
    name = member
    if candidate.game_prefix:
        if not name.startswith(candidate.game_prefix):
            return None
        game_relative = name[len(candidate.game_prefix) :]
    else:
        game_relative = name

    if candidate.prefix and name.startswith(candidate.prefix):
        relative = name[len(candidate.prefix) :]
        if not relative:
            return None
        first = relative.split("/", 1)[0]
        if first in COMMON_DIRS:
            return "game", relative
        if first in CONTENT_DIRS:
            return "content", relative
        return "version", relative

    if candidate.layout == "flat":
        first = game_relative.split("/", 1)[0]
        if first in COMMON_DIRS:
            return "game", game_relative
        if first in CONTENT_DIRS:
            return "content", game_relative
        if first == "versions":
            return None
        return "version", game_relative

    first = game_relative.split("/", 1)[0]
    if first in COMMON_DIRS:
        return "game", game_relative
    if first in CONTENT_DIRS:
        return "content", game_relative
    return None


def _jar_candidates(
    zf: zipfile.ZipFile,
    candidate: VersionZipCandidate,
) -> str:
    expected = {
        f"{candidate.json_rel.rsplit('/', 1)[-1][:-5]}.jar",
        f"{candidate.version_id}.jar",
        "minecraft.jar",
    }
    direct: list[str] = []
    fallback: list[str] = []
    for info in zf.infolist():
        if info.is_dir():
            continue
        try:
            name = _safe_member(info.filename)
        except VersionZipError:
            continue
        classified = _classify_member(name, candidate)
        if not classified or classified[0] != "version":
            continue
        relative = classified[1]
        leaf = PurePosixPath(relative).name
        if not leaf.lower().endswith(".jar"):
            continue
        if leaf in expected and "/" not in relative:
            return relative
        if "/" not in relative:
            direct.append(relative)
        elif relative.lower() in ("bin/minecraft.jar", f"bin/{candidate.version_id}.jar"):
            fallback.append(relative)
    if direct:
        return direct[0]
    if fallback:
        return fallback[0]
    return ""


def _count_candidate(zf: zipfile.ZipFile, candidate: VersionZipCandidate) -> None:
    files = 0
    total = 0
    mods = config = libraries = assets = 0
    for info in zf.infolist():
        if info.is_dir():
            continue
        name = _safe_member(info.filename)
        if _skip_name(PurePosixPath(name).name):
            continue
        classified = _classify_member(name, candidate)
        if not classified:
            continue
        kind, relative = classified
        files += 1
        total += max(0, int(info.file_size))
        first = relative.split("/", 1)[0]
        if kind == "content":
            if first == "mods":
                mods += 1
            elif first in ("config", "defaultconfigs"):
                config += 1
        elif kind == "game":
            if first == "libraries":
                libraries += 1
            elif first == "assets":
                assets += 1
        elif first == "mods":
            mods += 1
        elif first in ("config", "defaultconfigs"):
            config += 1
    candidate.total_files = files
    candidate.total_bytes = total
    candidate.mods = mods
    candidate.config = config
    candidate.libraries = libraries
    candidate.assets = assets
    candidate.jar_rel = _jar_candidates(zf, candidate)


_INSPECT_CACHE: dict[tuple[str, int, int], VersionZipInfo] = {}
_INSPECT_CACHE_LOCK = threading.Lock()
_INSPECT_CACHE_MAX = 8


def inspect_version_zip(path: Path | str) -> VersionZipInfo:
    archive = Path(path)
    if not archive.is_file():
        raise VersionZipError("版本压缩包不存在")
    try:
        stat = archive.stat()
    except OSError as exc:
        raise VersionZipError(f"无法打开压缩包：{exc}") from exc
    key = (str(archive.resolve()), stat.st_mtime_ns, stat.st_size)
    with _INSPECT_CACHE_LOCK:
        cached = _INSPECT_CACHE.get(key)
    if cached is not None:
        return cached
    result = _inspect_version_zip(archive, stat.st_size)
    with _INSPECT_CACHE_LOCK:
        if len(_INSPECT_CACHE) >= _INSPECT_CACHE_MAX:
            _INSPECT_CACHE.pop(next(iter(_INSPECT_CACHE)))
        _INSPECT_CACHE[key] = result
    return result


def _inspect_version_zip(archive: Path, size: int) -> VersionZipInfo:
    try:
        with zipfile.ZipFile(archive) as zf:
            infos = zf.infolist()
            if len(infos) > MAX_TOTAL_FILES:
                raise VersionZipError("压缩包内文件数量过多，已拒绝读取")
            seen: set[str] = set()
            total = 0
            for info in infos:
                name = _safe_member(info.filename)
                if name in seen:
                    raise VersionZipError(f"压缩包内存在重复文件：{name}")
                seen.add(name)
                mode = (info.external_attr >> 16) & 0o170000
                if mode == 0o120000:
                    raise VersionZipError(f"压缩包内不允许符号链接：{name}")
                if info.is_dir():
                    continue
                if info.flag_bits & 0x1:
                    raise VersionZipError("压缩包中的文件被加密，无法导入")
                total += max(0, int(info.file_size))
                if total > MAX_TOTAL_BYTES:
                    raise VersionZipError("压缩包解压后体积超过 64 GB，已拒绝导入")
            candidates: list[VersionZipCandidate] = []
            for info in infos:
                if info.is_dir():
                    continue
                name = _safe_member(info.filename)
                if not name.lower().endswith(".json"):
                    continue
                if name in ("manifest.json", "version/version.json"):
                    continue
                if _json_in_content_folder(name):
                    continue
                data = _read_json_member(zf, info)
                if not data or not _looks_like_version_json(data):
                    continue
                candidate = _candidate_from_json(name, data)
                if candidate is not None:
                    candidates.append(candidate)
            if not candidates:
                raise VersionZipError(
                    "压缩包里没有找到 Minecraft 版本 JSON。"
                    "请选择包含 <版本名>.json 的版本包。"
                )
            deduped: dict[tuple[str, str], VersionZipCandidate] = {}
            for candidate in candidates:
                key = (candidate.version_id, candidate.prefix)
                old = deduped.get(key)
                if old is None or (
                    candidate.json_member.endswith(f"/{candidate.version_id}.json")
                    and not old.json_member.endswith(f"/{old.version_id}.json")
                ):
                    deduped[key] = candidate
            result = list(deduped.values())
            for candidate in result:
                _count_candidate(zf, candidate)
            result.sort(
                key=lambda item: (
                    0 if item.has_jar else 1,
                    0 if item.layout == "game_dir" else 1,
                    0
                    if item.json_member.endswith(f"/{item.version_id}.json")
                    else 1,
                    -item.total_files,
                    item.version_id.lower(),
                )
            )
    except VersionZipError:
        raise
    except zipfile.BadZipFile as exc:
        raise VersionZipError("文件不是有效的 ZIP 压缩包") from exc
    except OSError as exc:
        raise VersionZipError(f"无法打开压缩包：{exc}") from exc
    return VersionZipInfo(path=archive, candidates=result, archive_bytes=size)


def _destination(
    candidate: VersionZipCandidate,
    target_id: str,
    isolated: bool,
    kind: str,
    relative: str,
) -> str:
    run_prefix = f"versions/{target_id}" if isolated else ""
    if kind == "content":
        return f"{run_prefix}/{relative}" if run_prefix else relative
    if kind == "game":
        return relative
    if relative == candidate.json_rel:
        return f"versions/{target_id}/{target_id}.json"
    if candidate.jar_rel and relative == candidate.jar_rel:
        return f"versions/{target_id}/{target_id}.jar"
    return f"versions/{target_id}/{relative}"


def import_version_zip(
    zip_path: Path | str,
    game_dir: Path | str,
    version_id: str,
    *,
    candidate_json: str = "",
    overwrite: bool = False,
    resume: bool = False,
    isolated: bool = True,
    complete: bool = True,
    source: str = "official",
    threads: int = 8,
    installer: Any | None = None,
    progress_cb: ProgressCallback | None = None,
    log_cb: LogCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> VersionZipImportResult:
    game_dir = Path(game_dir)
    version_id = normalize_version_id(version_id)
    info = inspect_version_zip(zip_path)
    candidate = info.find(candidate_json)
    version_dir = game_dir / "versions" / version_id
    if version_dir.exists() and not resume:
        raise VersionZipError(f"版本 {version_id} 已经存在，请换一个导入名称")
    if resume and not version_json_path(game_dir, version_id).is_file():
        raise VersionZipError("上次导入的版本骨架不存在，请换一个导入名称重新开始")
    progress = progress_cb or (lambda _p, _m: None)
    log = log_cb or (lambda _m: None)
    cancel = cancel_event or threading.Event()
    run_dir = version_game_dir(game_dir, version_id, isolated)

    progress(2, "读取 ZIP 版本信息…")
    fresh_version_dir = not version_dir.exists()
    version_dir.mkdir(parents=True, exist_ok=True)
    total_bytes = max(1, candidate.total_bytes)
    json_destination = game_dir / "versions" / version_id / f"{version_id}.json"
    json_size = 0
    json_added = False
    skipped = 0
    added = 0
    added_bytes = 0

    jobs: list[ExtractJob] = []
    seen_destinations: dict[str, int] = {}
    with zipfile.ZipFile(Path(zip_path)) as zf:
        for entry in zf.infolist():
            if entry.is_dir():
                continue
            name = _safe_member(entry.filename)
            if _skip_name(PurePosixPath(name).name):
                continue
            classified = _classify_member(name, candidate)
            if not classified:
                continue
            kind, relative = classified
            destination = game_dir / _destination(
                candidate, version_id, isolated, kind, relative
            )
            try:
                destination.resolve().relative_to(game_dir.resolve())
            except ValueError as exc:
                raise VersionZipError(f"压缩包路径越界：{name}") from exc
            if name == candidate.json_member:
                json_size = max(0, int(entry.file_size))
                continue
            job = ExtractJob(
                entry,
                destination,
                relative,
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

    if json_destination.exists() and not overwrite:
        skipped += 1
    else:
        data = dict(candidate.json_data)
        data["id"] = version_id
        if str(data.get("jar") or "") == candidate.version_id:
            data["jar"] = version_id
        json_destination.parent.mkdir(parents=True, exist_ok=True)
        json_destination.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        json_added = True
        added += 1
        added_bytes += json_size

    def extraction_progress(done_bytes: int, _files: int, label: str) -> None:
        if cancel.is_set():
            return
        progress(
            min(55, 5 + int(50 * (done_bytes + json_size) / total_bytes)),
            f"放入 {label}" if label else "放入版本文件…",
        )

    extract_threads = max(
        1, min(DEFAULT_EXTRACT_THREADS, int(threads or DEFAULT_EXTRACT_THREADS))
    )
    try:
        stats = extract_zip_entries(
            zip_path,
            jobs,
            threads=extract_threads,
            overwrite=overwrite,
            cancel_event=cancel,
            progress_cb=extraction_progress,
        )
    except ZipExtractCancelled as exc:
        raise VersionZipError("导入已取消") from exc
    except ZipExtractError as exc:
        raise VersionZipError(f"解压失败：{exc}") from exc
    if cancel.is_set():
        raise VersionZipError("导入已取消")
    added += stats.added
    skipped += stats.skipped
    added_bytes += stats.bytes_written

    if not version_json_path(game_dir, version_id).is_file():
        raise VersionZipError("压缩包里缺少版本 JSON，无法完成导入")
    log(
        f"已解压 {added} 个文件"
        + (f"，跳过 {skipped} 个已存在文件" if skipped else "")
    )

    if complete:
        progress(56, "补齐原版、依赖库与资源文件…")

        def mapped_progress(percent: int, message: str) -> None:
            if cancel.is_set():
                raise VersionZipError("导入已取消")
            progress(56 + int(max(0, min(100, percent)) * 0.43), message)

        if installer is None:
            installer = Installer(
                game_dir,
                source=source,
                progress_cb=mapped_progress,
                log_cb=log,
                cancel_event=cancel,
                threads=threads,
            )
        installer.install(version_id)
    if cancel.is_set():
        raise VersionZipError("导入已取消")

    progress(100, "版本已经放进猫窝")
    log(f"ZIP 版本导入完成：{version_id} · {human_size(added_bytes)}")
    return VersionZipImportResult(
        version_id=version_id,
        imported_files=added,
        skipped_files=skipped,
        imported_bytes=added_bytes,
    )
