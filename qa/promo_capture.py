from __future__ import annotations

"""宣传片素材采集：用演示数据驱动真实 MainWindow，逐页抓图。"""

import json
import os
import shutil
import sys
import tempfile
import time
import zipfile
from dataclasses import asdict
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["QT_SCALE_FACTOR"] = "1"
os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "0"

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from PySide6.QtCore import QBuffer, QEvent, QIODevice, QPoint, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QScrollArea

import darkitten.servers as servers_module
import darkitten.social as social_module
from darkitten import servers
from darkitten.config import Config
from darkitten.java import JavaInfo
from darkitten.mods import write_mod_meta
from darkitten.servers import ServerInfo, save_server
from darkitten.ui import icons, theme

OUT = PROJECT / "qa" / "promo_shots"
OUT.mkdir(parents=True, exist_ok=True)

app = QApplication(sys.argv)
for font_path in (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
):
    if Path(font_path).exists():
        QFontDatabase.addApplicationFont(font_path)
app.setFont(QFont("Microsoft YaHei UI", 10))

theme.set_current("dark")
app.setStyleSheet(theme.stylesheet("dark"))

Config.save = lambda self: None  # type: ignore[assignment]


def wait(ms: int) -> None:
    deadline = time.monotonic() + ms / 1000
    while time.monotonic() < deadline:
        app.processEvents()
        # 页面刷新用 deleteLater 替换旧行，offscreen 下必须显式派发
        # DeferredDelete，否则新旧控件会在同一位置叠图。
        app.sendPostedEvents(None, QEvent.DeferredDelete)
        time.sleep(0.01)


def clean_demo_paths(widget) -> None:
    """把界面上的工作区绝对路径换成演示路径，避免截图出现本机目录。"""
    home = Path.home()
    pairs = (
        (server_root, "D:/Servers/cat-survival"),
        (demo_root, "D:/Darkitten"),
        (PROJECT, "D:/Darkitten"),
        (root, "D:/Darkitten"),
        (home, "D:/Darkitten"),
    )
    replacements = []
    for path, display in pairs:
        for variant in (str(path), str(path).replace("\\", "/")):
            if variant not in replacements:
                replacements.append((variant, display))
    for label in widget.findChildren(QLabel):
        text = label.text()
        if not text:
            continue
        updated = text
        for old, new in replacements:
            updated = updated.replace(old, new)
        if updated != text:
            label.setText(updated)


def grab(widget, name: str) -> None:
    # 演示过程中皮肤导入等操作会弹出 Toast，截图前统一收掉，避免遮挡界面。
    for top in app.topLevelWidgets():
        toast = getattr(top, "toast_widget", None)
        if toast is not None:
            toast.hide()
    clean_demo_paths(widget)
    widget.grab().save(str(OUT / name))
    print("saved", name, widget.width(), "x", widget.height(), flush=True)


# ---------------------------------------------------------------------------
# 临时目录与演示数据

tmp = tempfile.TemporaryDirectory()
root = Path(tmp.name)
# 游戏目录与服务端目录放在工作区的 promo_demo 下，设置页和服端详情里的
# 路径才不会出现一长串 AppData\Local\Temp\...。
demo_root = PROJECT / "qa" / "promo_demo" / "instance"
demo_root.mkdir(parents=True, exist_ok=True)
game = demo_root / "game"
game.mkdir(exist_ok=True)
social_module.SOCIAL_PATH = root / "social.json"
servers_module.SERVERS_DIR = demo_root / "servers"
servers.SERVERS_DIR = demo_root / "servers"
servers.SERVERS_DIR.mkdir(exist_ok=True)

import darkitten.skin_library as library  # noqa: E402
import darkitten.ui.pages.skin as skin_page_module  # noqa: E402

library.LIBRARY_DIR = root / "library"
library.INDEX_PATH = library.LIBRARY_DIR / "index.json"
DEMO = PROJECT / "qa" / "promo_demo" / "skins"
DEMO.mkdir(parents=True, exist_ok=True)
local_skin = DEMO / "恶魔猫猫.png"
library.local_skin_path = lambda: local_skin  # type: ignore[assignment]
library.save_local_skin = (  # type: ignore[assignment]
    lambda skin: (local_skin.write_bytes(skin.data), local_skin)[-1]
)
skin_page_module.local_skin_path = lambda: local_skin  # type: ignore[assignment]


def vanilla(version_id: str, java: int = 21, release_time: str = "") -> dict:
    return {
        "id": version_id,
        "type": "release",
        "mainClass": "net.minecraft.client.main.Main",
        "assets": "1.21",
        "assetIndex": {"id": "1.21", "url": "http://example.invalid/a.json"},
        "javaVersion": {"majorVersion": java},
        "releaseTime": release_time or "2026-10-01T10:00:00+08:00",
    }


