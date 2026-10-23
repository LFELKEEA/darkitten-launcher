from __future__ import annotations

import hashlib
import os
import re
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from .config import APP_VERSION

USER_AGENT = f"DarkittenLauncher/{APP_VERSION} (Minecraft Launcher)"
CHUNK_SIZE = 256 * 1024
MIN_SEGMENT_SIZE = 2 * 1024 * 1024
MIN_CONNECTION_SIZE = 1024 * 1024

BytesCallback = Callable[[int, int], None]


class DownloadCancelled(Exception):
    """用户主动取消下载。"""


class TransferError(Exception):
    """网络传输或校验失败。"""


class RangeUnsupported(Exception):
    """服务器没有按 Range 请求返回分段内容。"""


def http_open(
    url: str, timeout: int = 30, headers: dict[str, str] | None = None
):
    merged = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
    }
    if headers:
        merged.update(headers)
    req = urllib.request.Request(url, headers=merged)
    return urllib.request.urlopen(req, timeout=timeout)


def _check_cancel(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise DownloadCancelled()


def _probe(url: str, cancel_event: threading.Event | None) -> tuple[int, bool]:
    """探测文件大小与是否支持 Range；失败时返回未知大小。"""
    _check_cancel(cancel_event)
    try:
        with http_open(
            url, timeout=20, headers={"Range": "bytes=0-0"}
        ) as resp:
            status = int(getattr(resp, "status", 200) or 200)
            total = 0
            content_range = str(resp.headers.get("Content-Range") or "")
            match = re.match(r"bytes\s+\d+-\d+/(\d+)", content_range)
            if match:
                total = int(match.group(1))
            elif status == 200:
                total = int(resp.headers.get("Content-Length") or 0)
            accept_ranges = (
                str(resp.headers.get("Accept-Ranges") or "").lower() == "bytes"
            )
            if status == 206:
                try:
                    resp.read(1)
                except Exception:
                    pass
            return total, accept_ranges or status == 206
    except DownloadCancelled:
        raise
    except Exception:
        return 0, False


def _segment_path(part: Path, index: int) -> Path:
    return part.with_name(f"{part.name}.seg{index}")


def _remove_quietly(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _download_segment(
    url: str,
    path: Path,
    start: int,
    end: int,
    cancel_event: threading.Event | None,
    on_bytes: Callable[[int], None],
    attempts: int = 3,
) -> None:
    expected = end - start + 1
    last_error: Exception | None = None
    for attempt in range(attempts):
        _check_cancel(cancel_event)
        written = 0
        try:
            with http_open(
                url,
                timeout=45,
                headers={"Range": f"bytes={start}-{end}"},
            ) as resp:
                if int(getattr(resp, "status", 200) or 200) != 206:
                    raise RangeUnsupported()
                with path.open("wb") as fp:
                    while written < expected:
                        _check_cancel(cancel_event)
                        chunk = resp.read(min(CHUNK_SIZE, expected - written))
                        if not chunk:
                            break
                        fp.write(chunk)
                        written += len(chunk)
                        on_bytes(len(chunk))
            if written != expected:
                raise TransferError(
                    f"分段大小不一致（期望 {expected}，实际 {written}）"
                )
            return
        except DownloadCancelled:
            _remove_quietly(path)
            raise
        except RangeUnsupported:
            _remove_quietly(path)
            raise
        except Exception as exc:
            last_error = exc
            _remove_quietly(path)
            if attempt + 1 < attempts:
                time.sleep(0.6 * (attempt + 1))
    raise TransferError(f"分段下载失败：{last_error}")


def _download_single(
    url: str,
    part: Path,
    expected: int,
    cancel_event: threading.Event | None,
    on_bytes: Callable[[int], None],
) -> tuple[str, int]:
    _check_cancel(cancel_event)
    digest = hashlib.sha1()
    written = 0
    with http_open(url, timeout=45) as resp:
        total = int(resp.headers.get("Content-Length") or 0) or expected
        if total and expected and total != expected:
            # 以探测到的真实大小为准，交给最终校验判断。
            pass
        with part.open("wb") as fp:
            while True:
                _check_cancel(cancel_event)
                chunk = resp.read(CHUNK_SIZE)
                if not chunk:
                    break
                fp.write(chunk)
                digest.update(chunk)
                written += len(chunk)
                on_bytes(len(chunk))
                if total and written > total:
                    raise TransferError("服务器返回了超出预期大小的数据")
    return digest.hexdigest(), written


def download_to_file(
    url: str,
    dest: Path,
    *,
    sha1: str = "",
    size: int = 0,
    cancel_event: threading.Event | None = None,
    progress_cb: BytesCallback | None = None,
    connections: int = 4,
    probe: bool = True,
) -> int:
    """下载到目标文件，支持多连接分段、取消、SHA1 校验和原子替换。

    失败或取消时会删除所有临时分段，不会留下半截文件。
    """
    cancel_event = cancel_event or threading.Event()
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    _check_cancel(cancel_event)

    for stale in [part] + [
        _segment_path(part, index) for index in range(16)
    ]:
        _remove_quietly(stale)

    total = int(size or 0)
    supports_range = False
    # 已知的小文件直接单流下载，省掉一次 Range 探测请求。
    if probe and connections > 1 and (not total or total >= MIN_SEGMENT_SIZE):
        probed, supports_range = _probe(url, cancel_event)
        total = total or probed
        if not probed:
            supports_range = False
    _check_cancel(cancel_event)

    lock = threading.Lock()
    downloaded = 0
    last_report = [0.0]

    def report(count: int) -> None:
        nonlocal downloaded
        if count <= 0:
            return
        with lock:
            downloaded += count
            current = downloaded
            now = time.time()
            if progress_cb is None or (
                now - last_report[0] < 0.12 and current < total
            ):
                return
            last_report[0] = now
        if progress_cb is not None:
            progress_cb(current, total)

    segment_count = 1
    if (
        supports_range
        and total >= MIN_SEGMENT_SIZE
        and connections > 1
    ):
        segment_count = max(
            2, min(int(connections), total // MIN_CONNECTION_SIZE)
        )
        segment_count = min(segment_count, 8)

    try:
        if segment_count > 1:
            per_segment = (total + segment_count - 1) // segment_count
            ranges = []
            for index in range(segment_count):
                start = index * per_segment
                end = min(total - 1, start + per_segment - 1)
                if start > end:
                    break
                ranges.append((index, start, end))

            try:
                with ThreadPoolExecutor(max_workers=len(ranges)) as pool:
                    futures = [
                        pool.submit(
                            _download_segment,
                            url,
                            _segment_path(part, index),
                            start,
                            end,
                            cancel_event,
                            report,
                        )
                        for index, start, end in ranges
                    ]
                    for future in futures:
                        future.result()
            except RangeUnsupported:
                for index, _start, _end in ranges:
                    _remove_quietly(_segment_path(part, index))
                segment_count = 1
                with lock:
                    downloaded = 0
            except DownloadCancelled:
                for index, _start, _end in ranges:
                    _remove_quietly(_segment_path(part, index))
                raise
            except Exception:
                for index, _start, _end in ranges:
                    _remove_quietly(_segment_path(part, index))
                raise

        if segment_count > 1:
            digest = hashlib.sha1()
            with part.open("wb") as out:
                for index, _start, _end in ranges:
                    segment = _segment_path(part, index)
                    with segment.open("rb") as src:
                        while True:
                            chunk = src.read(1024 * 1024)
                            if not chunk:
                                break
                            out.write(chunk)
                            digest.update(chunk)
                    _remove_quietly(segment)
            hexdigest = digest.hexdigest()
        else:
            hexdigest, _written = _download_single(
                url, part, total, cancel_event, report
            )

        _check_cancel(cancel_event)
        actual = part.stat().st_size
        if total and actual != total:
            raise TransferError(
                f"文件大小不一致（期望 {total}，实际 {actual}）"
            )
        if sha1 and hexdigest.lower() != sha1.lower():
            raise TransferError("SHA1 校验失败")
        os.replace(part, dest)
        if progress_cb is not None:
            progress_cb(actual or total, actual or total)
        return actual
    except DownloadCancelled:
        _remove_quietly(part)
        for index in range(16):
            _remove_quietly(_segment_path(part, index))
        raise
    except Exception:
        _remove_quietly(part)
        for index in range(16):
            _remove_quietly(_segment_path(part, index))
        raise


def _looks_like_partial(path: Path) -> bool:
    name = path.name.lower()
    return ".part" in name and (
        name.endswith(".part")
        or name.endswith(".part.part")
        or ".part.seg" in name
        or name.endswith(".part.tmp")
    )


def partial_files(root: Path) -> list[Path]:
    root = Path(root)
    result: list[Path] = []
    if not root.exists():
        return result
    try:
        for path in root.rglob("*"):
            if path.is_file() and _looks_like_partial(path):
                result.append(path)
    except OSError:
        return result
    return result


def cleanup_partial_files(root: Path) -> tuple[int, int]:
    """删除 root 下所有下载临时文件，返回 (数量, 字节数)。"""
    removed = 0
    freed = 0
    for path in partial_files(Path(root)):
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        try:
            path.unlink()
        except OSError:
            continue
        removed += 1
        freed += size
    return removed, freed
