from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import urllib.parse
import zipfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from PySide6.QtWidgets import QApplication, QWidget

from stellar import curseforge as cf
from stellar import modrinth as mr
from stellar import workers as wk
from stellar.config import Config
from stellar.curseforge_pack import (
    CurseForgePackError,
    import_curseforge_pack,
    inspect_curseforge_pack,
    looks_like_curseforge_pack,
)
from stellar.versions import VersionSummary
import stellar.ui.mod_dialog as mod_dialog
from stellar.ui.mod_dialog import ModBrowserDialog


def check(label: str, value: bool, detail: str = "") -> bool:
    print(f"{label}: {'OK' if value else 'FAIL'}" + (f" ({detail})" if detail else ""))
    return bool(value)


def modrinth_tests() -> bool:
    ok = True
    captured: list[str] = []

    def fake_get_json(url, timeout=30):
        captured.append(url)
        return {"hits": [], "total_hits": 0}

    original = mr._get_json
    try:
        mr._get_json = fake_get_json
        cases = [
            ("shader", "fabric", "1.20.1", [["project_type:shader"], ["versions:1.20.1"]]),
            ("resourcepack", "fabric", "", [["project_type:resourcepack"]]),
            (
                "modpack",
                "forge",
                "1.21.1",
                [["project_type:modpack"], ["versions:1.21.1"]],
            ),
            ("mod", "fabric", "", [["project_type:mod"], ["categories:fabric"]]),
        ]
        for kind, loader, version, expected in cases:
            mr.search_mods("x", loader=loader, game_version=version, project_type=kind)
            params = urllib.parse.parse_qs(
                urllib.parse.urlparse(captured[-1]).query
            )
            facets = json.loads(params["facets"][0])
            ok &= check(
                f"modrinth_{kind}_facets",
                facets == expected,
                str(facets),
            )

        def fake_versions(url, timeout=30):
            captured.append(url)
            return [
                {
                    "id": "v1",
                    "files": [
                        {
                            "primary": True,
                            "url": "https://cdn.modrinth.com/a.zip",
                            "filename": "a.zip",
                        }
                    ],
                }
            ]

        mr._get_json = fake_versions
        versions = mr.fetch_mod_versions(
            "abc", "fabric", "1.20.1", content_type="shader"
        )
        params = urllib.parse.parse_qs(
            urllib.parse.urlparse(captured[-1]).query
        )
        ok &= check(
            "modrinth_shader_versions",
            len(versions) == 1
            and "loaders" not in params
            and json.loads(params["game_versions"][0]) == ["1.20.1"],
            str(params),
        )
    finally:
        mr._get_json = original
    return ok


def curseforge_tests() -> bool:
    ok = True
    captured: list[tuple[str, dict]] = []

    def fake_json(path, params=None, timeout=35):
        captured.append((path, dict(params or {})))
        return {"data": [], "pagination": {"totalCount": 0}}

    original = cf._request_json
    try:
        cf._request_json = fake_json
        for kind, expected in (
            ("mod", 6),
            ("modpack", 4471),
            ("resourcepack", 12),
            ("shader", 6552),
        ):
            cf.search_mods("", loader="fabric", content_type=kind)
            params = captured[-1][1]
            ok &= check(
                f"curseforge_{kind}_class",
                params.get("classId") == expected
                and ("modLoaderType" in params) == (kind == "mod"),
                str(params),
            )

        cf.fetch_mod_versions("123", "fabric", "1.20.1", content_type="resourcepack")
        params = captured[-1][1]
        ok &= check(
            "curseforge_resourcepack_versions",
            "modLoaderType" not in params and params.get("gameVersion") == "1.20.1",
            str(params),
        )
    finally:
        cf._request_json = original

    shader = cf.normalize_file(
        {
            "id": 5,
            "displayName": "Demo Shader",
            "fileName": "demo.zip",
            "fileLength": 10,
            "downloadUrl": "https://edge.forgecdn.net/files/0/5/demo.zip",
            "gameVersions": ["1.20.1"],
            "hashes": [],
        },
        project_slug="demo-shader",
        content_type="shader",
    )
    ok &= check(
        "curseforge_shader_page_url",
        "/shaders/demo-shader/files/5" in shader["page_url"],
        shader["page_url"],
    )

    hit = cf.normalize_hit(
        {
            "id": 1,
            "name": "Demo Pack",
            "slug": "demo-pack",
            "classId": 4471,
            "latestFilesIndexes": [],
        }
    )
    ok &= check(
        "curseforge_pack_url",
        hit["project_url"].endswith("/modpacks/demo-pack")
        and hit["content_type"] == "modpack",
        hit["project_url"],
    )
    return ok


