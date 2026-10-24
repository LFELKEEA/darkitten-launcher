from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import urllib.parse
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication, QLineEdit, QWidget

import darkitten.developer as developer_module
from darkitten.accounts import Account
from darkitten.config import APP_VERSION, Config
from darkitten.developer import (
    FEEDBACK_TABLE_SALT,
    developer_mode,
    developer_page_token,
    feedback_table_url,
    lock_developer,
    set_developer_mode,
    unlock_developer,
    verify_developer_key,
)
from darkitten.feedback import (
    build_feedback_payload,
    build_github_issue_url,
    build_mailto_url,
    build_site_feedback_url,
    merge_feedback_items,
    normalize_github_issue,
)
from darkitten.ui import feedback_dialog as feedback_dialog_module
from darkitten.ui.feedback_dialog import FeedbackDialog
from darkitten.ui.main_window import AccountChip, MainWindow

PASS = 0
FAIL = 0
TEST_KEY = "unit-test-developer-key"


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"{name}: OK{(' (' + detail + ')') if detail else ''}")
    else:
        FAIL += 1
        print(f"{name}: FAIL{(' (' + detail + ')') if detail else ''}")


def pump(app: QApplication) -> None:
    for _ in range(3):
        app.processEvents()


class FakeMain(QWidget):
    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config
        self.messages: list[tuple[str, str]] = []

    def current_account(self) -> Account:
        return Account.from_config(self.config.account)

    def toast(self, text: str, kind: str = "info") -> None:
        self.messages.append((text, kind))


def developer_test() -> None:
    original_hash = developer_module.DEVELOPER_KEY_HASH
    try:
        developer_module.DEVELOPER_KEY_HASH = hashlib.sha256(TEST_KEY.encode()).hexdigest()
        check("developer_key_empty", not verify_developer_key(""))
        check("developer_key_wrong", not verify_developer_key("not-the-key"))
        check("developer_key_match", verify_developer_key(TEST_KEY))

        config = Config()
        config.developer_unlocked = False
        config.developer_page_token = ""
        check("developer_unlock_wrong", not unlock_developer(config, "not-the-key"))
        check("developer_unlock_wrong_state", not config.developer_unlocked)
        check("developer_unlock_ok", unlock_developer(config, TEST_KEY))
        check("developer_unlock_state", config.developer_unlocked and developer_mode())
        check(
            "developer_unlock_token",
            config.developer_page_token == developer_page_token(TEST_KEY),
        )
        expected = hashlib.sha256((FEEDBACK_TABLE_SALT + TEST_KEY).encode()).hexdigest()
        check("developer_page_token_hash", developer_page_token(TEST_KEY) == expected)
        url = feedback_table_url(config)
        check("developer_table_url", url.startswith("https://lfelkeea.github.io/"), url)
        check("developer_table_token", url.endswith("#key=" + config.developer_page_token))
        lock_developer(config)
        check("developer_lock_state", not config.developer_unlocked and not developer_mode())
        check("developer_lock_token", config.developer_page_token == "")
        check("developer_table_url_locked", "#key=" not in feedback_table_url(config))
    finally:
        developer_module.DEVELOPER_KEY_HASH = original_hash
        developer_module.set_developer_mode(False)


