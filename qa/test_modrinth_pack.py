from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import threading
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from stellar.modrinth_pack import (  # noqa: E402
    ModrinthPackError,
    import_modrinth_pack,
    inspect_modrinth_pack,
    looks_like_modrinth_pack,
)

checks: list[tuple[str, bool, str]] = []


def check(name: str, value, detail: str = "") -> bool:
    checks.append((name, bool(value), str(detail)))
    return bool(value)


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def make_pack(
    root: Path, *, deps: dict, files: list[dict], overrides: dict, tag: str = "test"
) -> Path:
    manifest = {
        "game": "minecraft",
        "formatVersion": 1,
        "versionId": "1.0.0",
        "name": "Test MR Pack",
        "summary": "A tiny test pack",
        "dependencies": deps,
        "files": files,
    }
    path = root / f"{tag}.mrpack"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "modrinth.index.json",
            json.dumps(manifest, ensure_ascii=False),
        )
        for name, data in overrides.items():
            zf.writestr(name, data)
    return path


def file_entry(path: str, data: bytes, url: str, client: str = "required") -> dict:
    entry = {
        "path": path,
        "hashes": {"sha1": sha1(data), "sha512": "x"},
        "downloads": [url],
        "fileSize": len(data),
    }
    if client != "required":
        entry["env"] = {"client": client, "server": "optional"}
    return entry


def fake_download(payloads: dict[str, bytes]):
    calls: list[str] = []

    def download(
        url,
        dest,
        *,
        sha1="",
        size=0,
        cancel_event=None,
        progress_cb=None,
        connections=4,
        probe=True,
    ):
        calls.append(url)
        data = payloads[url]
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(data)
        if progress_cb:
            progress_cb(len(data), len(data))
        return len(data)

    return download, calls


def fake_loader(calls: list[tuple]):
    def install(game_dir, mc, kind, version, **kwargs):
        calls.append((str(mc), kind, str(version)))
        loader_id = f"{kind}-loader-{version}-{mc}"
        path = Path(game_dir) / "versions" / loader_id / f"{loader_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"id": loader_id, "inheritsFrom": mc, "libraries": []}),
            encoding="utf-8",
        )
        return loader_id

    return install


class FakeInstaller:
    def __init__(self, calls: list[str]):
        self.calls = calls

    def install(self, version_id, **kwargs):
        self.calls.append(version_id)