def make_pack(path: Path) -> None:
    manifest = {
        "manifestType": "minecraftModpack",
        "manifestVersion": 1,
        "name": "猫猫测试整合包",
        "version": "1.0.0",
        "author": "LFELKEEA",
        "minecraft": {
            "version": "1.21.1",
            "modLoaders": [{"id": "fabric-0.16.9", "primary": True}],
        },
        "files": [{"projectID": 111, "fileID": 222, "required": True}],
        "overrides": "overrides",
    }
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("overrides/config/demo.toml", "enabled = true\n")
        zf.writestr("overrides/resourcepacks/extra.zip", b"pk")
        zf.writestr("overrides/shaderpacks/demo.zip", b"shader")


def pack_tests() -> bool:
    ok = True
    root = Path(tempfile.mkdtemp(prefix="cf_pack_"))
    pack = root / "demo.zip"
    make_pack(pack)

    ok &= check("looks_like_pack", looks_like_curseforge_pack(pack))
    info = inspect_curseforge_pack(pack)
    ok &= check(
        "inspect_pack",
        info.name == "猫猫测试整合包"
        and info.minecraft_version == "1.21.1"
        and info.loader_key == "fabric"
        and info.loader == "Fabric"
        and info.loader_version == "0.16.9"
        and len(info.files) == 1
        and len(info.overrides) == 3
        and info.counts["mods"] == 1
        and info.counts["config"] == 1
        and info.counts["resourcepacks"] == 1
        and info.counts["shaderpacks"] == 1
        and info.total_files == 4,
        f"files={len(info.overrides)} counts={info.counts}",
    )

    bad = root / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr(
            "manifest.json",
            json.dumps({"manifestType": "somethingElse", "manifestVersion": 1}),
        )
    ok &= check("not_curseforge_pack", not looks_like_curseforge_pack(bad))
    try:
        inspect_curseforge_pack(bad)
    except CurseForgePackError:
        ok &= check("inspect_rejects_bad_manifest", True)
    else:
        ok &= check("inspect_rejects_bad_manifest", False)

    game_dir = root / "game"
    calls: list[tuple] = []
    downloads: list[str] = []
    progress: list[tuple[int, str]] = []
    logs: list[str] = []

    def fake_loader(game, mc, kind, version, **kwargs):
        calls.append(("loader", str(game), mc, kind, version))
        return "fabric-loader-0.16.9-1.21.1"

    def fake_fetch(project_id, file_id, content_type="mod"):
        calls.append(("fetch", project_id, file_id, content_type))
        return {
            "files": [
                {
                    "primary": True,
                    "url": "https://edge.forgecdn.net/files/111/222/coolmod.jar",
                    "filename": "coolmod.jar",
                    "size": 4,
                    "hashes": {"sha1": "abc"},
                }
            ]
        }

    def fake_download(url, dest, **kwargs):
        downloads.append(url)
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"data")
        return 4

    try:
        result = import_curseforge_pack(
            pack,
            game_dir,
            "DemoPack",
            loader_installer=fake_loader,
            file_fetcher=fake_fetch,
            download_func=fake_download,
            progress_cb=lambda p, m: progress.append((p, m)),
            log_cb=lambda m: logs.append(m),
        )
        version_json = game_dir / "versions" / "DemoPack" / "DemoPack.json"
        run_dir = game_dir / "versions" / "DemoPack"
        ok &= check(
            "import_pack",
            result.version_id == "DemoPack"
            and result.downloaded_files == 1
            and result.imported_files == 4
            and version_json.is_file()
            and json.loads(version_json.read_text(encoding="utf-8"))["inheritsFrom"]
            == "fabric-loader-0.16.9-1.21.1"
            and (run_dir / "mods" / "coolmod.jar").read_bytes() == b"data"
            and (run_dir / "config" / "demo.toml").is_file()
            and (run_dir / "resourcepacks" / "extra.zip").is_file()
            and (run_dir / "shaderpacks" / "demo.zip").is_file()
            and progress[-1][0] == 100
            and any("CurseForge 整合包" in line for line in logs),
            f"result={result} progress={progress[-1] if progress else None}",
        )
        ok &= check(
            "import_loader_args",
            ("loader", str(game_dir), "1.21.1", "fabric", "0.16.9") in calls,
            str(calls),
        )
        ok &= check(
            "import_fetch_args",
            ("fetch", "111", "222", "mod") in calls,
            str(calls),
        )
        ok &= check(
            "import_cdn_url",
            downloads and downloads[0].startswith("https://mod.mcimirror.top/"),
            str(downloads),
        )

        try:
            import_curseforge_pack(
                pack,
                game_dir,
                "DemoPack",
                loader_installer=fake_loader,
                file_fetcher=fake_fetch,
                download_func=fake_download,
            )
        except CurseForgePackError as exc:
            ok &= check("import_rejects_existing", "已经存在" in str(exc))
        else:
            ok &= check("import_rejects_existing", False)

        blocked = root / "blocked.zip"
        with zipfile.ZipFile(blocked, "w") as zf:
            zf.writestr(
                "manifest.json",
                json.dumps(
                    {
                        "manifestType": "minecraftModpack",
                        "manifestVersion": 1,
                        "name": "Blocked",
                        "version": "1",
                        "minecraft": {
                            "version": "1.21.1",
                            "modLoaders": [
                                {"id": "fabric-0.16.9", "primary": True}
                            ],
                        },
                        "files": [{"projectID": 1, "fileID": 2, "required": True}],
                    }
                ),
            )
        try:
            import_curseforge_pack(
                blocked,
                root / "game2",
                "Blocked",
                loader_installer=fake_loader,
                file_fetcher=lambda *a, **k: {
                    "files": [
                        {
                            "primary": True,
                            "url": "",
                            "filename": "blocked.jar",
                            "size": 1,
                            "hashes": {},
                        }
                    ]
                },
                download_func=fake_download,
            )
        except CurseForgePackError as exc:
            ok &= check("import_blocked_file", "第三方下载" in str(exc))
        else:
            ok &= check("import_blocked_file", False)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return ok


