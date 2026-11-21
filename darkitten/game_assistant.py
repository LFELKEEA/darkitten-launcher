"""游戏助手的纯逻辑部分。

这里不依赖 Qt，方便单元测试。内容分成五块：

* 种子：Java 随机数、史莱姆区块、结构候选、坐标换算、地图深链
* 指令：常用指令目录、模板生成、校验、命令方块链、数据包导出
* AI：兼容 OpenAI Chat Completions 的指令生成客户端
* 附魔：EnchCracker 探测 / 启动、clientcommands 检测、教程
* 资料：MC百科搜索与常用文档站链接
"""

from __future__ import annotations

import hashlib
import html as html_module
import io
import json
import math
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from . import game_ids
from .config import APP_DIR

# ---------------------------------------------------------------------------
# 通用常量

MASK48 = (1 << 48) - 1
MASK64 = (1 << 64) - 1
JAVA_RANDOM_MULT = 0x5DEECE66D
JAVA_RANDOM_ADD = 0xB
CREATE_NO_WINDOW = 0x08000000

ASSISTANT_CONFIG_PATH = APP_DIR / "assistant.json"

DEFAULT_AI_CONFIG: dict[str, Any] = {
    "base_url": "https://api.openai.com/v1",
    "model": "gpt-4o-mini",
    "api_key": "",
    "temperature": 0.2,
    "timeout": 90,
}


# ---------------------------------------------------------------------------
# Java Random

def _to_signed64(value: int) -> int:
    value &= MASK64
    return value - (1 << 64) if value >= (1 << 63) else value


def _to_signed32(value: int) -> int:
    value &= 0xFFFFFFFF
    return value - (1 << 32) if value >= 0x80000000 else value


class JavaRandom:
    """java.util.Random 的 48 位 LCG 实现。"""

    def __init__(self, seed: int = 0) -> None:
        self.set_seed(seed)

    def set_seed(self, seed: int) -> None:
        self._seed = (int(seed) ^ JAVA_RANDOM_MULT) & MASK48

    def next(self, bits: int) -> int:
        if not 1 <= bits <= 32:
            raise ValueError("bits must be between 1 and 32")
        self._seed = (self._seed * JAVA_RANDOM_MULT + JAVA_RANDOM_ADD) & MASK48
        return self._seed >> (48 - bits)

    def next_int(self, bound: int) -> int:
        if bound <= 0:
            raise ValueError("bound must be positive")
        if bound & -bound == bound:
            return (bound * self.next(31)) >> 31
        bits = self.next(31)
        value = bits % bound
        while bits - value + (bound - 1) < 0:
            bits = self.next(31)
            value = bits % bound
        return value

    def next_long(self) -> int:
        hi = _to_signed32(self.next(32))
        lo = _to_signed32(self.next(32))
        return _to_signed64((hi << 32) + lo)

    def next_double(self) -> float:
        return (((self.next(26) << 27) + self.next(27)) / float(1 << 53))


# ---------------------------------------------------------------------------
# 种子解析

def java_string_hash(text: str) -> int:
    """等价于 Java String.hashCode()，按 UTF-16 码元计算。"""
    data = text.encode("utf-16-le", "surrogatepass")
    result = 0
    for index in range(0, len(data), 2):
        unit = data[index] | (data[index + 1] << 8)
        result = (31 * result + unit) & 0xFFFFFFFF
    return _to_signed32(result)


@dataclass(frozen=True)
class SeedInfo:
    raw: str
    value: int
    kind: str  # number / hex / text
    hash32: int | None = None

    @property
    def label(self) -> str:
        return {
            "number": "数字种子",
            "hex": "十六进制种子",
            "text": "文本种子（已按 Java hashCode 转换）",
        }.get(self.kind, self.kind)


_HEX_SEED = re.compile(r"^[+-]?0[xX][0-9a-fA-F]+$")
_DEC_SEED = re.compile(r"^[+-]?\d+$")


def parse_seed(raw: str) -> SeedInfo:
    """把用户输入解析成 Minecraft 使用的 64 位世界种子。

    数字在 long 范围内按数字处理；``0x`` 开头按十六进制处理（本启动器的扩展）；
    其他内容按 Java ``String.hashCode()`` 转成 32 位有符号整数，再当作种子。
    """
    text = (raw or "").strip()
    if not text:
        raise ValueError("请输入种子")
    if len(text) > 256:
        raise ValueError("种子太长了，请控制在 256 个字符以内")

    compact = text.replace("_", "")
    if _HEX_SEED.match(compact):
        value = int(compact, 16)
        if not -(1 << 63) <= value < (1 << 63):
            raise ValueError("十六进制种子超出 64 位范围")
        return SeedInfo(raw=text, value=value, kind="hex")

    if _DEC_SEED.match(compact):
        try:
            value = int(compact, 10)
        except ValueError:
            value = 0
        if -(1 << 63) <= value < (1 << 63):
            return SeedInfo(raw=text, value=value, kind="number")

    hashed = java_string_hash(text)
    return SeedInfo(raw=text, value=hashed, kind="text", hash32=hashed)


def seed_candidates(text: str) -> list[int]:
    """同时给出“数字种子”和“文本哈希”两种可能，方便排查。"""
    info = parse_seed(text)
    values = [info.value]
    if info.kind != "text":
        hashed = java_string_hash(info.raw)
        if hashed != info.value:
            values.append(hashed)
    return values


# ---------------------------------------------------------------------------
# 区块 / 坐标

def block_to_chunk(value: int) -> int:
    return value // 16


def chunk_to_block(chunk: int) -> int:
    return chunk * 16


def chunk_center(chunk: int) -> int:
    return chunk * 16 + 8


def overworld_to_nether(x: float, z: float) -> tuple[float, float]:
    return (math.floor(x / 8.0), math.floor(z / 8.0))


def nether_to_overworld(x: float, z: float) -> tuple[float, float]:
    return (math.floor(x * 8.0), math.floor(z * 8.0))


# ---------------------------------------------------------------------------
# 史莱姆区块

def _int_mul(*values: int) -> int:
    """模拟 Java int 的逐步乘法溢出（32 位环绕）。"""
    result = 1
    for value in values:
        result = _to_signed32(result * int(value))
    return result


def is_slime_chunk(seed: int, chunk_x: int, chunk_z: int) -> bool:
    """Java 版史莱姆区块判定。

    原版公式（注意 ``chunkX * chunkX * 4987142`` 等先按 int 运算溢出，再转 long）：
    ``Random(worldSeed + (long)(x*x*4987142) + (long)(x*5947611)
    + (long)(z*z)*4392871L + (long)(z*389711) ^ 987234911L).nextInt(10) == 0``
    """
    x = int(chunk_x)
    z = int(chunk_z)
    mixed = _to_signed64(
        _int_mul(x, x, 4987142)
        + _int_mul(x, 5947611)
        + _to_signed64(_int_mul(z, z)) * 4392871
        + _int_mul(z, 389711)
    )
    random_seed = _to_signed64(_to_signed64(seed + mixed) ^ 987234911)
    return JavaRandom(random_seed).next_int(10) == 0


@dataclass(frozen=True)
class SlimeChunk:
    chunk_x: int
    chunk_z: int
    block_x: int
    block_z: int
    distance: float

    @property
    def chunk_text(self) -> str:
        return f"{self.chunk_x}, {self.chunk_z}"

    @property
    def block_text(self) -> str:
        return f"{self.block_x}, {self.block_z}"


def find_slime_chunks(
    seed: int,
    center_x: int = 0,
    center_z: int = 0,
    radius: int = 8,
    limit: int = 32,
) -> list[SlimeChunk]:
    """在中心区块周围 ``radius`` 个区块内找史莱姆区块，按距离排序。"""
    radius = max(0, min(int(radius), 64))
    found: list[SlimeChunk] = []
    for cx in range(center_x - radius, center_x + radius + 1):
        for cz in range(center_z - radius, center_z + radius + 1):
            if is_slime_chunk(seed, cx, cz):
                bx = chunk_center(cx)
                bz = chunk_center(cz)
                distance = math.hypot(bx - chunk_center(center_x), bz - chunk_center(center_z))
                found.append(SlimeChunk(cx, cz, bx, bz, distance))
    found.sort(key=lambda item: (item.distance, item.chunk_x, item.chunk_z))
    return found[: max(1, int(limit))]


# ---------------------------------------------------------------------------
# 结构候选

@dataclass(frozen=True)
class StructureSpec:
    id: str
    name: str
    dimension: str
    spacing: int
    separation: int
    salt: int
    triangular: bool = False

    @property
    def spread(self) -> int:
        return max(1, self.spacing - self.separation)

    @property
    def dimension_name(self) -> str:
        return {"overworld": "主世界", "the_nether": "下界", "the_end": "末地"}.get(
            self.dimension, self.dimension
        )


