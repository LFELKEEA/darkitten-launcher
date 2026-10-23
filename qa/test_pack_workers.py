from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

import stellar.workers as workers
from stellar.packs import inspect_pack


def make_version(game_dir: Path, version_id: str) -> None:
    path = game_dir / "versions" / version_id / f"{version_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "id": version_id,
                "type": "release",
                "mainClass": "net.minecraft.client.main.Main",
                "assets": "1.21",
                "assetIndex": {"id": "1.21", "url": "http://example.invalid/a.json"},
                "downloads": {
                    "client": {
                        "url": "http://example.invalid/client.jar",
                        "size": 10,
                        "sha1": "",
                    }
                },
                "javaVersion": {"majorVersion": 21},
            }
        ),
        encoding="utf-8",
    )


def run_worker(worker, timeout_ms: int = 15000):
    loop = QEventLoop()
    captured: list[tuple] = []

    def on_signal(*args) -> None:
        captured.append(args)
        loop.quit()

    worker.finished.connect(on_signal)
    worker.start()
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    try:
        worker.finished.disconnect(on_signal)
    except (RuntimeError, TypeError):
        pass
    return captured[0] if captured else None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    data = b""

    def log_message(self, *args) -> None:
        pass

    def do_GET(self) -> None:
        if self.path != "/pack.dklpack":
            self.send_error(404)
            return
        body = self.data
        start = 0
        end = len(body) - 1
        header = self.headers.get("Range") or ""
        if header.startswith("bytes="):
            left, _, right = header.split("=", 1)[1].partition("-")
            start = int(left or 0)
            end = min(int(right), len(body) - 1) if right else end
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(body)}")
        else:
            self.send_response(200)
        chunk = body[start : end + 1]
        self.send_header("Content-Length", str(len(chunk)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        try:
            self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass


def main() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    ok = True
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        source = root / "game"
        make_version(source, "WorkerPack")
        run = source / "versions" / "WorkerPack"
        (run / "mods").mkdir(parents=True, exist_ok=True)
        (run / "mods" / "worker.jar").write_bytes(b"worker-mod")
        (run / "config").mkdir(parents=True, exist_ok=True)
        (run / "config" / "worker.toml").write_text("cat=1", encoding="utf-8")

        pack_path = root / "worker.dklpack"
        export = workers.PackExportWorker(
            source, "WorkerPack", pack_path, name="Worker 猫猫包"
        )
        exported = run_worker(export)
        export_ok = (
            exported is not None
            and exported[0] is True
            and pack_path.is_file()
        )
        print("pack_export_worker:", "OK" if export_ok else "FAIL", exported)
        ok &= export_ok

        info = inspect_pack(pack_path)
        print("pack_export_manifest:", "OK" if info.name == "Worker 猫猫包" else "FAIL")
        ok &= info.name == "Worker 猫猫包"

        Handler.data = pack_path.read_bytes()
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{server.server_address[1]}/pack.dklpack"
        original_app_dir = workers.APP_DIR
        workers.APP_DIR = root
        try:
            fetch = workers.PackFetchWorker(url, threads=4)
            fetched = run_worker(fetch)
            fetched_ok = (
                fetched is not None
                and fetched[1] == ""
                and Path(fetched[0]).is_file()
                and inspect_pack(Path(fetched[0])).name == "Worker 猫猫包"
            )
            print("pack_fetch_worker:", "OK" if fetched_ok else "FAIL", fetched)
            ok &= fetched_ok

            invalid = workers.PackFetchWorker("file:///tmp/x.dklpack")
            invalid_result = run_worker(invalid)
            invalid_ok = invalid_result is not None and "http" in invalid_result[1]
            print("pack_fetch_invalid_url:", "OK" if invalid_ok else "FAIL", invalid_result)
            ok &= invalid_ok
        finally:
            workers.APP_DIR = original_app_dir
            server.shutdown()

    app.processEvents()
    print("RESULT:", "PASS" if ok else "FAIL")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
