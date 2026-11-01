from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "plugin_submission.py"
SPEC = importlib.util.spec_from_file_location("plugin_submission", SCRIPT)
assert SPEC and SPEC.loader
submission = importlib.util.module_from_spec(SPEC)
sys.modules["plugin_submission"] = submission
SPEC.loader.exec_module(submission)


def manifest(**overrides):
    data = {
        "id": "test.demo",
        "name": "测试插件",
        "version": "1.0.0",
        "api_version": 1,
        "entry": "main.py",
        "description": "一个用于测试投稿流程的插件。",
        "author": "Alice",
        "permissions": ["pet.say"],
        "settings": {"interval": 10},
    }
    data.update(overrides)
    return data


def build_zip(files: dict[str, bytes | str], *, directories: tuple[str, ...] = ()) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in directories:
            archive.writestr(name.rstrip("/") + "/", b"")
        for name, content in files.items():
            if isinstance(content, str):
                content = content.encode("utf-8")
            archive.writestr(name, content)
    return buffer.getvalue()


def plugin_zip(**overrides) -> bytes:
    manifest_data = manifest(**overrides)
    root = str(manifest_data["id"])
    return build_zip(
        {
            f"{root}/plugin.json": json.dumps(manifest_data, ensure_ascii=False),
            f"{root}/main.py": "def setup(api):\n    api.say('hello')\n",
            f"{root}/README.md": "# demo\n",
        },
        directories=(f"{root}/",),
    )


