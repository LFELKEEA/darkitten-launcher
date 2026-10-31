from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QBuffer, QByteArray, Signal  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

import darkitten.config as config_module  # noqa: E402
import darkitten.littleskin as ls  # noqa: E402
import darkitten.skin_library as library  # noqa: E402
from darkitten.accounts import Account  # noqa: E402
from darkitten.config import Config  # noqa: E402
from darkitten.skins import default_skin, validate_skin_bytes  # noqa: E402
from darkitten.ui import theme  # noqa: E402
from darkitten.ui.pages import skin as skin_page_module  # noqa: E402

CHECKS: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name} FAILED {detail}")
    CHECKS.append(name)
    print(f"{name}: OK")


def png_bytes() -> bytes:
    image = default_skin()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QBuffer.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return bytes(data)


def texture_value(model: str = "") -> str:
    skin = {
        "url": "https://littleskin.cn/textures/test-skin-hash",
    }
    if model:
        skin["metadata"] = {"model": model}
    payload = {
        "timestamp": 1,
        "profileId": "0123456789abcdef0123456789abcdef",
        "profileName": "TestCat",
        "textures": {"SKIN": skin},
    }
    return base64.b64encode(
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
    ).decode("ascii")


def profile_payload(model: str = "") -> dict:
    return {
        "id": "0123456789abcdef0123456789abcdef",
        "name": "TestCat",
        "properties": [
            {"name": "textures", "value": texture_value(model)},
        ],
    }


class FakeMain(QWidget):
    account_changed = Signal()

    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config
        self.toasts: list[tuple[str, str]] = []

    def current_account(self) -> Account:
        return Account.offline(self.config.player_name)

    def toast(self, text: str, kind: str = "info") -> None:
        self.toasts.append((text, kind))

    def open_path(self, path) -> None:
        pass

    def switch_page(self, page: str) -> None:
        pass


def test_account_roundtrip() -> None:
    account = ls.LittleSkinAccount(
        username="cat@example.com",
        name="TestCat",
        uuid="0123456789abcdef0123456789abcdef",
        access_token="secret-access-token",
        client_token="secret-client-token",
        variant="slim",
        skin_url="https://littleskin.cn/textures/x",
        last_sync=123.0,
        profiles=[{"id": "0123", "name": "TestCat"}],
    )
    raw = account.to_config()
    check("config-no-password-field", "password" not in raw)
    check(
        "config-token-protected",
        raw["access_token"] != account.access_token
        and raw["client_token"] != account.client_token,
        str(raw),
    )
    loaded = ls.LittleSkinAccount.from_config(raw)
    check("config-token-roundtrip", loaded.access_token == account.access_token)
    check("config-client-roundtrip", loaded.client_token == account.client_token)
    check("config-variant", loaded.variant == "slim")
    check("config-profiles", loaded.profiles == account.profiles)
    check("config-logged-in", loaded.is_logged_in)
    check("short-uuid", "…" in account.short_uuid, account.short_uuid)
    check("empty-not-logged-in", not ls.LittleSkinAccount().is_logged_in)


def test_login_and_skin(monkeypatch) -> None:
    calls: list[str] = []

    def fake_request(url, payload=None, *, token="", timeout=25):
        calls.append(url)
        if url == ls.AUTHENTICATE_URL:
            check("auth-agent", payload["agent"]["name"] == "Minecraft", str(payload))
            check("auth-password-not-in-url", "password" not in url)
            return {
                "accessToken": "token-1",
                "clientToken": payload["clientToken"],
                "selectedProfile": {
                    "id": "0123456789abcdef0123456789abcdef",
                    "name": "TestCat",
                },
                "availableProfiles": [
                    {
                        "id": "0123456789abcdef0123456789abcdef",
                        "name": "TestCat",
                    }
                ],
            }
        if url.startswith(ls.PROFILE_URL.split("{")[0]):
            return profile_payload("slim")
        raise AssertionError(url)

    monkeypatch.setattr(ls, "_request_json", fake_request)
    monkeypatch.setattr(ls, "_download_texture", lambda url: png_bytes())

    account, skin = ls.login("cat@example.com", "hunter2")
    check("login-token", account.access_token == "token-1")
    check("login-client-token", bool(account.client_token))
    check("login-name", account.name == "TestCat")
    check("login-uuid", account.uuid.startswith("0123456789abcdef"))
    check("login-profiles", len(account.profiles) == 1)
    check("login-skin-present", skin is not None)
    check("login-skin-size", skin is not None and skin.image.width() == 64)
    check("login-variant-slim", account.variant == "slim")
    check("login-last-sync", account.last_sync > 0)
    check("login-profile-fetched", any("sessionserver" in item for item in calls))


def test_login_no_profile(monkeypatch) -> None:
    monkeypatch.setattr(
        ls,
        "_request_json",
        lambda *args, **kwargs: {
            "accessToken": "token-2",
            "availableProfiles": [],
        },
    )
    try:
        ls.login("cat@example.com", "x")
    except ls.LittleSkinError as exc:
        check("no-profile-error", "角色" in str(exc), str(exc))
    else:
        raise AssertionError("no-profile-error FAILED")


def test_login_requires_credentials() -> None:
    for username, password, word in (
        ("", "x", "邮箱"),
        ("cat", "", "密码"),
    ):
        try:
            ls.login(username, password)
        except ls.LittleSkinError as exc:
            check(f"requires-{word}", word in str(exc), str(exc))
        else:
            raise AssertionError(f"requires-{word} FAILED")