def payload_test() -> None:
    payload = build_feedback_payload(
        kind="suggestion",
        title="  希望下载中心支持排序  ",
        description="希望可以按完成时间排序，找刚下载的文件会方便很多。",
        contact="lfel278623@outlook.com",
    )
    check("payload_kind", payload["type"] == "suggestion", payload["type"])
    check("payload_title_trim", payload["title"] == "希望下载中心支持排序")
    check("payload_source", payload["source"] == "launcher")
    check("payload_page", payload["page"] == "launcher:feedback")
    check("payload_version", payload["version"] == APP_VERSION, payload["version"])

    site_url = build_site_feedback_url(payload)
    parts = urllib.parse.urlsplit(site_url)
    query = urllib.parse.parse_qs(parts.query)
    check("site_url_host", parts.netloc == "lfelkeea.github.io", parts.netloc)
    check("site_url_path", parts.path == "/darkitten-launcher/", parts.path)
    check("site_url_fragment", parts.fragment == "feedback-suggestion", parts.fragment)
    check("site_url_title", query.get("title", [""])[0] == payload["title"])
    check("site_url_type", query.get("type", [""])[0] == "suggestion")
    check("site_url_contact", query.get("contact", [""])[0] == "lfel278623@outlook.com")

    github_url = build_github_issue_url(payload)
    github_query = urllib.parse.parse_qs(urllib.parse.urlsplit(github_url).query)
    check("github_url_no_labels", "labels" not in github_query, str(github_query))
    check(
        "github_url_title",
        github_query.get("title", [""])[0] == "[DKL 改进建议] 希望下载中心支持排序",
        github_query.get("title", [""])[0],
    )
    check("github_url_body_meta", "**来源**：启动器" in github_query.get("body", [""])[0])

    bug = build_feedback_payload(kind="bug", title="启动报错", description="启动时提示缺少 Java，无法进入游戏。")
    bug_query = urllib.parse.parse_qs(urllib.parse.urlsplit(build_github_issue_url(bug)).query)
    check(
        "github_url_bug_title",
        bug_query.get("title", [""])[0] == "[DKL Bug] 启动报错",
        bug_query.get("title", [""])[0],
    )
    mail = build_mailto_url(payload)
    check("mailto_url", mail.startswith("mailto:lfel278623@outlook.com?"), mail[:60])


def github_normalize_test() -> None:
    issue = {
        "number": 12,
        "title": "[DKL 改进建议] 启动失败",
        "body": (
            "**来源**：网站\n"
            "**类型**：改进建议\n"
            "**启动器版本**：1.9.22\n"
            "**联系方式**：lfel278623@outlook.com\n"
            "**页面**：https://example.invalid/page\n"
            "**系统**：Windows 11\n"
            "\n---\n\n"
            "希望支持按完成时间排序。\n\n"
            "> 这条反馈通过 DKL 下载站提交，开发者可在网站反馈表查看。"
        ),
        "state": "closed",
        "created_at": "2026-10-24T10:00:00Z",
        "updated_at": "2026-10-24T11:00:00Z",
        "html_url": "https://github.com/LFELKEEA/darkitten-launcher/issues/12",
        "labels": [],
        "comments": 2,
    }
    record = normalize_github_issue(issue)
    check("github_normalize_record", record is not None)
    if record:
        check("github_normalize_id", record["id"] == "gh-12", record["id"])
        check("github_normalize_kind", record["kind"] == "suggestion", record["kind"])
        check("github_normalize_status", record["status"] == "resolved", record["status"])
        check("github_normalize_platform", record["platform"] == "github")
        check("github_normalize_version", record["version"] == "1.9.22", record["version"])
        check("github_normalize_contact", record["contact"] == "lfel278623@outlook.com")
        check("github_normalize_source", record["source"] == "website", record["source"])
        check("github_normalize_description", "希望支持按完成时间排序" in record["description"])
        check("github_normalize_meta_stripped", "**类型**" not in record["description"])
        check("github_normalize_quote_stripped", "这条反馈通过 DKL 下载站提交" not in record["description"])

    pr = dict(issue)
    pr["pull_request"] = {"url": "x"}
    check("github_skip_pr", normalize_github_issue(pr) is None)
    other = dict(issue)
    other["title"] = "普通 Issue"
    other["labels"] = [{"name": "question"}]
    check("github_skip_unrelated", normalize_github_issue(other) is None)
    labelled = dict(issue)
    labelled["title"] = "没有前缀但带标签"
    labelled["labels"] = [{"name": "bug"}]
    check("github_accept_bug_label", normalize_github_issue(labelled) is not None)
    check("github_skip_non_dict", normalize_github_issue(None) is None)  # type: ignore[arg-type]


