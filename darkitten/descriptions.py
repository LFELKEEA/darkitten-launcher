from __future__ import annotations

import re
from typing import Any

CONTENT_KIND = {
    "mod": "Mod",
    "modpack": "整合包",
    "resourcepack": "材质包",
    "shader": "光影包",
}

CATEGORY_ZH = {
    "adventure": "冒险",
    "cursed": "整活",
    "decoration": "装饰",
    "economy": "经济",
    "equipment": "装备",
    "food": "食物",
    "game-mechanics": "机制",
    "library": "前置库",
    "magic": "魔法",
    "management": "管理",
    "minigame": "小游戏",
    "mobs": "生物",
    "optimization": "性能优化",
    "social": "社交",
    "storage": "存储",
    "technology": "科技",
    "transportation": "交通",
    "utility": "实用工具",
    "worldgen": "世界生成",
    "armor": "盔甲",
    "tools": "工具",
    "combat": "战斗",
    "building": "建筑",
    "cosmetic": "外观",
    "realistic": "写实",
    "simplistic": "简约",
    "themed": "主题",
    "cartoon": "卡通",
    "fantasy": "奇幻",
    "fonts": "字体",
    "gui": "界面",
    "mod-supported": "支持 Mod",
    "tweaks": "微调",
    "core": "核心",
    "api": "前置库",
}

LOADER_LABELS = {
    "fabric": "Fabric",
    "forge": "Forge",
    "neoforge": "NeoForge",
    "quilt": "Quilt",
    "liteloader": "LiteLoader",
    "rift": "Rift",
    "iris": "Iris",
    "optifine": "OptiFine",
    "datapack": "数据包",
    "bukkit": "Bukkit",
    "spigot": "Spigot",
    "paper": "Paper",
    "purpur": "Purpur",
    "folia": "Folia",
}

