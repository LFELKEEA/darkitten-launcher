from __future__ import annotations

import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from darkitten.version_zip import (
    VersionZipError,
    import_version_zip,
    inspect_version_zip,
)


def version_json(version_id: str, inherits: str = "") -> str:
    data = {
        "id": version_id,
        "type": "release",
        "mainClass": "net.minecraft.client.main.Main",
        "assets": "1.21",
        "assetIndex": {"id": "1.21", "url": "http://example.invalid/index.json"},
        "downloads": {"client": {"url": "", "size": 0, "sha1": ""}},
        "javaVersion": {"majorVersion": 21},
        "libraries": [],
    }
    if inherits:
        data["inheritsFrom"] = inherits
    return json.dumps(data, ensure_ascii=False)


class FakeInstaller:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def install(self, version_id: str) -> None:
        self.calls.append(version_id)


def check(label: str, value: bool, detail: str = "") -> bool:
    print(f"{label}: {'OK' if value else 'FAIL'}" + (f" ({detail})" if detail else ""))
    return value


def build_game_dir_zip(path: Path) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("versions/TestZip/TestZip.json", version_json("TestZip"))
        zf.writestr("versions/TestZip/TestZip.jar", b"jar-data")
        zf.writestr("versions/TestZip/mods/demo.jar", b"mod-data")
        zf.writestr("versions/TestZip/config/demo.toml", "value=1")
        zf.writestr("libraries/foo/bar/1.0/bar-1.0.jar", b"lib-data")
        zf.writestr("assets/indexes/1.21.json", b"{}")
        zf.writestr("mods/shared.jar", b"shared-mod")


