"""OC 配置、记忆与提示词的单元测试（不联网）。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from darkitten import oc  # noqa: E402


class OCConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name)
        self._old_config = oc.CONFIG_PATH
        self._old_memory = oc.MEMORY_PATH
        oc.CONFIG_PATH = root / "oc_ai.json"
        oc.MEMORY_PATH = root / "oc_memory.json"

    def tearDown(self) -> None:
        oc.CONFIG_PATH = self._old_config
        oc.MEMORY_PATH = self._old_memory
        self._temp.cleanup()

    def test_defaults_are_not_ready(self) -> None:
        config = oc.load_config()
        self.assertFalse(config.ready)
        self.assertEqual(config.base_url, "https://api.openai.com/v1")
        self.assertEqual(config.model, "gpt-4o-mini")

    def test_roundtrip_keeps_values(self) -> None:
        saved = oc.OCConfig(
            base_url="https://api.example.com/v1",
            model="my-model",
            api_key="sk-test-value",
            temperature=0.4,
            timeout=30,
        )
        oc.save_config(saved)
        loaded = oc.load_config()
        self.assertEqual(loaded.to_dict(), saved.to_dict())
        self.assertTrue(loaded.ready)
        self.assertTrue(oc.CONFIG_PATH.is_file())

    def test_values_are_clamped(self) -> None:
        config = oc.OCConfig.from_dict(
            {"temperature": 9, "timeout": 1, "base_url": "  https://x/v1  "}
        )
        self.assertEqual(config.temperature, 2.0)
        self.assertEqual(config.timeout, 15)
        self.assertEqual(config.base_url, "https://x/v1")

    def test_endpoint_building(self) -> None:
        self.assertEqual(
            oc.OCConfig(base_url="https://api.openai.com/v1").endpoint,
            "https://api.openai.com/v1/chat/completions",
        )
        self.assertEqual(
            oc.OCConfig(base_url="https://api.deepseek.com").endpoint,
            "https://api.deepseek.com/chat/completions",
        )
        self.assertEqual(
            oc.OCConfig(base_url="https://x/v1/chat/completions").endpoint,
            "https://x/v1/chat/completions",
        )

    def test_memory_roundtrip_and_cap(self) -> None:
        for index in range(oc.MAX_MESSAGES):
            oc.remember("Tester", f"问 {index}", f"答 {index}")
        memory = oc.load_memory()
        self.assertEqual(memory["player"], "Tester")
        self.assertTrue(memory["first_met"])
        self.assertEqual(memory["chats"], oc.MAX_MESSAGES)
        self.assertEqual(len(memory["messages"]), oc.MAX_MESSAGES)
        self.assertEqual(memory["messages"][-1]["content"], f"答 {oc.MAX_MESSAGES - 1}")

    def test_clear_memory_removes_file(self) -> None:
        oc.remember("Tester", "你好", "嗯。")
        self.assertTrue(oc.MEMORY_PATH.is_file())
        oc.clear_memory()
        self.assertFalse(oc.MEMORY_PATH.is_file())
        self.assertEqual(oc.load_memory()["messages"], [])

    def test_broken_prompt_has_no_history(self) -> None:
        oc.remember("Tester", "我叫什么", "不知道。")
        memory = oc.load_memory()
        messages = oc.build_messages("shattered", "Tester", memory, "再来一次")
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("破碎之躯", messages[0]["content"])
        self.assertEqual(messages[1], {"role": "user", "content": "再来一次"})
        self.assertNotIn("我叫什么", str(messages))

    def test_attachment_prompt_includes_memory(self) -> None:
        oc.remember("Tester", "我喜欢喝热可可", "记住了。")
        memory = oc.load_memory()
        messages = oc.build_messages("attachment", "Tester", memory, "我回来了")
        self.assertGreaterEqual(len(messages), 4)
        self.assertIn("LFELKEEA", messages[0]["content"])
        self.assertIn("热可可", messages[0]["content"])
        self.assertEqual(messages[-1]["content"], "我回来了")

    def test_chat_requires_key(self) -> None:
        with self.assertRaises(oc.OCError):
            oc.chat(oc.OCConfig(api_key=""), [{"role": "user", "content": "hi"}])


if __name__ == "__main__":
    unittest.main()