STRUCTURES: tuple[StructureSpec, ...] = (
    StructureSpec("village", "村庄", "overworld", 34, 8, 10387312),
    StructureSpec("pillager_outpost", "掠夺者前哨站", "overworld", 32, 8, 165745296),
    StructureSpec("desert_pyramid", "沙漠神殿", "overworld", 32, 8, 14357617),
    StructureSpec("jungle_temple", "丛林神庙", "overworld", 32, 8, 14357619),
    StructureSpec("swamp_hut", "女巫小屋", "overworld", 32, 8, 14357620),
    StructureSpec("igloo", "雪屋", "overworld", 32, 8, 14357618),
    StructureSpec("ocean_monument", "海底神殿", "overworld", 32, 5, 10387313),
    StructureSpec("woodland_mansion", "林地府邸", "overworld", 80, 20, 10387319, True),
    StructureSpec("ocean_ruin", "海底废墟", "overworld", 20, 8, 14357621),
    StructureSpec("shipwreck", "沉船", "overworld", 24, 4, 165745295),
    StructureSpec("buried_treasure", "埋藏的宝藏", "overworld", 1, 0, 10387320),
    StructureSpec("ruined_portal", "废弃传送门", "overworld", 40, 15, 34222645),
    StructureSpec("ancient_city", "远古城市", "overworld", 24, 8, 20083232, True),
    StructureSpec("trail_ruins", "古迹废墟", "overworld", 34, 8, 83469867),
    StructureSpec("trial_chambers", "试炼密室", "overworld", 34, 12, 94251327),
    StructureSpec("nether_ruined_portal", "下界废弃传送门", "the_nether", 25, 10, 34222645),
    StructureSpec("nether_fortress", "下界要塞", "the_nether", 27, 4, 30084232),
    StructureSpec("bastion_remnant", "堡垒遗迹", "the_nether", 27, 4, 30084232),
    StructureSpec("nether_fossil", "下界化石", "the_nether", 2, 1, 14357921),
    StructureSpec("end_city", "末地城", "the_end", 20, 11, 10387313, True),
)

STRUCTURE_BY_ID = {item.id: item for item in STRUCTURES}

STRUCTURE_NOTES = {
    "buried_treasure": "埋藏的宝藏每个区块都会先选出候选点，但只有海滩/水下地形才会真正生成，必须配合生物群系判断。",
    "nether_fortress": "下界要塞与堡垒遗迹共用区域网格，实际生成时还会做重叠规避。",
    "bastion_remnant": "下界要塞与堡垒遗迹共用区域网格，实际生成时还会做重叠规避。",
    "stronghold": "要塞由独立的环带算法生成，无法用简单的区域随机公式精确定位，请使用地图工具。",
    "mineshaft": "废弃矿井没有区域网格，按区块概率生成，无法用种子精确列出。",
}


@dataclass(frozen=True)
class StructureCandidate:
    structure: str
    name: str
    dimension: str
    region_x: int
    region_z: int
    chunk_x: int
    chunk_z: int
    distance: float

    @property
    def block_x(self) -> int:
        return chunk_center(self.chunk_x)

    @property
    def block_z(self) -> int:
        return chunk_center(self.chunk_z)

    @property
    def chunk_text(self) -> str:
        return f"{self.chunk_x}, {self.chunk_z}"

    @property
    def block_text(self) -> str:
        return f"{self.block_x}, {self.block_z}"

    @property
    def tp_command(self) -> str:
        return f"/tp @s {self.block_x} ~ {self.block_z}"


def _structure_seed(seed: int, region_x: int, region_z: int, salt: int) -> int:
    return _to_signed64(
        int(seed)
        + int(region_x) * 341873128712
        + int(region_z) * 132897987541
        + int(salt)
    )


def structure_candidates(
    seed: int,
    structure_id: str,
    region_radius: int = 6,
    limit: int = 40,
) -> list[StructureCandidate]:
    """推算某个结构在若干区域内的候选区块。

    这只是“区域随机数给出的候选点”。结构真正生成时还会检查生物群系、
    地形和重叠，因此结果需要配合地图工具确认。
    """
    spec = STRUCTURE_BY_ID.get(structure_id)
    if spec is None:
        raise ValueError(f"未知结构：{structure_id}")
    radius = max(0, min(int(region_radius), 32))
    spread = spec.spread
    found: list[StructureCandidate] = []
    for rx in range(-radius, radius + 1):
        for rz in range(-radius, radius + 1):
            random = JavaRandom(_structure_seed(seed, rx, rz, spec.salt))
            offset_x = random.next_int(spread)
            offset_z = random.next_int(spread)
            if spec.triangular:
                offset_x = (offset_x + random.next_int(spread)) // 2
                offset_z = (offset_z + random.next_int(spread)) // 2
            chunk_x = rx * spec.spacing + offset_x
            chunk_z = rz * spec.spacing + offset_z
            distance = math.hypot(chunk_center(chunk_x), chunk_center(chunk_z))
            found.append(
                StructureCandidate(
                    structure=spec.id,
                    name=spec.name,
                    dimension=spec.dimension,
                    region_x=rx,
                    region_z=rz,
                    chunk_x=chunk_x,
                    chunk_z=chunk_z,
                    distance=distance,
                )
            )
    found.sort(key=lambda item: (item.distance, item.chunk_x, item.chunk_z))
    return found[: max(1, int(limit))]


def structures_for_dimension(dimension: str) -> list[StructureSpec]:
    return [item for item in STRUCTURES if item.dimension == dimension]


# ---------------------------------------------------------------------------
# 地图工具链接

# 平台 ID 与标签都取自 Chunkbase 种子地图的版本选择框（2026-11 抓取）。
# 标签里的范围就是该平台实际覆盖的游戏版本。
CHUNKBASE_VERSION_GROUPS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("Java 正式版", (
        ("Java 26.3", "java_26_3"),
        ("Java 26.2", "java_26_2"),
        ("Java 26.1", "java_26_1"),
        ("Java 1.21.9 – 1.21.11", "java_1_21_9"),
        ("Java 1.21.6 – 1.21.8", "java_1_21_6"),
        ("Java 1.21.5", "java_1_21_5"),
        ("Java 1.21.4", "java_1_21_4"),
        ("Java 1.21.2 – 1.21.3", "java_1_21_2"),
        ("Java 1.21 – 1.21.1", "java_1_21"),
        ("Java 1.20 – 1.20.6", "java_1_20"),
        ("Java 1.19.3 – 1.19.4", "java_1_19_3"),
        ("Java 1.19 – 1.19.2", "java_1_19"),
        ("Java 1.18 – 1.18.2", "java_1_18"),
        ("Java 1.17 – 1.17.1", "java_1_17"),
        ("Java 1.16 – 1.16.5", "java_1_16"),
        ("Java 1.15 – 1.15.2", "java_1_15"),
        ("Java 1.14 – 1.14.4", "java_1_14"),
        ("Java 1.13 – 1.13.2", "java_1_13"),
        ("Java 1.12 – 1.12.2", "java_1_12"),
        ("Java 1.11 – 1.11.2", "java_1_11"),
        ("Java 1.10 – 1.10.2", "java_1_10"),
        ("Java 1.9 – 1.9.4", "java_1_9"),
        ("Java 1.8 – 1.8.9", "java_1_8"),
        ("Java 1.7 – 1.7.10", "java_1_7"),
    )),
    ("Java 实验版", (
        ("Java 26.4 (实验)", "java_26_4"),
    )),
    ("Java 超大型生物群系 (LB)", (
        ("Java 26.3 (LB)", "java_26_3_lb"),
        ("Java 26.2 (LB)", "java_26_2_lb"),
        ("Java 26.1 (LB)", "java_26_1_lb"),
        ("Java 1.21.9 – 1.21.11 (LB)", "java_1_21_9_lb"),
        ("Java 1.21.6 – 1.21.8 (LB)", "java_1_21_6_lb"),
        ("Java 1.21.5 (LB)", "java_1_21_5_lb"),
        ("Java 1.21.4 (LB)", "java_1_21_4_lb"),
        ("Java 1.21.2 – 1.21.3 (LB)", "java_1_21_2_lb"),
        ("Java 1.21 – 1.21.1 (LB)", "java_1_21_lb"),
        ("Java 1.20 (LB)", "java_1_20_lb"),
        ("Java 1.19.3 – 1.19.4 (LB)", "java_1_19_3_lb"),
        ("Java 1.19 – 1.19.2 (LB)", "java_1_19_lb"),
        ("Java 1.18 (LB)", "java_1_18_lb"),
        ("Java 1.17 (LB)", "java_1_17_lb"),
        ("Java 1.16 (LB)", "java_1_16_lb"),
        ("Java 1.15 (LB)", "java_1_15_lb"),
        ("Java 1.14 (LB)", "java_1_14_lb"),
        ("Java 1.13 (LB)", "java_1_13_lb"),
        ("Java 1.12 (LB)", "java_1_12_lb"),
        ("Java 1.11 (LB)", "java_1_11_lb"),
        ("Java 1.10 (LB)", "java_1_10_lb"),
        ("Java 1.9 (LB)", "java_1_9_lb"),
        ("Java 1.8 (LB)", "java_1_8_lb"),
        ("Java 1.7 (LB)", "java_1_7_lb"),
        ("Java 26.4 (LB, 实验)", "java_26_4_lb"),
    )),
    ("基岩版 Bedrock", (
        ("Bedrock 26.60 – 26.65", "bedrock_26_60"),
        ("Bedrock 26.50 – 26.52", "bedrock_26_50"),
        ("Bedrock 26.30 – 26.45", "bedrock_26_30"),
        ("Bedrock 26.0 – 26.23", "bedrock_26_0"),
        ("Bedrock 1.21.120 – 1.21.132", "bedrock_1_21_120"),
        ("Bedrock 1.21.111 – 1.21.114", "bedrock_1_21_110"),
        ("Bedrock 1.21.90 – 1.21.101", "bedrock_1_21_90"),
        ("Bedrock 1.21.60 – 1.21.84", "bedrock_1_21_60"),
        ("Bedrock 1.21.50", "bedrock_1_21_50"),
        ("Bedrock 1.21 – 1.21.40", "bedrock_1_21"),
        ("Bedrock 1.20.60 – 1.20.81", "bedrock_1_20_60"),
        ("Bedrock 1.20.0 – 1.20.51", "bedrock_1_20"),
        ("Bedrock 1.19", "bedrock_1_19"),
        ("Bedrock 1.18", "bedrock_1_18"),
        ("Bedrock 1.17", "bedrock_1_17"),
        ("Bedrock 1.16", "bedrock_1_16"),
        ("Bedrock 1.14", "bedrock_1_14"),
        ("Bedrock 26.70 (实验)", "bedrock_26_70"),
    )),
)

