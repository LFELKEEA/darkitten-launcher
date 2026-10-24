from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QScrollArea

from darkitten.config import Config
from darkitten.developer import developer_page_token, set_developer_mode
from darkitten.ui.feedback_dialog import FeedbackDialog
from darkitten.ui.main_window import MainWindow

MainWindow.ensure_java_detected = lambda self, force=False: None
Config.save = lambda self: None

QA = PROJECT / "qa"
app = QApplication.instance() or QApplication(sys.argv)
for font_path in (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
):
    if Path(font_path).exists():
        QFontDatabase.addApplicationFont(font_path)
app.setFont(QFont("Microsoft YaHei UI", 10))

temp = tempfile.TemporaryDirectory()
root = Path(temp.name)
game = root / "game"
(game / "versions" / "1.21.1").mkdir(parents=True)

window = MainWindow(
    Config(
        game_dir=str(game),
        update_auto_check=False,
        developer_unlocked=False,
        developer_page_token="",
    )
)
window._feedback_checked = True
window.resize(1280, 820)
window.apply_theme("dark")
window.show()
window.switch_page("settings")
QTest.qWait(500)

page = window.settings_page
scroll = page.findChild(QScrollArea)
if scroll is not None:
    scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
QTest.qWait(300)
window.grab().save(str(QA / "dkl_1922_settings_developer_locked.png"))

window.config.developer_unlocked = True
window.config.developer_page_token = developer_page_token("screenshot-preview")
set_developer_mode(True)
window._update_account()
QTest.qWait(300)
if scroll is not None:
    scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
QTest.qWait(300)
window.grab().save(str(QA / "dkl_1922_settings_developer_unlocked.png"))

dialog = FeedbackDialog(window, kind="bug")
dialog.show()
QTest.qWait(400)
dialog.grab().save(str(QA / "dkl_1922_feedback_dialog.png"))

dialog.tabs.set_value("table", emit=True)
QTest.qWait(300)
dialog.grab().save(str(QA / "dkl_1922_feedback_dialog_table.png"))
dialog.close()

window.hide()
print("saved:", QA / "dkl_1922_settings_developer_locked.png")
print("saved:", QA / "dkl_1922_settings_developer_unlocked.png")
print("saved:", QA / "dkl_1922_feedback_dialog.png")
print("saved:", QA / "dkl_1922_feedback_dialog_table.png")
QTimer.singleShot(0, app.quit)
app.exec()
temp.cleanup()
