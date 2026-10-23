"""樱花穿透（SakuraFrp）内置联机支持。

只使用官方 v4 OpenAPI，不在启动器里保存明文访问密钥：
配置文件中通过 Windows DPAPI 加密，frpc 配置只写入临时房间目录。
"""

from __future__ import annotations

import configparser
import hashlib
import ipaddress
import json
import os
import re
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal

from .config import APP_DIR, APP_VERSION, Config

SAKURA_API = "https://api.natfrp.com/v4"
SAKURA_PANEL = "https://www.natfrp.com/"
SAKURA_MC_DOCS = "https://doc.natfrp.com/game/mc/"
SAKURA_TUNNEL_DOCS = "https://doc.natfrp.com/natfrp/tunnel.html"
ROOM_TUNNEL_NAME = "StellarRoom"
USER_AGENT = f"DarkittenLauncher/{APP_VERSION}"

_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


class SakuraError(Exception):
    pass


def _friendly_api_error(code: int, message: str) -> str:
    text = str(message or "").strip()
    if code == 401 or "访问密钥" in text or "token" in text.lower():
        return "樱花穿透访问密钥无效，请在管理面板重新复制访问密钥。"
    if code == 403 or "实名" in text or "认证" in text:
        return f"樱花穿透账户认证未完成：{text or '请先在管理面板完成实名认证。'}"
    if code == 429 or "频繁" in text:
        return "请求过于频繁，请稍后再试。"
    if "隧道" in text and ("上限" in text or "数量" in text or "限制" in text):
        return "隧道数量已达上限，请先在樱花穿透面板删除不用的隧道。"
    if "节点" in text and ("不可用" in text or "满载" in text or "离线" in text):
        return f"节点当前不可用：{text}"
    return f"樱花穿透接口返回错误：{text or f'HTTP {code}'}"