CHUNKBASE_VERSIONS: tuple[tuple[str, str], ...] = tuple(
    item for _group, items in CHUNKBASE_VERSION_GROUPS for item in items
)
CHUNKBASE_PLATFORMS = {platform for _label, platform in CHUNKBASE_VERSIONS}

# 1.12.0 及更早版本写入过这些不存在的平台 ID，读取设置时自动映射到有效值。
_CHUNKBASE_LEGACY_ALIASES = {
    "java_1_21_1": "java_1_21",
    "java_1_20_6": "java_1_20",
    "java_1_20_4": "java_1_20",
    "java_1_20_1": "java_1_20",
    "java_1_19_4": "java_1_19_3",
    "java_1_18_2": "java_1_18",
    "java_1_17_1": "java_1_17",
    "java_1_16_5": "java_1_16",
    "java_1_15_2": "java_1_15",
    "java_1_14_4": "java_1_14",
    "java_1_13_2": "java_1_13",
    "java_1_12_2": "java_1_12",
}

CHUNKBASE_DIMENSIONS: tuple[tuple[str, str], ...] = (
    ("主世界", "overworld"),
    ("下界", "nether"),
    ("末地", "end"),
)


_CHUNKBASE_PLATFORM_RE = re.compile(r"^(?:java|bedrock)_[0-9a-z_]+$")


def normalize_chunkbase_platform(value: str | None, fallback: str = "java_1_21_9") -> str:
    """校验平台 ID。允许运行时从 Chunkbase 抓到的新版本 ID。"""
    platform = str(value or "").strip()
    platform = _CHUNKBASE_LEGACY_ALIASES.get(platform, platform)
    if _CHUNKBASE_PLATFORM_RE.match(platform):
        return platform
    if _CHUNKBASE_PLATFORM_RE.match(str(fallback or "")):
        return fallback
    return "java_1_21_9"


def is_bedrock_platform(platform: str) -> bool:
    return str(platform or "").startswith("bedrock_")


def chunkbase_url(
    seed: int | str,
    version: str = "java_1_21_9",
    dimension: str = "overworld",
    x: int = 0,
    z: int = 0,
    zoom: float = 0.5,
) -> str:
    platform = normalize_chunkbase_platform(version)
    dimension = dimension if dimension in {"overworld", "nether", "end"} else "overworld"
    return (
        "https://www.chunkbase.com/apps/seed-map#"
        f"seed={urllib.parse.quote(str(seed))}"
        f"&platform={urllib.parse.quote(platform)}"
        f"&dimension={urllib.parse.quote(dimension)}"
        f"&x={int(x)}&z={int(z)}&zoom={zoom}"
    )


# MinecraftSeedMap 只提供 1.13 起的大版本页面。
_SEEDMAP_PLATFORM_VERSIONS = {
    "java_1_21_9": "1.21",
    "java_1_21_6": "1.21",
    "java_1_21_5": "1.21",
    "java_1_21_4": "1.21",
    "java_1_21_2": "1.21",
    "java_1_21": "1.21",
    "java_1_20": "1.20",
    "java_1_19_3": "1.19",
    "java_1_19": "1.19",
    "java_1_18": "1.18",
    "java_1_17": "1.17",
    "java_1_16": "1.16",
    "java_1_15": "1.15",
    "java_1_14": "1.14",
    "java_1_13": "1.13",
}


def minecraftseedmap_version(platform: str) -> str | None:
    """把 Chunkbase 平台 ID 降级成 MinecraftSeedMap 支持的大版本。"""
    return _SEEDMAP_PLATFORM_VERSIONS.get(normalize_chunkbase_platform(platform))


def minecraftseedmap_url(seed: int | str, version: str | None = None) -> str:
    base = f"https://minecraftseedmap.org/seed/{urllib.parse.quote(str(seed))}"
    major = minecraftseedmap_version(version) if version else None
    return f"{base}/{major}" if major else base


MC_MOD_FILTERS: tuple[tuple[str, int], ...] = (
    ("全部", 0),
    ("模组", 1),
    ("整合包", 2),
    ("资料", 3),
    ("教程", 4),
    ("作者", 5),
    ("用户", 6),
    ("社群", 7),
)


def mcmod_search_url(query: str, filter_id: int = 0, page: int = 1) -> str:
    keyword = urllib.parse.quote((query or "").strip())
    page = max(1, int(page or 1))
    return (
        "https://search.mcmod.cn/s?key="
        f"{keyword}&filter={max(0, int(filter_id or 0))}&page={page}"
    )


@dataclass(frozen=True)
class MCModResult:
    title: str
    url: str
    summary: str
    category: str = ""
    meta: str = ""


@dataclass(frozen=True)
class MCModPage:
    results: tuple[MCModResult, ...]
    keyword: str
    filter_id: int
    page: int
    total_pages: int
    total_text: str


