from __future__ import annotations

import json
import os
import sys
import tempfile
import zipfile
from dataclasses import asdict
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

import darkitten.server_pack as server_pack_module
import darkitten.servers as servers_module
from darkitten.config import Config
from darkitten.servers import ServerInfo
from darkitten.ui.main_window import MainWindow
from darkitten.ui.server_pack_dialog import (
    ServerPackExportDialog,
    ServerPackImportDialog,
)

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
servers_module.SERVERS_DIR = root / "servers"
server_pack_module.SERVERS_DIR = root / "servers"
server_root = root / "servers" / "demo-server"
server_root.mkdir(parents=True)
with zipfile.ZipFile(server_root / "server.jar", "w") as zf:
    zf.writestr("version.json", json.dumps({"id": "1.21.1"}))
    zf.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")
(server_root / "eula.txt").write_text("eula=true\n", encoding="utf-8")
(server_root / "server.properties").write_text(
    "level-name=world\nserver-port=25566\nonline-mode=false\nmotd=猫猫生存服\n",
    encoding="utf-8",
)
(server_root / "world" / "playerdata").mkdir(parents=True)
(server_root / "world" / "region").mkdir(parents=True)
(server_root / "world" / "level.dat").write_bytes(b"level")
(server_root / "world" / "region" / "r.0.0.mca").write_bytes(b"region")
(server_root / "world" / "playerdata" / "abc.dat").write_bytes(b"player")
(server_root / "whitelist.json").write_text(
    json.dumps([{"uuid": "1", "name": "Alice"}]), encoding="utf-8"
)
(server_root / "ops.json").write_text(
    json.dumps([{"uuid": "1", "name": "Alice", "level": 4}]), encoding="utf-8"
)
(server_root / "plugins").mkdir()
(server_root / "plugins" / "Demo.jar").write_bytes(b"jar")
info = ServerInfo(
    folder="demo-server",
    name="猫猫生存服",
    kind="paper",
    mc_version="1.21.1",
    memory_mb=4096,
    port=25566,
    eula=True,
    installed=True,
    launch_kind="jar",
    launch_target="server.jar",
    paper_build=88,
    created_at="2026-10-24 10:00:00",
)
(server_root / "server.json").write_text(
    json.dumps(asdict(info), ensure_ascii=False, indent=2), encoding="utf-8"
)

window: MainWindow | None = None
export_dialog: ServerPackExportDialog | None = None
import_dialog: ServerPackImportDialog | None = None


def build() -> None:
    global window
    window = MainWindow(Config(game_dir=str(game)))
    window.resize(1280, 820)
    window.apply_theme("dark")
    window.show()
    QTimer.singleShot(300, show_server_page)


def show_server_page() -> None:
    assert window is not None
    window.switch_page("server")
    QTimer.singleShot(500, capture_server_page)


def capture_server_page() -> None:
    assert window is not None
    window.grab().save(str(QA / "dkl_1916_server_page.png"))
    window.server_page.tabs.set_value("settings", emit=True)
    QTimer.singleShot(350, capture_settings)


def capture_settings() -> None:
    assert window is not None
    window.grab().save(str(QA / "dkl_1916_server_settings.png"))
    show_export_dialog()


def show_export_dialog() -> None:
    global export_dialog
    assert window is not None
    window.server_page.tabs.set_value("console")
    export_dialog = ServerPackExportDialog(window, info)
    export_dialog.show()
    QTimer.singleShot(450, capture_export_dialog)


def capture_export_dialog() -> None:
    assert export_dialog is not None
    export_dialog.grab().save(str(QA / "dkl_1916_server_export.png"))
    export_dialog.close()
    QTimer.singleShot(200, show_import_dialog)


def show_import_dialog() -> None:
    global import_dialog
    assert window is not None
    import_dialog = ServerPackImportDialog(window, server_root)
    import_dialog.show()
    QTimer.singleShot(450, capture_import_dialog)


def capture_import_dialog() -> None:
    assert import_dialog is not None
    import_dialog.grab().save(str(QA / "dkl_1916_server_import.png"))
    import_dialog.close()
    QTimer.singleShot(200, finish)


def finish() -> None:
    if window is not None:
        window.close()
    app.quit()


QTimer.singleShot(0, build)
app.exec()
print("shots saved")
