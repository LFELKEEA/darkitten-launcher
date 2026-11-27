"""CurseForge 适配层：插件搜索、gzip 解码与缓存。"""

from __future__ import annotations

import gzip
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from darkitten import curseforge  # noqa: E402


class DecodeBodyTests(unittest.TestCase):
    def test_decodes_gzip(self) -> None:
        raw = '{"data": []}'.encode("utf-8")
        packed = gzip.compress(raw)
        self.assertEqual(curseforge._decode_body(packed, "gzip"), raw)

    def test_keeps_plain_body(self) -> None:
        raw = b'{"data": []}'
        self.assertEqual(curseforge._decode_body(raw, ""), raw)

    def test_falls_back_when_gzip_is_broken(self) -> None:
        raw = b"not-gzip"
        self.assertEqual(curseforge._decode_body(raw, "gzip"), raw)


class CacheTests(unittest.TestCase):
    def setUp(self) -> None:
        curseforge.clear_cache()

    def tearDown(self) -> None:
        curseforge.clear_cache()

    def test_put_and_get(self) -> None:
        curseforge._cache_put("/mods/search?x=1", {"data": [1]})
        self.assertEqual(
            curseforge._cache_get("/mods/search?x=1", 60), {"data": [1]}
        )

    def test_expired_entry_is_dropped(self) -> None:
        with mock.patch("darkitten.curseforge.time.monotonic", return_value=100.0):
            curseforge._cache_put("/mods/search?x=2", {"data": []})
        with mock.patch("darkitten.curseforge.time.monotonic", return_value=200.0):
            self.assertIsNone(curseforge._cache_get("/mods/search?x=2", 60))


class SearchServerAddonsTests(unittest.TestCase):
    def setUp(self) -> None:
        curseforge.clear_cache()

    def _payload(self) -> dict:
        return {
            "data": [
                {
                    "id": 408295,
                    "slug": "chunky",
                    "name": "Chunky (Bukkit)",
                    "summary": "Pre-generate chunks",
                    "downloadCount": 124063,
                    "classId": 5,
                    "categories": [{"name": "Admin Tools"}],
                }
            ],
            "pagination": {"totalCount": 1},
        }

    def test_paper_searches_bukkit_plugins(self) -> None:
        with mock.patch.object(
            curseforge, "_request_json", return_value=self._payload()
        ) as request:
            data = curseforge.search_server_addons(
                "chunky", server_type="paper", game_version="1.21.11", limit=10
            )
        params = request.call_args.args[1]
        self.assertEqual(params["classId"], curseforge.CLASS_ID_PLUGINS)
        self.assertEqual(params["gameVersion"], "1.21.11")
        self.assertNotIn("modLoaderType", params)
        self.assertEqual(data["hits"][0]["content_type"], "plugin")
        self.assertEqual(data["total_hits"], 1)

    def test_spigot_also_uses_plugin_class(self) -> None:
        with mock.patch.object(
            curseforge, "_request_json", return_value=self._payload()
        ) as request:
            curseforge.search_server_addons("chunky", server_type="spigot")
        params = request.call_args.args[1]
        self.assertEqual(params["classId"], curseforge.CLASS_ID_PLUGINS)

    def test_fabric_searches_mods_with_loader(self) -> None:
        with mock.patch.object(
            curseforge, "_request_json", return_value=self._payload()
        ) as request:
            curseforge.search_server_addons(
                "sodium", server_type="fabric", game_version="1.21.11"
            )
        params = request.call_args.args[1]
        self.assertEqual(params["classId"], curseforge.CLASS_ID_MODS)
        self.assertEqual(params["modLoaderType"], curseforge.LOADER_TYPES["fabric"])

    def test_unknown_server_type_raises(self) -> None:
        with self.assertRaises(curseforge.CurseForgeError):
            curseforge.search_server_addons("x", server_type="bedrock")


class FetchServerAddonVersionsTests(unittest.TestCase):
    def test_paper_uses_plugin_content_type_without_loader(self) -> None:
        with mock.patch.object(
            curseforge, "fetch_mod_versions", return_value=[]
        ) as fetch:
            curseforge.fetch_server_addon_versions("408295", "paper", "1.21.11")
        args, kwargs = fetch.call_args
        self.assertEqual(args, ("408295", "", "1.21.11"))
        self.assertEqual(kwargs.get("content_type"), "plugin")

    def test_forge_forwards_loader(self) -> None:
        with mock.patch.object(
            curseforge, "fetch_mod_versions", return_value=[]
        ) as fetch:
            curseforge.fetch_server_addon_versions("1", "forge", "1.20.1")
        args, kwargs = fetch.call_args
        self.assertEqual(args, ("1", "forge", "1.20.1"))
        self.assertEqual(kwargs.get("content_type"), "mod")


