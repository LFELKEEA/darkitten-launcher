"""离线管理 Minecraft 单人存档与多人服务器列表。

支持读取 / 创建 / 删除存档，读取 / 添加 / 编辑 / 删除 servers.dat，
以及在不启动游戏的情况下修改难度、游戏模式、种子、天气、时间和常用游戏规则。

新版（1.21.11+）存档使用 game_rules / WorldGenSettings.seed 结构，
老版本使用 GameRules / RandomSeed，本模块会按存档实际格式读写。
"""

from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import nbt
from .config import APP_DIR

GAME_MODES: dict[int, str] = {
    0: "生存",
    1: "创造",
    2: "冒险",
    3: "旁观",
}

DIFFICULTIES: dict[int, str] = {
    0: "和平",
    1: "简单",
    2: "普通",
    3: "困难",
}

MODERN_TEMPLATE_PATH = APP_DIR / "assets" / "world_template_modern.dat"

_ILLEGAL_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class WorldError(ValueError):
    pass


@dataclass(frozen=True)
class RuleSpec:
    key: str
    label: str
    kind: str
    modern: str
    default: Any
    minimum: int = 0
    maximum: int = 100
    hint: str = ""


RULE_SPECS: tuple[RuleSpec, ...] = (
    RuleSpec("keepInventory", "死亡不掉落", "bool", "minecraft:keep_inventory", False),
    RuleSpec("doDaylightCycle", "昼夜循环", "bool", "minecraft:advance_time", True),
    RuleSpec("doWeatherCycle", "天气变化", "bool", "minecraft:advance_weather", True),
    RuleSpec("doMobSpawning", "自然生成生物", "bool", "minecraft:spawn_mobs", True),
    RuleSpec("mobGriefing", "生物破坏方块", "bool", "minecraft:mob_griefing", True),
    RuleSpec("naturalRegeneration", "自然回血", "bool", "minecraft:natural_health_regeneration", True),
    RuleSpec("doInsomnia", "幻翼生成", "bool", "minecraft:spawn_phantoms", True),
    RuleSpec("doImmediateRespawn", "立即重生", "bool", "minecraft:immediate_respawn", False),
    RuleSpec("announceAdvancements", "进度公告", "bool", "minecraft:show_advancement_messages", True),
    RuleSpec("doMobLoot", "生物掉落物", "bool", "minecraft:mob_drops", True),
    RuleSpec("doTileDrops", "方块掉落物", "bool", "minecraft:block_drops", True),
    RuleSpec("randomTickSpeed", "随机刻速度", "int", "minecraft:random_tick_speed", 3, 0, 100),
    RuleSpec("spawnRadius", "重生半径", "int", "minecraft:respawn_radius", 10, 0, 1000),
    RuleSpec("playersSleepingPercentage", "睡觉比例", "int", "minecraft:players_sleeping_percentage", 100, 0, 100),
)

RULE_BY_KEY = {spec.key: spec for spec in RULE_SPECS}

_INFO_CACHE: dict[str, tuple[tuple[int, int], "WorldInfo"]] = {}
_SERVERS_CACHE: dict[str, tuple[tuple[int, int], list["ServerEntry"]]] = {}


# ---------------------------------------------------------------------------
# 通用工具


def _now_ms() -> int:
    return int(time.time() * 1000)


def _data_of(root: dict[str, Any]) -> dict[str, Any]:
    data = root.get("Data")
    return data if isinstance(data, dict) else root


def _level_path(folder: Path) -> Path:
    return folder / "level.dat"


def _sig(path: Path) -> tuple[int, int]:
    try:
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_size
    except OSError:
        return 0, 0


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, (nbt.Byte, nbt.Short, nbt.Int, nbt.Long)):
        return int(value) != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return default


def uses_modern_schema(data: dict[str, Any]) -> bool:
    return isinstance(data.get("game_rules"), dict)


def world_seed(data: dict[str, Any]) -> int | None:
    settings = data.get("WorldGenSettings")
    if isinstance(settings, dict) and "seed" in settings:
        try:
            return int(settings["seed"])
        except (TypeError, ValueError):
            return None
    if "RandomSeed" in data:
        try:
            return int(data["RandomSeed"])
        except (TypeError, ValueError):
            return None
    return None


