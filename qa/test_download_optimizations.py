from __future__ import annotations

import hashlib
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from darkitten.downloader import (  # noqa: E402
    BatchDownload,
    TransferError,
    download_batch,
    download_to_file,
)

CHECKS: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name} FAILED {detail}")
    CHECKS.append(name)
    print(f"{name}: OK")


class Stats:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.requests = 0
        self.connections: set[int] = set()
        self.bytes = 0
        self.ranges: list[str] = []
        self.broken: set[str] = set()

    def connection(self, port: int) -> None:
        with self.lock:
            self.connections.add(port)

    def begin(self, range_header: str) -> tuple[int, bool]:
        with self.lock:
            self.requests += 1
            self.ranges.append(range_header)
            should_break = False
            if (
                range_header
                and range_header not in self.broken
                and len(self.broken) < 4
            ):
                self.broken.add(range_header)
                should_break = True
            return self.requests, should_break

    def sent(self, count: int) -> None:
        with self.lock:
            self.bytes += count

    def snapshot(self) -> tuple[int, int, int]:
        with self.lock:
            return self.requests, len(self.connections), self.bytes


def handler_for(stats: Stats, data: bytes, *, range_support: bool, break_once: bool):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self) -> None:
            super().setup()
            stats.connection(self.client_address[1])

        def handle(self) -> None:
            try:
                super().handle()
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                pass

        def log_message(self, _format, *_args) -> None:
            return

        def do_GET(self) -> None:
            range_header = self.headers.get("Range") or ""
            _request_no, should_break = stats.begin(range_header)
            start = 0
            end = len(data) - 1
            status = 200
            if range_support and range_header.startswith("bytes="):
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
                status = 206

            body = data[start : end + 1]
            if break_once and should_break and len(body) > 1:
                half = len(body) // 2
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.send_header(
                    "Accept-Ranges", "bytes" if range_support else "none"
                )
                if status == 206:
                    self.send_header(
                        "Content-Range", f"bytes {start}-{end}/{len(data)}"
                    )
                self.end_headers()
                try:
                    self.wfile.write(body[:half])
                    self.wfile.flush()
                    stats.sent(half)
                finally:
                    self.close_connection = True
                return

            self.send_response(status)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Accept-Ranges", "bytes" if range_support else "none")
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
    def __init__(
        self,
        data: bytes,
        *,
        range_support: bool = True,
        break_once: bool = False,
    ) -> None:
        self.stats = Stats()
        handler = handler_for(
            self.stats, data, range_support=range_support, break_once=break_once
        )
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/file"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def test_probe_behavior() -> None:
    data = bytes(range(256)) * (4 * 1024 * 1024 // 256)
    work = Path(tempfile.mkdtemp(prefix="dkl-known-"))
    server = Server(data)
    try:
        download_to_file(
            server.url,
            work / "known.bin",
            sha1=sha1(data),
            size=len(data),
            connections=4,
        )
        requests, connections, _ = server.stats.snapshot()
        check("known-size-no-probe", requests == 4, f"requests={requests}")
        check("known-size-segments", connections == 4, f"connections={connections}")
    finally:
        server.close()

    server = Server(data)
    try:
        download_to_file(
            server.url,
            work / "unknown.bin",
            sha1=sha1(data),
            size=0,
            connections=4,
        )
        requests, _connections, _ = server.stats.snapshot()
        check("unknown-size-probes", requests == 5, f"requests={requests}")
    finally:
        server.close()


def test_batch_keepalive() -> None:
    files = {
        f"small-{index}.bin": bytes([index]) * 65536 for index in range(30)
    }

    class MultiHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        stats = Stats()
        lock = threading.Lock()

        def setup(self) -> None:
            super().setup()
            self.stats.connection(self.client_address[1])

        def handle(self) -> None:
            try:
                super().handle()
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                pass

        def log_message(self, _format, *_args) -> None:
            return

        def do_GET(self) -> None:
            self.stats.begin(self.headers.get("Range") or "")
            name = self.path.rsplit("/", 1)[-1]
            data = files.get(name)
            if data is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
                self.wfile.flush()
                self.stats.sent(len(data))
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                self.close_connection = True

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), MultiHandler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    work = Path(tempfile.mkdtemp(prefix="dkl-batch-"))
    try:
        tasks = [
            BatchDownload(
                dest=work / name,
                urls=[f"{base}/{name}"],
                sha1=sha1(data),
                size=len(data),
                label=name,
            )
            for name, data in files.items()
        ]
        success, failures = download_batch(
            tasks, connections=2, max_workers=6, percent_range=(10, 90)
        )
        check("batch-success", success == len(files), str(failures))
        check("batch-files", all((work / name).read_bytes() == data for name, data in files.items()))
        requests, connections, _ = MultiHandler.stats.snapshot()
        check("batch-requests", requests == len(files), f"requests={requests}")
        check(
            "batch-keepalive",
            connections <= 8,
            f"connections={connections} workers=6",
        )
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_range_fallback() -> None:
    data = bytes(range(256)) * (4 * 1024 * 1024 // 256)
    work = Path(tempfile.mkdtemp(prefix="dkl-norange-"))
    server = Server(data, range_support=False)
    try:
        download_to_file(
            server.url,
            work / "norange.bin",
            sha1=sha1(data),
            size=len(data),
            connections=4,
        )
        check("range-fallback", (work / "norange.bin").read_bytes() == data)
        requests, _connections, _ = server.stats.snapshot()
        check("range-fallback-requests", requests == 5, f"requests={requests}")
    finally:
        server.close()


def test_segment_resume() -> None:
    data = bytes(range(256)) * (8 * 1024 * 1024 // 256)
    work = Path(tempfile.mkdtemp(prefix="dkl-resume-"))
    server = Server(data, break_once=True)
    try:
        download_to_file(
            server.url,
            work / "resume.bin",
            sha1=sha1(data),
            size=len(data),
            connections=4,
        )
        check("segment-resume-file", (work / "resume.bin").read_bytes() == data)
        requests, _connections, sent = server.stats.snapshot()
        check("segment-resume-retried", requests >= 8, f"requests={requests}")
        check(
            "segment-resume-not-restarted",
            sent <= len(data) + 512 * 1024,
            f"sent={sent} size={len(data)}",
        )
    finally:
        server.close()


def test_batch_progress_and_failure() -> None:
    work = Path(tempfile.mkdtemp(prefix="dkl-progress-"))
    percentages: list[int] = []
    payload = b"x" * 1024

    def fake(url, dest, **kwargs):
        dest = Path(dest)
        callback = kwargs.get("progress_cb")
        if "bad" in url:
            if callback:
                callback(512, 1024)
            raise RuntimeError("模拟失败")
        dest.write_bytes(payload)
        if callback:
            callback(1024, 1024)
        return len(payload)

    task = BatchDownload(
        dest=work / "retry.bin",
        urls=["http://example.invalid/bad", "http://example.invalid/good"],
        size=len(payload),
        label="retry.bin",
    )
    success, failures = download_batch(
        [task],
        max_workers=1,
        progress_cb=lambda percent, _message: percentages.append(percent),
        download_func=fake,
    )
    check("batch-retry-success", success == 1, str(failures))
    check("batch-retry-file", (work / "retry.bin").read_bytes() == payload)
    check(
        "batch-progress-monotonic",
        percentages == sorted(percentages),
        str(percentages),
    )
    check("batch-progress-final", percentages and percentages[-1] == 100, str(percentages))


def test_sha1_failure_cleans_up() -> None:
    data = bytes(range(256)) * (2 * 1024 * 1024 // 256)
    work = Path(tempfile.mkdtemp(prefix="dkl-sha-"))
    server = Server(data)
    try:
        try:
            download_to_file(
                server.url,
                work / "bad.bin",
                sha1="0" * 40,
                size=len(data),
                connections=4,
            )
        except TransferError:
            pass
        else:
            raise AssertionError("sha1 mismatch should fail")
        check("sha1-failure", not (work / "bad.bin").exists())
        check(
            "sha1-cleanup",
            not list(work.glob("*.part")) and not list(work.glob("*.seg*")),
        )
    finally:
        server.close()


def main() -> int:
    test_probe_behavior()
    test_batch_keepalive()
    test_range_fallback()
    test_segment_resume()
    test_batch_progress_and_failure()
    test_sha1_failure_cleans_up()
    print(f"RESULT: PASS ({len(CHECKS)} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
