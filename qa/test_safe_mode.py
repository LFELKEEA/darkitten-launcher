from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from darkitten.safe_mode import (  # noqa: E402
    enable_safe_mode,
    is_safe_mode,
    restore_pending,
    restore_safe_mode,
)

CHECKS: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name} FAILED {detail}")
    CHECKS.append(name)
    print(f"{name}: OK")


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="dkl-safe-"))
    mods = root / "mods"
    mods.mkdir()
    (mods / "sodium.jar").write_bytes(b"sodium")
    (mods / "lithium.jar").write_bytes(b"lithium")
    (mods / "already.jar.disabled").write_bytes(b"disabled")
    (mods / "notes.txt").write_text("keep", encoding="utf-8")
    (mods / ".hidden.jar").write_bytes(b"hidden")

    check("safe-mode-off", not is_safe_mode(mods))
    moved = enable_safe_mode(mods)
    check("safe-mode-moved", sorted(moved) == ["lithium.jar", "sodium.jar"], str(moved))
    check("safe-mode-on", is_safe_mode(mods))
    check("safe-mode-renamed", (mods / "sodium.jar.disabled").is_file())
    check("safe-mode-keeps-disabled", (mods / "already.jar.disabled").is_file())
    check("safe-mode-keeps-text", (mods / "notes.txt").is_file())
    check("safe-mode-keeps-hidden", (mods / ".hidden.jar").is_file())
    check("safe-mode-marker", (mods / ".dkl-safe-mode.json").is_file())

    # 重复调用不会重复改名。
    moved_again = enable_safe_mode(mods)
    check("safe-mode-idempotent", sorted(moved_again) == ["lithium.jar", "sodium.jar"])

    restored = restore_safe_mode(mods)
    check("safe-mode-restored-count", restored == 2, str(restored))
    check("safe-mode-restored-files", (mods / "sodium.jar").is_file() and (mods / "lithium.jar").is_file())
    check("safe-mode-disabled-stays", (mods / "already.jar.disabled").is_file())
    check("safe-mode-off-again", not is_safe_mode(mods))
    check("safe-mode-restore-idempotent", restore_safe_mode(mods) == 0)

    # 启动恢复：扫描根目录与 versions/*/mods。
    root_mods = root / "game" / "mods"
    version_mods = root / "game" / "versions" / "1.21.11" / "mods"
    root_mods.mkdir(parents=True)
    version_mods.mkdir(parents=True)
    (root_mods / "root.jar").write_bytes(b"root")
    (version_mods / "fabric.jar").write_bytes(b"fabric")
    enable_safe_mode(root_mods)
    enable_safe_mode(version_mods)
    handled = restore_pending(root / "game")
    check("safe-mode-pending-dirs", len(handled) == 2, str(handled))
    check("safe-mode-pending-root", (root_mods / "root.jar").is_file())
    check("safe-mode-pending-version", (version_mods / "fabric.jar").is_file())
    check("safe-mode-pending-cleared", not is_safe_mode(root_mods) and not is_safe_mode(version_mods))

    print(f"RESULT: PASS ({len(CHECKS)} checks)")


if __name__ == "__main__":
    main()
