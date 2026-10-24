from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...versions import LOADER_COLORS
from .. import icons
from ..widgets import (
    Card,
    ChibiCompanion,
    badge,
    colors,
    icon_button,
    transparent_children,
)


class GameBindingRow(QFrame):
    def __init__(
        self,
        game_id: str,
        game_type: str,
        checked: bool,
        current_loader: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SelectRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMinimumHeight(38)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)
        layout.setSpacing(8)

        self.check = QCheckBox(game_id)
        self.check.setChecked(checked)
        self.check.setCursor(Qt.PointingHandCursor)
        layout.addWidget(self.check, 1)

        type_badge = badge(game_type or "正式版", "gray")
        layout.addWidget(type_badge)

        if current_loader and not checked:
            layout.addWidget(badge(f"当前 {current_loader}", "orange"))
        elif checked:
            layout.addWidget(badge("使用中", "green"))
        transparent_children(self)

    def mouseReleaseEvent(self, event) -> None:
        # 整行可点，避免只有小方框能勾选。
        if event.button() == Qt.LeftButton:
            self.check.toggle()
        super().mouseReleaseEvent(event)


class LoaderCard(Card):
    def __init__(self, main, profile, parent=None) -> None:
        super().__init__(parent)
        self.main = main
        self.profile = profile
        layout = QVBoxLayout(self)
        layout.setContentsMargins(17, 14, 17, 14)
        layout.setSpacing(9)

        loader_name = profile.loader or "加载器"
        accent = LOADER_COLORS.get(loader_name, colors()["accent"])

        head = QHBoxLayout()
        head.setSpacing(9)
        mark = QLabel()
        mark.setFixedSize(34, 34)
        mark.setAlignment(Qt.AlignCenter)
        mark.setPixmap(icons.pixmap("package", accent, 21))
        head.addWidget(mark)

        titles = QVBoxLayout()
        titles.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        title = QLabel(profile.id)
        title.setStyleSheet("font-weight: 700; font-size: 13px;")
        title_row.addWidget(title)
        title_row.addWidget(badge(loader_name, "purple"))
        if profile.loader_version:
            title_row.addWidget(badge(f"v{profile.loader_version}", "blue"))
        title_row.addStretch(1)
        titles.addLayout(title_row)
        meta_bits = [f"基础版本 {profile.base_id or '未知'}"]
        if profile.release_time:
            meta_bits.append(profile.release_time[:10])
        if profile.java_major:
            meta_bits.append(f"Java {profile.java_major}+")
        meta = QLabel(" · ".join(meta_bits))
        meta.setObjectName("Tiny")
        titles.addWidget(meta)
        head.addLayout(titles, 1)

        folder_btn = icon_button("folder", "打开加载器配置目录", colors()["muted"], 28, 15)
        folder_btn.clicked.connect(
            lambda _=False: self.main.open_path(Path(profile.json_path).parent)
        )
        head.addWidget(folder_btn)
        delete_btn = icon_button(
            "trash", "删除这个加载器配置", colors()["muted"], 28, 15
        )
        delete_btn.clicked.connect(
            lambda _=False: self.main.delete_version(profile.id)
        )
        head.addWidget(delete_btn)
        layout.addLayout(head)

        hint = QLabel(
            "勾选要使用这个加载器的游戏版本。每个游戏版本只能绑定一个加载器，"
            "存档、Mod、材质和光影始终留在游戏版本自己的目录里。"
        )
        hint.setObjectName("Tiny")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        games = self.main.compatible_game_versions(profile.id)
        if not games:
            empty = QLabel(
                "没有可分配的原版游戏版本。需要先安装基础版本 "
                f"{profile.base_id or '对应原版'}，或它已被其他加载器占用。"
            )
            empty.setObjectName("Muted")
            empty.setWordWrap(True)
            layout.addWidget(empty)
            return

        tools = QHBoxLayout()
        tools.setSpacing(6)
        tools.addStretch(1)
        all_btn = QPushButton("全部使用")
        all_btn.setObjectName("Ghost")
        all_btn.setCursor(Qt.PointingHandCursor)
        all_btn.clicked.connect(lambda: self._assign_all(games, True))
        none_btn = QPushButton("全部取消")
        none_btn.setObjectName("Ghost")
        none_btn.setCursor(Qt.PointingHandCursor)
        none_btn.clicked.connect(lambda: self._assign_all(games, False))
        tools.addWidget(all_btn)
        tools.addWidget(none_btn)
        layout.addLayout(tools)

        holder = QVBoxLayout()
        holder.setSpacing(3)
        for game in games:
            binding = self.main.config.loader_for(game.id)
            checked = binding == profile.id
            current = ""
            if binding and not checked:
                other = next(
                    (v for v in self.main.versions if v.id == binding), None
                )
                current = (other.loader if other and other.loader else binding)
            row = GameBindingRow(
                game.id,
                game.type_name,
                checked,
                current,
            )
            row.check.toggled.connect(
                lambda value, gid=game.id, pid=profile.id: self._assign(
                    gid, pid, value
                )
            )
            holder.addWidget(row)
        layout.addLayout(holder)

    def _assign(self, game_id: str, profile_id: str, enabled: bool) -> None:
        if enabled:
            self.main.bind_loader(game_id, profile_id)
        elif self.main.config.loader_for(game_id) == profile_id:
            self.main.bind_loader(game_id, "")
        self.main.toast(
            "已更新加载器分配" if enabled else "该版本已切回原版启动",
            "success",
        )

    def _assign_all(self, games, enabled: bool) -> None:
        for game in games:
            if enabled:
                self.main.config.bind_loader(game.id, self.profile.id)
            elif self.main.config.loader_for(game.id) == self.profile.id:
                self.main.config.bind_loader(game.id, "")
        self.main.config.save()
        self.main.versions_changed.emit()
        self.main.selection_changed.emit(self.main.selected_version)
        self.main.toast(
            "已批量分配加载器" if enabled else "已批量切回原版启动",
            "success",
        )