def write_version(version_id: str, java: int = 21, release_time: str = "") -> None:
    folder = game / "versions" / version_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{version_id}.json").write_text(
        json.dumps(vanilla(version_id, java, release_time), ensure_ascii=False),
        encoding="utf-8",
    )
    (folder / f"{version_id}.jar").write_bytes(b"jar")


def write_loader(
    version_id: str,
    parent: str,
    main_class: str,
    libraries: list[dict],
    java: int = 21,
    release_time: str = "",
) -> None:
    folder = game / "versions" / version_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{version_id}.json").write_text(
        json.dumps(
            {
                "id": version_id,
                "inheritsFrom": parent,
                "type": "release",
                "mainClass": main_class,
                "libraries": libraries,
                "javaVersion": {"majorVersion": java},
                "releaseTime": release_time,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


write_version("1.21.11", release_time="2026-10-20T10:00:00+08:00")
write_version("1.21.8", release_time="2026-09-02T10:00:00+08:00")
write_version("1.20.1", java=17, release_time="2023-06-12T10:00:00+08:00")
write_version("1.16.5", java=8, release_time="2021-01-15T10:00:00+08:00")

fabric_id = "fabric-loader-0.19.5-1.21.11"
forge_id = "1.20.1-forge-47.3.0"
neoforge_id = "1.21.11-neoforge-21.1.80"
write_loader(
    fabric_id,
    "1.21.11",
    "net.fabricmc.loader.impl.launch.knot.KnotClient",
    [{"name": "net.fabricmc:fabric-loader:0.19.5"}],
)
write_loader(
    forge_id,
    "1.20.1",
    "cpw.mods.bootstraplauncher.BootstrapLauncher",
    [{"name": "net.minecraftforge:forge:1.20.1-47.3.0"}],
    java=17,
)
write_loader(
    neoforge_id,
    "1.21.11",
    "cpw.mods.bootstraplauncher.BootstrapLauncher",
    [{"name": "net.neoforged:neoforge:21.1.80"}],
)

# 模组
mods_dir = game / "versions" / "1.21.11" / "mods"
mods_dir.mkdir(parents=True, exist_ok=True)


def icon_bytes(name: str, color: str, size: int = 96) -> bytes:
    pixmap: QPixmap = icons.pixmap(name, color, size)
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    pixmap.toImage().save(buffer, "PNG")
    return bytes(buffer.data())


def make_mod(
    filename: str,
    mod_id: str,
    name: str,
    version: str,
    description: str,
    meta: dict,
    icon_name: str,
    color: str,
) -> None:
    icon = icon_bytes(icon_name, color)
    path = mods_dir / filename
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "fabric.mod.json",
            json.dumps(
                {
                    "id": mod_id,
                    "name": name,
                    "version": version,
                    "description": description,
                    "icon": "assets/icon.png",
                }
            ),
        )
        zf.writestr("assets/icon.png", icon)
    write_mod_meta(
        mods_dir,
        path.name,
        {
            "title": name,
            "project_id": mod_id,
            "source": meta.get("source", "modrinth"),
            "source_label": meta.get("source_label", "Modrinth"),
            "description": description,
            "description_zh": meta.get("description_zh", ""),
            "project_url": meta.get("project_url", ""),
        },
        icon,
    )


make_mod(
    "sodium-fabric-0.6.13.jar",
    "sodium",
    "Sodium",
    "0.6.13",
    "Rendering engine",
    {
        "description_zh": (
            "钠（Sodium）重写了区块与光照渲染，在保持原版画风的同时大幅提升帧率，"
            "低配电脑也能明显更流畅。"
        ),
        "project_url": "https://modrinth.com/mod/sodium",
    },
    "sparkles",
    "#E9B44C",
)
make_mod(
    "lithium-fabric-0.15.3.jar",
    "lithium",
    "Lithium",
    "0.15.3",
    "Game logic optimizations",
    {
        "description_zh": "锂优化实体、方块刻和区块加载，降低卡顿且不改动游戏内容。",
        "project_url": "https://modrinth.com/mod/lithium",
    },
    "bolt",
    "#55A6E0",
)
make_mod(
    "jei-1.21.11-fabric-19.27.0.jar",
    "jei",
    "Just Enough Items",
    "19.27.0",
    "View items and recipes",
    {
        "source": "curseforge",
        "source_label": "CurseForge",
        "description_zh": "JEI 是物品与配方查询模组，打开背包就能搜索物品和合成表。",
        "project_url": "https://www.curseforge.com/minecraft/mc-mods/jei",
    },
    "search",
    "#7C6BE8",
)
make_mod(
    "iris-fabric-1.9.2.jar",
    "iris",
    "Iris Shaders",
    "1.9.2",
    "Modern shader support",
    {
        "description_zh": "Iris 让 Fabric 支持光影包，兼容大部分 OptiFine 光影并有更好的性能。",
        "project_url": "https://modrinth.com/mod/iris",
    },
    "sun",
    "#F08A5D",
)
make_mod(
    "fabric-api-0.140.2.jar",
    "fabric-api",
    "Fabric API",
    "0.140.2",
    "Essential hooks for Fabric mods",
    {
        "description_zh": "Fabric 模组的基础 API，大多数 Fabric 模组都需要它。",
        "project_url": "https://modrinth.com/mod/fabric-api",
    },
    "package",
    "#B08A5A",
)
make_mod(
    "create-fabric-6.0.6.jar",
    "create",
    "Create",
    "6.0.6",
    "Building and automation",
    {
        "description_zh": "机械动力：用齿轮、传送带和旋转机械搭建自动化装置。",
        "project_url": "https://modrinth.com/mod/create",
    },
    "settings",
    "#D97757",
)

# 服务端
server_root = demo_root / "servers" / "cat-survival"
server_root.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(server_root / "server.jar", "w") as zf:
    zf.writestr("version.json", json.dumps({"id": "1.21.1"}))
    zf.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")
(server_root / "eula.txt").write_text("eula=true\n", encoding="utf-8")
(server_root / "server.properties").write_text(
    "level-name=world\n"
    "server-port=25565\n"
    "online-mode=false\n"
    "motd=猫猫生存服 · 一起种田吧\n"
    "max-players=20\n"
    "view-distance=10\n"
    "simulation-distance=8\n"
    "difficulty=normal\n"
    "pvp=true\n"
    "white-list=false\n"
    "enable-command-block=true\n",
    encoding="utf-8",
)
(server_root / "world" / "playerdata").mkdir(parents=True, exist_ok=True)
(server_root / "world" / "region").mkdir(parents=True, exist_ok=True)
(server_root / "world" / "level.dat").write_bytes(b"level")
(server_root / "world" / "region" / "r.0.0.mca").write_bytes(b"region")
(server_root / "world" / "playerdata" / "abc.dat").write_bytes(b"player")
(server_root / "whitelist.json").write_text(
    json.dumps([{"uuid": "1", "name": "LFELKEEA"}]), encoding="utf-8"
)
(server_root / "ops.json").write_text(
    json.dumps([{"uuid": "1", "name": "LFELKEEA", "level": 4}]), encoding="utf-8"
)
(server_root / "plugins").mkdir(exist_ok=True)
(server_root / "plugins" / "EssentialsX.jar").write_bytes(b"jar")
(server_root / "plugins" / "EssentialsX").mkdir(exist_ok=True)
(server_root / "plugins" / "EssentialsX" / "config.yml").write_text(
    "teleport-cooldown: 0\n"
    "sethome-multiple:\n"
    "  default: 3\n"
    "  vip: 10\n"
    "spawn-if-no-home: true\n"
    "warn-on-build-disallow: true\n"
    "currency-symbol: '$'\n",
    encoding="utf-8",
)
(server_root / "plugins" / "LuckPerms").mkdir(exist_ok=True)
(server_root / "plugins" / "LuckPerms" / "config.yml").write_text(
    "server: global\n"
    "include-global: true\n"
    "sync-minutes: -1\n"
    "enable-ops: true\n"
    "storage-method: H2\n"
    "storage-details:\n"
    "  file: luckperms-h2\n",
    encoding="utf-8",
)
(server_root / "server-icon.png").write_bytes(
    icon_bytes("paw", "#E23A5E", 64)
)
server_info = ServerInfo(
    folder="cat-survival",
    name="猫猫生存服",
    kind="paper",
    mc_version="1.21.1",
    memory_mb=4096,
    port=25565,
    eula=True,
    installed=True,
    launch_kind="jar",
    launch_target="server.jar",
    paper_build=132,
    created_at="2026-10-20 10:00:00",
)
(server_root / "server.json").write_text(
    json.dumps(asdict(server_info), ensure_ascii=False, indent=2), encoding="utf-8"
)
server2 = ServerInfo(
    folder="creative-lab",
    name="创造实验室",
    kind="fabric",
    mc_version="1.21.11",
    memory_mb=6144,
    port=25566,
    eula=True,
    installed=True,
    launch_kind="jar",
    launch_target="server.jar",
    created_at="2026-10-22 20:00:00",
)
server2.path.mkdir(parents=True, exist_ok=True)
(server2.path / "server.jar").write_bytes(b"jar")
(server2.path / "server.properties").write_text(
    "level-name=creative\nserver-port=25566\nonline-mode=false\n"
    "motd=创造实验室 · 建筑与红石\nmax-players=10\n",
    encoding="utf-8",
)
(server2.path / "server.json").write_text(
    json.dumps(asdict(server2), ensure_ascii=False, indent=2), encoding="utf-8"
)
(server2.path / "mods").mkdir(exist_ok=True)

# 皮肤库
COLORS = [
    ("恶魔猫猫", (196, 38, 60), (40, 30, 40)),
    ("薄荷狐", (86, 190, 150), (34, 70, 60)),
    ("夜莓", (146, 96, 190), (44, 32, 58)),
    ("焦糖猫", (214, 150, 80), (80, 52, 32)),
    ("深海", (70, 120, 190), (28, 42, 70)),
]


def recolor_skin(src: Path, dst: Path, shirt: tuple, pants: tuple) -> None:
    image = Image.open(src).convert("RGBA")
    arr = np.array(image).astype(np.float32)
    alpha = arr[..., 3]
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    shirt_mask = (g > 70) & (b > 70) & (r < 95) & (np.abs(g - b) < 70) & (alpha > 0)
    pants_mask = (
        (b > 60) & (b >= r + 15) & (b >= g + 5) & (alpha > 0) & (~shirt_mask)
    )
    luma = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
    factor = 0.65 + 0.5 * luma
    for mask, color in ((shirt_mask, shirt), (pants_mask, pants)):
        for channel in range(3):
            arr[..., channel] = np.where(
                mask,
                np.clip(color[channel] * factor, 0, 255),
                arr[..., channel],
            )
    dst.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr.astype(np.uint8), "RGBA").save(dst)