def _strip_html(text: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", text or "", flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_module.unescape(text)
    text = re.sub(r"\[h\d+=([^\]]*)\]", r"【\1】", text)
    return re.sub(r"\s+", " ", text).strip()


def _mcmod_category(url: str) -> str:
    path = urllib.parse.urlparse(url).path
    if "/modpack/" in path:
        return "整合包"
    if "/class/" in path:
        return "模组"
    if "/post/" in path:
        return "教程"
    if "/author/" in path:
        return "作者"
    if "/user/" in path:
        return "用户"
    if "/group/" in path:
        return "社群"
    if any(part in path for part in ("/item/", "/block/", "/entity/", "/fluid/")):
        return "资料"
    return "其他"


def _parse_mcmod_page(page: str, keyword: str, filter_id: int) -> MCModPage:
    blocks = re.findall(
        r'<div class="result-item">([\s\S]*?)(?=<div class="result-item">|'
        r'<div class="search-result-pages"|</body>)',
        page,
    )
    results: list[MCModResult] = []
    for block in blocks:
        head = re.search(
            r'<div class="head">([\s\S]*?)<div class="body">', block
        )
        if not head:
            continue
        chosen = None
        for href, raw_title in re.findall(
            r'<a[^>]*href="([^"]+)"[^>]*>([\s\S]*?)</a>', head.group(1)
        ):
            title = _strip_html(raw_title)
            if title:
                chosen = (href, title)
                break
        if chosen is None:
            continue
        url = html_module.unescape(chosen[0]).strip()
        title = chosen[1]
        if not url:
            continue
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/"):
            url = "https://www.mcmod.cn" + url
        body = re.search(r'<div class="body">([\s\S]*?)</div>', block)
        summary = _strip_html(body.group(1)) if body else ""
        if len(summary) > 320:
            summary = summary[:320].rstrip() + "…"
        foot = re.search(r'<div class="foot">([\s\S]*?)</div>', block)
        meta_parts = []
        if foot:
            for label, value in re.findall(
                r'<span>([^<]+)</span>\s*<span class="value">([\s\S]*?)</span>',
                foot.group(1),
            ):
                clean_label = _strip_html(label).rstrip("：:")
                clean_value = _strip_html(value)
                if clean_value and clean_label:
                    meta_parts.append(f"{clean_label}：{clean_value}")
        results.append(
            MCModResult(
                title=title,
                url=url,
                summary=summary,
                category=_mcmod_category(url),
                meta=" · ".join(meta_parts[:3]),
            )
        )

    info = re.search(r'<p class="info">([\s\S]*?)</p>', page)
    info_text = _strip_html(info.group(1)) if info else ""
    total_pages = 1
    page_match = re.search(r"共约?\s*(\d+)\s*页", info_text)
    if page_match:
        total_pages = max(1, int(page_match.group(1)))
    total_match = re.search(r"找到约?\s*([\d,]+)\s*条结果", info_text)
    total_text = (
        f"找到约 {total_match.group(1)} 条结果"
        if total_match
        else (info_text.split("[")[0].strip() or "搜索完成")
    )
    return MCModPage(
        results=tuple(results),
        keyword=keyword,
        filter_id=filter_id,
        page=max(1, int(re.search(r"&page=(\d+)", page).group(1)) if re.search(r"&page=(\d+)", page) else 1),
        total_pages=total_pages,
        total_text=total_text,
    )


def search_mcmod_page(
    query: str,
    *,
    filter_id: int = 0,
    page: int = 1,
    timeout: int = 15,
) -> MCModPage:
    """在启动器内抓取一页 MC百科搜索结果，包含分类与总页数。"""
    keyword = (query or "").strip()
    if not keyword:
        raise ValueError("请输入要搜索的关键词")
    request = urllib.request.Request(
        mcmod_search_url(keyword, filter_id, page),
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=max(5, timeout)) as response:
            html = response.read().decode("utf-8", "replace")
    except urllib.error.URLError as error:
        raise RuntimeError(f"连接 MC百科失败：{error.reason}") from error
    except OSError as error:
        raise RuntimeError(f"请求 MC百科失败：{error}") from error
    data = _parse_mcmod_page(html, keyword, filter_id)
    return MCModPage(
        results=data.results,
        keyword=data.keyword,
        filter_id=data.filter_id,
        page=max(1, min(int(page or 1), data.total_pages)),
        total_pages=data.total_pages,
        total_text=data.total_text,
    )


def search_mcmod(query: str, limit: int = 12, timeout: int = 15) -> list[MCModResult]:
    """兼容旧调用：返回单页结果，按 limit 截断。"""
    data = search_mcmod_page(query, timeout=timeout)
    return list(data.results)[: max(1, int(limit))]


WIKI_LINKS: tuple[tuple[str, str, str], ...] = (
    ("MC百科", "模组、整合包、教程与资料库", "https://www.mcmod.cn/"),
    ("中文 Minecraft Wiki", "原版机制、方块、物品与版本资料", "https://zh.minecraft.wiki/"),
    ("命令列表", "Java 版全部指令与语法", "https://zh.minecraft.wiki/w/%E5%91%BD%E4%BB%A4"),
    ("命令方块", "命令方块类型、连锁与条件", "https://zh.minecraft.wiki/w/%E5%91%BD%E4%BB%A4%E6%96%B9%E5%9D%97"),
    ("目标选择器", "@p、@a、@e 等选择器参数", "https://zh.minecraft.wiki/w/%E7%9B%AE%E6%A0%87%E9%80%89%E6%8B%A9%E5%99%A8"),
    ("数据包", "函数、标签与数据包结构", "https://zh.minecraft.wiki/w/%E6%95%B0%E6%8D%AE%E5%8C%85"),
    ("附魔", "附魔机制、等级与冲突", "https://zh.minecraft.wiki/w/%E9%99%84%E9%AD%94"),
    ("Chunkbase 种子地图", "在线查看群系、结构与史莱姆区块", "https://www.chunkbase.com/apps/seed-map"),
    ("MinecraftSeedMap", "另一个在线种子地图", "https://minecraftseedmap.org/"),
    ("MC百科命令方块生成器", "网页版命令方块工具", "https://www.mcmod.cn/tools/cbcreator/"),
    ("EnchCracker", "RNG 附魔预测工具源码与说明", "https://github.com/Earthcomputer/EnchCracker"),
    ("clientcommands", "提供 cenchant 等客户端指令的模组", "https://github.com/Earthcomputer/clientcommands"),
)


# ---------------------------------------------------------------------------
# 指令目录

@dataclass(frozen=True)
class CommandSpec:
    name: str
    usage: str
    summary: str
    example: str
    category: str = "基础"
    aliases: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    note: str = ""

    @property
    def search_text(self) -> str:
        return " ".join(
            (self.name, self.usage, self.summary, self.example, self.category, *self.aliases, *self.keywords)
        ).lower()


COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec("help", "/help [命令]", "查看命令帮助或某条命令的用法。", "/help give", "基础"),
    CommandSpec("give", "/give <目标> <物品> [数量]", "把物品直接放进玩家背包或指定容器。1.20.5+ 使用组件语法。", "/give @s minecraft:diamond_sword 1", "物品", keywords=("给物品", "刷物品")),
    CommandSpec("clear", "/clear [<目标>] [<物品>] [<数量>]", "清空玩家背包，或只清除指定物品。", "/clear @s minecraft:dirt", "物品"),
    CommandSpec("item", "/item replace <目标> <槽位> with <物品> [数量]", "直接替换实体或方块的物品槽位。", "/item replace entity @s armor.head with minecraft:diamond_helmet", "物品", note="1.20.5+ 的槽位与组件语法有调整。"),
    CommandSpec("enchant", "/enchant <目标> <附魔> [等级]", "给手持物品添加附魔，受原版附魔规则限制。", "/enchant @s minecraft:sharpness 5", "物品"),
    CommandSpec("xp", "/xp add <目标> <数量> [levels|points]", "增加或设置经验值。", "/xp add @s 30 levels", "物品"),
    CommandSpec("tp", "/tp <目标> <x> <y> <z> [<朝向>]", "传送实体到指定坐标或另一个实体。", "/tp @s 100 64 -200", "传送", aliases=("teleport",), keywords=("传送", "坐标")),
    CommandSpec("spreadplayers", "/spreadplayers <中心x> <中心z> <间距> <最大范围> <是否同队> <目标>", "把玩家随机分散到一片区域。", "/spreadplayers 0 0 10 200 false @a", "传送"),
    CommandSpec("spawnpoint", "/spawnpoint [<目标>] [<x> <y> <z>] [<角度>]", "设置玩家的个人重生点。", "/spawnpoint @s 0 70 0", "传送"),
    CommandSpec("setworldspawn", "/setworldspawn [<x> <y> <z>] [<角度>]", "设置世界默认出生点。", "/setworldspawn 0 70 0", "世界"),
    CommandSpec("setblock", "/setblock <x> <y> <z> <方块> [replace|destroy|keep]", "在指定位置放置单个方块。", "/setblock ~ ~1 ~ minecraft:stone", "世界", keywords=("放置方块",)),
    CommandSpec("fill", "/fill <x1> <y1> <z1> <x2> <y2> <z2> <方块> [模式]", "用方块填充一个长方体区域。单次最多 32768 格。", "/fill ~-5 ~ ~-5 ~5 ~5 ~5 minecraft:glass hollow", "世界", keywords=("填充", "区域")),
    CommandSpec("clone", "/clone <源起点> <源终点> <目标位置> [模式]", "复制一片区域到另一个位置。单次最多 32768 格。", "/clone ~-5 ~ ~-5 ~5 ~5 ~5 ~ ~10 ~", "世界"),
    CommandSpec("place", "/place structure|jigsaw|template|feature <ID> [x y z]", "直接放置结构、拼图、模板或地物。", "/place structure minecraft:village_plains 0 64 0", "世界"),
    CommandSpec("locate", "/locate structure|biome|poi <ID>", "查找最近的结构、生物群系或兴趣点。", "/locate structure minecraft:stronghold", "世界", keywords=("查找结构", "群系")),
    CommandSpec("forceload", "/forceload add|remove|query <x1> <z1> [x2 <z2>]", "强制加载指定区块，常用于红石/命令方块机器。", "/forceload add 0 0 31 31", "世界"),
    CommandSpec("worldborder", "/worldborder add|set|center|damage|get|warning <参数>", "设置世界边界的大小、中心与伤害。", "/worldborder set 2000", "世界"),
    CommandSpec("time", "/time add|set|query <值>", "调整或查询世界时间。", "/time set day", "世界"),
    CommandSpec("weather", "/weather clear|rain|thunder [<秒数>]", "设置天气。", "/weather thunder 600", "世界"),
    CommandSpec("difficulty", "/difficulty [peaceful|easy|normal|hard]", "查看或设置游戏难度。", "/difficulty hard", "世界"),
    CommandSpec("gamerule", "/gamerule <规则> [<值>]", "查看或修改游戏规则。", "/gamerule keepInventory true", "世界", keywords=("游戏规则",)),
    CommandSpec("seed", "/seed", "显示当前世界种子。", "/seed", "世界"),
    CommandSpec("summon", "/summon <实体> [<x> <y> <z>] [<NBT>]", "在指定位置生成实体。", "/summon minecraft:zombie ~ ~1 ~", "实体", keywords=("生成实体", "刷怪")),
    CommandSpec("kill", "/kill [<目标>]", "杀死实体。", "/kill @e[type=minecraft:zombie]", "实体"),
    CommandSpec("effect", "/effect give|clear <目标> [<效果>] [<秒数>] [<等级>] [hideParticles]", "给予或清除状态效果。", "/effect give @s minecraft:speed 30 1", "实体", keywords=("药水效果",)),
    CommandSpec("damage", "/damage <目标> <数量> [<伤害类型>] [at <位置>|by <实体>]", "对实体造成指定类型的伤害。", "/damage @s 5 minecraft:magic", "实体", note="1.19.4+。"),
    CommandSpec("ride", "/ride <目标> mount|dismount [<坐骑>]", "让实体骑乘或离开坐骑。", "/ride @s mount @e[type=minecraft:pig,limit=1]", "实体"),
    CommandSpec("attribute", "/attribute <目标> <属性> get|base|modifier ...", "读取或修改实体属性。", "/attribute @s minecraft:generic.max_health get", "实体", note="属性 ID 在 1.21+ 改为不带 generic. 前缀。"),
    CommandSpec("tag", "/tag <目标> add|remove|list [<标签>]", "给实体添加、移除或列出标签。", "/tag @s add boss", "实体", keywords=("标签",)),
    CommandSpec("team", "/team add|remove|join|leave|list|modify ...", "管理计分板队伍。", "/team add red", "计分板"),
    CommandSpec("scoreboard", "/scoreboard objectives|players ...", "管理计分板目标与分数。", "/scoreboard objectives add kills dummy", "计分板", keywords=("计分板", "分数")),
    CommandSpec("execute", "/execute as|at|positioned|if|unless|store|run ...", "组合执行条件与上下文，是复杂指令的核心。", "/execute as @a at @s run say 你好", "进阶", keywords=("条件", "复合指令", "as at")),
    CommandSpec("function", "/function <命名空间:函数>", "运行数据包中的函数。", "/function dkl:hello", "进阶", keywords=("函数", "数据包")),
    CommandSpec("schedule", "/schedule function <函数> <时间> [append|replace]", "延迟或循环执行函数。", "/schedule function dkl:tick 1s", "进阶"),
    CommandSpec("data", "/data get|merge|modify|remove <block|entity|storage> <目标> [<路径>]", "读写方块、实体或存储的 NBT 数据。", "/data get entity @s Health", "进阶", keywords=("NBT",)),
    CommandSpec("loot", "/loot give|insert|spawn|replace ...", "把战利品表结果发给玩家、容器或世界。", "/loot give @s loot minecraft:chests/simple_dungeon", "进阶"),
    CommandSpec("advancement", "/advancement grant|revoke <目标> <进度>", "授予或撤销进度。", "/advancement grant @s only minecraft:story/root", "进阶"),
    CommandSpec("recipe", "/recipe give|take <目标> <配方>", "解锁或移除玩家的配方。", "/recipe give @s *", "进阶"),
    CommandSpec("title", "/title <目标> title|subtitle|actionbar|times|clear|reset ...", "显示标题、副标题或动作栏文字。", "/title @a title {\"text\":\"欢迎\"}", "显示"),
    CommandSpec("tellraw", "/tellraw <目标> <JSON文本>", "发送可点击、可着色的 JSON 文本。", "/tellraw @a {\"text\":\"你好\",\"color\":\"gold\"}", "显示", keywords=("JSON", "文本")),
    CommandSpec("say", "/say <消息>", "向所有玩家发送系统消息。", "/say 服务器将在 5 分钟后重启", "显示"),
    CommandSpec("tell", "/tell <目标> <消息>", "私聊玩家。", "/tell @s 你好", "显示", aliases=("msg", "w")),
    CommandSpec("playsound", "/playsound <声音> <来源> <目标> [x y z] [音量] [音调]", "在指定位置播放声音。", "/playsound minecraft:entity.player.levelup master @a ~ ~ ~ 1 1", "显示"),
    CommandSpec("particle", "/particle <粒子> [x y z] [dx dy dz] [速度] [数量]", "生成粒子效果。", "/particle minecraft:flame ~ ~1 ~ 0.2 0.2 0.2 0.01 30", "显示"),
    CommandSpec("bossbar", "/bossbar add|remove|set|get|list ...", "创建和管理 Boss 血条。", "/bossbar add dkl:boss {\"text\":\"喵王\"}", "显示"),
)


