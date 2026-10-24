from __future__ import annotations

import hashlib
import http.client
import io
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import APP_VERSION

USER_AGENT = f"DarkittenLauncher/{APP_VERSION} (Minecraft Launcher)"
CHUNK_SIZE = 512 * 1024
MIN_SEGMENT_SIZE = 2 * 1024 * 1024
MIN_CONNECTION_SIZE = 1024 * 1024

# HTTP 长连接池。urllib 每发起一个请求都会重新完成一次 TCP + TLS 握手，
# 安装原版时几千个资源文件会因此浪费大量时间；这里按主机复用连接。
_IDLE_TTL = 45.0
_MAX_IDLE_PER_HOST = 24
_MAX_IDLE_TOTAL = 96
_MAX_DRAIN = 256 * 1024

BytesCallback = Callable[[int, int], None]
ProgressCallback = Callable[[int, str], None]
LogCallback = Callable[[str], None]

_pool_lock = threading.Lock()
_idle_pool: dict[
    tuple[str, str, int], list[tuple[http.client.HTTPConnection, float]]
] = {}
_idle_total = 0
_last_purge = 0.0
_ssl_context: ssl.SSLContext | None = None
_ssl_lock = threading.Lock()
_proxy_cache: dict[str, str] | None = None


class DownloadCancelled(Exception):
    """用户主动取消下载。"""


class TransferError(Exception):
    """网络传输或校验失败。"""


class RangeUnsupported(Exception):
    """服务器没有按 Range 请求返回分段内容。"""


def _get_ssl_context() -> ssl.SSLContext:
    global _ssl_context
    if _ssl_context is None:
        with _ssl_lock:
            if _ssl_context is None:
                _ssl_context = ssl.create_default_context()
    return _ssl_context


def _proxy_configured(scheme: str) -> bool:
    """代理环境下继续走 urllib，避免绕过用户系统代理。"""
    global _proxy_cache
    if _proxy_cache is None:
        try:
            _proxy_cache = {
                str(key).lower(): str(value)
                for key, value in urllib.request.getproxies().items()
                if value
            }
        except Exception:
            _proxy_cache = {}
    return bool(_proxy_cache.get(scheme) or _proxy_cache.get("all"))


def _new_connection(
    scheme: str, host: str, port: int, timeout: float
) -> http.client.HTTPConnection:
    if scheme == "https":
        return http.client.HTTPSConnection(
            host, port, timeout=timeout, context=_get_ssl_context()
        )
    return http.client.HTTPConnection(host, port, timeout=timeout)


def _purge_stale(now: float) -> None:
    global _idle_total, _last_purge
    if now - _last_purge < 15.0:
        return
    _last_purge = now
    for key in list(_idle_pool):
        entries = _idle_pool.get(key) or []
        keep: list[tuple[http.client.HTTPConnection, float]] = []
        for conn, stamp in entries:
            if now - stamp <= _IDLE_TTL and conn.sock is not None:
                keep.append((conn, stamp))
            else:
                try:
                    conn.close()
                except Exception:
                    pass
                _idle_total -= 1
        if keep:
            _idle_pool[key] = keep
        else:
            _idle_pool.pop(key, None)


def _acquire_connection(
    scheme: str,
    host: str,
    port: int,
    timeout: float,
    force_new: bool = False,
) -> tuple[http.client.HTTPConnection, tuple[str, str, int], bool]:
    global _idle_total
    key = (scheme, host, port)
    now = time.time()
    conn: http.client.HTTPConnection | None = None
    if not force_new:
        with _pool_lock:
            _purge_stale(now)
            entries = _idle_pool.get(key)
            while entries:
                candidate, stamp = entries.pop()
                if now - stamp <= _IDLE_TTL and candidate.sock is not None:
                    conn = candidate
                    _idle_total -= 1
                    break
                try:
                    candidate.close()
                except Exception:
                    pass
                _idle_total -= 1
            if entries is not None and not entries:
                _idle_pool.pop(key, None)

    if conn is None:
        return _new_connection(scheme, host, port, timeout), key, True

    conn.timeout = timeout
    try:
        if conn.sock is not None:
            conn.sock.settimeout(timeout)
    except OSError:
        pass
    return conn, key, False


