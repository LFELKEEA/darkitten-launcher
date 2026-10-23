from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDropEvent
from PySide6.QtWidgets import QApplication

from stellar.config import Config
from stellar.ui.main_window import MainWindow
from stellar.ui.version_zip_dialog import VersionZipDialog


def make_zip(path: Path) -> None:
    version = {
        "id": "DragCat",
        "type": "release",
        "mainClass": "net.minecraft.client.main.Main",
        "assets": "1.21",
        "assetIndex": {"id": "1.21", "url": "http://example.invalid/a.json"},
        "javaVersion": {"majorVersion": 21},
    }
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("versions/DragCat/DragCat.json", json.dumps(version))
        zf.writestr("versions/DragCat/DragCat.jar", b"jar")
        zf.writestr("versions/DragCat/mods/demo.jar", b"mod")


def make_mrpack(path: Path) -> None:
    data = b"fake mod"
    manifest = {
        "game": "minecraft",
        "formatVersion": 1,
        "versionId": "1.0.0",
        "name": "MRCat Pack",
        "summary": "拖拽测试整合包",
        "dependencies": {"minecraft": "1.21.11", "fabric-loader": "0.19.5"},
        "files": [
            {
                "path": "mods/demo.jar",
                "hashes": {"sha1": hashlib.sha1(data).hexdigest()},
                "downloads": ["https://example.invalid/demo.jar"],
                "fileSize": len(data),
            }
        ],
    }
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("modrinth.index.json", json.dumps(manifest, ensure_ascii=False))
        zf.writestr("overrides/config/demo.json", "{}")


def mime_for(path: Path) -> QMimeData:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    return mime


def check(label: str, value: bool, detail: str = "") -> bool:
    print(f"{label}: {'OK' if value else 'FAIL'}" + (f" ({detail})" if detail else ""))
    return value


