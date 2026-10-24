from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .fsops import recycle_path
from .mod_conflicts import (
    LOADER_FAMILIES,
    PLATFORM_IDS,
    ModDescriptor,
    _owner_conflicts,
    _owner_dependencies,
    version_in_range,
)

BACKUP_DIR_NAME = ".dkl-mod-backups"
MANIFEST_NAME = "repair.json"
ACTIVE_STATES = ("pending", "launched", "verified", "failed", "partial")


class RepairError(RuntimeError):
    pass


@dataclass
class RepairAction:
    path: Path
    reason: str


@dataclass
class RepairPlan:
    mods_dir: Path
    actions: list[RepairAction] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def files(self) -> list[str]:
        return [item.path.name for item in self.actions]


@dataclass
class RepairFile:
    name: str
    backup_name: str
    size: int
    sha1: str
    reason: str


@dataclass
class RepairBackup:
    directory: Path
    mods_dir: Path
    created_at: str
    state: str = "pending"
    files: list[RepairFile] = field(default_factory=list)
    launched_at: str = ""
    finished_at: str = ""
    exit_code: int | None = None
    runtime_seconds: float | None = None
    note: str = ""

    @property
    def manifest_path(self) -> Path:
        return self.directory / MANIFEST_NAME


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _backup_root(mods_dir: Path) -> Path:
    return Path(mods_dir).parent / BACKUP_DIR_NAME


def _sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_manifest(backup: RepairBackup) -> None:
    data = {
        "version": 1,
        "mods_dir": str(backup.mods_dir),
        "created_at": backup.created_at,
        "state": backup.state,
        "launched_at": backup.launched_at,
        "finished_at": backup.finished_at,
        "exit_code": backup.exit_code,
        "runtime_seconds": backup.runtime_seconds,
        "note": backup.note,
        "files": [
            {
                "name": item.name,
                "backup_name": item.backup_name,
                "size": item.size,
                "sha1": item.sha1,
                "reason": item.reason,
            }
            for item in backup.files
        ],
    }
    backup.directory.mkdir(parents=True, exist_ok=True)
    temp = backup.manifest_path.with_suffix(".tmp")
    temp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temp.replace(backup.manifest_path)


