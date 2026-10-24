from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from darkitten.mod_conflicts import describe_mod, scan_mod_issues  # noqa: E402
from darkitten.mod_repair import (  # noqa: E402
    RepairError,
    apply_repair,
    build_repair_plan,
    delete_repair_backup,
    latest_repair,
    mark_repair_finished,
    mark_repair_launched,
    restore_repair,
)

CHECKS: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name} FAILED {detail}")
    CHECKS.append(name)
    print(f"{name}: OK")


def fabric_jar(
    path: Path,
    modid: str,
    *,
    name: str = "",
    version: str = "1.0.0",
    depends: dict | None = None,
    breaks: dict | None = None,
) -> None:
    payload = {
        "schemaVersion": 1,
        "id": modid,
        "version": version,
        "name": name or modid.title(),
        "depends": depends or {"minecraft": ">=1.20"},
    }
    if breaks:
        payload["breaks"] = breaks
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("fabric.mod.json", json.dumps(payload))


def forge_jar(path: Path, modid: str, *, version: str = "1.0.0") -> None:
    lines = [
        'modLoader="javafml"',
        'loaderVersion="[47,)"',
        'license="MIT"',
        "",
        "[[mods]]",
        f'modId="{modid}"',
        f'version="{version}"',
        f'displayName="{modid}"',
    ]
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("META-INF/mods.toml", "\n".join(lines) + "\n")


def plan_for(work: Path, loader: str = "Fabric"):
    issues, descriptors = scan_mod_issues(work, target_loader=loader)
    return issues, build_repair_plan(work, descriptors, loader)


def test_duplicate_backup_restore() -> None:
    work = Path(tempfile.mkdtemp(prefix="dkl-repair-dup-"))
    fabric_jar(work / "dupe-old.jar", "dupe", version="1.0.0")
    fabric_jar(work / "dupe-new.jar", "dupe", version="2.0.0")
    issues, plan = plan_for(work)
    check("dup-issue", any(item.title == "Mod ID 重复" for item in issues))
    check(
        "dup-plan-one-file",
        plan.files == ["dupe-old.jar"],
        str(plan.files),
    )

    backup = apply_repair(work, plan.actions)
    check("dup-old-moved", not (work / "dupe-old.jar").exists())
    check("dup-new-kept", (work / "dupe-new.jar").exists())
    check("dup-manifest", backup.manifest_path.exists())
    check("dup-state-pending", backup.state == "pending", backup.state)
    check("dup-backup-file", (backup.directory / "dupe-old.jar").exists())
    latest = latest_repair(work)
    check("dup-latest", latest is not None and latest.directory == backup.directory)

    issues_after, _ = scan_mod_issues(work, target_loader="Fabric")
    check(
        "dup-after-clean",
        not any(item.title == "Mod ID 重复" for item in issues_after),
        str([item.title for item in issues_after]),
    )

    restored, conflicts = restore_repair(backup)
    check("dup-restored", restored == ["dupe-old.jar"], str(restored))
    check("dup-restore-conflicts", not conflicts, str(conflicts))
    check("dup-restored-file", (work / "dupe-old.jar").exists())
    check("dup-state-restored", backup.state == "restored", backup.state)
    check("dup-latest-hidden", latest_repair(work) is None)


def test_declared_conflict() -> None:
    work = Path(tempfile.mkdtemp(prefix="dkl-repair-conflict-"))
    fabric_jar(
        work / "sodium.jar",
        "sodium",
        version="0.8.15",
        breaks={"iris": "<=1.10.7"},
    )
    fabric_jar(work / "iris.jar", "iris", version="1.10.6")
    issues, plan = plan_for(work)
    check(
        "conflict-issue",
        any(item.title == "声明了冲突" for item in issues),
        str([item.title for item in issues]),
    )
    check(
        "conflict-plan-provider",
        plan.files == ["iris.jar"],
        str([(item.path.name, item.reason) for item in plan.actions]),
    )
    apply_repair(work, plan.actions)
    check("conflict-provider-moved", not (work / "iris.jar").exists())
    check("conflict-owner-kept", (work / "sodium.jar").exists())


def test_provider_needed_moves_owner() -> None:
    work = Path(tempfile.mkdtemp(prefix="dkl-repair-needed-"))
    fabric_jar(
        work / "sodium.jar",
        "sodium",
        version="0.8.15",
        breaks={"iris": "<=1.10.7"},
    )
    fabric_jar(work / "iris.jar", "iris", version="1.10.6")
    fabric_jar(
        work / "shaderpack-loader.jar",
        "shaderpack_loader",
        depends={"minecraft": ">=1.20", "iris": "*"},
    )
    issues, plan = plan_for(work)
    check(
        "needed-conflict-issue",
        any(item.title == "声明了冲突" for item in issues),
    )
    check(
        "needed-moves-owner",
        plan.files == ["sodium.jar"],
        str([(item.path.name, item.reason) for item in plan.actions]),
    )