def world_name(data: dict[str, Any], fallback: str) -> str:
    value = data.get("LevelName")
    text = str(value).strip() if value is not None else ""
    return text or fallback


def read_rule(data: dict[str, Any], spec: RuleSpec) -> Any:
    modern = uses_modern_schema(data)
    if modern:
        rules = data.get("game_rules") or {}
        if spec.modern in rules:
            return _to_bool(rules[spec.modern], bool(spec.default)) if spec.kind == "bool" else _to_int(rules[spec.modern], int(spec.default))
        return spec.default
    rules = data.get("GameRules") or {}
    if spec.key in rules:
        return _to_bool(rules[spec.key], bool(spec.default)) if spec.kind == "bool" else _to_int(rules[spec.key], int(spec.default))
    return spec.default


def _write_rule(data: dict[str, Any], spec: RuleSpec, value: Any) -> None:
    if uses_modern_schema(data):
        rules = data.setdefault("game_rules", {})
        if spec.kind == "bool":
            rules[spec.modern] = nbt.Byte(1 if value else 0)
        else:
            rules[spec.modern] = nbt.Int(_to_int(value, int(spec.default)))
        return
    rules = data.setdefault("GameRules", {})
    if spec.kind == "bool":
        rules[spec.key] = nbt.NBTString("true" if value else "false")
    else:
        rules[spec.key] = nbt.NBTString(str(_to_int(value, int(spec.default))))


def _set_byte(data: dict[str, Any], key: str, value: bool | int) -> None:
    data[key] = nbt.Byte(int(value))


def _set_int(data: dict[str, Any], key: str, value: int) -> None:
    data[key] = nbt.Int(int(value))


def _set_long(data: dict[str, Any], key: str, value: int) -> None:
    data[key] = nbt.Long(int(value))


def _set_seed(data: dict[str, Any], seed: int) -> None:
    settings = data.get("WorldGenSettings")
    if isinstance(settings, dict) and "seed" in settings:
        settings["seed"] = nbt.Long(int(seed))
    else:
        data["RandomSeed"] = nbt.Long(int(seed))


def _set_spawn(data: dict[str, Any], x: int = 0, y: int = 64, z: int = 0) -> None:
    spawn = data.get("spawn")
    if isinstance(spawn, dict):
        spawn["pos"] = nbt.NBTList(
            [nbt.Int(int(x)), nbt.Int(int(y)), nbt.Int(int(z))],
            element_type=nbt.TAG_INT,
        )
        spawn["pitch"] = nbt.Float(0.0)
        spawn["yaw"] = nbt.Float(0.0)
        spawn.setdefault("dimension", nbt.NBTString("minecraft:overworld"))
        return
    _set_int(data, "SpawnX", x)
    _set_int(data, "SpawnY", y)
    _set_int(data, "SpawnZ", z)


# ---------------------------------------------------------------------------
# 单人存档


@dataclass
class WorldInfo:
    folder: Path
    key: str
    name: str
    seed: int | None = None
    game_mode: int = 0
    difficulty: int = 2
    hardcore: bool = False
    cheats: bool = False
    version_name: str = ""
    data_version: int = 0
    last_played: int = 0
    icon_path: Path | None = None
    error: str = ""
    modern: bool = False
    rules: dict[str, Any] = field(default_factory=dict)

    @property
    def seed_text(self) -> str:
        return str(self.seed) if self.seed is not None else "未知"

    @property
    def mode_text(self) -> str:
        return GAME_MODES.get(self.game_mode, str(self.game_mode))

    @property
    def difficulty_text(self) -> str:
        return DIFFICULTIES.get(self.difficulty, str(self.difficulty))

    @property
    def last_played_text(self) -> str:
        if not self.last_played:
            return "从未记录"
        try:
            return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.last_played / 1000))
        except (OverflowError, OSError, ValueError):
            return "未知"


@dataclass
class WorldSettings:
    name: str
    seed: int
    game_mode: int = 0
    difficulty: int = 2
    hardcore: bool = False
    cheats: bool = False
    day_time: int = 1000
    raining: bool = False
    thundering: bool = False
    rules: dict[str, Any] = field(default_factory=dict)


