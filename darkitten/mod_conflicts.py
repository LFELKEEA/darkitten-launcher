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

LOADER_FAMILIES = {
    "fabric": {"fabric"},
    "quilt": {"fabric", "quilt"},
    "forge": {"forge"},
    "neoforge": {"neoforge"},
}


@dataclass
class ModDependency:
    modid: str
    version_range: str = ""
    mandatory: bool = True
    side: str = ""
    reason: str = ""


@dataclass
class ModConflict:
    modid: str
    version_range: str = ""


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
    conflicts: list[ModConflict] = field(default_factory=list)
    conflicts_by_loader: dict[str, list[ModConflict]] = field(
        default_factory=dict
    )
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


def _fabric_conflict(value: Any, modid: str) -> ModConflict:
    version_range = ""
    if isinstance(value, str):
        version_range = value
    elif isinstance(value, list):
        version_range = " || ".join(str(item) for item in value if item)
    elif isinstance(value, dict):
        version_range = _clean(value.get("version") or value.get("versions"))
    return ModConflict(modid=modid, version_range=version_range.strip())


def _parse_fabric(
    data: dict,
) -> tuple[list[str], list[ModDependency], list[ModConflict]]:
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
    conflicts: list[ModConflict] = []
    for key in ("breaks", "conflicts"):
        raw = data.get(key)
        if isinstance(raw, dict):
            for modid, value in raw.items():
                modid = _clean(modid)
                if not modid or modid.lower() in PLATFORM_IDS:
                    continue
                conflicts.append(_fabric_conflict(value, modid))
        elif isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    modid = _clean(item.get("id") or item.get("modid"))
                    if not modid or modid.lower() in PLATFORM_IDS:
                        continue
                    conflicts.append(_fabric_conflict(item, modid))
                elif _clean(item):
                    conflicts.append(ModConflict(modid=_clean(item)))
    return modids, deps, conflicts


def _parse_forge(
    data: dict,
) -> tuple[list[str], list[ModDependency], list[ModConflict]]:
    modids: list[str] = []
    for mod in data.get("mods") or []:
        if isinstance(mod, dict) and _clean(mod.get("modId")):
            modids.append(_clean(mod.get("modId")))
    deps: list[ModDependency] = []
    conflicts: list[ModConflict] = []
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
                    conflicts.append(
                        ModConflict(
                            modid=modid,
                            version_range=_clean(
                                item.get("versionRange")
                                or item.get("version")
                            ),
                        )
                    )
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
            seen_conflicts: set[tuple[str, str]] = set()
            for key, _entries in groups:
                for conflict in result.conflicts_by_loader.get(key, []):
                    marker = (conflict.modid.lower(), conflict.version_range)
                    if marker in seen_conflicts:
                        continue
                    seen_conflicts.add(marker)
                    result.conflicts.append(conflict)
    except (zipfile.BadZipFile, OSError, ValueError, UnicodeDecodeError) as exc:
        result.valid = False
        result.error = str(exc)
    return result


def _numeric_core(value: str) -> str:
    text = str(value or "").strip().lstrip("vV=")
    match = re.match(r"(\d+(?:\.\d+)*)", text)
    return match.group(1) if match else text


