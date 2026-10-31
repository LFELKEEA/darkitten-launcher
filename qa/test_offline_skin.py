from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import tempfile
from http.client import HTTPConnection
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from PySide6.QtCore import QBuffer, QIODevice, QEventLoop, QTimer
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

import darkitten.launch as launch
import darkitten.offline_skin as offline
from darkitten.accounts import Account, offline_uuid
from darkitten.config import Config
from darkitten.java import JavaInfo
from darkitten.launch import GameProcess

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> bool:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"{name}: OK{(' (' + detail + ')') if detail else ''}")
    else:
        FAIL += 1
        print(f"{name}: FAIL{(' (' + detail + ')') if detail else ''}")
    return condition


def png_bytes(path: Path, width: int = 64, height: int = 64) -> Path:
    image = QImage(width, height, QImage.Format_ARGB32)
    image.fill(QColor("#d06090"))
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(buffer.data()))
    return path


def request(
    server: offline.OfflineSkinServer,
    method: str,
    path: str,
    payload=None,
) -> tuple[int, dict[str, str], bytes]:
    parsed = urlsplit(server.url)
    connection = HTTPConnection(parsed.hostname, parsed.port, timeout=5)
    body = None
    headers = {}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    data = response.read()
    result = response.status, dict(response.getheaders()), data
    connection.close()
    return result


def verify_signature(value: str, signature: str) -> bool:
    raw = base64.b64decode(signature)
    block = pow(
        int.from_bytes(raw, "big"), offline._RSA_E, offline._RSA_N
    ).to_bytes(offline._RSA_KEY_BYTES, "big")
    digest_info = offline._SHA1_DIGEST_INFO + hashlib.sha1(
        value.encode("utf-8")
    ).digest()
    if not block.startswith(b"\x00\x01"):
        return False
    separator = block.find(b"\x00", 2)
    if separator < 10:
        return False
    return block[separator + 1 :] == digest_info


def server_tests(root: Path) -> None:
    skin_path = png_bytes(root / "Slim.png")
    skin_data = skin_path.read_bytes()
    server = offline.OfflineSkinServer("TestCat", offline_uuid("TestCat"), skin_data, "slim")
    server.start()
    check("server_bound_loopback", "127.0.0.1" in server.url, server.url)

    status, _, body = request(server, "GET", "/")
    metadata = json.loads(body)
    check(
        "metadata_skin_domains",
        status == 200 and metadata.get("skinDomains") == ["127.0.0.1", "localhost"],
    )
    check(
        "metadata_public_key",
        metadata.get("signaturePublickey") == offline._PUBLIC_KEY_PEM,
    )
    check(
        "metadata_features",
        metadata["meta"].get("feature.non_email_login") is True,
    )

    status, _, body = request(server, "GET", "/status")
    check("status_endpoint", status == 200 and json.loads(body)["user.count"] == 1)

    status, _, body = request(
        server, "POST", "/api/profiles/minecraft", ["TestCat", "Alex"]
    )
    profiles = json.loads(body)
    check(
        "profile_lookup",
        status == 200
        and len(profiles) == 1
        and profiles[0]["name"] == "TestCat"
        and profiles[0]["id"] == server.player_uuid,
    )
    status, _, body = request(
        server, "POST", "/api/profiles/minecraft", ["Alex"]
    )
    check("profile_lookup_miss", status == 200 and json.loads(body) == [])

    status, _, body = request(
        server,
        "GET",
        "/sessionserver/session/minecraft/hasJoined?username=TestCat&serverId=demo",
    )
    profile = json.loads(body)
    check(
        "has_joined",
        status == 200
        and profile["name"] == "TestCat"
        and profile["id"] == server.player_uuid,
    )
    prop = profile["properties"][0]
    value = prop["value"]
    texture = json.loads(base64.b64decode(value))
    check(
        "texture_payload",
        texture["profileName"] == "TestCat"
        and texture["profileId"] == server.player_uuid
        and texture["textures"]["SKIN"]["url"].endswith(server.texture_hash)
        and texture["textures"]["SKIN"]["metadata"]["model"] == "slim",
    )
    check("texture_signature", verify_signature(value, prop["signature"]))
    check("texture_signature_detects_tamper", not verify_signature(value + "x", prop["signature"]))

    status, _, body = request(
        server,
        "GET",
        "/sessionserver/session/minecraft/hasJoined?username=Other",
    )
    check("has_joined_miss", status == 204 and body == b"")

    status, headers, body = request(
        server, "GET", f"/textures/{server.texture_hash}"
    )
    check(
        "texture_download",
        status == 200
        and headers.get("Content-Type") == "image/png"
        and body == skin_data,
    )
    status, _, body = request(server, "GET", "/textures/not-found")
    check("texture_miss", status == 204 and body == b"")

    status, _, body = request(
        server, "GET", f"/sessionserver/session/minecraft/profile/{server.player_uuid}"
    )
    check(
        "profile_by_uuid",
        status == 200 and json.loads(body)["id"] == server.player_uuid,
    )

    status, _, body = request(
        server,
        "POST",
        "/authserver/authenticate",
        {"agent": {"name": "Minecraft", "version": 1}, "username": "TestCat"},
    )
    auth = json.loads(body)
    check(
        "authenticate",
        status == 200
        and auth["accessToken"] == server.access_token
        and auth["selectedProfile"]["name"] == "TestCat",
    )
    status, headers, _ = request(
        server, "POST", "/sessionserver/session/minecraft/join", {"accessToken": "x"}
    )
    check("join", status == 204)

    server.stop()
    check("server_stopped", server._httpd is None)
    server.stop()


