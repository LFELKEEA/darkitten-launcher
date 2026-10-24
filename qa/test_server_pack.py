from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import zipfile
from dataclasses import asdict
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

import darkitten.servers as servers_module
from darkitten.server_pack import (
    SERVER_PACK_MARKER,
    ServerPackCancelled,
    ServerPackError,
    export_server,
    import_server_pack,
    inspect_server_pack,
    looks_like_server_pack,
    suggest_server_folder,
)
from darkitten.servers import ServerInfo


def check(label: str, value: bool, detail: str = "") -> bool:
    print(f"{label}: {'OK' if value else 'FAIL'}" + (f" ({detail})" if detail else ""))
    return value


def make_jar(path: Path, mc_version: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "version.json",
            json.dumps({"id": mc_version, "name": mc_version}),
        )
        zf.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def make_server(root: Path, folder: str = "cat-server") -> ServerInfo:
    path = root / "servers" / folder
    path.mkdir(parents=True, exist_ok=True)
    make_jar(path / "server.jar", "1.21.1")
    (path / "eula.txt").write_text("eula=true\n", encoding="utf-8")
    (path / "server.properties").write_text(
        "\n".join(
            [
                "#Minecraft server properties",
                "level-name=world",
                "server-port=25566",
                "online-mode=false",
                "motd=Cat Server",
                "max-players=20",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (path / "world" / "playerdata").mkdir(parents=True, exist_ok=True)
    (path / "world" / "region").mkdir(parents=True, exist_ok=True)
    (path / "world" / "level.dat").write_bytes(b"LEVEL-DATA")
    (path / "world" / "playerdata" / "11111111-2222-3333-4444-555555555555.dat").write_bytes(
        b"PLAYER-DATA"
    )
    (path / "world" / "region" / "r.0.0.mca").write_bytes(b"REGION-DATA")
    (path / "world" / "session.lock").write_bytes(b"lock")
    write_json(
        path / "whitelist.json",
        [{"uuid": "1111", "name": "Alice"}, {"uuid": "2222", "name": "Bob"}],
    )
    write_json(path / "ops.json", [{"uuid": "1111", "name": "Alice", "level": 4}])
    write_json(path / "banned-players.json", [{"uuid": "3333", "name": "Grief"}])
    write_json(path / "banned-ips.json", [{"ip": "203.0.113.9"}])
    write_json(
        path / "usercache.json",
        [{"name": "Alice", "uuid": "1111"}, {"name": "Bob", "uuid": "2222"}],
    )
    (path / "plugins").mkdir(parents=True, exist_ok=True)
    (path / "plugins" / "Demo.jar").write_bytes(b"PLUGIN-JAR")
    (path / "plugins" / "Demo").mkdir(parents=True, exist_ok=True)
    (path / "plugins" / "Demo" / "config.yml").write_text(
        "enabled: true\n", encoding="utf-8"
    )
    (path / "config").mkdir(parents=True, exist_ok=True)
    (path / "config" / "cat.yml").write_text("meow: 1\n", encoding="utf-8")
    (path / "logs").mkdir(parents=True, exist_ok=True)
    (path / "logs" / "latest.log").write_text("secret log\n", encoding="utf-8")
    (path / "crash-reports").mkdir(parents=True, exist_ok=True)
    (path / "crash-reports" / "crash.txt").write_text("boom\n", encoding="utf-8")
    (path / "cache").mkdir(parents=True, exist_ok=True)
    (path / "cache" / "tmp.bin").write_bytes(b"CACHE")
    (path / "orphan.tmp").write_bytes(b"TEMP")

    info = ServerInfo(
        folder=folder,
        name="猫猫生存服",
        kind="paper",
        mc_version="1.21.1",
        loader_version="",
        memory_mb=4096,
        port=25566,
        online_mode=False,
        eula=True,
        installed=True,
        launch_kind="jar",
        launch_target="server.jar",
        paper_build=123,
        created_at="2026-10-24 10:00:00",
    )
    write_json(path / "server.json", asdict(info))
    return info


def zip_names(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as zf:
        return zf.namelist()


def main() -> None:
    ok = True
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        servers_module.SERVERS_DIR = root / "servers"
        info = make_server(root)
        source = Path(info.path)

        pack_path = root / "out" / "猫猫服务端.zip"
        export_server(info, pack_path)
        ok &= check("zip_created", pack_path.is_file())
        names = zip_names(pack_path)
        prefix = "猫猫生存服/"
        ok &= check("zip_prefix", all(name.startswith(prefix) for name in names), names[:3])
        ok &= check("zip_marker", prefix + SERVER_PACK_MARKER in names)
        ok &= check("zip_world", prefix + "world/level.dat" in names)
        ok &= check(
            "zip_playerdata",
            prefix + "world/playerdata/11111111-2222-3333-4444-555555555555.dat"
            in names,
        )
        ok &= check("zip_whitelist", prefix + "whitelist.json" in names)
        ok &= check("zip_ops", prefix + "ops.json" in names)
        ok &= check("zip_banned", prefix + "banned-players.json" in names)
        ok &= check("zip_plugin_config", prefix + "plugins/Demo/config.yml" in names)
        ok &= check(
            "logs_excluded",
            not any("/logs/" in name or name.startswith(prefix + "logs/") for name in names),
            str([name for name in names if "logs" in name]),
        )
        ok &= check(
            "cache_excluded",
            not any("/cache/" in name or name.startswith(prefix + "cache/") for name in names),
        )
        ok &= check("session_lock_excluded", prefix + "world/session.lock" not in names)
        ok &= check("tmp_excluded", prefix + "orphan.tmp" not in names)

        with zipfile.ZipFile(pack_path) as zf:
            manifest = json.loads(zf.read(prefix + SERVER_PACK_MARKER))
        ok &= check("manifest_format", manifest.get("format") == "dkl-server")
        ok &= check("manifest_name", manifest.get("name") == "猫猫生存服")
        ok &= check("manifest_kind", manifest.get("kind") == "paper")
        ok &= check("manifest_mc", manifest.get("mc_version") == "1.21.1")
        ok &= check("manifest_worlds", "world" in manifest.get("worlds", []), manifest.get("worlds"))
        ok &= check("manifest_players", manifest.get("players") == 1, str(manifest.get("players")))
        permissions = manifest.get("permissions") or {}
        ok &= check("manifest_whitelist", permissions.get("whitelist") == 2, str(permissions))
        ok &= check("manifest_ops", permissions.get("ops") == 1, str(permissions))
        ok &= check(
            "manifest_banned",
            permissions.get("banned_players") == 1 and permissions.get("banned_ips") == 1,
            str(permissions),
        )

        inspected = inspect_server_pack(pack_path)
        ok &= check("inspect_zip", inspected.is_zip and inspected.installed)
        ok &= check("inspect_name", inspected.name == "猫猫生存服", inspected.name)
        ok &= check("inspect_type", inspected.type_name == "Paper", inspected.type_name)
        ok &= check("inspect_worlds", inspected.worlds == ["world"], str(inspected.worlds))
        ok &= check("inspect_players", inspected.players == 1, str(inspected.players))
        ok &= check(
            "inspect_permissions",
            inspected.has_whitelist and inspected.has_ops and inspected.has_banned,
        )
        ok &= check("looks_zip", looks_like_server_pack(pack_path))
        ok &= check("looks_folder", looks_like_server_pack(source))

        target_root = root / "imported"
        imported = import_server_pack(
            pack_path, name="导入猫猫服", servers_dir=target_root
        )
        imported_path = target_root / imported
        ok &= check("import_folder_created", imported_path.is_dir(), imported)
        ok &= check(
            "import_world",
            (imported_path / "world" / "level.dat").read_bytes() == b"LEVEL-DATA",
        )
        ok &= check(
            "import_playerdata",
            (
                imported_path
                / "world"
                / "playerdata"
                / "11111111-2222-3333-4444-555555555555.dat"
            ).read_bytes()
            == b"PLAYER-DATA",
        )
        ok &= check(
            "import_region",
            (imported_path / "world" / "region" / "r.0.0.mca").read_bytes()
            == b"REGION-DATA",
        )
        ok &= check(
            "import_permissions",
            (imported_path / "whitelist.json").is_file()
            and (imported_path / "ops.json").is_file()
            and (imported_path / "banned-players.json").is_file()
            and (imported_path / "banned-ips.json").is_file(),
        )
        ok &= check(
            "import_plugin_config",
            (imported_path / "plugins" / "Demo" / "config.yml").is_file(),
        )
        properties = (imported_path / "server.properties").read_text(encoding="utf-8")
        ok &= check("import_properties", "server-port=25566" in properties and "level-name=world" in properties)
        ok &= check("import_no_logs", not (imported_path / "logs").exists())
        ok &= check("import_no_session_lock", not (imported_path / "world" / "session.lock").exists())
        data = json.loads((imported_path / "server.json").read_text(encoding="utf-8"))
        ok &= check("import_json_name", data.get("name") == "导入猫猫服", data.get("name"))
        ok &= check("import_json_folder", data.get("folder") == imported, data.get("folder"))
        ok &= check("import_json_port", data.get("port") == 25566, str(data.get("port")))
        ok &= check("import_json_online", data.get("online_mode") is False)
        ok &= check("import_json_java", data.get("java_path") == "")
        ok &= check("import_json_installed", data.get("installed") is True)
        ok &= check("import_json_kind", data.get("kind") == "paper", data.get("kind"))

        second = import_server_pack(pack_path, name="导入猫猫服", servers_dir=target_root)
        ok &= check("unique_folder", second != imported and (target_root / second).is_dir(), second)

        folder_target = root / "folder-out" / "猫猫生存服"
        export_server(info, folder_target, as_zip=False)
        ok &= check("folder_created", folder_target.is_dir())
        ok &= check("folder_marker", (folder_target / SERVER_PACK_MARKER).is_file())
        ok &= check(
            "folder_world",
            (folder_target / "world" / "level.dat").read_bytes() == b"LEVEL-DATA",
        )
        ok &= check("folder_no_logs", not (folder_target / "logs").exists())
        folder_info = inspect_server_pack(folder_target)
        ok &= check("folder_inspect", folder_info.name == "猫猫生存服" and folder_info.players == 1)
        ok &= check(
            "folder_import",
            import_server_pack(
                folder_target, name="文件夹猫猫", servers_dir=root / "folder-imported"
            )
            == "文件夹猫猫",
        )
        folder_imported = root / "folder-imported" / "文件夹猫猫"
        ok &= check(
            "folder_import_world",
            (folder_imported / "world" / "level.dat").is_file(),
            str(folder_imported),
        )
        ok &= check(
            "folder_import_ops",
            (folder_imported / "ops.json").is_file(),
        )

        raw = root / "servers" / "raw-server"
        raw.mkdir(parents=True)
        make_jar(raw / "server.jar", "1.20.1")
        (raw / "server.properties").write_text(
            "server-port=25570\nonline-mode=true\nlevel-name=rawworld\n",
            encoding="utf-8",
        )
        (raw / "rawworld").mkdir()
        (raw / "rawworld" / "level.dat").write_bytes(b"RAW")
        raw_info = inspect_server_pack(raw)
        ok &= check("raw_detect", raw.is_dir() and raw_info.mc_version == "1.20.1", raw_info.mc_version)
        ok &= check("raw_world", raw_info.worlds == ["rawworld"], str(raw_info.worlds))
        ok &= check("raw_installed", raw_info.installed)
        raw_imported = import_server_pack(
            raw, name="原版猫猫", servers_dir=root / "raw-imported"
        )
        raw_data = json.loads(
            (root / "raw-imported" / raw_imported / "server.json").read_text(
                encoding="utf-8"
            )
        )
        ok &= check("raw_import_kind", raw_data.get("kind") == "paper", str(raw_data.get("kind")))
        ok &= check("raw_import_online", raw_data.get("online_mode") is True)
        ok &= check("raw_import_mc", raw_data.get("mc_version") == "1.20.1")
        ok &= check("raw_import_installed", raw_data.get("installed") is True)

        suggestion = suggest_server_folder(pack_path, root / "imported")
        ok &= check("suggest_unique", suggestion not in (imported, second), suggestion)

        bad = root / "evil.zip"
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr("pack/server.json", json.dumps({"kind": "paper", "mc_version": "1.21"}))
            zf.writestr("pack/server.properties", "server-port=25565\n")
            zf.writestr("pack/../../evil.txt", "evil")
        evil_root = root / "evil-imported"
        try:
            import_server_pack(bad, servers_dir=evil_root)
        except ServerPackError as exc:
            ok &= check("zip_slip_rejected", True, str(exc))
        else:
            ok &= check("zip_slip_rejected", False)
        ok &= check(
            "zip_slip_no_leftover",
            not evil_root.exists() or not any(evil_root.iterdir()),
            str(list(evil_root.glob("*")) if evil_root.exists() else []),
        )

        cancel = threading.Event()
        cancel.set()
        cancelled_path = root / "cancelled.zip"
        try:
            export_server(info, cancelled_path, cancel_event=cancel)
        except ServerPackCancelled:
            ok &= check("cancel_raises", True)
        else:
            ok &= check("cancel_raises", False)
        ok &= check("cancel_no_file", not cancelled_path.exists())
        ok &= check("cancel_no_part", not cancelled_path.with_name(cancelled_path.name + ".part").exists())

        version_zip = root / "version.zip"
        with zipfile.ZipFile(version_zip, "w") as zf:
            zf.writestr("versions/1.21.1/1.21.1.json", "{}")
            zf.writestr("1.21.1.jar", "jar")
        ok &= check("version_zip_not_server", not looks_like_server_pack(version_zip))

    print("RESULT:", "PASS" if ok else "FAIL")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
