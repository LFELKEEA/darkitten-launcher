"""Minecraft 正式版版本清单。

界面里的版本下拉框和版本选择窗口共用这一份数据：优先读取启动器已经缓存
的 Mojang 版本清单（这样新版本发布后不用等启动器更新），没有缓存时退回
内置的完整正式版列表，最后再合入用户本机已安装的版本。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

# 只列正式版；快照仍然可以在下拉框里手动输入。
STATIC_RELEASE_VERSIONS: tuple[str, ...] = (
    "26.3",
    "26.2",
    "26.1.2",
    "26.1.1",
    "26.1",
    "1.21.11",
    "1.21.10",
    "1.21.9",
    "1.21.8",
    "1.21.7",
    "1.21.6",
    "1.21.5",
    "1.21.4",
    "1.21.3",
    "1.21.2",
    "1.21.1",
    "1.21",
    "1.20.6",
    "1.20.5",
    "1.20.4",
    "1.20.3",
    "1.20.2",
    "1.20.1",
    "1.20",
    "1.19.4",
    "1.19.3",
    "1.19.2",
    "1.19.1",
    "1.19",
    "1.18.2",
    "1.18.1",
    "1.18",
    "1.17.1",
    "1.17",
    "1.16.5",
    "1.16.4",
    "1.16.3",
    "1.16.2",
    "1.16.1",
    "1.16",
    "1.15.2",
    "1.15.1",
    "1.15",
    "1.14.4",
    "1.14.3",
    "1.14.2",
    "1.14.1",
    "1.14",
    "1.13.2",
    "1.13.1",
    "1.13",
    "1.12.2",
    "1.12.1",
    "1.12",
    "1.11.2",
    "1.11.1",
    "1.11",
    "1.10.2",
    "1.10.1",
    "1.10",
    "1.9.4",
    "1.9.3",
    "1.9.2",
    "1.9.1",
    "1.9",
    "1.8.9",
    "1.8.8",
    "1.8.7",
    "1.8.6",
    "1.8.5",
    "1.8.4",
    "1.8.3",
    "1.8.2",
    "1.8.1",
    "1.8",
    "1.7.10",
    "1.7.9",
    "1.7.8",
    "1.7.7",
    "1.7.6",
    "1.7.5",
    "1.7.4",
    "1.7.3",
    "1.7.2",
    "1.6.4",
    "1.6.2",
    "1.6.1",
    "1.5.2",
    "1.5.1",
    "1.4.7",
    "1.4.6",
    "1.4.5",
    "1.4.4",
    "1.4.2",
    "1.3.2",
    "1.3.1",
    "1.2.5",
    "1.2.4",
    "1.2.3",
    "1.2.2",
    "1.2.1",
    "1.1",
    "1.0",
)


def _version_key(version: str) -> tuple:
    text = str(version or "").strip().lower()
    if not text:
        return ()
    parts = re.findall(r"\d+|[a-z]+", text)
    key: list[tuple[int, int, str]] = []
    for part in parts:
        if part.isdigit():
            key.append((0, int(part), ""))
        else:
            key.append((1, 0, part))
    return tuple(key)


def _cached_release_versions() -> list[str]:
    try:
        from .installer import MANIFEST_CACHE

        path = Path(MANIFEST_CACHE)
        if not path.is_file():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        versions = data.get("versions")
        if not isinstance(versions, list):
            return []
        result: list[str] = []
        for item in versions:
            if not isinstance(item, dict):
                continue
            if str(item.get("type") or "") != "release":
                continue
            version = str(item.get("id") or "").strip()
            if version:
                result.append(version)
        return result
    except Exception:
        return []


def release_versions(extra: Iterable[str] = ()) -> list[str]:
    """完整正式版列表，新的在前；``extra`` 里的本机版本也会合入。"""
    seen: set[str] = set()
    result: list[str] = []

    def add(version: str) -> None:
        text = str(version or "").strip()
        if not text or text in seen:
            return
        seen.add(text)
        result.append(text)

    for version in _cached_release_versions():
        add(version)
    for version in STATIC_RELEASE_VERSIONS:
        add(version)
    for version in extra:
        add(version)
    result.sort(key=_version_key, reverse=True)
    return result
