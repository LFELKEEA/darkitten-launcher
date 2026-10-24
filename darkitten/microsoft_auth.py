from __future__ import annotations

import base64
import hashlib
import html
import http.server
import json
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from typing import Any, Callable

from .accounts import Account
from .config import APP_VERSION

DEVICE_CODE_URL = (
    "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode"
)
AUTHORIZE_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize"
TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
XBOX_USER_URL = "https://user.auth.xboxlive.com/user/authenticate"
XSTS_URL = "https://xsts.auth.xboxlive.com/xsts/authorize"
MC_LOGIN_URL = "https://api.minecraftservices.com/authentication/login_with_xbox"
MC_PROFILE_URL = "https://api.minecraftservices.com/minecraft/profile"
SCOPE = "XboxLive.signin offline_access"
USER_AGENT = f"DarkittenLauncher/{APP_VERSION} (Minecraft Launcher)"

CLIENT_ID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

XSTS_MESSAGES = {
    2148916233: "该 Microsoft 账户还没有 Xbox 账户，请先在 xbox.com 创建。",
    2148916235: "该账户所在地区无法使用 Xbox Live。",
    2148916236: "该账户需要先在 Xbox 页面完成年龄验证。",
    2148916237: "该账户需要先在 Xbox 页面完成年龄验证。",
    2148916238: "该账户是儿童账户，需要由家庭组中的成年人授权。",
}


class MicrosoftAuthError(Exception):
    pass


class MicrosoftAuthCancelled(Exception):
    pass


@dataclass
class DeviceCode:
    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int
    interval: int
    message: str


@dataclass
class TokenResult:
    access_token: str
    refresh_token: str
    expires_in: int


class _HttpFailure(Exception):
    def __init__(self, status: int, payload: dict[str, Any]) -> None:
        self.status = status
        self.payload = payload
        super().__init__(str(payload.get("error") or status))


def normalize_client_id(value: str) -> str:
    client_id = (value or "").strip()
    if not client_id:
        raise MicrosoftAuthError(
            "还没有填写 Azure 客户端 ID。请先在设置中填写应用注册的客户端 ID。"
        )
    if not CLIENT_ID_RE.fullmatch(client_id):
        raise MicrosoftAuthError(
            "客户端 ID 格式不正确，应为类似 00000000-0000-0000-0000-000000000000 的 GUID。"
        )
    return client_id


def _decode(raw: bytes) -> dict[str, Any]:
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _request(
    url: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    merged = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    if headers:
        merged.update(headers)
    request = urllib.request.Request(url, data=data, headers=merged, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        payload = _decode(exc.read())
        raise _HttpFailure(exc.code, payload) from None
    except urllib.error.URLError as exc:
        raise MicrosoftAuthError(f"无法连接 Microsoft 服务：{exc.reason}") from exc
    except OSError as exc:
        raise MicrosoftAuthError(f"网络请求失败：{exc}") from exc
    payload = _decode(raw)
    if not payload:
        raise MicrosoftAuthError("Microsoft 服务返回了无法识别的数据。")
    return payload


def _post_form(url: str, fields: dict[str, str], timeout: int = 30) -> dict[str, Any]:
    body = urllib.parse.urlencode(fields).encode("utf-8")
    return _request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=timeout,
    )


def _post_json(
    url: str, payload: dict[str, Any], timeout: int = 30
) -> dict[str, Any]:
    return _request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-xbl-contract-version": "1",
        },
        timeout=timeout,
    )


def _oauth_message(failure: _HttpFailure) -> str:
    code = str(failure.payload.get("error") or "")
    description = str(failure.payload.get("error_description") or "").strip()
    friendly = {
        "invalid_client": "Azure 客户端 ID 无效，或应用没有启用公共客户端流程。",
        "unauthorized_client": (
            "该 Azure 应用不允许当前登录方式，请启用公共客户端流，"
            "并确认已注册 http://localhost 重定向 URI。"
        ),
        "invalid_grant": "登录授权已失效或被拒绝，请重新登录 Microsoft 账户。",
        "invalid_request": "登录请求无效，请检查客户端 ID、重定向 URI 与网络环境。",
        "unsupported_response_type": "登录方式不受支持，请检查 Azure 应用配置。",
        "temporarily_unavailable": "Microsoft 登录服务暂时不可用，请稍后重试。",
    }
    if code in friendly:
        return friendly[code]
    if description:
        return description.splitlines()[0][:240]
    return f"Microsoft 登录失败（HTTP {failure.status}）。"


