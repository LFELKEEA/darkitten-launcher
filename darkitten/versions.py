from __future__ import annotations

import json
import platform
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

WINDOWS = platform.system().lower() == "windows"
OS_NAME = "windows" if WINDOWS else ("osx" if platform.system() == "Darwin" else "linux")
ARCH_RAW = platform.machine().lower()
if ARCH_RAW in ("amd64", "x86_64", "x64"):
    ARCH = "x86_64"
elif ARCH_RAW in ("arm64", "aarch64"):
    ARCH = "arm64"
elif ARCH_RAW in ("x86", "i386", "i686"):
    ARCH = "x86"
else:
    ARCH = ARCH_RAW

TYPE_NAMES = {
    "release": "正式版",
    "snapshot": "快照",
    "old_beta": "旧测试版",
    "old_alpha": "远古版",
}

LOADER_COLORS = {
    "Forge": "#e08b3f",
    "NeoForge": "#d97706",
    "Fabric": "#d4b483",
    "Quilt": "#a78bfa",
    "OptiFine": "#5aa469",
    "LiteLoader": "#7c9cbf",
}


class VersionError(Exception):
    pass


@dataclass
class VersionSummary:
    id: str
    type: str = "release"
    loader: str = ""
    release_time: str = ""
    json_path: str = ""
    java_major: int = 0
    error: str = ""
    folder_mtime: float = 0.0
    parent_id: str = ""
    base_id: str = ""
    # 纯加载器配置：自身是加载器版本，直接继承一个原版游戏版本。
    # 整合包实例通常会再继承一层加载器，因此不会被误判成加载器。
    is_loader_profile: bool = False

    @property
    def type_name(self) -> str:
        return TYPE_NAMES.get(self.type, self.type or "未知")

    @property
    def display_type(self) -> str:
        return self.loader or self.type_name

    @property
    def loader_version(self) -> str:
        """从版本名里提取加载器版本，失败时返回空字符串。"""
        value = self.id
        lower = value.lower()
        markers = (
            ("fabric-loader-", ""),
            ("quilt-loader-", ""),
        )
        for marker, _ in markers:
            if lower.startswith(marker):
                rest = value[len(marker) :]
                return rest.rsplit("-", 1)[0] if "-" in rest else rest
        if "-forge-" in lower:
            return value.split("-forge-", 1)[1]
        if lower.startswith("neoforge-"):
            return value[len("neoforge-") :]
        if "-neoforge-" in lower:
            return value.split("-neoforge-", 1)[1]
        return ""


def game_versions(versions: Iterable[VersionSummary]) -> list[VersionSummary]:
    return [item for item in versions if not item.is_loader_profile]


def loader_profiles(versions: Iterable[VersionSummary]) -> list[VersionSummary]:
    return [item for item in versions if item.is_loader_profile]


@dataclass
class Artifact:
    path: str
    url: str = ""
    sha1: str = ""
    size: int = 0
    name: str = ""


@dataclass
class NativeArtifact:
    artifact: Artifact
    excludes: list[str] = field(default_factory=list)


@dataclass
class ResolvedVersion:
    id: str
    jar_id: str
    type: str
    release_time: str
    main_class: str
    asset_index_id: str
    asset_index_url: str
    asset_index_sha1: str
    asset_index_size: int
    assets: str
    libraries: list[dict[str, Any]]
    arguments: dict[str, list[Any]]
    minecraft_arguments: str
    default_user_jvm: list[Any]
    logging: dict[str, Any]
    java_major: int
    client_download: dict[str, Any]
    chain: list[str]
    loader: str
    json_path: str
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def client_jar(self) -> str:
        return self.jar_id


# ---------------------------------------------------------------------------
# Rules

def _version_tuple(value: str) -> tuple[int, ...]:
    parts: list[int] = []
    for piece in re.split(r"[._\-+]", str(value)):
        m = re.match(r"(\d+)", piece)
        if m:
            parts.append(int(m.group(1)))
    return tuple(parts)


def _compare_versions(a: str, b: str) -> int:
    ta, tb = _version_tuple(a), _version_tuple(b)
    length = max(len(ta), len(tb))
    ta += (0,) * (length - len(ta))
    tb += (0,) * (length - len(tb))
    return (ta > tb) - (ta < tb)