def main() -> None:
    ok = True
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        archive = root / "game-dir.zip"
        build_game_dir_zip(archive)

        info = inspect_version_zip(archive)
        ok &= check(
            "inspect_cached",
            inspect_version_zip(archive) is info,
        )
        candidate = info.primary
        ok &= check("candidate_id", candidate.version_id == "TestZip", candidate.version_id)
        ok &= check("layout", candidate.layout == "game_dir", candidate.layout)
        ok &= check("has_jar", candidate.has_jar, candidate.jar_rel)
        ok &= check("jar_rel", candidate.jar_rel == "TestZip.jar", candidate.jar_rel)
        ok &= check("mods_count", candidate.mods == 2, str(candidate.mods))
        ok &= check("config_count", candidate.config == 1, str(candidate.config))
        ok &= check("libraries_count", candidate.libraries == 1, str(candidate.libraries))
        ok &= check("assets_count", candidate.assets == 1, str(candidate.assets))
        ok &= check("total_files", candidate.total_files == 7, str(candidate.total_files))

        target = root / "target-game"
        installer = FakeInstaller()
        result = import_version_zip(
            archive,
            target,
            "ZipCat",
            isolated=True,
            installer=installer,
        )
        run = target / "versions" / "ZipCat"
        ok &= check("installer_called", installer.calls == ["ZipCat"], str(installer.calls))
        ok &= check("json_written", (run / "ZipCat.json").is_file())
        ok &= check("jar_renamed", (run / "ZipCat.jar").read_bytes() == b"jar-data")
        ok &= check("version_mod", (run / "mods" / "demo.jar").is_file())
        ok &= check("shared_mod", (run / "mods" / "shared.jar").is_file())
        ok &= check("config_imported", (run / "config" / "demo.toml").is_file())
        ok &= check("library_imported", (target / "libraries/foo/bar/1.0/bar-1.0.jar").is_file())
        ok &= check("asset_imported", (target / "assets/indexes/1.21.json").is_file())
        restored = json.loads((run / "ZipCat.json").read_text(encoding="utf-8"))
        ok &= check("id_rewritten", restored.get("id") == "ZipCat", str(restored.get("id")))
        ok &= check("imported_count", result.imported_files == 7, str(result.imported_files))
        ok &= check("old_folder_absent", not (target / "versions" / "TestZip").exists())

        try:
            import_version_zip(archive, target, "ZipCat", isolated=True, installer=FakeInstaller())
            ok &= check("existing_rejected", False)
        except VersionZipError:
            ok &= check("existing_rejected", True)

        resume_installer = FakeInstaller()
        result2 = import_version_zip(
            archive,
            target,
            "ZipCat",
            resume=True,
            overwrite=True,
            isolated=True,
            installer=resume_installer,
        )
        ok &= check("resume_import", result2.imported_files == 7, str(result2.imported_files))
        ok &= check("resume_installer", resume_installer.calls == ["ZipCat"])

        flat = root / "flat.zip"
        with zipfile.ZipFile(flat, "w") as zf:
            zf.writestr("FlatCat.json", version_json("FlatCat"))
            zf.writestr("FlatCat.jar", b"flat-jar")
            zf.writestr("mods/flat.jar", b"flat-mod")
        flat_info = inspect_version_zip(flat)
        ok &= check("flat_layout", flat_info.primary.layout == "flat", flat_info.primary.layout)
        flat_target = root / "flat-game"
        import_version_zip(
            flat,
            flat_target,
            "FlatRestored",
            isolated=True,
            complete=False,
        )
        flat_run = flat_target / "versions" / "FlatRestored"
        ok &= check("flat_json", (flat_run / "FlatRestored.json").is_file())
        ok &= check("flat_jar", (flat_run / "FlatRestored.jar").read_bytes() == b"flat-jar")
        ok &= check("flat_mod", (flat_run / "mods" / "flat.jar").is_file())

        folder = root / "folder.zip"
        with zipfile.ZipFile(folder, "w") as zf:
            zf.writestr("MyCat/MyCat.json", version_json("MyCat", inherits="1.21.1"))
            zf.writestr("MyCat/MyCat.jar", b"mycat-jar")
            zf.writestr("MyCat/resourcepacks/rp.zip", b"rp")
            zf.writestr("MyCat/libraries/game/lib.jar", b"lib")
        folder_info = inspect_version_zip(folder)
        ok &= check("folder_layout", folder_info.primary.layout == "folder", folder_info.primary.layout)
        ok &= check(
            "inherits_display",
            folder_info.primary.minecraft_version == "1.21.1",
            folder_info.primary.minecraft_version,
        )
        folder_target = root / "folder-game"
        import_version_zip(
            folder,
            folder_target,
            "FolderRestored",
            isolated=True,
            complete=False,
        )
        ok &= check(
            "folder_library_at_root",
            (folder_target / "libraries/game/lib.jar").is_file(),
        )
        ok &= check(
            "folder_resourcepack",
            (
                folder_target
                / "versions/FolderRestored/resourcepacks/rp.zip"
            ).is_file(),
        )

        dotmc = root / "dotmc.zip"
        with zipfile.ZipFile(dotmc, "w") as zf:
            zf.writestr(".minecraft/versions/McDir/McDir.json", version_json("McDir"))
            zf.writestr(".minecraft/versions/McDir/McDir.jar", b"mc-jar")
            zf.writestr(".minecraft/libraries/lib/a.jar", b"lib")
            zf.writestr(".minecraft/assets/indexes/a.json", b"{}")
            zf.writestr(".minecraft/options.txt", "fov:70")
        mc_info = inspect_version_zip(dotmc)
        mc_c = mc_info.primary
        ok &= check("dotmc_prefix", mc_c.game_prefix == ".minecraft/", mc_c.game_prefix)
        mc_target = root / "dotmc-game"
        import_version_zip(
            dotmc,
            mc_target,
            "McRestored",
            isolated=True,
            complete=False,
        )
        ok &= check("dotmc_json", (mc_target / "versions/McRestored/McRestored.json").is_file())
        ok &= check("dotmc_jar", (mc_target / "versions/McRestored/McRestored.jar").is_file())
        ok &= check("dotmc_lib", (mc_target / "libraries/lib/a.jar").is_file())
        ok &= check("dotmc_asset", (mc_target / "assets/indexes/a.json").is_file())
        ok &= check("dotmc_options_ignored", not (mc_target / "options.txt").exists())

        multi = root / "multi.zip"
        with zipfile.ZipFile(multi, "w") as zf:
            zf.writestr("versions/A/A.json", version_json("A"))
            zf.writestr("versions/A/A.jar", b"a")
            zf.writestr("versions/B/B.json", version_json("B"))
        multi_info = inspect_version_zip(multi)
        ok &= check("multi_count", len(multi_info.candidates) == 2, str(len(multi_info.candidates)))
        ok &= check("multi_primary", multi_info.primary.version_id == "A", multi_info.primary.version_id)
        multi_target = root / "multi-game"
        selected = multi_info.find(version_id="B")
        import_version_zip(
            multi,
            multi_target,
            "RenamedB",
            candidate_json=selected.json_member,
            isolated=True,
            complete=False,
        )
        b_json = json.loads(
            (multi_target / "versions/RenamedB/RenamedB.json").read_text(encoding="utf-8")
        )
        ok &= check("multi_rename", b_json.get("id") == "RenamedB", str(b_json.get("id")))

        bad = root / "bad.zip"
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr("versions/Bad/Bad.json", version_json("Bad"))
            zf.writestr("../evil.txt", "oops")
        try:
            inspect_version_zip(bad)
            ok &= check("traversal_rejected", False)
        except VersionZipError:
            ok &= check("traversal_rejected", True)

        empty = root / "empty.zip"
        with zipfile.ZipFile(empty, "w") as zf:
            zf.writestr("mods/only.jar", b"mod")
        try:
            inspect_version_zip(empty)
            ok &= check("no_json_rejected", False)
        except VersionZipError:
            ok &= check("no_json_rejected", True)

        many = root / "many.zip"
        with zipfile.ZipFile(many, "w") as zf:
            zf.writestr("versions/Many/Many.json", version_json("Many"))
            for index in range(1200):
                zf.writestr(f"versions/Many/mods/m{index:04d}.jar", b"x" * 32)
        many_target = root / "many-game"
        calls: list[int] = []
        import_version_zip(
            many,
            many_target,
            "ManyRestored",
            isolated=True,
            complete=False,
            progress_cb=lambda percent, _message: calls.append(percent),
        )
        ok &= check(
            "many_files_imported",
            (many_target / "versions/ManyRestored/mods/m1199.jar").is_file(),
        )
        ok &= check("progress_throttled", len(calls) < 300, str(len(calls)))
        ok &= check("progress_final", calls[-1] == 100, str(calls[-1]))

        cancel_zip = root / "cancel.zip"
        with zipfile.ZipFile(cancel_zip, "w") as zf:
            zf.writestr("versions/Cancel/Cancel.json", version_json("Cancel"))
            zf.writestr("versions/Cancel/mods/a.jar", b"a")
        cancel_event = __import__("threading").Event()
        cancel_event.set()
        try:
            import_version_zip(
                cancel_zip,
                root / "cancel-game",
                "CancelRestored",
                isolated=True,
                complete=False,
                cancel_event=cancel_event,
            )
            ok &= check("cancel_rejected", False)
        except VersionZipError:
            ok &= check("cancel_rejected", True)

        import os
        import time

        stamp = archive.stat().st_mtime_ns
        os.utime(archive, ns=(stamp + 5_000_000_000, stamp + 5_000_000_000))
        ok &= check(
            "inspect_cache_invalidated",
            inspect_version_zip(archive) is not info,
        )

    print("RESULT:", "PASS" if ok else "FAIL")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
