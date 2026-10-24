from __future__ import annotations

import io
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
from darkitten.mods import read_mod  # noqa: E402

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


def inner_jar(modid: str, version: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as inner:
        inner.writestr(
            "META-INF/neoforge.mods.toml",
            'modLoader="javafml"\n'
            'loaderVersion="[4,)"\n'
            "\n[[mods]]\n"
            f'modId="{modid}"\n'
            f'version="{version}"\n'
            f'displayName="{modid}"\n',
        )
    return buffer.getvalue()


def neoforge_jar(
    path: Path,
    modid: str,
    *,
    name: str = "",
    version: str = "1.0.0",
    dependencies: list[dict] | None = None,
    fabric_stub: dict | None = None,
    services: list[str] | None = None,
    nested: list[tuple[str, bytes, str]] | None = None,
) -> None:
    lines = [
        'modLoader="javafml"',
        'loaderVersion="[4,)"',
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
                f'type="{dep.get("type", "required")}"',
                f'versionRange="{dep.get("versionRange", "*")}"',
                'ordering="NONE"',
                f'side="{dep.get("side", "BOTH")}"',
            ]
        )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("META-INF/neoforge.mods.toml", "\n".join(lines) + "\n")
        if fabric_stub:
            zf.writestr("fabric.mod.json", json.dumps(fabric_stub))
        for service in services or []:
            zf.writestr(service, "test\n")
        if nested:
            entries = []
            for inner_name, inner_bytes, artifact_version in nested:
                zf.writestr(inner_name, inner_bytes)
                entries.append(
                    {
                        "identifier": {
                            "group": "test",
                            "artifact": Path(inner_name).stem,
                        },
                        "version": {
                            "range": f"[{artifact_version},)",
                            "artifactVersion": artifact_version,
                        },
                        "path": inner_name,
                    }
                )
            zf.writestr(
                "META-INF/jarjar/metadata.json",
                json.dumps({"jars": entries}),
            )


def wrapper_jar(
    path: Path,
    inner_name: str,
    inner_bytes: bytes,
    artifact_version: str,
) -> None:
    metadata = {
        "jars": [
            {
                "identifier": {"group": "test", "artifact": Path(inner_name).stem},
                "version": {
                    "range": f"[{artifact_version},)",
                    "artifactVersion": artifact_version,
                },
                "path": inner_name,
            }
        ]
    }
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("META-INF/jarjar/metadata.json", json.dumps(metadata))
        zf.writestr(inner_name, inner_bytes)


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


def test_neoforge_stub_and_universal_loaders() -> None:
    work = Path(tempfile.mkdtemp(prefix="dkl-conflicts-neoforge-"))
    neoforge_jar(
        work / "journeymap-neoforge.jar",
        "journeymap",
        name="Journeymap",
        version="1.21.1-6.0.9",
        fabric_stub={
            "schemaVersion": 1,
            "id": "journeymap-wrongloader",
            "version": "not a fabric mod",
            "name": "Journeymap-NeoForge",
        },
    )
    desc = describe_mod(work / "journeymap-neoforge.jar")
    check("neoforge-stub-loaders", desc.loaders == ["NeoForge"], str(desc.loaders))
    check("neoforge-stub-modid", desc.modids == ["journeymap"], str(desc.modids))
    check("neoforge-stub-name", desc.name == "Journeymap", desc.name)
    ui = read_mod(work / "journeymap-neoforge.jar")
    check("neoforge-ui-loader", ui.loader == "NeoForge", ui.loader)
    check("neoforge-ui-version", ui.version == "1.21.1-6.0.9", ui.version)
    issues, _ = scan_mod_issues(work, target_loader="NeoForge")
    check("neoforge-stub-no-issues", not issues, str(titles(issues)))

    universal = Path(tempfile.mkdtemp(prefix="dkl-conflicts-universal-"))
    with zipfile.ZipFile(universal / "CustomSkinLoader_Universal-15.1.jar", "w") as zf:
        zf.writestr(
            "fabric.mod.json",
            json.dumps(
                {
                    "schemaVersion": 1,
                    "id": "customskinloader-bootstrap",
                    "version": "15.1",
                    "name": "CustomSkinLoader Bootstrap",
                }
            ),
        )
        for service in (
            "META-INF/services/net.neoforged.neoforgespi.locating.IModLocator",
            "META-INF/services/cpw.mods.modlauncher.api.ITransformationService",
        ):
            zf.writestr(service, "test\n")
    universal_desc = describe_mod(universal / "CustomSkinLoader_Universal-15.1.jar")
    check(
        "universal-loaders",
        set(universal_desc.loaders) == {"Fabric", "NeoForge", "Forge"},
        str(universal_desc.loaders),
    )
    universal_ui = read_mod(universal / "CustomSkinLoader_Universal-15.1.jar")
    check("universal-ui-loader", universal_ui.loader == "多加载器", universal_ui.loader)
    universal_issues, _ = scan_mod_issues(universal, target_loader="NeoForge")
    check(
        "universal-no-issues",
        not universal_issues,
        str(titles(universal_issues)),
    )


