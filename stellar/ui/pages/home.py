from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QCursor
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
from ...config import APP_VERSION
from ...versions import (
    LOADER_COLORS,
    ResolvedVersion,
    VersionSummary,
    check_installation,
    resolve_version,
)
from .. import icons, theme
from ..dialogs import TextInputDialog
from ..widgets import (
    Avatar,
    Card,
    HeroArt,
    VersionRow,
    badge,
    colors,
    hline,
    icon_button,
    repolish,
    transparent_children,
)


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
        self._version_rows: dict[str, VersionRow] = {}
        self._build()
        self.main.versions_changed.connect(self.refresh_versions)
        self.main.install_finished.connect(lambda *_: self._refresh_status())
        self.refresh_versions()

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
        title = QLabel("猫窝里的版本")
        title.setObjectName("H2")
        self.version_count = badge("0 个版本", "blue")
        header.addWidget(title)
        header.addWidget(self.version_count)
        header.addStretch(1)
        import_btn = icon_button("import", "导入版本包 / 整合包（.zip / .dklpack / .mrpack）")
        import_btn.clicked.connect(self.main.open_version_import)
        migrate_btn = icon_button("migrate", "迁移版本 / 升级加载器")
        migrate_btn.clicked.connect(
            lambda: self.main.open_migration_dialog(self._selected)
        )
        refresh_btn = icon_button("refresh", "刷新版本列表")
        refresh_btn.clicked.connect(self.main.refresh_versions)
        folder_btn = icon_button("folder", "打开游戏目录")
        folder_btn.clicked.connect(self.main.open_game_dir)
        header.addWidget(import_btn)
        header.addWidget(migrate_btn)
        header.addWidget(refresh_btn)
        header.addWidget(folder_btn)
        layout.addLayout(header)

        tip = QLabel(
            "双击启动 · 交换箭头迁移存档/Mod · 纸箱按钮打包 · 垃圾桶删除版本 · "
            "也可以直接把 .zip / .dklpack / .mrpack 拖进窗口"
        )
        tip.setObjectName("Tiny")
        layout.addWidget(tip)

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
        empty_title = QLabel("还没有可以住的猫窝")
        empty_title.setStyleSheet("font-weight: 700;")
        empty_sub = QLabel(
            "前往“下载”页面安装原版游戏，或导入 ZIP 版本包 / DKL 整合包 / "
            "Modrinth 整合包，也可以直接把压缩包拖进窗口。"
        )
        empty_sub.setObjectName("Tiny")
        empty_sub.setWordWrap(True)
        empty_actions = QHBoxLayout()
        empty_actions.setSpacing(8)
        download_btn = QPushButton("前往下载")
        download_btn.setObjectName("Soft")
        download_btn.setCursor(Qt.PointingHandCursor)
        download_btn.clicked.connect(lambda: self.main.switch_page("download"))
        import_btn = QPushButton("导入版本包")
        import_btn.setObjectName("Soft")
        import_btn.setCursor(Qt.PointingHandCursor)
        import_btn.setIcon(icons.icon("package", colors()["accent"], 15))
        import_btn.clicked.connect(self.main.open_version_import)
        empty_actions.addWidget(download_btn)
        empty_actions.addWidget(import_btn)
        empty_actions.addStretch(1)
        empty_layout.addWidget(empty_title)
        empty_layout.addWidget(empty_sub)
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
            ("folder", "游戏目录", "打开游戏文件夹", "#E23A5E", self.main.open_game_dir),
            ("package", "导入版本包", "ZIP 版本或 DKL 整合包", "#E8683F", self.main.open_version_import),
            ("migrate", "版本迁移", "搬走存档与 Mod", "#3FB3AA", lambda: self.main.open_migration_dialog(self._selected)),
            ("users", "好友与联机", "邀请卡直达房间", "#3FB3AA", self.main.open_social_dialog),
            ("cube", "模组管理", "管理已装 Mod", "#B15BE0", lambda: self.main.switch_page("mods")),
            ("download", "补全文件", "修复缺失依赖", "#C98A4B", self.main.complete_selected),
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
        versions: list[VersionSummary] = list(self.main.versions)
        self.version_count.setText(f"{len(versions)} 个版本")

        had_selection = self._selected
        while self.version_layout.count() > 1:
            item = self.version_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        self._version_rows.clear()
        if not versions:
            self.empty_box.setVisible(True)
            self.version_scroll.setVisible(False)
            self._selected = ""
            self.main.selected_version = ""
            self.main.selection_changed.emit("")
            self.hero_sub.setText("还没有版本可玩，先去仓库叼一个回来吧。")
            self.hero_status.setText("● 猫窝是空的")
            self.hero_status.setStyleSheet(
                "color: rgba(255,220,180,220); background: rgba(255,255,255,26);"
                "border-radius: 9px; padding: 5px 10px; font-size: 11px; font-weight: 600;"
            )
            self.launch_btn.setEnabled(False)
            return

        self.empty_box.setVisible(False)
        self.version_scroll.setVisible(True)

        valid_ids = {v.id for v in versions}
        target = had_selection if had_selection in valid_ids else ""
        if not target and self.main.config.last_version in valid_ids:
            target = self.main.config.last_version
        if not target:
            target = versions[0].id
        self._selected = target
        self.main.selected_version = target

        row_widgets: list[VersionRow] = []
        for summary in versions:
            row = VersionRow(summary, selected=(summary.id == target))
            row.clicked.connect(self.select_version)
            row.double_clicked.connect(self._launch_version)
            row.delete_requested.connect(self.main.delete_version)
            row.export_requested.connect(self.main.export_version)
            row.migrate_requested.connect(
                lambda version_id: self.main.open_migration_dialog(version_id)
            )
            row_widgets.append(row)
            self._version_rows[summary.id] = row
            self.version_layout.insertWidget(self.version_layout.count() - 1, row)

        self._update_hero()
        self.main.config.last_version = target
        self.main.config.save()

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
        display_type = summary.loader or summary.type_name
        java_text = f" · Java {summary.java_major}+" if summary.java_major else ""
        self.hero_sub.setText(
            f"Minecraft {summary.id} · {display_type}{java_text}"
        )
        self.hero_title.setText(
            f"今天也要去捣乱吗，{self.main.display_name()}？"
        )
        self._refresh_status()

    def _refresh_status(self) -> None:
        if not self._selected:
            return
        try:
            resolved = resolve_version(self.main.config.game_path, self._selected)
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
