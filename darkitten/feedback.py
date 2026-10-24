from __future__ import annotations

import json
import platform
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from PySide6.QtCore import QObject, Signal

from .config import APP_VERSION

FEEDBACK_SITE = "https://lfelkeea.github.io/darkitten-launcher/#feedback"
FEEDBACK_FORM_URL = "https://lfelkeea.github.io/darkitten-launcher/"
FEEDBACK_TABLE_PAGE = "feedback.html"
FEEDBACK_EMAIL = "lfel278623@outlook.com"
GITHUB_REPO = "LFELKEEA/darkitten-launcher"
GITHUB_ISSUES_PAGE = f"https://github.com/{GITHUB_REPO}/issues"
GITHUB_ISSUES_API = f"https://api.github.com/repos/{GITHUB_REPO}/issues"

USER_AGENT = f"DarkittenLauncher/{APP_VERSION} (feedback client)"

KIND_LABELS = {
    "bug": "Bug",
    "suggestion": "改进建议",
    "ui": "界面显示",
    "download": "下载安装",
    "update": "更新启动",
    "other": "其他",
}
STATUS_LABELS = {
    "open": "待处理",
    "resolved": "已处理",
    "archived": "已归档",
    "closed": "已关闭",
}
SOURCE_LABELS = {
    "launcher": "启动器",
    "website": "网站",
    "github": "GitHub",
}
GITHUB_FEEDBACK_LABELS = {"bug", "enhancement"}
TITLE_PREFIX = "[DKL"


class FeedbackError(Exception):
    pass


def _clean(value: object, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _request_json(url: str, *, timeout: int = 15) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
    }
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = f"GitHub 返回 HTTP {exc.code}。"
        if exc.code == 403:
            detail = "GitHub 接口访问次数已达上限，请稍后再试。"
        raise FeedbackError(detail) from None
    except urllib.error.URLError as exc:
        raise FeedbackError(f"无法连接 GitHub：{exc.reason}") from None
    except TimeoutError:
        raise FeedbackError("GitHub 响应超时，请稍后再试。") from None
    except OSError as exc:
        raise FeedbackError(f"网络请求失败：{exc}") from None

    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise FeedbackError("GitHub 返回了无法识别的数据。") from None


def build_feedback_payload(
    *,
    kind: str,
    title: str,
    description: str,
    version: str = APP_VERSION,
    contact: str = "",
    page: str = "launcher:feedback",
) -> dict[str, str]:
    clean_kind = _clean(kind, 20).lower()
    if clean_kind not in KIND_LABELS:
        clean_kind = "other"
    return {
        "type": clean_kind,
        "version": _clean(version or APP_VERSION, 40),
        "title": _clean(title, 80),
        "description": _clean(description, 4000),
        "contact": _clean(contact, 120),
        "source": "launcher",
        "page": _clean(page, 300),
        "system": _clean(platform.platform(), 300),
        "user_agent": USER_AGENT,
    }


def _issue_body(payload: dict[str, Any]) -> str:
    kind = _clean(payload.get("type") or payload.get("kind"), 20).lower()
    kind_label = KIND_LABELS.get(kind, "其他")
    metadata = [
        f"**来源**：{SOURCE_LABELS.get(str(payload.get('source') or 'launcher'), '启动器')}",
        f"**类型**：{kind_label}",
        f"**启动器版本**：{_clean(payload.get('version') or APP_VERSION, 40)}",
    ]
    contact = _clean(payload.get("contact"), 120)
    if contact:
        metadata.append(f"**联系方式**：{contact}")
    page = _clean(payload.get("page"), 300)
    if page:
        metadata.append(f"**页面**：{page}")
    system = _clean(payload.get("system"), 300)
    if system:
        metadata.append(f"**系统**：{system}")
    return "\n".join(
        [
            *metadata,
            "",
            "---",
            "",
            _clean(payload.get("description"), 6000),
            "",
            "> 这条反馈通过 DKL 下载站提交，开发者可在网站反馈表查看。",
        ]
    )


def build_github_issue_url(payload: dict[str, Any]) -> str:
    kind = _clean(payload.get("type") or payload.get("kind"), 20).lower()
    kind_label = KIND_LABELS.get(kind, "反馈")
    title = _clean(payload.get("title"), 80) or "DKL 反馈"
    query = urllib.parse.urlencode(
        {
            "title": f"{TITLE_PREFIX} {kind_label}] {title}",
            "body": _issue_body(payload)[:7500],
        }
    )
    return f"https://github.com/{GITHUB_REPO}/issues/new?{query}"


def build_site_feedback_url(payload: dict[str, Any]) -> str:
    """把启动器里写好的反馈带到下载站表单，最终由网站提交到 GitHub Issues。"""
    kind = _clean(payload.get("type") or payload.get("kind"), 20).lower()
    if kind not in KIND_LABELS:
        kind = "other"
    params = {
        "type": kind,
        "version": _clean(payload.get("version") or APP_VERSION, 40),
        "title": _clean(payload.get("title"), 80),
        "description": _clean(payload.get("description"), 4000),
        "contact": _clean(payload.get("contact"), 120),
    }
    query = urllib.parse.urlencode({key: value for key, value in params.items() if value})
    fragment = "#feedback-suggestion" if kind == "suggestion" else "#feedback-bug"
    return f"{FEEDBACK_FORM_URL}?{query}{fragment}"