def _release_connection(
    key: tuple[str, str, int], conn: http.client.HTTPConnection
) -> None:
    global _idle_total
    if conn.sock is None:
        try:
            conn.close()
        except Exception:
            pass
        return
    with _pool_lock:
        entries = _idle_pool.setdefault(key, [])
        if _idle_total >= _MAX_IDLE_TOTAL or len(entries) >= _MAX_IDLE_PER_HOST:
            try:
                conn.close()
            except Exception:
                pass
            if not entries:
                _idle_pool.pop(key, None)
            return
        entries.append((conn, time.time()))
        _idle_total += 1


class _PooledResponse:
    """把 http.client 响应包装成与 urllib 响应相近的上下文对象。"""

    def __init__(
        self,
        conn: http.client.HTTPConnection,
        resp: http.client.HTTPResponse,
        key: tuple[str, str, int],
        url: str,
    ) -> None:
        self._conn: http.client.HTTPConnection | None = conn
        self._resp = resp
        self._key = key
        self._url = url
        self._closed = False

    @property
    def status(self) -> int:
        return int(getattr(self._resp, "status", 200) or 200)

    @property
    def headers(self):
        return self._resp.headers

    @property
    def reason(self) -> str:
        return str(getattr(self._resp, "reason", "") or "")

    @property
    def url(self) -> str:
        return self._url

    def geturl(self) -> str:
        return self._url

    def read(self, amt: int | None = None) -> bytes:
        return self._resp.read(amt)

    def readinto(self, buffer) -> int:
        return self._resp.readinto(buffer)

    def isclosed(self) -> bool:
        return self._resp.isclosed()

    def __enter__(self) -> "_PooledResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.close()
        return False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        resp = self._resp
        conn = self._conn
        self._conn = None
        reusable = False
        try:
            if resp.isclosed():
                reusable = not resp.will_close
            else:
                length = getattr(resp, "length", None)
                if length is not None and length <= _MAX_DRAIN:
                    resp.read()
                    reusable = resp.isclosed() and not resp.will_close
                else:
                    resp.close()
        except Exception:
            reusable = False

        if conn is None:
            return
        if reusable and conn.sock is not None:
            _release_connection(self._key, conn)
        else:
            try:
                resp.close()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass


def _urllib_open(url: str, timeout: float, headers: dict[str, str]):
    req = urllib.request.Request(url, headers=headers)
    return urllib.request.urlopen(req, timeout=timeout)


def _pooled_open(url: str, timeout: float, headers: dict[str, str]):
    if not url.isascii():
        return _urllib_open(url, timeout, headers)

    current = url
    for _redirect in range(6):
        parts = urllib.parse.urlsplit(current)
        scheme = parts.scheme.lower()
        if scheme not in ("http", "https"):
            return _urllib_open(current, timeout, headers)
        host = parts.hostname
        if not host:
            raise ValueError(f"无效的下载地址：{current}")
        port = parts.port or (443 if scheme == "https" else 80)
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query

        conn, key, fresh = _acquire_connection(scheme, host, port, timeout)
        resp: http.client.HTTPResponse | None = None
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                conn.request("GET", path, headers=headers)
                resp = conn.getresponse()
                break
            except (http.client.HTTPException, OSError) as exc:
                last_error = exc
                try:
                    conn.close()
                except Exception:
                    pass
                # 只对“从池里取出的旧连接”做一次换新重试；新连接本身
                # 失败时直接交给上层重试，避免超时被成倍放大。
                if attempt >= 1 or fresh:
                    raise
                conn, key, fresh = _acquire_connection(
                    scheme, host, port, timeout, force_new=True
                )
        if resp is None:
            raise last_error or OSError("无法建立连接")

        status = int(getattr(resp, "status", 200) or 200)
        if status in (301, 302, 303, 307, 308):
            location = resp.getheader("Location")
            wrapper = _PooledResponse(conn, resp, key, current)
            wrapper.close()
            if not location:
                raise urllib.error.URLError("服务器返回了空的重定向地址")
            current = urllib.parse.urljoin(current, location)
            continue

        if status >= 400:
            body = b""
            try:
                body = resp.read(65536)
            except Exception:
                pass
            error_headers = resp.headers
            reason = str(getattr(resp, "reason", "") or "")
            wrapper = _PooledResponse(conn, resp, key, current)
            wrapper.close()
            raise urllib.error.HTTPError(
                current, status, reason, error_headers, io.BytesIO(body)
            )

        return _PooledResponse(conn, resp, key, current)

    raise urllib.error.URLError("重定向次数过多")


