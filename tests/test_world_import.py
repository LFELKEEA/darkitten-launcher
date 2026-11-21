"""服务端种子与跨版本存档导入的单元测试（不启动界面）。"""

from __future__ import annotations

import hashlib
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from darkitten import nbt, servers, worlds  # noqa: E402
from darkitten.world_import import (  # noqa: E402
    WorldImportCancelled,
    WorldImportError,
    import_into_server,
    import_world,
    inspect_world_source,
)


def _make_world(parent: Path, name: str = "远端世界") -> Path:
    info = worlds.create_world(
        parent,
        name=name,
        seed=424242,
        game_mode=0,
        difficulty=2,
        version_id="1.20.1",
    )
    folder = info.folder
    root = nbt.read_nbt(folder / "level.dat")
    root["Data"]["Version"] = {
        "Name": nbt.NBTString("1.20.1"),
        "Id": nbt.Int(3465),
    }
    root["Data"]["DataVersion"] = nbt.Int(3465)
    nbt.write_nbt_file(folder / "level.dat", root, compressed=True, backup=False)
    (folder / "region").mkdir(exist_ok=True)
    (folder / "region" / "r.0.0.mca").write_bytes(b"overworld-chunks")
    (folder / "DIM-1" / "region").mkdir(parents=True, exist_ok=True)
    (folder / "DIM-1" / "region" / "r.0.0.mca").write_bytes(b"nether-chunks")
    (folder / "DIM1" / "region").mkdir(parents=True, exist_ok=True)
    (folder / "DIM1" / "region" / "r.0.0.mca").write_bytes(b"end-chunks")
    (folder / "dimensions" / "mypack" / "custom" / "region").mkdir(parents=True)
    (folder / "dimensions" / "mypack" / "custom" / "region" / "r.0.0.mca").write_bytes(
        b"custom-chunks"
    )
    (folder / "playerdata").mkdir(exist_ok=True)
    (folder / "playerdata" / "abc.dat").write_bytes(b"player-inventory")
    (folder / "stats").mkdir(exist_ok=True)
    (folder / "stats" / "abc.json").write_text("{}", encoding="utf-8")
    (folder / "advancements").mkdir(exist_ok=True)
    (folder / "advancements" / "abc.json").write_text("{}", encoding="utf-8")
    return folder


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ServerSeedTests(unittest.TestCase):
    def test_seed_is_written_to_level_seed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(servers, "SERVERS_DIR", Path(tmp)):
                info = servers.ServerInfo(
                    folder="seed-server",
                    name="种子服",
                    kind="paper",
                    mc_version="1.20.1",
                    seed="123456789",
                )
                info.path.mkdir(parents=True)
                servers.update_server_properties(info)
                text = (info.path / "server.properties").read_text(encoding="utf-8")
                self.assertIn("level-seed=123456789", text)

    def test_blank_seed_keeps_existing_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(servers, "SERVERS_DIR", Path(tmp)):
                info = servers.ServerInfo(
                    folder="seed-server",
                    name="种子服",
                    kind="paper",
                    mc_version="1.20.1",
                    seed="123456789",
                )
                info.path.mkdir(parents=True)
                servers.update_server_properties(info)
                info.seed = ""
                servers.update_server_properties(info)
                text = (info.path / "server.properties").read_text(encoding="utf-8")
                self.assertIn("level-seed=123456789", text)


