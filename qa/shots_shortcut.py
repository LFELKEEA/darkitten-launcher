from __future__ import annotations

import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from stellar.config import Config
from stellar.ui.main_window import MainWindow

QA = PROJECT / "qa"
app = QApplication(sys.argv)
window: MainWindow | None = None
original_offered = False
import stellar.shortcut as shortcut_module
original_exists = shortcut_module.shortcut_exists


def build_window() -> None:
    global window, original_offered
    window = MainWindow(Config.load())
    original_offered = bool(window.config.shortcut_offered)
    window.resize(1464, 920)
    window.apply_theme("dark")
    window.switch_page("settings")
    window.show()
    QTimer.singleShot(700, capture_settings)


def capture_settings() -> None:
    assert window is not None
    row = window.settings_page.shortcut_btn.parentWidget()
    card = row.parentWidget() if row is not None else None
    target = card if card is not None else window.settings_page
    target.grab().save(str(QA / "dkl_shortcut_settings.png"))
    print("settings card", target.size())
    QTimer.singleShot(250, show_prompt)


def show_prompt() -> None:
    assert window is not None
    window.config.shortcut_offered = False
    shortcut_module.shortcut_exists = lambda: False

    def capture_dialog() -> None:
        dialog = QApplication.activeModalWidget()
        if dialog is not None:
            dialog.grab().save(str(QA / "dkl_shortcut_prompt.png"))
            print("prompt", dialog.size())
            dialog.reject()
        else:
            print("no modal dialog")
            finish()

    QTimer.singleShot(450, capture_dialog)
    window.offer_shortcut_once()
    QTimer.singleShot(200, finish)


def finish() -> None:
    shortcut_module.shortcut_exists = original_exists
    if window is not None:
        window.config.shortcut_offered = original_offered
        window.config.save()
        window.close()
    app.quit()


QTimer.singleShot(300, build_window)
app.exec()
