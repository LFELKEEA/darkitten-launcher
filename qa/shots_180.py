from __future__ import annotations

import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from stellar.config import Config
from stellar.packs import export_pack
from stellar.ui.animations import SplashScreen
from stellar.ui.main_window import MainWindow
from stellar.ui.pack_dialog import PackExportDialog, PackImportDialog

QA = PROJECT / "qa"
app = QApplication(sys.argv)
window: MainWindow | None = None
splash: SplashScreen | None = None
dialog = None
temp_dir = tempfile.TemporaryDirectory()


def capture_splash() -> None:
    assert splash is not None
    splash.grab().save(str(QA / "dkl_splash.png"))
    splash._done = True
    splash.close()
    QTimer.singleShot(250, build_window)


def build_window() -> None:
    global window
    window = MainWindow(Config.load())
    window.resize(1464, 920)
    window.apply_theme("dark")
    window.show()
    QTimer.singleShot(500, capture_loading)


def capture_loading() -> None:
    assert window is not None
    window.loading_overlay.show_loading(
        window.selected_version or "1.21.1", "正在检查 Java 与游戏文件…"
    )
    QTimer.singleShot(700, save_loading)


def save_loading() -> None:
    assert window is not None
    window.grab().save(str(QA / "dkl_loading.png"))
    window.loading_overlay.abort()
    QTimer.singleShot(250, show_export_dialog)


def show_export_dialog() -> None:
    global dialog
    assert window is not None
    dialog = PackExportDialog(window, window.selected_version or "1.21.1")
    dialog.show()
    QTimer.singleShot(500, save_export_dialog)


def save_export_dialog() -> None:
    assert dialog is not None
    dialog.grab().save(str(QA / "dkl_export.png"))
    dialog.reject()
    QTimer.singleShot(250, prepare_pack_and_import)


def prepare_pack_and_import() -> None:
    global dialog
    assert window is not None
    pack = Path(temp_dir.name) / "QA猫猫包.dklpack"
    export_pack(
        window.config.game_path,
        window.selected_version,
        pack,
        name="QA 猫猫整合包",
        description="用于检查导入预览界面的测试纸箱。",
    )
    dialog = PackImportDialog(window)
    dialog._load_pack(pack)
    dialog.show()
    QTimer.singleShot(500, save_import_dialog)


def save_import_dialog() -> None:
    assert dialog is not None
    dialog.grab().save(str(QA / "dkl_import.png"))
    dialog.reject()
    QTimer.singleShot(250, finish)


def finish() -> None:
    if window is not None:
        window.close()
    temp_dir.cleanup()
    app.quit()


splash = SplashScreen()
splash.start()
QTimer.singleShot(900, capture_splash)
app.exec()