def main() -> int:
    temp = Path(tempfile.mkdtemp(prefix="dkl_mrpack_"))
    try:
        root = temp / "pack"
        root.mkdir()
        mod = b"fake mod jar"
        optional = b"client mod"
        unsupported = b"server only"
        progress: list[tuple[int, str]] = []
        pack = make_pack(
            root,
            deps={"minecraft": "1.21.11", "fabric-loader": "0.19.5"},
            files=[
                file_entry("mods/cat.jar", mod, "https://example.test/cat.jar"),
                file_entry(
                    "mods/optional.jar",
                    optional,
                    "https://example.test/optional.jar",
                    client="optional",
                ),
                file_entry(
                    "mods/server-only.jar",
                    unsupported,
                    "https://example.test/server-only.jar",
                    client="unsupported",
                ),
            ],
            overrides={
                "overrides/config/app.json": "config",
                "overrides/options.txt": "options",
                "client-overrides/options.txt": "client-options",
                "client-overrides/config/client-only.json": "client-config",
            },
        )

        ok = True
        ok &= check("looks_like", looks_like_modrinth_pack(pack))
        info = inspect_modrinth_pack(pack)
        ok &= check("name", info.name == "Test MR Pack", info.name)
        ok &= check("mc", info.minecraft_version == "1.21.11", info.minecraft_version)
        ok &= check("loader", info.loader == "Fabric", info.loader)
        ok &= check("loader_version", info.loader_version == "0.19.5", info.loader_version)
        ok &= check("file_count", len(info.files) == 2, str([f.path for f in info.files]))
        ok &= check("skip_unsupported", all("server-only" not in f.path for f in info.files))
        options_entry = [o for o in info.overrides if o.relative == "options.txt"][0]
        ok &= check("options_identical", options_entry.size == len(b"options"))
        ok &= check("mods_count", info.counts["mods"] == 2, info.counts["mods"])
        ok &= check("config_count", info.counts["config"] == 2, info.counts["config"])

        game_dir = temp / "game"
        loader_calls: list[tuple] = []
        payloads = {
            "https://example.test/cat.jar": mod,
            "https://example.test/optional.jar": optional,
        }
        direct_urls = lambda item: item.urls  # noqa: E731
        download, calls = fake_download(payloads)
        result = import_modrinth_pack(
            pack,
            game_dir,
            "MRCat",
            isolated=True,
            progress_cb=lambda p, m: progress.append((p, m)),
            loader_installer=fake_loader(loader_calls),
            download_func=download,
            url_resolver=direct_urls,
        )
        run_dir = game_dir / "versions" / "MRCat"
        ok &= check("result_version", result.version_id == "MRCat")
        ok &= check("downloaded", result.downloaded_files == 2, result.downloaded_files)
        ok &= check("imported", result.imported_files == 6, result.imported_files)
        ok &= check("loader_called", loader_calls == [("1.21.11", "fabric", "0.19.5")], loader_calls)
        ok &= check("mod_file", (run_dir / "mods" / "cat.jar").read_bytes() == mod)
        ok &= check(
            "optional_mod",
            (run_dir / "mods" / "optional.jar").read_bytes() == optional,
        )
        ok &= check("unsupported_absent", not (run_dir / "mods" / "server-only.jar").exists())
        ok &= check(
            "override_config",
            (run_dir / "config" / "app.json").read_text() == "config",
        )
        ok &= check(
            "client_override_wins",
            (run_dir / "options.txt").read_text() == "client-options",
        )
        ok &= check("download_calls", len(calls) == 2, calls)
        wrapper = json.loads((run_dir / "MRCat.json").read_text())
        ok &= check(
            "wrapper_inherits",
            wrapper.get("inheritsFrom") == "fabric-loader-0.19.5-1.21.11",
            wrapper,
        )
        ok &= check("progress_end", progress[-1][0] == 100, progress[-1])

        # resume / overwrite
        download2, calls2 = fake_download(payloads)
        result2 = import_modrinth_pack(
            pack,
            game_dir,
            "MRCat",
            resume=True,
            overwrite=True,
            isolated=True,
            loader_installer=fake_loader([]),
            download_func=download2,
            url_resolver=direct_urls,
        )
        ok &= check("resume_downloaded", result2.downloaded_files == 0)
        ok &= check("resume_skipped", result2.skipped_files == 2, result2.skipped_files)
        ok &= check(
            "overwrite_config",
            (run_dir / "options.txt").read_text() == "client-options",
        )
        # no loader: vanilla install path
        vanilla = make_pack(
            root,
            tag="vanilla",
            deps={"minecraft": "1.21.11"},
            files=[file_entry("mods/v.jar", b"v", "https://example.test/v.jar")],
            overrides={},
        )
        vanilla_calls: list[str] = []
        result3 = import_modrinth_pack(
            vanilla,
            game_dir,
            "VanillaCat",
            isolated=True,
            installer=FakeInstaller(vanilla_calls),
            download_func=fake_download({"https://example.test/v.jar": b"v"})[0],
            url_resolver=direct_urls,
        )
        ok &= check("vanilla_install", vanilla_calls == ["1.21.11"], vanilla_calls)
        ok &= check("vanilla_version", result3.version_id == "VanillaCat")
        wrapper3 = json.loads(
            (game_dir / "versions" / "VanillaCat" / "VanillaCat.json").read_text()
        )
        ok &= check("vanilla_inherits", wrapper3.get("inheritsFrom") == "1.21.11")

        # unsafe path
        bad = make_pack(
            root,
            tag="bad",
            deps={"minecraft": "1.21.11"},
            files=[file_entry("../evil.jar", b"x", "https://example.test/evil.jar")],
            overrides={},
        )
        try:
            inspect_modrinth_pack(bad)
        except ModrinthPackError as exc:
            ok &= check("traversal_rejected", "越界" in str(exc), str(exc))
        else:
            ok &= check("traversal_rejected", False)

        # fake sha1
        bad2 = make_pack(
            root,
            tag="bad2",
            deps={"minecraft": "1.21.11"},
            files=[
                {
                    "path": "mods/nosha.jar",
                    "hashes": {},
                    "downloads": ["https://example.test/nosha.jar"],
                    "fileSize": 1,
                }
            ],
            overrides={},
        )
        try:
            inspect_modrinth_pack(bad2)
        except ModrinthPackError as exc:
            ok &= check("sha_rejected", "SHA1" in str(exc), str(exc))
        else:
            ok &= check("sha_rejected", False)

        # quilt unsupported
        quilt = make_pack(
            root,
            tag="quilt",
            deps={"minecraft": "1.21.11", "quilt-loader": "0.20.0"},
            files=[file_entry("mods/q.jar", b"q", "https://example.test/q.jar")],
            overrides={},
        )
        try:
            inspect_modrinth_pack(quilt)
        except ModrinthPackError as exc:
            ok &= check("quilt_rejected", "Quilt" in str(exc), str(exc))
        else:
            ok &= check("quilt_rejected", False)

        # cancel
        cancel_pack = make_pack(
            root,
            tag="cancel",
            deps={"minecraft": "1.21.11", "fabric-loader": "0.19.5"},
            files=[file_entry("mods/c.jar", b"c", "https://example.test/c.jar")],
            overrides={},
        )
        cancel = threading.Event()
        cancel.set()
        try:
            import_modrinth_pack(
                cancel_pack,
                game_dir,
                "CancelCat",
                isolated=True,
                cancel_event=cancel,
                loader_installer=fake_loader([]),
                download_func=fake_download(payloads)[0],
                url_resolver=direct_urls,
            )
        except ModrinthPackError as exc:
            ok &= check("cancel", "取消" in str(exc), str(exc))
        else:
            ok &= check("cancel", False)

        for name, passed, detail in checks:
            print(f"{name}: {'OK' if passed else 'FAIL'}" + (f" ({detail})" if detail and not passed else ""))
        print("RESULT:", "PASS" if all(item[1] for item in checks) else "FAIL")
        return 0 if all(item[1] for item in checks) else 1
    finally:
        shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