# 常用项目的中文介绍。键同时支持 Modrinth project id、slug 与小写标题。
CURATED: dict[str, str] = {
    "sodium": (
        "钠（Sodium）是一款渲染优化 Mod，重写了区块与光照渲染，"
        "在保持原版画风的同时大幅提升帧率，低配电脑也能更流畅。"
    ),
    "lithium": (
        "锂（Lithium）是一款通用逻辑优化 Mod，优化实体、方块刻、"
        "区块加载与 AI，通常能明显降低卡顿，且不改动游戏内容。"
    ),
    "iris": (
        "Iris 是面向 Fabric 的光影加载器，兼容 OptiFine 光影包，"
        "与 Sodium 搭配时能在较高帧率下获得完整的光影效果。"
    ),
    "indium": (
        "Indium 为 Fabric 提供 Sodium 的渲染 API 兼容层，"
        "让依赖 Fabric Rendering API 的 Mod 在 Sodium 下正常显示。"
    ),
    "sodium-extra": (
        "Sodium Extra 为 Sodium 补充更多画面选项，"
        "可以细调粒子、云层、雾效、实体距离等，兼顾画质与性能。"
    ),
    "jei": (
        "JEI（Just Enough Items）是物品与配方查询工具，"
        "按 R 查看物品用途、按 U 查看获取方式，是大型整合包的常驻助手。"
    ),
    "roughly-enough-items": (
        "REI（Roughly Enough Items）是轻量配方查看器，"
        "支持合成、熔炼、切石等配方，并可查看物品用途与来源。"
    ),
    "rei": (
        "REI（Roughly Enough Items）是轻量配方查看器，"
        "支持合成、熔炼、切石等配方，并可查看物品用途与来源。"
    ),
    "create": (
        "机械动力（Create）用旋转动力、齿轮、传送带与机械臂搭建工厂，"
        "既能做自动化生产，也能造出会动的机关与载具。"
    ),
    "ae2": (
        "应用能源 2（Applied Energistics 2）提供 ME 网络存储与自动合成，"
        "可以把海量物品装进终端，是科技整合包的核心仓储方案。"
    ),
    "applied-energistics-2": (
        "应用能源 2（Applied Energistics 2）提供 ME 网络存储与自动合成，"
        "可以把海量物品装进终端，是科技整合包的核心仓储方案。"
    ),
    "mekanism": (
        "通用机械（Mekanism）是大型科技 Mod，涵盖矿石处理、能源、"
        "传送、数字采矿与核聚变，适合喜欢自动化生产线的玩家。"
    ),
    "thermal-expansion": (
        "热力膨胀（Thermal Expansion）提供机器、能源与管道体系，"
        "玩法直观、兼容性好，是许多科技整合包的基础。"
    ),
    "immersive-engineering": (
        "沉浸工程（Immersive Engineering）以多方块机器、电线和传送带"
        "构建工业风自动化，视觉表现厚重，适合喜欢机械美感的玩家。"
    ),
    "botania": (
        "植物魔法（Botania）用花朵、魔力与符文驱动自动化，"
        "把资源生产、装备与挑战包装成一套完整的魔法科技体系。"
    ),
    "ars-nouveau": (
        "新生魔艺（Ars Nouveau）让玩家绘制法术、召唤使魔并搭建魔法自动化，"
        "风格轻快，适合喜欢自创法术组合的玩家。"
    ),
    "twilight-forest": (
        "暮色森林（Twilight Forest）新增一个永远黄昏的冒险维度，"
        "包含迷宫、Boss、战利品与专属装备，是经典的大型冒险 Mod。"
    ),
    "the-twilight-forest": (
        "暮色森林（Twilight Forest）新增一个永远黄昏的冒险维度，"
        "包含迷宫、Boss、战利品与专属装备，是经典的大型冒险 Mod。"
    ),
    "biomes-o-plenty": (
        "超多生物群系（Biomes O' Plenty）新增大量生物群系、树木与方块，"
        "让世界生成更丰富，适合建造与探索。"
    ),
    "terralith": (
        "Terralith 使用原版方块重塑世界生成，加入高山、峡谷、"
        "浮岛与洞穴等景观，不需要额外 Mod 就能获得全新的探索体验。"
    ),
    "oh-the-biomes-youll-go": (
        "You Will Go 系列生物群系 Mod 加入大量风格化群系与地物，"
        "重视氛围与建造体验，适合作为整合包的世界生成底子。"
    ),
    "farmers-delight": (
        "农夫乐事（Farmer's Delight）扩展农业与料理，"
        "加入新作物、厨具与大量食物，让种田和做饭更有成就感。"
    ),
    "farmers-delight-2": (
        "农夫乐事（Farmer's Delight）扩展农业与料理，"
        "加入新作物、厨具与大量食物，让种田和做饭更有成就感。"
    ),
    "waystones": (
        "传送石碑（Waystones）允许玩家在探索途中设置传送点，"
        "付费或消耗经验即可快速往返，减少长途赶路的重复劳动。"
    ),
    "xaeros-minimap": (
        "Xaero's Minimap 提供可自定义的小地图、路径点与实体标记，"
        "支持洞穴模式与多人服务器，是探索和跑图常用工具。"
    ),
    "xaeros-world-map": (
        "Xaero's World Map 生成可缩放的世界地图，"
        "记录已探索区域并支持路径点，与同作者小地图配合使用。"
    ),
    "journeymap": (
        "JourneyMap 提供实时小地图、全屏世界地图与路径点，"
        "支持网页地图和多人共享，适合长期生存与服务器使用。"
    ),
    "jade": (
        "Jade 在准星指向方块或实体时显示详细信息，"
        "包括掉落物、存储内容、生物状态与模组数据，兼容大量 Mod。"
    ),
    "appleskin": (
        "AppleSkin 显示饥饿值、饱和度与食物回复量，"
        "在准星提示中给出食物效果，帮助玩家更精确地管理补给。"
    ),
    "modmenu": (
        "Mod Menu 为 Fabric 提供一个 Mod 列表界面，"
        "可以查看已安装 Mod、打开配置页面并快速定位问题。"
    ),
    "cloth-config": (
        "Cloth Config 是许多 Fabric Mod 共用的配置界面库，"
        "为玩家提供统一的设置面板，通常作为其他 Mod 的前置安装。"
    ),
    "architectury-api": (
        "Architectury API 是跨加载器开发的前置库，"
        "让同一份代码同时运行在 Fabric、Forge 与 NeoForge 上，"
        "许多跨平台 Mod 都需要它。"
    ),
    "fabric-api": (
        "Fabric API 是 Fabric 生态的基础前置库，"
        "提供事件、网络、渲染与数据包等接口，绝大多数 Fabric Mod 都依赖它。"
    ),
    "geckolib": (
        "GeckoLib 是动画前置库，为实体、方块与物品提供骨骼动画，"
        "许多生物与Boss Mod 都需要它才能正常播放动作。"
    ),
    "curios": (
        "Curios 提供额外的饰品栏位，允许戒指、护符、腰带等饰品同时生效，"
        "是许多装备与魔法 Mod 的前置。"
    ),
    "patchouli": (
        "Patchouli 是游戏内手册前置库，为 Mod 提供可翻阅的图文指南，"
        "大型整合包常用它来写教程与说明。"
    ),
    "kotlin-for-forge": (
        "Kotlin for Forge 为 Forge / NeoForge 提供 Kotlin 运行环境，"
        "是使用 Kotlin 编写的 Mod 的必需前置。"
    ),
    "modernfix": (
        "ModernFix 面向现代版本 Minecraft 做启动、内存与渲染修复，"
        "可以缩短加载时间并减少部分崩溃，适合大型整合包。"
    ),
    "ferritecore": (
        "FerriteCore 优化模型与方块状态的内存占用，"
        "在大型整合包中能明显降低内存压力，对玩法没有改动。"
    ),
    "entityculling": (
        "Entity Culling 跳过看不见的实体与方块实体渲染，"
        "在生物密集或机械较多的场景中能提升帧率。"
    ),
    "distant-horizons": (
        "Distant Horizons 使用 LOD 技术渲染远处地形，"
        "可以显著拉长视距，让远景山脉与建筑不再被雾裁掉。"
    ),
    "krypton": (
        "Krypton 优化网络栈，降低多人游戏中的延迟与带宽占用，"
        "适合服务器和高延迟环境。"
    ),
    "c2me-fabric": (
        "C2ME 优化区块生成与加载的并行度，"
        "在探索新地形时减少卡顿，通常与 Lithium 等优化 Mod 搭配。"
    ),
    "embeddium": (
        "Embeddium 是面向 Forge / NeoForge 的渲染优化 Mod，"
        "基于 Sodium 思路重写渲染管线，提升帧率并兼容部分光影。"
    ),
    "oculus": (
        "Oculus 为 Forge / NeoForge 提供光影支持，"
        "兼容 OptiFine 光影包，常与 Embeddium 搭配使用。"
    ),
    "rubidium": (
        "Rubidium 是 Forge 平台的渲染优化 Mod，"
        "通过重写区块渲染提升帧率，适合中低配电脑。"
    ),
    "sophisticated-backpacks": (
        "精致背包（Sophisticated Backpacks）加入可升级的背包，"
        "支持存储、自动拾取、过滤与工具升级，是冒险与整理的实用装备。"
    ),
    "waystones-reforged": (
        "传送石碑（Waystones）允许玩家在探索途中设置传送点，"
        "付费或消耗经验即可快速往返，减少长途赶路的重复劳动。"
    ),
    "structory": (
        "Structory 新增一批与原版风格一致的结构，"
        "包括废墟、营地与小遗迹，让探索时更容易遇到惊喜。"
    ),
    "towns-and-towers": (
        "Towns and Towers 扩充村庄与塔楼结构，"
        "加入新的建筑组合和居民场景，适合喜欢探索与建造的玩家。"
    ),
    "when-dungeons-arise": (
        "When Dungeons Arise 新增大量大型地牢与遗迹，"
        "结构规模夸张、战利品丰富，适合冒险整合包。"
    ),
    "alexs-mobs": (
        "Alex's Mobs 新增大量风格各异的生物，"
        "每种生物都有独特行为、掉落与互动，让探索更有生机。"
    ),
    "bosses-of-mass-destruction": (
        "Bosses of Mass Destruction 新增多个机制完整的 Boss 与地牢，"
        "强调战斗节奏与阶段变化，适合冒险向整合包。"
    ),
    "puzzles-lib": (
        "Puzzles Lib 是多个 Mod 共用的前置库，"
        "提供配置、事件与渲染工具，通常需要随依赖 Mod 一起安装。"
    ),
    "balm": (
        "Balm 是 Blay 系列 Mod 的跨平台前置库，"
        "提供配置与网络工具，安装相关 Mod 时需要一并放入。"
    ),
    "collective": (
        "Collective 是多个实用 Mod 共用的前置库，"
        "提供通用工具与兼容层，缺少它时依赖的 Mod 可能无法启动。"
    ),
    "supermartijn642s-config-lib": (
        "SuperMartijn642's Config Lib 是配置与界面前置库，"
        "为同作者的机器、存储与装饰 Mod 提供统一设置界面。"
    ),
    "mouse-tweaks": (
        "Mouse Tweaks 改进物品栏的鼠标拖拽与 shift 点击操作，"
        "可以快速整理、分批移动物品，是背包整理的常用微调 Mod。"
    ),
    "controlling": (
        "Controlling 为按键设置加入搜索框，"
        "可以按名称快速定位冲突按键，大型整合包中非常实用。"
    ),
    "toast-control": (
        "Toast Control 控制成就与提示弹窗，"
        "可以关闭或缩短弹出时间，减少游戏过程中的遮挡。"
    ),
}


