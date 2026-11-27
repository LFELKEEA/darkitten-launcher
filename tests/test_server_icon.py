"""服务端图标裁剪逻辑的单元测试（不启动界面）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtGui import QColor, QImage  # noqa: E402

from darkitten.servers import required_java_major  # noqa: E402
from darkitten.ui.pages.server import (  # noqa: E402
    SERVER_ICON_SIZE,
    _fit_server_icon,
)


class FitServerIconTests(unittest.TestCase):
    def test_wide_image_is_center_cropped_and_scaled(self) -> None:
        image = QImage(120, 60, QImage.Format_ARGB32)
        image.fill(QColor("#FF0000"))
        painter_image = image.copy(0, 0, 20, 60)
        painter_image.fill(QColor("#00FF00"))
        from PySide6.QtGui import QPainter

        painter = QPainter(image)
        painter.drawImage(0, 0, painter_image)
        painter.end()

        result = _fit_server_icon(image)
        self.assertEqual(result.width(), SERVER_ICON_SIZE)
        self.assertEqual(result.height(), SERVER_ICON_SIZE)
        # 左右各裁掉 30px，结果里不应该再出现最左边的绿色。
        self.assertEqual(QColor(result.pixel(0, 32)).name(), "#ff0000")

    def test_square_image_keeps_center_pixels(self) -> None:
        image = QImage(64, 64, QImage.Format_ARGB32)
        image.fill(QColor("#3366CC"))
        result = _fit_server_icon(image)
        self.assertEqual(result.size(), image.size())
        self.assertEqual(QColor(result.pixel(32, 32)).name(), "#3366cc")

    def test_null_image_is_returned_unchanged(self) -> None:
        result = _fit_server_icon(QImage())
        self.assertTrue(result.isNull())


class RequiredJavaMajorTests(unittest.TestCase):
    def test_year_based_versions_need_java_25(self) -> None:
        self.assertEqual(required_java_major("26.3"), 25)
        self.assertEqual(required_java_major("26.1"), 25)

    def test_legacy_versions_keep_their_requirements(self) -> None:
        self.assertEqual(required_java_major("1.21.11"), 21)
        self.assertEqual(required_java_major("1.20.1"), 17)
        self.assertEqual(required_java_major("1.16.5"), 8)


if __name__ == "__main__":
    unittest.main()
