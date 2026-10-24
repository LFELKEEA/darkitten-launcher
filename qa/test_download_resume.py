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
    DownloadCancelled,
    cleanup_partial_files,
    download_to_file,
    partial_files,
)

CHECKS: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name} FAILED {detail}")
    CHECKS.append(name)
    print(f"{name}: OK")


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


class Stats:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.ranges: list[str] = []
        self.sent = 0

    def begin(self, value: str) -> None:
        with self.lock:
            self.ranges.append(value)

    def add(self, count: int) -> None:
        with self.lock:
            self.sent += count

    def reset(self) -> None:
        with self.lock:
            self.ranges.clear()
            self.sent = 0

    def snapshot(self) -> tuple[list[str], int]:
        with self.lock:
            return list(self.ranges), self.sent


def build_server(data: bytes, *, chunk: int = 64 * 1024, delay: float = 0.01):
    stats = Stats()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format, *_args) -> None:
            return

        def do_GET(self) -> None:
            range_header = self.headers.get("Range") or ""
            stats.begin(range_header)
            start, end = 0, len(data) - 1
            status = 200
            if range_header.startswith("bytes="):
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
                if start > end:
                    self.send_response(416)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                status = 206
            body = data[start : end + 1]
            self.send_response(status)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Accept-Ranges", "bytes")
            if status == 206:
                self.send_header(
                    "Content-Range", f"bytes {start}-{end}/{len(data)}"
                )
            self.end_headers()
            try:
                for offset in range(0, len(body), chunk):
                    piece = body[offset : offset + chunk]
                    self.wfile.write(piece)
                    self.wfile.flush()
                    stats.add(len(piece))
                    if delay:
                        time.sleep(delay)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                self.close_connection = True

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/file"

    def close() -> None:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)

    return url, stats, close


def run_cancelled(
    url: str,
    dest: Path,
    data: bytes,
    *,
    connections: int,
    delay: float,
) -> None:
    cancel = threading.Event()
    timer = threading.Timer(delay, cancel.set)
    timer.start()
    try:
        download_to_file(
            url,
            dest,
            sha1=sha1(data),
            size=len(data),
            connections=connections,
            cancel_event=cancel,
        )
    except DownloadCancelled:
        pass
    else:
        raise AssertionError("download should have been cancelled")
    finally:
        timer.cancel()


