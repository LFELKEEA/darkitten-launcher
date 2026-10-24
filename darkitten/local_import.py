from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from .paths import version_game_dir
from .packs import PackError, human_size, normalize_version_id, unique_version_id
from .version_zip import VersionZipError, inspect_version_zip
from .versions import version_json_path

ProgressCallback = Callable[[int, str], None]
LogCallback = Callable[[str], None]

MAX_TOTAL_FILES = 100_000
MAX_TOTAL_BYTES = 64 * 1024**3
MAX_JSON_BYTES = 2 * 1024 * 1024
MOD_SUFFIXES = (".jar", ".litemod")
MOD_DISABLED_SUFFIXES = (".jar.disabled", ".litemod.disabled")
MOD_FILE_SUFFIXES = MOD_SUFFIXES + MOD_DISABLED_SUFFIXES
CONTENT_CATEGORIES = (
    "mods",
    "resourcepacks",
    "shaderpacks",
    "config",
    "defaultconfigs",
)
_ARCHIVE_SUFFIXES = (".zip", ".dklpack", ".mrpack")
_JUNK_NAMES = {"desktop.ini", "thumbs.db", ".ds_store"}
_JUNK_SUFFIXES = (".part", ".tmp", ".download")
_SKIP_TOP_DIRS = {
    "logs",
    "crash-reports",
    "cache",
    "__pycache__",
    ".git",
}
_VERSION_MARKERS = {
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


class LocalImportError(Exception):
    pass


def content_kind_label(kind: str) -> str:
    return {
        "mod": "Mod 文件",
        "resourcepack": "材质包",
        "shaderpack": "光影包",
        "resourcepack_folder": "材质包文件夹",
        "shaderpack_folder": "光影包文件夹",
        "mod_bundle": "Mod 合集",
        "content_bundle": "内容合集",
    }.get(kind, kind or "本地内容")


def _is_junk_name(name: str) -> bool:
    lowered = name.lower()
    return (
        lowered in _JUNK_NAMES
        or lowered.endswith(_JUNK_SUFFIXES)
        or lowered.startswith("~")
    )


def _safe_member(name: str) -> str:
    value = str(name or "").replace("\\", "/")
    if not value or value.startswith("/") or (len(value) >= 2 and value[1] == ":"):
        raise LocalImportError(f"压缩包内包含非法路径：{name}")
    parts = PurePosixPath(value).parts
    if any(part in ("", ".", "..") for part in parts):
        raise LocalImportError(f"压缩包内包含越界路径：{name}")
    return "/".join(parts)


def _member_is_symlink(info: zipfile.ZipInfo) -> bool:
    return ((info.external_attr >> 16) & 0o170000) == 0o120000


def _zip_members(zf: zipfile.ZipFile) -> list[tuple[zipfile.ZipInfo, str]]:
    infos = zf.infolist()
    if len(infos) > MAX_TOTAL_FILES:
        raise LocalImportError("压缩包内文件数量过多，已拒绝读取")
    result: list[tuple[zipfile.ZipInfo, str]] = []
    seen: set[str] = set()
    total = 0
    for info in infos:
        name = _safe_member(info.filename)
        key = name.lower()
        if key in seen:
            raise LocalImportError(f"压缩包内存在重复文件：{name}")
        seen.add(key)
        if _member_is_symlink(info):
            raise LocalImportError(f"压缩包内不允许符号链接：{name}")
        if info.is_dir():
            continue
        if info.flag_bits & 0x1:
            raise LocalImportError("压缩包中的文件被加密，无法导入")
        if _is_junk_name(PurePosixPath(name).name):
            continue
        total += max(0, int(info.file_size))
        if total > MAX_TOTAL_BYTES:
            raise LocalImportError("压缩包解压后体积超过 64 GB，已拒绝导入")
        result.append((info, name))
    return result


def _single_wrapper(names: list[str]) -> tuple[str, str]:
    """返回 (单层包装目录名, 前缀)。压缩包没有唯一包装目录时返回空。"""
    if not names:
        return "", ""
    roots = {PurePosixPath(name).parts[0] for name in names}
    if len(roots) != 1:
        return "", ""
    root = next(iter(roots))
    if root.lower() in {
        "assets",
        "shaders",
        "mods",
        "resourcepacks",
        "shaderpacks",
        "config",
        "defaultconfigs",
        "meta-inf",
    }:
        return "", ""
    if any(len(PurePosixPath(name).parts) == 1 for name in names):
        return "", ""
    return root, root + "/"


def _has_mod_metadata(names: set[str]) -> bool:
    for name in names:
        lowered = name.lower()
        if lowered in {
            "fabric.mod.json",
            "quilt.mod.json",
            "mcmod.info",
            "meta-inf/mods.toml",
            "meta-inf/neoforge.mods.toml",
        }:
            return True
    return False


@dataclass
class ContentFile:
    arcname: str
    target: str
    size: int


@dataclass
class ContentPlan:
    path: Path
    kind: str
    label: str
    mode: str
    target_kind: str = ""
    copy_name: str = ""
    prefix: str = ""
    package_name: str = ""
    files: list[ContentFile] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    total_files: int = 0
    total_bytes: int = 0
    archive_bytes: int = 0
    note: str = ""

    @property
    def summary(self) -> str:
        bits = [f"{self.total_files} 个文件", human_size(self.total_bytes)]
        for key, label in (
            ("mods", "Mod"),
            ("resourcepacks", "材质"),
            ("shaderpacks", "光影"),
            ("config", "配置"),
        ):
            count = self.counts.get(key, 0)
            if count:
                bits.append(f"{label} {count}")
        return " · ".join(bits)


def _copy_plan(path: Path, kind: str, target_kind: str, copy_name: str = "", note: str = "") -> ContentPlan:
    size = 0
    try:
        size = path.stat().st_size
    except OSError:
        pass
    return ContentPlan(
        path=path,
        kind=kind,
        label=content_kind_label(kind),
        mode="copy",
        target_kind=target_kind,
        copy_name=copy_name or path.name,
        counts={target_kind: 1},
        total_files=1,
        total_bytes=size,
        archive_bytes=size,
        note=note,
    )


def _mod_bundle_plan(
    path: Path,
    members: list[tuple[zipfile.ZipInfo, str]],
    prefix: str,
    archive_bytes: int,
) -> ContentPlan | None:
    files: list[ContentFile] = []
    total = 0
    for info, name in members:
        relative = name[len(prefix) :] if prefix and name.startswith(prefix) else name
        parts = PurePosixPath(relative).parts
        if len(parts) == 1:
            leaf = parts[0]
        elif len(parts) == 2 and parts[0].lower() == "mods":
            leaf = parts[1]
        else:
            continue
        lowered = leaf.lower()
        if not lowered.endswith(MOD_FILE_SUFFIXES):
            continue
        files.append(
            ContentFile(arcname=name, target=leaf, size=info.file_size)
        )
        total += max(0, int(info.file_size))
    if not files:
        return None
    files.sort(key=lambda item: item.target.lower())
    return ContentPlan(
        path=path,
        kind="mod_bundle",
        label=content_kind_label("mod_bundle"),
        mode="extract_mods",
        target_kind="mods",
        prefix=prefix,
        files=files,
        counts={"mods": len(files)},
        total_files=len(files),
        total_bytes=total,
        archive_bytes=archive_bytes,
        note="把压缩包里的 Mod 直接放进 mods，不再临时中转。",
    )


def _bundle_plan(
    path: Path,
    members: list[tuple[zipfile.ZipInfo, str]],
    prefix: str,
    archive_bytes: int,
) -> ContentPlan | None:
    files: list[ContentFile] = []
    counts: dict[str, int] = {key: 0 for key in CONTENT_CATEGORIES}
    total = 0
    for info, name in members:
        relative = name[len(prefix) :] if prefix and name.startswith(prefix) else name
        parts = PurePosixPath(relative).parts
        if len(parts) < 2:
            continue
        category = parts[0].lower()
        if category not in CONTENT_CATEGORIES:
            continue
        target = "/".join((category,) + parts[1:])
        if category == "mods" and not relative.lower().endswith(MOD_FILE_SUFFIXES):
            continue
        files.append(ContentFile(arcname=name, target=target, size=info.file_size))
        counts[category] += 1
        total += max(0, int(info.file_size))
    if not files:
        return None
    files.sort(key=lambda item: item.target.lower())
    return ContentPlan(
        path=path,
        kind="content_bundle",
        label=content_kind_label("content_bundle"),
        mode="extract_bundle",
        prefix=prefix,
        files=files,
        counts=counts,
        total_files=len(files),
        total_bytes=total,
        archive_bytes=archive_bytes,
        note="同名文件会跳过，不会覆盖现有内容。",
    )


def inspect_content_path(path: Path | str) -> ContentPlan:
    source = Path(path)
    if not source.exists():
        raise LocalImportError("找不到要导入的文件")
    if source.is_dir():
        raise LocalImportError("本地内容目前支持 .jar、.litemod 和 .zip 文件")
    lowered = source.name.lower()
    if lowered.endswith(MOD_DISABLED_SUFFIXES):
        return _copy_plan(source, "mod", "mods", note="保留禁用状态直接放进 mods。")
    if lowered.endswith(MOD_SUFFIXES):
        return _copy_plan(source, "mod", "mods", note="直接复制到 mods，无需解压。")
    if source.suffix.lower() != ".zip":
        raise LocalImportError("只支持 .jar、.litemod 和 .zip 内容文件")

    try:
        with zipfile.ZipFile(source) as zf:
            members = _zip_members(zf)
    except zipfile.BadZipFile as exc:
        raise LocalImportError("文件不是有效的 ZIP 压缩包") from exc
    except OSError as exc:
        raise LocalImportError(f"无法打开压缩包：{exc}") from exc
    if not members:
        raise LocalImportError("压缩包是空的")

    names = [name for _info, name in members]
    lower_names = {name.lower() for name in names}
    archive_bytes = source.stat().st_size
    wrapper, prefix = _single_wrapper(names)
    relative_names = [
        name[len(prefix) :] if prefix and name.startswith(prefix) else name
        for name in names
    ]
    relative_lower = {name.lower() for name in relative_names}
    relative_parts = [PurePosixPath(name).parts for name in relative_names]

    # Minecraft 直接读取材质/光影 ZIP，这类压缩包不能拆散。
    if "pack.mcmeta" in relative_lower:
        if wrapper and not lower_names.intersection({"pack.mcmeta"}):
            files = [
                ContentFile(
                    arcname=name,
                    target=relative_names[index],
                    size=members[index][0].file_size,
                )
                for index, name in enumerate(names)
            ]
            total = sum(max(0, int(item.size)) for item in files)
            return ContentPlan(
                path=source,
                kind="resourcepack_folder",
                label=content_kind_label("resourcepack_folder"),
                mode="extract_package",
                target_kind="resourcepacks",
                prefix=prefix,
                package_name=wrapper,
                files=files,
                counts={"resourcepacks": 1},
                total_files=len(files),
                total_bytes=total,
                archive_bytes=archive_bytes,
                note="把包装目录解到 resourcepacks，游戏会识别整个文件夹。",
            )
        return _copy_plan(
            source,
            "resourcepack",
            "resourcepacks",
            note="Minecraft 直接读取 ZIP，保持压缩状态复制。",
        )

    if any(parts and parts[0].lower() == "shaders" for parts in relative_parts) or (
        "shaders.properties" in relative_lower
    ):
        if wrapper and not any(
            PurePosixPath(name).parts
            and PurePosixPath(name).parts[0].lower() == "shaders"
            for name in names
        ):
            files = [
                ContentFile(
                    arcname=name,
                    target=relative_names[index],
                    size=members[index][0].file_size,
                )
                for index, name in enumerate(names)
            ]
            total = sum(max(0, int(item.size)) for item in files)
            return ContentPlan(
                path=source,
                kind="shaderpack_folder",
                label=content_kind_label("shaderpack_folder"),
                mode="extract_package",
                target_kind="shaderpacks",
                prefix=prefix,
                package_name=wrapper,
                files=files,
                counts={"shaderpacks": 1},
                total_files=len(files),
                total_bytes=total,
                archive_bytes=archive_bytes,
                note="把包装目录解到 shaderpacks，游戏会识别整个文件夹。",
            )
        return _copy_plan(
            source,
            "shaderpack",
            "shaderpacks",
            note="Minecraft 直接读取 ZIP，保持压缩状态复制。",
        )

    if _has_mod_metadata(relative_lower):
        copy_name = PurePosixPath(source.name).stem + ".jar"
        return _copy_plan(
            source,
            "mod",
            "mods",
            copy_name=copy_name,
            note="压缩包里是 Mod 本体，按 .jar 放进 mods。",
        )

    # 只装 Mod 的压缩包：直接把 jar 抽到 mods。
    has_mods_dir = any(parts and parts[0].lower() == "mods" for parts in relative_parts)
    has_other_content = any(
        parts
        and parts[0].lower() in ("resourcepacks", "shaderpacks", "config", "defaultconfigs")
        for parts in relative_parts
    )
    if has_other_content:
        plan = _bundle_plan(source, members, prefix, archive_bytes)
        if plan:
            return plan

    mod_plan = _mod_bundle_plan(source, members, prefix, archive_bytes)
    if has_mods_dir and mod_plan:
        return mod_plan
    if mod_plan and all(
        len(PurePosixPath(name).parts) == 1
        or PurePosixPath(name).parts[0].lower() == "mods"
        for name in relative_names
    ):
        return mod_plan

    raise LocalImportError(
        "这个 ZIP 不像材质包、光影包或 Mod 合集。"
        "如果它是完整游戏版本，请拖版本文件夹或版本 ZIP；"
        "如果是整合包，请选择 .dklpack / .mrpack / CurseForge 整合包。"
    )


def _unique_path(destination: Path, reserved: set[str]) -> Path:
    key = str(destination).lower()
    if not destination.exists() and key not in reserved:
        reserved.add(key)
        return destination
    index = 2
    while True:
        candidate = destination.with_name(
            f"{destination.stem} ({index}){destination.suffix}"
        )
        key = str(candidate).lower()
        if not candidate.exists() and key not in reserved:
            reserved.add(key)
            return candidate
        index += 1
        if index > 9999:
            raise LocalImportError("同名文件太多，无法生成新的文件名")


def _within(path: Path, root: Path) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise LocalImportError(f"目标路径越界：{path}") from exc


@dataclass
class LocalContentResult:
    imported_files: int
    skipped_files: int
    imported_bytes: int
    targets: dict[str, int]


def install_local_contents(
    sources: list[Path | str],
    game_dir: Path | str,
    version_id: str,
    *,
    isolated: bool = True,
    progress_cb: ProgressCallback | None = None,
    log_cb: LogCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> LocalContentResult:
    game_dir = Path(game_dir)
    version_id = str(version_id or "").strip()
    if not version_id:
        raise LocalImportError("先选择一个游戏版本，猫猫才知道放哪里")
    progress = progress_cb or (lambda _p, _m: None)
    log = log_cb or (lambda _m: None)
    cancel = cancel_event or threading.Event()
    run_dir = version_game_dir(game_dir, version_id, isolated)
    plans = [inspect_content_path(item) for item in sources]
    if not plans:
        raise LocalImportError("没有选择要导入的内容")

    total_bytes = max(1, sum(plan.total_bytes for plan in plans))
    done_bytes = 0
    imported = skipped = imported_bytes = 0
    targets: dict[str, int] = {}
    reserved: set[str] = set()

    progress(2, "读取本地内容…")
    for plan in plans:
        if cancel.is_set():
            raise LocalImportError("导入已取消")
        progress(
            min(20, 2 + int(8 * done_bytes / total_bytes)),
            f"准备 {plan.label}…",
        )
        if plan.mode == "copy":
            base = run_dir / plan.target_kind
            base.mkdir(parents=True, exist_ok=True)
            destination = _unique_path(base / plan.copy_name, reserved)
            shutil.copy2(plan.path, destination)
            imported += 1
            imported_bytes += plan.total_bytes
            targets[plan.target_kind] = targets.get(plan.target_kind, 0) + 1
            done_bytes += plan.total_bytes
            log(f"{plan.label} → {destination}")
            progress(
                20 + int(75 * done_bytes / total_bytes),
                f"已复制 {destination.name}",
            )
            continue

        if plan.mode == "extract_package":
            base = run_dir / plan.target_kind
            base.mkdir(parents=True, exist_ok=True)
            destination_root = _unique_path(base / plan.package_name, reserved)
            destination_root.mkdir(parents=True, exist_ok=True)
            try:
                with zipfile.ZipFile(plan.path) as zf:
                    for item in plan.files:
                        if cancel.is_set():
                            raise LocalImportError("导入已取消")
                        info = zf.getinfo(item.arcname)
                        destination = destination_root / PurePosixPath(item.target)
                        _within(destination, destination_root)
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(info) as src, destination.open("wb") as out:
                            shutil.copyfileobj(src, out, 1024 * 1024)
                        imported += 1
                        imported_bytes += item.size
                        done_bytes += item.size
                        progress(
                            20 + int(75 * done_bytes / total_bytes),
                            f"解压 {item.target}",
                        )
            except Exception:
                # 这是本次刚创建的空目录；失败时只清理自己的产物。
                shutil.rmtree(destination_root, ignore_errors=True)
                raise
            targets[plan.target_kind] = targets.get(plan.target_kind, 0) + 1
            log(f"{plan.label} → {destination_root}")
            continue

        if plan.mode == "extract_mods":
            base = run_dir / "mods"
            base.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(plan.path) as zf:
                for item in plan.files:
                    if cancel.is_set():
                        raise LocalImportError("导入已取消")
                    info = zf.getinfo(item.arcname)
                    destination = _unique_path(base / item.target, reserved)
                    with zf.open(info) as src, destination.open("wb") as out:
                        shutil.copyfileobj(src, out, 1024 * 1024)
                    imported += 1
                    imported_bytes += item.size
                    done_bytes += item.size
                    progress(
                        20 + int(75 * done_bytes / total_bytes),
                        f"放入 mods/{destination.name}",
                    )
            targets["mods"] = targets.get("mods", 0) + plan.total_files
            log(f"{plan.label} → {base}")
            continue

        if plan.mode == "extract_bundle":
            with zipfile.ZipFile(plan.path) as zf:
                for item in plan.files:
                    if cancel.is_set():
                        raise LocalImportError("导入已取消")
                    destination = run_dir / PurePosixPath(item.target)
                    _within(destination, run_dir)
                    if destination.exists():
                        skipped += 1
                        done_bytes += item.size
                        continue
                    info = zf.getinfo(item.arcname)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info) as src, destination.open("wb") as out:
                        shutil.copyfileobj(src, out, 1024 * 1024)
                    imported += 1
                    imported_bytes += item.size
                    done_bytes += item.size
                    category = item.target.split("/", 1)[0]
                    targets[category] = targets.get(category, 0) + 1
                    progress(
                        20 + int(75 * done_bytes / total_bytes),
                        f"解压 {item.target}",
                    )
            log(f"{plan.label} → {run_dir}")
            continue

        raise LocalImportError(f"无法处理 {plan.path.name}")

    if cancel.is_set():
        raise LocalImportError("导入已取消")
    progress(100, "本地内容已放进对应文件夹")
    log(
        f"本地导入完成：{imported} 个文件"
        + (f"，跳过 {skipped} 个同名文件" if skipped else "")
    )
    return LocalContentResult(
        imported_files=imported,
        skipped_files=skipped,
        imported_bytes=imported_bytes,
        targets=targets,
    )


# ---------------------------------------------------------------------------
# 游戏版本文件夹

@dataclass
class VersionFolderCandidate:
    version_id: str
    json_path: Path
    version_root: Path
    game_root: Path | None
    layout: str
    json_data: dict[str, Any]
    jar_path: Path | None = None
    mods: int = 0
    config: int = 0
    resourcepacks: int = 0
    shaderpacks: int = 0
    saves: int = 0
    libraries: int = 0
    assets: int = 0
    total_files: int = 0
    total_bytes: int = 0

    @property
    def has_jar(self) -> bool:
        return self.jar_path is not None and self.jar_path.is_file()

    @property
    def layout_label(self) -> str:
        return {
            "game_dir": "完整游戏目录",
            "folder": "版本文件夹",
            "nested": "嵌套版本目录",
        }.get(self.layout, self.layout)


@dataclass
class VersionFolderInfo:
    path: Path
    candidates: list[VersionFolderCandidate]

    @property
    def primary(self) -> VersionFolderCandidate:
        if not self.candidates:
            raise LocalImportError("文件夹里没有找到 Minecraft 版本 JSON")
        return self.candidates[0]

    def find(self, json_path: Path | str = "", version_id: str = "") -> VersionFolderCandidate:
        if json_path:
            target = Path(json_path)
            for candidate in self.candidates:
                if candidate.json_path == target:
                    return candidate
        if version_id:
            for candidate in self.candidates:
                if candidate.version_id == version_id:
                    return candidate
        return self.primary


def _read_version_json(path: Path) -> dict[str, Any] | None:
    try:
        if path.stat().st_size > MAX_JSON_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    if not isinstance(data.get("id"), str) or not data["id"].strip():
        return None
    if len(_VERSION_MARKERS.intersection(data)) < 3:
        return None
    return data


def _direct_version_json(root: Path) -> Path | None:
    preferred = root / f"{root.name}.json"
    if preferred.is_file() and _read_version_json(preferred):
        return preferred
    try:
        children = sorted(root.glob("*.json"), key=lambda item: item.name.lower())
    except OSError:
        return None
    for path in children:
        if path.name.lower() in {"manifest.json", "modrinth.index.json"}:
            continue
        if _read_version_json(path):
            return path
    return None


def _find_jar(version_root: Path, json_path: Path, data: dict[str, Any]) -> Path | None:
    ids = {
        json_path.stem,
        str(data.get("id") or ""),
        str(data.get("jar") or ""),
        "minecraft",
    }
    for version_id in ids:
        if not version_id:
            continue
        for name in (f"{version_id}.jar", f"{version_id}.JAR"):
            candidate = version_root / name
            if candidate.is_file():
                return candidate
    for candidate in (version_root / "bin" / "minecraft.jar", version_root / "bin" / f"{json_path.stem}.jar"):
        if candidate.is_file():
            return candidate
    return None


def _walk_files(root: Path, *, skip_top: set[str] | None = None) -> list[tuple[Path, str]]:
    files: list[tuple[Path, str]] = []
    skip_top = skip_top or set()
    for current, dirs, names in os.walk(root, followlinks=False):
        current_path = Path(current)
        rel_dir = current_path.relative_to(root)
        dirs[:] = sorted(
            name
            for name in dirs
            if not (
                (not rel_dir.parts and name.lower() in skip_top)
                or name.lower() in _SKIP_TOP_DIRS
            )
        )
        for name in sorted(names):
            path = current_path / name
            if path.is_symlink():
                raise LocalImportError(f"文件夹里不允许符号链接：{path}")
            if _is_junk_name(name):
                continue
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            files.append((path, relative.as_posix()))
    return files


def _count_candidate(candidate: VersionFolderCandidate) -> None:
    files = _walk_files(candidate.version_root)
    counts = {
        "mods": 0,
        "config": 0,
        "resourcepacks": 0,
        "shaderpacks": 0,
        "saves": 0,
    }
    total = 0
    for _path, relative in files:
        total += 1
        first = relative.split("/", 1)[0].lower()
        if first in counts:
            counts[first] += 1
    libraries = assets = 0
    if candidate.game_root:
        for path, _relative in _walk_files(candidate.game_root):
            total += 1
            try:
                relative = path.relative_to(candidate.game_root)
            except ValueError:
                continue
            first = relative.parts[0].lower() if relative.parts else ""
            if first == "libraries":
                libraries += 1
            elif first == "assets":
                assets += 1
    candidate.mods = counts["mods"]
    candidate.config = counts["config"]
    candidate.resourcepacks = counts["resourcepacks"]
    candidate.shaderpacks = counts["shaderpacks"]
    candidate.saves = counts["saves"]
    candidate.libraries = libraries
    candidate.assets = assets
    candidate.total_files = total
    candidate.total_bytes = sum(
        max(0, path.stat().st_size)
        for path, _relative in files
        if path.exists()
    )


def _candidate_at(
    version_root: Path, game_root: Path | None, layout: str
) -> VersionFolderCandidate | None:
    json_path = _direct_version_json(version_root)
    if not json_path:
        return None
    data = _read_version_json(json_path)
    if not data:
        return None
    version_id = str(data.get("id") or json_path.stem or version_root.name).strip()
    if not version_id:
        return None
    candidate = VersionFolderCandidate(
        version_id=version_id,
        json_path=json_path,
        version_root=version_root,
        game_root=game_root,
        layout=layout,
        json_data=data,
        jar_path=_find_jar(version_root, json_path, data),
    )
    _count_candidate(candidate)
    return candidate


def inspect_version_folder(path: Path | str) -> VersionFolderInfo:
    source = Path(path)
    if not source.is_dir():
        raise LocalImportError("版本文件夹不存在")
    candidates: list[VersionFolderCandidate] = []
    versions_root = source / "versions"
    if versions_root.is_dir():
        for child in sorted(versions_root.iterdir(), key=lambda item: item.name.lower()):
            if child.is_dir():
                candidate = _candidate_at(child, source, "game_dir")
                if candidate:
                    candidates.append(candidate)
    if not candidates:
        candidate = _candidate_at(source, None, "folder")
        if candidate:
            candidates.append(candidate)
    if not candidates:
        try:
            children = sorted(source.iterdir(), key=lambda item: item.name.lower())
        except OSError:
            children = []
        for child in children:
            if not child.is_dir():
                continue
            candidate = _candidate_at(child, None, "nested")
            if candidate:
                candidates.append(candidate)
    if not candidates:
        # 再支持一层“包装目录/versions/<版本>”的常见分享结构。
        for child in sorted(source.iterdir(), key=lambda item: item.name.lower()):
            if not child.is_dir():
                continue
            nested_versions = child / "versions"
            if not nested_versions.is_dir():
                continue
            for version_dir in sorted(
                nested_versions.iterdir(), key=lambda item: item.name.lower()
            ):
                if version_dir.is_dir():
                    candidate = _candidate_at(version_dir, child, "game_dir")
                    if candidate:
                        candidates.append(candidate)
    if not candidates:
        raise LocalImportError(
            "文件夹里没有找到 Minecraft 版本 JSON。"
            "请选择包含 <版本名>.json 的版本文件夹或完整游戏目录。"
        )
    candidates.sort(
        key=lambda item: (
            0 if item.has_jar else 1,
            0 if item.layout == "game_dir" else 1,
            -item.total_files,
            item.version_id.lower(),
        )
    )
    return VersionFolderInfo(path=source, candidates=candidates)


@dataclass
class VersionFolderImportResult:
    version_id: str
    imported_files: int
    skipped_files: int
    imported_bytes: int


def import_version_folder(
    folder: Path | str,
    game_dir: Path | str,
    version_id: str = "",
    *,
    candidate_json: Path | str = "",
    complete: bool = True,
    source: str = "official",
    threads: int = 8,
    installer: Any | None = None,
    progress_cb: ProgressCallback | None = None,
    log_cb: LogCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> VersionFolderImportResult:
    game_dir = Path(game_dir)
    info = inspect_version_folder(folder)
    candidate = info.find(candidate_json)
    target_id = normalize_version_id(version_id or candidate.version_id)
    progress = progress_cb or (lambda _p, _m: None)
    log = log_cb or (lambda _m: None)
    cancel = cancel_event or threading.Event()
    progress(2, "读取版本文件夹…")

    target_dir = game_dir / "versions" / target_id
    if target_dir.exists() or version_json_path(game_dir, target_id).exists():
        raise LocalImportError(f"版本 {target_id} 已经存在，请换一个导入名称")
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = target_dir.parent / f".dkl-folder-{uuid.uuid4().hex}"
    staging.mkdir(parents=True, exist_ok=False)

    files = _walk_files(candidate.version_root)
    shared_files: list[tuple[Path, str]] = []
    if candidate.game_root:
        for path, relative in _walk_files(candidate.game_root):
            first = relative.split("/", 1)[0].lower()
            if first in ("libraries", "assets"):
                shared_files.append((path, relative))
    total_files = max(1, len(files) + len(shared_files))
    done = 0
    imported = imported_bytes = 0
    skipped = 0

    try:
        for path, relative in files:
            if cancel.is_set():
                raise LocalImportError("导入已取消")
            if path == candidate.json_path:
                payload = dict(candidate.json_data)
                payload["id"] = target_id
                if str(payload.get("jar") or "") == candidate.version_id:
                    payload["jar"] = target_id
                destination = staging / f"{target_id}.json"
                destination.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            elif candidate.jar_path is not None and path == candidate.jar_path:
                destination = staging / f"{target_id}.jar"
                shutil.copy2(path, destination)
            else:
                destination = staging / PurePosixPath(relative)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)
            imported += 1
            imported_bytes += max(0, path.stat().st_size)
            done += 1
            progress(
                3 + int(45 * done / total_files),
                f"直接复制 {relative}",
            )

        if not (staging / f"{target_id}.json").is_file():
            raise LocalImportError("版本 JSON 复制失败，无法完成导入")

        for path, relative in shared_files:
            if cancel.is_set():
                raise LocalImportError("导入已取消")
            destination = game_dir / PurePosixPath(relative)
            if destination.exists():
                skipped += 1
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)
                imported += 1
                imported_bytes += max(0, path.stat().st_size)
            done += 1
            progress(
                3 + int(45 * done / total_files),
                f"合并 {relative}",
            )

        if cancel.is_set():
            raise LocalImportError("导入已取消")
        os.replace(staging, target_dir)
        log(
            f"文件夹已直接放进 versions/{target_id}"
            + (f"，跳过 {skipped} 个已有依赖文件" if skipped else "")
        )
        if complete:
            progress(50, "补齐依赖库与资源文件…")

            def mapped_progress(percent: int, message: str) -> None:
                if cancel.is_set():
                    raise LocalImportError("导入已取消")
                progress(50 + int(max(0, min(100, percent)) * 0.49), message)

            if installer is None:
                from .installer import Installer

                installer = Installer(
                    game_dir,
                    source=source,
                    progress_cb=mapped_progress,
                    log_cb=log,
                    cancel_event=cancel,
                    threads=threads,
                )
            installer.install(target_id)
        if cancel.is_set():
            raise LocalImportError("导入已取消")
        progress(100, "版本文件夹已经放进猫窝")
        log(f"版本文件夹导入完成：{target_id} · {human_size(imported_bytes)}")
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise

    return VersionFolderImportResult(
        version_id=target_id,
        imported_files=imported,
        skipped_files=skipped,
        imported_bytes=imported_bytes,
    )