def runtime_tests(root: Path) -> None:
    skin_path = png_bytes(root / "Runtime.png")
    fake_jar = root / "runtime" / "authlib-injector-1.2.8.jar"
    fake_jar.parent.mkdir(parents=True, exist_ok=True)
    fake_jar.write_bytes(b"PK\x03\x04fake")

    config_path = root / "config.json"
    config_module = sys.modules["darkitten.config"]
    original_config_path = config_module.CONFIG_PATH
    config_module.CONFIG_PATH = config_path
    try:
        cfg = Config(game_dir=str(root / "minecraft"), player_name="OfflineCat")
        cfg.skin_path = str(skin_path)
        cfg.skin_variant = "slim"

        with mock.patch.object(
            offline, "ensure_authlib_jar", lambda status_cb=None: fake_jar
        ):
            runtime = offline.prepare_offline_runtime(
                cfg, "OfflineCat", offline_uuid("OfflineCat")
            )
        check("runtime_created", runtime is not None)
        assert runtime is not None
        check(
            "runtime_javaagent",
            runtime.jvm_args[0].startswith(f"-javaagent:{fake_jar}=")
            and runtime.jvm_args[1] == "-Dauthlibinjector.side=client",
            runtime.jvm_args[0],
        )
        check(
            "runtime_args_point_to_server",
            runtime.jvm_args[0].endswith(runtime.server.url),
        )
        check(
            "runtime_note",
            "OfflineCat" in runtime.note
            and "Slim" in runtime.note
            and runtime.server.url in runtime.note,
            runtime.note,
        )
        status, _, body = request(
            runtime.server,
            "GET",
            "/sessionserver/session/minecraft/hasJoined?username=OfflineCat",
        )
        check(
            "runtime_server_serves_profile",
            status == 200 and json.loads(body)["name"] == "OfflineCat",
        )
        runtime.close()
        check("runtime_close_idempotent", runtime.server._httpd is None)
        runtime.close()

        with mock.patch.object(
            offline, "ensure_authlib_jar", lambda status_cb=None: fake_jar
        ):
            with mock.patch.object(
                offline, "local_skin_path", lambda: root / "missing-local-skin.png"
            ):
                no_skin = offline.prepare_offline_runtime(
                    Config(game_dir=str(root / "minecraft")),
                    "NoSkin",
                    offline_uuid("NoSkin"),
                )
        check("runtime_skips_without_skin", no_skin is None)

        legacy_path = png_bytes(root / "Legacy.png", 64, 32)
        cfg.skin_path = str(legacy_path)
        cfg.skin_variant = "slim"
        with mock.patch.object(
            offline, "ensure_authlib_jar", lambda status_cb=None: fake_jar
        ):
            legacy_runtime = offline.prepare_offline_runtime(
                cfg, "OfflineCat", offline_uuid("OfflineCat")
            )
        check(
            "runtime_legacy_classic",
            legacy_runtime is not None and legacy_runtime.server.variant == "classic",
        )
        if legacy_runtime is not None:
            legacy_runtime.close()
    finally:
        config_module.CONFIG_PATH = original_config_path


