from __future__ import annotations

import json
import re
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from .curseforge import candidate_download_urls, fetch_file
from .downloader import (
    BatchDownload,
    download_batch,
    download_to_file,
)
from .installer import Installer
from .loaders import install_loader
from .paths import version_game_dir
from .packs import normalize_version_id
from .versions import version_json_path
from .zip_extract import (
    DEFAULT_EXTRACT_THREADS,
    ExtractJob,
    ZipExtractCancelled,
    ZipExtractError,
    extract_zip_entries,
)

CURSEFORGE_MANIFEST = "manifest.json"
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
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
_RESERVED_OVERRIDE_DIRS = {"versions", "libraries", "assets", "logs", "crash-reports"}
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

ProgressCallback = Callable[[int, str], None]
LogCallback = Callable[[str], None]


class CurseForgePackError(Exception):
    pass


@dataclass
class CurseForgeFile:
    project_id: str
    file_id: str
    required: bool = True


@dataclass
class CurseForgeOverride:
    member: str
    relative: str
    size: int


@dataclass
class CurseForgePackInfo:
    path: Path
    manifest: dict[str, Any]
    files: list[CurseForgeFile]
    overrides: list[CurseForgeOverride]
    counts: dict[str, int] = field(default_factory=dict)
    total_files: int = 0
    total_bytes: int = 0
    archive_bytes: int = 0

    @property
    def name(self) -> str:
        return str(self.manifest.get("name") or self.path.stem or "CurseForge 整合包")

    @property
    def description(self) -> str:
        text = str(self.manifest.get("author") or "").strip()
        return f"作者：{text}" if text else ""

    @property
    def version_id(self) -> str:
        return str(self.manifest.get("version") or "")

    @property
    def created_at(self) -> str:
        return ""

    @property
    def created_with(self) -> str:
        return "CurseForge"

    @property
    def minecraft_version(self) -> str:
        minecraft = self.manifest.get("minecraft") or {}
        if not isinstance(minecraft, dict):
            return ""
        return str(minecraft.get("version") or "")

    @property
    def loader_key(self) -> str:
        minecraft = self.manifest.get("minecraft") or {}
        if not isinstance(minecraft, dict):
            return ""
        loaders = minecraft.get("modLoaders") or []
        if not isinstance(loaders, list):
            return ""
        chosen = next(
            (
                item
                for item in loaders
                if isinstance(item, dict) and item.get("primary")
            ),
            loaders[0] if loaders else None,
        )
        if not isinstance(chosen, dict):
            return ""
        raw = str(chosen.get("id") or "").strip().lower()
        for key in ("neoforge", "fabric", "forge", "quilt"):
            if raw.startswith(key + "-"):
                return key
        return ""

    @property
    def loader(self) -> str:
        return {
            "fabric": "Fabric",
            "forge": "Forge",
            "neoforge": "NeoForge",
            "quilt": "Quilt",
        }.get(self.loader_key, "")

    @property
    def loader_version(self) -> str:
        minecraft = self.manifest.get("minecraft") or {}
        if not isinstance(minecraft, dict):
            return ""
        loaders = minecraft.get("modLoaders") or []
        if not isinstance(loaders, list) or not loaders:
            return ""
        chosen = next(
            (
                item
                for item in loaders
                if isinstance(item, dict) and item.get("primary")
            ),
            loaders[0],
        )
        if not isinstance(chosen, dict):
            return ""
        raw = str(chosen.get("id") or "").strip()
        prefix = self.loader_key + "-"
        if prefix and raw.lower().startswith(prefix):
            return raw[len(prefix) :]
        return ""


def _safe_path(name: str, label: str = "整合包") -> str:
    value = str(name or "").replace("\\", "/")
    if not value or value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise CurseForgePackError(f"{label}内包含非法路径：{name}")
    parts = PurePosixPath(value).parts
    if any(part in ("", ".", "..") for part in parts):
        raise CurseForgePackError(f"{label}内包含越界路径：{name}")
    for part in parts:
        if (
            re.search(r'[<>:"|?*\x00-\x1f]', part)
            or part.endswith((" ", "."))
            or part.split(".", 1)[0].upper() in _WINDOWS_RESERVED
        ):
            raise CurseForgePackError(f"{label}内包含 Windows 无法使用的路径：{name}")
    return "/".join(parts)


