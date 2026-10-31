from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFont, QFontDatabase, QFontMetrics
from PySide6.QtWidgets import QApplication, QWidget

from darkitten.ui.widgets import ChibiCompanion, HeroArt

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


app = QApplication.instance() or QApplication([])
for font_path in (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
):
    if Path(font_path).exists():
        QFontDatabase.addApplicationFont(font_path)
app.setFont(QFont("Microsoft YaHei UI", 10))

hero = HeroArt()
# 首页 Hero 内容区实际高度：218 - 上下 24px 边距 = 170。
hero.resize(410, 170)

font = hero._bubble_font()
check("hero-bubble-font-size", font.pixelSize() == 16, f"{font.pixelSize()}px")


def check_hero_phrase(group: str, phrase: str) -> None:
    hero._phrase = phrase
    bubble, text_rect, font = hero._bubble_layout()
    metrics = QFontMetrics(font)
    _layout, lines, total_h, max_w = hero._bubble_text_lines(
        font, phrase, text_rect.width()
    )
    inside = (
        bubble.left() >= 4
        and bubble.top() >= 4
        and bubble.right() <= hero.width() - 4
        and bubble.bottom() <= hero.height() - 4
    )
    fits = (
        max_w <= text_rect.width() + 0.5
        and total_h <= text_rect.height() + 0.5
    )
    check(f"hero-{group}-inside", inside, phrase)
    check(f"hero-{group}-fits", fits, f"{max_w:.1f}x{total_h:.1f} in {text_rect.width():.1f}x{text_rect.height():.1f}")
    # 上下各留出至少 1px 的余量，避免字形上下沿贴边被裁切。
    check(
        f"hero-{group}-vertical-pad",
        text_rect.height() - total_h >= 1.0,
        f"pad={text_rect.height() - total_h:.1f}",
    )
    if group == "normal":
        one_line = metrics.horizontalAdvance(phrase) <= text_rect.width() + 1
        check("hero-normal-one-line", one_line, phrase)


for phrase in HeroArt.PHRASES:
    check_hero_phrase("normal", phrase)
for phrase in HeroArt.DEVELOPER_PHRASES:
    check_hero_phrase("developer", phrase)

hero._phrase = "开发者大人，今天也要修 bug 吗"
bubble, text_rect, font = hero._bubble_layout()
_layout, long_lines, _total_h, _max_w = hero._bubble_text_lines(
    font, hero._phrase, text_rect.width()
)
check(
    "hero-long-wraps",
    len(long_lines) >= 2,
    f"lines={len(long_lines)} bubble={bubble.width():.0f}x{bubble.height():.0f}",
)
check("hero-long-fits", text_rect.height() >= 2 * 20, f"text h={text_rect.height():.0f}")

host = QWidget()
host.resize(980, 640)
host.show()
chibi = ChibiCompanion(58, "home", host)
chibi.move(900, 80)
chibi.show()
app.processEvents()


def check_chibi_phrase(group: str, phrase: str) -> None:
    chibi._phrase = phrase
    chibi._show_bubble()
    app.processEvents()
    label = chibi._bubble
    ok = label is not None and label.isVisible()
    check(f"chibi-{group}-visible", bool(ok), phrase)
    if label is None:
        return
    right = label.x() + label.width()
    bottom = label.y() + label.height()
    within = label.x() >= 0 and right <= host.width() and label.y() >= 0 and bottom <= host.height()
    check(f"chibi-{group}-inside", within, f"{label.width()}x{label.height()} at {label.x()},{label.y()}")
    check(f"chibi-{group}-text", label.text() == phrase, phrase)


chibi._phrase = ""
for phrases in ChibiCompanion.PHRASES.values():
    for phrase in phrases:
        check_chibi_phrase("normal", phrase)
for phrases in ChibiCompanion.DEVELOPER_PHRASES.values():
    for phrase in phrases:
        check_chibi_phrase("developer", phrase)

chibi._hide_bubble()
chibi._click_at = time.monotonic()
check("chibi-bubble-hidden", chibi._bubble is not None and not chibi._bubble.isVisible())

host.close()
print(f"RESULT: {'PASS' if FAIL == 0 else 'FAIL'} ({PASS} ok, {FAIL} failed)")
sys.exit(0 if FAIL == 0 else 1)
