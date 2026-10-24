from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from PySide6.QtWidgets import QApplication, QWidget

from darkitten import curseforge as cf
from darkitten.config import Config
from darkitten.downloader import DownloadCancelled
import darkitten.ui.mod_dialog as mod_dialog
from darkitten.ui.mod_dialog import ModBrowserDialog


def check(label: str, value: bool, detail: str = "") -> bool:
    print(f"{label}: {'OK' if value else 'FAIL'}" + (f" ({detail})" if detail else ""))
    return bool(value)


MOD = {
    "id": 238222,
    "name": "Just Enough Items",
    "slug": "jei",
    "summary": "View items and recipes",
    "description": "<p>Recipe viewer</p>",
    "downloadCount": 638976999,
    "favoriteCount": 12345,
    "thumbsUpCount": 999,
    "dateModified": "2026-10-23T14:03:53.520Z",
    "allowModDistribution": False,
    "authors": [{"name": "mezz"}],
    "categories": [{"name": "Utility"}, {"name": "Map and Information"}],
    "logo": {
        "thumbnailUrl": "https://media.forgecdn.net/avatars/thumbnails/29/69/256/256/a.png",
        "url": "https://media.forgecdn.net/avatars/29/69/a.png",
    },
    "links": {
        "websiteUrl": "https://www.curseforge.com/minecraft/mc-mods/jei",
        "sourceUrl": "https://github.com/mezz/JustEnoughItems",
        "issuesUrl": "https://github.com/mezz/JustEnoughItems/issues",
        "wikiUrl": "https://github.com/mezz/JustEnoughItems/wiki",
    },
    "screenshots": [
        {
            "title": "Recipe",
            "description": "lookup",
            "thumbnailUrl": "https://media.forgecdn.net/attachments/thumb.png",
            "url": "https://media.forgecdn.net/attachments/full.png",
        }
    ],
    "latestFilesIndexes": [
        {"gameVersion": "1.21.1", "modLoader": 4, "fileId": 1},
        {"gameVersion": "1.21.1", "modLoader": 1, "fileId": 2},
        {"gameVersion": "1.20.1", "modLoader": 4, "fileId": 3},
    ],
    "latestFiles": [
        {
            "gameVersions": ["1.21.1", "Fabric", "Client"],
            "downloadUrl": "https://edge.forgecdn.net/files/1/2/a.jar",
        }
    ],
}

FILE = {
    "id": 9298210,
    "displayName": "15.74.0.229 for Fabric 1.20.1",
    "fileName": "jei-1.20.1-fabric-15.74.0.229.jar",
    "releaseType": 2,
    "fileLength": 6928789,
    "downloadUrl": "https://edge.forgecdn.net/files/9298/210/jei.jar",
    "hashes": [
        {"value": "21081fe00c041c45ed1ac3aa1b956b1952946362", "algo": 1},
        {"value": "a1c14973b41a36c35f9eabeea5de88e7", "algo": 2},
    ],
    "gameVersions": ["1.20.1", "Fabric", "Client"],
    "dependencies": [
        {"modId": 419699, "relationType": 3},
        {"modId": 306612, "relationType": 2},
        {"modId": 0, "relationType": 3},
    ],
}


class FakeMain(QWidget):
    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config
        self.versions: list = []
        self.selected_version = ""
        self.messages: list[tuple[str, str]] = []

    def toast(self, text: str, kind: str = "info") -> None:
        self.messages.append((text, kind))

    def open_path(self, _path) -> None:
        pass

    def game_version_summaries(self):
        return list(self.versions)

    def loader_profile_for(self, _version_id):
        return None


