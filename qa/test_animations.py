from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from PySide6.QtCore import QEventLoop, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from darkitten.config import APP_NAME, APP_SHORT_NAME, Config
from darkitten.ui.animations import GameLoadingOverlay, SplashScreen
from darkitten.ui.main_window import MainWindow


def wait(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def main() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    ok = True

    branding_ok = (
        APP_NAME == "Darkitten Launcher" and APP_SHORT_NAME == "DKL"
    )
    print("branding:", "OK" if branding_ok else "FAIL", APP_NAME, APP_SHORT_NAME)
    ok &= branding_ok

    splash = SplashScreen()
    fired: list[bool] = []
    splash.finished.connect(lambda: fired.append(True))
    splash.start()
    wait(3200)
    splash_closed = len(fired) == 1
    print("splash_auto_finish:", "OK" if splash_closed else "FAIL", len(fired))
    ok &= splash_closed
    splash.close()
    splash.deleteLater()

    skipped = SplashScreen()
    skipped_fired: list[bool] = []
    skipped.finished.connect(lambda: skipped_fired.append(True))
    skipped.start()
    wait(80)
    QTest.mouseClick(skipped, Qt.LeftButton)
    wait(500)
    skip_ok = len(skipped_fired) == 1
    print("splash_skip:", "OK" if skip_ok else "FAIL")
    ok &= skip_ok
    skipped.close()
    skipped.deleteLater()

    window = MainWindow(Config.load())
    window.show()
    app.processEvents()
    overlay: GameLoadingOverlay = window.loading_overlay
    overlay.show_loading("1.21.1", "正在检查 Java…")
    wait(80)
    shown = overlay.isVisible() and overlay.cancel_btn.isVisible()
    status_ok = overlay._status == "正在检查 Java…"
    overlay.set_status("正在拼装参数…")
    status_ok = status_ok and overlay._status == "正在拼装参数…"
    print("overlay_show:", "OK" if shown else "FAIL")
    print("overlay_status:", "OK" if status_ok else "FAIL")
    ok &= shown and status_ok

    cancelled: list[bool] = []
    overlay.cancel_requested.connect(lambda: cancelled.append(True))
    overlay.cancel_btn.click()
    cancel_ok = (
        cancelled == [True]
        and not overlay.cancel_btn.isEnabled()
        and overlay.cancel_btn.text() == "正在收回猫爪…"
    )
    print("overlay_cancel:", "OK" if cancel_ok else "FAIL")
    ok &= cancel_ok

    overlay.abort()
    abort_ok = not overlay.isVisible()
    print("overlay_abort:", "OK" if abort_ok else "FAIL")
    ok &= abort_ok

    overlay.show_loading("1.21.1")
    overlay.finish("世界已加载，去玩吧喵。", 120)
    wait(260)
    finish_ok = not overlay.isVisible()
    print("overlay_finish:", "OK" if finish_ok else "FAIL")
    ok &= finish_ok

    window.close()
    app.processEvents()
    print("RESULT:", "PASS" if ok else "FAIL")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