def test_loader_mismatch_and_missing_dep() -> None:
    work = Path(tempfile.mkdtemp(prefix="dkl-repair-loader-"))
    fabric_jar(work / "fab.jar", "fab")
    forge_jar(work / "forge.jar", "forge_mod")
    issues, plan = plan_for(work, "Fabric")
    check(
        "loader-issue",
        any(item.title == "加载器不匹配" for item in issues),
        str([item.title for item in issues]),
    )
    check("loader-plan", plan.files == ["forge.jar"], str(plan.files))

    missing = Path(tempfile.mkdtemp(prefix="dkl-repair-missing-"))
    fabric_jar(
        missing / "needy.jar",
        "needy",
        depends={"minecraft": ">=1.20", "missinglib": "*"},
    )
    issues_missing, plan_missing = plan_for(missing)
    check(
        "missing-issue",
        any(item.title == "缺少必需前置" for item in issues_missing),
    )
    check("missing-no-actions", not plan_missing.actions, str(plan_missing.files))
    check(
        "missing-skipped",
        any("缺少前置" in text for text in plan_missing.skipped),
        str(plan_missing.skipped),
    )

    optifine = Path(tempfile.mkdtemp(prefix="dkl-repair-optifine-"))
    fabric_jar(optifine / "fab.jar", "fab")
    issues_optifine, plan_optifine = plan_for(optifine, "OptiFine")
    check(
        "optifine-no-mismatch",
        not any(item.title == "加载器不匹配" for item in issues_optifine),
        str([item.title for item in issues_optifine]),
    )
    check("optifine-no-actions", not plan_optifine.actions, str(plan_optifine.files))


def test_state_machine_and_delete_guard() -> None:
    work = Path(tempfile.mkdtemp(prefix="dkl-repair-state-"))
    fabric_jar(work / "a-old.jar", "dup", version="1.0.0")
    fabric_jar(work / "a-new.jar", "dup", version="2.0.0")
    _issues, plan = plan_for(work)
    backup = apply_repair(work, plan.actions)

    try:
        delete_repair_backup(backup, recycle=lambda path: None)
    except RepairError:
        pass
    else:
        raise AssertionError("delete-before-verify-guard FAILED")

    mark_repair_launched(work)
    check("state-launched", latest_repair(work).state == "launched")
    mark_repair_finished(work, 1, 33.0)
    failed = latest_repair(work)
    check("state-failed", failed.state == "failed", failed.state)
    check("state-exit-code", failed.exit_code == 1, str(failed.exit_code))

    mark_repair_launched(work)
    mark_repair_finished(work, 0, 12.0)
    short = latest_repair(work)
    check("state-short-run", short.state == "launched", short.state)

    mark_repair_launched(work)
    mark_repair_finished(work, 0, 45.0)
    verified = latest_repair(work)
    check("state-verified", verified.state == "verified", verified.state)
    check("state-runtime", verified.runtime_seconds == 45.0)

    removed: list[Path] = []

    def fake_recycle(path) -> None:
        removed.append(Path(path))
        shutil.rmtree(path)

    delete_repair_backup(verified, recycle=fake_recycle)
    check("delete-recycles", removed == [verified.directory])
    check("delete-latest-gone", latest_repair(work) is None)


def test_restore_never_overwrites() -> None:
    work = Path(tempfile.mkdtemp(prefix="dkl-repair-overwrite-"))
    fabric_jar(work / "dupe-old.jar", "dupe", version="1.0.0")
    fabric_jar(work / "dupe-new.jar", "dupe", version="2.0.0")
    _issues, plan = plan_for(work)
    backup = apply_repair(work, plan.actions)
    (work / "dupe-old.jar").write_bytes(b"new user file")
    restored, conflicts = restore_repair(backup)
    check("overwrite-no-restore", not restored, str(restored))
    check("overwrite-conflict", conflicts == ["dupe-old.jar"], str(conflicts))
    check(
        "overwrite-target-kept",
        (work / "dupe-old.jar").read_bytes() == b"new user file",
    )
    check("overwrite-backup-kept", (backup.directory / "dupe-old.jar").exists())
    check("overwrite-state-partial", backup.state == "partial", backup.state)


def test_nested_provider_not_moved() -> None:
    work = Path(tempfile.mkdtemp(prefix="dkl-repair-nested-"))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as inner:
        inner.writestr(
            "fabric.mod.json",
            json.dumps(
                {
                    "schemaVersion": 1,
                    "id": "nested_net",
                    "version": "1.0.0",
                }
            ),
        )
    with zipfile.ZipFile(work / "bundle.jar", "w") as zf:
        zf.writestr(
            "fabric.mod.json",
            json.dumps(
                {
                    "schemaVersion": 1,
                    "id": "bundle",
                    "version": "1.0.0",
                    "breaks": {"nested_net": "*"},
                }
            ),
        )
        zf.writestr("META-INF/jars/nested.jar", buffer.getvalue())
    fabric_jar(
        work / "user.jar",
        "nested_net",
        depends={"minecraft": ">=1.20"},
    )
    issues, plan = plan_for(work)
    check(
        "nested-provider-conflict",
        any(item.title == "声明了冲突" for item in issues),
        str([item.title for item in issues]),
    )
    check(
        "nested-no-blind-move",
        "bundle.jar" not in plan.files,
        str([(item.path.name, item.reason) for item in plan.actions]),
    )


def main() -> None:
    test_duplicate_backup_restore()
    test_declared_conflict()
    test_provider_needed_moves_owner()
    test_loader_mismatch_and_missing_dep()
    test_state_machine_and_delete_guard()
    test_restore_never_overwrites()
    test_nested_provider_not_moved()
    print(f"\n{len(CHECKS)} checks passed")


if __name__ == "__main__":
    main()