def feedback_payload_text(payload: dict[str, Any]) -> str:
    kind = KIND_LABELS.get(
        str(payload.get("type") or payload.get("kind") or "other"), "其他"
    )
    lines = [
        f"类型：{kind}",
        f"启动器版本：{payload.get('version') or APP_VERSION}",
        f"标题：{payload.get('title') or '—'}",
        f"联系方式：{payload.get('contact') or '未填写'}",
        f"页面：{payload.get('page') or '—'}",
        f"系统：{payload.get('system') or platform.platform()}",
        "",
        str(payload.get("description") or ""),
    ]
    return "\n".join(lines)


def build_mailto_url(payload: dict[str, Any]) -> str:
    kind = KIND_LABELS.get(
        str(payload.get("type") or payload.get("kind") or "other"), "反馈"
    )
    subject = f"[DKL {kind}] {payload.get('title') or '反馈'}"
    body = feedback_payload_text(payload)
    if len(body) > 1800:
        body = body[:1800] + "\n\n（完整内容已复制到剪贴板）"
    query = urllib.parse.urlencode({"subject": subject, "body": body})
    return f"mailto:{FEEDBACK_EMAIL}?{query}"


def _issue_field(body: str, name: str) -> str:
    match = re.search(
        r"^\*\*" + re.escape(name) + r"\*\*：(.*)$", body, re.MULTILINE
    )
    return match.group(1).strip() if match else ""


def _issue_description(body: str) -> str:
    text = str(body or "").replace("\r\n", "\n").strip()
    if not text:
        return "（提交者没有填写描述。）"
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"^\*\*[^*]+\*\*：", stripped):
            continue
        if stripped == "---" and not lines:
            continue
        if stripped.startswith("> 这条反馈通过 DKL 下载站提交"):
            continue
        lines.append(line)
    cleaned = "\n".join(lines).strip()
    return cleaned[:8000] if cleaned else "（提交者没有填写描述。）"


def _kind_from_body(body: str, title: str, labels: set[str]) -> str:
    type_label = _issue_field(body, "类型")
    for key, label in KIND_LABELS.items():
        if type_label == label:
            return key
    if "enhancement" in labels or "建议" in title:
        return "suggestion"
    return "bug"


def normalize_github_issue(issue: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(issue, dict) or issue.get("pull_request"):
        return None
    title = str(issue.get("title") or "").strip()
    labels: list[str] = []
    raw_labels = issue.get("labels")
    if isinstance(raw_labels, list):
        for label in raw_labels:
            if isinstance(label, dict):
                name = str(label.get("name") or "").strip()
            else:
                name = str(label or "").strip()
            if name:
                labels.append(name)
    lowered = {label.casefold() for label in labels}
    if not title.startswith(TITLE_PREFIX) and not (lowered & GITHUB_FEEDBACK_LABELS):
        return None
    body = str(issue.get("body") or "")
    kind = _kind_from_body(body, title, lowered)
    state = str(issue.get("state") or "open").lower()
    status = "resolved" if state == "closed" else "open"
    number = issue.get("number")
    source_label = _issue_field(body, "来源")
    source_key = "github"
    for key, label in SOURCE_LABELS.items():
        if source_label == label:
            source_key = key
            break
    return {
        "id": f"gh-{number}",
        "number": number,
        "title": title or "未命名反馈",
        "description": _issue_description(body),
        "kind": kind,
        "status": status,
        "source": source_key,
        "platform": "github",
        "created_at": str(issue.get("created_at") or ""),
        "updated_at": str(issue.get("updated_at") or ""),
        "html_url": str(issue.get("html_url") or ""),
        "version": _issue_field(body, "启动器版本"),
        "contact": _issue_field(body, "联系方式"),
        "page": _issue_field(body, "页面"),
        "system": _issue_field(body, "系统"),
        "labels": labels,
        "comments": int(issue.get("comments") or 0),
    }


def fetch_github_feedback(
    *,
    limit: int = 100,
    timeout: int = 15,
) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode(
        {
            "state": "all",
            "per_page": str(max(1, min(100, int(limit)))),
            "sort": "created",
            "direction": "desc",
        }
    )
    result = _request_json(f"{GITHUB_ISSUES_API}?{params}", timeout=timeout)
    if not isinstance(result, list):
        raise FeedbackError("GitHub 反馈列表格式不正确。")
    items: list[dict[str, Any]] = []
    for issue in result:
        record = normalize_github_issue(issue)
        if record is not None:
            items.append(record)
    return items


def merge_feedback_items(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for group in groups:
        for item in group:
            key = str(item.get("id") or "")
            if key and key not in merged:
                merged[key] = item
    return sorted(
        merged.values(),
        key=lambda item: str(item.get("created_at") or ""),
        reverse=True,
    )


class FeedbackListWorker(QObject):
    """读取网站反馈表背后的 GitHub Issues，用于开发者未读提醒。"""

    finished = Signal(object, object, str, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            items = fetch_github_feedback(limit=100)
        except Exception as exc:
            self.finished.emit([], None, "", f"读取反馈表失败：{exc}")
            return
        self.finished.emit(items, None, "github", "")
