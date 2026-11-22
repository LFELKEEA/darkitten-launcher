"""服务端插件/Mod 卸载测试（文件移入回收站 + 清理下载元数据）。"""

from __future__ import annotations

import sys
import tempfile
import unittest
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
from darkitten.servers import AddonFile, delete_addon  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()