def test_wrapped_dependencies_and_optional() -> None:
    work = Path(tempfile.mkdtemp(prefix="dkl-conflicts-wrapper-"))
    neoforge_jar(
        work / "jei-1.21.1-neoforge.jar",
        "jei",
        name="Just Enough Items",
        version="19.64.0.455",
        dependencies=[
            {
                "modId": "mezz_config",
                "type": "required",
                "versionRange": "[0.6.3,1.0.0)",
            },
            {
                "modId": "mezz_config_gui",
                "type": "optional",
                "versionRange": "[0.5.5,)",
            },
        ],
        nested=[
            (
                "META-INF/jarjar/mezz_config-1.21.1-neoforge-0.6.6.jar",
                inner_jar("mezz_config", "0.6.6"),
                "0.6.6",
            )
        ],
    )
    wrapper_jar(
        work / "mezz_config-1.21.1-neoforge-0.6.8-standalone.jar",
        "META-INF/jarjar/mezz_config-1.21.1-neoforge-0.6.8.jar",
        inner_jar("mezz_config", "0.6.8"),
        "0.6.8",
    )

    issues, _ = scan_mod_issues(work, target_loader="NeoForge")
    check("wrapper-no-issues", not issues, str(titles(issues)))

    wrapper = describe_mod(
        work / "mezz_config-1.21.1-neoforge-0.6.8-standalone.jar"
    )
    check("wrapper-modid", wrapper.modids == ["mezz_config"], str(wrapper.modids))
    check("wrapper-version", wrapper.version == "0.6.8", wrapper.version)
    check("wrapper-loader", wrapper.loaders == ["NeoForge"], str(wrapper.loaders))
    ui = read_mod(
        work / "mezz_config-1.21.1-neoforge-0.6.8-standalone.jar"
    )
    check("wrapper-ui-name", ui.name == "mezz_config", ui.name)
    check("wrapper-ui-version", ui.version == "0.6.8", ui.version)
    check("wrapper-ui-loader", ui.loader == "NeoForge", ui.loader)

    jei = describe_mod(work / "jei-1.21.1-neoforge.jar")
    check(
        "jei-nested-provides",
        jei.nested_provides.get("mezz_config") == "0.6.6",
        str(jei.nested_provides),
    )
    optional = next(dep for dep in jei.dependencies if dep.modid == "mezz_config_gui")
    check("optional-dep-not-mandatory", optional.mandatory is False)