base_skin = PROJECT / "skins" / "local-skin.png"
for index, (name, shirt, pants) in enumerate(COLORS):
    target = DEMO / f"{name}.png" if name == "恶魔猫猫" else root / "skins" / f"{name}.png"
    recolor_skin(base_skin, target, shirt, pants)

# 模组详情预览图
def promo_gallery() -> Path:
    width, height = 1280, 720
    image = Image.new("RGB", (width, height), "#1A1016")
    draw = ImageDraw.Draw(image)
    for y in range(height):
        blend = y / height
        color = (
            int(26 + 30 * blend),
            int(16 + 10 * blend),
            int(22 + 26 * blend),
        )
        draw.line([(0, y), (width, y)], fill=color)
    font_big = ImageFont.truetype(r"C:\Windows\Fonts\msyhbd.ttc", 74)
    font_small = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 30)
    draw.text((80, 250), "SODIUM", font=font_big, fill="#F0C060")
    draw.text(
        (80, 348), "渲染优化 · 更快更稳的区块加载", font=font_small, fill="#E8DADD"
    )
    oc = Image.open(PROJECT / "assets" / "chibi_oc.png").convert("RGBA")
    oc = oc.resize((520, 520))
    image.paste(oc, (760, 120), oc)
    draw.rectangle([(0, height - 6), (width, height)], fill="#E23A5E")
    path = root / "gallery.png"
    image.save(path)
    return path


