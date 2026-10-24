from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from stellar.config import Config
import stellar.ui.mod_dialog as mod_dialog
from stellar.ui.mod_dialog import ModBrowserDialog


class FakeMain(QWidget):
    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config
        self.versions: list = []
        self.selected_version = ""
        self.messages: list[tuple[str, str]] = []

    def game_version_summaries(self):
        return list(self.versions)

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


def dep_buttons(dialog: ModBrowserDialog) -> list[QPushButton]:
    return [
        button
        for button in dialog.dep_bar.findChildren(QPushButton)
        if button.text().startswith("前置：")
    ]


def back_buttons(dialog: ModBrowserDialog) -> list[QPushButton]:
    return [
        button
        for button in dialog.dep_bar.findChildren(QPushButton)
        if button.text() == "返回"
    ]


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    # 测试不联网：所有后台 worker 只记录，不真正启动线程。
    mod_dialog.ModSearchWorker.start = lambda self: None
    mod_dialog.ModProjectWorker.start = lambda self: None
    mod_dialog.ModVersionsWorker.start = lambda self: None
    mod_dialog.ModProjectsWorker.start = lambda self: None

    main_window = FakeMain(Config.load())
    dialog = ModBrowserDialog(main_window)
    app.processEvents()
    ok = True

    dlg = dialog
    dlg._dep_projects["arch"] = {
        "id": "arch",
        "slug": "architectury-api",
        "title": "Architectury API",
        "description": "跨加载器前置库",
        "downloads": 123456,
        "categories": ["library"],
        "icon_url": "",
    }
    dlg.dep_bar.update_projects(dlg._dep_projects)

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
    dlg._select_project("jei", hit)
    worker = dlg._versions_worker
    dlg._versions_ready(worker, "jei", [dep_version], "")
    app.processEvents()

    combo_ok = (
        isinstance(dlg.version_combo.currentData(), dict)
        and str(dlg.version_combo.currentData().get("id")) == "jei-1"
    )
    deps_visible = not dlg.dep_bar.isHidden() and len(dep_buttons(dlg)) == 1
    dep_name_ok = bool(dep_buttons(dlg)) and "Architectury API" in dep_buttons(dlg)[0].text()
    print("version_and_dep_bar:", "OK" if combo_ok and deps_visible else "FAIL")
    print("dep_project_name:", "OK" if dep_name_ok else "FAIL")
    ok &= combo_ok and deps_visible and dep_name_ok

    # 点击前置按钮：进入前置项目，并自动定位到声明的版本。
    dep_buttons(dlg)[0].click()
    app.processEvents()
    nav_ok = dlg._selected_project == "arch" and len(dlg._nav_stack) == 1
    print("open_dependency:", "OK" if nav_ok else "FAIL")
    ok &= nav_ok

    arch_version = make_version("arch-1", "9.2.14")
    arch_version_other = make_version("arch-2", "9.3.0")
    dlg._versions_ready(
        dlg._versions_worker, "arch", [arch_version_other, arch_version], ""
    )
    app.processEvents()
    pending_ok = (
        isinstance(dlg.version_combo.currentData(), dict)
        and str(dlg.version_combo.currentData().get("id")) == "arch-1"
    )
    back_ok = bool(back_buttons(dlg))
    print("pending_dep_version:", "OK" if pending_ok else "FAIL")
    print("back_button:", "OK" if back_ok else "FAIL")
    ok &= pending_ok and back_ok

    # 返回原 Mod，并恢复之前选择的文件版本。
    back_buttons(dlg)[0].click()
    app.processEvents()
    return_ok = dlg._selected_project == "jei" and not dlg._nav_stack
    print("back_to_project:", "OK" if return_ok else "FAIL")
    ok &= return_ok

    dlg._versions_ready(dlg._versions_worker, "jei", [dep_version], "")
    app.processEvents()
    restore_ok = (
        isinstance(dlg.version_combo.currentData(), dict)
        and str(dlg.version_combo.currentData().get("id")) == "jei-1"
        and not back_buttons(dlg)
        and len(dep_buttons(dlg)) == 1
    )
    print("restore_version_and_bar:", "OK" if restore_ok else "FAIL")
    ok &= restore_ok

    dialog.close()
    main_window.close()
    app.processEvents()
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