def request_device_code(client_id: str) -> DeviceCode:
    client_id = normalize_client_id(client_id)
    try:
        payload = _post_form(
            DEVICE_CODE_URL, {"client_id": client_id, "scope": SCOPE}
        )
    except _HttpFailure as exc:
        raise MicrosoftAuthError(_oauth_message(exc)) from None
    device_code = str(payload.get("device_code") or "")
    user_code = str(payload.get("user_code") or "")
    if not device_code or not user_code:
        raise MicrosoftAuthError(
            str(payload.get("error_description") or "无法获取 Microsoft 登录验证码。")
        )
    uri = str(payload.get("verification_uri") or "https://microsoft.com/devicelogin")
    complete = str(payload.get("verification_uri_complete") or "")
    try:
        expires_in = int(payload.get("expires_in") or 900)
    except Exception:
        expires_in = 900
    try:
        interval = max(1, int(payload.get("interval") or 5))
    except Exception:
        interval = 5
    return DeviceCode(
        device_code=device_code,
        user_code=user_code,
        verification_uri=uri,
        verification_uri_complete=complete,
        expires_in=expires_in,
        interval=interval,
        message=str(payload.get("message") or ""),
    )


def poll_device_code(
    client_id: str,
    device_code: str,
    *,
    interval: int = 5,
    expires_in: int = 900,
    cancel_event=None,
    on_status: Callable[[str], None] | None = None,
) -> TokenResult:
    client_id = normalize_client_id(client_id)
    deadline = time.monotonic() + max(30, expires_in)
    delay = max(1, interval)
    while time.monotonic() < deadline:
        if cancel_event is not None:
            if cancel_event.wait(delay):
                raise MicrosoftAuthCancelled()
        else:
            time.sleep(delay)
        try:
            payload = _post_form(
                TOKEN_URL,
                {
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "client_id": client_id,
                    "device_code": device_code,
                },
            )
        except _HttpFailure as exc:
            error = str(exc.payload.get("error") or "")
            if error == "authorization_pending":
                if on_status:
                    on_status("等待在浏览器中完成登录…")
                continue
            if error == "slow_down":
                delay += 5
                if on_status:
                    on_status("登录服务要求降低轮询频率，继续等待…")
                continue
            if error == "authorization_declined":
                raise MicrosoftAuthError("你取消了 Microsoft 登录授权。") from None
            if error == "expired_token":
                raise MicrosoftAuthError("登录验证码已过期，请重新登录。") from None
            if error == "bad_verification_code":
                raise MicrosoftAuthError("验证码无效，请重新登录。") from None
            raise MicrosoftAuthError(_oauth_message(exc)) from None
        token = str(payload.get("access_token") or "")
        if not token:
            raise MicrosoftAuthError(
                str(payload.get("error_description") or "Microsoft 没有返回访问令牌。")
            )
        try:
            expires = int(payload.get("expires_in") or 3600)
        except Exception:
            expires = 3600
        return TokenResult(
            access_token=token,
            refresh_token=str(payload.get("refresh_token") or ""),
            expires_in=expires,
        )
    raise MicrosoftAuthError("登录验证码已过期，请重新登录。")


class _OAuthCallbackServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int]) -> None:
        super().__init__(address, _OAuthCallbackHandler)
        self.result: dict[str, str] = {}
        self.done = threading.Event()


class _OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    server: _OAuthCallbackServer

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urllib.parse.urlparse(self.path)
        params = {
            key: values[0]
            for key, values in urllib.parse.parse_qs(
                parsed.query, keep_blank_values=True
            ).items()
        }
        if parsed.path not in ("", "/"):
            self.send_response(204)
            self.end_headers()
            return
        if not any(key in params for key in ("code", "error")):
            self._respond(
                400,
                "等待登录结果",
                "没有收到 Microsoft 登录结果，请回到启动器重新发起登录。",
            )
            return

        self.server.result = params
        self.server.done.set()
        error = params.get("error")
        if error:
            message = (
                params.get("error_description")
                or "Microsoft 登录没有完成，请回到启动器重试。"
            )
            self._respond(200, "登录未完成", message)
        else:
            self._respond(
                200,
                "登录完成",
                "已完成 Microsoft 授权，可以关闭此页面并返回启动器。",
            )

    def _respond(self, status: int, title: str, message: str) -> None:
        safe_title = html.escape(title)
        safe_message = html.escape(message[:500])
        body = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{safe_title} · Darkitten Launcher</title>
