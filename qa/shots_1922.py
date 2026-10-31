from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from darkitten.config import Config
from darkitten.mod_conflicts import ModIssue
from darkitten.mod_updates import ModUpdate
from darkitten.mods import list_mods, write_mod_meta
from darkitten.ui.main_window import MainWindow
from darkitten.ui.mod_health_dialog import ModHealthDialog
from darkitten.ui.mod_update_dialog import ModUpdatesDialog

MainWindow.ensure_java_detected = lambda self, force=False: None
Config.save = lambda self: None

QA = PROJECT / "qa"
app = QApplication.instance() or QApplication(sys.argv)
for font_path in (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
):
    if Path(font_path).exists():
        QFontDatabase.addApplicationFont(font_path)
app.setFont(QFont("Microsoft YaHei UI", 10))

temp = tempfile.TemporaryDirectory()
root = Path(temp.name)
game = root / "game"
version_dir = game / "versions" / "1.21.11"
version_dir.mkdir(parents=True)
(version_dir / "1.21.11.jar").write_bytes(b"jar")
(version_dir / "1.21.11.json").write_text(
    json.dumps(
        {
            "id": "1.21.11",
            "type": "release",
            "mainClass": "net.minecraft.client.main.Main",
            "assets": "1.21",
            "assetIndex": {"id": "1.21", "url": "http://example.invalid/a.json"},
            "javaVersion": {"majorVersion": 21},
        }
    ),
    encoding="utf-8",
)
loader_id = "fabric-loader-0.19.5-1.21.11"
loader_dir = game / "versions" / loader_id
loader_dir.mkdir(parents=True)
(loader_dir / f"{loader_id}.json").write_text(
    json.dumps(
        {
            "id": loader_id,
            "inheritsFrom": "1.21.11",
            "type": "release",
            "mainClass": "net.fabricmc.loader.impl.launch.knot.KnotClient",
            "libraries": [{"name": "net.fabricmc:fabric-loader:0.19.5"}],
            "javaVersion": {"majorVersion": 21},
        }
    ),
    encoding="utf-8",
)

mods_dir = version_dir / "mods"
mods_dir.mkdir()


def make_mod(filename: str, modid: str, name: str, version: str, intro: str) -> None:
    with zipfile.ZipFile(mods_dir / filename, "w") as zf:
        zf.writestr(
            "fabric.mod.json",
            json.dumps(
                {
                    "id": modid,
                    "name": name,
                    "version": version,
                    "description": intro,
                }
            ),
        )
    write_mod_meta(
        mods_dir,
        filename,
        {
            "title": name,
            "project_id": f"project-{modid}",
            "source": "modrinth",
            "source_label": "Modrinth",
            "description": intro,
            "description_zh": intro,
        },
    )


make_mod(
    "sodium-fabric-0.6.13.jar",
    "sodium",
    "Sodium",
    "0.6.13",
    "钠（Sodium）是一款渲染优化 Mod，重写区块与光照渲染，大幅提升帧率。",
)
make_mod(
    "lithium-fabric-0.15.3.jar",
    "lithium",
    "Lithium",
    "0.15.3",
    "锂（Lithium）优化实体、方块刻与区块加载，明显降低卡顿。",
)
make_mod(
    "fabric-api-0.92.2.jar",
    "fabric-api",
    "Fabric API",
    "0.92.2",
    "Fabric 生态的基础前置，提供事件、网络与注册表扩展。",
)
make_mod(
    "iris-fabric-1.8.0.jar",
    "iris",
    "Iris Shaders",
    "1.8.0",
    "Iris 让 Fabric 支持光影包，同时保持较高的帧率表现。",
)

window = MainWindow(
    Config(
        game_dir=str(game),
        update_auto_check=False,
        last_version="1.21.11",
        version_loaders={"1.21.11": loader_id},
    )
)
window.resize(1280, 820)
window.apply_theme("dark")
window.show()
window.select_version("1.21.11")
page = window.mods_page
page._check_health = lambda auto=False: None
window.switch_page("mods")
QTest.qWait(400)

mods = list_mods(mods_dir)


def update_for(mod, version_label: str) -> ModUpdate:
    content = f"{mod.name}-{version_label}".encode()
    digest = hashlib.sha1(content).hexdigest()
    return ModUpdate(
        mod=mod,
        version={
            "id": f"version-{version_label}",
            "version_number": version_label,
            "files": [
                {
                    "primary": True,
                    "url": "https://example.invalid/file.jar",
                    "filename": f"{mod.path.stem}-{version_label}.jar",
                    "size": len(content),
                    "hashes": {"sha1": digest},
                }
            ],
            "dependencies": [],
        },
        source="modrinth",
        version_label=version_label,
        file_name=f"{mod.path.stem}-{version_label}.jar",
        size=len(content),
        sha1=digest,
        page_url="https://modrinth.com/mod/example",
    )


def by_name(name: str):
    return next(item for item in mods if item.name == name)


updates = [
    update_for(by_name("Sodium"), "0.7.0"),
    update_for(by_name("Lithium"), "0.16.0"),
    update_for(by_name("Iris Shaders"), "1.9.0"),
]
page._updates = {item.mod.filename: item for item in updates}
page.refresh_mods()
page._health_auto = True
page._on_health_checked(
    [
        ModIssue(
            "warning",
            "前置版本不匹配",
            "Iris Shaders 要求 fabric-api >=0.100.0，当前安装的是 0.92.2。",
            "iris-fabric-1.8.0.jar",
            ["fabric-api-0.92.2.jar"],
        )
    ],
    [],
    "",
)
QTest.qWait(300)

window.grab().save(str(QA / "dkl_1922_mods_updates.png"))

dialog = ModUpdatesDialog(window, updates)
dialog.show()
QTest.qWait(300)
dialog.grab().save(str(QA / "dkl_1922_update_dialog.png"))
dialog.close()

health = ModHealthDialog(
    window,
    [
        ModIssue(
            "error",
            "缺少必需前置",
            "Iris Shaders 需要前置「fabric-api」，但 mods 文件夹里没有找到。",
            "iris-fabric-1.8.0.jar",
        ),
        ModIssue(
            "warning",
            "前置版本不匹配",
            "Iris Shaders 要求 fabric-api >=0.100.0，当前安装的是 0.92.2。",
            "iris-fabric-1.8.0.jar",
            ["fabric-api-0.92.2.jar"],
        ),
    ],
)
health.show()
QTest.qWait(300)
health.grab().save(str(QA / "dkl_1922_health_dialog.png"))
health.close()

window.hide()
print("saved:", QA / "dkl_1922_mods_updates.png")
print("saved:", QA / "dkl_1922_update_dialog.png")
print("saved:", QA / "dkl_1922_health_dialog.png")
QTimer.singleShot(0, app.quit)
app.exec()
temp.cleanup()