gallery_path = promo_gallery()

# ---------------------------------------------------------------------------
# 主窗口

from darkitten.ui.main_window import MainWindow  # noqa: E402
from darkitten.ui.pages.download import DownloadPage  # noqa: E402

# 演示清单是手工喂进去的；若不屏蔽真实清单请求，后台线程会把它覆盖掉。
DownloadPage.load_manifest = lambda self, force=False: None  # type: ignore[assignment]

MainWindow.ensure_java_detected = lambda self, force=False: None  # type: ignore[assignment]
MainWindow._setup_tray = lambda self: None  # type: ignore[assignment]
MainWindow._check_feedback_reminder = lambda self: None  # type: ignore[assignment]

window = MainWindow(
    Config(
        game_dir=str(game),
        player_name="LFELKEEA",
        last_version="1.21.11",
        version_loaders={"1.21.11": fabric_id, "1.20.1": forge_id},
        memory_mb=8192,
        java_path="C:/Program Files/Eclipse Adoptium/jdk-21.0.8/bin/java.exe",
        update_auto_check=False,
        skin_path=str(local_skin),
        theme="dark",
        window_width=1600,
        window_height=1000,
        background_enabled=True,
        feedback_last_seen="2099-01-01",
    )
)
window.resize(1600, 1000)
window.apply_theme("dark")
window.java_installs = [
    JavaInfo(
        path="C:/Program Files/Eclipse Adoptium/jdk-21.0.8/bin/java.exe",
        version="21.0.8",
        major=21,
        arch="x64",
        vendor="Eclipse Adoptium",
    ),
    JavaInfo(
        path="C:/Program Files/Java/jdk-17.0.16/bin/java.exe",
        version="17.0.16",
        major=17,
        arch="x64",
        vendor="Microsoft",
    ),
    JavaInfo(
        path="C:/Program Files/Java/jdk-8.0.452/bin/java.exe",
        version="1.8.0_452",
        major=8,
        arch="x64",
        vendor="Oracle",
    ),
]
window.java_installs_changed.emit(window.java_installs)
window.select_version("1.21.11")
window.set_status("已就绪 · Java 21 · 8 GB 内存", "success")
window.home_page.hero_status.setText("● 准备就绪")
window.home_page.hero_status.setStyleSheet(
    "color: #B9F3C6; background: rgba(80, 200, 120, 40);"
    "border-radius: 9px; padding: 5px 10px; font-size: 11px; font-weight: 600;"
)
window.show()
wait(900)

