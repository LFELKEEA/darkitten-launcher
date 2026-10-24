from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from darkitten import config as config_module
from darkitten import optimize as optimize_module
from darkitten.config import Config
from darkitten.mods import write_mod_meta
from darkitten.room_sync import (
    ManifestMod,
    RoomManifest,
    decode_manifest,
    encode_manifest,
)
from darkitten.sakura import encode_room_code
from darkitten.social import SocialProfile, encode_invite, load_store, parse_invite
from darkitten.ui import main_window as main_window_module
from darkitten.ui import theme
from darkitten.ui.main_window import MainWindow
from darkitten.ui.mod_sync_dialog import ModSyncDialog
from darkitten.ui.optimize_dialog import OptimizeDialog
from darkitten.ui.pages.home import QuickAction
from darkitten.ui.version_settings_dialog import VersionSettingsDialog
from darkitten.versions import VersionSummary


def wait_until(predicate, timeout_ms: int = 12000) -> bool:
    if predicate():
        return True
    loop = QEventLoop()
    timer = QTimer()
    timer.setInterval(25)
    timer.timeout.connect(lambda: loop.quit() if predicate() else None)
    timer.start()
    guard = QTimer()
    guard.setSingleShot(True)
    guard.timeout.connect(loop.quit)
    guard.start(timeout_ms)
    loop.exec()
    timer.stop()
    guard.stop()
    return bool(predicate())


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei UI", 9))

    root = Path(tempfile.mkdtemp(prefix="dkl_opt_ui_"))
    game_dir = root / "game"
    version_id = "1.20.1"
    version_dir = game_dir / "versions" / version_id
    version_dir.mkdir(parents=True)
    (version_dir / f"{version_id}.json").write_text(
        json.dumps(
            {
                "id": version_id,
                "type": "release",
                "mainClass": "net.minecraft.client.main.Main",
            }
        ),
        encoding="utf-8",
    )
    mods_dir = version_dir / "mods"
    mods_dir.mkdir()
    present_bytes = b"present-mod-bytes"
    (mods_dir / "present.jar").write_bytes(present_bytes)
    write_mod_meta(
        mods_dir,
        "present.jar",
        {
            "title": "Present Mod",
            "source": "modrinth",
            "project_id": "present-project",
            "version_id": "present-version",
            "file_id": "present-version",
            "slug": "present-mod",
        },
    )

    config_module.CONFIG_PATH = root / "config.json"
    social_path = root / "social.json"
    cfg = Config(
        game_dir=str(game_dir),
        memory_mb=4096,
        version_isolation=True,
        update_auto_check=False,
        last_version=version_id,
    )
    cfg.save = lambda: None
    theme.set_current(cfg.theme)
    app.setStyleSheet(theme.stylesheet(cfg.theme))

    original_load_store = main_window_module.load_store
    original_ensure_java = MainWindow.ensure_java_detected
    original_system_memory = optimize_module.system_memory_mb
    original_detect_gpus = optimize_module.detect_gpus
    main_window_module.load_store = lambda: load_store(social_path)
    MainWindow.ensure_java_detected = lambda self: None
    optimize_module.system_memory_mb = lambda: 16384
    optimize_module.detect_gpus = lambda refresh=False: [
        optimize_module.GpuInfo("Test GPU", 8192, "nvidia")
    ]

    ok = True

    def check(name: str, condition: bool) -> None:
        nonlocal ok
        print(f"{name}: {'OK' if condition else 'FAIL'}")
        ok &= bool(condition)

    window = None
    optimize_dialog = None
    sync_dialog = None
    settings_dialog = None
    loader_dialog = None
    try:
        window = MainWindow(cfg)
        window.config.save = lambda: None
        window._feedback_checked = True
        window.show()
        app.processEvents()

        home = window.home_page
        quick_titles = [
            label.text()
            for action in home.findChildren(QuickAction)
            for label in action.findChildren(QLabel)
        ]
        check("home_quick_optimize", "游戏优化" in quick_titles)
        check("home_quick_download", "下载中心" in quick_titles)
        commands = {item["id"]: item for item in window._palette_commands()}
        check(
            "palette_optimize",
            "game-optimize" in commands
            and "优化" in commands["game-optimize"]["keywords"],
        )

        settings_dialog = VersionSettingsDialog(window, version_id)
        buttons = [
            child.text() for child in settings_dialog.findChildren(QPushButton)
        ]
        check("version_settings_optimize", "游戏优化" in buttons)

        window.versions.append(
            VersionSummary(
                id=f"fabric-loader-0.15.0-{version_id}",
                loader="Fabric",
                base_id=version_id,
                parent_id=version_id,
                is_loader_profile=True,
            )
        )
        loader_dialog = VersionSettingsDialog(
            window, f"fabric-loader-0.15.0-{version_id}"
        )
        loader_buttons = [
            child.text() for child in loader_dialog.findChildren(QPushButton)
        ]
        check("loader_settings_no_optimize", "游戏优化" not in loader_buttons)
        window.versions.pop()

        room_page = window.room_page
        check("room_sync_button", room_page.sync_mods_btn.text() == "同步房主 Mod")

        room_code = encode_room_code("127.0.0.1:25565")
        manifest = RoomManifest(
            name="房主猫",
            version_id=version_id,
            mc_version="1.20.1",
            loader="Fabric",
            mods=[
                ManifestMod(
                    filename="present.jar",
                    size=len(present_bytes),
                    title="Present Mod",
                    project_id="present-project",
                    version_id="present-version",
                    source="modrinth",
                    slug="present-mod",
                ),
                ManifestMod(
                    filename="missing.jar",
                    size=1234,
                    title="Missing Mod",
                    project_id="missing-project",
                    version_id="missing-version",
                    source="modrinth",
                    slug="missing-mod",
                ),
                ManifestMod(filename="local-only.jar", size=99, title="Local Only"),
            ],
        )
        invite_text = encode_invite(
            SocialProfile(id="DKL-TEST0001", name="房主猫"),
            room_code=room_code,
            room_version=version_id,
            mod_manifest=encode_manifest(manifest),
        )
        check("invite_length_over_old_limit", len(invite_text) > 40)
        room_page.code_edit.setText(invite_text)
        app.processEvents()
        check(
            "room_invite_manifest",
            room_page._invite_manifest is not None
            and room_page._invite_manifest.mod_count == 3,
        )
        check("room_invite_code", room_page.code_edit.text() == room_code)
        check(
            "room_invite_hint_single_count",
            room_page.code_hint.text().count("房主清单") == 1,
        )
        check(
            "room_invite_button",
            room_page.sync_mods_btn.text() == "同步房主 Mod（3）",
        )

        room_page._clear_invite_manifest()
        room_page.code_edit.clear()
        app.processEvents()
        room_page._room_payload = {
            "room_code": room_code,
            "address": "127.0.0.1:25565",
        }
        room_page._copy_invite_card()
        check(
            "host_invite_starts",
            room_page._invite_worker is not None
            and not room_page.copy_invite_btn.isEnabled(),
        )
        finished = wait_until(lambda: room_page._invite_worker is None)
        app.processEvents()
        copied = QApplication.clipboard().text().strip()
        parsed = parse_invite(copied) if copied.startswith("DKLF1:") else None
        copied_manifest = (
            decode_manifest(parsed.mod_manifest)
            if parsed is not None and parsed.mod_manifest
            else None
        )
        check("host_invite_finished", finished)
        check("host_invite_room", parsed is not None and parsed.room_code == room_code)
        check(
            "host_invite_manifest",
            copied_manifest is not None
            and copied_manifest.mod_count == 1
            and copied_manifest.mods[0].filename == "present.jar",
        )
        check(
            "host_invite_button_restored",
            room_page.copy_invite_btn.isEnabled()
            and room_page.copy_invite_btn.text() == "好友邀请卡",
        )
        room_page._room_payload = None

        sync_dialog = ModSyncDialog(window, manifest, version_id)
        sync_dialog.show()
        app.processEvents()
        check(
            "sync_dialog_counts",
            sync_dialog.present_badge.text() == "已有 1"
            and sync_dialog.missing_badge.text() == "缺失 1"
            and sync_dialog.unknown_badge.text() == "无法识别 1",
        )
        check(
            "sync_dialog_missing",
            sync_dialog._plan is not None
            and sync_dialog._plan.missing[0].filename == "missing.jar"
            and sync_dialog.sync_btn.isEnabled(),
        )
        check("sync_dialog_warning", not sync_dialog.warn_label.isHidden())

        optimize_dialog = OptimizeDialog(window, version_id)
        optimize_dialog.show()
        app.processEvents()
        check(
            "optimize_dialog_state",
            optimize_dialog.state_badge.text() == "尚未优化"
            and optimize_dialog.performance_box.isVisible(),
        )
        check(
            "optimize_vanilla_mods",
            not optimize_dialog.install_mods_box.isEnabled()
            and optimize_dialog.install_mods_box.text() == "原版客户端无需优化 Mod",
        )
        check(
            "optimize_defaults",
            optimize_dialog.memory_box.isChecked()
            and optimize_dialog.jvm_box.isChecked()
            and optimize_dialog.options_box.isChecked(),
        )
        check(
            "optimize_graphics_locked",
            not optimize_dialog.iris_box.isEnabled()
            and not optimize_dialog.iris_box.isChecked(),
        )

        optimize_dialog.optimize_btn.click()
        applied = wait_until(lambda: optimize_dialog._worker is None)
        app.processEvents()
        check("optimize_worker_finished", applied)
        check(
            "optimize_memory_applied",
            int(window.config.version_memory.get(version_id, 0)) >= 512,
        )
        check(
            "optimize_jvm_applied",
            bool(str(window.config.version_jvm_args.get(version_id, "")).strip()),
        )
        check(
            "optimize_state_refreshed",
            optimize_dialog.state_badge.text() == "已应用优化",
        )

        optimize_dialog.reject()
        sync_dialog.reject()
        settings_dialog.reject()
        loader_dialog.reject()
        optimize_dialog = sync_dialog = settings_dialog = loader_dialog = None
        app.processEvents()
    finally:
        main_window_module.load_store = original_load_store
        MainWindow.ensure_java_detected = original_ensure_java
        optimize_module.system_memory_mb = original_system_memory
        optimize_module.detect_gpus = original_detect_gpus
        if window is not None:
            window.hide()
            window.deleteLater()
            app.processEvents()
        shutil.rmtree(root, ignore_errors=True)

    print("ALL UI INTEGRATION CHECKS PASSED" if ok else "UI INTEGRATION CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
