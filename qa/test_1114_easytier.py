from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from PySide6.QtWidgets import QApplication

import darkitten.config as config_module
import darkitten.easytier as et
from darkitten.config import Config
from darkitten.easytier import (
    EasyTierConfig,
    EasyTierPackage,
    EasyTierPeer,
    PlayerProfile,
    PUBLIC_NODES,
    RoomClient,
    RoomCode,
    RoomServer,
    _extract_json,
    _read_request,
    _read_response,
    _write_request,
    _write_response,
    guest_config,
    host_config,
    make_profile,
    parse_room_code,
    room_code_digits,
    select_nodes,
)
from darkitten.workers import EasyTierHostWorker, EasyTierJoinWorker

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> bool:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"{name}: OK{(' (' + detail + ')') if detail else ''}")
    else:
        FAIL += 1
        print(f"{name}: FAIL{(' (' + detail + ')') if detail else ''}")
    return condition


# ----------------------------------------------------------------------
# 房间号


def test_room_code() -> None:
    code = RoomCode.create()
    check("房间号格式", bool(__import__("re").fullmatch(
        r"U/[0-9A-Z]{4}-[0-9A-Z]{4}-[0-9A-Z]{4}-[0-9A-Z]{4}", code.code
    )), code.code)
    check("房间号可被 7 整除", code.value % 7 == 0, str(code.value))
    check("房间号 16 位", len(code.digits) == 16, code.digits)
    check(
        "网络名由前 8 位组成",
        code.network_name == "scaffolding-mc-" + code.digits[0:4] + "-" + code.digits[4:8],
        code.network_name,
    )
    check(
        "网络密钥由后 8 位组成",
        code.network_secret == code.digits[8:12] + "-" + code.digits[12:16],
        code.network_secret,
    )
    check("房间号字符串", str(code) == code.code)
    check("房间号 repr", code.code in repr(code))

    check("解析标准房间号", parse_room_code(code.code).value == code.value)
    check("解析省略前缀的房间号", parse_room_code(code.digits).value == code.value)
    check(
        "从聊天文本中找房间号",
        parse_room_code(f"来玩！房间号：{code.code.lower()} 快进").value
        == code.value,
    )
    check(
        "房间号允许 I/O 误输入",
        RoomCode.find(code.code.replace("1", "I").replace("0", "O")) is not None,
    )
    check("format_room_code", et.format_room_code("xx " + code.code) == code.code)
    check("format_room_code 无效", et.format_room_code("U/0000-0000-0000-0001") == "")
    check(
        "全零房间号本身合法",
        et.format_room_code("U/0000-0000-0000-0000") == "U/0000-0000-0000-0000",
    )
    check("room_code_digits", room_code_digits(code.code) == code.digits)
    check("room_code_digits 无效", room_code_digits("bad") == "")

    for bad in ("U/0000-0000-0000-0001", "", "hello"):
        try:
            parse_room_code(bad)
        except ValueError:
            ok = True
        else:
            ok = False
        check(f"拒绝无效房间号 {bad!r}", ok)

    for value in (-7, 1, RoomCode._range + 7):
        try:
            RoomCode(value)
        except ValueError:
            ok = True
        else:
            ok = False
        check(f"拒绝错误数值 {value}", ok)


# ----------------------------------------------------------------------
# TOML 配置


