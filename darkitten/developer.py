from __future__ import annotations

import hashlib
import hmac
from urllib.parse import quote

DEVELOPER_KEY_HASH = "81ce9dc6a39406b41cc60168771496fad2ecc539bab6fb88ada317a85795ba7f"
FEEDBACK_TABLE_URL = "https://lfelkeea.github.io/darkitten-launcher/feedback.html"
FEEDBACK_TABLE_SALT = "DKL::feedback-table::"

_developer_mode = False


def verify_developer_key(value: str) -> bool:
    """校验开发者口令，源码中只保存 SHA-256，不保存明文。"""
    text = str(value or "").strip()
    if not text:
        return False
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return hmac.compare_digest(digest, DEVELOPER_KEY_HASH)


def developer_page_token(value: str) -> str:
    """把口令派生为反馈表访问令牌，令牌本身不会写进网站源码。"""
    text = str(value or "").strip()
    return hashlib.sha256((FEEDBACK_TABLE_SALT + text).encode("utf-8")).hexdigest()


def unlock_developer(config, key: str) -> bool:
    if not verify_developer_key(key):
        return False
    config.developer_unlocked = True
    config.developer_page_token = developer_page_token(key)
    set_developer_mode(True)
    return True


def lock_developer(config) -> None:
    config.developer_unlocked = False
    config.developer_page_token = ""
    set_developer_mode(False)


def feedback_table_url(config) -> str:
    token = str(getattr(config, "developer_page_token", "") or "").strip()
    if not token:
        return FEEDBACK_TABLE_URL
    return f"{FEEDBACK_TABLE_URL}#key={quote(token, safe='')}"


def set_developer_mode(enabled: bool) -> None:
    global _developer_mode
    _developer_mode = bool(enabled)


def developer_mode() -> bool:
    return _developer_mode