def merge_test() -> None:
    merged = merge_feedback_items(
        [
            {"id": "a", "created_at": "2026-10-24T09:00:00Z", "title": "旧"},
            {"id": "b", "created_at": "2026-10-24T12:00:00Z", "title": "新"},
        ],
        [
            {"id": "a", "created_at": "2026-10-24T09:00:00Z", "title": "重复"},
            {"id": "gh-1", "created_at": "2026-10-24T10:30:00Z", "title": "GitHub"},
        ],
    )
    check("merge_dedup", len(merged) == 3, str(len(merged)))
    check(
        "merge_order",
        [item["id"] for item in merged] == ["b", "gh-1", "a"],
        str([item["id"] for item in merged]),
    )


def account_chip_test(app: QApplication) -> None:
    chip = AccountChip()
    chip.set_account("LFELKEEA", "正版", True)
    check("chip_dev_hidden_default", chip.dev_badge.isHidden())
    chip.set_developer(True)
    check("chip_dev_shown", not chip.dev_badge.isHidden())
    pump(app)
    chip.deleteLater()
    pump(app)


def dialog_test(app: QApplication) -> None:
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    config = Config(game_dir=str(root / "game"))
    config.microsoft_email = "lfel278623@outlook.com"
    config.account = Account(kind="msa", name="LFELKEEA", uuid="0" * 32).to_config()
    main = FakeMain(config)

    set_developer_mode(True)
    dialog = FeedbackDialog(main, kind="suggestion")
    pump(app)
    check("dialog_suggestion_title", dialog.form_title.text() == "提一个改进建议", dialog.form_title.text())
    check("dialog_suggestion_btn", dialog.submit_btn.text() == "在下载站提建议", dialog.submit_btn.text())
    check("dialog_dev_tabs", not dialog.tabs.isHidden() and dialog.tabs.current() == "compose")
    check("dialog_type_synced", dialog.type_combo.currentData() == "suggestion")
    check("dialog_has_table_page", dialog.stack.count() == 2)

    dialog.tabs.set_value("table", emit=True)
    pump(app)
    check("dialog_table_tab", dialog.stack.currentIndex() == 1)
    dialog.tabs.set_value("compose", emit=True)
    pump(app)

    dialog.set_kind("bug")
    check("dialog_bug_title", dialog.form_title.text() == "报告一个 Bug", dialog.form_title.text())
    dialog.title_edit.setText("启动失败")
    dialog.desc_edit.setPlainText("启动游戏时直接报错，退出代码 1。")
    payload = dialog._payload()
    check("dialog_payload_kind", payload["type"] == "bug", payload["type"])
    check("dialog_payload_contact", payload["contact"] == "lfel278623@outlook.com", payload["contact"])

    captured: list[str] = []
    original_desktop = feedback_dialog_module.QDesktopServices

    class FakeDesktopServices:
        @staticmethod
        def openUrl(url) -> None:
            captured.append(url.toString())

    feedback_dialog_module.QDesktopServices = FakeDesktopServices
    try:
        dialog._submit()
        pump(app)
    finally:
        feedback_dialog_module.QDesktopServices = original_desktop
    check("dialog_submit_opened", len(captured) == 1, str(captured))
    if captured:
        check("dialog_submit_site", "lfelkeea.github.io/darkitten-launcher/" in captured[0], captured[0][:90])
        check("dialog_submit_fragment", "#feedback-bug" in captured[0])
        check("dialog_submit_title", "title=" in captured[0] and "启动失败" in urllib.parse.unquote(captured[0]))
    check("dialog_submit_status", dialog.status_label.text().startswith("已打开下载站"), dialog.status_label.text())
    check("dialog_fallback_shown", not dialog.fallback_box.isHidden())
    dialog.deleteLater()
    pump(app)

    set_developer_mode(False)
    dialog2 = FeedbackDialog(main, kind="bug")
    pump(app)
    check("dialog_hides_table_for_user", dialog2.tabs.isHidden())
    check("dialog_user_compose", dialog2.stack.currentIndex() == 0)
    dialog2.deleteLater()
    pump(app)

    main.deleteLater()
    pump(app)
    temp.cleanup()


