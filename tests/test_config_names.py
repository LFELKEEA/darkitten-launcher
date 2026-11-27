"""版本显示名称的单元测试：只改显示，不动内部版本目录。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from darkitten.config import Config  # noqa: E402


class VersionNameTests(unittest.TestCase):
    def test_set_and_read_name(self):
        config = Config()
        config.set_version_name("1.21.4-Forge", "周末生存")
        self.assertEqual(config.version_name("1.21.4-Forge"), "周末生存")

    def test_empty_name_removes_alias(self):
        config = Config()
        config.set_version_name("1.21.4", "测试")
        config.set_version_name("1.21.4", "   ")
        self.assertEqual(config.version_name("1.21.4"), "")

    def test_name_equal_to_id_is_not_stored(self):
        config = Config()
        config.set_version_name("1.21.4", "1.21.4")
        self.assertNotIn("1.21.4", config.version_names)

    def test_blank_version_id_is_ignored(self):
        config = Config()
        config.set_version_name("  ", "名字")
        self.assertEqual(config.version_names, {})


if __name__ == "__main__":
    unittest.main()