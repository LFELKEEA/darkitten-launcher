from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import threading
import zipfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from darkitten.updater import (
    UpdateError,
    apply_update_code,
    check_for_update,
    download_update,
    is_newer,
    prepare_update,
    read_version,
    sha256_file,
    write_apply_script,
)


def build_payload(root: Path, version: str) -> Path:
    package = root / f"DarkittenLauncher-{version}-source"
    (package / "darkitten").mkdir(parents=True)
    (package / "assets").mkdir(parents=True)
    (package / "app.py").write_text(
        "from darkitten.config import APP_VERSION\nprint(APP_VERSION)\n",
        encoding="utf-8",
    )
    (package / "darkitten" / "config.py").write_text(
        f'APP_VERSION = "{version}"\n', encoding="utf-8"
    )
    (package / "darkitten" / "new_module.py").write_text(
        "VALUE = 'new'\n", encoding="utf-8"
    )
    (package / "darkitten" / "__pycache__").mkdir()
    (package / "darkitten" / "__pycache__" / "junk.pyc").write_bytes(b"junk")
    (package / "assets" / "new.txt").write_text("new asset", encoding="utf-8")
    (package / "requirements.txt").write_text(
        "PySide6-Essentials==6.12.0\n", encoding="utf-8"
    )
    (package / "launch.bat").write_text("@echo off\n", encoding="utf-8")
    return package