# 首页
window.switch_page("home")
window.home_page.version_tabs.set_value("games", emit=True)
wait(350)
grab(window, "home_games.png")
window.home_page.version_tabs.set_value("loaders", emit=True)
wait(350)
grab(window, "home_loaders.png")

# 下载页
window.switch_page("download")
manifest_versions = []
for vid, vtype, date in [
    ("1.21.11", "release", "2026-10-20T10:00:00+08:00"),
    ("1.21.10", "release", "2026-10-07T10:00:00+08:00"),
    ("1.21.9", "release", "2026-09-24T10:00:00+08:00"),
    ("1.21.8", "release", "2026-09-02T10:00:00+08:00"),
    ("1.21.7", "release", "2026-08-19T10:00:00+08:00"),
    ("1.21.6", "release", "2026-08-05T10:00:00+08:00"),
    ("1.21.5", "release", "2026-07-15T10:00:00+08:00"),
    ("1.21.4", "release", "2026-06-17T10:00:00+08:00"),
    ("1.21.3", "release", "2026-05-28T10:00:00+08:00"),
    ("1.21.2", "release", "2026-05-14T10:00:00+08:00"),
    ("1.21.1", "release", "2025-12-10T10:00:00+08:00"),
    ("1.21", "release", "2025-10-01T10:00:00+08:00"),
    ("1.20.6", "release", "2025-06-13T10:00:00+08:00"),
    ("1.20.4", "release", "2024-12-07T10:00:00+08:00"),
    ("1.20.1", "release", "2023-06-12T10:00:00+08:00"),
    ("1.19.4", "release", "2023-03-14T10:00:00+08:00"),
    ("1.18.2", "release", "2022-02-28T10:00:00+08:00"),
    ("1.17.1", "release", "2021-07-06T10:00:00+08:00"),
    ("1.16.5", "release", "2021-01-15T10:00:00+08:00"),
    ("1.12.2", "release", "2017-09-18T10:00:00+08:00"),
    ("25w45a", "snapshot", "2026-10-28T10:00:00+08:00"),
    ("25w44a", "snapshot", "2026-10-21T10:00:00+08:00"),
]:
    manifest_versions.append(
        {
            "id": vid,
            "type": vtype,
            "releaseTime": date,
            "url": f"http://example.invalid/{vid}.json",
        }
    )
window.download_page._manifest_ready(
    {
        "latest": {"release": "1.21.11", "snapshot": "25w45a"},
        "versions": manifest_versions,
    },
    "",
)
window.download_page.select_version("1.21.10", scroll=False)
window.download_page.progress_box.setVisible(True)
window.download_page.progress.setValue(38)
window.download_page.progress_text.setText("正在下载资源文件 · 1.21.10")
window.download_page.log.setPlainText(
    "解析版本清单完成\n"
    "下载 client.jar  ·  已完成\n"
    "下载 libraries  ·  18 / 47\n"
    "校验 assets  ·  进行中 38%"
)
window.download_page.install_btn.setText("正在安装…")
window.download_page.install_btn.setEnabled(False)
window.download_page.cancel_btn.setVisible(True)
window.download_page.cancel_btn.setEnabled(True)
window.download_page.install_state.setText("安装中")
window.download_page.install_state.setProperty("badge", "blue")
try:
    from darkitten.ui.widgets import repolish

    repolish(window.download_page.install_state)
except Exception:
    pass
wait(400)
grab(window, "download.png")

# 加载器
window.switch_page("loaders")
wait(500)
grab(window, "loaders.png")

# 模组
window.switch_page("mods")
window.mods_page.refresh_mods()
wait(450)
grab(window, "mods.png")

