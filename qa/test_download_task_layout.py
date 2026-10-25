from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from PySide6.QtWidgets import QApplication, QWidget

from darkitten.config import Config
from darkitten.ui.download_center import (
    DownloadCenter,
    DownloadTask,
    DownloadTasksDialog,
    TASK_RUNNING,
)

PASS = 0
FAIL = 0
LONG_TARGET = (
    r"D:\Darkitten Launcher\DarkittenLauncher-1.10.7-source\minecraft"
    r"\versions\1.21.11-Fabric\saves\a_very_long_world_name_that_keeps_going"
)


def check(name: str, condition: bool, detail: str = "") -> bool:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"{name}: OK{(' (' + detail + ')') if detail else ''}")
    else:
        FAIL += 1
        print(f"{name}: FAIL{(' (' + detail + ')') if detail else ''}")
    return condition


class FakeMain(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.config = Config(game_dir=str(Path(os.environ.get("TEMP", ".")) / "dkl_layout_game"))
        self.download_center = DownloadCenter(self)

    def toast(self, *_args, **_kwargs) -> None:
        pass

    def open_path(self, *_args, **_kwargs) -> None:
        pass

    def log(self, *_args, **_kwargs) -> None:
        pass


def build_dialog(app: QApplication) -> tuple[FakeMain, DownloadTask, DownloadTasksDialog]:
    main = FakeMain()
    task = DownloadTask(
        id=1,
        title="导入整合包 · 1.21.11-Fabric 0.19.5",
        content_type="local-import",
        source="local",
        target=Path(LONG_TARGET),
        progress=52,
        message="正在补全资源文件 2407/4591",
        status=TASK_RUNNING,
    )
    main.download_center._tasks[task.id] = task
    main.download_center._order = [task.id]
    dialog = DownloadTasksDialog(main)
    dialog.show()
    app.processEvents()
    return main, task, dialog


def target_label(row):
    for child in row.findChildren(type(row.title_label)):
        if child.toolTip() == LONG_TARGET:
            return child
    return None


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    fake_main, task, dialog = build_dialog(app)
    row = dialog._rows[task.id]
    viewport_width = dialog.scroll.viewport().width()

    check(
        "row_fits_viewport",
        row.width() <= viewport_width,
        f"row={row.width()} viewport={viewport_width}",
    )
    check(
        "row_minimum_fits_viewport",
        row.minimumSizeHint().width() <= viewport_width,
        f"min={row.minimumSizeHint().width()} viewport={viewport_width}",
    )
    check("badge_text", row.status_badge.text() == "导入中", row.status_badge.text())
    check(
        "badge_not_clipped",
        row.status_badge.width() >= row.status_badge.sizeHint().width()
        and row.status_badge.mapTo(row, row.status_badge.rect().topRight()).x()
        <= row.width(),
        f"badge={row.status_badge.width()} hint={row.status_badge.sizeHint().width()}",
    )
    check(
        "cancel_button_visible",
        row.cancel_btn.isVisible()
        and row.cancel_btn.mapTo(row, row.cancel_btn.rect().topRight()).x()
        <= row.width(),
        f"cancel_right={row.cancel_btn.mapTo(row, row.cancel_btn.rect().topRight()).x()} row={row.width()}",
    )

    label = target_label(row)
    check("target_label_found", label is not None)
    if label is not None:
        check(
            "target_elided",
            label.text() != LONG_TARGET and label.text().endswith("…"),
            label.text(),
        )
        check("target_full_tooltip", label.toolTip() == LONG_TARGET)
        check("target_full_kept", label._full_text == LONG_TARGET)
        check(
            "target_inside_row",
            label.mapTo(row, label.rect().topRight()).x() <= row.width(),
            f"target_right={label.mapTo(row, label.rect().topRight()).x()} row={row.width()}",
        )

    task.content_type = "version-install"
    row.update_task(task)
    app.processEvents()
    check(
        "install_badge_text",
        row.status_badge.text() == "安装中",
        row.status_badge.text(),
    )
    check(
        "install_badge_not_clipped",
        row.status_badge.width() >= row.status_badge.sizeHint().width()
        and row.status_badge.mapTo(row, row.status_badge.rect().topRight()).x()
        <= row.width(),
    )

    task.content_type = "mod"
    row.update_task(task)
    app.processEvents()
    check(
        "download_badge_text",
        row.status_badge.text() == "下载中",
        row.status_badge.text(),
    )

    dialog.close()
    fake_main.close()
    app.processEvents()
    print(f"\ndownload_task_layout: {PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
