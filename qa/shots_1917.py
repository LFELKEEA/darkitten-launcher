from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from darkitten.config import Config
from darkitten.ui.local_import_dialog import LocalImportDialog
from darkitten.ui.main_window import MainWindow

QA = PROJECT / "qa"
app = QApplication(sys.argv)
for font_path in (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
):
    if Path(font_path).exists():
        QFontDatabase.addApplicationFont(font_path)
app.setFont(QFont("Microsoft YaHei UI", 10))
Config.save = lambda self: None
temp = tempfile.TemporaryDirectory()
root = Path(temp.name)
game = root / "game"
game.mkdir()


def vanilla(version_id: str, java: int = 21) -> dict:
    return {
        "id": version_id,
        "type": "release",
        "mainClass": "net.minecraft.client.main.Main",
        "assets": "1.21",
        "assetIndex": {"id": "1.21", "url": "http://example.invalid/a.json"},
        "javaVersion": {"majorVersion": java},
    }


def write_version(version_id: str, java: int = 21) -> None:
    folder = game / "versions" / version_id
    folder.mkdir(parents=True)
    (folder / f"{version_id}.json").write_text(
        json.dumps(vanilla(version_id, java)), encoding="utf-8"
    )
    (folder / f"{version_id}.jar").write_bytes(b"jar")


write_version("1.21.11")
write_version("1.20.1", java=17)
loader_id = "fabric-loader-0.19.5-1.21.11"
loader_dir = game / "versions" / loader_id
loader_dir.mkdir(parents=True)
(loader_dir / f"{loader_id}.json").write_text(
    json.dumps(
        {
            "id": loader_id,
            "inheritsFrom": "1.21.11",
            "type": "release",
            "mainClass": "net.fabricmc.loader.impl.launch.knot.KnotClient",
            "libraries": [{"name": "net.fabricmc:fabric-loader:0.19.5"}],
            "javaVersion": {"majorVersion": 21},
        }
    ),
    encoding="utf-8",
)

mod = root / "恶魔小猫的魔法书.jar"
mod.write_bytes(b"mod")

window: MainWindow | None = None
dialog: LocalImportDialog | None = None


def build() -> None:
    global window
    config = Config(
        game_dir=str(game),
        update_auto_check=False,
        last_version="1.21.11",
        version_loaders={"1.21.11": loader_id},
    )
    window = MainWindow(config)
    window.resize(1280, 820)
    window.apply_theme("dark")
    window.show()
    QTimer.singleShot(400, capture_games)


def capture_games() -> None:
    assert window is not None
    window.home_page.version_tabs.set_value("games", emit=True)
    QTimer.singleShot(250, do_capture_games)


def do_capture_games() -> None:
    assert window is not None
    window.grab().save(str(QA / "dkl_1917_home_games.png"))
    window.home_page.version_tabs.set_value("loaders", emit=True)
    QTimer.singleShot(350, capture_loaders)


def capture_loaders() -> None:
    assert window is not None
    window.grab().save(str(QA / "dkl_1917_home_loaders.png"))
    window.switch_page("download")
    QTimer.singleShot(350, capture_download)


def capture_download() -> None:
    assert window is not None
    window.grab().save(str(QA / "dkl_1917_download.png"))
    show_dialog()


def show_dialog() -> None:
    global dialog
    assert window is not None
    dialog = LocalImportDialog(window, initial_path=mod)
    dialog.show()
    QTimer.singleShot(450, capture_dialog)


def capture_dialog() -> None:
    assert dialog is not None
    dialog.grab().save(str(QA / "dkl_1917_local_import.png"))
    dialog.close()
    QTimer.singleShot(200, finish)


def finish() -> None:
    if window is not None:
        window.close()
    app.quit()


QTimer.singleShot(0, build)
app.exec()
print("shots saved")
