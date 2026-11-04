from __future__ import annotations

import json
import sys
import tempfile
import zipfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from darkitten import mod_agent, pet

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


def write_state(root: Path, version: str) -> None:
    (root / "state.json").write_text(
        json.dumps(
            {"version": version, "installed_at": "2026-11-03T23:34:03+08:00"},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def write_version(app_dir: Path, relative: str, value: str) -> Path:
    path = app_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": value}), encoding="utf-8")
    return path


def test_pet(tmp: Path) -> None:
    root = tmp / "pet-root"
    root.mkdir()
    app_dir = pet.pet_app_dir(root)
    write_state(root, "1.3.0")

    check("pet_no_marker_uses_state", pet.installed_version(root) == "1.3.0")
    marker = write_version(app_dir, "_internal/version.json", "1.3.5")
    check("pet_packed_internal_wins", pet.installed_version(root) == "1.3.5")
    info = pet.installed_info(root)
    check("pet_keeps_installed_at", info.get("installed_at", "").startswith("2026-11-03"))

    marker.unlink()
    write_version(app_dir, "version.json", "v1.3.6")
    check("pet_packed_root_wins", pet.installed_version(root) == "1.3.6")

    (app_dir / "version.json").write_text("{broken", encoding="utf-8")
    check("pet_broken_marker_falls_back", pet.installed_version(root) == "1.3.0")


def test_mod_agent(tmp: Path) -> None:
    root = tmp / "agent-root"
    root.mkdir()
    app_dir = mod_agent.mod_agent_app_dir(root)
    write_state(root, "0.1.1")

    check("agent_no_marker_uses_state", mod_agent.installed_version(root) == "0.1.1")
    write_version(app_dir, "_internal/version.json", "0.3.12")
    check("agent_packed_internal_wins", mod_agent.installed_version(root) == "0.3.12")
    info = mod_agent.installed_info(root)
    check("agent_keeps_installed_at", info.get("installed_at", "").startswith("2026-11-03"))

    write_version(app_dir, "_internal/version.json", "0.3.10")
    write_version(app_dir, "version.json", "0.3.13")
    check("agent_root_marker_wins", mod_agent.installed_version(root) == "0.3.13")


def test_mod_agent_legacy_update(tmp: Path) -> None:
    root = tmp / "agent-legacy"
    root.mkdir()
    app_dir = mod_agent.mod_agent_app_dir(root)
    app_dir.mkdir(parents=True)
    (app_dir / "DarkittenModAgent.exe").write_bytes(b"new-exe-0311")
    updates = app_dir / "data" / "updates"
    updates.mkdir(parents=True)
    with zipfile.ZipFile(updates / "DarkittenModAgent-0.3.11.zip", "w") as archive:
        archive.writestr("DarkittenModAgent.exe", b"new-exe-0311")
    with zipfile.ZipFile(updates / "DarkittenModAgent-0.3.12.zip", "w") as archive:
        archive.writestr("DarkittenModAgent.exe", b"downloaded-but-not-applied")
    write_state(root, "0.1.1")

    check("agent_legacy_zip_fallback", mod_agent.installed_version(root) == "0.3.11")
    (updates / "staging-0.3.11").mkdir()
    check("agent_legacy_pending_ignored", mod_agent.installed_version(root) == "0.1.1")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        test_pet(base)
        test_mod_agent(base)
        test_mod_agent_legacy_update(base)
    print(f"PASS={PASS} FAIL={FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
