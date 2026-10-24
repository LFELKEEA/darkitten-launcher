from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from darkitten.ui.skin_preview import (  # noqa: E402
    SkinPreviewWidget,
    _rotate_about_x,
    _rotate_about_y,
)

CHECKS: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name} FAILED {detail}")
    CHECKS.append(name)
    print(f"{name}: OK")


def main() -> None:
    app = QApplication.instance() or QApplication([])
    widget = SkinPreviewWidget()
    widget.resize(360, 360)
    widget.show()
    app.processEvents()

    keys = [key for key, _label in SkinPreviewWidget.ACTIONS]
    check("actions-count", len(keys) >= 7, str(keys))
    for expected in ("idle", "walk", "run", "sneak", "wave", "sit", "spin"):
        check(f"actions-has-{expected}", expected in keys, str(keys))
    check(
        "minimum-size",
        widget.minimumWidth() <= 240 and widget.minimumHeight() <= 260,
        f"{widget.minimumWidth()}x{widget.minimumHeight()}",
    )

    for key in keys:
        widget.set_action(key)
        app.processEvents()
        check(f"action-{key}", widget.action() == key, widget.action())
        image = widget.grab().toImage()
        check(
            f"render-{key}",
            not image.isNull() and image.width() > 0 and image.height() > 0,
            f"{image.width()}x{image.height()}",
        )

    widget.set_action("not-a-real-action")
    check("action-invalid-kept", widget.action() in keys, widget.action())

    widget.set_action("run")
    run_pose = widget._pose()
    check("run-leans-forward", run_pose["body_x"] > 0, str(run_pose))
    check("run-head-compensates", run_pose["head_x"] < 0, str(run_pose))

    widget.set_action("sneak")
    sneak_pose = widget._pose()
    check("sneak-leans-forward", sneak_pose["body_x"] > 0, str(sneak_pose))
    check("sneak-head-compensates", sneak_pose["head_x"] < 0, str(sneak_pose))

    widget.set_action("sit")
    sit_pose = widget._pose()
    check("sit-left-leg-splayed", sit_pose["leg_l_y"] > 0, str(sit_pose))
    check("sit-right-leg-splayed", sit_pose["leg_r_y"] < 0, str(sit_pose))
    # 脚底中心从髋部旋转：先前伸，再向两侧外展。
    left_foot = _rotate_about_y(
        _rotate_about_x((2.0, 0.0, 0.0), (2.0, 12.0, 0.0), sit_pose["leg_l"]),
        (2.0, 12.0, 0.0),
        sit_pose["leg_l_y"],
    )
    right_foot = _rotate_about_y(
        _rotate_about_x((-2.0, 0.0, 0.0), (-2.0, 12.0, 0.0), sit_pose["leg_r"]),
        (-2.0, 12.0, 0.0),
        sit_pose["leg_r_y"],
    )
    gap = left_foot[0] - right_foot[0]
    check("sit-feet-separated", gap >= 6.0, f"{left_foot} / {right_foot} gap={gap:.2f}")

    widget.close()
    print(f"\n{len(CHECKS)} checks passed")


if __name__ == "__main__":
    main()
