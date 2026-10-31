from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..modrinth import primary_file, required_dependencies
from ..servers import ServerInfo, addons_dir
from ..workers import (
    ImageFetchWorker,
    ModDownloadWorker,
    ModProjectWorker,
    ModProjectsWorker,
    ServerAddonSearchWorker,
    ServerAddonVersionsWorker,
)
from . import icons
from .dialogs import StyledDialog
from .mod_dialog import (
    ModResultRow,
    _IMAGE_CACHE,
    _cache_image,
    _cover_pixmap,
    _human_number,
    _plain_excerpt,
    _short,
)
from .widgets import DependencyBar, badge, colors, repolish

PAGE_SIZE = 6


class ServerAddonDialog(StyledDialog):
    installed = Signal(str)

    def __init__(self, main, server: ServerInfo) -> None:
        super().__init__(
            main,
            f"下载{server.addon_label}",
            "plug" if server.is_plugin_server else "cube",
            icon_color="#0E9F9A" if server.is_plugin_server else "#7152E8",
            width=900,
        )
        self.main = main
        self.server = server
        self._search_worker: ServerAddonSearchWorker | None = None
        self._project_worker: ModProjectWorker | None = None
        self._versions_worker: ServerAddonVersionsWorker | None = None
        self._download_worker: ModDownloadWorker | None = None
        self._image_workers: dict[str, ImageFetchWorker] = {}
        self._image_waiters: dict[str, list] = {}
        self._dep_projects_worker: ModProjectsWorker | None = None
        self._dep_projects: dict[str, dict] = {}
        self._dep_projects_pending: set[str] = set()
        self._nav_stack: list[tuple[str, dict, str]] = []
        self._pending_project_id = ""
        self._pending_version_id = ""
        self._hits: list[dict] = []
        self._rows: list[ModResultRow] = []
        self._page = 0
        self._total = 0
        self._polling = False
        self._selected_project = ""
        self._project: dict | None = None
        self._hit: dict | None = None

        intro = QLabel(
            f"{server.type_name} {server.mc_version} · "
            f"{server.addon_label}会安装到：{addons_dir(server)}"
            + ("。服务器运行中安装后需要重启才能加载。" if server.is_plugin_server else "。")
        )
        intro.setObjectName("Tiny")
        intro.setWordWrap(True)
        self.body.addWidget(intro)

        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText(
            f"搜索服务端{server.addon_label}名称、作者或关键词，留空浏览热门项目…"
        )
        self.search.setClearButtonEnabled(True)
        self.search.addAction(
            icons.icon("search", colors()["muted"], 15), QLineEdit.LeadingPosition
        )
        self.search.returnPressed.connect(lambda: self._search(0))
        self.search_btn = QPushButton("搜索")
        self.search_btn.setObjectName("Primary")
        self.search_btn.setCursor(Qt.PointingHandCursor)
        self.search_btn.clicked.connect(lambda: self._search(0))
        search_row.addWidget(self.search, 1)
        search_row.addWidget(self.search_btn)
        self.body.addLayout(search_row)

        split = QHBoxLayout()
        split.setSpacing(10)

        results_panel = QFrame()
        results_panel.setObjectName("SoftCard")
        results_panel.setAttribute(Qt.WA_StyledBackground, True)
        results_panel.setFixedWidth(330)
        results_layout = QVBoxLayout(results_panel)
        results_layout.setContentsMargins(9, 8, 9, 8)
        results_layout.setSpacing(6)

        result_header = QHBoxLayout()
        result_title = QLabel("搜索结果")
        result_title.setStyleSheet("font-weight: 700; font-size: 12px;")
        self.result_count = QLabel("")
        self.result_count.setObjectName("Tiny")
        result_header.addWidget(result_title)
        result_header.addWidget(self.result_count)
        result_header.addStretch(1)
        results_layout.addLayout(result_header)

        self.results_scroll = QScrollArea()
        self.results_scroll.setWidgetResizable(True)
        self.results_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.results_holder = QWidget()
        self.results_layout = QVBoxLayout(self.results_holder)
        self.results_layout.setContentsMargins(0, 0, 3, 0)
        self.results_layout.setSpacing(3)
        self.results_layout.addStretch(1)
        self.results_scroll.setWidget(self.results_holder)
        results_layout.addWidget(self.results_scroll, 1)

        pager = QHBoxLayout()
        pager.setSpacing(5)
        self.prev_btn = QPushButton("上一页")
        self.prev_btn.setObjectName("Ghost")
        self.prev_btn.setCursor(Qt.PointingHandCursor)
        self.prev_btn.setIcon(icons.icon("chevron-left", colors()["text2"], 14))
        self.prev_btn.clicked.connect(self._prev_page)
        self.page_label = QLabel("第 1 页")
        self.page_label.setObjectName("Tiny")
        self.page_label.setAlignment(Qt.AlignCenter)
        self.next_btn = QPushButton("下一页")
        self.next_btn.setObjectName("Ghost")
        self.next_btn.setCursor(Qt.PointingHandCursor)
        self.next_btn.setIcon(icons.icon("chevron-right", colors()["text2"], 14))
        self.next_btn.setLayoutDirection(Qt.RightToLeft)
        self.next_btn.clicked.connect(self._next_page)
        pager.addWidget(self.prev_btn)
        pager.addWidget(self.page_label, 1)
        pager.addWidget(self.next_btn)
        results_layout.addLayout(pager)
        split.addWidget(results_panel)

        self.detail_panel = QFrame()
        self.detail_panel.setObjectName("SoftCard")
        self.detail_panel.setAttribute(Qt.WA_StyledBackground, True)
        detail_layout = QVBoxLayout(self.detail_panel)
        detail_layout.setContentsMargins(12, 10, 12, 10)
        detail_layout.setSpacing(7)

        hero = QHBoxLayout()
        hero.setSpacing(10)
        self.detail_icon = QLabel()
        self.detail_icon.setFixedSize(52, 52)
        self.detail_icon.setAlignment(Qt.AlignCenter)
        self.detail_icon.setStyleSheet(
            f"background: {colors()['card2']}; border-radius: 11px;"
        )
        self.detail_icon.setPixmap(icons.pixmap("plug", colors()["accent"], 27))
        hero.addWidget(self.detail_icon)

        info_layout = QVBoxLayout()
        info_layout.setSpacing(2)
        self.detail_title = QLabel(f"选择一个{server.addon_label}查看详情")
        self.detail_title.setStyleSheet("font-weight: 700; font-size: 15px;")
        self.detail_title.setWordWrap(True)
        self.detail_author = QLabel("从左侧搜索结果中选择项目。")
        self.detail_author.setObjectName("Tiny")
        self.detail_author.setWordWrap(True)
        info_layout.addWidget(self.detail_title)
        info_layout.addWidget(self.detail_author)
        hero.addLayout(info_layout, 1)
        detail_layout.addLayout(hero)

        stats = QHBoxLayout()
        stats.setSpacing(6)
        self.downloads_badge = badge("0 下载", "gray")
        self.followers_badge = badge("0 关注", "gray")
        self.updated_label = QLabel("")
        self.updated_label.setObjectName("Tiny")
        stats.addWidget(self.downloads_badge)
        stats.addWidget(self.followers_badge)
        stats.addWidget(self.updated_label)
        stats.addStretch(1)
        detail_layout.addLayout(stats)

        self.detail_desc = QLabel("")
        self.detail_desc.setObjectName("Tiny")
        self.detail_desc.setWordWrap(True)
        self.detail_desc.setMaximumHeight(46)
        detail_layout.addWidget(self.detail_desc)

        self.meta_categories = self._meta_row(detail_layout, "分类")
        self.meta_loaders = self._meta_row(detail_layout, "加载器")
        self.meta_versions = self._meta_row(detail_layout, "游戏版本")
        self.meta_license = self._meta_row(detail_layout, "许可证")

        self.body_excerpt = QLabel("")
        self.body_excerpt.setObjectName("Tiny")
        self.body_excerpt.setWordWrap(True)
        self.body_excerpt.setMaximumHeight(92)
        detail_layout.addWidget(self.body_excerpt)
        detail_layout.addStretch(1)

        version_row = QHBoxLayout()
        version_row.setSpacing(7)
        version_label = QLabel("安装文件")
        version_label.setObjectName("Tiny")
        self.version_combo = QComboBox()
        self.version_combo.setMinimumWidth(220)
        self.version_combo.setEnabled(False)
        self.version_combo.currentIndexChanged.connect(
            lambda _: self._on_version_changed()
        )
        self.download_btn = QPushButton(f"下载到 {server.addon_dir_name}")
        self.download_btn.setObjectName("Primary")
        self.download_btn.setCursor(Qt.PointingHandCursor)
        self.download_btn.setIcon(icons.icon("download", "#FFFFFF", 15))
        self.download_btn.setEnabled(False)
        self.download_btn.clicked.connect(self._download)
        version_row.addWidget(version_label)
        version_row.addWidget(self.version_combo, 1)
        version_row.addWidget(self.download_btn)
        detail_layout.addLayout(version_row)

        self.dep_label = QLabel("")
        self.dep_label.setObjectName("Tiny")
        self.dep_label.setWordWrap(True)
        detail_layout.addWidget(self.dep_label)

        self.dep_bar = DependencyBar()
        self.dep_bar.dependency_clicked.connect(self._open_dependency)
        self.dep_bar.back_clicked.connect(self._go_back_dependency)
        detail_layout.addWidget(self.dep_bar)

        self.progress_box = QWidget()
        progress_layout = QVBoxLayout(self.progress_box)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.setSpacing(3)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        self.progress_text = QLabel("")
        self.progress_text.setObjectName("Tiny")
        progress_layout.addWidget(self.progress)
        progress_layout.addWidget(self.progress_text)
        self.progress_box.setVisible(False)
        detail_layout.addWidget(self.progress_box)
        split.addWidget(self.detail_panel, 1)
        self.body.addLayout(split, 1)

        self._folder_btn = self.add_button("打开文件夹", "Secondary", self._open_folder)
        self.add_button("关闭", "Secondary", self.reject)

        self.results_scroll.setMinimumHeight(330)
        self.detail_panel.setMinimumHeight(330)
        QTimer.singleShot(0, lambda: self._search(0))

    def reject(self) -> None:
        worker = self._download_worker
        if worker is not None:
            self._download_worker = None
            try:
                worker.finished.disconnect(self._on_download_finished)
            except (RuntimeError, TypeError):
                pass
            worker.cancel()
        super().reject()

    @staticmethod
    def _meta_row(layout: QVBoxLayout, title: str) -> QLabel:
        row = QHBoxLayout()
        row.setSpacing(6)
        label = QLabel(title)
        label.setObjectName("Tiny")
        label.setFixedWidth(48)
        value = QLabel("—")
        value.setObjectName("Tiny")
        value.setWordWrap(True)
        row.addWidget(label, 0, Qt.AlignTop)
        row.addWidget(value, 1)
        layout.addLayout(row)
        return value

    # ------------------------------------------------------------------
    def _search(self, page: int = 0) -> None:
        if self._polling:
            return
        page = max(0, int(page))
        self._polling = True
        self.search_btn.setEnabled(False)
        offset = page * PAGE_SIZE
        self._update_pager()
        if page == 0:
            self._render_results(loading=True)
        worker = ServerAddonSearchWorker(
            self.search.text().strip(),
            self.server.kind,
            self.server.mc_version,
            offset,
            PAGE_SIZE,
            self,
        )
        self._search_worker = worker
        worker.ready.connect(
            lambda data, error, used, w=worker: self._search_ready(w, data, error, used)
        )
        worker.start()

    def _search_ready(self, worker, data, error: str, offset: int) -> None:
        if worker is not self._search_worker:
            return
        self._polling = False
        self.search_btn.setEnabled(True)
        if error or data is None:
            self._render_results(message=f"搜索失败：{_short(error, 70)}")
            return
        hits = [hit for hit in data.get("hits") or [] if isinstance(hit, dict)]
        self._hits = hits
        self._page = offset // PAGE_SIZE
        self._total = int(data.get("total_hits") or len(hits))
        self._render_results()
        if hits:
            first = str(hits[0].get("project_id") or hits[0].get("slug") or "")
            if first:
                self._select_project(first, hits[0])
        else:
            self._selected_project = ""
            self._project = None
            self._hit = None
            self._clear_detail("没有找到匹配的项目。")

    def _render_results(self, loading: bool = False, message: str = "") -> None:
        while self.results_layout.count() > 1:
            item = self.results_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()
        self._rows.clear()

        if loading:
            hint = QLabel("正在搜索 Modrinth…")
            hint.setObjectName("Tiny")
            hint.setAlignment(Qt.AlignCenter)
            self.results_layout.insertWidget(0, hint)
        elif message:
            hint = QLabel(message)
            hint.setObjectName("Muted")
            hint.setWordWrap(True)
            hint.setAlignment(Qt.AlignCenter)
            self.results_layout.insertWidget(0, hint)
        elif not self._hits:
            hint = QLabel(f"没有找到匹配的服务端{self.server.addon_label}。")
            hint.setObjectName("Muted")
            hint.setWordWrap(True)
            hint.setAlignment(Qt.AlignCenter)
            self.results_layout.insertWidget(0, hint)
        else:
            for hit in self._hits:
                row = ModResultRow(hit, self.server.kind)
                row.clicked.connect(
                    lambda project_id, h=hit: self._select_project(project_id, h)
                )
                row.set_selected(
                    str(hit.get("project_id") or hit.get("slug") or "")
                    == self._selected_project
                )
                self.results_layout.insertWidget(self.results_layout.count() - 1, row)
                self._rows.append(row)
                icon_url = str(hit.get("icon_url") or "")
                if icon_url:
                    image = _IMAGE_CACHE.get(icon_url)
                    if image is not None:
                        row.set_icon(image)
                    else:
                        self._load_image(icon_url, lambda img, r=row: r.set_icon(img))
        self._sync_results_height()
        self._update_pager()

    def _sync_results_height(self) -> None:
        self.results_layout.activate()
        self.results_holder.setMinimumHeight(self.results_layout.sizeHint().height())

    def _update_pager(self) -> None:
        pages = max(1, (self._total + PAGE_SIZE - 1) // PAGE_SIZE)
        current = min(self._page + 1, pages)
        self.page_label.setText(f"第 {current} / {pages} 页" if self._total else "第 1 页")
        self.result_count.setText(f"共 {self._total} 个" if self._total else "")
        self.prev_btn.setEnabled(not self._polling and self._page > 0)
        self.next_btn.setEnabled(
            not self._polling and (self._page + 1) * PAGE_SIZE < self._total
        )

    def _prev_page(self) -> None:
        if self._page > 0 and not self._polling:
            self._search(self._page - 1)

    def _next_page(self) -> None:
        if not self._polling and (self._page + 1) * PAGE_SIZE < self._total:
            self._search(self._page + 1)

    # ------------------------------------------------------------------
    def _select_project(
        self, project_id: str, hit: dict, *, keep_nav: bool = False
    ) -> None:
        if not project_id:
            return
        if not keep_nav:
            self._nav_stack.clear()
            self._pending_project_id = ""
            self._pending_version_id = ""
        self._selected_project = project_id
        self._hit = hit
        for row in self._rows:
            row.set_selected(row.project_id == project_id)
        same = self._project is not None and str(self._project.get("id") or "") == project_id
        if not same:
            self._project = None
            worker = ModProjectWorker(project_id, self)
            self._project_worker = worker
            worker.ready.connect(self._project_ready)
            worker.start()

        self.detail_title.setText(str(hit.get("title") or hit.get("slug") or "项目"))
        author = str(hit.get("author") or "")
        categories = [str(item) for item in hit.get("categories") or [] if item]
        meta_parts = []
        if author:
            meta_parts.append(f"作者：{author}")
        if categories:
            meta_parts.append(f"分类：{', '.join(categories[:3])}")
        self.detail_author.setText(" · ".join(meta_parts) or "Modrinth")
        self.detail_desc.setText(str(hit.get("description") or ""))
        self.downloads_badge.setText(f"{_human_number(hit.get('downloads'))} 下载")
        self.followers_badge.setText("0 关注")
        self.updated_label.setText("")
        self.version_combo.clear()
        self.version_combo.setEnabled(False)
        self.version_combo.addItem("正在获取可用文件…", "")
        self._sync_download_button()
        self.dep_label.setText("")
        self.dep_bar.set_state(
            [], show_back=bool(self._nav_stack), projects=self._dep_projects
        )
        self.progress_box.setVisible(False)
        self.body_excerpt.setText("")
        for label in (
            self.meta_categories,
            self.meta_loaders,
            self.meta_versions,
            self.meta_license,
        ):
            label.setText("—")
        self._set_detail_icon(str(hit.get("icon_url") or ""))

        versions_worker = ServerAddonVersionsWorker(
            project_id, self.server.kind, self.server.mc_version, self
        )
        self._versions_worker = versions_worker
        versions_worker.ready.connect(
            lambda data, error, w=versions_worker, pid=project_id: self._versions_ready(
                w, pid, data, error
            )
        )
        versions_worker.start()

    def _project_ready(self, project_id: str, data, error: str) -> None:
        if project_id != self._selected_project or error or not isinstance(data, dict):
            return
        self._project = data
        title = str(data.get("title") or self.detail_title.text())
        self.detail_title.setText(title)
        description = str(data.get("description") or "")
        if description:
            self.detail_desc.setText(description)
        categories = [str(item) for item in data.get("categories") or [] if item]
        author = str((self._hit or {}).get("author") or "")
        meta_parts = []
        if author:
            meta_parts.append(f"作者：{author}")
        if categories:
            meta_parts.append(f"分类：{', '.join(categories[:3])}")
        if meta_parts:
            self.detail_author.setText(" · ".join(meta_parts))
        self.downloads_badge.setText(f"{_human_number(data.get('downloads'))} 下载")
        self.followers_badge.setText(f"{_human_number(data.get('followers'))} 关注")
        updated = str(data.get("updated") or "")[:10]
        self.updated_label.setText(f"更新于 {updated}" if updated else "")
        self.meta_categories.setText(" / ".join(categories[:5]) or "—")
        loaders = [str(item) for item in data.get("loaders") or [] if item]
        self.meta_loaders.setText(" / ".join(loaders[:4]) or "—")
        versions = [str(item) for item in data.get("game_versions") or [] if item]
        text = "、".join(versions[:8])
        if len(versions) > 8:
            text += f" 等 {len(versions)} 个"
        self.meta_versions.setText(text or "—")
        license_data = data.get("license")
        if isinstance(license_data, dict):
            license_text = str(license_data.get("name") or license_data.get("id") or "")
        else:
            license_text = str(license_data or "")
        self.meta_license.setText(license_text or "—")
        excerpt = _plain_excerpt(str(data.get("body") or ""), 260)
        self.body_excerpt.setText(excerpt)
        self.body_excerpt.setVisible(bool(excerpt))
        self._set_detail_icon(
            str(data.get("icon_url") or (self._hit or {}).get("icon_url") or "")
        )

    def _versions_ready(self, worker, project_id: str, data, error: str) -> None:
        if worker is not self._versions_worker or project_id != self._selected_project:
            return
        preferred = (
            self._pending_version_id
            if project_id == self._pending_project_id
            else ""
        )
        self._pending_project_id = ""
        self._pending_version_id = ""

        self.version_combo.blockSignals(True)
        try:
            self.version_combo.clear()
            if error:
                self.version_combo.addItem("获取版本失败", "")
                self.dep_label.setText(error)
                self.dep_label.setStyleSheet(f"color: {colors()['red']};")
                self._sync_download_button()
                return
            if not data:
                self.version_combo.addItem("没有适配当前服务端的文件", "")
                self.dep_label.setText(
                    f"该项目没有适配 {self.server.type_name} {self.server.mc_version} 的文件，"
                    "可以尝试搜索其他项目。"
                )
                self.dep_label.setStyleSheet(f"color: {colors()['orange']};")
                self._sync_download_button()
                return
            target_index = 0
            for index, version in enumerate(data):
                file = primary_file(version) or {}
                version_number = str(
                    version.get("version_number") or version.get("name") or ""
                )
                filename = str(file.get("filename") or "")
                version_type = str(version.get("version_type") or "")
                suffix = f" · {version_type}" if version_type else ""
                self.version_combo.addItem(
                    f"{_short(version_number, 34)}{suffix}", version
                )
                tip = f"{version_number}\n{filename}" if filename else version_number
                self.version_combo.setItemData(index, tip, Qt.ToolTipRole)
                if preferred and str(version.get("id") or "") == preferred:
                    target_index = index
            self.version_combo.setCurrentIndex(target_index)
            self.version_combo.setEnabled(True)
        finally:
            self.version_combo.blockSignals(False)
        self.dep_label.setStyleSheet("")
        self._on_version_changed()

    def _on_version_changed(self) -> None:
        self._update_dependencies()
        self._sync_download_button()

    def _update_dependencies(self) -> None:
        version = self.version_combo.currentData()
        if not isinstance(version, dict):
            self.dep_bar.set_state(
                [], show_back=bool(self._nav_stack), projects=self._dep_projects
            )
            return
        deps = required_dependencies(version)
        visible = [dep for dep in deps if str(dep.get("project_id") or "")]
        file = primary_file(version) or {}
        size = int(file.get("size") or 0)
        parts = []
        if size:
            parts.append(f"{size / 1024 / 1024:.1f} MB")
        if visible:
            parts.append(
                f"需要 {len(visible)} 个前置 {self.server.addon_label}，"
                "点击下方按钮直接下载"
            )
        elif deps:
            parts.append(f"声明了 {len(deps)} 个必需前置，请确认已一并安装")
        else:
            parts.append("没有声明必需前置")
        self.dep_label.setText(" · ".join(parts))
        self.dep_label.setStyleSheet(
            f"color: {colors()['orange']};" if deps else ""
        )
        if visible:
            self._request_dependency_projects(visible)
        self.dep_bar.set_state(
            visible,
            show_back=bool(self._nav_stack),
            projects=self._dep_projects,
        )

    def _request_dependency_projects(self, deps: list[dict]) -> None:
        ids = [
            str(dep.get("project_id") or "")
            for dep in deps
            if isinstance(dep, dict)
        ]
        ids = [
            project_id
            for project_id in dict.fromkeys(ids)
            if project_id
            and project_id not in self._dep_projects
            and project_id not in self._dep_projects_pending
        ]
        if not ids:
            return
        self._dep_projects_pending.update(ids)
        worker = ModProjectsWorker(ids, self)
        self._dep_projects_worker = worker
        worker.ready.connect(
            lambda data, error, w=worker: self._dep_projects_ready(w, data, error)
        )
        worker.start()

    def _dep_projects_ready(
        self, worker: ModProjectsWorker, data, error: str
    ) -> None:
        self._dep_projects_pending.difference_update(worker.project_ids)
        if worker is self._dep_projects_worker:
            self._dep_projects_worker = None
        if isinstance(data, dict) and data:
            self._dep_projects.update(data)
            self.dep_bar.update_projects(data)

    def _current_version_id(self) -> str:
        version = self.version_combo.currentData()
        if isinstance(version, dict):
            return str(version.get("id") or "")
        return ""

    @staticmethod
    def _dependency_hit(project_id: str, project: dict | None) -> dict:
        data = project if isinstance(project, dict) else {}
        return {
            "project_id": str(data.get("id") or project_id),
            "slug": str(data.get("slug") or ""),
            "title": str(data.get("title") or data.get("slug") or project_id),
            "description": str(data.get("description") or ""),
            "author": str(data.get("author") or ""),
            "downloads": data.get("downloads") or 0,
            "categories": data.get("categories") or [],
            "icon_url": str(data.get("icon_url") or ""),
        }

    def _open_dependency(self, project_id: str, version_id: str) -> None:
        project_id = str(project_id or "")
        if not project_id:
            return
        if self._download_worker is not None:
            self.main.toast(
                f"请先等待当前{self.server.addon_label}下载结束，再查看前置项目", "info"
            )
            return
        project = self._dep_projects.get(project_id) or {}
        canonical = str(project.get("id") or project_id)
        if canonical == self._selected_project:
            return
        self._nav_stack.append(
            (
                self._selected_project,
                dict(self._hit or {}),
                self._current_version_id(),
            )
        )
        self._pending_project_id = canonical
        self._pending_version_id = str(version_id or "")
        self._select_project(
            canonical, self._dependency_hit(canonical, project), keep_nav=True
        )

    def _go_back_dependency(self) -> None:
        if not self._nav_stack:
            return
        if self._download_worker is not None:
            self.main.toast("请先等待当前下载结束，再返回", "info")
            return
        project_id, hit, version_id = self._nav_stack.pop()
        if not project_id:
            self.dep_bar.set_state(
                [], show_back=False, projects=self._dep_projects
            )
            return
        self._pending_project_id = project_id
        self._pending_version_id = version_id
        self._select_project(project_id, hit, keep_nav=True)

    def _set_detail_icon(self, url: str) -> None:
        fallback = "plug" if self.server.is_plugin_server else "cube"
        if not url:
            self.detail_icon.setPixmap(icons.pixmap(fallback, colors()["accent"], 27))
            return
        image = _IMAGE_CACHE.get(url)
        if image is not None:
            self.detail_icon.setPixmap(_cover_pixmap(image, 52, 11))
            return
        self.detail_icon.setPixmap(icons.pixmap(fallback, colors()["accent"], 27))
        self._load_image(
            url,
            lambda image, expected=url: self._detail_icon_ready(expected, image),
        )

    def _detail_icon_ready(self, url: str, image) -> None:
        expected = {
            str((self._hit or {}).get("icon_url") or ""),
            str((self._project or {}).get("icon_url") or ""),
        }
        if url not in expected:
            return
        if image is not None and not getattr(image, "isNull", lambda: True)():
            self.detail_icon.setPixmap(_cover_pixmap(image, 52, 11))

    def _clear_detail(self, message: str = "") -> None:
        self.detail_title.setText(f"选择一个{self.server.addon_label}查看详情")
        self.detail_author.setText("从左侧搜索结果中选择项目。")
        self.detail_desc.setText(message)
        self.downloads_badge.setText("0 下载")
        self.followers_badge.setText("0 关注")
        self.updated_label.setText("")
        self.version_combo.clear()
        self.version_combo.setEnabled(False)
        self.version_combo.addItem("请选择一个项目", "")
        self._sync_download_button()
        self.dep_label.setText("")
        self._nav_stack.clear()
        self._pending_project_id = ""
        self._pending_version_id = ""
        self.dep_bar.clear()
        self.progress_box.setVisible(False)
        self.body_excerpt.setText("")
        for label in (
            self.meta_categories,
            self.meta_loaders,
            self.meta_versions,
            self.meta_license,
        ):
            label.setText("—")
        self.detail_icon.setPixmap(
            icons.pixmap(
                "plug" if self.server.is_plugin_server else "cube",
                colors()["accent"],
                27,
            )
        )

    # ------------------------------------------------------------------
    def _load_image(self, url: str, callback) -> None:
        if not url:
            return
        cached = _IMAGE_CACHE.get(url)
        if cached is not None:
            try:
                callback(cached)
            except RuntimeError:
                pass
            return
        self._image_waiters.setdefault(url, []).append(callback)
        if url in self._image_workers:
            return
        worker = ImageFetchWorker(url, self)
        self._image_workers[url] = worker
        worker.ready.connect(self._image_ready)
        worker.start()

    def _image_ready(self, url: str, image) -> None:
        self._image_workers.pop(url, None)
        if image is not None and not getattr(image, "isNull", lambda: True)():
            _cache_image(url, image)
        callbacks = self._image_waiters.pop(url, [])
        for callback in callbacks:
            try:
                callback(image)
            except RuntimeError:
                continue

    def _sync_download_button(self) -> None:
        """下载中始终允许点“取消下载”，不被切换项目等刷新流程重新禁用。"""
        if self._download_worker is not None:
            self.download_btn.setEnabled(True)
        else:
            self.download_btn.setEnabled(
                isinstance(self.version_combo.currentData(), dict)
            )

    def _set_download_button(self, downloading: bool) -> None:
        if downloading:
            self.download_btn.setObjectName("Danger")
            self.download_btn.setText("取消下载")
            self.download_btn.setIcon(icons.icon("stop", colors()["red"], 15))
            self.download_btn.setEnabled(True)
        else:
            self.download_btn.setObjectName("Primary")
            self.download_btn.setText(f"下载到 {self.server.addon_dir_name}")
            self.download_btn.setIcon(icons.icon("download", "#FFFFFF", 15))
            self._sync_download_button()
        repolish(self.download_btn)

    def _download(self) -> None:
        if self._download_worker is not None:
            self._download_worker.cancel()
            self.download_btn.setText("正在取消…")
            self.download_btn.setEnabled(False)
            return
        version = self.version_combo.currentData()
        if not isinstance(version, dict):
            return
        target = addons_dir(self.server)
        self.progress.setValue(0)
        self.progress_text.setText(f"准备下载到 {self.server.addon_dir_name}…")
        self.progress_box.setVisible(True)
        worker = ModDownloadWorker(
            version,
            target,
            threads=self.main.config.download_threads,
            parent=self.main,
        )
        self._download_worker = worker
        worker.progress.connect(self._on_progress)
        worker.finished.connect(self._on_download_finished)
        self._set_download_button(True)
        worker.start()

    def _on_progress(self, percent: int, message: str) -> None:
        self.progress.setValue(max(0, min(100, percent)))
        self.progress_text.setText(message)

    def _on_download_finished(self, success: bool, message: str, filename: str) -> None:
        self._download_worker = None
        self._set_download_button(False)
        if success:
            self.progress.setValue(100)
            self.progress_text.setText(f"已安装 {filename}")
            self.main.toast(f"{filename} 已下载到 {self.server.addon_dir_name}", "success")
            self.installed.emit(filename)
            page = getattr(self.main, "server_page", None)
            if page is not None:
                page.refresh_addons()
        elif "取消" in str(message):
            self.progress_text.setText("下载已取消")
            self.main.toast("已取消下载，临时文件已清理", "info")
        else:
            self.progress_text.setText(message)
            self.main.toast(f"下载失败：{message}", "error")

    def _open_folder(self) -> None:
        path = addons_dir(self.server)
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.main.toast(f"无法创建文件夹：{exc}", "error")
            return
        self.main.open_path(path)