def test_toml() -> None:
    code = RoomCode.create()
    host = host_config(code, 25565, 25566)
    text = host.to_toml()
    data = tomllib.loads(text)
    check("房主 TOML 可解析", isinstance(data, dict))
    check(
        "房主主机名前缀",
        data["hostname"] == "scaffolding-mc-server-25565",
        data.get("hostname", ""),
    )
    check("房主固定虚拟地址", data["ipv4"] == "10.144.144.1")
    check("房主监听端口", len(data["listeners"]) == 2)
    check("房主 TCP 白名单", data["tcp_whitelist"] == ["25565", "25566"])
    check("房主 UDP 白名单", data["udp_whitelist"] == ["25566"])
    check(
        "网络名称密钥",
        data["network_identity"]["network_name"] == code.network_name
        and data["network_identity"]["network_secret"] == code.network_secret,
    )
    check("节点数量", len(data["peer"]) == len(PUBLIC_NODES), str(len(data["peer"])))
    flags = data["flags"]
    check("no_tun", flags["no_tun"] is True)
    check("latency_first", flags["latency_first"] is True)
    check("kcp proxy", flags["enable_kcp_proxy"] is True)
    check("quic proxy", flags["enable_quic_proxy"] is True)
    check("压缩算法", flags["data_compress_algo"] == 2)
    check("private mode", flags["private_mode"] is True)
    check("默认允许中继", flags["p2p_only"] is False)

    direct = tomllib.loads(
        host_config(code, 25565, 25566, direct_only=True).to_toml()
    )
    check("只允许直连", direct["flags"]["p2p_only"] is True)

    hidden = tomllib.loads(
        host_config(code, 25565, 25566, hide_address=True).to_toml()
    )
    check("隐藏地址关闭监听", hidden["listeners"] == [])
    check("隐藏地址禁用 P2P", hidden["flags"]["disable_p2p"] is True)
    check("隐藏地址禁用打洞", hidden["flags"]["disable_udp_hole_punching"] is True)
    check("隐藏地址强制中继", hidden["flags"]["p2p_only"] is False)

    guest = tomllib.loads(guest_config(code).to_toml())
    check("访客使用 DHCP", guest.get("dhcp") is True and "ipv4" not in guest)
    check("访客白名单", guest["tcp_whitelist"] == ["0"] and guest["udp_whitelist"] == ["0"])

    windows7 = tomllib.loads(
        EasyTierConfig(
            network_name="n",
            network_secret="s",
            hostname="h",
            windows7=True,
        ).to_toml()
    )
    check(
        "Windows 7 关闭 QUIC",
        windows7["flags"]["enable_quic_proxy"] is False
        and windows7["flags"]["disable_quic_input"] is True,
    )

    weird = EasyTierConfig(
        network_name='a"b',
        network_secret="c\\d",
        hostname="换行\n名字",
        peers=['tcp://a:1'],
    )
    try:
        parsed = tomllib.loads(weird.to_toml())
        ok = (
            parsed["hostname"] == "换行\n名字"
            and parsed["network_identity"]["network_name"] == 'a"b'
            and parsed["network_identity"]["network_secret"] == "c\\d"
        )
    except Exception as exc:
        ok = False
        print("TOML quote error:", exc)
    check("TOML 特殊字符转义", ok)

    check("公共节点固定 9 个", len(PUBLIC_NODES) == 9, str(len(PUBLIC_NODES)))
    check(
        "公共节点地址有效",
        all(
            node.url.startswith(("tcp://", "udp://", "wss://", "https://"))
            for node in PUBLIC_NODES
        ),
    )
    check("全部节点选择", len(select_nodes()) == len(PUBLIC_NODES))
    check(
        "指定节点选择",
        select_nodes(["sh-ct"]) == ["tcp://225284.xyz:11010"],
        str(select_nodes(["sh-ct"])),
    )
    check("未知节点回退全部", len(select_nodes(["nope"])) == len(PUBLIC_NODES))


# ----------------------------------------------------------------------
# 玩家资料与 CLI JSON


def test_profile_and_json() -> None:
    profile = PlayerProfile(
        name="猫猫",
        machine_id="a" * 32,
        vendor="Darkitten",
        kind="host",
        uuid="b" * 32,
        game_name="Cat",
        latency_ms=12.34,
    )
    data = profile.to_json(True)
    check("资料 kind 大写", data["kind"] == "HOST")
    check("资料延迟四舍五入", data["latency_ms"] == 12.3)
    check("资料可选字段", data["uuid"] == "b" * 32 and data["game_name"] == "Cat")

    back = PlayerProfile.from_json(data, None)
    check("资料往返", back is not None and back.name == "猫猫" and back.kind == "HOST")
    check("资料拒绝空 machine_id", PlayerProfile.from_json(
        {"name": "a", "machine_id": ""}, None
    ) is None)
    check("资料拒绝未知 kind", PlayerProfile.from_json(
        {"name": "a", "machine_id": "x", "kind": "ADMIN"}, None
    ) is None)
    check("资料强制 kind", PlayerProfile.from_json(
        {"name": "a", "machine_id": "x"}, "GUEST"
    ).kind == "GUEST")
    check("资料非法 uuid 清空", PlayerProfile.from_json(
        {"name": "a", "machine_id": "x", "kind": "GUEST", "uuid": "zz"}
    ).uuid == "")
    check("资料超长游戏名清空", PlayerProfile.from_json(
        {"name": "a", "machine_id": "x", "kind": "GUEST", "game_name": "x" * 17}
    ).game_name == "")
    check("资料延迟下限", PlayerProfile.from_json(
        {"name": "a", "machine_id": "x", "kind": "GUEST", "latency_ms": -1}
    ).latency_ms is None)
    check("资料延迟上限", PlayerProfile.from_json(
        {"name": "a", "machine_id": "x", "kind": "GUEST", "latency_ms": 60001}
    ).latency_ms is None)
    check("资料不是字典", PlayerProfile.from_json("x", None) is None)

    check(
        "CLI JSON 前缀日志",
        _extract_json('WARN xx\n[{"hostname": "a"}]\n') == [{"hostname": "a"}],
    )
    check("CLI JSON 对象", _extract_json('{"a": 1}') == {"a": 1})
    check("CLI JSON 空", _extract_json("") is None)
    check("CLI JSON 无效", _extract_json("hello") is None)

    peer = EasyTierPeer.from_json(
        {
            "hostname": "scaffolding-mc-server-25565",
            "ipv4": "10.144.144.1",
            "cost": "p2p",
            "lat_ms": "31.5",
            "tunnel_proto": "tcp",
            "nat_type": "PortRestricted",
        }
    )
    check("peer 字段", peer.hostname.endswith("25565") and peer.ipv4 == "10.144.144.1")
    check("peer 直连", peer.is_direct and peer.is_member and not peer.is_local)
    check("peer 延迟", peer.latency_ms == 31.5)
    local = EasyTierPeer.from_json({"cost": "Local", "ipv4": "10.144.144.1"})
    check("peer 本机", local.is_local and local.is_member)
    relay = EasyTierPeer.from_json({"cost": "relay", "ipv4": "10.144.144.2"})
    check("peer 中继", relay.is_member and not relay.is_direct)


