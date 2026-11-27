"""OC 头像右键长按的交互测试（离屏运行）。"""

from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from darkitten.ui.widgets import ChibiCompanion, HeroArt  # noqa: E402


class Host(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0
        self.chat_calls = 0
        self.last_pos = None

    def open_oc_menu(self, pos=None) -> None:
        self.calls += 1
        self.last_pos = pos

    def open_oc_chat(self) -> None:
        self.chat_calls += 1


class HoldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _pump(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self._app.processEvents()
            time.sleep(0.01)
        self._app.processEvents()

    def test_chibi_right_hold_opens_menu_once(self) -> None:
        host = Host()
        host.resize(320, 200)
        chibi = ChibiCompanion(64, "home", host)
        chibi.move(20, 20)
        host.show()
        self._pump(0.05)

        QTest.mousePress(chibi, Qt.RightButton, Qt.NoModifier, QPoint(32, 32))
        self.assertTrue(chibi._hold_active)
        self._pump(0.8)
        self.assertEqual(host.calls, 1)
        self.assertEqual(host.chat_calls, 0)
        # 松开不应再次触发
        QTest.mouseRelease(chibi, Qt.RightButton, Qt.NoModifier, QPoint(32, 32))
        self._pump(0.2)
        self.assertEqual(host.calls, 1)
        self.assertEqual(host.chat_calls, 0)
        host.close()

    def test_chibi_short_right_click_opens_chat(self) -> None:
        host = Host()
        host.resize(320, 200)
        chibi = ChibiCompanion(64, "home", host)
        chibi.move(20, 20)
        host.show()
        self._pump(0.05)

        QTest.mousePress(chibi, Qt.RightButton, Qt.NoModifier, QPoint(32, 32))
        self._pump(0.1)
        QTest.mouseRelease(chibi, Qt.RightButton, Qt.NoModifier, QPoint(32, 32))
        self._pump(0.7)
        self.assertEqual(host.calls, 0)
        self.assertEqual(host.chat_calls, 1)
        host.close()

    def test_left_click_still_pokes(self) -> None:
        host = Host()
        host.resize(320, 200)
        chibi = ChibiCompanion(64, "home", host)
        chibi.move(20, 20)
        phrases: list[str] = []
        chibi.clicked.connect(phrases.append)
        host.show()
        self._pump(0.05)

        QTest.mouseClick(chibi, Qt.LeftButton, Qt.NoModifier, QPoint(32, 32))
        self._pump(0.05)
        self.assertEqual(len(phrases), 1)
        self.assertEqual(host.calls, 0)
        host.close()

    def test_hero_hold_only_inside_body(self) -> None:
        host = Host()
        host.resize(500, 320)
        hero = HeroArt(host)
        hero.resize(500, 320)
        host.show()
        hero.show()
        self._pump(0.05)

        outside = QPoint(10, 10)
        self.assertFalse(hero._hit(outside))
        QTest.mousePress(hero, Qt.RightButton, Qt.NoModifier, outside)
        self.assertFalse(hero._hold_active)

        center = hero._body_rect().center().toPoint()
        QTest.mousePress(hero, Qt.RightButton, Qt.NoModifier, center)
        self.assertTrue(hero._hold_active)
        self._pump(0.8)
        self.assertEqual(host.calls, 1)
        QTest.mouseRelease(hero, Qt.RightButton, Qt.NoModifier, center)
        self.assertEqual(host.chat_calls, 0)
        host.close()

    def test_hero_short_right_click_opens_chat_only_inside_body(self) -> None:
        host = Host()
        host.resize(500, 320)
        hero = HeroArt(host)
        hero.resize(500, 320)
        host.show()
        hero.show()
        self._pump(0.05)

        outside = QPoint(10, 10)
        QTest.mousePress(hero, Qt.RightButton, Qt.NoModifier, outside)
        QTest.mouseRelease(hero, Qt.RightButton, Qt.NoModifier, outside)
        self.assertEqual(host.chat_calls, 0)

        center = hero._body_rect().center().toPoint()
        QTest.mousePress(hero, Qt.RightButton, Qt.NoModifier, center)
        self._pump(0.1)
        QTest.mouseRelease(hero, Qt.RightButton, Qt.NoModifier, center)
        self._pump(0.7)
        self.assertEqual(host.calls, 0)
        self.assertEqual(host.chat_calls, 1)
        host.close()


if __name__ == "__main__":
    unittest.main()