def _read_manifest(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> dict[str, Any]:
    if info.file_size > MAX_MANIFEST_BYTES:
        raise CurseForgePackError("manifest.json 过大，已拒绝读取")
    if info.flag_bits & 0x1:
        raise CurseForgePackError("整合包中的文件被加密，无法读取")
    try:
        raw = zf.read(info)
    except Exception as exc:
        raise CurseForgePackError(f"读取 CurseForge 清单失败：{exc}") from exc
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except Exception as exc:
        raise CurseForgePackError("manifest.json 不是有效的 JSON") from exc
    if not isinstance(data, dict):
        raise CurseForgePackError("manifest.json 格式不正确")
    return data


def _parse_files(manifest: dict[str, Any]) -> list[CurseForgeFile]:
    raw_files = manifest.get("files")
    if not isinstance(raw_files, list):
        raise CurseForgePackError("CurseForge 清单缺少 files 列表")
    result: list[CurseForgeFile] = []
    seen: set[tuple[str, str]] = set()
    for item in raw_files:
        if not isinstance(item, dict):
            raise CurseForgePackError("CurseForge 清单里的文件条目格式不正确")
        project_id = str(item.get("projectID") or item.get("projectId") or "").strip()
        file_id = str(item.get("fileID") or item.get("fileId") or "").strip()
        if not project_id or not file_id:
            continue
        key = (project_id, file_id)
        if key in seen:
            continue
        seen.add(key)
        result.append(
            CurseForgeFile(
                project_id=project_id,
                file_id=file_id,
                required=bool(item.get("required", True)),
            )
        )
    return result


def _load_archive(path: Path) -> tuple[dict[str, Any], list[CurseForgeOverride]]:
    archive = Path(path)
    if not archive.is_file():
        raise CurseForgePackError("整合包文件不存在")
    try:
        with zipfile.ZipFile(archive) as zf:
            infos = zf.infolist()
            if len(infos) > MAX_TOTAL_FILES:
                raise CurseForgePackError("整合包内文件数量过多，已拒绝读取")
            seen: set[str] = set()
            total = 0
            manifest_info: zipfile.ZipInfo | None = None
            for info in infos:
                name = _safe_path(info.filename, "整合包")
                key = name.lower()
                if key in seen:
                    raise CurseForgePackError(f"整合包内存在重复文件：{name}")
                seen.add(key)
                mode = (info.external_attr >> 16) & 0o170000
                if mode == 0o120000:
                    raise CurseForgePackError(f"整合包内不允许符号链接：{name}")
                if info.is_dir():
                    continue
                if info.flag_bits & 0x1:
                    raise CurseForgePackError("整合包中的文件被加密，无法导入")
                total += max(0, int(info.file_size))
                if total > MAX_TOTAL_BYTES:
                    raise CurseForgePackError("整合包解压后体积超过 64 GB，已拒绝导入")
                if name.lower() == CURSEFORGE_MANIFEST:
                    manifest_info = info
            if manifest_info is None:
                raise CurseForgePackError(
                    "这不是 CurseForge 整合包：缺少 manifest.json"
                )
            manifest = _read_manifest(zf, manifest_info)
            raw_override_dir = str(manifest.get("overrides") or "overrides")
            override_dir = _safe_path(raw_override_dir.strip("/"), "整合包")
            prefix = override_dir + "/"
            overrides: list[CurseForgeOverride] = []
            for info in infos:
                name = _safe_path(info.filename, "整合包")
                if info.is_dir() or not name.startswith(prefix):
                    continue
                relative = name[len(prefix) :]
                if not relative:
                    continue
                relative = _safe_path(relative, "整合包")
                reserved = relative.split("/", 1)[0].lower()
                if reserved in _RESERVED_OVERRIDE_DIRS:
                    raise CurseForgePackError(
                        f"整合包覆盖文件写入了保留目录：{relative}"
                    )
                overrides.append(
                    CurseForgeOverride(
                        member=info.filename,
                        relative=relative,
                        size=max(0, int(info.file_size)),
                    )
                )
    except CurseForgePackError:
        raise
    except zipfile.BadZipFile as exc:
        raise CurseForgePackError("文件不是有效的 ZIP 压缩包") from exc
    except OSError as exc:
        raise CurseForgePackError(f"无法打开整合包：{exc}") from exc
    return manifest, overrides


def looks_like_curseforge_pack(path: Path | str) -> bool:
    archive = Path(path)
    if not archive.is_file():
        return False
    try:
        with zipfile.ZipFile(archive) as zf:
            info = next(
                (
                    item
                    for item in zf.infolist()
                    if item.filename.replace("\\", "/").lower()
                    == CURSEFORGE_MANIFEST
                ),
                None,
            )
            if info is None or info.file_size > MAX_MANIFEST_BYTES:
                return False
            data = json.loads(zf.read(info).decode("utf-8-sig"))
            return (
                isinstance(data, dict)
                and str(data.get("manifestType") or "") == "minecraftModpack"
            )
    except Exception:
        return False


def inspect_curseforge_pack(path: Path | str) -> CurseForgePackInfo:
    archive = Path(path)
    manifest, overrides = _load_archive(archive)
    if str(manifest.get("manifestType") or "") != "minecraftModpack":
        raise CurseForgePackError("这个压缩包不是 CurseForge Minecraft 整合包")
    try:
        manifest_version = int(manifest.get("manifestVersion") or 0)
    except Exception:
        manifest_version = 0
    if manifest_version != 1:
        raise CurseForgePackError(
            f"CurseForge 整合包格式版本 {manifest_version or '未知'} 不受支持"
        )
    minecraft = manifest.get("minecraft") or {}
    if not isinstance(minecraft, dict):
        raise CurseForgePackError("CurseForge 清单缺少 minecraft 配置")
    mc_version = str(minecraft.get("version") or "").strip()
    if not re.fullmatch(r"[0-9A-Za-z._-]{1,40}", mc_version):
        raise CurseForgePackError(f"Minecraft 版本号不正确：{mc_version or '未知'}")
    files = _parse_files(manifest)
    if not files and not overrides:
        raise CurseForgePackError("整合包里没有任何需要导入的内容")

    counts = {name: 0 for name in CONTENT_DIRS}
    for item in overrides:
        first = item.relative.split("/", 1)[0]
        if first in counts:
            counts[first] += 1
    counts["mods"] += len(files)

    return CurseForgePackInfo(
        path=archive,
        manifest=manifest,
        files=files,
        overrides=overrides,
        counts=counts,
        total_files=len(files) + len(overrides),
        total_bytes=sum(item.size for item in overrides),
        archive_bytes=archive.stat().st_size,
    )


@dataclass
class CurseForgeImportResult:
    version_id: str
    imported_files: int
    skipped_files: int
    imported_bytes: int
    downloaded_files: int


def _inside(root: Path, path: Path) -> Path:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError) as exc:
        raise CurseForgePackError(f"整合包路径越界：{path}") from exc
    return path


