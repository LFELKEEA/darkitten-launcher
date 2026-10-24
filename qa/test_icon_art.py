from __future__ import annotations

import os
import struct
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


def test_pixel_art() -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QGuiApplication

    _app = QGuiApplication.instance() or QGuiApplication([])
    from darkitten.ui import icons

    image = icons.app_icon_image(32)
    check("32px canvas", image.size().width() == 32 and image.size().height() == 32)
    check("rounded corner transparent", image.pixelColor(0, 0).alpha() == 0)
    check(
        "rose badge top",
        image.pixelColor(10, 1).name() == "#e92e64",
        image.pixelColor(10, 1).name(),
    )
    check(
        "rose badge bottom",
        image.pixelColor(10, 30).name() == "#690b23",
        image.pixelColor(10, 30).name(),
    )
    check(
        "ivory ear on the left",
        image.pixelColor(5, 12).name() == "#fff6ee",
        image.pixelColor(5, 12).name(),
    )
    check(
        "ivory body on the right",
        image.pixelColor(18, 18).name() == "#fff6ee",
        image.pixelColor(18, 18).name(),
    )
    check(
        "crimson eye",
        image.pixelColor(7, 18).name() == "#d81b4a",
        image.pixelColor(7, 18).name(),
    )
    check(
        "tail on the right",
        image.pixelColor(27, 18).name() == "#fff6ee",
        image.pixelColor(27, 18).name(),
    )
    check(
        "cheek blush",
        image.pixelColor(9, 20).name() == "#f7dfe1",
        image.pixelColor(9, 20).name(),
    )
    check(
        "haunch shading",
        image.pixelColor(22, 19).name() == "#f7dfe1",
        image.pixelColor(22, 19).name(),
    )

    body_colors = set()
    for y in range(32):
        for x in range(32):
            color = image.pixelColor(x, y)
            if color.alpha():
                body_colors.add(color.name())
    check("rose gradient palette", 28 <= len(body_colors) <= 36, f"{len(body_colors)} colors")
    check("old grass green removed", "#7ec850" not in body_colors and "#5ebb3e" not in body_colors)

    small = icons.app_icon_image(16)
    check("eye survives 16px", small.pixelColor(3, 9).name() == "#d81b4a")
    check("eye slit survives 16px", small.pixelColor(3, 10).name() == "#d81b4a")

    large = icons.app_icon_image(256)
    check("256px canvas", large.size().width() == 256 and large.size().height() == 256)
    uniform = True
    for y in range(120, 128):
        for x in range(80, 88):
            if large.pixelColor(x, y).name() != "#fff6ee":
                uniform = False
    check("nearest-neighbour pixels", uniform)

    for size in (16, 24, 48, 64, 128, 256):
        scaled = icons.app_icon_image(size)
        check(f"{size}px generated", scaled.width() == size and scaled.height() == size)


def test_ico() -> None:
    from PySide6.QtGui import QImage

    ico = PROJECT / "assets" / "dkl.ico"
    payload = ico.read_bytes()
    check("ico exists", ico.is_file() and len(payload) > 1000, f"{len(payload)} bytes")
    reserved, kind, count = struct.unpack("<HHH", payload[:6])
    check("ico header", (reserved, kind, count) == (0, 1, 7), f"count={count}")

    expected = {16, 24, 32, 48, 64, 128, 256}
    found = set()
    for index in range(count):
        entry = payload[6 + index * 16 : 6 + (index + 1) * 16]
        width, height, _, _, _, _, length, offset = struct.unpack("<BBBBHHII", entry)
        size = 256 if width == 0 else width
        found.add(size)
        data = payload[offset : offset + length]
        decoded = QImage.fromData(data, "PNG")
        check(
            f"ico {size}px decodes",
            not decoded.isNull() and decoded.width() == size,
            f"{decoded.width()}x{decoded.height()}",
        )
    check("ico sizes", found == expected, str(sorted(found)))


def main() -> int:
    test_pixel_art()
    test_ico()
    print("all icon checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