def _os_matches(os_rule: dict[str, Any]) -> bool:
    name = os_rule.get("name")
    if name and name != OS_NAME:
        return False

    arch = os_rule.get("arch")
    if arch:
        if ARCH == "x86_64":
            if arch not in ("x86_64", "amd64", "x64"):
                return False
        elif ARCH == "arm64":
            if arch not in ("arm64", "aarch64"):
                return False
        elif arch != ARCH:
            return False

    version = os_rule.get("version")
    if version:
        try:
            if not re.search(version, platform.version()):
                return False
        except re.error:
            pass

    version_range = os_rule.get("versionRange")
    if version_range:
        current = platform.version()
        minimum = version_range.get("min")
        maximum = version_range.get("max")
        if minimum and _compare_versions(current, minimum) < 0:
            return False
        if maximum and _compare_versions(current, maximum) > 0:
            return False

    return True


def rules_allowed(
    rules: Iterable[dict[str, Any]] | None,
    features: dict[str, bool] | None = None,
) -> bool:
    if not rules:
        return True
    features = features or {}
    allowed = False
    for rule in rules:
        action = rule.get("action", "allow")
        match = True
        if "os" in rule and not _os_matches(rule["os"]):
            match = False
        if match and "features" in rule:
            for key, value in rule["features"].items():
                if bool(features.get(key, False)) != bool(value):
                    match = False
                    break
        if match:
            allowed = action == "allow"
    return allowed


def features_for(extra: dict[str, bool] | None = None) -> dict[str, bool]:
    features = {
        "is_demo_user": False,
        "has_custom_resolution": False,
        "has_quick_plays_support": False,
        "is_quick_play_singleplayer": False,
        "is_quick_play_multiplayer": False,
        "is_quick_play_realms": False,
    }
    if extra:
        features.update(extra)
    return features


def library_allowed(lib: dict[str, Any], features: dict[str, bool] | None = None) -> bool:
    return rules_allowed(lib.get("rules"), features)


# ---------------------------------------------------------------------------
# Maven helpers

def maven_path(name: str) -> str:
    name = name.split("@", 1)[0]
    parts = name.split(":")
    if len(parts) < 3:
        return ""
    group, artifact, version = parts[0], parts[1], parts[2]
    classifier = parts[3] if len(parts) > 3 else ""
    filename = f"{artifact}-{version}"
    if classifier:
        filename += f"-{classifier}"
    filename += ".jar"
    return "/".join(group.split(".") + [artifact, version, filename])


def library_artifact(lib: dict[str, Any]) -> Artifact | None:
    downloads = lib.get("downloads") or {}
    art = downloads.get("artifact")
    if isinstance(art, dict) and (art.get("path") or art.get("url")):
        path = art.get("path") or maven_path(lib.get("name", ""))
        url = art.get("url", "")
        if not url:
            base = str(lib.get("url") or "https://libraries.minecraft.net/")
            if not base.endswith("/"):
                base += "/"
            url = base + path
        return Artifact(
            path=path,
            url=url,
            sha1=art.get("sha1", ""),
            size=int(art.get("size") or 0),
            name=lib.get("name", ""),
        )

    if downloads:
        # 只提供 classifiers 的库是纯 natives 包，不进入 classpath。
        return None
    path = maven_path(lib.get("name", ""))
    if not path:
        return None
    base = str(lib.get("url") or "https://libraries.minecraft.net/")
    if not base.endswith("/"):
        base += "/"
    return Artifact(path=path, url=base + path, name=lib.get("name", ""))


def natives_classifier(lib: dict[str, Any]) -> str | None:
    natives = lib.get("natives")
    if not isinstance(natives, dict):
        return None
    classifier = natives.get(OS_NAME)
    if not classifier:
        return None
    arch = "64" if ARCH in ("x86_64", "arm64") else "32"
    return str(classifier).replace("${arch}", arch)