def settings_unlock_test(app: QApplication) -> None:
    original_hash = developer_module.DEVELOPER_KEY_HASH
    original_save = Config.save
    original_ensure_java = MainWindow.ensure_java_detected
    developer_module.DEVELOPER_KEY_HASH = hashlib.sha256(TEST_KEY.encode()).hexdigest()
    Config.save = lambda self: None
    MainWindow.ensure_java_detected = lambda self, force=False: None
    temp = tempfile.TemporaryDirectory()
    window = None
    try:
        game = Path(temp.name) / "game"
        (game / "versions" / "1.21.1").mkdir(parents=True)
        config = Config(
            game_dir=str(game),
            update_auto_check=False,
            developer_unlocked=False,
            developer_page_token="",
        )
        window = MainWindow(config)
        window._feedback_checked = True
        window.resize(1120, 760)
        window.apply_theme("dark")
        window.show()
        pump(app)

        page = window.settings_page
        check("settings_key_input", hasattr(page, "developer_key_edit"))
        check(
            "settings_key_password",
            page.developer_key_edit.echoMode() == QLineEdit.Password,
            str(page.developer_key_edit.echoMode()),
        )
        check("settings_unlock_visible", not page.developer_unlock_row.isHidden())
        check("settings_actions_hidden", page.developer_actions.isHidden())
        check("settings_badge_locked", page.developer_badge.text() == "未解锁")

        page.developer_key_edit.setText("wrong-key")
        page._unlock_developer()
        pump(app)
        check("settings_wrong_key_rejected", not config.developer_unlocked)
        check("settings_wrong_key_message", "不正确" in page.developer_status.text())

        page.developer_key_edit.setText(TEST_KEY)
        page._unlock_developer()
        pump(app)
        check("settings_key_unlocks", config.developer_unlocked)
        check("settings_key_token", config.developer_page_token == developer_page_token(TEST_KEY))
        check("settings_dev_mode", developer_mode())
        check("settings_key_input_cleared", page.developer_key_edit.text() == "")
        check("settings_badge_unlocked", page.developer_badge.text() == "已解锁")
        check("settings_unlock_row_hidden", page.developer_unlock_row.isHidden())
        check("settings_actions_visible", not page.developer_actions.isHidden())
        check("settings_table_url", feedback_table_url(config).endswith("#key=" + config.developer_page_token))
        check("settings_dev_badge_chip", not window.account_chip.dev_badge.isHidden())

        page._lock_developer()
        pump(app)
        check("settings_lock_clears", not config.developer_unlocked and not developer_mode())
        check("settings_lock_token", config.developer_page_token == "")
        check("settings_lock_badge", page.developer_badge.text() == "未解锁")
        check("settings_lock_actions", page.developer_actions.isHidden())
    finally:
        if window is not None:
            window.close()
            window.deleteLater()
            pump(app)
        temp.cleanup()
        developer_module.DEVELOPER_KEY_HASH = original_hash
        developer_module.set_developer_mode(False)
        Config.save = original_save
        MainWindow.ensure_java_detected = original_ensure_java


def main() -> int:
    developer_test()
    payload_test()
    github_normalize_test()
    merge_test()
    app = QApplication.instance() or QApplication(sys.argv)
    account_chip_test(app)
    dialog_test(app)
    settings_unlock_test(app)
    print(f"RESULT: {'PASS' if FAIL == 0 else 'FAIL'} ({PASS} ok, {FAIL} failed)")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