# ---------------------------------------------------------------------------
# 统一识别

@dataclass
class LocalImportInfo:
    path: Path
    kind: str
    label: str
    title: str
    detail: str
    version_folder: VersionFolderInfo | None = None
    content_plan: ContentPlan | None = None


def _modpack_info(path: Path) -> LocalImportInfo | None:
    from .curseforge_pack import looks_like_curseforge_pack
    from .modrinth_pack import looks_like_modrinth_pack
    from .packs import inspect_pack

    try:
        pack = inspect_pack(path)
    except PackError:
        pack = None
    except Exception:
        pack = None
    if pack is not None:
        return LocalImportInfo(
            path=path,
            kind="modpack",
            label="DKL 整合包",
            title=str(getattr(pack, "name", "") or path.stem),
            detail="交给整合包导入流程，保留版本、配置和 Mod。",
        )
    try:
        is_modrinth = looks_like_modrinth_pack(path)
    except Exception:
        is_modrinth = False
    if is_modrinth:
        return LocalImportInfo(
            path=path,
            kind="modpack",
            label="Modrinth 整合包",
            title=path.stem,
            detail="交给 Modrinth 整合包导入流程。",
        )
    try:
        is_curseforge = looks_like_curseforge_pack(path)
    except Exception:
        is_curseforge = False
    if is_curseforge:
        return LocalImportInfo(
            path=path,
            kind="modpack",
            label="CurseForge 整合包",
            title=path.stem,
            detail="交给 CurseForge 整合包导入流程。",
        )
    return None