def _icon_path(folder: Path) -> Path | None:
    icon = folder / "icon.png"
    return icon if icon.is_file() else None


def _read_world(folder: Path) -> tuple[WorldInfo, dict[str, Any]]:
    data: dict[str, Any] = {}
    error = ""
    try:
        data = _data_of(nbt.read_nbt(_level_path(folder)))
    except (OSError, nbt.NBTError) as exc:
        error = str(exc)

    player = data.get("Player")
    player = player if isinstance(player, dict) else {}
    game_mode = _to_int(data.get("GameType", player.get("playerGameType", 0)))
    if "playerGameType" in player:
        game_mode = _to_int(player.get("playerGameType"), game_mode)

    info = WorldInfo(
        folder=folder,
        key=folder.name,
        name=world_name(data, folder.name),
        seed=world_seed(data),
        game_mode=game_mode,
        difficulty=_to_int(data.get("Difficulty", 2), 2),
        hardcore=_to_bool(data.get("hardcore"), False),
        cheats=_to_bool(data.get("allowCommands"), False),
        version_name=str((data.get("Version") or {}).get("Name", "") if isinstance(data.get("Version"), dict) else ""),
        data_version=_to_int(data.get("DataVersion"), 0),
        last_played=_to_int(data.get("LastPlayed"), 0),
        icon_path=_icon_path(folder),
        error=error,
        modern=uses_modern_schema(data),
        rules={spec.key: read_rule(data, spec) for spec in RULE_SPECS} if data else {},
    )
    return info, data


def scan_worlds(game_dir: Path | str) -> list[WorldInfo]:
    saves = Path(game_dir) / "saves"
    if not saves.is_dir():
        return []
    worlds: list[WorldInfo] = []
    try:
        entries = list(saves.iterdir())
    except OSError:
        return []
    for entry in entries:
        if not entry.is_dir():
            continue
        level = _level_path(entry)
        if not level.is_file():
            continue
        key = str(level.resolve())
        signature = _sig(level)
        cached = _INFO_CACHE.get(key)
        if cached and cached[0] == signature:
            info = cached[1]
        else:
            info, _ = _read_world(entry)
            _INFO_CACHE[key] = (signature, info)
        worlds.append(info)
    def sort_key(item: WorldInfo) -> tuple[int, float]:
        try:
            mtime = item.folder.stat().st_mtime
        except OSError:
            mtime = 0.0
        return item.last_played, mtime

    worlds.sort(key=sort_key, reverse=True)
    return worlds


def load_world(folder: Path | str) -> tuple[WorldInfo, dict[str, Any]]:
    folder = Path(folder)
    if not _level_path(folder).is_file():
        raise WorldError("找不到 level.dat，可能不是有效的存档文件夹")
    return _read_world(folder)


def load_world_settings(folder: Path | str) -> WorldSettings:
    info, data = load_world(folder)
    return WorldSettings(
        name=info.name,
        seed=info.seed if info.seed is not None else 0,
        game_mode=info.game_mode,
        difficulty=info.difficulty,
        hardcore=info.hardcore,
        cheats=info.cheats,
        day_time=_to_int(data.get("DayTime", 1000), 1000) % 24000,
        raining=_to_bool(data.get("raining"), False),
        thundering=_to_bool(data.get("thundering"), False),
        rules={spec.key: read_rule(data, spec) for spec in RULE_SPECS},
    )


def clean_world_name(name: str) -> str:
    text = _ILLEGAL_NAME.sub("-", str(name or "")).strip().strip(".")
    return text[:64] or "新的世界"


def _free_folder(saves: Path, name: str) -> Path:
    base = clean_world_name(name)
    candidate = saves / base
    index = 2
    while candidate.exists():
        candidate = saves / f"{base} ({index})"
        index += 1
    return candidate


def _new_seed() -> int:
    return random.SystemRandom().getrandbits(64) - (1 << 63)


