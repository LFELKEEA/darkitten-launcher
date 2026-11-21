"""游戏助手用的 ID 清单与选项说明。

这里的条目不是“全量注册表”，而是启动器里最常用、最容易被写错的一批：
物品、方块、实体、药水效果、附魔、粒子、音效、游戏规则、伤害类型和结构。
每条都带中文名，搜索时可以按中文、英文或命名空间 ID 匹配。

清单不依赖 Qt，方便单元测试和后续从 jar 里补充。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Entry:
    value: str
    name: str
    category: str = ""
    help: str = ""


@dataclass(frozen=True)
class Option:
    value: str
    label: str
    help: str = ""


def _entries(
    raw: str, category: str = "", *, third_is_category: bool = False
) -> tuple[Entry, ...]:
    """把 ``id|中文|分类或说明`` 的紧凑文本展开成 Entry 元组。

    物品/方块/实体/附魔的第三列是分类，其余数据集第三列是说明。
    """
    items: list[Entry] = []
    for line in raw.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        value, _, rest = line.partition("|")
        name, _, extra = rest.partition("|")
        extra = extra.strip()
        items.append(
            Entry(
                value=value.strip(),
                name=name.strip(),
                category=extra if third_is_category else category,
                help="" if third_is_category else extra,
            )
        )
    return tuple(items)


# ---------------------------------------------------------------------------
# 目标选择器

TARGETS: tuple[Option, ...] = (
    Option("@s", "@s — 自己", "指令执行者自己；在命令方块里是命令方块。"),
    Option("@p", "@p — 最近的玩家", "距离执行位置最近的玩家。"),
    Option("@a", "@a — 所有玩家", "所有在线玩家，包括旁观者。"),
    Option("@r", "@r — 随机玩家", "随机挑一名在线玩家。"),
    Option("@e", "@e — 所有实体", "所有实体，包括掉落物、盔甲架和玩家。"),
    Option("@n", "@n — 最近的实体", "1.21+ 可用，距离最近的任意实体。"),
    Option("@e[type=minecraft:zombie]", "@e[type=…] — 某类实体", "按实体类型过滤，例如所有僵尸。"),
    Option("@e[type=minecraft:item]", "@e[type=item] — 掉落物", "所有掉在地上的物品实体。"),
    Option("@e[type=minecraft:armor_stand]", "@e[type=armor_stand] — 盔甲架", "常用于做展示或标记位置。"),
    Option("@a[distance=..10]", "@a[distance=..10] — 身边玩家", "只选距离执行点 10 格以内的玩家。"),
    Option("@a[gamemode=creative]", "@a[gamemode=creative] — 创造玩家", "按游戏模式过滤。"),
    Option("@a[level=30..]", "@a[level=30..] — 30 级以上", "按经验等级过滤，..30 表示 30 级以下。"),
    Option("@e[tag=target]", "@e[tag=target] — 带标签的实体", "给实体 /tag 添加标签后，用标签批量选中。"),
    Option("@a[team=red]", "@a[team=red] — 队伍玩家", "按计分板队伍过滤。"),
)


# ---------------------------------------------------------------------------
# 物品

_ITEMS_RAW = """
minecraft:netherite_sword|下界合金剑|战斗
minecraft:diamond_sword|钻石剑|战斗
minecraft:iron_sword|铁剑|战斗
minecraft:stone_sword|石剑|战斗
minecraft:wooden_sword|木剑|战斗
minecraft:netherite_axe|下界合金斧|战斗
minecraft:diamond_axe|钻石斧|战斗
minecraft:mace|重锤|战斗
minecraft:bow|弓|战斗
minecraft:crossbow|弩|战斗
minecraft:trident|三叉戟|战斗
minecraft:shield|盾牌|战斗
minecraft:arrow|箭|战斗
minecraft:spectral_arrow|光灵箭|战斗
minecraft:tipped_arrow|药箭|战斗
minecraft:wind_charge|风弹|战斗
minecraft:netherite_pickaxe|下界合金镐|工具
minecraft:diamond_pickaxe|钻石镐|工具
minecraft:iron_pickaxe|铁镐|工具
minecraft:stone_pickaxe|石镐|工具
minecraft:wooden_pickaxe|木镐|工具
minecraft:netherite_shovel|下界合金锹|工具
minecraft:diamond_shovel|钻石锹|工具
minecraft:shears|剪刀|工具
minecraft:flint_and_steel|打火石|工具
minecraft:fishing_rod|钓鱼竿|工具
minecraft:brush|刷子|工具
minecraft:spyglass|望远镜|工具
minecraft:clock|时钟|工具
minecraft:compass|指南针|工具
minecraft:recovery_compass|追溯指针|工具
minecraft:map|空地图|工具
minecraft:filled_map|已填充地图|工具
minecraft:netherite_helmet|下界合金头盔|防具
minecraft:netherite_chestplate|下界合金胸甲|防具
minecraft:netherite_leggings|下界合金护腿|防具
minecraft:netherite_boots|下界合金靴子|防具
minecraft:diamond_helmet|钻石头盔|防具
minecraft:diamond_chestplate|钻石胸甲|防具
minecraft:diamond_leggings|钻石护腿|防具
minecraft:diamond_boots|钻石靴子|防具
minecraft:iron_helmet|铁头盔|防具
minecraft:iron_chestplate|铁胸甲|防具
minecraft:chainmail_chestplate|锁链胸甲|防具
minecraft:golden_helmet|金头盔|防具
minecraft:leather_chestplate|皮革外套|防具
minecraft:turtle_helmet|海龟壳|防具
minecraft:elytra|鞘翅|防具
minecraft:golden_apple|金苹果|食物
minecraft:enchanted_golden_apple|附魔金苹果|食物
minecraft:bread|面包|食物
minecraft:cooked_beef|牛排|食物
minecraft:cooked_porkchop|熟猪排|食物
minecraft:cooked_chicken|熟鸡肉|食物
minecraft:cooked_mutton|熟羊肉|食物
minecraft:cooked_salmon|熟鲑鱼|食物
minecraft:golden_carrot|金胡萝卜|食物
minecraft:apple|苹果|食物
minecraft:carrot|胡萝卜|食物
minecraft:honey_bottle|蜂蜜瓶|食物
minecraft:cake|蛋糕|食物
minecraft:cookie|曲奇|食物
minecraft:pumpkin_pie|南瓜派|食物
minecraft:mushroom_stew|蘑菇煲|食物
minecraft:rabbit_stew|兔肉煲|食物
minecraft:beetroot_soup|甜菜汤|食物
minecraft:dried_kelp|干海带|食物
minecraft:chorus_fruit|紫颂果|食物
minecraft:suspicious_stew|迷之炖菜|食物
minecraft:diamond|钻石|材料
minecraft:netherite_ingot|下界合金锭|材料
minecraft:netherite_scrap|下界合金碎片|材料
minecraft:iron_ingot|铁锭|材料
minecraft:gold_ingot|金锭|材料
minecraft:copper_ingot|铜锭|材料
minecraft:emerald|绿宝石|材料
minecraft:lapis_lazuli|青金石|材料
minecraft:redstone|红石|材料
minecraft:coal|煤炭|材料
minecraft:charcoal|木炭|材料
minecraft:quartz|下界石英|材料
minecraft:amethyst_shard|紫水晶碎片|材料
minecraft:stick|木棍|材料
minecraft:string|线|材料
minecraft:feather|羽毛|材料
minecraft:leather|皮革|材料
minecraft:paper|纸|材料
minecraft:book|书|材料
minecraft:slime_ball|黏液球|材料
minecraft:gunpowder|火药|材料
minecraft:blaze_rod|烈焰棒|材料
minecraft:blaze_powder|烈焰粉|材料
minecraft:ender_pearl|末影珍珠|材料
minecraft:ender_eye|末影之眼|材料
minecraft:ghast_tear|恶魂之泪|材料
minecraft:phantom_membrane|幻翼膜|材料
minecraft:rabbit_foot|兔子脚|材料
minecraft:rabbit_hide|兔子皮|材料
minecraft:shulker_shell|潜影壳|材料
minecraft:turtle_scute|海龟鳞甲|材料
minecraft:armadillo_scute|犰狳鳞甲|材料
minecraft:nether_star|下界之星|材料
minecraft:dragon_breath|龙息|材料
minecraft:echo_shard|回响碎片|材料
minecraft:heart_of_the_sea|海洋之心|材料
minecraft:nautilus_shell|鹦鹉螺壳|材料
minecraft:prismarine_shard|海晶碎片|材料
minecraft:prismarine_crystals|海晶砂粒|材料
minecraft:magma_cream|岩浆膏|材料
minecraft:glowstone_dust|荧石粉|材料
minecraft:sugar|糖|材料
minecraft:wheat|小麦|材料
minecraft:bone|骨头|材料
minecraft:bone_meal|骨粉|材料
minecraft:flint|燧石|材料
minecraft:clay_ball|黏土球|材料
minecraft:brick|红砖|材料
minecraft:nether_brick|下界砖|材料
minecraft:netherite_upgrade_smithing_template|下界合金升级模板|材料
minecraft:totem_of_undying|不死图腾|杂项
minecraft:name_tag|命名牌|杂项
minecraft:saddle|鞍|杂项
minecraft:lead|拴绳|杂项
minecraft:end_crystal|末地水晶|杂项
minecraft:firework_rocket|烟花火箭|杂项
minecraft:firework_star|烟火之星|杂项
minecraft:snowball|雪球|杂项
minecraft:egg|鸡蛋|杂项
minecraft:bucket|桶|杂项
minecraft:water_bucket|水桶|杂项
minecraft:lava_bucket|熔岩桶|杂项
minecraft:milk_bucket|牛奶桶|杂项
minecraft:powder_snow_bucket|细雪桶|杂项
minecraft:axolotl_bucket|美西螈桶|杂项
minecraft:oak_boat|橡木船|杂项
minecraft:oak_chest_boat|橡木运输船|杂项
minecraft:minecart|矿车|杂项
minecraft:chest_minecart|运输矿车|杂项
minecraft:hopper_minecart|漏斗矿车|杂项
minecraft:tnt_minecart|TNT 矿车|杂项
minecraft:oak_sign|橡木告示牌|杂项
minecraft:oak_door|橡木门|杂项
minecraft:oak_fence|橡木栅栏|杂项
minecraft:torch|火把|杂项
minecraft:soul_torch|灵魂火把|杂项
minecraft:lantern|灯笼|杂项
minecraft:soul_lantern|灵魂灯笼|杂项
minecraft:campfire|营火|杂项
minecraft:soul_campfire|灵魂营火|杂项
minecraft:red_bed|红色床|杂项
minecraft:respawn_anchor|重生锚|杂项
minecraft:lodestone|磁石|杂项
minecraft:bee_nest|蜂巢|杂项
minecraft:honeycomb|蜜脾|杂项
minecraft:honey_block|蜂蜜块|杂项
minecraft:potion|药水|酿造
minecraft:splash_potion|喷溅药水|酿造
minecraft:lingering_potion|滞留药水|酿造
minecraft:glass_bottle|玻璃瓶|酿造
minecraft:brewing_stand|酿造台|酿造
minecraft:cauldron|炼药锅|酿造
minecraft:nether_wart|下界疣|酿造
minecraft:fermented_spider_eye|发酵蛛眼|酿造
minecraft:spider_eye|蛛眼|酿造
minecraft:repeater|红石中继器|红石
minecraft:comparator|红石比较器|红石
minecraft:piston|活塞|红石
minecraft:sticky_piston|黏性活塞|红石
minecraft:observer|侦测器|红石
minecraft:hopper|漏斗|红石
minecraft:dropper|投掷器|红石
minecraft:dispenser|发射器|红石
minecraft:redstone_torch|红石火把|红石
minecraft:lever|拉杆|红石
minecraft:stone_button|石按钮|红石
minecraft:stone_pressure_plate|石压力板|红石
minecraft:tripwire_hook|绊线钩|红石
minecraft:target|标靶|红石
minecraft:note_block|音符盒|红石
minecraft:jukebox|唱片机|红石
minecraft:daylight_detector|阳光探测器|红石
minecraft:sculk_sensor|幽匿感测体|红石
minecraft:calibrated_sculk_sensor|校准幽匿感测体|红石
minecraft:crafter|合成器|红石
minecraft:zombie_spawn_egg|僵尸刷怪蛋|刷怪蛋
minecraft:skeleton_spawn_egg|骷髅刷怪蛋|刷怪蛋
minecraft:creeper_spawn_egg|苦力怕刷怪蛋|刷怪蛋
minecraft:enderman_spawn_egg|末影人刷怪蛋|刷怪蛋
minecraft:villager_spawn_egg|村民刷怪蛋|刷怪蛋
minecraft:iron_golem_spawn_egg|铁傀儡刷怪蛋|刷怪蛋
minecraft:wither_spawn_egg|凋灵刷怪蛋|刷怪蛋
minecraft:ender_dragon_spawn_egg|末影龙刷怪蛋|刷怪蛋
"""

ITEMS: tuple[Entry, ...] = _entries(_ITEMS_RAW, third_is_category=True)

ITEM_CATEGORIES: tuple[str, ...] = (
    "全部",
    "战斗",
    "工具",
    "防具",
    "食物",
    "材料",
    "杂项",
    "酿造",
    "红石",
    "刷怪蛋",
)


# ---------------------------------------------------------------------------
# 方块

_BLOCKS_RAW = """
minecraft:stone|石头|基础
minecraft:cobblestone|圆石|基础
minecraft:deepslate|深板岩|基础
minecraft:granite|花岗岩|基础
minecraft:diorite|闪长岩|基础
minecraft:andesite|安山岩|基础
minecraft:tuff|凝灰岩|基础
minecraft:calcite|方解石|基础
minecraft:dirt|泥土|基础
minecraft:grass_block|草方块|基础
minecraft:sand|沙子|基础
minecraft:red_sand|红沙|基础
minecraft:gravel|沙砾|基础
minecraft:clay|黏土块|基础
minecraft:snow_block|雪块|基础
minecraft:ice|冰|基础
minecraft:packed_ice|浮冰|基础
minecraft:blue_ice|蓝冰|基础
minecraft:netherrack|下界岩|下界
minecraft:soul_sand|灵魂沙|下界
minecraft:soul_soil|灵魂土|下界
minecraft:basalt|玄武岩|下界
minecraft:blackstone|黑石|下界
minecraft:end_stone|末地石|末地
minecraft:obsidian|黑曜石|特殊
minecraft:crying_obsidian|哭泣的黑曜石|特殊
minecraft:bedrock|基岩|特殊
minecraft:glass|玻璃|建筑
minecraft:tinted_glass|遮光玻璃|建筑
minecraft:glowstone|荧石|光源
minecraft:sea_lantern|海晶灯|光源
minecraft:shroomlight|菌光体|光源
minecraft:ochre_froglight|赭黄蛙明灯|光源
minecraft:verdant_froglight|青翠蛙明灯|光源
minecraft:pearlescent_froglight|珠光蛙明灯|光源
minecraft:oak_log|橡木原木|木材
minecraft:oak_planks|橡木木板|木材
minecraft:spruce_planks|云杉木板|木材
minecraft:birch_planks|白桦木板|木材
minecraft:jungle_planks|丛林木板|木材
minecraft:acacia_planks|金合欢木板|木材
minecraft:dark_oak_planks|深色橡木木板|木材
minecraft:crimson_planks|绯红木板|木材
minecraft:warped_planks|诡异木板|木材
minecraft:oak_leaves|橡树树叶|木材
minecraft:oak_slab|橡木台阶|木材
minecraft:oak_stairs|橡木楼梯|木材
minecraft:oak_fence|橡木栅栏|木材
minecraft:oak_door|橡木门|木材
minecraft:oak_trapdoor|橡木活板门|木材
minecraft:bookshelf|书架|功能
minecraft:crafting_table|工作台|功能
minecraft:furnace|熔炉|功能
minecraft:blast_furnace|高炉|功能
minecraft:smoker|烟熏炉|功能
minecraft:chest|箱子|功能
minecraft:barrel|木桶|功能
minecraft:hopper|漏斗|功能
minecraft:dispenser|发射器|功能
minecraft:dropper|投掷器|功能
minecraft:piston|活塞|功能
minecraft:sticky_piston|黏性活塞|功能
minecraft:observer|侦测器|功能
minecraft:redstone_lamp|红石灯|功能
minecraft:note_block|音符盒|功能
minecraft:jukebox|唱片机|功能
minecraft:beacon|信标|功能
minecraft:conduit|潮涌核心|功能
minecraft:respawn_anchor|重生锚|功能
minecraft:lodestone|磁石|功能
minecraft:amethyst_block|紫水晶块|矿物
minecraft:copper_block|铜块|矿物
minecraft:iron_block|铁块|矿物
minecraft:gold_block|金块|矿物
minecraft:diamond_block|钻石块|矿物
minecraft:netherite_block|下界合金块|矿物
minecraft:emerald_block|绿宝石块|矿物
minecraft:lapis_block|青金石块|矿物
minecraft:redstone_block|红石块|矿物
minecraft:coal_block|煤炭块|矿物
minecraft:quartz_block|石英块|矿物
minecraft:prismarine|海晶石|海洋
minecraft:dark_prismarine|暗海晶石|海洋
minecraft:sea_pickle|海泡菜|海洋
minecraft:kelp|海带|海洋
minecraft:seagrass|海草|海洋
minecraft:sponge|海绵|海洋
minecraft:wet_sponge|湿海绵|海洋
minecraft:moss_block|苔藓块|自然
minecraft:moss_carpet|苔藓地毯|自然
minecraft:azalea_leaves|杜鹃树叶|自然
minecraft:flowering_azalea_leaves|盛开的杜鹃树叶|自然
minecraft:sculk|幽匿块|深暗
minecraft:sculk_catalyst|幽匿催发体|深暗
minecraft:sculk_shrieker|幽匿尖啸体|深暗
minecraft:sculk_sensor|幽匿感测体|深暗
minecraft:magma_block|岩浆块|下界
minecraft:fire|火|特殊
minecraft:soul_fire|灵魂火|特殊
minecraft:water|水|特殊
minecraft:lava|熔岩|特殊
minecraft:air|空气|特殊
minecraft:barrier|屏障|特殊
minecraft:light|光源方块|特殊
minecraft:structure_block|结构方块|特殊
minecraft:command_block|命令方块|特殊
minecraft:chain_command_block|连锁命令方块|特殊
minecraft:repeating_command_block|循环命令方块|特殊
minecraft:jigsaw|拼图方块|特殊
"""

BLOCKS: tuple[Entry, ...] = _entries(_BLOCKS_RAW, third_is_category=True)

BLOCK_CATEGORIES: tuple[str, ...] = (
    "全部",
    "基础",
    "木材",
    "功能",
    "矿物",
    "光源",
    "自然",
    "海洋",
    "下界",
    "末地",
    "深暗",
    "特殊",
)


# ---------------------------------------------------------------------------
# 实体

_ENTITIES_RAW = """
minecraft:zombie|僵尸|敌对
minecraft:skeleton|骷髅|敌对
minecraft:creeper|苦力怕|敌对
minecraft:spider|蜘蛛|敌对
minecraft:cave_spider|洞穴蜘蛛|敌对
minecraft:enderman|末影人|敌对
minecraft:witch|女巫|敌对
minecraft:slime|史莱姆|敌对
minecraft:magma_cube|岩浆怪|敌对
minecraft:blaze|烈焰人|敌对
minecraft:ghast|恶魂|敌对
minecraft:wither_skeleton|凋灵骷髅|敌对
minecraft:piglin|猪灵|敌对
minecraft:piglin_brute|猪灵蛮兵|敌对
minecraft:zombified_piglin|僵尸猪灵|敌对
minecraft:hoglin|疣猪兽|敌对
minecraft:zoglin|僵尸疣猪兽|敌对
minecraft:guardian|守卫者|敌对
minecraft:elder_guardian|远古守卫者|敌对
minecraft:drowned|溺尸|敌对
minecraft:husk|尸壳|敌对
minecraft:stray|流浪者|敌对
minecraft:phantom|幻翼|敌对
minecraft:pillager|掠夺者|敌对
minecraft:vindicator|卫道士|敌对
minecraft:evoker|唤魔者|敌对
minecraft:illusioner|幻术师|敌对
minecraft:ravager|劫掠兽|敌对
minecraft:vex|恼鬼|敌对
minecraft:shulker|潜影贝|敌对
minecraft:silverfish|蠹虫|敌对
minecraft:endermite|末影螨|敌对
minecraft:breeze|旋风人|敌对
minecraft:bogged|沼骸|敌对
minecraft:warden|监守者|敌对
minecraft:wither|凋灵|Boss
minecraft:ender_dragon|末影龙|Boss
minecraft:iron_golem|铁傀儡|友好
minecraft:snow_golem|雪傀儡|友好
minecraft:villager|村民|友好
minecraft:wandering_trader|流浪商人|友好
minecraft:cat|猫|友好
minecraft:wolf|狼|友好
minecraft:fox|狐狸|友好
minecraft:axolotl|美西螈|友好
minecraft:bee|蜜蜂|友好
minecraft:panda|熊猫|友好
minecraft:polar_bear|北极熊|友好
minecraft:turtle|海龟|友好
minecraft:dolphin|海豚|友好
minecraft:squid|鱿鱼|友好
minecraft:glow_squid|发光鱿鱼|友好
minecraft:bat|蝙蝠|友好
minecraft:chicken|鸡|友好
minecraft:cow|牛|友好
minecraft:pig|猪|友好
minecraft:sheep|羊|友好
minecraft:rabbit|兔子|友好
minecraft:horse|马|友好
minecraft:donkey|驴|友好
minecraft:mule|骡|友好
minecraft:llama|羊驼|友好
minecraft:goat|山羊|友好
minecraft:frog|青蛙|友好
minecraft:tadpole|蝌蚪|友好
minecraft:allay|悦灵|友好
minecraft:armor_stand|盔甲架|其他
minecraft:item|掉落物|其他
minecraft:falling_block|下落的方块|其他
minecraft:tnt|点燃的 TNT|其他
minecraft:fireball|火球|其他
minecraft:snowball|雪球|其他
minecraft:arrow|箭|其他
minecraft:trident|三叉戟|其他
minecraft:boat|船|其他
minecraft:minecart|矿车|其他
"""

ENTITIES: tuple[Entry, ...] = _entries(_ENTITIES_RAW, third_is_category=True)

ENTITY_CATEGORIES: tuple[str, ...] = ("全部", "敌对", "Boss", "友好", "其他")


# ---------------------------------------------------------------------------
# 状态效果 / 附魔

_EFFECTS_RAW = """
minecraft:speed|速度
minecraft:slowness|缓慢
minecraft:haste|急迫
minecraft:mining_fatigue|挖掘疲劳
minecraft:strength|力量
minecraft:instant_health|瞬间治疗
minecraft:instant_damage|瞬间伤害
minecraft:jump_boost|跳跃提升
minecraft:nausea|反胃
minecraft:regeneration|生命恢复
minecraft:resistance|抗性提升
minecraft:fire_resistance|抗火
minecraft:water_breathing|水下呼吸
minecraft:invisibility|隐身
minecraft:blindness|失明
minecraft:night_vision|夜视
minecraft:hunger|饥饿
minecraft:weakness|虚弱
minecraft:poison|中毒
minecraft:wither|凋零
minecraft:health_boost|生命提升
minecraft:absorption|伤害吸收
minecraft:saturation|饱和
minecraft:glowing|发光
minecraft:levitation|飘浮
minecraft:luck|幸运
minecraft:unluck|霉运
minecraft:slow_falling|缓降
minecraft:conduit_power|潮涌能量
minecraft:dolphins_grace|海豚的恩惠
minecraft:bad_omen|不祥之兆
minecraft:hero_of_the_village|村庄英雄
minecraft:darkness|黑暗
minecraft:trial_omen|试炼之兆
minecraft:raid_omen|袭击之兆
minecraft:wind_charged|蓄风
minecraft:weaving|盘丝
minecraft:oozing|渗浆
minecraft:infested|寄生
"""

EFFECTS: tuple[Entry, ...] = _entries(_EFFECTS_RAW)

_ENCHANTMENTS_RAW = """
minecraft:sharpness|锋利|战斗
minecraft:smite|亡灵杀手|战斗
minecraft:bane_of_arthropods|节肢杀手|战斗
minecraft:knockback|击退|战斗
minecraft:fire_aspect|火焰附加|战斗
minecraft:looting|抢夺|战斗
minecraft:sweeping_edge|横扫之刃|战斗
minecraft:efficiency|效率|工具
minecraft:silk_touch|精准采集|工具
minecraft:unbreaking|耐久|工具
minecraft:fortune|时运|工具
minecraft:power|力量|远程
minecraft:punch|冲击|远程
minecraft:flame|火矢|远程
minecraft:infinity|无限|远程
minecraft:protection|保护|防具
minecraft:fire_protection|火焰保护|防具
minecraft:feather_falling|摔落缓冲|防具
minecraft:blast_protection|爆炸保护|防具
minecraft:projectile_protection|弹射物保护|防具
minecraft:respiration|水下呼吸|防具
minecraft:aqua_affinity|水下速掘|防具
minecraft:thorns|荆棘|防具
minecraft:depth_strider|深海探索者|防具
minecraft:frost_walker|冰霜行者|防具
minecraft:soul_speed|灵魂疾行|防具
minecraft:swift_sneak|迅捷潜行|防具
minecraft:mending|经验修补|通用
minecraft:vanishing_curse|消失诅咒|诅咒
minecraft:binding_curse|绑定诅咒|诅咒
minecraft:loyalty|忠诚|三叉戟
minecraft:impaling|穿刺|三叉戟
minecraft:riptide|激流|三叉戟
minecraft:channeling|引雷|三叉戟
minecraft:multishot|多重射击|弩
minecraft:quick_charge|快速装填|弩
minecraft:piercing|穿透|弩
minecraft:density|密度|重锤
minecraft:breach|破甲|重锤
minecraft:wind_burst|风爆|重锤
"""

ENCHANTMENTS: tuple[Entry, ...] = _entries(_ENCHANTMENTS_RAW, third_is_category=True)

ENCHANTMENT_CATEGORIES: tuple[str, ...] = (
    "全部",
    "战斗",
    "工具",
    "远程",
    "防具",
    "通用",
    "诅咒",
    "三叉戟",
    "弩",
    "重锤",
)


# ---------------------------------------------------------------------------
# 粒子 / 音效

_PARTICLES_RAW = """
minecraft:flame|火焰
minecraft:smoke|烟雾
minecraft:large_smoke|浓烟
minecraft:campfire_cosy_smoke|营火烟
minecraft:soul|灵魂
minecraft:soul_fire_flame|灵魂火
minecraft:lava|岩浆
minecraft:lava_drip|岩浆滴落
minecraft:water_drip|水滴
minecraft:splash|水花
minecraft:bubble|气泡
minecraft:bubble_pop|气泡破裂
minecraft:cloud|云
minecraft:crit|暴击
minecraft:enchanted_hit|附魔命中
minecraft:enchant|附魔符文
minecraft:end_rod|末地烛
minecraft:dragon_breath|龙息
minecraft:damage_indicator|伤害指示
minecraft:heart|爱心
minecraft:angry_villager|愤怒村民
minecraft:happy_villager|开心村民
minecraft:note|音符
minecraft:portal|传送门
minecraft:reverse_portal|反向传送门
minecraft:totem_of_undying|不死图腾
minecraft:explosion|爆炸
minecraft:explosion_emitter|爆炸发射器
minecraft:poof|噗
minecraft:sweep_attack|横扫
minecraft:electric_spark|电火花
minecraft:wax_on|涂蜡
minecraft:wax_off|脱蜡
minecraft:scrape|刮削
minecraft:cherry_leaves|樱花树叶
minecraft:pale_oak_leaves|苍白橡木树叶
minecraft:dust|红石尘埃
minecraft:dust_color_transition|彩色尘埃
minecraft:item|物品
minecraft:block|方块
minecraft:falling_dust|下落尘埃
minecraft:sonic_boom|音爆
minecraft:trial_spawner_detection|试炼刷怪检测
minecraft:ominous_spawning|不祥生成
"""

PARTICLES: tuple[Entry, ...] = _entries(_PARTICLES_RAW)

_SOUNDS_RAW = """
minecraft:entity.player.levelup|玩家升级
minecraft:entity.player.hurt|玩家受伤
minecraft:entity.player.death|玩家死亡
minecraft:entity.experience_orb.pickup|拾取经验
minecraft:block.note_block.pling|音符盒（竖琴）
minecraft:block.note_block.bell|音符盒（铃铛）
minecraft:block.note_block.chime|音符盒（钟琴）
minecraft:block.note_block.bass|音符盒（贝斯）
minecraft:block.note_block.harp|音符盒（钢琴）
minecraft:block.note_block.flute|音符盒（长笛）
minecraft:block.note_block.xylophone|音符盒（木琴）
minecraft:entity.creeper.primed|苦力怕引信
minecraft:entity.generic.explode|爆炸
minecraft:entity.ender_dragon.growl|末影龙低吼
minecraft:entity.wither.spawn|凋灵生成
minecraft:entity.wither.death|凋灵死亡
minecraft:entity.ghast.scream|恶魂尖叫
minecraft:entity.blaze.shoot|烈焰人射击
minecraft:entity.lightning_bolt.thunder|雷声
minecraft:ambient.cave|洞穴环境音
minecraft:block.bell.use|钟声
minecraft:block.anvil.use|铁砧使用
minecraft:block.chest.open|箱子打开
minecraft:block.ender_chest.open|末影箱打开
minecraft:item.trident.thunder|三叉戟引雷
minecraft:ui.toast.challenge_complete|挑战完成
minecraft:ui.toast.task_complete|进度完成
minecraft:music_disc.pigstep|唱片 Pigstep
minecraft:music_disc.cat|唱片 Cat
minecraft:music_disc.13|唱片 13
minecraft:music_disc.otherside|唱片 Otherside
"""

SOUNDS: tuple[Entry, ...] = _entries(_SOUNDS_RAW)


# ---------------------------------------------------------------------------
# 游戏规则 / 伤害类型 / 结构 / 其他选项

GAMERULES: tuple[Entry, ...] = (
    Entry("keepInventory", "保留物品", "玩家", "死亡后是否保留背包物品。"),
    Entry("doDaylightCycle", "昼夜循环", "时间", "是否让太阳和月亮自动移动。"),
    Entry("doWeatherCycle", "天气循环", "天气", "是否让天气自动变化。"),
    Entry("doMobSpawning", "自然生成生物", "生成", "是否允许自然生成怪物和动物。"),
    Entry("mobGriefing", "生物破坏方块", "生成", "苦力怕、末影人等能否破坏或搬动方块。"),
    Entry("naturalRegeneration", "自然回血", "玩家", "饥饿值足够时是否自动恢复生命。"),
    Entry("doFireTick", "火焰蔓延", "世界", "火是否蔓延和烧毁方块。"),
    Entry("doInsomnia", "幻翼生成", "生成", "连续多天不睡觉是否生成幻翼。"),
    Entry("commandBlockOutput", "命令方块输出", "命令", "命令方块执行结果是否刷屏。"),
    Entry("sendCommandFeedback", "命令反馈", "命令", "玩家执行命令时是否显示反馈。"),
    Entry("logAdminCommands", "记录管理员命令", "命令", "是否把管理员命令写进日志。"),
    Entry("showDeathMessages", "死亡消息", "聊天", "是否在聊天栏广播死亡消息。"),
    Entry("announceAdvancements", "进度广播", "聊天", "是否广播玩家完成进度。"),
    Entry("doImmediateRespawn", "立即重生", "玩家", "死亡后是否跳过重生界面。"),
    Entry("disableElytraMovementCheck", "关闭鞘翅检测", "玩家", "是否关闭鞘翅飞行速度检测。"),
    Entry("spectatorsGenerateChunks", "旁观者加载区块", "世界", "旁观者周围是否生成区块。"),
    Entry("randomTickSpeed", "随机刻速度", "世界", "每区块每刻的随机刻数量，默认 3。"),
    Entry("spawnRadius", "出生点半径", "世界", "新玩家出生点随机范围。"),
    Entry("maxEntityCramming", "实体挤压上限", "世界", "同一格实体超过多少只开始受伤。"),
    Entry("playersSleepingPercentage", "睡觉比例", "玩家", "多少比例的玩家睡觉才能跳过夜晚。"),
    Entry("doTraderSpawning", "流浪商人", "生成", "是否生成流浪商人。"),
    Entry("doPatrolSpawning", "袭击巡逻队", "生成", "是否生成掠夺者巡逻队。"),
    Entry("doWardenSpawning", "监守者生成", "生成", "是否允许监守者生成。"),
    Entry("universalAnger", "通用愤怒", "生成", "被激怒的猪灵是否攻击所有玩家。"),
    Entry("forgiveDeadPlayers", "死亡后原谅", "生成", "目标玩家死亡后僵尸猪灵是否消气。"),
)

GAMERULE_CATEGORIES: tuple[str, ...] = (
    "全部",
    "玩家",
    "世界",
    "生成",
    "时间",
    "天气",
    "命令",
    "聊天",
)

DAMAGE_TYPES: tuple[Entry, ...] = (
    Entry("minecraft:in_fire", "着火"),
    Entry("minecraft:lightning_bolt", "闪电"),
    Entry("minecraft:on_fire", "身上着火"),
    Entry("minecraft:lava", "熔岩"),
    Entry("minecraft:hot_floor", "高温方块"),
    Entry("minecraft:in_wall", "卡墙"),
    Entry("minecraft:cramming", "实体挤压"),
    Entry("minecraft:drown", "溺水"),
    Entry("minecraft:starve", "饥饿"),
    Entry("minecraft:cactus", "仙人掌"),
    Entry("minecraft:fall", "摔落"),
    Entry("minecraft:fly_into_wall", "撞墙"),
    Entry("minecraft:out_of_world", "虚空"),
    Entry("minecraft:generic", "通用"),
    Entry("minecraft:magic", "魔法"),
    Entry("minecraft:wither", "凋零"),
    Entry("minecraft:dragon_breath", "龙息"),
    Entry("minecraft:dry_out", "干燥"),
    Entry("minecraft:sweet_berry_bush", "甜浆果丛"),
    Entry("minecraft:freeze", "冰冻"),
    Entry("minecraft:stalagmite", "石笋"),
    Entry("minecraft:falling_block", "下落的方块"),
    Entry("minecraft:falling_anvil", "下落的铁砧"),
    Entry("minecraft:sting", "蜂刺"),
    Entry("minecraft:mob_attack", "生物攻击"),
    Entry("minecraft:player_attack", "玩家攻击"),
    Entry("minecraft:arrow", "箭"),
    Entry("minecraft:trident", "三叉戟"),
    Entry("minecraft:mob_projectile", "生物弹射物"),
    Entry("minecraft:fireworks", "烟花"),
    Entry("minecraft:fireball", "火球"),
    Entry("minecraft:wither_skull", "凋灵之首"),
    Entry("minecraft:thorns", "荆棘"),
    Entry("minecraft:explosion", "爆炸"),
    Entry("minecraft:player_explosion", "玩家爆炸"),
    Entry("minecraft:sonic_boom", "音爆"),
    Entry("minecraft:campfire", "营火"),
    Entry("minecraft:mace_smash", "重锤猛击"),
)

STRUCTURES: tuple[Entry, ...] = (
    Entry("minecraft:stronghold", "要塞"),
    Entry("minecraft:village", "村庄"),
    Entry("minecraft:pillager_outpost", "掠夺者前哨站"),
    Entry("minecraft:mansion", "林地府邸"),
    Entry("minecraft:monument", "海底神殿"),
    Entry("minecraft:temple", "神庙"),
    Entry("minecraft:desert_pyramid", "沙漠神殿"),
    Entry("minecraft:jungle_temple", "丛林神庙"),
    Entry("minecraft:witch_hut", "女巫小屋"),
    Entry("minecraft:igloo", "雪屋"),
    Entry("minecraft:ancient_city", "远古城市"),
    Entry("minecraft:trial_chambers", "试炼密室"),
    Entry("minecraft:bastion_remnant", "堡垒遗迹"),
    Entry("minecraft:fortress", "下界要塞"),
    Entry("minecraft:end_city", "末地城"),
    Entry("minecraft:shipwreck", "沉船"),
    Entry("minecraft:buried_treasure", "埋藏的宝藏"),
    Entry("minecraft:mineshaft", "废弃矿井"),
    Entry("minecraft:ocean_ruin", "海底废墟"),
    Entry("minecraft:ruined_portal", "废弃传送门"),
    Entry("minecraft:nether_fossil", "下界化石"),
    Entry("minecraft:ocean_monument", "海底神殿（旧 ID）"),
)

CRITERIA: tuple[Entry, ...] = (
    Entry("dummy", "虚拟", "只由命令修改，最常用。"),
    Entry("deathCount", "死亡次数", "玩家每死亡一次加一。"),
    Entry("playerKillCount", "击杀玩家数", "击杀其他玩家时加一。"),
    Entry("totalKillCount", "总击杀数", "击杀任意生物时加一。"),
    Entry("health", "生命值", "同步玩家当前生命值。"),
    Entry("food", "饥饿值", "同步玩家当前饥饿值。"),
    Entry("air", "氧气值", "同步玩家当前氧气值。"),
    Entry("armor", "护甲值", "同步玩家当前护甲值。"),
    Entry("level", "经验等级", "同步玩家当前等级。"),
    Entry("xp", "经验点数", "同步玩家当前经验点数。"),
    Entry("trigger", "触发器", "由 /trigger 命令修改。"),
    Entry("teamkill.red", "击杀红队", "击杀红队成员时加一。"),
    Entry("killedByTeam.red", "被红队击杀", "被红队成员击杀时加一。"),
)

MODES: tuple[Option, ...] = (
    Option("replace", "replace — 替换", "只替换目标位置原有的方块。"),
    Option("destroy", "destroy — 破坏后放置", "先破坏原有方块再放置，会产生掉落物。"),
    Option("keep", "keep — 只填空位", "只替换空气等可替换方块。"),
    Option("hollow", "hollow — 空心", "只填充外壳，内部挖空。"),
    Option("outline", "outline — 只描边", "只填充外层轮廓。"),
)

TIME_VALUES: tuple[Option, ...] = (
    Option("day", "day — 白天", "约 1000 刻，太阳升起后。"),
    Option("noon", "noon — 正午", "约 6000 刻，太阳最高。"),
    Option("night", "night — 夜晚", "约 13000 刻，太阳落山。"),
    Option("midnight", "midnight — 午夜", "约 18000 刻，月亮最高。"),
    Option("0", "0 — 清晨", "一天的起点。"),
    Option("1000", "1000 — 上午", "太阳刚升起。"),
    Option("6000", "6000 — 正午", "与 noon 相同。"),
    Option("12000", "12000 — 黄昏", "太阳开始落山。"),
    Option("18000", "18000 — 午夜", "与 midnight 相同。"),
)

WEATHER_VALUES: tuple[Option, ...] = (
    Option("clear", "clear — 晴天", "停止下雨或雷雨。"),
    Option("rain", "rain — 下雨", "开始下雨（寒冷群系下雪）。"),
    Option("thunder", "thunder — 雷雨", "下雨并伴随闪电。"),
)

POSITIONS: tuple[Option, ...] = (
    Option("title", "title — 主标题", "屏幕中央的大字。"),
    Option("subtitle", "subtitle — 副标题", "主标题下方的小字。"),
    Option("actionbar", "actionbar — 动作栏", "物品栏上方的提示文字。"),
)

UNITS: tuple[Option, ...] = (
    Option("levels", "levels — 等级", "按经验等级增加或减少。"),
    Option("points", "points — 点数", "按经验点数增加或减少。"),
)

LOCATE_TYPES: tuple[Option, ...] = (
    Option("structure", "structure — 结构", "按结构 ID 查找，例如要塞、村庄。"),
    Option("biome", "biome — 群系", "按群系 ID 查找，例如平原、沙漠。"),
    Option("poi", "poi — 兴趣点", "按兴趣点查找，例如讲台、蜂巢。"),
)

SOUND_SOURCES: tuple[Option, ...] = (
    Option("master", "master — 主音量", "受主音量控制。"),
    Option("player", "player — 玩家", "受玩家音量控制。"),
    Option("block", "block — 方块", "受方块音量控制。"),
    Option("ambient", "ambient — 环境", "受环境音量控制。"),
    Option("hostile", "hostile — 敌对生物", "受敌对生物音量控制。"),
    Option("neutral", "neutral — 中立生物", "受中立生物音量控制。"),
    Option("record", "record — 唱片机", "受唱片机音量控制。"),
    Option("weather", "weather — 天气", "受天气音量控制。"),
)


# ---------------------------------------------------------------------------
# 物品组件（1.20.5+）

COMPONENTS: tuple[Entry, ...] = (
    Entry(
        "custom_name='{\"text\":\"喵剑\",\"color\":\"gold\",\"italic\":false}'",
        "自定义名称",
        "1.20.5+",
        "改物品显示名，支持 JSON 文本和颜色。",
    ),
    Entry(
        "lore=['{\"text\":\"第一行说明\",\"color\":\"gray\",\"italic\":false}']",
        "物品描述",
        "1.20.5+",
        "在名称下方添加多行说明文字。",
    ),
    Entry("unbreakable={}", "无法损坏", "1.20.5+", "物品不会消耗耐久。"),
    Entry(
        "enchantments={levels:{\"minecraft:sharpness\":5}}",
        "附魔",
        "1.21+",
        "用组件直接附魔，不受铁砧等级限制。",
    ),
    Entry("max_damage=1000", "最大耐久", "1.20.5+", "修改物品的最大耐久值。"),
    Entry("max_stack_size=64", "最大堆叠", "1.20.5+", "修改物品的最大堆叠数量。"),
    Entry('rarity="epic"', "稀有度", "1.20.5+", "common / uncommon / rare / epic。"),
    Entry("dyed_color=16711680", "染色", "1.20.5+", "给皮革防具染色，值是十进制 RGB。"),
    Entry(
        'potion_contents="minecraft:strength"',
        "药水内容",
        "1.20.5+",
        "给药水设置效果，例如力量、速度。",
    ),
    Entry(
        'item_model="minecraft:diamond_sword"',
        "物品模型",
        "1.21.4+",
        "换用另一个物品的模型。",
    ),
    Entry("custom_data={dkl:1}", "自定义数据", "1.20.5+", "写入自定义 NBT，供数据包读取。"),
    Entry("enchantment_glint_override=true", "强制附魔光效", "1.20.5+", "没有附魔也显示光效。"),
)


# ---------------------------------------------------------------------------
# 查询

DATASETS: dict[str, tuple[Entry, ...]] = {
    "item": ITEMS,
    "block": BLOCKS,
    "entity": ENTITIES,
    "effect": EFFECTS,
    "enchantment": ENCHANTMENTS,
    "particle": PARTICLES,
    "sound": SOUNDS,
    "gamerule": GAMERULES,
    "damage": DAMAGE_TYPES,
    "structure": STRUCTURES,
    "criteria": CRITERIA,
}

CATEGORIES: dict[str, tuple[str, ...]] = {
    "item": ITEM_CATEGORIES,
    "block": BLOCK_CATEGORIES,
    "entity": ENTITY_CATEGORIES,
    "enchantment": ENCHANTMENT_CATEGORIES,
    "gamerule": GAMERULE_CATEGORIES,
}

PICKER_TITLES: dict[str, str] = {
    "item": "选择物品 ID",
    "block": "选择方块 ID",
    "entity": "选择实体 ID",
    "effect": "选择状态效果",
    "enchantment": "选择附魔",
    "particle": "选择粒子",
    "sound": "选择音效",
    "gamerule": "选择游戏规则",
    "damage": "选择伤害类型",
    "structure": "选择结构",
    "criteria": "选择计分板准则",
}


def dataset(kind: str) -> tuple[Entry, ...]:
    return DATASETS.get(str(kind or ""), ())


def categories(kind: str) -> tuple[str, ...]:
    return CATEGORIES.get(str(kind or ""), ("全部",))


def picker_title(kind: str) -> str:
    return PICKER_TITLES.get(str(kind or ""), "选择 ID")


def search(kind: str, query: str = "", category: str = "全部") -> list[Entry]:
    """按中文名、英文 ID 或说明搜索；空关键词返回全部。"""
    needle = " ".join(str(query or "").split()).lower()
    wanted = str(category or "全部")
    found: list[Entry] = []
    for entry in dataset(kind):
        if wanted and wanted != "全部" and entry.category != wanted:
            continue
        if needle:
            haystack = " ".join(
                (entry.value, entry.name, entry.category, entry.help)
            ).lower()
            if needle not in haystack:
                continue
        found.append(entry)
    return found


def entries_for_options(options: tuple[Option, ...]) -> tuple[Entry, ...]:
    return tuple(Entry(item.value, item.label, "", item.help) for item in options)