def _write_version_wrapper(
    game_dir: Path, version_id: str, inherits_from: str
) -> Path:
    path = version_json_path(game_dir, version_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "id": version_id,
        "inheritsFrom": inherits_from,
        "type": "release",
        "name": version_id,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def import_curseforge_pack(
    pack_path: Path | str,
    game_dir: Path | str,
    version_id: str,
    *,
    java_path: str = "",
    overwrite: bool = False,
    resume: bool = False,
    isolated: bool = True,
    source: str = "official",
    threads: int = 8,
    progress_cb: ProgressCallback | None = None,
    log_cb: LogCallback | None = None,
    cancel_event: threading.Event | None = None,
    loader_installer: Callable[..., str] | None = None,
    download_func: Callable[..., int] | None = None,
    file_fetcher: Callable[..., dict[str, Any]] | None = None,
    installer: Any | None = None,
) -> CurseForgeImportResult:
    game_dir = Path(game_dir)
    info = inspect_curseforge_pack(pack_path)
    version_id = normalize_version_id(version_id)
    version_dir = game_dir / "versions" / version_id
    if version_dir.exists() and not resume:
        raise CurseForgePackError(f"版本 {version_id} 已经存在，请换一个导入名称")
    if resume and not version_json_path(game_dir, version_id).is_file():
        raise CurseForgePackError("上次导入的版本骨架不存在，请换一个导入名称重新开始")

    progress = progress_cb or (lambda _p, _m: None)
    log = log_cb or (lambda _m: None)
    cancel = cancel_event or threading.Event()
    loader_installer = loader_installer or install_loader
    download_func = download_func or download_to_file
    fetch_meta = file_fetcher or fetch_file
    connections = min(6, max(2, max(1, int(threads)) // 2))

    def check_cancel() -> None:
        if cancel.is_set():
            raise CurseForgePackError("导入已取消")

    def loader_progress(percent: int, message: str) -> None:
        check_cancel()
        progress(3 + int(max(0, min(100, percent)) * 0.42 / 100), message)

    mc_version = info.minecraft_version
    loader_kind = info.loader_key
    if loader_kind == "quilt":
        raise CurseForgePackError(
            "这个整合包使用 Quilt 加载器，当前启动器还不支持 Quilt。"
            "可以尝试使用 Prism 等其他启动器。"
        )
    log(
        f"CurseForge 整合包：{info.name} · Minecraft {mc_version} · "
        f"{info.loader or '原版'}"
        + (f" {info.loader_version}" if info.loader_version else "")
    )
    progress(2, "检查整合包与加载器…")
    check_cancel()

    inherits_from = ""
    if loader_kind:
        if loader_kind in ("forge", "neoforge") and not java_path:
            raise CurseForgePackError(
                f"安装 {info.loader} 需要 Java，请先在设置里选择 Java。"
            )
        progress(3, f"安装 Minecraft {mc_version} + {info.loader}…")
        inherits_from = loader_installer(
            game_dir,
            mc_version,
            loader_kind,
            info.loader_version,
            java_path=java_path,
            source=source,
            progress_cb=loader_progress,
            log_cb=log,
            cancel_event=cancel,
        )
        check_cancel()
    else:
        progress(3, f"安装 Minecraft {mc_version}…")
        target_installer = installer or Installer(
            game_dir,
            source=source,
            progress_cb=loader_progress,
            log_cb=log,
            cancel_event=cancel,
            threads=threads,
        )
        target_installer.install(mc_version)
        check_cancel()
        inherits_from = mc_version

    if inherits_from == version_id:
        raise CurseForgePackError("导入名称不能和加载器版本名称相同，请换一个名称")
    progress(46, "写入版本骨架…")
    _write_version_wrapper(game_dir, version_id, inherits_from)
    log(f"已创建版本 {version_id}，继承 {inherits_from}")

    run_dir = version_game_dir(game_dir, version_id, isolated)
    run_dir.mkdir(parents=True, exist_ok=True)
    mods_dir = run_dir / "mods"

    imported = 0
    skipped = 0
    imported_bytes = 0
    downloaded = 0
    total_files = max(1, len(info.files))

    def resolve(item: CurseForgeFile) -> tuple[CurseForgeFile, dict[str, Any]]:
        check_cancel()
        try:
            version = fetch_meta(
                item.project_id, item.file_id, content_type="mod"
            )
        except Exception as exc:
            raise CurseForgePackError(
                f"读取 CurseForge 文件 {item.project_id}/{item.file_id} 失败：{exc}"
            ) from exc
        files = [
            entry
            for entry in version.get("files") or []
            if isinstance(entry, dict)
        ]
        file = next(
            (entry for entry in files if entry.get("primary") and entry.get("url")),
            next((entry for entry in files if entry.get("url")), None),
        )
        if not isinstance(file, dict):
            raise CurseForgePackError(
                f"作者未开放文件 {item.project_id}/{item.file_id} 的第三方下载，"
                "请到 CurseForge 页面手动下载"
            )
        return item, file

    resolved: list[tuple[CurseForgeFile, dict[str, Any]]] = []
    if info.files:
        metadata_workers = min(8, max(2, int(threads)), len(info.files))
        if metadata_workers > 1:
            with ThreadPoolExecutor(max_workers=metadata_workers) as pool:
                resolved = list(pool.map(resolve, info.files))
        else:
            resolved = [resolve(item) for item in info.files]

    pending: list[BatchDownload] = []
    seen_dest: set[str] = set()
    for item, file in resolved:
        check_cancel()
        filename = Path(str(file.get("filename") or "")).name
        if not filename:
            raise CurseForgePackError(
                f"CurseForge 文件 {item.file_id} 的文件名无效"
            )
        destination = _inside(run_dir, mods_dir / filename)
        size = int(file.get("size") or 0)
        sha1 = str((file.get("hashes") or {}).get("sha1") or "")
        if destination.is_file() and size and destination.stat().st_size == size:
            skipped += 1
            continue
        key = str(destination).lower()
        if key in seen_dest:
            skipped += 1
            continue
        seen_dest.add(key)

        raw_url = str(file.get("url") or "")
        if not candidate_download_urls(raw_url):
            raise CurseForgePackError(f"文件 {filename} 没有可用的下载地址")

        def candidates(url: str = raw_url) -> list[str]:
            return candidate_download_urls(url)

        pending.append(
            BatchDownload(
                dest=destination,
                urls=candidates,
                sha1=sha1,
                size=size,
                label=f"mods/{filename}",
            )
        )

    if pending:
        start_percent = 48 + int(44 * skipped / total_files)
        downloaded, failures = download_batch(
            pending,
            cancel_event=cancel,
            connections=connections,
            max_workers=min(8, max(2, int(threads))),
            progress_cb=progress,
            percent_range=(start_percent, 92),
            log_cb=log,
            label="下载整合包文件",
            download_func=download_func,
        )
        imported += downloaded
        imported_bytes += sum(max(0, int(task.size)) for task in pending)
        if failures:
            detail = failures[0]
            raise CurseForgePackError(
                f"{len(failures)} 个文件下载失败：{detail}"
            )
    else:
        downloaded = 0

    override_total = sum(max(0, item.size) for item in info.overrides) or 1
    jobs: list[ExtractJob] = []
    groups: dict[str, list[int]] = {}
    try:
        with zipfile.ZipFile(info.path) as zf:
            indexes: dict[str, int] = {}
            for item in info.overrides:
                check_cancel()
                destination = _inside(run_dir, run_dir / item.relative)
                reserved = item.relative.split("/", 1)[0].lower()
                if reserved in _RESERVED_OVERRIDE_DIRS:
                    raise CurseForgePackError(
                        f"整合包覆盖文件写入了保留目录：{item.relative}"
                    )
                if item.relative.lower() == f"{version_id}.json".lower():
                    raise CurseForgePackError(
                        "整合包覆盖文件试图替换版本 JSON，已拒绝导入"
                    )
                try:
                    entry = zf.getinfo(item.member)
                except KeyError as exc:
                    raise CurseForgePackError(
                        f"整合包里找不到覆盖文件：{item.relative}"
                    ) from exc
                job = ExtractJob(entry, destination, item.relative)
                key = item.relative.lower()
                if key in indexes:
                    jobs[indexes[key]] = job
                    groups[key][0] += 1
                    groups[key][1] += max(0, item.size)
                else:
                    indexes[key] = len(jobs)
                    groups[key] = [
                        1,
                        max(0, item.size),
                        1 if destination.exists() else 0,
                    ]
                    jobs.append(job)
    except CurseForgePackError:
        raise
    except zipfile.BadZipFile as exc:
        raise CurseForgePackError("整合包在导入过程中损坏") from exc
    except OSError as exc:
        raise CurseForgePackError(f"读取整合包文件失败：{exc}") from exc

    check_cancel()

    def override_progress(done_bytes: int, _files: int, label: str) -> None:
        if label:
            progress(
                min(99, 92 + int(7 * done_bytes / override_total)),
                f"放入 {label}",
            )

    extract_threads = max(
        1, min(DEFAULT_EXTRACT_THREADS, int(threads or DEFAULT_EXTRACT_THREADS))
    )
    try:
        stats = extract_zip_entries(
            info.path,
            jobs,
            threads=extract_threads,
            overwrite=overwrite,
            cancel_event=cancel,
            progress_cb=override_progress,
        )
    except ZipExtractCancelled as exc:
        raise CurseForgePackError("导入已取消") from exc
    except ZipExtractError as exc:
        raise CurseForgePackError(f"解压覆盖文件失败：{exc}") from exc
    imported += stats.added
    skipped += stats.skipped
    imported_bytes += stats.bytes_written
    for count, size, existed in groups.values():
        if count <= 1:
            continue
        if existed and not overwrite:
            skipped += count - 1
        else:
            imported += count - 1
            imported_bytes += size

    check_cancel()
    progress(100, "猫猫已经把 CurseForge 整合包搬进猫窝")
    log(
        f"导入完成：{version_id} · 下载 {downloaded} 个文件 · "
        f"新增 {imported} 个文件"
        + (f" · 跳过 {skipped} 个已存在文件" if skipped else "")
    )
    return CurseForgeImportResult(
        version_id=version_id,
        imported_files=imported,
        skipped_files=skipped,
        imported_bytes=imported_bytes,
        downloaded_files=downloaded,
    )
