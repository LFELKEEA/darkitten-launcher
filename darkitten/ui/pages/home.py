from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...accounts import offline_uuid
from ...versions import (
    LOADER_COLORS,
    VersionSummary,
    check_installation,
    resolve_version,
)
from .. import icons
from ..dialogs import TextInputDialog
from ..widgets import (
    Avatar,
    Card,
    HeroArt,
    SegmentedControl,
    VersionRow,
    badge,
    colors,
    icon_button,
    repolish,
    transparent_children,
)


class LoaderOverviewRow(QFrame):
    clicked = Signal(str)

    def __init__(self, profile, bound_count: int, parent=None) -> None:
        super().__init__(parent)
        self.profile = profile
        self.setObjectName("SelectRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(60)
        self.setToolTip("打开加载器工坊，分配游戏版本或升级加载器")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(11)
        accent = LOADER_COLORS.get(profile.loader or "", colors()["orange"])
        if profile.error:
            accent = colors()["red"]
        mark = QLabel()
        mark.setFixedSize(34, 34)
        mark.setAlignment(Qt.AlignCenter)
        mark.setPixmap(icons.pixmap("package", accent, 22))

        info = QVBoxLayout()
        info.setSpacing(2)
        top = QHBoxLayout()
        top.setSpacing(6)
        name = QLabel(profile.id)
        name.setStyleSheet("font-weight: 700; font-size: 13px;")
        top.addWidget(name)
        top.addWidget(badge(profile.loader or "加载器", "purple"))
        if profile.loader_version:
            top.addWidget(badge(f"v{profile.loader_version}", "blue"))
        if profile.error:
            top.addWidget(badge("损坏", "red"))
        top.addStretch(1)
        info.addLayout(top)
        bits = [f"基础版本 {profile.base_id or '未知'}"]
        if profile.java_major:
            bits.append(f"Java {profile.java_major}+")
        bits.append(f"{bound_count} 个版本已绑定")
        meta = QLabel(" · ".join(bits))
        meta.setObjectName("Tiny")
        info.addWidget(meta)

        layout.addWidget(mark)
        layout.addLayout(info, 1)
        count_badge = badge(
            f"{bound_count} 个绑定" if bound_count else "未分配",
            "green" if bound_count else "gray",
        )
        layout.addWidget(count_badge)
        chevron = QLabel()
        chevron.setPixmap(icons.pixmap("chevron-right", colors()["muted"], 15))
        layout.addWidget(chevron)
        transparent_children(self)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.profile.id)
        super().mouseReleaseEvent(event)