class SakuraClient:
    """极简的樱花穿透 v4 API 客户端。"""

    def __init__(
        self,
        token: str,
        base_url: str = SAKURA_API,
        timeout: int = 25,
    ) -> None:
        self.token = str(token or "").strip()
        if not self.token:
            raise SakuraError("请先填写樱花穿透访问密钥。")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # ------------------------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
        timeout: int | None = None,
    ) -> Any:
        url = self.base_url + path
        headers = {
            "Authorization": f"Bearer {self.token}",
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
        }
        body: bytes | None = None
        if data is not None:
            body = urllib.parse.urlencode(
                {k: v for k, v in data.items() if v is not None}
            ).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        request = urllib.request.Request(
            url, data=body, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(
                request, timeout=timeout or self.timeout
            ) as response:
                raw = response.read()
                content_type = response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            code, message = _error_from_body(raw, exc.code)
            raise SakuraError(_friendly_api_error(code, message)) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SakuraError(
                "无法连接樱花穿透服务器，请检查网络连接或系统代理设置。"
            ) from exc

        text = raw.decode("utf-8", "replace")
        stripped = text.lstrip()
        # INI 配置也以 "[" 开头（如 [common]），不能仅凭方括号判断 JSON。
        looks_json = stripped.startswith("{") or bool(
            re.match(r"\[\s*[\{\[\]\"\d-]", stripped)
        )
        wants_json = "json" in content_type.lower() or looks_json
        if wants_json:
            try:
                parsed = json.loads(text)
            except ValueError as exc:
                if "json" in content_type.lower():
                    raise SakuraError(
                        "樱花穿透接口返回了无法解析的数据。"
                    ) from exc
                return text
            if isinstance(parsed, dict) and "code" in parsed:
                code = _as_int(parsed.get("code"), 0)
                if code not in (0, 1, 200):
                    raise SakuraError(
                        _friendly_api_error(code, str(parsed.get("msg") or ""))
                    )
            return parsed
        return text

    # ------------------------------------------------------------------
    def user_info(self) -> "SakuraUser":
        data = self._request("GET", "/user/info")
        if not isinstance(data, dict):
            raise SakuraError("樱花穿透用户信息格式异常。")
        ban = data.get("ban")
        if isinstance(ban, dict) and ban:
            title = str(ban.get("title") or "账户已被冻结")
            reason = str(ban.get("reason") or "").strip()
            raise SakuraError(
                f"{title}{('：' + reason) if reason else '，请到管理面板查看。'}"
            )
        return SakuraUser.from_dict(data)

    def nodes(self) -> dict[int, "SakuraNode"]:
        data = self._request("GET", "/nodes")
        result: dict[int, SakuraNode] = {}
        if not isinstance(data, dict):
            raise SakuraError("樱花穿透节点列表格式异常。")
        for key, value in data.items():
            if not isinstance(value, dict):
                continue
            try:
                node_id = int(key)
            except (TypeError, ValueError):
                continue
            result[node_id] = SakuraNode.from_dict(node_id, value)
        return result

    def tunnels(self) -> list["SakuraTunnel"]:
        data = self._request("GET", "/tunnels")
        if not isinstance(data, list):
            raise SakuraError("樱花穿透隧道列表格式异常。")
        return [
            SakuraTunnel.from_dict(item)
            for item in data
            if isinstance(item, dict)
        ]

    def create_tunnel(
        self,
        name: str,
        node_id: int,
        local_port: int,
        local_ip: str = "127.0.0.1",
    ) -> dict[str, Any]:
        data = self._request(
            "POST",
            "/tunnels",
            {
                "name": name,
                "type": "tcp",
                "node": int(node_id),
                "local_ip": local_ip,
                "local_port": int(local_port),
            },
        )
        if not isinstance(data, dict) or not data.get("id"):
            raise SakuraError("樱花穿透没有返回新隧道的 ID。")
        return data

    def edit_tunnel(
        self,
        tunnel_id: int,
        local_port: int,
        local_ip: str = "127.0.0.1",
    ) -> None:
        self._request(
            "POST",
            "/tunnel/edit",
            {
                "id": int(tunnel_id),
                "local_ip": local_ip,
                "local_port": int(local_port),
            },
        )

    def tunnel_config(self, tunnel_id: int, frpc_version: str) -> str:
        text = self._request(
            "POST",
            "/tunnel/config",
            {"query": str(int(tunnel_id)), "frpc": str(frpc_version)},
        )
        if not isinstance(text, str) or "server" not in text.lower():
            raise SakuraError("樱花穿透没有返回有效的 frpc 配置。")
        return text


@dataclass
class SakuraUser:
    id: int = 0
    name: str = ""
    avatar: str = ""
    speed: str = ""
    tunnel_limit: int = 0
    realname: int = 0
    group_name: str = ""
    group_level: int = 0
    traffic_used: int = 0
    traffic_left: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SakuraUser":
        group = data.get("group") if isinstance(data.get("group"), dict) else {}
        traffic = data.get("traffic") if isinstance(data.get("traffic"), list) else []
        used = _as_int(traffic[0], 0) if len(traffic) > 0 else 0
        left = _as_int(traffic[1], 0) if len(traffic) > 1 else 0
        return cls(
            id=_as_int(data.get("id"), 0),
            name=str(data.get("name") or ""),
            avatar=str(data.get("avatar") or ""),
            speed=str(data.get("speed") or ""),
            tunnel_limit=_as_int(data.get("tunnels"), 0),
            realname=_as_int(data.get("realname"), 0),
            group_name=str(group.get("name") or ""),
            group_level=_as_int(group.get("level"), 0),
            traffic_used=used,
            traffic_left=left,
        )

    @property
    def realname_text(self) -> str:
        return "已实名" if self.realname else "未实名"

    @property
    def traffic_text(self) -> str:
        if not self.traffic_left and not self.traffic_used:
            return "流量信息不可用"
        return (
            f"今日 {format_size(self.traffic_used)}"
            f" · 剩余 {format_size(self.traffic_left)}"
        )


@dataclass
class SakuraNode:
    id: int = 0
    name: str = ""
    host: str = ""
    description: str = ""
    vip: int = 0
    flag: int = 0

    @classmethod
    def from_dict(cls, node_id: int, data: dict[str, Any]) -> "SakuraNode":
        return cls(
            id=node_id,
            name=str(data.get("name") or f"节点 {node_id}"),
            host=str(data.get("host") or ""),
            description=str(data.get("description") or ""),
            vip=_as_int(data.get("vip"), 0),
            flag=_as_int(data.get("flag"), 0),
        )

    @property
    def online(self) -> bool:
        return not bool(self.flag & (1 << 9))

    @property
    def can_create(self) -> bool:
        return bool(self.flag & (1 << 2))

    @property
    def mainland(self) -> bool:
        return bool(self.flag & (1 << 3))

    @property
    def udp(self) -> bool:
        return bool(self.flag & (1 << 5))

    @property
    def public_game(self) -> bool:
        return bool(self.flag & (1 << 11)) or "多线" in self.name

    @property
    def selectable(self) -> bool:
        return self.online and self.can_create and bool(self.host)

    @property
    def label(self) -> str:
        tags: list[str] = []
        if self.public_game:
            tags.append("多线")
        if self.vip:
            tags.append(f"VIP{self.vip}")
        if self.mainland:
            tags.append("内地")
        suffix = f" · {' · '.join(tags)}" if tags else ""
        return f"{self.name}{suffix}"


@dataclass
class SakuraTunnel:
    id: int = 0
    name: str = ""
    type: str = ""
    node: int = 0
    status: int = 0
    online: bool = False
    note: str = ""
    local_ip: str = ""
    local_port: int = 0
    remote: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SakuraTunnel":
        return cls(
            id=_as_int(data.get("id"), 0),
            name=str(data.get("name") or ""),
            type=str(data.get("type") or ""),
            node=_as_int(data.get("node"), 0),
            status=_as_int(data.get("status"), 0),
            online=bool(data.get("online")),
            note=str(data.get("note") or ""),
            local_ip=str(data.get("local_ip") or ""),
            local_port=_as_int(data.get("local_port"), 0),
            remote=str(data.get("remote") or ""),
        )

    @property
    def remote_port(self) -> int:
        if self.remote.isdigit():
            return int(self.remote)
        return 0


@dataclass
class FrpcClient:
    path: Path
    version: str
    downloaded: bool = False


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _error_from_body(raw: bytes, fallback_code: int) -> tuple[int, str]:
    text = raw.decode("utf-8", "replace").strip()
    try:
        data = json.loads(text)
    except ValueError:
        return fallback_code, text
    if isinstance(data, dict):
        return _as_int(data.get("code"), fallback_code), str(data.get("msg") or "")
    return fallback_code, text


def format_size(value: int) -> str:
    size = float(max(0, value))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


# ----------------------------------------------------------------------
# 房间号编解码：版本(1B) + IPv4(4B) + 端口(2B) + CRC8(1B)
# 使用 Crockford Base32，去掉容易混淆的 I/L/O/U。

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ROOM_VERSION = 1


def _crc8(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def parse_server_address(address: str) -> tuple[str, int]:
    text = str(address or "").strip()
    if not text:
        raise SakuraError("服务器地址为空。")
    if text.startswith("["):
        end = text.find("]")
        if end < 0:
            raise SakuraError("IPv6 地址格式不正确。")
        host = text[1:end].strip()
        rest = text[end + 1 :].strip()
        if rest.startswith(":"):
            port_text = rest[1:]
        elif rest:
            raise SakuraError("服务器地址格式不正确。")
        else:
            port_text = "25565"
    elif text.count(":") == 1:
        host, port_text = text.rsplit(":", 1)
        host = host.strip()
    elif text.count(":") > 1:
        host = text
        port_text = "25565"
    else:
        host = text
        port_text = "25565"
    if not host:
        raise SakuraError("服务器地址缺少主机名。")
    try:
        port = int(port_text)
    except ValueError as exc:
        raise SakuraError("服务器端口必须是数字。") from exc
    if not 1 <= port <= 65535:
        raise SakuraError("服务器端口需在 1-65535 之间。")
    return host, port


def resolve_ipv4(host: str, port: int = 25565) -> str:
    try:
        return str(ipaddress.IPv4Address(host))
    except ipaddress.AddressValueError:
        pass
    try:
        infos = socket.getaddrinfo(
            host, port, family=socket.AF_INET, type=socket.SOCK_STREAM
        )
    except (socket.gaierror, OSError) as exc:
        raise SakuraError(
            f"无法把节点地址 {host} 解析为 IPv4，房间号暂时不可用，可以直接分享服务器地址。"
        ) from exc
    for info in infos:
        address = info[4][0]
        try:
            return str(ipaddress.IPv4Address(address))
        except ipaddress.AddressValueError:
            continue
    raise SakuraError("节点没有可用的 IPv4 地址，房间号暂时不可用。")


def normalize_room_code(code: str) -> str:
    text = re.sub(r"[\s\-_]", "", str(code or "")).upper()
    text = text.replace("I", "1").replace("L", "1").replace("O", "0")
    if len(text) != 13:
        raise SakuraError("房间号应为 13 位，例如 XXXXX-XXXX-XXXXX。")
    if any(char not in _CROCKFORD for char in text):
        raise SakuraError("房间号包含无效字符，请检查是否输入正确。")
    return text


def encode_room_code(address: str) -> str:
    host, port = parse_server_address(address)
    ip_text = resolve_ipv4(host, port)
    payload = (
        bytes([_ROOM_VERSION])
        + ipaddress.IPv4Address(ip_text).packed
        + port.to_bytes(2, "big")
    )
    payload += bytes([_crc8(payload)])

    buffer = 0
    bits = 0
    chars: list[str] = []
    for byte in payload:
        buffer = (buffer << 8) | byte
        bits += 8
        while bits >= 5:
            bits -= 5
            chars.append(_CROCKFORD[(buffer >> bits) & 0x1F])
    if bits:
        chars.append(_CROCKFORD[(buffer << (5 - bits)) & 0x1F])
    raw = "".join(chars)
    return f"{raw[:5]}-{raw[5:9]}-{raw[9:]}"


def decode_room_code(code: str) -> tuple[str, int]:
    raw = normalize_room_code(code)
    value = 0
    for char in raw:
        value = (value << 5) | _CROCKFORD.index(char)
    payload = (value >> 1).to_bytes(8, "big")
    if payload[0] != _ROOM_VERSION:
        raise SakuraError("房间号版本不受支持，请把启动器升级到最新版本。")
    if _crc8(payload[:7]) != payload[7]:
        raise SakuraError("房间号校验失败，请检查是否输入完整。")
    host = str(ipaddress.IPv4Address(payload[1:5]))
    port = int.from_bytes(payload[5:7], "big")
    if not 1 <= port <= 65535:
        raise SakuraError("房间号中的端口无效。")
    return host, port


def room_code_for_address(address: str) -> tuple[str, str]:
    host, port = parse_server_address(address)
    ip_text = resolve_ipv4(host, port)
    return encode_room_code(f"{ip_text}:{port}"), f"{ip_text}:{port}"


def decode_room_input(text: str) -> tuple[str, int]:
    """允许输入房间号或直接的 host:port 地址。"""
    value = str(text or "").strip()
    if not value:
        raise SakuraError("请先输入房间号。")
    compact = re.sub(r"[\s\-_]", "", value)
    if ":" not in value and len(compact) == 13:
        return decode_room_code(value)
    if ":" in value or re.fullmatch(r"[\d.]+", value):
        return parse_server_address(value)
    return decode_room_code(value)


def config_server_addr(config_text: str) -> str:
    text = str(config_text or "")
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    try:
        parser.read_string(text)
        for section in parser.sections():
            if section.strip().lower() in ("common", "common_config"):
                value = parser.get(section, "server_addr", fallback="")
                if not value:
                    value = parser.get(section, "serverAddr", fallback="")
                if value:
                    return value.strip().strip('"')
    except (configparser.Error, ValueError):
        pass
    match = re.search(
        r"(?:server_addr|serverAddr)\s*=\s*[\"']?([^\"'\s#]+)", text
    )
    return match.group(1) if match else ""


def tunnel_public_address(node: SakuraNode, tunnel: SakuraTunnel) -> str:
    host = node.host.strip()
    if not host:
        raise SakuraError("节点没有可用的公网地址。")
    port = tunnel.remote_port
    if not port:
        raise SakuraError("隧道还没有分配远程端口，请稍后重试。")
    return f"{host}:{port}"


# ----------------------------------------------------------------------
# frpc 客户端下载与运行


def _platform_arch() -> str:
    if sys.platform != "win32":
        raise SakuraError("内置樱花穿透目前只支持 Windows。")
    machine = (os.environ.get("PROCESSOR_ARCHITECTURE") or "").lower()
    if "arm64" in machine or "aarch64" in machine:
        return "windows_arm64"
    return "windows_amd64" if struct.calcsize("P") * 8 >= 64 else "windows_386"


def _fetch_client_meta() -> dict[str, Any]:
    request = urllib.request.Request(
        SAKURA_API + "/system/clients?download=true",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise SakuraError("无法获取樱花穿透客户端信息，请检查网络。") from exc
    if not isinstance(data, dict):
        raise SakuraError("樱花穿透客户端信息格式异常。")
    return data


def _detect_frpc_version(path: Path) -> str:
    if not path or not path.exists():
        return ""
    try:
        result = subprocess.run(
            [str(path), "-v"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            creationflags=_CREATE_NO_WINDOW,
        )
    except Exception:
        return ""
    output = (result.stdout or "") + " " + (result.stderr or "")
    match = re.search(r"(\d+\.\d+\.\d+(?:-sakura-\d+(?:\.\d+)?)?)", output)
    return match.group(1) if match else ""


def _file_hash(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_frpc(
    cfg: Config,
    progress_cb: Callable[[int, str], None] | None = None,
    log_cb: Callable[[str], None] | None = None,
) -> FrpcClient:
    """返回可执行的 frpc；缺失时从樱花穿透官方源下载并校验哈希。"""
    override = str(getattr(cfg, "sakura_frpc_path", "") or "").strip()
    if override:
        path = Path(os.path.expandvars(override)).expanduser()
        version = _detect_frpc_version(path)
        if not version:
            raise SakuraError(f"无法运行指定的 frpc：{path}")
        return FrpcClient(path=path, version=version)

    target = APP_DIR / "tools" / "frpc" / "frpc.exe"
    if target.exists():
        version = _detect_frpc_version(target)
        if version:
            if log_cb:
                log_cb(f"使用内置 frpc {version}")
            return FrpcClient(path=target, version=version)
        try:
            target.unlink()
        except OSError:
            pass

    meta = _fetch_client_meta()
    entry = meta.get("frpc") if isinstance(meta.get("frpc"), dict) else {}
    archs = entry.get("archs") if isinstance(entry.get("archs"), dict) else {}
    info = archs.get(_platform_arch())
    if not isinstance(info, dict):
        info = archs.get("windows_amd64")
    if not isinstance(info, dict) or not info.get("url"):
        raise SakuraError("樱花穿透没有提供适用于当前系统的 frpc 下载。")

    url = str(info.get("url") or "")
    expected_hash = str(info.get("hash") or "").lower()
    expected_size = _as_int(info.get("size"), 0)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    if log_cb:
        log_cb("正在下载内置 frpc…")

    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            total = _as_int(response.headers.get("Content-Length"), 0) or expected_size
            done = 0
            with partial.open("wb") as handle:
                while True:
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    done += len(chunk)
                    if progress_cb and total > 0:
                        progress_cb(
                            min(99, int(done * 100 / total)),
                            f"下载内置 frpc… {done // 1048576}/{max(1, total // 1048576)} MB",
                        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            pass
        raise SakuraError(f"内置 frpc 下载失败：{exc}") from exc

    if expected_hash:
        algorithm = "md5" if len(expected_hash) == 32 else "sha256"
        actual = _file_hash(partial, algorithm)
        if actual.lower() != expected_hash:
            try:
                partial.unlink(missing_ok=True)
            except OSError:
                pass
            raise SakuraError("内置 frpc 校验失败，文件可能不完整，请重试。")
    if expected_size and partial.stat().st_size != expected_size:
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            pass
        raise SakuraError("内置 frpc 大小不正确，请重试。")

    os.replace(partial, target)
    version = _detect_frpc_version(target) or str(entry.get("ver") or "")
    if not version:
        raise SakuraError("内置 frpc 无法运行，请检查杀毒软件是否拦截。")
    if progress_cb:
        progress_cb(100, "内置 frpc 已就绪")
    if log_cb:
        log_cb(f"内置 frpc {version} 已就绪")
    return FrpcClient(path=target, version=version, downloaded=True)


def write_tunnel_config(config_text: str, tunnel_id: int) -> Path:
    directory = APP_DIR / "rooms"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"room-{int(tunnel_id)}.ini"
    path.write_text(config_text, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def remove_tunnel_config(path: Path | str | None) -> None:
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def cleanup_room_configs(max_age_hours: int = 24) -> None:
    """清理崩溃后遗留的 frpc 配置，避免访问令牌长期留在磁盘上。"""
    directory = APP_DIR / "rooms"
    if not directory.exists():
        return
    cutoff = time.time() - max(1, int(max_age_hours)) * 3600
    try:
        candidates = list(directory.glob("room-*.ini"))
    except OSError:
        return
    for path in candidates:
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue


def detect_game_lan_ports(pid: int) -> list[int]:
    """从 netstat 中找出指定游戏进程监听的 TCP 端口。"""
    if not pid or sys.platform != "win32":
        return []
    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=12,
            creationflags=_CREATE_NO_WINDOW,
        )
    except Exception:
        return []
    ports: list[int] = []
    for line in (result.stdout or "").splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        if parts[0].upper() != "TCP" or "LISTEN" not in parts[3].upper():
            continue
        if parts[-1] != str(pid):
            continue
        local = parts[1]
        if ":" not in local:
            continue
        port_text = local.rsplit(":", 1)[1]
        try:
            port = int(port_text)
        except ValueError:
            continue
        if 1 <= port <= 65535 and port not in ports:
            ports.append(port)
    return ports


def preferred_lan_port(ports: list[int]) -> int:
    for port in ports:
        if port == 25565:
            return port
    for port in ports:
        if port >= 1024:
            return port
    return 25565


class SakuraTunnelProcess(QObject):
    """在本进程之外运行 frpc，并读取它的连接日志。"""

    log_line = Signal(str)
    ready = Signal(str)
    failed = Signal(str)
    finished = Signal(int)

    _READY_MARKERS = (
        "启动成功",
        "start proxy success",
        "login to server success",
        "隧道已启动",
        "successfully started",
    )
    _FAILURE_MARKERS = (
        "登录节点失败",
        "login to server failed",
        "连接节点失败",
        "隧道启动失败",
        "start proxy error",
        "访问密钥无效",
        "authentication failed",
        "token is invalid",
    )

    def __init__(
        self,
        frpc_path: Path | str,
        config_path: Path | str,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.frpc_path = Path(frpc_path)
        self.config_path = Path(config_path)
        self._process: subprocess.Popen[str] | None = None
        self._thread: threading.Thread | None = None
        self._stop_requested = False
        self._ready_sent = False
        self._failure_sent = False
        self._last_lines: list[str] = []
        self._started_at = 0.0

    @property
    def running(self) -> bool:
        return bool(self._process and self._process.poll() is None)

    @property
    def pid(self) -> int:
        return self._process.pid if self._process else 0

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        startupinfo = None
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
        command = [str(self.frpc_path), "-c", str(self.config_path)]
        self.log_line.emit("正在启动内置 frpc…")
        try:
            self._process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=_CREATE_NO_WINDOW,
                startupinfo=startupinfo,
            )
        except Exception as exc:
            self.failed.emit(f"无法启动内置 frpc：{exc}")
            self.finished.emit(-1)
            return

        self._started_at = time.time()
        threading.Thread(target=self._watchdog, daemon=True).start()
        assert self._process.stdout is not None
        try:
            for line in self._process.stdout:
                text = line.rstrip("\r\n")
                if not text:
                    continue
                self.log_line.emit(text)
                self._last_lines.append(text)
                if len(self._last_lines) > 30:
                    self._last_lines.pop(0)
                self._maybe_ready(text)
            code = self._process.wait()
        except Exception as exc:
            self.log_line.emit(f"[frpc 输出错误] {exc}")
            try:
                code = self._process.wait(timeout=5)
            except Exception:
                code = -1
        if (
            not self._ready_sent
            and not self._failure_sent
            and not self._stop_requested
        ):
            detail = " | ".join(self._last_lines[-4:])
            self.failed.emit(
                f"frpc 已退出（代码 {code}）。"
                + (f" {detail}" if detail else "请查看运行日志。")
            )
        self.finished.emit(code)

    def _maybe_ready(self, text: str) -> None:
        if self._ready_sent or self._failure_sent:
            return
        low = text.lower()
        if any(marker.lower() in low for marker in self._FAILURE_MARKERS):
            self._failure_sent = True
            message = "内置 frpc 连接节点失败，请检查网络或更换节点后重试。"
            if "refused" in low or "拒绝" in text:
                message += "（节点拒绝了连接）"
            elif "token" in low or "密钥" in text:
                message = "樱花穿透隧道凭证无效，请重新创建房间。"
            self.failed.emit(message)
            try:
                self.stop()
            except Exception:
                pass
            return
        if any(marker.lower() in low for marker in self._READY_MARKERS):
            self._ready_sent = True
            self.ready.emit("隧道已连接")

    def _watchdog(self) -> None:
        deadline = self._started_at + 45
        while time.time() < deadline:
            if self._stop_requested or self._failure_sent or not self.running:
                return
            if self._ready_sent:
                return
            time.sleep(0.5)
        if (
            self.running
            and not self._ready_sent
            and not self._failure_sent
            and not self._stop_requested
        ):
            self._ready_sent = True
            self.ready.emit("frpc 已启动，未检测到明确成功日志")

    def stop(self) -> None:
        self._stop_requested = True
        process = self._process
        if not process or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=4)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass


def start_timeout_seconds() -> int:
    return 45


def elapsed_text(started_at: float) -> str:
    seconds = max(0, int(time.time() - started_at))
    if seconds < 60:
        return f"{seconds} 秒"
    return f"{seconds // 60} 分 {seconds % 60} 秒"
