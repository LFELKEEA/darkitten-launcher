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

from PySide6.QtWidgets import QApplication

from darkitten import diagnostics
from darkitten.config import Config
from darkitten.ui import main_window as main_window_module
from darkitten.ui import pages as pages_module
from darkitten.ui.main_window import MainWindow
from darkitten.ui.pages import settings as settings_module

CHECKS: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name} FAILED {detail}")
    CHECKS.append(name)
    print(f"{name}: OK")


def wait_until(app, predicate, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return False


class FakeConfirm:
    result = 1

    def __init__(self, *args, **kwargs):
        pass

    def exec(self):
        return FakeConfirm.result


def main() -> None:
    MainWindow.ensure_java_detected = lambda self, force=False: None
    Config.save = lambda self: None
    app = QApplication.instance() or QApplication(sys.argv)

    temp = Path(tempfile.mkdtemp(prefix="dkl-diag-ui-"))
    game = temp / "game"
    version_dir = game / "versions" / "1.21.11"
    mods_dir = version_dir / "mods"
    mods_dir.mkdir(parents=True)
    with zipfile.ZipFile(mods_dir / "sodium.jar", "w") as archive:
        archive.writestr(
            "fabric.mod.json",
            json.dumps({"id": "sodium", "name": "Sodium", "version": "1.0.0"}),
        )
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

    diagnostics.APP_DIR = temp / "app"
    diagnostics.LOG_FILE = diagnostics.APP_DIR / "logs" / "launcher.log"
    diagnostics.LOG_FILE.parent.mkdir(parents=True)
    diagnostics.LOG_FILE.write_text("access_token=SECRET\n普通日志\n", encoding="utf-8")

    config = Config(
        game_dir=str(game),
        update_auto_check=False,
        last_version="1.21.11",
        version_isolation=True,
    )
    window = MainWindow(config)
    window.resize(1280, 900)
    window.apply_theme("dark")
    window.show()
    window.select_version("1.21.11")
    window.switch_page("settings")
    app.processEvents()

    page = window.settings_page
    check("ui-diagnostics-buttons", page.diagnostics_copy_btn.isEnabled() and page.diagnostics_bundle_btn.isEnabled())

    page.diagnostics_copy_btn.click()
    app.processEvents()
    check("ui-diagnostics-copy-busy", not page.diagnostics_copy_btn.isEnabled())
    ok = wait_until(app, lambda: page._diagnostics_worker is None)
    check("ui-diagnostics-copy-done", ok)
    clipboard = QApplication.clipboard().text()
    check("ui-diagnostics-copy-content", "Darkitten Launcher 诊断信息" in clipboard, clipboard[:80])
    check("ui-diagnostics-copy-no-secret", "SECRET" not in clipboard)

    page.diagnostics_bundle_btn.click()
    app.processEvents()
    ok = wait_until(app, lambda: page._diagnostics_worker is None)
    check("ui-diagnostics-bundle-done", ok)
    bundles = list((diagnostics.APP_DIR / "diagnostics").glob("*.zip"))
    check("ui-diagnostics-bundle-file", len(bundles) == 1, str(bundles))
    check("ui-diagnostics-hint", "最近诊断包" in page.diagnostics_hint.text(), page.diagnostics_hint.text())
    with zipfile.ZipFile(bundles[0]) as archive:
        names = set(archive.namelist())
        log_text = archive.read("launcher.log").decode("utf-8")
    check("ui-diagnostics-bundle-content", "summary.txt" in names and "system.json" in names)
    check("ui-diagnostics-bundle-redacted", "SECRET" not in log_text)

    # 安全模式：确认后禁用 Mod，游戏退出后恢复。
    launched: list[bool] = []
    window.launch_selected = lambda *args, **kwargs: launched.append(True)
    original_confirm = settings_module.ConfirmDialog
    settings_module.ConfirmDialog = FakeConfirm
    try:
        page.safe_mode_btn.click()
        app.processEvents()
    finally:
        settings_module.ConfirmDialog = original_confirm
    check("ui-safe-mode-launched", len(launched) == 1, str(launched))
    check("ui-safe-mode-disabled", (mods_dir / "sodium.jar.disabled").is_file())
    check("ui-safe-mode-state", window._safe_mode_mods_dir == mods_dir)

    window._game_finished(0)
    app.processEvents()
    check("ui-safe-mode-restored", (mods_dir / "sodium.jar").is_file())
    check("ui-safe-mode-state-cleared", window._safe_mode_mods_dir is None)

    # 再次进入安全模式，验证启动失败路径也会恢复。
    window.launch_safe_mode()
    app.processEvents()
    check("ui-safe-mode-second", (mods_dir / "sodium.jar.disabled").is_file())
    window._restore_safe_mode_mods()
    check("ui-safe-mode-second-restored", (mods_dir / "sodium.jar").is_file())

    print(f"RESULT: PASS ({len(CHECKS)} checks)")


if __name__ == "__main__":
    main()