def _legacy_level(*, name: str, seed: int, game_mode: int, difficulty: int, hardcore: bool, cheats: bool) -> dict[str, Any]:
    data: dict[str, Any] = {
        "version": nbt.Int(19133),
        "LevelName": nbt.NBTString(name),
        "RandomSeed": nbt.Long(seed),
        "GameType": nbt.Int(game_mode),
        "Difficulty": nbt.Byte(difficulty),
        "DifficultyLocked": nbt.Byte(0),
        "hardcore": nbt.Byte(1 if hardcore else 0),
        "allowCommands": nbt.Byte(1 if cheats else 0),
        "MapFeatures": nbt.Byte(1),
        "generatorName": nbt.NBTString("default"),
        "generatorVersion": nbt.Int(0),
        "generatorSettings": nbt.NBTString(""),
        "initialized": nbt.Byte(0),
        "SpawnX": nbt.Int(0),
        "SpawnY": nbt.Int(64),
        "SpawnZ": nbt.Int(0),
        "Time": nbt.Long(0),
        "DayTime": nbt.Long(1000),
        "LastPlayed": nbt.Long(_now_ms()),
        "raining": nbt.Byte(0),
        "rainTime": nbt.Int(0),
        "thundering": nbt.Byte(0),
        "thunderTime": nbt.Int(0),
        "clearWeatherTime": nbt.Int(0),
        "GameRules": {},
    }
    for spec in RULE_SPECS:
        rules = data["GameRules"]
        if spec.kind == "bool":
            rules[spec.key] = nbt.NBTString("true" if spec.default else "false")
        else:
            rules[spec.key] = nbt.NBTString(str(spec.default))
    return {"Data": data}


def _version_tuple(version_id: str) -> tuple[int, int, int]:
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", str(version_id or ""))
    if not match:
        return (0, 0, 0)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))


def _modern_template() -> dict[str, Any] | None:
    if not MODERN_TEMPLATE_PATH.is_file():
        return None
    try:
        return nbt.read_nbt(MODERN_TEMPLATE_PATH)
    except (OSError, nbt.NBTError):
        return None


def _apply_creation_fields(
    data: dict[str, Any],
    *,
    name: str,
    seed: int,
    game_mode: int,
    difficulty: int,
    hardcore: bool,
    cheats: bool,
) -> None:
    data["LevelName"] = nbt.NBTString(name)
    _set_seed(data, seed)
    _set_int(data, "GameType", game_mode)
    _set_byte(data, "Difficulty", difficulty)
    _set_byte(data, "DifficultyLocked", 0)
    _set_byte(data, "hardcore", 1 if hardcore else 0)
    _set_byte(data, "allowCommands", 1 if cheats else 0)
    _set_byte(data, "initialized", 0)
    _set_long(data, "Time", 0)
    _set_long(data, "DayTime", 1000)
    _set_long(data, "LastPlayed", _now_ms())
    _set_byte(data, "raining", 0)
    _set_int(data, "rainTime", 0)
    _set_byte(data, "thundering", 0)
    _set_int(data, "thunderTime", 0)
    _set_int(data, "clearWeatherTime", 0)
    _set_spawn(data, 0, 64, 0)
    for spec in RULE_SPECS:
        _write_rule(data, spec, spec.default)


def create_world(
    game_dir: Path | str,
    *,
    name: str,
    seed: int | None = None,
    game_mode: int = 0,
    difficulty: int = 2,
    hardcore: bool = False,
    cheats: bool = False,
    version_id: str = "",
) -> WorldInfo:
    saves = Path(game_dir) / "saves"
    saves.mkdir(parents=True, exist_ok=True)
    display_name = clean_world_name(name)
    folder = _free_folder(saves, display_name)
    seed_value = _new_seed() if seed is None else int(seed)
    if not -(1 << 63) <= seed_value < (1 << 63):
        raise WorldError("种子超出 64 位整数范围")
    if hardcore:
        game_mode = 0
    if game_mode not in GAME_MODES:
        game_mode = 0
    if difficulty not in DIFFICULTIES:
        difficulty = 2

    template = _modern_template() if _version_tuple(version_id) >= (1, 21, 11) else None
    if template is not None:
        data = _data_of(template)
        data.pop("Player", None)
        data.pop("ServerBrands", None)
        data.pop("WasModded", None)
        data.pop("WanderingTraderSpawnDelay", None)
        data.pop("WanderingTraderSpawnChance", None)
        packs = data.get("DataPacks")
        if isinstance(packs, dict):
            packs["Enabled"] = nbt.NBTList([nbt.NBTString("vanilla")], element_type=nbt.TAG_STRING)
            packs["Disabled"] = nbt.NBTList([], element_type=nbt.TAG_STRING)
        root = template
    else:
        root = _legacy_level(
            name=display_name,
            seed=seed_value,
            game_mode=game_mode,
            difficulty=difficulty,
            hardcore=hardcore,
            cheats=cheats,
        )
        data = _data_of(root)

    _apply_creation_fields(
        data,
        name=display_name,
        seed=seed_value,
        game_mode=game_mode,
        difficulty=difficulty,
        hardcore=hardcore,
        cheats=cheats,
    )
    folder.mkdir(parents=True, exist_ok=False)
    try:
        nbt.write_nbt_file(_level_path(folder), root, compressed=True, backup=False)
    except Exception:
        try:
            folder.rmdir()
        except OSError:
            pass
        raise
    _INFO_CACHE.pop(str(_level_path(folder).resolve()), None)
    info, _ = _read_world(folder)
    return info