def main() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    ok = True
    original_save = Config.save
    Config.save = lambda self: None  # 测试使用临时目录，不覆盖真实配置
    root = Path(tempfile.mkdtemp(prefix="dkl_zip_ui_"))
    try:
        game = root / "game"
        game.mkdir()
        archive = root / "drag-cat.zip"
        make_zip(archive)
        ignored = root / "note.txt"
        ignored.write_text("not an archive", encoding="utf-8")

        window = MainWindow(Config(game_dir=str(game)))
        window.resize(1000, 700)
        window.show()
        app.processEvents()

        paths = window._archive_paths(mime_for(archive))
        ok &= check("archive_paths", paths == [archive], str(paths))
        ok &= check("archive_filter", not window._archive_paths(mime_for(ignored)))

        mrpack = root / "drag-cat.mrpack"
        make_mrpack(mrpack)
        mr_as_zip = root / "mr-as-zip.zip"
        make_mrpack(mr_as_zip)
        mr_paths = window._archive_paths(mime_for(mrpack))
        ok &= check("mrpack_archive_paths", mr_paths == [mrpack], str(mr_paths))

        from stellar.ui.pack_dialog import PackImportDialog

        mr_dialog = PackImportDialog(window, initial_path=mrpack)
        mr_dialog_ok = (
            not mr_dialog.preview.isHidden()
            and mr_dialog.import_btn.isEnabled()
            and mr_dialog._kind == "modrinth"
            and mr_dialog.preview_name.text() == "MRCat Pack"
            and mr_dialog.name_edit.text().startswith("MRCat Pack")
            and "Modrinth" in mr_dialog.preview_meta.text()
        )
        ok &= check("mrpack_dialog_preview", mr_dialog_ok, mr_dialog.preview_name.text())
        mr_dialog.deleteLater()

        routed_packs: list[Path] = []
        original_pack_import = window.open_pack_import
        window.open_pack_import = routed_packs.append  # type: ignore[method-assign]
        try:
            window.open_version_import(mrpack)
            window.open_version_import(mr_as_zip)
        finally:
            window.open_pack_import = original_pack_import  # type: ignore[method-assign]
        ok &= check(
            "mrpack_routed",
            routed_packs == [mrpack, mr_as_zip],
            str(routed_packs),
        )

        captured: list[Path] = []
        original_import = window.open_version_import
        window.open_version_import = captured.append  # type: ignore[method-assign]
        target = window
        enter_mime = mime_for(archive)
        enter = QDragEnterEvent(
            QPoint(20, 20), Qt.CopyAction, enter_mime, Qt.LeftButton, Qt.NoModifier
        )
        QApplication.sendEvent(target, enter)
        hint_ok = window.drop_hint.isVisible()
        leave = QDragLeaveEvent()
        QApplication.sendEvent(target, leave)
        hint_hidden = not window.drop_hint.isVisible()
        ok &= check("drag_hint", hint_ok and hint_hidden, f"{hint_ok}/{hint_hidden}")

        drop_mime = mime_for(archive)
        drop = QDropEvent(
            QPointF(20, 20),
            Qt.CopyAction,
            drop_mime,
            Qt.LeftButton,
            Qt.NoModifier,
        )
        # QDropEvent 由 Qt 的拖放管理器投递，sendEvent 不会进入事件过滤器；
        # 这里直接调用过滤器，验证真实拖放时执行的同一段逻辑。
        accepted = window.eventFilter(target, drop)
        ok &= check("drop_accepted", bool(accepted))
        ok &= check("drop_routed", captured == [archive], str(captured))
        window.open_version_import = original_import  # type: ignore[method-assign]

        dialog = VersionZipDialog(window, initial_path=archive)
        dialog_ok = (
            not dialog.preview.isHidden()
            and dialog.import_btn.isEnabled()
            and dialog.name_edit.text().startswith("DragCat")
            and dialog.preview_name.text() == "DragCat"
            and dialog._candidate is not None
            and dialog._candidate.mods == 1
        )
        ok &= check("dialog_preview", dialog_ok, dialog.name_edit.text())

        dialog_mime = mime_for(archive)
        dialog_enter = QDragEnterEvent(
            QPoint(20, 20), Qt.CopyAction, dialog_mime, Qt.LeftButton, Qt.NoModifier
        )
        dialog.dragEnterEvent(dialog_enter)
        dialog_drop = QDropEvent(
            QPointF(20, 20),
            Qt.CopyAction,
            dialog_mime,
            Qt.LeftButton,
            Qt.NoModifier,
        )
        dialog.dropEvent(dialog_drop)
        drop_load_ok = (
            dialog_enter.isAccepted()
            and dialog_drop.isAccepted()
            and dialog._candidate is not None
            and dialog.preview_name.text() == "DragCat"
            and not dialog.url_edit.acceptDrops()
        )
        ok &= check("dialog_drop", drop_load_ok)
        dialog.deleteLater()

        from stellar.ui import version_zip_dialog as dialog_module
        from stellar.workers import VersionZipImportWorker

        worker_results: list[tuple[bool, str, str]] = []
        worker = VersionZipImportWorker(
            archive,
            game,
            "UiCat",
            isolated=True,
            complete=False,
            parent=window,
        )
        worker.finished.connect(
            lambda success, message, version_id: worker_results.append(
                (success, message, version_id)
            )
        )
        worker._run()
        worker_ok = (
            worker_results
            and worker_results[0][0]
            and worker_results[0][2] == "UiCat"
            and (game / "versions" / "UiCat" / "UiCat.json").is_file()
            and (game / "versions" / "UiCat" / "UiCat.jar").is_file()
            and (game / "versions" / "UiCat" / "mods" / "demo.jar").is_file()
        )
        ok &= check("worker_import", bool(worker_ok), str(worker_results))

        opened: list[str] = []
        original_exec = dialog_module.VersionZipDialog.exec

        def fake_exec(self) -> int:
            opened.append(self.path_edit.text())
            return 0

        dialog_module.VersionZipDialog.exec = fake_exec  # type: ignore[method-assign]
        try:
            window.open_version_import(archive)
        finally:
            dialog_module.VersionZipDialog.exec = original_exec
        ok &= check("entry_zip", opened == [str(archive)], str(opened))

        window.close()
        app.processEvents()
    finally:
        Config.save = original_save
        shutil.rmtree(root, ignore_errors=True)

    print("RESULT:", "PASS" if ok else "FAIL")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
