"""P2P 一键联机流程的单元测试（不显示窗口）。"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from darkitten.ui import easytier_panel as panel_module  # noqa: E402
from darkitten.ui.easytier_panel import EasyTierPanel  # noqa: E402

APP = QApplication.instance() or QApplication([])

VERSION = "1.21.11-Fabric 0.19.5"
WORLD = "新的世界"


class FakeConfig:
    easytier_last_port = 25565
    easytier_direct_only = False
    easytier_hide_address = False
    easytier_machine_id = "machine"
    easytier_node_ids: list[str] = []

    def version_name(self, version_id: str) -> str:
        return ""

    def version_game_path(self, version_id: str) -> str:
        return str(Path("game") / version_id)

    def save(self) -> None:
        pass


class FakeMain(QObject):
    versions_changed = Signal()
    selection_changed = Signal(str)
    game_state_changed = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self.config = FakeConfig()
        self.selected_version = VERSION
        self.game_running = False
        self.game_process = None
        self.launch_active = False
        self.install_active = False
        self.sessions: dict[str, SimpleNamespace] = {}
        self.launched: list[dict] = []
        self.toasts: list[str] = []
        self.selected: list[str] = []
        self.home_page = SimpleNamespace(select_version=self.selected.append)

    def display_name(self) -> str:
        return "Tester"

    def game_version_summaries(self):
        return [
            SimpleNamespace(
                id=VERSION,
                loader="Fabric",
                type_name="Fabric",
                error="",
            )
        ]

    def find_game_session(self, version_id: str):
        for session in self.sessions.values():
            if getattr(session, "version", "") == version_id:
                return session
        return None

    def session_process(self, key: str):
        session = self.sessions.get(key)
        return session.process if session else None

    def launch_selected(self, **kwargs) -> str:
        self.launched.append(kwargs)
        key = "game-1"
        process = SimpleNamespace(pid=4321)
        self.sessions[key] = SimpleNamespace(
            key=key, version=self.selected_version, process=process
        )
        self.game_running = True
        return key

    def toast(self, text: str, kind: str = "info") -> None:
        self.toasts.append(text)


def fake_world() -> SimpleNamespace:
    return SimpleNamespace(
        folder=SimpleNamespace(name=WORLD),
        name=WORLD,
        mode_text="创造",
        last_played_text="2026-11-22",
    )


class EasyTierQuickTests(unittest.TestCase):
    def setUp(self) -> None:
        self.main = FakeMain()
        patcher = mock.patch.object(
            panel_module, "scan_worlds", lambda path: [fake_world()]
        )
        self.addCleanup(patcher.stop)
        patcher.start()
        self.panel = EasyTierPanel(self.main)
        self.addCleanup(self.panel.shutdown)
        self.addCleanup(self.panel.deleteLater)

    def test_host_combos_are_populated(self) -> None:
        self.assertEqual(self.panel.host_version_combo.currentData(), VERSION)
        self.assertEqual(self.panel.host_save_combo.currentData(), WORLD)
        self.assertTrue(self.panel.host_quick_btn.isEnabled())

    def test_quick_start_launches_world_and_waits_for_port(self) -> None:
        self.panel.host_quick_start()
        self.assertEqual(
            self.main.launched,
            [{"quick_world": WORLD}],
        )
        self.assertTrue(self.panel._auto_host_flow)
        self.assertEqual(self.panel._host_session_key, "game-1")
        self.assertTrue(self.panel._lan_timer.isActive())

    def test_detected_port_creates_room_automatically(self) -> None:
        calls: list[bool] = []
        self.panel.create_room = (
            lambda checked=False, auto=False: calls.append(auto)
        )
        self.panel.host_quick_start()
        with mock.patch.object(
            panel_module, "detect_game_lan_ports", lambda pid: [57266]
        ), mock.patch.object(
            panel_module, "preferred_lan_port", lambda ports: 57266
        ):
            self.panel._poll_host_lan()
        self.assertEqual(calls, [True])
        self.assertEqual(self.panel.port_spin.value(), 57266)
        self.assertFalse(self.panel._lan_timer.isActive())

    def test_existing_version_is_reused(self) -> None:
        process = SimpleNamespace(pid=999)
        self.main.sessions["game-9"] = SimpleNamespace(
            key="game-9", version=VERSION, process=process
        )
        self.main.game_running = True
        self.panel.host_quick_start()
        self.assertEqual(self.main.launched, [])
        self.assertEqual(self.panel._host_session_key, "game-9")
        self.assertTrue(self.panel._auto_host_flow)
        self.assertTrue(self.panel._lan_timer.isActive())
        with mock.patch.object(
            panel_module, "detect_game_lan_ports", lambda pid: [25565]
        ), mock.patch.object(
            panel_module, "preferred_lan_port", lambda ports: 25565
        ), mock.patch.object(
            self.panel, "create_room", lambda checked=False, auto=False: None
        ):
            self.panel._poll_host_lan()
        self.assertEqual(self.panel.port_spin.value(), 25565)

    def test_game_exit_stops_waiting(self) -> None:
        self.panel.host_quick_start()
        self.main.sessions.clear()
        self.main.game_running = False
        self.main.launch_active = False
        self.panel._poll_host_lan()
        self.assertFalse(self.panel._auto_host_flow)
        self.assertFalse(self.panel._lan_timer.isActive())
        self.assertEqual(self.panel._host_session_key, "")


if __name__ == "__main__":
    unittest.main()