def native_artifact(lib: dict[str, Any]) -> NativeArtifact | None:
    classifier = natives_classifier(lib)
    if not classifier:
        return None
    downloads = lib.get("downloads") or {}
    classifiers = downloads.get("classifiers") or {}
    art = classifiers.get(classifier)
    if not isinstance(art, dict):
        return None
    path = art.get("path") or ""
    if not path:
        name = lib.get("name", "")
        parts = name.split(":")
        if len(parts) >= 3:
            group, artifact, version = parts[:3]
            path = "/".join(
                group.split(".")
                + [artifact, version, f"{artifact}-{version}-{classifier}.jar"]
            )
    url = art.get("url", "")
    if not url:
        base = str(lib.get("url") or "https://libraries.minecraft.net/")
        if not base.endswith("/"):
            base += "/"
        url = base + path
    if not path:
        return None
    extract = lib.get("extract") or {}
    excludes = list(extract.get("exclude") or [])
    return NativeArtifact(
        artifact=Artifact(
            path=path,
            url=url,
            sha1=art.get("sha1", ""),
            size=int(art.get("size") or 0),
            name=lib.get("name", ""),
        ),
        excludes=excludes,
    )


def active_library_artifacts(
    resolved: ResolvedVersion, features: dict[str, bool] | None = None
) -> list[Artifact]:
    result: list[Artifact] = []
    seen: set[str] = set()
    for lib in resolved.libraries:
        if not library_allowed(lib, features):
            continue
        art = library_artifact(lib)
        if art and art.path and art.path not in seen:
            seen.add(art.path)
            result.append(art)
    return result


def active_natives(resolved: ResolvedVersion) -> list[NativeArtifact]:
    result: list[NativeArtifact] = []
    seen: set[str] = set()
    for lib in resolved.libraries:
        if not library_allowed(lib, features_for()):
            continue
        native = native_artifact(lib)
        if native and native.artifact.path not in seen:
            seen.add(native.artifact.path)
            result.append(native)
    return result


def detect_loader(chain: list[str], main_class: str, libraries: list[dict[str, Any]]) -> str:
    blob = " ".join(chain).lower()
    main = (main_class or "").lower()
    lib_names = [str(lib.get("name", "")).lower() for lib in libraries]
    # NeoForge 1.20.1 沿用 Forge 的版本命名（如 1.20.1-forge-47.1.106），
    # 需要先看依赖里的 net.neoforged 才能和真正的 Forge 区分开。
    if (
        "neoforge" in blob
        or "neoforge" in main
        or any("net.neoforged" in name for name in lib_names)
    ):
        return "NeoForge"
    if "forge" in blob or "fml" in main or "forge" in main:
        return "Forge"
    if "quilt" in blob or "quilt" in main:
        return "Quilt"
    if "fabric" in blob or "fabric" in main:
        return "Fabric"
    if "optifine" in blob or "optifine" in main:
        return "OptiFine"
    if "liteloader" in blob or "liteloader" in main:
        return "LiteLoader"
    for name in lib_names:
        if "net.minecraftforge" in name:
            return "Forge"
        if "net.fabricmc" in name:
            return "Fabric"
        if "org.quiltmc" in name:
            return "Quilt"
    return ""


# ---------------------------------------------------------------------------
# Loading / resolving versions

