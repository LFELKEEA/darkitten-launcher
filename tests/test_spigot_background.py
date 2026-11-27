"""Spigot 下载策略与游戏进程调度的单元测试。

运行：pyruntime/python.exe tests/test_spigot_background.py
"""

from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from darkitten import proc_tune, servers  # noqa: E402
from darkitten.servers import ServerInfo  # noqa: E402


class SpigotUrlTests(unittest.TestCase):
    def test_builds_both_cdn_hosts(self) -> None:
        urls = servers.spigot_build_urls("1.21.1")
        self.assertEqual(
            urls,
            [
                "https://download.getbukkit.org/spigot/spigot-1.21.1.jar",
                "https://cdn.getbukkit.org/spigot/spigot-1.21.1.jar",
            ],
        )

    def test_empty_version_has_no_urls(self) -> None:
        self.assertEqual(servers.spigot_build_urls(""), [])

    def test_percent_stays_in_progress_range(self) -> None:
        self.assertEqual(servers._spigot_percent(0, 0), 6)
        self.assertEqual(servers._spigot_percent(0, 100), 6)
        self.assertLessEqual(servers._spigot_percent(10**9, 100), 90)


class ServerJarTests(unittest.TestCase):
    def test_valid_jar_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "server.jar"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")
                archive.writestr("patch.properties", "x" * 4096)
            self.assertTrue(servers._is_server_jar(path))

    def test_html_error_page_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "server.jar"
            path.write_text("<html>404 not found</html>" * 100, encoding="utf-8")
            self.assertFalse(servers._is_server_jar(path))

    def test_tiny_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "server.jar"
            path.write_bytes(b"PK")
            self.assertFalse(servers._is_server_jar(path))

    def test_missing_file_is_rejected(self) -> None:
        self.assertFalse(servers._is_server_jar(Path("no-such-file.jar")))


class SpigotPrebuiltTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._saved = servers.SERVERS_DIR
        servers.SERVERS_DIR = self.root

    def tearDown(self) -> None:
        servers.SERVERS_DIR = self._saved
        self._tmp.cleanup()

    def _info(self) -> ServerInfo:
        info = ServerInfo(
            folder="demo", name="演示", kind="spigot", mc_version="1.21.1", port=25565
        )
        info.path.mkdir(parents=True, exist_ok=True)
        return info

    def test_prebuilt_jar_is_used_without_building(self) -> None:
        info = self._info()
        target = info.path / "server.jar"

        def fake_download(url, dest, **kwargs):
            with zipfile.ZipFile(dest, "w") as archive:
                archive.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")
                archive.writestr("patch.properties", "x" * 4096)
            return 1

        with mock.patch.object(servers, "download_to_file", side_effect=fake_download):
            ok = servers._install_spigot_prebuilt(
                info, progress_cb=lambda *_: None, log_cb=lambda *_: None,
                cancel_event=None,
            )

        self.assertTrue(ok)
        self.assertTrue(target.is_file())
        self.assertEqual(info.launch_kind, "jar")
        self.assertEqual(info.launch_target, "server.jar")

    def test_invalid_response_falls_through_to_next_url(self) -> None:
        info = self._info()
        calls: list[str] = []

        def fake_download(url, dest, **kwargs):
            calls.append(url)
            dest.write_text("<html>blocked</html>" * 100, encoding="utf-8")
            return 1

        with mock.patch.object(servers, "download_to_file", side_effect=fake_download):
            ok = servers._install_spigot_prebuilt(
                info, progress_cb=lambda *_: None, log_cb=lambda *_: None,
                cancel_event=None,
            )

        self.assertFalse(ok)
        # 两个 CDN 都试过，各重试 4 次后放弃。
        self.assertEqual(len(set(calls)), 2)
        self.assertFalse((info.path / "server.jar").exists())

    def test_retries_same_url_before_switching(self) -> None:
        info = self._info()
        seen: list[str] = []
        state = {"n": 0}

        def flaky(url, dest, **kwargs):
            seen.append(url)
            state["n"] += 1
            if state["n"] < 3:
                raise OSError("connection reset")
            with zipfile.ZipFile(dest, "w") as archive:
                archive.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")
                archive.writestr("patch.properties", "x" * 4096)
            return 1

        with mock.patch.object(servers, "download_to_file", side_effect=flaky):
            ok = servers._install_spigot_prebuilt(
                info, progress_cb=lambda *_: None, log_cb=lambda *_: None,
                cancel_event=None,
            )

        self.assertTrue(ok)
        self.assertEqual(len(set(seen)), 1)


class ProcTuneTests(unittest.TestCase):
    def test_zero_pid_is_safe(self) -> None:
        self.assertFalse(proc_tune.set_background(0))
        self.assertFalse(proc_tune.restore_foreground(0))
        self.assertFalse(proc_tune.trim_working_set(0))
        self.assertEqual(proc_tune.window_handle(0), 0)

    def test_background_tune_flag_defaults_off(self) -> None:
        from darkitten.launch import LaunchPlan

        self.assertFalse(LaunchPlan.__dataclass_fields__["background_tune"].default)


if __name__ == "__main__":
    unittest.main()
