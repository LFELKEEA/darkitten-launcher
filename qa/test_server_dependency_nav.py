from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from stellar.config import Config
from stellar.servers import ServerInfo
import stellar.ui.server_addons as server_addons
from stellar.ui.server_addons import ServerAddonDialog


class FakeMain(QWidget):
    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config
        self.versions: list = []
        self.selected_version = ""
        self.messages: list[tuple[str, str]] = []

    def toast(self, text: str, kind: str = "info") -> None:
        self.messages.append((text, kind))

    def open_path(self, _path) -> None:
        pass


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
                "size": 2048,
                "hashes": {},
            }
        ],
        "dependencies": deps or [],
    }


def dep_buttons(dialog: ServerAddonDialog) -> list[QPushButton]:
    return [
        button
        for button in dialog.dep_bar.findChildren(QPushButton)
        if button.text().startswith("前置：")
    ]


def back_buttons(dialog: ServerAddonDialog) -> list[QPushButton]:
    return [
        button
        for button in dialog.dep_bar.findChildren(QPushButton)
        if button.text() == "返回"
    ]


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    server_addons.ServerAddonSearchWorker.start = lambda self: None
    server_addons.ServerAddonVersionsWorker.start = lambda self: None
    server_addons.ModProjectWorker.start = lambda self: None
    server_addons.ModProjectsWorker.start = lambda self: None

    main_window = FakeMain(Config.load())
    server = ServerInfo(
        folder="test-server",
        name="Test",
        kind="fabric",
        mc_version="1.20.1",
        installed=True,
    )
    dialog = ServerAddonDialog(main_window, server)
    app.processEvents()
    ok = True

    dialog._dep_projects["arch"] = {
        "id": "arch",
        "slug": "architectury-api",
        "title": "Architectury API",
        "description": "跨加载器前置库",
        "downloads": 123456,
        "categories": ["library"],
        "icon_url": "",
    }
    dialog.dep_bar.update_projects(dialog._dep_projects)

    dep_version = make_version(
        "jei-1",
        "15.0.0",
        deps=[
            {
                "project_id": "arch",
                "version_id": "arch-1",
                "dependency_type": "required",
                "file_name": "architectury-9.2.14.jar",
            }
        ],
    )
    hit = {
        "project_id": "jei",
        "slug": "jei",
        "title": "JEI",
        "description": "物品管理器",
        "author": "mezz",
        "downloads": 1000,
        "categories": ["utility"],
        "icon_url": "",
    }
    dialog._select_project("jei", hit)
    dialog._versions_ready(dialog._versions_worker, "jei", [dep_version], "")
    app.processEvents()

    combo_ok = (
        isinstance(dialog.version_combo.currentData(), dict)
        and str(dialog.version_combo.currentData().get("id")) == "jei-1"
    )
    deps_ok = not dialog.dep_bar.isHidden() and len(dep_buttons(dialog)) == 1
    name_ok = bool(dep_buttons(dialog)) and "Architectury API" in dep_buttons(dialog)[0].text()
    print("server_version_and_dep_bar:", "OK" if combo_ok and deps_ok else "FAIL")
    print("server_dep_project_name:", "OK" if name_ok else "FAIL")
    ok &= combo_ok and deps_ok and name_ok

    dep_buttons(dialog)[0].click()
    app.processEvents()
    nav_ok = dialog._selected_project == "arch" and len(dialog._nav_stack) == 1
    print("server_open_dependency:", "OK" if nav_ok else "FAIL")
    ok &= nav_ok

    arch_version = make_version("arch-1", "9.2.14")
    newer = make_version("arch-2", "9.3.0")
    dialog._versions_ready(
        dialog._versions_worker, "arch", [newer, arch_version], ""
    )
    app.processEvents()
    pending_ok = (
        isinstance(dialog.version_combo.currentData(), dict)
        and str(dialog.version_combo.currentData().get("id")) == "arch-1"
    )
    back_ok = bool(back_buttons(dialog))
    print("server_pending_dep_version:", "OK" if pending_ok else "FAIL")
    print("server_back_button:", "OK" if back_ok else "FAIL")
    ok &= pending_ok and back_ok

    back_buttons(dialog)[0].click()
    app.processEvents()
    return_ok = dialog._selected_project == "jei" and not dialog._nav_stack
    print("server_back_to_project:", "OK" if return_ok else "FAIL")
    ok &= return_ok

    dialog._versions_ready(dialog._versions_worker, "jei", [dep_version], "")
    app.processEvents()
    restore_ok = (
        isinstance(dialog.version_combo.currentData(), dict)
        and str(dialog.version_combo.currentData().get("id")) == "jei-1"
        and not back_buttons(dialog)
        and len(dep_buttons(dialog)) == 1
    )
    print("server_restore_version_and_bar:", "OK" if restore_ok else "FAIL")
    ok &= restore_ok

    dialog.close()
    main_window.close()
    app.processEvents()
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