def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise VersionError(f"无法读取版本 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise VersionError("版本 JSON 格式不正确")
    return data


def version_json_path(game_dir: Path, version_id: str) -> Path:
    return game_dir / "versions" / version_id / f"{version_id}.json"


def load_chain(game_dir: Path, version_id: str) -> list[dict[str, Any]]:
    chain: list[dict[str, Any]] = []
    seen: set[str] = set()
    current = version_id
    while current:
        if current in seen:
            raise VersionError("版本继承出现循环")
        seen.add(current)
        path = version_json_path(game_dir, current)
        if not path.exists():
            raise VersionError(f"缺少版本文件：{path.name}")
        data = _read_json(path)
        data.setdefault("id", current)
        chain.append(data)
        current = data.get("inheritsFrom") or ""
    chain.reverse()
    return chain


def _merge_libraries(
    chain: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for data in chain:
        for lib in data.get("libraries") or []:
            if not isinstance(lib, dict):
                continue
            name = str(lib.get("name", ""))
            parts = name.split(":")
            key = ":".join(parts[:2]) if len(parts) >= 2 else name
            classifier = parts[3] if len(parts) > 3 else ""
            key = f"{key}:{classifier}"
            if not key:
                key = f"unknown-{len(order)}"
            if key in merged:
                try:
                    order.remove(key)
                except ValueError:
                    pass
            merged[key] = lib
            order.append(key)
    return [merged[k] for k in order]


def _merge_args(chain: list[dict[str, Any]], key: str) -> list[Any]:
    result: list[Any] = []
    for data in chain:
        arguments = data.get("arguments") or {}
        if isinstance(arguments, dict):
            values = arguments.get(key)
            if isinstance(values, list):
                result.extend(values)
    return result


def _last(chain: list[dict[str, Any]], key: str, default: Any = None) -> Any:
    for data in reversed(chain):
        if key in data and data[key] is not None:
            return data[key]
    return default


def resolve_version(game_dir: Path, version_id: str) -> ResolvedVersion:
    chain = load_chain(game_dir, version_id)
    child = chain[-1]

    def tail(key: str, default: Any = None) -> Any:
        for data in reversed(chain):
            value = data.get(key)
            if value:
                return value
        return default

    main_class = str(tail("mainClass", ""))
    libraries = _merge_libraries(chain)
    loader = detect_loader([str(d.get("id", "")) for d in chain], main_class, libraries)

    downloads = {}
    for data in chain:
        downloads.update(data.get("downloads") or {})
    client_download = downloads.get("client") or {}

    jar_id = ""
    for data in reversed(chain):
        if data.get("jar"):
            jar_id = str(data["jar"])
            break
    if not jar_id:
        own_client = (child.get("downloads") or {}).get("client") or {}
        if own_client.get("url"):
            jar_id = str(child.get("id", version_id))
        elif len(chain) > 1:
            # Fabric/Forge/NeoForge 的版本 JSON 只描述加载器，客户端 jar
            # 仍然来自父版本；不能把父级的 downloads.client 误当成本版本自带。
            parent = chain[-2]
            jar_id = str(parent.get("jar") or parent.get("id") or version_id)
        else:
            jar_id = str(child.get("id", version_id))

    asset_index = _last(chain, "assetIndex", {}) or {}
    assets = str(_last(chain, "assets", "") or "")

    java_version = _last(chain, "javaVersion", {}) or {}
    try:
        java_major = int(java_version.get("majorVersion") or 0)
    except Exception:
        java_major = 0

    merged_raw: dict[str, Any] = {}
    for data in chain:
        merged_raw.update(data)

    return ResolvedVersion(
        id=str(child.get("id", version_id)),
        jar_id=jar_id,
        type=str(tail("type", "release")),
        release_time=str(tail("releaseTime", "")),
        main_class=main_class,
        asset_index_id=str(asset_index.get("id") or assets or ""),
        asset_index_url=str(asset_index.get("url") or ""),
        asset_index_sha1=str(asset_index.get("sha1") or ""),
        asset_index_size=int(asset_index.get("size") or 0),
        assets=assets,
        libraries=libraries,
        arguments={
            "game": _merge_args(chain, "game"),
            "jvm": _merge_args(chain, "jvm"),
        },
        minecraft_arguments=str(tail("minecraftArguments", "") or ""),
        default_user_jvm=_merge_args(chain, "default-user-jvm"),
        logging=_last(chain, "logging", {}) or {},
        java_major=java_major,
        client_download=client_download,
        chain=[str(d.get("id", "")) for d in chain],
        loader=loader,
        json_path=str(version_json_path(game_dir, version_id)),
        raw=merged_raw,
    )


def client_jar_path(game_dir: Path, resolved: ResolvedVersion) -> Path:
    path = game_dir / "versions" / resolved.jar_id / f"{resolved.jar_id}.jar"
    if path.exists():
        return path
    own = game_dir / "versions" / resolved.id / f"{resolved.id}.jar"
    return own


def libraries_dir(game_dir: Path) -> Path:
    return game_dir / "libraries"


def assets_dir(game_dir: Path) -> Path:
    return game_dir / "assets"


def natives_dir(game_dir: Path, resolved: ResolvedVersion) -> Path:
    return game_dir / "versions" / resolved.id / "natives"


def asset_index_path(game_dir: Path, resolved: ResolvedVersion) -> Path:
    return assets_dir(game_dir) / "indexes" / f"{resolved.asset_index_id}.json"


# ---------------------------------------------------------------------------
# Scanning

def dependent_versions(game_dir: Path, version_id: str) -> list[str]:
    """返回所有直接或间接依赖 version_id 的已安装版本。"""
    root = Path(game_dir) / "versions"
    if not root.exists():
        return []
    result: list[str] = []
    for folder in root.iterdir():
        if not folder.is_dir() or folder.name == version_id:
            continue
        json_path = folder / f"{folder.name}.json"
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        seen: set[str] = set()
        current = data
        while isinstance(current, dict):
            parent = str(current.get("inheritsFrom") or "")
            if not parent:
                break
            if parent == version_id:
                result.append(folder.name)
                break
            if parent in seen:
                break
            seen.add(parent)
            parent_path = version_json_path(game_dir, parent)
            try:
                current = json.loads(parent_path.read_text(encoding="utf-8"))
            except Exception:
                break
    return result


def scan_versions(game_dir: Path) -> list[VersionSummary]:
    versions_root = game_dir / "versions"
    result: list[VersionSummary] = []
    if not versions_root.exists():
        return result

    for folder in versions_root.iterdir():
        if not folder.is_dir():
            continue
        json_path = folder / f"{folder.name}.json"
        if not json_path.exists():
            continue
        summary = VersionSummary(
            id=folder.name,
            json_path=str(json_path),
            folder_mtime=folder.stat().st_mtime,
        )
        try:
            chain = load_chain(game_dir, folder.name)
            child = chain[-1]
            main_class = ""
            for data in reversed(chain):
                if data.get("mainClass"):
                    main_class = str(data["mainClass"])
                    break
            libs: list[dict[str, Any]] = []
            for data in chain:
                libs.extend(data.get("libraries") or [])
            summary.type = str(child.get("type") or "release")
            summary.release_time = str(child.get("releaseTime") or "")
            summary.loader = detect_loader(
                [str(d.get("id", "")) for d in chain], main_class, libs
            )
            summary.parent_id = str(child.get("inheritsFrom") or "")
            summary.base_id = str(chain[0].get("id") or folder.name)
            if summary.loader and summary.parent_id:
                try:
                    parent_chain = load_chain(game_dir, summary.parent_id)
                    parent_main = ""
                    for data in reversed(parent_chain):
                        if data.get("mainClass"):
                            parent_main = str(data["mainClass"])
                            break
                    parent_libs: list[dict[str, Any]] = []
                    for data in parent_chain:
                        parent_libs.extend(data.get("libraries") or [])
                    parent_loader = detect_loader(
                        [str(d.get("id", "")) for d in parent_chain],
                        parent_main,
                        parent_libs,
                    )
                except Exception:
                    parent_loader = ""
                summary.is_loader_profile = not parent_loader
            for data in reversed(chain):
                java_version = data.get("javaVersion") or {}
                try:
                    summary.java_major = int(java_version.get("majorVersion") or 0)
                except Exception:
                    summary.java_major = 0
                if summary.java_major:
                    break
        except Exception as exc:
            summary.error = str(exc)
            summary.type = "unknown"
        result.append(summary)

    def sort_key(item: VersionSummary) -> tuple:
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})T", item.release_time)
        stamp = 0
        if m:
            try:
                from datetime import datetime

                stamp = datetime(
                    int(m.group(1)), int(m.group(2)), int(m.group(3))
                ).timestamp()
            except Exception:
                stamp = 0
        return (item.error == "", stamp or item.folder_mtime)

    result.sort(key=sort_key, reverse=True)
    return result


def check_installation(
    game_dir: Path, resolved: ResolvedVersion, check_assets: bool = False
) -> list[str]:
    """返回缺失文件的描述列表。"""
    missing: list[str] = []

    jar = client_jar_path(game_dir, resolved)
    if not jar.exists():
        missing.append(f"客户端 {resolved.jar_id}.jar")

    for art in active_library_artifacts(resolved):
        if not (libraries_dir(game_dir) / art.path).exists():
            missing.append(f"依赖库 {art.name or art.path}")

    index_path = asset_index_path(game_dir, resolved)
    if not index_path.exists():
        missing.append("资源索引文件")

    log_file = ((resolved.logging or {}).get("client") or {}).get("file") or {}
    if log_file.get("id"):
        if not (assets_dir(game_dir) / "log_configs" / str(log_file["id"])).exists():
            missing.append("日志配置")

    if check_assets and index_path.exists():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
            objects = index.get("objects") or {}
            objects_root = assets_dir(game_dir) / "objects"
            for _name, obj in objects.items():
                h = str(obj.get("hash", ""))
                if h and not (objects_root / h[:2] / h).exists():
                    missing.append("资源文件")
                    break
        except Exception:
            missing.append("资源索引损坏")

    return missing
