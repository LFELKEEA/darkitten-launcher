"""指令生成器选择框交互的单元测试（不显示窗口）。"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QWheelEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from darkitten.ui.widgets import ChoiceComboBox, NoWheelSpinBox  # noqa: E402


def app() -> QApplication:
    instance = QApplication.instance()
    return instance if instance is not None else QApplication([])


def combo() -> ChoiceComboBox:
    widget = ChoiceComboBox()
    widget.addItem("等级（levels）", "levels")
    widget.addItem("秒（seconds）", "seconds")
    widget.setCurrentIndex(0)
    widget.resize(240, 34)
    return widget


def wheel_event() -> QWheelEvent:
    return QWheelEvent(
        QPointF(10, 10),
        QPointF(10, 10),
        QPoint(0, 0),
        QPoint(0, -120),
        Qt.NoButton,
        Qt.NoModifier,
        Qt.NoScrollPhase,
        False,
    )


class ChoiceComboTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = app()

    def tearDown(self) -> None:
        QApplication.processEvents()

    def test_text_field_is_always_editable(self) -> None:
        widget = combo()
        self.assertTrue(widget.isEditable())
        self.assertFalse(widget.lineEdit().isReadOnly())

    def test_arrow_button_opens_list(self) -> None:
        widget = combo()
        widget.show()
        QTest.mouseClick(
            widget,
            Qt.LeftButton,
            pos=QPoint(widget.width() - 15, widget.height() // 2),
        )
        self.assertTrue(widget.view().isVisible())
        widget.hidePopup()

    def test_clicking_text_area_does_not_open_list(self) -> None:
        widget = combo()
        widget.show()
        QTest.mouseClick(
            widget,
            Qt.LeftButton,
            pos=QPoint(12, widget.height() // 2),
        )
        self.assertFalse(widget.view().isVisible())

    def test_selecting_item_fills_field(self) -> None:
        widget = combo()
        widget.setCurrentIndex(1)
        self.assertEqual(widget.currentText(), "秒（seconds）")
        self.assertEqual(widget.currentData(), "seconds")

    def test_custom_text_is_not_added_to_list(self) -> None:
        widget = combo()
        widget.setEditText("tick")
        self.assertEqual(widget.count(), 2)
        self.assertEqual(widget.currentText(), "tick")

    def test_wheel_does_not_change_value(self) -> None:
        widget = combo()
        widget.wheelEvent(wheel_event())
        self.assertEqual(widget.currentIndex(), 0)

    def test_assistant_choice_field_uses_editable_combo(self) -> None:
        from darkitten import game_assistant as ga
        from darkitten import game_ids
        from darkitten.ui.pages.assistant import AssistantPage

        field = ga.TemplateField(
            key="unit",
            label="单位",
            default="levels",
            kind="choice",
            options=(
                game_ids.Option("levels", "等级"),
                game_ids.Option("seconds", "秒"),
            ),
        )
        widget = AssistantPage._make_field_widget(object(), field)
        self.assertIsInstance(widget, ChoiceComboBox)
        self.assertFalse(widget.lineEdit().isReadOnly())
        self.assertEqual(widget.currentData(), "levels")
        self.assertEqual(widget.count(), 2)

    def test_spin_box_ignores_wheel(self) -> None:
        spin = NoWheelSpinBox()
        spin.setRange(0, 100)
        spin.setValue(5)
        spin.wheelEvent(wheel_event())
        self.assertEqual(spin.value(), 5)


if __name__ == "__main__":
    unittest.main()
