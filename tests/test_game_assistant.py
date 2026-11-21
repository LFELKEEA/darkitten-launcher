"""游戏助手核心算法的单元测试。

这里的随机数、史莱姆区块、结构候选点都有对应的 Java 实现对照结果，
不是用 Python 实现自己验证自己。

运行：python tests/test_game_assistant.py
"""

from __future__ import annotations

import io
import json
import sys
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from darkitten import game_assistant as ga  # noqa: E402


class JavaRandomTests(unittest.TestCase):
    def test_next_int_sequence_matches_java(self):
        # java.util.Random(0).nextInt(100) x5
        random = ga.JavaRandom(0)
        self.assertEqual([random.next_int(100) for _ in range(5)], [60, 48, 29, 47, 15])

    def test_next_int_sequence_negative_seed(self):
        # java.util.Random(-1).nextInt(16) x5
        random = ga.JavaRandom(-1)
        self.assertEqual([random.next_int(16) for _ in range(5)], [4, 7, 0, 8, 10])

    def test_power_of_two_bound_uses_shift(self):
        random = ga.JavaRandom(42)
        self.assertEqual([random.next_int(8) for _ in range(4)], [5, 0, 5, 0])

    def test_string_hash_matches_java(self):
        # "hello".hashCode() == 99162322, "".hashCode() == 0
        self.assertEqual(ga.java_string_hash("hello"), 99162322)
        self.assertEqual(ga.java_string_hash(""), 0)
        self.assertEqual(ga.java_string_hash("你好"), 652829)

    def test_string_hash_is_32bit_signed(self):
        value = ga.java_string_hash("a" * 100)
        self.assertLess(value, 1 << 31)
        self.assertGreaterEqual(value, -(1 << 31))


class SeedTests(unittest.TestCase):
    def test_decimal(self):
        info = ga.parse_seed("  -12345  ")
        self.assertEqual((info.value, info.kind), (-12345, "number"))

    def test_underscores(self):
        self.assertEqual(ga.parse_seed("1_000").value, 1000)

    def test_hex(self):
        info = ga.parse_seed("0xFF")
        self.assertEqual((info.value, info.kind), (255, "hex"))

    def test_text_falls_back_to_hash(self):
        info = ga.parse_seed("hello")
        self.assertEqual((info.value, info.kind, info.hash32), (99162322, "text", 99162322))

    def test_empty_rejected(self):
        with self.assertRaises(ValueError):
            ga.parse_seed("   ")

    def test_too_long_rejected(self):
        with self.assertRaises(ValueError):
            ga.parse_seed("x" * 300)

    def test_candidates_include_both_interpretations(self):
        self.assertEqual(ga.seed_candidates("hello"), [99162322])
        values = ga.seed_candidates("123")
        self.assertEqual(values[0], 123)
        self.assertIn(ga.java_string_hash("123"), values)

    def test_fingerprint_is_stable(self):
        self.assertEqual(ga.seed_fingerprint(0), "5feceb66ffc86f38")
        self.assertEqual(ga.seed_fingerprint(0), ga.seed_fingerprint(0))


class CoordinateTests(unittest.TestCase):
    def test_block_to_chunk(self):
        cases = {0: 0, 15: 0, 16: 1, -1: -1, -16: -1, -17: -2}
        for block, chunk in cases.items():
            self.assertEqual(ga.block_to_chunk(block), chunk, block)

    def test_chunk_roundtrip(self):
        for chunk in (-3, 0, 7):
            self.assertEqual(ga.block_to_chunk(ga.chunk_to_block(chunk)), chunk)

    def test_chunk_center(self):
        self.assertEqual(ga.chunk_center(0), 8)
        self.assertEqual(ga.chunk_center(3), 56)

    def test_nether_conversion(self):
        # 与游戏一致：坐标除以 8 后向下取整。
        self.assertEqual(ga.overworld_to_nether(100, 200), (12, 25))
        self.assertEqual(ga.nether_to_overworld(12, 25), (96, 200))
        self.assertEqual(ga.overworld_to_nether(-100, -200), (-13, -25))

    def test_chunk_summary_shape(self):
        summary = ga.chunk_summary(0, 0, 0)
        self.assertEqual(summary["chunk"], (0, 0))
        self.assertEqual(summary["block_center"], (8, 8))
        self.assertFalse(summary["slime"])

    def test_dimension_info(self):
        self.assertEqual(len(ga.dimension_info()), 3)


