from __future__ import annotations

import base64
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import game_assistant as ga
from .. import worlds
from . import icons
from .dialogs import ConfirmDialog, StyledDialog
from .widgets import SegmentedControl, badge, colors, icon_button, repolish

MODERN_NOTE = (
    "修改会直接写入 level.dat，不启动游戏即可生效；"
    "如果游戏正在运行，请先退出，否则退出游戏时可能覆盖这里的修改。"
)


def _pixmap_from_icon(path: Path | None, size: int = 44) -> QPixmap:
    pixmap = QPixmap()
    if path is not None:
        pixmap.load(str(path))
    if pixmap.isNull():
        return icons.pixmap("cube", colors()["accent"], 24)
    return pixmap.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def _server_pixmap(entry: worlds.ServerEntry, size: int = 44) -> QPixmap:
    if entry.icon:
        try:
            data = base64.b64decode(entry.icon)
        except (ValueError, TypeError):
            data = b""
        if data:
            pixmap = QPixmap()
            if pixmap.loadFromData(data):
                return pixmap.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return icons.pixmap("server", colors()["accent"], 24)


def _ellipsis(label: QLabel, text: str, tooltip: str = "") -> None:
    label.setText(text)
    label.setToolTip(tooltip or text)
    label.setMinimumWidth(0)
    label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)


