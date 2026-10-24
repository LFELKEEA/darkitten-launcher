from __future__ import annotations

import hashlib
import json
import re
import shutil
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from urllib.parse import urlparse

from .downloader import DownloadCancelled, download_to_file, http_open
from .installer import Installer
from .loaders import install_loader
from .paths import version_game_dir
from .packs import normalize_version_id
from .versions import version_json_path

MODRINTH_MANIFEST = "modrinth.index.json"
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_TOTAL_FILES = 100_000
MAX_TOTAL_BYTES = 64 * 1024**3
MODRINTH_API = "https://api.modrinth.com/v2"

CONTENT_DIRS = (
    "mods",
    "config",
    "defaultconfigs",
    "resourcepacks",
    "shaderpacks",
    "saves",
)
_RESERVED_OVERRIDE_DIRS = {"versions", "libraries", "assets", "logs", "crash-reports"}
_RESERVED_DOWNLOAD_DIRS = {"versions", "libraries", "assets"}
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_LOADER_KEYS = {
    "fabric-loader": ("fabric", "Fabric"),
    "forge": ("forge", "Forge"),
    "neoforge": ("neoforge", "NeoForge"),
    "quilt-loader": ("quilt", "Quilt"),
}

ProgressCallback = Callable[[int, str], None]
LogCallback = Callable[[str], None]


class ModrinthPackError(Exception):
    pass


@dataclass
class ModrinthFile:
    path: str
    sha1: str
    size: int
    urls: list[str]
    client: str = "required"


@dataclass
class ModrinthOverride:
    member: str
    relative: str
    size: int
    client_only: bool = False


@dataclass
class ModrinthPackInfo:
    path: Path
    manifest: dict[str, Any]
    files: list[ModrinthFile]
    overrides: list[ModrinthOverride]
    counts: dict[str, int]
    total_files: int
    total_bytes: int
    archive_bytes: int

    @property
    def name(self) -> str:
        return str(self.manifest.get("name") or self.path.stem or "Modrinth 整合包")

    @property
    def description(self) -> str:
        return str(self.manifest.get("summary") or "")

    @property
    def version_id(self) -> str:
        return str(self.manifest.get("versionId") or "")

    @property
    def created_at(self) -> str:
        return ""

    @property
    def created_with(self) -> str:
        return "Modrinth"

    @property
    def minecraft_version(self) -> str:
        dependencies = self.manifest.get("dependencies") or {}
        return str(dependencies.get("minecraft") or "")

    @property
    def loader_key(self) -> str:
        dependencies = self.manifest.get("dependencies") or {}
        for key in _LOADER_KEYS:
            if dependencies.get(key):
                return key
        return ""

    @property
    def loader(self) -> str:
        key = self.loader_key
        return _LOADER_KEYS.get(key, ("", ""))[1]

    @property
    def loader_version(self) -> str:
        key = self.loader_key
        if not key:
            return ""
        dependencies = self.manifest.get("dependencies") or {}
        return str(dependencies.get(key) or "")

    @property
    def loader_kind(self) -> str:
        return _LOADER_KEYS.get(self.loader_key, ("", ""))[0]


@dataclass
class ModrinthImportResult:
    version_id: str
    imported_files: int
    skipped_files: int
    imported_bytes: int
    downloaded_files: int


def looks_like_modrinth_pack(path: Path | str) -> bool:
    archive = Path(path)
    if not archive.is_file():
        return False
    try:
        with zipfile.ZipFile(archive) as zf:
            return any(
                name.replace("\\", "/").lower() == MODRINTH_MANIFEST
                for name in zf.namelist()
            )
    except Exception:
        return False


def _safe_path(name: str, label: str = "整合包") -> str:
    value = str(name or "").replace("\\", "/")
    if not value or value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise ModrinthPackError(f"{label}内包含非法路径：{name}")
    parts = PurePosixPath(value).parts
    if any(part in ("", ".", "..") for part in parts):
        raise ModrinthPackError(f"{label}内包含越界路径：{name}")
    for part in parts:
        if (
            re.search(r'[<>:"|?*\x00-\x1f]', part)
            or part.endswith((" ", "."))
            or part.split(".", 1)[0].upper() in _WINDOWS_RESERVED
        ):
            raise ModrinthPackError(f"{label}内包含 Windows 无法使用的路径：{name}")
    return "/".join(parts)


