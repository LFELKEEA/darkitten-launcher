from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..workers import ModDownloadWorker
from . import icons
from .dialogs import StyledDialog
from .widgets import badge, colors, icon_button, repolish, transparent_children

TASK_RUNNING = "running"
TASK_SUCCESS = "success"
TASK_ERROR = "error"
TASK_CANCELLED = "cancelled"


@dataclass
class DownloadTask:
    id: int
    title: str
    content_type: str
    source: str
    target: Path
    pack_mode: bool = False
    progress: int = 0
    message: str = "准备下载…"
    status: str = TASK_RUNNING
    filename: str = ""
    error: str = ""
    metadata: dict = field(default_factory=dict)
    worker: ModDownloadWorker | None = None
    created_at: float = field(default_factory=time.time)
    finished_at: float = 0.0

    @property
    def running(self) -> bool:
        return self.status == TASK_RUNNING

    @property
    def status_text(self) -> str:
        return {
            TASK_RUNNING: "下载中",
            TASK_SUCCESS: "已完成",
            TASK_ERROR: "失败",
            TASK_CANCELLED: "已取消",
        }.get(self.status, self.status)

    @property
    def display_name(self) -> str:
        return self.filename or self.title


class _TaskRelay(QObject):
    """把工作线程信号排队回主线程，避免下载过程中直接操作界面数据。"""

    def __init__(self, center: "DownloadCenter", task_id: int) -> None:
        super().__init__(center)
        self.center = center
        self.task_id = task_id

    def on_progress(self, percent: int, message: str) -> None:
        self.center._on_progress(self.task_id, percent, message)

    def on_finished(self, success: bool, message: str, filename: str) -> None:
        self.center._on_finished(self.task_id, success, message, filename)


class DownloadCenter(QObject):
    """启动器级下载任务中心：窗口关闭后任务继续，由顶栏接管显示。"""

    task_progress = Signal(int, int, str)
    task_finished = Signal(object)
    tasks_changed = Signal()

    def __init__(self, main) -> None:
        super().__init__(main)
        self.main = main
        self._tasks: dict[int, DownloadTask] = {}
        self._order: list[int] = []
        self._relays: dict[int, _TaskRelay] = {}
        self._next_id = 1

    # ------------------------------------------------------------------
    # 任务生命周期

    def start_mod_download(
        self,
        version: dict,
        target_dir: Path,
        *,
        threads: int,
        title: str,
        content_type: str,
        source: str,
        metadata: dict | None = None,
        pack_mode: bool = False,
    ) -> DownloadTask:
        task_id = self._next_id
        self._next_id += 1
        target = Path(target_dir)
        task = DownloadTask(
            id=task_id,
            title=str(title or "下载内容"),
            content_type=str(content_type or "mod"),
            source=str(source or "modrinth"),
            target=target,
            pack_mode=bool(pack_mode),
            metadata=dict(metadata or {}),
        )
        worker = ModDownloadWorker(
            version,
            target,
            threads=threads,
            parent=self,
            source=source,
            metadata=task.metadata if task.content_type == "mod" else None,
        )
        task.worker = worker
        self._tasks[task_id] = task
        self._order.append(task_id)
        relay = _TaskRelay(self, task_id)
        self._relays[task_id] = relay
        worker.progress.connect(relay.on_progress)
        worker.finished.connect(relay.on_finished)
        self.tasks_changed.emit()
        worker.start()
        return task

    def task(self, task_id: int) -> DownloadTask | None:
        return self._tasks.get(int(task_id))

    def active_tasks(self) -> list[DownloadTask]:
        return [
            self._tasks[task_id]
            for task_id in self._order
            if self._tasks[task_id].running
        ]

    def latest_task(self) -> DownloadTask | None:
        for task_id in reversed(self._order):
            task = self._tasks.get(task_id)
            if task is not None:
                return task
        return None

    def recent_tasks(self, limit: int = 12) -> list[DownloadTask]:
        result: list[DownloadTask] = []
        for task_id in reversed(self._order):
            task = self._tasks.get(task_id)
            if task is not None:
                result.append(task)
            if len(result) >= max(1, int(limit)):
                break
        return result

    def has_running(self) -> bool:
        return any(task.running for task in self._tasks.values())

    def cancel(self, task_id: int) -> None:
        task = self._tasks.get(int(task_id))
        if task is None or not task.running or task.worker is None:
            return
        task.message = "正在取消，请稍候…"
        task.worker.cancel()
        self.tasks_changed.emit()

    def clear_finished(self) -> None:
        for task_id in list(self._order):
            task = self._tasks.get(task_id)
            if task is None or task.running:
                continue
            self._tasks.pop(task_id, None)
            self._order.remove(task_id)
            relay = self._relays.pop(task_id, None)
            if relay is not None:
                relay.deleteLater()
        self.tasks_changed.emit()

    # ------------------------------------------------------------------
    # 信号转发

    def _on_progress(self, task_id: int, percent: int, message: str) -> None:
        task = self._tasks.get(task_id)
        if task is None or not task.running:
            return
        task.progress = max(0, min(100, int(percent)))
        task.message = str(message or "")
        self.task_progress.emit(task.id, task.progress, task.message)
        self.tasks_changed.emit()

    def _on_finished(
        self, task_id: int, success: bool, message: str, filename: str
    ) -> None:
        task = self._tasks.get(task_id)
        if task is None:
            return
        task.worker = None
        task.filename = str(filename or "")
        task.finished_at = time.time()
        if success:
            task.status = TASK_SUCCESS
            task.progress = 100
            task.message = "下载完成"
        elif "取消" in str(message):
            task.status = TASK_CANCELLED
            task.message = "下载已取消"
        else:
            task.status = TASK_ERROR
            task.error = str(message or "下载失败")
            task.message = task.error
        self.task_finished.emit(task)
        self.tasks_changed.emit()


