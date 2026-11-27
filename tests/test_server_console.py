"""服务端控制台指令输入框的大小写输入测试（不显示窗口）。"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from darkitten.ui.pages.server import ConsoleCommandEdit  # noqa: E402


def app() -> QApplication:
    instance = QApplication.instance()
    return instance if instance is not None else QApplication([])


class ConsoleCommandEditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = app()

    def setUp(self) -> None:
        patcher = mock.patch(
            "darkitten.ui.pages.server._caps_lock_on", return_value=False
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.edit = ConsoleCommandEdit()
        self.edit.show()
        self.edit.setFocus()

    def tearDown(self) -> None:
        self.edit.deleteLater()
        QApplication.processEvents()

    def test_shift_letter_becomes_uppercase(self) -> None:
        QTest.keyClick(self.edit, Qt.Key_A, Qt.ShiftModifier)
        self.assertEqual(self.edit.text(), "A")

    def test_plain_letter_stays_lowercase(self) -> None:
        QTest.keyClick(self.edit, Qt.Key_B)
        self.assertEqual(self.edit.text(), "b")

    def test_mixed_command_keeps_original_case(self) -> None:
        QTest.keyClicks(self.edit, "say Hello World")
        self.assertEqual(self.edit.text(), "say Hello World")

    def test_shift_left_arrow_does_not_insert_letters(self) -> None:
        self.edit.setText("abc")
        self.edit.end(False)
        QTest.keyClick(self.edit, Qt.Key_Left, Qt.ShiftModifier)
        self.assertEqual(self.edit.text(), "abc")

    def test_caps_lock_letter_becomes_uppercase(self) -> None:
        with mock.patch(
            "darkitten.ui.pages.server._caps_lock_on", return_value=True
        ):
            QTest.keyClick(self.edit, Qt.Key_C)
        self.assertEqual(self.edit.text(), "C")

    def test_shift_with_caps_lock_stays_lowercase(self) -> None:
        with mock.patch(
            "darkitten.ui.pages.server._caps_lock_on", return_value=True
        ):
            QTest.keyClick(self.edit, Qt.Key_D, Qt.ShiftModifier)
        self.assertEqual(self.edit.text(), "d")


if __name__ == "__main__":
    unittest.main()
