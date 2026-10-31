from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication

from darkitten.config import Config
from darkitten.ui.main_window import MainWindow

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"{name}: OK{(' (' + detail + ')') if detail else ''}")
    else:
        FAIL += 1
        print(f"{name}: FAIL{(' (' + detail + ')') if detail else ''}")


class FakeTray:
    def __init__(self) -> None:
        self.tooltips: list[str] = []
        self.messages: list[tuple[str, str]] = []

    def setToolTip(self, text: str) -> None:
        self.tooltips.append(text)

    def showMessage(self, title: str, message: str, *args) -> None:
        self.messages.append((title, message))


def main() -> int:
    app = QApplication.instance() or QApplication([])
    defaults = Config()
    check("默认开启后台运行", defaults.background_enabled is True)
    check("默认未展示托盘提示", defaults.tray_hint_shown is False)

    temp = tempfile.TemporaryDirectory(prefix="dkl_bg_")
    original_save = Config.save
    save_calls: list[int] = []
    Config.save = lambda self: save_calls.append(1)
    try:
        game = Path(temp.name) / "game"
        version_dir = game / "versions" / "1.21.1"
        version_dir.mkdir(parents=True)
        (version_dir / "1.21.1.json").write_text(
            json.dumps(
                {
                    "id": "1.21.1",
                    "type": "release",
                    "mainClass": "net.minecraft.client.main.Main",
                    "libraries": [],
                }
            ),
            encoding="utf-8",
        )
        window = MainWindow(
            Config(
                game_dir=str(game),
                update_auto_check=False,
                background_enabled=False,
            )
        )
        try:
            check("禁用后台时不创建托盘", window.background_active() is False)
            check(
                "禁用后台时最后一个窗口退出",
                app.quitOnLastWindowClosed() is True,
            )

            fake = FakeTray()
            window.config.background_enabled = True
            window._tray = fake  # type: ignore[assignment]
            window.config.tray_hint_shown = False
            save_calls.clear()
            check("托盘可用状态", window.background_active() is True)

            event = QCloseEvent()
            window.show()
            app.processEvents()
            window.closeEvent(event)
            app.processEvents()
            check("关闭窗口时隐藏", window.isHidden())
            check("关闭事件被忽略", not event.isAccepted())
            check("托盘提示写入设置", window.config.tray_hint_shown is True)
            check("托盘提示只保存一次", len(save_calls) == 1, str(len(save_calls)))
            check(
                "托盘提示文案",
                fake.messages and "系统托盘" in fake.messages[-1][1],
                str(fake.messages[-1] if fake.messages else ""),
            )

            window._show_tray_hint()
            check("第二次不再保存", len(save_calls) == 1, str(len(save_calls)))
            check(
                "托盘悬浮提示",
                fake.tooltips and "正在后台运行" in fake.tooltips[-1],
                fake.tooltips[-1] if fake.tooltips else "",
            )

            window._tray = None  # type: ignore[assignment]
            event2 = QCloseEvent()
            window.closeEvent(event2)
            app.processEvents()
            check("没有托盘时正常退出", event2.isAccepted())
        finally:
            window._tray = None  # type: ignore[assignment]
            window.close()
            app.processEvents()
    finally:
        Config.save = original_save
        temp.cleanup()

    print(f"\nBACKGROUND QA: {PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