def _server_info(path: Path) -> LocalImportInfo | None:
    from .server_pack import looks_like_server_pack

    try:
        if not looks_like_server_pack(path):
            return None
    except Exception:
        return None
    return LocalImportInfo(
        path=path,
        kind="server",
        label="服务端包",
        title=path.stem if path.is_file() else path.name,
        detail="交给服务端导入流程，保留世界、玩家与权限设置。",
    )


def _content_info(path: Path) -> LocalImportInfo | None:
    try:
        plan = inspect_content_path(path)
    except LocalImportError:
        return None
    return LocalImportInfo(
        path=path,
        kind="content",
        label=plan.label,
        title=path.stem if path.is_file() else path.name,
        detail=plan.summary,
        content_plan=plan,
    )


def detect_local_import(path: Path | str) -> LocalImportInfo:
    source = Path(path)
    if not source.exists():
        raise LocalImportError("找不到要导入的文件")
    if source.is_dir():
        server = _server_info(source)
        if server:
            return server
        try:
            info = inspect_version_folder(source)
        except LocalImportError as exc:
            raise LocalImportError(
                "这个文件夹既不像服务端，也没有找到 Minecraft 版本 JSON"
            ) from exc
        candidate = info.primary
        return LocalImportInfo(
            path=source,
            kind="version_folder",
            label="版本文件夹",
            title=candidate.version_id,
            detail=(
                f"{candidate.layout_label} · {candidate.total_files} 个文件 · "
                f"{human_size(candidate.total_bytes)}"
            ),
            version_folder=info,
        )

    suffix = source.suffix.lower()
    if suffix == ".mrpack":
        return LocalImportInfo(
            path=source,
            kind="modpack",
            label="Modrinth 整合包",
            title=source.stem,
            detail="交给 Modrinth 整合包导入流程。",
        )
    if suffix == ".dklpack":
        pack_info = _modpack_info(source)
        if pack_info:
            return pack_info
        raise LocalImportError("这个 .dklpack 文件无法读取")
    if suffix == ".jar" or suffix == ".litemod" or source.name.lower().endswith(
        MOD_DISABLED_SUFFIXES
    ):
        content = _content_info(source)
        if content:
            return content
        raise LocalImportError("这个 Mod 文件无法读取")
    if suffix != ".zip":
        raise LocalImportError("只支持文件夹、.zip、.dklpack、.mrpack、.jar 和 .litemod")

    pack_info = _modpack_info(source)
    if pack_info:
        return pack_info
    server = _server_info(source)
    if server:
        return server
    try:
        info = inspect_version_zip(source)
    except VersionZipError:
        info = None
    except Exception:
        info = None
    if info is not None:
        candidate = info.primary
        return LocalImportInfo(
            path=source,
            kind="version_zip",
            label="版本压缩包",
            title=candidate.version_id,
            detail=(
                f"{candidate.layout_label} · {candidate.total_files} 个文件 · "
                f"{human_size(candidate.total_bytes)}"
            ),
        )
    content = _content_info(source)
    if content:
        return content
    raise LocalImportError(
        "这个 ZIP 既不是版本包、整合包、服务端，也不像材质 / 光影 / Mod 合集。"
    )


def unique_content_name(game_dir: Path | str, base: str) -> str:
    try:
        return unique_version_id(game_dir, base)
    except PackError as exc:
        raise LocalImportError(str(exc)) from exc
