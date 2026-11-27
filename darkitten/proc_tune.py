"""游戏进程调度：让 Minecraft 在后台运行时不抢占前台资源。

只做三件低风险的事：
- 把进程优先级降到 BELOW_NORMAL；
- 限制工作集，促使闲置内存换出到页面文件；
- 降低 IO 优先级，避免读写存档时卡住系统。

全部按 pid 操作，不注入、不改游戏本体；失败时静默返回，不影响启动。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

_WIN = sys.platform == "win32"

BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
NORMAL_PRIORITY_CLASS = 0x00000020
PROCESS_SET_QUOTA = 0x0100
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_SET_INFORMATION = 0x0200
QUOTA_LIMITS_HARDWS_MIN_DISABLE = 0x00000002
QUOTA_LIMITS_HARDWS_MAX_DISABLE = 0x00000008


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


def _kernel32():
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _open_process(pid: int, access: int):
    handle = _kernel32().OpenProcess(access, False, int(pid))
    return handle or None


def _close(handle) -> None:
    try:
        _kernel32().CloseHandle(handle)
    except Exception:
        pass


def set_background(pid: int) -> bool:
    """降优先级 + 限制工作集 + 降 IO 优先级，返回是否至少成功一项。"""
    if not _WIN or not pid:
        return False
    kernel = _kernel32()
    changed = False
    access = (
        PROCESS_SET_QUOTA
        | PROCESS_QUERY_INFORMATION
        | PROCESS_SET_INFORMATION
    )
    handle = _open_process(pid, access)
    if not handle:
        return False
    try:
        if kernel.SetPriorityClass(handle, BELOW_NORMAL_PRIORITY_CLASS):
            changed = True

        minimum = ctypes.c_size_t(0)
        maximum = ctypes.c_size_t(0)
        flags = ctypes.c_uint(
            QUOTA_LIMITS_HARDWS_MIN_DISABLE | QUOTA_LIMITS_HARDWS_MAX_DISABLE
        )
        if kernel.SetProcessWorkingSetSizeEx(
            handle, minimum, maximum, flags
        ):
            changed = True

        counters = _IO_COUNTERS()
        # 0 = Very Low，1 = Low；降到 Low 能明显减少和大文件读写抢盘。
        if kernel.SetProcessInformation(
            handle,
            33,  # ProcessIoPriority
            ctypes.byref(ctypes.c_int(1)),
            ctypes.sizeof(ctypes.c_int),
        ):
            changed = True
        del counters
    except Exception:
        return changed
    finally:
        _close(handle)
    return changed


def restore_foreground(pid: int) -> bool:
    """把优先级恢复正常，用于用户主动切回游戏前台。"""
    if not _WIN or not pid:
        return False
    handle = _open_process(pid, PROCESS_SET_INFORMATION)
    if not handle:
        return False
    try:
        return bool(_kernel32().SetPriorityClass(handle, NORMAL_PRIORITY_CLASS))
    except Exception:
        return False
    finally:
        _close(handle)


def trim_working_set(pid: int) -> bool:
    """把工作集里闲置的页换出，立即释放物理内存。"""
    if not _WIN or not pid:
        return False
    handle = _open_process(pid, PROCESS_SET_QUOTA)
    if not handle:
        return False
    try:
        kernel = _kernel32()
        kernel.SetProcessWorkingSetSize(handle, ctypes.c_size_t(-1), ctypes.c_size_t(-1))
        return True
    except Exception:
        return False
    finally:
        _close(handle)


def window_handle(pid: int) -> int:
    """找到进程的主窗口句柄，用于判断游戏是否在前台。"""
    if not _WIN or not pid:
        return 0
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        found = [0]

        callback_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
        )

        def enum_window(hwnd, _lparam) -> bool:
            owner = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == pid and user32.IsWindowVisible(hwnd):
                found[0] = int(hwnd)
                return False
            return True

        user32.EnumWindows(callback_type(enum_window), 0)
        return found[0]
    except Exception:
        return 0
