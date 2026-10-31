from __future__ import annotations

import hashlib
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .modrinth import (
    ModrinthError,
    download_mod_file,
    fetch_mod_versions,
    lookup_version_by_hash,
    version_supports,
)
from .paths import version_game_dir
from .versions import ResolvedVersion, resolve_version

ProgressCallback = Callable[[int, str], None]
LogCallback = Callable[[str], None]

CATEGORY_DIRS: dict[str, str] = {
    "saves": "saves",
    "config": "config",
    "resourcepacks": "resourcepacks",
    "shaderpacks": "shaderpacks",
}

CATEGORY_NAMES: dict[str, str] = {
    "saves": "存档",
    "config": "配置",
    "resourcepacks": "资源包",
    "shaderpacks": "光影",
    "mods": "模组",
    "options": "选项与服务器列表",
}

OPTION_FILES = ("options.txt", "optionsof.txt", "servers.dat")

DEFAULT_CATEGORIES = (
    "saves",
    "config",
    "resourcepacks",
    "shaderpacks",
    "mods",
    "options",
)


class MigrationError(Exception):
    pass


class MigrationCancelled(Exception):
    pass


@dataclass
class ModCandidate:
    path: Path
    filename: str
    disabled: bool
    size: int = 0
    sha1: str = ""

    @property
    def base_name(self) -> str:
        return self.filename[: -len(".disabled")] if self.disabled else self.filename

    @property
    def target_name(self) -> str:
        return self.base_name + (".disabled" if self.disabled else "")


@dataclass
class MigrationPlan:
    game_dir: Path
    source_id: str
    target_id: str
    source_mc: str
    target_mc: str
    source_loader: str
    target_loader: str
    source_dir: Path
    target_dir: Path
    categories: list[str]
    mods: list[ModCandidate] = field(default_factory=list)
    file_counts: dict[str, int] = field(default_factory=dict)
    total_files: int = 0
    total_bytes: int = 0
    conflicts: int = 0
    same_dir: bool = False


@dataclass
class MigrationReport:
    source_id: str
    target_id: str
    copied_files: int = 0
    skipped_files: int = 0
    matched_mods: int = 0
    copied_mods: int = 0
    skipped_mods: int = 0
    copied_bytes: int = 0
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"迁移 {self.copied_files} 个文件"]
        if self.matched_mods:
            parts.append(f"匹配 {self.matched_mods} 个 Mod")
        if self.copied_mods:
            parts.append(f"迁移 {self.copied_mods} 个 Mod")
        if self.skipped_files or self.skipped_mods:
            parts.append(f"跳过 {self.skipped_files + self.skipped_mods} 项")
        if self.warnings:
            parts.append(f"{len(self.warnings)} 条提醒")
        return "，".join(parts)