def apply_world_settings(folder: Path | str, settings: WorldSettings) -> WorldInfo:
    folder = Path(folder)
    if not _level_path(folder).is_file():
        raise WorldError("找不到 level.dat，可能不是有效的存档文件夹")
    try:
        root = nbt.read_nbt(_level_path(folder))
    except (OSError, nbt.NBTError) as exc:
        raise WorldError(f"读取存档失败：{exc}") from exc
    data = _data_of(root)
    display_name = clean_world_name(settings.name)
    if not display_name:
        raise WorldError("存档名称不能为空")
    if not -(1 << 63) <= int(settings.seed) < (1 << 63):
        raise WorldError("种子超出 64 位整数范围")
    game_mode = settings.game_mode if settings.game_mode in GAME_MODES else 0
    if settings.hardcore:
        game_mode = 0
    difficulty = settings.difficulty if settings.difficulty in DIFFICULTIES else 2

    data["LevelName"] = nbt.NBTString(display_name)
    _set_seed(data, int(settings.seed))
    _set_int(data, "GameType", game_mode)
    _set_byte(data, "Difficulty", difficulty)
    _set_byte(data, "hardcore", 1 if settings.hardcore else 0)
    _set_byte(data, "allowCommands", 1 if settings.cheats else 0)
    _set_long(data, "DayTime", max(0, int(settings.day_time)) % 24000)
    _set_byte(data, "raining", 1 if settings.raining else 0)
    _set_byte(data, "thundering", 1 if settings.thundering else 0)
    if settings.raining and _to_int(data.get("rainTime"), 0) <= 0:
        _set_int(data, "rainTime", 12000)
    if settings.thundering and _to_int(data.get("thunderTime"), 0) <= 0:
        _set_int(data, "thunderTime", 12000)
    for spec in RULE_SPECS:
        value = settings.rules.get(spec.key, spec.default)
        if spec.kind == "int":
            value = max(spec.minimum, min(spec.maximum, _to_int(value, int(spec.default))))
        _write_rule(data, spec, value)

    player = data.get("Player")
    if isinstance(player, dict):
        player["playerGameType"] = nbt.Int(game_mode)

    nbt.write_nbt_file(_level_path(folder), root, compressed=True, backup=True)
    _INFO_CACHE.pop(str(_level_path(folder).resolve()), None)
    updated, _ = _read_world(folder)
    return updated


def delete_world(folder: Path | str) -> tuple[bool, str]:
    folder = Path(folder)
    if folder.parent.name.lower() != "saves":
        raise WorldError("只能删除 saves 目录下的存档")
    if not _level_path(folder).is_file():
        raise WorldError("这个文件夹不是有效的存档")
    from .servers import send_to_recycle_bin

    _INFO_CACHE.pop(str(_level_path(folder).resolve()), None)
    return send_to_recycle_bin(folder)


# ---------------------------------------------------------------------------
# 多人服务器列表（servers.dat）


@dataclass
class ServerEntry:
    name: str
    address: str
    icon: str = ""
    accept_textures: bool = True
    hidden: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


