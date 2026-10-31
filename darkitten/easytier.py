"""Campfire 风格的 EasyTier P2P 联机支持。

和樱花穿透不同，这里不需要任何账户：房主用 EasyTier 组建一个临时的
点对点虚拟网络，把房间号（同时包含网络名和密钥）发给朋友，朋友加入后
通过本机端口转发连接到房主的局域网世界。

协议与 Campfire（GPL-3.0）保持一致，房间号、虚拟主机名、房间服务
（c:ping / c:server_port / c:player_ping / c:player_profiles_list）都可以
和 Campfire 客户端互通。EasyTier 以 no_tun 模式运行，不修改系统路由表。
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import secrets
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid as _uuid
import zipfile
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from .config import APP_DIR, APP_VERSION

# ----------------------------------------------------------------------
# 常量

EASYTIER_VERSION = "2.6.4"
EASYTIER_CORE = "easytier-core.exe"
EASYTIER_CLI = "easytier-cli.exe"
EASYTIER_PACKET = "Packet.dll"
EASYTIER_FILES = (EASYTIER_CORE, EASYTIER_CLI, EASYTIER_PACKET)

EASYTIER_HASHES: dict[str, dict[str, str]] = {
    "x86_64": {
        EASYTIER_CORE: "da7eb2d24b5416f3d3407636949e964a0750e3f9dc53a828cb6799a57ead445d",
        EASYTIER_CLI: "d8783e851e944b44a9b71b39fd02f227ec0a2a82b3165c55ead5dd32dcde53a1",
        EASYTIER_PACKET: "c7c03a87eac7243ccbe331554624b18803010b740e311fc8cfddb573096eacac",
    },
    "arm64": {
        EASYTIER_CORE: "da912e2befd4c59c81228c0dac7e19f0190aa4221475117108ad2f0045b3065d",
        EASYTIER_CLI: "295852b6d68ab5c675437f19f37e975e90d41814ec76eabc135d3eb367bfccd0",
        EASYTIER_PACKET: "fe56bf638c1f05751a4f36377d71f48fb7f43446e92475eda32dc0f4f2ebd60c",
    },
    "i686": {
        EASYTIER_CORE: "657400c756aa4e2868b9505f669527733ba872dff3b8877cee24d7772616c76b",
        EASYTIER_CLI: "e1d6a617e4cb3b9bf77c9d32616dace5d5715fabcbcb0f5f9969a98f3626d69a",
        EASYTIER_PACKET: "f0ab4f5457bd48958b72d6849148e1ece6dbc1059435dfe695051fc32b8cdc19",
    },
}

ROOM_CODE_ALPHABET = "0123456789ABCDEFGHJKLMNPQRSTUVWXYZ"
ROOM_CODE_DIGITS = 16
ROOM_CODE_CHECKSUM = 7
ROOM_CODE_PREFIX = "U/"
NETWORK_PREFIX = "scaffolding-mc-"
HOST_NAME_PREFIX = "scaffolding-mc-server-"
HOST_ADDRESS = "10.144.144.1"
ROOM_SERVICE_PORT = 13448

# 房主用这个固定内容确认对面真的是房间服务，而不是恰好占用了端口的程序。
ROOM_FINGERPRINT = bytes(
    (0x41, 0x57, 0x48, 0x44, 0x86, 0x37, 0x40, 0x59,
     0x57, 0x44, 0x92, 0x43, 0x96, 0x99, 0x85, 0x01)
)

ROOM_PING = "c:ping"
ROOM_PROTOCOLS = "c:protocols"
ROOM_SERVER_PORT = "c:server_port"
ROOM_PLAYER_PING = "c:player_ping"
ROOM_PLAYER_LIST = "c:player_profiles_list"
ROOM_MOD_LIST = "campfire:mods"
ROOM_MOD_FILE = "campfire:mod_file"

ROOM_REMOVED_REPLY = "campfire:removed"
ROOM_BLOCKED_REPLY = "campfire:blocked"
ROOM_MODS_OFF_REPLY = "campfire:mods_off"
ROOM_MOD_FILE_REFUSED_REPLY = "campfire:mod_file_refused"

ROOM_STATUS_OK = 0
ROOM_STATUS_ERROR = 255
ROOM_MAX_BODY = 1024 * 1024
ROOM_GUEST_TIMEOUT = 10.0
ROOM_IDLE_TIMEOUT = 64.0
ROOM_REQUEST_TIMEOUT = 64.0

_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


class EasyTierError(Exception):
    """联机组件或网络操作失败，消息可以直接展示给用户。"""


class EasyTierCancelled(EasyTierError):
    """用户取消了正在进行的联机操作。"""


# ----------------------------------------------------------------------
# 公共节点


@dataclass(frozen=True)
class PublicNode:
    id: str
    url: str
    provider: str
    limited: bool = False


# 与 Campfire 内置节点保持一致；玩家只有在至少一个共同节点上才能相遇。
PUBLIC_NODES: tuple[PublicNode, ...] = (
    PublicNode("sh-ct", "tcp://225284.xyz:11010", "海波"),
    PublicNode("xm-ct", "tcp://easytier.weiai.org.cn:11010", "为爱，唯爱"),
    PublicNode("cq-cm", "tcp://183.230.36.171:11010", "Just the Two of Us"),
    PublicNode("zz-bgp", "udp://et.basd1.de:11010", "宇智波.希"),
    PublicNode("sh-bgp", "tcp://et.sumnight.top:11010", "夏夜", limited=True),
    PublicNode("cn-eo", "wss://et.chinokou.cn", "ChinoKou"),
    PublicNode("yn-cu", "https://et-public.lctn.top", "凉城听暖"),
    PublicNode("intl-cf", "wss://et.xn--6kr850c.com", "林然"),
    PublicNode("intl-wk", "https://etnode.zkitefly.eu.org/node2", "zkitefly"),
)


def select_nodes(node_ids: Iterable[str] | None = None) -> list[str]:
    """返回选中的节点 URI；未知或空选择时使用全部内置节点。"""
    wanted = {str(item) for item in (node_ids or []) if str(item)}
    nodes = [node for node in PUBLIC_NODES if not wanted or node.id in wanted]
    return [node.url for node in (nodes or PUBLIC_NODES)]


# ----------------------------------------------------------------------
# 房间号


class RoomCode:
    """``U/XXXX-XXXX-XXXX-XXXX`` 形式的 Campfire 房间号。

    16 个字符是同一个大整数按 34 进制、低位在前的数字，并且整个数能被
    7 整除，用来挡住大部分输错。前 8 位是网络名，后 8 位是网络密钥。
    """

    _range = len(ROOM_CODE_ALPHABET) ** ROOM_CODE_DIGITS

    def __init__(self, value: int) -> None:
        value = int(value)
        if value < 0 or value >= self._range or value % ROOM_CODE_CHECKSUM:
            raise ValueError("房间号校验失败。")
        self.value = value
        digits: list[str] = []
        remaining = value
        for _ in range(ROOM_CODE_DIGITS):
            digits.append(ROOM_CODE_ALPHABET[remaining % len(ROOM_CODE_ALPHABET)])
            remaining //= len(ROOM_CODE_ALPHABET)
        self.digits = "".join(digits)
        self.code = ROOM_CODE_PREFIX + "-".join(
            self.digits[index:index + 4] for index in range(0, 16, 4)
        )
        self.network_name = (
            NETWORK_PREFIX + self.digits[0:4] + "-" + self.digits[4:8]
        )
        self.network_secret = self.digits[8:12] + "-" + self.digits[12:16]

    def __str__(self) -> str:
        return self.code

    def __repr__(self) -> str:
        return f"RoomCode({self.code!r})"

    @classmethod
    def create(cls) -> "RoomCode":
        value = int.from_bytes(secrets.token_bytes(16), "little") % cls._range
        return cls(value - value % ROOM_CODE_CHECKSUM)

    @classmethod
    def parse(cls, text: str) -> "RoomCode":
        found = cls.find(text)
        if found is None:
            raise ValueError("房间号无效，请检查是否输入完整。")
        return found

    @classmethod
    def find(cls, text: str) -> "RoomCode | None":
        """从聊天消息里找出第一个有效房间号，大小写不敏感。"""
        if not text:
            return None
        upper = str(text).upper()
        for start in range(0, max(0, len(upper) - 19)):
            if upper[start:start + 2] != ROOM_CODE_PREFIX:
                continue
            value = cls._try_decode(upper, start + 2)
            if value is not None:
                return cls(value)
        return None

    @classmethod
    def _try_decode(cls, text: str, offset: int) -> int | None:
        if len(text) < offset + 19:
            return None
        value = 0
        # 房间号最后一位是最高位，所以从后往前累加。
        for index in range(18, -1, -1):
            char = text[offset + index]
            if index in (4, 9, 14):
                if char != "-":
                    return None
                continue
            if char == "I":
                char = "1"
            elif char == "O":
                char = "0"
            digit = ROOM_CODE_ALPHABET.find(char)
            if digit < 0:
                return None
            value = value * len(ROOM_CODE_ALPHABET) + digit
        return value if value % ROOM_CODE_CHECKSUM == 0 else None


# ----------------------------------------------------------------------
# TOML 配置


def _toml_quote(value: str) -> str:
    out = ['"']
    for char in str(value or ""):
        if char in ('"', "\\"):
            out.append("\\" + char)
        elif ord(char) < 0x20:
            out.append(f"\\u{ord(char):04X}")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def _toml_array(values: Iterable[str]) -> str:
    return "[" + ", ".join(_toml_quote(item) for item in values) + "]"


@dataclass
class EasyTierConfig:
    network_name: str
    network_secret: str
    hostname: str
    ipv4: str | None = None
    peers: list[str] = field(default_factory=list)
    tcp_whitelist: list[str] = field(default_factory=list)
    udp_whitelist: list[str] = field(default_factory=list)
    direct_only: bool = False
    hide_address: bool = False
    windows7: bool = False

    def to_toml(self) -> str:
        lines: list[str] = [
            "hostname = " + _toml_quote(self.hostname),
        ]
        if self.ipv4:
            lines.append("ipv4 = " + _toml_quote(self.ipv4))
        else:
            lines.append("dhcp = true")
        lines.append(
            "listeners = []"
            if self.hide_address
            else 'listeners = ["tcp://0.0.0.0:0", "udp://0.0.0.0:0"]'
        )
        lines.append("tcp_whitelist = " + _toml_array(self.tcp_whitelist))
        lines.append("udp_whitelist = " + _toml_array(self.udp_whitelist))
        lines.append("")
        lines.append("[network_identity]")
        lines.append("network_name = " + _toml_quote(self.network_name))
        lines.append("network_secret = " + _toml_quote(self.network_secret))
        lines.append("")
        for peer in self.peers:
            lines.append("[[peer]]")
            lines.append("uri = " + _toml_quote(peer))
            lines.append("")
        lines.append("[flags]")
        lines.append("no_tun = true")
        lines.append("latency_first = true")
        lines.append("enable_kcp_proxy = true")
        if self.windows7:
            lines.append("enable_quic_proxy = false")
            lines.append("disable_quic_input = true")
        else:
            lines.append("enable_quic_proxy = true")
        lines.append("data_compress_algo = 2")
        lines.append("private_mode = true")
        lines.append(
            "p2p_only = "
            + ("true" if self.direct_only and not self.hide_address else "false")
        )
        if self.hide_address:
            lines.append("disable_p2p = true")
            lines.append("disable_udp_hole_punching = true")
            lines.append("disable_tcp_hole_punching = true")
            lines.append("disable_sym_hole_punching = true")
            lines.append("disable_upnp = true")
        return "\n".join(lines) + "\n"


def host_config(
    code: RoomCode,
    service_port: int,
    game_port: int,
    peers: Iterable[str] | None = None,
    direct_only: bool = False,
    hide_address: bool = False,
) -> EasyTierConfig:
    return EasyTierConfig(
        network_name=code.network_name,
        network_secret=code.network_secret,
        hostname=f"{HOST_NAME_PREFIX}{int(service_port)}",
        ipv4=HOST_ADDRESS,
        peers=list(peers if peers is not None else select_nodes()),
        tcp_whitelist=[str(int(service_port)), str(int(game_port))],
        udp_whitelist=[str(int(game_port))],
        direct_only=bool(direct_only),
        hide_address=bool(hide_address),
    )


def guest_config(
    code: RoomCode,
    peers: Iterable[str] | None = None,
    direct_only: bool = False,
    hide_address: bool = False,
) -> EasyTierConfig:
    return EasyTierConfig(
        network_name=code.network_name,
        network_secret=code.network_secret,
        hostname="darkitten-" + secrets.token_hex(4),
        ipv4=None,
        peers=list(peers if peers is not None else select_nodes()),
        tcp_whitelist=["0"],
        udp_whitelist=["0"],
        direct_only=bool(direct_only),
        hide_address=bool(hide_address),
    )


# ----------------------------------------------------------------------
# 组件下载


def detect_arch() -> str:
    machine = (platform.machine() or "").lower()
    if "arm64" in machine or "aarch64" in machine:
        return "arm64"
    if sys.platform == "win32" and struct.calcsize("P") * 8 >= 64:
        return "x86_64"
    return "i686" if struct.calcsize("P") * 8 < 64 else "x86_64"


def sha256_file(path: Path | str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class EasyTierPackage:
    """下载、校验并定位 EasyTier 2.6.4 的 core / cli / Packet.dll。"""

    version = EASYTIER_VERSION
    _lock = threading.Lock()
    _verified: bool | None = None

    @classmethod
    def install_dir(cls, arch: str | None = None) -> Path:
        return APP_DIR / "easytier" / f"{cls.version}-{arch or detect_arch()}"

    @classmethod
    def core_path(cls, arch: str | None = None) -> Path:
        return cls.install_dir(arch) / EASYTIER_CORE

    @classmethod
    def cli_path(cls, arch: str | None = None) -> Path:
        return cls.install_dir(arch) / EASYTIER_CLI

    @classmethod
    def expected_hashes(cls, arch: str | None = None) -> dict[str, str]:
        return EASYTIER_HASHES.get(arch or detect_arch(), EASYTIER_HASHES["x86_64"])

    @classmethod
    def is_installed(cls) -> bool:
        if cls._verified is not None:
            return cls._verified
        arch = detect_arch()
        hashes = cls.expected_hashes(arch)
        directory = cls.install_dir(arch)
        ok = True
        for name in EASYTIER_FILES:
            path = directory / name
            try:
                if not path.is_file() or sha256_file(path) != hashes[name]:
                    ok = False
                    break
            except OSError:
                ok = False
                break
        cls._verified = ok
        return ok

    @classmethod
    def source_urls(cls, arch: str | None = None) -> list[str]:
        arch = arch or detect_arch()
        asset = f"easytier-windows-{arch}-v{cls.version}.zip"
        github = (
            f"https://github.com/EasyTier/EasyTier/releases/download/"
            f"v{cls.version}/{asset}"
        )
        return [
            f"https://gitee.com/easytier/EasyTier/releases/download/v{cls.version}/{asset}",
            github,
            "https://ghfast.top/" + github,
            "https://gh-proxy.com/" + github,
        ]

    @classmethod
    def install(
        cls,
        progress: Callable[[int, str], None] | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> Path:
        """安装或修复组件，返回安装目录。"""
        with cls._lock:
            if cls.is_installed():
                return cls.install_dir()

            arch = detect_arch()
            directory = cls.install_dir(arch)
            directory.mkdir(parents=True, exist_ok=True)
            hashes = cls.expected_hashes(arch)

            def report(percent: int, message: str) -> None:
                if progress is not None:
                    progress(max(0, min(100, int(percent))), message)

            report(1, "正在准备 EasyTier 联机组件…")
            errors: list[str] = []
            for index, url in enumerate(cls.source_urls(arch), start=1):
                if cancel is not None and cancel():
                    raise EasyTierCancelled("已取消联机组件下载。")
                report(3, f"正在连接下载源 {index}/{len(cls.source_urls(arch))}…")
                try:
                    cls._install_from_archive(
                        url, directory, hashes, progress, cancel
                    )
                    cls._verified = True
                    report(100, "联机组件已就绪。")
                    return directory
                except EasyTierCancelled:
                    raise
                except Exception as exc:
                    errors.append(f"{url}：{exc}")
                    continue
            cls._verified = False
            detail = "；".join(errors[-2:]) if errors else "没有可用下载源"
            raise EasyTierError(
                "联机组件下载失败，请检查网络或代理后重试。"
                f"（{detail}）"
            )

    @classmethod
    def _install_from_archive(
        cls,
        url: str,
        directory: Path,
        hashes: dict[str, str],
        progress: Callable[[int, str], None] | None,
        cancel: Callable[[], bool] | None,
    ) -> None:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": f"DarkittenLauncher/{APP_VERSION}",
                "Accept": "application/octet-stream,*/*",
            },
        )
        archive_path = directory / "download.zip.tmp"
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                total = int(response.headers.get("Content-Length") or 0)
                received = 0
                with open(archive_path, "wb") as handle:
                    while True:
                        if cancel is not None and cancel():
                            raise EasyTierCancelled("已取消联机组件下载。")
                        chunk = response.read(64 * 1024)
                        if not chunk:
                            break
                        handle.write(chunk)
                        received += len(chunk)
                        if progress is not None:
                            percent = 5 + (received * 70 // total if total else 0)
                            progress(
                                min(75, percent),
                                f"正在下载联机组件 {received // 1024 // 1024} MB"
                                + (f" / {total // 1024 // 1024} MB" if total else ""),
                            )
            if cancel is not None and cancel():
                raise EasyTierCancelled("已取消联机组件下载。")

            if progress is not None:
                progress(80, "正在校验联机组件…")
            with zipfile.ZipFile(archive_path) as archive:
                entries: dict[str, zipfile.ZipInfo] = {}
                for info in archive.infolist():
                    name = info.filename.replace("\\", "/").rsplit("/", 1)[-1]
                    if name in EASYTIER_FILES and not info.is_dir():
                        entries.setdefault(name, info)
                missing = [name for name in EASYTIER_FILES if name not in entries]
                if missing:
                    raise EasyTierError("压缩包缺少文件：" + "、".join(missing))

                written: list[tuple[Path, Path]] = []
                try:
                    for index, name in enumerate(EASYTIER_FILES):
                        if cancel is not None and cancel():
                            raise EasyTierCancelled("已取消联机组件下载。")
                        temp = directory / (name + ".tmp")
                        with archive.open(entries[name]) as source, open(
                            temp, "wb"
                        ) as target:
                            for chunk in iter(lambda: source.read(256 * 1024), b""):
                                target.write(chunk)
                        digest = sha256_file(temp)
                        if digest.lower() != hashes[name].lower():
                            raise EasyTierError(
                                f"{name} 校验失败，下载源可能被篡改或文件损坏。"
                            )
                        written.append((temp, directory / name))
                        if progress is not None:
                            progress(
                                80 + (index + 1) * 18 // len(EASYTIER_FILES),
                                f"正在校验 {name}…",
                            )
                    for temp, target in written:
                        os.replace(temp, target)
                finally:
                    for temp in directory.glob("*.tmp"):
                        try:
                            temp.unlink()
                        except OSError:
                            pass
        finally:
            try:
                archive_path.unlink()
            except OSError:
                pass


# ----------------------------------------------------------------------
# 进程和命令行


def find_free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def is_port_free(port: int, host: str = "127.0.0.1") -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, int(port)))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def probe_tcp(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _format_endpoint(host: str, port: int) -> str:
    text = str(host)
    return f"[{text}]:{int(port)}" if ":" in text else f"{text}:{int(port)}"


def _extract_json(text: str) -> Any:
    value = (text or "").lstrip("\ufeff").strip()
    if not value:
        return None
    try:
        return json.loads(value)
    except ValueError:
        pass
    starts = [index for index in (value.find("["), value.find("{")) if index >= 0]
    if not starts:
        return None
    start = min(starts)
    for end in range(len(value), start, -1):
        if value[end - 1] not in "]}":
            continue
        try:
            return json.loads(value[start:end])
        except ValueError:
            continue
    return None


@dataclass
class EasyTierPeer:
    hostname: str = ""
    ipv4: str = ""
    cost: str = ""
    latency_ms: float | None = None
    tunnel: str = ""
    nat_type: str = ""

    @property
    def is_local(self) -> bool:
        return self.cost == "Local"

    @property
    def is_direct(self) -> bool:
        return self.cost == "p2p"

    @property
    def is_member(self) -> bool:
        return bool(self.ipv4)

    @classmethod
    def from_json(cls, item: dict[str, Any]) -> "EasyTierPeer":
        latency: float | None = None
        try:
            value = float(item.get("lat_ms"))
            if 0 <= value <= 60000:
                latency = value
        except (TypeError, ValueError):
            latency = None
        return cls(
            hostname=str(item.get("hostname") or ""),
            ipv4=str(item.get("ipv4") or ""),
            cost=str(item.get("cost") or ""),
            latency_ms=latency,
            tunnel=str(item.get("tunnel_proto") or ""),
            nat_type=str(item.get("nat_type") or ""),
        )


class EasyTierInstance:
    """一个正在运行的 EasyTier 实例，通过本机 RPC 端口控制。"""

    def __init__(
        self,
        process: subprocess.Popen,
        config_path: Path,
        rpc_port: int,
        install_dir: Path,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._process = process
        self._config_path = config_path
        self.rpc_port = int(rpc_port)
        self.install_dir = Path(install_dir)
        self._log_callback = log
        self._output: deque[str] = deque(maxlen=300)
        self._output_lock = threading.Lock()
        self.last_cli_error = ""
        self._reader = threading.Thread(target=self._read_output, daemon=True)
        self._reader.start()

    # ------------------------------------------------------------------
    @property
    def cli_path(self) -> Path:
        return self.install_dir / EASYTIER_CLI

    @property
    def alive(self) -> bool:
        return self._process.poll() is None

    @property
    def exit_code(self) -> int | None:
        return self._process.poll()

    def recent_output(self, lines: int = 30) -> str:
        with self._output_lock:
            return "\n".join(list(self._output)[-lines:])

    @classmethod
    def start(
        cls,
        config: EasyTierConfig,
        log: Callable[[str], None] | None = None,
        install_dir: Path | str | None = None,
    ) -> "EasyTierInstance":
        install = Path(install_dir) if install_dir else EasyTierPackage.install_dir()
        core = install / EASYTIER_CORE
        cli = install / EASYTIER_CLI
        if not core.is_file() or not cli.is_file():
            raise EasyTierError("联机组件还没有安装完成。")
        runtime = APP_DIR / "easytier" / "runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        rpc_port = find_free_port()
        config_path = runtime / f"{secrets.token_hex(8)}.toml"
        config_path.write_text(config.to_toml(), encoding="utf-8")
        args = [
            str(core),
            "-c",
            str(config_path),
            "-r",
            f"127.0.0.1:{rpc_port}",
            "--multi-thread",
        ]
        creationflags = _CREATE_NO_WINDOW
        try:
            process = subprocess.Popen(
                args,
                cwd=str(runtime),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as exc:
            raise EasyTierError(f"无法启动 EasyTier：{exc}") from exc
        instance = cls(process, config_path, rpc_port, install, log)
        time.sleep(0.6)
        if not instance.alive:
            output = instance.recent_output()
            instance.stop()
            raise EasyTierError(
                "EasyTier 启动后立即退出。"
                + (f"（{output}）" if output else "请查看网络或杀毒软件设置。")
            )
        return instance

    def _read_output(self) -> None:
        stream = self._process.stdout
        if stream is None:
            return
        try:
            for line in stream:
                text = line.rstrip("\r\n")
                if not text:
                    continue
                with self._output_lock:
                    self._output.append(text)
                if self._log_callback is not None:
                    try:
                        self._log_callback(text)
                    except Exception:
                        pass
        except Exception:
            pass

    # ------------------------------------------------------------------
    def run_cli(self, *arguments: Any, timeout: float = 10.0) -> str | None:
        if not self.alive:
            self.last_cli_error = "EasyTier 已退出。"
            return None
        args = [
            str(self.cli_path),
            "-p",
            f"127.0.0.1:{self.rpc_port}",
            *[str(argument) for argument in arguments],
        ]
        try:
            result = subprocess.run(
                args,
                cwd=str(self.install_dir),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=_CREATE_NO_WINDOW,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            self.last_cli_error = "EasyTier 命令行响应超时。"
            return None
        except OSError as exc:
            self.last_cli_error = f"无法启动 EasyTier 命令行：{exc}"
            return None
        if result.returncode != 0:
            self.last_cli_error = (result.stderr or result.stdout or "").strip()
            return None
        return result.stdout or ""

    def get_peers(self, timeout: float = 10.0) -> list[EasyTierPeer]:
        output = self.run_cli("-o", "json", "peer", "list", timeout=timeout)
        if output is None:
            return []
        data = _extract_json(output)
        if not isinstance(data, list):
            return []
        peers: list[EasyTierPeer] = []
        for item in data:
            if isinstance(item, dict):
                peers.append(EasyTierPeer.from_json(item))
        return peers

    def count_connected_nodes(self, timeout: float = 10.0) -> int:
        output = self.run_cli("-o", "json", "connector", timeout=timeout)
        if output is None:
            return -1
        data = _extract_json(output)
        if not isinstance(data, list):
            return -1
        count = 0
        for item in data:
            if not isinstance(item, dict):
                continue
            try:
                if int(item.get("status") or 0) == 0:
                    count += 1
            except (TypeError, ValueError):
                continue
        return count

    def add_port_forward(
        self,
        protocol: str,
        bind_host: str,
        bind_port: int,
        target_host: str,
        target_port: int,
        attempts: int = 3,
    ) -> bool:
        bind = _format_endpoint(bind_host, bind_port)
        target = _format_endpoint(target_host, target_port)
        for attempt in range(max(1, attempts)):
            if not self.alive:
                return False
            output = self.run_cli(
                "port-forward", "add", protocol, bind, target
            )
            if output is not None:
                return True
            if attempt + 1 < attempts:
                time.sleep(0.5 + attempt)
        return False

    def stop(self) -> None:
        process = self._process
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        pass
        except OSError:
            pass
        try:
            self._config_path.unlink()
        except OSError:
            pass
        if self._log_callback is not None:
            try:
                self._log_callback("EasyTier 网络实例已停止。")
            except Exception:
                pass


# ----------------------------------------------------------------------
# 房间服务协议


class RoomProtocolError(Exception):
    pass


class RoomRejectedError(RoomProtocolError):
    def __init__(self, blocked: bool) -> None:
        super().__init__(
            "房主已把你移出房间。" if not blocked else "房主已屏蔽这台电脑。"
        )
        self.blocked = bool(blocked)


@dataclass
class PlayerProfile:
    name: str
    machine_id: str
    vendor: str = ""
    kind: str = "GUEST"
    uuid: str = ""
    game_name: str = ""
    latency_ms: float | None = None

    def to_json(self, include_kind: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": self.name,
            "machine_id": self.machine_id,
            "vendor": self.vendor,
        }
        if include_kind:
            data["kind"] = "HOST" if self.kind.upper() == "HOST" else "GUEST"
        if self.uuid:
            data["uuid"] = self.uuid
        if self.game_name:
            data["game_name"] = self.game_name
        if self.latency_ms is not None:
            data["latency_ms"] = round(float(self.latency_ms), 1)
        return data

    @classmethod
    def from_json(
        cls, value: Any, kind_override: str | None = None
    ) -> "PlayerProfile | None":
        if not isinstance(value, dict):
            return None
        name = value.get("name")
        machine_id = value.get("machine_id")
        if not isinstance(name, str) or not isinstance(machine_id, str):
            return None
        if not machine_id:
            return None
        if kind_override:
            kind = kind_override
        else:
            kind = str(value.get("kind") or "").upper()
            if kind not in ("HOST", "GUEST"):
                return None
        profile_uuid = value.get("uuid")
        if not (
            isinstance(profile_uuid, str)
            and len(profile_uuid) == 32
            and all(char in "0123456789abcdefABCDEF" for char in profile_uuid)
        ):
            profile_uuid = ""
        game_name = value.get("game_name")
        if not isinstance(game_name, str) or not 1 <= len(game_name) <= 16:
            game_name = ""
        latency: float | None = None
        try:
            measured = float(value.get("latency_ms"))
            if 0 <= measured <= 60000:
                latency = measured
        except (TypeError, ValueError):
            latency = None
        return cls(
            name=name,
            machine_id=machine_id,
            vendor=str(value.get("vendor") or ""),
            kind=kind,
            uuid=profile_uuid,
            game_name=game_name,
            latency_ms=latency,
        )


def local_machine_id() -> str:
    """稳定的本机标识，只用于在房间里区分玩家。"""
    seed = f"{_uuid.getnode():012x}|{platform.node()}"
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:32]


def local_vendor() -> str:
    return f"Darkitten Launcher {APP_VERSION}, EasyTier v{EASYTIER_VERSION}"


def make_profile(
    name: str, machine_id: str = "", kind: str = "GUEST"
) -> PlayerProfile:
    return PlayerProfile(
        name=str(name or "Steve")[:32],
        machine_id=str(machine_id or local_machine_id()),
        vendor=local_vendor(),
        kind=kind,
    )


def _read_exact(sock: socket.socket, count: int) -> bytes:
    data = bytearray()
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise EOFError("连接已关闭。")
        data.extend(chunk)
    return bytes(data)


def _read_request(sock: socket.socket) -> tuple[str, bytes] | None:
    first = sock.recv(1)
    if not first:
        return None
    kind = _read_exact(sock, first[0]).decode("utf-8", "replace")
    length = struct.unpack(">I", _read_exact(sock, 4))[0]
    if length > ROOM_MAX_BODY:
        raise RoomProtocolError("请求体过大。")
    body = _read_exact(sock, length) if length else b""
    return kind, body


def _write_request(
    sock: socket.socket, kind: str, body: bytes = b""
) -> None:
    kind_bytes = kind.encode("utf-8")
    if len(kind_bytes) > 255:
        raise RoomProtocolError("请求类型过长。")
    sock.sendall(
        bytes([len(kind_bytes)]) + kind_bytes + struct.pack(">I", len(body)) + body
    )


def _read_response(sock: socket.socket) -> tuple[int, bytes]:
    status = _read_exact(sock, 1)[0]
    length = struct.unpack(">I", _read_exact(sock, 4))[0]
    if length > ROOM_MAX_BODY:
        raise RoomProtocolError("响应体过大。")
    body = _read_exact(sock, length) if length else b""
    return status, body


def _write_response(
    sock: socket.socket, status: int, body: bytes = b""
) -> None:
    sock.sendall(bytes([status]) + struct.pack(">I", len(body)) + body)


class RoomServer:
    """房主本机的房间服务，只监听 127.0.0.1，由虚拟网络转发进来。"""

    def __init__(self, host: PlayerProfile, game_port: int) -> None:
        self.host = host
        self.game_port = int(game_port)
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._listener.bind(("127.0.0.1", ROOM_SERVICE_PORT))
        except OSError:
            self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(16)
        self._listener.settimeout(0.5)
        self.port = int(self._listener.getsockname()[1])
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._guests: list[tuple[PlayerProfile, float]] = []
        self._clients: set[socket.socket] = set()
        self._protocol_list = "\0".join(
            (
                ROOM_PING,
                ROOM_PROTOCOLS,
                ROOM_SERVER_PORT,
                ROOM_PLAYER_PING,
                ROOM_PLAYER_LIST,
                ROOM_MOD_LIST,
                ROOM_MOD_FILE,
            )
        )
        self._accept_thread = threading.Thread(
            target=self._accept_loop, daemon=True
        )
        self._accept_thread.start()
        self._reaper = threading.Thread(target=self._reap_loop, daemon=True)
        self._reaper.start()

    # ------------------------------------------------------------------
    def players(self) -> list[PlayerProfile]:
        with self._lock:
            return [self.host] + [profile for profile, _ in self._guests]

    def stop(self) -> None:
        self._stop.set()
        try:
            self._listener.close()
        except OSError:
            pass
        with self._lock:
            clients = list(self._clients)
            self._clients.clear()
        for client in clients:
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                client.close()
            except OSError:
                pass

    # ------------------------------------------------------------------
    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                client, _ = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with self._lock:
                self._clients.add(client)
            threading.Thread(
                target=self._serve_client, args=(client,), daemon=True
            ).start()

    def _serve_client(self, client: socket.socket) -> None:
        try:
            client.settimeout(ROOM_IDLE_TIMEOUT)
            while not self._stop.is_set():
                request = _read_request(client)
                if request is None:
                    return
                kind, body = request
                status, response = self._handle(kind, body)
                _write_response(client, status, response)
        except (OSError, EOFError, RoomProtocolError):
            pass
        finally:
            with self._lock:
                self._clients.discard(client)
            try:
                client.close()
            except OSError:
                pass

    def _reap_loop(self) -> None:
        while not self._stop.wait(2.0):
            now = time.monotonic()
            with self._lock:
                self._guests = [
                    (profile, seen)
                    for profile, seen in self._guests
                    if now - seen <= ROOM_GUEST_TIMEOUT
                ]

    # ------------------------------------------------------------------
    def _handle(self, kind: str, body: bytes) -> tuple[int, bytes]:
        try:
            if kind == ROOM_PING:
                return ROOM_STATUS_OK, body
            if kind == ROOM_PROTOCOLS:
                return ROOM_STATUS_OK, self._protocol_list.encode("utf-8")
            if kind == ROOM_SERVER_PORT:
                port = max(0, min(65535, self.game_port))
                return ROOM_STATUS_OK, bytes([port >> 8, port & 0xFF])
            if kind == ROOM_PLAYER_PING:
                return self._handle_player_ping(body)
            if kind == ROOM_PLAYER_LIST:
                players = [profile.to_json(True) for profile in self.players()]
                return ROOM_STATUS_OK, json.dumps(
                    players, ensure_ascii=False
                ).encode("utf-8")
            if kind == ROOM_MOD_LIST:
                # 启动器暂不共享 Mod 文件；Campfire 客户端会显示“房主未开启”。
                return ROOM_STATUS_ERROR, ROOM_MODS_OFF_REPLY.encode("utf-8")
            if kind == ROOM_MOD_FILE:
                return ROOM_STATUS_ERROR, ROOM_MOD_FILE_REFUSED_REPLY.encode(
                    "utf-8"
                )
            return ROOM_STATUS_ERROR, "请求的协议尚未实现。".encode("utf-8")
        except Exception as exc:
            return ROOM_STATUS_ERROR, str(exc).encode("utf-8", "replace")

    def _handle_player_ping(self, body: bytes) -> tuple[int, bytes]:
        try:
            data = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return ROOM_STATUS_ERROR, b"Invalid player profile."
        profile = PlayerProfile.from_json(data, "GUEST")
        if profile is None:
            return ROOM_STATUS_ERROR, b"Invalid player profile."
        if profile.machine_id == self.host.machine_id:
            return ROOM_STATUS_ERROR, "The machine id belongs to the host.".encode()
        now = time.monotonic()
        with self._lock:
            for index, (existing, _) in enumerate(self._guests):
                if existing.machine_id == profile.machine_id:
                    self._guests[index] = (profile, now)
                    break
            else:
                self._guests.append((profile, now))
        return ROOM_STATUS_OK, b""


class RoomClient:
    """加入者连接到房主房间服务的客户端。"""

    def __init__(self, sock: socket.socket, timeout: float = ROOM_REQUEST_TIMEOUT):
        self._sock = sock
        self._timeout = timeout
        self._lock = threading.Lock()

    @classmethod
    def try_connect(
        cls, port: int, connect_timeout: float = 5.0, ping_timeout: float = 8.0
    ) -> "RoomClient | None":
        try:
            sock = socket.create_connection(
                ("127.0.0.1", int(port)), timeout=connect_timeout
            )
        except OSError:
            return None
        sock.settimeout(ping_timeout)
        client = cls(sock)
        try:
            status, body = client.request(ROOM_PING, ROOM_FINGERPRINT, ping_timeout)
        except (OSError, EOFError, RoomProtocolError, socket.timeout):
            client.close()
            return None
        if status != ROOM_STATUS_OK or body != ROOM_FINGERPRINT:
            client.close()
            return None
        sock.settimeout(ROOM_REQUEST_TIMEOUT)
        return client

    def request(
        self, kind: str, body: bytes = b"", timeout: float | None = None
    ) -> tuple[int, bytes]:
        with self._lock:
            previous = self._sock.gettimeout()
            if timeout is not None:
                self._sock.settimeout(timeout)
            try:
                _write_request(self._sock, kind, body)
                return _read_response(self._sock)
            finally:
                try:
                    self._sock.settimeout(previous)
                except OSError:
                    pass

    def server_port(self) -> int:
        status, body = self.request(ROOM_SERVER_PORT)
        if status != ROOM_STATUS_OK or len(body) != 2:
            raise RoomProtocolError(
                "房主当前没有可用的游戏端口（可能还没有对局域网开放）。"
            )
        return (body[0] << 8) | body[1]

    def heartbeat(self, profile: PlayerProfile) -> None:
        body = json.dumps(
            profile.to_json(False), ensure_ascii=False
        ).encode("utf-8")
        status, response = self.request(ROOM_PLAYER_PING, body)
        if status == ROOM_STATUS_OK:
            return
        reply = response.decode("utf-8", "replace")
        if reply == ROOM_REMOVED_REPLY:
            raise RoomRejectedError(False)
        if reply == ROOM_BLOCKED_REPLY:
            raise RoomRejectedError(True)
        raise RoomProtocolError("心跳被拒绝：" + (reply or "未知原因"))

    def players(self) -> list[PlayerProfile]:
        status, body = self.request(ROOM_PLAYER_LIST)
        if status != ROOM_STATUS_OK:
            raise RoomProtocolError("无法读取房间玩家列表。")
        try:
            data = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise RoomProtocolError("玩家列表格式错误。") from exc
        if not isinstance(data, list):
            raise RoomProtocolError("玩家列表格式错误。")
        result = []
        for item in data:
            profile = PlayerProfile.from_json(item, None)
            if profile is not None:
                result.append(profile)
        return result

    def close(self) -> None:
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass


# ----------------------------------------------------------------------
# 房间发现


def is_room_host(peer: EasyTierPeer) -> bool:
    if peer.is_local or not peer.is_member:
        return False
    if not peer.hostname.startswith(HOST_NAME_PREFIX):
        return False
    try:
        port = int(peer.hostname[len(HOST_NAME_PREFIX):])
    except ValueError:
        return False
    return 0 < port < 65536


def wait_for_host(
    instance: EasyTierInstance,
    allow_relay: bool = True,
    hide_address: bool = False,
    timeout: float = 120.0,
    progress: Callable[[str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> EasyTierPeer:
    """在虚拟网络里找到房主；必要时等待打洞或中继路径。"""
    if hide_address:
        allow_relay = True
    started = time.monotonic()
    first_seen: float | None = None
    connected_nodes = -1
    last_notice = 0.0
    while True:
        if cancel is not None and cancel():
            raise EasyTierCancelled("已取消加入房间。")
        if not instance.alive:
            detail = instance.recent_output(12)
            raise EasyTierError(
                "EasyTier 网络已退出。"
                + (f"（{detail}）" if detail else "")
            )
        peers = instance.get_peers()
        host = next((peer for peer in peers if is_room_host(peer)), None)
        now = time.monotonic()
        elapsed = now - started
        if host is not None:
            if first_seen is None:
                first_seen = now
                if progress is not None:
                    progress("已经找到房主，正在打通连接…")
            if host.is_direct or hide_address:
                return host
            if allow_relay and now - first_seen >= 3.0:
                return host
        elif elapsed >= 10 and (connected_nodes < 0 or int(elapsed) % 5 < 2):
            connected_nodes = instance.count_connected_nodes()
        if progress is not None and now - last_notice >= 5.0:
            last_notice = now
            if connected_nodes > 0:
                progress(
                    f"正在公共节点上寻找房主…已连接 {connected_nodes} 个节点"
                )
            else:
                progress("正在连接公共节点并寻找房主…")
        if elapsed > timeout:
            if host is not None:
                raise EasyTierError(
                    "找到了房主，但暂时没有打通直连路径。"
                    "请让房主允许中继，或稍后重试。"
                )
            if connected_nodes == 0:
                raise EasyTierError(
                    "没有连上任何公共节点。请检查网络，"
                    "或更换网络后重试。"
                )
            raise EasyTierError(
                "没有找到这个房间。请确认房间号正确、房主仍在房间中。"
            )
        time.sleep(1.5)


def wait_for_room_service(
    port: int, timeout: float = 30.0, cancel: Callable[[], bool] | None = None
) -> RoomClient:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cancel is not None and cancel():
            raise EasyTierCancelled("已取消加入房间。")
        client = RoomClient.try_connect(port)
        if client is not None:
            return client
        time.sleep(2.0)
    raise EasyTierError("房主的房间服务没有响应，请让房主重新创建房间。")


# ----------------------------------------------------------------------
# 会话
# ----------------------------------------------------------------------


class HostSession:
    """房主端：房间服务 + EasyTier 实例 + 后台状态刷新。"""

    def __init__(
        self,
        code: RoomCode,
        server: RoomServer,
        instance: EasyTierInstance,
    ) -> None:
        self.code = code
        self.server = server
        self.instance = instance
        self.players: list[PlayerProfile] = []
        self.connected_nodes = -1
        self.direct_peers = 0
        self.relayed_peers = 0
        self.lost = False
        self.error = ""
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._monitor, daemon=True)
        self._thread.start()

    @property
    def service_port(self) -> int:
        return self.server.port

    def _monitor(self) -> None:
        tick = 0
        while not self._stop.wait(3.0):
            tick += 1
            if not self.instance.alive:
                self.lost = True
                self.error = "EasyTier 网络已退出。"
                return
            self.players = self.server.players()
            try:
                peers = self.instance.get_peers()
            except Exception:
                continue
            members = [p for p in peers if p.is_member and not p.is_local]
            self.direct_peers = sum(1 for p in members if p.is_direct)
            self.relayed_peers = len(members) - self.direct_peers
            if tick % 2 == 0:
                nodes = self.instance.count_connected_nodes()
                if nodes >= 0:
                    self.connected_nodes = nodes

    def stop(self) -> None:
        self._stop.set()
        self.server.stop()
        self.instance.stop()


class GuestSession:
    """加入者端：EasyTier 实例 + 房间服务客户端 + 心跳。"""

    def __init__(
        self,
        code: RoomCode,
        instance: EasyTierInstance,
        client: RoomClient,
        local_port: int,
        host_name: str,
        profile: PlayerProfile,
    ) -> None:
        self.code = code
        self.instance = instance
        self.client = client
        self.local_port = int(local_port)
        self.host_name = host_name
        self.profile = profile
        self.players: list[PlayerProfile] = []
        self.host_latency_ms: float | None = None
        self.lost = False
        self.error = ""
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._thread.start()

    @property
    def address(self) -> str:
        return f"127.0.0.1:{self.local_port}"

    def _heartbeat_loop(self) -> None:
        failures = 0
        tick = 0
        while not self._stop.wait(4.0):
            tick += 1
            if not self.instance.alive:
                self.lost = True
                self.error = "EasyTier 网络已断开。"
                return
            try:
                if self.host_latency_ms is not None:
                    self.profile.latency_ms = self.host_latency_ms
                self.client.heartbeat(self.profile)
                self.players = self.client.players()
                failures = 0
            except RoomRejectedError as exc:
                self.lost = True
                self.error = str(exc)
                return
            except Exception as exc:
                failures += 1
                if failures >= 2:
                    self.lost = True
                    self.error = f"与房主的连接已断开：{exc}"
                    return
            if tick % 3 == 0:
                try:
                    peers = self.instance.get_peers()
                    host = next(
                        (p for p in peers if is_room_host(p)), None
                    )
                    if host is not None:
                        self.host_latency_ms = host.latency_ms
                except Exception:
                    pass

    def stop(self) -> None:
        self._stop.set()
        try:
            self.client.close()
        except Exception:
            pass
        self.instance.stop()


# ----------------------------------------------------------------------
# 常用操作


def create_host_session(
    game_port: int,
    player_name: str,
    machine_id: str = "",
    node_ids: Iterable[str] | None = None,
    direct_only: bool = False,
    hide_address: bool = False,
    progress: Callable[[int, str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    log: Callable[[str], None] | None = None,
) -> HostSession:
    """下载组件、建立房间服务并启动房主的虚拟网络。"""
    report = progress or (lambda percent, message: None)
    report(2, "正在检查 EasyTier 联机组件…")
    EasyTierPackage.install(report, cancel)
    if cancel is not None and cancel():
        raise EasyTierCancelled("已取消创建房间。")

    code = RoomCode.create()
    profile = make_profile(player_name, machine_id, "HOST")
    report(82, "正在启动房间服务…")
    server = RoomServer(profile, game_port)
    instance: EasyTierInstance | None = None
    try:
        config = host_config(
            code,
            server.port,
            game_port,
            peers=select_nodes(node_ids),
            direct_only=direct_only,
            hide_address=hide_address,
        )
        report(88, "正在启动 EasyTier 虚拟网络…")
        instance = EasyTierInstance.start(config, log=log)
        if cancel is not None and cancel():
            raise EasyTierCancelled("已取消创建房间。")
        report(94, "正在等待公共节点…")
        nodes = -1
        for _ in range(10):
            if cancel is not None and cancel():
                raise EasyTierCancelled("已取消创建房间。")
            nodes = instance.count_connected_nodes()
            if nodes > 0:
                break
            time.sleep(1.0)
        session = HostSession(code, server, instance)
        session.connected_nodes = max(0, nodes)
        report(100, "房间已创建。")
        return session
    except Exception:
        server.stop()
        if instance is not None:
            instance.stop()
        raise


def create_guest_session(
    code: RoomCode,
    player_name: str,
    machine_id: str = "",
    node_ids: Iterable[str] | None = None,
    allow_relay: bool = True,
    hide_address: bool = False,
    search_timeout: float = 120.0,
    progress: Callable[[int, str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    log: Callable[[str], None] | None = None,
) -> GuestSession:
    """加入房主网络、转发房间服务和游戏端口，返回可用来启动游戏的会话。"""
    report = progress or (lambda percent, message: None)
    report(2, "正在检查 EasyTier 联机组件…")
    EasyTierPackage.install(report, cancel)
    if cancel is not None and cancel():
        raise EasyTierCancelled("已取消加入房间。")

    instance: EasyTierInstance | None = None
    client: RoomClient | None = None
    try:
        config = guest_config(
            code,
            peers=select_nodes(node_ids),
            direct_only=not allow_relay,
            hide_address=hide_address,
        )
        report(68, "正在启动 EasyTier 虚拟网络…")
        instance = EasyTierInstance.start(config, log=log)
        report(72, "正在公共节点上寻找房主…")
        host = wait_for_host(
            instance,
            allow_relay=allow_relay,
            hide_address=hide_address,
            timeout=search_timeout,
            progress=lambda message: report(76, message),
            cancel=cancel,
        )
        service_port = int(host.hostname[len(HOST_NAME_PREFIX):])
        service_local = find_free_port()
        report(82, "正在转发房主的房间服务…")
        if not instance.add_port_forward(
            "tcp", "127.0.0.1", service_local, host.ipv4, service_port
        ):
            raise EasyTierError("无法建立房间服务转发，请重试。")
        client = wait_for_room_service(service_local, 30.0, cancel)
        report(86, "正在读取房主的游戏端口…")
        game_port = client.server_port()

        report(90, "正在转发游戏端口…")
        # 优先沿用房主的端口号，让依赖固定端口的 Mod 也能工作；
        # 本机端口被占用时退回系统分配的端口。
        local_port = game_port if is_port_free(game_port) else find_free_port()
        if not instance.add_port_forward(
            "tcp", "127.0.0.1", local_port, host.ipv4, game_port
        ):
            raise EasyTierError("无法建立游戏端口转发，请重试。")
        instance.add_port_forward(
            "udp", "127.0.0.1", local_port, host.ipv4, game_port
        )
        report(94, "正在确认连接…")
        for _ in range(6):
            if cancel is not None and cancel():
                raise EasyTierCancelled("已取消加入房间。")
            if probe_tcp("127.0.0.1", local_port, 3.0):
                break
            time.sleep(1.0)

        profile = make_profile(player_name, machine_id, "GUEST")
        if host.latency_ms is not None:
            profile.latency_ms = host.latency_ms
        try:
            client.heartbeat(profile)
            players = client.players()
        except RoomProtocolError:
            players = []
        session = GuestSession(
            code,
            instance,
            client,
            local_port,
            host.hostname,
            profile,
        )
        session.players = players or session.players
        session.host_latency_ms = host.latency_ms
        report(100, "已连接房主。")
        return session
    except Exception:
        if client is not None:
            client.close()
        if instance is not None:
            instance.stop()
        raise


def format_room_code(text: str) -> str:
    """把任意包含房间号的文本整理成标准形式；无效时返回空串。"""
    code = RoomCode.find(text)
    return code.code if code is not None else ""


def parse_room_code(text: str) -> RoomCode:
    """接受完整邀请文本、标准房间号，或省略 U/ 的 16 位字符。"""
    found = RoomCode.find(text)
    if found is not None:
        return found
    raw = re.sub(r"[\s\-_/]", "", str(text or "")).upper()
    if len(raw) == 17 and raw.startswith("U"):
        raw = raw[1:]
    if len(raw) == 16:
        grouped = "U/" + "-".join(
            raw[index:index + 4] for index in range(0, 16, 4)
        )
        found = RoomCode.find(grouped)
        if found is not None:
            return found
    raise ValueError("房间号无效，应为 U/XXXX-XXXX-XXXX-XXXX。")


def room_code_digits(code: str) -> str:
    """返回去掉前缀和分隔符后的 16 位数字，方便界面显示。"""
    value = RoomCode.find(code)
    return value.digits if value is not None else ""