class SlimeChunkTests(unittest.TestCase):
    """期望值由 Java 版同一公式直接运行得出。"""

    def test_known_slime_chunks(self):
        self.assertTrue(ga.is_slime_chunk(0, 2, 2))
        self.assertTrue(ga.is_slime_chunk(0, 8, -2))
        self.assertTrue(ga.is_slime_chunk(-1, 1, 2))
        self.assertTrue(ga.is_slime_chunk(123456789, 8, 2))

    def test_known_non_slime_chunks(self):
        self.assertFalse(ga.is_slime_chunk(0, 0, 0))
        self.assertFalse(ga.is_slime_chunk(0, 1, 2))
        self.assertFalse(ga.is_slime_chunk(1, 0, 0))
        self.assertFalse(ga.is_slime_chunk(123456789, 0, 0))

    def test_find_slime_chunks_centered(self):
        found = ga.find_slime_chunks(0, 2, 2, radius=0)
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0].chunk_x, found[0].chunk_z), (2, 2))
        # block_x/block_z 是该区块的中心方块坐标，用于游戏内传送。
        self.assertEqual(found[0].block_x, 40)
        self.assertEqual(found[0].block_z, 40)

    def test_find_slime_chunks_respects_limit(self):
        found = ga.find_slime_chunks(0, 0, 0, radius=12, limit=5)
        self.assertLessEqual(len(found), 5)


class StructureTests(unittest.TestCase):
    """期望值由 Java 版区域随机数算法直接运行得出。"""

    def test_village_region_origin(self):
        candidates = ga.structure_candidates(0, "village", region_radius=1, limit=100)
        origin = [
            item for item in candidates
            if item.region_x == 0 and item.region_z == 0
        ]
        self.assertEqual(len(origin), 1)
        self.assertEqual((origin[0].chunk_x, origin[0].chunk_z), (15, 2))

    def test_village_negative_region(self):
        candidates = ga.structure_candidates(0, "village", region_radius=1, limit=100)
        origin = [
            item for item in candidates
            if item.region_x == 0 and item.region_z == -1
        ]
        self.assertEqual((origin[0].chunk_x, origin[0].chunk_z), (19, -15))

    def test_mansion_triangular_spread(self):
        candidates = ga.structure_candidates(-1, "woodland_mansion", region_radius=1, limit=100)
        origin = [
            item for item in candidates
            if item.region_x == 0 and item.region_z == 0
        ]
        self.assertEqual((origin[0].chunk_x, origin[0].chunk_z), (11, 19))

    def test_unknown_structure_rejected(self):
        with self.assertRaises(ValueError):
            ga.structure_candidates(0, "not_a_structure")

    def test_structures_for_dimension(self):
        overworld = ga.structures_for_dimension("overworld")
        self.assertTrue(overworld)
        self.assertTrue(all(item.dimension == "overworld" for item in overworld))
        self.assertTrue(any(item.id == "village" for item in overworld))

    def test_candidates_sorted_by_distance(self):
        candidates = ga.structure_candidates(0, "village", region_radius=2, limit=10)
        distances = [item.distance for item in candidates]
        self.assertEqual(distances, sorted(distances))


