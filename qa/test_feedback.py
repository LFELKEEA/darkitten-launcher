from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QWidget

from darkitten.accounts import Account
from darkitten.config import Config
from darkitten.developer import is_developer, set_developer_mode
from darkitten.feedback import (
    build_feedback_payload,
    build_github_issue_url,
    merge_feedback_items,
    normalize_github_issue,
)
from darkitten.ui.feedback_dialog import FeedbackDialog
from darkitten.ui.main_window import AccountChip

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"{name}: OK{(' (' + detail + ')') if detail else ''}")
    else:
        FAIL += 1
        print(f"{name}: FAIL{(' (' + detail + ')') if detail else ''}")


class FakeMain(QWidget):
    account_changed = Signal()

    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config
        self.messages: list[tuple[str, str]] = []

    def current_account(self) -> Account:
        return Account.from_config(self.config.account)

    def toast(self, text: str, kind: str = "info") -> None:
        self.messages.append((text, kind))


def pump(app: QApplication) -> None:
    for _ in range(3):
        app.processEvents()


def msa_account(name: str = "LFELKEEA") -> Account:
    return Account(kind="msa", name=name, uuid="0" * 32)


def developer_gate_test() -> None:
    config = Config()
    config.microsoft_email = "Lfel278623@outlook.com"
    check(
        "developer_gate_match",
        is_developer(config, msa_account("LFELKEEA")),
    )
    check(
        "developer_gate_casefold",
        is_developer(config, msa_account("lfelkeea")),
    )
    check(
        "developer_gate_wrong_name",
        not is_developer(config, msa_account("Steve")),
    )
    config.microsoft_email = "other@outlook.com"
    check(
        "developer_gate_wrong_email",
        not is_developer(config, msa_account("LFELKEEA")),
    )
    config.microsoft_email = "lfel278623@outlook.com"
    check(
        "developer_gate_offline",
        not is_developer(config, Account.offline("LFELKEEA")),
    )


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
    url = build_github_issue_url(payload)
    check("github_url_enhancement", "labels=enhancement" in url)
    check("github_url_title", "%5BDKL" in url or "DKL" in url)
    bug = build_feedback_payload(kind="bug", title="启动报错", description="启动时提示缺少 Java，无法进入游戏。")
    check("github_url_bug_label", "labels=bug" in build_github_issue_url(bug))


def github_normalize_test() -> None:
    issue = {
        "number": 12,
        "title": "[DKL Bug] 启动失败",
        "body": "报错内容",
        "state": "closed",
        "created_at": "2026-10-24T10:00:00Z",
        "updated_at": "2026-10-24T11:00:00Z",
        "html_url": "https://github.com/LFELKEEA/darkitten-launcher/issues/12",
        "labels": [{"name": "bug"}],
        "comments": 2,
    }
    record = normalize_github_issue(issue)
    check("github_normalize_record", record is not None)
    if record:
        check("github_normalize_id", record["id"] == "gh-12", record["id"])
        check("github_normalize_kind", record["kind"] == "bug", record["kind"])
        check("github_normalize_status", record["status"] == "resolved", record["status"])
        check("github_normalize_platform", record["platform"] == "github")
    pr = dict(issue)
    pr["pull_request"] = {"url": "x"}
    check("github_skip_pr", normalize_github_issue(pr) is None)
    other = dict(issue)
    other["labels"] = [{"name": "question"}]
    check("github_skip_unlabelled", normalize_github_issue(other) is None)


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
    check("merge_order", [item["id"] for item in merged] == ["b", "gh-1", "a"], str([item["id"] for item in merged]))


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
    config.account = msa_account("LFELKEEA").to_config()
    main = FakeMain(config)

    set_developer_mode(True)
    dialog = FeedbackDialog(main, kind="suggestion")
    pump(app)
    check("dialog_suggestion_title", dialog.form_title.text() == "提一个改进建议", dialog.form_title.text())
    check("dialog_suggestion_btn", dialog.submit_btn.text() == "提交建议", dialog.submit_btn.text())
    check("dialog_dev_tabs", not dialog.tabs.isHidden() and dialog.tabs.current() == "compose")
    check("dialog_type_synced", dialog.type_combo.currentData() == "suggestion")

    dialog.tabs.set_value("inbox", emit=True)
    pump(app)
    check("dialog_inbox_tab", dialog.stack.currentIndex() == 1)

    dialog._items = [
        {
            "id": "20261024-120000-abcdef",
            "kind": "bug",
            "status": "open",
            "title": "安装后启动报错",
            "description": "安装 1.21.1 后启动提示缺少 Java。",
            "created_at": "2026-10-24T12:00:00Z",
            "source": "launcher",
            "platform": "center",
        },
        {
            "id": "gh-3",
            "kind": "suggestion",
            "status": "resolved",
            "title": "希望支持排序",
            "description": "下载中心按时间排序。",
            "created_at": "2026-10-24T11:00:00Z",
            "source": "github",
            "platform": "github",
        },
    ]
    dialog._render_inbox()
    pump(app)
    check("inbox_count", dialog.inbox_count.text() == "2 条", dialog.inbox_count.text())
    dialog.filter_kind.setCurrentIndex(dialog.filter_kind.findData("suggestion"))
    pump(app)
    check("inbox_filter_kind", dialog.inbox_count.text() == "1 条", dialog.inbox_count.text())
    dialog.filter_kind.setCurrentIndex(0)
    dialog.filter_status.setCurrentIndex(dialog.filter_status.findData("open"))
    pump(app)
    check("inbox_filter_status", dialog.inbox_count.text() == "1 条", dialog.inbox_count.text())
    dialog.filter_status.setCurrentIndex(0)
    pump(app)

    dialog.set_kind("bug")
    check("dialog_bug_title", dialog.form_title.text() == "报告一个 Bug", dialog.form_title.text())
    dialog.title_edit.setText("启动失败")
    dialog.desc_edit.setPlainText("启动游戏时直接报错，退出代码 1。")
    payload = dialog._payload()
    check("dialog_payload_kind", payload["type"] == "bug", payload["type"])
    check("dialog_payload_contact", payload["contact"] == "lfel278623@outlook.com", payload["contact"])
    dialog.deleteLater()
    pump(app)

    set_developer_mode(False)
    dialog2 = FeedbackDialog(main, kind="bug")
    pump(app)
    check("dialog_hides_inbox_for_user", dialog2.tabs.isHidden())
    dialog2.deleteLater()
    pump(app)

    main.deleteLater()
    pump(app)
    temp.cleanup()


def main() -> int:
    developer_gate_test()
    payload_test()
    github_normalize_test()
    merge_test()
    app = QApplication.instance() or QApplication(sys.argv)
    account_chip_test(app)
    dialog_test(app)
    print(f"RESULT: {'PASS' if FAIL == 0 else 'FAIL'} ({PASS} ok, {FAIL} failed)")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
