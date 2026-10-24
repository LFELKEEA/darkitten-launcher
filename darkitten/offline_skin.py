from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from .config import APP_NAME, APP_VERSION, APP_DIR
from .downloader import http_open
from .skins import SkinError, load_skin_file, local_skin_path, variant_text

AUTHLIB_VERSION = "1.2.8"
AUTHLIB_SHA256 = "9c7f4343e6c82034958ffb48c14a2cb0c85928be7283103ce17da00c6d5a7b10"
AUTHLIB_DIR = APP_DIR / "runtime"
AUTHLIB_JAR = AUTHLIB_DIR / f"authlib-injector-{AUTHLIB_VERSION}.jar"
AUTHLIB_URLS = (
    f"https://authlib-injector.yushi.moe/artifact/56/authlib-injector-{AUTHLIB_VERSION}.jar",
    f"https://bmclapi2.bangbang93.com/mirrors/authlib-injector/artifact/56/authlib-injector-{AUTHLIB_VERSION}.jar",
    f"https://github.com/yushijinhun/authlib-injector/releases/download/v{AUTHLIB_VERSION}/authlib-injector-{AUTHLIB_VERSION}.jar",
    f"https://ghfast.top/https://github.com/yushijinhun/authlib-injector/releases/download/v{AUTHLIB_VERSION}/authlib-injector-{AUTHLIB_VERSION}.jar",
)

