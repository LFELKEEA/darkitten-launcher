"""审判战战果与本地回调的单元测试。"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QCoreApplication  # noqa: E402

from darkitten import judgement  # noqa: E402
from darkitten.ui.oc_dialog import ResultServer  # noqa: E402


class JudgementStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self._old = judgement.STATE_PATH
        judgement.STATE_PATH = Path(self._temp.name) / "judgement.json"

    def tearDown(self) -> None:
        judgement.STATE_PATH = self._old
        self._temp.cleanup()

    def test_hard_genocide_unlocks_shattered(self) -> None:
        applied = judgement.apply_result("genocide", "hard", turns=12, damage=4, hits=2)
        self.assertEqual(applied["reward"], judgement.REWARD_SHATTERED)
        self.assertTrue(applied["first_unlock"])
        state = judgement.load_state()
        self.assertTrue(judgement.unlocked(state, judgement.REWARD_SHATTERED))
        self.assertEqual(judgement.active_persona(state), judgement.REWARD_SHATTERED)
        self.assertEqual(state["last"]["turns"], 12)
        self.assertTrue(judgement.STATE_PATH.is_file())

    def test_hard_mercy_unlocks_attachment_and_prefers_it(self) -> None:
        judgement.apply_result("genocide", "hard")
        applied = judgement.apply_result("mercy", "hard", turns=30, damage=9, hits=5)
        self.assertEqual(applied["reward"], judgement.REWARD_ATTACHMENT)
        state = judgement.load_state()
        self.assertTrue(judgement.unlocked(state, judgement.REWARD_SHATTERED))
        self.assertTrue(judgement.unlocked(state, judgement.REWARD_ATTACHMENT))
        self.assertEqual(judgement.active_persona(state), judgement.REWARD_ATTACHMENT)

    def test_normal_difficulty_never_unlocks(self) -> None:
        applied = judgement.apply_result("mercy", "normal")
        self.assertEqual(applied["reward"], "")
        self.assertFalse(judgement.has_any(judgement.load_state()))

    def test_neutral_route_is_recorded_without_reward(self) -> None:
        applied = judgement.apply_result("neutral", "hard")
        self.assertEqual(applied["reward"], "")
        state = judgement.load_state()
        self.assertFalse(judgement.has_any(state))
        self.assertEqual(state["last"]["route"], "neutral")

    def test_endurance_route_is_recorded_without_reward(self) -> None:
        applied = judgement.apply_result("endurance", "hard", turns=300, damage=0, hits=4)
        self.assertEqual(applied["reward"], "")
        state = judgement.load_state()
        self.assertFalse(judgement.has_any(state))
        self.assertEqual(state["last"]["route"], "endurance")
        self.assertEqual(state["last"]["turns"], 300)

    def test_invalid_payload_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            judgement.apply_result("victory", "hard")
        with self.assertRaises(ValueError):
            judgement.apply_result("mercy", "insane")

    def test_set_active_requires_unlock(self) -> None:
        with self.assertRaises(ValueError):
            judgement.set_active(judgement.REWARD_ATTACHMENT)
        judgement.apply_result("genocide", "hard")
        judgement.apply_result("mercy", "hard")
        state = judgement.set_active(judgement.REWARD_SHATTERED)
        self.assertEqual(judgement.active_persona(state), judgement.REWARD_SHATTERED)

    def test_corrupt_state_falls_back_to_empty(self) -> None:
        judgement.STATE_PATH.write_text("{not json", encoding="utf-8")
        state = judgement.load_state()
        self.assertEqual(state["rewards"], {})
        self.assertFalse(judgement.has_any(state))

    def test_bundled_html_is_ready(self) -> None:
        self.assertTrue(judgement.html_ready())


class ResultServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QCoreApplication.instance() or QCoreApplication([])

    def _pump(self, done, timeout: float = 4.0) -> None:
        deadline = time.monotonic() + timeout
        while not done() and time.monotonic() < deadline:
            self._app.processEvents()
            time.sleep(0.01)
        self._app.processEvents()

    def test_valid_token_delivers_result(self) -> None:
        server = ResultServer()
        self.assertTrue(server.start())
        received: list[dict] = []
        server.result.connect(received.append)
        query = urllib.parse.urlencode(
            {
                "token": server.token,
                "route": "mercy",
                "diff": "hard",
                "turns": "42",
                "damage": "7",
                "hits": "3",
            }
        )
        url = f"http://127.0.0.1:{server.port}/result?{query}"

        def client() -> None:
            urllib.request.urlopen(url, timeout=3).read()

        thread = threading.Thread(target=client, daemon=True)
        thread.start()
        self._pump(lambda: bool(received))
        server.stop()
        thread.join(timeout=2)
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["route"], "mercy")
        self.assertEqual(received[0]["diff"], "hard")
        self.assertEqual(received[0]["turns"], "42")

    def test_wrong_token_is_rejected(self) -> None:
        server = ResultServer()
        self.assertTrue(server.start())
        received: list[dict] = []
        server.result.connect(received.append)
        codes: list[int] = []
        url = (
            f"http://127.0.0.1:{server.port}/result"
            f"?token=wrong&route=genocide&diff=hard"
        )

        def client() -> None:
            try:
                urllib.request.urlopen(url, timeout=3).read()
                codes.append(200)
            except urllib.error.HTTPError as error:
                codes.append(error.code)

        thread = threading.Thread(target=client, daemon=True)
        thread.start()
        self._pump(lambda: bool(codes))
        server.stop()
        thread.join(timeout=2)
        self.assertEqual(codes, [403])
        self.assertEqual(received, [])

    def test_other_paths_are_not_accepted(self) -> None:
        server = ResultServer()
        self.assertTrue(server.start())
        codes: list[int] = []
        url = f"http://127.0.0.1:{server.port}/favicon.ico"

        def client() -> None:
            try:
                urllib.request.urlopen(url, timeout=3).read()
                codes.append(200)
            except urllib.error.HTTPError as error:
                codes.append(error.code)

        thread = threading.Thread(target=client, daemon=True)
        thread.start()
        self._pump(lambda: bool(codes))
        server.stop()
        thread.join(timeout=2)
        self.assertEqual(codes, [404])


if __name__ == "__main__":
    unittest.main()