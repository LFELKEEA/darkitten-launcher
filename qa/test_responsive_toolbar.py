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

from darkitten.config import Config
from darkitten.ui import theme
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


def labels_fit(page) -> bool:
    buttons = page._filters.findChildren(type(page.check_btn))
    for button in buttons:
        needed = button.fontMetrics().horizontalAdvance(button.text()) + 24
        if button.width() < needed:
            print(
                "clipped:",
                button.text(),
                "width",
                button.width(),
                "needed",
                needed,
            )
            return False
    return True


def main() -> int:
    app = QApplication.instance() or QApplication([])
    theme.set_current("light")
    app.setStyleSheet(theme.stylesheet("light"))
    Config.save = lambda self: None  # type: ignore[assignment]
    MainWindow.ensure_java_detected = lambda self, force=False: None  # type: ignore[assignment]
    MainWindow._should_auto_check_updates = lambda self: False  # type: ignore[assignment]

    with tempfile.TemporaryDirectory() as tmp:
        cfg = Config(game_dir=str(Path(tmp) / "game"), player_name="Tester")
        Path(cfg.game_dir).mkdir(parents=True)
        window = MainWindow(cfg)
        window.resize(980, 640)
        window.show()
        app.processEvents()
        window.switch_page("mods")
        app.processEvents()

        page = window.mods_page
        check("filters_three_items", len(page._filters._buttons) == 3)
        check("narrow_uses_two_rows", page._toolbar_compact)
        check("narrow_action_row_visible", page._toolbar_actions_row.isVisible())
        check("narrow_filter_labels_fit", labels_fit(page))
        check(
            "segment_min_width_has_padding",
            all(
                button.minimumWidth()
                >= button.fontMetrics().horizontalAdvance(button.text()) + 24
                for button in page._filters._buttons.values()
            ),
        )

        window.resize(1280, 800)
        app.processEvents()
        check("wide_uses_one_row", not page._toolbar_compact)
        check("wide_action_row_hidden", not page._toolbar_actions_row.isVisible())
        check("wide_filter_labels_fit", labels_fit(page))

        window.resize(980, 640)
        app.processEvents()
        check("shrink_returns_two_rows", page._toolbar_compact)
        check("shrink_filter_labels_fit", labels_fit(page))

        window.close()
        app.processEvents()

    print(f"\nresponsive_toolbar: {PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
