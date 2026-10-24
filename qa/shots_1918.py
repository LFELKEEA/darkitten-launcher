from __future__ import annotations

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

from PySide6.QtCore import QBuffer, QIODevice, QRect, QTimer
from PySide6.QtGui import QFont, QFontDatabase, QImage
from PySide6.QtWidgets import QApplication

from darkitten import servers
from darkitten.config import Config
from darkitten.mods import write_mod_meta
from darkitten.servers import ServerInfo, save_server
from darkitten.ui import icons
import darkitten.ui.mod_dialog as mod_dialog
from darkitten.ui.download_center import DownloadTask
from darkitten.ui.main_window import MainWindow
from darkitten.ui.mod_dialog import ModBrowserDialog

# 无头截图不需要 Java 探测线程，避免退出时后台线程干扰。
MainWindow.ensure_java_detected = lambda self, force=False: None

QA = PROJECT / "qa"
app = QApplication(sys.argv)
for font_path in (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
):
    if Path(font_path).exists():
        QFontDatabase.addApplicationFont(font_path)
app.setFont(QFont("Microsoft YaHei UI", 10))
Config.save = lambda self: None

temp = tempfile.TemporaryDirectory()
root = Path(temp.name)
game = root / "game"
game.mkdir()
servers_root = root / "servers"
old_servers_dir = servers.SERVERS_DIR
servers.SERVERS_DIR = servers_root


def png_bytes(pixmap) -> bytes:
    image = pixmap.toImage()
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def vanilla(version_id: str, java: int = 21) -> dict:
    return {
        "id": version_id,
        "type": "release",
        "mainClass": "net.minecraft.client.main.Main",
        "assets": "1.21",
        "assetIndex": {"id": "1.21", "url": "http://example.invalid/a.json"},
        "javaVersion": {"majorVersion": java},
    }


def write_version(version_id: str, java: int = 21) -> None:
    folder = game / "versions" / version_id
    folder.mkdir(parents=True)
    (folder / f"{version_id}.json").write_text(
        json.dumps(vanilla(version_id, java)), encoding="utf-8"
    )
    (folder / f"{version_id}.jar").write_bytes(b"jar")


write_version("1.21.11")
write_version("1.20.1", java=17)
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

mods_dir = game / "versions" / "1.21.11" / "mods"
mods_dir.mkdir(parents=True)
icon_image = icons.chibi_face_pixmap(64).toImage()
icon_buffer = QBuffer()
icon_buffer.open(QIODevice.WriteOnly)
icon_image.save(icon_buffer, "PNG")
oc_icon = bytes(icon_buffer.data())


def make_mod(filename: str, name: str, version: str, description: str) -> Path:
    path = mods_dir / filename
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "fabric.mod.json",
            json.dumps(
                {
                    "id": name.lower().replace(" ", "_"),
                    "name": name,
                    "version": version,
                    "description": description,
                    "icon": "assets/icon.png",
                }
            ),
        )
        zf.writestr("assets/icon.png", oc_icon)
    return path


sodium = make_mod("sodium-fabric-0.6.13.jar", "Sodium", "0.6.13", "Rendering engine")
write_mod_meta(
    mods_dir,
    sodium.name,
    {
        "title": "Sodium",
        "project_id": "sodium",
        "source": "modrinth",
        "source_label": "Modrinth",
        "description": "Rendering engine",
        "description_zh": (
            "钠（Sodium）是一款渲染优化 Mod，重写了区块与光照渲染，"
            "在保持原版画风的同时大幅提升帧率，低配电脑也能更流畅。"
        ),
        "project_url": "https://modrinth.com/mod/sodium",
    },
    oc_icon,
)
lithium = make_mod("lithium-fabric-0.15.3.jar", "Lithium", "0.15.3", "Game logic optimizations")
write_mod_meta(
    mods_dir,
    lithium.name,
    {
        "title": "Lithium",
        "project_id": "lithium",
        "source": "modrinth",
        "source_label": "Modrinth",
        "description": "Game logic optimizations",
        "description_zh": (
            "锂（Lithium）是一款通用逻辑优化 Mod，优化实体、方块刻、"
            "区块加载与 AI，通常能明显降低卡顿，且不改动游戏内容。"
        ),
        "project_url": "https://modrinth.com/mod/lithium",
    },
    oc_icon,
)
jei = make_mod("jei-1.21.11-fabric-19.27.0.jar", "Just Enough Items", "19.27.0", "View items and recipes")
write_mod_meta(
    mods_dir,
    jei.name,
    {
        "title": "Just Enough Items",
        "project_id": "jei",
        "source": "curseforge",
        "source_label": "CurseForge",
        "description": "View items and recipes",
        "description_zh": (
            "JEI（Just Enough Items）是物品与配方查询 Mod，打开背包就能搜索物品，"
            "查看合成表、用途和模组配方，是整合包里最常见的辅助模组之一。"
        ),
        "project_url": "https://www.curseforge.com/minecraft/mc-mods/jei",
    },
    oc_icon,
)

