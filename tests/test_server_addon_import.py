"""服务端插件/Mod 的本地导入与检查更新测试。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QMimeData, QPoint, QUrl, Qt  # noqa: E402
from PySide6.QtGui import QDragEnterEvent, QDropEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from darkitten.mods import load_mod_meta, read_mod, write_mod_meta  # noqa: E402
from darkitten.mod_updates import check_mod_update, check_mod_updates  # noqa: E402
from darkitten.servers import (  # noqa: E402
    addon_import_conflicts,
    import_addon_files,
    scan_addon_paths,
)
from darkitten.ui.pages.server import AddonDropArea  # noqa: E402


def _version(version_id: str, filename: str, size: int, sha1: str) -> dict:
    return {
        "id": version_id,
        "version_number": version_id,
        "files": [
            {
                "primary": True,
                "url": "https://example.com/" + filename,
                "filename": filename,
                "size": size,
                "hashes": {"sha1": sha1},
            }
        ],
        "dependencies": [],
    }


class ScanAddonPathsTests(unittest.TestCase):
    def test_collects_jars_recursively_and_skips_libraries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.jar").write_bytes(b"a")
            (root / "readme.txt").write_text("x", encoding="utf-8")
            nested = root / "plugins"
            nested.mkdir()
            (nested / "b.jar.disabled").write_bytes(b"b")
            libs = root / "libraries"
            libs.mkdir()
            (libs / "dep.jar").write_bytes(b"c")

            found, _ignored = scan_addon_paths([root])

            names = sorted(path.name for path in found)
            self.assertEqual(names, ["a.jar", "b.jar.disabled"])

    def test_deduplicates_same_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            jar = Path(tmp) / "a.jar"
            jar.write_bytes(b"a")
            found, _ignored = scan_addon_paths([jar, jar])
            self.assertEqual(found, [jar])

    def test_ignores_non_jar_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            text = Path(tmp) / "note.txt"
            text.write_text("x", encoding="utf-8")
            found, ignored = scan_addon_paths([text])
            self.assertEqual(found, [])
            self.assertEqual(ignored, [text])


class ImportAddonFilesTests(unittest.TestCase):
    def test_copies_jar_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_dir = root / "downloads"
            source_dir.mkdir()
            jar = source_dir / "example.jar"
            jar.write_bytes(b"jar-bytes")
            write_mod_meta(
                source_dir,
                jar.name,
                {"title": "Example", "project_id": "abc", "source": "modrinth"},
                icon_bytes=b"png",
            )
            target = root / "server" / "plugins"

            result = import_addon_files([jar], target)

            self.assertEqual(result.imported, [target / "example.jar"])
            self.assertFalse(result.errors)
            self.assertEqual((target / "example.jar").read_bytes(), b"jar-bytes")
            meta = load_mod_meta(target, "example.jar")
            self.assertEqual(meta.get("title"), "Example")
            self.assertEqual(meta.get("project_id"), "abc")

    def test_skips_identical_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            target = root / "target"
            source.mkdir()
            target.mkdir()
            (source / "same.jar").write_bytes(b"same")
            (target / "same.jar").write_bytes(b"same")

            self.assertEqual(
                addon_import_conflicts([source / "same.jar"], target), []
            )
            result = import_addon_files([source / "same.jar"], target)
            self.assertEqual(result.imported, [])
            self.assertEqual(len(result.skipped), 1)

    def test_conflict_and_overwrite_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            target = root / "target"
            source.mkdir()
            target.mkdir()
            new_file = source / "same.jar"
            old_file = target / "same.jar"
            new_file.write_bytes(b"new")
            old_file.write_bytes(b"old")

            conflicts = addon_import_conflicts([new_file], target)
            self.assertEqual(conflicts, [new_file])

            blocked = import_addon_files([new_file], target)
            self.assertEqual(blocked.imported, [])
            self.assertEqual(old_file.read_bytes(), b"old")

            result = import_addon_files([new_file], target, overwrite=True)
            self.assertEqual(result.imported, [old_file])
            self.assertEqual(old_file.read_bytes(), b"new")
            self.assertEqual(len(result.replaced), 1)
            self.assertTrue(result.replaced[0].is_file())
            self.assertEqual(result.replaced[0].read_bytes(), b"old")


class CheckAddonUpdateContentTypeTests(unittest.TestCase):
    def _make_mod(self, root: Path, name: str = "Plugin.jar"):
        jar = root / name
        jar.write_bytes(b"installed")
        return jar

    def test_curseforge_plugin_uses_plugin_content_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            jar = self._make_mod(root)
            write_mod_meta(
                root,
                jar.name,
                {
                    "source": "curseforge",
                    "project_id": "123",
                    "file_id": "old",
                    "sha1": "aa",
                },
            )
            mod = read_mod(jar)
            latest = _version("new", "Plugin-2.jar", 5, "bb")
            with mock.patch(
                "darkitten.mod_updates.cf.fetch_mod_versions",
                return_value=[latest],
            ) as fetch:
                update = check_mod_update(
                    mod, "1.20.4", "paper", content_type="plugin"
                )
            self.assertIsNotNone(update)
            self.assertEqual(update.content_type, "plugin")
            fetch.assert_called_once_with(
                "123", "paper", "1.20.4", content_type="plugin"
            )

    def test_modrinth_plugin_uses_server_addon_versions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            jar = self._make_mod(root, "Plugin.jar")
            write_mod_meta(
                root,
                jar.name,
                {
                    "source": "modrinth",
                    "project_id": "pid",
                    "file_id": "old",
                    "sha1": "aa",
                },
            )
            mod = read_mod(jar)
            latest = _version("new", "Plugin-2.jar", 5, "bb")
            with mock.patch(
                "darkitten.mod_updates.modrinth.fetch_server_addon_versions",
                return_value=[latest],
            ) as fetch:
                update = check_mod_update(
                    mod, "1.20.4", "spigot", content_type="plugin"
                )
            self.assertIsNotNone(update)
            fetch.assert_called_once_with("pid", "spigot", "1.20.4")

    def test_batch_check_forwards_content_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            jar = self._make_mod(root)
            write_mod_meta(root, jar.name, {"file_id": "old", "sha1": "aa"})
            mod = read_mod(jar)
            with mock.patch(
                "darkitten.mod_updates.check_mod_update", return_value=None
            ) as fake:
                check_mod_updates(
                    [mod], "1.20.4", "paper", content_type="plugin"
                )
            fake.assert_called_once_with(mod, "1.20.4", "paper", "plugin")


class AddonDropAreaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_viewport_drop_emits_local_paths(self) -> None:
        area = AddonDropArea()
        area.resize(320, 200)
        area.show()
        received: list[list[Path]] = []
        area.files_dropped.connect(received.append)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "a.jar").write_bytes(b"a")
            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(str(folder))])

            enter = QDragEnterEvent(
                QPoint(5, 5),
                Qt.CopyAction,
                mime,
                Qt.LeftButton,
                Qt.NoModifier,
            )
            QApplication.sendEvent(area.viewport(), enter)
            self.assertTrue(enter.isAccepted())

            drop = QDropEvent(
                QPoint(5, 5),
                Qt.CopyAction,
                mime,
                Qt.LeftButton,
                Qt.NoModifier,
            )
            QApplication.sendEvent(area.viewport(), drop)
            self.assertTrue(drop.isAccepted())
            self.assertEqual(received, [[folder]])
        area.deleteLater()
        QApplication.processEvents()


if __name__ == "__main__":
    unittest.main()