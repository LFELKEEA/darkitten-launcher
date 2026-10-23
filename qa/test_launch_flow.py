from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer

import stellar.workers as workers
from stellar.accounts import Account
from stellar.config import Config
from stellar.launch import GameProcess


def test_launch_cancel(app: QCoreApplication) -> bool:
    def slow_prepare(*_args, **_kwargs):
        time.sleep(0.8)
        return SimpleNamespace(warnings=[])

    original = workers.prepare_launch
    workers.prepare_launch = slow_prepare
    try:
        worker = workers.LaunchWorker(
            Path("."), "test", Config(), [], Account.offline("Tester")
        )
        state: list[str] = []
        loop = QEventLoop()
        guard = QTimer()
        guard.setSingleShot(True)
        guard.timeout.connect(loop.quit)
        guard.start(4000)
        worker.cancelled.connect(lambda: (state.append("cancelled"), loop.quit()))
        worker.ready.connect(lambda *_: (state.append("ready"), loop.quit()))
        worker.failed.connect(lambda *_: (state.append("failed"), loop.quit()))
        worker.start()
        QTimer.singleShot(150, worker.cancel)
        loop.exec()
        guard.stop()
        ok = state == ["cancelled"]
        print("launch_worker_cancel:", "OK" if ok else f"FAIL {state}")
        return ok
    finally:
        workers.prepare_launch = original


def test_game_close(app: QCoreApplication) -> bool:
    command = [sys.executable, "-c", "import time; time.sleep(20)"]
    plan = SimpleNamespace(command=command, cwd=str(Path.cwd()))
    process = GameProcess(plan)
    state: list[object] = []
    started: list[int] = []
    loop = QEventLoop()
    guard = QTimer()
    guard.setSingleShot(True)
    guard.timeout.connect(loop.quit)
    guard.start(15000)
    process.started_ok.connect(started.append)
    process.game_finished.connect(lambda code: (state.append(code), loop.quit()))
    begin = time.time()
    process.start()
    QTimer.singleShot(600, lambda: process.stop(graceful=True))
    loop.exec()
    guard.stop()
    elapsed = time.time() - begin
    ok = bool(started) and bool(state) and not process.running and elapsed < 14
    print(
        "game_graceful_close:",
        "OK" if ok else "FAIL",
        f"pid={started[0] if started else '-'}",
        f"code={state[0] if state else '-'}",
        f"{elapsed:.1f}s",
    )
    return ok


def main() -> None:
    app = QCoreApplication.instance() or QCoreApplication(sys.argv)
    ok = test_launch_cancel(app)
    ok &= test_game_close(app)
    print("RESULT:", "PASS" if ok else "FAIL")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