# 模组下载对话框
mod_dialog_module = __import__(
    "darkitten.ui.mod_dialog", fromlist=["ModBrowserDialog", "_IMAGE_CACHE"]
)
mod_dialog_module.ModSearchWorker.start = lambda self: None  # type: ignore[assignment]
mod_dialog_module.ModProjectWorker.start = lambda self: None  # type: ignore[assignment]
mod_dialog_module.ModVersionsWorker.start = lambda self: None  # type: ignore[assignment]
dialog = mod_dialog_module.ModBrowserDialog(window)
window._mod_browser_dialog = dialog  # keep ref
dialog.show()
wait(300)
cover = icon_bytes("sparkles", "#E9B44C", 256)
cover_image = QImage()
cover_image.loadFromData(cover, "PNG")
gallery_image = QImage(str(gallery_path))
mod_dialog_module._IMAGE_CACHE["promo://cover/sodium"] = cover_image
mod_dialog_module._IMAGE_CACHE["promo://gallery/sodium"] = gallery_image
hits = [
    {
        "project_id": "sodium",
        "slug": "sodium",
        "title": "Sodium",
        "description": "Rendering engine",
        "author": "jellysquid3",
        "downloads": 41200000,
        "categories": ["optimization"],
        "icon_url": "promo://cover/sodium",
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
        "icon_url": "promo://cover/sodium",
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
        "icon_url": "promo://cover/sodium",
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
        "icon_url": "promo://cover/sodium",
        "project_url": "https://modrinth.com/mod/create",
    },
]
dialog._hits = hits
dialog._total = 4
dialog._render_results()
dialog._select_project("sodium", hits[0])
project = {
    "id": "sodium",
    "title": "Sodium",
    "description": "Rendering engine",
    "categories": ["optimization"],
    "downloads": 41200000,
    "followers": 186000,
    "updated": "2026-10-18T10:00:00+08:00",
    "loaders": ["fabric", "neoforge"],
    "game_versions": ["1.21.11", "1.21.10", "1.21.8", "1.20.1"],
    "license": {"name": "LGPL-3.0"},
    "body": "Sodium is a modern rendering engine for Minecraft which greatly improves performance and fixes many graphical issues.",
    "icon_url": "promo://cover/sodium",
    "gallery": [{"url": "promo://gallery/sodium", "title": "Sodium 渲染效果"}],
}
dialog._project_ready("sodium", project, "")
versions = [
    {
        "id": "v1",
        "version_number": "0.6.13+mc1.21.11",
        "version_type": "release",
        "files": [
            {
                "filename": "sodium-fabric-0.6.13+mc1.21.11.jar",
                "url": "https://example.invalid/sodium.jar",
                "size": 1048576,
                "primary": True,
            }
        ],
        "dependencies": [],
    }
]
dialog._versions_ready(dialog._versions_worker, "sodium", versions, "")
wait(500)
grab(dialog, "mod_browser.png")
dialog.close()
dialog.deleteLater()
wait(200)

# 皮肤
window.switch_page("skin")
wait(300)
for name, _shirt, _pants in COLORS[1:]:
    window.skin_page._import_to_library(root / "skins" / f"{name}.png")
window.skin_page._import_to_library(local_skin)
window.skin_page._apply_local_file(local_skin)
window.skin_page.file_edit.setText("skins/恶魔猫猫.png")
wait(500)
grab(window, "skin.png")

# 联机 P2P
window.switch_page("room")
wait(400)
panel = window.room_page.easytier_panel
room_code = None
try:
    from darkitten.easytier import RoomCode

    room_code = RoomCode.create()
except Exception:
    room_code = None
panel._set_badge(panel.host_badge, "房间开启", "green")
panel._set_status(
    panel.host_status,
    "P2P 房间已开启。把房间号发给朋友，保持游戏和启动器运行。",
    "success",
)
panel.room_code_label.setText(str(room_code or "U/4K7M-2QXA-9F3D-8W6P"))
panel.code_detail.setText("房间服务端口 43118 · 游戏端口 25565 · 公共节点 9 个")
panel.player_line.setText(
    "房间玩家：LFELKEEA（房主）、薄荷狐、夜莓 · 直连 2 · 中继 0 · 公共节点 9"
)
panel.result_box.setVisible(True)
panel.create_btn.setVisible(False)
panel.close_btn.setVisible(True)
panel.log_box.setVisible(True)
panel.log_box.setPlainText(
    "12:40:01  EasyTier 2.6.4 已启动\n"
    "12:40:02  已连接公共节点 sh-ct、xm-ct、cn-eo\n"
    "12:40:03  房间网络创建完成，等待玩家加入\n"
    "12:40:09  薄荷狐 已加入房间（直连）\n"
    "12:40:12  夜莓 已加入房间（直连）"
)
wait(350)
grab(window, "room_p2p.png")

# 樱花联机
from darkitten.sakura import SakuraNode, SakuraUser  # noqa: E402

