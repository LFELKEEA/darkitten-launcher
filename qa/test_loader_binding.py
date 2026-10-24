from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from PySide6.QtWidgets import QApplication, QCheckBox

import stellar.config as config_module
from stellar.config import Config
from stellar.ui.main_window import MainWindow
from stellar.ui.pages.loaders import LoaderCard
from stellar.ui.version_settings_dialog import VersionSettingsDialog
from stellar.versions import game_versions, loader_profiles, scan_versions

VANILLA = "1.21.1"
FABRIC = "fabric-loader-0.16.5-1.21.1"
PACK = "MyPack"


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def build_game(root: Path) -> None:
    write_json(
        root / "versions" / VANILLA / f"{VANILLA}.json",
        {
            "id": VANILLA,
            "type": "release",
            "mainClass": "net.minecraft.client.main.Main",
            "releaseTime": "2024-08-08T14:00:00+00:00",
            "javaVersion": {"majorVersion": 21},
            "assets": "17",
            "assetIndex": {"id": "17"},
            "libraries": [],
        },
    )
    write_json(
        root / "versions" / FABRIC / f"{FABRIC}.json",
        {
            "id": FABRIC,
            "inheritsFrom": VANILLA,
            "type": "release",
            "mainClass": "net.fabricmc.loader.impl.launch.knot.KnotClient",
            "releaseTime": "2024-09-01T00:00:00+00:00",
            "libraries": [
                {"name": "net.fabricmc:fabric-loader:0.16.5"},
                {"name": "net.fabricmc:intermediary:1.21.1"},
            ],
        },
    )
    write_json(
        root / "versions" / PACK / f"{PACK}.json",
        {
            "id": PACK,
            "inheritsFrom": FABRIC,
            "type": "release",
            "mainClass": "net.fabricmc.loader.impl.launch.knot.KnotClient",
            "releaseTime": "2024-09-02T00:00:00+00:00",
            "libraries": [],
        },
    )


def main() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    ok = True

    def check(name: str, condition: bool, detail: str = "") -> None:
        nonlocal ok
        print(f"{name}: {'OK' if condition else 'FAIL'} {detail}".rstrip())
        ok &= bool(condition)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        game = root / "minecraft"
        build_game(game)
        config_module.CONFIG_PATH = root / "config.json"
        config = Config(game_dir=str(game))

        scans = scan_versions(game)
        check("loader_profile_scan", len(loader_profiles(scans)) == 1)
        check("game_version_scan", len(game_versions(scans)) == 2)
        check(
            "pack_not_loader_profile",
            next(v for v in scans if v.id == PACK).is_loader_profile is False,
        )

        window = MainWindow(config)
        try:
            check(
                "home_excludes_loader_profiles",
                set(window.home_page._version_rows) == {VANILLA, PACK},
            )
            check(
                "auto_bind_base",
                window.config.loader_for(VANILLA) == FABRIC,
            )
            check("migration_flag_saved", window.config.loaders_migrated is True)
            check(
                "pack_not_auto_bound",
                window.config.loader_for(PACK) == "",
            )
            profile_id, instance_id = window.launch_profile(VANILLA)
            check(
                "launch_uses_profile_and_instance",
                profile_id == FABRIC and instance_id == VANILLA,
                f"{profile_id}|{instance_id}",
            )
            pack_profile, pack_instance = window.launch_profile(PACK)
            check(
                "pack_keeps_builtin_loader",
                pack_profile == PACK and pack_instance == PACK,
                f"{pack_profile}|{pack_instance}",
            )
            check(
                "mode_note_builtin_loader",
                "Fabric" in window._launch_mode_note(PACK, PACK),
                window._launch_mode_note(PACK, PACK),
            )
            check(
                "loader_page_card",
                len(window.loader_page.findChildren(LoaderCard)) == 1,
            )
            check(
                "version_settings_offers_loader",
                VersionSettingsDialog(window, VANILLA).mode_combo.count() == 2,
            )
            check(
                "compatible_games_for_profile",
                [g.id for g in window.compatible_game_versions(FABRIC)]
                == [VANILLA],
            )

            window.bind_loader(VANILLA, "")
            check("unbind_switches_vanilla", window.launch_profile(VANILLA) == (VANILLA, VANILLA))
            window.refresh_versions()
            check(
                "unbind_survives_refresh",
                window.config.loader_for(VANILLA) == "",
            )
            window.bind_loader(VANILLA, FABRIC)
            check("rebind_loader", window.launch_profile(VANILLA) == (FABRIC, VANILLA))

            # 删除加载器后，绑定必须一起清掉，不能留下悬空配置。
            window.config.version_loaders[PACK] = FABRIC
            shutil.rmtree(game / "versions" / FABRIC)
            window._on_version_deleted(FABRIC, True, "")
            check(
                "delete_clears_bindings",
                FABRIC not in window.config.version_loaders.values(),
            )
        finally:
            window.deleteLater()
            app.processEvents()

    print("RESULT:", "PASS" if ok else "FAIL")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