class QuickAction(QFrame):
    clicked = Signal()

    def __init__(
        self, icon_name: str, title: str, subtitle: str, accent: str, parent=None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SelectRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(66)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 9, 8, 9)
        layout.setSpacing(8)

        icon_box = QLabel()
        icon_box.setFixedSize(30, 30)
        icon_box.setAlignment(Qt.AlignCenter)
        icon_box.setPixmap(icons.pixmap(icon_name, accent, 18))
        color = QColor(accent)
        icon_box.setStyleSheet(
            f"background: rgba({color.red()},{color.green()},{color.blue()},26);"
            "border-radius: 8px;"
        )

        texts = QVBoxLayout()
        texts.setSpacing(1)
        name = QLabel(title)
        name.setStyleSheet("font-weight: 700; font-size: 12px;")
        sub = QLabel(subtitle)
        sub.setObjectName("Tiny")
        sub.setToolTip(subtitle)
        for label in (name, sub):
            label.setMinimumWidth(0)
            label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        texts.addWidget(name)
        texts.addWidget(sub)

        layout.addWidget(icon_box)
        layout.addLayout(texts, 1)
        transparent_children(self)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class HomePage(QWidget):
    def __init__(self, main, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.main = main
        self._selected = ""
        self._closing = False
        self._view = "games"
        self._game_versions: list[VersionSummary] = []
        self._version_rows: dict[str, VersionRow] = {}
        self._build()
        self.main.versions_changed.connect(self.refresh_versions)
        self.main.install_finished.connect(lambda *_: self._refresh_status())
        self.refresh_versions()
        self._refresh_account_card()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        container = QWidget()
        container.setObjectName("HomeContainer")
        scroll.setWidget(container)
        outer.addWidget(scroll)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(16)

        layout.addWidget(self._build_hero())

        bottom = QHBoxLayout()
        bottom.setSpacing(16)
        bottom.addWidget(self._build_versions_card(), 2)

        right = QVBoxLayout()
        right.setSpacing(16)
        right.addWidget(self._build_account_card())
        right.addWidget(self._build_quick_card(), 1)
        wrapper = QWidget()
        wrapper.setLayout(right)
        wrapper.setFixedWidth(340)
        bottom.addWidget(wrapper, 1)
        layout.addLayout(bottom, 1)

    def _build_hero(self) -> QWidget:
        hero = QFrame()
        hero.setObjectName("Hero")
        hero.setAttribute(Qt.WA_StyledBackground, True)
        hero.setFixedHeight(218)
        layout = QHBoxLayout(hero)
        layout.setContentsMargins(30, 24, 12, 24)
        layout.setSpacing(8)

        left = QVBoxLayout()
        left.setSpacing(8)
        pill = QLabel("DARKITTEN · MINECRAFT JAVA")
        pill.setObjectName("HeroPill")
        pill.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        left.addWidget(pill, 0, Qt.AlignLeft)

        self.hero_title = QLabel("今天也要去捣乱吗？")
        self.hero_title.setObjectName("HeroTitle")
        left.addWidget(self.hero_title)

        self.hero_sub = QLabel("正在读取本地版本…")
        self.hero_sub.setObjectName("HeroSub")
        left.addWidget(self.hero_sub)
        left.addStretch(1)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.launch_btn = QPushButton("启动游戏")
        self.launch_btn.setObjectName("Launch")
        self.launch_btn.setFixedSize(166, 46)
        self.launch_btn.setCursor(Qt.PointingHandCursor)
        self.launch_btn.setIcon(icons.icon("play", "#3A0F1C", 19))
        self.launch_btn.setIconSize(QSize(19, 19))
        self.launch_btn.clicked.connect(self._on_launch_click)

        self.hero_status = QLabel("● 检查中")
        self.hero_status.setStyleSheet(
            "color: rgba(255,255,255,190); background: rgba(255,255,255,26);"
            "border-radius: 9px; padding: 5px 10px; font-size: 11px; font-weight: 600;"
        )
        actions.addWidget(self.launch_btn)
        actions.addWidget(self.hero_status, 0, Qt.AlignVCenter)
        actions.addStretch(1)
        left.addLayout(actions)

        layout.addLayout(left, 1)
        self.hero_art = HeroArt()
        self.hero_art.setFixedWidth(410)
        layout.addWidget(self.hero_art, 0)
        return hero

    def _build_versions_card(self) -> QWidget:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel("猫窝档案")
        title.setObjectName("H2")
        self.version_count = badge("0 个版本", "blue")
        self.version_tabs = SegmentedControl()
        self.version_tabs.add_item("游戏版本", "games")
        self.version_tabs.add_item("加载器", "loaders")
        self.version_tabs.changed.connect(self._on_view_changed)
        header.addWidget(title)
        header.addWidget(self.version_count)
        header.addSpacing(4)
        header.addWidget(self.version_tabs)
        header.addStretch(1)

        self.import_btn = icon_button(
            "import", "导入本地文件（版本包 / 版本文件夹 / Mod / 材质 / 光影 / 整合包）"
        )
        self.import_btn.clicked.connect(lambda: self.main.open_local_import())
        self.loader_install_btn = icon_button("download", "安装一个加载器")
        self.loader_install_btn.clicked.connect(self.main.open_loader_dialog)
        self.loader_install_btn.setVisible(False)
        self.migrate_btn = icon_button("migrate", "迁移版本 / 升级加载器")
        self.migrate_btn.clicked.connect(
            lambda: self.main.open_migration_dialog(self._selected)
        )
        self.refresh_btn = icon_button("refresh", "刷新列表")
        self.refresh_btn.clicked.connect(self.main.refresh_versions)
        folder_btn = icon_button("folder", "打开游戏目录")
        folder_btn.clicked.connect(self.main.open_game_dir)
        header.addWidget(self.import_btn)
        header.addWidget(self.loader_install_btn)
        header.addWidget(self.migrate_btn)
        header.addWidget(self.refresh_btn)
        header.addWidget(folder_btn)
        layout.addLayout(header)

        self.tip_label = QLabel(
            "双击启动 · 齿轮选择原版/加载器 · 交换箭头迁移存档/Mod · "
            "纸箱按钮打包 · 垃圾桶删除版本 · 文件可以直接拖进窗口"
        )
        self.tip_label.setObjectName("Tiny")
        self.tip_label.setWordWrap(True)
        layout.addWidget(self.tip_label)

        self.version_scroll = QScrollArea()
        self.version_scroll.setWidgetResizable(True)
        self.version_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.version_holder = QWidget()
        self.version_layout = QVBoxLayout(self.version_holder)
        self.version_layout.setContentsMargins(0, 0, 4, 0)
        self.version_layout.setSpacing(4)
        self.version_layout.addStretch(1)
        self.version_scroll.setWidget(self.version_holder)
        layout.addWidget(self.version_scroll, 1)

        self.empty_box = QFrame()
        self.empty_box.setObjectName("SoftCard")
        self.empty_box.setAttribute(Qt.WA_StyledBackground, True)
        empty_layout = QVBoxLayout(self.empty_box)
        empty_layout.setContentsMargins(20, 18, 20, 18)
        empty_layout.setSpacing(8)
        self.empty_title = QLabel("还没有可以住的猫窝")
        self.empty_title.setStyleSheet("font-weight: 700;")
        self.empty_sub = QLabel("")
        self.empty_sub.setObjectName("Tiny")
        self.empty_sub.setWordWrap(True)
        empty_actions = QHBoxLayout()
        empty_actions.setSpacing(8)
        self.empty_download_btn = QPushButton("前往游戏版本")
        self.empty_download_btn.setObjectName("Soft")
        self.empty_download_btn.setCursor(Qt.PointingHandCursor)
        self.empty_download_btn.clicked.connect(lambda: self.main.switch_page("download"))
        self.empty_import_btn = QPushButton("导入版本包")
        self.empty_import_btn.setObjectName("Soft")
        self.empty_import_btn.setCursor(Qt.PointingHandCursor)
        self.empty_import_btn.setIcon(icons.icon("package", colors()["accent"], 15))
        self.empty_import_btn.clicked.connect(lambda: self.main.open_local_import())
        self.empty_loader_btn = QPushButton("安装加载器")
        self.empty_loader_btn.setObjectName("Soft")
        self.empty_loader_btn.setCursor(Qt.PointingHandCursor)
        self.empty_loader_btn.setIcon(icons.icon("download", colors()["accent"], 15))
        self.empty_loader_btn.clicked.connect(self.main.open_loader_dialog)
        self.empty_loader_btn.setVisible(False)
        empty_actions.addWidget(self.empty_download_btn)
        empty_actions.addWidget(self.empty_import_btn)
        empty_actions.addWidget(self.empty_loader_btn)
        empty_actions.addStretch(1)
        empty_layout.addWidget(self.empty_title)
        empty_layout.addWidget(self.empty_sub)
        empty_layout.addLayout(empty_actions)
        self.empty_box.setVisible(False)
        layout.addWidget(self.empty_box)
        return card

    def _build_account_card(self) -> QWidget:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 15, 18, 15)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("现在是谁在捣乱")
        title.setObjectName("H2")
        header.addWidget(title)
        header.addStretch(1)
        self.account_badge = badge("离线模式", "gray")
        header.addWidget(self.account_badge)
        layout.addLayout(header)

        row = QHBoxLayout()
        row.setSpacing(11)
        self.avatar = Avatar(48)
        row.addWidget(self.avatar)

        info = QVBoxLayout()
        info.setSpacing(2)
        self.account_name = QLabel("Steve")
        self.account_name.setStyleSheet("font-size: 16px; font-weight: 700;")
        self.account_uuid = QLabel("")
        self.account_uuid.setObjectName("Tiny")
        info.addWidget(self.account_name)
        info.addWidget(self.account_uuid)
        row.addLayout(info, 1)

        self.account_btn = icon_button("edit", "修改玩家名称")
        self.account_btn.clicked.connect(self.edit_account)
        row.addWidget(self.account_btn)
        layout.addLayout(row)

        self.account_note = QLabel(
            "离线账户仅用于本地游戏，无法加入正版验证服务器。"
        )
        self.account_note.setObjectName("Tiny")
        self.account_note.setWordWrap(True)
        layout.addWidget(self.account_note)
        return card

    def _build_quick_card(self) -> QWidget:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 15, 18, 15)
        layout.setSpacing(9)
        title = QLabel("猫爪快捷方式")
        title.setObjectName("H2")
        layout.addWidget(title)

        grid = QHBoxLayout()
        grid.setSpacing(8)
        col1 = QVBoxLayout()
        col1.setSpacing(8)
        col2 = QVBoxLayout()
        col2.setSpacing(8)

        actions = [
            ("paw", "桌宠 LFELKEEA", "下载并召唤桌面猫猫", "#C98A4B", self.main.open_pet_dialog),
            ("folder", "游戏目录", "打开游戏文件夹", "#E23A5E", self.main.open_game_dir),
            ("package", "导入本地文件", "版本与 Mod", "#E8683F", self.main.open_local_import),
            ("migrate", "版本迁移", "搬走存档与 Mod", "#3FB3AA", lambda: self.main.open_migration_dialog(self._selected)),
            ("users", "好友与联机", "邀请卡直达房间", "#3FB3AA", self.main.open_social_dialog),
            ("cube", "模组管理", "管理已装 Mod", "#B15BE0", lambda: self.main.switch_page("mods")),
            ("download", "补全文件", "修复缺失依赖", "#C98A4B", self.main.complete_selected),
            ("bolt", "游戏优化", "内存、画面与优化 Mod", "#E23A5E", lambda: self.main.open_optimize_dialog(self._selected)),
            ("download", "下载中心", "查看后台任务", "#3FB3AA", self.main.open_download_tasks),
        ]
        for index, (icon_name, title_text, sub, accent, callback) in enumerate(actions):
            action = QuickAction(icon_name, title_text, sub, accent)
            action.clicked.connect(callback)
            (col1 if index % 2 == 0 else col2).addWidget(action)

        grid.addLayout(col1, 1)
        grid.addLayout(col2, 1)
        layout.addLayout(grid, 1)
        return card

    # ------------------------------------------------------------------
    def refresh_versions(self) -> None:
        versions: list[VersionSummary] = list(self.main.game_version_summaries())
        self._game_versions = versions

        had_selection = self._selected
        if versions:
            valid_ids = {v.id for v in versions}
            target = had_selection if had_selection in valid_ids else ""
            if not target and self.main.config.last_version in valid_ids:
                target = self.main.config.last_version
            if not target:
                target = versions[0].id
            self._selected = target
            self.main.selected_version = target
            self.main.config.last_version = target
            self.main.config.save()
        else:
            self._selected = ""
            self.main.selected_version = ""
            self.main.selection_changed.emit("")
            self.hero_sub.setText("还没有版本可玩，先去游戏版本仓库叼一个回来吧。")
            self.hero_status.setText("● 猫窝是空的")
            self.hero_status.setStyleSheet(
                "color: rgba(255,220,180,220); background: rgba(255,255,255,26);"
                "border-radius: 9px; padding: 5px 10px; font-size: 11px; font-weight: 600;"
            )
            self.launch_btn.setEnabled(False)

        self._render_current_view()
        if self._selected and self._view == "loaders":
            self._update_hero()

    def _on_view_changed(self, value: str) -> None:
        self._view = value if value in ("games", "loaders") else "games"
        self._render_current_view()

    def _render_current_view(self) -> None:
        while self.version_layout.count() > 1:
            item = self.version_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self._version_rows.clear()
        if self._view == "loaders":
            self._render_loaders()
        else:
            self._render_games()

    def _render_games(self) -> None:
        versions = self._game_versions
        self.version_count.setText(f"{len(versions)} 个版本")
        self.import_btn.setVisible(True)
        self.loader_install_btn.setVisible(False)
        self.tip_label.setText(
            "双击启动 · 齿轮选择原版/加载器 · 交换箭头迁移存档/Mod · "
            "纸箱按钮打包 · 垃圾桶删除版本 · 文件可以直接拖进窗口"
        )
        if not versions:
            self._show_empty(
                "还没有可以住的猫窝",
                "前往“游戏版本”页面安装原版游戏，或导入版本 ZIP / 版本文件夹 / "
                "整合包，也可以直接把文件拖进窗口。",
                mode="games",
            )
            return

        self.empty_box.setVisible(False)
        self.version_scroll.setVisible(True)
        target = self._selected
        for summary in versions:
            profile = self.main.loader_profile_for(summary.id)
            bound_loader = ""
            if profile:
                bound_loader = profile.loader or profile.id
            row = VersionRow(
                summary,
                selected=(summary.id == target),
                bound_loader=bound_loader,
            )
            row.clicked.connect(self.select_version)
            row.double_clicked.connect(self._launch_version)
            row.delete_requested.connect(self.main.delete_version)
            row.export_requested.connect(self.main.export_version)
            row.migrate_requested.connect(
                lambda version_id: self.main.open_migration_dialog(version_id)
            )
            row.settings_requested.connect(self.main.open_version_settings)
            self._version_rows[summary.id] = row
            self.version_layout.insertWidget(self.version_layout.count() - 1, row)
        if target:
            self._update_hero()

    def _render_loaders(self) -> None:
        profiles = list(self.main.loader_profiles())
        self.version_count.setText(f"{len(profiles)} 个加载器")
        self.import_btn.setVisible(False)
        self.loader_install_btn.setVisible(True)
        self.tip_label.setText(
            "加载器住在独立的配置目录里。点一下加载器就能进入加载器工坊，"
            "勾选要让哪些游戏版本使用它；游戏版本本身仍然保持原版，可随时切回。"
        )
        if not profiles:
            self._show_empty(
                "还没有加载器配置",
                "先安装一个 Fabric、Forge 或 NeoForge，再去加载器工坊分配游戏版本。"
                "加载器和原版版本分开管理，不会把客户端文件混在一起。",
                mode="loaders",
            )
            return

        self.empty_box.setVisible(False)
        self.version_scroll.setVisible(True)
        for profile in profiles:
            bound = sum(
                1
                for game in self.main.compatible_game_versions(profile.id)
                if self.main.config.loader_for(game.id) == profile.id
            )
            row = LoaderOverviewRow(profile, bound)
            row.clicked.connect(lambda _profile_id: self.main.switch_page("loaders"))
            self.version_layout.insertWidget(self.version_layout.count() - 1, row)

        hint = QFrame()
        hint.setObjectName("SoftCard")
        hint.setAttribute(Qt.WA_StyledBackground, True)
        hint_layout = QHBoxLayout(hint)
        hint_layout.setContentsMargins(12, 8, 12, 8)
        hint_layout.setSpacing(8)
        text = QLabel("加载器只负责启动方式，Mod、存档、材质和光影仍然住在游戏版本自己的目录里。")
        text.setObjectName("Tiny")
        text.setWordWrap(True)
        hint_layout.addWidget(text, 1)
        manage = QPushButton("打开加载器工坊")
        manage.setObjectName("Soft")
        manage.setCursor(Qt.PointingHandCursor)
        manage.clicked.connect(lambda: self.main.switch_page("loaders"))
        hint_layout.addWidget(manage)
        self.version_layout.insertWidget(self.version_layout.count() - 1, hint)

    def _show_empty(self, title: str, subtitle: str, mode: str) -> None:
        self.empty_title.setText(title)
        self.empty_sub.setText(subtitle)
        self.empty_download_btn.setVisible(mode == "games")
        self.empty_import_btn.setVisible(mode == "games")
        self.empty_loader_btn.setVisible(mode == "loaders")
        self.empty_box.setVisible(True)
        self.version_scroll.setVisible(False)

    def _launch_version(self, version_id: str) -> None:
        if version_id != self._selected:
            self.select_version(version_id)
        self.main.launch_selected()

    def select_version(self, version_id: str) -> None:
        if version_id == self._selected:
            return
        self._selected = version_id
        self.main.selected_version = version_id
        self.main.config.last_version = version_id
        self.main.config.save()
        self.main.selection_changed.emit(version_id)
        for vid, row in self._version_rows.items():
            row.setProperty("selected", "true" if vid == version_id else "false")
            row.style().unpolish(row)
            row.style().polish(row)
            row.update()
        self._update_hero()

    def _update_hero(self) -> None:
        summary = next((v for v in self.main.versions if v.id == self._selected), None)
        if not summary:
            return
        self.main.config.last_version = self._selected
        profile = self.main.loader_profile_for(summary.id)
        loader_name = profile.loader if profile else summary.loader
        display_type = loader_name or summary.type_name
        suffix = "（绑定加载器）" if profile else ""
        java_text = f" · Java {summary.java_major}+" if summary.java_major else ""
        self.hero_sub.setText(
            f"Minecraft {summary.id} · {display_type}{suffix}{java_text}"
        )
        self.hero_title.setText(
            f"今天也要去捣乱吗，{self.main.display_name()}？"
        )
        self._refresh_status()

    def _refresh_status(self) -> None:
        self._refresh_account_card()
        if not self._selected:
            return
        profile_id, _instance_id = self.main.launch_profile(self._selected)
        try:
            resolved = resolve_version(self.main.config.game_path, profile_id)
            missing = check_installation(
                self.main.config.game_path, resolved, check_assets=False
            )
        except Exception:
            missing = ["版本文件异常"]

        self._refresh_launch_button()
        if self._closing:
            self.hero_status.setText("● 正在把猫猫拖回来…")
            style = (
                "color: #FFD9D9; background: rgba(255,110,110,45);"
                "border-radius: 9px; padding: 5px 10px; font-size: 11px; font-weight: 600;"
            )
        elif self.main.launch_active:
            self.hero_status.setText("● 正在磨爪子…")
            style = (
                "color: #D8E6FF; background: rgba(120,170,255,50);"
                "border-radius: 9px; padding: 5px 10px; font-size: 11px; font-weight: 600;"
            )
        elif self.main.game_running:
            self.hero_status.setText("● 在游戏里捣乱中")
            style = (
                "color: #C9F7D8; background: rgba(80,220,140,55);"
                "border-radius: 9px; padding: 5px 10px; font-size: 11px; font-weight: 600;"
            )
        elif missing:
            self.hero_status.setText(f"● 还差 {len(missing)} 块拼图")
            style = (
                "color: #FFE1B8; background: rgba(255,190,90,45);"
                "border-radius: 9px; padding: 5px 10px; font-size: 11px; font-weight: 600;"
            )
        else:
            self.hero_status.setText("● 猫爪已就绪")
            style = (
                "color: #C9F7D8; background: rgba(80,220,140,45);"
                "border-radius: 9px; padding: 5px 10px; font-size: 11px; font-weight: 600;"
            )
        self.hero_status.setStyleSheet(style)

    def _refresh_account_card(self) -> None:
        account = self.main.current_account()
        self.hero_title.setText(
            f"今天也要去捣乱吗，{self.main.display_name()}？"
        )
        self.avatar.refresh()
        self.account_name.setText(self.main.display_name())
        if account.is_msa:
            uid = account.uuid
            self.account_uuid.setText(
                f"正版 · UUID {uid[:8]}…{uid[-4:]}" if uid else "正版账户"
            )
            self.account_badge.setText("微软正版")
            self.account_badge.setProperty("badge", "green")
            self.account_note.setText(
                "已通过 Microsoft 正版验证，可以进入正版验证服务器。"
            )
            self.account_btn.setToolTip("管理正版账户")
        else:
            uid = offline_uuid(self.main.config.player_name)
            self.account_uuid.setText(f"UUID {uid[:8]}…{uid[-4:]}")
            self.account_badge.setText("离线模式")
            self.account_badge.setProperty("badge", "gray")
            self.account_note.setText(
                "离线账户仅用于本地游戏，无法加入正版验证服务器。"
            )
            self.account_btn.setToolTip("修改玩家名称")
        repolish(self.account_badge)

    def edit_account(self) -> None:
        if self.main.current_account().is_msa:
            self.main.switch_page("settings")
            return
        dialog = TextInputDialog(
            self.main,
            "修改玩家名称",
            "离线模式使用的游戏内名称。",
            self.main.config.player_name,
        )
        if dialog.exec():
            self.main.config.player_name = dialog.value()
            self.main.config.save()
            self.main.account_changed.emit()
            self._update_hero()
            self.main.toast("玩家名称已更新", "success")

    def _on_launch_click(self) -> None:
        if self.main.launch_active:
            self.main.cancel_launch()
        elif self.main.game_running:
            self.main.close_game()
        else:
            self.main.launch_selected()

    def _refresh_launch_button(self) -> None:
        if self._closing:
            text, icon_name, color, obj = "正在关闭…", "stop", colors()["red"], "Danger"
            enabled = False
        elif self.main.launch_active:
            text, icon_name, color, obj = "取消启动", "stop", colors()["red"], "Danger"
            enabled = True
        elif self.main.game_running:
            text, icon_name, color, obj = "关闭游戏", "stop", colors()["red"], "Danger"
            enabled = True
        else:
            text, icon_name, color, obj = "启动游戏", "play", "#3A0F1C", "Launch"
            enabled = bool(self._selected) and not self.main.install_active
        self.launch_btn.setObjectName(obj)
        self.launch_btn.setText(text)
        self.launch_btn.setEnabled(enabled)
        self.launch_btn.setIcon(icons.icon(icon_name, color, 19))
        repolish(self.launch_btn)

    def set_launching(self, launching: bool) -> None:
        if launching:
            self._closing = False
        self._refresh_status()

    def set_game_closing(self) -> None:
        self._closing = True
        self._refresh_status()

    def set_game_running(self, running: bool) -> None:
        if not running:
            self._closing = False
        self._refresh_status()