def has_chinese(text: str) -> bool:
    return bool(re.search(r"[\u3400-\u9fff]", str(text or "")))


def _human_number(value: Any) -> str:
    try:
        number = int(value)
    except Exception:
        return ""
    if number >= 1_000_000:
        return f"{number / 1_000_000:.1f}M"
    if number >= 1_000:
        return f"{number / 1_000:.1f}K"
    return str(number)


def _text_list(value: Any, limit: int = 0) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    items = [str(item).strip() for item in value if str(item).strip()]
    return items[:limit] if limit else items


def category_labels(categories: Any, limit: int = 3) -> list[str]:
    result: list[str] = []
    for item in _text_list(categories):
        label = CATEGORY_ZH.get(item.lower().replace("_", "-"))
        if label and label not in result:
            result.append(label)
        if len(result) >= limit:
            break
    return result


def loader_labels(loaders: Any, fallback: str = "", limit: int = 3) -> list[str]:
    result: list[str] = []
    for item in _text_list(loaders) or ([fallback] if fallback else []):
        label = LOADER_LABELS.get(item.lower(), item)
        if label and label not in result:
            result.append(label)
        if len(result) >= limit:
            break
    return result


def _project_keys(data: dict) -> list[str]:
    keys: list[str] = []
    for name in ("project_id", "id", "slug", "title"):
        value = str(data.get(name) or "").strip().lower()
        if value and value not in keys:
            keys.append(value)
    return keys


