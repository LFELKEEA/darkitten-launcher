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

from PySide6.QtWidgets import QApplication, QCheckBox, QLabel, QPushButton

from darkitten.config import Config
from darkitten.mod_conflicts import ModIssue
from darkitten.mod_updates import ModUpdate
from darkitten.mods import list_mods, write_mod_meta
from darkitten.ui.main_window import MainWindow
from darkitten.ui.mod_health_dialog import ModHealthDialog
from darkitten.ui.mod_update_dialog import ModUpdatesDialog

CHECKS: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name} FAILED {detail}")
    CHECKS.append(name)
    print(f"{name}: OK")


def make_mod(path: Path, modid: str, name: str, version: str) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "fabric.mod.json",
            json.dumps(
                {
                    "id": modid,
                    "name": name,
                    "version": version,
                    "description": "Test mod",
                }
            ),
        )


def fake_update(mod, *, version_label: str = "2.0.0") -> ModUpdate:
    content = f"{mod.name}-{version_label}".encode()
    import hashlib

    digest = hashlib.sha1(content).hexdigest()
    return ModUpdate(
        mod=mod,
        version={
            "id": f"version-{version_label}",
            "project_id": "proj-1",
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


def find_buttons(widget) -> list[QPushButton]:
    return widget.findChildren(QPushButton)


def find_labels(widget) -> list[str]:
    return [label.text() for label in widget.findChildren(QLabel)]


def main() -> None:
    MainWindow.ensure_java_detected = lambda self, force=False: None
    Config.save = lambda self: None
    app = QApplication.instance() or QApplication(sys.argv)

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
    make_mod(mods_dir / "sodium.jar", "sodium", "Sodium", "1.0.0")
    make_mod(mods_dir / "lithium.jar", "lithium", "Lithium", "1.0.0")
    write_mod_meta(
        mods_dir,
        "sodium.jar",
        {"title": "Sodium", "project_id": "AANobbMI", "source": "modrinth"},
    )
    write_mod_meta(
        mods_dir,
        "lithium.jar",
        {"title": "Lithium", "project_id": "gvQqBUqZ", "source": "modrinth"},
    )

    config = Config(
        game_dir=str(game),
        update_auto_check=False,
        last_version="1.21.11",
        version_loaders={"1.21.11": loader_id},
    )
    window = MainWindow(config)
    window.resize(1280, 820)
    window.apply_theme("dark")
    window.show()
    window.select_version("1.21.11")
    app.processEvents()

    page = window.mods_page
    page._check_health = lambda auto=False: None
    page.refresh_mods()
    app.processEvents()

    mods = list_mods(mods_dir)
    check("ui-mod-count", len(mods) == 2, str(len(mods)))
    sodium = next(item for item in mods if item.name == "Sodium")
    lithium = next(item for item in mods if item.name == "Lithium")
    update_sodium = fake_update(sodium)
    update_lithium = fake_update(lithium, version_label="2.1.0")
    page._updates = {
        sodium.filename: update_sodium,
        lithium.filename: update_lithium,
    }
    page.refresh_mods()
    app.processEvents()

    check("ui-update-all-visible", not page.update_all_btn.isHidden())
    check(
        "ui-update-all-text",
        "2" in page.update_all_btn.text(),
        page.update_all_btn.text(),
    )
    all_labels = [label.text() for label in page.findChildren(QLabel)]
    check("ui-update-badge", all_labels.count("可更新") == 2, str(all_labels))
    update_buttons = [
        button for button in page.findChildren(QPushButton)
        if button.text() == "更新"
    ]
    check("ui-update-buttons", len(update_buttons) == 2, str(len(update_buttons)))

    captured: list[ModUpdate] = []
    page._apply_updates = lambda updates: captured.extend(updates)
    update_buttons[0].click()
    app.processEvents()
    check("ui-update-button-signal", len(captured) == 1, str(len(captured)))

    dialog = ModUpdatesDialog(window, [update_sodium, update_lithium])
    check("ui-dialog-rows", len(dialog._rows) == 2)
    checks = dialog.findChildren(QCheckBox)
    check("ui-dialog-checks", len(checks) == 2, str(len(checks)))
    check("ui-dialog-initial-selected", len(dialog._checked()) == 2)
    checks[0].setChecked(False)
    check("ui-dialog-toggle", len(dialog._checked()) == 1)
    check("ui-dialog-button", "1" in dialog.update_btn.text(), dialog.update_btn.text())
    dialog._confirm()
    check("ui-dialog-confirmed", dialog.selected == [update_lithium])
    dialog.deleteLater()

    issue = ModIssue(
        "error",
        "缺少必需前置",
        "Sodium 需要前置「fabric-api」，但 mods 文件夹里没有找到。",
        "sodium.jar",
    )
    health = ModHealthDialog(window, [issue])
    check("ui-health-title", "1 个错误" in health.title_label.text(), health.title_label.text())
    check("ui-health-issue", "缺少必需前置" in find_labels(health))
    health._copy()
    report = QApplication.clipboard().text()
    check("ui-health-copy", "Sodium" in report and "fabric-api" in report, report)
    health.deleteLater()

    page._health_auto = True
    page._on_health_checked([issue], [], "")
    check("ui-health-badge", page.health_badge.text() == "1 个错误", page.health_badge.text())
    page._on_health_checked([], [], "")
    check("ui-health-clean", page.health_badge.text() == "环境正常", page.health_badge.text())

    window.close()
    app.processEvents()
    temp.cleanup()
    print(f"\n{len(CHECKS)} checks passed")


if __name__ == "__main__":
    main()