def unit_tests() -> bool:
    ok = True

    hit = cf.normalize_hit(MOD)
    ok &= check(
        "hit_fields",
        hit["project_id"] == "238222"
        and hit["title"] == "Just Enough Items"
        and hit["author"] == "mezz"
        and hit["downloads"] == 638976999
        and hit["source"] == "curseforge"
        and hit["icon_url"].endswith("/256/a.png"),
        str(hit),
    )

    project = cf.normalize_project(MOD)
    ok &= check(
        "project_fields",
        project["body"].startswith("<p>")
        and project["followers"] == 12345
        and project["loaders"] == ["Fabric", "Forge"]
        and project["game_versions"] == ["1.21.1", "1.20.1"]
        and project["gallery"][0]["full_url"].endswith("full.png")
        and project["source_url"].startswith("https://github.com/")
        and project["project_url"].endswith("/mc-mods/jei"),
        str(project["loaders"]) + " " + str(project["game_versions"]),
    )

    version = cf.normalize_file(FILE)
    file = version["files"][0]
    ok &= check(
        "file_fields",
        version["id"] == "9298210"
        and version["version_type"] == "beta"
        and version["source"] == "curseforge"
        and file["filename"].endswith(".jar")
        and file["size"] == 6928789
        and file["hashes"]["sha1"].startswith("21081fe")
        and version["loaders"] == ["fabric"]
        and version["game_versions"] == ["1.20.1"]
        and version["dependencies"]
        == [{"project_id": "419699", "version_id": "", "dependency_type": "required"}],
        str(version),
    )

    no_url = dict(FILE, downloadUrl=None)
    no_url_version = cf.normalize_file(no_url)
    ok &= check(
        "file_without_url",
        no_url_version["files"][0]["url"] == "",
        str(no_url_version["files"]),
    )

    captured: list[tuple[str, dict]] = []

    def fake_json(path, params=None, timeout=35):
        captured.append((path, dict(params or {})))
        return {
            "data": [MOD],
            "pagination": {"index": 0, "pageSize": 5, "totalCount": 42},
        }

    original_json = cf._request_json
    try:
        cf._request_json = fake_json
        result = cf.search_mods(
            "jei", loader="fabric", game_version="1.21.1", limit=5, offset=10
        )
        params = captured[-1][1]
        ok &= check(
            "search_query_params",
            params.get("sortField") == 11
            and "sortOrder" not in params
            and params.get("modLoaderType") == 4
            and params.get("gameVersion") == "1.21.1"
            and params.get("index") == 10
            and params.get("searchFilter") == "jei"
            and result["total_hits"] == 42
            and result["hits"][0]["source"] == "curseforge",
            str(params),
        )
        cf.search_mods("", loader="forge", game_version="1.20.1", limit=5)
        params = captured[-1][1]
        ok &= check(
            "search_browse_params",
            params.get("sortField") == 2
            and params.get("sortOrder") == "desc"
            and params.get("modLoaderType") == 1
            and "searchFilter" not in params,
            str(params),
        )
    finally:
        cf._request_json = original_json

    # fetch_mod_versions 只保留匹配加载器/游戏版本的文件。
    captured.clear()

    def fake_files(path, params=None, timeout=35):
        captured.append((path, dict(params or {})))
        return {
            "data": [
                dict(FILE),
                dict(FILE, id=2, fileName="jei-forge.jar", gameVersions=["1.20.1", "Forge"]),
                dict(FILE, id=3, fileName="jei-1211.jar", gameVersions=["1.21.1", "Fabric"]),
            ]
        }

    try:
        cf._request_json = fake_files
        versions = cf.fetch_mod_versions("238222", "fabric", "1.20.1")
        ok &= check(
            "versions_filter",
            len(versions) == 1
            and versions[0]["files"][0]["filename"] == "jei-1.20.1-fabric-15.74.0.229.jar"
            and captured[-1][1].get("modLoaderType") == 4
            and captured[-1][1].get("gameVersion") == "1.20.1",
            str([v["files"][0]["filename"] for v in versions]),
        )
    finally:
        cf._request_json = original_json

    urls = cf.candidate_download_urls(FILE["downloadUrl"])
    ok &= check(
        "cdn_fallback_urls",
        urls[0].startswith("https://mod.mcimirror.top/files/9298/210/")
        and any("mediafilez.forgecdn.net" in url for url in urls)
        and urls[-1] == FILE["downloadUrl"],
        str(urls),
    )
    ok &= check(
        "non_cdn_url",
        cf.candidate_download_urls("https://example.com/a.jar")
        == ["https://example.com/a.jar"],
    )

    # 下载：第一个镜像失败后自动回退到第二个地址。
    calls: list[str] = []

    def fake_download(url, dest, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            raise OSError("mirror down")
        Path(dest).write_bytes(b"x" * 10)
        return 10

    original_download = cf.download_to_file
    root = Path(tempfile.mkdtemp(prefix="cf_unit_"))
    try:
        cf.download_to_file = fake_download
        version = {
            "source": "curseforge",
            "files": [
                {
                    "primary": True,
                    "url": FILE["downloadUrl"],
                    "filename": "demo.jar",
                    "size": 10,
                    "hashes": {"sha1": "abc"},
                }
            ],
        }
        path = cf.download_mod_file(version, root)
        ok &= check(
            "download_fallback",
            path.name == "demo.jar"
            and len(calls) == 2
            and calls[0].startswith("https://mod.mcimirror.top/")
            and calls[1].startswith("https://mediafilez.forgecdn.net/"),
            str(calls),
        )
        calls.clear()
        cf.download_mod_file(version, root)
        ok &= check("download_skip_existing", not calls, str(calls))

        no_url = {
            "files": [
                {
                    "primary": True,
                    "url": "",
                    "filename": "blocked.jar",
                    "size": 10,
                    "hashes": {},
                }
            ]
        }
        try:
            cf.download_mod_file(no_url, root)
        except cf.CurseForgeError as exc:
            ok &= check("download_blocked", "第三方下载" in str(exc))
        else:
            ok &= check("download_blocked", False)

        def cancel_download(url, dest, **kwargs):
            raise DownloadCancelled()

        cf.download_to_file = cancel_download
        try:
            cf.download_mod_file(version, root / "cancel")
        except DownloadCancelled:
            ok &= check("download_cancel", True)
        else:
            ok &= check("download_cancel", False)
    finally:
        cf.download_to_file = original_download
        import shutil

        shutil.rmtree(root, ignore_errors=True)
    return ok


def ui_tests() -> bool:
    ok = True
    app = QApplication.instance() or QApplication(sys.argv)
    mod_dialog.ModSearchWorker.start = lambda self: None
    mod_dialog.ModProjectWorker.start = lambda self: None
    mod_dialog.ModVersionsWorker.start = lambda self: None
    mod_dialog.ModProjectsWorker.start = lambda self: None

    root = Path(tempfile.mkdtemp(prefix="cf_ui_"))
    window = FakeMain(Config(game_dir=str(root)))
    window.config.version_isolation = False
    dialog = ModBrowserDialog(window)
    app.processEvents()
    dialog._polling = False
    dialog.search_btn.setEnabled(True)

    dialog._set_source("curseforge")
    app.processEvents()
    worker = dialog._search_worker
    ok &= check(
        "source_switch",
        dialog._source == "curseforge"
        and dialog.source_tabs.current() == "curseforge"
        and worker is not None
        and worker.source == "curseforge"
        and not dialog.source_note.isHidden(),
        f"source={dialog._source} worker={getattr(worker, 'source', '')}",
    )

    hit = cf.normalize_hit(MOD)
    dialog._search_ready(worker, {"hits": [hit], "total_hits": 1}, "", 0)
    app.processEvents()
    ok &= check(
        "cf_results",
        len(dialog._rows) == 1
        and dialog.detail_title.text() == "Just Enough Items"
        and dialog.followers_badge.text().endswith("收藏"),
        dialog.detail_title.text(),
    )

    dialog._select_project("238222", hit)
    app.processEvents()
    versions_worker = dialog._versions_worker
    version = cf.normalize_file(FILE)
    version["files"][0]["url"] = ""
    dialog._versions_ready(versions_worker, "238222", [version], "")
    app.processEvents()
    combo_text = dialog.version_combo.currentText()
    ok &= check(
        "cf_blocked_file",
        "仅网页下载" in combo_text and not dialog.download_btn.isEnabled(),
        combo_text,
    )

    downloadable = cf.normalize_file(FILE)
    dialog._dep_projects["419699"] = {
        "id": "419699",
        "slug": "architectury-api",
        "title": "Architectury API",
        "description": "library",
        "downloads": 1,
        "categories": ["Library"],
        "icon_url": "",
        "source": "curseforge",
        "source_label": "CurseForge",
    }
    dialog.dep_bar.update_projects(dialog._dep_projects)
    dialog._versions_ready(versions_worker, "238222", [downloadable], "")
    app.processEvents()
    dep_buttons = [
        button
        for button in dialog.dep_bar.findChildren(mod_dialog.QPushButton)
        if button.text().startswith("前置：")
    ]
    ok &= check(
        "cf_downloadable_and_deps",
        dialog.download_btn.isEnabled()
        and bool(dep_buttons)
        and "Architectury API" in dep_buttons[0].text(),
        f"enabled={dialog.download_btn.isEnabled()} deps={len(dep_buttons)}",
    )

    dialog._set_source("modrinth")
    app.processEvents()
    ok &= check(
        "source_switch_back",
        dialog._source == "modrinth"
        and dialog.source_tabs.current() == "modrinth"
        and dialog.source_note.isHidden()
        and not dialog._rows,
        f"source={dialog._source} rows={len(dialog._rows)}",
    )

    dialog.close()
    window.close()
    app.processEvents()
    import shutil

    shutil.rmtree(root, ignore_errors=True)
    return ok


def main() -> int:
    ok = unit_tests()
    ok &= ui_tests()
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
