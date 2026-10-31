from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.pop("QT_QPA_PLATFORM", None)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QScrollArea

from darkitten.config import Config
from darkitten.mods import read_mod
from darkitten.ui import theme
from darkitten.ui.dialogs import ConfirmDialog
from darkitten.ui.main_window import MainWindow
from darkitten.ui.theme import colors

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "qa" / "shots170"


def wait(ms: int = 250) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def save(widget, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    widget.repaint()
    wait(120)
    pixmap = widget.grab()
    path = OUT / name
    pixmap.save(str(path))
    print(f"saved {path.name} {pixmap.width()}x{pixmap.height()}", flush=True)


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    OUT.mkdir(parents=True, exist_ok=True)
    config = Config.load()
    app.setFont(QFont("Microsoft YaHei UI", 9))
    theme.set_current(config.theme)
    app.setStyleSheet(theme.stylesheet(config.theme))
    window = MainWindow(config)
    window.resize(1180, 760)
    window.show()
    wait(400)

    window.switch_page("home")
    window.home_page.select_version("1.21.1")
    window.game_running = False
    window.launch_active = False
    window.home_page.set_game_running(False)
    window.home_page.set_launching(False)
    window.set_status("准备就绪", "idle")
    window._update_action_button()
    wait(300)
    save(window, "01_home_versions.png")

    window.launch_active = True
    window.home_page.set_launching(True)
    window.set_status("正在检查游戏文件…", "busy")
    window._update_action_button()
    wait(250)
    save(window, "02_home_cancel_launch.png")

    window.launch_active = False
    window.game_running = True
    window._running_version = "1.21.1"
    window.home_page.set_game_running(True)
    window.set_status("游戏运行中", "success")
    window._update_action_button()
    wait(250)
    save(window, "03_home_close_game.png")

    window.game_running = False
    window.home_page.set_game_running(False)
    window._update_action_button()

    # 下载页取消安装状态
    window.switch_page("download")
    for _ in range(20):
        if window.download_page._manifest:
            break
        wait(150)
    window.download_page.select_version("1.21.1")
    wait(200)
    window.install_active = True
    window._install_cancelling = False
    window._install_version = "1.21.1"
    window.download_page._on_install_started("1.21.1")
    window.download_page._on_install_progress(46, "正在补全资源文件 1200/3911")
    window.install_worker = None
    window.set_status("1.21.1 · 正在补全资源文件 1200/3911", "busy")
    window._update_action_button()
    wait(300)
    save(window, "04_download_cancel.png")
    window.install_active = False
    window.download_page._on_install_finished(False, "下载已取消", "1.21.1")
    window._update_action_button()
    window.set_status("准备就绪", "idle")

    # 模组页与删除 Mod 确认
    window.home_page.select_version("1.21.1")
    window.switch_page("mods")
    window.mods_page.refresh_mods()
    wait(400)
    save(window, "05_mods_delete_rows.png")

    mod = read_mod(
        window.config.game_path / "versions" / "1.21.1" / "mods" / "Sodium.jar"
    )
    mod_dialog = ConfirmDialog(
        window,
        "删除 Mod",
        f"确定删除 {mod.filename} 吗？文件会移入系统回收站，可在回收站恢复。",
        primary_text="移入回收站",
        secondary_text="取消",
        icon_name="trash",
        icon_color=colors()["red"],
        details=f"文件：{mod.path}",
    )
    mod_dialog.setModal(False)
    mod_dialog.show()
    wait(250)
    save(mod_dialog, "06_mod_delete_confirm.png")
    mod_dialog.close()

    # 设置：下载线程与残留清理
    window.switch_page("settings")
    wait(300)
    scroll = window.settings_page.findChild(QScrollArea)
    if scroll is not None:
        bar = scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
    wait(300)
    save(window, "07_settings_download.png")

    # 删除版本确认
    version_dir = window.config.game_path / "versions" / "1.21.1"
    dialog = ConfirmDialog(
        window,
        "删除游戏版本",
        "确定删除版本 1.21.1 吗？文件会移入系统回收站，可在回收站恢复。",
        primary_text="移入回收站",
        secondary_text="取消",
        icon_name="trash",
        icon_color=colors()["red"],
        details=(
            f"目录：{version_dir}\n"
            "版本隔离已开启，此目录内的 mods、saves 和 config 也会一并移入回收站。"
        ),
    )
    dialog.setModal(False)
    dialog.show()
    wait(250)
    save(dialog, "08_version_delete_confirm.png")
    dialog.close()

    window.close()
    app.processEvents()
    print("SHOTS_OK", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