def _version_parts(value: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", _numeric_core(value))
    return tuple(int(item) for item in parts[:4]) if parts else ()


def _compare_versions(left: str, right: str) -> int:
    a = _version_parts(left)
    b = _version_parts(right)
    length = max(len(a), len(b))
    a = a + (0,) * (length - len(a))
    b = b + (0,) * (length - len(b))
    return (a > b) - (a < b)


def _version_tokens(value: str) -> list[str]:
    text = str(value or "").strip().lower().lstrip("vV=")
    return [item for item in re.split(r"[.\-_+]", text) if item]


def _wildcard_match(version: str, pattern: str) -> bool | None:
    """匹配 0.8.x / 1.20.* 这类通配版本；不是通配模式时返回 None。"""
    parts = _version_tokens(pattern)
    if not any(item in ("x", "*") for item in parts):
        return None
    if not parts:
        return None
    version_parts = _version_tokens(version)
    if len(version_parts) < len(parts):
        version_parts += ["0"] * (len(parts) - len(version_parts))
    for expected, actual in zip(parts, version_parts):
        if expected in ("x", "*"):
            return True
        expected_num = re.match(r"\d+", expected)
        actual_num = re.match(r"\d+", actual)
        if expected_num and actual_num:
            if int(expected_num.group()) != int(actual_num.group()):
                return False
            continue
        if expected != actual:
            return False
    return len(version_parts) >= len(parts)


def _match_interval(version: str, chunk: str) -> bool:
    match = re.match(
        r"^([\[(])\s*([^,\])]*)\s*(?:,\s*([^,\])]*)\s*)?([\])])$",
        chunk,
    )
    if not match:
        return False
    left_bracket, low, high, right_bracket = match.groups()
    if high is None:
        # Maven 的 [1.2.3] 表示精确版本。
        if left_bracket != "[" or right_bracket != "]":
            return False
        return _compare_versions(version, low) == 0
    if low:
        cmp = _compare_versions(version, low)
        if cmp < 0 or (cmp == 0 and left_bracket == "("):
            return False
    if high:
        cmp = _compare_versions(version, high)
        if cmp > 0 or (cmp == 0 and right_bracket == ")"):
            return False
    return True


def _range_groups(text: str):
    """把「>=1.20 || [1.21,1.22)」拆成若干组，组内空格表示 AND。"""
    for raw in re.split(r"\s*\|\|\s*", text):
        raw = raw.strip()
        if not raw:
            continue
        if raw.startswith(("[", "(")):
            yield [raw]
            continue
        # 先合并「>= 1.20」这类运算符与值之间的空格，再按空格拆分条件。
        raw = re.sub(r"(>=|<=|>|<|=|~|\^|!=)\s+", r"\1", raw)
        tokens = [token for token in re.split(r"[\s,]+", raw) if token]
        if tokens:
            yield tokens


def _match_token(version: str, token: str) -> bool:
    token = token.strip()
    if not token or token == "*":
        return True
    if token.startswith(("[", "(")):
        return _match_interval(version, token)
    if token.startswith(("[", "(", "]")):
        return False

    wildcard = _wildcard_match(version, token)
    if wildcard is not None:
        return wildcard

    operator = ""
    for prefix in ("!=", ">=", "<=", ">", "<", "=", "~", "^"):
        if token.startswith(prefix):
            operator = prefix
            token = token[len(prefix) :].strip()
            break
    if not token:
        return True
    if token.endswith("+"):
        return _compare_versions(version, token[:-1]) >= 0

    cmp = _compare_versions(version, token)
    if operator in ("!=",):
        return cmp != 0
    if operator == ">=":
        return cmp >= 0
    if operator == ">":
        return cmp > 0
    if operator == "<=":
        return cmp <= 0
    if operator == "<":
        return cmp < 0
    if operator == "=":
        return cmp == 0 or _numeric_core(version) == _numeric_core(token)
    if operator == "~":
        target = _version_parts(token)
        actual = _version_parts(version)
        width = min(2, len(target))
        return cmp >= 0 and actual[:width] == target[:width]
    if operator == "^":
        target = _version_parts(token)
        actual = _version_parts(version)
        if not target or not actual or cmp < 0:
            return False
        if target[0] > 0:
            return actual[0] == target[0]
        if len(target) > 1 and target[1] > 0:
            return actual[:2] == target[:2]
        return actual[:3] == target[:3]
    actual_core = _numeric_core(version)
    token_core = _numeric_core(token)
    if actual_core == token_core:
        return True
    # 1.2 可以匹配 1.2.3，但不能把 1.20 当成 1.2 的前缀。
    return actual_core.startswith(token_core + ".")


def version_in_range(version: str, version_range: str) -> bool:
    """支持 Maven 区间、通配符和空格 AND；无法解析时放行，避免误报。"""
    text = str(version_range or "").strip()
    if not text or text in ("*", "any"):
        return True
    version = str(version or "").strip()
    if not version:
        return True
    for group in _range_groups(text):
        if all(_match_token(version, token) for token in group):
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


def _owner_conflicts(
    owner: ModDescriptor, target_loader: str
) -> list[ModConflict]:
    if not owner.conflicts_by_loader:
        return owner.conflicts
    conflicts: list[ModConflict] = []
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
        compatible = LOADER_FAMILIES.get(wanted)
        mismatched: dict[str, list[ModDescriptor]] = {}
        if not compatible:
            # OptiFine / LiteLoader 等不是 Mod 加载器，不能把普通 Mod
            # 全部判成“加载器不匹配”。
            compatible = set()
        for desc in descriptors:
            if not compatible or not desc.valid or not desc.loaders:
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

        seen_conflicts: set[tuple[str, str]] = set()
        for conflict in _owner_conflicts(owner, target_loader):
            key = str(conflict.modid or "").strip().lower()
            marker = (key, conflict.version_range)
            if not key or key in PLATFORM_IDS or marker in seen_conflicts:
                continue
            seen_conflicts.add(marker)
            entry = provided.get(key)
            if entry is None or entry[0] is owner:
                continue
            provider, provider_version = entry
            # 只在已安装版本落入声明的不兼容区间时提示，否则 Sodium
            # 对旧版 Iris / fabric-api 的 breaks 会被误报成当前冲突。
            if not version_in_range(provider_version, conflict.version_range):
                continue
            range_text = (
                f" {conflict.version_range}" if conflict.version_range else ""
            )
            current = f"（当前 {provider_version}）" if provider_version else ""
            issues.append(
                ModIssue(
                    "warning",
                    "声明了冲突",
                    f"{owner.name} 声明与「{conflict.modid}」{range_text} 冲突"
                    f"{current}，两个 Mod 目前同时启用。",
                    owner.path.name,
                    [provider.path.name],
                )
            )

    order = {"error": 0, "warning": 1, "info": 2}
    issues.sort(key=lambda item: (order.get(item.severity, 3), item.title))
    return issues, descriptors
