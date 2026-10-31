from __future__ import annotations

import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QWidget

from darkitten.config import Config
from darkitten.descriptions import project_zh_intro
from darkitten.mods import (
    load_mod_meta,
    mod_meta_icon_path,
    read_mod,
    remember_downloaded_mod,
    write_mod_meta,
)
from darkitten.servers import ServerInfo, save_server
from darkitten.ui import download_center as dc
from darkitten.ui.download_center import (
    DownloadCenter,
    DownloadTask,
    TopDownloadChip,
)
from darkitten.ui.mod_dialog import ModBrowserDialog, ModResultRow
from darkitten.ui.pages.server import _server_icon_pixmap
from darkitten.versions import VersionSummary

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"{name}: OK{(' (' + detail + ')') if detail else ''}")
    else:
        FAIL += 1
        print(f"{name}: FAIL{(' (' + detail + ')') if detail else ''}")


def png_bytes(size: int = 64, color: str = "#e84545") -> bytes:
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(QColor(color))
    from PySide6.QtCore import QBuffer, QIODevice

    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def make_mod_jar(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "fabric.mod.json",
            json.dumps(
                {
                    "id": "demo_mod",
                    "name": "Demo Mod",
                    "version": "1.0.0",
                    "description": "A demo mod.",
                    "icon": "assets/demo.png",
                }
            ),
        )
        zf.writestr("assets/demo.png", png_bytes(64, "#3fb3aa"))


class FakeMain(QWidget):
    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config
        self.versions = [VersionSummary(id="1.21.1")]
        self.selected_version = "1.21.1"
        self.messages: list[tuple[str, str]] = []
        self.download_center = DownloadCenter(self)

    def game_version_summaries(self):
        return list(self.versions)

    def loader_profile_for(self, _version_id):
        return None

    def toast(self, text: str, kind: str = "info") -> None:
        self.messages.append((text, kind))

    def open_path(self, _path) -> None:
        pass

    def log(self, _message: str) -> None:
        pass


def descriptions_test() -> None:
    sodium = {
        "title": "Sodium",
        "slug": "sodium",
        "description": "Rendering engine",
        "categories": ["optimization"],
        "loaders": ["fabric"],
        "game_versions": ["1.21.1"],
        "downloads": 1234567,
    }
    text = project_zh_intro(sodium, loader="fabric")
    check("description_curated_zh", "钠" in text and "帧率" in text, text[:20])
    unknown = {
        "title": "Example Mod",
        "description": "English only",
        "categories": ["adventure", "decoration"],
        "loaders": ["neoforge"],
        "game_versions": ["1.20.1"],
        "author": "Dev",
        "downloads": 5000,
    }
    generated = project_zh_intro(unknown, content_type="mod", loader="neoforge")
    check(
        "description_generated_zh",
        "Example Mod" in generated and "冒险" in generated and "NeoForge" in generated,
        generated,
    )


def mod_meta_test() -> None:
    temp = tempfile.TemporaryDirectory()
    mods_dir = Path(temp.name) / "mods"
    jar = mods_dir / "demo_mod.jar"
    make_mod_jar(jar)

    info = read_mod(jar)
    check(
        "jar_icon_extracted",
        info.icon_bytes.startswith(b"\x89PNG") and len(info.icon_bytes) > 50,
        f"{len(info.icon_bytes)} bytes",
    )
    write_mod_meta(
        mods_dir,
        jar.name,
        {
            "title": "演示 Mod",
            "project_id": "demo",
            "description": "A demo mod.",
            "description_zh": "这是一个用于测试的中文介绍。",
            "project_url": "https://example.invalid/demo",
            "source_label": "Modrinth",
        },
        info.icon_bytes,
    )
    meta = load_mod_meta(mods_dir, jar.name)
    saved_info = read_mod(jar)
    check("sidecar_written", meta.get("description_zh") == "这是一个用于测试的中文介绍。")
    check("sidecar_icon_saved", mod_meta_icon_path(mods_dir, jar.name).is_file())
    check("sidecar_icon_used", saved_info.icon_bytes == info.icon_bytes)
    check("sidecar_intro_used", saved_info.intro == "这是一个用于测试的中文介绍。")
    check("sidecar_project_url", saved_info.project_url.endswith("/demo"))
    temp.cleanup()


def download_center_test(app: QApplication) -> DownloadTask | None:
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    mods_dir = root / "mods"
    main = FakeMain(Config(game_dir=str(root / "game")))
    center = main.download_center

    def fake_download(version, target_dir, progress_cb=None, cancel_event=None, threads=8):
        target = Path(target_dir) / "demo_mod.jar"
        target.parent.mkdir(parents=True, exist_ok=True)
        if progress_cb:
            progress_cb(15, "开始下载")
        make_mod_jar(target)
        if progress_cb:
            progress_cb(100, "下载完成")
        return target

    import darkitten.workers as workers

    original = workers.download_mod_file
    workers.download_mod_file = fake_download
    finished: list[DownloadTask] = []
    loop = QEventLoop()
    center.task_finished.connect(lambda task: (finished.append(task), loop.quit()))
    QTimer.singleShot(6000, loop.quit)
    task = center.start_mod_download(
        {"files": [{"filename": "demo_mod.jar"}]},
        mods_dir,
        threads=2,
        title="演示 Mod",
        content_type="mod",
        source="modrinth",
        metadata={
            "title": "演示 Mod",
            "project_id": "demo",
            "description": "A demo mod.",
            "description_zh": "下载后保存的中文介绍。",
            "content_type": "mod",
        },
    )
    loop.exec()
    workers.download_mod_file = original

    check("download_center_finished", bool(finished) and finished[0].id == task.id)
    check("download_center_success", task.status == dc.TASK_SUCCESS, task.status)
    check("download_center_filename", task.filename == "demo_mod.jar")
    saved = read_mod(mods_dir / "demo_mod.jar")
    check("download_center_icon", len(saved.icon_bytes) > 50, f"{len(saved.icon_bytes)} bytes")
    check("download_center_zh", saved.intro == "下载后保存的中文介绍。")

    chip = TopDownloadChip()
    chip.resize(196, 38)
    task.progress = 42
    task.status = dc.TASK_RUNNING
    task.message = "正在下载资源"
    center._tasks[task.id] = task
    center._order = [task.id]
    chip.sync(center)
    check(
        "chip_running",
        not chip.isHidden() and chip.percent_label.text() == "42%",
        chip.status_label.text(),
    )
    check("chip_progress", chip.progress.value() == 42)

    task.status = dc.TASK_SUCCESS
    task.progress = 100
    task.message = "下载完成"
    chip.sync(center)
    check(
        "chip_finished",
        chip.status_label.text() == "已完成" and chip._hide_timer.isActive(),
        chip.status_label.text(),
    )
    chip.close()
    temp.cleanup()
    return None


