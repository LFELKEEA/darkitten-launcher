from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from darkitten.config import Config
from darkitten.ui import download_center as dc
from darkitten.ui.download_center import (
    DownloadCenter,
    DownloadTasksDialog,
    TopDownloadChip,
)
from darkitten.ui.local_import_dialog import LocalImportDialog
from darkitten.ui.pack_dialog import PackImportDialog
from darkitten.ui.version_zip_dialog import VersionZipDialog

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


class FakeWorker(QObject):
    progress = Signal(int, str)
    finished = Signal(bool, str, str)
    log_line = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.started = False
        self.cancelled = False

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True


class FakeMain(QWidget):
    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config
        self.messages: list[tuple[str, str]] = []
        self.statuses: list[tuple[str, str]] = []
        self.selected: list[str] = []
        self.opened: list[str] = []
        self.refreshed = 0
        self.download_center = DownloadCenter(self)

    def toast(self, text: str, kind: str = "info") -> None:
        self.messages.append((text, kind))

    def log(self, _message: str) -> None:
        pass

    def set_status(self, text: str, kind: str = "idle") -> None:
        self.statuses.append((text, kind))

    def refresh_versions(self) -> None:
        self.refreshed += 1

    def select_version(self, version_id: str) -> None:
        self.selected.append(version_id)

    def open_path(self, path) -> None:
        self.opened.append(str(path))

    def open_local_import(self, path) -> None:
        self.opened.append(str(path))


def pump(app: QApplication) -> None:
    for _ in range(3):
        app.processEvents()


def start_fake(
    center: DownloadCenter,
    root: Path,
    content_type: str,
    title: str = "测试任务",
) -> tuple[object, FakeWorker]:
    worker = FakeWorker()
    task = center.start_worker(
        worker,
        title=title,
        content_type=content_type,
        target=root,
    )
    return task, worker


def center_lifecycle_test(app: QApplication) -> None:
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    main = FakeMain(Config(game_dir=str(root / "game")))
    center = main.download_center

    task, worker = start_fake(center, root, "version-install", "安装 1.21.1")
    check("generic_worker_started", worker.started)
    check("generic_worker_owned_by_center", worker.parent() is center)
    check("install_action_text", task.action_text == "安装中", task.action_text)
    check("task_tracked", center.has_running() and center.active_tasks() == [task])

    worker.progress.emit(37, "正在补全资源")
    pump(app)
    check("task_progress", task.progress == 37, str(task.progress))

    chip = TopDownloadChip()
    chip.show()
    chip.sync(center)
    pump(app)
    check(
        "chip_shows_install",
        chip.isVisible() and chip.status_label.text() == "安装中" and chip.percent_label.text() == "37%",
        f"{chip.status_label.text()} {chip.percent_label.text()}",
    )

    worker.finished.emit(True, "安装完成", "")
    pump(app)
    check("install_finished_success", task.status == dc.TASK_SUCCESS, task.status)
    check("install_finished_message", task.message == "安装完成", task.message)
    chip.sync(center)
    check("chip_final_state", chip.status_label.text() == "已完成", chip.status_label.text())

    task2, worker2 = start_fake(center, root, "modpack", "导入整合包")
    center.cancel(task2.id)
    check("cancel_reaches_worker", worker2.cancelled)
    worker2.finished.emit(False, "下载已取消", "")
    pump(app)
    check("cancel_state", task2.status == dc.TASK_CANCELLED, task2.status)

    center.clear_finished()
    chip.sync(center)
    pump(app)
    check(
        "idle_chip_visible",
        chip.isVisible() and chip.status_label.text() == "下载中心",
        chip.status_label.text(),
    )
    check("idle_chip_compact", chip.maximumWidth() == 48, str(chip.maximumWidth()))
    check("idle_chip_tooltip", "下载中心" in chip.toolTip())

    clicked: list[bool] = []
    chip.clicked.connect(lambda: clicked.append(True))
    QTest.mouseClick(chip, Qt.LeftButton)
    pump(app)
    check("idle_chip_clickable", bool(clicked), str(clicked))

    chip.close()
    temp.cleanup()


def dialog_close_test(app: QApplication, kind: str) -> None:
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    main = FakeMain(Config(game_dir=str(root / "game")))
    center = main.download_center

    if kind == "pack":
        dialog = PackImportDialog(main)
    elif kind == "zip":
        dialog = VersionZipDialog(main)
    else:
        dialog = LocalImportDialog(main)

    task, worker = start_fake(center, root, "modpack", "后台导入")
    if kind == "local":
        dialog._mode = "import"
    else:
        dialog._mode = "import"
    dialog._task = task

    cancelled: list[int] = []
    original_cancel = center.cancel
    center.cancel = lambda task_id: cancelled.append(int(task_id))
    dialog.reject()
    pump(app)
    center.cancel = original_cancel

    check(f"{kind}_close_keeps_task", not cancelled and task.running, f"cancel={cancelled}")
    check(
        f"{kind}_close_toast",
        any("顶栏" in text for text, _kind in main.messages),
        str(main.messages),
    )
    check(f"{kind}_worker_not_cancelled", not worker.cancelled)

    worker.progress.emit(64, "仍在后台导入")
    worker.finished.emit(True, "导入完成", "后台版本")
    pump(app)
    check(f"{kind}_task_finishes_in_background", task.status == dc.TASK_SUCCESS, task.status)
    check(f"{kind}_filename_saved", task.filename == "后台版本", task.filename)

    dialog.deleteLater()
    pump(app)
    temp.cleanup()


def import_routing_test(app: QApplication) -> None:
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    main = FakeMain(Config(game_dir=str(root / "game")))
    center = main.download_center
    target = root / "cache"
    target.mkdir(parents=True)
    archive = target / "pack.zip"
    archive.write_bytes(b"PK\x05\x06" + b"\x00" * 18)

    task = dc.DownloadTask(
        id=91,
        title="下载整合包",
        content_type="pack-fetch",
        source="url",
        target=target,
        pack_mode=True,
        progress=100,
        message="下载完成",
        status=dc.TASK_SUCCESS,
        filename=archive.name,
    )
    center._tasks[task.id] = task
    center._order = [task.id]

    dialog = DownloadTasksDialog(main)
    check("tasks_dialog_empty_hint", "导入" in dialog.empty.text(), dialog.empty.text())
    check("tasks_dialog_has_row", task.id in dialog._rows)
    if task.id in dialog._rows:
        row = dialog._rows[task.id]
        check("tasks_dialog_import_visible", not row.import_btn.isHidden())
        row.import_btn.click()
        pump(app)
    check(
        "tasks_dialog_import_routes",
        main.opened == [str(archive)],
        str(main.opened),
    )
    dialog.deleteLater()
    pump(app)
    temp.cleanup()


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    center_lifecycle_test(app)
    dialog_close_test(app, "pack")
    dialog_close_test(app, "zip")
    dialog_close_test(app, "local")
    import_routing_test(app)
    print(f"RESULT: {'PASS' if FAIL == 0 else 'FAIL'} ({PASS} ok, {FAIL} failed)")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