COMMAND_BY_NAME: dict[str, CommandSpec] = {}
for _spec in COMMANDS:
    COMMAND_BY_NAME[_spec.name] = _spec
    for _alias in _spec.aliases:
        COMMAND_BY_NAME.setdefault(_alias, _spec)

COMMAND_ROOTS = set(COMMAND_BY_NAME)


def search_commands(query: str, category: str = "全部", limit: int = 80) -> list[CommandSpec]:
    text = (query or "").strip().lower()
    result: list[CommandSpec] = []
    for spec in COMMANDS:
        if category and category not in ("全部", "all") and spec.category != category:
            continue
        if text and text not in spec.search_text:
            continue
        result.append(spec)
    return result[: max(1, int(limit))]


def command_categories() -> list[str]:
    seen: list[str] = ["全部"]
    for spec in COMMANDS:
        if spec.category not in seen:
            seen.append(spec.category)
    return seen


# ---------------------------------------------------------------------------
# 指令校验

@dataclass(frozen=True)
class ValidationIssue:
    level: str  # error / warn / info
    message: str

    @property
    def icon(self) -> str:
        return {"error": "×", "warn": "!", "info": "i"}.get(self.level, "i")


_COORD_RE = re.compile(r"^[~^]?-?\d*(?:\.\d+)?$")
_SELECTOR_RE = re.compile(r"@[praes](?:\[[^\]]*\])?")


def _brackets_balanced(text: str) -> tuple[bool, str]:
    pairs = {"(": ")", "[": "]", "{": "}"}
    closing = {value: key for key, value in pairs.items()}
    stack: list[str] = []
    quote = ""
    escaped = False
    for char in text:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in ("'", '"'):
            quote = char
            continue
        if char in pairs:
            stack.append(char)
        elif char in closing:
            if not stack or stack[-1] != closing[char]:
                return False, f"括号不匹配：多出来的 {char}"
            stack.pop()
    if quote:
        return False, "引号没有闭合"
    if stack:
        return False, f"括号没有闭合：还差 {''.join(reversed(stack))}"
    return True, ""


def _numeric_coordinate(token: str) -> float | None:
    if not _COORD_RE.match(token):
        return None
    if token.startswith("~") or token.startswith("^"):
        return None
    if token in ("", "+", "-"):
        return None
    try:
        return float(token)
    except ValueError:
        return None


