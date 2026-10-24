from __future__ import annotations

import json
import time
from pathlib import Path

MARKER_NAME = ".dkl-safe-mode.json"


class SafeModeError(Exception):
    pass


def marker_path(mods_dir: Path) -> Path:
    return Path(mods_dir) / MARKER_NAME


def is_safe_mode(mods_dir: Path) -> bool:
    return marker_path(mods_dir).is_file()


def enable_safe_mode(mods_dir: Path) -> list[str]:
    """把启用中的 Mod 临时改名为 .disabled，返回被禁用的文件名。"""
    mods_dir = Path(mods_dir)
    mods_dir.mkdir(parents=True, exist_ok=True)
    marker = marker_path(mods_dir)
    if marker.is_file():
        return list(_read_marker(marker).get("files") or [])

    moved: list[str] = []
    for path in sorted(mods_dir.iterdir()):
        if not path.is_file():
            continue
        if path.name.startswith(".") or path.suffix.lower() != ".jar":
            continue
        target = path.with_name(path.name + ".disabled")
        if target.exists():
            continue
        try:
            path.rename(target)
        except OSError as exc:
            raise SafeModeError(f"无法禁用 {path.name}：{exc}") from exc
        moved.append(path.name)

    marker.write_text(
        json.dumps(
            {
                "created_at": time.time(),
                "created_text": time.strftime("%Y-%m-%d %H:%M:%S"),
                "files": moved,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return moved


def _read_marker(marker: Path) -> dict:
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def restore_safe_mode(mods_dir: Path) -> int:
    """恢复被安全模式改名的 Mod，返回恢复数量。"""
    mods_dir = Path(mods_dir)
    marker = marker_path(mods_dir)
    if not marker.is_file():
        return 0
    data = _read_marker(marker)
    restored = 0
    for name in data.get("files") or []:
        if not isinstance(name, str) or not name:
            continue
        original = mods_dir / name
        disabled = mods_dir / (name + ".disabled")
        if original.exists():
            continue
        if not disabled.is_file():
            continue
        try:
            disabled.rename(original)
        except OSError:
            continue
        restored += 1
    try:
        marker.unlink()
    except OSError:
        pass
    return restored


def restore_pending(game_path: Path) -> list[Path]:
    """启动时清理上次未恢复的安全模式，返回处理过的目录。"""
    game_path = Path(game_path)
    candidates = [game_path / "mods"]
    versions = game_path / "versions"
    if versions.is_dir():
        candidates.extend(path / "mods" for path in versions.iterdir() if path.is_dir())
    handled: list[Path] = []
    for mods_dir in candidates:
        if mods_dir.is_dir() and is_safe_mode(mods_dir):
            if restore_safe_mode(mods_dir) >= 0:
                handled.append(mods_dir)
    return handled
