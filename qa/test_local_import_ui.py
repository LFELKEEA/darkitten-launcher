from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QApplication, QDialog, QPushButton

from darkitten.config import Config
from darkitten.ui import local_import_dialog as local_dialog_module
from darkitten.ui.local_import_dialog import LocalImportDialog
from darkitten.ui.main_window import MainWindow
from darkitten.ui.pages import mods as mods_module
from darkitten.ui.pages.home import LoaderOverviewRow
from darkitten.ui.widgets import local_transfer_paths


def vanilla_json(version_id: str) -> dict:
    return {
        "id": version_id,
        "type": "release",
        "mainClass": "net.minecraft.client.main.Main",
        "assets": "1.21",
        "assetIndex": {"id": "1.21", "url": "http://example.invalid/a.json"},
        "javaVersion": {"majorVersion": 21},
    }


def write_version(game: Path, version_id: str) -> None:
    folder = game / "versions" / version_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{version_id}.json").write_text(
        json.dumps(vanilla_json(version_id)), encoding="utf-8"
    )
    (folder / f"{version_id}.jar").write_bytes(b"jar")


def write_loader(game: Path, profile_id: str, base_id: str) -> None:
    folder = game / "versions" / profile_id
    folder.mkdir(parents=True, exist_ok=True)
    data = {
        "id": profile_id,
        "inheritsFrom": base_id,
        "type": "release",
        "mainClass": "net.fabricmc.loader.impl.launch.knot.KnotClient",
        "libraries": [{"name": "net.fabricmc:fabric-loader:0.19.5"}],
        "javaVersion": {"majorVersion": 21},
    }
    (folder / f"{profile_id}.json").write_text(json.dumps(data), encoding="utf-8")
    (folder / f"{profile_id}.jar").write_bytes(b"loader")


def make_version_zip(path: Path) -> None:
    version = vanilla_json("DragCat")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("versions/DragCat/DragCat.json", json.dumps(version))
        zf.writestr("versions/DragCat/DragCat.jar", b"jar")


def mime_for(path: Path) -> QMimeData:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    return mime


def check(label: str, value: bool, detail: str = "") -> bool:
    print(f"{label}: {'OK' if value else 'FAIL'}" + (f" ({detail})" if detail else ""))
    return value


