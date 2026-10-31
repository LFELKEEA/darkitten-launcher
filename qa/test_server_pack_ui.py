from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import zipfile
from dataclasses import asdict
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from PySide6.QtWidgets import QApplication, QWidget

import darkitten.server_pack as server_pack_module
import darkitten.servers as servers_module
from darkitten.server_pack import export_server
from darkitten.servers import ServerInfo
from darkitten.ui.server_pack_dialog import (
    ServerPackExportDialog,
    ServerPackImportDialog,
)


def check(label: str, value: bool, detail: str = "") -> bool:
    print(f"{label}: {'OK' if value else 'FAIL'}" + (f" ({detail})" if detail else ""))
    return value


class FakeMain(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.logs: list[str] = []
        self.messages: list[tuple[str, str]] = []

    def log(self, message: str) -> None:
        self.logs.append(message)

    def toast(self, text: str, kind: str = "info") -> None:
        self.messages.append((text, kind))


def make_jar(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("version.json", json.dumps({"id": "1.21.1"}))
        zf.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")


def make_server(root: Path) -> ServerInfo:
    path = root / "servers" / "ui-server"
    path.mkdir(parents=True, exist_ok=True)
    make_jar(path / "server.jar")
    (path / "eula.txt").write_text("eula=true\n", encoding="utf-8")
    (path / "server.properties").write_text(
        "level-name=world\nserver-port=25571\nonline-mode=false\n",
        encoding="utf-8",
    )
    (path / "world" / "playerdata").mkdir(parents=True, exist_ok=True)
    (path / "world" / "level.dat").write_bytes(b"LEVEL")
    (path / "world" / "playerdata" / "a.dat").write_bytes(b"PLAYER")
    (path / "whitelist.json").write_text(
        json.dumps([{"uuid": "1", "name": "Alice"}]), encoding="utf-8"
    )
    (path / "ops.json").write_text(
        json.dumps([{"uuid": "1", "name": "Alice", "level": 4}]), encoding="utf-8"
    )
    info = ServerInfo(
        folder="ui-server",
        name="界面猫猫服",
        kind="paper",
        mc_version="1.21.1",
        port=25571,
        eula=True,
        installed=True,
        launch_kind="jar",
        launch_target="server.jar",
        created_at="2026-10-24 10:00:00",
    )
    (path / "server.json").write_text(
        json.dumps(asdict(info), ensure_ascii=False), encoding="utf-8"
    )
    return info


def wait_for(app: QApplication, predicate, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    app.processEvents()
    return bool(predicate())


def main() -> None:
    ok = True
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        servers_module.SERVERS_DIR = root / "servers"
        server_pack_module.SERVERS_DIR = root / "servers"
        info = make_server(root)
        source_path = Path(info.path)
        main_window = FakeMain()

        export_zip = root / "ui-out" / "界面猫猫服.zip"
        export_zip.parent.mkdir(parents=True, exist_ok=True)
        export_dialog = ServerPackExportDialog(main_window, info)
        export_dialog._target_dir = export_zip.parent
        export_dialog._sync_destination()
        export_dialog._start_export()
        ok &= check(
            "ui_export_finished",
            wait_for(app, lambda: bool(export_dialog.result_path())),
            export_dialog.path_edit.text(),
        )
        ok &= check("ui_export_file", export_zip.is_file(), str(export_zip))
        export_dialog.close()

        servers_module.SERVERS_DIR = root / "ui-imported"
        server_pack_module.SERVERS_DIR = root / "ui-imported"
        import_dialog = ServerPackImportDialog(main_window, export_zip)
        ok &= check("ui_import_preview", not import_dialog.preview.isHidden())
        ok &= check(
            "ui_import_name",
            "界面猫猫服" in import_dialog.name_edit.text(),
            import_dialog.name_edit.text(),
        )
        import_dialog.name_edit.setText("界面导入猫猫")
        import_dialog._start_import()
        ok &= check(
            "ui_import_finished",
            wait_for(app, lambda: bool(import_dialog.imported_folder)),
            import_dialog.imported_folder,
        )
        imported = root / "ui-imported" / import_dialog.imported_folder
        ok &= check("ui_import_folder", imported.is_dir(), str(imported))
        ok &= check("ui_import_world", (imported / "world" / "level.dat").is_file())
        ok &= check(
            "ui_import_playerdata",
            (imported / "world" / "playerdata" / "a.dat").is_file(),
        )
        ok &= check("ui_import_ops", (imported / "ops.json").is_file())
        data = json.loads((imported / "server.json").read_text(encoding="utf-8"))
        ok &= check("ui_import_display_name", data.get("name") == "界面导入猫猫", data.get("name"))
        import_dialog.close()

        from darkitten.ui.main_window import MainWindow

        class Router:
            install_active = False
            launch_active = False

            def __init__(self) -> None:
                self.servers: list[Path] = []
                self.versions: list[Path] = []
                self.messages: list[str] = []

            def toast(self, text: str, kind: str = "info") -> None:
                self.messages.append(text)

            def open_server_import(self, path=None) -> None:
                self.servers.append(Path(path))

            def open_version_import(self, path=None) -> None:
                self.versions.append(Path(path))

            def open_local_import(self, paths=None) -> None:
                # 1.9.17 起拖拽统一走本地导入入口，这里保留真实的类型识别。
                from darkitten.local_import import detect_local_import

                raw = paths if isinstance(paths, (list, tuple)) else [paths]
                for item in raw:
                    info = detect_local_import(Path(item))
                    if info.kind == "server":
                        self.open_server_import(info.path)
                    elif info.kind == "version_zip":
                        self.open_version_import(info.path)

        router = Router()
        MainWindow._handle_dropped_archives(router, [source_path])
        MainWindow._handle_dropped_archives(router, [export_zip])
        version_zip = root / "version.zip"
        version_json = {
            "id": "1.21.1",
            "type": "release",
            "mainClass": "net.minecraft.client.main.Main",
            "assets": "1.21",
            "assetIndex": {"id": "1.21", "url": "http://example.invalid/a.json"},
            "javaVersion": {"majorVersion": 21},
        }
        with zipfile.ZipFile(version_zip, "w") as zf:
            zf.writestr("versions/1.21.1/1.21.1.json", json.dumps(version_json))
            zf.writestr("versions/1.21.1/1.21.1.jar", "jar")
        MainWindow._handle_dropped_archives(router, [version_zip])
        ok &= check(
            "drag_server_folder",
            router.servers[:1] == [source_path],
            str(router.servers),
        )
        ok &= check("drag_server_zip", router.servers[1:2] == [export_zip], str(router.servers))
        ok &= check(
            "drag_version_zip",
            router.versions == [version_zip],
            str(router.versions),
        )

    print("RESULT:", "PASS" if ok else "FAIL")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
