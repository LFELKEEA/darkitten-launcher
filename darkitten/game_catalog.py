"""从在线数据源拉取 Minecraft 全量注册表。

内置的 ``game_ids`` 只是常用条目，避免第一次打开就必须联网。这个模块负责
按目标版本从 PrismarineJS/minecraft-data 拉取完整的物品、方块、实体、效果、
附魔、粒子和音效清单，再配合 Minecraft 官方中文语言文件补上中文名。

数据会缓存在 ``cache/game_catalog`` 下，之后打开选择窗口可以直接用本地缓存；
点“刷新清单”会重新下载。模块本身不依赖 Qt，方便单元测试。
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import APP_DIR
from .game_ids import Entry

CACHE_DIR = APP_DIR / "cache" / "game_catalog"
CACHE_TTL = 7 * 24 * 3600

_DATA_PATHS_URLS = (
    "https://cdn.jsdelivr.net/gh/PrismarineJS/minecraft-data@master/data/dataPaths.json",
    "https://raw.githubusercontent.com/PrismarineJS/minecraft-data/master/data/dataPaths.json",
)

_REGISTRY_FILES = (
    ("blocks", "blocks.json"),
    ("items", "items.json"),
    ("entities", "entities.json"),
    ("effects", "effects.json"),
    ("enchantments", "enchantments.json"),
    ("particles", "particles.json"),
    ("sounds", "sounds.json"),
    ("biomes", "biomes.json"),
)

_USER_AGENT = "DarkittenLauncher/1.14 (+https://lfelkeea.github.io/darkitten-launcher/)"


@dataclass(frozen=True)
class CatalogResult:
    entries: dict[str, tuple[Entry, ...]]
    version: str
    data_version: str
    fetched_at: float
    source: str  # network / cache


def _version_key(version: str) -> tuple:
    text = str(version or "").strip().lower()
    parts = re.findall(r"\d+|[a-z]+", text)
    key: list[tuple[int, int, str]] = []
    for part in parts:
        if part.isdigit():
            key.append((0, int(part), ""))
        else:
            key.append((1, 0, part))
    return tuple(key)


def _snake(name: str) -> str:
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(name or "").strip())
    return text.replace(" ", "_").replace("-", "_").lower()


def _prettify(name: str) -> str:
    return str(name or "").replace("_", " ").strip().title()


def _get_json(urls: tuple[str, ...], timeout: int = 45) -> Any:
    error: Exception | None = None
    for url in urls:
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": _USER_AGENT}
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # 换下一个镜像
            error = exc
    raise RuntimeError(str(error) if error else "下载失败")


def _registry_urls(data_path: str, filename: str) -> tuple[str, str]:
    return (
        f"https://cdn.jsdelivr.net/gh/PrismarineJS/minecraft-data@master/data/{data_path}/{filename}",
        f"https://raw.githubusercontent.com/PrismarineJS/minecraft-data/master/data/{data_path}/{filename}",
    )


def _language_urls(version: str) -> tuple[str, str]:
    return (
        f"https://cdn.jsdelivr.net/gh/InventivetalentDev/minecraft-assets@{version}/assets/minecraft/lang/zh_cn.json",
        f"https://raw.githubusercontent.com/InventivetalentDev/minecraft-assets/{version}/assets/minecraft/lang/zh_cn.json",
    )


def resolve_data_version(version: str, data_paths: dict[str, Any]) -> str:
    """把用户版本映射到 minecraft-data 里最近的一份可用数据。"""
    pc = data_paths.get("pc") if isinstance(data_paths, dict) else None
    if not isinstance(pc, dict) or not pc:
        return str(version or "")
    text = str(version or "").strip()
    if text in pc:
        return text
    match = re.match(r"\d+(?:\.\d+){0,2}", text)
    if match and match.group(0) in pc:
        return match.group(0)
    candidates = [
        item
        for item in pc
        if re.fullmatch(r"\d+(?:\.\d+){0,2}", str(item))
    ]
    if not candidates:
        return text
    candidates.sort(key=_version_key, reverse=True)
    target = _version_key(text)
    for item in candidates:
        if _version_key(item) <= target:
            return item
    return candidates[-1]


def _block_category(name: str, material: str) -> str:
    text = str(name or "").lower()
    if text.endswith("_ore") or text in {"ancient_debris"}:
        return "矿石"
    if any(
        token in text
        for token in ("_planks", "_log", "_wood", "_leaves", "_sapling", "_sign")
    ) or text in {"bookshelf", "crafting_table", "chest", "barrel", "ladder"}:
        return "木质与植物"
    if any(
        token in text
        for token in (
            "_slab",
            "_stairs",
            "_wall",
            "_fence",
            "_door",
            "_trapdoor",
            "_button",
            "_pressure_plate",
        )
    ):
        return "建筑方块"
    if "glass" in text or text.endswith("_pane"):
        return "玻璃"
    if any(
        token in text
        for token in ("_wool", "_carpet", "_bed", "_banner")
    ):
        return "羊毛与装饰"
    if any(
        token in text
        for token in ("_terracotta", "_concrete", "_glazed", "_stained_")
    ):
        return "染色方块"
    if "coral" in text or text in {"sponge", "wet_sponge"}:
        return "珊瑚与海绵"
    mapping = {
        "mineable/pickaxe": "镐类方块",
        "mineable/axe": "斧类方块",
        "mineable/shovel": "锹类方块",
        "mineable/hoe": "锄类方块",
        "incorrect_for_wooden_tool": "高硬度方块",
        "wool": "羊毛与装饰",
        "leaves;mineable/hoe": "木质与植物",
        "plant;mineable/axe": "木质与植物",
        "gourd;mineable/axe": "作物",
        "vine_or_glow_lichen;plant;mineable/axe": "木质与植物",
        "sword_instantly_mines": "作物",
        "coweb": "特殊方块",
        "default": "其他方块",
    }
    return mapping.get(str(material or ""), "其他方块")


def _item_category(name: str, block_names: set[str]) -> str:
    text = str(name or "").lower()
    if text == "air":
        return "其他物品"
    if text in block_names:
        return "方块物品"
    if text.endswith("_spawn_egg"):
        return "刷怪蛋"
    if text.endswith(("_sword", "_axe", "_pickaxe", "_shovel", "_hoe")):
        return "工具与武器"
    if text.endswith(("_helmet", "_chestplate", "_leggings", "_boots")):
        return "盔甲"
    if text.endswith(
        ("_potion", "_splash_potion", "_lingering_potion", "_tipped_arrow")
    ):
        return "药水与箭"
    if text.endswith("_bucket"):
        return "桶"
    if text.endswith(("_boat", "_minecart", "_rail", "_wagon", "minecart")):
        return "交通"
    if text.endswith("_dye"):
        return "染料"
    if "music_disc" in text:
        return "唱片"
    if text.endswith("_banner") or text == "shield":
        return "装饰"
    if text.endswith(
        (
            "_seeds",
            "apple",
            "bread",
            "carrot",
            "potato",
            "beef",
            "porkchop",
            "chicken",
            "mutton",
            "rabbit",
            "cod",
            "salmon",
            "cookie",
            "cake",
            "pie",
            "stew",
            "soup",
        )
    ):
        return "食物"
    return "其他物品"


_ENTITY_CATEGORIES = {
    "Hostile mobs": "敌对生物",
    "Passive mobs": "被动生物",
    "Vehicles": "载具",
    "Projectiles": "弹射物",
    "Immobile": "固定实体",
    "UNKNOWN": "其他实体",
}

_ENCHANT_CATEGORIES = {
    "armor": "盔甲",
    "head_armor": "头盔",
    "foot_armor": "靴子",
    "leg_armor": "护腿",
    "weapon": "武器",
    "sharp_weapon": "武器",
    "melee_weapon": "近战武器",
    "bow": "弓",
    "crossbow": "弩",
    "trident": "三叉戟",
    "mace": "重锤",
    "mining": "挖掘",
    "mining_loot": "挖掘",
    "fishing": "钓鱼",
    "durability": "耐久",
    "equippable": "装备",
    "vanishing": "特殊",
}

_SOUND_CATEGORIES = {
    "entity": "实体音效",
    "block": "方块音效",
    "item": "物品音效",
    "ui": "界面音效",
    "music": "音乐",
    "ambient": "环境音效",
    "weather": "天气音效",
    "environment": "环境音效",
    "particle": "粒子音效",
    "event": "事件音效",
}


def _name_from_lang(zh: dict[str, str], keys: tuple[str, ...], fallback: str) -> str:
    for key in keys:
        value = zh.get(key)
        if value:
            return str(value).replace("\n", " ").strip()
    return str(fallback or "").strip()


def build_entries(
    registries: dict[str, Any], zh: dict[str, str]
) -> dict[str, tuple[Entry, ...]]:
    """把 minecraft-data 的注册表转成界面使用的条目。"""
    blocks_raw = list(registries.get("blocks") or [])
    items_raw = list(registries.get("items") or [])
    entities_raw = list(registries.get("entities") or [])
    effects_raw = list(registries.get("effects") or [])
    enchantments_raw = list(registries.get("enchantments") or [])
    particles_raw = list(registries.get("particles") or [])
    sounds_raw = list(registries.get("sounds") or [])
    biomes_raw = list(registries.get("biomes") or [])

    block_names = {
        str(item.get("name") or "").strip()
        for item in blocks_raw
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    }

    blocks: list[Entry] = []
    for item in blocks_raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        display = str(item.get("displayName") or _prettify(name))
        zh_name = _name_from_lang(
            zh, (f"block.minecraft.{name}",), display
        )
        blocks.append(
            Entry(
                f"minecraft:{name}",
                zh_name,
                _block_category(name, str(item.get("material") or "")),
                "",
            )
        )

    items: list[Entry] = []
    for item in items_raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name or name == "air":
            continue
        display = str(item.get("displayName") or _prettify(name))
        zh_name = _name_from_lang(
            zh,
            (f"item.minecraft.{name}", f"block.minecraft.{name}"),
            display,
        )
        stack = item.get("stackSize")
        help_text = f"最大堆叠：{stack}" if isinstance(stack, int) and stack > 1 else ""
        items.append(
            Entry(
                f"minecraft:{name}",
                zh_name,
                _item_category(name, block_names),
                help_text,
            )
        )

    entities: list[Entry] = []
    for item in entities_raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name or name == "player":
            continue
        display = str(item.get("displayName") or _prettify(name))
        zh_name = _name_from_lang(zh, (f"entity.minecraft.{name}",), display)
        raw_category = str(item.get("category") or "")
        category = _ENTITY_CATEGORIES.get(raw_category, "其他实体")
        entities.append(Entry(f"minecraft:{name}", zh_name, category, ""))

    effects: list[Entry] = []
    for item in effects_raw:
        if not isinstance(item, dict):
            continue
        raw_name = str(item.get("name") or "").strip()
        if not raw_name:
            continue
        name = _snake(raw_name)
        display = str(item.get("displayName") or _prettify(name))
        zh_name = _name_from_lang(zh, (f"effect.minecraft.{name}",), display)
        kind = str(item.get("type") or "")
        category = {"good": "增益", "bad": "负面", "neutral": "中性"}.get(
            kind, "状态效果"
        )
        effects.append(Entry(f"minecraft:{name}", zh_name, category, ""))

    enchantments: list[Entry] = []
    for item in enchantments_raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        display = str(item.get("displayName") or _prettify(name))
        zh_name = _name_from_lang(
            zh, (f"enchantment.minecraft.{name}",), display
        )
        category = _ENCHANT_CATEGORIES.get(
            str(item.get("category") or ""), "其他附魔"
        )
        helps: list[str] = []
        max_level = item.get("maxLevel")
        if isinstance(max_level, int):
            helps.append(f"最高 {max_level} 级")
        if item.get("curse"):
            helps.append("诅咒附魔")
        elif item.get("treasureOnly"):
            helps.append("仅可通过宝箱 / 交易获得")
        enchantments.append(
            Entry(f"minecraft:{name}", zh_name, category, "，".join(helps))
        )

    particles: list[Entry] = []
    for item in particles_raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        particles.append(
            Entry(f"minecraft:{name}", _prettify(name), "粒子", "")
        )

    sounds: list[Entry] = []
    for item in sounds_raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        display = _prettify(name)
        zh_name = _name_from_lang(
            zh, (f"subtitles.{name}",), display
        )
        prefix = name.split(".", 1)[0]
        sounds.append(
            Entry(
                f"minecraft:{name}",
                zh_name,
                _SOUND_CATEGORIES.get(prefix, "其他音效"),
                "",
            )
        )

    # 群系也放进“结构”选择器：/locate biome 会用到，按 ID 搜索更方便。
    structures: list[Entry] = []
    for item in biomes_raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        display = str(item.get("displayName") or _prettify(name))
        zh_name = _name_from_lang(zh, (f"biome.minecraft.{name}",), display)
        structures.append(Entry(f"minecraft:{name}", zh_name, "群系", ""))

    return {
        "block": tuple(blocks),
        "item": tuple(items),
        "entity": tuple(entities),
        "effect": tuple(effects),
        "enchantment": tuple(enchantments),
        "particle": tuple(particles),
        "sound": tuple(sounds),
        "structure": tuple(structures),
    }


def _cache_path(version: str) -> Path:
    safe = re.sub(r"[^0-9A-Za-z._+-]", "_", str(version or "").strip()) or "latest"
    return CACHE_DIR / f"{safe}.json"


def load_cached(version: str) -> CatalogResult | None:
    path = _cache_path(version)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    kinds = data.get("kinds")
    if not isinstance(kinds, dict):
        return None
    entries: dict[str, tuple[Entry, ...]] = {}
    for kind, rows in kinds.items():
        if not isinstance(rows, list):
            continue
        collected: list[Entry] = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 2:
                continue
            collected.append(
                Entry(
                    str(row[0]),
                    str(row[1]),
                    str(row[2]) if len(row) > 2 else "",
                    str(row[3]) if len(row) > 3 else "",
                )
            )
        entries[str(kind)] = tuple(collected)
    if not entries:
        return None
    return CatalogResult(
        entries=entries,
        version=str(data.get("version") or version),
        data_version=str(data.get("data_version") or ""),
        fetched_at=float(data.get("fetched_at") or 0),
        source="cache",
    )


def _save_cache(result: CatalogResult) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": result.version,
        "data_version": result.data_version,
        "fetched_at": result.fetched_at,
        "kinds": {
            kind: [[entry.value, entry.name, entry.category, entry.help] for entry in entries]
            for kind, entries in result.entries.items()
        },
    }
    path = _cache_path(result.version)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(path)


def fetch_catalog(version: str, force: bool = False) -> CatalogResult:
    """拉取指定版本的完整清单；网络失败时退回旧缓存。"""
    cached = load_cached(version)
    if (
        cached is not None
        and not force
        and time.time() - cached.fetched_at < CACHE_TTL
    ):
        return cached

    try:
        data_paths = _get_json(_DATA_PATHS_URLS)
        data_version = resolve_data_version(version, data_paths)
        pc = data_paths.get("pc") or {}
        relative = ""
        if isinstance(pc, dict) and isinstance(pc.get(data_version), dict):
            relative = str(pc[data_version].get("blocks") or "")
        if not relative:
            relative = f"pc/{data_version}"
        registries: dict[str, Any] = {}
        with ThreadPoolExecutor(max_workers=7) as pool:
            futures = {
                key: pool.submit(_get_json, _registry_urls(relative, filename))
                for key, filename in _REGISTRY_FILES
            }
            zh_future = pool.submit(_get_json, _language_urls(data_version))
            for key, future in futures.items():
                try:
                    registries[key] = future.result()
                except Exception:
                    registries[key] = []
            try:
                zh = zh_future.result()
            except Exception:
                zh = {}
        entries = build_entries(registries, zh if isinstance(zh, dict) else {})
        result = CatalogResult(
            entries=entries,
            version=str(version or data_version),
            data_version=data_version,
            fetched_at=time.time(),
            source="network",
        )
        _save_cache(result)
        return result
    except Exception:
        if cached is not None:
            return cached
        raise


def default_version() -> str:
    try:
        from .mc_versions import release_versions

        versions = release_versions()
        if versions:
            return versions[0]
    except Exception:
        pass
    return "26.3"
