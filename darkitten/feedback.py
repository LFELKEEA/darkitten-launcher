from __future__ import annotations

import json
import platform
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from PySide6.QtCore import QObject, Signal

from .config import APP_VERSION
from .microsoft_auth import MicrosoftAuthError, ensure_minecraft_token

FEEDBACK_API = "https://dkl-feedback.lfelkeea.workers.dev"
FEEDBACK_SITE = "https://lfelkeea.github.io/darkitten-launcher/#feedback"
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


class FeedbackError(Exception):
    pass


def _clean(value: object, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    token: str = "",
    timeout: int = 15,
) -> Any:
    headers = {
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raw_error = b""
        try:
            raw_error = exc.read()
        except Exception:
            pass
        detail = ""
        try:
            error_payload = json.loads(raw_error.decode("utf-8"))
            if isinstance(error_payload, dict):
                detail = str(
                    error_payload.get("error")
                    or error_payload.get("message")
                    or ""
                ).strip()
        except Exception:
            detail = ""
        if not detail:
            detail = f"服务器返回 HTTP {exc.code}。"
        raise FeedbackError(detail) from None
    except urllib.error.URLError as exc:
        raise FeedbackError(f"无法连接反馈服务：{exc.reason}") from None
    except TimeoutError:
        raise FeedbackError("反馈服务响应超时，请稍后再试。") from None
    except OSError as exc:
        raise FeedbackError(f"网络请求失败：{exc}") from None

    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise FeedbackError("反馈服务返回了无法识别的数据。") from None


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


def submit_feedback(
    payload: dict[str, Any],
    *,
    api_base: str = FEEDBACK_API,
    timeout: int = 15,
) -> str:
    title = _clean(payload.get("title"), 80)
    description = _clean(payload.get("description"), 4000)
    if len(title) < 2:
        raise FeedbackError("标题至少需要 2 个字。")
    if len(description) < 10:
        raise FeedbackError("描述至少需要 10 个字，写清楚操作步骤会更容易定位。")
    data = dict(payload)
    data["title"] = title
    data["description"] = description
    data.setdefault("source", "launcher")
    data.setdefault("system", _clean(platform.platform(), 300))
    data.setdefault("user_agent", USER_AGENT)
    result = _request_json(
        f"{api_base.rstrip('/')}/api/feedback",
        method="POST",
        payload=data,
        timeout=timeout,
    )
    if not isinstance(result, dict) or not result.get("ok"):
        message = result.get("error") if isinstance(result, dict) else ""
        raise FeedbackError(str(message or "反馈没有成功保存。"))
    return str(result.get("id") or "")


def fetch_feedback(
    token: str,
    *,
    limit: int = 100,
    kind: str = "",
    status: str = "",
    api_base: str = FEEDBACK_API,
    timeout: int = 15,
) -> list[dict[str, Any]]:
    if not token:
        raise FeedbackError("没有可用的 Minecraft 访问令牌。")
    params: dict[str, str] = {"limit": str(max(1, min(200, int(limit))))}
    if kind:
        params["kind"] = _clean(kind, 20).lower()
    if status:
        params["status"] = _clean(status, 20).lower()
    url = f"{api_base.rstrip('/')}/api/feedback?{urllib.parse.urlencode(params)}"
    result = _request_json(url, token=token, timeout=timeout)
    if not isinstance(result, dict) or not result.get("ok"):
        message = result.get("error") if isinstance(result, dict) else ""
        raise FeedbackError(str(message or "读取反馈列表失败。"))
    items = result.get("items")
    if not isinstance(items, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        record = dict(item)
        record["source"] = str(record.get("source") or "website")
        record["kind"] = str(record.get("kind") or record.get("type") or "other")
        record["status"] = str(record.get("status") or "open")
        record["id"] = str(record.get("id") or "")
        record["title"] = str(record.get("title") or "未命名反馈")
        record["description"] = str(
            record.get("description") or record.get("body") or ""
        )
        record["platform"] = "center"
        normalized.append(record)
    return normalized


def update_feedback_status(
    token: str,
    feedback_id: str,
    status: str,
    *,
    api_base: str = FEEDBACK_API,
    timeout: int = 15,
) -> dict[str, Any]:
    if not token:
        raise FeedbackError("没有可用的 Minecraft 访问令牌。")
    if status not in ("open", "resolved", "archived"):
        raise FeedbackError("不支持的处理状态。")
    if not feedback_id:
        raise FeedbackError("缺少反馈编号。")
    url = (
        f"{api_base.rstrip('/')}/api/feedback/"
        f"{urllib.parse.quote(feedback_id, safe='')}"
    )
    result = _request_json(
        url,
        method="PATCH",
        payload={"status": status},
        token=token,
        timeout=timeout,
    )
    if not isinstance(result, dict) or not result.get("ok"):
        message = result.get("error") if isinstance(result, dict) else ""
        raise FeedbackError(str(message or "更新反馈状态失败。"))
    item = result.get("item")
    return item if isinstance(item, dict) else {}


def github_issue_label(kind: str) -> str:
    return "enhancement" if _clean(kind, 20).lower() == "suggestion" else "bug"


def build_github_issue_url(payload: dict[str, Any]) -> str:
    kind = _clean(payload.get("type") or payload.get("kind"), 20).lower()
    kind_label = KIND_LABELS.get(kind, "反馈")
    title = _clean(payload.get("title"), 80) or "DKL 反馈"
    description = _clean(payload.get("description"), 6000)
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
    body = "\n".join(
        [
            *metadata,
            "",
            "---",
            "",
            description,
            "",
            "> 这条反馈通过 Darkitten Launcher 的公开兜底入口提交。",
        ]
    )
    query = urllib.parse.urlencode(
        {
            "title": f"[DKL {kind_label}] {title}",
            "body": body[:7500],
            "labels": github_issue_label(kind),
        }
    )
    return f"https://github.com/{GITHUB_REPO}/issues/new?{query}"


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


def feedback_as_text(item: dict[str, Any]) -> str:
    kind = KIND_LABELS.get(str(item.get("kind") or "other"), "其他")
    status = STATUS_LABELS.get(str(item.get("status") or "open"), "待处理")
    lines = [
        f"反馈编号：{item.get('id') or '—'}",
        f"类型：{kind}",
        f"状态：{status}",
        f"标题：{item.get('title') or '未命名反馈'}",
        f"时间：{item.get('created_at') or item.get('updated_at') or '—'}",
        f"版本：{item.get('version') or '—'}",
        f"来源：{SOURCE_LABELS.get(str(item.get('source') or ''), item.get('source') or '—')}",
    ]
    contact = str(item.get("contact") or "").strip()
    if contact:
        lines.append(f"联系方式：{contact}")
    page = str(item.get("page") or "").strip()
    if page:
        lines.append(f"页面：{page}")
    system = str(item.get("system") or "").strip()
    if system:
        lines.append(f"系统：{system}")
    lines.extend(["", "描述：", str(item.get("description") or "")])
    return "\n".join(lines)


def _github_issue_text(body: str) -> str:
    text = str(body or "").replace("\r\n", "\n").strip()
    if not text:
        return "（提交者没有填写描述。）"
    return text[:8000]


def normalize_github_issue(issue: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(issue, dict) or issue.get("pull_request"):
        return None
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
    if not (lowered & GITHUB_FEEDBACK_LABELS):
        return None
    kind = "suggestion" if "enhancement" in lowered else "bug"
    state = str(issue.get("state") or "open").lower()
    status = "resolved" if state == "closed" else "open"
    number = issue.get("number")
    return {
        "id": f"gh-{number}",
        "number": number,
        "title": str(issue.get("title") or "未命名反馈"),
        "description": _github_issue_text(str(issue.get("body") or "")),
        "kind": kind,
        "status": status,
        "source": "github",
        "platform": "github",
        "created_at": str(issue.get("created_at") or ""),
        "updated_at": str(issue.get("updated_at") or ""),
        "html_url": str(issue.get("html_url") or ""),
        "version": "",
        "contact": "",
        "page": "",
        "system": "",
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


class FeedbackSubmitWorker(QObject):
    finished = Signal(bool, str, str)

    def __init__(self, payload: dict[str, Any], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.payload = dict(payload)
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            feedback_id = submit_feedback(self.payload)
        except Exception as exc:
            self.finished.emit(False, str(exc), "")
            return
        self.finished.emit(True, "反馈已提交。", feedback_id)


class FeedbackListWorker(QObject):
    finished = Signal(object, object, str, str)

    def __init__(
        self,
        account,
        client_id: str,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.account = account
        self.client_id = str(client_id or "")
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        updated_account = self.account
        token = ""
        center_items: list[dict[str, Any]] = []
        errors: list[str] = []
        try:
            updated_account = ensure_minecraft_token(self.account, self.client_id)
            token = str(updated_account.mc_access_token or "")
            if token:
                center_items = fetch_feedback(token, limit=100)
            else:
                errors.append("没有可用的正版访问令牌。")
        except (FeedbackError, MicrosoftAuthError) as exc:
            errors.append(str(exc))
        except Exception as exc:
            errors.append(f"读取反馈中心失败：{exc}")

        github_items: list[dict[str, Any]] = []
        github_error = ""
        try:
            github_items = fetch_github_feedback(limit=100)
        except Exception as exc:
            github_error = str(exc)

        items = merge_feedback_items(center_items, github_items)
        if center_items and github_items:
            warning = ""
            source = "center+github"
        elif center_items:
            warning = ""
            source = "center"
        elif github_items:
            warning = errors[0] if errors else "反馈中心暂时离线，已切换到公开反馈。"
            source = "github"
        else:
            warning = "；".join(errors) if errors else ""
            if github_error:
                warning = f"{warning}；GitHub 反馈读取失败：{github_error}".strip("；")
            if not warning:
                warning = "暂时没有读取到反馈。"
            source = ""
        self.finished.emit(items, updated_account, source, warning)


class FeedbackUpdateWorker(QObject):
    finished = Signal(bool, str, object, object)

    def __init__(
        self,
        account,
        client_id: str,
        feedback_id: str,
        status: str,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.account = account
        self.client_id = str(client_id or "")
        self.feedback_id = str(feedback_id or "")
        self.status = str(status or "")
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        updated_account = self.account
        try:
            updated_account = ensure_minecraft_token(self.account, self.client_id)
            token = str(updated_account.mc_access_token or "")
            item = update_feedback_status(token, self.feedback_id, self.status)
        except Exception as exc:
            self.finished.emit(False, str(exc), updated_account, None)
            return
        self.finished.emit(True, "反馈状态已更新。", updated_account, item)
