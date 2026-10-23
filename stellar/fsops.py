from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from pathlib import Path


class RecycleError(OSError):
    pass


if sys.platform == "win32":
    class _SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", ctypes.c_uint16),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]


def _recycle_windows(path: Path) -> None:
    if not hasattr(ctypes, "windll"):
        raise RecycleError("当前系统不支持回收站删除")
    shell = ctypes.windll.shell32
    operation = _SHFILEOPSTRUCTW()
    operation.hwnd = None
    operation.wFunc = 3  # FO_DELETE
    # SHFileOperation 需要双 NUL 结尾的路径列表
    operation.pFrom = str(path) + "\0\0"
    operation.pTo = None
    operation.fFlags = 0x0040 | 0x0010 | 0x0004 | 0x0400
    # FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
    operation.fAnyOperationsAborted = False
    operation.hNameMappings = None
    operation.lpszProgressTitle = None
    result = shell.SHFileOperationW(ctypes.byref(operation))
    if result != 0:
        raise RecycleError(f"移入回收站失败（错误码 {result}）")
    if operation.fAnyOperationsAborted:
        raise RecycleError("移入回收站操作被中止")


def recycle_path(path: Path | str) -> None:
    """把文件或目录移入系统回收站，不做永久删除。"""
    target = Path(path)
    if not target.exists():
        raise RecycleError(f"路径不存在：{target}")
    if sys.platform == "win32":
        _recycle_windows(target)
        return
    try:
        import send2trash  # type: ignore

        send2trash.send2trash(os.fspath(target))
    except ImportError as exc:
        raise RecycleError(
            "当前系统未安装 send2trash，无法安全地移入回收站"
        ) from exc