# 这组密钥只用于本机 127.0.0.1 皮肤服务的 textures 属性签名，
# 不会用于联网身份验证，也不代表任何 Mojang / Microsoft 凭据。
_PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA2IwWFmDr31AX97HIvmDW
Oir0pxVtoMUvjcKpynNUb9ccH7mFVw2vvUo9HqumpThewe9ERg8cnWnL7j/m7ozV
Y1rsulYLKFGHI0Nujgvg2/oawkc2g7LwExxkHyE/mvCT1xspAhX2NSLTnYKrd0vM
zlhXyCAIDjiiqHtewiVA5EuVH1ZhJ3llDZ6uu/PfwHrK0eD6HHnlULjyWPvY3OAR
p6KDD86fhU1Ebvmshndk3QzAZVnMwm3x5CZKYt+e6e+9/ucwoTZ5CsMRqPCWtdP5
PHY2QzZ+xVe8LSzBBZS2wtoajWgETkM2NVcFmjBtk6aEWYITkr46VNOxux4VuVIZ
VQIDAQAB
-----END PUBLIC KEY-----
"""
_RSA_N = int(
    "27336552991442943942390207097575131251834104238598772732870240596929815718692350579847365794261019922170742476121165998562344451845065847425627988604035849304867050471895417249201588686935885738101508014815242072599919748794018573179596505997758278507313772344167650871068472047901389424152979382852629183598523181292366966318633024491751059485569025224835035322265210196934499424915887284454874260912135137059084588648620710652588519922640196192981671697260023874248006504004831696209136788664598859850445724475758313322673134801135448232453257154780660968142906462667610526920927453083395452856154686425399737588053"
)
_RSA_E = 65537
_RSA_D = int(
    "853790783507131532665538203443227265889307428176223763819480382204185023845309574713432738863163826042675499497491866385087829083638594383961085266615378278079152580125658239814984354538882848310125036491560135600137194107965153111329014879619660152385055285350694966790300630969171606152497717474117478710492436331589158113186698152689137659154958297329115743493326363961288224837727731234150933844300749499856701309035251927285588137514251221708137399964776124627703511713965642571223952651628738422070774367265965897234802342363773378767508848404553559556898437473153307397060768776536537865456397902481480214473"
)
_RSA_KEY_BYTES = (_RSA_N.bit_length() + 7) // 8
_SHA1_DIGEST_INFO = bytes.fromhex("3021300906052b0e03021a05000414")


class OfflineSkinError(Exception):
    pass


def _sha256_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def ensure_authlib_jar(
    status_cb: Callable[[str], None] | None = None,
) -> Path:
    if AUTHLIB_JAR.is_file() and _sha256_file(AUTHLIB_JAR) == AUTHLIB_SHA256:
        return AUTHLIB_JAR
    AUTHLIB_DIR.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    for index, url in enumerate(AUTHLIB_URLS, start=1):
        if status_cb is not None:
            status_cb(f"正在准备离线皮肤组件（{index}/{len(AUTHLIB_URLS)}）…")
        try:
            with http_open(url, timeout=45) as response:
                data = response.read(8 * 1024 * 1024)
            if not data.startswith(b"PK\x03\x04"):
                raise OfflineSkinError("下载到的文件不是有效的 JAR。")
            if hashlib.sha256(data).hexdigest() != AUTHLIB_SHA256:
                raise OfflineSkinError("组件校验失败，已拒绝使用。")
            tmp = AUTHLIB_JAR.with_name(AUTHLIB_JAR.name + ".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, AUTHLIB_JAR)
            if status_cb is not None:
                status_cb("离线皮肤组件已就绪。")
            return AUTHLIB_JAR
        except Exception as exc:
            errors.append(f"{url}：{exc}")
            try:
                AUTHLIB_JAR.with_name(AUTHLIB_JAR.name + ".tmp").unlink(
                    missing_ok=True
                )
            except OSError:
                pass
    detail = errors[-1] if errors else "未知错误"
    raise OfflineSkinError(f"无法下载离线皮肤组件：{detail}")


def sign_texture_value(value: str) -> str:
    digest = hashlib.sha1(value.encode("utf-8")).digest()
    payload = _SHA1_DIGEST_INFO + digest
    padding = b"\xff" * (_RSA_KEY_BYTES - len(payload) - 3)
    encoded = b"\x00\x01" + padding + b"\x00" + payload
    signature = pow(int.from_bytes(encoded, "big"), _RSA_D, _RSA_N)
    return base64.b64encode(
        signature.to_bytes(_RSA_KEY_BYTES, "big")
    ).decode("ascii")


def _compact_uuid(value: str) -> str:
    return str(value or "").replace("-", "").lower()


class OfflineSkinServer:
    """在 127.0.0.1 上模拟 Yggdrasil，供 authlib-injector 读取皮肤。"""

    def __init__(
        self,
        player_name: str,
        player_uuid: str,
        skin_data: bytes,
        variant: str = "classic",
    ) -> None:
        if not skin_data:
            raise OfflineSkinError("离线皮肤数据为空。")
        self.player_name = str(player_name or "").strip()
        self.player_uuid_dashed = str(player_uuid or "").strip()
        self.player_uuid = _compact_uuid(self.player_uuid_dashed)
        self.texture_hash = hashlib.sha256(skin_data).hexdigest()
        self.skin_data = skin_data
        self.variant = "slim" if str(variant).lower() == "slim" else "classic"
        self.access_token = uuid.uuid4().hex
        self.client_token = uuid.uuid4().hex
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        if self._httpd is None:
            return ""
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> None:
        if self._httpd is not None:
            return
        server = self

        class Handler(_SkinRequestHandler):
            pass

        class Server(ThreadingHTTPServer):
            daemon_threads = True
            allow_reuse_address = True
            runtime = server

            def handle_error(self, request, client_address) -> None:
                # authlib-injector 读取完元数据后会主动断开短连接，
                # 这是正常行为，不需要把 ConnectionResetError 打到日志里。
                error = sys.exc_info()[1]
                if isinstance(
                    error,
                    (ConnectionResetError, ConnectionAbortedError, BrokenPipeError),
                ):
                    return
                super().handle_error(request, client_address)

        try:
            self._httpd = Server(("127.0.0.1", 0), Handler)
        except OSError as exc:
            raise OfflineSkinError(f"无法启动本地皮肤服务：{exc}") from exc
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            kwargs={"poll_interval": 0.15},
            daemon=True,
            name="dkl-offline-skin",
        )
        self._thread.start()

    def stop(self) -> None:
        httpd = self._httpd
        self._httpd = None
        if httpd is not None:
            try:
                httpd.shutdown()
            except Exception:
                pass
            try:
                httpd.server_close()
            except Exception:
                pass
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)

    # ------------------------------------------------------------------
    def metadata(self) -> dict[str, Any]:
        return {
            "meta": {
                "serverName": f"{APP_NAME} 离线皮肤",
                "implementationName": APP_NAME,
                "implementationVersion": APP_VERSION,
                "feature.non_email_login": True,
            },
            "skinDomains": ["127.0.0.1", "localhost"],
            "signaturePublickey": _PUBLIC_KEY_PEM,
        }

    def texture_payload(self) -> str:
        textures: dict[str, Any] = {
            "SKIN": {"url": f"{self.url}/textures/{self.texture_hash}"}
        }
        if self.variant == "slim":
            textures["SKIN"]["metadata"] = {"model": "slim"}
        payload = {
            "timestamp": int(time.time() * 1000),
            "profileId": self.player_uuid,
            "profileName": self.player_name,
            "textures": textures,
        }
        raw = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        return base64.b64encode(raw).decode("ascii")

    def profile(self, *, complete: bool = True) -> dict[str, Any]:
        response: dict[str, Any] = {
            "id": self.player_uuid,
            "name": self.player_name,
        }
        if complete:
            value = self.texture_payload()
            response["properties"] = [
                {
                    "name": "textures",
                    "value": value,
                    "signature": sign_texture_value(value),
                }
            ]
        return response


class _SkinRequestHandler(BaseHTTPRequestHandler):
    server_version = f"DarkittenSkin/{APP_VERSION}"
    protocol_version = "HTTP/1.1"

    @property
    def runtime(self) -> OfflineSkinServer:
        return self.server.runtime  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send(
        self,
        status: int,
        body: bytes = b"",
        content_type: str = "application/json; charset=utf-8",
        cache: str = "no-store",
    ) -> None:
        self.send_response(status)
        if body:
            self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        if body:
            try:
                self.wfile.write(body)
            except OSError:
                pass

    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        self._send(status, body)

    def _no_content(self) -> None:
        self._send(204)

    def _read_json(self) -> Any:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return None
        try:
            return json.loads(self.rfile.read(min(length, 1024 * 1024)))
        except Exception:
            return None

    def do_GET(self) -> None:
        runtime = self.runtime
        parsed = urlsplit(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/":
            self._json(runtime.metadata())
            return
        if path == "/status":
            self._json(
                {
                    "user.count": 1,
                    "token.count": 1,
                    "pendingAuthentication.count": 0,
                }
            )
            return
        if path == "/sessionserver/session/minecraft/hasJoined":
            names = parse_qs(parsed.query).get("username") or []
            if names and names[0] == runtime.player_name:
                self._json(runtime.profile(complete=True))
            else:
                self._no_content()
            return
        prefix = "/sessionserver/session/minecraft/profile/"
        if path.startswith(prefix):
            requested = _compact_uuid(path[len(prefix) :])
            if requested and requested == runtime.player_uuid:
                self._json(runtime.profile(complete=True))
            else:
                self._no_content()
            return
        prefix = "/textures/"
        if path.startswith(prefix):
            requested = path[len(prefix) :].lower()
            if requested == runtime.texture_hash:
                self._send(
                    200,
                    runtime.skin_data,
                    "image/png",
                    "public, max-age=2592000",
                )
            else:
                self._no_content()
            return
        if path == "/authserver/validate":
            self._no_content()
            return
        self._no_content()

    def do_POST(self) -> None:
        runtime = self.runtime
        parsed = urlsplit(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/api/profiles/minecraft":
            names = self._read_json()
            if not isinstance(names, list):
                names = []
            if runtime.player_name in [str(name) for name in names]:
                self._json(
                    [
                        {
                            "id": runtime.player_uuid,
                            "name": runtime.player_name,
                        }
                    ]
                )
            else:
                self._json([])
            return
        if path in (
            "/authserver/authenticate",
            "/authserver/refresh",
        ):
            profile = {
                "id": runtime.player_uuid,
                "name": runtime.player_name,
            }
            self._json(
                {
                    "accessToken": runtime.access_token,
                    "clientToken": runtime.client_token,
                    "availableProfiles": [profile],
                    "selectedProfile": profile,
                }
            )
            return
        if path == "/sessionserver/session/minecraft/join":
            self._no_content()
            return
        if path == "/authserver/validate":
            self._no_content()
            return
        self._no_content()


@dataclass
class OfflineSkinRuntime:
    server: OfflineSkinServer
    jar: Path
    jvm_args: list[str] = field(default_factory=list)
    note: str = ""
    _closed: bool = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.server.stop()


def active_skin_path(cfg) -> Path | None:
    candidates: list[Path] = []
    configured = str(getattr(cfg, "skin_path", "") or "").strip()
    if configured:
        candidates.append(Path(configured))
    local = local_skin_path()
    if local not in candidates:
        candidates.append(local)
    for path in candidates:
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


def prepare_offline_runtime(
    cfg,
    player_name: str,
    player_uuid: str,
    status_cb: Callable[[str], None] | None = None,
) -> OfflineSkinRuntime | None:
    path = active_skin_path(cfg)
    if path is None:
        return None
    try:
        skin = load_skin_file(path)
    except SkinError:
        return None
    if status_cb is not None:
        status_cb("正在准备离线皮肤…")
    jar = ensure_authlib_jar(status_cb)
    variant = "classic" if skin.legacy else str(
        getattr(cfg, "skin_variant", "classic") or "classic"
    )
    server = OfflineSkinServer(
        player_name, player_uuid, skin.data, variant=variant
    )
    try:
        server.start()
    except Exception:
        server.stop()
        raise
    args = [
        f"-javaagent:{jar}={server.url}",
        "-Dauthlibinjector.side=client",
    ]
    return OfflineSkinRuntime(
        server=server,
        jar=jar,
        jvm_args=args,
        note=(
            f"已启用离线皮肤：{player_name} · "
            f"{variant_text(variant)}（本地服务 {server.url}）"
        ),
    )
