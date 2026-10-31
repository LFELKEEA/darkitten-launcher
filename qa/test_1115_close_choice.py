from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication

from darkitten.config import Config
from darkitten.ui.dialogs import CloseChoiceDialog
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
        self.hidden = False
        self.menu = None

    def setToolTip(self, text: str) -> None:
        self.tooltips.append(text)

    def showMessage(self, title: str, message: str, *args) -> None:
        self.messages.append((title, message))

    def hide(self) -> None:
        self.hidden = True

    def setContextMenu(self, menu) -> None:
        self.menu = menu

    def deleteLater(self) -> None:
        pass


HELPER_SOURCE = """
import json
import sys
import tempfile
from pathlib import Path

project = Path(sys.argv[2])
sys.path.insert(0, str(project))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from darkitten.config import Config
from darkitten.ui.dialogs import CloseChoiceDialog
from darkitten.ui.main_window import MainWindow

Config.save = lambda self: None
app = QApplication([])
temp = tempfile.TemporaryDirectory(prefix="dkl_close_helper_")
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
        background_enabled=True,
    )
)


class FakeTray:
    def setToolTip(self, text):
        pass

    def showMessage(self, *args):
        pass

    def hide(self):
        pass

    def setContextMenu(self, menu):
        pass

    def deleteLater(self):
        pass


window._tray = FakeTray()
window.show()
mode = sys.argv[1]


def trigger():
    if mode == "tray":
        window.quit_from_tray()
    else:
        window._show_close_choice()
        dialog = window._close_choice_dialog
        if dialog is not None:
            dialog._choose(CloseChoiceDialog.EXIT)


def timed_out():
    print("timeout", flush=True)
    app.quit()


QTimer.singleShot(400, trigger)
QTimer.singleShot(8000, timed_out)
code = app.exec()
print("quit", code, flush=True)
"""


def subprocess_case(helper: Path, mode: str) -> None:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONIOENCODING"] = "utf-8"
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, str(helper), mode, str(PROJECT)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(PROJECT),
        timeout=20,
    )
    elapsed = time.monotonic() - started
    output = (result.stdout or "") + (result.stderr or "")
    check(
        f"{mode} 退出后事件循环结束",
        result.returncode == 0 and "quit" in output and "timeout" not in output,
        output.strip(),
    )
    check(f"{mode} 退出及时", elapsed < 5.0, f"{elapsed:.2f}s")


def main() -> int:
    app = QApplication.instance() or QApplication([])
    original_save = Config.save
    Config.save = lambda self: None
    temp = tempfile.TemporaryDirectory(prefix="dkl_close_")
    try:
        helper = Path(temp.name) / "close_helper.py"
        helper.write_text(HELPER_SOURCE, encoding="utf-8")
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
                background_enabled=True,
            )
        )
        quit_calls: list[int] = []
        window._quit_application = lambda: quit_calls.append(1)  # type: ignore[method-assign]
        app.setQuitOnLastWindowClosed(False)
        try:
            tray = FakeTray()
            window._tray = tray  # type: ignore[assignment]
            window.config.tray_hint_shown = False

            # 无界面 QA 不弹选择框，仍然直接隐藏到托盘。
            window.show()
            app.processEvents()
            event = QCloseEvent()
            window.closeEvent(event)
            app.processEvents()
            check("无界面关闭仍隐藏到托盘", window.isHidden())
            check("无界面关闭事件被忽略", not event.isAccepted())
            check("关闭选择框未创建", window._close_choice_dialog is None)

            # 真实窗口关闭时显示三选一，选择后台运行。
            window.show()
            app.processEvents()
            window._close_choice_dialog = None
            window._show_close_choice()
            app.processEvents()
            dialog = window._close_choice_dialog
            check("创建关闭选择框", dialog is not None)
            check("关闭选择框可见", bool(dialog and dialog.isVisible()))
            if dialog is not None:
                dialog._choose(CloseChoiceDialog.BACKGROUND)
                app.processEvents()
            check("选择后台运行后隐藏窗口", window.isHidden())
            check("选择后台运行后清理对话框", window._close_choice_dialog is None)
            check("选择后台运行不退出进程", not quit_calls)

            # 取消：窗口保持可见，不退出。
            window.show()
            app.processEvents()
            window._show_close_choice()
            app.processEvents()
            dialog = window._close_choice_dialog
            if dialog is not None:
                dialog.reject()
                app.processEvents()
            check("取消关闭后窗口仍显示", window.isVisible())
            check("取消关闭不退出进程", not quit_calls)
            check("取消关闭清理对话框", window._close_choice_dialog is None)

            # 选择退出程序：走安全确认并真正结束事件循环。
            window.show()
            app.processEvents()
            window._show_close_choice()
            app.processEvents()
            dialog = window._close_choice_dialog
            if dialog is not None:
                dialog._choose(CloseChoiceDialog.EXIT)
                app.processEvents()
            check("选择退出程序后窗口关闭", window.isHidden())
            check("选择退出程序后标记强制退出", window._force_quit is True)
            check("选择退出程序后结束事件循环", quit_calls == [1], str(quit_calls))
            check("选择退出程序后托盘已移除", window._tray is None)

            # 复现用户反馈：窗口已经隐藏在托盘时，托盘菜单退出也必须结束进程。
            tray2 = FakeTray()
            window._tray = tray2  # type: ignore[assignment]
            window._force_quit = False
            window._quit_requested = False
            quit_calls.clear()
            window.show()
            app.processEvents()
            hidden_event = QCloseEvent()
            window.closeEvent(hidden_event)
            app.processEvents()
            check("复现窗口隐藏到托盘", window.isHidden() and not hidden_event.isAccepted())
            window.quit_from_tray()
            app.processEvents()
            check("托盘退出后窗口保持隐藏", window.isHidden())
            check("托盘退出后标记强制退出", window._force_quit is True)
            check("托盘退出后结束事件循环", quit_calls == [1], str(quit_calls))
            check("托盘退出后托盘已移除", window._tray is None)
        finally:
            window._tray = None  # type: ignore[assignment]
            window._force_quit = False
            window.close()
            app.processEvents()

        subprocess_case(helper, "tray")
        subprocess_case(helper, "choice")
    finally:
        Config.save = original_save
        temp.cleanup()

    print(f"\nCLOSE CHOICE QA: {PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