def dialog_detach_test(app: QApplication) -> None:
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    main = FakeMain(Config(game_dir=str(root / "game")))
    import darkitten.ui.mod_dialog as mod_dialog

    old_search = mod_dialog.ModSearchWorker.start
    mod_dialog.ModSearchWorker.start = lambda self: None
    dialog = ModBrowserDialog(main)
    task = DownloadTask(
        id=77,
        title="仍在下载",
        content_type="mod",
        source="modrinth",
        target=root / "mods",
    )
    main.download_center._tasks[77] = task
    main.download_center._order = [77]
    dialog._download_task = task
    dialog._download_bound = True
    cancelled: list[int] = []
    main.download_center.cancel = lambda task_id: cancelled.append(task_id)
    dialog.reject()
    mod_dialog.ModSearchWorker.start = old_search

    check("dialog_close_keeps_task", not cancelled, f"cancel={cancelled}")
    check("dialog_close_detaches", dialog._download_task is None)
    check(
        "dialog_close_toast",
        any("顶栏" in text for text, _kind in main.messages),
        str(main.messages),
    )
    check("task_still_running", main.download_center.has_running())
    temp.cleanup()


def row_intro_test(app: QApplication) -> None:
    hit = {
        "project_id": "sodium",
        "title": "Sodium",
        "description": "Rendering engine",
        "categories": ["optimization"],
        "author": "jellysquid3",
        "downloads": 1000,
    }
    row = ModResultRow(hit, "fabric")
    check("row_zh_intro", "钠" in row.desc_label.full_text(), row.desc_label.full_text()[:24])
    check("row_original_tip", row.desc_label.toolTip() == "Rendering engine")
    check("row_category_zh", "优化" in row.meta_label.full_text(), row.meta_label.full_text())


def server_icon_test(app: QApplication) -> None:
    import darkitten.servers as servers

    temp = tempfile.TemporaryDirectory()
    old_dir = servers.SERVERS_DIR
    servers.SERVERS_DIR = Path(temp.name) / "servers"
    try:
        info = ServerInfo(
            folder="RedServer",
            name="RedServer",
            kind="paper",
            mc_version="1.21.1",
            installed=True,
        )
        info.path.mkdir(parents=True)
        (info.path / "server-icon.png").write_bytes(png_bytes(64, "#ff2020"))
        save_server(info)

        pixmap = _server_icon_pixmap(info, 38, 9)
        check("server_icon_pixmap", pixmap is not None and pixmap.size().width() == 38)
        if pixmap is not None:
            color = pixmap.toImage().pixelColor(19, 19)
            check(
                "server_icon_color",
                color.red() > 200 and color.green() < 80,
                f"{color.red()},{color.green()},{color.blue()}",
            )

        Config.save = lambda self: None
        from darkitten.ui.main_window import MainWindow

        window = MainWindow(Config(game_dir=str(Path(temp.name) / "game"), update_auto_check=False))
        try:
            window.server_page.refresh_servers("RedServer")
            check(
                "server_row_custom_icon",
                bool(window.server_page._rows)
                and window.server_page._rows[0].icon_label.pixmap().toImage().pixelColor(19, 19).red()
                > 200,
            )
            detail_color = window.server_page.detail_icon.pixmap().toImage().pixelColor(23, 23)
            check(
                "server_detail_custom_icon",
                detail_color.red() > 200 and detail_color.green() < 80,
                f"{detail_color.red()},{detail_color.green()},{detail_color.blue()}",
            )
        finally:
            window.close()
    finally:
        servers.SERVERS_DIR = old_dir
        temp.cleanup()


def fallback_icon_test(app: QApplication) -> None:
    temp = tempfile.TemporaryDirectory()
    info = ServerInfo(
        folder="NoIcon",
        name="NoIcon",
        kind="fabric",
        mc_version="1.21.1",
    )
    check("server_icon_fallback_none", _server_icon_pixmap(info, 38, 9) is None)
    temp.cleanup()


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    descriptions_test()
    mod_meta_test()
    download_center_test(app)
    dialog_detach_test(app)
    row_intro_test(app)
    server_icon_test(app)
    fallback_icon_test(app)
    print(f"RESULT: {'PASS' if FAIL == 0 else 'FAIL'} ({PASS} ok, {FAIL} failed)")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
