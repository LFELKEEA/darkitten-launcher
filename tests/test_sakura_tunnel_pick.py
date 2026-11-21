"""樱花穿透隧道复用逻辑的单元测试。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from darkitten.sakura import SakuraTunnel, _friendly_api_error  # noqa: E402
from darkitten.workers import SakuraRoomWorker  # noqa: E402


def tunnel(
    tunnel_id: int,
    name: str = "tunnel",
    tunnel_type: str = "tcp",
    status: int = 0,
    local_port: int = 25565,
    online: bool = False,
    node: int = 1,
) -> SakuraTunnel:
    return SakuraTunnel(
        id=tunnel_id,
        name=name,
        type=tunnel_type,
        node=node,
        status=status,
        online=online,
        local_ip="127.0.0.1",
        local_port=local_port,
    )


def worker(
    preferred: int = 0,
    avoid: int = 0,
    local_port: int = 25565,
    name: str = "DarkittenServer",
) -> SakuraRoomWorker:
    return SakuraRoomWorker(
        "token",
        local_port,
        preferred_tunnel_id=preferred,
        tunnel_name=name,
        avoid_tunnel_id=avoid,
    )


class PickTunnelTests(unittest.TestCase):
    def test_preferred_tunnel_wins(self) -> None:
        picked, reused = worker(preferred=7)._pick_tunnel(
            [tunnel(3, "DarkittenServer-1"), tunnel(7, "mine")]
        )
        self.assertTrue(reused)
        self.assertIsNotNone(picked)
        self.assertEqual(picked.id, 7)

    def test_any_existing_tcp_tunnel_is_reused(self) -> None:
        # 用户自己命名的隧道也要能直接进入，不能因为没有固定前缀就新建。
        picked, reused = worker()._pick_tunnel(
            [tunnel(11, "我的世界"), tunnel(12, "nas", tunnel_type="http")]
        )
        self.assertTrue(reused)
        self.assertIsNotNone(picked)
        self.assertEqual(picked.id, 11)

    def test_ignores_banned_and_non_tcp_tunnels(self) -> None:
        picked, reused = worker()._pick_tunnel(
            [tunnel(1, "banned", status=2), tunnel(2, "web", tunnel_type="http")]
        )
        self.assertFalse(reused)
        self.assertIsNone(picked)

    def test_avoid_tunnel_is_skipped_when_alternative_exists(self) -> None:
        picked, reused = worker(avoid=1)._pick_tunnel(
            [tunnel(1, "mine", local_port=25565), tunnel(2, "other", local_port=25570)]
        )
        self.assertTrue(reused)
        self.assertIsNotNone(picked)
        self.assertEqual(picked.id, 2)

    def test_avoid_tunnel_still_used_when_it_is_the_only_one(self) -> None:
        # 只有一条隧道时宁可复用，也不去触发“隧道数量已达上限”。
        picked, reused = worker(avoid=1)._pick_tunnel([tunnel(1, "mine")])
        self.assertTrue(reused)
        self.assertIsNotNone(picked)
        self.assertEqual(picked.id, 1)

    def test_empty_list_means_create_new(self) -> None:
        picked, reused = worker()._pick_tunnel([])
        self.assertFalse(reused)
        self.assertIsNone(picked)


class FriendlyErrorTests(unittest.TestCase):
    def test_tunnel_limit_is_not_reported_as_auth_problem(self) -> None:
        message = _friendly_api_error(
            403, "您的隧道数量已达到上限（当前 2 + 创建 1 > 2）"
        )
        self.assertIn("隧道数量已达上限", message)
        self.assertNotIn("认证", message)

    def test_real_auth_error_keeps_its_message(self) -> None:
        message = _friendly_api_error(403, "请先完成实名认证")
        self.assertIn("认证未完成", message)

    def test_invalid_token_still_reported(self) -> None:
        message = _friendly_api_error(401, "访问密钥无效")
        self.assertIn("访问密钥无效", message)


if __name__ == "__main__":
    unittest.main()
