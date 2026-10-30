from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QSpinBox, QWidget

import darkitten.server_config as sc
import darkitten.servers as servers_module
from darkitten.servers import ServerInfo
from darkitten.ui import server_config_dialog as dialog_module
from darkitten.ui.widgets import Switch

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> bool:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"{name}: OK{(' (' + detail + ')') if detail else ''}")
    else:
        FAIL += 1
        print(f"{name}: FAIL{(' (' + detail + ')') if detail else ''}")
    return condition


def make_server(root: Path, folder: str = "test") -> ServerInfo:
    info = ServerInfo(
        folder=folder,
        name="测试服",
        kind="paper",
        mc_version="1.21.1",
        port=25565,
        online_mode=False,
        installed=True,
        created_at="2026-10-30 12:00:00",
    )
    info.path.mkdir(parents=True, exist_ok=True)
    (info.path / "server.json").write_text(
        json.dumps(asdict(info), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return info


def test_properties_parser(info: ServerInfo) -> None:
    path = info.path / "server.properties"
    path.write_text(
        "#Minecraft server properties\n"
        "motd=Hello\\u4e16\\u754c\n"
        "escaped\\=key=value\n"
        "colon:value\n"
        "space value\n"
        "multi=line1\\\n"
        "    line2\n"
        "empty=\n",
        encoding="utf-8",
    )
    values = sc.read_server_properties(path)
    check("properties 中文转义", values.get("motd") == "Hello世界", repr(values.get("motd")))
    check("properties 转义键名", values.get("escaped=key") == "value")
    check("properties 冒号分隔", values.get("colon") == "value")
    check("properties 空格分隔", values.get("space") == "value")
    check("properties 续行", values.get("multi") == "line1line2", repr(values.get("multi")))
    check("properties 空值", values.get("empty") == "")
    check("properties 不存在文件", sc.read_server_properties(info.path / "nope.properties") == {})


def test_property_rows() -> None:
    common = sc.property_rows({})
    check("常用配置补齐 15 项", len(common) == len(sc.COMMON_PROPERTY_KEYS), str(len(common)))
    all_rows = sc.property_rows({}, include_all=True)
    check("全部配置 65 项", len(all_rows) == len(sc.SERVER_PROPERTY_SPECS), str(len(all_rows)))
    rows = sc.property_rows({"custom-key": "1", "motd": "hi"})
    custom = next(spec for spec, value in rows if spec.key == "custom-key")
    check("未知键归入其他分类", custom.category == "其他")
    check("未知键保留原值", dict((s.key, v) for s, v in rows)["custom-key"] == "1")
    check("常用项不会重复", [s.key for s, _ in rows].count("motd") == 1)


def test_property_validation() -> None:
    port = sc.SERVER_PROPERTY_SPECS["server-port"]
    check("端口合法", sc.validate_property(port, "25565") == ("25565", ""))
    check("端口非整数", bool(sc.validate_property(port, "abc")[1]))
    check("端口越界", bool(sc.validate_property(port, "70000")[1]))
    pvp = sc.SERVER_PROPERTY_SPECS["pvp"]
    check("布尔 yes 归一化", sc.validate_property(pvp, "yes") == ("true", ""))
    check("布尔非法值", bool(sc.validate_property(pvp, "maybe")[1]))
    difficulty = sc.SERVER_PROPERTY_SPECS["difficulty"]
    check("枚举合法", sc.validate_property(difficulty, "hard") == ("hard", ""))
    check("枚举非法", bool(sc.validate_property(difficulty, "nightmare")[1]))


def test_save_properties(info: ServerInfo) -> None:
    path = info.path / "server.properties"
    original = (
        "#Minecraft server properties\n"
        "# 这是注释，保存后应保留\n"
        "motd=旧标语\n"
        "server-port=25565\n"
        "online-mode=false\n"
        "custom-key=keep-me\n"
    )
    path.write_text(original, encoding="utf-8")
    values = sc.read_server_properties(path)
    values["server-port"] = "25566"
    values["online-mode"] = "true"
    values["motd"] = "新标语"
    sc.save_server_properties(info, values)
    saved = sc.read_server_properties(path)
    text = path.read_text(encoding="utf-8")
    check("保存端口", saved.get("server-port") == "25566")
    check("保存正版验证", saved.get("online-mode") == "true")
    check("保存标语", saved.get("motd") == "新标语")
    check("保留注释", "# 这是注释，保存后应保留" in text)
    check("保留未知键", saved.get("custom-key") == "keep-me")
    check("更新 info 端口", info.port == 25566, str(info.port))
    check("更新 info 正版", info.online_mode is True)
    record = json.loads((info.path / "server.json").read_text(encoding="utf-8"))
    check("同步 server.json 端口", record.get("port") == 25566, str(record.get("port")))
    backup = info.path / "server.properties.bak"
    check("生成 properties 备份", backup.is_file() and "旧标语" in backup.read_text(encoding="utf-8"))
    summary = sc.config_summary(info)
    check("配置摘要含端口", "25566" in summary, summary)


def test_scan_configs(info: ServerInfo) -> None:
    plugins = info.path / "plugins"
    (plugins / "Demo").mkdir(parents=True, exist_ok=True)
    (plugins / "Demo" / "config.yml").write_text("enabled: true\n", encoding="utf-8")
    (plugins / "Demo" / "limits.json").write_text('{"max": 1}\n', encoding="utf-8")
    (plugins / "Demo" / "data").mkdir(parents=True, exist_ok=True)
    (plugins / "Demo" / "data" / "cache.yml").write_text("x: 1\n", encoding="utf-8")
    (plugins / "Demo.jar").write_bytes(b"jar")
    (plugins / "Demo" / "big.yml").write_text("a" * (sc.MAX_CONFIG_BYTES + 10), encoding="utf-8")
    config = info.path / "config"
    config.mkdir(parents=True, exist_ok=True)
    (config / "mod.toml").write_text("[general]\nenabled = true\n", encoding="utf-8")
    (info.path / "bukkit.yml").write_text("settings:\n  allow-end: true\n", encoding="utf-8")
    logs = info.path / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "latest.yml").write_text("skip: true\n", encoding="utf-8")
    found = sc.scan_plugin_configs(info)
    names = {item.relative for item in found}
    check("扫描插件配置", "plugins/Demo/config.yml" in names, str(sorted(names)))
    check("扫描 Mod 配置", "config/mod.toml" in names)
    check("扫描根目录服务配置", "bukkit.yml" in names)
    check("跳过数据目录", "plugins/Demo/data/cache.yml" not in names)
    check("跳过日志目录", "logs/latest.yml" not in names)
    check("跳过超大文件", "plugins/Demo/big.yml" not in names)
    check("根目录配置排在最前", found[0].relative == "bukkit.yml", found[0].relative)
    check("配置格式识别", next(i for i in found if i.relative == "config/mod.toml").format == "TOML")
    check("配置文件大小文本", found[0].display_size.endswith("B") or found[0].display_size.endswith("KB"))


def test_text_io(info: ServerInfo) -> None:
    path = info.path / "plugins" / "Demo" / "gbk.yml"
    path.write_bytes("prefix: 中文\n".encode("gb18030"))
    check("GB18030 回退读取", "中文" in sc.read_config_text(path))
    target = info.path / "plugins" / "Demo" / "config.yml"
    original = target.read_text(encoding="utf-8")
    sc.write_text_atomic(target, "enabled: false\n")
    check("原子写入生效", target.read_text(encoding="utf-8") == "enabled: false\n")
    check("原子写入保留备份", (target.parent / "config.yml.bak").read_text(encoding="utf-8") == original)
    check("原子写入不残留 tmp", not (target.parent / "config.yml.tmp").exists())


def test_validation() -> None:
    check("JSON 合法", sc.validate_config_text('{"a": 1}', ".json") == "")
    check("JSON 非法", bool(sc.validate_config_text("{oops}", ".json")))
    check("TOML 合法", sc.validate_config_text("[general]\nenabled = true\n", ".toml") == "")
    check("TOML 非法", bool(sc.validate_config_text("[general\nenabled = true", ".toml")))
    check("YAML 合法", sc.validate_config_text("enabled: true\nlist:\n  - a\n", ".yml") == "")
    check("YAML Tab 缩进", bool(sc.validate_config_text("a:\n\tb: 1\n", ".yml")))
    check(
        "YAML 重复顶层键",
        bool(sc.validate_config_text("a: 1\na: 2\n", ".yml")),
    )
    check(
        "Properties 无效转义",
        bool(sc.validate_config_text("motd=\\u12zz\n", ".properties")),
    )


def test_explanations() -> None:
    check("known 键解释", "服务器描述" in sc.explain_config_key("motd"))
    check("分组键解释", "地址" in sc.explain_config_key("database.host"))
    check("复合键解释", "加入" in sc.explain_config_key("join-message"))
    check("未知键通用解释", "官方文档" in sc.explain_config_key("totally-unknown-key"))
    yaml_text = "enabled: true\nprefix: '&aDemo'\nmessages:\n  join: hi\n"
    hints = sc.describe_config(yaml_text, ".yml")
    check("YAML 顶层键数量", [h.key for h in hints] == ["enabled", "prefix", "messages"], str([h.key for h in hints]))
    check("YAML 键说明", bool(hints[0].note))
    json_hints = sc.describe_config('{"server-port": 25565, "motd": "hi"}', ".json")
    check("JSON 顶层键", {h.key for h in json_hints} == {"server-port", "motd"})
    toml_hints = sc.describe_config("[general]\nenabled = true\n", ".toml")
    check("TOML 顶层键", [h.key for h in toml_hints] == ["general"])
    prop_hints = sc.describe_config("motd=hi\nserver-port=1\n", ".properties")
    check("Properties 顶层键", [h.key for h in prop_hints] == ["motd", "server-port"])
    check("光标行取键", sc.key_at_line("  port: 25565\n", 1) == "port")
    check("注释行无键", sc.key_at_line("# port: 1\n", 1) == "")
    check("列表行无键", sc.key_at_line("- port: 1\n", 1) == "port")


def test_dialog(info: ServerInfo) -> None:
    dialog = dialog_module.ServerConfigDialog(None, info)
    common_count = len(dialog._property_bindings)
    check("对话框属性行数", common_count >= 15, str(common_count))
    port_control = next(c for s, c in dialog._property_bindings if s.key == "server-port")
    check("端口控件数值", isinstance(port_control, QSpinBox) and port_control.value() == info.port)
    online_control = next(c for s, c in dialog._property_bindings if s.key == "online-mode")
    check("正版开关状态", isinstance(online_control, Switch) and online_control.isChecked() == info.online_mode)
    dialog.all_switch.setChecked(True)
    check(
        "显示全部配置",
        len(dialog._property_bindings) == len(sc.SERVER_PROPERTY_SPECS) + 1,
        str(len(dialog._property_bindings)),
    )
    dialog.all_switch.setChecked(False)
    check("切回常用配置", len(dialog._property_bindings) == common_count)
    dialog.property_search.setText("服务器端口")
    check(
        "搜索过滤",
        dialog.property_count.text().startswith(f"显示 1 / {common_count}"),
        dialog.property_count.text(),
    )
    dialog.property_search.clear()
    dialog.property_category.setCurrentIndex(1)
    check("分类过滤可用", "显示" in dialog.property_count.text())
    dialog.property_category.setCurrentIndex(0)

    port_control = next(c for s, c in dialog._property_bindings if s.key == "server-port")
    online_control = next(c for s, c in dialog._property_bindings if s.key == "online-mode")
    port_control.setValue(25567)
    online_control.setChecked(True)
    dialog._save_properties()
    saved = sc.read_server_properties(info.path / "server.properties")
    check("对话框保存端口", saved.get("server-port") == "25567")
    check("对话框保存正版", saved.get("online-mode") == "true")
    check("对话框标记已保存", not dialog._property_dirty)

    entries = [dialog.config_list.item(i) for i in range(dialog.config_list.count())]
    demo_row = next(
        i for i, item in enumerate(entries) if str(item.data(Qt.UserRole)) == "plugins/Demo/config.yml"
    )
    dialog.config_list.setCurrentRow(demo_row)
    check("对话框加载插件配置", dialog._current_config is not None and dialog._current_config.relative.endswith("config.yml"))
    dialog.config_editor.setPlainText("enabled: false\nprefix: '&bX'\n")
    dialog._save_config()
    check(
        "对话框保存插件配置",
        (info.path / "plugins" / "Demo" / "config.yml").read_text(encoding="utf-8")
        == "enabled: false\nprefix: '&bX'\n",
    )
    check("插件配置备份", (info.path / "plugins" / "Demo" / "config.yml.bak").exists())
    json_row = next(
        i for i, item in enumerate(entries) if str(item.data(Qt.UserRole)) == "plugins/Demo/limits.json"
    )
    dialog.config_list.setCurrentRow(json_row)
    before = (info.path / "plugins" / "Demo" / "limits.json").read_text(encoding="utf-8")
    dialog.config_editor.setPlainText("{oops}")
    dialog._save_config()
    check("非法 JSON 阻止保存", "JSON" in dialog.config_status.text(), dialog.config_status.text())
    check(
        "非法 JSON 不写盘",
        (info.path / "plugins" / "Demo" / "limits.json").read_text(encoding="utf-8") == before,
    )

    running = dialog_module.ServerConfigDialog(None, info, running=True)
    warning = next(
        (label for label in running.findChildren(QLabel) if "服务端正在运行" in label.text()),
        None,
    )
    check("运行中警告", warning is not None and warning.isVisibleTo(running))


class FakeConfirm:
    result = False

    def __init__(self, *args, **kwargs) -> None:
        pass

    def exec(self) -> bool:
        return FakeConfirm.result


def test_dirty_guard(info: ServerInfo) -> None:
    original = dialog_module.ConfirmDialog
    dialog_module.ConfirmDialog = FakeConfirm  # type: ignore[assignment]
    try:
        dialog = dialog_module.ServerConfigDialog(None, info)
        port_control = next(c for s, c in dialog._property_bindings if s.key == "server-port")
        port_control.setValue(25570)
        check("修改后标记未保存", dialog._property_dirty)
        FakeConfirm.result = False
        dialog.all_switch.setChecked(True)
        check("拒绝丢弃时不切换", not dialog.all_switch.isChecked())
        check("切换失败仍保留修改", dialog._property_dirty)
        FakeConfirm.result = True
        dialog.all_switch.setChecked(True)
        check("同意丢弃后切换", dialog.all_switch.isChecked())
        check("切换后重置修改标记", not dialog._property_dirty)
    finally:
        dialog_module.ConfirmDialog = original  # type: ignore[assignment]


class FakeMain(QWidget):
    java_installs_changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.config = SimpleNamespace()
        self.download_center = SimpleNamespace()
        self.java_installs: list = []
        self.toasts: list[tuple[str, str]] = []

    def ensure_java_detected(self) -> None:
        pass

    def open_path(self, path) -> None:
        pass

    def switch_page(self, name: str) -> None:
        pass

    def toast(self, text: str, kind: str = "info") -> None:
        self.toasts.append((text, kind))


def test_page_integration(info: ServerInfo) -> None:
    from darkitten.ui.pages import server as page_module

    page = page_module.ServerPage(FakeMain())
    check("设置页有编辑配置按钮", page.config_btn.text() == "编辑配置文件")
    check("编辑配置按钮可用", page.config_btn.isEnabled())
    check("页面提供打开配置对话框", callable(page.open_config_dialog))

    calls: list[tuple[str, bool]] = []

    class FakeDialog:
        saved = Signal()

        def __init__(self, parent, server, *, running=False) -> None:
            calls.append((server.folder, running))

        def exec(self) -> int:
            return 0

    original = page_module.ServerConfigDialog
    page_module.ServerConfigDialog = FakeDialog  # type: ignore[assignment]
    try:
        page._selected_folder = info.folder
        page.open_config_dialog()
        check("页面打开配置对话框", calls == [(info.folder, False)], str(calls))
    finally:
        page_module.ServerConfigDialog = original  # type: ignore[assignment]
    page.deleteLater()


def main() -> int:
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        servers_module.SERVERS_DIR = root / "servers"
        info = make_server(root)

        test_properties_parser(info)
        test_property_rows()
        test_property_validation()
        test_save_properties(info)
        test_scan_configs(info)
        test_text_io(info)
        test_validation()
        test_explanations()
        test_dialog(info)
        test_dirty_guard(info)
        test_page_integration(info)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