class WorldRow(QFrame):
    edit_requested = Signal(str)
    delete_requested = Signal(str)
    folder_requested = Signal(str)
    seed_copy_requested = Signal(str)

    def __init__(self, info: worlds.WorldInfo, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.info = info
        self.setObjectName("SelectRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(64)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(11)

        mark = QLabel()
        mark.setFixedSize(46, 46)
        mark.setAlignment(Qt.AlignCenter)
        mark.setPixmap(_pixmap_from_icon(info.icon_path))
        layout.addWidget(mark)

        texts = QVBoxLayout()
        texts.setSpacing(3)
        title = QHBoxLayout()
        title.setSpacing(6)
        name = QLabel(info.name)
        name.setStyleSheet("font-weight: 700; font-size: 13px;")
        title.addWidget(name)
        title.addWidget(badge(info.mode_text, "purple" if info.game_mode == 1 else "green"))
        title.addWidget(badge(info.difficulty_text, "orange" if info.difficulty >= 2 else "gray"))
        if info.hardcore:
            title.addWidget(badge("硬核", "red"))
        if info.cheats:
            title.addWidget(badge("作弊", "blue"))
        if info.error:
            title.addWidget(badge("读取异常", "red"))
        title.addStretch(1)
        texts.addLayout(title)

        seed_text = info.seed_text
        meta = QLabel(
            f"最后游玩 {info.last_played_text} · 版本 {info.version_name or '未知'} · "
            f"种子 {seed_text}"
        )
        meta.setObjectName("Tiny")
        meta.setTextInteractionFlags(Qt.TextSelectableByMouse)
        _ellipsis(meta, meta.text(), meta.text())
        texts.addWidget(meta)
        layout.addLayout(texts, 1)

        copy_seed = icon_button("copy", f"复制种子：{seed_text}", colors()["muted"], 28, 15)
        copy_seed.clicked.connect(
            lambda _=False: self.seed_copy_requested.emit(seed_text)
        )
        layout.addWidget(copy_seed)

        folder_btn = icon_button("folder", "打开存档文件夹", colors()["muted"], 28, 15)
        folder_btn.clicked.connect(
            lambda _=False: self.folder_requested.emit(str(self.info.folder))
        )
        layout.addWidget(folder_btn)

        edit_btn = icon_button("settings", "存档设置：难度、种子、天气与游戏规则", colors()["muted"], 28, 15)
        edit_btn.clicked.connect(
            lambda _=False: self.edit_requested.emit(str(self.info.folder))
        )
        layout.addWidget(edit_btn)

        delete_btn = icon_button("trash", "删除存档（移到回收站）", colors()["muted"], 28, 15)
        delete_btn.clicked.connect(
            lambda _=False: self.delete_requested.emit(str(self.info.folder))
        )
        layout.addWidget(delete_btn)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.edit_requested.emit(str(self.info.folder))
        super().mouseDoubleClickEvent(event)


class ServerRow(QFrame):
    edit_requested = Signal(int)
    delete_requested = Signal(int)
    copy_requested = Signal(str)

    def __init__(self, entry: worlds.ServerEntry, index: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.entry = entry
        self.index = index
        self.setObjectName("SelectRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMinimumHeight(64)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(11)

        mark = QLabel()
        mark.setFixedSize(46, 46)
        mark.setAlignment(Qt.AlignCenter)
        mark.setPixmap(_server_pixmap(entry))
        layout.addWidget(mark)

        texts = QVBoxLayout()
        texts.setSpacing(3)
        title = QHBoxLayout()
        title.setSpacing(6)
        name = QLabel(entry.name or "未命名服务器")
        name.setStyleSheet("font-weight: 700; font-size: 13px;")
        title.addWidget(name)
        if not entry.accept_textures:
            title.addWidget(badge("不接收资源包", "gray"))
        if entry.hidden:
            title.addWidget(badge("已隐藏", "orange"))
        title.addStretch(1)
        texts.addLayout(title)

        address = QLabel(entry.address)
        address.setObjectName("Tiny")
        address.setTextInteractionFlags(Qt.TextSelectableByMouse)
        _ellipsis(address, entry.address)
        texts.addWidget(address)
        layout.addLayout(texts, 1)

        copy_btn = icon_button("copy", "复制服务器地址", colors()["muted"], 28, 15)
        copy_btn.clicked.connect(
            lambda _=False: self.copy_requested.emit(self.entry.address)
        )
        layout.addWidget(copy_btn)

        edit_btn = icon_button("settings", "编辑服务器名称与地址", colors()["muted"], 28, 15)
        edit_btn.clicked.connect(lambda _=False: self.edit_requested.emit(self.index))
        layout.addWidget(edit_btn)

        delete_btn = icon_button("trash", "从多人游戏列表删除", colors()["muted"], 28, 15)
        delete_btn.clicked.connect(lambda _=False: self.delete_requested.emit(self.index))
        layout.addWidget(delete_btn)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.edit_requested.emit(self.index)
        super().mouseDoubleClickEvent(event)


class WorldManagerDialog(StyledDialog):
    """某个游戏版本的单人存档 / 多人服务器预览与管理。"""

    def __init__(self, main, version_id: str) -> None:
        self.main = main
        self.version_id = version_id
        self.game_dir = Path(main.config.version_game_path(version_id))
        super().__init__(parent=main, title="", icon_name="globe", width=820)
        self.title_label.setText(f"存档与服务器 · {version_id}")
        self._tab = "saves"
        self._build()
        self._fit_height()
        self._reload()

    def _fit_height(self) -> None:
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry().height() if screen else 800
        self.setFixedHeight(max(560, min(680, available - 80)))

    # ------------------------------------------------------------------
    def _build(self) -> None:
        summary = QFrame()
        summary.setObjectName("SoftCard")
        summary.setAttribute(Qt.WA_StyledBackground, True)
        box = QVBoxLayout(summary)
        box.setContentsMargins(12, 9, 12, 9)
        box.setSpacing(4)
        head = QHBoxLayout()
        head.setSpacing(6)
        title = QLabel(f"版本 {self.version_id}")
        title.setStyleSheet("font-weight: 700;")
        head.addWidget(title)
        head.addWidget(badge("单人存档 + 多人服务器", "blue"))
        head.addStretch(1)
        box.addLayout(head)
        path = QLabel(str(self.game_dir))
        path.setObjectName("Tiny")
        path.setWordWrap(True)
        path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        box.addWidget(path)
        if getattr(self.main, "game_running", False):
            warning = QLabel("游戏正在运行：这里的修改只有在退出游戏后保存才不会丢失。")
            warning.setStyleSheet(f"color: {colors()['orange']}; font-size: 11px;")
            warning.setWordWrap(True)
            box.addWidget(warning)
        self.body.addWidget(summary)

        header = QHBoxLayout()
        header.setSpacing(8)
        self.tabs = SegmentedControl()
        self.tabs.add_item("单人游戏", "saves")
        self.tabs.add_item("多人游戏", "servers")
        self.tabs.changed.connect(self._on_tab_changed)
        header.addWidget(self.tabs)
        header.addStretch(1)
        self.refresh_btn = icon_button("refresh", "重新扫描存档与服务器", colors()["muted"], 30, 15)
        self.refresh_btn.clicked.connect(self._reload)
        header.addWidget(self.refresh_btn)
        folder_btn = icon_button("folder", "打开这个版本的游戏目录", colors()["muted"], 30, 15)
        folder_btn.clicked.connect(lambda: self.main.open_path(self.game_dir))
        header.addWidget(folder_btn)
        self.body.addLayout(header)

        self.note = QLabel(
            "存档列表会直接显示种子。双击存档可以修改难度、模式、天气和游戏规则。"
        )
        self.note.setObjectName("Tiny")
        self.note.setWordWrap(True)
        self.body.addWidget(self.note)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.holder = QWidget()
        self.list_layout = QVBoxLayout(self.holder)
        self.list_layout.setContentsMargins(0, 0, 6, 0)
        self.list_layout.setSpacing(5)
        self.list_layout.addStretch(1)
        self.scroll.setWidget(self.holder)
        self.body.addWidget(self.scroll, 1)

        self.import_world_btn = self.add_button(
            "导入存档", "Soft", self._import_world
        )
        self.new_world_btn = self.add_button("新建存档", "Primary", self._create_world)
        self.add_server_btn = self.add_button("添加服务器", "Primary", self._add_server)
        self.add_button("关闭", "Secondary", self.reject)
        self._update_footer()

    # ------------------------------------------------------------------
    def _toast(self, text: str, kind: str = "info") -> None:
        toast = getattr(self.main, "toast", None)
        if callable(toast):
            toast(text, kind)

    def _copy(self, text: str, message: str) -> None:
        if text:
            QApplication.clipboard().setText(text)
            self._toast(message, "success")

    def _update_footer(self) -> None:
        self.import_world_btn.setVisible(self._tab == "saves")
        self.new_world_btn.setVisible(self._tab == "saves")
        self.add_server_btn.setVisible(self._tab == "servers")

    def _on_tab_changed(self, value: str) -> None:
        self._tab = value if value in ("saves", "servers") else "saves"
        self._update_footer()
        self._reload()

    def _clear(self) -> None:
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _empty(self, title: str, subtitle: str) -> None:
        card = QFrame()
        card.setObjectName("SoftCard")
        card.setAttribute(Qt.WA_StyledBackground, True)
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(4)
        title_label = QLabel(title)
        title_label.setStyleSheet("font-weight: 700;")
        box.addWidget(title_label)
        sub = QLabel(subtitle)
        sub.setObjectName("Tiny")
        sub.setWordWrap(True)
        box.addWidget(sub)
        self.list_layout.insertWidget(0, card)

    def _reload(self) -> None:
        self._clear()
        if self._tab == "saves":
            self._reload_worlds()
        else:
            self._reload_servers()
        repolish(self.holder)

    def _reload_worlds(self) -> None:
        items = worlds.scan_worlds(self.game_dir)
        self.tabs.set_item_text("saves", f"单人游戏 {len(items)}")
        self.note.setText(
            "存档列表会直接显示种子。双击存档可以修改难度、模式、天气和游戏规则；"
            "删除会先移到回收站。"
        )
        if not items:
            self._empty(
                "还没有单人存档",
                "点右下角「新建存档」可以直接创建一个世界，之后不启动游戏也能改规则。",
            )
            return
        for info in items:
            row = WorldRow(info)
            row.edit_requested.connect(self._edit_world)
            row.delete_requested.connect(self._delete_world)
            row.folder_requested.connect(lambda folder: self.main.open_path(folder))
            row.seed_copy_requested.connect(
                lambda seed: self._copy(seed, "种子已复制")
            )
            self.list_layout.insertWidget(self.list_layout.count() - 1, row)

    def _reload_servers(self) -> None:
        items = worlds.read_servers(self.game_dir)
        self.tabs.set_item_text("servers", f"多人游戏 {len(items)}")
        self.note.setText(
            "这里对应游戏里的「多人游戏」服务器列表（servers.dat）。"
            "修改后下次进入游戏即可看到，不需要启动游戏。"
        )
        if not items:
            self._empty(
                "多人游戏列表是空的",
                "点右下角「添加服务器」填入名称和地址，启动器会直接写入 servers.dat。",
            )
            return
        for index, entry in enumerate(items):
            row = ServerRow(entry, index)
            row.edit_requested.connect(self._edit_server)
            row.delete_requested.connect(self._delete_server)
            row.copy_requested.connect(lambda text: self._copy(text, "服务器地址已复制"))
            self.list_layout.insertWidget(self.list_layout.count() - 1, row)

    # ------------------------------------------------------------------
    def _import_world(self) -> None:
        if getattr(self.main, "game_running", False):
            self._toast("游戏正在运行，请先退出游戏再导入存档", "error")
            return
        from .world_import_dialog import WorldImportDialog

        dialog = WorldImportDialog(
            self,
            target_text=(
                f"把其他游戏版本的完整存档导入版本 {self.version_id} 的 saves 目录。"
                "所有维度、玩家数据和物品栏都会原样保留，"
                "跨版本升级由 Minecraft 首次打开世界时完成。"
            ),
            default_name="",
            name_label="导入后的文件夹名",
            dest_parent=self.game_dir / "saves",
            target_version=self.version_id,
        )
        dialog.exec()
        if dialog.import_result is not None:
            self._toast(f"已导入存档「{dialog.import_result.source.name}」", "success")
            self._reload()

    def _create_world(self) -> None:
        dialog = WorldCreateDialog(self, self.version_id)
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            info = worlds.create_world(
                self.game_dir,
                name=dialog.world_name,
                seed=dialog.seed_value,
                game_mode=dialog.game_mode,
                difficulty=dialog.difficulty,
                hardcore=dialog.hardcore,
                cheats=dialog.cheats,
                version_id=self.version_id,
            )
        except (OSError, ValueError) as error:
            self._toast(f"创建存档失败：{error}", "error")
            return
        if dialog.keep_inventory:
            try:
                settings = worlds.load_world_settings(info.folder)
                settings.rules["keepInventory"] = True
                worlds.apply_world_settings(info.folder, settings)
            except (OSError, ValueError):
                pass
        self._toast(f"已创建存档「{info.name}」", "success")
        self._reload()

    def _edit_world(self, folder: str) -> None:
        dialog = WorldSettingsDialog(
            self,
            Path(folder),
            main=self.main,
            game_running=getattr(self.main, "game_running", False),
        )
        if dialog.exec() == QDialog.Accepted:
            self._toast("存档设置已保存", "success")
            self._reload()

    def _delete_world(self, folder: str) -> None:
        path = Path(folder)
        details = "删除后会移到 Windows 回收站，可以从回收站恢复。"
        if getattr(self.main, "game_running", False):
            details += " 游戏正在运行，建议先退出游戏。"
        confirm = ConfirmDialog(
            self,
            "删除存档",
            f"确定删除「{path.name}」吗？",
            "移到回收站",
            "取消",
            details=details,
        )
        if confirm.exec() != QDialog.Accepted:
            return
        try:
            ok, message = worlds.delete_world(path)
        except (OSError, ValueError) as error:
            self._toast(f"删除失败：{error}", "error")
            return
        self._toast(message, "success" if ok else "error")
        self._reload()

    def _add_server(self) -> None:
        dialog = ServerEditDialog(self)
        if dialog.exec() != QDialog.Accepted or dialog.entry is None:
            return
        try:
            worlds.upsert_server(self.game_dir, dialog.entry)
        except (OSError, ValueError) as error:
            self._toast(f"添加失败：{error}", "error")
            return
        self._toast(f"已添加服务器「{dialog.entry.name}」", "success")
        self._reload()

    def _edit_server(self, index: int) -> None:
        items = worlds.read_servers(self.game_dir)
        if not (0 <= index < len(items)):
            return
        dialog = ServerEditDialog(self, items[index])
        if dialog.exec() != QDialog.Accepted or dialog.entry is None:
            return
        try:
            worlds.upsert_server(self.game_dir, dialog.entry, index)
        except (OSError, ValueError) as error:
            self._toast(f"保存失败：{error}", "error")
            return
        self._toast("服务器信息已保存", "success")
        self._reload()

    def _delete_server(self, index: int) -> None:
        items = worlds.read_servers(self.game_dir)
        if not (0 <= index < len(items)):
            return
        name = items[index].name or items[index].address
        confirm = ConfirmDialog(
            self,
            "删除服务器",
            f"从多人游戏列表移除「{name}」？",
            "移除",
            "取消",
            details="只会删除服务器列表里的这一条记录，不会影响服务器本身。",
        )
        if confirm.exec() != QDialog.Accepted:
            return
        try:
            worlds.delete_server(self.game_dir, index)
        except (OSError, ValueError) as error:
            self._toast(f"删除失败：{error}", "error")
            return
        self._toast("已从列表移除", "success")
        self._reload()


class WorldCreateDialog(StyledDialog):
    def __init__(self, parent: QWidget | None, version_id: str) -> None:
        super().__init__(parent, "新建单人存档", "plus", width=540)
        self._version_id = version_id
        self.world_name = "新的世界"
        self.seed_value: int | None = None
        self.game_mode = 0
        self.difficulty = 2
        self.hardcore = False
        self.cheats = False
        self.keep_inventory = False

        intro = QLabel(
            "存档会直接创建在当前版本的 saves 目录里。种子留空会随机生成；"
            "填文字也可以，启动器会按 Java 的字符串哈希换算成数字种子。"
        )
        intro.setObjectName("DialogText")
        intro.setWordWrap(True)
        self.body.addWidget(intro)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        self.name_edit = QLineEdit("新的世界")
        self.name_edit.setMaxLength(64)
        self.seed_edit = QLineEdit("")
        self.seed_edit.setPlaceholderText("留空随机，例如 123456789 或 hello")
        self.mode_combo = QComboBox()
        for value, label in worlds.GAME_MODES.items():
            if value <= 1:
                self.mode_combo.addItem(label, value)
        self.difficulty_combo = QComboBox()
        for value, label in worlds.DIFFICULTIES.items():
            self.difficulty_combo.addItem(label, value)
        self.difficulty_combo.setCurrentIndex(2)
        grid.addWidget(QLabel("存档名称"), 0, 0)
        grid.addWidget(self.name_edit, 0, 1)
        grid.addWidget(QLabel("种子"), 1, 0)
        grid.addWidget(self.seed_edit, 1, 1)
        grid.addWidget(QLabel("游戏模式"), 2, 0)
        grid.addWidget(self.mode_combo, 2, 1)
        grid.addWidget(QLabel("难度"), 3, 0)
        grid.addWidget(self.difficulty_combo, 3, 1)
        self.body.addLayout(grid)

        self.hardcore_box = QCheckBox("硬核模式（只有一条命，强制生存）")
        self.cheats_box = QCheckBox("允许作弊（开启指令）")
        self.keep_box = QCheckBox("死亡不掉落（keepInventory）")
        for box in (self.hardcore_box, self.cheats_box, self.keep_box):
            self.body.addWidget(box)
        self.hardcore_box.toggled.connect(self._on_hardcore)

        self.error = QLabel("")
        self.error.setStyleSheet(f"color: {colors()['red']}; font-size: 11px;")
        self.error.setVisible(False)
        self.body.addWidget(self.error)

        self.add_button("取消", "Secondary", self.reject)
        self.add_button("创建存档", "Primary", self._save)
        self.name_edit.setFocus()
        self.name_edit.selectAll()

    def _on_hardcore(self, checked: bool) -> None:
        if checked:
            self.mode_combo.setCurrentIndex(0)
        self.mode_combo.setEnabled(not checked)

    def _save(self) -> None:
        name = worlds.clean_world_name(self.name_edit.text())
        if not name:
            self._fail("存档名称不能为空")
            return
        seed_text = self.seed_edit.text().strip()
        seed_value: int | None = None
        if seed_text:
            try:
                seed_value = ga.parse_seed(seed_text).value
            except ValueError as error:
                self._fail(f"种子无法解析：{error}")
                return
            if not -(1 << 63) <= int(seed_value) < (1 << 63):
                self._fail("种子超出 64 位整数范围")
                return
        self.world_name = name
        self.seed_value = int(seed_value) if seed_value is not None else None
        self.game_mode = int(self.mode_combo.currentData() or 0)
        self.difficulty = int(self.difficulty_combo.currentData() or 2)
        self.hardcore = self.hardcore_box.isChecked()
        self.cheats = self.cheats_box.isChecked()
        self.keep_inventory = self.keep_box.isChecked()
        self.accept()

    def _fail(self, message: str) -> None:
        self.error.setText(message)
        self.error.setVisible(True)


class WorldSettingsDialog(StyledDialog):
    def __init__(
        self,
        parent: QWidget | None,
        folder: Path,
        *,
        main=None,
        game_running: bool = False,
    ) -> None:
        self.folder = folder
        self.main_window = main
        self.game_running = game_running
        try:
            self.settings = worlds.load_world_settings(folder)
        except (OSError, ValueError) as error:
            self.settings = None
            load_error = str(error)
        else:
            load_error = ""
        title = self.settings.name if self.settings is not None else folder.name
        super().__init__(parent, f"存档设置 · {title}", "settings", width=720)
        self._rule_widgets: dict[str, QWidget] = {}
        if self.settings is None:
            text = QLabel(f"读取存档失败：{load_error}")
            text.setObjectName("DialogText")
            text.setWordWrap(True)
            self.body.addWidget(text)
            self.add_button("关闭", "Secondary", self.reject)
            return
        self._build()
        self._fit_height()

    def _fit_height(self) -> None:
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry().height() if screen else 800
        self.setFixedHeight(max(580, min(740, available - 70)))

    def _build(self) -> None:
        settings = self.settings
        warning_text = MODERN_NOTE
        if self.game_running:
            warning_text = "游戏正在运行：请先退出游戏再保存，否则退出时可能覆盖这些修改。"
        warning = QLabel(warning_text)
        warning.setStyleSheet(
            f"color: {colors()['orange'] if self.game_running else colors()['muted']};"
            "font-size: 11px;"
        )
        warning.setWordWrap(True)
        self.body.addWidget(warning)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 6, 0)
        layout.setSpacing(10)
        scroll.setWidget(holder)
        self.body.addWidget(scroll, 1)

        basic = QFrame()
        basic.setObjectName("SoftCard")
        basic.setAttribute(Qt.WA_StyledBackground, True)
        basic_layout = QGridLayout(basic)
        basic_layout.setContentsMargins(12, 10, 12, 10)
        basic_layout.setHorizontalSpacing(10)
        basic_layout.setVerticalSpacing(8)
        self.name_edit = QLineEdit(settings.name)
        self.name_edit.setMaxLength(64)
        self.seed_edit = QLineEdit(str(settings.seed))
        self.seed_edit.setToolTip("只影响新生成的区块，已经生成的地形不会改变")
        self.mode_combo = QComboBox()
        for value, label in worlds.GAME_MODES.items():
            self.mode_combo.addItem(label, value)
        self.mode_combo.setCurrentIndex(max(0, self.mode_combo.findData(settings.game_mode)))
        self.difficulty_combo = QComboBox()
        for value, label in worlds.DIFFICULTIES.items():
            self.difficulty_combo.addItem(label, value)
        self.difficulty_combo.setCurrentIndex(
            max(0, self.difficulty_combo.findData(settings.difficulty))
        )
        basic_layout.addWidget(QLabel("存档名称"), 0, 0)
        basic_layout.addWidget(self.name_edit, 0, 1)
        basic_layout.addWidget(QLabel("种子"), 1, 0)
        basic_layout.addWidget(self.seed_edit, 1, 1)
        basic_layout.addWidget(QLabel("游戏模式"), 2, 0)
        basic_layout.addWidget(self.mode_combo, 2, 1)
        basic_layout.addWidget(QLabel("难度"), 3, 0)
        basic_layout.addWidget(self.difficulty_combo, 3, 1)
        self.hardcore_box = QCheckBox("硬核模式（强制生存）")
        self.hardcore_box.setChecked(settings.hardcore)
        self.cheats_box = QCheckBox("允许作弊（开启指令）")
        self.cheats_box.setChecked(settings.cheats)
        basic_layout.addWidget(self.hardcore_box, 4, 0, 1, 2)
        basic_layout.addWidget(self.cheats_box, 5, 0, 1, 2)
        seed_note = QLabel("改种子只影响之后新生成的区块，已经走过的地方不会变。")
        seed_note.setObjectName("Tiny")
        basic_layout.addWidget(seed_note, 6, 0, 1, 2)
        layout.addWidget(basic)

        time_card = QFrame()
        time_card.setObjectName("SoftCard")
        time_card.setAttribute(Qt.WA_StyledBackground, True)
        time_layout = QVBoxLayout(time_card)
        time_layout.setContentsMargins(12, 10, 12, 10)
        time_layout.setSpacing(6)
        time_title = QLabel("时间与天气")
        time_title.setStyleSheet("font-weight: 700; font-size: 12px;")
        time_layout.addWidget(time_title)
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(QLabel("时间"))
        self.day_time = QSpinBox()
        self.day_time.setRange(0, 23999)
        self.day_time.setSingleStep(500)
        self.day_time.setValue(int(settings.day_time) % 24000)
        self.day_time.valueChanged.connect(self._update_time_label)
        row.addWidget(self.day_time)
        self.time_label = QLabel("")
        self.time_label.setObjectName("Tiny")
        row.addWidget(self.time_label)
        for text, value in (("日出", 0), ("正午", 6000), ("日落", 12000), ("午夜", 18000)):
            button = QPushButton(text)
            button.setObjectName("Soft")
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _=False, v=value: self.day_time.setValue(v))
            row.addWidget(button)
        row.addStretch(1)
        time_layout.addLayout(row)
        weather = QHBoxLayout()
        weather.setSpacing(12)
        self.rain_box = QCheckBox("下雨")
        self.rain_box.setChecked(settings.raining)
        self.thunder_box = QCheckBox("雷暴")
        self.thunder_box.setChecked(settings.thundering)
        weather.addWidget(self.rain_box)
        weather.addWidget(self.thunder_box)
        weather.addStretch(1)
        time_layout.addLayout(weather)
        layout.addWidget(time_card)

        rules_card = QFrame()
        rules_card.setObjectName("SoftCard")
        rules_card.setAttribute(Qt.WA_StyledBackground, True)
        rules_layout = QVBoxLayout(rules_card)
        rules_layout.setContentsMargins(12, 10, 12, 10)
        rules_layout.setSpacing(6)
        rules_title = QLabel("游戏规则（参考网易我的世界的设置项）")
        rules_title.setStyleSheet("font-weight: 700; font-size: 12px;")
        rules_layout.addWidget(rules_title)
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        for index, spec in enumerate(worlds.RULE_SPECS):
            value = settings.rules.get(spec.key, spec.default)
            if spec.kind == "bool":
                widget = QCheckBox(spec.label)
                widget.setChecked(bool(value))
            else:
                line = QWidget()
                line_layout = QHBoxLayout(line)
                line_layout.setContentsMargins(0, 0, 0, 0)
                line_layout.setSpacing(6)
                line_layout.addWidget(QLabel(spec.label))
                widget = QSpinBox()
                widget.setRange(spec.minimum, spec.maximum)
                widget.setValue(int(value))
                line_layout.addWidget(widget)
                line_layout.addStretch(1)
                widget = line
            self._rule_widgets[spec.key] = widget
            grid.addWidget(widget, index % 10, index // 10)
        rules_layout.addLayout(grid)
        layout.addWidget(rules_card)
        layout.addStretch(1)

        self.error = QLabel("")
        self.error.setStyleSheet(f"color: {colors()['red']}; font-size: 11px;")
        self.error.setVisible(False)
        self.body.addWidget(self.error)

        self.add_button("打开存档文件夹", "Secondary", lambda: self.main_open())
        self.add_button("取消", "Secondary", self.reject)
        self.add_button("保存修改", "Primary", self._save)
        self._update_time_label()

    def main_open(self) -> None:
        opener = getattr(self.main_window, "open_path", None)
        if callable(opener):
            opener(self.folder)

    def _update_time_label(self) -> None:
        ticks = int(self.day_time.value())
        hours = (ticks // 1000 + 6) % 24
        minutes = int((ticks % 1000) * 60 / 1000)
        self.time_label.setText(f"约 {hours:02d}:{minutes:02d}")

    def _rule_value(self, spec: worlds.RuleSpec):
        widget = self._rule_widgets[spec.key]
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        spin = widget.findChild(QSpinBox)
        return spin.value() if spin is not None else spec.default

    def _save(self) -> None:
        if self.settings is None:
            self.reject()
            return
        try:
            seed = int(self.seed_edit.text().strip())
        except ValueError:
            self._fail("种子必须是 64 位整数（可以点复制种子后修改）")
            return
        if not -(1 << 63) <= seed < (1 << 63):
            self._fail("种子超出 64 位整数范围")
            return
        if self.game_running:
            confirm = ConfirmDialog(
                self,
                "游戏正在运行",
                "现在保存可能会在退出游戏时被覆盖，仍然保存吗？",
                "仍然保存",
                "取消",
                details="建议先退出游戏，再修改存档设置。",
            )
            if confirm.exec() != QDialog.Accepted:
                return
        settings = worlds.WorldSettings(
            name=self.name_edit.text(),
            seed=seed,
            game_mode=int(self.mode_combo.currentData() or 0),
            difficulty=int(self.difficulty_combo.currentData() or 2),
            hardcore=self.hardcore_box.isChecked(),
            cheats=self.cheats_box.isChecked(),
            day_time=int(self.day_time.value()),
            raining=self.rain_box.isChecked(),
            thundering=self.thunder_box.isChecked(),
            rules={spec.key: self._rule_value(spec) for spec in worlds.RULE_SPECS},
        )
        try:
            worlds.apply_world_settings(self.folder, settings)
        except (OSError, ValueError) as error:
            self._fail(f"保存失败：{error}")
            return
        self.accept()

    def _fail(self, message: str) -> None:
        self.error.setText(message)
        self.error.setVisible(True)


class ServerEditDialog(StyledDialog):
    def __init__(
        self,
        parent: QWidget | None,
        entry: worlds.ServerEntry | None = None,
    ) -> None:
        self.original = entry
        super().__init__(
            parent,
            "编辑服务器" if entry is not None else "添加服务器",
            "server",
            width=500,
        )
        self.entry: worlds.ServerEntry | None = None

        intro = QLabel(
            "会写入当前版本的 servers.dat，对应游戏里的「多人游戏」列表。"
        )
        intro.setObjectName("DialogText")
        intro.setWordWrap(True)
        self.body.addWidget(intro)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        self.name_edit = QLineEdit(entry.name if entry else "")
        self.name_edit.setPlaceholderText("服务器名称")
        self.name_edit.setMaxLength(64)
        self.address_edit = QLineEdit(entry.address if entry else "")
        self.address_edit.setPlaceholderText("例如 mc.example.com 或 127.0.0.1:25565")
        self.address_edit.setMaxLength(255)
        grid.addWidget(QLabel("名称"), 0, 0)
        grid.addWidget(self.name_edit, 0, 1)
        grid.addWidget(QLabel("地址"), 1, 0)
        grid.addWidget(self.address_edit, 1, 1)
        self.body.addLayout(grid)

        self.textures_box = QCheckBox("接收服务器资源包")
        self.textures_box.setChecked(entry.accept_textures if entry else True)
        self.hidden_box = QCheckBox("在列表中隐藏")
        self.hidden_box.setChecked(entry.hidden if entry else False)
        self.body.addWidget(self.textures_box)
        self.body.addWidget(self.hidden_box)

        self.error = QLabel("")
        self.error.setStyleSheet(f"color: {colors()['red']}; font-size: 11px;")
        self.error.setVisible(False)
        self.body.addWidget(self.error)

        self.add_button("取消", "Secondary", self.reject)
        self.add_button("保存", "Primary", self._save)
        self.name_edit.setFocus()

    def _save(self) -> None:
        try:
            name, address = worlds.validate_server(
                self.name_edit.text(), self.address_edit.text()
            )
        except ValueError as error:
            self.error.setText(str(error))
            self.error.setVisible(True)
            return
        raw = dict(self.original.raw) if self.original is not None else {}
        self.entry = worlds.ServerEntry(
            name=name,
            address=address,
            icon=self.original.icon if self.original is not None else "",
            accept_textures=self.textures_box.isChecked(),
            hidden=self.hidden_box.isChecked(),
            raw=raw,
        )
        self.accept()
