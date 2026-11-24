"""服务端种子与跨版本存档导入的单元测试（不启动界面）。"""

from __future__ import annotations

import hashlib
import shutil
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
    import_dimensions_into_server,
    import_into_server,
    import_world,
    import_world_dimensions,
    inspect_world_source,
    is_newer_version,
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


class VersionCompareTests(unittest.TestCase):
    def test_newer_source_versions_are_detected(self) -> None:
        self.assertTrue(is_newer_version("1.21.1", "1.20.1"))
        self.assertTrue(is_newer_version("26.1", "1.21.11"))
        self.assertFalse(is_newer_version("1.20.1", "1.21.1"))
        self.assertFalse(is_newer_version("1.20.1", "1.20.1"))
        self.assertFalse(is_newer_version("", "1.20.1"))
        self.assertFalse(is_newer_version("1.20.1", ""))


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


class DimensionImportTests(unittest.TestCase):
    """只导入主世界、下界或末地时，不能动到其他数据。"""

    def _target(self, root: Path) -> Path:
        target = _make_world(root / "server", name="目标世界")
        return target

    def test_overworld_only_keeps_other_dimensions_and_player_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = _make_world(Path(tmp) / "src", name="来源世界")
            target = self._target(Path(tmp))
            level_digest = _digest(target / "level.dat")
            (target / "region" / "r.0.0.mca").write_bytes(b"old-overworld")
            (target / "DIM-1" / "region" / "r.0.0.mca").write_bytes(b"old-nether")
            (target / "DIM1" / "region" / "r.0.0.mca").write_bytes(b"old-end")
            (target / "playerdata" / "abc.dat").write_bytes(b"target-player")

            result = import_world_dimensions(
                source,
                target,
                dimensions=["overworld"],
                level_name=target.name,
                target_version="1.20.1",
                server_kind="fabric",
            )

            self.assertEqual(
                (target / "region" / "r.0.0.mca").read_bytes(), b"overworld-chunks"
            )
            self.assertEqual(
                (target / "DIM-1" / "region" / "r.0.0.mca").read_bytes(),
                b"old-nether",
            )
            self.assertEqual(
                (target / "DIM1" / "region" / "r.0.0.mca").read_bytes(), b"old-end"
            )
            self.assertEqual(
                (target / "playerdata" / "abc.dat").read_bytes(), b"target-player"
            )
            self.assertEqual(_digest(target / "level.dat"), level_digest)
            self.assertTrue(result.backups)
            self.assertIn("主世界", result.dimension_text)
            backup = next(p for p in result.backups if p.name.startswith("region.bak-"))
            self.assertEqual(
                (backup / "r.0.0.mca").read_bytes(), b"old-overworld"
            )

    def test_nether_only_replaces_only_nether(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = _make_world(Path(tmp) / "src", name="来源世界")
            target = self._target(Path(tmp))
            (target / "region" / "r.0.0.mca").write_bytes(b"old-overworld")
            (target / "DIM-1" / "region" / "r.0.0.mca").write_bytes(b"old-nether")
            (target / "DIM1" / "region" / "r.0.0.mca").write_bytes(b"old-end")
            (target / "playerdata" / "abc.dat").write_bytes(b"target-player")

            import_world_dimensions(
                source,
                target,
                dimensions=["nether"],
                level_name=target.name,
                target_version="1.20.1",
                server_kind="fabric",
            )

            self.assertEqual(
                (target / "DIM-1" / "region" / "r.0.0.mca").read_bytes(),
                b"nether-chunks",
            )
            self.assertEqual(
                (target / "region" / "r.0.0.mca").read_bytes(), b"old-overworld"
            )
            self.assertEqual(
                (target / "DIM1" / "region" / "r.0.0.mca").read_bytes(), b"old-end"
            )
            self.assertEqual(
                (target / "playerdata" / "abc.dat").read_bytes(), b"target-player"
            )

    def test_paper_split_layout_uses_sibling_world_folders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = _make_world(Path(tmp) / "src", name="来源世界")
            target = self._target(Path(tmp))
            shutil.rmtree(target / "DIM-1", ignore_errors=True)
            shutil.rmtree(target / "DIM1", ignore_errors=True)
            (target / "region" / "r.0.0.mca").write_bytes(b"old-overworld")

            import_world_dimensions(
                source,
                target,
                dimensions=["nether", "end"],
                level_name=target.name,
                target_version="1.20.1",
                server_kind="paper",
            )

            nether = target.parent / f"{target.name}_nether" / "DIM-1" / "region"
            end = target.parent / f"{target.name}_the_end" / "DIM1" / "region"
            self.assertEqual(
                (nether / "r.0.0.mca").read_bytes(), b"nether-chunks"
            )
            self.assertEqual((end / "r.0.0.mca").read_bytes(), b"end-chunks")
            self.assertEqual(
                (target / "region" / "r.0.0.mca").read_bytes(), b"old-overworld"
            )

    def test_namespaced_layout_uses_dimensions_minecraft(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = _make_world(Path(tmp) / "src", name="来源世界")
            target = self._target(Path(tmp))
            shutil.rmtree(target / "DIM-1", ignore_errors=True)
            shutil.rmtree(target / "DIM1", ignore_errors=True)
            ns_overworld = target / "dimensions" / "minecraft" / "overworld"
            (ns_overworld / "region").mkdir(parents=True)
            (ns_overworld / "region" / "r.0.0.mca").write_bytes(b"old-ns")

            import_world_dimensions(
                source,
                target,
                dimensions=["overworld", "nether"],
                level_name=target.name,
                target_version="26.1",
                server_kind="paper",
            )

            self.assertEqual(
                (ns_overworld / "region" / "r.0.0.mca").read_bytes(),
                b"overworld-chunks",
            )
            self.assertEqual(
                (
                    target
                    / "dimensions"
                    / "minecraft"
                    / "the_nether"
                    / "region"
                    / "r.0.0.mca"
                ).read_bytes(),
                b"nether-chunks",
            )
            self.assertEqual(
                (
                    target
                    / "dimensions"
                    / "mypack"
                    / "custom"
                    / "region"
                    / "r.0.0.mca"
                ).read_bytes(),
                b"custom-chunks",
            )
            self.assertFalse((target / "DIM-1").exists())

    def test_new_world_copies_common_files_and_selected_dimension(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = _make_world(Path(tmp) / "src", name="来源世界")
            new_world = Path(tmp) / "saves" / "新世界"

            import_world_dimensions(
                source,
                new_world,
                dimensions=["nether"],
                level_name="新世界",
                target_version="1.20.1",
                server_kind="fabric",
            )

            self.assertTrue((new_world / "level.dat").is_file())
            self.assertEqual(
                (new_world / "playerdata" / "abc.dat").read_bytes(),
                b"player-inventory",
            )
            self.assertEqual(
                (new_world / "DIM-1" / "region" / "r.0.0.mca").read_bytes(),
                b"nether-chunks",
            )
            self.assertFalse((new_world / "region").exists())
            self.assertFalse((new_world / "DIM1").exists())

    def test_zip_source_supports_selective_import(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = _make_world(Path(tmp) / "src", name="来源世界")
            archive = Path(tmp) / "world.zip"
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
                for path in source.rglob("*"):
                    if path.is_file():
                        zf.write(path, Path("我的世界") / path.relative_to(source))
            new_world = Path(tmp) / "saves" / "只导末地"

            import_world_dimensions(
                archive,
                new_world,
                dimensions=["end"],
                level_name="只导末地",
                target_version="1.20.1",
                server_kind="fabric",
            )

            self.assertTrue((new_world / "level.dat").is_file())
            self.assertEqual(
                (new_world / "DIM1" / "region" / "r.0.0.mca").read_bytes(),
                b"end-chunks",
            )
            self.assertFalse((new_world / "DIM-1").exists())
            self.assertFalse((new_world / "region").exists())

    def test_missing_source_dimension_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = _make_world(Path(tmp) / "src", name="来源世界")
            shutil.rmtree(source / "DIM1")
            target = self._target(Path(tmp))

            with self.assertRaises(WorldImportError):
                import_world_dimensions(
                    source,
                    target,
                    dimensions=["end"],
                    level_name=target.name,
                    target_version="1.20.1",
                )

    def test_cancel_restores_overwritten_dimension(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = _make_world(Path(tmp) / "src", name="来源世界")
            target = self._target(Path(tmp))
            (target / "region" / "r.0.0.mca").write_bytes(b"old-overworld")
            event = threading.Event()
            event.set()

            with self.assertRaises(WorldImportCancelled):
                import_world_dimensions(
                    source,
                    target,
                    dimensions=["overworld"],
                    level_name=target.name,
                    target_version="1.20.1",
                    cancel_event=event,
                )

            self.assertEqual(
                (target / "region" / "r.0.0.mca").read_bytes(), b"old-overworld"
            )
            leftovers = [p.name for p in target.iterdir() if ".bak-" in p.name]
            self.assertEqual(leftovers, [])

    def test_all_three_dimensions_falls_back_to_full_import(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = _make_world(Path(tmp) / "src", name="来源世界")
            target = self._target(Path(tmp))
            (target / "region" / "r.0.0.mca").write_bytes(b"old-overworld")

            result = import_world_dimensions(
                source,
                target,
                dimensions=["overworld", "nether", "end"],
                level_name=target.name,
                target_version="1.20.1",
                server_kind="fabric",
            )

            self.assertIsNotNone(result.backup)
            self.assertEqual(result.dimension_text, "主世界、下界、末地")
            self.assertEqual(
                (target / "dimensions" / "mypack" / "custom" / "region" / "r.0.0.mca")
                .read_bytes(),
                b"custom-chunks",
            )

    def test_dimension_import_into_paper_server_sets_level_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(servers, "SERVERS_DIR", Path(tmp) / "servers"):
                info = servers.ServerInfo(
                    folder="my-server",
                    name="我的服",
                    kind="paper",
                    mc_version="1.20.1",
                )
                info.path.mkdir(parents=True)
                source = _make_world(Path(tmp) / "src", name="来源世界")

                result = import_dimensions_into_server(
                    info,
                    source,
                    dimensions=["nether"],
                    level_name="world",
                )

                self.assertEqual(result.folder, info.path / "world")
                self.assertTrue((info.path / "world" / "level.dat").is_file())
                self.assertEqual(
                    (
                        info.path
                        / "world_nether"
                        / "DIM-1"
                        / "region"
                        / "r.0.0.mca"
                    ).read_bytes(),
                    b"nether-chunks",
                )
                text = (info.path / "server.properties").read_text(encoding="utf-8")
                self.assertIn("level-name=world", text)


if __name__ == "__main__":
    unittest.main()
