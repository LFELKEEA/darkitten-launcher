from __future__ import annotations

import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from stellar.config import Config
from stellar.ui.main_window import MainWindow
from stellar.ui.version_zip_dialog import VersionZipDialog

QA = PROJECT / "qa"
app = QApplication(sys.argv)
app.setFont(QFont("Microsoft YaHei UI", 10))
Config.save = lambda self: None
temp = tempfile.TemporaryDirectory()
root = Path(temp.name)
game = root / "game"
game.mkdir()
version = {
    "id": "CatPack 1.21.1",
    "type": "release",
    "mainClass": "net.minecraft.client.main.Main",
    "assets": "1.21",
    "assetIndex": {"id": "1.21", "url": "http://example.invalid/a.json"},
    "javaVersion": {"majorVersion": 21},
}
archive = root / "猫猫整合版本.zip"
with zipfile.ZipFile(archive, "w") as zf:
    zf.writestr("versions/CatPack 1.21.1/CatPack 1.21.1.json", json.dumps(version, ensure_ascii=False))
    zf.writestr("versions/CatPack 1.21.1/CatPack 1.21.1.jar", b"x" * 2048)
    for index in range(8):
        zf.writestr(f"versions/CatPack 1.21.1/mods/mod{index}.jar", b"m" * 512)
    for index in range(3):
        zf.writestr(f"versions/CatPack 1.21.1/config/cfg{index}.toml", b"a=1")
    zf.writestr("libraries/org/example/lib.jar", b"l" * 1024)

window: MainWindow | None = None
dialog: VersionZipDialog | None = None


def build() -> None:
    global window
    window = MainWindow(Config(game_dir=str(game)))
    window.resize(1280, 820)
    window.apply_theme("dark")
    window.show()
    QTimer.singleShot(300, show_dialog)


def show_dialog() -> None:
    global dialog
    assert window is not None
    dialog = VersionZipDialog(window, initial_path=archive)
    dialog.show()
    QTimer.singleShot(500, capture_dialog)


def capture_dialog() -> None:
    assert dialog is not None
    dialog.grab().save(str(QA / "dkl_199_zip_dialog.png"))
    dialog.close()
    QTimer.singleShot(200, show_drop_hint)


def show_drop_hint() -> None:
    assert window is not None
    window.switch_page("download")
    QTimer.singleShot(300, capture_download)


def capture_download() -> None:
    assert window is not None
    window.grab().save(str(QA / "dkl_199_download.png"))
    window.switch_page("home")
    QTimer.singleShot(200, show_hint)


def show_hint() -> None:
    assert window is not None
    window._show_drop_hint()
    QTimer.singleShot(300, capture_hint)


def capture_hint() -> None:
    assert window is not None
    window.grab().save(str(QA / "dkl_199_drop_hint.png"))
    QTimer.singleShot(150, finish)


def finish() -> None:
    if window is not None:
        window.close()
    temp.cleanup()
    app.quit()


QTimer.singleShot(200, build)
app.exec()
