from __future__ import annotations

import json
import os
import struct
import sys
import tempfile
import threading
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))


def check(label: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{label} failed {detail}")
    print(f"ok  {label}" + (f" · {detail}" if detail else ""))


def make_version(root: Path, version_id: str) -> None:
    folder = root / "versions" / version_id
    folder.mkdir(parents=True, exist_ok=True)
    data = {
        "id": version_id,
        "type": "release",
        "mainClass": "net.minecraft.client.main.Main",
        "releaseTime": "2024-01-01T00:00:00+00:00",
        "javaVersion": {"majorVersion": 21},
        "assets": "5",
    }
    (folder / f"{version_id}.json").write_text(
        json.dumps(data), encoding="utf-8"
    )


def test_migration() -> None:
    from stellar.migration import (
        MigrationCancelled,
        collect_migration_plan,
        run_migration,
    )

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        make_version(root, "1.20.1")
        make_version(root, "1.21.1")
        source = root / "versions" / "1.20.1"
        target = root / "versions" / "1.21.1"
        (source / "saves" / "world").mkdir(parents=True)
        (source / "saves" / "world" / "level.dat").write_bytes(b"level-old")
        (source / "config").mkdir()
        (source / "config" / "demo.toml").write_text("key=1", encoding="utf-8")
        (source / "resourcepacks").mkdir()
        (source / "resourcepacks" / "pack.zip").write_bytes(b"pack")
        (source / "shaderpacks").mkdir()
        (source / "shaderpacks" / "shader.zip").write_bytes(b"shader")
        (source / "mods").mkdir()
        (source / "mods" / "example.jar").write_bytes(b"jar")
        (source / "mods" / "off.jar.disabled").write_bytes(b"off")
        (source / "options.txt").write_text("fov:0.5", encoding="utf-8")
        (source / "servers.dat").write_bytes(b"servers")

        plan = collect_migration_plan(root, "1.20.1", "1.21.1")
        check("plan counts", plan.total_files == 8, f"total={plan.total_files}")
        check(
            "plan categories",
            plan.file_counts
            == {
                "saves": 1,
                "config": 1,
                "resourcepacks": 1,
                "shaderpacks": 1,
                "options": 2,
                "mods": 2,
            },
            str(plan.file_counts),
        )
        check("plan mc versions", (plan.source_mc, plan.target_mc) == ("1.20.1", "1.21.1"))
        check("plan no conflict", plan.conflicts == 0, str(plan.conflicts))

        logs: list[str] = []
        report = run_migration(
            plan,
            match_mods=False,
            copy_unmatched=True,
            log_cb=logs.append,
        )
        check("copy all files", report.copied_files == 6, f"copied={report.copied_files}")
        check("copy mods", report.copied_mods == 2, f"mods={report.copied_mods}")
        check(
            "save copied",
            (target / "saves" / "world" / "level.dat").read_bytes() == b"level-old",
        )
        check("options copied", (target / "options.txt").is_file())
        check("servers copied", (target / "servers.dat").is_file())
        check("mod copied", (target / "mods" / "example.jar").read_bytes() == b"jar")
        check(
            "disabled mod preserved",
            (target / "mods" / "off.jar.disabled").read_bytes() == b"off",
        )
        check("warnings mention mods", any("example.jar" in line for line in logs))

        (target / "saves" / "world" / "level.dat").write_bytes(b"keep-me")
        plan2 = collect_migration_plan(root, "1.20.1", "1.21.1", ["saves"])
        check("conflict counted", plan2.conflicts == 1, str(plan2.conflicts))
        report2 = run_migration(plan2, match_mods=False, overwrite=False)
        check("conflict skipped", report2.skipped_files == 1, f"skip={report2.skipped_files}")
        check(
            "conflict kept target",
            (target / "saves" / "world" / "level.dat").read_bytes() == b"keep-me",
        )
        run_migration(plan2, match_mods=False, overwrite=True)
        check(
            "overwrite applied",
            (target / "saves" / "world" / "level.dat").read_bytes() == b"level-old",
        )

        flat = collect_migration_plan(root, "1.20.1", "1.21.1", ["saves"], isolated=False)
        check("shared dir detected", flat.same_dir)
        flat_report = run_migration(flat, match_mods=False)
        check("shared dir no copy", flat_report.copied_files == 0)

        cancel_plan = collect_migration_plan(root, "1.20.1", "1.21.1", ["config"])
        event = threading.Event()
        event.set()
        try:
            run_migration(cancel_plan, cancel_event=event)
        except MigrationCancelled:
            print("ok  migration cancellation")
        else:
            raise AssertionError("cancellation not raised")


def test_social() -> None:
    from stellar.social import (
        SocialError,
        encode_invite,
        load_store,
        parse_invite,
    )

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "social.json"
        store = load_store(path)
        profile = store.ensure_profile("Tester")
        check("profile id", profile.id.startswith("DKL-"), profile.id)
        check("profile persisted", path.is_file())
        check("profile reuse", load_store(path).profile.id == profile.id)

        store.rename_profile("猫猫")
        check("rename profile", load_store(path).profile.name == "猫猫")

        store.set_room("ROOM-1234", "1.21.1-fabric")
        check("room saved", load_store(path).profile.room_code == "ROOM-1234")

        card = encode_invite(profile, "ROOM-1234", "1.21.1-fabric", note="来玩")
        invite = parse_invite(card)
        check("invite roundtrip", invite.id == profile.id and invite.room_code == "ROOM-1234")
        check("invite version", invite.room_version == "1.21.1-fabric")
        check("invite note", invite.note == "来玩")

        try:
            store.add_invite(invite)
        except SocialError as exc:
            check("own invite rejected", "自己" in str(exc))
        else:
            raise AssertionError("own invite accepted")

        other = parse_invite(
            encode_invite(
                type(profile)(id="DKL-ABCD1234", name="好友"),
                "ROOM-9999",
                "1.20.1",
            )
        )
        friend = store.add_invite(other)
        check("friend added", friend.id == "DKL-ABCD1234", friend.id)
        check("friend persisted", load_store(path).friends[0].room_code == "ROOM-9999")

        store.update_room("DKL-ABCD1234", "ROOM-0000", "1.20.1")
        check(
            "friend room update",
            load_store(path).friends[0].room_code == "ROOM-0000",
        )
        check("remove friend", store.remove_friend("DKL-ABCD1234"))
        check("friends empty", load_store(path).friends == [])

        for bad in ("", "hello", "DKLF1:@@@"):
            try:
                parse_invite(bad)
            except SocialError:
                pass
            else:
                raise AssertionError(f"bad invite accepted: {bad!r}")
        print("ok  invalid invites rejected")


def test_visuals() -> None:
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from stellar.ui import icons
    from stellar.ui.widgets import Avatar, HeroArt

    avatar = Avatar(48)
    avatar.refresh()
    check("avatar refresh", not avatar._pixmap.isNull())

    face = icons.skin_face_pixmap(64)
    check("skin face size", face.size().width() == 64, str(face.size()))
    active = icons.active_skin_image()
    check("active skin", not active.isNull(), f"{active.width()}x{active.height()}")

    icon = icons.app_icon()
    check("app icon loaded", not icon.isNull())
    sizes = sorted(size.width() for size in icon.availableSizes())
    check("app icon sizes", len(sizes) >= 5, str(sizes))

    ico = PROJECT / "assets" / "dkl.ico"
    payload = ico.read_bytes()
    reserved, kind, count = struct.unpack("<HHH", payload[:6])
    check("ico header", (reserved, kind, count) == (0, 1, 7), f"count={count}")

    art = HeroArt()
    art.resize(420, 260)
    art._poke()
    check("hero particles", len(art._particles) == 10, str(len(art._particles)))
    check("hero phrase", art._phrase in art.PHRASES, art._phrase)
    for _ in range(10):
        art._tick()
    image = QImage(420, 260, QImage.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    art.render(painter, QPoint(0, 0))
    painter.end()
    check("hero painted", not image.isNull())
    app.processEvents()


def main() -> None:
    test_migration()
    test_social()
    test_visuals()
    print("ALL 1.9.0 FEATURE TESTS PASSED")


if __name__ == "__main__":
    main()