class LinkTests(unittest.TestCase):
    def test_chunkbase_url(self):
        url = ga.chunkbase_url(12345)
        self.assertIn("seed=12345", url)
        self.assertIn("chunkbase.com", url)
        self.assertIn("java_1_21_9", url)

    def test_chunkbase_dimensions_use_site_values(self):
        # Chunkbase 的维度值是 overworld / nether / end。
        for label, value in ga.CHUNKBASE_DIMENSIONS:
            self.assertIn(value, {"overworld", "nether", "end"}, label)
            self.assertIn(f"dimension={value}", ga.chunkbase_url(1, "java_1_16", value))

    def test_chunkbase_accepts_every_listed_platform(self):
        for label, platform in ga.CHUNKBASE_VERSIONS:
            url = ga.chunkbase_url(1, platform)
            self.assertIn(f"platform={platform}", url, label)

    def test_platform_list_is_complete_and_unique(self):
        platforms = [platform for _label, platform in ga.CHUNKBASE_VERSIONS]
        self.assertEqual(len(platforms), len(set(platforms)))
        self.assertGreaterEqual(len(platforms), 60)
        for expected in (
            "java_26_3",
            "java_1_21_9",
            "java_1_21",
            "java_1_20",
            "java_1_16",
            "java_1_7",
            "java_1_21_9_lb",
            "bedrock_26_60",
            "bedrock_1_21_120",
            "bedrock_1_14",
        ):
            self.assertIn(expected, platforms)
        # 旧版本里写错、Chunkbase 并不存在的 ID 不应再出现。
        for invalid in ("java_1_20_6", "java_1_16_5", "java_1_12_2"):
            self.assertNotIn(invalid, platforms)

    def test_legacy_platform_alias(self):
        self.assertEqual(ga.normalize_chunkbase_platform("java_1_20_6"), "java_1_20")
        self.assertEqual(ga.normalize_chunkbase_platform("java_1_16_5"), "java_1_16")
        self.assertEqual(ga.normalize_chunkbase_platform("java_1_19_4"), "java_1_19_3")
        self.assertEqual(ga.normalize_chunkbase_platform("bedrock_1_21_120"), "bedrock_1_21_120")
        self.assertEqual(ga.normalize_chunkbase_platform("nonsense"), "java_1_21_9")

    def test_bedrock_platform_flag(self):
        self.assertTrue(ga.is_bedrock_platform("bedrock_1_20"))
        self.assertFalse(ga.is_bedrock_platform("java_1_20"))

    def test_seedmap_version_mapping(self):
        self.assertEqual(ga.minecraftseedmap_version("java_1_21_4"), "1.21")
        self.assertEqual(ga.minecraftseedmap_version("java_1_16"), "1.16")
        self.assertIsNone(ga.minecraftseedmap_version("java_1_7"))
        self.assertIsNone(ga.minecraftseedmap_version("java_1_16_lb"))
        self.assertIsNone(ga.minecraftseedmap_version("bedrock_1_20"))

    def test_minecraftseedmap_url(self):
        self.assertTrue(ga.minecraftseedmap_url(-1).endswith("/seed/-1"))
        self.assertTrue(ga.minecraftseedmap_url(-1, "java_1_16").endswith("/seed/-1/1.16"))
        # 不支持的版本退回不带版本号的页面。
        self.assertTrue(ga.minecraftseedmap_url(-1, "java_1_7").endswith("/seed/-1"))

    def test_mcmod_search_url_escaped(self):
        url = ga.mcmod_search_url("钻石 剑")
        self.assertIn("search.mcmod.cn/s", url)
        self.assertIn("%E9%92%BB%E7%9F%B3", url)


class CommandTests(unittest.TestCase):
    def test_search_by_name(self):
        names = [item.name for item in ga.search_commands("give")]
        self.assertIn("give", names)

    def test_search_by_description(self):
        self.assertTrue(ga.search_commands("附魔"))

    def test_empty_query_returns_all(self):
        self.assertTrue(len(ga.search_commands("")) > 10)

    def test_categories(self):
        self.assertEqual(ga.command_categories()[0], "全部")
        self.assertIn("物品", ga.command_categories())

    def test_valid_command(self):
        issues = ga.validate_command("/say hi")
        self.assertEqual([item.level for item in issues], ["info"])

    def test_empty_command(self):
        issues = ga.validate_command("   ")
        self.assertEqual(issues[0].level, "error")

    def test_newline_rejected(self):
        issues = ga.validate_command("say hi\ntp @s 0 0 0")
        self.assertTrue(any("换行" in item.message for item in issues))

    def test_execute_requires_run(self):
        issues = ga.validate_command("execute as @s at @s")
        self.assertTrue(any(item.level == "error" and "run" in item.message for item in issues))

    def test_execute_with_run_ok(self):
        issues = ga.validate_command("execute as @s at @s run say hi")
        self.assertFalse(any(item.level == "error" for item in issues))

    def test_fill_volume_limit(self):
        issues = ga.validate_command("fill 0 0 0 100 100 100 stone")
        self.assertTrue(any(item.level == "error" and "32768" in item.message for item in issues))

    def test_fill_small_volume_ok(self):
        issues = ga.validate_command("fill 0 0 0 4 4 4 stone")
        self.assertFalse(any(item.level == "error" for item in issues))

    def test_unbalanced_brackets(self):
        issues = ga.validate_command('tellraw @a {"text":"hi"')
        self.assertTrue(any(item.level == "error" for item in issues))

    def test_broad_selector_warning(self):
        issues = ga.validate_command("kill @e")
        self.assertTrue(any(item.level == "warn" for item in issues))

    def test_filtered_selector_no_warning(self):
        issues = ga.validate_command("kill @e[type=minecraft:zombie]")
        self.assertFalse(any("@e" in item.message for item in issues))

    def test_long_command_warning(self):
        issues = ga.validate_command("say " + "a" * 300)
        self.assertTrue(any(item.level == "warn" for item in issues))

    def test_unknown_root_warning(self):
        issues = ga.validate_command("mycustommod dosomething")
        self.assertTrue(any(item.level == "warn" for item in issues))