def worker_tests() -> bool:
    ok = True
    QApplication.instance() or QApplication(sys.argv)
    captured: list[tuple[str, dict]] = []
    original = (
        wk.search_mods,
        wk.search_curseforge_mods,
        wk.fetch_mod_versions,
        wk.fetch_curseforge_mod_versions,
    )

    def fake_mr_search(query, **kwargs):
        captured.append(("mr_search", dict(kwargs)))
        return {"hits": [], "total_hits": 0}

    def fake_cf_search(query, **kwargs):
        captured.append(("cf_search", dict(kwargs)))
        return {"hits": [], "total_hits": 0}

    def fake_mr_versions(project_id, loader="", game_version="", **kwargs):
        captured.append(("mr_versions", dict(kwargs)))
        return []

    def fake_cf_versions(project_id, loader="", game_version="", **kwargs):
        captured.append(("cf_versions", dict(kwargs)))
        return []

    try:
        wk.search_mods = fake_mr_search
        wk.search_curseforge_mods = fake_cf_search
        wk.fetch_mod_versions = fake_mr_versions
        wk.fetch_curseforge_mod_versions = fake_cf_versions

        results: list[tuple] = []
        worker = wk.ModSearchWorker(
            "q", "fabric", "1.21.1", source="modrinth", content_type="shader"
        )
        worker.ready.connect(lambda data, error, offset: results.append((data, error)))
        worker._run()
        ok &= check(
            "worker_modrinth_type",
            captured[-1][0] == "mr_search"
            and captured[-1][1].get("project_type") == "shader"
            and results == [({"hits": [], "total_hits": 0}, "")],
            str(captured[-1]),
        )

        worker = wk.ModSearchWorker(
            "q", "fabric", "1.21.1", source="curseforge", content_type="modpack"
        )
        worker._run()
        ok &= check(
            "worker_curseforge_type",
            captured[-1][0] == "cf_search"
            and captured[-1][1].get("content_type") == "modpack",
            str(captured[-1]),
        )

        worker = wk.ModVersionsWorker(
            "abc", "fabric", "1.21.1", source="modrinth", content_type="resourcepack"
        )
        worker._run()
        ok &= check(
            "worker_version_type",
            captured[-1][0] == "mr_versions"
            and captured[-1][1].get("content_type") == "resourcepack",
            str(captured[-1]),
        )
    finally:
        (
            wk.search_mods,
            wk.search_curseforge_mods,
            wk.fetch_mod_versions,
            wk.fetch_curseforge_mod_versions,
        ) = original
    return ok


