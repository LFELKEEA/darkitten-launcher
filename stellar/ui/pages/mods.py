from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...mods import ModInfo, list_mods, toggle_mod
from ...workers import DeleteWorker
from .. import icons
from ..dialogs import ConfirmDialog
from ..widgets import (
    Card,
    ChibiCompanion,
    SegmentedControl,
    Switch,
    badge,
    colors,
    icon_button,
    transparent_children,
)


class ModRow(QFrame):
    toggled = Signal(object, bool)
    delete_requested = Signal(object)

    def __init__(self, mod: ModInfo, parent=None) -> None:
        super().__init__(parent)
        self.mod = mod
        self.setObjectName("SelectRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(64)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(13, 8, 13, 8)
        layout.setSpacing(11)

        icon_box = QLabel()
        icon_box.setFixedSize(36, 36)
        icon_box.setAlignment(Qt.AlignCenter)
        icon_box.setPixmap(icons.pixmap("cube", colors()["purple"], 21))
        icon_box.setStyleSheet(
            f"background: {colors()['purple_soft']}; border-radius: 9px;"
        )

        info = QVBoxLayout()
        info.setSpacing(2)
        top = QHBoxLayout()
        top.setSpacing(6)
        name = QLabel(mod.name)
        name.setStyleSheet("font-weight: 700; font-size: 12px;")
        top.addWidget(name)
        if mod.version:
            version = QLabel(mod.version)
            version.setObjectName("Tiny")
            top.addWidget(version)
        if mod.loader:
            top.addWidget(badge(mod.loader, "purple" if mod.loader == "Fabric" else "orange"))
        if not mod.enabled:
            top.addWidget(badge("已禁用", "gray"))
        top.addStretch(1)
        info.addLayout(top)

        meta = QLabel(f"{mod.filename} · {mod.size_text}")
        meta.setObjectName("Tiny")
        info.addWidget(meta)

        switch = Switch()
        switch.setChecked(mod.enabled)
        switch.setToolTip("启用或禁用此 Mod")
        switch.toggled.connect(lambda checked: self.toggled.emit(self.mod, checked))

        delete_btn = icon_button("trash", "删除此 Mod", colors()["muted"], 30, 15)
        delete_btn.clicked.connect(lambda _=False: self.delete_requested.emit(self.mod))

        layout.addWidget(icon_box)
        layout.addLayout(info, 1)
        layout.addWidget(switch)
        layout.addWidget(delete_btn)
        transparent_children(self)
        switch.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        delete_btn.setAttribute(Qt.WA_TransparentForMouseEvents, False)


class ModsPage(QWidget):
    def __init__(self, main, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.main = main
        self._mods: list[ModInfo] = []
        self._delete_worker: DeleteWorker | None = None
        self._filter = "all"
        self._build()
        self.main.versions_changed.connect(self._update_version_label)
        self.main.selection_changed.connect(self._on_selection_changed)
        self.refresh_mods()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 18, 22, 18)
        outer.setSpacing(14)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = QLabel("猫猫的模组抽屉")
        title.setObjectName("PageTitle")
        self.sub = QLabel(
            "Mod 按游戏版本隔离存放在 versions/<版本>/mods。"
            "禁用会保留文件但不会被游戏加载。"
        )
        self.sub.setObjectName("Muted")
        titles.addWidget(title)
        titles.addWidget(self.sub)
        header.addLayout(titles, 1)
        header.addWidget(ChibiCompanion(60, "mods"), 0, Qt.AlignTop)
        self.version_label = badge("未选择版本", "blue")
        header.addWidget(self.version_label)
        loader_btn = QPushButton("安装加载器")
        loader_btn.setObjectName("Ghost")
        loader_btn.setCursor(Qt.PointingHandCursor)
        loader_btn.clicked.connect(self.main.open_loader_dialog)
        header.addWidget(loader_btn)
        outer.addLayout(header)

        toolbar = Card()
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(15, 10, 15, 10)
        toolbar_layout.setSpacing(9)

        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索 Mod…")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(
            icons.icon("search", colors()["muted"], 15), QLineEdit.LeadingPosition
        )
        self.search.setFixedWidth(240)
        self.search.textChanged.connect(lambda _: self.refresh_mods())
        toolbar_layout.addWidget(self.search)

        filters = SegmentedControl()
        filters.add_item("全部", "all")
        filters.add_item("已启用", "enabled")
        filters.add_item("已禁用", "disabled")
        filters.changed.connect(self._on_filter)
        toolbar_layout.addWidget(filters)
        toolbar_layout.addStretch(1)

        self.count_label = QLabel("")
        self.count_label.setObjectName("Tiny")
        toolbar_layout.addWidget(self.count_label)

        download_btn = QPushButton("下载 Mod")
        download_btn.setObjectName("Primary")
        download_btn.setIcon(icons.icon("download", "#FFFFFF", 15))
        download_btn.setCursor(Qt.PointingHandCursor)
        download_btn.setToolTip("从 Modrinth 或 CurseForge 在线下载 Mod")
        download_btn.clicked.connect(self.main.open_mod_download)
        toolbar_layout.addWidget(download_btn)

        add_btn = QPushButton("添加 Mod")
        add_btn.setObjectName("Secondary")
        add_btn.setIcon(icons.icon("plus", colors()["text2"], 15))
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.clicked.connect(self.add_mods)
        toolbar_layout.addWidget(add_btn)

        folder_btn = QPushButton("打开文件夹")
        folder_btn.setObjectName("Secondary")
        folder_btn.setIcon(icons.icon("folder", colors()["text2"], 15))
        folder_btn.setCursor(Qt.PointingHandCursor)
        folder_btn.clicked.connect(self.open_folder)
        toolbar_layout.addWidget(folder_btn)

        refresh_btn = icon_button("refresh", "刷新 Mod 列表")
        refresh_btn.clicked.connect(self.refresh_mods)
        toolbar_layout.addWidget(refresh_btn)
        outer.addWidget(toolbar)

        card = Card()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(8)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.holder = QWidget()
        self.list_layout = QVBoxLayout(self.holder)
        self.list_layout.setContentsMargins(0, 0, 4, 0)
        self.list_layout.setSpacing(3)
        self.list_layout.addStretch(1)
        self.scroll.setWidget(self.holder)
        card_layout.addWidget(self.scroll, 1)

        self.empty = QWidget()
        empty_layout = QVBoxLayout(self.empty)
        empty_layout.setContentsMargins(0, 40, 0, 40)
        empty_layout.addStretch(1)
        empty_icon = QLabel()
        empty_icon.setPixmap(icons.pixmap("cube", colors()["muted"], 52))
        empty_icon.setAlignment(Qt.AlignCenter)
        self.empty_title = QLabel("抽屉里还没有 Mod")
        self.empty_title.setAlignment(Qt.AlignCenter)
        self.empty_title.setStyleSheet("font-size: 15px; font-weight: 700;")
        empty_sub = QLabel("右上角可以在线叼回 Mod，也可以把 .jar 直接拖进来。")
        empty_sub.setAlignment(Qt.AlignCenter)
        empty_sub.setObjectName("Muted")
        empty_btn = QPushButton("打开 mods 文件夹")
        empty_btn.setObjectName("Soft")
        empty_btn.setCursor(Qt.PointingHandCursor)
        empty_btn.clicked.connect(self.open_folder)
        empty_layout.addWidget(empty_icon)
        empty_layout.addWidget(self.empty_title)
        empty_layout.addWidget(empty_sub, 0, Qt.AlignHCenter)
        empty_layout.addSpacing(6)
        empty_layout.addWidget(empty_btn, 0, Qt.AlignHCenter)
        empty_layout.addStretch(1)
        card_layout.addWidget(self.empty, 1)

        outer.addWidget(card, 1)
        self._update_version_label()

    # ------------------------------------------------------------------
    def _mods_dir(self) -> Path:
        return self.main.config.mods_path(self.main.selected_version)

    def _update_version_label(self) -> None:
        version = self.main.selected_version or "未选择版本"
        isolated = bool(
            self.main.selected_version and self.main.config.version_isolation
        )
        self.version_label.setText(
            f"当前版本：{version} · {'独立目录' if isolated else '公共目录'}"
        )
        if self.main.selected_version:
            self.empty_title.setText(f"{self.main.selected_version} 的 mods 文件夹是空的")
        else:
            self.empty_title.setText("抽屉里还没有 Mod")
        self.sub.setText(
            "Mod 按游戏版本隔离存放在 versions/<版本>/mods。"
            if self.main.config.version_isolation
            else "版本隔离已关闭，所有版本共用游戏目录下的 mods 文件夹。"
        )

    def _on_selection_changed(self, _version_id: str = "") -> None:
        self._update_version_label()
        self.refresh_mods()

    def _on_filter(self, value: str) -> None:
        self._filter = value
        self.refresh_mods()

    def refresh_mods(self) -> None:
        self._mods = list_mods(self._mods_dir())
        query = self.search.text().strip().lower()
        visible = []
        for mod in self._mods:
            if self._filter == "enabled" and not mod.enabled:
                continue
            if self._filter == "disabled" and mod.enabled:
                continue
            if query and query not in mod.name.lower() and query not in mod.filename.lower():
                continue
            visible.append(mod)

        self.count_label.setText(f"{len(visible)} / {len(self._mods)}")
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        if not self._mods:
            self.empty.setVisible(True)
            self.scroll.setVisible(False)
            return
        self.empty.setVisible(False)
        self.scroll.setVisible(True)

        if not visible:
            hint = QLabel("没有匹配的 Mod")
            hint.setObjectName("Muted")
            hint.setAlignment(Qt.AlignCenter)
            self.list_layout.insertWidget(0, hint)
            return

        for mod in visible:
            row = ModRow(mod)
            row.toggled.connect(self._toggle_mod)
            row.delete_requested.connect(self._delete_mod)
            self.list_layout.insertWidget(self.list_layout.count() - 1, row)

    def _delete_mod(self, mod: ModInfo) -> None:
        if self._delete_worker is not None:
            self.main.toast("已有删除任务正在进行", "info")
            return
        if (
            self.main.game_running
            and self.main._running_version == self.main.selected_version
        ):
            self.main.toast("游戏正在运行，请先关闭游戏再删除 Mod", "error")
            return
        dialog = ConfirmDialog(
            self,
            "删除 Mod",
            f"确定删除 {mod.name} 吗？文件会移入系统回收站，可在回收站恢复。",
            primary_text="移入回收站",
            secondary_text="取消",
            icon_name="trash",
            icon_color=colors()["red"],
            details=f"文件：{mod.filename}\n位置：{mod.path.parent}",
        )
        if not dialog.exec():
            return
        self.main.set_status(f"正在删除 {mod.filename}…", "busy")
        worker = DeleteWorker(mod.path, self)
        self._delete_worker = worker
        worker.finished.connect(
            lambda ok, message, m=mod: self._on_mod_deleted(m, ok, message)
        )
        worker.start()

    def _on_mod_deleted(self, mod: ModInfo, success: bool, message: str) -> None:
        self._delete_worker = None
        if not success:
            self.main.set_status(f"删除失败：{message}", "error")
            self.main.toast(f"删除失败：{message}", "error")
            return
        self.main.set_status(f"已删除 {mod.filename}", "success")
        self.main.toast(f"{mod.name} 已移入回收站", "success")
        self.refresh_mods()

    def _toggle_mod(self, mod: ModInfo, enabled: bool) -> None:
        if enabled == mod.enabled:
            return
        try:
            new_path = toggle_mod(mod)
        except Exception as exc:
            self.main.toast(f"修改失败：{exc}", "error")
            self.refresh_mods()
            return
        action = "已启用" if new_path.name.endswith(".jar") else "已禁用"
        self.main.toast(f"{action} {mod.name}", "success")
        self.refresh_mods()

    def open_folder(self) -> None:
        mods_dir = self._mods_dir()
        try:
            mods_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.main.toast(f"无法创建 mods 文件夹：{exc}", "error")
            return
        self.main.open_path(mods_dir)

    def add_mods(self) -> None:
        mods_dir = self._mods_dir()
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择 Mod 文件",
            str(mods_dir),
            "Mod 文件 (*.jar *.jar.disabled)",
        )
        if not files:
            return
        try:
            mods_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.main.toast(f"无法创建 mods 文件夹：{exc}", "error")
            return

        added = 0
        skipped = 0
        for raw in files:
            src = Path(raw)
            dest = mods_dir / src.name
            if dest.exists():
                skipped += 1
                continue
            try:
                shutil.copy2(src, dest)
                added += 1
            except OSError:
                skipped += 1
        if added:
            self.main.toast(f"已添加 {added} 个 Mod" + (f"，跳过 {skipped} 个" if skipped else ""), "success")
        else:
            self.main.toast("没有添加新的 Mod（同名文件已存在）", "info")
        self.refresh_mods()

    def on_show(self) -> None:
        self._update_version_label()
        self.refresh_mods()  # 版本或隔离设置可能在其他页面变化