window.room_page.mode_tabs.set_value("sakura", emit=True)
sakura_payload = {
    "room_code": "U/8Q2L-6NVK-3TRA-5Y7C",
    "address": "frp-cat.com:38214",
    "share_address": "cn-cq.frp-cat.com:38214",
    "tunnel_id": 12846,
    "node_name": "重庆电信 · 多线",
    "local_port": 25565,
    "frpc_version": "0.62.1",
}
page = window.room_page
page._user = SakuraUser(
    id=98241,
    name="LFELKEEA",
    realname=1,
    tunnel_limit=2,
    group_name="进阶版",
    traffic_used=318 * 1024 * 1024,
    traffic_left=int(19.7 * 1024**3),
)
page._nodes = {
    431: SakuraNode(
        id=431,
        name="重庆电信",
        host="cn-cq.frp-cat.com",
        description="内地多线节点",
        flag=(1 << 2) | (1 << 3) | (1 << 11),
    ),
    512: SakuraNode(
        id=512,
        name="上海移动",
        host="cn-sh.frp-cat.com",
        description="内地多线节点",
        flag=(1 << 2) | (1 << 3) | (1 << 11),
    ),
    708: SakuraNode(
        id=708,
        name="香港 BGP",
        host="hk.frp-cat.com",
        description="免备案节点",
        vip=1,
        flag=(1 << 2) | (1 << 11),
    ),
}
page._tunnels = []
page._token = "demo-sakura-token"
page._populate_nodes()
page.node_combo.setCurrentIndex(1)
page.token_edit.setText("demo-sakura-token")
page.token_edit.setCursorPosition(0)
page.token_edit.setPlaceholderText("已保存访问密钥（已加密）")
page.connect_btn.setText("重新验证")
page.account_line.setText(
    "已连接 · LFELKEEA · 已实名 · 今日 318 MB · 剩余 19.7 GB · 隧道 1/2"
)
page._room_payload = dict(sakura_payload)
page._show_room_result(sakura_payload)
page._set_badge(page.host_badge, "房间开启", "green")
page._set_status(page.host_status, "房间已开启，把房间号发给朋友就能一起玩。", "success")
page.log_box.setVisible(True)
page.log_box.setPlainText(
    "12:44:10  正在分配樱花穿透节点…\n"
    "12:44:11  已选择节点：重庆电信 · 多线\n"
    "12:44:12  创建隧道 #12846 成功\n"
    "12:44:13  frpc 0.62.1 已连接，本地端口 25565\n"
    "12:44:14  房间地址：frp-cat.com:38214"
)
page._sync_controls()
wait(400)
grab(window, "room_sakura.png")

# 服务端
from types import SimpleNamespace  # noqa: E402

window.switch_page("server")
# 让页面显示“运行中”，与下面的控制台日志保持一致。
window.server_page._processes["cat-survival"] = SimpleNamespace(
    state="running",
    process=SimpleNamespace(pid=48212),
    send=lambda command: None,
)
window.server_page.refresh_servers("cat-survival")
wait(500)
for line in [
    "[12:35:02] [Server thread/INFO]: Starting minecraft server version 1.21.1",
    "[12:35:03] [Server thread/INFO]: Loading properties",
    "[12:35:04] [Server thread/INFO]: Preparing level \"world\"",
    "[12:35:08] [Server thread/INFO]: Preparing spawn area: 92%",
    "[12:35:10] [Server thread/INFO]: Done (7.842s)! For help, type \"help\"",
    "[12:36:11] [Server thread/INFO]: LFELKEEA joined the game",
    "[12:36:18] [Server thread/INFO]: <LFELKEEA> 大家好啊",
    "[12:36:26] [Server thread/INFO]: <薄荷狐> 来种田！",
    "[12:37:02] [Server thread/INFO]: Saving the game",
]:
    window.server_page._append_console("cat-survival", line)
clean_demo_paths(window)
wait(300)
grab(window, "server_console.png")
window.server_page.tabs.set_value("settings", emit=True)
wait(350)
grab(window, "server_settings.png")

# 服务端配置编辑器
from darkitten.ui.server_config_dialog import ServerConfigDialog  # noqa: E402

config_dialog = ServerConfigDialog(window, server_info)
config_dialog.show()
wait(500)
clean_demo_paths(config_dialog)
grab(config_dialog, "server_config_properties.png")
config_dialog.tabs.set_value("plugins", emit=True)
wait(200)
# 选中 EssentialsX 配置
for index in range(config_dialog.config_list.count()):
    item = config_dialog.config_list.item(index)
    if "EssentialsX/config.yml" in item.text():
        config_dialog.config_list.setCurrentRow(index)
        break
clean_demo_paths(config_dialog)
wait(350)
grab(config_dialog, "server_config_plugins.png")
config_dialog.close()
config_dialog.deleteLater()
wait(200)

# 设置
window.switch_page("settings")
wait(350)
settings_scroll = window.settings_page.findChild(QScrollArea)
if settings_scroll is not None:
    settings_scroll.verticalScrollBar().setValue(0)