def wait_until(app: QApplication, predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    app.processEvents()
    return bool(predicate())


def main() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    ok = True
    original_save = Config.save
    Config.save = lambda self: None  # 使用临时目录，不碰真实配置
    root = Path(tempfile.mkdtemp(prefix="dkl_local_ui_"))
    try:
        game = root / "game"
        game.mkdir()
        write_version(game, "1.21.11")
        write_loader(game, "fabric-loader-0.19.5-1.21.11", "1.21.11")

        window = MainWindow(Config(game_dir=str(game), update_auto_check=False))
        window.resize(1180, 760)
        window.show()
        app.processEvents()

        # ---- 两个视图：游戏版本 / 加载器 ------------------------------
        home = window.home_page
        ok &= check("nav_rename", window.nav_buttons["download"].text() == "游戏版本")
        ok &= check(
            "tab_labels",
            home.version_tabs._buttons["games"].text() == "游戏版本"
            and home.version_tabs._buttons["loaders"].text() == "加载器",
        )
        ok &= check(
            "games_default",
            home._view == "games"
            and "1.21.11" in home._version_rows
            and not home.loader_install_btn.isVisible()
            and home.import_btn.isVisible(),
            f"view={home._view} rows={sorted(home._version_rows)}",
        )

        home.version_tabs.set_value("loaders", emit=True)
        app.processEvents()
        loader_rows = home.version_holder.findChildren(LoaderOverviewRow)
        ok &= check(
            "loaders_view",
            home._view == "loaders"
            and home.version_count.text() == "1 个加载器"
            and bool(loader_rows)
            and loader_rows[0].profile.id == "fabric-loader-0.19.5-1.21.11"
            and home.loader_install_btn.isVisible()
            and not home.import_btn.isVisible(),
            f"count={home.version_count.text()} rows={len(loader_rows)}",
        )
        if loader_rows:
            loader_rows[0].clicked.emit(loader_rows[0].profile.id)
            app.processEvents()
            ok &= check("loader_row_opens_workshop", window.current_page == "loaders")
            window.switch_page("home")
            app.processEvents()
        home.version_tabs.set_value("games", emit=True)
        app.processEvents()
        ok &= check("back_to_games", home._view == "games" and home.import_btn.isVisible())

        # ---- 拖拽过滤 -------------------------------------------------
        inbox = root / "inbox"
        inbox.mkdir()
        mod = inbox / "demo.jar"
        mod.write_bytes(b"mod")
        disabled = inbox / "old.jar.disabled"
        disabled.write_bytes(b"disabled")
        resource_zip = inbox / "pack.zip"
        with zipfile.ZipFile(resource_zip, "w") as zf:
            zf.writestr("pack.mcmeta", "{}")
        text_file = inbox / "note.txt"
        text_file.write_text("no", encoding="utf-8")

        accepted = local_transfer_paths(mime_for(mod))
        disabled_ok = local_transfer_paths(mime_for(disabled)) == [disabled]
        folder_ok = local_transfer_paths(mime_for(inbox)) == [inbox]
        text_rejected = not local_transfer_paths(mime_for(text_file))
        ok &= check(
            "drag_filter",
            accepted == [mod] and disabled_ok and folder_ok and text_rejected,
            f"accepted={accepted} disabled={disabled_ok} folder={folder_ok} text={text_rejected}",
        )

        dropped: list[list[Path]] = []
        original_local = window.open_local_import
        window.open_local_import = lambda paths=None, **kw: dropped.append(
            list(paths) if isinstance(paths, (list, tuple)) else [paths]
        )  # type: ignore[method-assign]
        try:
            drop_mime = mime_for(mod)
            drop = QDropEvent(
                QPointF(20, 20),
                Qt.CopyAction,
                drop_mime,
                Qt.LeftButton,
                Qt.NoModifier,
            )
            accepted_drop = window.eventFilter(window, drop)
        finally:
            window.open_local_import = original_local  # type: ignore[method-assign]
        ok &= check(
            "drop_routes_local",
            bool(accepted_drop) and dropped == [[mod]],
            f"accepted={accepted_drop} dropped={dropped}",
        )

        # ---- 统一入口把版本 ZIP 转给旧对话框 --------------------------
        version_zip = inbox / "drag-cat.zip"
        make_version_zip(version_zip)
        routed_zips: list[Path] = []
        original_zip = window.open_version_import
        window.open_version_import = routed_zips.append  # type: ignore[method-assign]
        try:
            window.open_local_import(version_zip)
        finally:
            window.open_version_import = original_zip  # type: ignore[method-assign]
        ok &= check("version_zip_routed", routed_zips == [version_zip], str(routed_zips))

        # ---- 内容导入：Mod 直接复制到 mods ---------------------------
        content_dialog = LocalImportDialog(window, initial_path=mod)
        target = str(window.config.version_game_path("1.21.11"))
        content_ok = (
            content_dialog._mode == "idle"
            and not content_dialog.preview.isHidden()
            and content_dialog.preview_badge.text().startswith("Mod")
            and content_dialog.version_combo.count() == 1
            and content_dialog.import_btn.isEnabled()
            and target in content_dialog.target_hint.text()
        )
        ok &= check(
            "content_dialog_preview",
            content_ok,
            f"badge={content_dialog.preview_badge.text()} versions={content_dialog.version_combo.count()}",
        )
        content_dialog._start_import()
        finished = wait_until(app, lambda: content_dialog._worker is None)
        target_mod = game / "versions" / "1.21.11" / "mods" / "demo.jar"
        ok &= check(
            "content_import",
            finished
            and content_dialog.result() == QDialog.Accepted
            and target_mod.is_file()
            and mod.is_file(),
            f"finished={finished} result={content_dialog.result()} exists={target_mod.is_file()}",
        )
        content_dialog.deleteLater()

        # ---- 版本文件夹导入：直接搬进 versions -----------------------
        source = root / "VersionCat"
        source.mkdir()
        (source / "VersionCat.json").write_text(
            json.dumps(vanilla_json("VersionCat")), encoding="utf-8"
        )
        (source / "VersionCat.jar").write_bytes(b"catjar")
        (source / "mods").mkdir()
        (source / "mods" / "extra.jar").write_bytes(b"extra")

        version_dialog = LocalImportDialog(window, initial_path=source)
        version_ok = (
            version_dialog._version_info is not None
            and not version_dialog.version_options.isHidden()
            and version_dialog.name_edit.text().startswith("VersionCat")
            and version_dialog.import_btn.isEnabled()
        )
        ok &= check(
            "version_dialog_preview",
            version_ok,
            f"name={version_dialog.name_edit.text()}",
        )
        version_dialog.name_edit.setText("LocalCat")
        version_dialog.complete.setChecked(False)
        version_dialog._start_import()
        version_finished = wait_until(app, lambda: version_dialog._worker is None)
        imported = game / "versions" / "LocalCat"
        imported_json = imported / "LocalCat.json"
        json_ok = False
        if imported_json.is_file():
            data = json.loads(imported_json.read_text(encoding="utf-8"))
            json_ok = data.get("id") == "LocalCat"
        leftovers = list((game / "versions").glob(".dkl-folder-*"))
        ok &= check(
            "version_folder_import",
            version_finished
            and version_dialog.result() == QDialog.Accepted
            and version_dialog.imported_version == "LocalCat"
            and imported_json.is_file()
            and json_ok
            and (imported / "LocalCat.jar").is_file()
            and (imported / "mods" / "extra.jar").is_file()
            and not leftovers,
            f"finished={version_finished} result={version_dialog.result()} "
            f"imported={version_dialog.imported_version} json={json_ok}",
        )
        version_dialog.deleteLater()
        app.processEvents()

        # ---- 页面按钮接入 -------------------------------------------
        executed: list[str] = []
        original_exec = local_dialog_module.LocalImportDialog.exec

        def fake_exec(self) -> int:
            executed.append(self.path_edit.text())
            return 0

        local_dialog_module.LocalImportDialog.exec = fake_exec  # type: ignore[method-assign]
        try:
            download_btn = next(
                (
                    button
                    for button in window.download_page.findChildren(QPushButton)
                    if button.text() == "本地导入"
                ),
                None,
            )
            if download_btn is not None:
                download_btn.click()
            app.processEvents()
            home.import_btn.click()
            app.processEvents()
        finally:
            local_dialog_module.LocalImportDialog.exec = original_exec  # type: ignore[method-assign]
        ok &= check(
            "page_buttons",
            download_btn is not None and len(executed) == 2,
            f"button={download_btn is not None} exec={executed}",
        )

        picked: list[list[Path]] = []
        original_pick = mods_module.QFileDialog.getOpenFileNames
        mods_module.QFileDialog.getOpenFileNames = staticmethod(
            lambda *args, **kwargs: ([str(mod)], "")
        )  # type: ignore[assignment]
        original_import = window.open_local_import
        window.open_local_import = lambda paths=None, **kw: picked.append(
            list(paths) if isinstance(paths, (list, tuple)) else [paths]
        )  # type: ignore[method-assign]
        try:
            window.mods_page.add_mods()
        finally:
            mods_module.QFileDialog.getOpenFileNames = original_pick  # type: ignore[assignment]
            window.open_local_import = original_import  # type: ignore[method-assign]
        ok &= check(
            "mods_add_contents",
            picked == [[mod]],
            f"picked={picked}",
        )

        window.close()
        app.processEvents()
    finally:
        Config.save = original_save
        shutil.rmtree(root, ignore_errors=True)

    print("RESULT:", "PASS" if ok else "FAIL")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
