from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QWidget

import darkitten.microsoft_auth as auth
from darkitten.accounts import Account
from darkitten.config import Config
from darkitten.ui import account_dialog

GUID = "12345678-1234-1234-1234-123456789abc"
OTHER = "87654321-4321-4321-4321-cba987654321"
LEGACY = "00000000441cc96b"

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


class FakeWorker(QObject):
    url_ready = Signal(str)
    code_ready = Signal(object)
    status = Signal(str)
    finished = Signal(object, str)

    def __init__(self, client_id: str, *args, **kwargs) -> None:
        super().__init__(kwargs.get("parent"))
        self.client_id = client_id
        self.email = args[0] if args else ""
        self.started = False
        self.cancelled = False

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True


class FakeMain(QWidget):
    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config


def test_normalize() -> None:
    check("normalize_accepts_guid", auth.normalize_client_id(f"  {GUID}  ") == GUID)
    check(
        "normalize_accepts_legacy_app_id",
        auth.normalize_client_id(f"  {LEGACY}  ") == LEGACY,
    )
    check(
        "normalize_accepts_uppercase_legacy_app_id",
        auth.normalize_client_id("00000000441CC96B") == "00000000441CC96B",
    )
    for name, value in (
        ("normalize_rejects_empty", ""),
        ("normalize_rejects_plain_text", "darkitten"),
        ("normalize_rejects_short_guid", "12345678-1234-1234-1234"),
        ("normalize_rejects_15_hex", "00000000441cc96"),
    ):
        try:
            auth.normalize_client_id(value)
        except auth.MicrosoftAuthError:
            check(name, True)
        else:
            check(name, False)


def test_legacy_flow_routing() -> None:
    check("is_legacy_true", auth.is_legacy_client_id(LEGACY))
    check("is_legacy_false_for_guid", not auth.is_legacy_client_id(GUID))

    calls: list[tuple[str, dict]] = []
    original_cookie_post = auth._post_form_with_cookie

    def fake_cookie_post(url, fields, timeout=30):
        calls.append((url, dict(fields)))
        return (
            {
                "device_code": "live-device",
                "user_code": "CODE1234",
                "verification_uri": "https://www.microsoft.com/link",
                "expires_in": 900,
                "interval": 5,
            },
            "cookie=live",
        )

    auth._post_form_with_cookie = fake_cookie_post
    try:
        device = auth.request_device_code(LEGACY)
        check(
            "live_device_uses_live_endpoint",
            calls and calls[0][0] == auth.LIVE_DEVICE_CODE_URL,
            calls[0][0] if calls else "",
        )
        check(
            "live_device_uses_mbi_scope",
            calls and calls[0][1].get("scope") == auth.LIVE_SCOPE,
            str(calls[0][1]) if calls else "",
        )
        check("live_device_returns_cookie", device.cookie == "cookie=live")
    finally:
        auth._post_form_with_cookie = original_cookie_post

    v2_calls: list[tuple[str, dict]] = []
    original_post = auth._post_form

    def fake_v2_post(url, fields, timeout=30, headers=None):
        v2_calls.append((url, dict(fields)))
        return {
            "device_code": "v2-device",
            "user_code": "V2CODE",
            "verification_uri": "https://microsoft.com/devicelogin",
            "expires_in": 900,
            "interval": 5,
        }

    auth._post_form = fake_v2_post
    try:
        auth.request_device_code(GUID)
        check(
            "guid_device_uses_v2_endpoint",
            v2_calls and v2_calls[0][0] == auth.DEVICE_CODE_URL,
            v2_calls[0][0] if v2_calls else "",
        )
        check(
            "guid_device_uses_v2_scope",
            v2_calls and v2_calls[0][1].get("scope") == auth.SCOPE,
            str(v2_calls[0][1]) if v2_calls else "",
        )
    finally:
        auth._post_form = original_post

    try:
        auth.login_with_browser(LEGACY)
    except auth.MicrosoftAuthError as exc:
        check("browser_rejects_legacy_with_hint", "验证码" in str(exc), str(exc))
    else:
        check("browser_rejects_legacy_with_hint", False)

    poll_calls: list[tuple[str, dict, dict | None]] = []
    responses = [
        {"error": "authorization_pending"},
        {"access_token": "live-at", "refresh_token": "live-rt", "expires_in": 3600},
    ]
    original_post = auth._post_form
    original_sleep = auth.time.sleep

    def fake_post(url, fields, timeout=30, headers=None):
        poll_calls.append((url, dict(fields), headers))
        return responses.pop(0)

    auth._post_form = fake_post
    auth.time.sleep = lambda _seconds: None
    try:
        token = auth.poll_device_code(
            LEGACY,
            "live-device",
            interval=1,
            expires_in=5,
            cookie="cookie=live",
        )
        check(
            "live_poll_uses_live_token_url",
            poll_calls and poll_calls[0][0].startswith(auth.LIVE_TOKEN_URL),
            poll_calls[0][0] if poll_calls else "",
        )
        check(
            "live_poll_sends_cookie",
            poll_calls and poll_calls[0][2] == {"Cookie": "cookie=live"},
            str(poll_calls[0][2]) if poll_calls else "",
        )
        check("live_poll_returns_token", token.access_token == "live-at")
    finally:
        auth._post_form = original_post
        auth.time.sleep = original_sleep

    refresh_calls: list[tuple[str, dict]] = []

    def fake_refresh_post(url, fields, timeout=30, headers=None):
        refresh_calls.append((url, dict(fields)))
        return {
            "access_token": "refreshed-at",
            "refresh_token": "refreshed-rt",
            "expires_in": 3600,
        }

    auth._post_form = fake_refresh_post
    try:
        refreshed = auth.refresh_microsoft_token(LEGACY, "live-rt")
        check(
            "live_refresh_uses_live_token_url",
            refresh_calls and refresh_calls[0][0] == auth.LIVE_TOKEN_URL,
            refresh_calls[0][0] if refresh_calls else "",
        )
        check(
            "live_refresh_uses_mbi_scope",
            refresh_calls and refresh_calls[0][1].get("scope") == auth.LIVE_SCOPE,
            str(refresh_calls[0][1]) if refresh_calls else "",
        )
        check("live_refresh_returns_token", refreshed.access_token == "refreshed-at")
    finally:
        auth._post_form = original_post

    tickets: list[str] = []
    original_json = auth._post_json

    def fake_json(url, payload, timeout=30):
        tickets.append(payload["Properties"]["RpsTicket"])
        return {
            "Token": "xbl-token",
            "DisplayClaims": {"xui": [{"uhs": "uhs", "xid": "xid"}]},
        }

    auth._post_json = fake_json
    try:
        _, uhs, _ = auth._xbox_user_token("live-at", legacy=True)
        check("live_xbox_uses_t_ticket", tickets == ["t=live-at"], str(tickets))
        check("live_xbox_returns_uhs", uhs == "uhs")
        tickets.clear()
        auth._xbox_user_token("azure-at", legacy=False)
        check("azure_xbox_uses_d_ticket", tickets == ["d=azure-at"], str(tickets))
    finally:
        auth._post_json = original_json


