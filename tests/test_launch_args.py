"""启动参数拼装的单元测试：1.20+ 用 quickPlay 直接进服。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from darkitten.launch import _server_launch_args, _singleplayer_launch_args  # noqa: E402


class ServerLaunchArgsTests(unittest.TestCase):
    def test_modern_version_uses_quick_play(self):
        self.assertEqual(
            _server_launch_args("127.0.0.1", 25565, "1.21.4"),
            ["--quickPlayMultiplayer", "127.0.0.1:25565"],
        )

    def test_1_20_boundary(self):
        self.assertEqual(
            _server_launch_args("example.com", 25566, "1.20"),
            ["--quickPlayMultiplayer", "example.com:25566"],
        )

    def test_legacy_version_keeps_server_flags(self):
        self.assertEqual(
            _server_launch_args("example.com", 25565, "1.19.4"),
            ["--server", "example.com", "--port", "25565"],
        )

    def test_ipv6_host_is_bracketed(self):
        self.assertEqual(
            _server_launch_args("[::1]", 25565, "1.21.1"),
            ["--quickPlayMultiplayer", "[::1]:25565"],
        )


class SingleplayerLaunchArgsTests(unittest.TestCase):
    def test_modern_version_uses_quick_play(self):
        self.assertEqual(
            _singleplayer_launch_args("My World", "1.21.4"),
            ["--quickPlaySingleplayer", "My World"],
        )

    def test_1_20_boundary(self):
        self.assertEqual(
            _singleplayer_launch_args("world", "1.20"),
            ["--quickPlaySingleplayer", "world"],
        )

    def test_legacy_version_has_no_quick_play(self):
        self.assertEqual(_singleplayer_launch_args("world", "1.19.4"), [])

    def test_empty_level_is_ignored(self):
        self.assertEqual(_singleplayer_launch_args("   ", "1.21.4"), [])


if __name__ == "__main__":
    unittest.main()