server = ServerInfo(
    folder="OC-Server",
    name="恶魔小猫的服务器",
    kind="paper",
    mc_version="1.21.11",
    installed=True,
    port=25565,
)
server.path.mkdir(parents=True, exist_ok=True)
(server.path / "server-icon.png").write_bytes(
    png_bytes(icons.chibi_face_pixmap(64))
)
save_server(server)

window: MainWindow | None = None
dialog: ModBrowserDialog | None = None


def build() -> None:
    global window
    config = Config(
        game_dir=str(game),
        update_auto_check=False,
        last_version="1.21.11",
        version_loaders={"1.21.11": loader_id},
    )
    window = MainWindow(config)
    window.resize(1280, 820)
    window.apply_theme("dark")
    window.select_version("1.21.11")
    window.show()
    QTimer.singleShot(450, capture_top)


def capture_top() -> None:
    print("step capture_top", flush=True)
    assert window is not None
    task = DownloadTask(
        id=901,
        title="Sodium 0.6.13",
        content_type="mod",
        source="modrinth",
        target=mods_dir,
        progress=63,
        message="正在下载 sodium-fabric-0.6.13.jar",
        status="running",
    )
    window.download_center._tasks[task.id] = task
    window.download_center._order.append(task.id)
    window.download_chip.resize(196, 38)
    window.download_chip.sync(window.download_center)
    QTimer.singleShot(200, do_capture_top)


def do_capture_top() -> None:
    print("step do_capture_top", flush=True)
    assert window is not None
    shot = window.grab()
    shot.save(str(QA / "dkl_1918_top_download.png"))
    shot.copy(QRect(0, 0, window.width(), 74)).save(
        str(QA / "dkl_1918_topbar.png")
    )
    window.switch_page("mods")
    window.mods_page.refresh_mods()
    QTimer.singleShot(400, capture_mods)


def capture_mods() -> None:
    print("step capture_mods", flush=True)
    assert window is not None
    window.grab().save(str(QA / "dkl_1918_mods.png"))
    window.switch_page("server")
    if hasattr(window.server_page, "refresh_servers"):
        window.server_page.refresh_servers("OC-Server")
    QTimer.singleShot(450, capture_server)


def capture_server() -> None:
    print("step capture_server", flush=True)
    assert window is not None
    window.grab().save(str(QA / "dkl_1918_server_icon.png"))
    show_dialog()


def show_dialog() -> None:
    print("step show_dialog", flush=True)
    global dialog
    assert window is not None
    mod_dialog.ModSearchWorker.start = lambda self: None
    mod_dialog.ModProjectWorker.start = lambda self: None
    mod_dialog.ModVersionsWorker.start = lambda self: None
    dialog = ModBrowserDialog(window)
    dialog.show()
    QTimer.singleShot(300, fill_dialog)


def fill_dialog() -> None:
    print("step fill_dialog", flush=True)
    global dialog
    assert dialog is not None
    cover_url = "https://example.invalid/sodium-cover.png"
    mod_dialog._IMAGE_CACHE[cover_url] = icons.chibi_face_pixmap(256).toImage()
    hits = [
        {
            "project_id": "sodium",
            "slug": "sodium",
            "title": "Sodium",
            "description": "Rendering engine",
            "author": "jellysquid3",
            "downloads": 41200000,
            "categories": ["optimization"],
            "icon_url": cover_url,
            "project_url": "https://modrinth.com/mod/sodium",
        },
        {
            "project_id": "lithium",
            "slug": "lithium",
            "title": "Lithium",
            "description": "Game logic optimizations",
            "author": "jellysquid3",
            "downloads": 28800000,
            "categories": ["optimization"],
            "icon_url": cover_url,
            "project_url": "https://modrinth.com/mod/lithium",
        },
        {
            "project_id": "jei",
            "slug": "jei",
            "title": "Just Enough Items",
            "description": "View items and recipes",
            "author": "mezz",
            "downloads": 638000000,
            "categories": ["utility", "adventure"],
            "icon_url": cover_url,
            "project_url": "https://modrinth.com/mod/jei",
        },
        {
            "project_id": "create",
            "slug": "create",
            "title": "Create",
            "description": "Building and automation",
            "author": "simibubi",
            "downloads": 22100000,
            "categories": ["technology", "decoration"],
            "icon_url": cover_url,
            "project_url": "https://modrinth.com/mod/create",
        },
    ]
    dialog._hits = hits
    dialog._total = 4
    dialog._render_results()
    dialog._select_project("sodium", hits[0])
    QTimer.singleShot(450, capture_dialog)


def capture_dialog() -> None:
    print("step capture_dialog", flush=True)
    assert dialog is not None
    dialog.grab().save(str(QA / "dkl_1918_mod_download.png"))
    dialog._detach_download_ui()
    dialog.close()
    QTimer.singleShot(200, finish)


def finish() -> None:
    print("step finish", flush=True)
    servers.SERVERS_DIR = old_servers_dir
    # 截图进程不需要再走关闭确认与后台线程回收，直接退出避免无头环境清理时卡住。
    os._exit(0)


QTimer.singleShot(0, build)
app.exec()
print("shots saved")