# ----------------------------------------------------------------------
# 协议分帧


def test_framing() -> None:
    left, right = socket.socketpair()
    try:
        _write_request(left, "c:ping", b"abc")
        kind, body = _read_request(right)
        check("请求类型", kind == "c:ping", kind)
        check("请求体", body == b"abc")
        _write_response(right, 0, b"ok")
        status, response = _read_response(left)
        check("响应状态", status == 0)
        check("响应体", response == b"ok")

        right.sendall(bytes([255]) + (2).to_bytes(4, "big") + b"xx")
        status, response = _read_response(left)
        check("错误响应", status == 255 and response == b"xx")

        # 超长包必须在读正文前拒绝，用独立连接避免影响上面的收发状态。
        a, b = socket.socketpair()
        try:
            b.sendall(
                bytes([1]) + b"x" + (et.ROOM_MAX_BODY + 1).to_bytes(4, "big")
            )
            try:
                _read_request(a)
                ok = False
            except et.RoomProtocolError:
                ok = True
            check("拒绝超大请求", ok)

            b.sendall(bytes([0]) + (et.ROOM_MAX_BODY + 1).to_bytes(4, "big"))
            try:
                _read_response(a)
                ok = False
            except et.RoomProtocolError:
                ok = True
            check("拒绝超大响应", ok)
        finally:
            a.close()
            b.close()
        try:
            _write_request(left, "x" * 256)
            ok = False
        except et.RoomProtocolError:
            ok = True
        check("拒绝超长请求类型", ok)
    finally:
        left.close()
        right.close()


# ----------------------------------------------------------------------
# 房间服务


