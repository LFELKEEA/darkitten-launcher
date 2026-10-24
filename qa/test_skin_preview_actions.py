from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from darkitten.ui.skin_preview import SkinPreviewWidget  # noqa: E402

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
    check("actions-count", len(keys) == 3, str(keys))
    for expected in ("idle", "walk", "spin"):
        check(f"actions-has-{expected}", expected in keys, str(keys))
    for removed in ("run", "sneak", "wave", "sit"):
        check(f"actions-removed-{removed}", removed not in keys, str(keys))
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

    for removed in ("run", "sneak", "wave", "sit", "not-a-real-action"):
        widget.set_action(removed)
        check(
            f"action-{removed}-falls-back-to-idle",
            widget.action() == "idle",
            widget.action(),
        )

    widget.set_action("walk")
    check("walk-keeps-upright-body", widget._pose()["body_x"] == 0.0, str(widget._pose()))

    widget.close()
    print(f"\n{len(CHECKS)} checks passed")


if __name__ == "__main__":
    main()
