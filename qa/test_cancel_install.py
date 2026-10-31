from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer

from darkitten.downloader import cleanup_partial_files, partial_files
from darkitten.workers import InstallWorker

ROOT = Path(tempfile.gettempdir()) / "darkitten_shots" / "cancel_game"


def main() -> int:
    ROOT.mkdir(parents=True, exist_ok=True)
    cleanup_partial_files(ROOT)
    app = QCoreApplication.instance() or QCoreApplication(sys.argv)
    worker = InstallWorker(ROOT, "1.21.1", source="auto", threads=8)
    result: dict = {"done": False}

    def on_finished(success: bool, message: str) -> None:
        result.update(done=True, success=success, message=message)
        app.quit()

    worker.finished.connect(on_finished)
    worker.log_line.connect(lambda line: print(f"LOG {line}", flush=True))
    loop = QEventLoop()
    guard = QTimer()
    guard.setSingleShot(True)
    guard.timeout.connect(loop.quit)
    guard.start(120000)
    worker.finished.connect(loop.quit)
    worker.start()

    started = time.time()
    QTimer.singleShot(6000, worker.cancel)
    loop.exec()
    guard.stop()

    leftovers = partial_files(ROOT)
    print(f"elapsed={time.time() - started:.1f}s", flush=True)
    print(f"finished={result.get('done')} success={result.get('success')}", flush=True)
    print(f"message={result.get('message')}", flush=True)
    print(f"partials={[str(p.relative_to(ROOT)) for p in leftovers][:8]}", flush=True)
    # 1.9.22 起取消安装会保留断点，设置页的“清理下载残余”负责手动释放空间。
    kept = bool(leftovers)
    removed, _freed = cleanup_partial_files(ROOT)
    cleaned = removed == len(leftovers) and not partial_files(ROOT)
    ok = (
        result.get("done")
        and result.get("success") is False
        and "取消" in str(result.get("message"))
        and kept
        and cleaned
    )
    print("CANCEL_INSTALL_OK" if ok else "CANCEL_INSTALL_FAIL", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
