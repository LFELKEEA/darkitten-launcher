from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPolygonF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from darkitten.ui.skin_preview import SkinPreviewWidget, _polygon_area  # noqa: E402

CHECKS: list[str] = []
RED = QColor(255, 0, 0, 255)


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name} FAILED {detail}")
    CHECKS.append(name)
    print(f"{name}: OK")


def blank_skin() -> QImage:
    image = QImage(64, 64, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    return image


def fill_rect(image: QImage, x: int, y: int, width: int, height: int) -> None:
    for yy in range(y, y + height):
        for xx in range(x, x + width):
            image.setPixelColor(xx, yy, RED)


def red_mask(image: QImage) -> set[tuple[int, int]]:
    mask: set[tuple[int, int]] = set()
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            if color.red() >= 180 and color.green() <= 80 and color.blue() <= 80:
                mask.add((x, y))
    return mask


def frozen_pose(arm: float) -> dict[str, float]:
    return {
        "head_x": 0.0,
        "head_y": 0.0,
        "body_x": 0.0,
        "arm_l": -arm,
        "arm_r": arm,
        "arm_r_z": 0.0,
        "leg_l": arm * 0.9,
        "leg_r": -arm * 0.9,
        "bob": 0.4,
        "shift": 0.0,
        "yaw": 0.0,
    }


def arm_skin(overlay: bool) -> QImage:
    image = blank_skin()
    if overlay:
        fill_rect(image, 40, 32, 11, 16)  # right arm overlay
        fill_rect(image, 48, 48, 11, 16)  # left arm overlay
    else:
        fill_rect(image, 40, 16, 11, 16)  # right arm base
        fill_rect(image, 32, 48, 11, 16)  # left arm base
    return image


def render(widget: SkinPreviewWidget, app: QApplication, arm: float) -> QImage:
    widget._pose = lambda: frozen_pose(arm)  # type: ignore[method-assign]
    app.processEvents()
    return widget.grab().toImage()


def main() -> None:
    app = QApplication.instance() or QApplication([])

    forwarded = QPolygonF(
        [
            QPointF(0.0, 0.0),
            QPointF(1.0, 0.0),
            QPointF(1.0, 1.0),
            QPointF(0.0, 1.0),
        ]
    )
    reversed_polygon = QPolygonF(list(reversed(list(forwarded))))
    check("polygon-area-positive", _polygon_area(forwarded) == 1.0, str(_polygon_area(forwarded)))
    check(
        "polygon-area-negative",
        _polygon_area(reversed_polygon) == -1.0,
        str(_polygon_area(reversed_polygon)),
    )

    widget = SkinPreviewWidget()
    widget.resize(240, 260)
    widget.set_variant("slim")
    widget.set_action("walk")
    widget.show()
    app.processEvents()

    widget.set_skin(arm_skin(overlay=False))
    widget._mirror_left_arm = False
    base_masks: dict[float, set[tuple[int, int]]] = {}
    for arm in (-30.0, -22.5, -15.0, -7.5, 0.0, 7.5, 15.0, 22.5, 30.0):
        base_masks[arm] = red_mask(render(widget, app, arm))

    widget.set_skin(arm_skin(overlay=True))
    widget._mirror_left_arm = False

    for arm in (-30.0, -22.5, -15.0, -7.5, 0.0, 7.5, 15.0, 22.5, 30.0):
        overlay_mask = red_mask(render(widget, app, arm))
        base_mask = base_masks[arm]
        union = base_mask | overlay_mask
        diff = base_mask ^ overlay_mask
        ratio = len(diff) / max(1, len(union))
        check(
            f"walk-arm-overlay-flush-{arm:.1f}",
            len(base_mask) > 100 and len(overlay_mask) > 100 and ratio < 0.02,
            f"base={len(base_mask)} overlay={len(overlay_mask)} diff={len(diff)} ratio={ratio:.4f}",
        )

    widget.close()
    print(f"\n{len(CHECKS)} checks passed")


if __name__ == "__main__":
    main()
