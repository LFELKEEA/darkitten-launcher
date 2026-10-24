from __future__ import annotations

import re
import urllib.parse
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QImage,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..config import APP_DIR
from ..descriptions import (
    category_labels,
    project_zh_intro,
    project_title,
)
from ..modrinth import primary_file, required_dependencies
from ..versions import resolve_version
from ..workers import (
    ImageFetchWorker,
    ModProjectWorker,
    ModProjectsWorker,
    ModSearchWorker,
    ModVersionsWorker,
)
from . import icons
from .dialogs import StyledDialog
from .download_center import TASK_CANCELLED, TASK_SUCCESS, DownloadTask
from .loader_dialog import LoaderDialog
from .widgets import (
    DependencyBar,
    SegmentedControl,
    badge,
    colors,
    icon_button,
    repolish,
    transparent_children,
)

COMMON_VERSIONS = [
    "1.21.1",
    "1.21",
    "1.20.6",
    "1.20.4",
    "1.20.1",
    "1.19.4",
    "1.18.2",
    "1.16.5",
]

LOADER_LABELS = {"fabric": "Fabric", "neoforge": "NeoForge", "forge": "Forge"}
CONTENT_LABELS = {
    "mod": "Mod",
    "modpack": "整合包",
    "resourcepack": "材质",
    "shader": "光影",
}
CONTENT_FOLDERS = {
    "mod": "mods",
    "resourcepack": "resourcepacks",
    "shader": "shaderpacks",
}
MODRINTH_PATHS = {
    "mod": "mod",
    "modpack": "modpack",
    "resourcepack": "resourcepack",
    "shader": "shader",
}
PAGE_SIZE = 5
FORUM_HOME = "https://klpbbs.com/"
_MAX_CACHE = 80
_IMAGE_CACHE: dict[str, QImage] = {}


def forum_search_url(query: str = "") -> str:
    text = (query or "").strip()
    if not text:
        return FORUM_HOME
    return (
        "https://klpbbs.com/search.php?mod=forum&srchtxt="
        + urllib.parse.quote(text)
    )


def _human_number(value) -> str:
    try:
        number = int(value)
    except Exception:
        return "0"
    if number >= 1_000_000:
        return f"{number / 1_000_000:.1f}M"
    if number >= 1_000:
        return f"{number / 1_000:.1f}K"
    return str(number)


