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
RED = (255, 0, 0)
GREEN = (0, 220, 0)
YELLOW = (255, 230, 0)
BLUE = (0, 80, 255)
MAGENTA = (255, 0, 220)
CYAN = (0, 220, 255)


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name} FAILED {detail}")
    CHECKS.append(name)
    print(f"{name}: OK")


def blank_skin() -> QImage:
    image = QImage(64, 64, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    return image


def fill_rect(
    image: QImage, x: int, y: int, width: int, height: int, color: tuple[int, int, int]
) -> None:
    value = QColor(color[0], color[1], color[2], 255)
    for yy in range(y, y + height):
        for xx in range(x, x + width):
            image.setPixelColor(xx, yy, value)


def color_mask(image: QImage, color: str) -> set[tuple[int, int]]:
    mask: set[tuple[int, int]] = set()
    for y in range(image.height()):
        for x in range(image.width()):
            c = image.pixelColor(x, y)
            r, g, b = c.red(), c.green(), c.blue()
            if color == "red":
                hit = r >= 150 and g <= 100 and b <= 100
            elif color == "green":
                hit = g >= 150 and r <= 120 and b <= 120
            elif color == "yellow":
                hit = r >= 180 and g >= 180 and b <= 110
            else:
                raise ValueError(color)
            if hit:
                mask.add((x, y))
    return mask


def mean_xy(mask: set[tuple[int, int]]) -> tuple[float, float]:
    count = max(1, len(mask))
    return (
        sum(x for x, _ in mask) / count,
        sum(y for _, y in mask) / count,
    )


def near(mask: set[tuple[int, int]], x: int, y: int, radius: int) -> bool:
    for yy in range(y - radius, y + radius + 1):
        for xx in range(x - radius, x + radius + 1):
            if (xx, yy) in mask:
                return True
    return False


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


def solid_arm_skin(base: bool, overlay: bool) -> QImage:
    image = blank_skin()
    if base:
        fill_rect(image, 40, 16, 16, 16, RED)
    if overlay:
        fill_rect(image, 40, 32, 16, 16, GREEN)
    return image


def face_skin() -> QImage:
    image = blank_skin()
    # 右臂 slim 6 个面分别涂色，用于锁定 UV 方向。
    fill_rect(image, 40, 20, 4, 12, RED)  # right
    fill_rect(image, 44, 20, 3, 6, GREEN)  # front 上半
    fill_rect(image, 44, 26, 3, 6, YELLOW)  # front 下半（手部）
    fill_rect(image, 47, 20, 4, 12, BLUE)  # left
    fill_rect(image, 51, 20, 3, 12, MAGENTA)  # back
    fill_rect(image, 44, 16, 3, 4, CYAN)  # top
    fill_rect(image, 47, 16, 3, 4, BLUE)  # bottom
    return image


def render(widget: SkinPreviewWidget, app: QApplication, arm: float = 0.0) -> QImage:
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
    widget.resize(420, 520)
    widget.set_variant("slim")
    widget.set_action("walk")
    widget.show()
    app.processEvents()

    # 1. 只画第二层时也必须可见，防止外层被整层跳过。
    widget.set_skin(solid_arm_skin(base=False, overlay=True))
    widget._mirror_left_arm = False
    overlay_only = color_mask(render(widget, app), "green")
    check(
        "overlay-only-renders",
        len(overlay_only) > 200,
        f"green={len(overlay_only)}",
    )

    # 2. 叠加层是外扩薄壳：必须超出底层轮廓，但不能脱离底层。
    widget.set_skin(solid_arm_skin(base=True, overlay=False))
    widget._mirror_left_arm = False
    base_masks = {
        arm: color_mask(render(widget, app, arm), "red")
        for arm in (-30.0, -15.0, 0.0, 15.0, 30.0)
    }

    widget.set_skin(solid_arm_skin(base=True, overlay=True))
    widget._mirror_left_arm = False
    for arm, base_mask in base_masks.items():
        overlay_mask = color_mask(render(widget, app, arm), "green")
        outside = {p for p in overlay_mask if not near(base_mask, p[0], p[1], 2)}
        detached = {p for p in overlay_mask if not near(base_mask, p[0], p[1], 8)}
        ratio = len(outside) / max(1, len(overlay_mask))
        check(
            f"overlay-shell-visible-{arm:.1f}",
            len(overlay_mask) > 200 and len(outside) > 0 and ratio > 0.01,
            f"green={len(overlay_mask)} outside={len(outside)} ratio={ratio:.3f}",
        )
        check(
            f"overlay-shell-attached-{arm:.1f}",
            not detached,
            f"detached={len(detached)}",
        )

    # 3. 手臂贴图方向：右侧面在屏幕左侧，手部（front 下半）在下方。
    widget.set_skin(face_skin())
    widget._mirror_left_arm = False
    face_image = render(widget, app)
    right_face = color_mask(face_image, "red")
    front_top = color_mask(face_image, "green")
    front_bottom = color_mask(face_image, "yellow")
    check(
        "arm-face-colors-render",
        len(right_face) > 100 and len(front_top) > 30 and len(front_bottom) > 30,
        f"right={len(right_face)} top={len(front_top)} bottom={len(front_bottom)}",
    )
    rx, _ = mean_xy(right_face)
    fx, _ = mean_xy(front_top | front_bottom)
    _, ty = mean_xy(front_top)
    _, by = mean_xy(front_bottom)
    check("arm-side-left-of-front", rx < fx, f"right_x={rx:.1f} front_x={fx:.1f}")
    check("arm-hand-below-sleeve", by > ty, f"top_y={ty:.1f} bottom_y={by:.1f}")

    # 4. 只有背面有像素时，外层检测和左臂镜像回退都不能漏判。
    head_part = next(part for part in widget._parts() if part.name == "head")
    back_only = blank_skin()
    fill_rect(back_only, 56, 0, 8, 16, RED)  # head overlay back face
    widget.set_skin(back_only)
    check("overlay-detects-back-face-only", widget._has_overlay(head_part))

    arm_back_only = blank_skin()
    fill_rect(arm_back_only, 43, 52, 3, 12, RED)  # left arm base back face
    widget.set_skin(arm_back_only)
    check("left-arm-back-face-not-mirrored", widget._mirror_left_arm is False)

    widget.close()
    print(f"\n{len(CHECKS)} checks passed")


if __name__ == "__main__":
    main()