def http_open(
    url: str, timeout: float = 30, headers: dict[str, str] | None = None
):
    merged = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Accept-Encoding": "identity",
        "Connection": "keep-alive",
    }
    if headers:
        merged.update(headers)
    scheme = urllib.parse.urlsplit(url).scheme.lower()
    if scheme in ("http", "https") and not _proxy_configured(scheme):
        return _pooled_open(url, timeout, merged)
    return _urllib_open(url, timeout, merged)


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
        written = path.stat().st_size if path.exists() else 0
        if written > expected:
            _remove_quietly(path)
            written = 0
        if written == expected:
            return
        try:
            request_start = start + written
            with http_open(
                url,
                timeout=45,
                headers={"Range": f"bytes={request_start}-{end}"},
            ) as resp:
                if int(getattr(resp, "status", 200) or 200) != 206:
                    raise RangeUnsupported()
                content_range = str(resp.headers.get("Content-Range") or "")
                match = re.match(
                    r"bytes\s+(\d+)-(\d+)/(\d+|\*)", content_range
                )
                if match and int(match.group(1)) != request_start:
                    _remove_quietly(path)
                    raise TransferError("服务器返回了错误的分段范围")
                with path.open("ab" if written else "wb") as fp:
                    remaining = expected - written
                    while remaining > 0:
                        _check_cancel(cancel_event)
                        chunk = resp.read(min(CHUNK_SIZE, remaining))
                        if not chunk:
                            break
                        fp.write(chunk)
                        written += len(chunk)
                        remaining -= len(chunk)
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
        except TransferError as exc:
            # 范围异常时不要沿用可能已经损坏的分段，下一轮从头开始。
            if "错误的分段范围" in str(exc):
                _remove_quietly(path)
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.4 * (attempt + 1))
        except Exception as exc:
            last_error = exc
            # 保留已写入的部分，下一次只补剩余字节，避免整段重下。
            if attempt + 1 < attempts:
                time.sleep(0.4 * (attempt + 1))
    _remove_quietly(path)
    raise TransferError(f"分段下载失败：{last_error}")


