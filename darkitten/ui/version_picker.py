"""可搜索的 Minecraft 版本选择窗口。

比直接在小下拉框里上下滚动更直观：支持搜索、显示已安装标记，也可以
手动输入清单里没有的版本或快照。
"""

from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtWidgets import (
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
)

from ..mc_versions import release_versions
from .dialogs import StyledDialog


class _ManifestSignals(QObject):
    done = Signal()
    failed = Signal(str)


class _ManifestTask(QRunnable):
    """后台向 Mojang 同步一次版本清单，新版本发布后不用等启动器更新。"""

    def __init__(self) -> None:
        super().__init__()
        self.signals = _ManifestSignals()

    def run(self) -> None:
        try:
            from ..installer import get_manifest

            get_manifest(force=True)
        except Exception as error:
            self.signals.failed.emit(str(error))
            return
        self.signals.done.emit()


class VersionPickerDialog(StyledDialog):
    def __init__(
        self,
        parent,
        current: str = "",
        installed: Iterable[str] = (),
        title: str = "选择 Minecraft 版本",
    ) -> None:
        super().__init__(parent, title, "package", width=520)
        self._result = str(current or "").strip()
        self._installed = {
            str(item or "").strip() for item in installed if str(item or "").strip()
        }

        tip = QLabel(
            "列表是完整正式版清单，新的在前。也可以直接在下面输入快照、"
            "预发布版或清单里没有的版本。"
        )
        tip.setObjectName("Tiny")
        tip.setWordWrap(True)
        self.body.addWidget(tip)

        self.status = QLabel("正在向 Mojang 同步最新版本清单…")
        self.status.setObjectName("Tiny")
        self.status.setWordWrap(True)
        self.body.addWidget(self.status)

        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索版本，例如 1.21、1.20.1")
        self.search.setClearButtonEnabled(True)
        self.body.addWidget(self.search)

        self.list = QListWidget()
        self.list.setAlternatingRowColors(True)
        self.list.setFixedHeight(330)
        self.body.addWidget(self.list)

        self.manual = QLineEdit(self._result)
        self.manual.setPlaceholderText("也可以手动输入版本，例如 1.21.11-pre1")
        self.body.addWidget(self.manual)

        self.detail = QLabel("")
        self.detail.setObjectName("Tiny")
        self.detail.setWordWrap(True)
        self.body.addWidget(self.detail)

        self.search.textChanged.connect(self._refresh)
        self.list.currentItemChanged.connect(self._on_select)
        self.list.itemDoubleClicked.connect(lambda _item: self._accept())
        self.manual.returnPressed.connect(self._accept)

        self.add_button("取消", "Secondary", self.reject)
        self.add_button("使用这个版本", "Primary", self._accept)
        self._refresh()
        self._preselect(self._result)

        task = _ManifestTask()
        task.signals.done.connect(self._on_manifest_done)
        task.signals.failed.connect(self._on_manifest_failed)
        self._task = task
        QThreadPool.globalInstance().start(task)

    def _on_manifest_done(self) -> None:
        self._task = None
        self.status.setText("已同步 Mojang 最新版本清单。")
        current = self.manual.text().strip()
        self._refresh()
        self._preselect(current)

    def _on_manifest_failed(self, message: str) -> None:
        self._task = None
        self.status.setText(
            f"在线同步失败，先使用本机缓存的版本清单（{len(self._versions())} 个）。"
        )

    def _versions(self) -> list[str]:
        return release_versions(self._installed)

    def _refresh(self) -> None:
        keyword = self.search.text().strip().lower()
        versions = [
            version
            for version in self._versions()
            if not keyword or keyword in version.lower()
        ]
        self.list.blockSignals(True)
        self.list.clear()
        for version in versions:
            label = version
            if version in self._installed:
                label += "    · 已安装"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, version)
            self.list.addItem(item)
        self.list.blockSignals(False)
        self.detail.setText(f"共 {len(versions)} 个版本")
        if versions and not self.manual.text().strip():
            self.list.setCurrentRow(0)

    def _preselect(self, current: str) -> None:
        text = str(current or "").strip()
        if not text:
            return
        self.manual.setText(text)
        for row in range(self.list.count()):
            item = self.list.item(row)
            if str(item.data(Qt.UserRole) or "") == text:
                self.list.setCurrentRow(row)
                break

    def _on_select(self, current: QListWidgetItem | None, _previous=None) -> None:
        if current is None:
            return
        version = str(current.data(Qt.UserRole) or "")
        if version:
            self.manual.setText(version)

    def _accept(self) -> None:
        version = self.manual.text().strip()
        if not version:
            self.detail.setText("请先选择一个版本，或手动输入版本号。")
            return
        self._result = version
        self.accept()

    def value(self) -> str:
        return self._result
