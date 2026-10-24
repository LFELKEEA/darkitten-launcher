from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


def main() -> None:
    app = QApplication.instance() or QApplication([])
    from darkitten.config import Config
    from darkitten.ui.main_window import MainWindow

    config = Config.load()
    window = MainWindow(config)
    store = window.social_store
    old_room = store.profile.room_code
    old_version = store.profile.room_version
    calls: list[str] = []
    window.room_page.join_room = lambda: calls.append("join")
    try:
        window.join_friend_room("127.0.0.1:25566", "1.21.1")
        QTest.qWait(60)
        assert calls == ["join"], calls
        assert window.room_page.code_edit.text() == "127.0.0.1:25566"
        assert "127.0.0.1:25566" in window.room_page.code_hint.text()
        print("ok  friend room join invoked")

        window.join_friend_room("127.0.0.1:25566", "9.9.9")
        QTest.qWait(30)
        assert calls == ["join"], calls
        assert "9.9.9" in window.room_page.guest_status.text()
        print("ok  missing version blocked", window.room_page.guest_status.text())

        window.room_page._sync_social_room("ROOM-TEST-1")
        assert store.profile.room_code == "ROOM-TEST-1"
        assert store.profile.room_version == str(window.selected_version or "")
        print("ok  room shared to social profile")
        window.room_page._clear_social_room()
        assert store.profile.room_code == ""
        print("ok  room cleared from social profile")
    finally:
        store.profile.room_code = old_room
        store.profile.room_version = old_version
        store.save()
        window.close()
        app.processEvents()
    print("ROOM FLOW OK")


if __name__ == "__main__":
    main()