def _download_single(
    url: str,
    part: Path,
    expected: int,
    cancel_event: threading.Event | None,
    on_bytes: Callable[[int], None],
    attempts: int = 3,
) -> tuple[str, int]:
    last_error: Exception | None = None
    for attempt in range(attempts):
        _check_cancel(cancel_event)
        written = part.stat().st_size if part.exists() else 0
        if expected and written > expected:
            _remove_quietly(part)
            written = 0
        digest = hashlib.sha1()
        if written:
            try:
                with part.open("rb") as existing:
                    while True:
                        chunk = existing.read(1024 * 1024)
                        if not chunk:
                            break
                        digest.update(chunk)
            except OSError:
                _remove_quietly(part)
                written = 0
                digest = hashlib.sha1()

        headers = {"Range": f"bytes={written}-"} if written else None
        mode = "ab" if written else "wb"
        try:
            with http_open(url, timeout=45, headers=headers) as resp:
                status = int(getattr(resp, "status", 200) or 200)
                if written and status == 200:
                    # 服务器不支持断点续传，只能从头覆盖。
                    written = 0
                    mode = "wb"
                    digest = hashlib.sha1()
                elif written and status != 206:
                    raise TransferError(f"续传失败（HTTP {status}）")

                total = int(resp.headers.get("Content-Length") or 0)
                if status == 206:
                    content_range = str(
                        resp.headers.get("Content-Range") or ""
                    )
                    match = re.match(
                        r"bytes\s+\d+-\d+/(\d+)", content_range
                    )
                    if match:
                        expected = int(match.group(1))
                    elif total:
                        expected = written + total
                elif total and not expected:
                    expected = written + total

                with part.open(mode) as fp:
                    while True:
                        _check_cancel(cancel_event)
                        chunk = resp.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        fp.write(chunk)
                        digest.update(chunk)
                        written += len(chunk)
                        on_bytes(len(chunk))
                        if expected and written > expected:
                            raise TransferError("服务器返回了超出预期大小的数据")
            if expected and written != expected:
                raise TransferError(
                    f"文件大小不一致（期望 {expected}，实际 {written}）"
                )
            return digest.hexdigest(), written
        except DownloadCancelled:
            _remove_quietly(part)
            raise
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.4 * (attempt + 1))
    _remove_quietly(part)
    raise TransferError(f"下载失败：{last_error}")