def test_room_server() -> None:
    host = make_profile("房主猫", "h" * 32, "HOST")
    server = RoomServer(host, 25565)
    client = None
    try:
        check("房间服务端口", 0 < server.port < 65536, str(server.port))
        check("房间服务初始玩家", [p.kind for p in server.players()] == ["HOST"])

        client = RoomClient.try_connect(server.port)
        check("客户端指纹校验", client is not None)
        if client is None:
            return

        status, body = client.request(et.ROOM_PING, et.ROOM_FINGERPRINT)
        check("ping 回显", status == 0 and body == et.ROOM_FINGERPRINT)
        status, body = client.request(et.ROOM_PROTOCOLS)
        check(
            "协议清单",
            status == 0 and b"c:player_ping" in body and b"campfire:mods" in body,
        )
        check("读取游戏端口", client.server_port() == 25565)

        guest = make_profile("访客狗", "g" * 32, "GUEST")
        client.heartbeat(guest)
        players = client.players()
        check(
            "玩家列表",
            [(p.name, p.kind) for p in players] == [("房主猫", "HOST"), ("访客狗", "GUEST")],
            str([(p.name, p.kind) for p in players]),
        )

        guest.name = "访客猫"
        guest.latency_ms = 42.0
        client.heartbeat(guest)
        players = client.players()
        check("心跳更新资料", players[-1].name == "访客猫" and players[-1].latency_ms == 42.0)
        check("机器码不重复", len(players) == 2, str(len(players)))

        status, body = client.request(
            et.ROOM_PLAYER_PING, json.dumps(host.to_json(False)).encode()
        )
        check(
            "拒绝房主机码",
            status == 255 and b"host" in body,
            f"{status} {body[:60]!r}",
        )

        status, body = client.request(et.ROOM_MOD_LIST)
        check(
            "Mod 清单未开启",
            status == 255 and body == et.ROOM_MODS_OFF_REPLY.encode(),
        )
        status, body = client.request(et.ROOM_MOD_FILE)
        check(
            "拒绝 Mod 文件",
            status == 255 and body == et.ROOM_MOD_FILE_REFUSED_REPLY.encode(),
        )
        status, body = client.request("c:unknown")
        check("未知协议", status == 255 and "尚未实现".encode() in body)

        status, body = client.request(et.ROOM_PLAYER_PING, b"not json")
        check("拒绝损坏的资料", status == 255 and b"Invalid" in body)

        status, body = client.request(et.ROOM_PLAYER_PING, json.dumps(
            {"name": "x", "machine_id": "y", "kind": "GUEST"}
        ).encode())
        check("接受无 kind 的心跳", status == 0)

        status, body = client.request(et.ROOM_SERVER_PORT)
        check("端口响应固定两字节", status == 0 and len(body) == 2)

        # 超时清理由后台线程负责，这里直接改时间戳验证逻辑。
        with server._lock:
            server._guests = [
                (profile, time.monotonic() - et.ROOM_GUEST_TIMEOUT - 1)
                for profile, _ in server._guests
            ]
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline and len(server.players()) > 1:
            time.sleep(0.2)
        check("过期访客被清理", len(server.players()) == 1, str(len(server.players())))
    finally:
        if client is not None:
            client.close()
        server.stop()


# ----------------------------------------------------------------------
# 配置持久化


def test_config() -> None:
    temp = tempfile.TemporaryDirectory(prefix="dkl_et_cfg_")
    original = config_module.CONFIG_PATH
    try:
        path = Path(temp.name) / "config.json"
        config_module.CONFIG_PATH = path
        path.write_text(
            json.dumps(
                {
                    "easytier_machine_id": "",
                    "easytier_direct_only": 1,
                    "easytier_hide_address": 0,
                    "easytier_last_port": 70000,
                    "easytier_node_ids": ["sh-ct", "  ", 3],
                }
            ),
            encoding="utf-8",
        )
        cfg = Config.load()
        check("机器码自动生成", len(cfg.easytier_machine_id) == 32, cfg.easytier_machine_id)
        check("直连开关", cfg.easytier_direct_only is True)
        check("隐藏地址开关", cfg.easytier_hide_address is False)
        check("端口上限", cfg.easytier_last_port == 65535, str(cfg.easytier_last_port))
        check("节点过滤", cfg.easytier_node_ids == ["sh-ct", "3"], str(cfg.easytier_node_ids))

        cfg.easytier_machine_id = "f" * 32
        cfg.easytier_last_port = 0
        cfg.save()
        cfg2 = Config.load()
        check("机器码保存", cfg2.easytier_machine_id == "f" * 32)
        check("端口下限", cfg2.easytier_last_port == 1, str(cfg2.easytier_last_port))
        data = json.loads(path.read_text(encoding="utf-8"))
        check("配置写入磁盘", data["easytier_machine_id"] == "f" * 32)

        path.write_text(json.dumps({"easytier_last_port": "bad"}), encoding="utf-8")
        cfg3 = Config.load()
        check("端口非法回退", cfg3.easytier_last_port == 25565, str(cfg3.easytier_last_port))
    finally:
        config_module.CONFIG_PATH = original
        temp.cleanup()


# ----------------------------------------------------------------------
# 组件与 Worker


