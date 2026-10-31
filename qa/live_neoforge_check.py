"""启动一次真实的 NeoForge 客户端，确认模块解析不再失败。

默认使用 config.json 里的游戏目录和 neoforge-21.1.264。测试进程会在看到
启动成功标记后自行关闭，只影响本次 QA 启动的进程。
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from darkitten.config import Config
from darkitten.launch import prepare_launch
from darkitten.paths import version_game_dir

VERSION = "neoforge-21.1.264"
LOG_PATH = ROOT / "qa" / "live_neoforge.log"
MARKERS = (
    "Sound engine started",
    "OpenAL initialized",
)


def close_windows(pid: int) -> None:
    """向测试进程的可见窗口发送 WM_CLOSE，让它正常退出。"""
    try:
        user32 = ctypes.windll.user32
        callback_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
        )
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.PostMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.PostMessageW.restype = wintypes.BOOL
        user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        user32.EnumWindows.restype = wintypes.BOOL

        def enum_window(hwnd, _lparam) -> bool:
            window_pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
            if window_pid.value == pid and user32.IsWindowVisible(hwnd):
                user32.PostMessageW(hwnd, 0x0010, 0, 0)
            return True

        callback = callback_type(enum_window)
        user32.EnumWindows(callback, 0)
    except Exception as exc:
        print("close_windows:", exc)


def main() -> int:
    cfg = Config.load()
    game_dir = Path(cfg.game_dir).expanduser()
    plan = prepare_launch(game_dir, VERSION, cfg)
    ignores = [arg for arg in plan.command if arg.startswith("-DignoreList=")]
    print("java:", plan.java.path, plan.java.version)
    print("run_dir:", version_game_dir(game_dir, VERSION, cfg.version_isolation))
    print("ignoreList:", ignores)

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("w", encoding="utf-8", errors="replace") as log:
        started = time.time()
        proc = subprocess.Popen(
            plan.command,
            cwd=plan.cwd,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=0x08000000,
        )
        print("pid:", proc.pid)
        status = "TIMEOUT"
        while time.time() - started < 180:
            if proc.poll() is not None:
                status = f"EXIT {proc.returncode}"
                break
            text = LOG_PATH.read_text(encoding="utf-8", errors="replace")
            if "ResolutionException" in text:
                status = "RESOLUTION_ERROR"
                break
            if any(marker in text for marker in MARKERS):
                status = "LAUNCHED"
                break
            time.sleep(2)

        if proc.poll() is None:
            close_windows(proc.pid)
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)

    text = LOG_PATH.read_text(encoding="utf-8", errors="replace")
    print("elapsed: %.1fs" % (time.time() - started))
    print("status:", status)
    errors = [
        line
        for line in text.splitlines()
        if "ResolutionException" in line or "Exception in thread" in line
    ]
    for line in errors[:10]:
        print("ERR:", line[:300])
    print("RESULT:", "PASS" if status == "LAUNCHED" else "FAIL")
    return 0 if status == "LAUNCHED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
