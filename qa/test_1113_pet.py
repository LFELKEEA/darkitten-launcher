from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import threading
import zipfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from darkitten import pet
from darkitten.ui import pet_dialog as pet_dialog_module
from darkitten.ui.download_center import TASK_SUCCESS
from darkitten.ui.pet_dialog import PetDialog

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> bool:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"{name}: OK{(' (' + detail + ')') if detail else ''}")
    else:
        FAIL += 1
        print(f"{name}: FAIL{(' (' + detail + ')') if detail else ''}")
    return condition


def make_pet_zip(path: Path, exe_bytes: bytes = b"exe") -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("LFELKEEA/LFELKEEA.exe", exe_bytes)
        zf.writestr("LFELKEEA/_internal/runtime.dll", b"dll")
        zf.writestr("LFELKEEA/_internal/assets/face.png", b"png")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class FakeCenter(QObject):
    task_progress = Signal(int, int, str)
    task_finished = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.cancelled: list[int] = []
        self.started: list[tuple[object, dict]] = []

    def active_tasks(self):
        return []

    def cancel(self, task_id: int) -> None:
        self.cancelled.append(int(task_id))

    def start_worker(self, worker, **kwargs):
        self.started.append((worker, kwargs))
        return SimpleNamespace(id=1)


class FakeMain(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.config = SimpleNamespace(download_threads=4, update_url="")
        self.download_center = FakeCenter()
        self.toasts: list[tuple[str, str]] = []

    def toast(self, text: str, kind: str = "info") -> None:
        self.toasts.append((str(text), str(kind)))


class DummyManifestWorker(QObject):
    ready = Signal(object, str)

    def __init__(self, url: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.url = url

    def start(self) -> None:
        pass


class ReleaseStub:
    def __init__(self, version: str, size: int, digest: str) -> None:
        self.version = version
        self.name = "LFELKEEA 桌宠"
        self.size = size
        self.sha256 = digest
        self.download_url = ""
        self.released = "2026-10-27"
        self.notes: list[str] = []


class FakeTask:
    def __init__(self, task_id: int, status: str, message: str) -> None:
        self.id = task_id
        self.status = status
        self.message = message


def test_manifest_and_install(tmp: Path) -> None:
    manifest = {
        "pet": {
            "name": "LFELKEEA 桌宠",
            "version": "v1.0.0",
            "released": "2026-10-27",
            "size": 123,
            "sha256": "a" * 64,
            "download_url": "releases/download/pet-v1.0.0/LFELKEEA.zip",
            "notes": ["- 第一条", "第二条"],
        }
    }
    release = pet.parse_pet_release(manifest, "https://example.com/dkl/update.json")
    check("parse_version", release is not None and release.version == "1.0.0")
    check(
        "parse_relative_url",
        release is not None
        and release.download_url
        == "https://example.com/dkl/releases/download/pet-v1.0.0/LFELKEEA.zip",
        release.download_url if release else "",
    )
    check("parse_notes", release is not None and release.notes == ["第一条", "第二条"])
    check("missing_pet", pet.parse_pet_release({}) is None)

    root = tmp / "install"
    archive = tmp / "pet.zip"
    make_pet_zip(archive)
    pet.install_pet(archive, "1.0.0", root=root)
    check("installed_exe", pet.is_installed(root))
    check("installed_version", pet.installed_version(root) == "1.0.0")
    check("installed_entries", (root / "LFELKEEA" / "_internal" / "runtime.dll").is_file())

    data = root / "LFELKEEA" / "data"
    data.mkdir()
    (data / "config.json").write_text('{"position": [1, 2]}', encoding="utf-8")
    make_pet_zip(archive, b"exe-v2")
    pet.install_pet(archive, "1.0.1", root=root)
    check("upgrade_exe", (root / "LFELKEEA" / "LFELKEEA.exe").read_bytes() == b"exe-v2")
    check(
        "upgrade_keeps_data",
        (data / "config.json").read_text(encoding="utf-8") == '{"position": [1, 2]}',
    )
    check("upgrade_version", pet.installed_version(root) == "1.0.1")

    bad = tmp / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("LFELKEEA/../../evil.exe", b"evil")
    try:
        pet.install_pet(bad, "9.9.9", root=tmp / "bad")
    except pet.PetError:
        rejected = True
    else:
        rejected = False
    check("reject_path_traversal", rejected)
    check("no_partial_staging", not (tmp / "bad" / ".staging-LFELKEEA").exists())

    check("version_compare_newer", pet.is_newer("1.2.0", "1.1.9"))
    check("version_compare_same", not pet.is_newer("1.1.9", "1.1.9"))


def test_download(tmp: Path) -> None:
    serve_dir = tmp / "serve"
    serve_dir.mkdir()
    archive = serve_dir / "LFELKEEA.zip"
    make_pet_zip(archive)
    digest = sha256(archive)
    size = archive.stat().st_size

    handler = lambda *args, **kwargs: SimpleHTTPRequestHandler(  # noqa: E731
        *args, directory=str(serve_dir), **kwargs
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/LFELKEEA.zip"
        release = pet.PetRelease(
            version="1.0.0", download_url=url, sha256=digest, size=size
        )
        progress: list[int] = []
        archive_path = pet.download_pet(
            release,
            root=tmp / "download-ok",
            progress_cb=lambda p, _m: progress.append(int(p)),
            threads=2,
        )
        check("download_file", archive_path.is_file() and archive_path.stat().st_size == size)
        check("download_sha", sha256(archive_path) == digest)
        check("download_progress", progress and progress[-1] == 100, str(progress[-3:]))

        bad = pet.PetRelease(
            version="1.0.0", download_url=url, sha256="0" * 64, size=size
        )
        try:
            pet.download_pet(bad, root=tmp / "download-bad", threads=2)
        except pet.PetError:
            mismatch = True
        else:
            mismatch = False
        check("reject_bad_sha", mismatch)
        check("remove_bad_file", not pet.archive_path(bad, tmp / "download-bad").exists())
    finally:
        server.shutdown()
        server.server_close()


def test_dialog() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    fake_main = FakeMain()
    original_worker = pet_dialog_module.PetManifestWorker
    pet_dialog_module.PetManifestWorker = DummyManifestWorker
    original_installed = pet.is_installed
    original_version = pet.installed_version
    try:
        dialog = PetDialog(fake_main)
        check("dialog_initial_badge", dialog.version_badge.text() == "未安装")
        release = ReleaseStub("1.0.0", 135975564, "b" * 64)
        release.notes = ["桌面常驻小猫"]
        dialog._on_manifest(release, "")
        app.processEvents()
        check("dialog_action_download", dialog.action_btn.text() == "下载桌宠")
        check("dialog_action_enabled", dialog.action_btn.isEnabled())
        check("dialog_size", dialog.size_value.text() == "129.7 MB", dialog.size_value.text())
        check("dialog_notes", not dialog.notes_label.isHidden())

        pet.is_installed = lambda root=None: True
        pet.installed_version = lambda root=None: "1.0.0"
        dialog._refresh_state()
        check("dialog_action_redownload", dialog.action_btn.text() == "重新下载")
        check("dialog_launch_enabled", dialog.launch_btn.isEnabled())

        release.version = "1.1.0"
        dialog._refresh_state()
        check("dialog_action_update", dialog.action_btn.text() == "更新到 v1.1.0")

        dialog._task_id = 42
        dialog._on_task_progress(42, 55, "下载中")
        check("dialog_progress_value", dialog.progress.value() == 55)
        check("dialog_progress_text", dialog.status_label.text() == "下载中")
        dialog._on_task_finished(FakeTask(42, TASK_SUCCESS, "桌宠 1.1.0 已安装"))
        check("dialog_finish_resets", dialog._task_id is None)
        check("dialog_toast", any("安装完成" in text for text, _ in fake_main.toasts))

        dialog._on_task_progress(99, 10, "ignored")
        check("dialog_ignore_other_task", dialog.progress.value() == 55)
        dialog.close()
    finally:
        pet_dialog_module.PetManifestWorker = original_worker
        pet.is_installed = original_installed
        pet.installed_version = original_version


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp_text:
        tmp = Path(tmp_text)
        test_manifest_and_install(tmp)
        test_download(tmp)
    test_dialog()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