def build_install(root: Path) -> Path:
    install = root / "install"
    (install / "darkitten").mkdir(parents=True)
    (install / "assets").mkdir(parents=True)
    (install / "minecraft" / "versions" / "1.21.1" / "mods").mkdir(parents=True)
    (install / "skins").mkdir(parents=True)
    (install / "servers" / "paper").mkdir(parents=True)
    (install / "app.py").write_text("OLD APP\n", encoding="utf-8")
    (install / "darkitten" / "config.py").write_text(
        'APP_VERSION = "1.9.11"\n', encoding="utf-8"
    )
    (install / "darkitten" / "old_module.py").write_text(
        "OLD = True\n", encoding="utf-8"
    )
    (install / "darkitten" / "__pycache__").mkdir()
    (install / "darkitten" / "__pycache__" / "old.pyc").write_bytes(b"old")
    (install / "assets" / "old.txt").write_text("old asset", encoding="utf-8")
    (install / "config.json").write_text(
        json.dumps(
            {
                "account": {"name": "LFELKEEA", "token": "encrypted"},
                "player_name": "Steve",
                "update_url": "http://keep.me/update.json",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (install / "social.json").write_text(
        json.dumps({"friends": ["Mimi", "Neko"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (install / "minecraft" / "versions" / "1.21.1" / "mods" / "sodium.jar").write_bytes(
        b"MODDATA"
    )
    (install / "skins" / "custom.png").write_bytes(b"SKIN")
    (install / "servers" / "paper" / "server.properties").write_text(
        "motd=DKL\n", encoding="utf-8"
    )
    return install


def make_zip(package: Path, target: Path, root_name: str = "") -> Path:
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in package.rglob("*"):
            if path.is_file():
                relative = path.relative_to(package)
                arcname = (
                    f"{root_name}/{relative.as_posix()}"
                    if root_name
                    else relative.as_posix()
                )
                zf.write(path, arcname)
    return target


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format, *args) -> None:
        pass


def main() -> int:
    ok = True

    def check(name: str, condition: bool, detail: str = "") -> None:
        nonlocal ok
        print(f"{name}: {'OK' if condition else 'FAIL'}{(' ' + detail) if detail and not condition else ''}")
        ok &= bool(condition)

    check("version_compare", is_newer("1.9.12", "1.9.11") and not is_newer("1.9.11", "1.9.12"))
    check("version_compare_minor", is_newer("1.10.0", "1.9.99") and is_newer("2.0", "1.99.99"))

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        install = build_install(root)
        payload = build_payload(root / "src", "1.9.12")
        web = root / "web"
        web.mkdir()
        archive = make_zip(
            payload, web / "DKL-1.9.12-update.zip", payload.name
        )
        digest = sha256_file(archive)
        manifest = {
            "version": "1.9.12",
            "released": "2026-10-23",
            "notes": ["启动器更新检测", "下载站", "保留游戏数据"],
            "download_url": "DKL-1.9.12-update.zip",
            "page_url": "index.html",
            "sha256": digest,
            "size": archive.stat().st_size,
        }
        (web / "update.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (web / "index.html").write_text("<html>DKL</html>", encoding="utf-8")

        server = ThreadingHTTPServer(("127.0.0.1", 0), lambda *a, **k: QuietHandler(*a, directory=str(web), **k))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}/"
        try:
            release = check_for_update("1.9.11", base + "update.json")
            check(
                "manifest_check",
                release is not None
                and release.version == "1.9.12"
                and release.download_url.endswith("DKL-1.9.12-update.zip")
                and release.page_url.endswith("index.html")
                and len(release.notes) == 3,
            )
            check("manifest_latest", check_for_update("1.9.12", base + "update.json") is None)

            updates = install / "updates"
            progress: list[tuple[int, str]] = []
            downloaded = download_update(
                release, updates, progress_cb=lambda p, m: progress.append((p, m))
            )
            check(
                "download_sha",
                downloaded.is_file() and sha256_file(downloaded) == digest,
            )
            check("download_progress", bool(progress) and progress[-1][0] == 100)

            prepared = prepare_update(downloaded, updates, "1.9.12")
            check(
                "prepare_payload",
                read_version(prepared) == "1.9.12"
                and (prepared / "darkitten" / "new_module.py").is_file(),
            )

            backup = apply_update_code(prepared, install)
            check(
                "applied_code",
                "from darkitten.config" in (install / "app.py").read_text(encoding="utf-8")
                and (install / "darkitten" / "new_module.py").is_file()
                and not (install / "darkitten" / "old_module.py").exists()
                and read_version(install) == "1.9.12",
            )
            check(
                "preserved_config",
                json.loads((install / "config.json").read_text(encoding="utf-8"))[
                    "account"
                ]["name"]
                == "LFELKEEA"
                and json.loads((install / "config.json").read_text(encoding="utf-8"))[
                    "update_url"
                ]
                == "http://keep.me/update.json",
            )
            check(
                "preserved_social",
                json.loads((install / "social.json").read_text(encoding="utf-8"))[
                    "friends"
                ]
                == ["Mimi", "Neko"],
            )
            check(
                "preserved_game",
                (
                    install
                    / "minecraft"
                    / "versions"
                    / "1.21.1"
                    / "mods"
                    / "sodium.jar"
                ).read_bytes()
                == b"MODDATA"
                and (install / "skins" / "custom.png").read_bytes() == b"SKIN"
                and (
                    install / "servers" / "paper" / "server.properties"
                ).read_text(encoding="utf-8")
                == "motd=DKL\n",
            )
            check(
                "backup_created",
                (backup / "app.py").read_text(encoding="utf-8") == "OLD APP\n"
                and (backup / "darkitten" / "old_module.py").is_file(),
            )
            check(
                "pycache_ignored",
                not (install / "darkitten" / "__pycache__" / "junk.pyc").exists(),
            )

            bad_manifest = dict(manifest)
            bad_manifest["sha256"] = "0" * 64
            (web / "bad.json").write_text(
                json.dumps(bad_manifest, ensure_ascii=False), encoding="utf-8"
            )
            bad_release = check_for_update(
                "1.9.11", base + "bad.json"
            )
            try:
                download_update(bad_release, root / "bad-download")
            except UpdateError:
                bad_rejected = True
            else:
                bad_rejected = False
            check("sha_rejected", bad_rejected)

            missing_sha = dict(manifest)
            missing_sha.pop("sha256")
            (web / "missing.json").write_text(
                json.dumps(missing_sha, ensure_ascii=False), encoding="utf-8"
            )
            missing_release = check_for_update(
                "1.9.11", base + "missing.json"
            )
            try:
                download_update(missing_release, root / "missing-download")
            except UpdateError:
                missing_rejected = True
            else:
                missing_rejected = False
            check("missing_sha_rejected", missing_rejected)

            slip_zip = root / "slip.zip"
            with zipfile.ZipFile(slip_zip, "w") as zf:
                zf.writestr("../evil.txt", "bad")
                zf.writestr("DKL/app.py", "bad")
                zf.writestr("DKL/darkitten/config.py", "bad")
            try:
                prepare_update(slip_zip, root / "slip-out", "1.9.12")
            except UpdateError:
                slip_rejected = True
            else:
                slip_rejected = False
            check("zip_slip_rejected", slip_rejected)
            check(
                "zip_slip_no_file",
                not (root / "evil.txt").exists()
                and not (root / "slip-out" / "evil.txt").exists(),
            )

            script = write_apply_script(root / "script-out")
            check(
                "apply_script",
                script.is_file()
                and "apply_update_code" in script.read_text(encoding="utf-8"),
            )
        finally:
            server.shutdown()
            server.server_close()

    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