class LoaderPage(QWidget):
    def __init__(self, main, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.main = main
        self._build()
        self.main.versions_changed.connect(self.refresh)
        self.refresh()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 18, 22, 18)
        outer.setSpacing(14)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = QLabel("猫猫的加载器工坊")
        title.setObjectName("PageTitle")
        sub = QLabel(
            "加载器住在这里，游戏版本住在猫窝。装好 Fabric、Forge 或 NeoForge 后，"
            "在下面勾选要让哪些游戏版本使用它；也可以随时切回原版启动。"
        )
        sub.setObjectName("Muted")
        sub.setWordWrap(True)
        titles.addWidget(title)
        titles.addWidget(sub)
        header.addLayout(titles, 1)
        header.addWidget(ChibiCompanion(60, "mods"), 0, Qt.AlignTop)
        self.install_btn = QPushButton("安装加载器")
        self.install_btn.setObjectName("Primary")
        self.install_btn.setCursor(Qt.PointingHandCursor)
        self.install_btn.setIcon(icons.icon("download", "#FFFFFF", 16))
        self.install_btn.clicked.connect(self.main.open_loader_dialog)
        header.addWidget(self.install_btn)
        outer.addLayout(header)

        self.summary_row = QHBoxLayout()
        self.summary_row.setSpacing(8)
        self.loader_count = badge("0 个加载器", "blue")
        self.binding_count = badge("0 个版本已绑定", "green")
        self.summary_row.addWidget(self.loader_count)
        self.summary_row.addWidget(self.binding_count)
        self.summary_row.addStretch(1)
        outer.addLayout(self.summary_row)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.holder = QWidget()
        self.card_layout = QVBoxLayout(self.holder)
        self.card_layout.setContentsMargins(0, 0, 4, 0)
        self.card_layout.setSpacing(10)
        self.card_layout.addStretch(1)
        self.scroll.setWidget(self.holder)
        outer.addWidget(self.scroll, 1)

    def on_show(self) -> None:
        self.refresh()

    def refresh_theme(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        profiles = self.main.loader_profiles()
        games = self.main.game_version_summaries()
        bound = sum(
            1
            for game in games
            if not game.loader and self.main.loader_profile_for(game.id)
        )
        self.loader_count.setText(f"{len(profiles)} 个加载器")
        self.binding_count.setText(f"{bound} 个版本已绑定")
        self.install_btn.setEnabled(not self.main.install_active)

        while self.card_layout.count() > 1:
            item = self.card_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        if not profiles:
            self._add_empty_state()
            return

        bound_any = set()
        for profile in profiles:
            card = LoaderCard(self.main, profile)
            self.card_layout.insertWidget(self.card_layout.count() - 1, card)
            for game in self.main.compatible_game_versions(profile.id):
                if self.main.config.loader_for(game.id) == profile.id:
                    bound_any.add(game.id)

        plain_games = [game for game in games if not game.loader]
        idle_games = [game for game in plain_games if game.id not in bound_any]
        if idle_games:
            card = Card()
            layout = QVBoxLayout(card)
            layout.setContentsMargins(17, 13, 17, 13)
            layout.setSpacing(7)
            head = QHBoxLayout()
            title = QLabel("原版启动的游戏版本")
            title.setObjectName("H2")
            head.addWidget(title)
            head.addStretch(1)
            open_btn = QPushButton("版本设置")
            open_btn.setObjectName("Ghost")
            open_btn.setCursor(Qt.PointingHandCursor)
            open_btn.setIcon(icons.icon("settings", colors()["muted"], 15))
            open_btn.clicked.connect(
                lambda: self.main.open_version_settings(
                    idle_games[0].id if idle_games else ""
                )
            )
            head.addWidget(open_btn)
            layout.addLayout(head)
            names = QLabel("、".join(game.id for game in idle_games))
            names.setObjectName("Muted")
            names.setWordWrap(True)
            layout.addWidget(names)
            tip = QLabel("这些版本不加载任何 Mod 加载器；可以在版本行的齿轮里随时绑定加载器。")
            tip.setObjectName("Tiny")
            tip.setWordWrap(True)
            layout.addWidget(tip)
            self.card_layout.insertWidget(self.card_layout.count() - 1, card)

    def _add_empty_state(self) -> None:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(10)
        icon_label = QLabel()
        icon_label.setPixmap(icons.pixmap("package", colors()["muted"], 54))
        icon_label.setAlignment(Qt.AlignCenter)
        title = QLabel("还没有加载器配置")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 16px; font-weight: 700;")
        sub = QLabel(
            "先到“下载”页面安装原版游戏，再在这里添加 Fabric、Forge 或 NeoForge。"
            "加载器和游戏版本分开管理，同一个加载器可以分配给多个游戏版本。"
        )
        sub.setObjectName("Muted")
        sub.setWordWrap(True)
        sub.setAlignment(Qt.AlignCenter)
        btn = QPushButton("安装第一个加载器")
        btn.setObjectName("Primary")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setIcon(icons.icon("download", "#FFFFFF", 16))
        btn.clicked.connect(self.main.open_loader_dialog)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(btn)
        btn_row.addStretch(1)
        layout.addWidget(icon_label)
        layout.addWidget(title)
        layout.addWidget(sub)
        layout.addLayout(btn_row)
        self.card_layout.insertWidget(self.card_layout.count() - 1, card)
