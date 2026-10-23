from __future__ import annotations

import hashlib
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from stellar.downloader import (
    DownloadCancelled,
    cleanup_partial_files,
    download_to_file,
    partial_files,
)

SIZE = 8 * 1024 * 1024
DATA = bytes((i * 37 + 11) % 256 for i in range(SIZE))
SHA1 = hashlib.sha1(DATA).hexdigest()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:
        pass

    def do_GET(self) -> None:
        if self.path not in ("/file.bin", "/slow.bin"):
            self.send_error(404)
            return
        data = DATA
        start = 0
        end = len(data) - 1
        header = self.headers.get("Range") or ""
        if header.startswith("bytes="):
            part = header.split("=", 1)[1]
            left, _, right = part.partition("-")
            start = int(left or 0)
            end = int(right) if right else len(data) - 1
            end = min(end, len(data) - 1)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
        else:
            self.send_response(200)
        body = data[start : end + 1]
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        delay = 0.02 if self.path == "/slow.bin" else 0.002
        try:
            for offset in range(0, len(body), 65536):
                self.wfile.write(body[offset : offset + 65536])
                self.wfile.flush()
                time.sleep(delay)
        except (BrokenPipeError, ConnectionResetError):
            pass


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    ok = True
    try:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            progress = []
            dest = root / "multi.bin"
            written = download_to_file(
                f"{base}/file.bin",
                dest,
                sha1=SHA1,
                size=SIZE,
                progress_cb=lambda done, total: progress.append((done, total)),
                connections=4,
            )
            good = (
                written == SIZE
                and dest.read_bytes() == DATA
                and not dest.with_name(dest.name + ".part").exists()
            )
            print("multi_stream:", "OK" if good else "FAIL", written, len(progress))
            ok &= good

            slow_dest = root / "cancel.bin"
            cancel = threading.Event()

            def cancel_later() -> None:
                time.sleep(0.35)
                cancel.set()

            threading.Thread(target=cancel_later, daemon=True).start()
            try:
                download_to_file(
                    f"{base}/slow.bin",
                    slow_dest,
                    cancel_event=cancel,
                    connections=4,
                )
            except DownloadCancelled:
                cancelled_ok = not slow_dest.exists() and not partial_files(root)
                print("cancel_cleanup:", "OK" if cancelled_ok else "FAIL")
                ok &= cancelled_ok
            else:
                print("cancel_cleanup: FAIL (not cancelled)")
                ok = False

            (root / "keep.txt").write_text("keep", encoding="utf-8")
            (root / "stale.part").write_bytes(b"x" * 1024)
            (root / "stale2.part.seg1").write_bytes(b"y" * 2048)
            removed, freed = cleanup_partial_files(root)
            cleanup_ok = (
                removed == 2
                and freed == 3072
                and (root / "keep.txt").exists()
                and not partial_files(root)
            )
            print("partial_cleanup:", "OK" if cleanup_ok else "FAIL", removed, freed)
            ok &= cleanup_ok
    finally:
        server.shutdown()
    print("RESULT:", "PASS" if ok else "FAIL")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
