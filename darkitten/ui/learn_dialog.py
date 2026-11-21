"""模组学习：搜索 Modrinth、下载 JAR、反编译成源码阅读。

这是给玩家/开发者学习模组实现用的工具，不参与游戏启动流程。
"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import decompiler, modrinth
from ..bridge import LEARN_DIR
from .dialogs import StyledDialog
from .widgets import SegmentedControl, tiny

LOADERS = [("全部", ""), ("Fabric", "fabric"), ("NeoForge", "neoforge"), ("Forge", "forge"), ("Quilt", "quilt")]


class _Task(QObject):
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(int, str)

    def __init__(self, fn, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.fn = fn

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            result = self.fn(self.progress.emit)
        except Exception as exc:  # noqa: BLE001 - 直接显示给用户
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        else:
            self.done.emit(result)


class LearnDialog(StyledDialog):
    def __init__(self, main) -> None:
        super().__init__(main, "模组学习", "cube", icon_color="#7152E8", width=960)
        self.main = main
        self._tasks: list[_Task] = []
        self._hits: list[dict] = []
        self._versions: list[dict] = []
        self._jar: Path | None = None
        self._project: dict = {}
        self._version: dict = {}
        self._build()
        self.resize(920, 660)
        QTimer.singleShot(0, self._search)

    # ------------------------------------------------------------------
    def _build(self) -> None:
        info = tiny(
            "从 Modrinth 搜索并下载模组，反编译后的源码保存在启动器 learn 目录，"
            "只用于本地学习与排错，请遵守原模组许可证。"
        )
        info.setWordWrap(True)
        self.body.addWidget(info)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.mc_combo = QComboBox()
        self.mc_combo.setEditable(True)
        self.mc_combo.setMinimumWidth(120)
        self.mc_combo.addItems(self._mc_candidates())
        self.mc_combo.setCurrentText(self._default_mc())
        row.addWidget(QLabel("Minecraft"))
        row.addWidget(self.mc_combo)

        self.loader_tabs = SegmentedControl()
        for label, value in LOADERS:
            self.loader_tabs.add_item(label, value)
        row.addWidget(QLabel("加载器"))
        row.addWidget(self.loader_tabs)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索模组，例如 create / sodium / 暮色森林")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.returnPressed.connect(self._search)
        row.addWidget(self.search_edit, 1)
        self.search_btn = QPushButton("搜索")
        self.search_btn.setObjectName("Primary")
        self.search_btn.clicked.connect(self._search)
        row.addWidget(self.search_btn)
        self.body.addLayout(row)

        lists = QHBoxLayout()
        lists.setSpacing(10)
        self.results = QListWidget()
        self.results.setAlternatingRowColors(True)
        self.results.currentRowChanged.connect(self._on_result)
        lists.addWidget(self.results, 3)

        side = QVBoxLayout()
        side.setSpacing(8)
        self.detail_title = QLabel("选择一个模组")
        self.detail_title.setObjectName("H2")
        self.detail_title.setWordWrap(True)
        side.addWidget(self.detail_title)
        self.detail_desc = tiny("")
        self.detail_desc.setWordWrap(True)
        side.addWidget(self.detail_desc)
        side.addWidget(QLabel("可用版本"))
        self.version_combo = QComboBox()
        self.version_combo.setMinimumWidth(220)
        side.addWidget(self.version_combo)
        self.version_note = tiny("")
        self.version_note.setWordWrap(True)
        side.addWidget(self.version_note)
        side.addStretch(1)
        lists.addLayout(side, 2)
        self.body.addLayout(lists, 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        self.body.addWidget(self.progress)
        self.status = tiny("就绪")
        self.status.setWordWrap(True)
        self.body.addWidget(self.status)

        self.download_btn = QPushButton("下载到学习库")
        self.download_btn.setObjectName("Primary")
        self.download_btn.setEnabled(False)
        self.download_btn.clicked.connect(self._download)
        self.decompile_btn = QPushButton("反编译成源码")
        self.decompile_btn.setObjectName("Secondary")
        self.decompile_btn.setEnabled(False)
        self.decompile_btn.clicked.connect(self._decompile)
        self.open_src_btn = QPushButton("打开源码目录")
        self.open_src_btn.setObjectName("Ghost")
        self.open_src_btn.setEnabled(False)
        self.open_src_btn.clicked.connect(self._open_sources)
        self.open_lib_btn = QPushButton("打开学习库")
        self.open_lib_btn.setObjectName("Ghost")
        self.open_lib_btn.clicked.connect(lambda: self._open(LEARN_DIR))
        self.footer.addWidget(self.open_lib_btn)
        self.footer.addWidget(self.open_src_btn)
        self.footer.addWidget(self.decompile_btn)
        self.footer.addWidget(self.download_btn)

    # ------------------------------------------------------------------
    def _default_mc(self) -> str:
        version_id = str(getattr(self.main, "selected_version", "") or "")
        for item in getattr(self.main, "versions", []) or []:
            if item.id == version_id:
                if item.base_id:
                    return item.base_id
                break
        return version_id or "1.21.1"

    def _mc_candidates(self) -> list[str]:
        values: list[str] = []
        for item in getattr(self.main, "versions", []) or []:
            value = item.base_id or item.id
            if value and value not in values and value[:2].isdigit():
                values.append(value)
        values.sort(reverse=True)
        return values or ["1.21.1"]

    def _loader(self) -> str:
        return str(self.loader_tabs.current() or "")

    def _run(self, fn, done, failed=None, progress=None) -> _Task:
        task = _Task(fn, self)
        self._tasks.append(task)
        task.done.connect(lambda result: self._task_done(task, done, result))
        task.failed.connect(lambda message: self._task_failed(task, failed, message))
        if progress is not None:
            task.progress.connect(progress)
        task.start()
        return task

    def _task_done(self, task, callback, result) -> None:
        if task in self._tasks:
            self._tasks.remove(task)
        self.progress.setVisible(False)
        self._set_busy(False)
        callback(result)

    def _task_failed(self, task, callback, message) -> None:
        if task in self._tasks:
            self._tasks.remove(task)
        self.progress.setVisible(False)
        self._set_busy(False)
        self.status.setText("失败：" + message)
        if callback is not None:
            callback(message)

    def _set_busy(self, busy: bool) -> None:
        self.search_btn.setEnabled(not busy)
        self.download_btn.setEnabled(not busy and bool(self._selected_version()))
        self.decompile_btn.setEnabled(not busy and bool(self._jar))
        if busy:
            self.progress.setVisible(True)
            self.progress.setValue(0)

    def _on_progress(self, value: int, message: str) -> None:
        self.progress.setVisible(True)
        self.progress.setValue(int(value))
        self.status.setText(str(message))

    # ------------------------------------------------------------------
    def _search(self) -> None:
        query = self.search_edit.text().strip()
        loader = self._loader()
        mc = self.mc_combo.currentText().strip()
        self.status.setText("正在搜索…")
        self._set_busy(True)

        def work(_progress):
            return modrinth.search_mods(query, loader=loader, game_version=mc, limit=30)

        self._run(work, self._show_results, progress=self._on_progress)

    def _show_results(self, data) -> None:
        self.results.clear()
        self._hits = list(data.get("hits") or [])
        for hit in self._hits:
            title = str(hit.get("title") or hit.get("slug") or "未命名")
            downloads = int(hit.get("downloads") or 0)
            description = " ".join(str(hit.get("description") or "").split())
            item = QListWidgetItem(f"{title}\n{downloads:,} 次下载 · {description}")
            self.results.addItem(item)
        self.status.setText(f"找到 {len(self._hits)} 个结果")
        if self._hits:
            self.results.setCurrentRow(0)

    def _on_result(self, row: int) -> None:
        if row < 0 or row >= len(self._hits):
            return
        hit = self._hits[row]
        self._project = hit
        self._version = {}
        self._jar = None
        self.download_btn.setEnabled(False)
        self.decompile_btn.setEnabled(False)
        self.open_src_btn.setEnabled(False)
        self.detail_title.setText(str(hit.get("title") or hit.get("slug") or ""))
        self.detail_desc.setText(" ".join(str(hit.get("description") or "").split()))
        project_id = str(hit.get("project_id") or hit.get("slug") or "")
        loader = self._loader()
        mc = self.mc_combo.currentText().strip()
        self.version_combo.clear()
        self.version_note.setText("正在读取版本…")

        def work(_progress):
            return modrinth.fetch_mod_versions(project_id, loader=loader, game_version=mc)

        self._run(work, self._show_versions, failed=lambda m: self.version_note.setText("读取版本失败：" + m))

    def _show_versions(self, versions) -> None:
        self._versions = list(versions or [])
        self.version_combo.clear()
        for version in self._versions:
            name = str(version.get("name") or version.get("version_number") or version.get("id"))
            games = ", ".join(version.get("game_versions") or [])
            self.version_combo.addItem(f"{name}  [{games}]", version)
        if not self._versions:
            self.version_note.setText("当前加载器/游戏版本没有可用文件，可切换加载器或手动填写版本。")
            return
        self.version_note.setText(f"共 {len(self._versions)} 个版本，默认选第一个发布版。")
        self.download_btn.setEnabled(True)

    def _selected_version(self) -> dict:
        data = self.version_combo.currentData()
        return data if isinstance(data, dict) else {}

    def _download(self) -> None:
        version = self._selected_version()
        if not version:
            self.status.setText("请先选择版本")
            return
        filename = ""
        file = modrinth.primary_file(version) or {}
        filename = str(file.get("filename") or "mod.jar")
        target_dir = LEARN_DIR / "mods"
        self.decompile_btn.setEnabled(False)
        self._set_busy(True)
        self.status.setText(f"正在下载 {filename}…")

        def work(progress):
            return modrinth.download_mod_file(
                version,
                target_dir,
                progress_cb=lambda percent, message: progress(percent, message),
            )

        def done(path):
            self._jar = Path(path)
            self._version = version
            self.status.setText(f"已下载：{self._jar}")
            self.download_btn.setEnabled(True)
            self.decompile_btn.setEnabled(True)

        self._run(work, done, progress=self._on_progress)

    def _decompile(self) -> None:
        if self._jar is None or not self._jar.is_file():
            self.status.setText("请先下载 JAR")
            return
        hit = self._project
        slug = decompiler.safe_name(hit.get("slug") or hit.get("project_id") or "mod")
        label = decompiler.safe_name(str(self._version.get("version_number") or "version"))
        out_dir = LEARN_DIR / "src" / slug / label
        self._set_busy(True)
        self.status.setText("正在反编译，首次会先下载 Vineflower…")

        def work(progress):
            return decompiler.decompile_jar(
                self._jar,
                out_dir,
                config=getattr(self.main, "config", None),
                progress=lambda percent, message: progress(percent, message),
            )

        def done(info):
            entry = {
                "project": hit.get("project_id", ""),
                "slug": hit.get("slug", ""),
                "title": hit.get("title", ""),
                "license": (hit.get("license") or "") if isinstance(hit.get("license"), str) else "",
                "version": self._version.get("id", ""),
                "version_name": self._version.get("version_number", ""),
                "jar": str(self._jar),
                "source_dir": info["source_dir"],
                "index": info["index"],
                "classes": info["classes"],
                "files": info["files"],
                "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            decompiler.write_learn_index(LEARN_DIR, entry)
            self.status.setText(
                f"反编译完成：{info['files']} 个源码文件，目录 {info['source_dir']}"
            )
            self.open_src_btn.setEnabled(True)
            self.decompile_btn.setEnabled(True)
            if hasattr(self.main, "toast"):
                self.main.toast("反编译完成，可以打开源码目录学习", "success")

        self._run(work, done, progress=self._on_progress)

    def _open_sources(self) -> None:
        if not self._jar:
            return
        slug = decompiler.safe_name(self._project.get("slug") or "mod")
        label = decompiler.safe_name(str(self._version.get("version_number") or "version"))
        self._open(LEARN_DIR / "src" / slug / label)

    def _open(self, path: Path) -> None:
        Path(path).mkdir(parents=True, exist_ok=True)
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))