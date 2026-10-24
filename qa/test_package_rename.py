from __future__ import annotations

import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from darkitten import config as config_module
from darkitten.updater import (
    CODE_PATHS,
    UpdateError,
    apply_update_code,
    prepare_update,
    read_version,
)


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise SystemExit(f"FAIL {name} {detail}")
    print(f"ok  {name} {detail}".rstrip())


def make_zip(package: Path, target: Path, root_name: str = "") -> Path:
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in package.rglob("*"):
            if path.is_file():
                relative = path.relative_to(package).as_posix()
                zf.write(path, f"{root_name}/{relative}" if root_name else relative)
    return target


def build_payload(root: Path, include_legacy_shim: bool) -> Path:
    package = root / "DKL-1.9.15"
    shutil.copytree(ROOT / "darkitten", package / "darkitten")
    shutil.copytree(ROOT / "assets", package / "assets")
    shutil.copy2(ROOT / "app.py", package / "app.py")
    shutil.copy2(ROOT / "requirements.txt", package / "requirements.txt")
    if include_legacy_shim:
        (package / "stellar").mkdir()
        (package / "stellar" / "__init__.py").write_text("", encoding="utf-8")
        (package / "stellar" / "config.py").write_text(
            'APP_VERSION = "1.9.15"\n', encoding="utf-8"
        )
        (package / "stellar" / "updater.py").write_text(
            "def apply_update_code(*args, **kwargs):\n    return None\n",
            encoding="utf-8",
        )
    return package


def build_old_install(root: Path) -> Path:
    install = root / "install"
    (install / "stellar").mkdir(parents=True)
    (install / "assets").mkdir(parents=True)
    (install / "minecraft" / "versions" / "1.21.1" / "mods").mkdir(parents=True)
    (install / "app.py").write_text(
        "from stellar.config import APP_NAME\n", encoding="utf-8"
    )
    (install / "stellar" / "__init__.py").write_text("", encoding="utf-8")
    (install / "stellar" / "config.py").write_text(
        'APP_VERSION = "1.9.14"\n', encoding="utf-8"
    )
    (install / "stellar" / "old_module.py").write_text("OLD = True\n", encoding="utf-8")
    (install / "assets" / "old.txt").write_text("old", encoding="utf-8")
    (install / "minecraft" / "versions" / "1.21.1" / "mods" / "sodium.jar").write_bytes(
        b"MOD"
    )
    (install / "config.json").write_text('{"player_name": "Steve"}', encoding="utf-8")
    return install


def main() -> int:
    check("darkitten_package", (ROOT / "darkitten" / "config.py").is_file())
    check("no_stellar_package", not (ROOT / "stellar").exists())
    check("version_1915", config_module.APP_VERSION == "1.9.15", config_module.APP_VERSION)
    check(
        "code_paths",
        "darkitten" in CODE_PATHS and "stellar" not in CODE_PATHS,
        repr(CODE_PATHS),
    )
    for path in [ROOT / "app.py", *sorted((ROOT / "darkitten").rglob("*.py"))]:
        text = path.read_text(encoding="utf-8", errors="replace")
        check(
            f"no_legacy_import_{path.relative_to(ROOT).as_posix()}",
            "stellar" not in text.lower(),
        )

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload = build_payload(root / "payload", include_legacy_shim=True)
        check("payload_version", read_version(payload) == "1.9.15")
        archive = make_zip(payload, root / "update.zip", payload.name)
        prepared = prepare_update(archive, root / "staging", "1.9.15")
        check("prepare_new_payload", read_version(prepared) == "1.9.15")

        legacy_only = root / "legacy-only"
        (legacy_only / "stellar").mkdir(parents=True)
        (legacy_only / "app.py").write_text("x\n", encoding="utf-8")
        (legacy_only / "stellar" / "config.py").write_text(
            'APP_VERSION = "1.9.15"\n', encoding="utf-8"
        )
        legacy_zip = make_zip(legacy_only, root / "legacy.zip")
        try:
            prepare_update(legacy_zip, root / "legacy-staging", "1.9.15")
        except UpdateError:
            rejected = True
        else:
            rejected = False
        check("stellar_only_payload_rejected", rejected)

        install = build_old_install(root / "old")
        backup = apply_update_code(prepared, install)
        check(
            "installed_darkitten",
            (install / "darkitten" / "config.py").is_file()
            and read_version(install) == "1.9.15",
        )
        check(
            "shim_not_installed_as_code",
            (install / "stellar" / "old_module.py").is_file()
            and not (install / "stellar" / "updater.py").exists(),
        )
        check(
            "legacy_backed_up",
            (backup / "app.py").is_file()
            and "stellar" in (backup / "app.py").read_text(encoding="utf-8"),
        )
        check(
            "user_data_kept",
            (install / "minecraft" / "versions" / "1.21.1" / "mods" / "sodium.jar").read_bytes()
            == b"MOD"
            and (install / "config.json").is_file(),
        )

    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