class WorldImportTests(unittest.TestCase):
    def test_inspect_detects_dimensions_and_players(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = _make_world(Path(tmp) / "src")
            info = inspect_world_source(source)
            self.assertEqual(info.seed, 424242)
            self.assertEqual(info.version_name, "1.20.1")
            self.assertEqual(info.players, 1)
            self.assertIn("主世界", info.dimensions)
            self.assertIn("下界", info.dimensions)
            self.assertIn("末地", info.dimensions)
            self.assertIn("mypack:custom", info.dimensions)

    def test_folder_import_copies_everything_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = _make_world(Path(tmp) / "src")
            level_digest = _digest(source / "level.dat")
            player_digest = _digest(source / "playerdata" / "abc.dat")

            result = import_world(source, Path(tmp) / "saves")

            self.assertEqual(result.folder.name, source.name)
            self.assertTrue((result.folder / "level.dat").is_file())
            self.assertEqual(_digest(result.folder / "level.dat"), level_digest)
            self.assertEqual(
                _digest(result.folder / "playerdata" / "abc.dat"), player_digest
            )
            for relative in (
                Path("region/r.0.0.mca"),
                Path("DIM-1/region/r.0.0.mca"),
                Path("DIM1/region/r.0.0.mca"),
                Path("dimensions/mypack/custom/region/r.0.0.mca"),
                Path("stats/abc.json"),
                Path("advancements/abc.json"),
            ):
                self.assertEqual(
                    _digest(result.folder / relative),
                    _digest(source / relative),
                    msg=str(relative),
                )
            loaded, _ = worlds.load_world(result.folder)
            self.assertEqual(loaded.seed, 424242)

    def test_zip_import_with_wrapper_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = _make_world(Path(tmp) / "src")
            archive = Path(tmp) / "world.zip"
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
                for path in source.rglob("*"):
                    if path.is_file():
                        zf.write(path, Path("我的世界") / path.relative_to(source))

            info = inspect_world_source(archive)
            self.assertEqual(info.name, "远端世界")
            self.assertEqual(info.players, 1)
            self.assertIn("末地", info.dimensions)

            result = import_world(info, Path(tmp) / "saves", folder_name="导入世界")
            self.assertEqual(result.folder.name, "导入世界")
            self.assertEqual(
                (result.folder / "DIM1" / "region" / "r.0.0.mca").read_bytes(),
                b"end-chunks",
            )
            self.assertEqual(
                (result.folder / "playerdata" / "abc.dat").read_bytes(),
                b"player-inventory",
            )

    def test_existing_world_is_backed_up_not_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            saves = Path(tmp) / "saves"
            source = _make_world(Path(tmp) / "src", name="新世界")
            old = saves / source.name
            old.mkdir(parents=True)
            (old / "level.dat").write_bytes(b"old-world")

            result = import_world(source, saves)

            self.assertIsNotNone(result.backup)
            self.assertTrue(result.backup.is_dir())
            self.assertEqual((result.backup / "level.dat").read_bytes(), b"old-world")
            self.assertNotEqual((result.folder / "level.dat").read_bytes(), b"old-world")

    def test_zip_slip_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = _make_world(Path(tmp) / "src")
            archive = Path(tmp) / "slip.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("world/level.dat", (source / "level.dat").read_bytes())
                zf.writestr("../evil.txt", b"should-not-escape")
                zf.writestr("/absolute.txt", b"should-not-be-absolute")

            result = import_world(archive, Path(tmp) / "saves")
            self.assertFalse((Path(tmp) / "evil.txt").exists())
            self.assertFalse((result.folder / "absolute.txt").exists())
            self.assertTrue((result.folder / "level.dat").is_file())

    def test_multiple_worlds_in_one_folder_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _make_world(Path(tmp) / "saves", name="世界一")
            _make_world(Path(tmp) / "saves", name="世界二")
            with self.assertRaises(WorldImportError):
                inspect_world_source(Path(tmp) / "saves")

    def test_cancel_stops_before_copying(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = _make_world(Path(tmp) / "src")
            event = threading.Event()
            event.set()
            with self.assertRaises(WorldImportCancelled):
                import_world(source, Path(tmp) / "saves", cancel_event=event)

    def test_cancel_restores_existing_world_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            saves = Path(tmp) / "saves"
            source = _make_world(Path(tmp) / "src", name="要导入的世界")
            old = saves / source.name
            old.mkdir(parents=True)
            (old / "level.dat").write_bytes(b"old-world")
            event = threading.Event()
            event.set()

            with self.assertRaises(WorldImportCancelled):
                import_world(source, saves, cancel_event=event)

            self.assertTrue(old.is_dir())
            self.assertEqual((old / "level.dat").read_bytes(), b"old-world")
            leftovers = [p.name for p in saves.iterdir() if ".bak-" in p.name]
            self.assertEqual(leftovers, [])

    def test_import_into_server_sets_level_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(servers, "SERVERS_DIR", Path(tmp) / "servers"):
                info = servers.ServerInfo(
                    folder="my-server",
                    name="我的服",
                    kind="paper",
                    mc_version="1.20.1",
                )
                info.path.mkdir(parents=True)
                source = _make_world(Path(tmp) / "src")

                result = import_into_server(info, source, level_name="world")

                self.assertEqual(result.folder, info.path / "world")
                self.assertTrue((info.path / "world" / "level.dat").is_file())
                text = (info.path / "server.properties").read_text(encoding="utf-8")
                self.assertIn("level-name=world", text)


if __name__ == "__main__":
    unittest.main()