def servers_path(game_dir: Path | str) -> Path:
    return Path(game_dir) / "servers.dat"


def _server_entry(raw: dict[str, Any]) -> ServerEntry:
    return ServerEntry(
        name=str(raw.get("name", "") or ""),
        address=str(raw.get("ip", "") or ""),
        icon=str(raw.get("icon", "") or ""),
        accept_textures=_to_bool(raw.get("acceptTextures"), True),
        hidden=_to_bool(raw.get("hidden"), False),
        raw=raw,
    )


def read_servers(game_dir: Path | str) -> list[ServerEntry]:
    path = servers_path(game_dir)
    if not path.is_file():
        return []
    key = str(path.resolve())
    signature = _sig(path)
    cached = _SERVERS_CACHE.get(key)
    if cached and cached[0] == signature:
        return [ServerEntry(**{**entry.__dict__, "raw": dict(entry.raw)}) for entry in cached[1]]
    try:
        root = nbt.read_nbt(path, compressed=False)
    except (OSError, nbt.NBTError):
        return []
    raw_list = root.get("servers")
    if not isinstance(raw_list, list):
        return []
    entries = [_server_entry(item) for item in raw_list if isinstance(item, dict)]
    _SERVERS_CACHE[key] = (signature, entries)
    return [ServerEntry(**{**entry.__dict__, "raw": dict(entry.raw)}) for entry in entries]


def validate_server(name: str, address: str) -> tuple[str, str]:
    clean_name = str(name or "").strip()
    clean_address = str(address or "").strip()
    if not clean_name:
        raise WorldError("服务器名称不能为空")
    if len(clean_name) > 64:
        raise WorldError("服务器名称不能超过 64 个字符")
    if not clean_address:
        raise WorldError("服务器地址不能为空")
    if len(clean_address) > 255:
        raise WorldError("服务器地址过长")
    if any(char in clean_address for char in "\r\n\t "):
        raise WorldError("服务器地址不能包含空格或换行")
    return clean_name, clean_address


def write_servers(game_dir: Path | str, entries: list[ServerEntry]) -> Path:
    path = servers_path(game_dir)
    root: dict[str, Any] = {}
    if path.is_file():
        try:
            root = nbt.read_nbt(path, compressed=False)
        except (OSError, nbt.NBTError):
            root = {}
    items: list[dict[str, Any]] = []
    for entry in entries:
        raw = dict(entry.raw or {})
        raw["name"] = nbt.NBTString(entry.name)
        raw["ip"] = nbt.NBTString(entry.address)
        raw["acceptTextures"] = nbt.Byte(1 if entry.accept_textures else 0)
        if entry.hidden:
            raw["hidden"] = nbt.Byte(1)
        else:
            raw.pop("hidden", None)
        if entry.icon:
            raw["icon"] = nbt.NBTString(entry.icon)
        else:
            raw.pop("icon", None)
        items.append(raw)
    root["servers"] = nbt.NBTList(items, element_type=nbt.TAG_COMPOUND)
    nbt.write_nbt_file(path, root, compressed=False, backup=True)
    _SERVERS_CACHE.pop(str(path.resolve()), None)
    return path


def upsert_server(game_dir: Path | str, entry: ServerEntry, index: int = -1) -> list[ServerEntry]:
    name, address = validate_server(entry.name, entry.address)
    entry.name = name
    entry.address = address
    entries = read_servers(game_dir)
    if 0 <= index < len(entries):
        entries[index] = entry
    else:
        entries.append(entry)
    write_servers(game_dir, entries)
    return read_servers(game_dir)


def delete_server(game_dir: Path | str, index: int) -> list[ServerEntry]:
    entries = read_servers(game_dir)
    if 0 <= index < len(entries):
        entries.pop(index)
        write_servers(game_dir, entries)
    return read_servers(game_dir)


def overview(game_dir: Path | str) -> tuple[int, int]:
    """返回 (存档数, 服务器数)，供版本列表预览使用。"""
    try:
        worlds = scan_worlds(game_dir)
    except OSError:
        worlds = []
    try:
        servers = read_servers(game_dir)
    except OSError:
        servers = []
    return len(worlds), len(servers)
