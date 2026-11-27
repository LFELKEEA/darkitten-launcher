"""版本清单与指令模板结构的单元测试。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from darkitten import game_assistant as ga  # noqa: E402
from darkitten.mc_versions import release_versions  # noqa: E402


class ReleaseVersionTests(unittest.TestCase):
    def test_contains_newest_release(self):
        versions = release_versions()
        self.assertIn("26.3", versions)
        self.assertIn("1.21.11", versions)
        self.assertIn("1.20.1", versions)
        self.assertIn("1.7.10", versions)

    def test_sorted_newest_first(self):
        versions = release_versions()
        self.assertEqual(versions[0], "26.3")
        self.assertLess(versions.index("26.3"), versions.index("1.21.11"))
        self.assertLess(versions.index("1.21.11"), versions.index("1.20.1"))
        self.assertLess(versions.index("1.20.1"), versions.index("1.12.2"))

    def test_extra_versions_are_merged(self):
        versions = release_versions(["1.21.11-Forge", "自定义版本"])
        self.assertIn("1.21.11-Forge", versions)
        self.assertIn("自定义版本", versions)
        self.assertEqual(len(versions), len(set(versions)))


class TemplateSkeletonTests(unittest.TestCase):
    def test_every_template_has_skeleton(self):
        for template in ga.COMMAND_TEMPLATES:
            self.assertIn(template.id, ga.TEMPLATE_SKELETONS, template.id)

    def test_skeleton_placeholders_match_fields(self):
        for template in ga.COMMAND_TEMPLATES:
            keys = {field.key for field in template.fields}
            skeleton = ga.TEMPLATE_SKELETONS[template.id]
            names = set()
            for piece in skeleton.replace("}{", "} {").split():
                if piece.startswith("{") and piece.endswith("}"):
                    names.add(piece.strip("{}"))
            self.assertTrue(names, template.id)
            self.assertLessEqual(names, keys, template.id)

    def test_skeleton_fixed_words_present_in_command(self):
        for template in ga.COMMAND_TEMPLATES:
            command = ga.build_from_template(template.id, ga.default_template_values(template))
            skeleton = ga.template_skeleton(template)
            fixed = [
                word
                for word in skeleton.split()
                if not word.startswith("{") and not word.startswith("~")
            ]
            for word in fixed:
                self.assertIn(word, command, template.id)


if __name__ == "__main__":
    unittest.main()
