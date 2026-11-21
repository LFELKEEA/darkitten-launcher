"""启动器联动桥的端到端测试。

不依赖真实的 Minecraft 安装：版本 JSON 和游戏目录都是临时构造的。
运行：python tests/test_bridge.py
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from unittest import mock

from PySide6.QtCore import Qt

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from darkitten import bridge, decompiler  # noqa: E402


class FakeConfig:
    def __init__(self, game_dir: Path) -> None:
        self.game_path = game_dir

    def mods_path(self, instance: str = "") -> Path:
        return self.game_path / "mods" / (instance or "")


def _request(url: str, token: str = "", method: str = "GET", body: dict | None = None):
    headers = {"Accept": "application/json"}
    if token:
        headers["X-Darkitten-Token"] = token
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, headers=headers, data=data, method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {"error": raw}
        return exc.code, payload


class MirrorCase(unittest.TestCase):
    def test_mirror_hosts(self) -> None:
        url = "https://piston-data.mojang.com/v1/objects/abc/client.jar"
        self.assertEqual(
            bridge.mirror_url(url),
            "https://bmclapi2.bangbang93.com/v1/objects/abc/client.jar",
        )
        library = "https://libraries.minecraft.net/com/example/demo/1.0/demo-1.0.jar"
        self.assertEqual(
            bridge.mirror_url(library),
            "https://bmclapi2.bangbang93.com/maven/com/example/demo/1.0/demo-1.0.jar",
        )
        asset = "https://resources.download.minecraft.net/ab/abcdef"
        self.assertEqual(
            bridge.mirror_url(asset),
            "https://bmclapi2.bangbang93.com/assets/ab/abcdef",
        )
        untouched = "https://example.com/file.jar"
        self.assertEqual(bridge.mirror_url(untouched), untouched)


class DecompilerCase(unittest.TestCase):
    def test_safe_name(self) -> None:
        self.assertEqual(decompiler.safe_name("Sodium 0.5.8 / beta"), "Sodium-0.5.8-beta")
        self.assertEqual(decompiler.safe_name(""), "mod")
        self.assertEqual(decompiler.safe_name("..."), "mod")

    def test_nested_jars(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="dma-bridge-"))
        jar = root / "outer.jar"
        nested = io.BytesIO()
        with zipfile.ZipFile(nested, "w") as inner:
            inner.writestr("demo/Inner.class", b"fake")
        with zipfile.ZipFile(jar, "w") as archive:
            archive.writestr("META-INF/jarjar/inner.jar", nested.getvalue())
            archive.writestr("other/nested.jar", nested.getvalue())
            archive.writestr("demo/Outer.class", b"fake")
        found = decompiler._nested_jars(jar)
        self.assertEqual([name for name, _ in found], ["META-INF/jarjar/inner.jar"])
        self.assertGreater(len(found[0][1]), 0)

    def test_nested_jars_broken_file(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="dma-bridge-"))
        jar = root / "broken.jar"
        jar.write_bytes(b"not a zip")
        self.assertEqual(decompiler._nested_jars(jar), [])


class BridgeCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="dma-bridge-http-")
        self.tmp = Path(self._tmp.name)
        self.game = self.tmp / "minecraft"
        version_dir = self.game / "versions" / "1.21.1"
        version_dir.mkdir(parents=True)
        self.version_json = version_dir / "1.21.1.json"
        self.version_json.write_text(
            json.dumps(
                {
                    "id": "1.21.1",
                    "type": "release",
                    "releaseTime": "2024-08-08T00:00:00+00:00",
                    "javaVersion": {"majorVersion": 21},
                    "assetIndex": {"id": "21", "url": "https://example.com/assets.json"},
                    "downloads": {
                        "client": {
                            "url": "https://piston-data.mojang.com/v1/objects/abc/client.jar",
                            "sha1": "a" * 40,
                            "size": 123,
                        },
                        "server": {
                            "url": "https://piston-data.mojang.com/v1/objects/def/server.jar",
                            "sha1": "b" * 40,
                            "size": 456,
                        },
                    },
                    "libraries": [
                        {
                            "name": "com.example:demo:1.0",
                            "downloads": {
                                "artifact": {
                                    "path": "com/example/demo/1.0/demo-1.0.jar",
                                    "url": "https://libraries.minecraft.net/com/example/demo/1.0/demo-1.0.jar",
                                    "sha1": "c" * 40,
                                    "size": 789,
                                }
                            },
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.learn = self.tmp / "learn"
        self.bridge_file = self.tmp / "bridge.json"
        self._patches = [
            mock.patch.object(bridge, "BRIDGE_FILE", self.bridge_file),
            mock.patch.object(bridge, "LEARN_DIR", self.learn),
        ]
        for item in self._patches:
            item.start()

    def tearDown(self) -> None:
        for item in reversed(self._patches):
            item.stop()
        self._tmp.cleanup()

    def start(self) -> bridge.BridgeServer:
        server = bridge.BridgeServer(FakeConfig(self.game))
        server.start()
        self.addCleanup(server.stop)
        return server

    def test_health_is_public_and_descriptor_written(self) -> None:
        server = self.start()
        status, payload = _request(f"{server.base_url}/health")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["name"], "Darkitten Launcher")
        self.assertTrue(self.bridge_file.is_file())
        descriptor = json.loads(self.bridge_file.read_text(encoding="utf-8"))
        self.assertEqual(descriptor["token"], server.token)
        self.assertEqual(descriptor["port"], server.port)
        self.assertEqual(Path(descriptor["game_dir"]), self.game)

    def test_token_required(self) -> None:
        server = self.start()
        status, payload = _request(f"{server.base_url}/versions")
        self.assertEqual(status, 401)
        self.assertFalse(payload["ok"])
        status, payload = _request(f"{server.base_url}/versions", token=server.token)
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        ids = {item["id"] for item in payload["versions"]}
        self.assertIn("1.21.1", ids)

    def test_vanilla_local_and_mirror(self) -> None:
        server = self.start()
        status, payload = _request(
            f"{server.base_url}/vanilla/1.21.1", token=server.token
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["id"], "1.21.1")
        self.assertEqual(payload["java_major"], 21)
        self.assertEqual(Path(payload["local"]), self.version_json)
        self.assertTrue(payload["downloads"]["client"]["mirror"].startswith("https://bmclapi2.bangbang93.com/"))
        self.assertEqual(payload["libraries"][0]["path"], "com/example/demo/1.0/demo-1.0.jar")
        self.assertTrue(payload["libraries"][0]["mirror"].startswith("https://bmclapi2.bangbang93.com/maven/"))

    def test_unknown_route_and_missing_version(self) -> None:
        server = self.start()
        status, payload = _request(f"{server.base_url}/nope", token=server.token)
        self.assertEqual(status, 404)
        self.assertFalse(payload["ok"])
        status, payload = _request(
            f"{server.base_url}/launch", token=server.token, method="POST", body={"version": "9.9.9"}
        )
        self.assertEqual(status, 400)
        self.assertIn("没有版本", payload["error"])

    def test_launch_emits_signal(self) -> None:
        server = self.start()
        received: list[str] = []
        server.signals.launch.connect(received.append, Qt.ConnectionType.DirectConnection)
        status, payload = _request(
            f"{server.base_url}/launch",
            token=server.token,
            method="POST",
            body={"version": "1.21.1"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["queued"])
        # Qt 信号在没有事件循环时按直连方式同步触发。
        self.assertEqual(received, ["1.21.1"])

    def test_open_path_is_restricted(self) -> None:
        server = self.start()
        status, payload = _request(
            f"{server.base_url}/open",
            token=server.token,
            method="POST",
            body={"path": str(self.tmp / "outside.txt")},
        )
        self.assertEqual(status, 403)
        self.assertFalse(payload["ok"])
        self.assertIn("只能打开", payload["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)