def test_segment_resume() -> None:
    data = bytes(range(256)) * (4 * 1024 * 1024 // 256)
    work = Path(tempfile.mkdtemp(prefix="dkl-resume-seg-"))
    dest = work / "big.bin"
    url, stats, close = build_server(data, delay=0.01)
    try:
        run_cancelled(url, dest, data, connections=2, delay=0.10)
        part = dest.with_name(dest.name + ".part")
        meta = dest.with_name(dest.name + ".part.json")
        segments = list(work.glob("big.bin.part.seg*"))
        check("segment-partial-exists", segments, f"segments={segments}")
        check("segment-sidecar-exists", meta.is_file())
        first_ranges, first_sent = stats.snapshot()
        check(
            "segment-first-cancelled-early",
            first_sent < len(data),
            f"sent={first_sent}",
        )
        check(
            "segment-first-used-ranges",
            any(item.startswith("bytes=") for item in first_ranges),
            str(first_ranges),
        )

        stats.reset()
        download_to_file(
            url,
            dest,
            sha1=sha1(data),
            size=len(data),
            connections=4,
        )
        check("segment-resume-content", dest.read_bytes() == data)
        check("segment-sidecar-removed", not meta.exists())
        check(
            "segment-partials-removed",
            not partial_files(work),
            str(partial_files(work)),
        )
        second_ranges, second_sent = stats.snapshot()
        check(
            "segment-resume-used-range",
            any(
                item.startswith("bytes=")
                and not item.startswith("bytes=0-")
                for item in second_ranges
            ),
            str(second_ranges),
        )
        check(
            "segment-resume-saved-bytes",
            second_sent < len(data),
            f"second_sent={second_sent}",
        )
    finally:
        close()


def test_single_resume() -> None:
    # 单流下载按 1 MiB 读取，数据必须大于一个读取块，取消才能稳定落在中途。
    data = bytes(range(251)) * (3 * 1024 * 1024 // 251)
    work = Path(tempfile.mkdtemp(prefix="dkl-resume-single-"))
    dest = work / "single.bin"
    url, stats, close = build_server(data, delay=0.01)
    try:
        run_cancelled(url, dest, data, connections=1, delay=0.06)
        part = dest.with_name(dest.name + ".part")
        meta = dest.with_name(dest.name + ".part.json")
        check("single-part-exists", part.is_file())
        check("single-sidecar-exists", meta.is_file())
        check(
            "single-sidecar-mode",
            '"mode": "single"' in meta.read_text(encoding="utf-8"),
        )

        stats.reset()
        download_to_file(
            url,
            dest,
            sha1=sha1(data),
            size=len(data),
            connections=1,
        )
        check("single-resume-content", dest.read_bytes() == data)
        check("single-sidecar-removed", not meta.exists())
        ranges, sent = stats.snapshot()
        check(
            "single-resume-range",
            any(
                item.startswith("bytes=")
                and not item.startswith("bytes=0-")
                for item in ranges
            ),
            str(ranges),
        )
        check("single-resume-saved-bytes", sent < len(data), f"sent={sent}")
    finally:
        close()


def test_complete_part_resume() -> None:
    data = bytes(range(251)) * (1024 * 1024 // 251)
    work = Path(tempfile.mkdtemp(prefix="dkl-resume-complete-"))
    dest = work / "complete.bin"
    part = dest.with_name(dest.name + ".part")
    meta = dest.with_name(dest.name + ".part.json")
    url, stats, close = build_server(data, delay=0.02)
    try:
        part.write_bytes(data)
        meta.write_text(
            '{"url": "%s", "size": %d, "sha1": "%s", '
            '"mode": "single", "ranges": []}'
            % (url, len(data), sha1(data)),
            encoding="utf-8",
        )
        download_to_file(
            url,
            dest,
            sha1=sha1(data),
            size=len(data),
            connections=1,
        )
        check("complete-part-content", dest.read_bytes() == data)
        check("complete-part-sidecar-removed", not meta.exists())
        ranges, sent = stats.snapshot()
        check("complete-part-no-request", not ranges, str(ranges))
        check("complete-part-no-bytes", sent == 0, f"sent={sent}")
    finally:
        close()


def test_stale_partial_cleanup() -> None:
    data = b"x" * (512 * 1024)
    work = Path(tempfile.mkdtemp(prefix="dkl-resume-stale-"))
    dest = work / "stale.bin"
    part = dest.with_name(dest.name + ".part")
    meta = dest.with_name(dest.name + ".part.json")
    part.write_bytes(b"old-data")
    meta.write_text(
        '{"url": "http://example.invalid/old", "size": 8, "sha1": "", '
        '"mode": "single", "ranges": []}',
        encoding="utf-8",
    )
    url, _stats, close = build_server(data, delay=0)
    try:
        download_to_file(url, dest, sha1=sha1(data), size=len(data))
        check("stale-url-content", dest.read_bytes() == data)
        check("stale-url-partial-cleaned", not meta.exists())
    finally:
        close()


def test_cleanup_includes_sidecar() -> None:
    work = Path(tempfile.mkdtemp(prefix="dkl-resume-clean-"))
    (work / "x.part").write_bytes(b"123")
    (work / "x.part.seg0").write_bytes(b"456")
    (work / "x.part.json").write_text("{}", encoding="utf-8")
    check("cleanup-sees-sidecar", len(partial_files(work)) == 3)
    removed, freed = cleanup_partial_files(work)
    check("cleanup-removed-all", removed == 3, f"removed={removed}")
    check("cleanup-freed-bytes", freed == 8, f"freed={freed}")


def main() -> None:
    test_segment_resume()
    test_single_resume()
    test_complete_part_resume()
    test_stale_partial_cleanup()
    test_cleanup_includes_sidecar()
    print(f"\n{len(CHECKS)} checks passed")


if __name__ == "__main__":
    main()
