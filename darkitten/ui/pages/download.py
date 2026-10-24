from __future__ import annotations

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...workers import ManifestWorker
from .. import icons
from ..widgets import (
    Card,
    ChibiCompanion,
    SegmentedControl,
    badge,
    colors,
    icon_button,
    repolish,
    transparent_children,
)

TYPE_LABELS = {
    "release": "正式版",
    "snapshot": "快照",
    "old_beta": "旧测试版",
    "old_alpha": "远古版",
}


def _date_text(value: str) -> str:
    if not value:
        return "未知时间"
    return value[:10]


class DownloadVersionRow(QFrame):
    clicked = Signal(str)

    def __init__(self, entry: dict, installed: bool, parent=None) -> None:
        super().__init__(parent)
        self.entry = entry
        self.setObjectName("SelectRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(52)
        self.setProperty("selected", "false")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(11, 6, 10, 6)
        layout.setSpacing(9)

        marker = QLabel()
        marker.setFixedSize(28, 28)
        marker.setAlignment(Qt.AlignCenter)
        vtype = str(entry.get("type", "release"))
        color = colors()["green"] if vtype == "release" else (
            colors()["orange"] if vtype == "snapshot" else colors()["muted"]
        )
        marker.setPixmap(icons.pixmap("cube", color, 18))

        info = QVBoxLayout()
        info.setSpacing(1)
        top = QHBoxLayout()
        top.setSpacing(6)
        name = QLabel(str(entry.get("id", "")))
        name.setStyleSheet("font-weight: 700; font-size: 12px;")
        top.addWidget(name)
        top.addWidget(
            badge(
                TYPE_LABELS.get(vtype, vtype),
                "green" if vtype == "release" else ("orange" if vtype == "snapshot" else "gray"),
            )
        )
        top.addStretch(1)
        if installed:
            top.addWidget(badge("已安装", "blue"))
        info.addLayout(top)

        meta = QLabel(_date_text(str(entry.get("releaseTime", ""))))
        meta.setObjectName("Tiny")
        info.addWidget(meta)

        layout.addWidget(marker)
        layout.addLayout(info, 1)
        chevron = QLabel()
        chevron.setPixmap(icons.pixmap("chevron-right", colors()["muted"], 15))
        layout.addWidget(chevron)
        transparent_children(self)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", "true" if selected else "false")
        repolish(self)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit(str(self.entry.get("id", "")))
        super().mouseReleaseEvent(event)


class DownloadPage(QWidget):
    def __init__(self, main, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.main = main
        self._manifest: dict | None = None
        self._entries: list[dict] = []
        self._rows: dict[str, DownloadVersionRow] = {}
        self._selected = ""
        self._filter = "all"
        self._limit = 70
        self._worker: ManifestWorker | None = None
        self._loading = False
        self._build()
        self.main.versions_changed.connect(self._on_local_versions_changed)
        self.main.install_started.connect(self._on_install_started)
        self.main.install_progress.connect(self._on_install_progress)
        self.main.task_log.connect(self._on_task_log)
        self.main.install_finished.connect(self._on_install_finished)

    # ------------------------------------------------------------------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 18, 22, 18)
        outer.setSpacing(14)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = QLabel("猫猫的游戏版本仓库")
        title.setObjectName("PageTitle")
        sub = QLabel(
            "在线安装 Mojang 原版，或把版本 ZIP / 版本文件夹直接拖进来。"
            "本地文件会直接落进对应目录，不再绕临时文件夹。"
        )
        sub.setObjectName("Muted")
        sub.setWordWrap(True)
        titles.addWidget(title)
        titles.addWidget(sub)
        header.addLayout(titles, 1)
        header.addWidget(ChibiCompanion(60, "download"), 0, Qt.AlignTop)
        local_btn = QPushButton("本地导入")
        local_btn.setObjectName("Secondary")
        local_btn.setIcon(icons.icon("folder", colors()["accent"], 15))
        local_btn.setCursor(Qt.PointingHandCursor)
        local_btn.setToolTip(
            "导入版本文件夹、版本 ZIP、Mod、材质、光影、整合包或服务端"
        )
        local_btn.clicked.connect(lambda: self.main.open_local_import())
        header.addWidget(local_btn)
        pack_btn = QPushButton("导入 ZIP / 整合包")
        pack_btn.setObjectName("Secondary")
        pack_btn.setIcon(icons.icon("package", colors()["accent"], 15))
        pack_btn.setCursor(Qt.PointingHandCursor)
        pack_btn.setToolTip(
            "从本地或链接导入 ZIP 版本包、DKL 整合包或 Modrinth 整合包"
        )
        pack_btn.clicked.connect(self.main.open_version_import)
        header.addWidget(pack_btn)
        loader_btn = QPushButton("管理加载器")
        loader_btn.setObjectName("Secondary")
        loader_btn.setIcon(icons.icon("cube", colors()["purple"], 15))
        loader_btn.setCursor(Qt.PointingHandCursor)
        loader_btn.setToolTip("前往加载器页面安装或分配 Fabric / Forge / NeoForge")
        loader_btn.clicked.connect(lambda: self.main.switch_page("loaders"))
        header.addWidget(loader_btn)
        self.source_badge = badge("官方源", "blue")
        source_btn = QPushButton("更改下载源")
        source_btn.setObjectName("Ghost")
        source_btn.setCursor(Qt.PointingHandCursor)
        source_btn.clicked.connect(lambda: self.main.switch_page("settings"))
        header.addWidget(self.source_badge)
        header.addWidget(source_btn)
        outer.addLayout(header)
        self._update_source_badge()

        content = QHBoxLayout()
        content.setSpacing(14)
        content.addWidget(self._build_list_card(), 2)
        content.addWidget(self._build_detail_card(), 3)
        outer.addLayout(content, 1)

    def _build_list_card(self) -> QWidget:
        card = Card()
        card.setMinimumWidth(360)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(15, 14, 15, 12)
        layout.setSpacing(9)

        top = QHBoxLayout()
        title = QLabel("可领养的版本")
        title.setObjectName("H2")
        top.addWidget(title)
        top.addStretch(1)
        refresh = icon_button("refresh", "刷新版本清单")
        refresh.clicked.connect(lambda: self.load_manifest(force=True))
        top.addWidget(refresh)
        layout.addLayout(top)

        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索版本号…")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(
            icons.icon("search", colors()["muted"], 15), QLineEdit.LeadingPosition
        )
        self.search.textChanged.connect(self._on_search)
        layout.addWidget(self.search)

        filter_row = QHBoxLayout()
        self.filters = SegmentedControl()
        self.filters.add_item("全部", "all")
        self.filters.add_item("正式版", "release")
        self.filters.add_item("快照", "snapshot")
        self.filters.changed.connect(self._on_filter)
        filter_row.addWidget(self.filters)
        filter_row.addStretch(1)
        self.count_label = QLabel("")
        self.count_label.setObjectName("Tiny")
        filter_row.addWidget(self.count_label)
        layout.addLayout(filter_row)

        self.list_scroll = QScrollArea()
        self.list_scroll.setWidgetResizable(True)
        self.list_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list_holder = QWidget()
        self.list_layout = QVBoxLayout(self.list_holder)
        self.list_layout.setContentsMargins(0, 0, 4, 0)
        self.list_layout.setSpacing(3)
        self.list_layout.addStretch(1)
        self.list_scroll.setWidget(self.list_holder)
        layout.addWidget(self.list_scroll, 1)

        self.load_more = QPushButton("加载更多版本")
        self.load_more.setObjectName("Ghost")
        self.load_more.setCursor(Qt.PointingHandCursor)
        self.load_more.clicked.connect(self._more)
        self.load_more.setVisible(False)
        layout.addWidget(self.load_more)

        self.list_status = QLabel("正在准备版本清单…")
        self.list_status.setObjectName("Tiny")
        self.list_status.setAlignment(Qt.AlignCenter)
        self.list_status.setMinimumHeight(40)
        layout.addWidget(self.list_status)
        return card

    def _build_detail_card(self) -> QWidget:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)

        self.empty = QWidget()
        empty_layout = QVBoxLayout(self.empty)
        empty_layout.setContentsMargins(0, 0, 0, 0)
        empty_layout.addStretch(1)
        icon_label = QLabel()
        icon_label.setPixmap(icons.pixmap("cube", colors()["muted"], 58))
        icon_label.setAlignment(Qt.AlignCenter)
        empty_title = QLabel("挑一个版本吧")
        empty_title.setAlignment(Qt.AlignCenter)
        empty_title.setStyleSheet("font-size: 16px; font-weight: 700;")
        empty_sub = QLabel("点左侧任意版本，本喵去把它搬回你的猫窝。")
        empty_sub.setObjectName("Muted")
        empty_sub.setAlignment(Qt.AlignCenter)
        empty_layout.addWidget(icon_label)
        empty_layout.addWidget(empty_title)
        empty_layout.addWidget(empty_sub)
        empty_layout.addStretch(1)
        layout.addWidget(self.empty, 1)

        self.detail = QWidget()
        detail_layout = QVBoxLayout(self.detail)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.detail_badge = badge("正式版", "green")
        self.detail_title = QLabel("")
        self.detail_title.setStyleSheet("font-size: 24px; font-weight: 800;")
        head.addWidget(self.detail_title)
        head.addWidget(self.detail_badge)
        head.addStretch(1)
        self.install_state = badge("未安装", "gray")
        head.addWidget(self.install_state)
        detail_layout.addLayout(head)

        self.detail_sub = QLabel("")
        self.detail_sub.setObjectName("Muted")
        detail_layout.addWidget(self.detail_sub)

        info_box = QFrame()
        info_box.setObjectName("SoftCard")
        info_box.setAttribute(Qt.WA_StyledBackground, True)
        info_layout = QHBoxLayout(info_box)
        info_layout.setContentsMargins(16, 12, 16, 12)
        info_layout.setSpacing(26)
        self.info_type = self._info_column(info_layout, "版本类型")
        self.info_date = self._info_column(info_layout, "发布时间")
        self.info_source = self._info_column(info_layout, "下载源")
        info_layout.addStretch(1)
        detail_layout.addWidget(info_box)

        desc = QLabel(
            "将安装客户端、依赖库、运行库与完整资源文件。"
            "如果本机已经存在相同文件会自动跳过，不会重复下载。"
        )
        desc.setWordWrap(True)
        desc.setObjectName("Tiny")
        detail_layout.addWidget(desc)

        detail_layout.addStretch(1)

        self.progress_box = QWidget()
        progress_layout = QVBoxLayout(self.progress_box)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.setSpacing(5)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        info_row = QHBoxLayout()
        self.progress_text = QLabel("准备中…")
        self.progress_text.setObjectName("Tiny")
        self.speed_text = QLabel("")
        self.speed_text.setObjectName("Tiny")
        info_row.addWidget(self.progress_text, 1)
        info_row.addWidget(self.speed_text)
        progress_layout.addWidget(self.progress)
        progress_layout.addLayout(info_row)
        self.progress_box.setVisible(False)
        detail_layout.addWidget(self.progress_box)

        self.log = QPlainTextEdit()
        self.log.setObjectName("SoftLog")
        self.log.setReadOnly(True)
        self.log.setFixedHeight(130)
        self.log.setPlaceholderText("安装日志会显示在这里")
        self.log.setMaximumBlockCount(1000)
        detail_layout.addWidget(self.log)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addStretch(1)
        self.cancel_btn = QPushButton("取消安装")
        self.cancel_btn.setObjectName("Danger")
        self.cancel_btn.setCursor(Qt.PointingHandCursor)
        self.cancel_btn.setMinimumSize(110, 42)
        self.cancel_btn.setIcon(icons.icon("stop", colors()["red"], 16))
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self._cancel_install)
        actions.addWidget(self.cancel_btn)
        self.install_btn = QPushButton("安装此版本")
        self.install_btn.setObjectName("Primary")
        self.install_btn.setCursor(Qt.PointingHandCursor)
        self.install_btn.setMinimumSize(148, 42)
        self.install_btn.setIcon(icons.icon("download", "#FFFFFF", 17))
        self.install_btn.clicked.connect(self._install_selected)
        actions.addWidget(self.install_btn)
        detail_layout.addLayout(actions)

        layout.addWidget(self.detail, 1)
        self.detail.setVisible(False)
        return card

    @staticmethod
    def _info_column(layout: QHBoxLayout, title: str) -> QLabel:
        box = QVBoxLayout()
        box.setSpacing(2)
        label = QLabel(title)
        label.setObjectName("Tiny")
        value = QLabel("—")
        value.setStyleSheet("font-weight: 700;")
        box.addWidget(label)
        box.addWidget(value)
        layout.addLayout(box)
        return value

    # ------------------------------------------------------------------
    def _source_text(self) -> str:
        source = self.main.config.download_source
        if source == "bmclapi":
            return "BMCLAPI 镜像"
        if source == "auto":
            return "智能加速"
        return "Mojang 官方"

    def _update_source_badge(self) -> None:
        source = self.main.config.download_source
        self.source_badge.setText(self._source_text())
        self.source_badge.setProperty(
            "badge", "purple" if source == "bmclapi" else ("green" if source == "auto" else "blue")
        )
        repolish(self.source_badge)

    def on_show(self) -> None:
        self._update_source_badge()
        self._update_cancel_button()
        if not self._manifest and not self._loading:
            self.load_manifest()
        QTimer.singleShot(0, self._ensure_selected_visible)

    def _ensure_selected_visible(self) -> None:
        row = self._rows.get(self._selected)
        if row:
            self.list_scroll.ensureWidgetVisible(row, 0, 8)

    def load_manifest(self, force: bool = False) -> None:
        if self._loading:
            return
        self._loading = True
        self.list_status.setText("正在获取版本清单…")
        self.list_status.setVisible(True)
        self.search.setEnabled(False)
        self.filters.setEnabled(False)
        self._worker = ManifestWorker(self.main.config.download_source, force)
        self._worker.ready.connect(self._manifest_ready)
        self._worker.start()

    def _manifest_ready(self, data, error: str) -> None:
        self._loading = False
        self.search.setEnabled(True)
        self.filters.setEnabled(True)
        if not data:
            self.list_status.setText(f"获取版本清单失败：{error}")
            self.list_status.setVisible(True)
            retry = self.load_more
            retry.setVisible(True)
            retry.setText("重试")
            try:
                retry.clicked.disconnect()
            except (RuntimeError, TypeError):
                pass
            retry.clicked.connect(lambda: self.load_manifest(force=True))
            return

        self._manifest = data
        raw = data.get("versions") or []
        self._entries = [v for v in raw if isinstance(v, dict) and v.get("id")]
        self._limit = 70
        self._apply_filter()
        latest = data.get("latest") or {}
        preferred = latest.get("release") or (self._entries[0]["id"] if self._entries else "")
        if self.main.selected_version in {e.get("id") for e in self._entries}:
            preferred = self.main.selected_version
        if preferred:
            self.select_version(str(preferred), scroll=False)

    def refresh_theme(self) -> None:
        self._update_source_badge()
        if self._manifest:
            self._apply_filter()
            if self._selected:
                entry = next(
                    (e for e in self._entries if str(e.get("id")) == self._selected),
                    None,
                )
                if entry and not self.main.install_active:
                    self._update_detail(entry)
            QTimer.singleShot(0, self._ensure_selected_visible)

    def _on_search(self, _text: str) -> None:
        self._limit = 70
        self._apply_filter()

    def _on_filter(self, value: str) -> None:
        self._filter = value
        self._limit = 70
        self._apply_filter()

    def _filtered(self) -> list[dict]:
        query = self.search.text().strip().lower()
        result = []
        for entry in self._entries:
            vtype = str(entry.get("type", ""))
            if self._filter == "release" and vtype != "release":
                continue
            if self._filter == "snapshot" and vtype != "snapshot":
                continue
            if query and query not in str(entry.get("id", "")).lower():
                continue
            result.append(entry)
        return result

    def _apply_filter(self) -> None:
        filtered = self._filtered()
        self.count_label.setText(f"{len(filtered)} 个")
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self._rows.clear()

        if not filtered:
            self.list_status.setText("没有匹配的版本")
            self.list_status.setVisible(True)
            self.load_more.setVisible(False)
            return

        self.list_status.setVisible(False)
        installed = {v.id for v in self.main.versions}
        for entry in filtered[: self._limit]:
            row = DownloadVersionRow(entry, str(entry.get("id")) in installed)
            row.clicked.connect(self.select_version)
            row.set_selected(str(entry.get("id")) == self._selected)
            self._rows[str(entry.get("id"))] = row
            self.list_layout.insertWidget(self.list_layout.count() - 1, row)

        self.load_more.setText("加载更多版本")
        self.load_more.setVisible(len(filtered) > self._limit)
        try:
            self.load_more.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.load_more.clicked.connect(self._more)

    def _more(self) -> None:
        self._limit += 90
        self._apply_filter()

    def select_version(self, version_id: str, scroll: bool = True) -> None:
        entry = next(
            (e for e in self._entries if str(e.get("id")) == version_id), None
        )
        if not entry:
            return
        self._selected = version_id
        for vid, row in self._rows.items():
            row.set_selected(vid == version_id)
        self._update_detail(entry)
        if scroll:
            row = self._rows.get(version_id)
            if row:
                self.list_scroll.ensureWidgetVisible(row)

    def _update_detail(self, entry: dict) -> None:
        self.empty.setVisible(False)
        self.detail.setVisible(True)
        version_id = str(entry.get("id", ""))
        vtype = str(entry.get("type", "release"))
        type_label = TYPE_LABELS.get(vtype, vtype)
        self.detail_title.setText(version_id)
        self.detail_badge.setText(type_label)
        self.detail_badge.setProperty(
            "badge", "green" if vtype == "release" else ("orange" if vtype == "snapshot" else "gray")
        )
        repolish(self.detail_badge)

        latest = (self._manifest or {}).get("latest") or {}
        tags = []
        if latest.get("release") == version_id:
            tags.append("最新正式版")
        if latest.get("snapshot") == version_id:
            tags.append("最新快照")
        sub = " · ".join(tags + [f"Minecraft Java Edition {type_label}"])
        self.detail_sub.setText(sub)

        self.info_type.setText(type_label)
        self.info_date.setText(_date_text(str(entry.get("releaseTime", ""))))
        self.info_source.setText(self._source_text())

        installed_ids = {v.id for v in self.main.versions}
        installed = version_id in installed_ids
        self.install_state.setText("已安装" if installed else "未安装")
        self.install_state.setProperty("badge", "green" if installed else "gray")
        repolish(self.install_state)
        self.install_btn.setText("补全 / 校验文件" if installed else "安装此版本")
        self.install_btn.setEnabled(not self.main.install_active)
        self._update_cancel_button()
        self.progress_box.setVisible(False)
        self.log.clear()

    def _install_selected(self) -> None:
        if not self._selected:
            return
        if self.main.install_active:
            self.main.toast("已有安装任务正在进行", "info")
            return
        entry = next(
            (e for e in self._entries if str(e.get("id")) == self._selected), None
        )
        if not entry:
            return
        installed = self._selected in {v.id for v in self.main.versions}
        self.progress.setValue(0)
        self.progress_text.setText("准备安装…")
        self.progress_box.setVisible(True)
        self.log.clear()
        self.main.begin_install(
            str(entry.get("id")),
            version_url=str(entry.get("url", "")),
            verify=installed,
        )

    def _on_install_started(self, version_id: str) -> None:
        if version_id == self._selected:
            self.progress_box.setVisible(True)
            self.progress.setValue(0)
            self.progress_text.setText("准备安装…")
            self.install_btn.setEnabled(False)
        self._update_cancel_button()

    def _update_cancel_button(self) -> None:
        if not hasattr(self, "cancel_btn"):
            return
        installed = self.main.install_active
        self.cancel_btn.setVisible(bool(installed))
        self.cancel_btn.setEnabled(bool(installed) and not self.main._install_cancelling)
        self.cancel_btn.setText(
            "正在取消…" if installed and self.main._install_cancelling else "取消安装"
        )

    def _cancel_install(self) -> None:
        self.main.cancel_install()
        self.cancel_btn.setText("正在取消…")
        self.cancel_btn.setEnabled(False)

    def _on_install_progress(self, percent: int, message: str) -> None:
        self.progress.setValue(percent)
        self.progress_text.setText(message)
        worker = self.main.install_worker
        if worker and worker.installer:
            self.speed_text.setText(worker.installer.speed_text)

    def _on_task_log(self, line: str) -> None:
        if self.progress_box.isVisible():
            self.log.appendPlainText(line)
            bar = self.log.verticalScrollBar()
            bar.setValue(bar.maximum())

    def _on_install_finished(self, success: bool, message: str, version_id: str) -> None:
        self._update_cancel_button()
        if version_id == self._selected:
            self.install_btn.setEnabled(True)
            if success:
                self.progress.setValue(100)
                self.progress_text.setText("安装完成")
                self.speed_text.setText("")
            else:
                self.progress_text.setText(message)
                self.speed_text.setText("")
            self.install_btn.setText("补全 / 校验文件")

    def _on_local_versions_changed(self) -> None:
        if not self._manifest:
            return
        self._apply_filter()
        if self._selected:
            entry = next(
                (e for e in self._entries if str(e.get("id")) == self._selected), None
            )
            if entry:
                installed = self._selected in {v.id for v in self.main.versions}
                self.install_state.setText("已安装" if installed else "未安装")
                self.install_state.setProperty(
                    "badge", "green" if installed else "gray"
                )
                repolish(self.install_state)
                self.install_btn.setText(
                    "补全 / 校验文件" if installed else "安装此版本"
                )