def test_bundled_nested_dependency() -> None:
    work = Path(tempfile.mkdtemp(prefix="dkl-conflicts-bundled-"))
    neoforge_jar(
        work / "journeymap-neoforge.jar",
        "journeymap",
        name="Journeymap",
        version="1.21.1-6.0.9",
        dependencies=[
            {
                "modId": "commonnetworking",
                "type": "required",
                "versionRange": "[1.0.16,)",
            }
        ],
        fabric_stub={
            "schemaVersion": 1,
            "id": "journeymap-wrongloader",
            "version": "not a fabric mod",
        },
        nested=[
            (
                "META-INF/jarjar/common-networking-neoforge-1.0.21-1.21.1.jar",
                inner_jar("commonnetworking", "${file.jarVersion}"),
                "1.0.21-1.21.1",
            )
        ],
    )
    desc = describe_mod(work / "journeymap-neoforge.jar")
    check(
        "bundled-version-fallback",
        desc.nested_provides.get("commonnetworking") == "1.0.21-1.21.1",
        str(desc.nested_provides),
    )
    issues, _ = scan_mod_issues(work, target_loader="NeoForge")
    check("bundled-no-issues", not issues, str(titles(issues)))


def test_forge_dependency_types() -> None:
    required = Path(tempfile.mkdtemp(prefix="dkl-conflicts-required-"))
    neoforge_jar(
        required / "needs.jar",
        "needs",
        dependencies=[
            {
                "modId": "missing_core",
                "type": "required",
                "versionRange": "[1.0,)",
            }
        ],
    )
    issues, _ = scan_mod_issues(required, target_loader="NeoForge")
    check("required-type-missing", "缺少必需前置" in titles(issues), str(titles(issues)))
    desc = describe_mod(required / "needs.jar")
    check("required-type-mandatory", desc.dependencies[0].mandatory is True)

    optional = Path(tempfile.mkdtemp(prefix="dkl-conflicts-optional-"))
    neoforge_jar(
        optional / "soft.jar",
        "soft",
        dependencies=[
            {
                "modId": "missing_soft",
                "type": "optional",
                "versionRange": "[1.0,)",
            }
        ],
    )
    issues_optional, _ = scan_mod_issues(optional, target_loader="NeoForge")
    check(
        "optional-type-missing",
        not issues_optional,
        str(titles(issues_optional)),
    )
    desc_optional = describe_mod(optional / "soft.jar")
    check("optional-type-mandatory", desc_optional.dependencies[0].mandatory is False)


def test_loader_mix_ignores_universal() -> None:
    work = Path(tempfile.mkdtemp(prefix="dkl-conflicts-mix-"))
    fabric_jar(work / "fabric-only.jar", "fab", name="Fabric Only")
    neoforge_jar(work / "neoforge-only.jar", "neo", name="NeoForge Only")
    with zipfile.ZipFile(work / "universal.jar", "w") as zf:
        zf.writestr(
            "fabric.mod.json",
            json.dumps({"id": "universal", "version": "1.0.0"}),
        )
        zf.writestr(
            "META-INF/services/net.neoforged.neoforgespi.locating.IModLocator",
            "test\n",
        )
    issues, _ = scan_mod_issues(work)
    mix = next(item for item in issues if item.title == "加载器混装")
    check("mix-counts", "Fabric 1 个" in mix.message, mix.message)
    check("mix-counts-neoforge", "NeoForge 1 个" in mix.message, mix.message)

    issues_fabric, _ = scan_mod_issues(work, target_loader="Fabric")
    mismatches = [item for item in issues_fabric if item.title == "加载器不匹配"]
    check("mix-mismatch-count", len(mismatches) == 1, str(titles(issues_fabric)))
    check(
        "mix-universal-not-flagged",
        "universal.jar" not in mismatches[0].related,
        str(mismatches[0].related),
    )


def main() -> None:
    test_version_ranges()
    test_dependency_issues()
    test_duplicates_and_conflicts()
    test_loader_checks()
    test_invalid_jar_and_forge_dependencies()
    test_neoforge_stub_and_universal_loaders()
    test_wrapped_dependencies_and_optional()
    test_bundled_nested_dependency()
    test_forge_dependency_types()
    test_loader_mix_ignores_universal()
    print(f"\n{len(CHECKS)} checks passed")


if __name__ == "__main__":
    main()