def test_package_and_workers() -> None:
    install_dir = EasyTierPackage.install_dir()
    check("组件目录", install_dir.name.startswith(et.EASYTIER_VERSION + "-"), str(install_dir))
    check("核心文件名", install_dir.name and et.EASYTIER_CORE.endswith(".exe"))
    check(
        "哈希长度",
        len(et.EASYTIER_HASHES["x86_64"][et.EASYTIER_CORE]) == 64
        and all(
            len(value) == 64
            for arch in et.EASYTIER_HASHES.values()
            for value in arch.values()
        ),
    )
    check("下载源", len(EasyTierPackage.source_urls()) >= 2, str(len(EasyTierPackage.source_urls())))
    if EasyTierPackage.is_installed():
        result = subprocess.run(
            [str(install_dir / et.EASYTIER_CORE), "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        check(
            "已安装组件版本",
            et.EASYTIER_VERSION in (result.stdout or ""),
            (result.stdout or result.stderr or "").strip()[:80],
        )
    else:
        print("组件未安装，跳过本机版本检查（首次开房会自动下载）。")

    host_worker = EasyTierHostWorker(
        25565,
        "HostCat",
        machine_id="a" * 32,
        node_ids=["sh-ct"],
        direct_only=True,
        hide_address=True,
    )
    check("房主 Worker 参数", host_worker.game_port == 25565 and host_worker.direct_only)
    check("房主 Worker 信号", hasattr(host_worker, "ready") and hasattr(host_worker, "failed"))
    host_worker.cancel()
    check("房主 Worker 取消", host_worker.cancel_event.is_set())

    code = RoomCode.create()
    join_worker = EasyTierJoinWorker(
        code,
        "GuestDog",
        machine_id="b" * 32,
        node_ids=[],
        allow_relay=False,
        hide_address=True,
        search_timeout=3.0,
    )
    check("访客 Worker 参数", join_worker.code.value == code.value and join_worker.search_timeout == 3.0)
    check("访客 Worker 信号", hasattr(join_worker, "progress") and hasattr(join_worker, "cancelled"))
    join_worker.cancel()
    check("访客 Worker 取消", join_worker.cancel_event.is_set())


# ----------------------------------------------------------------------
# 界面


def test_ui() -> None:
    app = QApplication.instance() or QApplication([])
    from darkitten.ui.main_window import MainWindow

    temp = tempfile.TemporaryDirectory(prefix="dkl_et_ui_")
    original_save = Config.save
    Config.save = lambda self: None
    try:
        game = Path(temp.name) / "game"
        version_dir = game / "versions" / "1.21.1"
        version_dir.mkdir(parents=True)
        (version_dir / "1.21.1.json").write_text(
            json.dumps(
                {
                    "id": "1.21.1",
                    "type": "release",
                    "mainClass": "net.minecraft.client.main.Main",
                    "libraries": [],
                }
            ),
            encoding="utf-8",
        )
        config = Config(
            game_dir=str(game),
            update_auto_check=False,
            easytier_last_port=25599,
            easytier_direct_only=True,
            easytier_hide_address=False,
        )
        window = MainWindow(config)
        try:
            page = window.room_page
            panel = page.easytier_panel
            check("联机页双模式", page.mode_tabs is not None and page.mode_stack.count() == 2)
            check("默认 P2P 模式", page.mode_tabs.current() == "p2p" and page.mode_stack.currentIndex() == 0)
            page.mode_tabs.set_value("sakura", emit=True)
            app.processEvents()
            check("切换到樱花模式", page.mode_stack.currentIndex() == 1)
            page.mode_tabs.set_value("p2p", emit=True)
            app.processEvents()
            check("切回 P2P 模式", page.mode_stack.currentIndex() == 0)

            check("端口恢复", panel.port_spin.value() == 25599, str(panel.port_spin.value()))
            check("直连开关恢复", panel.direct_switch.isChecked() is True)
            check("隐藏开关恢复", panel.hide_switch.isChecked() is False)
            check(
                "版本列表",
                panel.version_combo.count() >= 1 and panel.version_combo.currentData() == "1.21.1",
                str(panel.version_combo.currentData()),
            )

            code = RoomCode.create()
            panel.code_edit.setText(f"一起玩 {code.code} 速来")
            app.processEvents()
            check("界面识别房间号", panel.code_edit.text() == code.code, panel.code_edit.text())
            check("房间号提示", code.code in panel.code_hint.text(), panel.code_hint.text())

            panel.code_edit.setText("U/0000-0000-0000-0001")
            panel.join_room()
            app.processEvents()
            check("拒绝无效房间号加入", "有效" in panel.guest_status.text(), panel.guest_status.text())
            check("加入按钮可用", panel.join_btn.isEnabled())
            check("无效房间号不启动任务", panel._join_worker is None and not panel._busy)

            panel.code_edit.setText(code.code)
            panel._update_code_hint()
            check("有效房间号提示", "将加入房间" in panel.code_hint.text())
        finally:
            window.close()
            app.processEvents()
    finally:
        Config.save = original_save
        temp.cleanup()


# ----------------------------------------------------------------------


def main() -> int:
    test_room_code()
    test_toml()
    test_profile_and_json()
    test_framing()
    test_room_server()
    test_config()
    test_package_and_workers()
    test_ui()
    print(f"\nEASYTIER QA: {PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
