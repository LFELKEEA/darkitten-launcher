from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...mod_updates import ModUpdate
from ...mods import ModInfo, forget_mod_meta, list_mods, toggle_mod
from ...workers import (
    DeleteWorker,
    ModConflictCheckWorker,
    ModUpdateApplyWorker,
    ModUpdateCheckWorker,
)
from ..mod_health_dialog import ModHealthDialog
from ..mod_update_dialog import ModUpdatesDialog
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
    repolish,
    transparent_children,
)


class _ElidedLabel(QLabel):
    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = ""
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:
        self._full_text = " ".join(str(text or "").split())
        self._refresh()

    def full_text(self) -> str:
        return self._full_text

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self) -> None:
        width = self.contentsRect().width()
        if width <= 0:
            QLabel.setText(self, self._full_text)
            return
        elided = self.fontMetrics().elidedText(
            self._full_text, Qt.ElideRight, width
        )
        if QLabel.text(self) != elided:
            QLabel.setText(self, elided)


def _icon_pixmap(data: bytes, size: int = 38, radius: int = 9) -> QPixmap:
    image = QImage()
    if data:
        image.loadFromData(data)
    canvas = QImage(size, size, QImage.Format_ARGB32)
    canvas.fill(Qt.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    path = QPainterPath()
    path.addRoundedRect(0.5, 0.5, size - 1, size - 1, radius, radius)
    painter.setClipPath(path)
    if not image.isNull():
        scaled = image.scaled(
            size,
            size,
            Qt.KeepAspectRatioByExpanding,
            Qt.SmoothTransformation,
        )
        painter.drawImage(
            (size - scaled.width()) // 2, (size - scaled.height()) // 2, scaled
        )
    painter.end()
    return QPixmap.fromImage(canvas)


class ModRow(QFrame):
    toggled = Signal(object, bool)
    delete_requested = Signal(object)
    update_requested = Signal(object, object)

    def __init__(
        self, mod: ModInfo, parent=None, update: ModUpdate | None = None
    ) -> None:
        super().__init__(parent)
        self.mod = mod
        self.setObjectName("SelectRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(82)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(13, 7, 13, 7)
        layout.setSpacing(11)

        icon_box = QLabel()
        icon_box.setFixedSize(40, 40)
        icon_box.setAlignment(Qt.AlignCenter)
        pixmap = _icon_pixmap(mod.icon_bytes, 40, 10)
        if pixmap.isNull() or not mod.icon_bytes:
            icon_box.setPixmap(icons.pixmap("cube", colors()["purple"], 22))
            icon_box.setStyleSheet(
                f"background: {colors()['purple_soft']}; border-radius: 9px;"
            )
        else:
            icon_box.setPixmap(pixmap)
            icon_box.setStyleSheet("background: transparent;")
        self.icon_box = icon_box

        info = QVBoxLayout()
        info.setSpacing(2)
        top = QHBoxLayout()
        top.setSpacing(6)
        name = QLabel(mod.name)
        name.setStyleSheet("font-weight: 700; font-size: 12px;")
        name.setToolTip(mod.name)
        top.addWidget(name)
        if mod.version:
            version = QLabel(mod.version)
            version.setObjectName("Tiny")
            version.setToolTip(mod.version)
            top.addWidget(version)
        if mod.loader:
            top.addWidget(
                badge(
                    mod.loader,
                    "purple" if mod.loader == "Fabric" else "orange",
                )
            )
        if not mod.enabled:
            top.addWidget(badge("已禁用", "gray"))
        if update is not None:
            top.addWidget(badge("可更新", "green"))
        top.addStretch(1)
        info.addLayout(top)

        intro = _ElidedLabel(mod.intro)
        intro.setObjectName("Tiny")
        tip = mod.intro
        if mod.description and mod.description != mod.intro:
            tip += "\n\n原版简介：" + mod.description
        if mod.project_url:
            tip += "\n项目地址：" + mod.project_url
        intro.setToolTip(tip)
        self.intro_label = intro
        info.addWidget(intro)

        meta = _ElidedLabel(f"{mod.filename} · {mod.size_text}")
        meta.setObjectName("Tiny")
        meta.setToolTip(f"{mod.filename} · {mod.size_text}")
        info.addWidget(meta)

        update_btn = None
        if update is not None:
            update_btn = QPushButton("更新")
            update_btn.setObjectName("Soft")
            update_btn.setCursor(Qt.PointingHandCursor)
            update_btn.setToolTip(
                f"{mod.filename} → {update.file_name}\n"
                f"来源：{update.source_label}"
            )
            update_btn.clicked.connect(
                lambda _=False: self.update_requested.emit(self.mod, update)
            )

        switch = Switch()
        switch.setChecked(mod.enabled)
        switch.setToolTip("启用或禁用此 Mod")
        switch.toggled.connect(lambda checked: self.toggled.emit(self.mod, checked))

        delete_btn = icon_button("trash", "删除此 Mod", colors()["muted"], 30, 15)
        delete_btn.clicked.connect(lambda _=False: self.delete_requested.emit(self.mod))

        layout.addWidget(icon_box)
        layout.addLayout(info, 1)
        if update_btn is not None:
            layout.addWidget(update_btn)
        layout.addWidget(switch)
        layout.addWidget(delete_btn)
        transparent_children(self)
        switch.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        delete_btn.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        if update_btn is not None:
            update_btn.setAttribute(Qt.WA_TransparentForMouseEvents, False)


class ModsPage(QWidget):
    def __init__(self, main, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.main = main
        self._mods: list[ModInfo] = []
        self._delete_worker: DeleteWorker | None = None
        self._update_worker: ModUpdateCheckWorker | None = None
        self._updates: dict[str, ModUpdate] = {}
        self._health_worker: ModConflictCheckWorker | None = None
        self._health_issues = []
        self._health_descriptors = []
        self._health_auto = False
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
        self.health_badge = badge("环境待检查", "gray")
        self.health_badge.setToolTip("检查加载器混装、重复 modId、缺失前置和版本冲突")
        header.addWidget(self.health_badge)
        loader_btn = QPushButton("管理加载器")
        loader_btn.setObjectName("Ghost")
        loader_btn.setCursor(Qt.PointingHandCursor)
        loader_btn.setToolTip("前往加载器页面安装或分配 Fabric / Forge / NeoForge")
        loader_btn.clicked.connect(lambda: self.main.switch_page("loaders"))
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

        self.check_btn = QPushButton("检查更新")
        self.check_btn.setObjectName("Ghost")
        self.check_btn.setCursor(Qt.PointingHandCursor)
        self.check_btn.setIcon(icons.icon("refresh", colors()["text2"], 14))
        self.check_btn.setToolTip(
            "通过 Modrinth / CurseForge 检查当前版本已安装 Mod 的新版本"
        )
        self.check_btn.clicked.connect(self._check_updates)
        toolbar_layout.addWidget(self.check_btn)

        self.health_btn = QPushButton("环境检查")
        self.health_btn.setObjectName("Ghost")
        self.health_btn.setCursor(Qt.PointingHandCursor)
        self.health_btn.setToolTip("检查 Mod 冲突、缺失前置和加载器混装")
        self.health_btn.clicked.connect(self._show_health)
        toolbar_layout.addWidget(self.health_btn)

        self.update_all_btn = QPushButton("全部更新")
        self.update_all_btn.setObjectName("Primary")
        self.update_all_btn.setCursor(Qt.PointingHandCursor)
        self.update_all_btn.setVisible(False)
        self.update_all_btn.clicked.connect(self._update_all)
        toolbar_layout.addWidget(self.update_all_btn)

        download_btn = QPushButton("下载内容")
        download_btn.setObjectName("Primary")
        download_btn.setIcon(icons.icon("download", "#FFFFFF", 15))
        download_btn.setCursor(Qt.PointingHandCursor)
        download_btn.setToolTip("从 Modrinth 或 CurseForge 下载 Mod、整合包、材质与光影")
        download_btn.clicked.connect(self.main.open_mod_download)
        toolbar_layout.addWidget(download_btn)

        add_btn = QPushButton("添加内容")
        add_btn.setObjectName("Secondary")
        add_btn.setIcon(icons.icon("plus", colors()["text2"], 15))
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.setToolTip("导入 Mod、材质包、光影包或内容合集")
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
        empty_sub = QLabel(
            "右上角可以在线叼回内容，也可以把 Mod .jar、材质 / 光影 ZIP "
            "或内容合集直接拖进来。"
        )
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
        profile = (
            self.main.loader_profile_for(self.main.selected_version)
            if self.main.selected_version
            else None
        )
        summary = next(
            (v for v in self.main.versions if v.id == self.main.selected_version),
            None,
        )
        loader_name = profile.loader if profile else (
            summary.loader if summary else ""
        )
        loader_note = f" · {loader_name}" if loader_name else " · 原版"
        self.version_label.setText(
            f"当前版本：{version}{loader_note} · "
            f"{'独立目录' if isolated else '公共目录'}"
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
        self._updates.clear()
        self._update_version_label()
        self.refresh_mods()
        self._check_health(auto=True)

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
            if (
                query
                and query not in mod.name.lower()
                and query not in mod.filename.lower()
                and query not in mod.intro.lower()
            ):
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
            update = self._updates.get(mod.filename)
            row = ModRow(mod, update=update)
            row.toggled.connect(self._toggle_mod)
            row.delete_requested.connect(self._delete_mod)
            row.update_requested.connect(self._update_one)
            self.list_layout.insertWidget(self.list_layout.count() - 1, row)
        self.update_all_btn.setVisible(bool(self._updates))
        if self._updates:
            self.update_all_btn.setText(f"全部更新 ({len(self._updates)})")

    def _game_context(self) -> tuple[str, str]:
        version_id = self.main.selected_version or ""
        summary = next(
            (v for v in self.main.versions if v.id == version_id),
            None,
        )
        game_version = (
            (summary.base_id or summary.id) if summary else version_id
        )
        profile = (
            self.main.loader_profile_for(version_id) if version_id else None
        )
        loader = profile.loader if profile else (
            summary.loader if summary else ""
        )
        return game_version, loader

    def _check_updates(self) -> None:
        if self._update_worker is not None:
            self.main.toast("正在检查 Mod 更新，请稍候", "info")
            return
        mods = list_mods(self._mods_dir())
        if not mods:
            self.main.toast("当前版本还没有安装 Mod", "info")
            return
        game_version, loader = self._game_context()
        self.check_btn.setEnabled(False)
        self.check_btn.setText("检查中…")
        self.count_label.setText("正在读取项目版本…")
        worker = ModUpdateCheckWorker(mods, game_version, loader, self)
        self._update_worker = worker
        worker.progress.connect(self._on_update_check_progress)
        worker.finished.connect(self._on_update_checked)
        worker.start()

    def _on_update_check_progress(self, done: int, total: int, name: str) -> None:
        self.check_btn.setText(f"检查中 {done}/{total}")
        self.count_label.setText(f"正在检查 {name}…")

    def _on_update_checked(self, updates, errors) -> None:
        self._update_worker = None
        self.check_btn.setEnabled(True)
        self.check_btn.setText("检查更新")
        self.count_label.setText(f"{len(self._mods)} 个 Mod")
        found = list(updates or [])
        self._updates = {
            update.mod.filename: update for update in found
        }
        self.refresh_mods()
        if errors:
            detail = "；".join(str(item) for item in list(errors)[:2])
            self.main.toast(f"{len(found)} 个可更新，部分检查失败：{detail}", "info")
        if not found:
            self.main.toast("所有 Mod 都是最新版本，或没有找到可用项目来源", "success")
            return
        dialog = ModUpdatesDialog(self, found)
        if dialog.exec():
            self._apply_updates(dialog.selected)

    def _update_all(self) -> None:
        updates = list(self._updates.values())
        if not updates:
            self.main.toast("还没有可更新的 Mod，请先检查更新", "info")
            return
        dialog = ModUpdatesDialog(self, updates)
        if dialog.exec():
            self._apply_updates(dialog.selected)

    def _update_one(self, _mod: ModInfo, update: ModUpdate) -> None:
        if not isinstance(update, ModUpdate):
            return
        self._apply_updates([update])

    def _apply_updates(self, updates: list[ModUpdate]) -> None:
        if not updates:
            return
        if (
            self.main.game_running
            and self.main._running_version == self.main.selected_version
        ):
            self.main.toast("游戏正在运行，请先关闭游戏再更新 Mod", "error")
            return
        worker = ModUpdateApplyWorker(
            updates,
            self._mods_dir(),
            threads=self.main.config.download_threads,
            parent=self,
        )
        worker.finished.connect(self._on_updates_applied)
        title = (
            f"更新 {len(updates)} 个 Mod"
            if len(updates) > 1
            else f"更新 Mod：{updates[0].mod.name}"
        )
        self.main.download_center.start_worker(
            worker,
            title=title,
            content_type="mod-update",
            target=self._mods_dir(),
            source="mixed",
        )
        self.main.toast("更新已加入下载中心，关闭页面也会继续", "info")

    def _on_updates_applied(self, success: bool, message: str, _filename: str) -> None:
        self._updates.clear()
        self.refresh_mods()
        self._check_health(auto=True)
        self.main.toast(message, "success" if success else "error")
        if success:
            self.main.set_status(message, "success")

    def _check_health(self, auto: bool = False) -> None:
        if self._health_worker is not None:
            if not auto:
                self._health_auto = False
            return
        if not self._mods:
            self._health_issues = []
            self._health_descriptors = []
            self.health_badge.setText("无 Mod")
            self.health_badge.setProperty("badge", "gray")
            repolish(self.health_badge)
            if not auto:
                self.main.toast("当前版本还没有安装 Mod", "info")
            return
        self._health_auto = bool(auto)
        self.health_badge.setText("检查中…")
        self.health_badge.setProperty("badge", "blue")
        repolish(self.health_badge)
        _game_version, loader = self._game_context()
        worker = ModConflictCheckWorker(self._mods_dir(), loader, self)
        self._health_worker = worker
        worker.finished.connect(self._on_health_checked)
        worker.start()

    def _on_health_checked(self, issues, descriptors, error: str) -> None:
        self._health_worker = None
        self._health_issues = list(issues or [])
        self._health_descriptors = list(descriptors or [])
        errors = sum(
            1 for item in self._health_issues if item.severity == "error"
        )
        warnings = sum(
            1 for item in self._health_issues if item.severity == "warning"
        )
        if error:
            self.health_badge.setText("检查失败")
            self.health_badge.setProperty("badge", "red")
        elif errors:
            self.health_badge.setText(f"{errors} 个错误")
            self.health_badge.setProperty("badge", "red")
        elif warnings:
            self.health_badge.setText(f"{warnings} 个警告")
            self.health_badge.setProperty("badge", "orange")
        else:
            self.health_badge.setText("环境正常")
            self.health_badge.setProperty("badge", "green")
        repolish(self.health_badge)
        if error:
            self.main.toast(f"环境检查失败：{error}", "error")
            return
        if not self._health_auto:
            ModHealthDialog(
                self, self._health_issues, self._health_descriptors
            ).exec()

    def _show_health(self) -> None:
        if self._health_worker is not None:
            self._health_auto = False
            self.main.toast("正在检查 Mod 环境，稍等一下", "info")
            return
        if self._health_issues or not self._mods:
            ModHealthDialog(
                self, self._health_issues, self._health_descriptors
            ).exec()
            return
        self._check_health(auto=False)

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
        forget_mod_meta(mod.path.parent, mod.filename)
        self._updates.pop(mod.filename, None)
        self.refresh_mods()
        self._check_health(auto=True)

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
        self._updates.clear()
        self.refresh_mods()
        self._check_health(auto=True)

    def open_folder(self) -> None:
        mods_dir = self._mods_dir()
        try:
            mods_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.main.toast(f"无法创建 mods 文件夹：{exc}", "error")
            return
        self.main.open_path(mods_dir)

    def add_mods(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择要导入的本地内容",
            str(self._mods_dir()),
            (
                "Mod / 材质 / 光影 (*.jar *.litemod *.zip "
                "*.jar.disabled *.litemod.disabled);;所有文件 (*)"
            ),
        )
        if not files:
            return
        self.main.open_local_import([Path(item) for item in files])

    def on_show(self) -> None:
        self._update_version_label()
        self.refresh_mods()  # 版本或隔离设置可能在其他页面变化
        self._check_health(auto=True)