def _check_cancel(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise MigrationCancelled()


def _mc_version(resolved: ResolvedVersion) -> str:
    return str(resolved.chain[0] if resolved.chain else resolved.id)


def _iter_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    result: list[Path] = []
    for path in root.rglob("*"):
        try:
            if path.is_symlink() or not path.is_file():
                continue
        except OSError:
            continue
        result.append(path)
    return result


def _mod_candidates(source_dir: Path) -> list[ModCandidate]:
    mods_dir = source_dir / "mods"
    if not mods_dir.is_dir():
        return []
    result: list[ModCandidate] = []
    for path in sorted(mods_dir.iterdir(), key=lambda item: item.name.lower()):
        try:
            if path.is_symlink() or not path.is_file():
                continue
        except OSError:
            continue
        name = path.name
        disabled = name.lower().endswith(".jar.disabled")
        if not disabled and not name.lower().endswith(".jar"):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        result.append(
            ModCandidate(
                path=path,
                filename=name,
                disabled=disabled,
                size=size,
            )
        )
    return result


def collect_migration_plan(
    game_dir: Path,
    source_id: str,
    target_id: str = "",
    categories: list[str] | tuple[str, ...] | None = None,
    isolated: bool = True,
) -> MigrationPlan:
    game_dir = Path(game_dir)
    selected = [
        key
        for key in (categories or DEFAULT_CATEGORIES)
        if key in CATEGORY_DIRS or key in ("mods", "options")
    ]
    try:
        source_resolved = resolve_version(game_dir, source_id)
    except Exception as exc:
        raise MigrationError(f"无法读取源版本 {source_id}：{exc}") from exc

    target_resolved: ResolvedVersion | None = None
    if target_id:
        try:
            target_resolved = resolve_version(game_dir, target_id)
        except Exception as exc:
            raise MigrationError(f"无法读取目标版本 {target_id}：{exc}") from exc

    source_dir = version_game_dir(game_dir, source_id, isolated)
    target_dir = (
        version_game_dir(game_dir, target_id, isolated)
        if target_id
        else source_dir
    )
    same_dir = bool(target_id) and source_dir.resolve() == target_dir.resolve()

    counts: dict[str, int] = {}
    total_bytes = 0
    total_files = 0
    conflicts = 0

    for key in selected:
        if key in CATEGORY_DIRS:
            files = _iter_files(source_dir / CATEGORY_DIRS[key])
            counts[key] = len(files)
            for path in files:
                try:
                    total_bytes += path.stat().st_size
                except OSError:
                    pass
                if target_id and not same_dir:
                    rel = path.relative_to(source_dir / CATEGORY_DIRS[key])
                    if (target_dir / CATEGORY_DIRS[key] / rel).exists():
                        conflicts += 1
            total_files += len(files)
        elif key == "options":
            files = [
                source_dir / name
                for name in OPTION_FILES
                if (source_dir / name).is_file()
            ]
            counts[key] = len(files)
            for path in files:
                try:
                    total_bytes += path.stat().st_size
                except OSError:
                    pass
                if target_id and not same_dir and (target_dir / path.name).exists():
                    conflicts += 1
            total_files += len(files)

    mods = _mod_candidates(source_dir) if "mods" in selected else []
    counts["mods"] = len(mods)
    total_files += len(mods)
    total_bytes += sum(mod.size for mod in mods)
    if target_id and not same_dir:
        target_mods = target_dir / "mods"
        conflicts += sum(1 for mod in mods if (target_mods / mod.target_name).exists())

    return MigrationPlan(
        game_dir=game_dir,
        source_id=source_id,
        target_id=target_id,
        source_mc=_mc_version(source_resolved),
        target_mc=_mc_version(target_resolved) if target_resolved else "",
        source_loader=source_resolved.loader,
        target_loader=target_resolved.loader if target_resolved else "",
        source_dir=source_dir,
        target_dir=target_dir,
        categories=selected,
        mods=mods,
        file_counts=counts,
        total_files=total_files,
        total_bytes=total_bytes,
        conflicts=conflicts,
        same_dir=same_dir,
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


def _copy_file(source: Path, target: Path) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    try:
        return target.stat().st_size
    except OSError:
        return 0


def _copy_mod(mod: ModCandidate, target: Path) -> int:
    if target.exists():
        return 0
    return _copy_file(mod.path, target)


def _find_compatible_mod(
    mod: ModCandidate,
    loader: str,
    mc_version: str,
    log: LogCallback,
) -> dict | None:
    if not loader or not mc_version:
        return None
    try:
        if not mod.sha1:
            mod.sha1 = _sha1_file(mod.path)
        cached = lookup_version_by_hash(mod.sha1)
    except Exception as exc:
        log(f"{mod.base_name}: 查询 Modrinth 失败（{exc}）")
        return None
    if isinstance(cached, dict) and version_supports(cached, loader, mc_version):
        return cached
    project_id = ""
    if isinstance(cached, dict):
        project_id = str(cached.get("project_id") or "")
        if not project_id:
            project_id = str(cached.get("projectId") or "")
    if not project_id:
        return None
    try:
        versions = fetch_mod_versions(project_id, loader, mc_version)
    except ModrinthError as exc:
        log(f"{mod.base_name}: 获取目标版本失败（{exc}）")
        return None
    if not versions:
        return None
    preferred = [version for version in versions if version_supports(version, loader, mc_version)]
    return preferred[0] if preferred else None


def run_migration(
    plan: MigrationPlan,
    *,
    match_mods: bool = True,
    copy_unmatched: bool = True,
    overwrite: bool = False,
    progress_cb: ProgressCallback | None = None,
    log_cb: LogCallback | None = None,
    cancel_event: threading.Event | None = None,
    threads: int = 8,
) -> MigrationReport:
    progress = progress_cb or (lambda p, m: None)
    log = log_cb or (lambda m: None)
    report = MigrationReport(source_id=plan.source_id, target_id=plan.target_id)

    # ---------------------------------------------------------------- 复制目录
    tasks: list[tuple[Path, Path, str]] = []
    if not plan.same_dir:
        for key in plan.categories:
            if key in CATEGORY_DIRS:
                root = plan.source_dir / CATEGORY_DIRS[key]
                for source in _iter_files(root):
                    rel = source.relative_to(root)
                    tasks.append(
                        (source, plan.target_dir / CATEGORY_DIRS[key] / rel, key)
                    )
            elif key == "options":
                for name in OPTION_FILES:
                    source = plan.source_dir / name
                    if source.is_file():
                        tasks.append((source, plan.target_dir / name, key))

    log(
        f"迁移计划：{plan.source_id} → {plan.target_id or '（安装中）'}"
        f" · {plan.total_files} 个文件 · {plan.total_bytes / 1024 / 1024:.1f} MB"
    )
    if plan.same_dir:
        log("源版本与目标版本共用同一个游戏目录，跳过内容复制，只处理 Mod 匹配。")
    if plan.total_files == 0:
        log("没有找到可迁移的内容。")

    total_tasks = max(1, len(tasks))
    for index, (source, target, category) in enumerate(tasks):
        _check_cancel(cancel_event)
        progress(
            int(index * 60 / total_tasks),
            f"正在迁移{CATEGORY_NAMES.get(category, category)} "
            f"{index + 1}/{len(tasks)}",
        )
        try:
            if target.exists() and not overwrite:
                report.skipped_files += 1
                continue
            report.copied_bytes += _copy_file(source, target)
            report.copied_files += 1
        except Exception as exc:
            report.warnings.append(f"{source.name}: 复制失败（{exc}）")
            log(f"复制失败：{source}（{exc}）")

    progress(60, "内容复制完成，正在处理 Mod…")

    # ---------------------------------------------------------------- 迁移 Mod
    target_mods = plan.target_dir / "mods"
    mod_total = max(1, len(plan.mods))
    loader_key = str(plan.target_loader or "").strip().lower()
    for index, mod in enumerate(plan.mods):
        _check_cancel(cancel_event)
        percent = 60 + int(index * 36 / mod_total)
        progress(percent, f"正在处理 Mod {index + 1}/{len(plan.mods)} · {mod.base_name}")
        target = target_mods / mod.target_name
        base_target = target_mods / mod.base_name
        if target.exists() and not overwrite:
            report.skipped_mods += 1
            continue
        if mod.disabled and base_target.exists() and not target.exists():
            # 目标里已有同名启用文件，不再额外放一个禁用副本。
            report.skipped_mods += 1
            log(f"{mod.base_name}: 目标已有启用版本，跳过禁用副本")
            continue

        matched: dict | None = None
        if match_mods:
            matched = _find_compatible_mod(mod, loader_key, plan.target_mc, log)

        if matched is not None:
            try:
                log(f"{mod.base_name}: 匹配到 Modrinth 目标版本")
                downloaded = download_mod_file(
                    matched,
                    target_mods,
                    progress_cb=lambda p, m, i=index: progress(
                        60 + int((i + p / 100.0) * 36 / mod_total), m
                    ),
                    cancel_event=cancel_event,
                    threads=threads,
                )
                final = target
                if mod.disabled:
                    final = target_mods / (downloaded.name + ".disabled")
                if downloaded != final:
                    if final.exists() and not overwrite:
                        report.skipped_mods += 1
                    else:
                        downloaded.replace(final)
                report.matched_mods += 1
                report.copied_mods += 1
                continue
            except MigrationCancelled:
                raise
            except Exception as exc:
                message = f"{mod.base_name}: 下载匹配版本失败（{exc}）"
                report.warnings.append(message)
                log(message)

        if copy_unmatched:
            try:
                size = _copy_mod(mod, target)
                report.copied_mods += 1
                report.copied_bytes += size
                reason = (
                    "未匹配到目标加载器/MC 版本"
                    if match_mods
                    else "未开启 Mod 匹配"
                )
                message = f"{mod.base_name}: {reason}，已保留原文件（可能不兼容）"
                report.warnings.append(message)
                log(message)
            except Exception as exc:
                report.warnings.append(f"{mod.base_name}: 复制失败（{exc}）")
                log(f"Mod 复制失败：{mod.base_name}（{exc}）")
        else:
            report.skipped_mods += 1
            message = f"{mod.base_name}: 未匹配到目标版本，已跳过"
            report.warnings.append(message)
            log(message)

    progress(100, "迁移完成")
    log(report.summary())
    return report