def download_to_file(
    url: str,
    dest: Path,
    *,
    sha1: str = "",
    size: int = 0,
    cancel_event: threading.Event | None = None,
    progress_cb: BytesCallback | None = None,
    connections: int = 4,
    probe: bool | None = None,
) -> int:
    """下载到目标文件，支持多连接分段、断点重试、取消与 SHA1 校验。

    probe 为 None（默认）时只在文件大小未知的情况下做一次 Range 探测；
    调用方已给出 size 时直接开始分段，可少一个网络往返。

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
    should_probe = (
        probe is True or (probe is None and total <= 0)
    ) and connections > 1 and (not total or total >= MIN_SEGMENT_SIZE)

    if should_probe:
        probed, supports_range = _probe(url, cancel_event)
        total = total or probed
        if not probed:
            supports_range = False
    elif total >= MIN_SEGMENT_SIZE and connections > 1:
        # 大文件且已知大小：直接尝试分段。若服务器不支持 Range，
        # 分段请求会立刻抛出 RangeUnsupported，再回退单流。
        supports_range = True
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
    if supports_range and total >= MIN_SEGMENT_SIZE and connections > 1:
        segment_count = max(
            2, min(int(connections), total // MIN_CONNECTION_SIZE)
        )
        segment_count = min(segment_count, 12)

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


@dataclass
class BatchDownload:
    """一个批量下载任务，供整合包等多文件场景使用。"""

    dest: Path
    urls: list[str] | Callable[[], list[str]]
    sha1: str = ""
    size: int = 0
    label: str = ""
    fallback: Callable[[], list[str]] | None = None


def download_batch(
    tasks: list[BatchDownload],
    *,
    cancel_event: threading.Event | None = None,
    connections: int = 4,
    max_workers: int = 8,
    progress_cb: ProgressCallback | None = None,
    percent_range: tuple[int, int] = (0, 100),
    log_cb: LogCallback | None = None,
    label: str = "下载",
    download_func: Callable[..., int] | None = None,
) -> tuple[int, list[str]]:
    """并发下载多个文件，返回 (成功数量, 失败信息)。

    每个任务内部仍可使用多连接分段；多个任务之间共享同一个并发池，
    适合整合包这类包含几十上百个中小文件的场景。
    """
    cancel_event = cancel_event or threading.Event()
    task_list = list(tasks)
    if not task_list:
        return 0, []

    start_pct, end_pct = percent_range
    total_bytes = sum(max(0, int(task.size or 0)) for task in task_list)
    total_tasks = len(task_list)
    lock = threading.Lock()
    per_task_done: dict[int, int] = {}
    state = {
        "bytes": 0,
        "tasks": 0,
        "last_report": 0.0,
        "max_percent": start_pct,
    }
    failures: list[str] = []

    def percent_for(fraction: float) -> int:
        fraction = max(0.0, min(1.0, fraction))
        return int(start_pct + (end_pct - start_pct) * fraction)

    def emit(force: bool = False) -> None:
        with lock:
            fraction = (
                min(1.0, state["bytes"] / total_bytes)
                if total_bytes
                else state["tasks"] / max(1, total_tasks)
            )
            percent = max(state["max_percent"], percent_for(fraction))
            state["max_percent"] = percent
            now = time.time()
            if not force and now - state["last_report"] < 0.15:
                return
            state["last_report"] = now
            done_bytes = state["bytes"]
            done_tasks = state["tasks"]
        if progress_cb is None:
            return
        message = f"{label} {done_tasks}/{total_tasks}"
        if total_bytes:
            message += (
                f" · {done_bytes / 1024 / 1024:.1f}"
                f"/{total_bytes / 1024 / 1024:.1f} MB"
            )
        progress_cb(percent, message)

    def report_file(index: int, done: int) -> None:
        with lock:
            previous = per_task_done.get(index, 0)
            delta = done - previous if done >= previous else done
            per_task_done[index] = done
            state["bytes"] += max(0, delta)
        emit()

    def reset_file(index: int) -> None:
        """换下载地址重试时扣掉上一次已经上报的字节，避免进度虚高。"""
        with lock:
            previous = per_task_done.pop(index, 0)
            state["bytes"] -= previous

    def attempt(url: str, task: BatchDownload, index: int) -> bool:
        def on_bytes(done: int, _total: int) -> None:
            report_file(index, max(0, int(done)))

        (download_func or download_to_file)(
            url,
            task.dest,
            sha1=task.sha1,
            size=task.size,
            cancel_event=cancel_event,
            progress_cb=on_bytes,
            connections=connections,
        )
        return True

    def run_task(index: int, task: BatchDownload) -> bool:
        if cancel_event.is_set():
            raise DownloadCancelled()
        task.dest = Path(task.dest)
        errors: list[str] = []
        try:
            task.dest.parent.mkdir(parents=True, exist_ok=True)
            raw_urls = task.urls() if callable(task.urls) else task.urls
        except DownloadCancelled:
            raise
        except Exception as exc:
            errors.append(str(exc))
            raw_urls = []
        candidates: list[str] = []
        seen: set[str] = set()
        for url in raw_urls or []:
            if url and url not in seen:
                seen.add(url)
                candidates.append(url)

        succeeded = False
        for position, url in enumerate(candidates):
            if cancel_event.is_set():
                raise DownloadCancelled()
            if position:
                reset_file(index)
            try:
                attempt(url, task, index)
                succeeded = True
                break
            except DownloadCancelled:
                raise
            except Exception as exc:
                errors.append(str(exc))

        if not succeeded and task.fallback is not None:
            try:
                fallback_urls = task.fallback() or []
            except Exception:
                fallback_urls = []
            for url in fallback_urls:
                if not url or url in seen:
                    continue
                seen.add(url)
                if cancel_event.is_set():
                    raise DownloadCancelled()
                reset_file(index)
                try:
                    attempt(url, task, index)
                    succeeded = True
                    break
                except DownloadCancelled:
                    raise
                except Exception as exc:
                    errors.append(str(exc))

        if succeeded:
            if task.size:
                report_file(index, int(task.size))
            with lock:
                state["tasks"] += 1
            if log_cb is not None:
                log_cb(f"下载完成：{task.label or task.dest.name}")
            emit(force=True)
            return True

        with lock:
            failures.append(
                f"{task.label or task.dest.name}："
                f"{errors[-1] if errors else '没有可用的下载地址'}"
            )
            state["tasks"] += 1
        emit(force=True)
        return False

    successes = 0
    workers = max(1, min(int(max_workers), len(task_list)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(run_task, index, task)
            for index, task in enumerate(task_list)
        ]
        try:
            for future in as_completed(futures):
                if future.result():
                    successes += 1
        except DownloadCancelled:
            for future in futures:
                future.cancel()
            raise

    return successes, failures


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
