from __future__ import annotations

import html
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QStandardItemModel
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ... import chunkbase_registry as registry
from ... import game_assistant as ga
from ...java import pick_java
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

MC_VERSIONS = (
    "1.21.9",
    "1.21.4",
    "1.21.1",
    "1.20.6",
    "1.20.4",
    "1.20.1",
    "1.19.4",
    "1.18.2",
    "1.17.1",
    "1.16.5",
)

TAB_ORDER = ("seed", "commands", "enchant", "wiki", "tools")

CATEGORY_LABELS = {
    "全部": "全部",
    "基础": "基础",
    "物品": "物品",
    "传送": "传送",
    "世界": "世界",
    "实体": "实体",
    "计分板": "计分板",
    "进阶": "进阶",
    "显示": "显示",
}

QUICK_IDS: dict[str, tuple[tuple[str, str], ...]] = {
    "常用附魔": (
        ("锋利", "minecraft:sharpness"),
        ("保护", "minecraft:protection"),
        ("效率", "minecraft:efficiency"),
        ("时运", "minecraft:fortune"),
        ("经验修补", "minecraft:mending"),
        ("抢夺", "minecraft:looting"),
        ("耐久", "minecraft:unbreaking"),
        ("力量", "minecraft:power"),
        ("无限", "minecraft:infinity"),
        ("冰霜行者", "minecraft:frost_walker"),
    ),
    "常用物品": (
        ("钻石剑", "minecraft:diamond_sword"),
        ("下界合金剑", "minecraft:netherite_sword"),
        ("金苹果", "minecraft:golden_apple"),
        ("附魔金苹果", "minecraft:enchanted_golden_apple"),
        ("鞘翅", "minecraft:elytra"),
        ("不死图腾", "minecraft:totem_of_undying"),
        ("末影珍珠", "minecraft:ender_pearl"),
        ("命名牌", "minecraft:name_tag"),
    ),
    "常用实体": (
        ("僵尸", "minecraft:zombie"),
        ("骷髅", "minecraft:skeleton"),
        ("苦力怕", "minecraft:creeper"),
        ("末影人", "minecraft:enderman"),
        ("村民", "minecraft:villager"),
        ("铁傀儡", "minecraft:iron_golem"),
        ("凋灵", "minecraft:wither"),
        ("末影龙", "minecraft:ender_dragon"),
    ),
    "常用粒子": (
        ("火焰", "minecraft:flame"),
        ("末地烛", "minecraft:end_rod"),
        ("爱心", "minecraft:heart"),
        ("音符", "minecraft:note"),
        ("烟雾", "minecraft:smoke"),
        ("爆炸", "minecraft:explosion"),
        ("龙息", "minecraft:dragon_breath"),
        ("闪光", "minecraft:flash"),
    ),
}


# ---------------------------------------------------------------------------
# 后台任务


class AIWorker(QObject):
    finished = Signal(str)
    failed = Signal(str)

    def __init__(self, request: str, config: ga.AIConfig, version: str, context: str, parent=None) -> None:
        super().__init__(parent)
        self.request = request
        self.config = config
        self.version = version
        self.context = context

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            self.finished.emit(
                ga.ai_generate_commands(self.request, self.config, self.version, self.context)
            )
        except Exception as error:  # noqa: BLE001 - 交给界面展示
            self.failed.emit(str(error))


class MCModWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, query: str, parent=None) -> None:
        super().__init__(parent)
        self.query = query

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            self.finished.emit(ga.search_mcmod(self.query, limit=12))
        except Exception as error:  # noqa: BLE001
            self.failed.emit(str(error))


class VersionRegistryWorker(QObject):
    """后台读取 Chunkbase 的版本列表，避免卡住界面。"""

    done = Signal(object, str, str)

    def __init__(self, force: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.force = force

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            groups, source = registry.load_groups(force=self.force)
            self.done.emit(groups, source, "")
        except Exception as error:  # noqa: BLE001
            self.done.emit(None, "", str(error))


# ---------------------------------------------------------------------------
# 小部件


class MCModRow(QFrame):
    clicked = Signal(str)

    def __init__(self, result: ga.MCModResult, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("SelectRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.PointingHandCursor)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(2)
        title = QLabel(result.title)
        title.setStyleSheet("font-weight: 700; font-size: 12px;")
        title.setWordWrap(True)
        summary = QLabel(result.summary or result.url)
        summary.setObjectName("Tiny")
        summary.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(summary)
        transparent_children(self)
        self._url = result.url

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self._url)
        super().mouseReleaseEvent(event)


class CommandRow(QFrame):
    clicked = Signal(object)

    def __init__(self, spec: ga.CommandSpec, parent=None) -> None:
        super().__init__(parent)
        self.spec = spec
        self.setObjectName("SelectRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.PointingHandCursor)
        self.setProperty("selected", "false")
        self.setMinimumHeight(48)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)
        layout.setSpacing(1)
        top = QHBoxLayout()
        top.setSpacing(6)
        name = QLabel(spec.name)
        name.setStyleSheet("font-weight: 700; font-size: 12px;")
        top.addWidget(name)
        top.addWidget(badge(spec.category, "blue"))
        top.addStretch(1)
        top.addWidget(badge("/" + spec.name, "gray"))
        layout.addLayout(top)
        usage = QLabel(spec.summary)
        usage.setObjectName("Tiny")
        usage.setWordWrap(False)
        layout.addWidget(usage)
        transparent_children(self)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", "true" if selected else "false")
        repolish(self)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.spec)
        super().mouseReleaseEvent(event)


class AIConfigDialog(QDialog):
    def __init__(self, config: ga.AIConfig, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("AI 指令接口")
        self.setModal(True)
        self.setMinimumWidth(500)
        self.saved: ga.AIConfig | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)

        title = QLabel("配置 OpenAI 兼容接口")
        title.setObjectName("H2")
        layout.addWidget(title)
        tip = QLabel(
            "填写兼容 Chat Completions 的地址即可。API Key 只保存在本机 assistant.json，"
            "不会写进日志，也不会随启动器更新上传。"
        )
        tip.setObjectName("Tiny")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        self.base = QLineEdit(config.base_url)
        self.base.setPlaceholderText("https://api.openai.com/v1")
        self.model = QLineEdit(config.model)
        self.model.setPlaceholderText("gpt-4o-mini / qwen-plus / deepseek-chat")
        self.key = QLineEdit(config.api_key)
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText("本地 Ollama / LM Studio 可以留空")
        self.temperature = QDoubleSpinBox()
        self.temperature.setRange(0.0, 2.0)
        self.temperature.setSingleStep(0.1)
        self.temperature.setValue(float(config.temperature))
        self.timeout = QSpinBox()
        self.timeout.setRange(10, 600)
        self.timeout.setValue(int(config.timeout))
        self.timeout.setSuffix(" 秒")

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        grid.addWidget(QLabel("接口地址"), 0, 0)
        grid.addWidget(self.base, 0, 1, 1, 3)
        grid.addWidget(QLabel("模型"), 1, 0)
        grid.addWidget(self.model, 1, 1, 1, 3)
        grid.addWidget(QLabel("API Key"), 2, 0)
        grid.addWidget(self.key, 2, 1, 1, 3)
        grid.addWidget(QLabel("温度"), 3, 0)
        grid.addWidget(self.temperature, 3, 1)
        grid.addWidget(QLabel("超时"), 3, 2)
        grid.addWidget(self.timeout, 3, 3)
        layout.addLayout(grid)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setObjectName("Secondary")
        cancel.clicked.connect(self.reject)
        save = QPushButton("保存")
        save.setObjectName("Primary")
        save.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)

    def _save(self) -> None:
        base = self.base.text().strip()
        model = self.model.text().strip()
        if not base:
            QMessageBox.warning(self, "缺少接口地址", "请填写 OpenAI 兼容接口的地址。")
            return
        if not model:
            QMessageBox.warning(self, "缺少模型", "请填写要调用的模型名称。")
            return
        self.saved = ga.AIConfig(
            base_url=base,
            model=model,
            api_key=self.key.text().strip(),
            temperature=float(self.temperature.value()),
            timeout=int(self.timeout.value()),
        )
        self.accept()


class DatapackDialog(QDialog):
    def __init__(self, version: str = "1.21.4", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("导出数据包")
        self.setModal(True)
        self.setMinimumWidth(430)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)
        title = QLabel("打包为数据包")
        title.setObjectName("H2")
        layout.addWidget(title)
        tip = QLabel(
            "会生成 pack.mcmeta 和 data/<命名空间>/function/*.mcfunction。"
            "1.21+ 使用单数 function 目录，旧版本使用 functions。"
        )
        tip.setObjectName("Tiny")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        self.name = QLineEdit("main")
        self.namespace = QLineEdit("dkl")
        self.version = QComboBox()
        for item in MC_VERSIONS:
            self.version.addItem(item, item)
        index = self.version.findData(version)
        if index >= 0:
            self.version.setCurrentIndex(index)
        self.load = QCheckBox("在 world load 时自动运行")
        self.tick = QCheckBox("每 tick 自动运行（谨慎）")

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        grid.addWidget(QLabel("函数名"), 0, 0)
        grid.addWidget(self.name, 0, 1)
        grid.addWidget(QLabel("命名空间"), 1, 0)
        grid.addWidget(self.namespace, 1, 1)
        grid.addWidget(QLabel("目标版本"), 2, 0)
        grid.addWidget(self.version, 2, 1)
        layout.addLayout(grid)
        layout.addWidget(self.load)
        layout.addWidget(self.tick)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setObjectName("Secondary")
        cancel.clicked.connect(self.reject)
        save = QPushButton("选择保存位置")
        save.setObjectName("Primary")
        save.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)


