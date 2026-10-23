from __future__ import annotations

import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from stellar.config import Config
from stellar.ui.animations import GameLoadingOverlay, SplashScreen
from stellar.ui.main_window import MainWindow
from stellar.ui.widgets import ChibiCompanion

QA = PROJECT / "qa"
app = QApplication(sys.argv)
window: MainWindow | None = None
splash: SplashScreen | None = None
overlay: GameLoadingOverlay | None = None

PAGES = ("download", "mods", "skin", "room", "server", "settings")
index = 0


def build_window() -> None:
    global window
    window = MainWindow(Config.load())
    window.resize(1464, 920)
    window.apply_theme("dark")
    window.switch_page("home")
    window.show()
    QTimer.singleShot(800, capture_home)


def capture_home() -> None:
    assert window is not None
    window.home_page.hero_art._poke()
    QTimer.singleShot(300, save_home)


def save_home() -> None:
    assert window is not None
    window.grab().save(str(QA / "dkl_194_home_dark.png"))
    QTimer.singleShot(120, next_page)


def next_page() -> None:
    global index
    assert window is not None
    if index >= len(PAGES):
        QTimer.singleShot(200, show_splash)
        return
    name = PAGES[index]
    index += 1
    window.switch_page(name)
    QTimer.singleShot(700, lambda: capture_page(name))


def capture_page(name: str) -> None:
    assert window is not None
    companions = [
        child for child in window.findChildren(ChibiCompanion) if child.isVisible()
    ]
    if companions:
        companions[0]._poke()
    QTimer.singleShot(260, lambda: save_page(name))


def save_page(name: str) -> None:
    assert window is not None
    window.grab().save(str(QA / f"dkl_194_{name}.png"))
    QTimer.singleShot(120, next_page)


def show_splash() -> None:
    global splash
    splash = SplashScreen()
    splash._elapsed = 1000
    splash._progress = 0.55
    splash.show()
    QTimer.singleShot(420, save_splash)


def save_splash() -> None:
    global splash
    assert splash is not None
    splash.grab().save(str(QA / "dkl_194_splash.png"))
    splash.close()
    QTimer.singleShot(180, show_loading)


def show_loading() -> None:
    global overlay
    assert window is not None
    window.switch_page("home")
    overlay = GameLoadingOverlay(window)
    overlay.show_loading("1.21.1", "正在把猫猫送进世界…")
    overlay.set_status("正在检查 1.21.1 的文件…")
    QTimer.singleShot(420, save_loading)


def save_loading() -> None:
    global overlay
    assert overlay is not None
    overlay.grab().save(str(QA / "dkl_194_loading.png"))
    overlay.abort()
    QTimer.singleShot(200, finish)


def finish() -> None:
    if window is not None:
        window.close()
    app.quit()


QTimer.singleShot(300, build_window)
app.exec()