def _read_manifest(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> dict[str, Any]:
    if info.file_size > MAX_MANIFEST_BYTES:
        raise ModrinthPackError("modrinth.index.json 过大，已拒绝读取")
    if info.flag_bits & 0x1:
        raise ModrinthPackError("整合包中的文件被加密，无法读取")
    try:
        raw = zf.read(info)
    except Exception as exc:
        raise ModrinthPackError(f"读取 Modrinth 清单失败：{exc}") from exc
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except Exception as exc:
        raise ModrinthPackError("modrinth.index.json 不是有效的 JSON") from exc
    if not isinstance(data, dict):
        raise ModrinthPackError("modrinth.index.json 格式不正确")
    return data


def _parse_files(manifest: dict[str, Any]) -> list[ModrinthFile]:
    raw_files = manifest.get("files")
    if not isinstance(raw_files, list):
        raise ModrinthPackError("Modrinth 清单缺少 files 列表")
    result: list[ModrinthFile] = []
    seen: set[str] = set()
    for item in raw_files:
        if not isinstance(item, dict):
            raise ModrinthPackError("Modrinth 清单里的文件条目格式不正确")
        path = _safe_path(str(item.get("path") or ""), "整合包")
        if path.lower() == MODRINTH_MANIFEST or path.startswith(
            ("overrides/", "client-overrides/")
        ):
            raise ModrinthPackError(f"下载文件路径占用了保留目录：{path}")
        if path.split("/", 1)[0].lower() in _RESERVED_DOWNLOAD_DIRS:
            raise ModrinthPackError(f"下载文件路径写入了保留目录：{path}")
        key = path.lower()
        if key in seen:
            raise ModrinthPackError(f"整合包内存在重复文件：{path}")
        seen.add(key)

        env = item.get("env") or {}
        client = str(env.get("client") or "required").lower()
        if client == "unsupported":
            continue

        hashes = item.get("hashes") or {}
        sha1 = str(hashes.get("sha1") or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{40}", sha1):
            raise ModrinthPackError(f"文件 {path} 缺少有效的 SHA1 校验值")
        try:
            size = int(item.get("fileSize") or 0)
        except Exception:
            size = 0
        if size < 0:
            size = 0
        downloads: list[str] = []
        for url in item.get("downloads") or []:
            text = str(url or "").strip()
            if text.startswith(("http://", "https://")):
                downloads.append(text)
        if not downloads:
            raise ModrinthPackError(f"文件 {path} 没有可用的下载地址")
        result.append(
            ModrinthFile(
                path=path,
                sha1=sha1,
                size=size,
                urls=downloads,
                client=client,
            )
        )
    return result


def inspect_modrinth_pack(path: Path | str) -> ModrinthPackInfo:
    archive = Path(path)
    if not archive.is_file():
        raise ModrinthPackError("整合包文件不存在")
    try:
        with zipfile.ZipFile(archive) as zf:
            infos = zf.infolist()
            if len(infos) > MAX_TOTAL_FILES:
                raise ModrinthPackError("整合包内文件数量过多，已拒绝读取")
            seen: set[str] = set()
            total = 0
            manifest_info: zipfile.ZipInfo | None = None
            overrides: list[ModrinthOverride] = []
            for info in infos:
                name = _safe_path(info.filename, "整合包")
                key = name.lower()
                if key in seen:
                    raise ModrinthPackError(f"整合包内存在重复文件：{name}")
                seen.add(key)
                mode = (info.external_attr >> 16) & 0o170000
                if mode == 0o120000:
                    raise ModrinthPackError(f"整合包内不允许符号链接：{name}")
                if info.is_dir():
                    continue
                if info.flag_bits & 0x1:
                    raise ModrinthPackError("整合包中的文件被加密，无法导入")
                total += max(0, int(info.file_size))
                if total > MAX_TOTAL_BYTES:
                    raise ModrinthPackError("整合包解压后体积超过 64 GB，已拒绝导入")
                if name.lower() == MODRINTH_MANIFEST:
                    manifest_info = info
                    continue
                if name.startswith("overrides/"):
                    relative = name[len("overrides/") :]
                    overrides.append(
                        ModrinthOverride(
                            member=info.filename,
                            relative=_safe_path(relative, "整合包"),
                            size=max(0, int(info.file_size)),
                        )
                    )
                elif name.startswith("client-overrides/"):
                    relative = name[len("client-overrides/") :]
                    overrides.append(
                        ModrinthOverride(
                            member=info.filename,
                            relative=_safe_path(relative, "整合包"),
                            size=max(0, int(info.file_size)),
                            client_only=True,
                        )
                    )
            for item in overrides:
                if item.relative.split("/", 1)[0].lower() in _RESERVED_OVERRIDE_DIRS:
                    raise ModrinthPackError(
                        f"整合包覆盖文件写入了保留目录：{item.relative}"
                    )
            if manifest_info is None:
                raise ModrinthPackError(
                    "这不是 Modrinth 整合包：缺少 modrinth.index.json"
                )
            manifest = _read_manifest(zf, manifest_info)
    except ModrinthPackError:
        raise
    except zipfile.BadZipFile as exc:
        raise ModrinthPackError("文件不是有效的 ZIP / mrpack 压缩包") from exc
    except OSError as exc:
        raise ModrinthPackError(f"无法打开整合包：{exc}") from exc

    if str(manifest.get("game") or "") != "minecraft":
        raise ModrinthPackError("这个 Modrinth 包不是 Minecraft 整合包")
    try:
        format_version = int(manifest.get("formatVersion") or 0)
    except Exception:
        format_version = 0
    if format_version != 1:
        raise ModrinthPackError(
            f"Modrinth 整合包格式版本 {format_version or '未知'} 不受支持"
        )
    dependencies = manifest.get("dependencies") or {}
    if not isinstance(dependencies, dict) or not dependencies.get("minecraft"):
        raise ModrinthPackError("Modrinth 清单没有指定 Minecraft 版本")
    mc_version = str(dependencies.get("minecraft") or "").strip()
    if not re.fullmatch(r"[0-9A-Za-z._-]{1,40}", mc_version):
        raise ModrinthPackError(f"Minecraft 版本号不正确：{mc_version}")
    loader_key = ""
    for key in _LOADER_KEYS:
        if dependencies.get(key):
            loader_key = key
            break
    if loader_key:
        loader_version = str(dependencies.get(loader_key) or "").strip()
        if not re.fullmatch(r"[0-9A-Za-z._+-]{1,80}", loader_version):
            raise ModrinthPackError(f"加载器版本号不正确：{loader_version}")
        if loader_key == "quilt-loader":
            raise ModrinthPackError(
                "这个整合包使用 Quilt 加载器，当前启动器还不支持 Quilt。"
                "可以尝试让整合包作者导出 Fabric 版本，或使用 Prism 等启动器。"
            )

    files = _parse_files(manifest)
    if not files and not overrides:
        raise ModrinthPackError("整合包里没有任何需要导入的内容")
    counts = {name: 0 for name in CONTENT_DIRS}
    for item in files:
        first = item.path.split("/", 1)[0]
        if first in counts:
            counts[first] += 1
    for item in overrides:
        first = item.relative.split("/", 1)[0]
        if first in counts:
            counts[first] += 1

    return ModrinthPackInfo(
        path=archive,
        manifest=manifest,
        files=files,
        overrides=overrides,
        counts=counts,
        total_files=len(files) + len(overrides),
        total_bytes=sum(item.size for item in files)
        + sum(item.size for item in overrides),
        archive_bytes=archive.stat().st_size,
    )


def _sha1_file(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as fp:
        while True:
            chunk = fp.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _file_matches(path: Path, sha1: str, size: int) -> bool:
    try:
        if not path.is_file():
            return False
        if size and path.stat().st_size != size:
            return False
        return _sha1_file(path) == sha1
    except OSError:
        return False


def _modrinth_fallback_urls(sha1: str) -> list[str]:
    """用 SHA1 向 Modrinth 查询同一个文件的 CDN 地址。"""
    try:
        with http_open(
            f"{MODRINTH_API}/version_file/{sha1}?algorithm=sha1",
            timeout=12,
        ) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    result: list[str] = []
    for item in data.get("files") or []:
        if not isinstance(item, dict):
            continue
        item_sha1 = str((item.get("hashes") or {}).get("sha1") or "").lower()
        if item_sha1 and item_sha1 != sha1:
            continue
        url = str(item.get("url") or "").strip()
        if url.startswith(("http://", "https://")):
            result.append(url)
    return list(dict.fromkeys(result))


def _preferred_urls(item: ModrinthFile) -> list[str]:
    """ForgeCDN 在部分网络下不可达时，优先换成 Modrinth CDN。"""
    cdn = [
        url
        for url in item.urls
        if (urlparse(url).hostname or "").lower() == "cdn.modrinth.com"
    ]
    if cdn:
        return list(dict.fromkeys(cdn + item.urls))
    return list(dict.fromkeys(_modrinth_fallback_urls(item.sha1) + item.urls))


def _inside(root: Path, path: Path) -> Path:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError) as exc:
        raise ModrinthPackError(f"整合包路径越界：{path}") from exc
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


def import_modrinth_pack(
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
    installer: Any | None = None,
    url_resolver: Callable[[ModrinthFile], list[str]] | None = None,
) -> ModrinthImportResult:
    game_dir = Path(game_dir)
    info = inspect_modrinth_pack(pack_path)
    version_id = normalize_version_id(version_id)
    version_dir = game_dir / "versions" / version_id
    if version_dir.exists() and not resume:
        raise ModrinthPackError(f"版本 {version_id} 已经存在，请换一个导入名称")
    if resume and not version_json_path(game_dir, version_id).is_file():
        raise ModrinthPackError("上次导入的版本骨架不存在，请换一个导入名称重新开始")

    progress = progress_cb or (lambda _p, _m: None)
    log = log_cb or (lambda _m: None)
    cancel = cancel_event or threading.Event()
    loader_installer = loader_installer or install_loader
    download_func = download_func or download_to_file
    connections = min(6, max(2, max(1, int(threads)) // 2))

    def check_cancel() -> None:
        if cancel.is_set():
            raise ModrinthPackError("导入已取消")

    def loader_progress(percent: int, message: str) -> None:
        check_cancel()
        progress(3 + int(max(0, min(100, percent)) * 0.42 / 100), message)

    mc_version = info.minecraft_version
    loader_kind = info.loader_kind
    log(
        f"Modrinth 整合包：{info.name} · Minecraft {mc_version} · "
        f"{info.loader or '原版'}"
        + (f" {info.loader_version}" if info.loader_version else "")
    )
    progress(2, "检查整合包与加载器…")
    check_cancel()

    inherits_from = ""
    if loader_kind:
        if loader_kind in ("forge", "neoforge") and not java_path:
            raise ModrinthPackError(
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
        raise ModrinthPackError("导入名称不能和加载器版本名称相同，请换一个名称")
    progress(46, "写入版本骨架…")
    _write_version_wrapper(game_dir, version_id, inherits_from)
    log(f"已创建版本 {version_id}，继承 {inherits_from}")

    run_dir = version_game_dir(game_dir, version_id, isolated)
    run_dir.mkdir(parents=True, exist_ok=True)
    imported = 0
    skipped = 0
    imported_bytes = 0
    downloaded = 0
    total_files = max(1, len(info.files))

    for index, item in enumerate(info.files, 1):
        check_cancel()
        destination = _inside(run_dir, run_dir / item.path)
        start = 48 + int(44 * (index - 1) / total_files)
        end = 48 + int(44 * index / total_files)
        if _file_matches(destination, item.sha1, item.size):
            skipped += 1
            progress(end, f"已有 {item.path}")
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        last = [0]

        def on_bytes(done: int, total: int, start=start, end=end) -> None:
            percent = start + int((end - start) * min(1.0, done / total)) if total else start
            if percent != last[0]:
                last[0] = percent
                progress(percent, f"下载 {item.path}")

        if url_resolver is not None:
            candidates = list(url_resolver(item) or item.urls)
        else:
            candidates = _preferred_urls(item)
        candidates = list(dict.fromkeys(candidates + item.urls))
        errors: list[str] = []
        for url in candidates:
            check_cancel()
            try:
                download_func(
                    url,
                    destination,
                    sha1=item.sha1,
                    size=item.size,
                    cancel_event=cancel,
                    progress_cb=on_bytes,
                    connections=connections,
                )
                break
            except DownloadCancelled:
                raise ModrinthPackError("导入已取消") from None
            except Exception as exc:
                check_cancel()
                errors.append(str(exc))
        else:
            if url_resolver is None and not any(
                (urlparse(url).hostname or "").lower() == "cdn.modrinth.com"
                for url in candidates
            ):
                for url in _modrinth_fallback_urls(item.sha1):
                    check_cancel()
                    try:
                        download_func(
                            url,
                            destination,
                            sha1=item.sha1,
                            size=item.size,
                            cancel_event=cancel,
                            progress_cb=on_bytes,
                            connections=connections,
                        )
                        break
                    except DownloadCancelled:
                        raise ModrinthPackError("导入已取消") from None
                    except Exception as exc:
                        check_cancel()
                        errors.append(str(exc))
                else:
                    detail = errors[-1] if errors else "没有可用的下载地址"
                    raise ModrinthPackError(f"下载 {item.path} 失败：{detail}")
            else:
                detail = errors[-1] if errors else "没有可用的下载地址"
                raise ModrinthPackError(f"下载 {item.path} 失败：{detail}")
        imported += 1
        downloaded += 1
        imported_bytes += max(0, int(item.size))
        progress(end, f"已下载 {item.path}")
        log(f"下载完成：{item.path}")

    override_total = sum(max(0, item.size) for item in info.overrides) or 1
    override_done = 0
    written: set[str] = set()
    try:
        with zipfile.ZipFile(info.path) as zf:
            ordered = sorted(info.overrides, key=lambda item: item.client_only)
            for item in ordered:
                check_cancel()
                destination = _inside(run_dir, run_dir / item.relative)
                reserved = item.relative.split("/", 1)[0].lower()
                if reserved in _RESERVED_OVERRIDE_DIRS:
                    raise ModrinthPackError(
                        f"整合包覆盖文件写入了保留目录：{item.relative}"
                    )
                if item.relative.lower() == f"{version_id}.json".lower():
                    raise ModrinthPackError(
                        "整合包覆盖文件试图替换版本 JSON，已拒绝导入"
                    )
                key = item.relative.lower()
                if destination.exists() and not overwrite and key not in written:
                    skipped += 1
                    override_done += max(0, item.size)
                    progress(
                        min(99, 92 + int(7 * override_done / override_total)),
                        f"跳过已有文件 {item.relative}",
                    )
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with zf.open(item.member) as src, destination.open("wb") as out:
                        shutil.copyfileobj(src, out, 1024 * 1024)
                except Exception as exc:
                    raise ModrinthPackError(
                        f"解压覆盖文件 {item.relative} 失败：{exc}"
                    ) from exc
                written.add(key)
                imported += 1
                imported_bytes += max(0, item.size)
                override_done += max(0, item.size)
                progress(
                    min(99, 92 + int(7 * override_done / override_total)),
                    f"放入 {item.relative}",
                )
    except ModrinthPackError:
        raise
    except zipfile.BadZipFile as exc:
        raise ModrinthPackError("整合包在导入过程中损坏") from exc
    except OSError as exc:
        raise ModrinthPackError(f"写入整合包文件失败：{exc}") from exc

    check_cancel()
    progress(100, "猫猫已经把 Modrinth 整合包搬进猫窝")
    log(
        f"导入完成：{version_id} · 下载 {downloaded} 个文件 · "
        f"新增 {imported} 个文件"
        + (f" · 跳过 {skipped} 个已存在文件" if skipped else "")
    )
    return ModrinthImportResult(
        version_id=version_id,
        imported_files=imported,
        skipped_files=skipped,
        imported_bytes=imported_bytes,
        downloaded_files=downloaded,
    )
