"""NBT、存档与服务器列表的单元测试。

运行：pyruntime/python.exe tests/test_worlds.py
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from darkitten import chunkbase_registry as registry  # noqa: E402
from darkitten import game_assistant as ga  # noqa: E402
from darkitten import nbt, worlds  # noqa: E402


class NBTTests(unittest.TestCase):
    def test_roundtrip_all_tags(self):
        root = {
            "Data": {
                "byte": nbt.Byte(-3),
                "short": nbt.Short(300),
                "int": nbt.Int(70000),
                "long": nbt.Long(1 << 40),
                "float": nbt.Float(1.5),
                "double": nbt.Double(3.25),
                "text": nbt.NBTString("你好，猫猫🐱"),
                "list": nbt.NBTList([nbt.Int(1), nbt.Int(2)], element_type=nbt.TAG_INT),
                "empty": nbt.NBTList([], element_type=nbt.TAG_STRING),
                "nested": {"flag": nbt.Byte(1)},
                "bytes": nbt.ByteArray([-1, 2, 127]),
                "ints": nbt.IntArray([1, -2, 70000]),
                "longs": nbt.LongArray([1, -(1 << 45)]),
            }
        }
        encoded = nbt.write_nbt(root)
        decoded = nbt.parse_nbt(encoded)
        self.assertEqual(nbt.write_nbt(decoded), encoded)
        self.assertEqual(str(decoded["Data"]["text"]), "你好，猫猫🐱")
        self.assertIsInstance(decoded["Data"]["byte"], nbt.Byte)
        self.assertIsInstance(decoded["Data"]["long"], nbt.Long)
        self.assertEqual(list(decoded["Data"]["nested"].items())[0][0], "flag")
        self.assertEqual(decoded["Data"]["empty"].element_type, nbt.TAG_STRING)

    def test_gzip_file_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "level.dat"
            nbt.write_nbt_file(path, {"Data": {"name": nbt.NBTString("存档")}}, compressed=True)
            self.assertEqual(path.read_bytes()[:2], b"\x1f\x8b")
            loaded = nbt.read_nbt(path)
            self.assertEqual(str(loaded["Data"]["name"]), "存档")

    def test_truncated_data_rejected(self):
        with self.assertRaises(nbt.NBTError):
            nbt.parse_nbt(b"\x0a\x00\x00\x03")


class WorldTests(unittest.TestCase):
    def test_create_read_and_edit_legacy_world(self):
        with tempfile.TemporaryDirectory() as tmp:
            info = worlds.create_world(
                tmp,
                name="猫猫世界",
                seed=123456789,
                game_mode=1,
                difficulty=3,
                cheats=True,
                version_id="1.20.1",
            )
            self.assertTrue((info.folder / "level.dat").is_file())
            self.assertEqual(info.name, "猫猫世界")
            self.assertEqual(info.seed, 123456789)
            self.assertEqual(info.seed_text, "123456789")
            self.assertEqual(info.game_mode, 1)
            self.assertEqual(info.difficulty, 3)
            self.assertTrue(info.cheats)
            self.assertFalse(info.modern)

            items = worlds.scan_worlds(tmp)
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].folder, info.folder)
            self.assertEqual(items[0].seed_text, "123456789")

            settings = worlds.load_world_settings(info.folder)
            settings.name = "改名后的世界"
            settings.seed = 42
            settings.game_mode = 0
            settings.difficulty = 0
            settings.cheats = False
            settings.day_time = 18000
            settings.raining = True
            settings.rules["keepInventory"] = True
            settings.rules["randomTickSpeed"] = 0
            settings.rules["spawnRadius"] = 32
            updated = worlds.apply_world_settings(info.folder, settings)
            self.assertEqual(updated.name, "改名后的世界")
            self.assertEqual(updated.seed, 42)
            self.assertEqual(updated.game_mode, 0)
            self.assertEqual(updated.difficulty, 0)
            self.assertFalse(updated.cheats)
            self.assertTrue(updated.rules["keepInventory"])
            self.assertEqual(updated.rules["randomTickSpeed"], 0)
            self.assertEqual(updated.rules["spawnRadius"], 32)
            self.assertTrue((info.folder / "level.dat_old").is_file())

    @unittest.skipUnless(
        (ROOT / "assets" / "world_template_modern.dat").is_file(),
        "没有现代版世界模板",
    )
    def test_create_read_and_edit_modern_world(self):
        with tempfile.TemporaryDirectory() as tmp:
            info = worlds.create_world(
                tmp,
                name="现代世界",
                seed=-5236563929054714751,
                game_mode=0,
                difficulty=2,
                cheats=False,
                version_id="1.21.11",
            )
            self.assertTrue(info.modern)
            self.assertEqual(info.seed, -5236563929054714751)
            settings = worlds.load_world_settings(info.folder)
            settings.rules["keepInventory"] = True
            settings.difficulty = 1
            updated = worlds.apply_world_settings(info.folder, settings)
            self.assertTrue(updated.rules["keepInventory"])
            self.assertEqual(updated.difficulty, 1)
            data = nbt.read_nbt(info.folder / "level.dat")["Data"]
            self.assertIn("game_rules", data)
            self.assertEqual(int(data["game_rules"]["minecraft:keep_inventory"]), 1)

    def test_create_collision_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = worlds.create_world(tmp, name="同名", seed=1, version_id="1.20.1")
            second = worlds.create_world(tmp, name="同名", seed=2, version_id="1.20.1")
            self.assertNotEqual(first.folder, second.folder)
            self.assertTrue(second.folder.name.startswith("同名"))

    def test_clean_world_name(self):
        self.assertEqual(worlds.clean_world_name(' a/b:c*? '), "a-b-c--")
        self.assertEqual(worlds.clean_world_name(""), "新的世界")

    def test_delete_rejects_non_save_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(worlds.WorldError):
                worlds.delete_world(Path(tmp))


class ServerTests(unittest.TestCase):
    def test_add_edit_delete_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            entry = worlds.ServerEntry(name="猫猫服", address="mc.example.com")
            worlds.upsert_server(tmp, entry)
            items = worlds.read_servers(tmp)
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].name, "猫猫服")
            self.assertEqual(items[0].address, "mc.example.com")
            self.assertTrue(items[0].accept_textures)

            edited = worlds.ServerEntry(
                name="猫猫服 2",
                address="127.0.0.1:25565",
                accept_textures=False,
                hidden=True,
                raw=items[0].raw,
            )
            worlds.upsert_server(tmp, edited, 0)
            items = worlds.read_servers(tmp)
            self.assertEqual(items[0].name, "猫猫服 2")
            self.assertEqual(items[0].address, "127.0.0.1:25565")
            self.assertFalse(items[0].accept_textures)
            self.assertTrue(items[0].hidden)
            self.assertTrue((Path(tmp) / "servers.dat_old").is_file())

            worlds.delete_server(tmp, 0)
            self.assertEqual(worlds.read_servers(tmp), [])

    def test_invalid_address_rejected(self):
        with self.assertRaises(worlds.WorldError):
            worlds.validate_server("name", "")
        with self.assertRaises(worlds.WorldError):
            worlds.validate_server("name", "host with space")
        with self.assertRaises(worlds.WorldError):
            worlds.validate_server("", "host")

    def test_overview_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            worlds.create_world(tmp, name="世界", seed=1, version_id="1.20.1")
            worlds.upsert_server(tmp, worlds.ServerEntry(name="服", address="mc.example.com"))
            self.assertEqual(worlds.overview(tmp), (1, 1))


class ChunkbaseRegistryTests(unittest.TestCase):
    PAGE = """
    <select id="platform">
      <optgroup label="Latest Versions">
        <option value="latest//java_99_1_lb">Java 99.1 (LB)</option>
        <option value="latest//bedrock_99_1">Bedrock 99.1</option>
      </optgroup>
      <optgroup label="Experimental">
        <option value="experimental//java_99_2">Java 99.2 (Exp.)</option>
        <option value="experimental//bedrock_99_2">Bedrock 99.2 (Exp.)</option>
      </optgroup>
      <optgroup label="Java Edition">
        <option value="java//java_99_1">Java 99.1</option>
        <option value="java//java_1_21_9">Java 1.21.9 - 1.21.11</option>
        <option value="java//java_1_20">Java 1.20 - 1.20.6</option>
        <option value="java//java_1_19">Java 1.19 - 1.19.2</option>
      </optgroup>
      <optgroup label="Java Edition - Large Biomes">
        <option value="java_lb//java_99_1_lb">Java 99.1 (LB)</option>
        <option value="java_lb//java_1_21_9_lb">Java 1.21.9 - 1.21.11 (LB)</option>
      </optgroup>
      <optgroup label="Bedrock Edition">
        <option value="bedrock//bedrock_99_1">Bedrock 99.1</option>
        <option value="bedrock//bedrock_1_21">Bedrock 1.21</option>
        <option value="bedrock//bedrock_1_20">Bedrock 1.20</option>
      </optgroup>
    </select>
    """

    def test_parse_groups_and_deduplicate(self):
        groups = registry.parse_platform_options(self.PAGE)
        names = [name for name, _items in groups]
        self.assertEqual(
            names,
            [
                registry.GROUP_JAVA,
                registry.GROUP_JAVA_EXPERIMENTAL,
                registry.GROUP_JAVA_LB,
                registry.GROUP_BEDROCK,
            ],
        )
        platforms = registry.platforms(groups)
        self.assertIn("java_99_1", platforms)
        self.assertIn("java_99_2", platforms)
        self.assertIn("bedrock_99_2", platforms)
        self.assertEqual(len(platforms), 11)
        self.assertEqual(names.count(registry.GROUP_BEDROCK), 1)

    def test_future_platform_accepted_by_normalizer(self):
        self.assertEqual(ga.normalize_chunkbase_platform("java_99_1"), "java_99_1")
        self.assertEqual(ga.normalize_chunkbase_platform("bedrock_99_1"), "bedrock_99_1")
        self.assertEqual(
            ga.normalize_chunkbase_platform("javascript:alert(1)"), "java_1_21_9"
        )
        url = ga.chunkbase_url(1, "java_99_1", "end")
        self.assertIn("platform=java_99_1", url)
        self.assertIn("dimension=end", url)

    def test_parse_rejects_empty_page(self):
        with self.assertRaises(registry.RegistryError):
            registry.parse_platform_options("<html></html>")


if __name__ == "__main__":
    unittest.main(verbosity=2)
