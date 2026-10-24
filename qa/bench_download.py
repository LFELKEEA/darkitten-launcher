from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from darkitten import downloader  # noqa: E402
from darkitten.downloader import BatchDownload, download_batch, download_to_file  # noqa: E402

BIG_BYTES = 16 * 1024 * 1024
SMALL_BYTES = 64 * 1024
SMALL_COUNT = 80
REQUEST_DELAY = 0.01
HANDSHAKE_DELAY = 0.05

BIG_DATA = bytes(range(256)) * (BIG_BYTES // 256)
SMALL_DATA = {
    f"file-{index:03d}.bin": bytes([index % 256]) * SMALL_BYTES
    for index in range(SMALL_COUNT)
}
NORANGE_DATA = bytes(range(256)) * ((4 * 1024 * 1024) // 256)


class Stats:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.requests = 0
        self.connections: set[int] = set()
        self.bytes = 0

    def connection(self, port: int) -> None:
        with self.lock:
            self.connections.add(port)

    def request(self) -> None:
        with self.lock:
            self.requests += 1

    def sent(self, count: int) -> None:
        with self.lock:
            self.bytes += count

    def reset(self) -> None:
        with self.lock:
            self.requests = 0
            self.connections.clear()
            self.bytes = 0

    def snapshot(self) -> tuple[int, int, int]:
        with self.lock:
            return self.requests, len(self.connections), self.bytes


def make_handler(stats: Stats):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self) -> None:
            super().setup()
            stats.connection(self.client_address[1])
            time.sleep(HANDSHAKE_DELAY)

        def handle(self) -> None:
            try:
                super().handle()
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                pass

        def log_message(self, _format, *_args) -> None:
            return

        def do_GET(self) -> None:
            stats.request()
            time.sleep(REQUEST_DELAY)
            path = self.path.split("?", 1)[0]
            if path == "/big":
                data = BIG_DATA
            elif path == "/norange":
                data = NORANGE_DATA
            elif path.startswith("/small/"):
                data = SMALL_DATA.get(path.rsplit("/", 1)[-1])
                if data is None:
                    self.send_error(404)
                    return
            else:
                self.send_error(404)
                return

            start = 0
            end = len(data) - 1
            status = 200
            range_header = self.headers.get("Range") or ""
            supports_range = path != "/norange"
            if supports_range and range_header.startswith("bytes="):
                spec = range_header.split("=", 1)[1].split(",", 1)[0]
                left, _, right = spec.partition("-")
                try:
                    if left:
                        start = int(left)
                        if right:
                            end = min(int(right), len(data) - 1)
                    elif right:
                        start = max(0, len(data) - int(right))
                except ValueError:
                    start, end = 0, len(data) - 1
                if start > end or start >= len(data):
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{len(data)}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                status = 206

            body = data[start : end + 1]
            self.send_response(status)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Accept-Ranges", "bytes" if supports_range else "none")
            if status == 206:
                self.send_header(
                    "Content-Range", f"bytes {start}-{end}/{len(data)}"
                )
            self.end_headers()
            try:
                self.wfile.write(body)
                self.wfile.flush()
                stats.sent(len(body))
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                self.close_connection = True

    return Handler


class Server:
    def __init__(self) -> None:
        self.stats = Stats()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.stats))
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def run_big(size_known: bool) -> tuple[float, int, int, int]:
    server = Server()
    work = Path(tempfile.mkdtemp(prefix="dkl-bench-big-"))
    try:
        server.stats.reset()
        started = time.perf_counter()
        download_to_file(
            server.base + "/big",
            work / "big.bin",
            sha1=sha1(BIG_DATA),
            size=BIG_BYTES if size_known else 0,
            connections=4,
        )
        elapsed = time.perf_counter() - started
        assert (work / "big.bin").stat().st_size == BIG_BYTES
        requests, connections, _ = server.stats.snapshot()
        return elapsed, requests, connections, BIG_BYTES
    finally:
        server.close()


def run_small_batch(workers: int, reuse: bool) -> tuple[float, int, int, int]:
    server = Server()
    work = Path(tempfile.mkdtemp(prefix="dkl-bench-small-"))
    original_release = downloader._release_connection
    if not reuse:
        def close_instead(key, conn):
            try:
                conn.close()
            except Exception:
                pass

        downloader._release_connection = close_instead
    try:
        server.stats.reset()
        tasks = [
            BatchDownload(
                dest=work / name,
                urls=[server.base + "/small/" + name],
                sha1=sha1(data),
                size=len(data),
                label=name,
            )
            for name, data in SMALL_DATA.items()
        ]
        started = time.perf_counter()
        success, failures = download_batch(
            tasks,
            connections=2,
            max_workers=workers,
            percent_range=(0, 100),
        )
        elapsed = time.perf_counter() - started
        assert success == SMALL_COUNT, (success, failures)
        assert not failures, failures
        for name, data in SMALL_DATA.items():
            assert (work / name).read_bytes() == data, name
        requests, connections, _ = server.stats.snapshot()
        return elapsed, requests, connections, sum(len(v) for v in SMALL_DATA.values())
    finally:
        downloader._release_connection = original_release
        server.close()


def run_norange() -> tuple[float, int, int, int]:
    server = Server()
    work = Path(tempfile.mkdtemp(prefix="dkl-bench-norange-"))
    try:
        server.stats.reset()
        started = time.perf_counter()
        download_to_file(
            server.base + "/norange",
            work / "norange.bin",
            sha1=sha1(NORANGE_DATA),
            size=len(NORANGE_DATA),
            connections=4,
        )
        elapsed = time.perf_counter() - started
        assert (work / "norange.bin").read_bytes() == NORANGE_DATA
        requests, connections, _ = server.stats.snapshot()
        return elapsed, requests, connections, len(NORANGE_DATA)
    finally:
        server.close()


def main() -> int:
    print("DKL download benchmark")
    print(
        f"latency: request={REQUEST_DELAY * 1000:.0f}ms "
        f"handshake={HANDSHAKE_DELAY * 1000:.0f}ms"
    )
    print()
    header = f"{'scenario':<34}{'time':>9}{'MB/s':>9}{'req':>7}{'conn':>7}"
    print(header)
    print("-" * len(header))

    for label, size_known in (
        ("16MB, size known (segments)", True),
        ("16MB, size unknown (probe)", False),
    ):
        elapsed, requests, connections, total = run_big(size_known)
        print(
            f"{label:<34}{elapsed:>8.3f}s"
            f"{total / elapsed / 1024 / 1024:>9.1f}"
            f"{requests:>7}{connections:>7}"
        )

    for label, workers, reuse in (
        ("80 small, sequential, no reuse", 1, False),
        ("80 small, sequential, keep-alive", 1, True),
        ("80 small, 8 workers, keep-alive", 8, True),
    ):
        elapsed, requests, connections, total = run_small_batch(workers, reuse)
        print(
            f"{label:<34}{elapsed:>8.3f}s"
            f"{total / elapsed / 1024 / 1024:>9.1f}"
            f"{requests:>7}{connections:>7}"
        )

    elapsed, requests, connections, total = run_norange()
    print(
        f"{'4MB, no Range fallback':<34}{elapsed:>8.3f}s"
        f"{total / elapsed / 1024 / 1024:>9.1f}"
        f"{requests:>7}{connections:>7}"
    )
    print()
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    raise SystemExit(main())
