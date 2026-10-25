from __future__ import annotations

import queue
import shutil
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# 解压是磁盘密集型任务，实测 4 线程在机械盘和 SSD 上都最稳。
DEFAULT_EXTRACT_THREADS = 4
_SMALL_BUFFER = 1024 * 1024
_LARGE_BUFFER = 4 * 1024 * 1024
_LARGE_FILE = 8 * 1024 * 1024


class ZipExtractError(Exception):
    pass


class ZipExtractCancelled(ZipExtractError):
    pass


@dataclass(frozen=True)
class ExtractJob:
    info: zipfile.ZipInfo
    destination: Path
    label: str = ""
    # 全新版本目录里的目标文件必然不存在，可省掉每个文件一次 exists() 系统调用。
    check_exists: bool = True


@dataclass
class ExtractStats:
    added: int = 0
    skipped: int = 0
    bytes_written: int = 0


ProgressCallback = Callable[[int, int, str], None]


def extract_zip_entries(
    zip_path: Path | str,
    jobs: list[ExtractJob],
    *,
    threads: int = DEFAULT_EXTRACT_THREADS,
    overwrite: bool = False,
    cancel_event: threading.Event | None = None,
    progress_cb: ProgressCallback | None = None,
    progress_interval: float = 0.08,
) -> ExtractStats:
    """并行解压已经规划好目标路径的 ZIP 条目。

    progress_cb 参数为 (已处理字节, 已处理文件数, 最近文件标签)，默认按时间节流，
    避免几万个小文件产生同样数量的跨线程 UI 信号。
    """
    stats = ExtractStats()
    if not jobs:
        return stats
    archive = Path(zip_path)
    external_cancel = cancel_event or threading.Event()
    stop = threading.Event()
    lock = threading.Lock()
    pending: queue.SimpleQueue[ExtractJob] = queue.SimpleQueue()
    for job in jobs:
        pending.put(job)

    done_bytes = 0
    done_files = 0
    last_report = 0.0
    first_error: BaseException | None = None

    def report(label: str) -> None:
        nonlocal last_report
        if progress_cb is None:
            return
        now = time.monotonic()
        with lock:
            if now - last_report < progress_interval:
                return
            last_report = now
            snapshot = (done_bytes, done_files)
        progress_cb(snapshot[0], snapshot[1], label)

    def worker() -> None:
        nonlocal done_bytes, done_files, first_error
        buffer = _SMALL_BUFFER
        created_dirs: set[Path] = set()
        try:
            with zipfile.ZipFile(archive) as zf:
                while not stop.is_set() and not external_cancel.is_set():
                    try:
                        job = pending.get_nowait()
                    except queue.Empty:
                        break
                    try:
                        size = max(0, int(job.info.file_size))
                        if (
                            job.check_exists
                            and not overwrite
                            and job.destination.exists()
                        ):
                            with lock:
                                stats.skipped += 1
                                done_bytes += size
                                done_files += 1
                            report(job.label)
                            continue
                        parent = job.destination.parent
                        if parent not in created_dirs:
                            parent.mkdir(parents=True, exist_ok=True)
                            created_dirs.add(parent)
                        buffer = _LARGE_BUFFER if size >= _LARGE_FILE else _SMALL_BUFFER
                        with zf.open(
                            zf.getinfo(job.info.filename)
                        ) as src, job.destination.open("wb") as out:
                            shutil.copyfileobj(src, out, buffer)
                        with lock:
                            stats.added += 1
                            stats.bytes_written += size
                            done_bytes += size
                            done_files += 1
                        report(job.label)
                    except Exception as exc:
                        with lock:
                            if first_error is None:
                                first_error = ZipExtractError(
                                    f"{job.label or job.info.filename}：{exc}"
                                )
                        stop.set()
                        break
        except Exception as exc:
            with lock:
                if first_error is None:
                    first_error = ZipExtractError(str(exc))
            stop.set()

    requested = max(1, int(threads or DEFAULT_EXTRACT_THREADS))
    total_size = sum(max(0, int(job.info.file_size)) for job in jobs)
    average_size = total_size / len(jobs)
    if requested > 2 and average_size < 64 * 1024:
        # 大量几 KB 的小文件受文件系统元数据限制，2 线程反而比 4/8 线程快。
        requested = 2
    count = max(1, min(requested, len(jobs)))
    threads_list = [
        threading.Thread(target=worker, name=f"dkl-extract-{i}", daemon=True)
        for i in range(count)
    ]
    for thread in threads_list:
        thread.start()
    for thread in threads_list:
        thread.join()

    if external_cancel.is_set():
        raise ZipExtractCancelled("导入已取消")
    if first_error is not None:
        raise ZipExtractError(str(first_error)) from first_error
    if progress_cb is not None:
        with lock:
            snapshot = (done_bytes, done_files)
        progress_cb(snapshot[0], snapshot[1], "")
    return stats
