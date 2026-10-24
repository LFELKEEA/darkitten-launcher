from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import zipfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from darkitten.config import Config
from darkitten.ui import snapshot_dialog as snapshot_dialog_module
from darkitten.ui.main_window import MainWindow
from darkitten.ui.snapshot_dialog import SnapshotDialog

CHECKS: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name} FAILED {detail}")
    CHECKS.append(name)
    print(f"{name}: OK")


def wait_until(app, predicate, timeout: float = 8.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return False


def main() -> None:
    MainWindow.ensure_java_detected = lambda self, force=False: None
    Config.save = lambda self: None
    app = QApplication.instance() or QApplication(sys.argv)

    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    game = root / "game"
    version_dir = game / "versions" / "1.21.11"
    (version_dir / "saves" / "world").mkdir(parents=True)
    (version_dir / "saves" / "world" / "level.dat").write_bytes(b"original")
    (version_dir / "saves" / "world" / "session.lock").write_bytes(b"lock")
    (version_dir / "config").mkdir()
    (version_dir / "config" / "mod.json").write_text("{}", encoding="utf-8")
    (version_dir / "options.txt").write_text("lang:zh_cn\n", encoding="utf-8")
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

    config = Config(
        game_dir=str(game),
        update_auto_check=False,
        last_version="1.21.11",
        version_isolation=True,
    )
    window = MainWindow(config)
    window.resize(1280, 860)
    window.apply_theme("dark")
    window.show()
    window.select_version("1.21.11")
    window.switch_page("settings")
    app.processEvents()

    page = window.settings_page
    check("ui-snapshot-hint-empty", "还没有快照" in page.snapshot_hint.text(), page.snapshot_hint.text())

    page.snapshot_create_btn.click()
    app.processEvents()
    check("ui-snapshot-busy", not page.snapshot_create_btn.isEnabled())
    ok = wait_until(app, lambda: page._snapshot_create_worker is None)
    check("ui-snapshot-worker-done", ok)
    check("ui-snapshot-button-restored", page.snapshot_create_btn.isEnabled())
    snapshots = list((version_dir / ".dkl-backups" / "snapshots").glob("*.zip"))
    check("ui-snapshot-file", len(snapshots) == 1, str(snapshots))
    check("ui-snapshot-hint", "共 1 个快照" in page.snapshot_hint.text(), page.snapshot_hint.text())
    with zipfile.ZipFile(snapshots[0]) as archive:
        names = set(archive.namelist())
    check("ui-snapshot-content", "saves/world/level.dat" in names and "config/mod.json" in names)
    check("ui-snapshot-no-lock", "saves/world/session.lock" not in names)

    dialog = SnapshotDialog(window, version_dir, "1.21.11")
    check("ui-snapshot-dialog-rows", len(dialog.findChildren(QPushButton)) >= 3)
    restore_buttons = [
        button for button in dialog.findChildren(QPushButton) if button.text() == "恢复"
    ]
    check("ui-snapshot-dialog-restore-btn", len(restore_buttons) == 1)

    emitted: list[Path] = []
    dialog.restore_requested.connect(emitted.append)

    class FakeConfirm:
        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return 1

    original_confirm = snapshot_dialog_module.ConfirmDialog
    snapshot_dialog_module.ConfirmDialog = FakeConfirm
    try:
        restore_buttons[0].click()
        app.processEvents()
    finally:
        snapshot_dialog_module.ConfirmDialog = original_confirm
    check("ui-snapshot-restore-signal", len(emitted) == 1, str(emitted))

    # 修改后通过设置页恢复，验证 worker 与按钮状态。
    (version_dir / "saves" / "world" / "level.dat").write_bytes(b"broken")
    stored = snaps = list((version_dir / ".dkl-backups" / "snapshots").glob("*.zip"))
    page._restore_snapshot(snaps[0], version_dir, "1.21.11")
    app.processEvents()
    check("ui-restore-busy", not page.snapshot_manage_btn.isEnabled())
    ok = wait_until(app, lambda: page._snapshot_restore_worker is None)
    check("ui-restore-worker-done", ok)
    check("ui-restore-button-restored", page.snapshot_manage_btn.isEnabled())
    check(
        "ui-restore-content",
        (version_dir / "saves" / "world" / "level.dat").read_bytes() == b"original",
    )

    # 无版本时应给出提示而不是崩溃。
    window.selected_version = ""
    config.last_version = ""
    check("ui-snapshot-no-version", page._snapshot_target() is None)

    print(f"RESULT: PASS ({len(CHECKS)} checks)")


if __name__ == "__main__":
    main()
