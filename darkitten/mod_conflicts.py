from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .mods import jar_identity, list_mods, read_jar_declarations

PLATFORM_IDS = {
    "minecraft",
    "java",
    "fabricloader",
    "fabric-loader",
    "quilt_loader",
    "quilt-loader",
    "forge",
    "neoforge",
    "javafml",
    "lowcodefml",
    "mixinextras",
}


@dataclass
class ModDependency:
    modid: str
    version_range: str = ""
    mandatory: bool = True
    side: str = ""
    reason: str = ""


@dataclass
class ModDescriptor:
    path: Path
    name: str
    modids: list[str] = field(default_factory=list)
    version: str = ""
    loader: str = ""
    loaders: list[str] = field(default_factory=list)
    dependencies: list[ModDependency] = field(default_factory=list)
    dependencies_by_loader: dict[str, list[ModDependency]] = field(
        default_factory=dict
    )
    conflicts: list[str] = field(default_factory=list)
    conflicts_by_loader: dict[str, list[str]] = field(default_factory=dict)
    nested_provides: dict[str, str] = field(default_factory=dict)
    valid: bool = True
    error: str = ""


@dataclass
class ModIssue:
    severity: str
    title: str
    message: str
    filename: str = ""
    related: list[str] = field(default_factory=list)

    @property
    def badge(self) -> str:
        return {"error": "错误", "warning": "警告", "info": "提示"}.get(
            self.severity, "提示"
        )


