"""服务端插件/Mod 卸载测试（文件移入回收站 + 清理下载元数据）。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from darkitten.mods import (  # noqa: E402
    mod_meta_icon_path,
    mod_meta_json_path,
    write_mod_meta,
)
from darkitten.servers import (  # noqa: E402
    AddonFile,
    ServerInfo,
    delete_addon,
    list_addons,
)


def _addon(path: Path, enabled: bool = True) -> AddonFile:
    return AddonFile(
        path=path,
        name=path.name,
        filename=path.name,
        size=path.stat().st_size if path.exists() else 0,
        enabled=enabled,
    )


class DeleteAddonTests(unittest.TestCase):
    def _fake_recycle(self, path: Path):
        path.unlink()
        return True, "已移动到回收站"

    def test_moves_jar_and_cleans_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mods = Path(tmp)
            jar = mods / "example-mod.jar"
            jar.write_bytes(b"jar")
            write_mod_meta(
                mods,
                jar.name,
                {"title": "Example Mod", "project_id": "example"},
                icon_bytes=b"png",
            )
            meta = mod_meta_json_path(mods, jar.name)
            icon = mod_meta_icon_path(mods, jar.name)
            self.assertTrue(meta.is_file())
            self.assertTrue(icon.is_file())

            with mock.patch(
                "darkitten.servers.send_to_recycle_bin",
                side_effect=self._fake_recycle,
            ):
                ok, message = delete_addon(_addon(jar))

            self.assertTrue(ok)
            self.assertEqual(message, "已移动到回收站")
            self.assertFalse(jar.exists())
            self.assertFalse(meta.exists())
            self.assertFalse(icon.exists())

    def test_cleans_metadata_after_disabled_rename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mods = Path(tmp)
            original = mods / "example-mod.jar"
            original.write_bytes(b"jar")
            write_mod_meta(mods, original.name, {"title": "Example Mod"})
            disabled = mods / "example-mod.jar.disabled"
            original.rename(disabled)

            meta = mod_meta_json_path(mods, "example-mod.jar")
            self.assertTrue(meta.is_file())

            with mock.patch(
                "darkitten.servers.send_to_recycle_bin",
                side_effect=self._fake_recycle,
            ):
                ok, _message = delete_addon(_addon(disabled, enabled=False))

            self.assertTrue(ok)
            self.assertFalse(disabled.exists())
            self.assertFalse(meta.exists())

    def test_reports_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.jar"
            ok, message = delete_addon(_addon(missing))
            self.assertFalse(ok)
            self.assertEqual(message, "文件不存在")


class ListAddonsTests(unittest.TestCase):
    def _server(self, root: Path, kind: str = "fabric") -> ServerInfo:
        folder = root / "srv"
        (folder / ("mods" if kind != "paper" else "plugins")).mkdir(parents=True)
        return ServerInfo(
            folder=folder.name,
            name="Srv",
            kind=kind,
            mc_version="1.21.1",
        )

    @staticmethod
    def _write_mod(path: Path, name: str, version: str) -> None:
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr(
                "fabric.mod.json",
                json.dumps(
                    {
                        "id": "demo",
                        "name": name,
                        "version": version,
                        "description": "A demo mod",
                    }
                ),
            )
            zf.writestr("icon.png", b"png-bytes")

    def test_reads_identity_from_jar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            info = self._server(root)
            jar = root / "srv" / "mods" / "demo-mod-1.0.jar"
            self._write_mod(jar, "Demo Mod", "1.0")

            with mock.patch("darkitten.servers.SERVERS_DIR", root):
                addons = list_addons(info)

            self.assertEqual(len(addons), 1)
            addon = addons[0]
            self.assertEqual(addon.name, "Demo Mod")
            self.assertEqual(addon.version, "1.0")
            self.assertEqual(addon.loader, "Fabric")
            self.assertIn("demo mod", addon.intro.lower())
            self.assertEqual(addon.icon_bytes, b"png-bytes")
            self.assertTrue(addon.enabled)

    def test_disabled_jar_keeps_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            info = self._server(root)
            jar = root / "srv" / "mods" / "demo-mod-1.0.jar.disabled"
            self._write_mod(jar, "Demo Mod", "1.0")

            with mock.patch("darkitten.servers.SERVERS_DIR", root):
                addons = list_addons(info)

            self.assertEqual(len(addons), 1)
            self.assertFalse(addons[0].enabled)
            self.assertEqual(addons[0].name, "Demo Mod")
            self.assertEqual(addons[0].filename, "demo-mod-1.0.jar.disabled")

    def test_broken_jar_falls_back_to_filename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            info = self._server(root)
            jar = root / "srv" / "mods" / "broken.jar"
            jar.write_bytes(b"not a zip")

            with mock.patch("darkitten.servers.SERVERS_DIR", root):
                addons = list_addons(info)

            self.assertEqual(len(addons), 1)
            self.assertEqual(addons[0].name, "broken")
            self.assertEqual(addons[0].filename, "broken.jar")


if __name__ == "__main__":
    unittest.main()