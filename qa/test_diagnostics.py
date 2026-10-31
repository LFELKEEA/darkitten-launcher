from __future__ import annotations

import json
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from darkitten import diagnostics  # noqa: E402
from darkitten.config import APP_VERSION, Config  # noqa: E402
from darkitten.java import JavaInfo  # noqa: E402

CHECKS: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name} FAILED {detail}")
    CHECKS.append(name)
    print(f"{name}: OK")


def main() -> None:
    temp = Path(tempfile.mkdtemp(prefix="dkl-diag-"))
    game = temp / "game"
    version_dir = game / "versions" / "1.21.11"
    (version_dir / "mods").mkdir(parents=True)
    (version_dir / "logs").mkdir()
    (version_dir / "crash-reports").mkdir()
    (version_dir / "1.21.11.jar").write_bytes(b"jar")
    (version_dir / "1.21.11.json").write_text(
        json.dumps(
            {
                "id": "1.21.11",
                "type": "release",
                "mainClass": "net.minecraft.client.main.Main",
                "assets": "1.21",
                "assetIndex": {"id": "1.21", "url": "http://example.invalid/a.json"},
                "javaVersion": {"majorVersion": 21},
            }
        ),
        encoding="utf-8",
    )
    with zipfile.ZipFile(version_dir / "mods" / "sodium.jar", "w") as archive:
        archive.writestr(
            "fabric.mod.json",
            json.dumps({"id": "sodium", "name": "Sodium", "version": "1.0.0"}),
        )
    (version_dir / "logs" / "latest.log").write_text(
        "[12:00:00] game started\nAuthorization: Bearer SECRET-GAME-TOKEN\n",
        encoding="utf-8",
    )
    (version_dir / "crash-reports" / "crash-1.txt").write_text(
        "crash report access_token=\"SECRET-CRASH\"\n", encoding="utf-8"
    )

    diagnostics.APP_DIR = temp
    log_file = temp / "logs" / "launcher.log"
    log_file.parent.mkdir(parents=True)
    diagnostics.LOG_FILE = log_file
    log_file.write_text(
        "启动器日志\naccess_token=SECRET-LAUNCHER\nsakura_token: SECRET-SAKURA\n"
        'clientToken="SECRET-CLIENT"\n正常内容\n',
        encoding="utf-8",
    )

    config = Config(
        game_dir=str(game),
        last_version="1.21.11",
        memory_mb=6144,
        download_source="auto",
        account={"access_token": "SECRET-ACCOUNT"},
        sakura_token="SECRET-SAKURA",
    )
    java = JavaInfo(path="C:/Java/bin/java.exe", version="21.0.4", major=21, arch="x64")

    summary, info = diagnostics.build_summary(
        config, [java], "1.21.11", "fabric-loader-0.19.5-1.21.11"
    )
    check("summary-version", f"启动器版本：{APP_VERSION}" in summary, summary[:200])
    check("summary-java", "21.0.4" in summary)
    check("summary-mod", "sodium.jar" in summary and "Sodium" in summary)
    check("summary-source", "自动" in info["source_health"] or "官方" in info["source_health"])
    check("summary-no-token", "SECRET" not in summary, summary)

    path, summary2 = diagnostics.create_bundle(
        config, [java], "1.21.11", "fabric-loader-0.19.5-1.21.11"
    )
    check("bundle-created", path.is_file() and path.suffix == ".zip")
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        launcher_log = archive.read("launcher.log").decode("utf-8")
        game_log = archive.read("game-latest.log").decode("utf-8")
        crash = archive.read("crash-reports/crash-1.txt").decode("utf-8")
        system = json.loads(archive.read("system.json").decode("utf-8"))
        config_safe = json.loads(archive.read("config-safe.json").decode("utf-8"))
        summary_zip = archive.read("summary.txt").decode("utf-8")
        version_file = archive.read("version.txt").decode("utf-8")
    check("bundle-summary", "summary.txt" in names and "诊断信息" in summary_zip)
    check("bundle-system", system["app"] == "Darkitten Launcher")
    check("bundle-launcher-log", "正常内容" in launcher_log)
    check("bundle-game-log", "game started" in game_log)
    check("bundle-crash", "crash report" in crash)
    check("bundle-redacts-launcher", "SECRET-LAUNCHER" not in launcher_log, launcher_log)
    check("bundle-redacts-sakura", "SECRET-SAKURA" not in launcher_log, launcher_log)
    check("bundle-redacts-client-token", "SECRET-CLIENT" not in launcher_log, launcher_log)
    check("bundle-redacts-game", "SECRET-GAME-TOKEN" not in game_log, game_log)
    check("bundle-redacts-crash", "SECRET-CRASH" not in crash, crash)
    check("bundle-no-account", "access_token" not in config_safe)
    check("bundle-no-sakura", "sakura_token" not in config_safe)
    check("bundle-version-file", "1.21.11" in version_file)
    check("bundle-summary-match", summary2.startswith("Darkitten Launcher 诊断信息"))

    # 日志尾部截断：超长日志只保留尾部。
    big = temp / "big.log"
    big.write_text("A" * 1000 + "TAIL-MARKER", encoding="utf-8")
    tail = diagnostics._read_tail(big, limit=64)
    check("log-tail-limit", len(tail) <= 64 and "TAIL-MARKER" in tail, str(len(tail)))

    check(
        "redact-plain",
        "SECRET" not in diagnostics.redact("token=SECRET and access_token: SECRET2"),
    )

    print(f"RESULT: PASS ({len(CHECKS)} checks)")


if __name__ == "__main__":
    main()