def make_version(root: Path, version_id: str = "Demo") -> Path:
    folder = root / "versions" / version_id
    folder.mkdir(parents=True, exist_ok=True)
    data = {
        "id": version_id,
        "type": "release",
        "mainClass": "net.minecraft.client.main.Main",
        "minecraftArguments": (
            "--username ${auth_player_name} --version ${version_name} "
            "--gameDir ${game_directory} --assetsDir ${assets_root} "
            "--assetIndex ${assets_index_name} --uuid ${auth_uuid} "
            "--accessToken ${auth_access_token} --userType ${user_type}"
        ),
        "assetIndex": {"id": "1.21", "url": "http://example.invalid/a.json"},
        "assets": "1.21",
        "javaVersion": {"majorVersion": 21},
        "libraries": [],
    }
    (folder / f"{version_id}.json").write_text(
        json.dumps(data), encoding="utf-8"
    )
    return root


class FakeRuntime:
    def __init__(self) -> None:
        self.jvm_args = [
            "-javaagent:C:/fake/authlib-injector.jar=http://127.0.0.1:12345",
            "-Dauthlibinjector.side=client",
        ]
        self.note = "已启用离线皮肤：Steve"
        self.closed = False

    def close(self) -> None:
        self.closed = True


def launch_tests(root: Path) -> None:
    game_root = make_version(root / "game")
    cfg = Config(game_dir=str(game_root), player_name="Steve")
    cfg.version_isolation = False
    config_module = sys.modules["darkitten.config"]
    original_config_path = config_module.CONFIG_PATH
    config_module.CONFIG_PATH = root / "launch-config.json"
    try:
        java = JavaInfo(path="C:/fake/java.exe", version="21", major=21)
        runtime = FakeRuntime()
        with mock.patch.object(launch, "_check_files", lambda *_a, **_k: []):
            with mock.patch.object(
                offline,
                "prepare_offline_runtime",
                return_value=runtime,
            ) as prepare:
                plan = launch.prepare_launch(
                    game_root,
                    "Demo",
                    cfg,
                    account=Account.offline("Steve"),
                    java_info=java,
                )
        check("launch_prepare_called", prepare.call_count == 1)
        check("launch_cleanup_attached", plan.cleanup is runtime)
        main_index = plan.command.index("net.minecraft.client.main.Main")
        agent_index = plan.command.index(runtime.jvm_args[0])
        side_index = plan.command.index(runtime.jvm_args[1])
        check(
            "launch_agent_before_main_class",
            agent_index < main_index and side_index < main_index,
            f"agent={agent_index} main={main_index}",
        )
        check(
            "launch_agent_after_classpath",
            "-cp" in plan.command[:agent_index],
        )
        check(
            "launch_warning_note",
            any("已启用离线皮肤" in line for line in plan.warnings),
        )
        plan.close()
        check("launch_plan_close", runtime.closed)

        msa = Account(
            kind="msa",
            name="DevCat",
            uuid="0123456789abcdef0123456789abcdef",
            mc_access_token="token",
        )
        with mock.patch.object(launch, "_check_files", lambda *_a, **_k: []):
            with mock.patch.object(offline, "prepare_offline_runtime") as prepare_msa:
                plan_msa = launch.prepare_launch(
                    game_root,
                    "Demo",
                    cfg,
                    account=msa,
                    java_info=java,
                )
        check("launch_msa_skips_offline_skin", prepare_msa.call_count == 0)
        check(
            "launch_msa_has_no_agent",
            not any(arg.startswith("-javaagent:") for arg in plan_msa.command),
        )

        with mock.patch.object(launch, "_check_files", lambda *_a, **_k: []):
            with mock.patch.object(
                offline,
                "prepare_offline_runtime",
                side_effect=RuntimeError("boom"),
            ):
                plan_fail = launch.prepare_launch(
                    game_root,
                    "Demo",
                    cfg,
                    account=Account.offline("Steve"),
                    java_info=java,
                )
        check(
            "launch_offline_failure_warns",
            any("离线皮肤暂不可用：boom" in line for line in plan_fail.warnings),
        )
        resolved = launch.resolve_version(game_root, "Demo")
        expected_native = "-Djava.library.path=" + str(
            launch.natives_dir(game_root, resolved)
        )
        check(
            "launch_offline_failure_continues",
            not any(arg.startswith("-javaagent:") for arg in plan_fail.command)
            and plan_fail.command[
                plan_fail.command.index("net.minecraft.client.main.Main") - 1
            ]
            == expected_native,
        )
    finally:
        config_module.CONFIG_PATH = original_config_path


