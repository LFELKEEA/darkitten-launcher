from __future__ import annotations

from .accounts import Account

DEVELOPER_NAME = "LFELKEEA"
DEVELOPER_EMAIL = "lfel278623@outlook.com"

_developer_mode = False


def is_developer(config, account: Account | None) -> bool:
    """只把 LFELKEEA 且填写了指定 Microsoft 邮箱的账户识别为开发者。"""
    if account is None or not account.is_msa:
        return False
    if str(account.name or "").strip().casefold() != DEVELOPER_NAME.casefold():
        return False
    email = str(getattr(config, "microsoft_email", "") or "").strip().casefold()
    return email == DEVELOPER_EMAIL.casefold()


def set_developer_mode(enabled: bool) -> None:
    global _developer_mode
    _developer_mode = bool(enabled)


def developer_mode() -> bool:
    return _developer_mode