def test_account_roundtrip() -> None:
    account = Account(
        kind="msa",
        name="Tester",
        uuid="uuid",
        access_token="ms",
        refresh_token="refresh",
        client_id=GUID,
        expires_at=123.0,
        mc_access_token="mc",
        mc_expires_at=456.0,
        xuid="xuid",
    )
    restored = Account.from_config(account.to_config())
    check("account_client_id_roundtrip", restored.client_id == GUID, restored.client_id)
    legacy = Account.from_config(
        {
            "kind": "msa",
            "name": "Old",
            "uuid": "uuid",
            "access_token": "ms",
            "refresh_token": "refresh",
        }
    )
    check("legacy_account_without_client_id", legacy.client_id == "", legacy.client_id)


def test_refresh_uses_login_client_id() -> None:
    calls: list[str] = []
    original_refresh = auth.refresh_microsoft_token
    original_chain = auth._complete_chain

    def fake_refresh(client_id: str, refresh_token: str) -> auth.TokenResult:
        calls.append(client_id)
        return auth.TokenResult(
            access_token="new-ms", refresh_token="new-refresh", expires_in=3600
        )

    def fake_chain(account: Account) -> Account:
        account.name = account.name or "Tester"
        account.uuid = account.uuid or "uuid"
        account.mc_access_token = "new-mc"
        account.mc_expires_at = time.time() + 3600
        return account

    auth.refresh_microsoft_token = fake_refresh
    auth._complete_chain = fake_chain
    try:
        account = Account(
            kind="msa",
            name="Tester",
            uuid="uuid",
            refresh_token="refresh",
            client_id=OTHER,
        )
        auth.ensure_minecraft_token(account, GUID)
        check("refresh_prefers_account_client_id", calls == [OTHER], str(calls))

        legacy = Account(
            kind="msa",
            name="Tester",
            uuid="uuid",
            refresh_token="refresh",
        )
        auth.ensure_minecraft_token(legacy, GUID)
        check("refresh_falls_back_to_setting", calls[-1:] == [GUID], str(calls))
        check("refresh_adopts_setting", legacy.client_id == GUID, legacy.client_id)
    finally:
        auth.refresh_microsoft_token = original_refresh
        auth._complete_chain = original_chain


