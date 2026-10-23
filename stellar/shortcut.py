from __future__ import annotations

import base64
import ctypes
import subprocess
import sys
from pathlib import Path

from .config import APP_DIR

SHORTCUT_NAME = "Darkitten Launcher.lnk"
DESKTOP_CSIDL = 0x0010


class ShortcutError(Exception):
    pass


def _desktop_dir() -> Path:
    if sys.platform != "win32":
        raise ShortcutError("只有 Windows 支持创建桌面快捷方式。")
    try:
        buffer = ctypes.create_unicode_buffer(260)
        result = ctypes.windll.shell32.SHGetFolderPathW(
            None, DESKTOP_CSIDL, None, 0, buffer
        )
        if result == 0 and buffer.value:
            return Path(buffer.value)
    except Exception:
        pass
    for name in ("Desktop", "桌面"):
        candidate = Path.home() / name
        if candidate.is_dir():
            return candidate
    raise ShortcutError("找不到桌面文件夹。")


def shortcut_path() -> Path:
    return _desktop_dir() / SHORTCUT_NAME


def shortcut_exists() -> bool:
    try:
        return shortcut_path().is_file()
    except ShortcutError:
        return False


def _ps_quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _launch_target() -> tuple[Path, str]:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve(), ""
    script = (APP_DIR / "app.py").resolve()
    if not script.is_file():
        raise ShortcutError(f"找不到启动脚本：{script}")
    executable = Path(sys.executable).resolve()
    if executable.name.lower() == "python.exe":
        windowless = executable.with_name("pythonw.exe")
        if windowless.is_file():
            executable = windowless
    return executable, f'"{script}"'


def create_desktop_shortcut() -> Path:
    if sys.platform != "win32":
        raise ShortcutError("只有 Windows 支持创建桌面快捷方式。")
    executable, arguments = _launch_target()
    if not executable.is_file():
        raise ShortcutError(f"找不到启动程序：{executable}")
    icon = (APP_DIR / "assets" / "dkl.ico").resolve()
    target = shortcut_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "$ws = New-Object -ComObject WScript.Shell",
        f"$link = $ws.CreateShortcut({_ps_quote(target)})",
        f"$link.TargetPath = {_ps_quote(executable)}",
        f"$link.Arguments = {_ps_quote(arguments)}",
        f"$link.WorkingDirectory = {_ps_quote(APP_DIR.resolve())}",
        "$link.Description = 'Darkitten Launcher - Minecraft'",
        "$link.WindowStyle = 1",
    ]
    if icon.is_file():
        lines.append(f"$link.IconLocation = {_ps_quote(str(icon) + ',0')}")
    lines.append("$link.Save()")
    script = "\n".join(lines)
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-EncodedCommand",
                encoded,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            creationflags=flags,
        )
    except Exception as exc:
        raise ShortcutError(f"调用 PowerShell 失败：{exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise ShortcutError(detail or "系统拒绝了快捷方式创建请求。")
    if not target.is_file():
        raise ShortcutError("快捷方式创建后没有出现在桌面上。")
    return target
