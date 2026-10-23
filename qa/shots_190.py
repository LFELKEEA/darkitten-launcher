from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from stellar.config import Config
from stellar.ui.main_window import MainWindow
from stellar.ui.migration_dialog import MigrationDialog
from stellar.ui.social_dialog import SocialDialog

QA = PROJECT / "qa"
SOCIAL = PROJECT / "social.json"
app = QApplication(sys.argv)
window: MainWindow | None = None
dialog = None
temp_dir = tempfile.TemporaryDirectory()
backup = Path(temp_dir.name) / "social.backup"
had_social = SOCIAL.is_file()
if had_social:
    shutil.copy2(SOCIAL, backup)


def build_window() -> None:
    global window
    window = MainWindow(Config.load())
    window.resize(1464, 920)
    window.apply_theme("dark")
    window.switch_page("home")
    window.show()
    QTimer.singleShot(700, poke_and_capture)


def poke_and_capture() -> None:
    assert window is not None
    window.home_page.hero_art._poke()
    QTimer.singleShot(260, save_home_dark)


def save_home_dark() -> None:
    assert window is not None
    window.grab().save(str(QA / "dkl_190_home_dark.png"))
    window.apply_theme("light")
    QTimer.singleShot(500, save_home_light)


def save_home_light() -> None:
    assert window is not None
    window.grab().save(str(QA / "dkl_190_home_light.png"))
    window.apply_theme("dark")
    QTimer.singleShot(350, show_migration)


def show_migration() -> None:
    global dialog
    assert window is not None
    dialog = MigrationDialog(window)
    dialog.show()
    QTimer.singleShot(550, save_migration)


def save_migration() -> None:
    global dialog
    assert dialog is not None
    dialog.grab().save(str(QA / "dkl_190_migration.png"))
    dialog.reject()
    QTimer.singleShot(250, show_social)


def show_social() -> None:
    global dialog
    assert window is not None
    window.social_store.add_friend(
        "夜猫子", "DKL-TESTCAT1", "ROOM-7788", "1.21.1"
    )
    dialog = SocialDialog(window)
    dialog.show()
    QTimer.singleShot(550, save_social)


def save_social() -> None:
    assert dialog is not None
    dialog.grab().save(str(QA / "dkl_190_social.png"))
    dialog.reject()
    QTimer.singleShot(250, finish)


def finish() -> None:
    if window is not None:
        window.social_store.remove_friend("DKL-TESTCAT1")
        window.close()
    if had_social and backup.is_file():
        shutil.copy2(backup, SOCIAL)
    elif SOCIAL.is_file():
        SOCIAL.unlink()
    temp_dir.cleanup()
    app.quit()


QTimer.singleShot(300, build_window)
app.exec()