def test_profile_without_skin(monkeypatch) -> None:
    payload = {
        "id": "0123456789abcdef0123456789abcdef",
        "name": "TestCat",
        "properties": [],
    }
    monkeypatch.setattr(ls, "_request_json", lambda *args, **kwargs: payload)
    account = ls.LittleSkinAccount(
        username="cat",
        name="TestCat",
        uuid="0123456789abcdef0123456789abcdef",
        access_token="token",
        client_token="client",
    )
    skin = ls.fetch_skin(account)
    check("no-skin-returns-none", skin is None)
    check("no-skin-clears-url", account.skin_url == "")
    check("no-skin-records-sync", account.last_sync > 0)


def test_invalid_texture(monkeypatch) -> None:
    payload = profile_payload("")
    payload["properties"][0]["value"] = "not-base64"
    monkeypatch.setattr(ls, "_request_json", lambda *args, **kwargs: payload)
    account = ls.LittleSkinAccount(
        name="TestCat",
        uuid="0123456789abcdef0123456789abcdef",
        access_token="token",
    )
    check("invalid-texture-none", ls.fetch_skin(account) is None)


def test_error_message() -> None:
    check(
        "error-api-message",
        ls._error_message(b'{"errorMessage":"Incorrect username or password."}', 403)
        == "Incorrect username or password.",
    )
    check(
        "error-401",
        "重新登录" in ls._error_message(b"", 401),
    )
    check("error-429", "频繁" in ls._error_message(b"", 429))


def test_ui_layout_and_apply() -> None:
    app = QApplication.instance() or QApplication([])
    app.setFont(QFont("Microsoft YaHei UI", 9))
    theme.set_current("dark")
    app.setStyleSheet(theme.stylesheet("dark"))
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = root / "library"
        library.LIBRARY_DIR = target
        library.INDEX_PATH = target / "index.json"
        config_module.CONFIG_PATH = root / "config.json"
        cfg = Config(game_dir=str(root / "minecraft"), player_name="LFELKEEA")
        main = FakeMain(cfg)
        page = skin_page_module.SkinPage(main)
        main.resize(980, 594)
        page.resize(980, 594)
        page.show()
        app.processEvents()
        page._fit_preview()
        app.processEvents()

        preview_pos = page._preview_card.mapTo(page, page._preview_card.rect().topLeft())
        community_pos = page._community_card.mapTo(
            page, page._community_card.rect().topLeft()
        )
        check(
            "community-below-preview",
            community_pos.y() >= preview_pos.y() + page._preview_card.height(),
            f"{preview_pos.y()} -> {community_pos.y()}",
        )
        check(
            "community-same-width",
            page._community_card.width() == page._preview_card.width(),
            f"{page._community_card.width()} vs {page._preview_card.width()}",
        )
        check("littleskin-login-visible", page.ls_login_btn.isVisible())
        check("littleskin-open-visible", page.ls_open_btn.isVisible())
        check(
            "community-fills-left",
            page._community_card.width() >= 300,
            str(page._community_card.width()),
        )

        account = ls.LittleSkinAccount(
            username="cat@example.com",
            name="TestCat",
            uuid="0123456789abcdef0123456789abcdef",
            access_token="token",
            client_token="client",
            variant="slim",
            last_sync=123.0,
        )
        page._littleskin = account
        page._refresh_littleskin()
        check("littleskin-title", "TestCat" in page.ls_title.text(), page.ls_title.text())
        check("littleskin-login-hidden", not page.ls_login_btn.isVisible())
        check("littleskin-sync-visible", page.ls_sync_btn.isVisible())

        skin = validate_skin_bytes(png_bytes(), source="test")
        page._apply_littleskin_skin(account, skin)
        app.processEvents()
        check("littleskin-skin-applied", page.preview.isVisible())
        check(
            "littleskin-skin-path",
            Path(cfg.skin_path).is_file(),
            cfg.skin_path,
        )
        check(
            "littleskin-current-label",
            "LittleSkin" in page.current_label.text(),
            page.current_label.text(),
        )
        check(
            "littleskin-library-entry",
            any("LittleSkin" in item.name for item in library.list_skins()),
            str([item.name for item in library.list_skins()]),
        )
        check(
            "littleskin-variant",
            cfg.skin_variant == "slim",
            cfg.skin_variant,
        )

        saved = ls.load_account(cfg)
        check("littleskin-saved", saved.is_logged_in and saved.name == "TestCat")
        ls.clear_account(cfg)
        check("littleskin-cleared", not ls.load_account(cfg).is_logged_in)

        page.close()
        page.deleteLater()
        main.close()
        main.deleteLater()
        app.processEvents()


def main() -> None:
    # 用简单的 monkeypatch 上下文，避免引入额外依赖。
    class MonkeyPatch:
        def __init__(self) -> None:
            self._saved: list[tuple[object, str, object]] = []

        def setattr(self, target, name: str, value) -> None:
            self._saved.append((target, name, getattr(target, name)))
            setattr(target, name, value)

        def undo(self) -> None:
            for target, name, value in reversed(self._saved):
                setattr(target, name, value)
            self._saved.clear()

    test_account_roundtrip()
    for func in (
        test_login_and_skin,
        test_login_no_profile,
        test_profile_without_skin,
        test_invalid_texture,
    ):
        patch = MonkeyPatch()
        try:
            func(patch)
        finally:
            patch.undo()
    test_login_requires_credentials()
    test_error_message()
    test_ui_layout_and_apply()
    print(f"\n{len(CHECKS)} checks passed")


if __name__ == "__main__":
    main()