class TemplateTests(unittest.TestCase):
    def test_give_defaults(self):
        command = ga.build_from_template("give", {"target": "@s"})
        self.assertEqual(command, "/give @s minecraft:diamond_sword 1")

    def test_tp(self):
        command = ga.build_from_template("tp", {"target": "@s", "x": "0", "y": "64", "z": "0"})
        self.assertEqual(command, "/tp @s 0 64 0")

    def test_title_wraps_json(self):
        command = ga.build_from_template("title", {"target": "@a", "text": "hello"})
        self.assertIn('{"text": "hello"}', command)

    def test_default_values_fill_every_field(self):
        for template in ga.COMMAND_TEMPLATES:
            values = ga.default_template_values(template)
            self.assertEqual(set(values), {field.key for field in template.fields})
            command = ga.build_from_template(template.id, values)
            self.assertTrue(command.startswith("/"), template.id)
            self.assertGreater(len(command), 2, template.id)

    def test_unknown_template_rejected(self):
        with self.assertRaises(ValueError):
            ga.build_from_template("nope", {})


class ChainBlockTests(unittest.TestCase):
    def test_block_state_and_nbt(self):
        lines = ga.build_chain_blocks(["/say hi", "tp @s 0 0 0"], conditional=True)
        self.assertEqual(
            lines[0],
            '/setblock 0 64 0 minecraft:repeating_command_block'
            '[facing=east,conditional=false]{Command:"say hi",auto:1b} replace',
        )
        self.assertEqual(
            lines[1],
            '/setblock 1 64 0 minecraft:chain_command_block'
            '[facing=east,conditional=true]{Command:"tp @s 0 0 0",auto:1b} replace',
        )

    def test_first_impulse(self):
        lines = ga.build_chain_blocks(["say hi"], first="impulse")
        self.assertIn("minecraft:command_block", lines[0])

    def test_auto_off(self):
        lines = ga.build_chain_blocks(["say hi"], auto=False)
        self.assertIn("auto:0b", lines[0])

    def test_facing_changes_offsets(self):
        lines = ga.build_chain_blocks(["say hi", "say bye"], facing="south")
        self.assertTrue(lines[0].startswith("/setblock 0 64 0 "))
        self.assertTrue(lines[1].startswith("/setblock 0 64 1 "))

    def test_quotes_escaped_in_nbt(self):
        line = ga.build_chain_blocks(['tellraw @a {"text":"hi"}'])[0]
        self.assertIn(r'Command:"tellraw @a {\"text\":\"hi\"}"', line)

    def test_empty_rejected(self):
        with self.assertRaises(ValueError):
            ga.build_chain_blocks(["   "])

    def test_newline_rejected(self):
        with self.assertRaises(ValueError):
            ga.build_chain_blocks(["say hi\ntp @s 0 0 0"])


