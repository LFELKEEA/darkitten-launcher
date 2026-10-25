from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))


def check(label: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{label} failed {detail}")
    print(f"ok  {label}" + (f" · {detail}" if detail else ""))


def make_mrpack(path: Path) -> None:
    manifest = {
        "game": "minecraft",
        "formatVersion": 1,
        "versionId": "1.0.0",
        "name": "RetryCat",
        "dependencies": {"minecraft": "1.21.11"},
        "files": [],
    }
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("modrinth.index.json", json.dumps(manifest))
        zf.writestr("overrides/config/demo.json", "{}")


def make_version_zip(path: Path) -> None:
    version = {
        "id": "RetryZip",
        "type": "release",
        "mainClass": "net.minecraft.client.main.Main",
        "assets": "1.21",
        "assetIndex": {"id": "1.21", "url": "http://example.invalid/a.json"},
        "javaVersion": {"majorVersion": 21},
    }
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("versions/RetryZip/RetryZip.json", json.dumps(version))
        zf.writestr("versions/RetryZip/mods/demo.jar", b"mod")


class _StallHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Length", str(8 * 1024 * 1024))
        self.end_headers()
        self.wfile.flush()
        time.sleep(8)

    def log_message(self, *_args):
        pass


class _StallServer(ThreadingHTTPServer):
    daemon_threads = True


def test_artifact_size() -> None:
    from darkitten.versions import library_artifact

    lib = {
        "name": "net.fabricmc:sponge-mixin:0.17.4+mixin.0.8.7",
        "url": "https://maven.fabricmc.net/",
        "sha1": "5f66cc9f59b8efaa942155a3d5a30599bf6640dd",
        "size": 1539080,
    }
    art = library_artifact(lib)
    check("fabric artifact size", art is not None and art.size == 1539080, str(art))
    check(
        "fabric artifact sha1",
        art is not None
        and art.sha1 == "5f66cc9f59b8efaa942155a3d5a30599bf6640dd",
    )

    mojang = {
        "name": "com.mojang:brigadier:1.0.18",
        "downloads": {
            "artifact": {
                "path": "com/mojang/brigadier/1.0.18/brigadier-1.0.18.jar",
                "url": (
                    "https://libraries.minecraft.net/com/mojang/brigadier/"
                    "1.0.18/brigadier-1.0.18.jar"
                ),
                "sha1": "abc",
                "size": 123,
            }
        },
    }
    art2 = library_artifact(mojang)
    check("mojang artifact unchanged", art2 is not None and art2.size == 123)


def test_mirror_order() -> None:
    import darkitten.installer as installer

    url = (
        "https://maven.fabricmc.net/net/fabricmc/sponge-mixin/"
        "0.17.4+mixin.0.8.7/sponge-mixin-0.17.4+mixin.0.8.7.jar"
    )
    original = installer.is_source_usable

    installer.is_source_usable = lambda _source: True  # type: ignore[assignment]
    try:
        urls = installer.mirror_urls(url, "auto")
    finally:
        installer.is_source_usable = original  # type: ignore[assignment]
    check(
        "fabric mirror first",
        urls and urls[0].startswith("https://bmclapi2.bangbang93.com/maven/"),
        str(urls),
    )
    check(
        "fabric api second",
        len(urls) > 2
        and urls[1].startswith("https://repo1.maven.org/maven2/")
        and urls[2] == url,
        str(urls),
    )

    installer.is_source_usable = lambda _source: False  # type: ignore[assignment]
    try:
        blocked = installer.mirror_urls(url, "auto")
    finally:
        installer.is_source_usable = original  # type: ignore[assignment]
    check(
        "fabric official when mirror blocked",
        blocked and blocked[0] == url and blocked[-1].startswith("https://bmclapi"),
        str(blocked),
    )


def test_library_progress() -> None:
    import darkitten.installer as installer
    from darkitten.versions import Artifact

    with tempfile.TemporaryDirectory(prefix="dkl_lib_progress_") as temp:
        game = Path(temp)
        messages: list[tuple[int, str]] = []

        def progress(percent: int, message: str) -> None:
            messages.append((percent, message))

        inst = installer.Installer(game, progress_cb=progress, threads=2)
        a = Artifact(
            path="a.jar", url="https://example.invalid/a.jar", size=1024 * 1024
        )
        b = Artifact(
            path="b.jar", url="https://example.invalid/b.jar", size=1024 * 1024
        )

        original_arts = installer.active_library_artifacts
        original_natives = installer.active_natives

        def fake_download(url, dest, **_kwargs):
            dest = Path(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.name == "a.jar":
                dest.write_bytes(b"a" * (1024 * 1024))
                return True
            raise installer.DownloadError("boom")

        original_download = inst._download_file
        installer.active_library_artifacts = lambda _resolved: [a, b]
        installer.active_natives = lambda _resolved: []
        inst._download_file = fake_download  # type: ignore[method-assign]
        try:
            try:
                inst._install_libraries(None)  # type: ignore[arg-type]
            except installer.DownloadError:
                pass
            else:
                raise AssertionError("library failure should surface")
        finally:
            installer.active_library_artifacts = original_arts
            installer.active_natives = original_natives
            inst._download_file = original_download  # type: ignore[method-assign]

        joined = "\n".join(message for _percent, message in messages)
        check(
            "failed library not counted as complete",
            "2.0/2.0 MB" not in joined,
            joined,
        )
        check(
            "library progress stays below stage end",
            messages and messages[-1][0] < inst.STAGE_LIBS[1],
            str(messages[-1] if messages else None),
        )


def test_cancel_prompt() -> None:
    from darkitten.downloader import DownloadCancelled, download_to_file

    server = _StallServer(("127.0.0.1", 0), _StallHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    cancel = threading.Event()
    result: dict[str, object] = {}

    with tempfile.TemporaryDirectory(prefix="dkl_cancel_") as temp:
        def run() -> None:
            started = time.time()
            try:
                download_to_file(
                    f"http://127.0.0.1:{server.server_port}/stall.bin",
                    Path(temp) / "stall.bin",
                    size=8 * 1024 * 1024,
                    connections=1,
                    cancel_event=cancel,
                    timeout=1.0,
                    stall_timeout=1.0,
                )
                result["status"] = "done"
            except DownloadCancelled:
                result["status"] = "cancelled"
            except Exception as exc:  # pragma: no cover - diagnostic
                result["status"] = f"{type(exc).__name__}: {exc}"
            result["elapsed"] = time.time() - started

        worker = threading.Thread(target=run)
        worker.start()
        time.sleep(0.4)
        cancel.set()
        worker.join(5)
        alive = worker.is_alive()
    server.shutdown()

    check("stalled download cancels", result.get("status") == "cancelled", str(result))
    check("stalled download not stuck", not alive and float(result.get("elapsed", 99)) < 5)


def test_resume_partial_dirs() -> None:
    from darkitten.modrinth_pack import import_modrinth_pack
    from darkitten.version_zip import import_version_zip

    class NoopInstaller:
        def install(self, *_args, **_kwargs):
            return None

    with tempfile.TemporaryDirectory(prefix="dkl_resume_") as temp:
        root = Path(temp)
        game = root / "game"
        game.mkdir()

        mrpack = root / "retry.mrpack"
        make_mrpack(mrpack)
        mr_dir = game / "versions" / "RetryCat"
        (mr_dir / "mods").mkdir(parents=True)
        (mr_dir / "mods" / "keep.jar").write_bytes(b"keep")
        result = import_modrinth_pack(
            mrpack,
            game,
            "RetryCat",
            resume=True,
            isolated=True,
            installer=NoopInstaller(),
        )
        check("modrinth resume without skeleton", result.version_id == "RetryCat")
        check(
            "modrinth resume keeps partial",
            (mr_dir / "mods" / "keep.jar").read_bytes() == b"keep",
        )
        check(
            "modrinth resume writes skeleton",
            (mr_dir / "RetryCat.json").is_file(),
        )

        archive = root / "retry.zip"
        make_version_zip(archive)
        zip_dir = game / "versions" / "RetryZip"
        (zip_dir / "mods").mkdir(parents=True)
        (zip_dir / "mods" / "keep.jar").write_bytes(b"keep")
        result2 = import_version_zip(
            archive,
            game,
            "RetryZip",
            resume=True,
            isolated=True,
            complete=False,
        )
        check("zip resume without skeleton", result2.version_id == "RetryZip")
        check(
            "zip resume keeps partial",
            (zip_dir / "mods" / "keep.jar").read_bytes() == b"keep",
        )
        check("zip resume writes skeleton", (zip_dir / "RetryZip.json").is_file())


def test_dialog_retry() -> None:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from darkitten.config import Config
    from darkitten.ui.download_center import DownloadTask, TASK_CANCELLED, TASK_RUNNING
    from darkitten.ui.main_window import MainWindow
    from darkitten.ui.pack_dialog import PackImportDialog

    original_save = Config.save
    Config.save = lambda self: None
    try:
        with tempfile.TemporaryDirectory(prefix="dkl_dialog_retry_") as temp:
            root = Path(temp)
            game = root / "game"
            game.mkdir()
            pack = root / "retry.mrpack"
            make_mrpack(pack)
            window = MainWindow(Config(game_dir=str(game)))
            app.processEvents()
            dialog = PackImportDialog(window, initial_path=pack)
            target = "RetryCat"
            dialog.name_edit.setText(target)
            partial = game / "versions" / target
            (partial / "mods").mkdir(parents=True)
            (partial / "mods" / "keep.jar").write_bytes(b"keep")
            dialog._attempted_version_id = target

            captured: list[object] = []

            def fake_start(worker, **_kwargs):
                captured.append(worker)
                return DownloadTask(
                    id=7001,
                    title="导入整合包 · RetryCat",
                    content_type="modpack",
                    source="auto",
                    target=partial,
                    progress=0,
                    message="准备",
                    status=TASK_RUNNING,
                )

            original_start = window.download_center.start_worker
            window.download_center.start_worker = fake_start  # type: ignore[method-assign]
            try:
                dialog._start_import()
                app.processEvents()
            finally:
                window.download_center.start_worker = original_start  # type: ignore[method-assign]

            check("dialog starts retry", len(captured) == 1, str(len(captured)))
            check(
                "dialog passes resume",
                bool(getattr(captured[0], "resume", False)),
            )
            check("dialog no existing error", not dialog.error.isVisible())

            fake_task = dialog._task
            check("dialog task bound", fake_task is not None)
            fake_task.status = TASK_CANCELLED
            fake_task.message = "任务已取消"
            dialog._on_task_finished(fake_task)
            app.processEvents()
            check("dialog cancel resets", dialog._mode == "idle", dialog._mode)
            check(
                "dialog retry button",
                dialog.import_btn.text() == "重新导入"
                and dialog.import_btn.isEnabled(),
                dialog.import_btn.text(),
            )
            check(
                "dialog cancel hint",
                "取消" in dialog.error.text() and "重新导入" in dialog.error.text(),
                dialog.error.text(),
            )
            dialog.deleteLater()
            window.close()
            window.deleteLater()
            app.processEvents()
    finally:
        Config.save = original_save


def main() -> int:
    test_artifact_size()
    test_mirror_order()
    test_library_progress()
    test_cancel_prompt()
    test_resume_partial_dirs()
    test_dialog_retry()
    print("all 1.11.2 library retry checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
