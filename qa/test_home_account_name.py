from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from PySide6.QtWidgets import QApplication

from darkitten.accounts import offline_uuid
from darkitten.config import Config
from darkitten.ui.main_window import MainWindow

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> bool:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"{name}: OK{(' (' + detail + ')') if detail else ''}")
    else:
        FAIL += 1
        print(f"{name}: FAIL{(' (' + detail + ')') if detail else ''}")
    return condition


def expected_uuid_text(name: str) -> str:
    uid = offline_uuid(name)
    return f"UUID {uid[:8]}…{uid[-4:]}"


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    with tempfile.TemporaryDirectory() as tmp:
        game = Path(tmp) / "game"
        game.mkdir(parents=True, exist_ok=True)
        config = Config(
            game_dir=str(game),
            player_name="LFELKEEA",
            update_auto_check=False,
        )
        config.save = lambda: None
        window = MainWindow(config)
        window.config.save = lambda: None
        page = window.home_page

        check("no_versions", window.versions == [], f"{len(window.versions)} versions")
        check("nothing_selected", page._selected == "", repr(page._selected))
        check(
            "initial_name_from_config",
            page.account_name.text() == "LFELKEEA",
            page.account_name.text(),
        )
        check(
            "initial_uuid",
            page.account_uuid.text() == expected_uuid_text("LFELKEEA"),
            page.account_uuid.text(),
        )
        check(
            "initial_badge",
            page.account_badge.text() == "离线模式",
            page.account_badge.text(),
        )
        check(
            "initial_hero_title",
            "LFELKEEA" in page.hero_title.text(),
            page.hero_title.text(),
        )

        config.player_name = "SecondCat"
        window.account_changed.emit()
        app.processEvents()

        check(
            "renamed_card",
            page.account_name.text() == "SecondCat",
            page.account_name.text(),
        )
        check(
            "renamed_uuid",
            page.account_uuid.text() == expected_uuid_text("SecondCat"),
            page.account_uuid.text(),
        )
        check(
            "renamed_hero_title",
            "SecondCat" in page.hero_title.text(),
            page.hero_title.text(),
        )
        check(
            "note_kept",
            page.account_note.text() == "离线账户仅用于本地游戏，无法加入正版验证服务器。",
            page.account_note.text(),
        )

        config.player_name = "Steve"
        page._refresh_status()
        app.processEvents()
        check(
            "refresh_without_version",
            page.account_name.text() == "Steve",
            page.account_name.text(),
        )
        check(
            "refresh_uuid_without_version",
            page.account_uuid.text() == expected_uuid_text("Steve"),
            page.account_uuid.text(),
        )

        window.close()
        app.processEvents()

    print(f"\nhome_account_name: {PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