def _clean(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("${") or text.startswith("%"):
        return ""
    return text


def _dep_from_fabric(value: Any, modid: str) -> ModDependency:
    version_range = ""
    if isinstance(value, str):
        version_range = value
    elif isinstance(value, list):
        version_range = " || ".join(str(item) for item in value if item)
    elif isinstance(value, dict):
        version_range = _clean(value.get("version") or value.get("versions"))
    return ModDependency(modid=modid, version_range=version_range)


def _parse_fabric(data: dict) -> tuple[list[str], list[ModDependency], list[str]]:
    modids = [_clean(data.get("id"))] if _clean(data.get("id")) else []
    provides = data.get("provides")
    if isinstance(provides, list):
        modids.extend(_clean(item) for item in provides if _clean(item))
    deps: list[ModDependency] = []
    depends = data.get("depends")
    if isinstance(depends, dict):
        for modid, value in depends.items():
            if _clean(modid).lower() in PLATFORM_IDS:
                continue
            deps.append(_dep_from_fabric(value, _clean(modid)))
    elif isinstance(depends, list):
        # Quilt 的 depends 是对象数组，而不是 Fabric 的字典。
        for item in depends:
            if not isinstance(item, dict):
                continue
            modid = _clean(item.get("id"))
            if not modid or modid.lower() in PLATFORM_IDS:
                continue
            deps.append(_dep_from_fabric(item, modid))
    conflicts: list[str] = []
    for key in ("breaks", "conflicts"):
        raw = data.get(key)
        if isinstance(raw, dict):
            conflicts.extend(_clean(item) for item in raw if _clean(item))
    return modids, deps, conflicts


def _parse_forge(
    data: dict,
) -> tuple[list[str], list[ModDependency], list[str]]:
    modids: list[str] = []
    for mod in data.get("mods") or []:
        if isinstance(mod, dict) and _clean(mod.get("modId")):
            modids.append(_clean(mod.get("modId")))
    deps: list[ModDependency] = []
    conflicts: list[str] = []
    raw_deps = data.get("dependencies")
    if isinstance(raw_deps, dict):
        for _owner, items in raw_deps.items():
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                modid = _clean(item.get("modId"))
                if not modid or modid.lower() in PLATFORM_IDS:
                    continue
                dep_type = _clean(item.get("type")).lower()
                if dep_type == "incompatible":
                    conflicts.append(modid)
                    continue
                if dep_type == "optional":
                    mandatory = False
                elif dep_type == "discouraged":
                    mandatory = False
                elif dep_type == "required":
                    mandatory = True
                else:
                    mandatory = item.get("mandatory", True)
                    if isinstance(mandatory, str):
                        mandatory = mandatory.strip().lower() != "false"
                deps.append(
                    ModDependency(
                        modid=modid,
                        version_range=_clean(
                            item.get("versionRange") or item.get("version")
                        ),
                        mandatory=bool(mandatory),
                        side=_clean(item.get("side")).upper(),
                        reason=_clean(item.get("ordering")),
                    )
                )
    return modids, deps, conflicts


def describe_mod(path: Path) -> ModDescriptor:
    path = Path(path)
    result = ModDescriptor(path=path, name=path.name)
    try:
        with zipfile.ZipFile(path) as zf:
            decl = read_jar_declarations(zf, with_nested_provides=True)
            if not decl.has_metadata:
                return result
            name, version, _description, _icon = jar_identity(decl)
            result.name = name or path.name
            result.version = version
            result.loaders = list(decl.loaders)
            if len(decl.loaders) > 1:
                result.loader = "多加载器"
            elif decl.loaders:
                result.loader = decl.loaders[0]

            groups = (
                ("fabric", decl.fabric),
                ("quilt", decl.quilt),
                ("neoforge", decl.neoforge),
                ("forge", decl.forge),
            )
            for key, entries in groups:
                for data in entries:
                    if key in ("fabric", "quilt"):
                        modids, deps, conflicts = _parse_fabric(data)
                    else:
                        modids, deps, conflicts = _parse_forge(data)
                    result.modids.extend(modids)
                    result.dependencies_by_loader.setdefault(key, []).extend(
                        deps
                    )
                    result.conflicts_by_loader.setdefault(key, []).extend(
                        conflicts
                    )

            result.modids = list(dict.fromkeys(result.modids))
            result.nested_provides = dict(decl.nested_provides)
            seen_deps: set[tuple[str, str, bool]] = set()
            for key, _entries in groups:
                for dep in result.dependencies_by_loader.get(key, []):
                    marker = (dep.modid.lower(), dep.version_range, dep.mandatory)
                    if marker in seen_deps:
                        continue
                    seen_deps.add(marker)
                    result.dependencies.append(dep)
            for key, _entries in groups:
                for conflict in result.conflicts_by_loader.get(key, []):
                    if conflict not in result.conflicts:
                        result.conflicts.append(conflict)
    except (zipfile.BadZipFile, OSError, ValueError, UnicodeDecodeError) as exc:
        result.valid = False
        result.error = str(exc)
    return result


def _version_parts(value: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", str(value or ""))
    return tuple(int(item) for item in parts[:4]) if parts else ()


def _compare_versions(left: str, right: str) -> int:
    a = _version_parts(left)
    b = _version_parts(right)
    length = max(len(a), len(b))
    a = a + (0,) * (length - len(a))
    b = b + (0,) * (length - len(b))
    return (a > b) - (a < b)


def _range_chunks(text: str):
    """把「>=1.20 || [1.21,1.22)」拆成单个条件，保留带空格的 Maven 区间。"""
    for raw in re.split(r"\s*\|\|\s*", text):
        raw = raw.strip()
        if not raw:
            continue
        if raw.startswith(("[", "(")):
            yield raw
            continue
        # 先合并「>= 1.20」这类运算符与值之间的空格，再按空格拆分条件。
        raw = re.sub(r"(>=|<=|>|<|=|~|\^)\s+", r"\1", raw)
        for token in raw.split():
            if token:
                yield token


def version_in_range(version: str, version_range: str) -> bool:
    """支持 Maven 区间、单区间和简单前缀；无法解析时放行，避免误报。"""
    text = str(version_range or "").strip()
    if not text or text in ("*", "any"):
        return True
    version = str(version or "").strip()
    if not version:
        return True
    for chunk in _range_chunks(text):
        chunk = chunk.strip()
        if not chunk or chunk == "*":
            return True
        if chunk.startswith("[") or chunk.startswith("("):
            match = re.match(r"^([\[(])\s*([^,\])]*)\s*,\s*([^,\])]*)\s*([\])])$", chunk)
            if not match:
                continue
            left_bracket, low, high, right_bracket = match.groups()
            if low:
                cmp = _compare_versions(version, low)
                if cmp < 0 or (cmp == 0 and left_bracket == "("):
                    continue
            if high:
                cmp = _compare_versions(version, high)
                if cmp > 0 or (cmp == 0 and right_bracket == ")"):
                    continue
            return True
            # continue 表示这个区间不匹配，尝试下一个区间。
        if chunk.startswith(("[", "(", "]")):
            continue
        operator = ""
        for prefix in (">=", "<=", ">", "<", "=", "~", "^"):
            if chunk.startswith(prefix):
                operator = prefix
                chunk = chunk[len(prefix) :].strip()
                break
        cmp = _compare_versions(version, chunk)
        if operator:
            if operator == ">=" and cmp < 0:
                continue
            if operator == ">" and cmp <= 0:
                continue
            if operator == "<=" and cmp > 0:
                continue
            if operator == "<" and cmp >= 0:
                continue
            if operator == "=" and cmp != 0:
                continue
            if operator == "~" and (
                _version_parts(version)[:2] != _version_parts(chunk)[:2]
            ):
                continue
            if operator == "^" and (
                _version_parts(version)[:1] != _version_parts(chunk)[:1]
            ):
                continue
            return True
        if version == chunk or version.startswith(chunk):
            return True
        if cmp == 0:
            return True
    return False


def _dependency_side_applies(side: str) -> bool:
    return str(side or "").upper() not in ("SERVER",)


def _metadata_keys(target_loader: str, available: dict[str, Any]) -> list[str]:
    wanted = str(target_loader or "").strip().lower()
    if wanted == "neoforge":
        return ["neoforge"] if "neoforge" in available else ["forge"]
    if wanted == "forge":
        return ["forge"]
    if wanted == "quilt":
        return ["quilt", "fabric"]
    if wanted == "fabric":
        return ["fabric"]
    return ["fabric", "quilt", "neoforge", "forge"]


def _owner_dependencies(
    owner: ModDescriptor, target_loader: str
) -> list[ModDependency]:
    if not owner.dependencies_by_loader:
        return owner.dependencies
    deps: list[ModDependency] = []
    for key in _metadata_keys(target_loader, owner.dependencies_by_loader):
        deps.extend(owner.dependencies_by_loader.get(key, []))
    return deps


def _owner_conflicts(owner: ModDescriptor, target_loader: str) -> list[str]:
    if not owner.conflicts_by_loader:
        return owner.conflicts
    conflicts: list[str] = []
    for key in _metadata_keys(target_loader, owner.conflicts_by_loader):
        conflicts.extend(owner.conflicts_by_loader.get(key, []))
    return conflicts


def scan_mod_issues(
    mods_dir: Path,
    *,
    target_loader: str = "",
) -> tuple[list[ModIssue], list[ModDescriptor]]:
    mods_dir = Path(mods_dir)
    mods = list_mods(mods_dir)
    descriptors: list[ModDescriptor] = []
    for mod in mods:
        if not mod.enabled:
            continue
        descriptors.append(describe_mod(mod.path))

    issues: list[ModIssue] = []
    provided: dict[str, tuple[ModDescriptor, str]] = {}
    duplicates: dict[str, list[ModDescriptor]] = {}
    fabric_only: list[ModDescriptor] = []
    forge_only: list[ModDescriptor] = []

    for desc in descriptors:
        if not desc.valid:
            issues.append(
                ModIssue(
                    "warning",
                    "无法读取 Mod 元数据",
                    f"{desc.path.name} 不是有效的 JAR，或元数据已损坏：{desc.error}",
                    desc.path.name,
                )
            )
            continue
        families: set[str] = set()
        for loader in desc.loaders:
            if loader in ("Fabric", "Quilt"):
                families.add("fabric")
            elif loader in ("Forge", "NeoForge"):
                families.add("forge")
        if families == {"fabric"}:
            fabric_only.append(desc)
        elif families == {"forge"}:
            forge_only.append(desc)
        if not desc.modids:
            issues.append(
                ModIssue(
                    "warning",
                    "缺少 Mod ID",
                    "这个文件没有声明 modId，加载器可能无法识别它。",
                    desc.path.name,
                )
            )
        for modid in desc.modids:
            key = modid.lower()
            if key in provided:
                duplicates.setdefault(key, [provided[key][0]]).append(desc)
            else:
                provided[key] = (desc, desc.version)

    for desc in descriptors:
        if not desc.valid:
            continue
        for modid, version in desc.nested_provides.items():
            key = modid.lower()
            if key in provided:
                continue
            provided[key] = (desc, version)

    for modid, group in duplicates.items():
        names = "、".join(sorted({item.name for item in group}))
        issues.append(
            ModIssue(
                "error",
                "Mod ID 重复",
                f"modId「{modid}」被多个文件同时提供：{names}。"
                "游戏可能只加载其中一个，建议只保留一个版本。",
                group[-1].path.name,
                [item.path.name for item in group],
            )
        )

    if fabric_only and forge_only:
        counts: dict[str, int] = {}
        for desc in fabric_only + forge_only:
            label = desc.loaders[0]
            counts[label] = counts.get(label, 0) + 1
        summary = "、".join(
            f"{loader} {count} 个" for loader, count in counts.items()
        )
        issues.append(
            ModIssue(
                "error",
                "加载器混装",
                f"同一个 mods 文件夹里同时存在 {summary}。"
                "Fabric/Quilt 与 Forge/NeoForge 的 Mod 不能混用。",
            )
        )
    if target_loader:
        wanted = target_loader.strip().lower()
        compatible = {
            "fabric": {"fabric"},
            "quilt": {"fabric", "quilt"},
            "forge": {"forge"},
            "neoforge": {"neoforge"},
        }.get(wanted, {wanted})
        mismatched: dict[str, list[ModDescriptor]] = {}
        for desc in descriptors:
            if not desc.valid or not desc.loaders:
                continue
            supported = {loader.lower() for loader in desc.loaders}
            if supported & compatible:
                continue
            mismatched.setdefault(desc.loaders[0], []).append(desc)
        for loader, items in mismatched.items():
            names = "、".join(item.name for item in items[:4])
            issues.append(
                ModIssue(
                    "error",
                    "加载器不匹配",
                    f"当前版本使用 {target_loader}，但这些 Mod 是 {loader} 的：{names}。",
                    items[0].path.name,
                    [item.path.name for item in items],
                )
            )

    for owner in descriptors:
        if not owner.valid:
            continue
        seen_deps: set[tuple[str, str, bool]] = set()
        for dep in _owner_dependencies(owner, target_loader):
            marker = (dep.modid.lower(), dep.version_range, dep.mandatory)
            if marker in seen_deps:
                continue
            seen_deps.add(marker)
            if not _dependency_side_applies(dep.side):
                continue
            key = dep.modid.lower()
            if key in PLATFORM_IDS:
                continue
            entry = provided.get(key)
            if entry is None:
                if dep.mandatory:
                    issues.append(
                        ModIssue(
                            "error",
                            "缺少必需前置",
                            f"{owner.name} 需要前置「{dep.modid}」"
                            f"{'（' + dep.version_range + '）' if dep.version_range else ''}，"
                            "但 mods 文件夹里没有找到。",
                            owner.path.name,
                        )
                    )
                continue
            provider, provider_version = entry
            if (
                dep.version_range
                and provider_version
                and not version_in_range(provider_version, dep.version_range)
            ):
                issues.append(
                    ModIssue(
                        "warning",
                        "前置版本不匹配",
                        f"{owner.name} 要求 {dep.modid} {dep.version_range}，"
                        f"当前安装的是 {provider_version}。",
                        owner.path.name,
                        [provider.path.name],
                    )
                )

        seen_conflicts: set[str] = set()
        for conflict in _owner_conflicts(owner, target_loader):
            key = str(conflict or "").strip().lower()
            if not key or key in seen_conflicts:
                continue
            seen_conflicts.add(key)
            entry = provided.get(key)
            if entry is not None and entry[0] is not owner:
                issues.append(
                    ModIssue(
                        "warning",
                        "声明了冲突",
                        f"{owner.name} 声明与「{conflict}」冲突，两个 Mod 目前同时启用。",
                        owner.path.name,
                        [entry[0].path.name],
                    )
                )

    order = {"error": 0, "warning": 1, "info": 2}
    issues.sort(key=lambda item: (order.get(item.severity, 3), item.title))
    return issues, descriptors