def launch_worker_cancel_cleanup_test(app: QApplication) -> None:
    import darkitten.workers as workers

    class Plan:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    plan = Plan()
    holder: dict[str, object] = {}

    def fake_prepare(*_args, **_kwargs):
        holder["worker"].cancel_event.set()  # type: ignore[attr-defined]
        return plan

    original = workers.prepare_launch
    workers.prepare_launch = fake_prepare
    try:
        worker = workers.LaunchWorker(
            Path("."), "demo", Config(), [], Account.offline("Tester")
        )
        holder["worker"] = worker
        state: list[str] = []
        loop = QEventLoop()
        guard = QTimer()
        guard.setSingleShot(True)
        guard.timeout.connect(loop.quit)
        guard.start(5000)
        worker.cancelled.connect(lambda: (state.append("cancelled"), loop.quit()))
        worker.ready.connect(lambda *_: (state.append("ready"), loop.quit()))
        worker.failed.connect(lambda *_: (state.append("failed"), loop.quit()))
        worker.start()
        loop.exec()
        guard.stop()
        check(
            "launch_worker_cancel_closes_plan",
            state == ["cancelled"] and plan.closed,
            f"state={state} closed={plan.closed}",
        )
    finally:
        workers.prepare_launch = original


def game_process_cleanup_test(app: QApplication) -> None:
    class Plan:
        def __init__(self) -> None:
            self.command = [
                sys.executable,
                "-c",
                "import time; time.sleep(0.2)",
            ]
            self.cwd = str(PROJECT)
            self.closed = False

        def close(self) -> None:
            self.closed = True

    plan = Plan()
    process = GameProcess(plan)
    loop = QEventLoop()
    guard = QTimer()
    guard.setSingleShot(True)
    guard.timeout.connect(loop.quit)
    guard.start(8000)
    process.game_finished.connect(lambda _code: loop.quit())
    process.start()
    loop.exec()
    guard.stop()
    check("game_exit_closes_plan", plan.closed and not process.running)


def main() -> int:
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        server_tests(root)
        runtime_tests(root)
        launch_tests(root)
        launch_worker_cancel_cleanup_test(app)
        game_process_cleanup_test(app)
    print(f"\noffline_skin: {PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