<style>
  body {{ margin: 0; min-height: 100vh; display: grid; place-items: center;
         background: #f4f6fa; color: #1b2433;
         font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif; }}
  .card {{ width: min(460px, calc(100% - 40px)); padding: 36px 32px;
          background: #fff; border-radius: 16px; text-align: center;
          box-shadow: 0 14px 40px rgba(20, 35, 60, .10); }}
  h1 {{ margin: 0 0 10px; font-size: 22px; }}
  p {{ margin: 0; color: #5c6b80; font-size: 14px; line-height: 1.7; }}
</style>
</head>
<body><div class="card"><h1>{safe_title}</h1><p>{safe_message}</p></div></body>
</html>"""
        raw = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(raw)
        except OSError:
            pass

    def log_message(self, format: str, *args: Any) -> None:
        return


def _base64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = _base64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def _browser_error_message(error: str, description: str) -> str:
    if error in ("access_denied", "consent_required", "interaction_required"):
        return "你取消了 Microsoft 授权，或需要重新确认登录。"
    friendly = {
        "invalid_request": "Microsoft 登录请求无效，请检查 Azure 重定向 URI 是否为 http://localhost。",
        "unauthorized_client": (
            "该 Azure 应用不允许浏览器登录，请启用公共客户端流，"
            "并添加 http://localhost 重定向 URI。"
        ),
        "invalid_client": "Azure 客户端 ID 无效，或应用没有启用公共客户端流程。",
        "server_error": "Microsoft 登录服务出错，请稍后重试。",
        "temporarily_unavailable": "Microsoft 登录服务暂时不可用，请稍后重试。",
    }
    if error in friendly:
        return friendly[error]
    if description:
        return description.splitlines()[0][:240]
    return f"Microsoft 登录没有完成（{error}）。"


def login_with_browser(
    client_id: str,
    *,
    login_hint: str = "",
    on_url: Callable[[str], None] | None = None,
    on_status: Callable[[str], None] | None = None,
    cancel_event: threading.Event | None = None,
    timeout: int = 300,
) -> Account:
    """通过系统浏览器 + PKCE + 本地回调完成 Microsoft 登录。

    Microsoft 不允许第三方应用直接收集账户密码；这里会把邮箱作为 login_hint
    预填到 Microsoft 官方登录页，授权完成后由本机回环地址自动接收授权码。
    """
    client_id = normalize_client_id(client_id)
    login_hint = (login_hint or "").strip()

    try:
        server = _OAuthCallbackServer(("127.0.0.1", 0))
    except OSError as exc:
        raise MicrosoftAuthError(f"无法启动本地登录回调服务：{exc}") from None

    port = int(server.server_address[1])
    redirect_uri = f"http://localhost:{port}"
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(32)
    query = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "response_mode": "query",
        "scope": SCOPE,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if login_hint:
        query["login_hint"] = login_hint
    url = AUTHORIZE_URL + "?" + urllib.parse.urlencode(query)

    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True
    )
    thread.start()
    try:
        if on_url:
            on_url(url)
        try:
            opened = bool(webbrowser.open(url))
        except Exception:
            opened = False
        if on_status:
            on_status(
                "已打开系统浏览器，请在 Microsoft 页面完成登录…"
                if opened
                else "无法自动打开浏览器，请点击“重新打开登录页”。"
            )

        deadline = time.monotonic() + max(60, int(timeout))
        while not server.done.is_set():
            if cancel_event is not None:
                if cancel_event.wait(0.25):
                    raise MicrosoftAuthCancelled()
            else:
                time.sleep(0.25)
            if time.monotonic() >= deadline:
                raise MicrosoftAuthError(
                    "等待 Microsoft 登录超时。请重试，并确认浏览器已完成授权。"
                )
        result = dict(server.result)
    finally:
        try:
            server.shutdown()
        except Exception:
            pass
        server.server_close()
        thread.join(timeout=1)

    if str(result.get("state") or "") != state:
        raise MicrosoftAuthError("登录回调校验失败，请重新登录 Microsoft 账户。")
    error = str(result.get("error") or "")
    if error:
        raise MicrosoftAuthError(
            _browser_error_message(
                error, str(result.get("error_description") or "").strip()
            )
        )
    code = str(result.get("code") or "")
    if not code:
        raise MicrosoftAuthError("Microsoft 登录没有返回授权码，请重试。")

    if on_status:
        on_status("正在完成 Microsoft 与 Minecraft 账户验证…")
    try:
        payload = _post_form(
            TOKEN_URL,
            {
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
                "scope": SCOPE,
            },
        )
    except _HttpFailure as exc:
        raise MicrosoftAuthError(_oauth_message(exc)) from None

    access_token = str(payload.get("access_token") or "")
    if not access_token:
        raise MicrosoftAuthError("Microsoft 没有返回访问令牌，请重新登录。")
    try:
        expires_in = int(payload.get("expires_in") or 3600)
    except Exception:
        expires_in = 3600
    account = Account(
        kind="msa",
        access_token=access_token,
        refresh_token=str(payload.get("refresh_token") or ""),
        expires_at=time.time() + expires_in,
    )
    return _complete_chain(account)


def refresh_microsoft_token(client_id: str, refresh_token: str) -> TokenResult:
    client_id = normalize_client_id(client_id)
    if not refresh_token:
        raise MicrosoftAuthError("登录状态已失效，请重新登录 Microsoft 账户。")
    try:
        payload = _post_form(
            TOKEN_URL,
            {
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": refresh_token,
                "scope": SCOPE,
            },
        )
    except _HttpFailure as exc:
        raise MicrosoftAuthError(_oauth_message(exc)) from None
    token = str(payload.get("access_token") or "")
    if not token:
        raise MicrosoftAuthError("无法刷新 Microsoft 登录状态，请重新登录。")
    try:
        expires = int(payload.get("expires_in") or 3600)
    except Exception:
        expires = 3600
    return TokenResult(
        access_token=token,
        refresh_token=str(payload.get("refresh_token") or refresh_token),
        expires_in=expires,
    )


def _xbox_user_token(ms_access_token: str) -> tuple[str, str, str]:
    last: _HttpFailure | None = None
    for ticket in (f"d={ms_access_token}", ms_access_token):
        try:
            payload = _post_json(
                XBOX_USER_URL,
                {
                    "Properties": {
                        "AuthMethod": "RPS",
                        "SiteName": "user.auth.xboxlive.com",
                        "RpsTicket": ticket,
                    },
                    "RelyingParty": "http://auth.xboxlive.com",
                    "TokenType": "JWT",
                },
            )
            break
        except _HttpFailure as exc:
            last = exc
    else:
        raise MicrosoftAuthError(
            f"Xbox Live 身份验证失败（HTTP {last.status if last else '?'}）。"
        )
    token = str(payload.get("Token") or "")
    claims = (payload.get("DisplayClaims") or {}).get("xui") or []
    uhs = str((claims[0] if claims else {}).get("uhs") or "")
    xuid = str((claims[0] if claims else {}).get("xid") or "")
    if not token or not uhs:
        raise MicrosoftAuthError("Xbox Live 没有返回有效的用户令牌。")
    return token, uhs, xuid


def _xsts_token(user_token: str) -> tuple[str, str, str]:
    try:
        payload = _post_json(
            XSTS_URL,
            {
                "Properties": {
                    "SandboxId": "RETAIL",
                    "UserTokens": [user_token],
                },
                "RelyingParty": "rp://api.minecraftservices.com/",
                "TokenType": "JWT",
            },
        )
    except _HttpFailure as exc:
        try:
            xerr = int(exc.payload.get("XErr") or 0)
        except Exception:
            xerr = 0
        message = XSTS_MESSAGES.get(xerr)
        if message:
            raise MicrosoftAuthError(message) from None
        raise MicrosoftAuthError(
            f"Xbox 安全令牌验证失败（HTTP {exc.status}"
            + (f"，XErr {xerr}" if xerr else "")
            + "）。"
        ) from None
    token = str(payload.get("Token") or "")
    claims = (payload.get("DisplayClaims") or {}).get("xui") or []
    uhs = str((claims[0] if claims else {}).get("uhs") or "")
    xuid = str((claims[0] if claims else {}).get("xid") or "")
    if not token or not uhs:
        raise MicrosoftAuthError("Xbox 安全令牌响应不完整。")
    return token, uhs, xuid


def minecraft_login(xsts_token: str, uhs: str) -> tuple[str, int]:
    try:
        payload = _post_json(
            MC_LOGIN_URL,
            {"identityToken": f"XBL3.0 x={uhs};{xsts_token}"},
        )
    except _HttpFailure as exc:
        if exc.status in (400, 401, 403):
            raise MicrosoftAuthError(
                "Minecraft 登录被拒绝，请确认该账户已购买 Minecraft Java Edition。"
            ) from None
        raise MicrosoftAuthError(
            f"Minecraft 登录失败（HTTP {exc.status}）。"
        ) from None
    token = str(payload.get("access_token") or "")
    if not token:
        raise MicrosoftAuthError("Minecraft 服务没有返回访问令牌。")
    try:
        expires_in = int(payload.get("expires_in") or 86400)
    except Exception:
        expires_in = 86400
    return token, expires_in


def fetch_profile(mc_access_token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        MC_PROFILE_URL,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Authorization": f"Bearer {mc_access_token}",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        if exc.status == 404:
            raise MicrosoftAuthError(
                "该 Microsoft 账户没有 Minecraft Java Edition，或还没有创建游戏档案。"
            ) from None
        if exc.status == 401:
            raise MicrosoftAuthError("Minecraft 登录已失效，请重新登录。") from None
        raise MicrosoftAuthError(
            f"获取 Minecraft 档案失败（HTTP {exc.status}）。"
        ) from None
    except urllib.error.URLError as exc:
        raise MicrosoftAuthError(f"无法连接 Minecraft 服务：{exc.reason}") from None
    payload = _decode(raw)
    if not payload.get("id") or not payload.get("name"):
        raise MicrosoftAuthError("Minecraft 档案数据不完整。")
    return payload


def _complete_chain(account: Account) -> Account:
    user_token, uhs, xuid = _xbox_user_token(account.access_token)
    xsts_token, xsts_uhs, xsts_xuid = _xsts_token(user_token)
    mc_token, expires_in = minecraft_login(xsts_token, xsts_uhs or uhs)
    profile = fetch_profile(mc_token)
    account.mc_access_token = mc_token
    account.mc_expires_at = time.time() + max(0, expires_in - 120)
    account.uuid = str(profile.get("id") or "")
    account.name = str(profile.get("name") or "")
    account.xuid = xsts_xuid or xuid
    if not account.uuid or not account.name:
        raise MicrosoftAuthError("无法读取 Minecraft 账户名称。")
    return account


def login_with_device_code(
    client_id: str,
    *,
    on_code: Callable[[DeviceCode], None] | None = None,
    on_status: Callable[[str], None] | None = None,
    cancel_event=None,
) -> Account:
    client_id = normalize_client_id(client_id)
    device = request_device_code(client_id)
    if on_code:
        on_code(device)
    token = poll_device_code(
        client_id,
        device.device_code,
        interval=device.interval,
        expires_in=device.expires_in,
        cancel_event=cancel_event,
        on_status=on_status,
    )
    account = Account(
        kind="msa",
        access_token=token.access_token,
        refresh_token=token.refresh_token,
        expires_at=time.time() + token.expires_in,
    )
    if on_status:
        on_status("正在验证 Xbox 与 Minecraft 账户…")
    return _complete_chain(account)


def ensure_minecraft_token(account: Account, client_id: str) -> Account:
    """确保正版账户拥有可用的 Minecraft 访问令牌。"""
    if not account.is_msa:
        return account
    if (
        account.mc_access_token
        and account.mc_expires_at > time.time() + 60
        and account.name
        and account.uuid
    ):
        return account
    if not client_id:
        raise MicrosoftAuthError(
            "缺少 Azure 客户端 ID，无法刷新正版登录状态。请到设置中填写后重试。"
        )
    token = refresh_microsoft_token(client_id, account.refresh_token)
    account.access_token = token.access_token
    account.refresh_token = token.refresh_token or account.refresh_token
    account.expires_at = time.time() + token.expires_in
    return _complete_chain(account)