def zip_manifest(data: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        name = next(item for item in archive.namelist() if item.endswith("/plugin.json"))
        return json.loads(archive.read(name).decode("utf-8"))


def body_for(data: bytes, *, user="alice", extra=None) -> str:
    manifest_data = zip_manifest(data)
    meta = {
        "schema": 1,
        "id": manifest_data["id"],
        "name": manifest_data.get("name", manifest_data["id"]),
        "version": manifest_data["version"],
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "archive_name": f"{manifest_data['id']}-{manifest_data['version']}.zip",
        "notes": "测试投稿",
    }
    if extra:
        meta.update(extra)
    return (
        submission.MARKER
        + "\n\n### 插件投稿\n\n```json\n"
        + json.dumps(meta, ensure_ascii=False, indent=2)
        + "\n```\n\n### 插件包\n\n"
        + "[test.demo-1.0.0.zip](https://github.com/user-attachments/assets/00000000-0000-0000-0000-000000000000)\n"
    )


def event_for(data: bytes, *, user="alice", extra=None) -> dict:
    return {
        "issue": {
            "number": 42,
            "title": "[插件投稿] 测试插件",
            "body": body_for(data, user=user, extra=extra),
            "user": {"login": user},
            "created_at": "2026-11-01T00:00:00Z",
            "labels": [{"name": "plugin-submission"}],
        }
    }


class ZipValidationTests(unittest.TestCase):
    def test_valid_plugin(self):
        data = plugin_zip()
        result = submission.validate_zip(data)
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(result.plugin["id"], "test.demo")
        self.assertEqual(result.plugin["version"], "1.0.0")
        self.assertEqual(result.plugin["permissions"], ["pet.say"])
        self.assertEqual(result.plugin["file"], "plugins/test.demo-1.0.0.zip")
        self.assertEqual(result.sha256, hashlib.sha256(data).hexdigest())

    def test_directory_entries_are_allowed(self):
        data = plugin_zip()
        # plugin_zip 已经包含目录条目，这里再确认不会因为尾部斜杠被拒绝。
        self.assertTrue(submission.validate_zip(data).ok)

    def test_wrong_sha(self):
        result = submission.validate_zip(plugin_zip(), expected_sha="0" * 64)
        self.assertFalse(result.ok)
        self.assertIn("SHA-256", " ".join(result.errors))

    def test_wrong_size(self):
        result = submission.validate_zip(plugin_zip(), expected_size=1)
        self.assertFalse(result.ok)
        self.assertIn("大小", " ".join(result.errors))

    def test_root_must_match_id(self):
        data = build_zip(
            {
                "other/plugin.json": json.dumps(manifest(), ensure_ascii=False),
                "other/main.py": "def setup(api): pass\n",
            }
        )
        result = submission.validate_zip(data)
        self.assertFalse(result.ok)
        self.assertIn("根目录名", " ".join(result.errors))

    def test_reserved_prefix(self):
        result = submission.validate_zip(plugin_zip(id="lfelkeea.demo"))
        self.assertFalse(result.ok)
        self.assertIn("保留", " ".join(result.errors))

    def test_unknown_permission(self):
        result = submission.validate_zip(plugin_zip(permissions=["pet.say", "root.everything"]))
        self.assertFalse(result.ok)
        self.assertIn("已知权限", " ".join(result.errors))

    def test_missing_entry(self):
        manifest_data = manifest()
        data = build_zip(
            {
                "test.demo/plugin.json": json.dumps(manifest_data, ensure_ascii=False),
            }
        )
        result = submission.validate_zip(data)
        self.assertFalse(result.ok)
        self.assertIn("入口文件", " ".join(result.errors))

    def test_path_traversal(self):
        data = plugin_zip()
        data = build_zip(
            {
                "test.demo/plugin.json": json.dumps(manifest(), ensure_ascii=False),
                "test.demo/main.py": "def setup(api): pass\n",
                "test.demo/../evil.py": "print('evil')\n",
            }
        )
        result = submission.validate_zip(data)
        self.assertFalse(result.ok)
        self.assertIn("..", " ".join(result.errors))

    def test_dangerous_suffix(self):
        manifest_data = manifest()
        data = build_zip(
            {
                "test.demo/plugin.json": json.dumps(manifest_data, ensure_ascii=False),
                "test.demo/main.py": "def setup(api): pass\n",
                "test.demo/tool.exe": b"MZ",
            }
        )
        result = submission.validate_zip(data)
        self.assertFalse(result.ok)
        self.assertIn("可执行", " ".join(result.errors))

    def test_dangerous_config(self):
        manifest_data = manifest()
        data = build_zip(
            {
                "test.demo/plugin.json": json.dumps(manifest_data, ensure_ascii=False),
                "test.demo/main.py": "def setup(api): pass\n",
                "test.demo/config.json": "{}",
            }
        )
        result = submission.validate_zip(data)
        self.assertFalse(result.ok)
        self.assertIn("配置文件", " ".join(result.errors))

    def test_too_many_entries(self):
        manifest_data = manifest()
        files = {
            "test.demo/plugin.json": json.dumps(manifest_data, ensure_ascii=False),
            "test.demo/main.py": "def setup(api): pass\n",
        }
        for index in range(300):
            files[f"test.demo/files/{index}.txt"] = "x"
        result = submission.validate_zip(build_zip(files))
        self.assertFalse(result.ok)
        self.assertIn("300", " ".join(result.errors))


class IssueParsingTests(unittest.TestCase):
    def test_parse_meta_and_attachment(self):
        data = plugin_zip()
        body = body_for(data)
        meta, errors = submission.parse_issue_body(body)
        self.assertFalse(errors)
        self.assertEqual(meta["id"], "test.demo")
        urls = submission.attachment_urls(body)
        self.assertEqual(len(urls), 1)
        self.assertIn("user-attachments", urls[0])

    def test_missing_marker(self):
        meta, errors = submission.parse_issue_body("```json\n{}\n```")
        self.assertIsNone(meta)
        self.assertTrue(errors)

    def test_invalid_json(self):
        body = submission.MARKER + "\n```json\n{oops}\n```"
        meta, errors = submission.parse_issue_body(body)
        self.assertIsNone(meta)
        self.assertTrue(errors)


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.public = self.root / "website" / "public"
        self.plugins = self.public / "plugins"
        self.plugins.mkdir(parents=True)
        (self.root / "website" / "community_plugins.json").write_text(
            json.dumps({"schema": 1, "plugins": []}, ensure_ascii=False),
            encoding="utf-8",
        )
        (self.public / "plugins.json").write_text(
            json.dumps(
                {
                    "updated": "2026-10-31",
                    "api_version": 1,
                    "pet_version": "1.3.3",
                    "plugins": [
                        {
                            "id": "lfelkeea.focus_guard",
                            "name": "专注时间",
                            "version": "1.0.0",
                            "file": "plugins/lfelkeea.focus_guard-1.0.0.zip",
                            "size": 1,
                            "sha256": "f" * 64,
                            "featured": True,
                            "bundled": False,
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.zip_path = self.root / "submission.zip"

    def tearDown(self):
        self.temp.cleanup()

    def publish(self, data: bytes, *, user="alice", extra=None):
        self.zip_path.write_bytes(data)
        return submission.publish_event(
            event_for(data, user=user, extra=extra),
            repo_root=self.root,
            attachment=str(self.zip_path),
        )[0]

    def test_publish_adds_plugin(self):
        result = self.publish(plugin_zip())
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(result["zip_rel"], "plugins/test.demo-1.0.0.zip")
        self.assertTrue((self.plugins / "test.demo-1.0.0.zip").is_file())

        registry = json.loads((self.public / "plugins.json").read_text(encoding="utf-8"))
        ids = [item["id"] for item in registry["plugins"]]
        self.assertIn("test.demo", ids)
        entry = next(item for item in registry["plugins"] if item["id"] == "test.demo")
        self.assertEqual(entry["version"], "1.0.0")
        self.assertEqual(entry["sha256"], hashlib.sha256(plugin_zip()).hexdigest())
        self.assertFalse(entry["featured"])
        self.assertTrue(entry["community"])

        community = json.loads((self.root / "website" / "community_plugins.json").read_text(encoding="utf-8"))
        self.assertEqual(community["plugins"][0]["submitted_by"], "alice")
        self.assertEqual(community["plugins"][0]["entry"]["name"], "测试插件")
        self.assertTrue(community["plugins"][0]["entry"]["community"])

    def test_same_version_is_rejected(self):
        self.assertTrue(self.publish(plugin_zip())["ok"])
        result = self.publish(plugin_zip())
        self.assertFalse(result["ok"])
        self.assertIn("新版本必须更高", " ".join(result["errors"]))

    def test_update_replaces_entry_and_keeps_registry(self):
        self.assertTrue(self.publish(plugin_zip())["ok"])
        result = self.publish(plugin_zip(version="1.1.0"))
        self.assertTrue(result["ok"], result["errors"])
        registry = json.loads((self.public / "plugins.json").read_text(encoding="utf-8"))
        entry = next(item for item in registry["plugins"] if item["id"] == "test.demo")
        self.assertEqual(entry["version"], "1.1.0")
        self.assertEqual(len([item for item in registry["plugins"] if item["id"] == "test.demo"]), 1)
        self.assertTrue(any(item["id"] == "lfelkeea.focus_guard" for item in registry["plugins"]))

    def test_official_id_cannot_be_replaced(self):
        registry_path = self.public / "plugins.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["plugins"].append(
            {
                "id": "official.demo",
                "name": "官方插件",
                "version": "1.0.0",
                "file": "plugins/official.demo-1.0.0.zip",
                "size": 1,
                "sha256": "e" * 64,
            }
        )
        registry_path.write_text(json.dumps(registry, ensure_ascii=False), encoding="utf-8")
        result = self.publish(plugin_zip(id="official.demo", name="劫持"))
        self.assertFalse(result["ok"])
        self.assertIn("官方", " ".join(result["errors"]))

    def test_other_submitter_warns(self):
        self.assertTrue(self.publish(plugin_zip())["ok"])
        result = self.publish(plugin_zip(version="1.1.0"), user="bob")
        self.assertTrue(result["ok"], result["errors"])
        self.assertTrue(any("别人提交过" in item for item in result["warnings"]))


class BuildPluginsTests(unittest.TestCase):
    def test_community_entry_keeps_full_metadata(self):
        build_spec = importlib.util.spec_from_file_location(
            "build_plugins_test", ROOT / "website" / "build_plugins.py"
        )
        assert build_spec and build_spec.loader
        build = importlib.util.module_from_spec(build_spec)
        build_spec.loader.exec_module(build)

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            site = base / "website"
            public = site / "public"
            dest = public / "plugins"
            dest.mkdir(parents=True)
            pet = base / "desktop_pet"
            source = pet / "plugins"
            source.mkdir(parents=True)
            docs = pet / "docs"
            docs.mkdir()

            data = plugin_zip()
            (dest / "test.demo-1.0.0.zip").write_bytes(data)
            entry = {
                "id": "test.demo",
                "name": "测试插件",
                "version": "1.0.0",
                "author": "Alice",
                "description": "社区插件",
                "api_version": 1,
                "permissions": ["pet.say"],
                "permission_labels": ["说话"],
                "requires": [],
                "highlights": [],
                "settings": {},
                "bundled": False,
                "featured": False,
                "community": True,
            }
            (site / "community_plugins.json").write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "plugins": [
                            {
                                "id": "test.demo",
                                "version": "1.0.0",
                                "file": "plugins/test.demo-1.0.0.zip",
                                "submitted_by": "alice",
                                "entry": entry,
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (site / "pet_release.json").write_text(
                json.dumps({"version": "1.3.3"}), encoding="utf-8"
            )

            old = {
                "SITE": build.SITE,
                "PUBLIC": build.PUBLIC,
                "PET": build.PET,
                "SOURCE": build.SOURCE,
                "DOCS": build.DOCS,
                "DEST": build.DEST,
                "REGISTRY": build.REGISTRY,
                "COMMUNITY": build.COMMUNITY,
            }
            try:
                build.SITE = site
                build.PUBLIC = public
                build.PET = pet
                build.SOURCE = source
                build.DOCS = docs
                build.DEST = dest
                build.REGISTRY = public / "plugins.json"
                build.COMMUNITY = site / "community_plugins.json"
                build.main()
            finally:
                for key, value in old.items():
                    setattr(build, key, value)

            registry = json.loads((public / "plugins.json").read_text(encoding="utf-8"))
            self.assertEqual(len(registry["plugins"]), 1)
            item = registry["plugins"][0]
            self.assertEqual(item["name"], "测试插件")
            self.assertEqual(item["description"], "社区插件")
            self.assertEqual(item["permissions"], ["pet.say"])
            self.assertTrue(item["community"])
            self.assertEqual(item["sha256"], hashlib.sha256(data).hexdigest())


class ReportTests(unittest.TestCase):
    def test_validate_report_mentions_next_steps(self):
        data = plugin_zip()
        validation = submission.validate_zip(data)
        self.assertTrue(validation.ok, validation.errors)
        result = {
            "ok": True,
            "errors": [],
            "warnings": [],
            "plugin": validation.plugin,
            "sha256": validation.sha256,
            "size": validation.size,
            "archive_name": "test.demo-1.0.0.zip",
            "issue": {"number": 12, "user": "alice"},
            "attachment_url": "https://github.com/user-attachments/files/1/test.zip",
        }
        markdown = submission.markdown_report(result, "validate")
        self.assertTrue(markdown.startswith("<!-- dkl-plugin-check:v1 -->"))
        self.assertIn("自动校验：✅ 通过", markdown)
        self.assertIn("`test.demo`", markdown)
        self.assertIn("`pet.say`", markdown)
        self.assertIn("/approve", markdown)
        self.assertIn(validation.sha256, markdown)
        self.assertIn("https://github.com/user-attachments", markdown)

    def test_validate_report_lists_errors(self):
        result = {
            "ok": False,
            "errors": ["ZIP 里只有一个顶层插件文件夹。"],
            "warnings": [],
            "plugin": {},
            "issue": {"number": 3, "user": "bob"},
        }
        markdown = submission.markdown_report(result, "validate")
        self.assertIn("自动校验：❌ 未通过", markdown)
        self.assertIn("ZIP 里只有一个顶层插件文件夹", markdown)
        self.assertIn("修改后编辑本 Issue", markdown)

    def test_publish_report_uses_separate_marker(self):
        result = {
            "ok": True,
            "errors": [],
            "warnings": [],
            "plugin": {"id": "test.demo", "name": "测试插件", "version": "1.0.0"},
            "sha256": "a" * 64,
            "size": 2048,
            "zip_rel": "plugins/test.demo-1.0.0.zip",
            "issue": {"number": 9, "user": "alice"},
        }
        markdown = submission.markdown_report(result, "publish")
        self.assertTrue(markdown.startswith("<!-- dkl-plugin-publish:v1 -->"))
        self.assertIn("发布结果：✅ 已发布", markdown)
        self.assertIn("plugins/test.demo-1.0.0.zip", markdown)
        self.assertIn("plugins.html", markdown)


if __name__ == "__main__":
    unittest.main()