def _versions_text(data: dict, fallback: str = "", limit: int = 4) -> str:
    versions = _text_list(data.get("game_versions"))
    if not versions and fallback:
        versions = [fallback]
    if not versions:
        return ""
    shown = versions[:limit]
    if len(versions) > limit:
        return "、".join(shown) + f" 等 {len(versions)} 个版本"
    return "、".join(shown)


def project_zh_intro(
    data: dict,
    content_type: str = "mod",
    loader: str = "",
    game_version: str = "",
    limit: int = 0,
) -> str:
    """根据项目元数据生成中文介绍；常用 Mod 使用人工整理文案。"""
    data = data if isinstance(data, dict) else {}
    for key in _project_keys(data):
        curated = CURATED.get(key)
        if curated:
            return _trim(curated, limit)
        cleaned = re.sub(r"[^a-z0-9]+", "-", key).strip("-")
        if cleaned and cleaned in CURATED:
            return _trim(CURATED[cleaned], limit)

    title = str(data.get("title") or data.get("slug") or "这个项目").strip()
    kind = CONTENT_KIND.get(str(content_type or "mod").lower(), "Mod")
    categories = category_labels(data.get("categories"), 2)
    loaders = loader_labels(data.get("loaders"), loader)
    versions = _versions_text(data, game_version)
    author = str(data.get("author") or "").strip()
    downloads = _human_number(data.get("downloads"))

    parts: list[str] = []
    if content_type == "resourcepack":
        parts.append(f"{title} 是一款 Minecraft 材质包")
        if categories:
            parts.append(f"，整体偏向{('、'.join(categories))}风格")
    elif content_type == "shader":
        parts.append(f"{title} 是一款 Minecraft 光影包")
        if categories:
            parts.append(f"，画面风格以{('、'.join(categories))}为主")
    elif content_type == "modpack":
        parts.append(f"{title} 是一个 Minecraft 整合包")
        if loaders:
            parts.append(f"，基于 {' / '.join(loaders)} 加载器")
    else:
        parts.append(f"{title} 是一个")
        if loaders:
            parts.append(f"面向 {' / '.join(loaders)} 的")
        parts.append(f"{('、'.join(categories) + '类') if categories else '综合性'} Mod")

    if versions:
        parts.append(f"，支持 Minecraft {versions}")
    parts.append("。")
    if author:
        parts.append(f"作者是 {author}。")
    if downloads:
        parts.append(f"项目累计下载约 {downloads} 次。")
    if not author and not downloads:
        parts.append("详细信息可以在项目页面查看，安装前请确认前置与游戏版本。")
    return _trim("".join(parts), limit)


def local_mod_zh_intro(
    name: str,
    loader: str = "",
    version: str = "",
    description: str = "",
) -> str:
    """为本地导入的 Mod 生成中文说明；原简介本身是中文时直接使用。"""
    if has_chinese(description):
        return _trim(description, 140)
    title = str(name or "这个 Mod").strip()
    loader_text = f"{loader} 平台的 " if loader else ""
    version_text = f"（版本 {version}）" if version else ""
    return (
        f"{title}{version_text} 是一个 {loader_text}本地 Mod，"
        "文件已经放入当前游戏版本的 mods 文件夹，启用后会被游戏加载。"
    )


def project_title(data: dict) -> str:
    return str(data.get("title") or data.get("name") or data.get("slug") or "").strip()


def _trim(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    if limit and len(text) > limit:
        return text[: max(1, limit - 1)] + "…"
    return text