class FakeMain(QWidget):
    def __init__(self, config: Config, versions: list[VersionSummary]) -> None:
        super().__init__()
        self.config = config
        self.versions = versions
        self.selected_version = versions[0].id if versions else ""
        self.messages: list[tuple[str, str]] = []

    def toast(self, text: str, kind: str = "info") -> None:
        self.messages.append((text, kind))

    def open_path(self, _path) -> None:
        pass

    def log(self, _message: str) -> None:
        pass

    def game_version_summaries(self):
        return list(self.versions)

    def loader_profile_for(self, _version_id):
        return None

    def refresh_versions(self) -> None:
        pass

    def select_version(self, _version_id: str) -> None:
        pass


def ui_tests() -> bool:
    ok = True
    app = QApplication.instance() or QApplication(sys.argv)
    mod_dialog.ModSearchWorker.start = lambda self: None
    mod_dialog.ModProjectWorker.start = lambda self: None
    mod_dialog.ModVersionsWorker.start = lambda self: None
    mod_dialog.ModProjectsWorker.start = lambda self: None

    root = Path(tempfile.mkdtemp(prefix="ct_ui_"))
    for version_id, data in (
        ("1.21.1", {"id": "1.21.1"}),
        ("1.21.1-Fabric", {"id": "1.21.1-Fabric", "inheritsFrom": "1.21.1"}),
    ):
        folder = root / "versions" / version_id
        folder.mkdir(parents=True)
        (folder / f"{version_id}.json").write_text(
            json.dumps(data), encoding="utf-8"
        )

    versions = [
        VersionSummary(
            id="1.21.1-Fabric",
            loader="Fabric",
            parent_id="1.21.1",
            base_id="1.21.1",
        )
    ]
    window = FakeMain(Config(game_dir=str(root)), versions)
    window.config.version_isolation = True
    dialog = ModBrowserDialog(window)
    app.processEvents()
    dialog._polling = False
    dialog.search_btn.setEnabled(True)

    ok &= check(
        "default_content_type",
        dialog._content_type == "mod"
        and dialog.type_tabs.current() == "mod"
        and dialog.title_label.text() == "下载 Mod",
        f"{dialog._content_type} {dialog.title_label.text()}",
    )

    dialog._set_content_type("resourcepack")
    app.processEvents()
    worker = dialog._search_worker
    ok &= check(
        "resourcepack_mode",
        dialog._content_type == "resourcepack"
        and dialog.type_tabs.current() == "resourcepack"
        and dialog.title_label.text() == "下载 材质"
        and dialog.loader_tabs.isHidden()
        and dialog.download_btn.text() == "下载到 resourcepacks"
        and dialog._folder_btn.text() == "打开 resourcepacks 文件夹"
        and worker is not None
        and worker.content_type == "resourcepack"
        and dialog._target_version_id == "1.21.1-Fabric"
        and dialog._target_dir()
        == root / "versions" / "1.21.1-Fabric" / "resourcepacks",
        f"target={dialog._target_version_id} dir={dialog._target_dir()}",
    )

    dialog._polling = False
    dialog.search_btn.setEnabled(True)
    dialog._set_content_type("shader")
    app.processEvents()
    ok &= check(
        "shader_mode",
        dialog.title_label.text() == "下载 光影"
        and dialog.download_btn.text() == "下载到 shaderpacks"
        and dialog._target_dir()
        == root / "versions" / "1.21.1-Fabric" / "shaderpacks",
        dialog.download_btn.text(),
    )

    dialog._polling = False
    dialog.search_btn.setEnabled(True)
    dialog._set_content_type("modpack")
    app.processEvents()
    dialog.version_combo.clear()
    dialog.version_combo.addItem(
        "Demo Pack",
        {
            "source": "modrinth",
            "files": [
                {
                    "primary": True,
                    "url": "https://cdn.modrinth.com/demo.mrpack",
                    "filename": "demo.mrpack",
                }
            ],
        },
    )
    dialog._sync_download_button()
    ok &= check(
        "modpack_mode",
        dialog.title_label.text() == "下载 整合包"
        and dialog.download_btn.text() == "下载并导入整合包"
        and dialog._folder_btn.text() == "打开整合包缓存"
        and not dialog.loader_tabs.isVisible()
        and dialog._target_version_id == ""
        and dialog.download_btn.isEnabled(),
        f"button={dialog.download_btn.text()} enabled={dialog.download_btn.isEnabled()}",
    )

    dialog._polling = False
    dialog.search_btn.setEnabled(True)
    dialog._set_content_type("mod")
    app.processEvents()
    ok &= check(
        "back_to_mod",
        dialog.title_label.text() == "下载 Mod"
        and not dialog.loader_tabs.isHidden()
        and dialog._download_button_text() == "下载到 mods",
        dialog.title_label.text(),
    )

    dialog._polling = False
    dialog.search_btn.setEnabled(True)
    dialog._set_source("curseforge")
    app.processEvents()
    dialog._polling = False
    dialog.search_btn.setEnabled(True)
    dialog._set_content_type("shader")
    app.processEvents()
    worker = dialog._search_worker
    ok &= check(
        "curseforge_shader_search",
        worker is not None
        and worker.source == "curseforge"
        and worker.content_type == "shader",
        f"source={getattr(worker, 'source', '')} type={getattr(worker, 'content_type', '')}",
    )

    from stellar.ui.pack_dialog import PackImportDialog

    pack = root / "demo.zip"
    make_pack(pack)
    pack_dialog = PackImportDialog(window, initial_path=pack)
    app.processEvents()
    ok &= check(
        "pack_dialog_curseforge",
        pack_dialog._kind == "curseforge"
        and pack_dialog.preview_name.text() == "猫猫测试整合包"
        and "CurseForge" in pack_dialog.preview_meta.text()
        and "Minecraft 1.21.1" in pack_dialog.preview_mc.text()
        and "Fabric 0.16.9" in pack_dialog.preview_loader.text()
        and pack_dialog.import_btn.isEnabled()
        and bool(pack_dialog.name_edit.text()),
        f"kind={pack_dialog._kind} meta={pack_dialog.preview_meta.text()}",
    )
    pack_dialog.deleteLater()

    dialog.close()
    window.close()
    app.processEvents()
    shutil.rmtree(root, ignore_errors=True)
    return ok


def main() -> int:
    ok = modrinth_tests()
    ok &= curseforge_tests()
    ok &= pack_tests()
    ok &= worker_tests()
    ok &= ui_tests()
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