def test_browser_dialog(app: QApplication) -> None:
    config = Config()
    config.save = lambda: None
    main = FakeMain(config)
    original_browser_worker = account_dialog.BrowserLoginWorker
    original_device_worker = account_dialog.DeviceLoginWorker
    account_dialog.BrowserLoginWorker = FakeWorker
    account_dialog.DeviceLoginWorker = FakeWorker
    try:
        dialog = account_dialog.BrowserLoginDialog(main, "")
        check("browser_has_client_edit", dialog.client_edit.text() == "")
        dialog.client_edit.setText("not-a-guid")
        dialog._start()
        check(
            "browser_blocks_invalid_client_id",
            dialog._worker is None
            and not dialog.error.isHidden()
            and bool(dialog.error.text()),
            dialog.error.text(),
        )

        dialog.client_edit.setText(GUID)
        dialog._start()
        worker = dialog._worker
        check(
            "browser_uses_edited_client_id",
            isinstance(worker, FakeWorker)
            and worker.client_id == GUID
            and worker.started,
            getattr(worker, "client_id", ""),
        )
        check("browser_saves_client_id", config.microsoft_client_id == GUID)
        check("browser_locks_client_edit", not dialog.client_edit.isEnabled())
        dialog.reject()

        legacy = account_dialog.BrowserLoginDialog(main, "")
        legacy.client_edit.setText(LEGACY)
        check(
            "browser_shows_legacy_warning",
            not legacy.legacy_box.isHidden(),
        )
        check(
            "browser_legacy_button_text",
            legacy.start_btn.text() == "使用验证码登录",
            legacy.start_btn.text(),
        )
        legacy._start()
        check(
            "browser_legacy_requires_risk_confirm",
            legacy._worker is None
            and not legacy.error.isHidden()
            and bool(legacy.error.text()),
            legacy.error.text(),
        )
        legacy.risk_check.setChecked(True)
        legacy._start()
        check(
            "browser_legacy_uses_device_worker",
            isinstance(legacy._worker, FakeWorker)
            and legacy._worker.client_id == LEGACY
            and legacy._worker.started,
            getattr(legacy._worker, "client_id", ""),
        )
        check("browser_saves_legacy_app_id", config.microsoft_client_id == LEGACY)
        legacy.reject()
    finally:
        account_dialog.BrowserLoginWorker = original_browser_worker
        account_dialog.DeviceLoginWorker = original_device_worker
        main.close()


def test_device_dialog(app: QApplication) -> None:
    config = Config()
    config.save = lambda: None
    main = FakeMain(config)
    original_worker = account_dialog.DeviceLoginWorker
    account_dialog.DeviceLoginWorker = FakeWorker
    try:
        dialog = account_dialog.MicrosoftLoginDialog(main, GUID)
        check("device_prefills_client_id", dialog.client_edit.text() == GUID)
        dialog.client_edit.setText("")
        dialog._start()
        check(
            "device_blocks_empty_client_id",
            dialog._worker is None and not dialog.error.isHidden(),
            dialog.error.text(),
        )

        dialog.client_edit.setText(OTHER)
        dialog._start()
        worker = dialog._worker
        check(
            "device_uses_edited_client_id",
            isinstance(worker, FakeWorker)
            and worker.client_id == OTHER
            and worker.started,
            getattr(worker, "client_id", ""),
        )
        check("device_saves_client_id", config.microsoft_client_id == OTHER)
        dialog.reject()

        legacy = account_dialog.MicrosoftLoginDialog(main, LEGACY)
        check(
            "device_shows_legacy_warning",
            not legacy.legacy_box.isHidden(),
        )
        check(
            "device_legacy_disables_login",
            not legacy.login_btn.isEnabled(),
        )
        legacy._start()
        check(
            "device_legacy_requires_risk_confirm",
            legacy._worker is None
            and not legacy.error.isHidden()
            and bool(legacy.error.text()),
            legacy.error.text(),
        )
        legacy.risk_check.setChecked(True)
        legacy._start()
        check(
            "device_legacy_uses_device_worker",
            isinstance(legacy._worker, FakeWorker)
            and legacy._worker.client_id == LEGACY
            and legacy._worker.started,
            getattr(legacy._worker, "client_id", ""),
        )
        legacy.reject()
    finally:
        account_dialog.DeviceLoginWorker = original_worker
        main.close()


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    test_normalize()
    test_legacy_flow_routing()
    test_account_roundtrip()
    test_refresh_uses_login_client_id()
    test_browser_dialog(app)
    test_device_dialog(app)
    print(f"\nclient_id_login: {PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())