# ---------------------------------------------------------------------------
# 游戏助手页面


class AssistantPage(QWidget):
    def __init__(self, main, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.main = main
        self.settings = ga.load_assistant_settings()
        self._seed_info: ga.SeedInfo | None = None
        self._command_rows: list[CommandRow] = []
        self._selected_command: ga.CommandSpec | None = None
        self._ai_worker: AIWorker | None = None
        self._mcmod_worker: MCModWorker | None = None
        self._registry_worker: VersionRegistryWorker | None = None
        self._registry_manual = False
        self._version_groups, self._version_source = registry.load_groups(
            allow_network=False
        )
        self._template_widgets: list[tuple[QLabel, QLineEdit, QWidget]] = []
        self._build()
        QTimer.singleShot(600, self._auto_refresh_versions)

    # ------------------------------------------------------------------
    # 基础布局

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 18, 22, 16)
        outer.setSpacing(12)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = QLabel("猫猫的游戏助手")
        title.setObjectName("PageTitle")
        sub = QLabel(
            "查种子、看区块、生成指令、预测附魔、翻资料。全部留在启动器里，不进游戏。"
        )
        sub.setObjectName("Muted")
        sub.setWordWrap(True)
        titles.addWidget(title)
        titles.addWidget(sub)
        header.addLayout(titles, 1)
        wiki_btn = QPushButton("打开 MC百科")
        wiki_btn.setObjectName("Secondary")
        wiki_btn.setIcon(icons.icon("globe", colors()["accent"], 15))
        wiki_btn.setCursor(Qt.PointingHandCursor)
        wiki_btn.clicked.connect(lambda: self._open_url("https://www.mcmod.cn/"))
        header.addWidget(wiki_btn)
        header.addWidget(ChibiCompanion(60, "assistant"), 0, Qt.AlignTop)
        outer.addLayout(header)

        self.tabs = SegmentedControl()
        self.tabs.add_item("种子地图", "seed")
        self.tabs.add_item("指令工坊", "commands")
        self.tabs.add_item("附魔预测", "enchant")
        self.tabs.add_item("资料站", "wiki")
        self.tabs.add_item("小工具", "tools")
        self.tabs.changed.connect(self._switch_tab)
        outer.addWidget(self.tabs, 0, Qt.AlignLeft)

        self.tab_stack = QStackedWidget()
        self.tab_stack.addWidget(self._build_seed_tab())
        self.tab_stack.addWidget(self._build_command_tab())
        self.tab_stack.addWidget(self._build_enchant_tab())
        self.tab_stack.addWidget(self._build_wiki_tab())
        self.tab_stack.addWidget(self._build_tools_tab())
        outer.addWidget(self.tab_stack, 1)

    def _switch_tab(self, value: str) -> None:
        try:
            self.tab_stack.setCurrentIndex(TAB_ORDER.index(value))
        except ValueError:
            self.tab_stack.setCurrentIndex(0)

    def _scroll_tab(self) -> tuple[QScrollArea, QVBoxLayout]:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        container = QWidget()
        scroll.setWidget(container)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 2, 4, 10)
        layout.setSpacing(12)
        return scroll, layout

    def _card_header(self, layout: QVBoxLayout, title: str, subtitle: str = "") -> QHBoxLayout:
        row = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(1)
        label = QLabel(title)
        label.setObjectName("H2")
        col.addWidget(label)
        if subtitle:
            note = QLabel(subtitle)
            note.setObjectName("Tiny")
            note.setWordWrap(True)
            col.addWidget(note)
        row.addLayout(col, 1)
        layout.addLayout(row)
        return row

    def _output(self, height: int = 140, placeholder: str = "") -> QPlainTextEdit:
        edit = QPlainTextEdit()
        edit.setReadOnly(True)
        edit.setPlaceholderText(placeholder)
        edit.setFixedHeight(height)
        return edit

    def _button(self, text: str, icon: str = "", kind: str = "Secondary") -> QPushButton:
        button = QPushButton(text)
        button.setObjectName(kind)
        button.setCursor(Qt.PointingHandCursor)
        if icon:
            button.setIcon(icons.icon(icon, colors()["accent"] if kind != "Primary" else "#FFFFFF", 15))
        return button

    def _open_url(self, url: str) -> None:
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def _copy(self, text: str, message: str = "已复制到剪贴板") -> None:
        if text:
            QApplication.clipboard().setText(text)
            self.main.toast(message, "success")

    def _save_settings(self) -> None:
        try:
            ga.save_assistant_settings(self.settings)
        except OSError:
            pass

    # ------------------------------------------------------------------
    # 种子地图

    def _build_seed_tab(self) -> QWidget:
        scroll, layout = self._scroll_tab()

        layout.addWidget(self._build_seed_card())
        layout.addWidget(self._build_slime_card())
        layout.addWidget(self._build_structure_card())
        layout.addWidget(self._build_coord_card())
        layout.addWidget(self._build_dimension_card())
        layout.addStretch(1)
        return scroll

    def _build_seed_card(self) -> QWidget:
        card = Card()
        body = QVBoxLayout(card)
        body.setContentsMargins(16, 14, 16, 14)
        body.setSpacing(10)
        header = self._card_header(
            body,
            "种子解析与地图",
            "支持数字、文本和 0x 十六进制。文本种子按 Java String.hashCode() 转换。",
        )
        header.addWidget(badge("Java 版", "blue"))

        row = QHBoxLayout()
        row.setSpacing(8)
        self.seed_input = QLineEdit(str(self.settings.get("last_seed", "0")))
        self.seed_input.setPlaceholderText("例如 123456789、hello 或 0x1A2B3C")
        self.seed_input.setClearButtonEnabled(True)
        self.seed_input.returnPressed.connect(self._parse_seed)
        parse_btn = self._button("解析种子", "bolt", "Primary")
        parse_btn.clicked.connect(self._parse_seed)
        copy_btn = self._button("复制数值", "copy")
        copy_btn.clicked.connect(self._copy_seed_value)
        row.addWidget(QLabel("种子"))
        row.addWidget(self.seed_input, 1)
        row.addWidget(parse_btn)
        row.addWidget(copy_btn)
        body.addLayout(row)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        self.seed_version = QComboBox()
        self.seed_version.setMinimumWidth(280)
        self.seed_version.setMaxVisibleItems(24)
        self._populate_seed_versions()
        saved_platform = ga.normalize_chunkbase_platform(
            self.settings.get("seed_version", "java_1_21_9")
        )
        index = self.seed_version.findData(saved_platform)
        if index >= 0:
            self.seed_version.setCurrentIndex(index)
        self.seed_version.currentIndexChanged.connect(self._on_seed_version_changed)
        self.seed_dimension = SegmentedControl()
        for label, value in ga.CHUNKBASE_DIMENSIONS:
            self.seed_dimension.add_item(label, value)
        row2.addWidget(QLabel("地图版本"))
        row2.addWidget(self.seed_version)
        row2.addSpacing(10)
        row2.addWidget(QLabel("维度"))
        row2.addWidget(self.seed_dimension)
        self.seed_refresh_btn = icon_button(
            "refresh", "从 Chunkbase 重新读取版本列表", size=30, icon_size=15
        )
        self.seed_refresh_btn.clicked.connect(self._refresh_versions_clicked)
        row2.addWidget(self.seed_refresh_btn)
        row2.addStretch(1)
        chunkbase_btn = self._button("在 Chunkbase 打开", "globe", "Primary")
        chunkbase_btn.clicked.connect(self._open_chunkbase)
        seedmap_btn = self._button("MinecraftSeedMap", "external")
        seedmap_btn.clicked.connect(self._open_seedmap)
        row2.addWidget(chunkbase_btn)
        row2.addWidget(seedmap_btn)
        body.addLayout(row2)

        self.seed_platform_note = QLabel()
        self.seed_platform_note.setObjectName("Tiny")
        self.seed_platform_note.setWordWrap(True)
        body.addWidget(self.seed_platform_note)
        self._update_platform_note()

        self.seed_info = QLabel("还没有解析种子。")
        self.seed_info.setObjectName("Muted")
        self.seed_info.setWordWrap(True)
        self.seed_info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        body.addWidget(self.seed_info)
        self.seed_detail = QLabel(
            "提示：群系、结构和地形需要完整的世界生成器，启动器只做种子解析、"
            "史莱姆区块和结构候选推算；精确地图请点上面的按钮。"
        )
        self.seed_detail.setObjectName("Tiny")
        self.seed_detail.setWordWrap(True)
        body.addWidget(self.seed_detail)

        self._parse_seed(quiet=True)
        return card

    def _populate_seed_versions(self) -> None:
        """按 Chunkbase 的分组填入版本；组标题是不可选中的分隔行。"""
        current = str(self.seed_version.currentData() or "")
        model = self.seed_version.model()
        self.seed_version.blockSignals(True)
        try:
            self.seed_version.clear()
            for group_label, items in self._version_groups:
                self.seed_version.addItem(group_label, None)
                row = self.seed_version.count() - 1
                if isinstance(model, QStandardItemModel):
                    item = model.item(row)
                    if item is not None:
                        item.setEnabled(False)
                for label, platform in items:
                    self.seed_version.addItem("    " + label, platform)
        finally:
            self.seed_version.blockSignals(False)
        target = current or ga.normalize_chunkbase_platform(
            self.settings.get("seed_version", "java_1_21_9")
        )
        index = self.seed_version.findData(target)
        if index < 0:
            index = next(
                (
                    i
                    for i in range(self.seed_version.count())
                    if self.seed_version.itemData(i)
                ),
                0,
            )
        if index >= 0:
            self.seed_version.setCurrentIndex(index)
        if hasattr(self, "seed_platform_note"):
            self._update_platform_note()

    def _auto_refresh_versions(self) -> None:
        self._start_registry_worker(force=False, manual=False)

    def _refresh_versions_clicked(self) -> None:
        self._start_registry_worker(force=True, manual=True)

    def _start_registry_worker(self, *, force: bool, manual: bool) -> None:
        if self._registry_worker is not None:
            return
        self._registry_manual = manual
        worker = VersionRegistryWorker(force=force, parent=self)
        worker.done.connect(self._on_registry_done)
        self._registry_worker = worker
        if hasattr(self, "seed_refresh_btn"):
            self.seed_refresh_btn.setEnabled(False)
            if manual:
                self.seed_platform_note.setText("正在从 Chunkbase 读取最新版本列表…")
        worker.start()

    def _on_registry_done(self, groups, source: str, error: str) -> None:
        self._registry_worker = None
        if hasattr(self, "seed_refresh_btn"):
            self.seed_refresh_btn.setEnabled(True)
        if groups:
            self._version_groups = groups
            self._version_source = source
            self._populate_seed_versions()
            if self._registry_manual:
                total = sum(len(items) for _name, items in groups)
                self.main.toast(f"地图版本已更新，共 {total} 个", "success")
        else:
            self._update_platform_note()
            if self._registry_manual:
                self.main.toast(f"版本列表更新失败：{error}", "error")

    def _on_seed_version_changed(self) -> None:
        platform = self.seed_version.currentData()
        if platform:
            self.settings["seed_version"] = str(platform)
            self._save_settings()
        self._update_platform_note()

    def _update_platform_note(self) -> None:
        platform = str(self.seed_version.currentData() or "")
        total = sum(len(items) for _name, items in self._version_groups)
        if self._version_source == "remote":
            source = "版本列表刚从 Chunkbase 更新"
        elif self._version_source == "cache":
            source = "版本列表来自本地缓存，打开助手时会在后台检查更新"
        else:
            source = "暂时没连上 Chunkbase，先用内置列表，联网后会自动更新"
        if ga.is_bedrock_platform(platform):
            text = (
                f"已收录 {total} 个地图版本（{source}）。当前是基岩版：种子地图可直接查看；"
                "史莱姆区块和结构候选只按 Java 版算法计算，基岩版请以 Chunkbase 地图为准。"
            )
        elif platform.endswith("_lb"):
            text = (
                f"已收录 {total} 个地图版本（{source}）。当前是超大型生物群系（LB）："
                "地图会按 LB 重新生成，群系范围与普通世界不同。"
            )
        else:
            text = (
                f"已收录 {total} 个地图版本（{source}），覆盖 Java 1.7 到最新版、LB 和基岩版。"
                "史莱姆区块在 Java 版长期未变，结构参数以 1.18+ 为准，"
                "老版本请以地图结果为准。"
            )
        self.seed_platform_note.setText(text)

    def _parse_seed(self, quiet: bool = False) -> None:
        raw = self.seed_input.text()
        try:
            info = ga.parse_seed(raw)
        except ValueError as error:
            self._seed_info = None
            self.seed_info.setText(f"解析失败：{error}")
            if not quiet:
                self.main.toast(str(error), "error")
            return
        self._seed_info = info
        self.settings["last_seed"] = info.raw
        self._save_settings()
        hash_text = ""
        if info.hash32 is not None:
            hash_text = f" · Java hashCode = {info.hash32}"
        self.seed_info.setText(
            f"{info.label}：{info.value}{hash_text}\n"
            f"区块原点约在 ({ga.block_to_chunk(0)}, {ga.block_to_chunk(0)})，"
            f"坐标 0,0 所在区块是 ({0 // 16}, {0 // 16})。"
        )
        self.seed_detail.setText(
            f"种子指纹 {ga.seed_fingerprint(info.value)} · "
            "文本种子存在哈希碰撞，两个不同字符串可能生成同一个世界。"
        )

    def _copy_seed_value(self) -> None:
        if self._seed_info is None:
            self._parse_seed()
        if self._seed_info is not None:
            self._copy(str(self._seed_info.value), "种子数值已复制")

    def _current_seed(self) -> ga.SeedInfo | None:
        if self._seed_info is None:
            self._parse_seed()
        return self._seed_info

    def _open_chunkbase(self) -> None:
        info = self._current_seed()
        if info is None:
            return
        version = str(self.seed_version.currentData() or "java_1_21_9")
        dimension = self.seed_dimension.current() or "overworld"
        self._open_url(ga.chunkbase_url(info.value, version, dimension))
        self.main.toast("已在浏览器打开 Chunkbase 种子地图", "success")

    def _open_seedmap(self) -> None:
        info = self._current_seed()
        if info is None:
            return
        version = str(self.seed_version.currentData() or "")
        self._open_url(ga.minecraftseedmap_url(info.value, version or None))
        self.main.toast("已在浏览器打开 MinecraftSeedMap", "success")

    def _build_slime_card(self) -> QWidget:
        card = Card()
        body = QVBoxLayout(card)
        body.setContentsMargins(16, 14, 16, 14)
        body.setSpacing(9)
        self._card_header(
            body,
            "史莱姆区块",
            "完全按 Java 版公式离线计算，结果与游戏一致；扫描半径为区块数。",
        )
        row = QHBoxLayout()
        row.setSpacing(8)
        self.slime_x = QSpinBox()
        self.slime_x.setRange(-30000000, 30000000)
        self.slime_z = QSpinBox()
        self.slime_z.setRange(-30000000, 30000000)
        self.slime_radius = QSpinBox()
        self.slime_radius.setRange(1, 32)
        self.slime_radius.setValue(8)
        scan = self._button("扫描附近", "search", "Primary")
        scan.clicked.connect(self._scan_slime)
        check = self._button("只查这个区块", "check")
        check.clicked.connect(self._check_slime)
        row.addWidget(QLabel("中心区块 X"))
        row.addWidget(self.slime_x)
        row.addWidget(QLabel("Z"))
        row.addWidget(self.slime_z)
        row.addWidget(QLabel("半径"))
        row.addWidget(self.slime_radius)
        row.addStretch(1)
        row.addWidget(check)
        row.addWidget(scan)
        body.addLayout(row)

        self.slime_output = self._output(150, "扫描结果会出现在这里。")
        body.addWidget(self.slime_output)
        foot = QHBoxLayout()
        self.slime_summary = QLabel("史莱姆区块约占 1/10，建议在 Y=40 以下做平台。")
        self.slime_summary.setObjectName("Tiny")
        self.slime_summary.setWordWrap(True)
        foot.addWidget(self.slime_summary, 1)
        copy_btn = self._button("复制结果", "copy")
        copy_btn.clicked.connect(lambda: self._copy(self.slime_output.toPlainText(), "史莱姆区块已复制"))
        foot.addWidget(copy_btn)
        body.addLayout(foot)
        return card

    def _slime_coords(self) -> tuple[int, int]:
        return int(self.slime_x.value()), int(self.slime_z.value())

    def _check_slime(self) -> None:
        info = self._current_seed()
        if info is None:
            return
        cx, cz = self._slime_coords()
        is_slime = ga.is_slime_chunk(info.value, cx, cz)
        self.slime_output.setPlainText(
            f"种子 {info.value}\n"
            f"区块 ({cx}, {cz}) 方块范围 X {ga.chunk_to_block(cx)}~{ga.chunk_to_block(cx) + 15}，"
            f"Z {ga.chunk_to_block(cz)}~{ga.chunk_to_block(cz) + 15}\n"
            f"方块中心 ({ga.chunk_center(cx)}, {ga.chunk_center(cz)})\n"
            f"结果：{'是' if is_slime else '不是'}史莱姆区块"
        )
        self.slime_summary.setText("单个区块查询完成。")

    def _scan_slime(self) -> None:
        info = self._current_seed()
        if info is None:
            return
        cx, cz = self._slime_coords()
        radius = int(self.slime_radius.value())
        chunks = ga.find_slime_chunks(info.value, cx, cz, radius, limit=64)
        if not chunks:
            self.slime_output.setPlainText(f"在 ({cx}, {cz}) 周围 {radius} 个区块内没有找到史莱姆区块。")
            self.slime_summary.setText("可以扩大半径再试一次。")
            return
        lines = [
            f"种子 {info.value} · 中心区块 ({cx}, {cz}) · 半径 {radius}",
            f"共找到 {len(chunks)} 个，按距离排序：",
            "",
        ]
        for item in chunks:
            lines.append(
                f"区块 {item.chunk_text:<18} 方块中心 {item.block_text:<18} "
                f"距离 {item.distance:6.1f} 格"
            )
        self.slime_output.setPlainText("\n".join(lines))
        self.slime_summary.setText(
            f"找到 {len(chunks)} 个史莱姆区块。最近的方块中心在 {chunks[0].block_text}。"
        )

    def _build_structure_card(self) -> QWidget:
        card = Card()
        body = QVBoxLayout(card)
        body.setContentsMargins(16, 14, 16, 14)
        body.setSpacing(9)
        self._card_header(
            body,
            "结构候选推算",
            "按原版区域随机公式推算候选区块，再按距离排序；结构参数以 1.18+ 为准，"
            "老版本请以 Chunkbase 地图确认。",
        )
        row = QHBoxLayout()
        row.setSpacing(8)
        self.structure_combo = QComboBox()
        for spec in ga.STRUCTURES:
            self.structure_combo.addItem(
                f"{spec.name} · {spec.dimension_name}", spec.id
            )
        self.structure_combo.setMinimumWidth(220)
        self.structure_radius = QSpinBox()
        self.structure_radius.setRange(1, 24)
        self.structure_radius.setValue(6)
        self.structure_radius.setPrefix("区域半径 ")
        calc = self._button("推算候选", "search", "Primary")
        calc.clicked.connect(self._find_structures)
        row.addWidget(QLabel("结构"))
        row.addWidget(self.structure_combo, 1)
        row.addWidget(self.structure_radius)
        row.addWidget(calc)
        body.addLayout(row)

        self.structure_output = self._output(180, "候选位置会出现在这里。")
        body.addWidget(self.structure_output)
        foot = QHBoxLayout()
        note = QLabel(
            "候选点不等于一定生成：村庄、神殿等还要满足生物群系；要塞和矿井不在区域网格里。"
        )
        note.setObjectName("Tiny")
        note.setWordWrap(True)
        foot.addWidget(note, 1)
        copy_btn = self._button("复制最近坐标", "copy")
        copy_btn.clicked.connect(self._copy_nearest_structure)
        foot.addWidget(copy_btn)
        body.addLayout(foot)
        return card

    def _find_structures(self) -> None:
        info = self._current_seed()
        if info is None:
            return
        structure_id = str(self.structure_combo.currentData() or "village")
        radius = int(self.structure_radius.value())
        try:
            candidates = ga.structure_candidates(info.value, structure_id, radius, limit=40)
        except ValueError as error:
            self.main.toast(str(error), "error")
            return
        spec = ga.STRUCTURE_BY_ID[structure_id]
        lines = [
            f"{spec.name} · {spec.dimension_name} · 区域半径 {radius}",
            f"spacing={spec.spacing} separation={spec.separation} salt={spec.salt}",
            "",
        ]
        for item in candidates:
            lines.append(
                f"区块 {item.chunk_text:<18} 大致方块 {item.block_text:<18} "
                f"距离 {item.distance:7.1f} 格"
            )
        self.structure_output.setPlainText("\n".join(lines))
        note = ga.STRUCTURE_NOTES.get(structure_id)
        if note:
            self.structure_output.appendPlainText("\n注意：" + note)

    def _copy_nearest_structure(self) -> None:
        text = self.structure_output.toPlainText().strip()
        if not text:
            self.main.toast("先推算一次结构候选", "error")
            return
        info = self._current_seed()
        if info is None:
            return
        structure_id = str(self.structure_combo.currentData() or "village")
        try:
            candidate = ga.structure_candidates(info.value, structure_id, int(self.structure_radius.value()), 1)[0]
        except (ValueError, IndexError):
            return
        self._copy(candidate.tp_command, "最近候选的 /tp 已复制")

    def _build_coord_card(self) -> QWidget:
        card = Card()
        body = QVBoxLayout(card)
        body.setContentsMargins(16, 14, 16, 14)
        body.setSpacing(9)
        self._card_header(body, "维度坐标换算", "主世界与下界按 8:1 换算，末地不参与。")
        row = QHBoxLayout()
        row.setSpacing(8)
        self.coord_x = QLineEdit("0")
        self.coord_z = QLineEdit("0")
        self.coord_x.setMaximumWidth(130)
        self.coord_z.setMaximumWidth(130)
        self.coord_direction = SegmentedControl()
        self.coord_direction.add_item("主世界 → 下界", "o2n")
        self.coord_direction.add_item("下界 → 主世界", "n2o")
        convert = self._button("换算", "rotate", "Primary")
        convert.clicked.connect(self._convert_coords)
        row.addWidget(QLabel("X"))
        row.addWidget(self.coord_x)
        row.addWidget(QLabel("Z"))
        row.addWidget(self.coord_z)
        row.addWidget(self.coord_direction)
        row.addStretch(1)
        row.addWidget(convert)
        body.addLayout(row)
        self.coord_result = QLabel("输入坐标后点击换算。")
        self.coord_result.setObjectName("Muted")
        self.coord_result.setTextInteractionFlags(Qt.TextSelectableByMouse)
        body.addWidget(self.coord_result)
        return card

    def _convert_coords(self) -> None:
        try:
            x = float(self.coord_x.text().strip())
            z = float(self.coord_z.text().strip())
        except ValueError:
            self.main.toast("坐标必须是数字", "error")
            return
        if self.coord_direction.current() == "n2o":
            nx, nz = ga.nether_to_overworld(x, z)
            self.coord_result.setText(
                f"下界 ({x:g}, {z:g}) → 主世界 ({nx:g}, {nz:g})"
            )
        else:
            nx, nz = ga.overworld_to_nether(x, z)
            self.coord_result.setText(
                f"主世界 ({x:g}, {z:g}) → 下界 ({nx:g}, {nz:g})"
            )

    def _build_dimension_card(self) -> QWidget:
        card = Card()
        body = QVBoxLayout(card)
        body.setContentsMargins(16, 14, 16, 14)
        body.setSpacing(8)
        self._card_header(body, "维度速查", "建传送门、挖矿和找末地城时先确认高度范围。")
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        for row_index, item in enumerate(ga.dimension_info()):
            name = QLabel(item["name"])
            name.setStyleSheet("font-weight: 700;")
            y = QLabel(item["y"])
            y.setObjectName("Tiny")
            note = QLabel(item["note"])
            note.setObjectName("Tiny")
            note.setWordWrap(True)
            grid.addWidget(name, row_index, 0)
            grid.addWidget(y, row_index, 1)
            grid.addWidget(note, row_index, 2)
        body.addLayout(grid)
        return card

    # ------------------------------------------------------------------
    # 指令工坊

    def _build_command_tab(self) -> QWidget:
        scroll, layout = self._scroll_tab()
        top = QHBoxLayout()
        top.setSpacing(12)
        top.addWidget(self._build_command_list_card(), 2)
        top.addWidget(self._build_command_detail_card(), 3)
        layout.addLayout(top)
        layout.addWidget(self._build_generator_card())
        layout.addWidget(self._build_ai_card())
        layout.addWidget(self._build_chain_card())
        layout.addStretch(1)
        return scroll

    def _build_command_list_card(self) -> QWidget:
        card = Card()
        card.setMinimumHeight(330)
        body = QVBoxLayout(card)
        body.setContentsMargins(14, 13, 14, 12)
        body.setSpacing(8)
        header = self._card_header(body, "Java 指令库", "搜索名称、用途或分类，点一条查看语法。")
        self.command_count = badge(f"{len(ga.COMMANDS)} 条", "gray")
        header.addWidget(self.command_count)

        self.command_search = QLineEdit()
        self.command_search.setPlaceholderText("搜索 give、传送、粒子、计分板…")
        self.command_search.setClearButtonEnabled(True)
        self.command_search.addAction(icons.icon("search", colors()["muted"], 15), QLineEdit.LeadingPosition)
        self.command_search.textChanged.connect(self._refresh_command_list)
        body.addWidget(self.command_search)

        filter_row = QHBoxLayout()
        self.command_category = QComboBox()
        for category in ga.command_categories():
            self.command_category.addItem(CATEGORY_LABELS.get(category, category), category)
        self.command_category.currentIndexChanged.connect(self._refresh_command_list)
        filter_row.addWidget(self.command_category)
        filter_row.addStretch(1)
        body.addLayout(filter_row)

        self.command_scroll = QScrollArea()
        self.command_scroll.setWidgetResizable(True)
        self.command_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.command_holder = QWidget()
        self.command_list_layout = QVBoxLayout(self.command_holder)
        self.command_list_layout.setContentsMargins(0, 0, 4, 0)
        self.command_list_layout.setSpacing(2)
        self.command_list_layout.addStretch(1)
        self.command_scroll.setWidget(self.command_holder)
        body.addWidget(self.command_scroll, 1)
        self._refresh_command_list()
        return card

    def _refresh_command_list(self) -> None:
        if not hasattr(self, "command_list_layout"):
            return
        while self.command_list_layout.count():
            item = self.command_list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._command_rows.clear()
        query = self.command_search.text() if hasattr(self, "command_search") else ""
        category = str(self.command_category.currentData() or "全部") if hasattr(self, "command_category") else "全部"
        specs = ga.search_commands(query, category, limit=120)
        if hasattr(self, "command_count"):
            self.command_count.setText(f"{len(specs)} 条")
        for spec in specs:
            row = CommandRow(spec)
            row.clicked.connect(self._show_command)
            self.command_list_layout.addWidget(row)
            self._command_rows.append(row)
        if not specs:
            empty = QLabel("没有匹配的指令，换个关键词试试。")
            empty.setObjectName("Tiny")
            empty.setAlignment(Qt.AlignCenter)
            self.command_list_layout.addWidget(empty)
        self.command_list_layout.addStretch(1)
        if self._selected_command is not None:
            self._highlight_command(self._selected_command.name)

    def _highlight_command(self, name: str) -> None:
        for row in self._command_rows:
            row.set_selected(row.spec.name == name)

    def _build_command_detail_card(self) -> QWidget:
        card = Card()
        card.setMinimumHeight(330)
        body = QVBoxLayout(card)
        body.setContentsMargins(18, 16, 18, 15)
        body.setSpacing(9)
        head = QHBoxLayout()
        self.command_title = QLabel("挑一条指令")
        self.command_title.setStyleSheet("font-size: 20px; font-weight: 800;")
        head.addWidget(self.command_title)
        self.command_badge = badge("—", "gray")
        head.addWidget(self.command_badge)
        head.addStretch(1)
        body.addLayout(head)

        self.command_usage = QLabel("左侧点选后，这里会显示语法。")
        self.command_usage.setObjectName("Muted")
        self.command_usage.setWordWrap(True)
        self.command_usage.setTextInteractionFlags(Qt.TextSelectableByMouse)
        body.addWidget(self.command_usage)

        self.command_summary = QLabel("")
        self.command_summary.setWordWrap(True)
        body.addWidget(self.command_summary)

        example_title = QLabel("示例")
        example_title.setObjectName("Tiny")
        body.addWidget(example_title)
        self.command_example = self._output(84)
        body.addWidget(self.command_example)
        self.command_note = QLabel("")
        self.command_note.setObjectName("Tiny")
        self.command_note.setWordWrap(True)
        body.addWidget(self.command_note)
        body.addStretch(1)

        row = QHBoxLayout()
        row.addStretch(1)
        copy_btn = self._button("复制示例", "copy")
        copy_btn.clicked.connect(
            lambda: self._copy(self.command_example.toPlainText(), "示例指令已复制")
        )
        templ_btn = self._button("用模板生成", "bolt")
        templ_btn.clicked.connect(self._use_template_for_selected)
        wiki_btn = self._button("查看 Wiki", "external")
        wiki_btn.clicked.connect(lambda: self._open_url("https://zh.minecraft.wiki/w/%E5%91%BD%E4%BB%A4"))
        row.addWidget(wiki_btn)
        row.addWidget(templ_btn)
        row.addWidget(copy_btn)
        body.addLayout(row)
        return card

    def _show_command(self, spec: ga.CommandSpec) -> None:
        self._selected_command = spec
        self._highlight_command(spec.name)
        self.command_title.setText("/" + spec.name)
        self.command_badge.setText(spec.category)
        self.command_usage.setText(spec.usage)
        self.command_summary.setText(spec.summary)
        self.command_example.setPlainText(spec.example)
        self.command_note.setText(spec.note or "")

    def _use_template_for_selected(self) -> None:
        if self._selected_command is None:
            self.main.toast("先选一条指令", "error")
            return
        template_id = self._selected_command.name
        index = self.template_combo.findData(template_id)
        if index < 0:
            self.main.toast("这条指令还没有模板，直接复制示例改吧", "info")
            return
        self.template_combo.setCurrentIndex(index)
        self.main.toast("已切到对应模板，填好参数后生成", "success")

    def _build_generator_card(self) -> QWidget:
        card = Card()
        body = QVBoxLayout(card)
        body.setContentsMargins(16, 14, 16, 14)
        body.setSpacing(9)
        header = self._card_header(
            body,
            "指令生成器",
            "选模板、填参数，自动拼出完整指令；生成后会顺手校验括号、长度和大区域填充。",
        )
        header.addWidget(badge("模板", "purple"))

        top = QHBoxLayout()
        top.setSpacing(8)
        self.template_combo = QComboBox()
        for template in ga.COMMAND_TEMPLATES:
            self.template_combo.addItem(template.name, template.id)
        self.template_combo.currentIndexChanged.connect(self._render_template_fields)
        self.gen_version = QComboBox()
        for item in MC_VERSIONS:
            self.gen_version.addItem(item, item)
        top.addWidget(QLabel("模板"))
        top.addWidget(self.template_combo, 1)
        top.addWidget(QLabel("目标版本"))
        top.addWidget(self.gen_version)
        body.addLayout(top)

        self.template_form = QWidget()
        self.template_grid = QGridLayout(self.template_form)
        self.template_grid.setContentsMargins(0, 0, 0, 0)
        self.template_grid.setHorizontalSpacing(10)
        self.template_grid.setVerticalSpacing(6)
        body.addWidget(self.template_form)
        self._build_template_fields()

        self.gen_output = self._output(70, "生成的指令会出现在这里。")
        body.addWidget(self.gen_output)
        self.gen_issues = QLabel("")
        self.gen_issues.setObjectName("Tiny")
        self.gen_issues.setWordWrap(True)
        self.gen_issues.setTextFormat(Qt.RichText)
        body.addWidget(self.gen_issues)

        row = QHBoxLayout()
        row.addStretch(1)
        gen = self._button("生成指令", "bolt", "Primary")
        gen.clicked.connect(self._generate_command)
        copy = self._button("复制", "copy")
        copy.clicked.connect(lambda: self._copy(self.gen_output.toPlainText(), "指令已复制"))
        save_fn = self._button("保存 .mcfunction", "folder")
        save_fn.clicked.connect(self._save_function)
        export_dp = self._button("导出数据包", "package")
        export_dp.clicked.connect(self._export_datapack)
        row.addWidget(export_dp)
        row.addWidget(save_fn)
        row.addWidget(copy)
        row.addWidget(gen)
        body.addLayout(row)
        self._render_template_fields()
        return card

    def _build_template_fields(self) -> None:
        max_fields = max(len(template.fields) for template in ga.COMMAND_TEMPLATES)
        for index in range(max_fields):
            label = QLabel("")
            label.setObjectName("Tiny")
            edit = QLineEdit()
            edit.setClearButtonEnabled(True)
            holder = QWidget()
            col = QVBoxLayout(holder)
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(2)
            col.addWidget(label)
            col.addWidget(edit)
            self.template_grid.addWidget(holder, index // 2, index % 2)
            self._template_widgets.append((label, edit, holder))
            holder.hide()

    def _render_template_fields(self) -> None:
        if not hasattr(self, "template_combo"):
            return
        template = ga.TEMPLATE_BY_ID.get(str(self.template_combo.currentData()))
        if template is None:
            return
        for index, (label, edit, holder) in enumerate(self._template_widgets):
            if index < len(template.fields):
                field = template.fields[index]
                label.setText(field.label + (" *" if field.required else ""))
                edit.setPlaceholderText(field.placeholder or field.default)
                edit.setText(field.default)
                holder.show()
            else:
                holder.hide()

    def _template_values(self) -> dict[str, str]:
        template = ga.TEMPLATE_BY_ID.get(str(self.template_combo.currentData()))
        if template is None:
            return {}
        values: dict[str, str] = {}
        for index, field in enumerate(template.fields):
            if index < len(self._template_widgets):
                values[field.key] = self._template_widgets[index][1].text()
        return values

    def _generate_command(self) -> None:
        template_id = str(self.template_combo.currentData() or "")
        try:
            command = ga.build_from_template(template_id, self._template_values())
        except ValueError as error:
            self.main.toast(str(error), "error")
            return
        self.gen_output.setPlainText(command)
        self._show_issues(command)

    def _show_issues(self, command: str) -> None:
        issues = ga.validate_command(command)
        color_map = {"error": colors()["red"], "warn": colors()["orange"], "info": colors()["green"]}
        parts = []
        for issue in issues:
            color = color_map.get(issue.level, colors()["muted"])
            parts.append(
                f'<span style="color:{color};">{issue.icon} {html.escape(issue.message)}</span>'
            )
        self.gen_issues.setText("　".join(parts))

    def _save_function(self) -> None:
        text = ga.build_mcfunction(self.gen_output.toPlainText().splitlines())
        if not text.strip():
            self.main.toast("先生成一条指令", "error")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "保存为 .mcfunction",
            str(Path.home() / "dkl_assistant.mcfunction"),
            "Minecraft 函数 (*.mcfunction);;所有文件 (*)",
        )
        if not path:
            return
        try:
            Path(path).write_text(text, encoding="utf-8")
        except OSError as error:
            self.main.toast(f"保存失败：{error}", "error")
            return
        self.main.toast("函数文件已保存", "success")

    def _export_datapack(self) -> None:
        commands = self.gen_output.toPlainText().splitlines()
        if not any(line.strip() for line in commands):
            self.main.toast("先生成一条指令", "error")
            return
        version = str(self.gen_version.currentData() or "1.21.4")
        dialog = DatapackDialog(version, self)
        if not dialog.exec():
            return
        try:
            data = ga.build_datapack(
                dialog.name.text(),
                commands,
                namespace=dialog.namespace.text(),
                version=str(dialog.version.currentData() or version),
                load=dialog.load.isChecked(),
                tick=dialog.tick.isChecked(),
            )
        except ValueError as error:
            self.main.toast(str(error), "error")
            return
        default = f"{dialog.name.text().strip() or 'main'}.zip"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "保存数据包",
            str(Path.home() / default),
            "数据包 (*.zip);;所有文件 (*)",
        )
        if not path:
            return
        try:
            Path(path).write_bytes(data)
        except OSError as error:
            self.main.toast(f"保存失败：{error}", "error")
            return
        self.main.toast("数据包已导出，放进存档的 datapacks 文件夹即可", "success")

    # ------------------------------------------------------------------
    # AI 指令

    def _build_ai_card(self) -> QWidget:
        card = Card()
        body = QVBoxLayout(card)
        body.setContentsMargins(16, 14, 16, 14)
        body.setSpacing(9)
        header = self._card_header(
            body,
            "AI 生成复杂指令",
            "接 OpenAI 兼容接口。适合写 execute 嵌套、多个命令方块的复合逻辑和 JSON 文本。",
        )
        self.ai_status = badge("未配置", "gray")
        header.addWidget(self.ai_status)
        config_btn = self._button("配置接口", "settings")
        config_btn.clicked.connect(self._configure_ai)
        header.addWidget(config_btn)
        self._refresh_ai_status()

        self.ai_prompt = QPlainTextEdit()
        self.ai_prompt.setPlaceholderText(
            "例如：做一个每秒检测玩家脚下是否为钻石矿，如果是就播放音效并给玩家发标题的复合指令"
        )
        self.ai_prompt.setFixedHeight(70)
        body.addWidget(self.ai_prompt)

        row = QHBoxLayout()
        self.ai_context = QLineEdit()
        self.ai_context.setPlaceholderText("可选上下文：模组名、目标版本、命令方块类型…")
        row.addWidget(self.ai_context, 1)
        gen = self._button("AI 生成指令", "sparkles", "Primary")
        gen.clicked.connect(self._generate_ai)
        self.ai_generate_btn = gen
        row.addWidget(gen)
        body.addLayout(row)

        self.ai_output = self._output(120, "AI 返回的指令会先清理 Markdown，再显示在这里。")
        body.addWidget(self.ai_output)
        foot = QHBoxLayout()
        note = QLabel("API Key 只保存在本机 assistant.json；生成结果请自己检查版本差异。")
        note.setObjectName("Tiny")
        note.setWordWrap(True)
        foot.addWidget(note, 1)
        to_gen = self._button("填入生成器", "import")
        to_gen.clicked.connect(self._ai_to_generator)
        to_chain = self._button("送去命令方块链", "terminal")
        to_chain.clicked.connect(self._ai_to_chain)
        copy = self._button("复制", "copy")
        copy.clicked.connect(lambda: self._copy(self.ai_output.toPlainText(), "AI 指令已复制"))
        foot.addWidget(copy)
        foot.addWidget(to_gen)
        foot.addWidget(to_chain)
        body.addLayout(foot)
        return card

    def _configure_ai(self) -> None:
        config = ga.AIConfig.from_dict(self.settings.get("ai"))
        dialog = AIConfigDialog(config, self)
        if dialog.exec() and dialog.saved is not None:
            self.settings["ai"] = dialog.saved.to_dict()
            self._save_settings()
            self._refresh_ai_status()
            self.main.toast("AI 接口配置已保存", "success")

    def _refresh_ai_status(self) -> None:
        if not hasattr(self, "ai_status"):
            return
        config = ga.AIConfig.from_dict(self.settings.get("ai"))
        local = bool(config.base_url) and (
            "localhost" in config.base_url or "127.0.0.1" in config.base_url
        )
        if config.api_key:
            self.ai_status.setText("已配置")
            self.ai_status.setProperty("badge", "green")
        elif local:
            self.ai_status.setText("本地接口")
            self.ai_status.setProperty("badge", "blue")
        elif config.base_url:
            self.ai_status.setText("待填 Key")
            self.ai_status.setProperty("badge", "orange")
        else:
            self.ai_status.setText("未配置")
            self.ai_status.setProperty("badge", "gray")
        repolish(self.ai_status)

    def _generate_ai(self) -> None:
        if self._ai_worker is not None:
            self.main.toast("上一条 AI 指令还在生成", "info")
            return
        request = self.ai_prompt.toPlainText().strip()
        if not request:
            self.main.toast("先写清楚要生成什么指令", "error")
            return
        config = ga.AIConfig.from_dict(self.settings.get("ai"))
        version = str(self.gen_version.currentData() or "1.21.4")
        self.ai_output.setPlainText("正在请求 AI…")
        self.ai_generate_btn.setEnabled(False)
        worker = AIWorker(request, config, version, self.ai_context.text().strip(), self)
        self._ai_worker = worker
        worker.finished.connect(self._ai_finished)
        worker.failed.connect(self._ai_failed)
        worker.start()

    def _ai_finished(self, text: str) -> None:
        self._ai_worker = None
        self.ai_generate_btn.setEnabled(True)
        self.ai_output.setPlainText(text)
        self.main.toast("AI 指令已生成", "success")

    def _ai_failed(self, message: str) -> None:
        self._ai_worker = None
        self.ai_generate_btn.setEnabled(True)
        self.ai_output.setPlainText("生成失败：" + message)
        self.main.toast(message, "error")

    def _ai_to_generator(self) -> None:
        text = self.ai_output.toPlainText().strip()
        if not text or text.startswith("正在请求") or text.startswith("生成失败"):
            self.main.toast("还没有可用的 AI 结果", "error")
            return
        self.gen_output.setPlainText(text)
        self._show_issues(text.splitlines()[0] if text.splitlines() else "")

    def _ai_to_chain(self) -> None:
        text = self.ai_output.toPlainText().strip()
        if not text or text.startswith("正在请求") or text.startswith("生成失败"):
            self.main.toast("还没有可用的 AI 结果", "error")
            return
        self.chain_input.setPlainText(text)
        self.main.toast("已送入命令方块链", "success")

    # ------------------------------------------------------------------
    # 命令方块链

    def _build_chain_card(self) -> QWidget:
        card = Card()
        body = QVBoxLayout(card)
        body.setContentsMargins(16, 14, 16, 14)
        body.setSpacing(9)
        self._card_header(
            body,
            "命令方块链",
            "每行一条指令。生成 /setblock 放置指令，会自动处理循环/连锁、条件、自动和朝向。",
        )
        self.chain_input = QPlainTextEdit()
        self.chain_input.setPlaceholderText("/say 第一段\n/execute as @a at @s run particle minecraft:flame ~ ~1 ~ 0.1 0.1 0.1 0.01 10")
        self.chain_input.setFixedHeight(84)
        body.addWidget(self.chain_input)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.chain_first = QComboBox()
        self.chain_first.addItem("循环命令方块", "repeating")
        self.chain_first.addItem("脉冲命令方块", "impulse")
        self.chain_facing = QComboBox()
        for label, value in (("向东 +X", "east"), ("向西 -X", "west"), ("向南 +Z", "south"), ("向北 -Z", "north"), ("向上 +Y", "up"), ("向下 -Y", "down")):
            self.chain_facing.addItem(label, value)
        self.chain_origin = QLineEdit("0 64 0")
        self.chain_origin.setMaximumWidth(110)
        self.chain_origin.setToolTip("第一条命令方块的坐标 x y z")
        self.chain_conditional = QCheckBox("条件连锁")
        self.chain_auto = QCheckBox("自动激活")
        self.chain_auto.setChecked(True)
        controls.addWidget(QLabel("首块"))
        controls.addWidget(self.chain_first)
        controls.addWidget(QLabel("朝向"))
        controls.addWidget(self.chain_facing)
        controls.addWidget(QLabel("起点"))
        controls.addWidget(self.chain_origin)
        controls.addWidget(self.chain_conditional)
        controls.addWidget(self.chain_auto)
        controls.addStretch(1)
        generate = self._button("生成放置指令", "bolt", "Primary")
        generate.clicked.connect(self._generate_chain)
        controls.addWidget(generate)
        body.addLayout(controls)

        self.chain_output = self._output(130, "生成的 /setblock 会出现在这里。")
        body.addWidget(self.chain_output)
        foot = QHBoxLayout()
        note = QLabel("提示：链命令方块必须紧挨着，朝向要从第一个方块指向后续方块。")
        note.setObjectName("Tiny")
        note.setWordWrap(True)
        foot.addWidget(note, 1)
        copy = self._button("复制", "copy")
        copy.clicked.connect(lambda: self._copy(self.chain_output.toPlainText(), "放置指令已复制"))
        fn = self._button("存为函数", "folder")
        fn.clicked.connect(self._save_chain_function)
        dp = self._button("导出数据包", "package")
        dp.clicked.connect(self._export_chain_datapack)
        foot.addWidget(dp)
        foot.addWidget(fn)
        foot.addWidget(copy)
        body.addLayout(foot)
        return card

    def _chain_commands(self) -> list[str]:
        return [line.strip() for line in self.chain_input.toPlainText().splitlines() if line.strip()]

    def _generate_chain(self) -> None:
        commands = self._chain_commands()
        if not commands:
            self.main.toast("先写至少一条指令", "error")
            return
        parts = self.chain_origin.text().replace(",", " ").split()
        if len(parts) != 3:
            self.main.toast("起点坐标要写成 x y z", "error")
            return
        try:
            origin = tuple(int(float(part)) for part in parts)
        except ValueError:
            self.main.toast("起点坐标必须是数字", "error")
            return
        try:
            lines = ga.build_chain_blocks(
                commands,
                origin=origin,  # type: ignore[arg-type]
                first=str(self.chain_first.currentData() or "repeating"),
                conditional=self.chain_conditional.isChecked(),
                auto=self.chain_auto.isChecked(),
                facing=str(self.chain_facing.currentData() or "east"),
            )
        except ValueError as error:
            self.main.toast(str(error), "error")
            return
        self.chain_output.setPlainText("\n".join(lines))

    def _save_chain_function(self) -> None:
        commands = self._chain_commands()
        if not commands:
            self.main.toast("先写至少一条指令", "error")
            return
        text = ga.build_mcfunction(commands)
        path, _ = QFileDialog.getSaveFileName(
            self,
            "保存为 .mcfunction",
            str(Path.home() / "dkl_chain.mcfunction"),
            "Minecraft 函数 (*.mcfunction);;所有文件 (*)",
        )
        if not path:
            return
        try:
            Path(path).write_text(text, encoding="utf-8")
        except OSError as error:
            self.main.toast(f"保存失败：{error}", "error")
            return
        self.main.toast("函数文件已保存", "success")

    def _export_chain_datapack(self) -> None:
        commands = self._chain_commands()
        if not commands:
            self.main.toast("先写至少一条指令", "error")
            return
        version = str(self.gen_version.currentData() or "1.21.4")
        dialog = DatapackDialog(version, self)
        if not dialog.exec():
            return
        try:
            data = ga.build_datapack(
                dialog.name.text(),
                commands,
                namespace=dialog.namespace.text(),
                version=str(dialog.version.currentData() or version),
                load=dialog.load.isChecked(),
                tick=dialog.tick.isChecked(),
            )
        except ValueError as error:
            self.main.toast(str(error), "error")
            return
        default = f"{dialog.name.text().strip() or 'main'}.zip"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "保存数据包",
            str(Path.home() / default),
            "数据包 (*.zip);;所有文件 (*)",
        )
        if not path:
            return
        try:
            Path(path).write_bytes(data)
        except OSError as error:
            self.main.toast(f"保存失败：{error}", "error")
            return
        self.main.toast("数据包已导出", "success")

    # ------------------------------------------------------------------
    # 附魔预测

    def _build_enchant_tab(self) -> QWidget:
        scroll, layout = self._scroll_tab()
        layout.addWidget(self._build_enchant_status_card())
        layout.addWidget(self._build_enchant_tutorial_card())
        layout.addWidget(self._build_enchant_links_card())
        layout.addStretch(1)
        return scroll

    def _build_enchant_status_card(self) -> QWidget:
        card = Card()
        body = QVBoxLayout(card)
        body.setContentsMargins(16, 14, 16, 14)
        body.setSpacing(9)
        header = self._card_header(
            body,
            "RNG 附魔预测",
            "内置 EnchCracker 1.6（Apache-2.0），配合 clientcommands 的 /cenchant 读取附魔种子。",
        )
        header.addWidget(badge("EnchCracker", "purple"))

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        self.enchant_java_label = QLabel("检测中…")
        self.enchant_jar_label = QLabel("检测中…")
        self.enchant_mod_label = QLabel("检测中…")
        for row, (title, label) in enumerate(
            (("Java 运行时", self.enchant_java_label), ("预测工具", self.enchant_jar_label), ("clientcommands", self.enchant_mod_label))
        ):
            key = QLabel(title)
            key.setObjectName("Tiny")
            label.setWordWrap(True)
            grid.addWidget(key, row, 0)
            grid.addWidget(label, row, 1)
        grid.setColumnStretch(1, 1)
        body.addLayout(grid)

        row = QHBoxLayout()
        row.addStretch(1)
        refresh = self._button("刷新检测", "refresh")
        refresh.clicked.connect(self._refresh_enchant_status)
        launch = self._button("启动附魔预测", "play", "Primary")
        launch.clicked.connect(self._launch_enchcracker)
        folder = self._button("打开工具目录", "folder")
        folder.clicked.connect(self._open_enchcracker_dir)
        row.addWidget(folder)
        row.addWidget(refresh)
        row.addWidget(launch)
        body.addLayout(row)

        note = QLabel(
            "为什么需要 clientcommands：EnchCracker 不能直接读游戏内存，"
            "它通过 /cenchant 获取客户端发送的附魔种子。服务器是否允许该指令取决于服务器规则。"
        )
        note.setObjectName("Tiny")
        note.setWordWrap(True)
        body.addWidget(note)
        return card

    def _game_mods_dirs(self) -> list[Path]:
        dirs: list[Path] = []
        try:
            selected = getattr(self.main, "selected_version", "")
            if selected and hasattr(self.main.config, "mods_path"):
                dirs.append(Path(self.main.config.mods_path(selected)))
            game_dir = Path(self.main.config.game_dir or "")
            if game_dir and game_dir.exists():
                dirs.append(game_dir / "mods")
                versions = game_dir / "versions"
                if versions.exists():
                    dirs.extend(path / "mods" for path in versions.iterdir() if path.is_dir())
        except Exception:
            pass
        unique: list[Path] = []
        for path in dirs:
            key = str(path).lower()
            if key not in {str(item).lower() for item in unique}:
                unique.append(path)
        return unique

    def _refresh_enchant_status(self) -> None:
        if not hasattr(self, "enchant_java_label"):
            return
        self.main.ensure_java_detected()
        java = pick_java(getattr(self.main, "java_installs", []), 8, getattr(self.main.config, "java_path", ""))
        if java is None:
            self.enchant_java_label.setText("未找到 Java 8+，请先在设置里配置 Java。")
        else:
            self.enchant_java_label.setText(f"{java.version or '未知版本'} · {java.path}")

        jar = ga.find_enchcracker()
        if jar is None:
            self.enchant_jar_label.setText("未找到 enchcracker-1.6.jar，请重新安装启动器。")
        else:
            size_kb = max(1, jar.stat().st_size // 1024)
            self.enchant_jar_label.setText(f"已就绪 · {jar.name} · {size_kb} KB")

        found: list[Path] = []
        for mods_dir in self._game_mods_dirs():
            found.extend(ga.find_clientcommands_mods(mods_dir.parent))
        if found:
            self.enchant_mod_label.setText(
                "已检测到：" + "、".join(path.name for path in found[:3])
            )
        else:
            self.enchant_mod_label.setText(
                "未检测到 clientcommands。请从官方 Releases 下载后放进对应版本的 mods 文件夹。"
            )

    def _launch_enchcracker(self) -> None:
        self.main.ensure_java_detected()
        java = pick_java(getattr(self.main, "java_installs", []), 8, getattr(self.main.config, "java_path", ""))
        if java is None:
            self.main.toast("没有可用的 Java 8+，请先到设置页配置", "error")
            return
        jar = ga.find_enchcracker()
        if jar is None:
            self.main.toast("没有找到 EnchCracker jar", "error")
            return
        try:
            ga.launch_enchcracker(java.path, jar)
        except (OSError, FileNotFoundError) as error:
            self.main.toast(f"启动失败：{error}", "error")
            return
        self.main.toast("EnchCracker 已启动，进游戏执行 /cenchant", "success")

    def _open_enchcracker_dir(self) -> None:
        directory = ga.enchcracker_dir()
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    def _build_enchant_tutorial_card(self) -> QWidget:
        card = Card()
        body = QVBoxLayout(card)
        body.setContentsMargins(16, 14, 16, 14)
        body.setSpacing(8)
        self._card_header(body, "附魔预测教程", "按顺序做一遍就能看到下一次附魔结果。")
        for title, text in ga.ENCHANT_TUTORIAL:
            step = QLabel(title)
            step.setStyleSheet("font-weight: 700;")
            step.setWordWrap(True)
            body.addWidget(step)
            detail = QLabel(text)
            detail.setObjectName("Muted")
            detail.setWordWrap(True)
            body.addWidget(detail)
        return card

    def _build_enchant_links_card(self) -> QWidget:
        card = Card()
        body = QVBoxLayout(card)
        body.setContentsMargins(16, 14, 16, 14)
        body.setSpacing(8)
        self._card_header(body, "下载与教程链接", "只从官方 Releases / Wiki 下载，别用来路不明的 jar。")
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        for index, (label, url) in enumerate(ga.ENCHANT_LINKS):
            button = self._button(label, "external")
            button.clicked.connect(lambda _=False, u=url: self._open_url(u))
            grid.addWidget(button, index // 2, index % 2)
        body.addLayout(grid)
        return card

    # ------------------------------------------------------------------
    # 资料站

    def _build_wiki_tab(self) -> QWidget:
        scroll, layout = self._scroll_tab()
        layout.addWidget(self._build_mcmod_card())
        layout.addWidget(self._build_link_card())
        layout.addWidget(self._build_quick_id_card())
        layout.addWidget(self._build_version_note_card())
        layout.addStretch(1)
        return scroll

    def _build_mcmod_card(self) -> QWidget:
        card = Card()
        body = QVBoxLayout(card)
        body.setContentsMargins(16, 14, 16, 14)
        body.setSpacing(9)
        self._card_header(
            body,
            "MC百科搜索",
            "在启动器里直接看结果；点条目会用系统浏览器打开详情页。",
        )
        row = QHBoxLayout()
        self.mcmod_query = QLineEdit()
        self.mcmod_query.setPlaceholderText("搜索模组、整合包、物品、教程…")
        self.mcmod_query.setClearButtonEnabled(True)
        self.mcmod_query.returnPressed.connect(self._search_mcmod)
        search = self._button("搜索", "search", "Primary")
        search.clicked.connect(self._search_mcmod)
        browser = self._button("浏览器打开", "external")
        browser.clicked.connect(lambda: self._open_url(ga.mcmod_search_url(self.mcmod_query.text())))
        row.addWidget(self.mcmod_query, 1)
        row.addWidget(search)
        row.addWidget(browser)
        body.addLayout(row)

        self.mcmod_status = QLabel("输入关键词开始搜索。")
        self.mcmod_status.setObjectName("Tiny")
        body.addWidget(self.mcmod_status)
        self.mcmod_scroll = QScrollArea()
        self.mcmod_scroll.setWidgetResizable(True)
        self.mcmod_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.mcmod_scroll.setFixedHeight(250)
        self.mcmod_holder = QWidget()
        self.mcmod_list_layout = QVBoxLayout(self.mcmod_holder)
        self.mcmod_list_layout.setContentsMargins(0, 0, 4, 0)
        self.mcmod_list_layout.setSpacing(3)
        self.mcmod_list_layout.addStretch(1)
        self.mcmod_scroll.setWidget(self.mcmod_holder)
        body.addWidget(self.mcmod_scroll)
        return card

    def _search_mcmod(self) -> None:
        if self._mcmod_worker is not None:
            self.main.toast("上一次搜索还在进行", "info")
            return
        query = self.mcmod_query.text().strip()
        if not query:
            self.main.toast("先输入要搜索的关键词", "error")
            return
        self.mcmod_status.setText("正在搜索 MC百科…")
        worker = MCModWorker(query, self)
        self._mcmod_worker = worker
        worker.finished.connect(self._mcmod_finished)
        worker.failed.connect(self._mcmod_failed)
        worker.start()

    def _clear_mcmod_results(self) -> None:
        while self.mcmod_list_layout.count():
            item = self.mcmod_list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _mcmod_finished(self, results) -> None:
        self._mcmod_worker = None
        self._clear_mcmod_results()
        items = list(results or [])
        if not items:
            self.mcmod_status.setText("没有找到结果，换个关键词或直接去浏览器搜索。")
            self.mcmod_list_layout.addStretch(1)
            return
        self.mcmod_status.setText(f"找到 {len(items)} 条结果，点击打开详情。")
        for result in items:
            row = MCModRow(result)
            row.clicked.connect(self._open_url)
            self.mcmod_list_layout.addWidget(row)
        self.mcmod_list_layout.addStretch(1)

    def _mcmod_failed(self, message: str) -> None:
        self._mcmod_worker = None
        self._clear_mcmod_results()
        self.mcmod_status.setText("搜索失败：" + message)
        self.mcmod_list_layout.addStretch(1)
        self.main.toast(message, "error")

    def _build_link_card(self) -> QWidget:
        card = Card()
        body = QVBoxLayout(card)
        body.setContentsMargins(16, 14, 16, 14)
        body.setSpacing(8)
        self._card_header(body, "常用资料站", "原版机制、数据包、命令方块和种子地图都在这里。")
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        for index, (title, subtitle, url) in enumerate(ga.WIKI_LINKS):
            button = self._button(title, "globe")
            button.setToolTip(subtitle)
            button.setMinimumHeight(30)
            button.clicked.connect(lambda _=False, u=url: self._open_url(u))
            grid.addWidget(button, index // 3, index % 3)
        body.addLayout(grid)
        return card

    def _build_quick_id_card(self) -> QWidget:
        card = Card()
        body = QVBoxLayout(card)
        body.setContentsMargins(16, 14, 16, 14)
        body.setSpacing(8)
        self._card_header(body, "常用 ID 速查", "点击直接复制命名空间 ID，粘贴到指令里就能用。")
        for title, entries in QUICK_IDS.items():
            label = QLabel(title)
            label.setObjectName("Tiny")
            body.addWidget(label)
            grid = QGridLayout()
            grid.setHorizontalSpacing(4)
            grid.setVerticalSpacing(4)
            for index, (name, value) in enumerate(entries):
                button = QPushButton(name)
                button.setObjectName("Ghost")
                button.setCursor(Qt.PointingHandCursor)
                button.setToolTip(value)
                button.setMinimumHeight(24)
                button.clicked.connect(lambda _=False, v=value, n=name: self._copy(v, f"{n} 已复制"))
                grid.addWidget(button, index // 5, index % 5)
            body.addLayout(grid)
        return card

    def _build_version_note_card(self) -> QWidget:
        card = Card()
        body = QVBoxLayout(card)
        body.setContentsMargins(16, 14, 16, 14)
        body.setSpacing(6)
        self._card_header(body, "版本差异提示", "写指令前先确认版本，尤其是 1.20.5 和 1.21 的语法变化。")
        notes = (
            "1.20.5+：物品 NBT 改为组件语法，/give 的写法会变。",
            "1.21+：数据包的 functions 目录改名为 function，tags/functions 改名 tags/function。",
            "1.21+：属性 ID 去掉了 generic. 等前缀，例如 minecraft:max_health。",
            "1.19.4+：新增 /damage、/ride 等指令；旧版本请换成对应写法。",
            "模组指令（如 //wand、/lp）不在原版目录里，用 /help 查模组文档。",
        )
        for text in notes:
            label = QLabel("· " + text)
            label.setObjectName("Muted")
            label.setWordWrap(True)
            body.addWidget(label)
        return card

    # ------------------------------------------------------------------
    # 小工具

    def _build_tools_tab(self) -> QWidget:
        scroll, layout = self._scroll_tab()
        layout.addWidget(self._build_chunk_tool_card())
        layout.addWidget(self._build_quick_command_card())
        layout.addWidget(self._build_fingerprint_card())
        layout.addStretch(1)
        return scroll

    def _build_chunk_tool_card(self) -> QWidget:
        card = Card()
        body = QVBoxLayout(card)
        body.setContentsMargins(16, 14, 16, 14)
        body.setSpacing(9)
        self._card_header(body, "方块 / 区块换算", "输入方块坐标，算出所在区块、区块内偏移与中心点。")
        row = QHBoxLayout()
        self.block_x = QLineEdit("0")
        self.block_z = QLineEdit("0")
        self.block_x.setMaximumWidth(140)
        self.block_z.setMaximumWidth(140)
        calc = self._button("计算", "rotate", "Primary")
        calc.clicked.connect(self._calc_chunk)
        row.addWidget(QLabel("方块 X"))
        row.addWidget(self.block_x)
        row.addWidget(QLabel("方块 Z"))
        row.addWidget(self.block_z)
        row.addStretch(1)
        row.addWidget(calc)
        body.addLayout(row)
        self.chunk_result = QLabel("输入方块坐标后点击计算。")
        self.chunk_result.setObjectName("Muted")
        self.chunk_result.setWordWrap(True)
        self.chunk_result.setTextInteractionFlags(Qt.TextSelectableByMouse)
        body.addWidget(self.chunk_result)
        return card

    def _calc_chunk(self) -> None:
        try:
            x = int(float(self.block_x.text().strip()))
            z = int(float(self.block_z.text().strip()))
        except ValueError:
            self.main.toast("坐标必须是数字", "error")
            return
        cx, cz = ga.block_to_chunk(x), ga.block_to_chunk(z)
        ox, oz = x - ga.chunk_to_block(cx), z - ga.chunk_to_block(cz)
        info = self._current_seed()
        slime = ""
        if info is not None:
            slime = " · 该区块是史莱姆区块" if ga.is_slime_chunk(info.value, cx, cz) else " · 该区块不是史莱姆区块"
        self.chunk_result.setText(
            f"方块 ({x}, {z}) → 区块 ({cx}, {cz})，区块内偏移 ({ox}, {oz})，"
            f"区块范围 X {ga.chunk_to_block(cx)}~{ga.chunk_to_block(cx) + 15}，"
            f"Z {ga.chunk_to_block(cz)}~{ga.chunk_to_block(cz) + 15}，"
            f"中心 ({ga.chunk_center(cx)}, {ga.chunk_center(cz)}){slime}"
        )

    def _build_quick_command_card(self) -> QWidget:
        card = Card()
        body = QVBoxLayout(card)
        body.setContentsMargins(16, 14, 16, 14)
        body.setSpacing(8)
        self._card_header(body, "快捷指令", "一键生成常用世界设置，再复制进游戏。")
        presets = (
            ("白天", "/time set day"),
            ("夜晚", "/time set midnight"),
            ("晴天", "/weather clear"),
            ("雷雨", "/weather thunder 600"),
            ("保留物品", "/gamerule keepInventory true"),
            ("关闭昼夜", "/gamerule doDaylightCycle false"),
            ("显示坐标", "/gamerule reducedDebugInfo false"),
            ("开启命令方块", "/gamerule commandBlockOutput true"),
        )
        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        for index, (label, command) in enumerate(presets):
            button = self._button(label, "bolt")
            button.clicked.connect(lambda _=False, c=command: self._append_quick_command(c))
            grid.addWidget(button, index // 4, index % 4)
        body.addLayout(grid)
        self.quick_output = self._output(90, "点上面的按钮，指令会累积到这里。")
        body.addWidget(self.quick_output)
        row = QHBoxLayout()
        row.addStretch(1)
        clear = self._button("清空", "trash")
        clear.clicked.connect(lambda: self.quick_output.clear())
        copy = self._button("复制全部", "copy", "Primary")
        copy.clicked.connect(lambda: self._copy(self.quick_output.toPlainText(), "快捷指令已复制"))
        row.addWidget(clear)
        row.addWidget(copy)
        body.addLayout(row)
        return card

    def _append_quick_command(self, command: str) -> None:
        current = self.quick_output.toPlainText().strip()
        self.quick_output.setPlainText((current + "\n" + command).strip())
        self.main.toast("已加入快捷指令", "success")

    def _build_fingerprint_card(self) -> QWidget:
        card = Card()
        body = QVBoxLayout(card)
        body.setContentsMargins(16, 14, 16, 14)
        body.setSpacing(9)
        self._card_header(body, "种子指纹", "用 SHA-256 前 16 位做一个短指纹，方便在截图或聊天里核对种子。")
        row = QHBoxLayout()
        self.fingerprint_input = QLineEdit()
        self.fingerprint_input.setPlaceholderText("输入种子，支持数字/文本/0x")
        calc = self._button("生成指纹", "sparkles", "Primary")
        calc.clicked.connect(self._calc_fingerprint)
        row.addWidget(self.fingerprint_input, 1)
        row.addWidget(calc)
        body.addLayout(row)
        self.fingerprint_result = QLabel("等待计算。")
        self.fingerprint_result.setObjectName("Muted")
        self.fingerprint_result.setTextInteractionFlags(Qt.TextSelectableByMouse)
        body.addWidget(self.fingerprint_result)
        return card

    def _calc_fingerprint(self) -> None:
        try:
            info = ga.parse_seed(self.fingerprint_input.text())
        except ValueError as error:
            self.fingerprint_result.setText(f"解析失败：{error}")
            return
        fingerprint = ga.seed_fingerprint(info.value)
        self.fingerprint_result.setText(
            f"种子数值 {info.value} · 指纹 {fingerprint}"
        )
        self._copy(fingerprint, "种子指纹已复制")

    # ------------------------------------------------------------------
    # 生命周期

    def on_show(self) -> None:
        self.main.ensure_java_detected()
        self._refresh_enchant_status()

    def refresh_theme(self) -> None:
        if hasattr(self, "gen_output"):
            text = self.gen_output.toPlainText().strip()
            if text and "\n" not in text:
                self._show_issues(text)
        self._refresh_ai_status()