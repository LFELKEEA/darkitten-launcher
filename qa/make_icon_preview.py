from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPen


def main() -> int:
    app = QGuiApplication.instance() or QGuiApplication([])
    from stellar.ui import icons

    width, height = 760, 380
    canvas = QImage(width, height, QImage.Format_ARGB32)
    canvas.fill(QColor("#171A21"))

    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.Antialiasing, False)

    # faint checkerboard so the rounded transparent corners are visible
    for y in range(0, height, 20):
        for x in range(0, width, 20):
            if (x // 20 + y // 20) % 2 == 0:
                painter.fillRect(QRect(x, y, 20, 20), QColor("#191C24"))

    title = QFont("Segoe UI", 17)
    title.setBold(True)
    painter.setFont(title)
    painter.setPen(QColor("#F4EFF1"))
    painter.drawText(QPoint(32, 38), "Darkitten Launcher 1.9.12")

    subtitle = QFont("Segoe UI", 10)
    painter.setFont(subtitle)
    painter.setPen(QColor("#8E97A6"))
    painter.drawText(QPoint(32, 60), "Demon kitten icon - rose badge, 32x32 source grid, nearest-neighbour scaling")

    big = 256
    painter.drawImage(32, 76, icons.app_icon_image(big))

    baseline = 300
    items = ((128, 334), (64, 486), (48, 564), (32, 628), (16, 676))
    label_font = QFont("Segoe UI", 9)
    painter.setFont(label_font)
    for size, x in items:
        painter.drawImage(x, baseline - size, icons.app_icon_image(size))
        painter.setPen(QColor("#D4DAE4"))
        painter.drawText(
            QRect(x - 10, baseline + 10, size + 20, 18),
            Qt.AlignHCenter | Qt.AlignTop,
            str(size),
        )

    painter.setPen(QPen(QColor("#2B303B"), 1))
    painter.drawLine(32, 348, width - 32, 348)
    painter.setPen(QColor("#6F7888"))
    painter.drawText(
        QPoint(32, 368),
        "ivory fur / crimson eye / rose gradient badge / no antialiasing",
    )
    painter.end()

    target = PROJECT / "qa" / "dkl_icon_preview.png"
    canvas.save(str(target))
    print(f"wrote {target}")
    app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
