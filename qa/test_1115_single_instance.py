from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from PySide6.QtNetwork import QLocalServer
from PySide6.QtWidgets import QApplication

from darkitten.single_instance import SingleInstanceGuard, default_server_name

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"{name}: OK{(' (' + detail + ')') if detail else ''}")
    else:
        FAIL += 1
        print(f"{name}: FAIL{(' (' + detail + ')') if detail else ''}")


def wait_for(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    app = QApplication.instance()
    while time.monotonic() < deadline:
        if app is not None:
            app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    if app is not None:
        app.processEvents()
    return bool(predicate())


def drain(pipe, lines: queue.Queue[str]) -> None:
    try:
        for line in iter(pipe.readline, ""):
            lines.put(line.rstrip())
    finally:
        try:
            pipe.close()
        except Exception:
            pass


def wait_line(lines: queue.Queue[str], timeout: float) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return lines.get(timeout=0.05)
        except queue.Empty:
            continue
    return ""


def guard_case(app: QApplication) -> None:
    name = f"darkitten-test-{uuid.uuid4().hex[:10]}"
    primary = SingleInstanceGuard(name)
    received: list[int] = []
    primary.activated.connect(lambda: received.append(1))
    check("首个实例成为主实例", primary.acquire() is True)
    check("主实例处于监听状态", primary.is_primary)
    check(
        "主实例服务已建立",
        primary._server is not None and primary._server.isListening(),
    )

    second = SingleInstanceGuard(name)
    check("第二个实例不应成为主实例", second.acquire() is False)
    check(
        "第二个实例激活已有窗口",
        wait_for(lambda: len(received) == 1),
        str(len(received)),
    )
    check("第二个实例自身不监听", second._server is None)

    # 激活发生在窗口创建之前时，先挂起，等界面就绪再补发。
    primary.close()
    check("关闭后释放主实例状态", primary.is_primary is False)

    pending_name = f"darkitten-test-{uuid.uuid4().hex[:10]}"
    pending = SingleInstanceGuard(pending_name)
    check("挂起场景主实例启动", pending.acquire() is True)
    pending_second = SingleInstanceGuard(pending_name)
    check("挂起场景第二实例退出", pending_second.acquire() is False)
    check(
        "界面未就绪时记录激活",
        wait_for(lambda: pending.has_pending_activation),
    )
    late: list[int] = []
    pending.activated.connect(lambda: late.append(1))
    check("挂起期间没有丢信号", late == [])
    pending.flush_pending_activation()
    check("界面就绪后补发激活", late == [1], str(late))
    check("补发后清空挂起标记", pending.has_pending_activation is False)
    pending.close()

    # 同名服务关闭后可以重新接管，模拟上次异常退出后的恢复。
    reuse_name = f"darkitten-test-{uuid.uuid4().hex[:10]}"
    old = SingleInstanceGuard(reuse_name)
    check("首次接管名称", old.acquire() is True)
    old.close()
    fresh = SingleInstanceGuard(reuse_name)
    check("关闭后可重新接管", fresh.acquire() is True)
    fresh.close()

    # 遗留的本地服务端点不应阻止下一次启动。
    stale_name = f"darkitten-test-{uuid.uuid4().hex[:10]}"
    stale = QLocalServer()
    stale.listen(stale_name)
    stale.close()
    recovered = SingleInstanceGuard(stale_name)
    check("遗留服务端点自动恢复", recovered.acquire() is True)
    recovered.close()

    # 不同用户/不同实例名互不影响。
    first_name = f"darkitten-test-{uuid.uuid4().hex[:10]}"
    other_name = f"darkitten-test-{uuid.uuid4().hex[:10]}"
    first = SingleInstanceGuard(first_name)
    other = SingleInstanceGuard(other_name)
    check("不同服务名可各自启动", first.acquire() and other.acquire())
    first.close()
    other.close()


def cross_process_case(helper: Path, server_name: str) -> None:
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    primary = subprocess.Popen(
        [sys.executable, str(helper), server_name, str(PROJECT)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(PROJECT),
    )
    lines: queue.Queue[str] = queue.Queue()
    assert primary.stdout is not None
    threading.Thread(
        target=drain, args=(primary.stdout, lines), daemon=True
    ).start()
    first_line = wait_line(lines, 8.0)
    check("跨进程主实例启动", first_line == "primary", first_line)

    started = time.monotonic()
    second = subprocess.run(
        [sys.executable, str(helper), server_name, str(PROJECT)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(PROJECT),
        timeout=10,
    )
    elapsed = time.monotonic() - started
    check("跨进程第二实例正常退出", second.returncode == 0, str(second.returncode))
    check(
        "跨进程第二实例识别为主实例已存在",
        "secondary" in (second.stdout or ""),
        (second.stdout or "").strip(),
    )
    check("第二实例快速退出", elapsed < 5.0, f"{elapsed:.2f}s")

    activated = False
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline and primary.poll() is None:
        line = wait_line(lines, 0.2)
        if line == "activated":
            activated = True
            break
        if line:
            print(f"helper: {line}")
    check("跨进程激活已有窗口", activated)

    try:
        primary.wait(timeout=8)
    except subprocess.TimeoutExpired:
        primary.terminate()
        try:
            primary.wait(timeout=3)
        except subprocess.TimeoutExpired:
            primary.kill()
            primary.wait(timeout=3)
    check("主实例正常结束", primary.returncode == 0, str(primary.returncode))


def main() -> int:
    app = QApplication.instance() or QApplication([])

    name = default_server_name()
    check(
        "默认服务名格式",
        bool(re.fullmatch(r"darkitten-launcher-[0-9a-f]{16}", name)),
        name,
    )
    check("默认服务名稳定", default_server_name() == name)

    guard_case(app)

    helper_source = """
import sys
from pathlib import Path

project = Path(sys.argv[2])
sys.path.insert(0, str(project))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from darkitten.single_instance import SingleInstanceGuard

app = QApplication([])
guard = SingleInstanceGuard(sys.argv[1])
if not guard.acquire():
    print("secondary", flush=True)
    raise SystemExit(0)
print("primary", flush=True)
guard.activated.connect(lambda: print("activated", flush=True))
QTimer.singleShot(3000, app.quit)
code = app.exec()
guard.close()
raise SystemExit(code)
"""
    with tempfile.TemporaryDirectory(prefix="dkl_single_") as temp:
        helper = Path(temp) / "helper.py"
        helper.write_text(helper_source, encoding="utf-8")
        cross_process_case(helper, f"darkitten-test-{uuid.uuid4().hex[:10]}")

    print(f"\nSINGLE INSTANCE QA: {PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
