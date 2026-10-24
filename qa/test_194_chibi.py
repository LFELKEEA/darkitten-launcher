from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))


def check(label: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{label} failed {detail}")
    print(f"ok  {label}" + (f" · {detail}" if detail else ""))


def main() -> int:
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtWidgets import QApplication, QWidget

    app = QApplication.instance() or QApplication([])
    from darkitten.ui import icons
    from darkitten.ui.widgets import ChibiCompanion, GrassLogo, HeroArt

    logo = icons.logo_pixmap(32)
    app_icon = icons.app_icon_pixmap(32)
    check("internal logo size", logo.size().width() == 32, str(logo.size()))
    logo_image = logo.toImage()
    icon_image = app_icon.toImage()
    same = True
    for x, y in ((10, 1), (10, 30), (7, 18), (5, 12), (27, 18)):
        if logo_image.pixelColor(x, y) != icon_image.pixelColor(x, y):
            same = False
    check("internal logo matches app icon", same)
    check("old dark badge removed", logo_image.pixelColor(0, 0).alpha() == 0)
    grass = GrassLogo(32)
    check("titlebar logo pixmap", not grass._pixmap.isNull())

    face = icons.chibi_face_pixmap(128)
    face_image = face.toImage()
    check("chibi face size", face.size().width() == 128, str(face.size()))
    check("chibi face transparent margin", face_image.pixelColor(0, 0).alpha() == 0)
    check("chibi face opaque centre", face_image.pixelColor(64, 64).alpha() > 0)

    body = icons.chibi_pixmap(240)
    body_image = body.toImage()
    check("chibi body height", body.height() == 240, str(body.size()))
    check("chibi body aspect", body.width() > 150, str(body.size()))
    check("chibi body transparent corner", body_image.pixelColor(0, 0).alpha() == 0)

    host = QWidget()
    host.resize(520, 220)
    host.show()
    companion = ChibiCompanion(60, "download", host)
    companion.move(30, 30)
    companion.show()
    app.processEvents()
    companion._poke()
    check("companion phrase context", companion._phrase in companion.PHRASES["download"], companion._phrase)
    check("companion particles", len(companion._particles) == 7, str(len(companion._particles)))
    check("companion bubble", companion._bubble is not None and companion._bubble.isVisible())
    check("companion bubble text", companion._bubble.text() == companion._phrase)
    for _ in range(8):
        companion._tick()
    image = QImage(520, 220, QImage.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    companion.render(painter, QPoint(0, 0))
    painter.end()
    check("companion painted", not image.isNull())
    companion._hide_bubble()
    check("companion bubble hides", not companion._bubble.isVisible())

    hero = HeroArt()
    hero.resize(420, 240)
    hero._poke()
    check("hero phrase", hero._phrase in hero.PHRASES, hero._phrase)
    check("hero particles", len(hero._particles) == 10, str(len(hero._particles)))
    body_rect = hero._body_rect()
    check("hero portrait", body_rect.height() > 180, str(body_rect))
    check("hero hit area", hero._hit(body_rect.center()))
    hero_image = QImage(420, 240, QImage.Format_ARGB32)
    hero_image.fill(0)
    painter = QPainter(hero_image)
    hero.render(painter, QPoint(0, 0))
    painter.end()
    check("hero painted", not hero_image.isNull())

    host.close()
    app.processEvents()
    print("all 1.9.12 chibi checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