def load_repair(backup_dir: Path) -> RepairBackup | None:
    backup_dir = Path(backup_dir)
    manifest = backup_dir / MANIFEST_NAME
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        files = [
            RepairFile(
                name=str(item.get("name") or ""),
                backup_name=str(item.get("backup_name") or ""),
                size=int(item.get("size") or 0),
                sha1=str(item.get("sha1") or ""),
                reason=str(item.get("reason") or ""),
            )
            for item in data.get("files") or []
            if isinstance(item, dict)
        ]
        return RepairBackup(
            directory=backup_dir,
            mods_dir=Path(str(data.get("mods_dir") or "")),
            created_at=str(data.get("created_at") or ""),
            state=str(data.get("state") or "pending"),
            files=files,
            launched_at=str(data.get("launched_at") or ""),
            finished_at=str(data.get("finished_at") or ""),
            exit_code=(
                int(data["exit_code"])
                if data.get("exit_code") is not None
                else None
            ),
            runtime_seconds=(
                float(data["runtime_seconds"])
                if data.get("runtime_seconds") is not None
                else None
            ),
            note=str(data.get("note") or ""),
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def latest_repair(mods_dir: Path) -> RepairBackup | None:
    mods_dir = Path(mods_dir)
    root = _backup_root(mods_dir)
    if not root.is_dir():
        return None
    target = mods_dir.resolve()
    found: list[RepairBackup] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        backup = load_repair(child)
        if backup is None or backup.state not in ACTIVE_STATES:
            continue
        try:
            if backup.mods_dir.resolve() != target:
                continue
        except OSError:
            continue
        found.append(backup)
    if not found:
        return None
    found.sort(key=lambda item: item.created_at, reverse=True)
    return found[0]


def _version_key(desc: ModDescriptor, path: Path) -> tuple:
    parts = tuple(
        int(item) for item in re.findall(r"\d+", desc.version)[:4]
    )
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return (parts, len(desc.modids), mtime)


def _loader_compatible(desc: ModDescriptor, compatible: set[str]) -> bool:
    supported = {item.lower() for item in desc.loaders}
    return bool(supported & compatible)


def _add_action(
    actions: dict[Path, RepairAction], path: Path, reason: str
) -> None:
    existing = actions.get(path)
    if existing is None:
        actions[path] = RepairAction(path=path, reason=reason)
    elif reason not in existing.reason:
        existing.reason = f"{existing.reason}；{reason}"


def build_repair_plan(
    mods_dir: Path,
    descriptors: list[ModDescriptor],
    target_loader: str = "",
) -> RepairPlan:
    """根据检查结果生成“移出文件”的修复计划，不执行任何写操作。"""
    mods_dir = Path(mods_dir)
    plan = RepairPlan(mods_dir=mods_dir)
    actions: dict[Path, RepairAction] = {}
    valid: list[ModDescriptor] = []

    for desc in descriptors:
        if not desc.path.exists():
            continue
        if not desc.valid:
            _add_action(actions, desc.path, "无法读取 Mod 元数据")
            continue
        valid.append(desc)

    wanted = target_loader.strip().lower()
    compatible = LOADER_FAMILIES.get(wanted)
    if compatible:
        for desc in valid:
            if desc.loaders and not _loader_compatible(desc, compatible):
                _add_action(
                    actions,
                    desc.path,
                    f"加载器不匹配：当前 {target_loader}，文件是 {desc.loader or '未知'}",
                )
    else:
        if not wanted:
            families = {
                "fabric"
                for desc in valid
                if any(item in ("Fabric", "Quilt") for item in desc.loaders)
            }
            if families and any(
                item in ("Forge", "NeoForge")
                for desc in valid
                for item in desc.loaders
            ):
                plan.skipped.append(
                    "同时存在 Fabric 与 Forge 系 Mod，但当前版本没有识别到加载器，"
                    "无法判断该保留哪一边；请先在加载器页面完成配置。"
                )

    remaining = [item for item in valid if item.path not in actions]

    # 重复 modId：保留版本号最高（同版本则文件更新）的一个。
    provider_files: dict[str, list[ModDescriptor]] = {}
    for desc in remaining:
        for modid in desc.modids:
            provider_files.setdefault(modid.lower(), []).append(desc)
    keepers: dict[str, ModDescriptor] = {}
    duplicates = {
        modid: group
        for modid, group in provider_files.items()
        if len(group) > 1
    }
    for modid, group in duplicates.items():
        keeper = max(group, key=lambda item: _version_key(item, item.path))
        keepers[modid] = keeper
        for desc in group:
            if desc is keeper:
                continue
            _add_action(
                actions,
                desc.path,
                f"重复提供 modId「{modid}」，保留 {keeper.path.name}",
            )

    # 如果某个文件被判定移出，却是另一组重复 ID 的保留项，就不动它，
    # 避免修好一个冲突又拆掉另一个。
    marked = set(actions)
    protected: set[Path] = set()
    for desc in remaining:
        if desc.path not in marked:
            continue
        if any(
            keepers.get(modid.lower()) is desc for modid in desc.modids
        ):
            protected.add(desc.path)
    for path in protected:
        actions.pop(path, None)
        plan.skipped.append(
            f"{path.name} 同时参与多组重复 modId，自动处理可能破坏另一组，"
            "已跳过；请在列表里手动选择保留版本。"
        )

    remaining = [item for item in valid if item.path not in actions]

    # 重建“谁提供了什么”，并记录哪些文件被别的 Mod 强制依赖。
    provided: dict[str, tuple[ModDescriptor, str]] = {}
    for desc in remaining:
        for modid in desc.modids:
            provided.setdefault(modid.lower(), (desc, desc.version))
    for desc in remaining:
        for modid, version in desc.nested_provides.items():
            provided.setdefault(modid.lower(), (desc, version))
    required_by: dict[str, set[Path]] = {}
    for desc in remaining:
        for dep in _owner_dependencies(desc, target_loader):
            if not dep.mandatory:
                continue
            key = dep.modid.lower()
            if key in PLATFORM_IDS:
                continue
            required_by.setdefault(key, set()).add(desc.path)

    def needed(desc: ModDescriptor) -> bool:
        keys = [item.lower() for item in desc.modids]
        keys.extend(item.lower() for item in desc.nested_provides)
        return any(
            required_by.get(key, set()) - {desc.path} for key in keys
        )

    seen_pairs: set[tuple[str, str]] = set()
    for owner in remaining:
        for conflict in _owner_conflicts(owner, target_loader):
            key = str(conflict.modid or "").strip().lower()
            if not key or key in PLATFORM_IDS:
                continue
            entry = provided.get(key)
            if entry is None or entry[0] is owner:
                continue
            provider, provider_version = entry
            if not version_in_range(provider_version, conflict.version_range):
                continue
            pair = (owner.path.name.lower(), provider.path.name.lower())
            if pair in seen_pairs or (pair[1], pair[0]) in seen_pairs:
                continue
            seen_pairs.add(pair)
            range_text = (
                f" {conflict.version_range}" if conflict.version_range else ""
            )
            if needed(provider) and needed(owner):
                plan.skipped.append(
                    f"{owner.name} 与 {provider.name} 互相被其他 Mod 依赖，"
                    f"冲突「{conflict.modid}」{range_text} 需要手动取舍。"
                )
                continue
            target = owner if needed(provider) else provider
            other = provider if target is owner else owner
            _add_action(
                actions,
                target.path,
                f"解决冲突：{owner.name} 声明与「{conflict.modid}」"
                f"{range_text} 不兼容，移出 {target.path.name}（保留 {other.name}）",
            )

    # 缺前置 / 前置版本不符无法靠“移出文件”解决，明确告诉用户。
    for desc in remaining:
        for dep in _owner_dependencies(desc, target_loader):
            if not dep.mandatory or dep.modid.lower() in PLATFORM_IDS:
                continue
            entry = provided.get(dep.modid.lower())
            if entry is None:
                plan.skipped.append(
                    f"{desc.name} 缺少前置「{dep.modid}」，需要下载安装，"
                    "不能通过移出文件解决。"
                )
                continue
            provider, provider_version = entry
            if (
                dep.version_range
                and provider_version
                and not version_in_range(provider_version, dep.version_range)
            ):
                plan.skipped.append(
                    f"{desc.name} 要求 {dep.modid} {dep.version_range}，"
                    f"当前是 {provider_version}；请更新前置而不是移出文件。"
                )

    plan.actions = list(actions.values())
    plan.actions.sort(key=lambda item: item.path.name.lower())
    return plan


def apply_repair(mods_dir: Path, actions: list[RepairAction]) -> RepairBackup:
    mods_dir = Path(mods_dir)
    actions = [item for item in actions if item.path.exists()]
    if not actions:
        raise RepairError("没有可以自动修复的文件")

    root = _backup_root(mods_dir)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = root / f"{stamp}-{uuid.uuid4().hex[:6]}"
    backup = RepairBackup(
        directory=backup_dir,
        mods_dir=mods_dir,
        created_at=_now(),
    )
    moved: list[tuple[Path, Path]] = []
    try:
        backup_dir.mkdir(parents=True, exist_ok=False)
        used_names: set[str] = set()
        for action in actions:
            source = action.path
            try:
                source.resolve().relative_to(mods_dir.resolve())
            except (OSError, ValueError):
                continue
            name = source.name
            backup_name = name
            counter = 1
            while backup_name.lower() in used_names:
                backup_name = f"{source.stem}-{counter}{source.suffix}"
                counter += 1
            used_names.add(backup_name.lower())
            size = source.stat().st_size
            sha1 = _sha1(source)
            destination = backup_dir / backup_name
            shutil.move(str(source), str(destination))
            moved.append((source, destination))
            backup.files.append(
                RepairFile(
                    name=name,
                    backup_name=backup_name,
                    size=size,
                    sha1=sha1,
                    reason=action.reason,
                )
            )
        if not backup.files:
            raise RepairError("没有文件被移入备份")
        backup.state = "pending"
        _write_manifest(backup)
        return backup
    except Exception:
        for source, destination in reversed(moved):
            try:
                if destination.exists() and not source.exists():
                    shutil.move(str(destination), str(source))
            except OSError:
                pass
        try:
            if backup_dir.exists():
                shutil.rmtree(backup_dir)
        except OSError:
            pass
        raise


def restore_repair(
    backup: RepairBackup,
) -> tuple[list[str], list[str]]:
    """把备份文件移回 mods 目录；遇到同名文件绝不覆盖。"""
    restored: list[str] = []
    conflicts: list[str] = []
    backup_root = backup.directory.resolve()
    mods_root = backup.mods_dir.resolve()
    for item in backup.files:
        source = backup.directory / item.backup_name
        target = backup.mods_dir / item.name
        try:
            if source.resolve().parent != backup_root:
                continue
            if target.resolve().parent != mods_root:
                continue
        except OSError:
            continue
        if not source.exists():
            continue
        if target.exists():
            conflicts.append(item.name)
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
            restored.append(item.name)
        except OSError:
            conflicts.append(item.name)
    if conflicts:
        backup.state = "partial"
        backup.note = "以下文件因同名而未还原：" + "、".join(conflicts)
    else:
        backup.state = "restored"
        backup.note = ""
    try:
        _write_manifest(backup)
    except OSError:
        pass
    return restored, conflicts


def mark_repair_launched(mods_dir: Path) -> None:
    backup = latest_repair(mods_dir)
    if backup is None or backup.state == "verified":
        return
    backup.state = "launched"
    backup.launched_at = _now()
    try:
        _write_manifest(backup)
    except OSError:
        pass


def mark_repair_finished(
    mods_dir: Path, exit_code: int, runtime_seconds: float
) -> None:
    backup = latest_repair(mods_dir)
    if backup is None or backup.state not in ("pending", "launched", "failed"):
        return
    backup.finished_at = _now()
    backup.exit_code = int(exit_code)
    backup.runtime_seconds = round(float(runtime_seconds), 1)
    if exit_code != 0:
        backup.state = "failed"
        backup.note = "上次启动异常退出，建议还原备份后排查。"
    elif runtime_seconds >= 20:
        backup.state = "verified"
        backup.note = "游戏已正常运行，备份可以删除了。"
    else:
        backup.state = "launched"
        backup.note = "这次运行时间较短，请再启动一次确认。"
    try:
        _write_manifest(backup)
    except OSError:
        pass


def delete_repair_backup(
    backup: RepairBackup, recycle=recycle_path
) -> None:
    if backup.state != "verified":
        raise RepairError("请先启动一次游戏，确认没有问题后再删除备份")
    if not backup.directory.exists():
        return
    recycle(backup.directory)
