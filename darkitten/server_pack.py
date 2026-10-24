from __future__ import annotations

import json
import os
import re
import shutil
import threading
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterator

from .packs import human_size
from .servers import (
    SERVER_NAMES,
    SERVERS_DIR,
    ServerInfo,
    _safe_folder,
)

SERVER_PACK_MARKER = "dkl-server.json"
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_TOTAL_FILES = 300_000
MAX_TOTAL_BYTES = 128 * 1024**3
_SKIP_TOP_DIRS = {
    "logs",
    "crash-reports",
    "cache",
    "__pycache__",
    ".git",
    "updates",
}
_SKIP_NAMES = {
    "session.lock",
    ".ds_store",
    "thumbs.db",
    "desktop.ini",
}
_SKIP_SUFFIXES = (".tmp", ".part", ".download")
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


class ServerPackError(Exception):
    pass


class ServerPackCancelled(Exception):
    pass


@dataclass
class ServerPackInfo:
    path: Path
    is_zip: bool
    root: Path | None = None
    prefix: str = ""
    manifest: dict[str, Any] = field(default_factory=dict)
    name: str = ""
    kind: str = ""
    mc_version: str = ""
    loader_version: str = ""
    total_files: int = 0
    total_bytes: int = 0
    archive_bytes: int = 0
    worlds: list[str] = field(default_factory=list)
    players: int = 0
    has_whitelist: bool = False
    has_ops: bool = False
    has_banned: bool = False
    installed: bool = True

    @property
    def type_name(self) -> str:
        return SERVER_NAMES.get(self.kind, self.kind or "服务端")

    @property
    def display_version(self) -> str:
        if self.loader_version:
            return f"{self.mc_version or '未知版本'} · {self.loader_version}"
        return self.mc_version or "未知版本"

    @property
    def size_text(self) -> str:
        return human_size(self.total_bytes)

    @property
    def world_text(self) -> str:
        return "、".join(self.worlds) if self.worlds else "未检测到世界目录"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _check_cancel(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise ServerPackCancelled()


def _skip_relative(relative: Path) -> bool:
    parts = relative.parts
    if not parts:
        return False
    if len(parts) == 1 and parts[0].lower() in (SERVER_PACK_MARKER, "server.json"):
        return True
    if parts[0].lower() in _SKIP_TOP_DIRS:
        return True
    name = relative.name.lower()
    if name in _SKIP_NAMES:
        return True
    return name.endswith(_SKIP_SUFFIXES)


def _scan_files(root: Path) -> Iterator[tuple[Path, Path, int]]:
    """遍历服务端文件；不跟随目录链接，跳过日志、缓存与临时文件。"""
    for current, dirs, names in os.walk(root, followlinks=False):
        current_path = Path(current)
        try:
            relative_dir = current_path.relative_to(root)
        except ValueError:
            continue
        if not relative_dir.parts:
            dirs[:] = [d for d in dirs if d.lower() not in _SKIP_TOP_DIRS]
        else:
            dirs[:] = [d for d in dirs if not (current_path / d).is_symlink()]
        dirs.sort()
        for name in sorted(names):
            path = current_path / name
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(root)
            if _skip_relative(relative):
                continue
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            yield relative, path, max(0, int(size))


def _read_json(path: Path) -> dict[str, Any]:
    try:
        if not path.is_file() or path.stat().st_size > MAX_MANIFEST_BYTES:
            return {}
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _read_properties(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return data
    for line in lines:
        raw = line.strip()
        if not raw or raw.startswith(("#", "!")) or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def _json_list_length(path: Path) -> int:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return 0
    return len(data) if isinstance(data, list) else 0


def _eula_accepted(root: Path) -> bool:
    try:
        text = (root / "eula.txt").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return any(
        line.strip().lower() == "eula=true" for line in text.splitlines()
    )


def _world_names(root: Path) -> list[str]:
    props = _read_properties(root / "server.properties")
    level = props.get("level-name", "").strip()
    names: list[str] = []
    if level and (root / level / "level.dat").is_file():
        names.append(level)
    try:
        for item in root.iterdir():
            if not item.is_dir():
                continue
            if (item / "level.dat").is_file() and item.name not in names:
                names.append(item.name)
    except OSError:
        pass
    return names


def _player_count(root: Path, worlds: list[str]) -> int:
    count = 0
    for world in worlds:
        folder = root / world / "playerdata"
        if not folder.is_dir():
            continue
        try:
            count += sum(1 for item in folder.iterdir() if item.suffix.lower() == ".dat")
        except OSError:
            pass
    if count:
        return count
    try:
        data = json.loads((root / "usercache.json").read_text(encoding="utf-8-sig"))
    except Exception:
        return 0
    return len(data) if isinstance(data, list) else 0


def _infer_launch(root: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "kind": "",
        "mc_version": "",
        "loader_version": "",
        "launch_kind": "",
        "launch_target": "",
        "launch_classpath": [],
    }
    for pattern, kind in (
        ("libraries/net/minecraftforge/forge/*/win_args.txt", "forge"),
        ("libraries/net/neoforged/neoforge/*/win_args.txt", "neoforge"),
    ):
        hits = sorted(root.glob(pattern))
        if not hits:
            continue
        target = hits[0]
        result["kind"] = kind
        result["launch_kind"] = "args"
        result["launch_target"] = target.relative_to(root).as_posix()
        if kind == "forge":
            folder = target.parent.name
            if "-" in folder:
                minecraft, loader = folder.split("-", 1)
                result["mc_version"] = minecraft
                result["loader_version"] = loader
        break
    if not result["kind"] and (root / "fabric-server-launch.jar").is_file():
        result["kind"] = "fabric"
        result["launch_kind"] = "jar"
        result["launch_target"] = "fabric-server-launch.jar"
        loader_hits = sorted(root.glob("libraries/net/fabricmc/fabric-loader/*"))
        if loader_hits:
            result["loader_version"] = loader_hits[0].name
    if not result["kind"] and (root / "server.jar").is_file():
        result["kind"] = "paper"
        result["launch_kind"] = "jar"
        result["launch_target"] = "server.jar"

    if not result["mc_version"]:
        result["mc_version"] = _jar_minecraft_version(root / "server.jar")
    if not result["mc_version"]:
        for folder in sorted(root.glob("libraries/net/minecraft/server/*")):
            name = folder.name
            if not name:
                continue
            result["mc_version"] = name.split("-", 1)[0]
            break
    return result


def _jar_minecraft_version(jar: Path) -> str:
    if not jar.is_file():
        return ""
    try:
        with zipfile.ZipFile(jar) as archive:
            try:
                data = json.loads(archive.read("version.json").decode("utf-8-sig"))
            except (KeyError, ValueError, UnicodeDecodeError):
                return ""
    except (OSError, zipfile.BadZipFile):
        return ""
    if not isinstance(data, dict):
        return ""
    for key in ("id", "name", "release_target"):
        value = str(data.get(key) or "").strip()
        if value and re.fullmatch(r"[0-9A-Za-z._-]{1,40}", value):
            return value
    return ""


def _pack_manifest(
    info: ServerInfo,
    root_name: str,
    files: int,
    total_bytes: int,
    worlds: list[str],
    players: int,
) -> dict[str, Any]:
    whitelist = _json_list_length(info.path / "whitelist.json")
    ops = _json_list_length(info.path / "ops.json")
    banned_players = _json_list_length(info.path / "banned-players.json")
    banned_ips = _json_list_length(info.path / "banned-ips.json")
    return {
        "format": "dkl-server",
        "format_version": 1,
        "name": info.name,
        "kind": info.kind,
        "type_name": info.type_name,
        "mc_version": info.mc_version,
        "loader_version": info.loader_version,
        "paper_build": info.paper_build,
        "memory_mb": info.memory_mb,
        "port": info.port,
        "online_mode": info.online_mode,
        "eula": info.eula or _eula_accepted(info.path),
        "created_at": info.created_at,
        "exported_at": _now(),
        "source_folder": info.folder,
        "root": root_name,
        "files": files,
        "bytes": total_bytes,
        "worlds": worlds,
        "players": players,
        "permissions": {
            "whitelist": whitelist,
            "ops": ops,
            "banned_players": banned_players,
            "banned_ips": banned_ips,
            "online_mode": info.online_mode,
        },
    }


def _server_json_for_export(info: ServerInfo, root_name: str) -> dict[str, Any]:
    data = asdict(info)
    data["folder"] = root_name
    data["java_path"] = ""
    data["name"] = info.name
    return data


def _ensure_outside(source: Path, target: Path) -> None:
    try:
        target.resolve().relative_to(source.resolve())
    except ValueError:
        return
    raise ServerPackError("不能把服务端包保存到服务端自己的文件夹里")


def _write_metadata(
    folder: Path,
    info: ServerInfo,
    root_name: str,
    files: int,
    total_bytes: int,
    worlds: list[str],
    players: int,
) -> None:
    manifest = _pack_manifest(info, root_name, files, total_bytes, worlds, players)
    (folder / SERVER_PACK_MARKER).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (folder / "server.json").write_text(
        json.dumps(
            _server_json_for_export(info, root_name), ensure_ascii=False, indent=2
        )
        + "\n",
        encoding="utf-8",
    )


def export_server(
    info: ServerInfo,
    target: Path | str,
    *,
    as_zip: bool = True,
    progress_cb: ProgressCallback | None = None,
    log_cb: LogCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    """打包服务端代码、世界、玩家数据和权限设置；日志与缓存不会写入。"""
    root = Path(info.path)
    if not root.is_dir():
        raise ServerPackError("服务端文件夹不存在")
    destination = Path(target)
    if as_zip and destination.suffix.lower() != ".zip":
        destination = destination.with_suffix(".zip")
    _ensure_outside(root, destination)

    progress = progress_cb or (lambda _p, _m: None)
    log = log_cb or (lambda _m: None)
    progress(2, "扫描服务端文件…")
    files = list(_scan_files(root))
    if not files:
        raise ServerPackError("服务端文件夹里没有可打包的文件")
    total_bytes = sum(item[2] for item in files)
    worlds = _world_names(root)
    players = _player_count(root, worlds)
    root_name = _safe_folder(info.name or info.folder) or info.folder
    log(
        f"打包 {info.name}：{len(files)} 个文件 · {human_size(total_bytes)} · "
        f"世界 {len(worlds)} 个 · 玩家数据 {players} 份"
    )

    if as_zip:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp = destination.with_name(destination.name + ".part")
        try:
            with zipfile.ZipFile(
                temp, "w", zipfile.ZIP_DEFLATED, compresslevel=6
            ) as archive:
                manifest = _pack_manifest(
                    info, root_name, len(files), total_bytes, worlds, players
                )
                archive.writestr(
                    f"{root_name}/{SERVER_PACK_MARKER}",
                    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                )
                archive.writestr(
                    f"{root_name}/server.json",
                    json.dumps(
                        _server_json_for_export(info, root_name),
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n",
                )
                done = 0
                for relative, path, size in files:
                    _check_cancel(cancel_event)
                    archive.write(path, f"{root_name}/{relative.as_posix()}")
                    done += size
                    percent = 5 + int(90 * done / max(1, total_bytes))
                    progress(percent, f"打包 {relative.as_posix()}")
            if destination.exists():
                destination.unlink()
            os.replace(temp, destination)
        except ServerPackCancelled:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        except Exception:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        size = destination.stat().st_size
        progress(100, "ZIP 打包完成")
        log(f"ZIP 已保存：{destination} · {human_size(size)}")
        return destination

    if destination.exists():
        try:
            if any(destination.iterdir()):
                raise ServerPackError("目标文件夹不为空，请换一个位置")
        except OSError as exc:
            raise ServerPackError(f"无法读取目标文件夹：{exc}") from exc
    temp = destination.parent / f".{destination.name}.dkl-export"
    if temp.exists():
        shutil.rmtree(temp, ignore_errors=True)
    temp.mkdir(parents=True, exist_ok=True)
    try:
        done = 0
        for relative, path, size in files:
            _check_cancel(cancel_event)
            dest = temp / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
            done += size
            percent = 5 + int(90 * done / max(1, total_bytes))
            progress(percent, f"复制 {relative.as_posix()}")
        _write_metadata(temp, info, root_name, len(files), total_bytes, worlds, players)
        if destination.exists():
            destination.rmdir()
        os.replace(temp, destination)
    except ServerPackCancelled:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    progress(100, "文件夹打包完成")
    log(f"服务端文件夹已保存：{destination}")
    return destination


# ---------------------------------------------------------------------------
# 识别 / 导入


def _detect_zip_prefix(names: list[str]) -> tuple[str, ...]:
    def collect(marker: str, limit: int) -> list[tuple[str, ...]]:
        found: list[tuple[str, ...]] = []
        for name in names:
            normalized = str(name or "").replace("\\", "/")
            if not normalized or normalized.endswith("/"):
                continue
            parts = PurePosixPath(normalized).parts
            if not parts or len(parts) > limit:
                continue
            if parts[-1].lower() != marker:
                continue
            prefix = tuple(parts[:-1])
            if any(part.lower() == "__macosx" for part in prefix):
                continue
            if prefix not in found:
                found.append(prefix)
        return found

    for marker, limit in (
        (SERVER_PACK_MARKER, 3),
        ("server.json", 3),
        ("server.properties", 3),
        ("eula.txt", 3),
    ):
        found = collect(marker, limit)
        if not found:
            continue
        shortest = min(len(item) for item in found)
        roots = [item for item in found if len(item) == shortest]
        if len(roots) > 1:
            raise ServerPackError(
                "压缩包里包含多个服务端，请解压后单独拖入其中一个文件夹"
            )
        return roots[0]
    raise ServerPackError("这个压缩包不像 Minecraft 服务端包")


def _zip_manifest(zf: zipfile.ZipFile, prefix: tuple[str, ...]) -> dict[str, Any]:
    member = "/".join((*prefix, SERVER_PACK_MARKER))
    try:
        info = zf.getinfo(member)
    except KeyError:
        return {}
    if info.file_size > MAX_MANIFEST_BYTES:
        raise ServerPackError("服务端清单过大，已拒绝读取")
    if info.flag_bits & 0x1:
        raise ServerPackError("服务端包中的文件被加密，无法导入")
    try:
        data = json.loads(zf.read(info).decode("utf-8-sig"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _zip_server_json(zf: zipfile.ZipFile, prefix: tuple[str, ...]) -> dict[str, Any]:
    member = "/".join((*prefix, "server.json"))
    try:
        info = zf.getinfo(member)
        if info.file_size > MAX_MANIFEST_BYTES:
            return {}
        data = json.loads(zf.read(info).decode("utf-8-sig"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _zip_relative(name: str, prefix: tuple[str, ...]) -> str | None:
    normalized = str(name or "").replace("\\", "/")
    parts = PurePosixPath(normalized).parts
    if len(parts) < len(prefix) or tuple(parts[: len(prefix)]) != prefix:
        return None
    return "/".join(parts[len(prefix) :])


def _zip_stats(
    zf: zipfile.ZipFile, prefix: tuple[str, ...]
) -> dict[str, Any]:
    files = 0
    total = 0
    worlds: list[str] = []
    players = 0
    has_whitelist = False
    has_ops = False
    has_banned = False
    installed = False
    for item in zf.infolist():
        if item.is_dir():
            continue
        relative = _zip_relative(item.filename, prefix)
        if not relative:
            continue
        rel = Path(relative)
        if _skip_relative(rel):
            continue
        size = max(0, int(item.file_size))
        total += size
        files += 1
        if files > MAX_TOTAL_FILES:
            raise ServerPackError("服务端包内文件数量过多，已拒绝读取")
        if total > MAX_TOTAL_BYTES:
            raise ServerPackError("服务端包解压后体积超过 128 GB，已拒绝读取")
        lower = relative.lower()
        if lower in ("whitelist.json",):
            has_whitelist = True
        elif lower in ("ops.json",):
            has_ops = True
        elif lower.startswith("banned-"):
            has_banned = True
        elif lower in ("server.jar", "fabric-server-launch.jar"):
            installed = True
        elif lower.endswith("/win_args.txt") or lower.endswith("/unix_args.txt"):
            installed = True
        parts = rel.parts
        if len(parts) == 2 and parts[1].lower() == "level.dat":
            if parts[0] not in worlds:
                worlds.append(parts[0])
        if (
            len(parts) >= 3
            and parts[-2].lower() == "playerdata"
            and parts[-1].lower().endswith(".dat")
        ):
            players += 1
    return {
        "files": files,
        "bytes": total,
        "worlds": worlds,
        "players": players,
        "has_whitelist": has_whitelist,
        "has_ops": has_ops,
        "has_banned": has_banned,
        "installed": installed,
    }


def _find_folder_root(path: Path) -> Path:
    def marker(folder: Path) -> bool:
        return (
            any(
                (folder / name).exists()
                for name in (
                    SERVER_PACK_MARKER,
                    "server.json",
                    "server.properties",
                    "eula.txt",
                )
            )
            or (folder / "server.jar").is_file()
            or (folder / "fabric-server-launch.jar").is_file()
        )

    if marker(path) or (path / "world").joinpath("level.dat").is_file():
        return path
    try:
        candidates = [
            item
            for item in path.iterdir()
            if item.is_dir() and marker(item)
        ]
    except OSError:
        candidates = []
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise ServerPackError(
            "这个文件夹里包含多个服务端，请单独拖入其中一个服务端文件夹"
        )
    raise ServerPackError("这个文件夹不像 Minecraft 服务端文件夹")


def _folder_stats(root: Path) -> dict[str, Any]:
    files = 0
    total = 0
    for _relative, _path, size in _scan_files(root):
        files += 1
        total += size
    if files > MAX_TOTAL_FILES or total > MAX_TOTAL_BYTES:
        raise ServerPackError("服务端文件夹过大，无法打包或导入")
    worlds = _world_names(root)
    return {
        "files": files,
        "bytes": total,
        "worlds": worlds,
        "players": _player_count(root, worlds),
        "has_whitelist": (root / "whitelist.json").is_file(),
        "has_ops": (root / "ops.json").is_file(),
        "has_banned": any(root.glob("banned-*.json")),
        "installed": (
            (root / "server.jar").is_file()
            or (root / "fabric-server-launch.jar").is_file()
            or bool(list(root.glob("libraries/**/win_args.txt")))
            or bool(list(root.glob("libraries/**/unix_args.txt")))
        ),
    }


def _fill_from_source(
    result: ServerPackInfo,
    manifest: dict[str, Any],
    server_data: dict[str, Any],
    stats: dict[str, Any],
    fallback_name: str,
) -> None:
    result.manifest = manifest
    result.name = str(
        manifest.get("name")
        or server_data.get("name")
        or fallback_name
        or "Minecraft 服务端"
    ).strip()
    result.kind = str(manifest.get("kind") or server_data.get("kind") or "").strip()
    result.mc_version = str(
        manifest.get("mc_version") or server_data.get("mc_version") or ""
    ).strip()
    result.loader_version = str(
        manifest.get("loader_version") or server_data.get("loader_version") or ""
    ).strip()
    result.total_files = int(stats.get("files") or manifest.get("files") or 0)
    result.total_bytes = int(stats.get("bytes") or manifest.get("bytes") or 0)
    result.worlds = list(
        stats.get("worlds") or manifest.get("worlds") or []
    )
    result.players = int(stats.get("players") or manifest.get("players") or 0)
    result.has_whitelist = bool(
        stats.get("has_whitelist")
        or (manifest.get("permissions") or {}).get("whitelist")
    )
    result.has_ops = bool(
        stats.get("has_ops") or (manifest.get("permissions") or {}).get("ops")
    )
    result.has_banned = bool(
        stats.get("has_banned")
        or (manifest.get("permissions") or {}).get("banned_players")
        or (manifest.get("permissions") or {}).get("banned_ips")
    )
    if "installed" in manifest:
        result.installed = bool(manifest.get("installed"))
    elif stats.get("installed"):
        result.installed = True
    elif server_data:
        result.installed = bool(server_data.get("installed"))
    else:
        result.installed = False
    if not result.kind:
        result.kind = "paper"
    if result.kind not in SERVER_NAMES:
        result.kind = "paper"


def inspect_server_pack(path: Path | str) -> ServerPackInfo:
    source = Path(path)
    if source.is_dir():
        root = _find_folder_root(source)
        manifest = _read_json(root / SERVER_PACK_MARKER)
        server_data = _read_json(root / "server.json")
        stats = _folder_stats(root)
        inferred = _infer_launch(root)
        merged = {**inferred, **server_data}
        result = ServerPackInfo(
            path=source,
            is_zip=False,
            root=root,
            archive_bytes=0,
        )
        _fill_from_source(
            result, manifest, merged, stats, root.name
        )
        if not result.mc_version:
            result.mc_version = str(inferred.get("mc_version") or "")
        if not result.kind:
            result.kind = str(inferred.get("kind") or "paper")
        if not result.loader_version:
            result.loader_version = str(inferred.get("loader_version") or "")
        return result

    if not source.is_file():
        raise ServerPackError("找不到服务端包")
    if source.suffix.lower() != ".zip":
        raise ServerPackError("只支持 ZIP 服务端包或服务端文件夹")
    try:
        with zipfile.ZipFile(source) as zf:
            infos = zf.infolist()
            if len(infos) > MAX_TOTAL_FILES:
                raise ServerPackError("服务端包内文件数量过多，已拒绝读取")
            names = [item.filename for item in infos]
            prefix = _detect_zip_prefix(names)
            manifest = _zip_manifest(zf, prefix)
            server_data = _zip_server_json(zf, prefix)
            stats = _zip_stats(zf, prefix)
    except ServerPackError:
        raise
    except zipfile.BadZipFile as exc:
        raise ServerPackError("文件不是有效的 ZIP 压缩包") from exc
    except OSError as exc:
        raise ServerPackError(f"无法读取服务端包：{exc}") from exc

    fallback = str(prefix[-1] if prefix else source.stem)
    result = ServerPackInfo(
        path=source,
        is_zip=True,
        prefix="/".join(prefix),
        archive_bytes=source.stat().st_size,
    )
    _fill_from_source(result, manifest, server_data, stats, fallback)
    return result


def looks_like_server_pack(path: Path | str) -> bool:
    """只做轻量识别，供拖拽路由使用；完整检查交给 inspect_server_pack。"""
    source = Path(path)
    try:
        if source.is_dir():
            _find_folder_root(source)
            return True
        if not source.is_file() or source.suffix.lower() != ".zip":
            return False
        with zipfile.ZipFile(source) as zf:
            names = [item.filename for item in zf.infolist()]
        _detect_zip_prefix(names)
    except ServerPackError as exc:
        return "多个服务端" in str(exc)
    except Exception:
        return False
    return True


def _unique_folder(dest_root: Path, base: str) -> str:
    folder = _safe_folder(base) or "server"
    candidate = folder
    index = 2
    while (dest_root / candidate).exists():
        candidate = f"{folder[:44]}-{index}"
        index += 1
    return candidate


def suggest_server_folder(path: Path | str, servers_dir: Path | None = None) -> str:
    dest_root = Path(servers_dir or SERVERS_DIR)
    try:
        info = inspect_server_pack(path)
        base = info.name or "server"
    except Exception:
        base = Path(path).stem or "server"
    return _unique_folder(dest_root, base)


def _safe_member(name: str) -> tuple[str, ...]:
    value = str(name or "").replace("\\", "/")
    if not value or value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise ServerPackError(f"服务端包内包含非法路径：{name}")
    parts = PurePosixPath(value).parts
    if any(part in ("", ".", "..") for part in parts):
        raise ServerPackError(f"服务端包内包含越界路径：{name}")
    for part in parts:
        if (
            re.search(r'[<>:"|?*\x00-\x1f]', part)
            or part.endswith((" ", "."))
            or part.split(".", 1)[0].upper() in _WINDOWS_RESERVED
        ):
            raise ServerPackError(f"服务端包内包含 Windows 无法使用的路径：{name}")
    return parts


def _copy_folder(
    root: Path,
    staging: Path,
    files: list[tuple[Path, Path, int]],
    total_bytes: int,
    progress: ProgressCallback,
    cancel_event: threading.Event | None,
) -> None:
    done = 0
    staging.mkdir(parents=True, exist_ok=True)
    for relative, path, size in files:
        _check_cancel(cancel_event)
        destination = staging / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        done += size
        progress(
            5 + int(85 * done / max(1, total_bytes)),
            f"导入 {relative.as_posix()}",
        )


def _extract_zip(
    zf: zipfile.ZipFile,
    prefix: tuple[str, ...],
    staging: Path,
    total_bytes: int,
    progress: ProgressCallback,
    cancel_event: threading.Event | None,
) -> None:
    done = 0
    staging.mkdir(parents=True, exist_ok=True)
    for item in zf.infolist():
        if item.is_dir():
            continue
        if item.flag_bits & 0x1:
            raise ServerPackError("服务端包中的文件被加密，无法导入")
        mode = (item.external_attr >> 16) & 0o170000
        if mode == 0o120000:
            raise ServerPackError("服务端包里不允许符号链接")
        relative = _zip_relative(item.filename, prefix)
        if not relative:
            continue
        parts = _safe_member(relative)
        rel = Path(*parts)
        if _skip_relative(rel):
            continue
        _check_cancel(cancel_event)
        destination = staging / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(item) as source, destination.open("wb") as target:
            shutil.copyfileobj(source, target, 1024 * 1024)
        done += max(0, int(item.file_size))
        progress(
            5 + int(85 * done / max(1, total_bytes)),
            f"解压 {relative}",
        )


def _launch_exists(folder: Path, data: dict[str, Any]) -> bool:
    kind = str(data.get("launch_kind") or "")
    target = str(data.get("launch_target") or "")
    if kind == "args":
        return bool(target) and (folder / target).is_file()
    if kind == "classpath":
        return bool(target) and bool(data.get("launch_classpath"))
    if kind == "jar":
        return bool(target) and (folder / target).is_file()
    return (folder / "server.jar").is_file() or (
        folder / "fabric-server-launch.jar"
    ).is_file()


def _write_import_server_json(
    folder: Path,
    info: ServerPackInfo,
    display_name: str,
    folder_name: str,
) -> None:
    data = _read_json(folder / "server.json")
    for key in (
        "kind",
        "mc_version",
        "loader_version",
        "paper_build",
        "memory_mb",
        "port",
        "online_mode",
        "launch_kind",
        "launch_target",
        "launch_classpath",
    ):
        value = info.manifest.get(key)
        if value not in (None, "") and data.get(key) in (None, "", []):
            data[key] = value
    if not data.get("kind"):
        data["kind"] = info.kind or "paper"
    if not data.get("mc_version"):
        data["mc_version"] = info.mc_version
    if not data.get("loader_version"):
        data["loader_version"] = info.loader_version
    inferred = _infer_launch(folder)
    for key, value in inferred.items():
        if value and not data.get(key):
            data[key] = value
    data["folder"] = folder_name
    data["name"] = display_name
    data["java_path"] = ""

    props = _read_properties(folder / "server.properties")
    try:
        data["port"] = int(props.get("server-port") or data.get("port") or 25565)
    except Exception:
        data["port"] = 25565
    online = str(props.get("online-mode", "")).strip().lower()
    if online in ("true", "false"):
        data["online_mode"] = online == "true"
    elif "online_mode" not in data:
        data["online_mode"] = False
    try:
        data["memory_mb"] = max(512, min(65536, int(data.get("memory_mb") or 4096)))
    except Exception:
        data["memory_mb"] = 4096
    data["eula"] = _eula_accepted(folder)
    data["installed"] = _launch_exists(folder, data)
    if not data.get("created_at"):
        data["created_at"] = _now()
    known = set(ServerInfo.__dataclass_fields__)
    clean = {key: value for key, value in data.items() if key in known}
    (folder / "server.json").write_text(
        json.dumps(clean, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def import_server_pack(
    source: Path | str,
    *,
    name: str = "",
    servers_dir: Path | str | None = None,
    progress_cb: ProgressCallback | None = None,
    log_cb: LogCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> str:
    """把 ZIP 或文件夹导入为新的服务端，保留世界、玩家与权限数据。"""
    info = inspect_server_pack(source)
    dest_root = Path(servers_dir or SERVERS_DIR)
    dest_root.mkdir(parents=True, exist_ok=True)
    display_name = str(name or info.name or "Minecraft 服务端").strip()
    folder_name = _unique_folder(dest_root, display_name)
    target = dest_root / folder_name
    progress = progress_cb or (lambda _p, _m: None)
    log = log_cb or (lambda _m: None)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    staging = dest_root / f".import-{stamp}-{os.getpid()}"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)

    progress(1, "检查服务端包…")
    try:
        if info.is_zip:
            with zipfile.ZipFile(info.path) as zf:
                prefix = tuple(info.prefix.split("/")) if info.prefix else ()
                _extract_zip(
                    zf,
                    prefix,
                    staging,
                    info.total_bytes,
                    progress,
                    cancel_event,
                )
        else:
            root = info.root or Path(info.path)
            if root.resolve() == dest_root.resolve():
                raise ServerPackError("不能把 servers 目录本身当作服务端导入")
            files = list(_scan_files(root))
            _copy_folder(
                root,
                staging,
                files,
                info.total_bytes,
                progress,
                cancel_event,
            )
        _check_cancel(cancel_event)
        progress(94, "写入服务端设置…")
        _write_import_server_json(staging, info, display_name, folder_name)
        _check_cancel(cancel_event)
        if target.exists():
            raise ServerPackError(f"服务端 {folder_name} 已经存在")
        os.replace(staging, target)
    except ServerPackCancelled:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    progress(100, "导入完成")
    log(
        f"服务端已导入：{display_name} · {info.type_name} {info.mc_version} · "
        f"世界 {len(info.worlds)} 个 · 玩家 {info.players} 份"
    )
    return folder_name
