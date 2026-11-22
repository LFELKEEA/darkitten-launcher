"""启动参数拼装的单元测试：1.20+ 用 quickPlay 直接进服。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from darkitten.launch import _server_launch_args, _singleplayer_launch_args  # noqa: E402
from darkitten.versions import ResolvedVersion  # noqa: E402


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


class GameVersionTests(unittest.TestCase):
    @staticmethod
    def resolved(chain: list[str], jar_id: str) -> ResolvedVersion:
        item = object.__new__(ResolvedVersion)
        item.id = chain[-1]
        item.jar_id = jar_id
        item.chain = list(chain)
        return item

    def test_fabric_loader_parent_uses_minecraft_version(self):
        resolved = self.resolved(
            [
                "1.21.11",
                "fabric-loader-0.19.5-1.21.11",
                "1.21.11-Fabric 0.19.5",
            ],
            "fabric-loader-0.19.5-1.21.11",
        )
        self.assertEqual(resolved.game_version, "1.21.11")
        self.assertEqual(
            _singleplayer_launch_args("新的世界", resolved.game_version),
            ["--quickPlaySingleplayer", "新的世界"],
        )

    def test_vanilla_chain_returns_itself(self):
        resolved = self.resolved(["1.20.1"], "1.20.1")
        self.assertEqual(resolved.game_version, "1.20.1")

    def test_snapshot_chain_is_treated_as_modern(self):
        resolved = self.resolved(
            ["23w45a", "fabric-loader-0.15.0-23w45a"],
            "fabric-loader-0.15.0-23w45a",
        )
        self.assertEqual(resolved.game_version, "1.20")


if __name__ == "__main__":
    unittest.main()