class DownloadTaskRow(QFrame):
    cancel_requested = Signal(int)
    import_requested = Signal(object)
    folder_requested = Signal(str)

    def __init__(self, task: DownloadTask, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.task = task
        self.setObjectName("SelectRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMinimumHeight(72)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(9)

        icon_box = QLabel()
        icon_box.setFixedSize(34, 34)
        icon_box.setAlignment(Qt.AlignCenter)
        icon_box.setPixmap(icons.pixmap("download", colors()["accent"], 18))
        icon_box.setStyleSheet(
            f"background: {colors()['accent_soft']}; border-radius: 9px;"
        )
        self.icon_box = icon_box
        layout.addWidget(icon_box, 0, Qt.AlignTop)

        info = QVBoxLayout()
        info.setSpacing(2)
        top = QHBoxLayout()
        top.setSpacing(6)
        self.title_label = QLabel(task.title)
        self.title_label.setStyleSheet("font-weight: 700; font-size: 12px;")
        self.title_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        top.addWidget(self.title_label, 1)
        self.status_badge = badge("下载中", "blue")
        top.addWidget(self.status_badge)
        self.percent_label = QLabel("0%")
        self.percent_label.setObjectName("Tiny")
        top.addWidget(self.percent_label)
        info.addLayout(top)

        self.message_label = QLabel("")
        self.message_label.setObjectName("Tiny")
        self.message_label.setWordWrap(True)
        info.addWidget(self.message_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(5)
        info.addWidget(self.progress)

        target = QLabel(str(task.target))
        target.setObjectName("Tiny")
        target.setToolTip(str(task.target))
        info.addWidget(target)
        layout.addLayout(info, 1)

        actions = QVBoxLayout()
        actions.setSpacing(4)
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setObjectName("Ghost")
        self.cancel_btn.setCursor(Qt.PointingHandCursor)
        self.cancel_btn.clicked.connect(lambda _=False: self.cancel_requested.emit(self.task.id))
        self.folder_btn = QPushButton("打开文件夹")
        self.folder_btn.setObjectName("Ghost")
        self.folder_btn.setCursor(Qt.PointingHandCursor)
        self.folder_btn.clicked.connect(
            lambda _=False: self.folder_requested.emit(str(self.task.target))
        )
        self.import_btn = QPushButton("导入")
        self.import_btn.setObjectName("Primary")
        self.import_btn.setCursor(Qt.PointingHandCursor)
        self.import_btn.clicked.connect(lambda _=False: self.import_requested.emit(self.task))
        actions.addWidget(self.cancel_btn)
        actions.addWidget(self.import_btn)
        actions.addWidget(self.folder_btn)
        layout.addLayout(actions)
        transparent_children(self)
        self.cancel_btn.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.folder_btn.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.import_btn.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.update_task(task)

    def update_task(self, task: DownloadTask) -> None:
        self.task = task
        self.title_label.setText(task.title)
        self.title_label.setToolTip(task.display_name)
        self.message_label.setText(task.message or task.display_name)
        self.progress.setValue(task.progress)
        self.percent_label.setText(f"{task.progress}%")
        color = {
            TASK_RUNNING: "blue",
            TASK_SUCCESS: "green",
            TASK_ERROR: "red",
            TASK_CANCELLED: "gray",
        }.get(task.status, "gray")
        self.status_badge.setText(task.status_text)
        self.status_badge.setProperty("badge", color)
        repolish(self.status_badge)
        running = task.running
        self.cancel_btn.setVisible(running)
        self.cancel_btn.setEnabled(running)
        self.import_btn.setVisible(
            task.status == TASK_SUCCESS and task.pack_mode and bool(task.filename)
        )
        self.folder_btn.setVisible(not running and task.status == TASK_SUCCESS)
        if task.status == TASK_SUCCESS:
            self.icon_box.setPixmap(icons.pixmap("check", colors()["green"], 18))
            self.icon_box.setStyleSheet(
                f"background: {colors()['green_soft']}; border-radius: 9px;"
            )
        elif task.status in (TASK_ERROR, TASK_CANCELLED):
            self.icon_box.setPixmap(icons.pixmap("warning", colors()["orange"], 18))
            self.icon_box.setStyleSheet(
                f"background: {colors()['orange_soft']}; border-radius: 9px;"
            )
        elif task.worker is not None:
            self.icon_box.setPixmap(icons.pixmap("download", colors()["accent"], 18))


class DownloadTasksDialog(StyledDialog):
    def __init__(self, main) -> None:
        super().__init__(main, "下载任务", "download", icon_color="#3FB3AA", width=640)
        self.main = main
        self.center = main.download_center
        self.body.setSpacing(8)
        self.summary = QLabel("")
        self.summary.setObjectName("Tiny")
        self.body.addWidget(self.summary)

        card = QFrame()
        card.setObjectName("SoftCard")
        card.setAttribute(Qt.WA_StyledBackground, True)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(8, 8, 8, 8)
        card_layout.setSpacing(4)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setMinimumHeight(260)
        self.scroll.setMaximumHeight(460)
        self.holder = QWidget()
        self.list_layout = QVBoxLayout(self.holder)
        self.list_layout.setContentsMargins(0, 0, 4, 0)
        self.list_layout.setSpacing(4)
        self.list_layout.addStretch(1)
        self.scroll.setWidget(self.holder)
        card_layout.addWidget(self.scroll, 1)
        self.empty = QLabel("暂时没有下载任务。关闭 Mod 下载窗口后，任务会继续并显示在这里。")
        self.empty.setObjectName("Muted")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setWordWrap(True)
        card_layout.addWidget(self.empty)
        self.body.addWidget(card, 1)

        self.clear_btn = self.add_button("清空已完成", "Secondary", self.center.clear_finished)
        self.add_button("关闭", "Secondary", self.reject)

        self._rows: dict[int, DownloadTaskRow] = {}
        self.center.tasks_changed.connect(self.refresh)
        self.center.task_progress.connect(self._on_progress)
        self.finished.connect(self._on_closed)
        self.refresh()

    def _on_closed(self, _result: int = 0) -> None:
        try:
            self.center.tasks_changed.disconnect(self.refresh)
            self.center.task_progress.disconnect(self._on_progress)
        except (RuntimeError, TypeError):
            pass

    def refresh(self) -> None:
        tasks = self.center.recent_tasks(12)
        active = self.center.active_tasks()
        self.summary.setText(
            f"{len(active)} 个任务正在下载，关闭此窗口不会中断下载。"
            if active
            else "下载任务会在启动器顶栏显示进度，关闭窗口不会取消下载。"
        )
        if not tasks:
            self._clear_rows()
            self.empty.setVisible(True)
            self.scroll.setVisible(False)
            self.clear_btn.setEnabled(False)
            return
        self.empty.setVisible(False)
        self.scroll.setVisible(True)
        self.clear_btn.setEnabled(any(not item.running for item in tasks))
        existing = set()
        for index, task in enumerate(tasks):
            existing.add(task.id)
            row = self._rows.get(task.id)
            if row is None:
                row = DownloadTaskRow(task, self.holder)
                row.cancel_requested.connect(self.center.cancel)
                row.import_requested.connect(self._import_pack)
                row.folder_requested.connect(self.main.open_path)
                self._rows[task.id] = row
            else:
                row.update_task(task)
            if self.list_layout.indexOf(row) != index:
                self.list_layout.removeWidget(row)
                self.list_layout.insertWidget(index, row)
        for task_id, row in list(self._rows.items()):
            if task_id not in existing:
                row.setParent(None)
                row.deleteLater()
                self._rows.pop(task_id, None)
        self.list_layout.activate()

    def _clear_rows(self) -> None:
        for row in self._rows.values():
            row.setParent(None)
            row.deleteLater()
        self._rows.clear()

    def _on_progress(self, task_id: int, percent: int, message: str) -> None:
        row = self._rows.get(int(task_id))
        task = self.center.task(task_id)
        if row is not None and task is not None:
            row.update_task(task)

    def _import_pack(self, task: DownloadTask) -> None:
        path = task.target / task.filename
        if not path.is_file():
            self.main.toast("整合包文件没有保存成功，请重新下载", "error")
            return
        from .pack_dialog import PackImportDialog

        dialog = PackImportDialog(self.main, initial_path=path)
        dialog.exec()
        if dialog.imported_version:
            self.main.refresh_versions()
            self.main.select_version(dialog.imported_version)
        self.refresh()


class _ChipLabel(QLabel):
    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._full_text = str(text or "")
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def setText(self, text: str) -> None:
        self._full_text = str(text or "")
        self._refresh()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self) -> None:
        width = self.contentsRect().width()
        if width <= 0:
            QLabel.setText(self, self._full_text)
            return
        QLabel.setText(
            self,
            self.fontMetrics().elidedText(self._full_text, Qt.ElideRight, width),
        )


class TopDownloadChip(QFrame):
    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DownloadChip")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(38)
        self.setMinimumWidth(38)
        self.setMaximumWidth(196)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._task: DownloadTask | None = None
        self._final_task_id = 0
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(8000)
        self._hide_timer.timeout.connect(self.hide)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(7, 4, 9, 4)
        outer.setSpacing(7)

        self.icon_box = QLabel()
        self.icon_box.setFixedSize(24, 24)
        self.icon_box.setAlignment(Qt.AlignCenter)
        self.icon_box.setPixmap(icons.pixmap("download", colors()["accent"], 14))
        self.icon_box.setStyleSheet(
            f"background: {colors()['accent_soft']}; border-radius: 7px;"
        )
        outer.addWidget(self.icon_box)

        text_box = QVBoxLayout()
        text_box.setSpacing(0)
        top = QHBoxLayout()
        top.setSpacing(4)
        self.status_label = QLabel("下载中")
        self.status_label.setStyleSheet("font-size: 10px; font-weight: 700;")
        self.percent_label = QLabel("0%")
        self.percent_label.setObjectName("Tiny")
        top.addWidget(self.status_label)
        top.addStretch(1)
        top.addWidget(self.percent_label)
        text_box.addLayout(top)
        self.message_label = _ChipLabel("")
        self.message_label.setObjectName("Tiny")
        text_box.addWidget(self.message_label)
        self.progress = QProgressBar()
        self.progress.setObjectName("ChipProgress")
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(3)
        text_box.addWidget(self.progress)
        outer.addLayout(text_box, 1)
        transparent_children(self)
        self.hide()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        compact = self.width() < 104
        self.message_label.setVisible(not compact)
        self.percent_label.setVisible(self.width() >= 72)
        self.status_label.setVisible(self.width() >= 84)
        if compact:
            self.progress.setVisible(False)
        else:
            self.progress.setVisible(True)

    def sync(self, center: DownloadCenter) -> None:
        active = center.active_tasks()
        if active:
            task = active[-1]
            self._hide_timer.stop()
            self._final_task_id = 0
            self._apply(task, running_count=len(active))
            return
        latest = center.latest_task()
        if latest is None:
            self.hide()
            return
        self._apply(latest, running_count=0)
        if self._final_task_id != latest.id:
            self._final_task_id = latest.id
            self._hide_timer.start()

    def _apply(self, task: DownloadTask, running_count: int) -> None:
        self._task = task
        self.setProperty("state", task.status)
        repolish(self)
        self.status_label.setText(
            f"下载中 · {running_count} 项"
            if running_count > 1
            else ("下载中" if task.running else task.status_text)
        )
        self.percent_label.setText(f"{task.progress}%")
        self.message_label.setText(task.message or task.display_name)
        self.progress.setValue(task.progress)
        self.setToolTip(
            f"{task.title}\n{task.message}\n目标：{task.target}\n点击查看下载任务"
        )
        if task.status == TASK_SUCCESS:
            self.icon_box.setPixmap(icons.pixmap("check", colors()["green"], 14))
            self.icon_box.setStyleSheet(
                f"background: {colors()['green_soft']}; border-radius: 7px;"
            )
        elif task.status in (TASK_ERROR, TASK_CANCELLED):
            self.icon_box.setPixmap(icons.pixmap("warning", colors()["orange"], 14))
            self.icon_box.setStyleSheet(
                f"background: {colors()['orange_soft']}; border-radius: 7px;"
            )
        else:
            self.icon_box.setPixmap(icons.pixmap("download", colors()["accent"], 14))
            self.icon_box.setStyleSheet(
                f"background: {colors()['accent_soft']}; border-radius: 7px;"
            )
        self.show()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._task is not None:
            self.clicked.emit()
        super().mouseReleaseEvent(event)
