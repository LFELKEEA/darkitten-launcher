from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import app as entry
from darkitten.ui.main_window import MainWindow


def main() -> None:
    original_exec = QApplication.exec
    snapshot: list[dict[str, object]] = []
    splash_keys = 0
    main_keys = 0

    def record() -> None:
        application = QApplication.instance()
        for widget in application.topLevelWidgets():
            snapshot.append(
                {
                    "type": type(widget).__name__,
                    "visible": widget.isVisible(),
                }
            )

    def press_splash_keys() -> None:
        nonlocal splash_keys
        application = QApplication.instance()
        for widget in application.topLevelWidgets():
            if type(widget).__name__ == "SplashScreen" and widget.isVisible():
                for key in (Qt.Key_A, Qt.Key_Space, Qt.Key_Return, Qt.Key_Escape):
                    QTest.keyClick(widget, key)
                    splash_keys += 1
                break

    def press_main_keys() -> None:
        nonlocal main_keys
        application = QApplication.instance()
        for widget in application.topLevelWidgets():
            if type(widget).__name__ == "MainWindow" and widget.isVisible():
                for key in (Qt.Key_A, Qt.Key_B, Qt.Key_Escape):
                    QTest.keyClick(widget, key)
                    main_keys += 1
                break

    def exec_with_autoclose(*args) -> int:
        application = QApplication.instance()
        QTimer.singleShot(60, press_splash_keys)
        poll = QTimer(application)
        poll.setInterval(100)

        def poll_main_window() -> None:
            press_main_keys()
            if main_keys:
                poll.stop()

        poll.timeout.connect(poll_main_window)
        poll.start()
        QTimer.singleShot(4600, record)
        QTimer.singleShot(5400, application.quit)
        return original_exec()

    QApplication.exec = exec_with_autoclose
    try:
        code = entry.main()
    finally:
        QApplication.exec = original_exec

    main_visible = [s for s in snapshot if s["type"] == "MainWindow" and s["visible"]]
    splash_visible = [
        s for s in snapshot if s["type"] == "SplashScreen" and s["visible"]
    ]
    ok = (
        code == 0
        and len(main_visible) == 1
        and not splash_visible
        and splash_keys == 4
        and main_keys == 3
    )
    print(
        "startup:",
        "OK" if ok else "FAIL",
        f"code={code}",
        f"splash_keys={splash_keys}",
        f"main_keys={main_keys}",
        f"snapshot={snapshot}",
    )
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
