from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from darkitten import config as config_module
from darkitten.config import Config
from darkitten.mods import write_mod_meta
from darkitten.room_sync import (
    MAX_MANIFEST_MODS,
    ManifestMod,
    RoomManifest,
    RoomSyncError,
    build_room_manifest,
    decode_manifest,
    encode_manifest,
    plan_room_sync,
    sync_missing_mods,
)
from darkitten.social import SocialProfile, encode_invite, parse_invite
from darkitten.versions import VersionSummary

CHECKS: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name} FAILED {detail}")
    CHECKS.append(name)
    print(f"{name}: OK")


def make_versions(base: str = "1.21.1") -> list[VersionSummary]:
    profile = f"fabric-loader-0.19.5-{base}"
    return [
        VersionSummary(id=base, base_id=base),
        VersionSummary(
            id=profile,
            base_id=base,
            loader="Fabric",
            is_loader_profile=True,
        ),
    ]


def main() -> None:
    original_path = config_module.CONFIG_PATH
    root = Path(tempfile.mkdtemp(prefix="dkl_room_sync_test_"))
    config_module.CONFIG_PATH = root / "config.json"
    try:
        game = root / "game"
        game.mkdir()
        config = Config(game_dir=str(game), update_auto_check=False)
        versions = make_versions()
        config.version_loaders["1.21.1"] = "fabric-loader-0.19.5-1.21.1"

        mods = config.version_game_path("1.21.1") / "mods"
        mods.mkdir(parents=True, exist_ok=True)
        sodium = mods / "sodium.jar"
        sodium.write_bytes(b"sodium-content")
        sodium_sha1 = hashlib.sha1(sodium.read_bytes()).hexdigest()
        sodium_size = sodium.stat().st_size
        write_mod_meta(
            mods,
            sodium.name,
            {
                "title": "Sodium",
                "slug": "sodium",
                "project_id": "AANobbMI",
                "version_id": "sodium-v1",
                "source": "modrinth",
                "sha1": sodium_sha1,
            },
        )
        (mods / "lithium.jar.disabled").write_bytes(b"lithium-content")

        built = build_room_manifest(
            "1.21.1",
            config,
            versions,
            host_name="Host",
            lookup_unknown=False,
        )
        check("build_count", built.mod_count == 1, str(built.mod_count))
        entry = built.mods[0]
        check("build_filename", entry.filename == "sodium.jar")
        check("build_title", entry.title == "Sodium")
        check("build_source", entry.source == "modrinth")
        check("build_project", entry.project_id == "AANobbMI")
        check("build_sha1", entry.sha1 == sodium_sha1)
        check("build_loader", built.loader == "Fabric")
        check("build_mc", built.mc_version == "1.21.1")
        check("build_host", built.name == "Host")

        encoded = encode_manifest(built)
        check("manifest_prefix", encoded.startswith("DKLM1:"))
        decoded = decode_manifest(encoded)
        check("manifest_roundtrip", decoded.mod_count == 1)
        check("manifest_name", decoded.name == "Host")
        check("manifest_mc", decoded.mc_version == "1.21.1")
        check("manifest_loader", decoded.loader == "Fabric")
        check("manifest_sha1", decoded.mods[0].sha1 == sodium_sha1)

        profile = SocialProfile(id="DKL-ABCD1234", name="Host")
        invite_text = encode_invite(
            profile,
            "ABCDE-FGHIJ-KLMNO",
            "1.21.1",
            mod_manifest=encoded,
        )
        invite = parse_invite(invite_text)
        check("invite_room", invite.room_code == "ABCDE-FGHIJ-KLMNO")
        check("invite_manifest_present", bool(invite.mod_manifest))
        from_invite = decode_manifest(invite_text)
        check("invite_manifest_decode", from_invite.mod_count == 1)
        check("invite_manifest_name", from_invite.name == "Host")

        # 安全与边界
        traversal = ManifestMod.from_dict({"f": "../../evil.jar"})
        check("sanitize_path", traversal is not None and traversal.filename == "evil.jar")
        check("reject_non_jar", ManifestMod.from_dict({"f": "evil.txt"}) is None)
        bad_hash = ManifestMod.from_dict({"f": "a.jar", "h": "xyz"})
        check("sanitize_hash", bad_hash is not None and bad_hash.sha1 == "")
        bad_source = ManifestMod.from_dict({"f": "a.jar", "d": "evil"})
        check("sanitize_source", bad_source is not None and bad_source.source == "")
        too_many = {
            "mods": [{"f": f"m{i}.jar"} for i in range(MAX_MANIFEST_MODS + 1)]
        }
        try:
            RoomManifest.from_dict(too_many)
            check("limit_mods", False)
        except RoomSyncError:
            check("limit_mods", True)

        manifest = RoomManifest(
            name="Host",
            version_id="1.21.1",
            mc_version="1.21.1",
            loader="Fabric",
            mods=[
                ManifestMod(
                    filename="sodium.jar",
                    sha1=sodium_sha1,
                    size=sodium_size,
                    title="Sodium",
                    project_id="AANobbMI",
                    source="modrinth",
                ),
                ManifestMod(
                    filename="lithium.jar",
                    sha1="b" * 40,
                    size=321,
                    title="Lithium",
                    project_id="gvQqBUqZ",
                    source="modrinth",
                ),
                ManifestMod(filename="mystery.jar", title="Mystery"),
            ],
        )
        plan = plan_room_sync(manifest, "1.21.1", config, versions)
        check("plan_present", plan.present == 1, str(plan.present))
        check("plan_missing", len(plan.missing) == 1)
        check("plan_missing_name", plan.missing[0].title == "Lithium")
        check("plan_unknown", len(plan.unknown) == 1)
        check("plan_unknown_name", plan.unknown[0].title == "Mystery")
        check("plan_loader", plan.context.loader_label == "Fabric")
        check("plan_unknown_warning", any("无法自动下载" in item for item in plan.warnings))

        mismatch = RoomManifest(
            name="Host",
            version_id="1.20.1",
            mc_version="1.20.1",
            loader="Forge",
            mods=[manifest.mods[0]],
        )
        mismatch_plan = plan_room_sync(mismatch, "1.21.1", config, versions)
        text = "\n".join(mismatch_plan.warnings)
        check("warn_loader", "Forge" in text)
        check("warn_mc", "1.20.1" in text)

        only_present = RoomManifest(
            name="Host",
            version_id="1.21.1",
            mc_version="1.21.1",
            loader="Fabric",
            mods=[manifest.mods[0]],
        )
        present_plan = plan_room_sync(only_present, "1.21.1", config, versions)
        check("present_no_missing", not present_plan.missing)
        result = sync_missing_mods(present_plan, threads=2)
        check("sync_noop", not result.downloaded and not result.errors)

        print(f"ALL {len(CHECKS)} ROOM SYNC CHECKS PASSED")
    finally:
        config_module.CONFIG_PATH = original_path


if __name__ == "__main__":
    main()
