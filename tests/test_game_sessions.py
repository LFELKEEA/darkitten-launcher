"""多游戏会话的单元测试（不显示窗口）。"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from darkitten.ui.main_window import GameSession, MainWindow  # noqa: E402


def fake_main(*sessions: GameSession) -> SimpleNamespace:
    holder = SimpleNamespace(
        game_sessions={item.key: item for item in sessions},
        game_running=bool(sessions),
        game_process=None,
        _running_version="",
        _running_profile_id="",
        _game_started_at=0.0,
        _launch_closing=False,
    )
    holder.game_session = lambda key: MainWindow.game_session(holder, key)
    holder.session_process = lambda key: MainWindow.session_process(holder, key)
    holder._latest_session = lambda: MainWindow._latest_session(holder)
    return holder


class GameSessionTests(unittest.TestCase):
    def test_defaults(self) -> None:
        session = GameSession(
            key="game-1",
            version="1.21.11",
            profile_id="1.21.11",
            instance_id="1.21.11",
        )
        self.assertEqual(session.pid, 0)
        self.assertFalse(session.closing)
        self.assertIsNone(session.process)

    def test_find_by_version_profile_or_instance(self) -> None:
        session = GameSession(
            key="game-1",
            version="1.21.11-Fabric 0.19.5",
            profile_id="fabric-loader-0.19.5-1.21.11",
            instance_id="1.21.11-Fabric 0.19.5",
        )
        holder = fake_main(session)
        self.assertIs(
            MainWindow.find_game_session(holder, "1.21.11-Fabric 0.19.5"),
            session,
        )
        self.assertIs(
            MainWindow.find_game_session(holder, "fabric-loader-0.19.5-1.21.11"),
            session,
        )
        self.assertIsNone(MainWindow.find_game_session(holder, "1.20.1"))

    def test_multiple_sessions_are_independent(self) -> None:
        first = GameSession("game-1", "1.21.11", "1.21.11", "1.21.11", pid=101)
        second = GameSession("game-2", "1.20.1", "1.20.1", "1.20.1", pid=202)
        holder = fake_main(first, second)
        self.assertEqual(len(holder.game_sessions), 2)
        self.assertEqual(holder.session_process("game-1"), first.process)
        self.assertEqual(holder.session_process("game-2"), second.process)
        self.assertIs(holder._latest_session(), second)

    def test_latest_session_uses_started_at(self) -> None:
        older = GameSession("game-1", "1.20.1", "1.20.1", "1.20.1", started_at=10)
        newer = GameSession("game-2", "1.21.11", "1.21.11", "1.21.11", started_at=20)
        holder = fake_main(newer, older)
        self.assertIs(holder._latest_session(), newer)

    def test_sync_state_tracks_remaining_sessions(self) -> None:
        first = GameSession("game-1", "1.20.1", "1.20.1", "1.20.1", started_at=10)
        second = GameSession("game-2", "1.21.11", "1.21.11", "1.21.11", started_at=20)
        holder = fake_main(first, second)
        MainWindow._sync_game_state(holder)
        self.assertTrue(holder.game_running)
        self.assertEqual(holder._running_version, "1.21.11")

        del holder.game_sessions["game-2"]
        MainWindow._sync_game_state(holder)
        self.assertTrue(holder.game_running)
        self.assertEqual(holder._running_version, "1.20.1")

        del holder.game_sessions["game-1"]
        MainWindow._sync_game_state(holder)
        self.assertFalse(holder.game_running)
        self.assertIsNone(holder.game_process)
        self.assertEqual(holder._running_version, "")


if __name__ == "__main__":
    unittest.main()