class FunctionTests(unittest.TestCase):
    def test_strips_leading_slash(self):
        text = ga.build_mcfunction(["/say hi", "# comment", "/tp @s 0 0 0"])
        self.assertIn("say hi\n", text)
        self.assertNotIn("/say hi", text)
        self.assertIn("# comment", text)
        self.assertIn("tp @s 0 0 0", text)

    def test_header_can_be_disabled(self):
        text = ga.build_mcfunction(["say hi"], header=False)
        self.assertEqual(text, "say hi\n")


class DatapackTests(unittest.TestCase):
    def _read(self, blob: bytes) -> dict[str, str]:
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            return {name: archive.read(name).decode("utf-8") for name in archive.namelist()}

    def test_folders_by_version(self):
        self.assertEqual(ga.datapack_folders("1.20.1"), ("functions", "functions"))
        self.assertEqual(ga.datapack_folders("1.21"), ("function", "function"))

    def test_pack_format_by_version(self):
        self.assertEqual(ga.pack_format_for("1.21.4"), 61)
        self.assertEqual(ga.pack_format_for("1.20.1"), 26)
        self.assertEqual(ga.pack_format_for("1.16.5"), 6)

    def test_modern_layout(self):
        blob = ga.build_datapack("hello", ["/say hi"], load=True, tick=True)
        files = self._read(blob)
        self.assertIn("pack.mcmeta", files)
        self.assertIn("data/dkl/function/hello.mcfunction", files)
        self.assertIn("data/minecraft/tags/function/load.json", files)
        self.assertIn("data/minecraft/tags/function/tick.json", files)
        self.assertIn("say hi", files["data/dkl/function/hello.mcfunction"])
        meta = json.loads(files["pack.mcmeta"])
        self.assertEqual(meta["pack"]["pack_format"], 61)

    def test_legacy_layout(self):
        blob = ga.build_datapack("hello", ["say hi"], version="1.20.1")
        files = self._read(blob)
        self.assertIn("data/dkl/functions/hello.mcfunction", files)

    def test_invalid_namespace(self):
        with self.assertRaises(ValueError):
            ga.build_datapack("hello", ["say hi"], namespace="Bad Name!")

    def test_empty_commands(self):
        with self.assertRaises(ValueError):
            ga.build_datapack("hello", ["  "])


class AIOutputTests(unittest.TestCase):
    def test_strips_fences_and_numbering(self):
        raw = "```mcfunction\n/say hi\n1. /tp @s 0 0 0\n说明文字不要\n```"
        self.assertEqual(ga.clean_ai_output(raw), "/say hi\n/tp @s 0 0 0")

    def test_keeps_comments(self):
        self.assertEqual(ga.clean_ai_output("# note\n/ say hi"), "# note\n/ say hi")

    def test_endpoint_building(self):
        self.assertEqual(
            ga.AIConfig(base_url="https://api.openai.com/v1").endpoint,
            "https://api.openai.com/v1/chat/completions",
        )
        self.assertEqual(
            ga.AIConfig(base_url="https://api.deepseek.com/v1/").endpoint,
            "https://api.deepseek.com/v1/chat/completions",
        )
        self.assertEqual(
            ga.AIConfig(base_url="https://x.test/chat/completions").endpoint,
            "https://x.test/chat/completions",
        )

    def test_messages_include_version_and_request(self):
        messages = ga.build_ai_messages("造个电梯", "1.21.4", "在平原")
        joined = json.dumps(messages, ensure_ascii=False)
        self.assertIn("造个电梯", joined)
        self.assertIn("1.21.4", joined)
        self.assertIn("在平原", joined)


class EnchantToolTests(unittest.TestCase):
    def test_tutorial_mentions_clientcommands(self):
        joined = " ".join(text for _, text in ga.ENCHANT_TUTORIAL)
        self.assertIn("clientcommands", joined)
        self.assertIn("/cenchant", joined)

    def test_jar_is_bundled(self):
        jar = ga.find_enchcracker()
        self.assertIsNotNone(jar)
        self.assertTrue(jar.exists())
        self.assertEqual(jar.name, "enchcracker-1.6.jar")

    def test_license_files_present(self):
        folder = ga.enchcracker_dir()
        self.assertTrue((folder / "LICENSE-APACHE-2.0.txt").exists())
        self.assertTrue((folder / "NOTICE.txt").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)