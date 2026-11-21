"""在线注册表转换与本地清单合并的单元测试（不联网）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from darkitten import game_catalog, game_ids  # noqa: E402


class ResolveVersionTests(unittest.TestCase):
    PATHS = {
        "pc": {
            "1.21.11": {"blocks": "pc/1.21.11"},
            "1.21.9": {"blocks": "pc/1.21.9"},
            "1.21.6": {"blocks": "pc/1.21.6"},
            "1.20.1": {"blocks": "pc/1.20"},
        }
    }

    def test_exact_version(self):
        self.assertEqual(
            game_catalog.resolve_data_version("1.21.11", self.PATHS), "1.21.11"
        )

    def test_loader_suffix_is_stripped(self):
        self.assertEqual(
            game_catalog.resolve_data_version("1.21.11-Forge", self.PATHS),
            "1.21.11",
        )

    def test_missing_version_falls_back_to_nearest_lower(self):
        self.assertEqual(
            game_catalog.resolve_data_version("1.21.7", self.PATHS), "1.21.6"
        )


class BuildEntriesTests(unittest.TestCase):
    REGISTRIES = {
        "blocks": [
            {"name": "stone", "displayName": "Stone", "material": "mineable/pickaxe"},
            {"name": "diamond_ore", "displayName": "Diamond Ore", "material": "default"},
        ],
        "items": [
            {"name": "stone", "displayName": "Stone", "stackSize": 64},
            {"name": "diamond_sword", "displayName": "Diamond Sword", "stackSize": 1},
            {"name": "air", "displayName": "Air", "stackSize": 64},
        ],
        "entities": [
            {"name": "zombie", "displayName": "Zombie", "category": "Hostile mobs"},
            {"name": "player", "displayName": "Player", "category": "UNKNOWN"},
        ],
        "effects": [
            {"name": "InstantHealth", "displayName": "Instant Health", "type": "good"}
        ],
        "enchantments": [
            {
                "name": "sharpness",
                "displayName": "Sharpness",
                "category": "weapon",
                "maxLevel": 5,
                "treasureOnly": False,
                "curse": False,
            }
        ],
        "particles": [{"name": "flame"}],
        "sounds": [{"name": "entity.zombie.ambient"}],
        "biomes": [{"name": "plains", "displayName": "Plains"}],
    }
    ZH = {
        "block.minecraft.stone": "石头",
        "block.minecraft.diamond_ore": "钻石矿石",
        "item.minecraft.stone": "石头",
        "item.minecraft.diamond_sword": "钻石剑",
        "entity.minecraft.zombie": "僵尸",
        "effect.minecraft.instant_health": "瞬间治疗",
        "enchantment.minecraft.sharpness": "锋利",
        "subtitles.entity.zombie.ambient": "僵尸：低吼",
        "biome.minecraft.plains": "平原",
    }

    def setUp(self):
        self.entries = game_catalog.build_entries(self.REGISTRIES, self.ZH)

    def test_chinese_names_and_ids(self):
        blocks = {item.value: item for item in self.entries["block"]}
        self.assertEqual(blocks["minecraft:stone"].name, "石头")
        self.assertEqual(blocks["minecraft:diamond_ore"].category, "矿石")
        items = {item.value: item for item in self.entries["item"]}
        self.assertNotIn("minecraft:air", items)
        self.assertEqual(items["minecraft:diamond_sword"].name, "钻石剑")
        self.assertEqual(items["minecraft:diamond_sword"].category, "工具与武器")

    def test_camel_case_effect_becomes_snake_case(self):
        effects = {item.value: item for item in self.entries["effect"]}
        self.assertIn("minecraft:instant_health", effects)
        self.assertEqual(effects["minecraft:instant_health"].name, "瞬间治疗")

    def test_entity_player_is_skipped(self):
        values = {item.value for item in self.entries["entity"]}
        self.assertIn("minecraft:zombie", values)
        self.assertNotIn("minecraft:player", values)

    def test_enchantment_help_and_category(self):
        entries = {item.value: item for item in self.entries["enchantment"]}
        entry = entries["minecraft:sharpness"]
        self.assertEqual(entry.name, "锋利")
        self.assertEqual(entry.category, "武器")
        self.assertIn("最高 5 级", entry.help)

    def test_sound_uses_subtitle_and_biome_is_under_structure(self):
        sounds = {item.value: item for item in self.entries["sound"]}
        self.assertEqual(sounds["minecraft:entity.zombie.ambient"].name, "僵尸：低吼")
        structures = {item.value: item for item in self.entries["structure"]}
        self.assertEqual(structures["minecraft:plains"].category, "群系")


class DynamicCatalogMergeTests(unittest.TestCase):
    def tearDown(self):
        game_ids.clear_catalog()

    def test_dynamic_entries_keep_curated_name_and_help(self):
        dynamic = {
            "block": (
                game_ids.Entry("minecraft:stone", "Stone", "镐类方块", ""),
                game_ids.Entry("minecraft:new_block", "New Block", "矿石", ""),
            )
        }
        game_ids.set_catalog(dynamic, "1.21.11")
        merged = {item.value: item for item in game_ids.dataset("block")}
        self.assertEqual(merged["minecraft:stone"].name, "石头")
        self.assertIn("minecraft:new_block", merged)
        self.assertIn("镐类方块", game_ids.categories("block"))
        self.assertEqual(game_ids.catalog_version(), "1.21.11")
        self.assertTrue(game_ids.catalog_loaded("block"))
        self.assertFalse(game_ids.catalog_loaded("item"))

    def test_dynamic_entries_replace_stale_builtin_ids(self):
        game_ids.set_catalog(
            {"block": (game_ids.Entry("minecraft:new_block", "New Block"),)}
        )
        values = {item.value for item in game_ids.dataset("block")}
        self.assertEqual(values, {"minecraft:new_block"})

    def test_structure_keeps_builtin_structures_with_biomes(self):
        game_ids.set_catalog(
            {"structure": (game_ids.Entry("minecraft:plains", "平原", "群系"),)}
        )
        values = {item.value for item in game_ids.dataset("structure")}
        self.assertIn("minecraft:plains", values)
        self.assertIn("minecraft:stronghold", values)

    def test_clear_returns_to_builtin(self):
        before = len(game_ids.dataset("block"))
        game_ids.set_catalog({"block": (game_ids.Entry("minecraft:x", "X"),)})
        game_ids.clear_catalog()
        self.assertEqual(len(game_ids.dataset("block")), before)


if __name__ == "__main__":
    unittest.main()
