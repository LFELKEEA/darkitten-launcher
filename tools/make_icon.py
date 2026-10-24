"""生成 assets/dkl.ico（多尺寸 PNG 内嵌），供 Windows 使用。"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QGuiApplication

from darkitten.ui import icons

SIZES = (16, 24, 32, 48, 64, 128, 256)


def png_bytes(size: int) -> bytes:
    image = icons.app_icon_image(size)
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return bytes(data)


def build_ico() -> bytes:
    payloads = [(size, png_bytes(size)) for size in SIZES]
    header = struct.pack("<HHH", 0, 1, len(payloads))
    offset = len(header) + 16 * len(payloads)
    entries = bytearray()
    for size, data in payloads:
        dim = 0 if size >= 256 else size
        entries += struct.pack(
            "<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset
        )
        offset += len(data)
    return header + bytes(entries) + b"".join(data for _, data in payloads)


def main() -> int:
    _app = QGuiApplication([])
    target = icons.ASSETS_DIR / "dkl.ico"
    target.write_bytes(build_ico())
    print(f"wrote {target} ({target.stat().st_size} bytes, sizes={SIZES})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