class NormalizeFileTests(unittest.TestCase):
    def test_plugin_page_url_uses_bukkit_path(self) -> None:
        version = curseforge.normalize_file(
            {
                "id": 6677879,
                "displayName": "Chunky 1.4.40",
                "fileName": "Chunky-Bukkit-1.4.40.jar",
                "downloadUrl": "https://edge.forgecdn.net/files/6677/879/x.jar",
                "fileLength": 296244,
                "gameVersions": ["1.21.11", "Paper", "Spigot", "Bukkit"],
            },
            project_slug="chunky",
            content_type="plugin",
        )
        self.assertEqual(version["source"], "curseforge")
        self.assertEqual(version["content_type"], "plugin")
        self.assertIn("/bukkit-plugins/chunky/files/6677879", version["page_url"])
        self.assertTrue(version["files"][0]["filename"].endswith(".jar"))


class WorkerDispatchTests(unittest.TestCase):
    def test_search_worker_uses_curseforge_when_requested(self) -> None:
        from darkitten import workers

        seen: dict = {}
        worker = workers.ServerAddonSearchWorker(
            "chunky", "paper", "1.21.11", 0, 20, source="curseforge"
        )
        worker.ready.connect(
            lambda data, error, offset: seen.update(
                data=data, error=error, offset=offset
            )
        )
        with mock.patch.object(
            workers,
            "search_curseforge_server_addons",
            return_value={"hits": [{"project_id": "1"}], "total_hits": 1},
        ) as fetch, mock.patch.object(
            workers, "search_server_addons"
        ) as modrinth:
            worker._run()
        fetch.assert_called_once()
        modrinth.assert_not_called()
        self.assertEqual(seen["data"]["total_hits"], 1)
        self.assertEqual(seen["error"], "")

    def test_versions_worker_uses_curseforge_when_requested(self) -> None:
        from darkitten import workers

        seen: dict = {}
        worker = workers.ServerAddonVersionsWorker(
            "408295", "paper", "1.21.11", source="curseforge"
        )
        worker.ready.connect(
            lambda data, error: seen.update(data=data, error=error)
        )
        with mock.patch.object(
            workers, "fetch_curseforge_server_addon_versions", return_value=[{"id": "1"}]
        ) as fetch, mock.patch.object(
            workers, "fetch_server_addon_versions"
        ) as modrinth:
            worker._run()
        fetch.assert_called_once_with("408295", "paper", "1.21.11")
        modrinth.assert_not_called()
        self.assertEqual(seen["data"], [{"id": "1"}])


class SegmentedSearchTests(unittest.TestCase):
    """大页搜索会拆成多段并发请求，再按顺序合并去重。"""

    def setUp(self) -> None:
        curseforge.clear_cache()

    def _hit(self, project_id: str) -> dict:
        return {
            "id": int(project_id),
            "name": f"项目{project_id}",
            "slug": f"p{project_id}",
            "classId": curseforge.CLASS_ID_MODS,
        }

    def test_single_segment_when_page_is_small(self) -> None:
        payload = {
            "data": [self._hit("1"), self._hit("2")],
            "pagination": {"totalCount": 2},
        }
        with mock.patch.object(
            curseforge, "_request_json", return_value=payload
        ) as request:
            result = curseforge.search_mods("jei", limit=20)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(len(result["hits"]), 2)
        self.assertEqual(result["total_hits"], 2)

    def test_large_page_is_split_and_merged_in_order(self) -> None:
        calls: list[dict] = []

        def fake_request(path, params=None, timeout=20, cache_ttl=0.0):
            calls.append(dict(params or {}))
            start = int(params["index"])
            size = int(params["pageSize"])
            ids = [str(start + i + 1) for i in range(size)]
            return {
                "data": [self._hit(i) for i in ids],
                "pagination": {"totalCount": 200},
            }

        with mock.patch.object(curseforge, "_request_json", side_effect=fake_request):
            result = curseforge.search_mods("jei", limit=60)

        self.assertEqual(len(calls), 3)
        self.assertEqual([c["index"] for c in calls], [0, 20, 40])
        self.assertTrue(all(c["pageSize"] == 20 for c in calls))
        self.assertEqual(len(result["hits"]), 60)
        self.assertEqual(
            [hit["project_id"] for hit in result["hits"]],
            [str(i) for i in range(1, 61)],
        )
        self.assertEqual(result["total_hits"], 200)

    def test_duplicate_projects_are_collapsed(self) -> None:
        def fake_request(path, params=None, timeout=20, cache_ttl=0.0):
            index = int(params["index"])
            # 两段都返回同一个项目，模拟排序抖动导致的重叠。
            return {
                "data": [self._hit("9"), self._hit(str(index + 100))],
                "pagination": {"totalCount": 4},
            }

        with mock.patch.object(curseforge, "_request_json", side_effect=fake_request):
            result = curseforge.search_mods("jei", limit=40)

        ids = [hit["project_id"] for hit in result["hits"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids[0], "9")

    def test_offset_is_respected(self) -> None:
        seen: list[int] = []

        def fake_request(path, params=None, timeout=20, cache_ttl=0.0):
            seen.append(int(params["index"]))
            size = int(params["pageSize"])
            return {
                "data": [
                    self._hit(str(int(params["index"]) + i + 1)) for i in range(size)
                ],
                "pagination": {"totalCount": 500},
            }

        with mock.patch.object(curseforge, "_request_json", side_effect=fake_request):
            curseforge.search_mods("jei", limit=40, offset=80)

        self.assertEqual(seen, [80, 100])


if __name__ == "__main__":
    unittest.main()