def validate_command(command: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    text = (command or "").strip()
    if not text:
        return [ValidationIssue("error", "指令为空")]
    if "\n" in text or "\r" in text:
        issues.append(ValidationIssue("error", "一条指令里不能换行；多条请分开填写"))
        text = text.replace("\r", " ").replace("\n", " ")
    if text.startswith("/"):
        text = text[1:].lstrip()
    if not text:
        return [ValidationIssue("error", "斜杠后面没有内容")]

    if len(text) > 32500:
        issues.append(ValidationIssue("error", "超过命令方块 32500 字符上限"))
    elif len(text) > 256:
        issues.append(ValidationIssue("warn", "超过聊天栏 256 字符上限，只能放进命令方块或函数"))

    ok, reason = _brackets_balanced(text)
    if not ok:
        issues.append(ValidationIssue("error", reason))

    parts = text.split()
    root = parts[0].lower()
    if root not in COMMAND_ROOTS:
        issues.append(ValidationIssue("warn", f"未收录的指令根节点：{root}（可能是模组指令）"))
    elif root == "execute" and " run " not in f" {text} ":
        issues.append(ValidationIssue("error", "execute 必须以 run <指令> 结尾"))

    if root in ("fill", "clone"):
        coords = parts[1:7]
        numbers = [_numeric_coordinate(token) for token in coords]
        if len(numbers) == 6 and all(value is not None for value in numbers):
            xs = sorted((numbers[0], numbers[3]))  # type: ignore[arg-type]
            ys = sorted((numbers[1], numbers[4]))  # type: ignore[arg-type]
            zs = sorted((numbers[2], numbers[5]))  # type: ignore[arg-type]
            volume = (xs[1] - xs[0] + 1) * (ys[1] - ys[0] + 1) * (zs[1] - zs[0] + 1)
            if volume > 32768:
                issues.append(ValidationIssue("error", f"区域体积 {int(volume)} 超过 32768 上限"))

    for selector in _SELECTOR_RE.findall(text):
        if selector.startswith("@e") and not re.search(r"\[[^\]]*(type|limit|distance|tag|name|predicate)=", selector):
            issues.append(ValidationIssue("warn", "@e 没有加 type/limit/distance 等过滤，可能选中大量实体"))
            break

    if not issues:
        issues.append(ValidationIssue("info", "看起来没问题"))
    return issues


# ---------------------------------------------------------------------------
# 指令模板

@dataclass(frozen=True)
class TemplateField:
    key: str
    label: str
    default: str = ""
    placeholder: str = ""
    required: bool = True
    kind: str = "text"  # text / int / choice / picker / component
    options: tuple[game_ids.Option, ...] = ()
    picker: str = ""
    help: str = ""


@dataclass(frozen=True)
class CommandTemplate:
    id: str
    name: str
    description: str
    fields: tuple[TemplateField, ...]
    builder: Callable[[dict[str, str]], str]


def _value(values: dict[str, str], key: str, default: str = "") -> str:
    return (values.get(key) or default).strip()


def _json_text(text: str) -> str:
    return json.dumps({"text": text}, ensure_ascii=False)


def _tpl_give(values: dict[str, str]) -> str:
    target = _value(values, "target", "@s")
    item = _value(values, "item", "minecraft:diamond_sword")
    count = _value(values, "count", "1")
    extra = _value(values, "extra")
    command = f"/give {target} {item}{extra} {count}"
    return command


def _tpl_tp(values: dict[str, str]) -> str:
    return (
        f"/tp {_value(values, 'target', '@s')} "
        f"{_value(values, 'x', '~')} {_value(values, 'y', '~')} {_value(values, 'z', '~')}"
    )


def _tpl_setblock(values: dict[str, str]) -> str:
    return (
        f"/setblock {_value(values, 'x', '~')} {_value(values, 'y', '~')} {_value(values, 'z', '~')} "
        f"{_value(values, 'block', 'minecraft:stone')} {_value(values, 'mode', 'replace')}"
    )


def _tpl_fill(values: dict[str, str]) -> str:
    return (
        f"/fill {_value(values, 'x1', '~-5')} {_value(values, 'y1', '~')} {_value(values, 'z1', '~-5')} "
        f"{_value(values, 'x2', '~5')} {_value(values, 'y2', '~5')} {_value(values, 'z2', '~5')} "
        f"{_value(values, 'block', 'minecraft:glass')} {_value(values, 'mode', 'replace')}"
    )


def _tpl_summon(values: dict[str, str]) -> str:
    extra = _value(values, "nbt")
    base = (
        f"/summon {_value(values, 'entity', 'minecraft:zombie')} "
        f"{_value(values, 'x', '~')} {_value(values, 'y', '~1')} {_value(values, 'z', '~')}"
    )
    return f"{base} {extra}" if extra else base


def _tpl_effect(values: dict[str, str]) -> str:
    return (
        f"/effect give {_value(values, 'target', '@s')} {_value(values, 'effect', 'minecraft:speed')} "
        f"{_value(values, 'duration', '30')} {_value(values, 'amplifier', '1')}"
    )


def _tpl_enchant(values: dict[str, str]) -> str:
    return (
        f"/enchant {_value(values, 'target', '@s')} {_value(values, 'enchantment', 'minecraft:sharpness')} "
        f"{_value(values, 'level', '5')}"
    )


def _tpl_title(values: dict[str, str]) -> str:
    position = _value(values, "position", "title")
    text = _value(values, "text", "欢迎来到喵喵世界")
    return f"/title {_value(values, 'target', '@a')} {position} {_json_text(text)}"


def _tpl_tellraw(values: dict[str, str]) -> str:
    return f"/tellraw {_value(values, 'target', '@a')} {_json_text(_value(values, 'text', '你好'))}"


def _tpl_playsound(values: dict[str, str]) -> str:
    return (
        f"/playsound {_value(values, 'sound', 'minecraft:entity.player.levelup')} "
        f"{_value(values, 'source', 'master')} {_value(values, 'target', '@a')} ~ ~ ~ "
        f"{_value(values, 'volume', '1')} {_value(values, 'pitch', '1')}"
    )


def _tpl_particle(values: dict[str, str]) -> str:
    return (
        f"/particle {_value(values, 'particle', 'minecraft:flame')} "
        f"{_value(values, 'x', '~')} {_value(values, 'y', '~1')} {_value(values, 'z', '~')} "
        f"{_value(values, 'dx', '0.2')} {_value(values, 'dy', '0.2')} {_value(values, 'dz', '0.2')} "
        f"{_value(values, 'speed', '0.01')} {_value(values, 'count', '30')}"
    )


def _tpl_execute(values: dict[str, str]) -> str:
    return (
        f"/execute as {_value(values, 'target', '@a')} at @s "
        f"if {_value(values, 'condition', 'entity @s type=minecraft:player')} "
        f"run {_value(values, 'command', 'say hello')}"
    )


def _tpl_scoreboard_add(values: dict[str, str]) -> str:
    return (
        f"/scoreboard objectives add {_value(values, 'objective', 'kills')} "
        f"{_value(values, 'criterion', 'dummy')}"
    )


def _tpl_scoreboard_set(values: dict[str, str]) -> str:
    return (
        f"/scoreboard players set {_value(values, 'target', '@s')} "
        f"{_value(values, 'objective', 'kills')} {_value(values, 'value', '0')}"
    )


def _tpl_spawnpoint(values: dict[str, str]) -> str:
    return (
        f"/spawnpoint {_value(values, 'target', '@s')} "
        f"{_value(values, 'x', '~')} {_value(values, 'y', '~')} {_value(values, 'z', '~')}"
    )


def _tpl_locate(values: dict[str, str]) -> str:
    return f"/locate {_value(values, 'type', 'structure')} {_value(values, 'id', 'minecraft:stronghold')}"


def _tpl_worldborder(values: dict[str, str]) -> str:
    return f"/worldborder set {_value(values, 'diameter', '2000')}"


def _tpl_time(values: dict[str, str]) -> str:
    return f"/time set {_value(values, 'value', 'day')}"


def _tpl_weather(values: dict[str, str]) -> str:
    return (
        f"/weather {_value(values, 'type', 'clear')} "
        f"{_value(values, 'duration', '600')}"
    )


def _tpl_gamerule(values: dict[str, str]) -> str:
    return f"/gamerule {_value(values, 'rule', 'keepInventory')} {_value(values, 'value', 'true')}"


def _tpl_clear(values: dict[str, str]) -> str:
    return (
        f"/clear {_value(values, 'target', '@s')} "
        f"{_value(values, 'item', 'minecraft:dirt')} {_value(values, 'count', '64')}"
    )


def _tpl_xp(values: dict[str, str]) -> str:
    return (
        f"/xp add {_value(values, 'target', '@s')} {_value(values, 'amount', '30')} "
        f"{_value(values, 'unit', 'levels')}"
    )


def _tpl_damage(values: dict[str, str]) -> str:
    return (
        f"/damage {_value(values, 'target', '@s')} {_value(values, 'amount', '5')} "
        f"{_value(values, 'type', 'minecraft:magic')}"
    )




def _target_field(default: str = "@s") -> TemplateField:
    return TemplateField(
        "target",
        "目标",
        default,
        "选择要让谁执行",
        kind="choice",
        options=game_ids.TARGETS,
        help="常用选择器都在列表里，选完下方会显示它代表什么。",
    )


COMMAND_TEMPLATES: tuple[CommandTemplate, ...] = (
    CommandTemplate("give", "给予物品", "支持 1.20.5+ 组件语法，也兼容旧版 NBT。", (
        _target_field(),
        TemplateField(
            "item", "物品 ID", "minecraft:diamond_sword", "搜索中文名或 ID",
            kind="picker", picker="item",
            help="点“选择”可以按中文名搜索，也可以直接粘贴命名空间 ID。",
        ),
        TemplateField(
            "extra", "物品组件（可空）", "", "例如 [custom_name=…]",
            kind="component",
            help="1.20.5+ 用 [组件]；1.20.4 及更早用 {NBT}。点“组件”看可用项和说明。",
        ),
        TemplateField(
            "count", "数量", "1", "1-64", kind="int",
            help="一次最多堆叠 64 个；超过 64 会被游戏截断。",
        ),
    ), _tpl_give),
    CommandTemplate("tp", "传送", "把目标送到坐标，支持 ~ 相对坐标。", (
        _target_field(),
        TemplateField("x", "X", "~", "支持 ~ 和 ^", help="~ 表示相对当前位置，^ 表示相对朝向。"),
        TemplateField("y", "Y", "~", "支持 ~"),
        TemplateField("z", "Z", "~", "支持 ~"),
    ), _tpl_tp),
    CommandTemplate("setblock", "放置方块", "单方块放置，支持 replace / destroy / keep。", (
        TemplateField("x", "X", "~"), TemplateField("y", "Y", "~"), TemplateField("z", "Z", "~"),
        TemplateField("block", "方块 ID", "minecraft:stone", kind="picker", picker="block"),
        TemplateField("mode", "模式", "replace", kind="choice", options=game_ids.MODES),
    ), _tpl_setblock),
    CommandTemplate("fill", "区域填充", "一次最多 32768 格，超出请拆成多条。", (
        TemplateField("x1", "起点 X", "~-5"), TemplateField("y1", "起点 Y", "~"), TemplateField("z1", "起点 Z", "~-5"),
        TemplateField("x2", "终点 X", "~5"), TemplateField("y2", "终点 Y", "~5"), TemplateField("z2", "终点 Z", "~5"),
        TemplateField("block", "方块 ID", "minecraft:glass", kind="picker", picker="block"),
        TemplateField("mode", "模式", "replace", kind="choice", options=game_ids.MODES),
    ), _tpl_fill),
    CommandTemplate("summon", "生成实体", "生成实体并可选附加 NBT。", (
        TemplateField("entity", "实体 ID", "minecraft:zombie", kind="picker", picker="entity"),
        TemplateField("x", "X", "~"), TemplateField("y", "Y", "~1"), TemplateField("z", "Z", "~"),
        TemplateField("nbt", "NBT（可空）", "", '{NoAI:1b,CustomName:\'"测试"\'}',
                      help="可选。NBT 写错会导致实体生成失败，先用空值测试。"),
    ), _tpl_summon),
    CommandTemplate("effect", "状态效果", "给予速度、力量等药水效果。", (
        _target_field(),
        TemplateField("effect", "效果 ID", "minecraft:speed", kind="picker", picker="effect"),
        TemplateField("duration", "持续秒数", "30", kind="int",
                      help="单位是秒；写 0 或负数通常表示立即结束。"),
        TemplateField("amplifier", "等级（0 为 I 级）", "1", kind="int",
                      help="0 = I 级，1 = II 级，以此类推。"),
    ), _tpl_effect),
    CommandTemplate("enchant", "附魔", "受原版附魔规则限制，不能乱叠冲突附魔。", (
        _target_field(),
        TemplateField("enchantment", "附魔 ID", "minecraft:sharpness", kind="picker", picker="enchantment"),
        TemplateField("level", "等级", "5", kind="int",
                      help="不能超过该附魔的原版最大等级。"),
    ), _tpl_enchant),
    CommandTemplate("title", "标题文字", "自动把文字包装成 JSON 文本。", (
        _target_field("@a"),
        TemplateField("position", "位置", "title", kind="choice", options=game_ids.POSITIONS),
        TemplateField("text", "文字", "欢迎来到喵喵世界"),
    ), _tpl_title),
    CommandTemplate("tellraw", "JSON 文本", "自动包装成 JSON，可再手动加颜色。", (
        _target_field("@a"),
        TemplateField("text", "文字", "你好，冒险者"),
    ), _tpl_tellraw),
    CommandTemplate("playsound", "播放声音", "在目标位置播放音效。", (
        TemplateField("sound", "声音 ID", "minecraft:entity.player.levelup", kind="picker", picker="sound"),
        TemplateField("source", "声音频道", "master", kind="choice", options=game_ids.SOUND_SOURCES),
        _target_field("@a"),
        TemplateField("volume", "音量", "1", help="1 是正常音量，0.5 是一半。"),
        TemplateField("pitch", "音调", "1", help="1 是原调，2 高一个八度，0.5 低一个八度。"),
    ), _tpl_playsound),
    CommandTemplate("particle", "粒子效果", "生成粒子，适合做特效。", (
        TemplateField("particle", "粒子 ID", "minecraft:flame", kind="picker", picker="particle"),
        TemplateField("x", "X", "~"), TemplateField("y", "Y", "~1"), TemplateField("z", "Z", "~"),
        TemplateField("dx", "扩散 X", "0.2"), TemplateField("dy", "扩散 Y", "0.2"), TemplateField("dz", "扩散 Z", "0.2"),
        TemplateField("speed", "速度", "0.01"),
        TemplateField("count", "数量", "30", kind="int"),
    ), _tpl_particle),
    CommandTemplate("execute", "execute 条件执行", "复杂指令的核心：指定执行者、位置与条件。", (
        TemplateField("target", "执行者", "@a", kind="choice", options=game_ids.TARGETS),
        TemplateField(
            "condition", "条件", "entity @s type=minecraft:player",
            "entity @s type=… / block ~ ~-1 ~ …",
            help="常用写法：entity @s type=minecraft:player、block ~ ~-1 ~ minecraft:stone。",
        ),
        TemplateField("command", "执行的指令", "say hello",
                      help="不要写开头的 /，例如 say hello。"),
    ), _tpl_execute),
    CommandTemplate("scoreboard_add", "计分板：新建目标", "创建一个计分板目标。", (
        TemplateField("objective", "目标名", "kills", help="只能用字母、数字、下划线。"),
        TemplateField(
            "criterion",
            "准则",
            "dummy",
            kind="choice",
            options=tuple(
                game_ids.Option(item.value, f"{item.value} — {item.name}", item.help)
                for item in game_ids.CRITERIA
            ),
            help="决定这项分数怎么涨，例如 dummy 需要手动加、kill 会自动统计击杀。",
        ),
    ), _tpl_scoreboard_add),
    CommandTemplate("scoreboard_set", "计分板：设置分数", "给玩家或实体设置分数。", (
        _target_field(),
        TemplateField("objective", "目标名", "kills"),
        TemplateField("value", "分数", "0", kind="int"),
    ), _tpl_scoreboard_set),
    CommandTemplate("spawnpoint", "设置重生点", "设置个人重生点。", (
        _target_field(),
        TemplateField("x", "X", "~"), TemplateField("y", "Y", "~"), TemplateField("z", "Z", "~"),
    ), _tpl_spawnpoint),
    CommandTemplate("locate", "查找最近结构", "查找最近的结构/群系/兴趣点。", (
        TemplateField("type", "类型", "structure", kind="choice", options=game_ids.LOCATE_TYPES),
        TemplateField("id", "ID", "minecraft:stronghold", kind="picker", picker="structure",
                      help="type 选 biome / poi 时，这里也可以手动填对应 ID。"),
    ), _tpl_locate),
    CommandTemplate("worldborder", "世界边界", "设置世界边界直径。", (
        TemplateField("diameter", "直径（格）", "2000", kind="int"),
    ), _tpl_worldborder),
    CommandTemplate("time", "设置时间", "day / night / noon / midnight 或数字。", (
        TemplateField("value", "时间", "day", kind="choice", options=game_ids.TIME_VALUES),
    ), _tpl_time),
    CommandTemplate("weather", "设置天气", "clear / rain / thunder。", (
        TemplateField("type", "天气", "clear", kind="choice", options=game_ids.WEATHER_VALUES),
        TemplateField("duration", "持续秒数", "600", kind="int"),
    ), _tpl_weather),
    CommandTemplate("gamerule", "游戏规则", "例如 keepInventory、doDaylightCycle。", (
        TemplateField("rule", "规则名", "keepInventory", kind="picker", picker="gamerule",
                      help="列表里每条规则都带中文说明。"),
        TemplateField("value", "值", "true", "true / false / 数字",
                      help="开关类规则填 true / false；randomTickSpeed 这类填数字。"),
    ), _tpl_gamerule),
    CommandTemplate("clear", "清除物品", "清空背包或指定物品。", (
        _target_field(),
        TemplateField("item", "物品 ID", "minecraft:dirt", kind="picker", picker="item"),
        TemplateField("count", "数量", "64", kind="int"),
    ), _tpl_clear),
    CommandTemplate("xp", "经验值", "增加或设置经验。", (
        _target_field(),
        TemplateField("amount", "数量", "30", kind="int"),
        TemplateField("unit", "单位", "levels", kind="choice", options=game_ids.UNITS),
    ), _tpl_xp),
    CommandTemplate("damage", "造成伤害", "按指定伤害类型造成伤害。", (
        _target_field(),
        TemplateField("amount", "伤害值", "5", kind="int"),
        TemplateField("type", "伤害类型", "minecraft:magic", kind="picker", picker="damage"),
    ), _tpl_damage),
)

TEMPLATE_BY_ID = {item.id: item for item in COMMAND_TEMPLATES}


def default_template_values(template: CommandTemplate) -> dict[str, str]:
    return {field.key: field.default for field in template.fields}


def build_from_template(template_id: str, values: dict[str, str]) -> str:
    template = TEMPLATE_BY_ID.get(template_id)
    if template is None:
        raise ValueError(f"未知模板：{template_id}")
    merged = default_template_values(template)
    merged.update({key: value for key, value in values.items() if value is not None})
    command = template.builder(merged).strip()
    return command if command.startswith("/") else "/" + command


# ---------------------------------------------------------------------------
# 命令方块链 / 函数 / 数据包

def _nbt_quote(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


_FACING_OFFSETS = {
    "east": (1, 0, 0),
    "west": (-1, 0, 0),
    "south": (0, 0, 1),
    "north": (0, 0, -1),
    "up": (0, 1, 0),
    "down": (0, -1, 0),
}


def build_chain_blocks(
    commands: Iterable[str],
    origin: tuple[int, int, int] = (0, 64, 0),
    first: str = "repeating",
    conditional: bool = False,
    auto: bool = True,
    facing: str = "east",
) -> list[str]:
    """生成一排命令方块的 /setblock 放置指令。

    ``first``：repeating（循环）或 impulse（脉冲）。后续统一使用连锁方块。
    ``facing``：方块朝向，决定链的延伸方向。
    """
    clean = [str(item).strip().lstrip("/") for item in commands if str(item).strip()]
    if not clean:
        raise ValueError("至少需要一条指令")
    if any("\n" in item for item in clean):
        raise ValueError("命令方块里的指令不能换行")
    direction = facing if facing in _FACING_OFFSETS else "east"
    dx, dy, dz = _FACING_OFFSETS[direction]
    ox, oy, oz = origin
    auto_value = "1b" if auto else "0b"
    lines: list[str] = []
    for index, command in enumerate(clean):
        block = (
            "repeating_command_block"
            if index == 0 and first == "repeating"
            else ("command_block" if index == 0 else "chain_command_block")
        )
        x = ox + dx * index
        y = oy + dy * index
        z = oz + dz * index
        # facing / conditional 是方块状态，Command / auto 才是方块实体 NBT。
        block_state = (
            f"[facing={direction},"
            f"conditional={'true' if conditional and index else 'false'}]"
        )
        nbt = f'{{Command:"{_nbt_quote(command)}",auto:{auto_value}}}'
        lines.append(
            f"/setblock {x} {y} {z} minecraft:{block}{block_state}{nbt} replace"
        )
    return lines


def build_mcfunction(commands: Iterable[str], header: bool = True) -> str:
    lines: list[str] = []
    if header:
        lines.append("# Generated by Darkitten Launcher - Game Assistant")
    for command in commands:
        text = str(command).strip()
        if not text:
            continue
        if text.startswith("#"):
            lines.append(text)
        else:
            # .mcfunction 里的指令不能带开头的斜杠。
            lines.append(text[1:] if text.startswith("/") else text)
    return "\n".join(lines) + ("\n" if lines else "")


_VERSION_RE = re.compile(r"(\d+)\.(\d+)")


def _version_tuple(version: str) -> tuple[int, int]:
    match = _VERSION_RE.search(version or "")
    if not match:
        return (1, 21)
    return (int(match.group(1)), int(match.group(2)))


def datapack_folders(version: str = "1.21.4") -> tuple[str, str]:
    """返回（函数目录, 函数标签目录），1.21 起改成单数。"""
    if _version_tuple(version) >= (1, 21):
        return ("function", "function")
    return ("functions", "functions")


def pack_format_for(version: str = "1.21.4") -> int:
    major, minor = _version_tuple(version)
    if major >= 1 and minor >= 21:
        return 61
    if (major, minor) >= (1, 20):
        return 26 if minor >= 4 else 15
    if (major, minor) >= (1, 19):
        return 12 if minor >= 4 else 10
    if (major, minor) >= (1, 18):
        return 8
    if (major, minor) >= (1, 17):
        return 7
    if (major, minor) >= (1, 16):
        return 6
    return 4


_NAMESPACE_RE = re.compile(r"^[a-z0-9_.-]+$")


def build_datapack(
    name: str,
    commands: Iterable[str],
    namespace: str = "dkl",
    version: str = "1.21.4",
    load: bool = False,
    tick: bool = False,
    description: str = "由 Darkitten Launcher 游戏助手生成",
    extra_files: dict[str, str] | None = None,
) -> bytes:
    """把一个函数打包成最小可用的数据包 zip，返回字节。"""
    function_name = (name or "main").strip().lower()
    function_name = re.sub(r"[^a-z0-9_.-]+", "_", function_name) or "main"
    namespace = (namespace or "dkl").strip().lower()
    if not _NAMESPACE_RE.match(namespace):
        raise ValueError("命名空间只能使用小写字母、数字、下划线、点和短横线")
    function_dir, tag_dir = datapack_folders(version)
    clean_commands = [str(item).strip() for item in commands if str(item).strip()]
    if not clean_commands:
        raise ValueError("没有可导出的指令")
    body = build_mcfunction(clean_commands)

    meta = {
        "pack": {
            "pack_format": pack_format_for(version),
            "supported_formats": {"min_inclusive": 4, "max_inclusive": 999},
            "description": description,
        }
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("pack.mcmeta", json.dumps(meta, ensure_ascii=False, indent=2))
        archive.writestr(f"data/{namespace}/{function_dir}/{function_name}.mcfunction", body)
        if load:
            archive.writestr(
                f"data/minecraft/tags/{tag_dir}/load.json",
                json.dumps({"values": [f"{namespace}:{function_name}"]}, ensure_ascii=False, indent=2),
            )
        if tick:
            archive.writestr(
                f"data/minecraft/tags/{tag_dir}/tick.json",
                json.dumps({"values": [f"{namespace}:{function_name}"]}, ensure_ascii=False, indent=2),
            )
        for path, content in (extra_files or {}).items():
            archive.writestr(path, content)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# AI 指令生成

class AICommandError(RuntimeError):
    pass


@dataclass
class AIConfig:
    base_url: str = DEFAULT_AI_CONFIG["base_url"]
    model: str = DEFAULT_AI_CONFIG["model"]
    api_key: str = ""
    temperature: float = 0.2
    timeout: int = 90

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "AIConfig":
        data = data or {}
        return cls(
            base_url=str(data.get("base_url") or DEFAULT_AI_CONFIG["base_url"]).strip(),
            model=str(data.get("model") or DEFAULT_AI_CONFIG["model"]).strip(),
            api_key=str(data.get("api_key") or ""),
            temperature=float(data.get("temperature", 0.2)),
            timeout=int(data.get("timeout", 90)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "model": self.model,
            "api_key": self.api_key,
            "temperature": self.temperature,
            "timeout": self.timeout,
        }

    @property
    def endpoint(self) -> str:
        base = (self.base_url or "").rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        if base.endswith("/v1"):
            return base + "/chat/completions"
        if "api.openai.com" in base:
            return base + "/v1/chat/completions"
        return base + "/chat/completions"


def load_assistant_settings() -> dict[str, Any]:
    data: dict[str, Any] = {}
    try:
        if ASSISTANT_CONFIG_PATH.exists():
            loaded = json.loads(ASSISTANT_CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
    except (OSError, ValueError):
        data = {}
    merged = deepcopy(data)
    ai = dict(DEFAULT_AI_CONFIG)
    if isinstance(data.get("ai"), dict):
        ai.update(data["ai"])
    merged["ai"] = ai
    return merged


def save_assistant_settings(settings: dict[str, Any]) -> None:
    current = load_assistant_settings()
    current.update({key: value for key, value in settings.items() if key != "ai"})
    if "ai" in settings and isinstance(settings["ai"], dict):
        current["ai"] = {**current.get("ai", {}), **settings["ai"]}
    ASSISTANT_CONFIG_PATH.write_text(
        json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8"
    )


AI_SYSTEM_PROMPT = (
    "你是 Minecraft Java 版指令专家。请根据用户需求生成可以直接执行的指令。"
    "规则：只输出指令或注释，不要解释，不要 Markdown 代码围栏。"
    "每条指令独占一行，以 / 开头。需要多条指令时按执行顺序排列。"
    "优先使用当前版本存在的语法；不确定版本差异时在注释里说明。"
    "不要生成破坏存档、外挂、盗号或攻击服务器的内容。"
)


def build_ai_messages(
    request: str,
    version: str = "1.21.4",
    context: str = "",
) -> list[dict[str, str]]:
    detail = f"目标版本：{version}。"
    if context.strip():
        detail += f"\n额外上下文：{context.strip()}"
    return [
        {"role": "system", "content": AI_SYSTEM_PROMPT},
        {"role": "user", "content": f"{detail}\n需求：{request.strip()}"},
    ]


def clean_ai_output(text: str) -> str:
    """去掉 Markdown 围栏、编号和多余解释，只保留指令行。"""
    lines: list[str] = []
    for raw in (text or "").replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if not line or line.startswith("```"):
            continue
        line = re.sub(r"^(?:[-*]\s+|\d+[.)]\s+)", "", line)
        if not line:
            continue
        if line.startswith("/") or line.startswith("#") or line.startswith("//"):
            lines.append(line)
    return "\n".join(lines)


def ai_generate_commands(
    request: str,
    config: AIConfig,
    version: str = "1.21.4",
    context: str = "",
) -> str:
    if not (request or "").strip():
        raise AICommandError("请先写清楚要生成什么指令")
    if not config.base_url:
        raise AICommandError("还没有配置 AI 接口地址")
    payload = {
        "model": config.model,
        "messages": build_ai_messages(request, version, context),
        "temperature": max(0.0, min(2.0, float(config.temperature))),
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "DarkittenLauncher-GameAssistant/1.0",
    }
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    request_obj = urllib.request.Request(
        config.endpoint, data=data, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request_obj, timeout=max(10, config.timeout)) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        detail = ""
        try:
            detail = error.read().decode("utf-8", "replace")[:500]
        except Exception:
            pass
        raise AICommandError(f"AI 接口返回 HTTP {error.code}：{detail or error.reason}") from error
    except urllib.error.URLError as error:
        raise AICommandError(f"连接 AI 接口失败：{error.reason}") from error
    except TimeoutError as error:
        raise AICommandError("AI 接口超时，请检查网络或调大超时时间") from error
    except OSError as error:
        raise AICommandError(f"AI 请求失败：{error}") from error

    try:
        parsed = json.loads(raw)
        content = parsed["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as error:
        raise AICommandError("AI 返回的内容不是预期的 Chat Completions 格式") from error
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    cleaned = clean_ai_output(str(content))
    return cleaned or str(content).strip()


# ---------------------------------------------------------------------------
# EnchCracker / 附魔预测

ENCHCRACKER_JAR = "enchcracker-1.6.jar"

ENCHANT_TUTORIAL: tuple[tuple[str, str], ...] = (
    (
        "1. 准备 Java 与工具",
        "本启动器已内置 EnchCracker 1.6（Apache-2.0）。点击“启动附魔预测”即可，"
        "无需单独下载。它会读取客户端发送的附魔种子，并推算附魔台结果。",
    ),
    (
        "2. 安装 clientcommands 模组",
        "EnchCracker 依赖 clientcommands 提供的 /cenchant 指令。"
        "从官方 Releases 下载与你的游戏版本匹配的 jar，放进对应版本的 mods 文件夹，"
        "并确认已安装 Fabric（clientcommands 是 Fabric 模组）。",
    ),
    (
        "3. 进入单人存档或允许指令的服务器",
        "在聊天栏输入 /cenchant。首次使用会提示选择附魔种子来源；"
        "单人模式下可直接读取世界种子，服务器则需要管理员权限或服务器允许。",
    ),
    (
        "4. 让 EnchCracker 连接",
        "打开 EnchCracker 后，在 Minecraft 里执行 /cenchant 并按提示操作，"
        "工具窗口会显示当前附魔种子与下一次附魔结果。",
    ),
    (
        "5. 看预测结果",
        "附魔台的结果由“附魔种子 + 等级 + 书架数量 + 物品”共同决定。"
        "调整书架数量或等级后，重新执行 /cenchant 即可刷新预测。"
        "每次附魔会消耗一次随机数，结果会随种子推进。",
    ),
    (
        "6. 常见问题",
        "如果 /cenchant 不存在：检查 clientcommands 是否装对版本、是否 Fabric、是否重启游戏。"
        "如果工具连不上：确认游戏与工具在同一台机器，且没有防火墙拦截本地连接。"
        "服务器若禁用相关指令，本工具无法绕过权限。",
    ),
)

ENCHANT_LINKS: tuple[tuple[str, str], ...] = (
    ("clientcommands 下载（官方 Releases）", "https://github.com/Earthcomputer/clientcommands/releases"),
    ("cenchant 用法 Wiki", "https://github.com/Earthcomputer/clientcommands/wiki/cenchant"),
    ("EnchCracker 源码", "https://github.com/Earthcomputer/EnchCracker"),
    ("视频教程（YouTube）", "https://youtu.be/hfiTZF0hlzw"),
    ("图文教程（imgur）", "https://imgur.com/a/oaxCC5x"),
)


def enchcracker_dir() -> Path:
    return APP_DIR / "tools" / "enchcracker"


def find_enchcracker() -> Path | None:
    candidates = [
        enchcracker_dir() / ENCHCRACKER_JAR,
        enchcracker_dir() / "enchcracker.jar",
        APP_DIR / "tools" / ENCHCRACKER_JAR,
    ]
    for path in candidates:
        try:
            if path.exists() and path.stat().st_size > 10000:
                return path
        except OSError:
            continue
    return None


def find_clientcommands_mods(game_dir: str | os.PathLike[str] | None) -> list[Path]:
    if not game_dir:
        return []
    root = Path(game_dir)
    if not root.exists():
        return []
    found: list[Path] = []
    for pattern in ("mods/*clientcommands*.jar", "mods/**/*clientcommands*.jar"):
        try:
            found.extend(path for path in root.glob(pattern) if path.is_file())
        except OSError:
            continue
    unique: dict[str, Path] = {}
    for path in found:
        unique[str(path.resolve()).lower()] = path
    return sorted(unique.values(), key=lambda item: item.name.lower())


def launch_enchcracker(java_path: str, jar_path: Path | None = None) -> subprocess.Popen:
    jar = jar_path or find_enchcracker()
    if jar is None:
        raise FileNotFoundError("没有找到 EnchCracker jar，请重新安装启动器")
    if not java_path:
        raise FileNotFoundError("没有可用的 Java，请先在设置里配置 Java 路径")
    command = [
        java_path,
        "-Xms1G",
        "-Xmx1G",
        "-classpath",
        str(jar),
        "enchcracker.EnchCrackerWindow",
    ]
    kwargs: dict[str, Any] = {"cwd": str(jar.parent)}
    if os.name == "nt":
        kwargs["creationflags"] = CREATE_NO_WINDOW
    return subprocess.Popen(command, **kwargs)


# ---------------------------------------------------------------------------
# 小工具

def dimension_info() -> list[dict[str, str]]:
    return [
        {
            "name": "主世界 Overworld",
            "y": "Y -64 ~ 320（1.18+）",
            "note": "坐标基准维度；下界坐标 ×8 约等于主世界坐标。",
        },
        {
            "name": "下界 Nether",
            "y": "Y 0 ~ 256（1.18+ 仍为 0~256）",
            "note": "主世界坐标 ÷8 对应下界坐标，用于快速交通。",
        },
        {
            "name": "末地 The End",
            "y": "Y 0 ~ 256",
            "note": "主岛在 (0,0)，末地城在外部岛屿；坐标不与其他维度换算。",
        },
    ]


def seed_fingerprint(seed: int) -> str:
    digest = hashlib.sha256(str(int(seed)).encode("utf-8")).hexdigest()
    return digest[:16]


def chunk_summary(seed: int, chunk_x: int, chunk_z: int) -> dict[str, Any]:
    return {
        "seed": int(seed),
        "chunk": (int(chunk_x), int(chunk_z)),
        "block_origin": (chunk_to_block(chunk_x), chunk_to_block(chunk_z)),
        "block_center": (chunk_center(chunk_x), chunk_center(chunk_z)),
        "slime": is_slime_chunk(seed, chunk_x, chunk_z),
    }