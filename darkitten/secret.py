from __future__ import annotations

import base64
import ctypes
import sys
from ctypes import wintypes


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data, len(data))
    value = _DataBlob(
        len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))
    )
    return value, buffer


def _dpapi(data: bytes, decrypt: bool) -> bytes:
    crypt32 = ctypes.windll.crypt32
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32 = ctypes.windll.kernel32
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL

    source, _source_buffer = _blob(data)
    output = _DataBlob()
    flags = 0x1  # CRYPTPROTECT_UI_FORBIDDEN
    if decrypt:
        description = wintypes.LPWSTR()
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(source),
            ctypes.byref(description),
            None,
            None,
            None,
            flags,
            ctypes.byref(output),
        )
        if description.value:
            kernel32.LocalFree(description)
    else:
        ok = crypt32.CryptProtectData(
            ctypes.byref(source),
            "DarkittenLauncher",
            None,
            None,
            None,
            flags,
            ctypes.byref(output),
        )
    if not ok:
        raise OSError("Windows DPAPI 调用失败")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)


def protect(text: str) -> str:
    """用 Windows DPAPI 加密敏感字符串；非 Windows 平台退化为 Base64。"""
    if not text:
        return ""
    raw = text.encode("utf-8")
    try:
        if sys.platform == "win32":
            return "dpapi:" + base64.b64encode(_dpapi(raw, False)).decode("ascii")
    except Exception:
        pass
    return "b64:" + base64.b64encode(raw).decode("ascii")


def unprotect(value: str) -> str:
    if not value:
        return ""
    if value.startswith("dpapi:"):
        try:
            raw = base64.b64decode(value[6:])
            return _dpapi(raw, True).decode("utf-8")
        except Exception:
            return ""
    if value.startswith("b64:"):
        try:
            return base64.b64decode(value[4:]).decode("utf-8")
        except Exception:
            return ""
    return value
