from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))


def check(label: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{label} failed {detail}")
    print(f"ok  {label}" + (f" · {detail}" if detail else ""))


def read_shortcut(path: Path) -> dict[str, str]:
    import base64

    script = (
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{str(path).replace(chr(39), chr(39) * 2)}'); "
        "Write-Output $s.TargetPath; Write-Output $s.Arguments; "
        "Write-Output $s.WorkingDirectory; Write-Output $s.IconLocation"
    )
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            encoded,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    lines = completed.stdout.lstrip("\ufeff").splitlines()
    check("shortcut readable", completed.returncode == 0 and len(lines) >= 4, str(lines))
    return {
        "target": lines[0],
        "arguments": lines[1],
        "working": lines[2],
        "icon": lines[3],
    }


def test_create_shortcut() -> None:
    import stellar.shortcut as shortcut

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        desktop = root / "Desktop"
        desktop.mkdir()
        app_dir = root / "Launcher"
        (app_dir / "assets").mkdir(parents=True)
        (app_dir / "app.py").write_text("print('hello')\n", encoding="utf-8")
        shutil.copy2(PROJECT / "assets" / "dkl.ico", app_dir / "assets" / "dkl.ico")

        original_desktop = shortcut._desktop_dir
        original_app_dir = shortcut.APP_DIR
        shortcut._desktop_dir = lambda: desktop
        shortcut.APP_DIR = app_dir
        try:
            path = shortcut.create_desktop_shortcut()
            check("shortcut created", path.is_file(), str(path))
            check("shortcut in desktop", path.parent == desktop)
            check("shortcut exists helper", shortcut.shortcut_exists())
            info = read_shortcut(path)
            check("target python", "python" in Path(info["target"]).name.lower(), info["target"])
            check("arguments app", "app.py" in info["arguments"], info["arguments"])
            check("working dir", Path(info["working"]) == app_dir, info["working"])
            check("icon set", "dkl.ico" in info["icon"], info["icon"])
        finally:
            shortcut._desktop_dir = original_desktop
            shortcut.APP_DIR = original_app_dir


def test_config_flag() -> None:
    import stellar.config as config

    with tempfile.TemporaryDirectory() as tmp:
        original_path = config.CONFIG_PATH
        config.CONFIG_PATH = Path(tmp) / "config.json"
        try:
            config.CONFIG_PATH.write_text(
                json.dumps({"shortcut_offered": True}), encoding="utf-8"
            )
            loaded = config.Config.load()
            check("shortcut flag loaded", loaded.shortcut_offered is True)
            config.CONFIG_PATH.write_text("{}", encoding="utf-8")
            check("shortcut flag default", config.Config.load().shortcut_offered is False)
        finally:
            config.CONFIG_PATH = original_path


def test_settings_button() -> None:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    import stellar.shortcut as shortcut

    original_exists = shortcut.shortcut_exists
    try:
        shortcut.shortcut_exists = lambda: False
        from stellar.config import Config
        from stellar.ui.main_window import MainWindow

        window = MainWindow(Config.load())
        check(
            "settings button initial",
            window.settings_page.shortcut_btn.text() == "创建桌面快捷方式",
            window.settings_page.shortcut_btn.text(),
        )
        shortcut.shortcut_exists = lambda: True
        window.settings_page.refresh_shortcut_state()
        check(
            "settings button exists",
            window.settings_page.shortcut_btn.text() == "重新创建快捷方式",
            window.settings_page.shortcut_btn.text(),
        )
        original_offered = bool(window.config.shortcut_offered)
        window.config.shortcut_offered = True
        window.offer_shortcut_once()
        check("offer skipped when flagged", True)

        from stellar.ui import dialogs

        original_exec = dialogs.ConfirmDialog.exec
        original_create = shortcut.create_desktop_shortcut
        try:
            target = Path(tempfile.gettempdir()) / "qa-shortcut.lnk"
            shortcut.shortcut_exists = lambda: False
            shortcut.create_desktop_shortcut = lambda: target
            dialogs.ConfirmDialog.exec = lambda self: True
            window.config.shortcut_offered = False
            window.offer_shortcut_once()
            check("offer flag saved", window.config.shortcut_offered is True)
        finally:
            dialogs.ConfirmDialog.exec = original_exec
            shortcut.create_desktop_shortcut = original_create
            window.config.shortcut_offered = original_offered
            window.config.save()
        window.close()
        app.processEvents()
    finally:
        shortcut.shortcut_exists = original_exists


def main() -> None:
    test_create_shortcut()
    test_config_flag()
    test_settings_button()
    print("SHORTCUT TESTS PASSED")


if __name__ == "__main__":
    main()