# 宣传图里用常见目录展示，避免暴露本机临时路径。
window.settings_page.game_dir.setText("D:/Games/.minecraft")
window.settings_page.game_dir.setCursorPosition(0)
wait(200)
grab(window, "settings_top.png")
if settings_scroll is not None:
    content = settings_scroll.widget()
    # 让“启动器设置”正好落在视口顶部，作为第二段干净的画面。
    if content is not None:
        target = next(
            (
                label
                for label in content.findChildren(QLabel)
                if label.text() == "启动器设置"
            ),
            None,
        )
        if target is not None:
            offset = target.mapTo(content, QPoint(0, 0)).y()
            bar = settings_scroll.verticalScrollBar()
            bar.setValue(max(0, offset - 12))
            wait(300)
            grab(window, "settings_mid.png")
            bar.setValue(0)
            wait(150)
    if content is not None:
        # 整页长图，供宣传片里做纵向平移，避免上下两张截图拼接时切到半张卡片。
        content.grab().save(str(OUT / "settings_full.png"))
        print("saved settings_full.png", content.width(), "x", content.height(), flush=True)
    bar = settings_scroll.verticalScrollBar()
    bar.setValue(bar.maximum())
    wait(300)
    grab(window, "settings_bottom.png")
    bar.setValue(0)
    wait(150)

# 下载任务
from darkitten.ui.download_center import DownloadTask  # noqa: E402

# 任务目标是展示文案，不指向本机真实目录，避免截图泄露工作区路径。
tasks = [
    DownloadTask(
        id=801,
        title="Sodium 0.6.13",
        content_type="mod",
        source="modrinth",
        target="D:/Games/.minecraft/versions/1.21.11/mods",
        progress=63,
        message="正在下载 sodium-fabric-0.6.13.jar",
        status="running",
    ),
    DownloadTask(
        id=802,
        title="Paper 1.21.1 build 132",
        content_type="server",
        source="official",
        target="D:/Servers/cat-survival",
        progress=100,
        message="服务端安装完成",
        status="success",
    ),
    DownloadTask(
        id=803,
        title="JEI 19.27.0",
        content_type="mod",
        source="curseforge",
        target="D:/Games/.minecraft/versions/1.21.11/mods",
        progress=100,
        message="已安装到 1.21.11/mods",
        status="success",
    ),
]
for task in tasks:
    window.download_center._tasks[task.id] = task
    window.download_center._order.append(task.id)
window.download_center.tasks_changed.emit()
window.download_chip.sync(window.download_center)
window.open_download_tasks()
wait(500)
if window._download_tasks_dialog is not None:
    grab(window._download_tasks_dialog, "download_tasks.png")
    window._download_tasks_dialog.close()
    wait(200)

# 快捷命令
from darkitten.ui.command_palette import CommandPalette  # noqa: E402

palette = CommandPalette(window, window._palette_commands())
palette.show()
wait(300)
palette.input.clear()
wait(300)
print("palette commands", palette.list.count(), flush=True)
grab(palette, "command_palette.png")
palette.close()
palette.deleteLater()
wait(150)

# 版本设置
from darkitten.ui.version_settings_dialog import VersionSettingsDialog  # noqa: E402

version_dialog = VersionSettingsDialog(window, "1.21.11")
version_dialog.show()
wait(400)
grab(version_dialog, "version_settings.png")
version_dialog.close()
version_dialog.deleteLater()
wait(150)

# 桌宠
from darkitten.ui.pet_dialog import PetDialog  # noqa: E402

PetDialog._load_manifest = lambda self: None  # type: ignore[assignment]
pet_dialog = PetDialog(window)
pet_dialog.version_badge.setText("v1.0.0 已安装")
pet_dialog.version_badge.setProperty("badge", "green")
try:
    from darkitten.ui.widgets import repolish

    repolish(pet_dialog.version_badge)
except Exception:
    pass
pet_dialog.size_value.setText("129.6 MB")
pet_dialog.release_value.setText("2026-10-27")
pet_dialog.path_value.setText("D:/Darkitten/pet/LFELKEEA")
pet_dialog.status_label.setText("已经是最新版本，点“启动桌宠”把它叫出来。")
pet_dialog.folder_btn.setEnabled(True)
pet_dialog.launch_btn.setEnabled(True)
pet_dialog.action_btn.setText("重新下载")
pet_dialog.action_btn.setEnabled(True)
pet_dialog.show()
wait(350)
grab(pet_dialog, "pet_dialog.png")
pet_dialog.close()
pet_dialog.deleteLater()
wait(150)

# 关闭选择
from darkitten.ui.dialogs import CloseChoiceDialog  # noqa: E402

close_dialog = CloseChoiceDialog(window)
close_dialog.show()
wait(300)
grab(close_dialog, "close_choice.png")
close_dialog.close()
close_dialog.deleteLater()
wait(150)

# 拖拽导入提示
window.switch_page("home")
window.home_page.version_tabs.set_value("games", emit=True)
wait(250)
window.drop_hint.move(
    QPoint(
        (window.width() - window.drop_hint.width()) // 2,
        (window.height() - window.drop_hint.height()) // 2,
    )
)
window.drop_hint.show()
window.drop_hint.raise_()
wait(250)
grab(window, "drag_import.png")
window.drop_hint.hide()

# 结束，直接退出避免后台线程与关闭确认
print("all shots done", flush=True)
window._force_quit = True
os._exit(0)
