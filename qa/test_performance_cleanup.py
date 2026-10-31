from __future__ import annotations

import base64
import os
import sys
import tempfile
import time
import zipfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from darkitten.mods import (  # noqa: E402
    list_mods,
    read_mod,
    toggle_mod,
    write_mod_meta,
)
from darkitten.ui import icons  # noqa: E402
from darkitten.ui.pages.mods import _icon_pixmap  # noqa: E402

APP = QApplication.instance() or QApplication([])

CHECKS: list[str] = []
PNG_ONE = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
PNG_TWO = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name} FAILED {detail}")
    CHECKS.append(name)
    print(f"{name}: OK")


def neoforge_jar(path: Path, version: str = "1.0.0", name: str = "Demo") -> None:
    toml = f"""modLoader="javafml"
loaderVersion="[1,)"
license="MIT"

[[mods]]
modId="demo"
version="{version}"
displayName="{name}"
description="demo mod"
"""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("META-INF/neoforge.mods.toml", toml)


def touch_later(path: Path) -> None:
    stamp = time.time() + 2.0
    os.utime(path, (stamp, stamp))


def main() -> None:
    with tempfile.TemporaryDirectory() as raw:
        mods_dir = Path(raw)
        jar_a = mods_dir / "demo-a.jar"
        jar_b = mods_dir / "demo-b.jar"
        neoforge_jar(jar_a)
        neoforge_jar(jar_b, version="2.0.0", name="Other")

        first = list_mods(mods_dir)
        check("cache-first-scan", len(first) == 2)
        second = list_mods(mods_dir)
        check("cache-value-equal", [m.name for m in second] == [m.name for m in first])
        check("cache-returns-copies", second[0] is not first[0])

        neoforge_jar(jar_a, version="1.1.0")
        touch_later(jar_a)
        updated = read_mod(jar_a)
        check("cache-jar-invalidated", updated.version == "1.1.0", updated.version)

        write_mod_meta(
            mods_dir,
            jar_a.name,
            {"title": "喵喵工具", "description_zh": "中文介绍", "source": "modrinth"},
            PNG_ONE,
        )
        with_meta = read_mod(jar_a)
        check("cache-meta-jar-name-priority", with_meta.name == "Demo", with_meta.name)
        check(
            "cache-meta-description",
            with_meta.description_zh == "中文介绍",
            with_meta.description_zh,
        )
        check("cache-meta-icon", with_meta.icon_bytes == PNG_ONE)

        time.sleep(0.01)
        write_mod_meta(
            mods_dir,
            jar_a.name,
            {"title": "喵喵工具二代", "description_zh": "更新后的介绍"},
            PNG_TWO,
        )
        changed = read_mod(jar_a)
        check(
            "cache-meta-invalidated",
            changed.description_zh == "更新后的介绍",
            changed.description_zh,
        )
        check("cache-icon-invalidated", changed.icon_bytes == PNG_TWO)

        disabled_path = toggle_mod(changed)
        disabled = read_mod(disabled_path)
        check("cache-disabled-state", not disabled.enabled)
        check("cache-disabled-name", disabled.name == "Demo", disabled.name)

    face = icons.chibi_face_pixmap(24)
    check("chibi-face-cache", face is icons.chibi_face_pixmap(24))
    body = icons.chibi_pixmap(120)
    check("chibi-body-cache", body is icons.chibi_pixmap(120))
    glyph = icons.pixmap("play", "#112233", 18)
    check("glyph-cache", glyph is icons.pixmap("play", "#112233", 18))
    check("glyph-cache-color", glyph is not icons.pixmap("play", "#445566", 18))
    icon = _icon_pixmap(PNG_ONE, 32, 8)
    check("mod-icon-cache", icon is _icon_pixmap(PNG_ONE, 32, 8))

    print(f"performance_cleanup: {len(CHECKS)} checks passed")


if __name__ == "__main__":
    main()
