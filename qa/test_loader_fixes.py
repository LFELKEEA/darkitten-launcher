from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stellar.loaders import (
    _find_loader_version,
    _installer_urls,
    _short_forge_version,
)
from stellar.config import Config
from stellar.java import JavaInfo
from stellar.launch import _ignore_client_jar, prepare_launch
from stellar.versions import detect_loader, scan_versions


def write_version(root: Path, version_id: str, data: dict) -> None:
    folder = root / "versions" / version_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{version_id}.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )


def main() -> int:
    ok = True

    forge_urls, forge_name = _installer_urls("forge", "1.20.1", "47.1.106")
    forge_ok = (
        forge_name == "forge-1.20.1-47.1.106-installer.jar"
        and any(
            "net/minecraftforge/forge/1.20.1-47.1.106/" in url
            for url in forge_urls
        )
    )
    print("forge_url:", "OK" if forge_ok else "FAIL")
    ok &= forge_ok

    forge_full_urls, forge_full_name = _installer_urls(
        "forge", "1.20.1", "1.20.1-47.1.106"
    )
    forge_full_ok = forge_full_name == forge_name and forge_full_urls == forge_urls
    print("forge_full_version_normalized:", "OK" if forge_full_ok else "FAIL")
    ok &= forge_full_ok

    neo_legacy_urls, neo_legacy_name = _installer_urls(
        "neoforge", "1.20.1", "1.20.1-47.1.106"
    )
    neo_legacy_ok = (
        neo_legacy_name == "forge-1.20.1-47.1.106-installer.jar"
        and any(
            "net/neoforged/forge/1.20.1-47.1.106/" in url
            for url in neo_legacy_urls
        )
        and not any("neoforge-1.20.1-47.1.106" in url for url in neo_legacy_urls)
    )
    print("neoforge_1201_url:", "OK" if neo_legacy_ok else "FAIL")
    ok &= neo_legacy_ok

    neo_urls, neo_name = _installer_urls("neoforge", "1.21.1", "21.1.80")
    neo_ok = (
        neo_name == "neoforge-21.1.80-installer.jar"
        and any(
            "net/neoforged/neoforge/21.1.80/neoforge-21.1.80-installer.jar" in url
            for url in neo_urls
        )
    )
    print("neoforge_modern_url:", "OK" if neo_ok else "FAIL")
    ok &= neo_ok

    short_ok = (
        _short_forge_version("1.20.1", "1.20.1-47.1.106") == "47.1.106"
        and _short_forge_version("1.7.10", "10.13.4.1614-1.7.10")
        == "10.13.4.1614-1.7.10"
    )
    print("short_forge_version:", "OK" if short_ok else "FAIL")
    ok &= short_ok

    ignore_args = _ignore_client_jar(
        [
            "-DignoreList=client-extra,neoforge-21.1.264.jar",
            "-DignoreList=client-extra,other.jar",
        ],
        "1.21.1.jar",
    )
    ignore_ok = (
        ignore_args[0]
        == "-DignoreList=client-extra,neoforge-21.1.264.jar,1.21.1.jar"
        and ignore_args[1] == "-DignoreList=client-extra,other.jar,1.21.1.jar"
        and _ignore_client_jar(ignore_args, "1.21.1.jar") == ignore_args
        and _ignore_client_jar(["-p", "x"], "1.21.1.jar") == ["-p", "x"]
    )
    print("ignore_client_jar:", "OK" if ignore_ok else "FAIL")
    ok &= ignore_ok

    neo_loaders = detect_loader(
        ["1.20.1", "1.20.1-forge-47.1.106"],
        "cpw.mods.bootstraplauncher.BootstrapLauncher",
        [{"name": "net.neoforged:neoforge:47.1.106"}],
    )
    forge_loader = detect_loader(
        ["1.20.1", "1.20.1-forge-47.2.0"],
        "cpw.mods.bootstraplauncher.BootstrapLauncher",
        [{"name": "net.minecraftforge:forge:1.20.1-47.2.0"}],
    )
    detect_ok = neo_loaders == "NeoForge" and forge_loader == "Forge"
    print("detect_loader:", "OK" if detect_ok else "FAIL")
    ok &= detect_ok

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        write_version(root, "1.20.1", {"id": "1.20.1", "type": "release"})
        write_version(
            root,
            "1.20.1-forge-47.1.106",
            {
                "id": "1.20.1-forge-47.1.106",
                "inheritsFrom": "1.20.1",
                "type": "release",
                "mainClass": "cpw.mods.bootstraplauncher.BootstrapLauncher",
                "libraries": [{"name": "net.neoforged:neoforge:47.1.106"}],
            },
        )
        summaries = {item.id: item for item in scan_versions(root)}
        scan_ok = summaries["1.20.1-forge-47.1.106"].loader == "NeoForge"
        print("scan_neoforge_1201:", "OK" if scan_ok else "FAIL")
        ok &= scan_ok

        fresh = _find_loader_version(
            root, "1.20.1", "neoforge", "1.20.1-47.1.106", set()
        )
        reinstall = _find_loader_version(
            root,
            "1.20.1",
            "neoforge",
            "1.20.1-47.1.106",
            {"1.20.1-forge-47.1.106"},
        )
        short = _find_loader_version(
            root,
            "1.20.1",
            "neoforge",
            "47.1.106",
            {"1.20.1-forge-47.1.106"},
        )
        lookup_ok = (
            fresh == "1.20.1-forge-47.1.106"
            and reinstall == fresh
            and short == fresh
        )
        print("find_generated_version:", "OK" if lookup_ok else "FAIL")
        ok &= lookup_ok

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        (root / "versions" / "1.21.1").mkdir(parents=True)
        (root / "versions" / "1.21.1" / "1.21.1.jar").write_bytes(b"jar")
        (root / "assets" / "indexes").mkdir(parents=True)
        (root / "assets" / "indexes" / "1.21.1.json").write_text(
            '{"objects": {}}', encoding="utf-8"
        )
        jvm_args = ["-DignoreList=client-extra,${version_name}.jar"]
        write_version(
            root,
            "1.21.1",
            {
                "id": "1.21.1",
                "type": "release",
                "mainClass": "net.minecraft.client.main.Main",
                "assetIndex": {"id": "1.21.1"},
                "arguments": {
                    "game": ["--version", "${version_name}"],
                    "jvm": jvm_args,
                },
            },
        )
        write_version(
            root,
            "neoforge-21.1.264",
            {
                "id": "neoforge-21.1.264",
                "inheritsFrom": "1.21.1",
                "type": "release",
                "mainClass": "cpw.mods.bootstraplauncher.BootstrapLauncher",
                "arguments": {
                    "game": ["--version", "${version_name}"],
                    "jvm": jvm_args,
                },
            },
        )
        plan = prepare_launch(
            root,
            "neoforge-21.1.264",
            Config(),
            java_info=JavaInfo(path="java", major=21),
        )
        ignores = [a for a in plan.command if a.startswith("-DignoreList=")]
        launch_ok = bool(ignores) and all("1.21.1.jar" in item for item in ignores)
        print("launch_ignorelist:", "OK" if launch_ok else "FAIL")
        ok &= launch_ok

    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
