from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.pop("QT_QPA_PLATFORM", None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QPushButton

from stellar.config import Config
from stellar.ui import theme
from stellar.ui.main_window import MainWindow
import stellar.ui.mod_dialog as mod_dialog

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "qa" / "shots171"


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


def make_version(version_id: str, number: str, deps=None) -> dict:
    return {
        "id": version_id,
        "version_number": number,
        "version_type": "release",
        "files": [
            {
                "primary": True,
                "url": "https://example.invalid/test.jar",
                "filename": f"{version_id}.jar",
                "size": 5_242_880,
                "hashes": {},
            }
        ],
        "dependencies": deps or [],
    }


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    mod_dialog.ModSearchWorker.start = lambda self: None
    mod_dialog.ModProjectWorker.start = lambda self: None
    mod_dialog.ModVersionsWorker.start = lambda self: None
    mod_dialog.ModProjectsWorker.start = lambda self: None

    config = Config.load()
    app.setFont(QFont("Microsoft YaHei UI", 9))
    theme.set_current(config.theme)
    app.setStyleSheet(theme.stylesheet(config.theme))
    window = MainWindow(config)
    window.resize(1180, 760)
    window.show()
    wait(350)
    window.set_status("准备就绪", "idle")

    dialog = mod_dialog.ModBrowserDialog(window)
    dialog.setModal(False)
    dialog.show()
    wait(300)
    app.processEvents()
    dialog.status_label.setText("")

    hits = [
        {
            "project_id": "jei",
            "slug": "jei",
            "title": "Just Enough Items",
            "description": "查看物品配方与用途的物品管理器",
            "author": "mezz",
            "downloads": 312_000_000,
            "categories": ["utility", "storage"],
            "icon_url": "",
        },
        {
            "project_id": "arch",
            "slug": "architectury-api",
            "title": "Architectury API",
            "description": "供多加载器 Mod 使用的前置库",
            "author": "shedaniel",
            "downloads": 148_000_000,
            "categories": ["library"],
            "icon_url": "",
        },
        {
            "project_id": "sodium",
            "slug": "sodium",
            "title": "Sodium",
            "description": "现代化渲染优化 Mod",
            "author": "jellysquid3",
            "downloads": 92_000_000,
            "categories": ["optimization"],
            "icon_url": "",
        },
    ]
    dialog._hits = hits
    dialog._total = len(hits)
    dialog._page = 0
    dialog._render_results()

    dialog._dep_projects.update(
        {
            "arch": {
                "id": "arch",
                "slug": "architectury-api",
                "title": "Architectury API",
                "description": "供多加载器 Mod 使用的前置库",
                "downloads": 148_000_000,
                "categories": ["library"],
                "icon_url": "",
            }
        }
    )
    dialog.dep_bar.update_projects(dialog._dep_projects)

    dialog._select_project("jei", hits[0])
    version = make_version(
        "jei-1",
        "15.2.0.27",
        deps=[
            {
                "project_id": "arch",
                "version_id": "arch-1",
                "dependency_type": "required",
                "file_name": "architectury-9.2.14-fabric.jar",
            }
        ],
    )
    dialog._project_ready(
        "jei",
        {
            "id": "jei",
            "slug": "jei",
            "title": "Just Enough Items",
            "description": "查看物品配方与用途的物品管理器",
            "downloads": 312_000_000,
            "followers": 12_400,
            "updated": "2026-10-18T10:20:00Z",
            "categories": ["utility", "storage"],
            "loaders": ["fabric", "forge", "neoforge"],
            "game_versions": ["1.21.1", "1.20.1", "1.19.4", "1.18.2"],
            "license": {"name": "MIT"},
            "body": "Just Enough Items 是一个物品与配方查看器。安装后按 R 查看配方，按 U 查看用途。",
            "gallery": [],
            "icon_url": "",
        },
        "",
    )
    dialog._versions_ready(dialog._versions_worker, "jei", [version], "")
    wait(300)
    save(dialog, "01_mod_required_dependency.png")

    dep_button = next(
        (
            button
            for button in dialog.dep_bar.findChildren(QPushButton)
            if button.text().startswith("前置：")
        ),
        None,
    )
    if dep_button is not None:
        dep_button.click()
        app.processEvents()
        dialog._project_ready(
            "arch",
            {
                "id": "arch",
                "slug": "architectury-api",
                "title": "Architectury API",
                "description": "供多加载器 Mod 使用的前置库",
                "downloads": 148_000_000,
                "followers": 8_300,
                "updated": "2026-10-12T08:00:00Z",
                "categories": ["library"],
                "loaders": ["fabric", "forge", "neoforge"],
                "game_versions": ["1.21.1", "1.20.1", "1.19.4"],
                "license": {"name": "LGPL-3.0"},
                "body": "Architectury API 为跨加载器 Mod 提供统一抽象层。",
                "gallery": [],
                "icon_url": "",
            },
            "",
        )
        arch_versions = [
            make_version("arch-2", "9.3.0"),
            make_version("arch-1", "9.2.14"),
        ]
        dialog._versions_ready(
            dialog._versions_worker, "arch", arch_versions, ""
        )
        wait(300)
        save(dialog, "02_dependency_jump.png")

        back_button = next(
            (
                button
                for button in dialog.dep_bar.findChildren(QPushButton)
                if button.text() == "返回"
            ),
            None,
        )
        if back_button is not None:
            back_button.click()
            app.processEvents()
            dialog._project_ready(
                "jei",
                {
                    "id": "jei",
                    "slug": "jei",
                    "title": "Just Enough Items",
                    "description": "查看物品配方与用途的物品管理器",
                    "downloads": 312_000_000,
                    "followers": 12_400,
                    "updated": "2026-10-18T10:20:00Z",
                    "categories": ["utility", "storage"],
                    "loaders": ["fabric", "forge", "neoforge"],
                    "game_versions": ["1.21.1", "1.20.1", "1.19.4", "1.18.2"],
                    "license": {"name": "MIT"},
                    "body": "Just Enough Items 是一个物品与配方查看器。",
                    "gallery": [],
                    "icon_url": "",
                },
                "",
            )
            dialog._versions_ready(dialog._versions_worker, "jei", [version], "")
            wait(300)
            save(dialog, "03_back_to_mod.png")

    dialog.close()
    window.close()
    app.processEvents()
    print("SHOTS_OK", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
