"""审判战启动 URL 与战果回传的界面流程测试（不真正开浏览器）。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QUrl  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from darkitten import judgement  # noqa: E402
from darkitten.ui import oc_dialog  # noqa: E402


class MainStub(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.opened = []

    def open_oc_chat(self) -> None:
        self.opened.append("chat")

    def open_oc_config(self) -> None:
        self.opened.append("config")


class FlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self._old_state = judgement.STATE_PATH
        self._old_launch = oc_dialog._launch_browser
        self._old_result = oc_dialog.JudgementResultDialog
        judgement.STATE_PATH = Path(self._temp.name) / "judgement.json"
        self.captured: dict = {}

        def fake_launch(url: str) -> bool:
            self.captured["url"] = url
            return True

        class FakeResult:
            def __init__(self, parent, applied) -> None:
                self.chat_requested = False
                self.captured_applied = applied
                captured["applied"] = applied

            def exec(self) -> int:
                return 0

        captured = self.captured
        self._fake_result = FakeResult
        oc_dialog._launch_browser = fake_launch
        oc_dialog.JudgementResultDialog = FakeResult
        self.main = MainStub()

    def tearDown(self) -> None:
        judgement.STATE_PATH = self._old_state
        oc_dialog._launch_browser = self._old_launch
        oc_dialog.JudgementResultDialog = self._old_result
        self.main.close()
        self._temp.cleanup()

    def test_start_url_and_hard_mercy_result(self) -> None:
        dialog = oc_dialog.JudgementDialog(self.main, None)
        dialog._start()
        self.assertTrue(dialog._playing)
        self.assertIn("url", self.captured)

        parsed = QUrl(self.captured["url"])
        self.assertEqual(parsed.scheme(), "file")
        self.assertTrue(parsed.path().endswith("judgement-lfelkeea.html"))
        query = urllib.parse.parse_qs(parsed.query())
        callback = (query.get("cb") or [""])[0]
        token = (query.get("token") or [""])[0]
        self.assertTrue(callback.startswith("http://127.0.0.1:"))
        self.assertTrue(callback.endswith("/result"))
        self.assertEqual(token, dialog._server.token)

        dialog._on_result(
            {
                "route": "mercy",
                "diff": "hard",
                "turns": "50",
                "damage": "11",
                "hits": "6",
            }
        )
        self.assertFalse(dialog._playing)
        self.assertIsNone(dialog._server)
        state = judgement.load_state()
        self.assertEqual(
            judgement.active_persona(state), judgement.REWARD_ATTACHMENT
        )
        self.assertEqual(state["last"]["turns"], 50)
        dialog.close()

    def test_normal_route_does_not_unlock(self) -> None:
        dialog = oc_dialog.JudgementDialog(self.main, None)
        dialog._start()
        dialog._on_result(
            {
                "route": "mercy",
                "diff": "normal",
                "turns": "20",
                "damage": "3",
                "hits": "1",
            }
        )
        self.assertEqual(judgement.load_state()["rewards"], {})
        self.assertEqual(self.captured["applied"]["reward"], "")
        dialog.close()

    def test_stop_session_stops_server(self) -> None:
        dialog = oc_dialog.JudgementDialog(self.main, None)
        dialog._start()
        server = dialog._server
        self.assertTrue(server._server.isListening())
        dialog._stop_session()
        self.assertFalse(server._server.isListening())
        self.assertFalse(dialog._playing)
        self.assertIsNone(dialog._server)
        dialog.close()


if __name__ == "__main__":
    unittest.main()