def _short(text: str, length: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= length else text[: length - 1] + "…"


def _plain_excerpt(body: str, limit: int = 360) -> str:
    text = str(body or "")
    if not text:
        return ""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[#>*_~|]+", " ", text)
    text = re.sub(r"^\s*[-+]\s+", "", text, flags=re.M)
    text = re.sub(r"\s+", " ", text).strip()
    return _short(text, limit)


def _cover_pixmap(image: QImage, size: int, radius: int = 9) -> QPixmap:
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
        x = (size - scaled.width()) // 2
        y = (size - scaled.height()) // 2
        painter.drawImage(x, y, scaled)
    painter.end()
    return QPixmap.fromImage(canvas)


def _cache_image(url: str, image: QImage) -> None:
    if not url or image.isNull():
        return
    _IMAGE_CACHE[url] = image
    while len(_IMAGE_CACHE) > _MAX_CACHE:
        _IMAGE_CACHE.pop(next(iter(_IMAGE_CACHE)))


class _ElidedLabel(QLabel):
    """按可用宽度省略文本，避免固定宽度列表里出现硬裁切。"""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = ""
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:
        self._full_text = " ".join(str(text or "").split())
        if self._full_text:
            self.setToolTip(self._full_text)
        else:
            self.setToolTip("")
        self._refresh()

    def full_text(self) -> str:
        return self._full_text

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self) -> None:
        width = self.contentsRect().width()
        if width <= 0:
            return
        elided = self.fontMetrics().elidedText(
            self._full_text, Qt.ElideRight, width
        )
        if QLabel.text(self) != elided:
            QLabel.setText(self, elided)


class GalleryPreview(QLabel):
    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(150)
        self.setMinimumWidth(220)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self._image = QImage()
        self._message = "该项目暂未提供预览图"
        self._hover = False

    def set_image(self, image: QImage) -> None:
        self._image = image
        self.update()

    def set_message(self, message: str) -> None:
        self._image = QImage()
        self._message = message
        self.update()

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        path = QPainterPath()
        path.addRoundedRect(rect, 9, 9)
        painter.setClipPath(path)
        painter.fillRect(rect, QColor("#101722"))
        if self._image.isNull():
            painter.setPen(QColor("#93A4BD"))
            painter.drawText(rect, Qt.AlignCenter, self._message)
        else:
            scaled = self._image.scaled(
                self.width(),
                self.height(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            painter.drawImage(x, y, scaled)
            if self._hover:
                painter.fillRect(rect, QColor(255, 255, 255, 10))
        painter.end()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class ModResultRow(QFrame):
    clicked = Signal(str)

    def __init__(
        self,
        hit: dict,
        loader: str,
        parent=None,
        source_label: str = "Modrinth",
        content_type: str = "mod",
    ) -> None:
        super().__init__(parent)
        self.hit = hit
        self.project_id = str(hit.get("project_id") or hit.get("slug") or "")
        self.source_label = source_label
        intro_text = project_zh_intro(
            hit, content_type=content_type, loader=loader
        )
        self.setObjectName("SelectRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(58)
        self.setProperty("selected", "false")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(9, 6, 9, 6)
        layout.setSpacing(9)

        self.icon_label = QLabel()
        self.icon_label.setFixedSize(40, 40)
        self.icon_label.setAlignment(Qt.AlignCenter)
        self.icon_label.setStyleSheet(
            f"background: {colors()['card2']}; border-radius: 9px;"
        )
        self.icon_label.setPixmap(icons.pixmap("cube", colors()["purple"], 22))

        info = QVBoxLayout()
        info.setSpacing(0)
        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        self.title_label = _ElidedLabel(
            str(hit.get("title") or hit.get("slug") or "未知项目")
        )
        self.title_label.setStyleSheet("font-weight: 700; font-size: 12px;")
        title_row.addWidget(self.title_label, 1)
        self.download_badge = badge(
            f"{_human_number(hit.get('downloads'))} 下载", "gray"
        )
        title_row.addWidget(self.download_badge, 0)
        info.addLayout(title_row)

        self.desc_label = _ElidedLabel(intro_text)
        self.desc_label.setObjectName("Tiny")
        self.desc_label.setToolTip(str(hit.get("description") or intro_text))
        info.addWidget(self.desc_label)

        author = str(hit.get("author") or "")
        categories = "、".join(category_labels(hit.get("categories"), 2))
        meta_text = " · ".join(item for item in (author, categories) if item) or source_label
        self.meta_label = _ElidedLabel(meta_text)
        self.meta_label.setObjectName("Tiny")
        info.addWidget(self.meta_label)

        layout.addWidget(self.icon_label)
        layout.addLayout(info, 1)
        transparent_children(self)

    def set_icon(self, image: QImage | None) -> None:
        try:
            if image is not None and not image.isNull():
                self.icon_label.setPixmap(_cover_pixmap(image, 40, 9))
            else:
                self.icon_label.setPixmap(icons.pixmap("cube", colors()["purple"], 22))
        except RuntimeError:
            pass

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", "true" if selected else "false")
        repolish(self)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.project_id:
            self.clicked.emit(self.project_id)
        super().mouseReleaseEvent(event)


class ModBrowserDialog(StyledDialog):
    def __init__(self, main) -> None:
        super().__init__(main, "下载 Mod", "cube", icon_color="#7152E8", width=960)
        self.main = main
        self.body.setSpacing(8)
        self._search_worker: ModSearchWorker | None = None
        self._project_worker: ModProjectWorker | None = None
        self._versions_worker: ModVersionsWorker | None = None
        self._download_task: DownloadTask | None = None
        self._download_bound = False
        self._image_workers: dict[str, ImageFetchWorker] = {}
        self._image_waiters: dict[str, list] = {}
        self._hits: list[dict] = []
        self._rows: list[ModResultRow] = []
        self._page = 0
        self._page_size = PAGE_SIZE
        self._total = 0
        self._polling = False
        self._source = "modrinth"
        self._content_type = "mod"
        self._pack_download_dir: Path | None = None
        self._selected_project = ""
        self._project: dict | None = None
        self._hit: dict | None = None
        self._target_version_id = ""
        self._gallery: list[dict] = []
        self._gallery_index = 0
        self._gallery_url = ""
        self._gallery_caption = ""
        self._dep_projects_worker: ModProjectsWorker | None = None
        self._dep_projects: dict[str, dict] = {}
        self._dep_projects_pending: set[str] = set()
        self._nav_stack: list[tuple[str, dict, str]] = []
        self._pending_project_id = ""
        self._pending_version_id = ""

        self._build_context()
        self._build_search()
        self._build_split()
        self._build_footer()
        self._apply_adaptive_height()

        self._reload_bases()
        self.mc_combo.activated.connect(lambda _: self._on_context_changed())
        self.mc_combo.lineEdit().editingFinished.connect(self._on_context_changed)
        self.loader_tabs.changed.connect(lambda _: self._on_context_changed())
        self._apply_content_mode()
        QTimer.singleShot(0, lambda: self._search(0))

    def _downloading(self) -> bool:
        return self._download_task is not None and self._download_task.running

    def reject(self) -> None:
        task = self._download_task
        self._detach_download_ui()
        if task is not None and task.running:
            self.main.toast(
                "下载已转到顶栏，可以继续操作启动器；点击顶栏进度可查看任务",
                "info",
            )
        super().reject()

    def _detach_download_ui(self) -> None:
        if self._download_bound:
            try:
                self.main.download_center.task_progress.disconnect(
                    self._on_download_progress
                )
                self.main.download_center.task_finished.disconnect(
                    self._on_download_finished
                )
            except (RuntimeError, TypeError):
                pass
            self._download_bound = False
        self._download_task = None

    # ------------------------------------------------------------------
    # 界面构建

    def _build_context(self) -> None:
        context = QFrame()
        context.setObjectName("SoftCard")
        context.setAttribute(Qt.WA_StyledBackground, True)
        context_layout = QVBoxLayout(context)
        context_layout.setContentsMargins(12, 9, 12, 9)
        context_layout.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(8)
        mc_label = QLabel("Minecraft")
        mc_label.setFixedWidth(68)
        mc_label.setStyleSheet("font-weight: 700; font-size: 12px;")
        self.mc_combo = QComboBox()
        self.mc_combo.setEditable(True)
        self.mc_combo.setMinimumWidth(132)
        self.mc_combo.setInsertPolicy(QComboBox.NoInsert)
        row.addWidget(mc_label)
        row.addWidget(self.mc_combo)

        loader_label = QLabel("加载器")
        loader_label.setFixedWidth(46)
        loader_label.setStyleSheet("font-weight: 700; font-size: 12px;")
        self._loader_label = loader_label
        self.loader_tabs = SegmentedControl()
        self.loader_tabs.add_item("Fabric", "fabric")
        self.loader_tabs.add_item("NeoForge", "neoforge")
        self.loader_tabs.add_item("Forge", "forge")
        row.addSpacing(6)
        row.addWidget(loader_label)
        row.addWidget(self.loader_tabs)
        row.addStretch(1)

        self.loader_btn = QPushButton("安装加载器")
        self.loader_btn.setObjectName("Ghost")
        self.loader_btn.setCursor(Qt.PointingHandCursor)
        self.loader_btn.clicked.connect(self._open_loader_dialog)
        row.addWidget(self.loader_btn)

        self.forum_btn = QPushButton("苦力怕论坛")
        self.forum_btn.setObjectName("Ghost")
        self.forum_btn.setCursor(Qt.PointingHandCursor)
        self.forum_btn.setIcon(icons.icon("globe", colors()["text2"], 15))
        self.forum_btn.setToolTip("用当前搜索词前往苦力怕论坛查找汉化、整合包与讨论")
        self.forum_btn.clicked.connect(self._open_forum_search)
        row.addWidget(self.forum_btn)
        context_layout.addLayout(row)

        self.context_label = QLabel("")
        self.context_label.setObjectName("Tiny")
        self.context_label.setWordWrap(True)
        context_layout.addWidget(self.context_label)
        self.body.addWidget(context)

    def _build_search(self) -> None:
        type_row = QHBoxLayout()
        type_row.setSpacing(8)
        type_label = QLabel("内容")
        type_label.setObjectName("Tiny")
        self.type_tabs = SegmentedControl()
        self.type_tabs.add_item("Mod", "mod")
        self.type_tabs.add_item("整合包", "modpack")
        self.type_tabs.add_item("材质", "resourcepack")
        self.type_tabs.add_item("光影", "shader")
        self.type_tabs.changed.connect(self._set_content_type)
        type_row.addWidget(type_label)
        type_row.addWidget(self.type_tabs)
        type_row.addStretch(1)
        source_label = QLabel("下载源")
        source_label.setObjectName("Tiny")
        self.source_tabs = SegmentedControl()
        self.source_tabs.add_item("Modrinth", "modrinth")
        self.source_tabs.add_item("CurseForge", "curseforge")
        self.source_tabs.changed.connect(self._set_source)
        self.source_tabs.setToolTip(
            "CurseForge 通过 MCIM 镜像访问；文件下载优先走镜像 CDN。"
        )
        type_row.addWidget(source_label)
        type_row.addWidget(self.source_tabs)
        self.body.addLayout(type_row)

        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索 Mod 名称、作者或关键词，留空则浏览热门 Mod…")
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

        self.source_note = QLabel("")
        self.source_note.setObjectName("Tiny")
        self.source_note.setWordWrap(True)
        self.source_note.setVisible(False)
        self.body.addWidget(self.source_note)

    def _source_name(self) -> str:
        return "CurseForge" if self._source == "curseforge" else "Modrinth"

    def _content_label(self) -> str:
        return CONTENT_LABELS.get(self._content_type, "Mod")

    def _update_search_placeholder(self) -> None:
        label = self._content_label()
        source = self._source_name()
        if self._content_type == "modpack":
            self.search.setPlaceholderText(
                f"搜索 {source} 整合包名称或关键词，留空则浏览热门整合包…"
            )
        else:
            self.search.setPlaceholderText(
                f"搜索 {source} {label}名称、作者或关键词，留空则浏览热门{label}…"
            )

    def _set_content_type(self, content_type: str) -> None:
        kind = str(content_type or "mod").lower()
        if kind not in CONTENT_LABELS:
            kind = "mod"
        self.type_tabs.set_value(kind)
        if kind == self._content_type:
            return
        self._content_type = kind
        self.title_label.setText(f"下载 {CONTENT_LABELS[kind]}")
        self._apply_content_mode()
        self._reset_results()
        self._search(0)

    def _apply_content_mode(self) -> None:
        is_mod = self._content_type == "mod"
        self._loader_label.setVisible(is_mod)
        self.loader_tabs.setVisible(is_mod)
        self.loader_btn.setVisible(is_mod)
        self._update_search_placeholder()
        if self._content_type == "modpack":
            self._folder_btn.setText("打开整合包缓存")
        else:
            self._folder_btn.setText(
                f"打开 {CONTENT_FOLDERS.get(self._content_type, 'mods')} 文件夹"
            )
        if not self._downloading():
            self.download_btn.setText(self._download_button_text())
        self._sync_download_button()
        self._update_context()

    def _reset_results(self) -> None:
        self._page = 0
        self._selected_project = ""
        self._project = None
        self._hit = None
        self._hits = []
        self._total = 0
        self._nav_stack.clear()
        self._dep_projects.clear()
        self._dep_projects_pending.clear()
        self.dep_bar.clear()
        self._render_results(loading=True)
        self._clear_detail()

    def _set_source(self, source: str) -> None:
        source = "curseforge" if source == "curseforge" else "modrinth"
        self.source_tabs.set_value(source)
        if source == self._source:
            return
        self._source = source
        self._update_search_placeholder()
        if source == "curseforge":
            self.source_note.setText(
                "CurseForge 数据与文件镜像由 MCIM 提供；"
                "若作者关闭第三方下载，会在详情里给出 CurseForge 页面。"
            )
            self.source_note.setVisible(True)
        else:
            self.source_note.setVisible(False)
            self.source_note.setText("")
        self._reset_results()
        self._search(0)

    def _build_split(self) -> None:
        split = QHBoxLayout()
        split.setSpacing(10)

        self.results_panel = QFrame()
        self.results_panel.setObjectName("SoftCard")
        self.results_panel.setAttribute(Qt.WA_StyledBackground, True)
        self.results_panel.setFixedWidth(336)
        results_layout = QVBoxLayout(self.results_panel)
        results_layout.setContentsMargins(9, 8, 9, 8)
        results_layout.setSpacing(6)

        result_header = QHBoxLayout()
        result_header.setSpacing(7)
        result_title = QLabel("搜索结果")
        result_title.setStyleSheet("font-weight: 700; font-size: 12px;")
        self.result_count = QLabel("")
        self.result_count.setObjectName("Tiny")
        result_header.addWidget(result_title)
        result_header.addWidget(self.result_count)
        result_header.addStretch(1)
        self.status_label = QLabel("")
        self.status_label.setObjectName("Tiny")
        self.status_label.setMaximumWidth(150)
        self.status_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        result_header.addWidget(self.status_label)
        results_layout.addLayout(result_header)

        self.results_scroll = QScrollArea()
        self.results_scroll.setWidgetResizable(True)
        self.results_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.results_scroll.setMinimumHeight(150)
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
        split.addWidget(self.results_panel)

        self.detail_panel = QFrame()
        self.detail_panel.setObjectName("SoftCard")
        self.detail_panel.setAttribute(Qt.WA_StyledBackground, True)
        detail_layout = QVBoxLayout(self.detail_panel)
        detail_layout.setContentsMargins(12, 10, 12, 10)
        detail_layout.setSpacing(6)

        self.detail_scroll = QScrollArea()
        self.detail_scroll.setWidgetResizable(True)
        self.detail_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.detail_scroll.setFrameShape(QFrame.NoFrame)
        self.detail_holder = QWidget()
        self.detail_layout = QVBoxLayout(self.detail_holder)
        self.detail_layout.setContentsMargins(0, 0, 4, 0)
        self.detail_layout.setSpacing(7)
        self.detail_scroll.setWidget(self.detail_holder)
        detail_layout.addWidget(self.detail_scroll, 1)

        self._build_detail_content()

        from .widgets import hline

        detail_layout.addWidget(hline())
        action_box = QWidget()
        action_layout = QVBoxLayout(action_box)
        action_layout.setContentsMargins(0, 4, 0, 0)
        action_layout.setSpacing(5)

        version_row = QHBoxLayout()
        version_row.setSpacing(7)
        version_label = QLabel("安装文件")
        version_label.setObjectName("Tiny")
        self.version_combo = QComboBox()
        self.version_combo.setMinimumWidth(200)
        self.version_combo.setEnabled(False)
        self.download_btn = QPushButton("下载到 mods")
        self.download_btn.setObjectName("Primary")
        self.download_btn.setCursor(Qt.PointingHandCursor)
        self.download_btn.setIcon(icons.icon("download", "#FFFFFF", 15))
        self.download_btn.setEnabled(False)
        self.download_btn.clicked.connect(self._download)
        self.version_combo.currentIndexChanged.connect(
            lambda _: self._on_version_changed()
        )
        version_row.addWidget(version_label)
        version_row.addWidget(self.version_combo, 1)
        version_row.addWidget(self.download_btn)
        action_layout.addLayout(version_row)

        self.dep_label = QLabel("")
        self.dep_label.setObjectName("Tiny")
        self.dep_label.setWordWrap(True)
        action_layout.addWidget(self.dep_label)

        self.dep_bar = DependencyBar()
        self.dep_bar.dependency_clicked.connect(self._open_dependency)
        self.dep_bar.back_clicked.connect(self._go_back_dependency)
        action_layout.addWidget(self.dep_bar)

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
        action_layout.addWidget(self.progress_box)
        detail_layout.addWidget(action_box)
        split.addWidget(self.detail_panel, 1)
        self.body.addLayout(split, 1)

    def _build_detail_content(self) -> None:
        hero = QHBoxLayout()
        hero.setSpacing(10)
        self.detail_icon = QLabel()
        self.detail_icon.setFixedSize(54, 54)
        self.detail_icon.setAlignment(Qt.AlignCenter)
        self.detail_icon.setStyleSheet(
            f"background: {colors()['card2']}; border-radius: 11px;"
        )
        self.detail_icon.setPixmap(icons.pixmap("cube", colors()["purple"], 28))
        hero.addWidget(self.detail_icon)

        info = QVBoxLayout()
        info.setSpacing(2)
        self.detail_title = QLabel("选择一个 Mod 查看详细信息")
        self.detail_title.setStyleSheet("font-weight: 700; font-size: 15px;")
        self.detail_title.setWordWrap(True)
        self.detail_author = QLabel("从左侧搜索结果中选择项目。")
        self.detail_author.setObjectName("Tiny")
        self.detail_author.setWordWrap(True)
        info.addWidget(self.detail_title)
        info.addWidget(self.detail_author)
        hero.addLayout(info, 1)

        self.detail_forum_btn = icon_button("globe", "在苦力怕论坛搜索这个 Mod", size=30, icon_size=15)
        self.detail_forum_btn.clicked.connect(self._open_forum_for_selected)
        hero.addWidget(self.detail_forum_btn)
        self.detail_layout.addLayout(hero)

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
        self.detail_layout.addLayout(stats)

        self.detail_desc = QLabel("")
        self.detail_desc.setObjectName("Tiny")
        self.detail_desc.setWordWrap(True)
        self.detail_desc.setMaximumHeight(58)
        self.detail_layout.addWidget(self.detail_desc)

        gallery_header = QHBoxLayout()
        gallery_header.setSpacing(6)
        gallery_title = QLabel("预览图")
        gallery_title.setStyleSheet("font-weight: 700; font-size: 12px;")
        self.gallery_count = QLabel("0 / 0")
        self.gallery_count.setObjectName("Tiny")
        gallery_header.addWidget(gallery_title)
        gallery_header.addWidget(self.gallery_count)
        gallery_header.addStretch(1)
        self.gallery_open_btn = QPushButton("浏览器")
        self.gallery_open_btn.setObjectName("Ghost")
        self.gallery_open_btn.setCursor(Qt.PointingHandCursor)
        self.gallery_open_btn.setIcon(icons.icon("external", colors()["text2"], 13))
        self.gallery_open_btn.setEnabled(False)
        self.gallery_open_btn.clicked.connect(self._open_gallery_current)
        gallery_header.addWidget(self.gallery_open_btn)
        self.detail_layout.addLayout(gallery_header)

        self.gallery_preview = GalleryPreview()
        self.gallery_preview.clicked.connect(self._open_gallery_current)
        self.detail_layout.addWidget(self.gallery_preview)

        gallery_controls = QHBoxLayout()
        gallery_controls.setSpacing(5)
        self.gallery_prev = icon_button(
            "chevron-left", "上一张预览图（翻面）", size=27, icon_size=14
        )
        self.gallery_next = icon_button(
            "chevron-right", "下一张预览图（翻面）", size=27, icon_size=14
        )
        self.gallery_prev.clicked.connect(lambda: self._show_gallery(self._gallery_index - 1))
        self.gallery_next.clicked.connect(lambda: self._show_gallery(self._gallery_index + 1))
        self.gallery_flip = QPushButton("翻面")
        self.gallery_flip.setObjectName("Ghost")
        self.gallery_flip.setCursor(Qt.PointingHandCursor)
        self.gallery_flip.setIcon(icons.icon("flip", colors()["text2"], 13))
        self.gallery_flip.setToolTip("翻到下一张预览图，到末尾后回到第一张")
        self.gallery_flip.clicked.connect(self._flip_gallery)
        self.gallery_flip.setEnabled(False)
        self.gallery_caption_label = QLabel("")
        self.gallery_caption_label.setObjectName("Tiny")
        self.gallery_caption_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        gallery_controls.addWidget(self.gallery_prev)
        gallery_controls.addWidget(self.gallery_next)
        gallery_controls.addWidget(self.gallery_flip)
        gallery_controls.addWidget(self.gallery_caption_label, 1)
        self.detail_layout.addLayout(gallery_controls)

        meta = QVBoxLayout()
        meta.setSpacing(2)
        self.meta_categories = self._meta_row(meta, "分类")
        self.meta_loaders = self._meta_row(meta, "加载器")
        self.meta_versions = self._meta_row(meta, "游戏版本")
        self.meta_license = self._meta_row(meta, "许可证")
        self.detail_layout.addLayout(meta)

        self.body_excerpt = QLabel("")
        self.body_excerpt.setObjectName("Tiny")
        self.body_excerpt.setWordWrap(True)
        self.body_excerpt.setMaximumHeight(96)
        self.detail_layout.addWidget(self.body_excerpt)

        self.links_holder = QWidget()
        self.links_layout = QHBoxLayout(self.links_holder)
        self.links_layout.setContentsMargins(0, 0, 0, 0)
        self.links_layout.setSpacing(4)
        self.detail_layout.addWidget(self.links_holder)

        self.detail_note = QLabel("")
        self.detail_note.setObjectName("Tiny")
        self.detail_note.setWordWrap(True)
        self.detail_layout.addWidget(self.detail_note)
        self.detail_layout.addStretch(1)

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

    def _build_footer(self) -> None:
        self._folder_btn = self.add_button(
            "打开 mods 文件夹", "Secondary", self._open_folder
        )
        self.add_button("关闭", "Secondary", self.reject)

    def _apply_adaptive_height(self) -> None:
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry().height() if screen else 900
        height = max(320, min(470, int(available) - 360))
        self.results_panel.setFixedHeight(height)
        self.detail_panel.setFixedHeight(height)

    # ------------------------------------------------------------------
    # 版本上下文 / 下载目标

    def _installed_versions(self) -> list[tuple[str, str, str]]:
        items: list[tuple[str, str, str]] = []
        seen: set[tuple[str, str, str]] = set()
        for summary in self.main.game_version_summaries():
            base = summary.id
            loader = summary.loader
            if not loader:
                profile = self.main.loader_profile_for(summary.id)
                loader = profile.loader if profile else ""
            try:
                chain = resolve_version(
                    self.main.config.game_path, summary.id
                ).chain
                if chain:
                    base = chain[0]
            except Exception:
                pass
            key = (base, loader, summary.id)
            if key not in seen:
                seen.add(key)
                items.append(key)
        return items

    def _reload_bases(self) -> None:
        current = self.mc_combo.currentText().strip()
        bases: list[str] = []
        preferred = ""
        selected = next(
            (v for v in self.main.versions if v.id == self.main.selected_version),
            None,
        )
        if selected:
            try:
                chain = resolve_version(
                    self.main.config.game_path, selected.id
                ).chain
                if chain:
                    preferred = chain[0]
            except Exception:
                preferred = selected.id
        for base, _loader, _version_id in self._installed_versions():
            if base not in bases:
                bases.append(base)
        for version in COMMON_VERSIONS:
            if version not in bases:
                bases.append(version)

        self.mc_combo.blockSignals(True)
        self.mc_combo.clear()
        self.mc_combo.addItems(bases)
        target = preferred or current or (bases[0] if bases else "")
        if target:
            index = self.mc_combo.findText(target)
            if index >= 0:
                self.mc_combo.setCurrentIndex(index)
            else:
                self.mc_combo.setEditText(target)
        self.mc_combo.blockSignals(False)

        loader = (selected.loader if selected else "").lower()
        if selected and not loader:
            profile = self.main.loader_profile_for(selected.id)
            loader = (profile.loader if profile else "").lower()
        loader = {"neoforge": "neoforge", "forge": "forge", "fabric": "fabric"}.get(
            loader, "fabric"
        )
        self.loader_tabs.set_value(loader)
        self._update_context()

    def _update_context(self) -> None:
        mc = self.mc_combo.currentText().strip()
        kind = self._content_type
        if kind == "modpack":
            self._target_version_id = ""
            self.context_label.setText(
                f"整合包会以 Minecraft {mc} 为基础创建新的游戏版本，"
                "加载器与整合包文件由清单自动决定，不会覆盖现有版本。"
            )
            self.context_label.setStyleSheet(f"color: {colors()['green']};")
        elif kind in ("resourcepack", "shader"):
            folder = CONTENT_FOLDERS[kind]
            label = CONTENT_LABELS[kind]
            matches = [
                version_id
                for base, _name, version_id in self._installed_versions()
                if base == mc
            ]
            if matches:
                target = (
                    self.main.selected_version
                    if self.main.selected_version in matches
                    else matches[0]
                )
                self._target_version_id = target
                if self.main.config.version_isolation:
                    self.context_label.setText(
                        f"将下载到版本 {target} 的独立 {folder} 文件夹："
                        f"versions/{target}/{folder}"
                    )
                else:
                    self.context_label.setText(
                        f"本地已有 Minecraft {mc}。版本隔离已关闭，"
                        f"{label}会放入公共 {folder} 文件夹。"
                    )
                self.context_label.setStyleSheet(f"color: {colors()['green']};")
            else:
                self._target_version_id = ""
                self.context_label.setText(
                    f"本地还没有 Minecraft {mc} 的游戏版本，"
                    f"无法确定 {folder} 目录。请先在“下载”页面安装游戏版本。"
                )
                self.context_label.setStyleSheet(f"color: {colors()['orange']};")
        else:
            loader = self.loader_tabs.current()
            loader_name = LOADER_LABELS.get(loader, loader)
            matches = [
                version_id
                for base, name, version_id in self._installed_versions()
                if base == mc and name == loader_name
            ]
            if matches:
                target = (
                    self.main.selected_version
                    if self.main.selected_version in matches
                    else matches[0]
                )
                self._target_version_id = target
                if self.main.config.version_isolation:
                    self.context_label.setText(
                        f"将下载到版本 {target} 的独立 mods 文件夹：versions/{target}/mods"
                    )
                else:
                    self.context_label.setText(
                        f"本地已有 Minecraft {mc} · {loader_name}。"
                        "版本隔离已关闭，Mod 会放入公共 mods 文件夹。"
                    )
                self.context_label.setStyleSheet(f"color: {colors()['green']};")
            else:
                self._target_version_id = ""
                if self.main.config.version_isolation:
                    self.context_label.setText(
                        f"本地还没有 Minecraft {mc} 的 {loader_name} 版本，"
                        "无法确定独立 mods 目录。请先到“加载器”页面安装并绑定加载器。"
                    )
                else:
                    self.context_label.setText(
                        f"本地还没有 Minecraft {mc} 的 {loader_name} 版本。"
                        "可以先安装加载器，否则下载的 Mod 不会被加载。"
                    )
                self.context_label.setStyleSheet(f"color: {colors()['orange']};")
        if hasattr(self, "download_btn"):
            self._sync_download_button()

    def _on_context_changed(self) -> None:
        self._update_context()
        self._page = 0
        self._selected_project = ""
        self._project = None
        self._hit = None
        self._gallery = []
        self._hits = []
        self._nav_stack.clear()
        self._pending_project_id = ""
        self._pending_version_id = ""
        self.dep_bar.clear()
        self._render_results(loading=True)
        self._clear_detail()
        self._search(0)

    def _open_loader_dialog(self) -> None:
        dialog = LoaderDialog(self.main)
        dialog.exec()
        self._reload_bases()

    def _can_download(self) -> bool:
        if self._downloading():
            return False
        version = self.version_combo.currentData()
        if not isinstance(version, dict):
            return False
        if not primary_file(version):
            return False
        if self._content_type == "modpack":
            return True
        if self.main.config.version_isolation and not self._target_version_id:
            return False
        return True

    def _sync_download_button(self) -> None:
        """下载中始终允许点“取消下载”，不被切换项目等刷新流程重新禁用。"""
        if self._downloading():
            self.download_btn.setEnabled(True)
        else:
            self.download_btn.setEnabled(self._can_download())

    def _target_dir(self) -> Path:
        target = self._target_version_id or self.main.selected_version
        if self._content_type == "resourcepack":
            return self.main.config.resourcepacks_path(target)
        if self._content_type == "shader":
            return self.main.config.shaderpacks_path(target)
        return self.main.config.mods_path(target)

    def _open_folder(self) -> None:
        if self._content_type == "modpack":
            folder = "整合包缓存"
            path = APP_DIR / "cache" / "packs"
        else:
            folder = CONTENT_FOLDERS.get(self._content_type, "mods")
            path = self._target_dir()
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.main.toast(f"无法创建 {folder} 文件夹：{exc}", "error")
            return
        self.main.open_path(path)

    def _open_forum_search(self) -> None:
        self._open_forum_for(self.search.text().strip())

    def _open_forum_for_selected(self) -> None:
        title = ""
        if self._project:
            title = str(self._project.get("title") or "")
        if not title and self._hit:
            title = str(self._hit.get("title") or "")
        self._open_forum_for(title or self.search.text().strip())

    @staticmethod
    def _open_forum_for(query: str) -> None:
        QDesktopServices.openUrl(QUrl(forum_search_url(query)))

    # ------------------------------------------------------------------
    # 搜索与分页

    def _search(self, page: int = 0) -> None:
        if self._polling:
            return
        mc = self.mc_combo.currentText().strip()
        if not mc:
            self.status_label.setText("请先填写 MC 版本")
            return
        page = max(0, int(page))
        loader = self.loader_tabs.current() if self._content_type == "mod" else ""
        offset = page * self._page_size
        self._polling = True
        self.search_btn.setEnabled(False)
        self._update_pager()
        self.status_label.setText(f"正在搜索 {self._source_name()}…")
        self.status_label.setToolTip("")
        if page == 0:
            self._render_results(loading=True)

        worker = ModSearchWorker(
            self.search.text().strip(),
            loader,
            mc,
            offset,
            self._page_size,
            self,
            source=self._source,
            content_type=self._content_type,
        )
        self._search_worker = worker
        worker.ready.connect(
            lambda data, error, used, w=worker: self._search_ready(
                w, data, error, used
            )
        )
        worker.start()

    def _search_ready(
        self, worker: ModSearchWorker, data, error: str, offset: int
    ) -> None:
        if worker is not self._search_worker:
            return
        self._polling = False
        self.search_btn.setEnabled(True)
        current_mc = self.mc_combo.currentText().strip()
        expected_loader = (
            self.loader_tabs.current() if self._content_type == "mod" else ""
        )
        if (
            worker.loader != expected_loader
            or worker.game_version != current_mc
            or worker.source != self._source
            or worker.content_type != self._content_type
        ):
            QTimer.singleShot(60, lambda: self._search(0))
            return
        if error or data is None:
            self.status_label.setText("搜索失败")
            self.status_label.setToolTip(error or "未知错误")
            if not self._hits:
                self._render_results(message=f"搜索失败：{_short(error, 60)}")
            else:
                self._update_pager()
            return
        hits = [hit for hit in data.get("hits") or [] if isinstance(hit, dict)]
        self._hits = hits
        self._page = offset // self._page_size
        self._total = int(data.get("total_hits") or len(hits))
        self.status_label.setText("")
        self.status_label.setToolTip("")
        self._render_results()
        if hits:
            first = str(hits[0].get("project_id") or hits[0].get("slug") or "")
            if first:
                self._select_project(first, hits[0])
        else:
            self._selected_project = ""
            self._project = None
            self._hit = None
            self._clear_detail("这一页没有可显示的项目。")

    def _render_results(self, loading: bool = False, message: str = "") -> None:
        while self.results_layout.count() > 1:
            item = self.results_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()
        self._rows.clear()

        if loading:
            hint = QLabel(f"正在搜索 {self._source_name()}…")
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
            hint = QLabel(
                f"没有找到匹配的{self._content_label()}，换个关键词试试。\n"
                "也可以到苦力怕论坛搜索。"
            )
            hint.setObjectName("Muted")
            hint.setWordWrap(True)
            hint.setAlignment(Qt.AlignCenter)
            self.results_layout.insertWidget(0, hint)
        else:
            loader = self.loader_tabs.current()
            for hit in self._hits:
                row = ModResultRow(
                    hit,
                    loader,
                    source_label=self._source_name(),
                    content_type=self._content_type,
                )
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
                        self._load_image(
                            icon_url,
                            lambda img, r=row: r.set_icon(img),
                        )
        self._sync_results_height()
        self._update_pager()

    def _sync_results_height(self) -> None:
        self.results_layout.activate()
        self.results_holder.setMinimumHeight(self.results_layout.sizeHint().height())

    def _update_pager(self) -> None:
        pages = max(1, (self._total + self._page_size - 1) // self._page_size)
        current = min(self._page + 1, pages)
        self.page_label.setText(f"第 {current} / {pages} 页" if self._total else "第 1 页")
        self.result_count.setText(f"共 {self._total} 个" if self._total else "")
        self.prev_btn.setEnabled(not self._polling and self._page > 0)
        self.next_btn.setEnabled(
            not self._polling and (self._page + 1) * self._page_size < self._total
        )

    def _prev_page(self) -> None:
        if self._page > 0 and not self._polling:
            self._search(self._page - 1)

    def _next_page(self) -> None:
        if not self._polling and (self._page + 1) * self._page_size < self._total:
            self._search(self._page + 1)

    # ------------------------------------------------------------------
    # 项目详情

    def _select_project(
        self, project_id: str, hit: dict, *, keep_nav: bool = False
    ) -> None:
        if not project_id:
            return
        if not keep_nav:
            # 直接点击搜索结果时结束前置导航，返回按钮随之消失。
            self._nav_stack.clear()
            self._pending_project_id = ""
            self._pending_version_id = ""
        self._selected_project = project_id
        self._hit = hit
        self.detail_scroll.verticalScrollBar().setValue(0)
        for row in self._rows:
            row.set_selected(row.project_id == project_id)
        same = self._project is not None and str(self._project.get("id") or "") == project_id

        self.detail_title.setText(
            str(hit.get("title") or hit.get("slug") or self._content_label())
        )
        author = str(hit.get("author") or "")
        raw_categories = [str(item) for item in hit.get("categories") or [] if item]
        categories = "、".join(
            category_labels(raw_categories, 3) or raw_categories[:3]
        )
        meta_parts = []
        if author:
            meta_parts.append(f"作者：{author}")
        if categories:
            meta_parts.append(f"分类：{categories}")
        self.detail_author.setText(" · ".join(meta_parts) or self._source_name())
        intro = project_zh_intro(
            hit,
            content_type=self._content_type,
            loader=self.loader_tabs.current() if self._content_type == "mod" else "",
            game_version=self.mc_combo.currentText().strip(),
        )
        self.detail_desc.setText(intro)
        self.detail_desc.setToolTip(str(hit.get("description") or intro))
        self.downloads_badge.setText(f"{_human_number(hit.get('downloads'))} 下载")
        self.followers_badge.setText(
            "0 收藏" if self._source == "curseforge" else "0 关注"
        )
        self.updated_label.setText("")
        self._update_badges()
        self.version_combo.clear()
        self.version_combo.setEnabled(False)
        self.version_combo.addItem(
            f"正在获取 {self._source_name()} 文件…", ""
        )
        self.download_btn.setEnabled(False)
        self.dep_label.setText("")
        self.dep_label.setStyleSheet("")
        self.dep_bar.set_state(
            [], show_back=bool(self._nav_stack), projects=self._dep_projects
        )
        self.progress_box.setVisible(False)
        self.detail_note.setText("正在读取项目详情与预览图…")
        self.detail_note.setStyleSheet("")
        self._gallery = []
        self._gallery_index = 0
        self._gallery_url = ""
        self.gallery_count.setText("0 / 0")
        self.gallery_prev.setEnabled(False)
        self.gallery_next.setEnabled(False)
        self.gallery_flip.setEnabled(False)
        self.gallery_open_btn.setEnabled(False)
        self.gallery_preview.set_message("正在加载项目详情…")
        self.gallery_caption_label.setText("")
        self.body_excerpt.setText("")
        for label in (
            self.meta_categories,
            self.meta_loaders,
            self.meta_versions,
            self.meta_license,
        ):
            label.setText("—")
        self._clear_links()
        if same:
            self._project_ready(project_id, self._project, "")
        else:
            fallback_url = str(hit.get("project_url") or "")
            if not fallback_url and hit.get("slug"):
                path = MODRINTH_PATHS.get(self._content_type, "mod")
                fallback_url = (
                    f"https://modrinth.com/{path}/{hit.get('slug')}"
                    if self._source == "modrinth"
                    else ""
                )
            fallback_links: list[tuple[str, str]] = []
            if fallback_url:
                fallback_links.append((self._source_name(), fallback_url))
            fallback_title = str(hit.get("title") or "")
            if fallback_title:
                fallback_links.append(("论坛搜索", forum_search_url(fallback_title)))
            if fallback_links:
                self._set_links(fallback_links)
        icon_url = str(hit.get("icon_url") or "")
        self._set_detail_icon(icon_url)

        if not same:
            self._project = None
            worker = ModProjectWorker(
                project_id, self, source=self._source
            )
            self._project_worker = worker
            worker.ready.connect(self._project_ready)
            worker.start()

        versions_worker = ModVersionsWorker(
            project_id,
            self.loader_tabs.current() if self._content_type == "mod" else "",
            self.mc_combo.currentText().strip(),
            self,
            source=self._source,
            content_type=self._content_type,
        )
        self._versions_worker = versions_worker
        versions_worker.ready.connect(
            lambda data, error, w=versions_worker, pid=project_id: self._versions_ready(
                w, pid, data, error
            )
        )
        versions_worker.start()

    def _project_ready(self, project_id: str, data, error: str) -> None:
        if project_id != self._selected_project:
            return
        if error or not isinstance(data, dict):
            self.detail_note.setText(f"无法读取项目详情：{error}")
            self.detail_note.setStyleSheet(f"color: {colors()['orange']};")
            return
        self._project = data
        self.detail_note.setText("")
        title = str(data.get("title") or self.detail_title.text())
        self.detail_title.setText(title)
        merged = {**(self._hit or {}), **data}
        intro = project_zh_intro(
            merged,
            content_type=self._content_type,
            loader=self.loader_tabs.current() if self._content_type == "mod" else "",
            game_version=self.mc_combo.currentText().strip(),
        )
        self.detail_desc.setText(intro)
        self.detail_desc.setToolTip(
            str(data.get("description") or (self._hit or {}).get("description") or intro)
        )
        author = str((self._hit or {}).get("author") or "")
        categories = [str(c) for c in data.get("categories") or [] if c]
        meta_parts = []
        if author:
            meta_parts.append(f"作者：{author}")
        if categories:
            meta_parts.append(
                f"分类：{'、'.join(category_labels(categories, 3) or categories[:3])}"
            )
        if meta_parts:
            self.detail_author.setText(" · ".join(meta_parts))
        downloads = data.get("downloads")
        followers = data.get("followers")
        self.downloads_badge.setText(f"{_human_number(downloads)} 下载")
        self.followers_badge.setText(
            f"{_human_number(followers)} 收藏"
            if self._source == "curseforge"
            else f"{_human_number(followers)} 关注"
        )
        updated = str(data.get("updated") or "")[:10]
        self.updated_label.setText(f"更新于 {updated}" if updated else "")
        self._update_badges()

        self.meta_categories.setText(
            " / ".join(category_labels(categories, 5) or categories[:5]) or "—"
        )
        loaders = [str(item) for item in data.get("loaders") or [] if item]
        self.meta_loaders.setText(" / ".join(loaders[:4]) or "—")
        versions = [str(item) for item in data.get("game_versions") or [] if item]
        version_text = "、".join(versions[:8])
        if len(versions) > 8:
            version_text += f" 等 {len(versions)} 个"
        self.meta_versions.setText(version_text or "—")
        license_data = data.get("license")
        if isinstance(license_data, dict):
            license_text = str(license_data.get("name") or license_data.get("id") or "")
        else:
            license_text = str(license_data or "")
        self.meta_license.setText(license_text or "—")
        excerpt = _plain_excerpt(str(data.get("body") or ""), 280)
        self.body_excerpt.setText(f"原文简介：{excerpt}" if excerpt else "")
        self.body_excerpt.setVisible(bool(excerpt))

        icon_url = str(data.get("icon_url") or (self._hit or {}).get("icon_url") or "")
        self._set_detail_icon(icon_url)
        self._set_links(self._project_links(data))
        self._set_gallery(data.get("gallery") or [])

    def _update_badges(self) -> None:
        repolish(self.downloads_badge)
        repolish(self.followers_badge)

    def _set_detail_icon(self, url: str) -> None:
        if not url:
            self.detail_icon.setPixmap(icons.pixmap("cube", colors()["purple"], 28))
            return
        image = _IMAGE_CACHE.get(url)
        if image is not None:
            self.detail_icon.setPixmap(_cover_pixmap(image, 54, 11))
            return
        self.detail_icon.setPixmap(icons.pixmap("cube", colors()["purple"], 28))
        self._load_image(
            url,
            lambda image, expected=url: self._set_detail_icon_image(expected, image),
        )

    def _set_detail_icon_image(self, url: str, image: QImage | None) -> None:
        if url != str((self._project or {}).get("icon_url") or (self._hit or {}).get("icon_url") or ""):
            if url not in (
                str((self._hit or {}).get("icon_url") or ""),
                str((self._project or {}).get("icon_url") or ""),
            ):
                return
        if image is not None and not image.isNull():
            self.detail_icon.setPixmap(_cover_pixmap(image, 54, 11))

    def _project_links(self, data: dict) -> list[tuple[str, str]]:
        links: list[tuple[str, str]] = []
        project_url = str(data.get("project_url") or "").strip()
        if project_url:
            links.append(
                (
                    str(data.get("source_label") or self._source_name()),
                    project_url,
                )
            )
        else:
            slug = str(data.get("slug") or "")
            if slug:
                path = MODRINTH_PATHS.get(self._content_type, "mod")
                links.append(("Modrinth", f"https://modrinth.com/{path}/{slug}"))
        for key, label in (
            ("source_url", "源码"),
            ("issues_url", "问题"),
            ("wiki_url", "Wiki"),
            ("discord_url", "Discord"),
        ):
            value = str(data.get(key) or "").strip()
            if value:
                links.append((label, value))
        title = str(data.get("title") or (self._hit or {}).get("title") or "")
        links.append(("论坛搜索", forum_search_url(title)))
        return links[:5]

    def _clear_links(self) -> None:
        while self.links_layout.count():
            item = self.links_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def _set_links(self, links: list[tuple[str, str]]) -> None:
        self._clear_links()
        for label, url in links:
            button = QPushButton(label)
            button.setObjectName("Ghost")
            button.setCursor(Qt.PointingHandCursor)
            button.setIcon(icons.icon("external", colors()["text2"], 12))
            button.setStyleSheet("font-size: 11px; padding: 4px 7px;")
            button.clicked.connect(lambda _=False, target=url: QDesktopServices.openUrl(QUrl(target)))
            self.links_layout.addWidget(button)
        self.links_layout.addStretch(1)

    def _clear_detail(self, message: str = "") -> None:
        label = self._content_label()
        self.detail_scroll.verticalScrollBar().setValue(0)
        self.detail_title.setText(f"选择一个{label}查看详细信息")
        self.detail_author.setText("从左侧搜索结果中选择项目。")
        self.detail_desc.setText(message)
        self.detail_desc.setToolTip("")
        self.downloads_badge.setText("0 下载")
        self.followers_badge.setText(
            "0 收藏" if self._source == "curseforge" else "0 关注"
        )
        self.updated_label.setText("")
        self._update_badges()
        self.version_combo.clear()
        self.version_combo.setEnabled(False)
        self.version_combo.addItem(f"请选择一个{label}", "")
        self._sync_download_button()
        self.dep_label.setText("")
        self._nav_stack.clear()
        self._pending_project_id = ""
        self._pending_version_id = ""
        self.dep_bar.clear()
        self.progress_box.setVisible(False)
        self.detail_note.setText("")
        self._gallery = []
        self._gallery_index = 0
        self._gallery_url = ""
        self.gallery_count.setText("0 / 0")
        self.gallery_prev.setEnabled(False)
        self.gallery_next.setEnabled(False)
        self.gallery_flip.setEnabled(False)
        self.gallery_open_btn.setEnabled(False)
        self.gallery_preview.set_message(f"选择{label}后显示预览图")
        self.gallery_caption_label.setText("")
        self.body_excerpt.setText("")
        for label in (
            self.meta_categories,
            self.meta_loaders,
            self.meta_versions,
            self.meta_license,
        ):
            label.setText("—")
        self._clear_links()
        self.detail_icon.setPixmap(icons.pixmap("cube", colors()["purple"], 28))

    def _set_gallery(self, gallery: list) -> None:
        self._gallery = [
            item if isinstance(item, dict) else {"url": str(item)}
            for item in gallery
            if (isinstance(item, dict) and item.get("url")) or isinstance(item, str)
        ]
        total = len(self._gallery)
        self.gallery_count.setText(f"1 / {total}" if total else "0 / 0")
        self.gallery_prev.setEnabled(False)
        self.gallery_next.setEnabled(total > 1)
        self.gallery_flip.setEnabled(total > 1)
        self.gallery_open_btn.setEnabled(bool(total))
        if not total:
            self.gallery_preview.set_message("该项目暂未提供预览图")
            self.gallery_caption_label.setText("可以前往苦力怕论坛查找玩家截图")
            return
        self._show_gallery(0)

    def _show_gallery(self, index: int) -> None:
        if not self._gallery:
            return
        index = max(0, min(len(self._gallery) - 1, int(index)))
        self._gallery_index = index
        item = self._gallery[index]
        url = str(item.get("url") or "")
        caption = str(item.get("title") or item.get("description") or "").strip()
        self._gallery_url = url
        self._gallery_caption = caption
        self.gallery_count.setText(f"{index + 1} / {len(self._gallery)}")
        self.gallery_prev.setEnabled(index > 0)
        self.gallery_next.setEnabled(index < len(self._gallery) - 1)
        self.gallery_flip.setEnabled(len(self._gallery) > 1)
        self.gallery_open_btn.setEnabled(bool(url))
        self.gallery_preview.set_message("正在加载预览图…")
        self.gallery_caption_label.setText(_short(caption, 42) if caption else "")
        self.gallery_caption_label.setToolTip(caption)
        if not url:
            self.gallery_preview.set_message("预览图地址无效")
            return
        cached = _IMAGE_CACHE.get(url)
        if cached is not None:
            self.gallery_preview.set_image(cached)
        else:
            self._load_image(
                url,
                lambda image, expected=url: self._gallery_image_ready(expected, image),
            )
        if index + 1 < len(self._gallery):
            next_url = str(self._gallery[index + 1].get("url") or "")
            if next_url and next_url not in _IMAGE_CACHE:
                self._load_image(next_url, lambda *_: None)

    def _flip_gallery(self) -> None:
        if len(self._gallery) <= 1:
            return
        self._show_gallery((self._gallery_index + 1) % len(self._gallery))

    def _gallery_image_ready(self, url: str, image: QImage | None) -> None:
        if url != self._gallery_url:
            return
        if image is None or image.isNull():
            self.gallery_preview.set_message("预览图加载失败")
        else:
            self.gallery_preview.set_image(image)

    def _open_gallery_current(self) -> None:
        if not self._gallery:
            return
        item = self._gallery[self._gallery_index]
        url = str(item.get("full_url") or self._gallery_url or "")
        if url:
            QDesktopServices.openUrl(QUrl(url))

    # ------------------------------------------------------------------
    # 异步图标

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

    # ------------------------------------------------------------------
    # 版本文件与下载

    def _versions_ready(
        self, worker: ModVersionsWorker, project_id: str, data, error: str
    ) -> None:
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
                self.version_combo.addItem("没有适配当前版本的文件", "")
                if self._content_type == "mod":
                    detail = (
                        "该 Mod 没有同时适配所选 Minecraft 版本和加载器的文件，"
                        "可以尝试切换加载器或游戏版本。"
                    )
                else:
                    detail = (
                        f"该{self._content_label()}没有适配所选 Minecraft 版本的文件，"
                        "可以尝试切换游戏版本。"
                    )
                self.dep_label.setText(detail)
                self.dep_label.setStyleSheet(f"color: {colors()['orange']};")
                self._sync_download_button()
                return

            target_index = 0
            for index, version in enumerate(data):
                file = primary_file(version) or {}
                version_number = str(
                    version.get("version_number") or version.get("name") or ""
                )
                version_type = str(version.get("version_type") or "")
                filename = str(file.get("filename") or "")
                suffix = f" · {version_type}" if version_type else ""
                if not file.get("url"):
                    suffix += " · 仅网页下载"
                self.version_combo.addItem(
                    f"{_short(version_number, 30)}{suffix}", version
                )
                tip = f"{version_number}\n{filename}" if filename else version_number
                if not file.get("url"):
                    tip += "\n该文件未开放第三方下载，请打开 CurseForge 页面手动下载。"
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
        raw_file = next(
            (
                item
                for item in version.get("files") or []
                if isinstance(item, dict)
            ),
            {},
        )
        file = primary_file(version) or raw_file
        size = int(file.get("size") or 0)
        downloadable = bool(file.get("url"))
        parts = []
        if size:
            parts.append(f"{size / 1024 / 1024:.1f} MB")
        if not downloadable:
            parts.append(
                "该文件未开放第三方下载，请点击上方 CurseForge 页面手动下载"
            )
        if self._content_type != "mod":
            visible = []
            deps = []
            if downloadable:
                if self._content_type == "modpack":
                    parts.append("下载后会打开导入窗口，自动创建新的游戏版本")
                else:
                    parts.append(
                        f"下载到 {CONTENT_FOLDERS.get(self._content_type, 'mods')} 文件夹"
                    )
        elif visible:
            parts.append(f"需要 {len(visible)} 个前置 Mod，点击下方按钮直接下载")
        elif deps:
            parts.append(f"声明了 {len(deps)} 个必需前置，请确认已安装")
        elif downloadable:
            parts.append("没有声明必需前置")
        self.dep_label.setText(" · ".join(parts))
        self.dep_label.setStyleSheet(
            f"color: {colors()['orange']};"
            if deps or not downloadable
            else ""
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
        worker = ModProjectsWorker(ids, self, source=self._source)
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

    def _dependency_hit(self, project_id: str, project: dict | None) -> dict:
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
            "project_url": str(data.get("project_url") or ""),
            "source": str(data.get("source") or self._source),
            "source_label": str(
                data.get("source_label") or self._source_name()
            ),
        }

    def _open_dependency(self, project_id: str, version_id: str) -> None:
        project_id = str(project_id or "")
        if not project_id:
            return
        if self._downloading():
            self.main.toast("请先等待当前下载结束，再查看前置 Mod", "info")
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
        if self._downloading():
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

    def _download_button_text(self) -> str:
        if self._content_type == "modpack":
            return "下载并导入整合包"
        folder = CONTENT_FOLDERS.get(self._content_type, "mods")
        return f"下载到 {folder}"

    def _set_download_button(self, downloading: bool) -> None:
        if downloading:
            self.download_btn.setObjectName("Danger")
            self.download_btn.setText("取消下载")
            self.download_btn.setIcon(icons.icon("stop", colors()["red"], 15))
            self.download_btn.setEnabled(True)
        else:
            self.download_btn.setObjectName("Primary")
            self.download_btn.setText(self._download_button_text())
            self.download_btn.setIcon(icons.icon("download", "#FFFFFF", 15))
            self.download_btn.setEnabled(self._can_download())
        repolish(self.download_btn)
        self.adjustSize()

    def _bind_download_center(self) -> None:
        if self._download_bound:
            return
        center = self.main.download_center
        center.task_progress.connect(self._on_download_progress)
        center.task_finished.connect(self._on_download_finished)
        self._download_bound = True

    def _download_metadata(self, version: dict | None = None) -> dict:
        project = dict(self._project or {})
        hit = dict(self._hit or {})
        merged = {**hit, **{key: value for key, value in project.items() if value}}
        game_version = self.mc_combo.currentText().strip()
        loader = self.loader_tabs.current() if self._content_type == "mod" else ""
        current_version = version if isinstance(version, dict) else {}
        asset = primary_file(current_version) or {}
        hashes = asset.get("hashes") if isinstance(asset.get("hashes"), dict) else {}
        return {
            "title": str(merged.get("title") or self.detail_title.text()),
            "project_id": str(
                merged.get("project_id")
                or merged.get("id")
                or self._selected_project
            ),
            "slug": str(merged.get("slug") or ""),
            "source": self._source,
            "source_label": self._source_name(),
            "content_type": self._content_type,
            "icon_url": str(merged.get("icon_url") or ""),
            "author": str(merged.get("author") or ""),
            "categories": merged.get("categories") or [],
            "loaders": merged.get("loaders") or ([loader] if loader else []),
            "game_versions": merged.get("game_versions")
            or ([game_version] if game_version else []),
            "downloads": merged.get("downloads") or 0,
            "description": str(merged.get("description") or ""),
            "description_zh": project_zh_intro(
                merged,
                content_type=self._content_type,
                loader=loader,
                game_version=game_version,
            ),
            "project_url": str(merged.get("project_url") or ""),
            "game_version": game_version,
            "loader": loader,
            "version_id": str(current_version.get("id") or ""),
            "file_id": str(current_version.get("id") or ""),
            "file_name": str(asset.get("filename") or ""),
            "sha1": str(hashes.get("sha1") or ""),
        }

    def _download(self) -> None:
        if self._downloading():
            task = self._download_task
            if task is not None:
                self.main.download_center.cancel(task.id)
            self.download_btn.setText("正在取消…")
            self.download_btn.setEnabled(False)
            return
        version = self.version_combo.currentData()
        if not isinstance(version, dict):
            return
        if not primary_file(version):
            self.main.toast(
                "该文件未开放第三方下载，请打开 CurseForge 页面手动下载",
                "error",
            )
            return
        pack_mode = self._content_type == "modpack"
        if pack_mode:
            target_dir = APP_DIR / "cache" / "packs"
            progress_text = "准备下载整合包…"
        else:
            if self.main.config.version_isolation and not self._target_version_id:
                self.main.toast(
                    f"请先准备一个 Minecraft {self.mc_combo.currentText().strip()} "
                    "游戏版本，再下载",
                    "error",
                )
                return
            target_dir = self._target_dir()
            progress_text = (
                f"准备下载到 {target_dir.parent.name}/{target_dir.name}…"
            )
        self.progress.setValue(0)
        self.progress_text.setText(progress_text)
        self.progress_box.setVisible(True)
        self.adjustSize()
        metadata = (
            self._download_metadata(version)
            if self._content_type == "mod"
            else None
        )
        title = str(
            (metadata or {}).get("title")
            or self.detail_title.text()
            or self._content_label()
        )
        self._bind_download_center()
        task = self.main.download_center.start_mod_download(
            version,
            target_dir,
            threads=self.main.config.download_threads,
            title=title,
            content_type=self._content_type,
            source=self._source,
            metadata=metadata,
            pack_mode=pack_mode,
        )
        self._download_task = task
        self._pack_download_dir = target_dir if pack_mode else None
        self._set_download_button(True)
        self.progress_text.setText(
            "下载中，关闭本窗口后会转到顶栏继续下载"
            if not pack_mode
            else "下载中，可以关闭本窗口，进度会显示在顶栏"
        )

    def _on_download_progress(self, task_id: int, percent: int, message: str) -> None:
        task = self._download_task
        if task is None or task.id != int(task_id):
            return
        self.progress.setValue(max(0, min(100, percent)))
        self.progress_text.setText(message)

    def _on_download_finished(self, task: DownloadTask) -> None:
        current = self._download_task
        if current is None or current.id != task.id:
            return
        self._download_task = task
        self._pack_download_dir = None
        self._set_download_button(False)
        filename = task.filename
        if task.status == TASK_SUCCESS:
            self.progress.setValue(100)
            self.progress_text.setText(f"已保存 {filename}")
            self.main.toast(f"已下载 {filename}", "success")
            if task.pack_mode:
                self._open_pack_import(task.target / filename)
                return
            if hasattr(self.main, "mods_page"):
                self.main.mods_page.refresh_mods()
        elif task.status == TASK_CANCELLED:
            self.progress_text.setText("下载已取消")
            self.main.toast("已取消下载，临时文件已清理", "info")
        else:
            self.progress_text.setText(task.error or task.message)
            self.main.toast(f"下载失败：{task.error or task.message}", "error")

    def _open_pack_import(self, path: Path) -> None:
        if not path.is_file():
            self.main.toast("整合包文件没有保存成功，请重试", "error")
            return
        from .pack_dialog import PackImportDialog

        self.progress_text.setText("整合包下载完成，正在打开导入窗口…")
        dialog = PackImportDialog(self.main, initial_path=path)
        dialog.exec()
        if dialog.imported_version:
            self.main.refresh_versions()
            self.main.select_version(dialog.imported_version)
