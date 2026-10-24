from __future__ import annotations

import json
import tempfile
import zipfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from darkitten.mod_conflicts import (  # noqa: E402
    describe_mod,
    scan_mod_issues,
    version_in_range,
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


def forge_jar(
    path: Path,
    modid: str,
    *,
    name: str = "",
    version: str = "1.0.0",
    dependencies: list[dict] | None = None,
) -> None:
    lines = [
        'modLoader="javafml"',
        'loaderVersion="[47,)"',
        'license="MIT"',
        "",
        "[[mods]]",
        f'modId="{modid}"',
        f'version="{version}"',
        f'displayName="{name or modid.title()}"',
    ]
    for dep in dependencies or []:
        lines.extend(
            [
                "",
                f"[[dependencies.{modid}]]",
                f'modId="{dep["modId"]}"',
                "mandatory=true",
                f'versionRange="{dep.get("versionRange", "*")}"',
                'ordering="NONE"',
                'side="BOTH"',
            ]
        )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("META-INF/mods.toml", "\n".join(lines) + "\n")


def titles(issues) -> list[str]:
    return [item.title for item in issues]


def test_version_ranges() -> None:
    cases = [
        ("1.20.1", ">=1.20", True),
        ("1.19.4", ">=1.20", False),
        ("1.20.1", "[1.20,1.21)", True),
        ("1.21", "[1.20,1.21)", False),
        ("1.20.1", "[1.20, 1.21)", True),
        ("1.20.1", ">= 1.20", True),
        ("1.20.1", "<1.21", True),
        ("1.21.1", "< 1.21", False),
        ("2.0.0", "~1.20", False),
        ("1.20.5", "~1.20", True),
        ("1.99.0", "^1.20.1", True),
        ("2.0.0", "^1.20.1", False),
        ("1.20.1", "*", True),
        ("1.20.1", "", True),
    ]
    for version, spec, expected in cases:
        actual = version_in_range(version, spec)
        check(
            f"range[{spec}][{version}]",
            actual is expected,
            f"expected {expected}, got {actual}",
        )


def test_dependency_issues() -> None:
    work = Path(tempfile.mkdtemp(prefix="dkl-conflicts-dep-"))
    fabric_jar(work / "base.jar", "base", name="Base", version="1.5.0")
    fabric_jar(
        work / "addon.jar",
        "addon",
        name="Addon",
        depends={"minecraft": ">=1.20", "base": ">=2.0"},
    )
    fabric_jar(
        work / "needy.jar",
        "needy",
        name="Needy",
        depends={"minecraft": ">=1.20", "missinglib": "*"},
    )
    issues, descriptors = scan_mod_issues(work)
    check("dependency-descriptors", len(descriptors) == 3)
    check("dependency-missing-required", "缺少必需前置" in titles(issues), str(titles(issues)))
    check("dependency-version-mismatch", "前置版本不匹配" in titles(issues), str(titles(issues)))
    missing = next(item for item in issues if item.title == "缺少必需前置")
    check("dependency-missing-modid", "missinglib" in missing.message)
    mismatch = next(item for item in issues if item.title == "前置版本不匹配")
    check("dependency-mismatch-owner", mismatch.filename == "addon.jar")
    check("dependency-mismatch-related", mismatch.related == ["base.jar"])

    disabled = work / "needy.jar.disabled"
    (work / "needy.jar").rename(disabled)
    issues_disabled, _ = scan_mod_issues(work)
    check(
        "dependency-disabled-ignored",
        "缺少必需前置" not in titles(issues_disabled),
        str(titles(issues_disabled)),
    )


def test_duplicates_and_conflicts() -> None:
    work = Path(tempfile.mkdtemp(prefix="dkl-conflicts-dup-"))
    fabric_jar(work / "dupe-a.jar", "dupe", name="Dupe A")
    fabric_jar(work / "dupe-b.jar", "dupe", name="Dupe B")
    fabric_jar(work / "rival.jar", "rival", name="Rival")
    fabric_jar(
        work / "breaker.jar",
        "breaker",
        name="Breaker",
        breaks={"rival": "*"},
    )
    issues, _ = scan_mod_issues(work)
    check("duplicate-modid", "Mod ID 重复" in titles(issues), str(titles(issues)))
    check("declared-conflict", "声明了冲突" in titles(issues), str(titles(issues)))
    duplicate = next(item for item in issues if item.title == "Mod ID 重复")
    check("duplicate-severity", duplicate.severity == "error")
    check("duplicate-related", len(duplicate.related) == 2, str(duplicate.related))


def test_loader_checks() -> None:
    work = Path(tempfile.mkdtemp(prefix="dkl-conflicts-loader-"))
    fabric_jar(work / "fabric-mod.jar", "fab", name="Fab")
    forge_jar(work / "forge-mod.jar", "forge_mod", name="Forge Mod")
    issues, _ = scan_mod_issues(work)
    check("loader-mix", "加载器混装" in titles(issues), str(titles(issues)))

    issues_fabric, _ = scan_mod_issues(work, target_loader="Fabric")
    check(
        "loader-target-mismatch",
        "加载器不匹配" in titles(issues_fabric),
        str(titles(issues_fabric)),
    )

    clean = Path(tempfile.mkdtemp(prefix="dkl-conflicts-clean-"))
    fabric_jar(clean / "fab.jar", "fab", name="Fab")
    issues_clean, _ = scan_mod_issues(clean, target_loader="Fabric")
    check("loader-clean", not issues_clean, str(titles(issues_clean)))


def test_invalid_jar_and_forge_dependencies() -> None:
    work = Path(tempfile.mkdtemp(prefix="dkl-conflicts-invalid-"))
    (work / "broken.jar").write_bytes(b"not a zip")
    issues, _ = scan_mod_issues(work)
    check("invalid-jar-warning", "无法读取 Mod 元数据" in titles(issues), str(titles(issues)))

    forge = Path(tempfile.mkdtemp(prefix="dkl-conflicts-forge-"))
    forge_jar(
        forge / "neoforge-addon.jar",
        "nf_addon",
        name="NF Addon",
        dependencies=[{"modId": "missing_core", "versionRange": "[1.0,2.0)"}],
    )
    issues_forge, descriptors = scan_mod_issues(forge, target_loader="NeoForge")
    check("forge-descriptor", descriptors[0].loader == "Forge")
    check(
        "forge-missing-dep",
        "缺少必需前置" in titles(issues_forge),
        str(titles(issues_forge)),
    )

    desc = describe_mod(forge / "neoforge-addon.jar")
    check("forge-modid", desc.modids == ["nf_addon"], str(desc.modids))
    check("forge-dep-range", desc.dependencies[0].version_range == "[1.0,2.0)")


def main() -> None:
    test_version_ranges()
    test_dependency_issues()
    test_duplicates_and_conflicts()
    test_loader_checks()
    test_invalid_jar_and_forge_dependencies()
    print(f"\n{len(CHECKS)} checks passed")


if __name__ == "__main__":
    main()
