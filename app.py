from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from stellar.config import APP_NAME, LOG_DIR, Config, ensure_dirs
from stellar.ui import icons, theme
from stellar.ui.animations import SplashScreen
from stellar.ui.main_window import MainWindow


def _install_exception_hook() -> None:
    def handler(exc_type, exc_value, exc_tb):
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            (LOG_DIR / "crash.log").write_text(text, encoding="utf-8")
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = handler


def _arg_value(name: str, default: str = "") -> str:
    try:
        index = sys.argv.index(name)
        return sys.argv[index + 1]
    except (ValueError, IndexError):
        return default


def _debug(message: str) -> None:
    if "--debug" in sys.argv:
        print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def main() -> int:
    if "--debug" in sys.argv:
        import faulthandler

        faulthandler.dump_traceback_later(25, exit=True)

    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
    ensure_dirs()
    _install_exception_hook()

    _debug("creating QApplication")
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName("StellarLauncher")
    app.setWindowIcon(icons.app_icon())
    app.setFont(QFont("Microsoft YaHei UI", 9))
    app.setQuitOnLastWindowClosed(True)

    config = Config.load()
    theme.set_current(config.theme)
    app.setStyleSheet(theme.stylesheet(config.theme))

    shot = _arg_value("--screenshot")
    use_splash = not shot and "--no-splash" not in sys.argv
    if use_splash:
        # splash 是启动阶段唯一的可见窗口，先关闭自动退出，避免它收尾时
        # 触发 quitOnLastWindowClosed 把整个启动器带走。
        app.setQuitOnLastWindowClosed(False)
    splash: SplashScreen | None = None
    if use_splash:
        _debug("showing splash")
        splash = SplashScreen()
        splash.start()
        app.processEvents()

    _debug("building main window")
    window = MainWindow(config)

    def offer_shortcut() -> None:
        if shot or os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen":
            return
        QTimer.singleShot(900, window.offer_shortcut_once)

    if use_splash and splash is not None:
        revealed = False

        def reveal() -> None:
            nonlocal revealed
            if revealed:
                return
            revealed = True
            _debug("showing main window")
            window.show()
            window.raise_()
            window.activateWindow()
            # 先隐藏 splash 再销毁，等主窗口真正显示后再恢复“最后一个窗口
            # 关闭即退出”，避免 splash 收尾时把整个启动器带走。
            splash.hide()
            QTimer.singleShot(0, splash.deleteLater)
            app.setQuitOnLastWindowClosed(True)
            offer_shortcut()

        splash.finished.connect(reveal)
        # finished 依赖淡出动画收尾，极端情况下可能不再发出，这里兜底。
        QTimer.singleShot(4000, reveal)
    else:
        _debug("showing main window")
        window.show()
        window.raise_()
        window.activateWindow()
        offer_shortcut()
    _debug("window shown")
    if shot:
        page = _arg_value("--page", "home")
        delay = int(_arg_value("--delay", "2200"))
        forced_theme = _arg_value("--theme")
        if forced_theme in ("light", "dark"):
            window.apply_theme(forced_theme)
        if page in ("home", "download", "mods", "skin", "room", "server", "settings"):
            window.switch_page(page)

        def prepare() -> None:
            _debug("capturing screenshot")
            saved = window.grab().save(shot)
            _debug(f"screenshot saved={saved}")
            app.quit()

        QTimer.singleShot(max(300, delay), prepare)

    _debug("entering event loop")
    code = app.exec()
    _debug(f"event loop exited {code}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
