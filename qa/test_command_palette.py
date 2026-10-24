from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from darkitten.config import Config
from darkitten.ui.command_palette import CommandPalette
from darkitten.ui.main_window import MainWindow

CHECKS: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name} FAILED {detail}")
    CHECKS.append(name)
    print(f"{name}: OK")


def visible_count(palette: CommandPalette) -> int:
    return sum(
        not palette.list.item(row).isHidden()
        for row in range(palette.list.count())
    )


def main() -> None:
    MainWindow.ensure_java_detected = lambda self, force=False: None
    Config.save = lambda self: None
    app = QApplication.instance() or QApplication(sys.argv)

    temp = tempfile.TemporaryDirectory()
    game = Path(temp.name) / "game"
    (game / "versions" / "1.21.11").mkdir(parents=True)
    config = Config(
        game_dir=str(game),
        update_auto_check=False,
        account={"kind": "msa", "name": "LFELKEEA"},
        microsoft_email="lfel278623@outlook.com",
        developer_unlocked=True,
        developer_page_token="test-page-token",
    )
    window = MainWindow(config)
    window.resize(1280, 820)
    window.apply_theme("dark")
    window.show()
    window._update_account()
    app.processEvents()

    commands = window._palette_commands()
    ids = {item["id"] for item in commands}
    check("palette-command-count", len(commands) >= 20, str(len(commands)))
    check("palette-has-pages", {"page-home", "page-mods", "page-settings"} <= ids)
    check("palette-has-actions", {"launch", "snapshot", "diagnostics-bundle"} <= ids)
    check("palette-developer-inbox", "feedback-inbox" in ids)

    palette = CommandPalette(window, commands)
    check("palette-all-visible", visible_count(palette) == len(commands), str(visible_count(palette)))
    palette.input.setText("诊断")
    app.processEvents()
    check("palette-filter", visible_count(palette) == 2, str(visible_count(palette)))
    palette.input.setText("snapshot")
    app.processEvents()
    check("palette-keyword", visible_count(palette) >= 1, str(visible_count(palette)))
    palette.input.setText("不存在的命令xyz")
    app.processEvents()
    check("palette-empty", visible_count(palette) == 0)

    palette.input.setText("")
    app.processEvents()
    check("palette-reset", visible_count(palette) == len(commands))
    palette.input.setText("设置")
    app.processEvents()
    palette._activate_current()
    check("palette-chosen", palette.chosen == "page-settings", palette.chosen)

    # 键盘上下移动会跳过隐藏项。
    palette2 = CommandPalette(window, commands)
    visible_rows = [
        row for row in range(palette2.list.count())
        if not palette2.list.item(row).isHidden()
    ]
    palette2.list.setCurrentRow(visible_rows[0])
    palette2._move(1)
    check("palette-move-down", palette2.list.currentRow() == visible_rows[1])
    palette2._move(-1)
    check("palette-move-up", palette2.list.currentRow() == visible_rows[0])
    QTest.keyClick(palette2.input, Qt.Key_Down)
    check("palette-key-down", palette2.list.currentRow() == visible_rows[1])

    # 命令执行与主题切换。
    window._run_palette_command("page-settings")
    check("palette-page-switch", window.current_page == "settings", window.current_page)
    old_theme = config.theme
    window._run_palette_command("toggle-theme")
    check("palette-theme-toggle", config.theme != old_theme, config.theme)
    check("palette-shortcut", window.command_shortcut.key().toString() == "Ctrl+K")
    check("palette-button", hasattr(window, "command_btn"))

    # 开发者反馈红点。
    check("feedback-dot-exists", hasattr(window, "feedback_dot"))
    config.feedback_last_seen = "2026-01-01T00:00:00Z"
    old_dot = window.feedback_dot.isHidden()
    window._on_feedback_reminder(
        [
            {
                "id": "1",
                "status": "open",
                "created_at": "2026-10-24T06:00:00Z",
            },
            {
                "id": "2",
                "status": "resolved",
                "created_at": "2026-10-24T07:00:00Z",
            },
        ],
        None,
        "center+github",
        "",
    )
    check("feedback-dot-shown", not window.feedback_dot.isHidden())
    check("feedback-tooltip", "1 条新反馈" in window.feedback_btn.toolTip(), window.feedback_btn.toolTip())
    window._mark_feedback_seen()
    check("feedback-dot-cleared", window.feedback_dot.isHidden())
    check("feedback-seen-updated", config.feedback_last_seen > "2026-10-24T06:00:00Z")

    # 非开发者不应出现收件箱命令。
    config.developer_unlocked = False
    config.developer_page_token = ""
    window._update_account()
    ids = {item["id"] for item in window._palette_commands()}
    check("palette-no-inbox-for-user", "feedback-inbox" not in ids)

    print(f"RESULT: PASS ({len(CHECKS)} checks)")


if __name__ == "__main__":
    main()
