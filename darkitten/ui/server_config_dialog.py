from __future__ import annotations

import html
import re
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..server_config import (
    CATEGORY_ORDER,
    SERVER_PROPERTY_SPECS,
    PluginConfigFile,
    describe_config,
    explain_config_key,
    key_at_line,
    normalise_bool,
    property_rows,
    read_config_text,
    read_server_properties,
    save_server_properties,
    scan_plugin_configs,
    validate_config_text,
    validate_property,
    write_text_atomic,
)
from ..servers import ServerInfo
from . import icons
from .dialogs import ConfirmDialog, StyledDialog
from .widgets import (
    Card,
    SegmentedControl,
    Switch,
    badge,
    colors,
    hline,
    icon_button,
    repolish,
)


def _shorten(value: str, limit: int = 90) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


class ServerConfigDialog(StyledDialog):
    """在启动器内编辑 server.properties 与插件/Mod 配置文件。"""

    saved = Signal()

    def __init__(
        self,
        parent: QWidget | None,
        info: ServerInfo,
        *,
        running: bool = False,
    ) -> None:
        super().__init__(parent, "服务端配置文件", "settings", width=1160)
        self.info = info
        self.running = bool(running)
        self._property_bindings: list[tuple[object, QWidget]] = []
        self._property_row_data: list[tuple[QWidget, str, str]] = []
        self._property_dirty = False
        self._plugin_dirty = False
        self._current_config: PluginConfigFile | None = None
        self._config_map: dict[str, PluginConfigFile] = {}
        self._ignore_plugin_text = False
        self._previous_plugin_item: QListWidgetItem | None = None
        self._build()
        self._load_properties()
        self._scan_configs()
        self._fit_height()

    # ------------------------------------------------------------------
    def _fit_height(self) -> None:
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry().height() if screen else 800
        self.setFixedHeight(max(600, min(760, available - 70)))

    def _toast(self, text: str, kind: str = "info") -> None:
        parent = self.parent()
        toast = getattr(parent, "toast", None)
        if callable(toast):
            toast(text, kind)

    # ------------------------------------------------------------------
    def _build(self) -> None:
        summary = QFrame()
        summary.setObjectName("SoftCard")
        summary.setAttribute(Qt.WA_StyledBackground, True)
        box = QVBoxLayout(summary)
        box.setContentsMargins(12, 9, 12, 9)
        box.setSpacing(3)
        title = QLabel(f"{self.info.name} · {self.info.type_name} {self.info.mc_version}")
        title.setStyleSheet("font-weight: 700;")
        box.addWidget(title)
        path = QLabel(str(self.info.path))
        path.setObjectName("Tiny")
        path.setWordWrap(True)
        path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        box.addWidget(path)
        if self.running:
            warning = QLabel("服务端正在运行：保存不会热重载，需要重启服务端后生效。")
            warning.setStyleSheet(f"color: {colors()['orange']}; font-size: 11px;")
            warning.setWordWrap(True)
            box.addWidget(warning)
        self.body.addWidget(summary)

        self.tabs = SegmentedControl()
        self.tabs.add_item("server.properties", "properties")
        self.tabs.add_item("插件 / Mod 配置", "plugins")
        self.tabs.changed.connect(self._on_tab_changed)
        self.body.addWidget(self.tabs)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_properties_page())
        self.stack.addWidget(self._build_plugins_page())
        self.stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.body.addWidget(self.stack, 1)

        self.add_button("关闭", "Secondary", self.reject)

    # ------------------------------------------------------------------
    def _build_properties_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.property_search = QLineEdit()
        self.property_search.setPlaceholderText("搜索配置项、中文名称或说明…")
        self.property_search.setClearButtonEnabled(True)
        self.property_search.setMinimumWidth(260)
        self.property_search.textChanged.connect(self._filter_properties)
        top.addWidget(self.property_search, 1)
        self.property_category = QComboBox()
        self.property_category.setMinimumWidth(130)
        self.property_category.currentIndexChanged.connect(self._filter_properties)
        top.addWidget(self.property_category)
        self.all_switch = Switch()
        self.all_switch.setToolTip("显示 server.properties 支持的全部配置项")
        self.all_switch.toggled.connect(self._toggle_all_properties)
        top.addWidget(self.all_switch)
        all_label = QLabel("显示全部")
        all_label.setObjectName("Tiny")
        top.addWidget(all_label)
        reload_btn = QPushButton("重新读取")
        reload_btn.setObjectName("Ghost")
        reload_btn.setCursor(Qt.PointingHandCursor)
        reload_btn.setIcon(icons.icon("refresh", colors()["text2"], 14))
        reload_btn.clicked.connect(self._reload_properties)
        top.addWidget(reload_btn)
        layout.addLayout(top)

        hint = QLabel(
            "左侧中文名称和说明对应 server.properties 里的英文键。"
            "修改后会自动同步端口和正版验证设置，并保留原文件备份。"
        )
        hint.setObjectName("Tiny")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.property_scroll = QScrollArea()
        self.property_scroll.setWidgetResizable(True)
        self.property_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.property_holder = QWidget()
        self.property_layout = QVBoxLayout(self.property_holder)
        self.property_layout.setContentsMargins(0, 0, 4, 0)
        self.property_layout.setSpacing(6)
        self.property_layout.addStretch(1)
        self.property_scroll.setWidget(self.property_holder)
        layout.addWidget(self.property_scroll, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.property_status = QLabel("")
        self.property_status.setObjectName("Tiny")
        self.property_status.setWordWrap(True)
        bottom.addWidget(self.property_status, 1)
        self.property_count = QLabel("")
        self.property_count.setObjectName("Tiny")
        bottom.addWidget(self.property_count)
        self.property_save_btn = QPushButton("保存 server.properties")
        self.property_save_btn.setObjectName("Primary")
        self.property_save_btn.setCursor(Qt.PointingHandCursor)
        self.property_save_btn.clicked.connect(self._save_properties)
        bottom.addWidget(self.property_save_btn)
        layout.addLayout(bottom)
        return page

    def _build_plugins_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 6, 0)
        left_layout.setSpacing(6)
        self.config_search = QLineEdit()
        self.config_search.setPlaceholderText("搜索文件或插件名…")
        self.config_search.setClearButtonEnabled(True)
        self.config_search.textChanged.connect(self._filter_configs)
        left_layout.addWidget(self.config_search)
        self.config_list = QListWidget()
        self.config_list.setMinimumWidth(245)
        self.config_list.itemSelectionChanged.connect(self._on_config_selected)
        left_layout.addWidget(self.config_list, 1)
        self.config_count = QLabel("")
        self.config_count.setObjectName("Tiny")
        left_layout.addWidget(self.config_count)
        split.addWidget(left)

        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 6, 0)
        center_layout.setSpacing(5)
        header = QHBoxLayout()
        header.setSpacing(6)
        self.config_title = QLabel("选择左侧配置文件")
        self.config_title.setStyleSheet("font-weight: 700;")
        self.config_title.setWordWrap(True)
        header.addWidget(self.config_title, 1)
        self.config_format = badge("", "gray")
        header.addWidget(self.config_format)
        open_btn = icon_button("folder", "打开配置文件所在文件夹", size=28, icon_size=14)
        open_btn.clicked.connect(self._open_config_folder)
        header.addWidget(open_btn)
        center_layout.addLayout(header)
        self.config_path_label = QLabel("")
        self.config_path_label.setObjectName("Tiny")
        self.config_path_label.setWordWrap(True)
        self.config_path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        center_layout.addWidget(self.config_path_label)

        self.config_editor = QPlainTextEdit()
        self.config_editor.setPlaceholderText("选择左侧文件后在这里编辑…")
        editor_font = QFont("Consolas")
        editor_font.setStyleHint(QFont.Monospace)
        editor_font.setPointSize(9)
        self.config_editor.setFont(editor_font)
        self.config_editor.setTabStopDistance(32)
        self.config_editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.config_editor.textChanged.connect(self._on_config_text_changed)
        self.config_editor.cursorPositionChanged.connect(self._update_cursor_hint)
        center_layout.addWidget(self.config_editor, 1)

        editor_bottom = QHBoxLayout()
        editor_bottom.setSpacing(8)
        self.config_status = QLabel("")
        self.config_status.setObjectName("Tiny")
        self.config_status.setWordWrap(True)
        editor_bottom.addWidget(self.config_status, 1)
        reload_btn = QPushButton("重新读取")
        reload_btn.setObjectName("Ghost")
        reload_btn.setCursor(Qt.PointingHandCursor)
        reload_btn.setIcon(icons.icon("refresh", colors()["text2"], 14))
        reload_btn.clicked.connect(self._reload_config)
        editor_bottom.addWidget(reload_btn)
        self.config_save_btn = QPushButton("保存当前文件")
        self.config_save_btn.setObjectName("Primary")
        self.config_save_btn.setCursor(Qt.PointingHandCursor)
        self.config_save_btn.clicked.connect(self._save_config)
        editor_bottom.addWidget(self.config_save_btn)
        center_layout.addLayout(editor_bottom)
        split.addWidget(center)

        right = Card()
        right.setMinimumWidth(260)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(12, 10, 12, 10)
        right_layout.setSpacing(6)
        hint_title = QLabel("键名解释")
        hint_title.setObjectName("H2")
        right_layout.addWidget(hint_title)
        self.cursor_hint = QLabel("把光标放到某一行，可以查看这个键的中文说明。")
        self.cursor_hint.setObjectName("Tiny")
        self.cursor_hint.setWordWrap(True)
        right_layout.addWidget(self.cursor_hint)
        right_layout.addWidget(hline())

        self.hint_scroll = QScrollArea()
        self.hint_scroll.setWidgetResizable(True)
        self.hint_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.hint_holder = QWidget()
        self.hint_layout = QVBoxLayout(self.hint_holder)
        self.hint_layout.setContentsMargins(0, 0, 4, 0)
        self.hint_layout.setSpacing(7)
        self.hint_layout.addStretch(1)
        self.hint_scroll.setWidget(self.hint_holder)
        right_layout.addWidget(self.hint_scroll, 1)
        split.addWidget(right)

        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setStretchFactor(2, 0)
        split.setSizes([255, 660, 300])
        layout.addWidget(split, 1)

        note = QLabel(
            "支持 YAML、JSON、TOML、properties 和文本配置。"
            "保存前会校验可解析格式，并在同目录保留一份 .bak 备份。"
        )
        note.setObjectName("Tiny")
        note.setWordWrap(True)
        layout.addWidget(note)
        return page

    # ------------------------------------------------------------------
    def _on_tab_changed(self, value: str) -> None:
        self.stack.setCurrentIndex(0 if value == "properties" else 1)

    # ------------------------------------------------------------------
    # server.properties
    def _clear_property_rows(self) -> None:
        while self.property_layout.count():
            item = self.property_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._property_bindings = []
        self._property_row_data = []

    def _load_properties(self) -> None:
        self._clear_property_rows()
        path = self.info.path / "server.properties"
        values = read_server_properties(path)
        # 文件里没写端口/正版验证时，以启动器记录为准，避免写入冲突的默认值。
        values.setdefault("server-port", str(self.info.port))
        values.setdefault(
            "online-mode", "true" if self.info.online_mode else "false"
        )
        rows = property_rows(values, include_all=self.all_switch.isChecked())
        for spec, value in rows:
            if spec.key not in SERVER_PROPERTY_SPECS:
                spec = self._detect_generic_spec(spec, value)
            row, control = self._property_row(spec, value)
            self.property_layout.addWidget(row)
            self._property_bindings.append((spec, control))
            self._property_row_data.append(
                (row, f"{spec.key} {spec.label} {spec.help}".lower(), spec.category)
            )
        self.property_layout.addStretch(1)

        categories: list[str] = []
        for spec, _ in self._property_bindings:
            if spec.category not in categories:
                categories.append(spec.category)
        categories.sort(
            key=lambda name: CATEGORY_ORDER.index(name)
            if name in CATEGORY_ORDER
            else 99
        )
        self.property_category.blockSignals(True)
        self.property_category.clear()
        self.property_category.addItem("全部分类", "")
        for category in categories:
            self.property_category.addItem(category, category)
        self.property_category.blockSignals(False)
        self._property_dirty = False
        self._set_property_status("", "")
        self._filter_properties()

    @staticmethod
    def _detect_generic_spec(spec, value: str):
        raw = str(value or "").strip()
        lowered = raw.lower()
        if lowered in {"true", "false"}:
            return replace(spec, kind="bool", default=lowered)
        if re.fullmatch(r"-?\d{1,9}", raw):
            return replace(
                spec,
                kind="int",
                default="0",
                minimum=-2147483648,
                maximum=2147483647,
            )
        return spec

    def _property_row(self, spec, value: str) -> tuple[QWidget, QWidget]:
        row = QFrame()
        row.setObjectName("SoftCard")
        row.setAttribute(Qt.WA_StyledBackground, True)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(11, 8, 11, 8)
        layout.setSpacing(12)

        texts = QVBoxLayout()
        texts.setSpacing(2)
        title = QLabel(spec.label)
        title.setStyleSheet("font-weight: 700; font-size: 12px;")
        title.setWordWrap(True)
        texts.addWidget(title)
        meta = QLabel(f"{spec.key} · {spec.category}")
        meta.setObjectName("Tiny")
        meta.setWordWrap(True)
        texts.addWidget(meta)
        help_label = QLabel(spec.help)
        help_label.setObjectName("Tiny")
        help_label.setWordWrap(True)
        texts.addWidget(help_label)
        layout.addLayout(texts, 1)

        control = self._property_control(spec, value)
        control.setToolTip(spec.help)
        layout.addWidget(control, 0, Qt.AlignVCenter)
        return row, control

    def _property_control(self, spec, value: str) -> QWidget:
        raw = str(value or "")
        if spec.kind == "bool":
            lowered = raw.strip().lower()
            if lowered in {"true", "false"}:
                switch = Switch()
                switch.setChecked(lowered == "true")
                switch.toggled.connect(self._mark_properties_dirty)
                return switch
            combo = QComboBox()
            combo.setFixedWidth(180)
            for text, data in (("开启", "true"), ("关闭", "false")):
                combo.addItem(text, data)
            combo.addItem(f"当前值：{raw or '空'}", raw)
            combo.setCurrentIndex(2)
            combo.currentIndexChanged.connect(self._mark_properties_dirty)
            return combo
        if spec.kind == "int":
            try:
                number = int(raw)
            except (TypeError, ValueError):
                edit = QLineEdit(raw)
                edit.setFixedWidth(300)
                edit.setPlaceholderText("需要整数")
                edit.textChanged.connect(self._mark_properties_dirty)
                return edit
            spin = QSpinBox()
            spin.setFixedWidth(180)
            spin.setRange(
                spec.minimum if spec.minimum is not None else -2147483648,
                spec.maximum if spec.maximum is not None else 2147483647,
            )
            if spec.minimum is None and spec.maximum is None:
                spin.setSingleStep(1)
            spin.setValue(number)
            spin.valueChanged.connect(self._mark_properties_dirty)
            return spin
        if spec.kind == "choice":
            combo = QComboBox()
            combo.setFixedWidth(220)
            for choice in spec.choices:
                combo.addItem(choice, choice)
            index = combo.findData(raw)
            if index < 0:
                combo.addItem(f"当前值：{raw or '空'}", raw)
                index = combo.count() - 1
            combo.setCurrentIndex(index)
            combo.currentIndexChanged.connect(self._mark_properties_dirty)
            return combo
        edit = QLineEdit(raw)
        edit.setFixedWidth(360)
        edit.setMaxLength(4096)
        edit.setClearButtonEnabled(True)
        edit.textChanged.connect(self._mark_properties_dirty)
        return edit

    @staticmethod
    def _control_value(control: QWidget) -> str:
        if isinstance(control, Switch):
            return "true" if control.isChecked() else "false"
        if isinstance(control, QSpinBox):
            return str(control.value())
        if isinstance(control, QComboBox):
            data = control.currentData()
            return str(data if data is not None else control.currentText())
        if isinstance(control, QLineEdit):
            return control.text()
        return ""

    def _mark_properties_dirty(self, *_args) -> None:
        self._property_dirty = True
        self._set_property_status("有未保存的修改。", colors()["orange"])

    def _set_property_status(self, text: str, color: str = "") -> None:
        self.property_status.setText(text)
        self.property_status.setStyleSheet(
            f"color: {color or colors()['muted']}; font-size: 11px;"
        )

    def _filter_properties(self, *_args) -> None:
        query = self.property_search.text().strip().lower()
        category = str(self.property_category.currentData() or "")
        visible = 0
        for row, haystack, row_category in self._property_row_data:
            match = (not query or query in haystack) and (
                not category or category == row_category
            )
            row.setVisible(match)
            if match:
                visible += 1
        self.property_count.setText(f"显示 {visible} / {len(self._property_row_data)} 项")

    def _toggle_all_properties(self, checked: bool) -> None:
        if self._property_dirty and not self._confirm_discard(
            "server.properties 还有未保存的修改，切换显示范围会丢弃这些修改。"
        ):
            self.all_switch.blockSignals(True)
            self.all_switch.setChecked(not checked)
            self.all_switch.blockSignals(False)
            return
        self._load_properties()

    def _reload_properties(self) -> None:
        if self._property_dirty and not self._confirm_discard(
            "server.properties 还有未保存的修改，重新读取会丢弃这些修改。"
        ):
            return
        self._load_properties()
        self._toast("已重新读取 server.properties", "info")

    def _save_properties(self) -> None:
        values: dict[str, str] = {}
        errors: list[str] = []
        focus_widget: QWidget | None = None
        for spec, control in self._property_bindings:
            raw = self._control_value(control)
            normalized, error = validate_property(spec, raw)
            values[spec.key] = normalized
            if error:
                errors.append(error)
                if focus_widget is None:
                    focus_widget = control
        if errors:
            self._set_property_status(errors[0], colors()["red"])
            if focus_widget is not None:
                focus_widget.setFocus()
            return
        try:
            save_server_properties(self.info, values)
        except Exception as exc:
            self._set_property_status(f"保存失败：{exc}", colors()["red"])
            return
        self._property_dirty = False
        self._set_property_status(
            "已保存 server.properties；原文件备份为 server.properties.bak。",
            colors()["green"],
        )
        self._toast("server.properties 已保存", "success")
        self.saved.emit()

    # ------------------------------------------------------------------
    # 插件 / Mod 配置
    def _scan_configs(self) -> None:
        configs = scan_plugin_configs(self.info)
        self._config_map = {item.relative: item for item in configs}
        self.config_list.blockSignals(True)
        self.config_list.clear()
        for item in configs:
            entry = QListWidgetItem(f"{item.relative}")
            entry.setData(Qt.UserRole, item.relative)
            entry.setToolTip(
                f"{item.path}\n{item.format} · {item.display_size}"
            )
            self.config_list.addItem(entry)
        self.config_list.blockSignals(False)
        self.config_count.setText(f"{len(configs)} 个文件")
        if configs:
            self.config_list.setCurrentRow(0)
        else:
            self._current_config = None
            self.config_title.setText("没有找到可编辑的配置文件")
            self.config_path_label.setText(
                "启动一次服务端或安装插件后，plugins/ 与 config/ 里的配置文件会显示在这里。"
            )
            self.config_format.setText("—")
            self.config_format.setProperty("badge", "gray")
            repolish(self.config_format)
            self.config_editor.clear()
            self.config_editor.setEnabled(False)
            self.config_save_btn.setEnabled(False)
            self._rebuild_hints([])
        self._filter_configs()

    def _filter_configs(self, *_args) -> None:
        query = self.config_search.text().strip().lower()
        visible = 0
        for index in range(self.config_list.count()):
            item = self.config_list.item(index)
            match = not query or query in item.text().lower()
            item.setHidden(not match)
            if match:
                visible += 1
        self.config_count.setText(f"{visible} / {self.config_list.count()} 个文件")

    def _current_plugin(self) -> PluginConfigFile | None:
        item = self.config_list.currentItem()
        if item is None:
            return None
        return self._config_map.get(str(item.data(Qt.UserRole) or ""))

    def _on_config_selected(self) -> None:
        selected = self._current_plugin()
        if selected is None:
            return
        previous = self._current_config
        if self._plugin_dirty and previous is not None and selected.relative != previous.relative:
            if not self._confirm_discard(
                f"{previous.relative} 还有未保存的修改，切换文件会丢弃这些修改。"
            ):
                self.config_list.blockSignals(True)
                for index in range(self.config_list.count()):
                    item = self.config_list.item(index)
                    if str(item.data(Qt.UserRole) or "") == previous.relative:
                        self.config_list.setCurrentItem(item)
                        break
                self.config_list.blockSignals(False)
                return
        self._load_config_file(selected)

    def _load_config_file(self, entry: PluginConfigFile) -> None:
        try:
            text = read_config_text(entry.path)
        except Exception as exc:
            self._set_config_status(f"读取失败：{exc}", colors()["red"])
            return
        self._current_config = entry
        self._ignore_plugin_text = True
        self.config_editor.setPlainText(text)
        self._ignore_plugin_text = False
        self.config_editor.setEnabled(True)
        self.config_save_btn.setEnabled(True)
        self._plugin_dirty = False
        self.config_title.setText(entry.relative)
        self.config_path_label.setText(str(entry.path))
        self.config_format.setText(entry.format)
        self.config_format.setProperty("badge", "blue")
        repolish(self.config_format)
        self._set_config_status(
            f"{entry.format} · {entry.display_size} · 保存时保留 .bak 备份",
            colors()["muted"],
        )
        self._rebuild_hints(describe_config(text, entry.path.suffix))
        self._update_cursor_hint()
        self.config_editor.setFocus()

    def _on_config_text_changed(self) -> None:
        if self._ignore_plugin_text or self._current_config is None:
            return
        self._plugin_dirty = True
        self._set_config_status("有未保存的修改。", colors()["orange"])

    def _set_config_status(self, text: str, color: str = "") -> None:
        self.config_status.setText(text)
        self.config_status.setStyleSheet(
            f"color: {color or colors()['muted']}; font-size: 11px;"
        )

    def _clear_hints(self) -> None:
        while self.hint_layout.count():
            item = self.hint_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self.hint_layout.addStretch(1)

    def _rebuild_hints(self, hints) -> None:
        self._clear_hints()
        if not hints:
            label = QLabel("没有解析到顶层配置键；仍然可以直接编辑文本。")
            label.setObjectName("Tiny")
            label.setWordWrap(True)
            self.hint_layout.insertWidget(0, label)
            return
        muted = colors()["muted"]
        for hint in hints[:40]:
            value = _shorten(hint.value, 72)
            value_html = (
                f"<br><span style='color:{muted}; font-size:10px;'>"
                f"当前值：{html.escape(value)}</span>"
                if value
                else ""
            )
            label = QLabel(
                f"<b>{html.escape(hint.key)}</b>{value_html}"
                f"<br><span style='font-size:10px;'>{html.escape(hint.note)}</span>"
            )
            label.setTextFormat(Qt.RichText)
            label.setWordWrap(True)
            label.setContentsMargins(0, 0, 0, 4)
            self.hint_layout.insertWidget(self.hint_layout.count() - 1, label)

    def _update_cursor_hint(self) -> None:
        if self._current_config is None:
            return
        cursor = self.config_editor.textCursor()
        key = key_at_line(self.config_editor.toPlainText(), cursor.blockNumber() + 1)
        if not key:
            self.cursor_hint.setText(
                f"第 {cursor.blockNumber() + 1} 行：把光标放到配置键所在行可查看解释。"
            )
            return
        note = explain_config_key(key)
        self.cursor_hint.setText(
            f"第 {cursor.blockNumber() + 1} 行 · {key}\n{note}"
        )

    def _reload_config(self) -> None:
        entry = self._current_config
        if entry is None:
            return
        if self._plugin_dirty and not self._confirm_discard(
            f"{entry.relative} 还有未保存的修改，重新读取会丢弃这些修改。"
        ):
            return
        self._load_config_file(entry)
        self._toast("配置文件已重新读取", "info")

    def _save_config(self) -> None:
        entry = self._current_config
        if entry is None:
            return
        text = self.config_editor.toPlainText()
        error = validate_config_text(text, entry.path.suffix)
        if error:
            self._set_config_status(error, colors()["red"])
            return
        try:
            write_text_atomic(entry.path, text)
        except Exception as exc:
            self._set_config_status(f"保存失败：{exc}", colors()["red"])
            return
        self._plugin_dirty = False
        self._set_config_status(
            f"已保存 {entry.relative}；原文件备份为 {entry.path.name}.bak。",
            colors()["green"],
        )
        self._toast(f"{entry.relative} 已保存", "success")
        self.saved.emit()

    def _open_config_folder(self) -> None:
        entry = self._current_config
        path = entry.path.parent if entry is not None else self.info.path
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    # ------------------------------------------------------------------
    def _confirm_discard(self, message: str) -> bool:
        dialog = ConfirmDialog(
            self,
            "放弃未保存的修改",
            message,
            primary_text="放弃修改",
            secondary_text="继续编辑",
            icon_name="warning",
            icon_color=colors()["orange"],
        )
        return bool(dialog.exec())

    def reject(self) -> None:
        if self._property_dirty or self._plugin_dirty:
            dialog = ConfirmDialog(
                self,
                "还有未保存的修改",
                "关闭窗口会丢弃尚未保存的配置修改。",
                primary_text="放弃并关闭",
                secondary_text="继续编辑",
                icon_name="warning",
                icon_color=colors()["orange"],
            )
            if not dialog.exec():
                return
        super().reject()
