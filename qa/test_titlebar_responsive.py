from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from PySide6.QtCore import QPoint
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from darkitten.config import Config
from darkitten.ui import icons
from darkitten.ui.main_window import MainWindow


def icon_bounds(size: int) -> tuple[int, int, int]:
    image = icons.pixmap("download", "#E23A5E", size).toImage()
    image = image.convertToFormat(QImage.Format_RGBA8888)
    xs = [
        x
        for y in range(image.height())
        for x in range(image.width())
        if QColor.fromRgba(image.pixel(x, y)).alpha() > 24
    ]
    return min(xs), max(xs), image.width()


def main() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    ok = True

    # 图标本身必须左右对称，14px 时不能被裁掉或整体偏移。
    for size in (14, 16, 20):
        left, right, width = icon_bounds(size)
        centered = abs((left + right) - (width - 1)) <= 1
        print(f"download_icon_symmetric_{size}:", "OK" if centered else "FAIL")
        ok &= centered

    with tempfile.TemporaryDirectory() as temp:
        bar_heights: set[int] = set()
        for width in (1500, 1400, 1180, 1179, 1057, 980):
            config = Config(
                game_dir=str(Path(temp) / f"game-{width}"),
                player_name="LFELKEEA",
                window_width=width,
                window_height=720,
                update_auto_check=False,
            )
            window = MainWindow(config)
            window.resize(width, 720)
            window.show()
            app.processEvents()

            buttons = list(window.nav_buttons.values())
            clipped = [b.text() for b in buttons if b.width() < b.sizeHint().width()]
            nav_ok = not clipped
            print(f"nav_unclipped_{width}:", "OK" if nav_ok else f"FAIL {clipped}")
            ok &= nav_ok

            last_nav = buttons[-1]
            nav_right = last_nav.mapTo(window, QPoint(last_nav.width(), 0)).x()
            command_left = window.command_btn.mapTo(window, QPoint(0, 0)).x()
            no_overlap = nav_right <= command_left
            print(f"nav_no_overlap_{width}:", "OK" if no_overlap else "FAIL")
            ok &= no_overlap

            chip = window.account_chip
            chip_right = chip.mapTo(window, QPoint(chip.width(), 0)).x()
            min_left = window.min_btn.mapTo(window, QPoint(0, 0)).x()
            chip_ok = chip_right <= min_left - 2
            print(f"account_fits_{width}:", "OK" if chip_ok else "FAIL")
            ok &= chip_ok

            brand_expected = width >= 1180
            brand_ok = window.brand_name.isVisible() == brand_expected
            print(f"brand_mode_{width}:", "OK" if brand_ok else "FAIL")
            ok &= brand_ok

            compact_expected = width < 1400
            compact_ok = (
                chip.name.isVisible() != compact_expected
                and (chip.width() == 47 if compact_expected else chip.width() > 47)
            )
            print(f"account_mode_{width}:", "OK" if compact_ok else "FAIL")
            ok &= compact_ok

            download = window.download_chip
            icon_center = download.icon_box.mapTo(
                download, QPoint(download.icon_box.width() // 2, 0)
            ).x()
            icon_ok = abs(icon_center - download.width() // 2) <= 1
            print(f"download_icon_center_{width}:", "OK" if icon_ok else "FAIL")
            ok &= icon_ok

            bar_heights.add(window.titlebar.height())
            window.close()
            window.deleteLater()
            app.processEvents()

        bar_ok = len(bar_heights) == 1
        print("titlebar_height_stable:", "OK" if bar_ok else f"FAIL {bar_heights}")
        ok &= bar_ok

        # 拖动窗口跨过断点时，布局必须同步收缩/恢复，而不是只在启动时生效。
        config = Config(
            game_dir=str(Path(temp) / "game-resize"),
            player_name="LFELKEEA",
            window_width=1500,
            window_height=720,
            update_auto_check=False,
        )
        window = MainWindow(config)
        window.resize(1500, 720)
        window.show()
        app.processEvents()
        window.resize(1057, 720)
        app.processEvents()
        dynamic_ok = (
            window.account_chip.width() == 47
            and not window.account_chip.name.isVisible()
            and all(
                b.width() >= b.sizeHint().width()
                for b in window.nav_buttons.values()
            )
            and abs(
                window.download_chip.icon_box.mapTo(
                    window.download_chip,
                    QPoint(window.download_chip.icon_box.width() // 2, 0),
                ).x()
                - window.download_chip.width() // 2
            )
            <= 1
        )
        window.resize(1500, 720)
        app.processEvents()
        dynamic_ok = dynamic_ok and (
            window.account_chip.width() > 47
            and window.account_chip.name.isVisible()
            and window.brand_name.isVisible()
        )
        print("dynamic_resize:", "OK" if dynamic_ok else "FAIL")
        ok &= dynamic_ok
        window.close()
        window.deleteLater()
        app.processEvents()

    print("RESULT:", "OK" if ok else "FAIL")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
