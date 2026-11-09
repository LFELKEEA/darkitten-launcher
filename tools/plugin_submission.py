"""桌宠社区插件投稿处理工具。

投稿 Issue 的正文以 ``MARKER`` 开头，里面带一段 JSON 元数据和插件 ZIP 附件。
这个模块负责：校验 ZIP、解析 Issue、把通过审核的插件写入站点注册表，以及
生成人工审核用的队列快照。它同时提供 CLI，方便 GitHub Actions 直接调用：

    python tools/plugin_submission.py validate --zip plugin.zip --body issue.md
    python tools/plugin_submission.py publish --zip plugin.zip --issue event.json
    python tools/plugin_submission.py queue --issues issues.json --out plugin-queue.json
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

MARKER = "<!-- dkl-plugin-check:v1 -->"
PUBLISH_MARKER = "<!-- dkl-plugin-publish:v1 -->"
QUEUE_SCHEMA = 1

MAX_ENTRIES = 300
MAX_ARCHIVE_BYTES = 20 * 1024 * 1024
RESERVED_PREFIX = "lfelkeea."
ENTRY_DEFAULT = "main.py"

KNOWN_PERMISSIONS = {
    "pet.say",
    "pet.action",
    "pet.model",
    "pet.persona",
    "ai.chat",
    "chat.context",
    "screen.capture",
    "timer",
    "events",
    "settings",
    "storage",
}

PERMISSION_LABELS = {
    "pet.say": "说话",
    "pet.action": "动作",
    "pet.model": "Live2D 模型",
    "pet.persona": "人格与台词",
    "ai.chat": "AI 对话",
    "chat.context": "聊天上下文",
    "screen.capture": "截屏",
    "timer": "定时器",
    "events": "事件",
    "settings": "设置",
    "storage": "本地存储",
}

DANGEROUS_SUFFIXES = {
    ".exe",
    ".dll",
    ".bat",
    ".cmd",
    ".com",
    ".msi",
    ".scr",
    ".ps1",
    ".vbs",
    ".js",
    ".jse",
    ".wsf",
    ".sh",
    ".app",
    ".dylib",
    ".so",
}

BLOCKED_FILENAMES = {"config.json"}

STATUS_LABELS = {
    "plugin-validated": "validated",
    "plugin-published": "published",
    "plugin-invalid": "invalid",
    "plugin-rejected": "rejected",
}


@dataclass
class Validation:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    plugin: dict[str, Any] = field(default_factory=dict)
    sha256: str = ""
    size: int = 0


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _version_key(version: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", str(version or ""))
    return tuple(int(item) for item in parts) if parts else (0,)


def _as_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _load_manifest(archive: zipfile.ZipFile, names: list[str]) -> tuple[dict[str, Any] | None, str]:
    candidates = [name for name in names if name.endswith("/plugin.json")]
    if not candidates:
        return None, "ZIP 里缺少 plugin.json。"
    if len(candidates) > 1:
        return None, "ZIP 里只能有一个 plugin.json。"
    try:
        raw = archive.read(candidates[0]).decode("utf-8")
    except (KeyError, UnicodeDecodeError) as exc:
        return None, f"plugin.json 必须是不带 BOM 的 UTF-8：{exc}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"plugin.json 不是合法 JSON：{exc}"
    if not isinstance(data, dict):
        return None, "plugin.json 顶层必须是对象。"
    return data, ""


def validate_zip(
    data: bytes,
    *,
    expected_sha: str | None = None,
    expected_size: int | None = None,
) -> Validation:
    """校验投稿 ZIP，返回结构化结果。"""

    result = Validation(ok=False)
    if not data:
        result.errors.append("插件包是空的。")
        return result

    result.size = len(data)
    result.sha256 = _sha256(data)

    if expected_sha and expected_sha.lower() != result.sha256:
        result.errors.append(
            f"SHA-256 不一致：元数据写的是 {expected_sha}，实际是 {result.sha256}。"
        )
    if expected_size is not None and int(expected_size) != result.size:
        result.errors.append(
            f"大小不一致：元数据写的是 {expected_size} 字节，实际是 {result.size} 字节。"
        )
    if result.size > MAX_ARCHIVE_BYTES:
        result.errors.append(
            f"插件包超过 {MAX_ARCHIVE_BYTES // (1024 * 1024)} MB 上限。"
        )

    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        result.errors.append("不是有效的 ZIP 文件。")
        return result

    with archive:
        names = archive.namelist()
        if len(names) > MAX_ENTRIES:
            result.errors.append(
                f"ZIP 条目过多（{len(names)} 个，上限 {MAX_ENTRIES} 个）。"
            )

        files: list[str] = []
        tops: set[str] = set()
        for name in names:
            normalized = name.replace("\\", "/")
            parts = [part for part in normalized.split("/") if part not in {"", "."}]
            if any(part == ".." for part in parts):
                result.errors.append(f"文件名包含 ..，拒绝接受：{name}")
                continue
            if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
                result.errors.append(f"文件名不能是绝对路径：{name}")
                continue
            if normalized.endswith("/"):
                if parts:
                    tops.add(parts[0])
                continue
            if parts:
                tops.add(parts[0])
            files.append("/".join(parts))
            suffix = Path(normalized).suffix.lower()
            if suffix in DANGEROUS_SUFFIXES:
                result.errors.append(f"插件包不能包含可执行文件：{name}")
            if Path(normalized).name.lower() in BLOCKED_FILENAMES:
                result.errors.append(f"插件包不能自带配置文件：{name}")

        manifest, manifest_error = _load_manifest(archive, names)
        if manifest is None:
            result.errors.append(manifest_error)
            result.ok = not result.errors
            return result

        plugin_id = _as_text(manifest.get("id"))
        version = _as_text(manifest.get("version"))
        if not plugin_id:
            result.errors.append("plugin.json 缺少 id。")
        elif not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,63}", plugin_id):
            result.errors.append(
                "插件 id 只能使用小写字母、数字、点、下划线和连字符，长度 2–64。"
            )
        if plugin_id.startswith(RESERVED_PREFIX):
            result.errors.append(
                f"id 前缀 {RESERVED_PREFIX} 是官方保留的，社区投稿不能使用。"
            )
        if not version:
            result.errors.append("plugin.json 缺少 version。")

        if len(tops) != 1:
            result.errors.append("ZIP 里只能有一个顶层插件文件夹。")
        elif plugin_id and tops != {plugin_id}:
            found = next(iter(tops))
            result.errors.append(
                f"ZIP 根目录名必须等于插件 id：期望 {plugin_id}/，实际是 {found}/。"
            )

        entry_name = _as_text(manifest.get("entry")) or ENTRY_DEFAULT
        entry_path = f"{plugin_id}/{entry_name}" if plugin_id else entry_name
        if files and entry_path not in files:
            result.errors.append(f"找不到入口文件 {entry_path}。")

        permissions = manifest.get("permissions") or []
        if not isinstance(permissions, list):
            result.errors.append("permissions 必须是数组。")
            permissions = []
        unknown = [item for item in permissions if item not in KNOWN_PERMISSIONS]
        if unknown:
            result.errors.append(
                "包含未知权限：" + "、".join(str(item) for item in unknown)
                + "。请使用已知权限列表中的值。"
            )

        plugin = {
            "id": plugin_id,
            "name": _as_text(manifest.get("name")) or plugin_id,
            "version": version,
            "author": _as_text(manifest.get("author")),
            "description": _as_text(manifest.get("description")),
            "api_version": manifest.get("api_version", 1),
            "entry": entry_name,
            "permissions": [str(item) for item in permissions],
            "permission_labels": [
                PERMISSION_LABELS.get(str(item), str(item)) for item in permissions
            ],
            "requires": manifest.get("requires") or [],
            "highlights": manifest.get("highlights") or [],
            "settings": manifest.get("settings") or {},
            "file": f"plugins/{plugin_id}-{version}.zip" if plugin_id and version else "",
        }
        result.plugin = plugin
        result.ok = not result.errors
        return result


def parse_issue_body(body: str) -> tuple[dict[str, Any] | None, list[str]]:
    """从 Issue 正文里取出元数据 JSON。"""

    if not body or MARKER not in body:
        return None, [f"正文里缺少投稿标记 {MARKER}，请使用投稿模板重新提交。"]
    rest = body.split(MARKER, 1)[1]
    match = re.search(r"```json\s*(.*?)```", rest, re.S)
    if not match:
        return None, ["没有找到 ```json 元数据块。"]
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        return None, [f"元数据不是合法 JSON：{exc}"]
    if not isinstance(data, dict):
        return None, ["元数据顶层必须是对象。"]
    return data, []


def attachment_urls(body: str) -> list[str]:
    """取出正文里的 GitHub 附件链接。"""

    urls = re.findall(r"https://[^\s)\]>]+", body or "")
    rows: list[str] = []
    for url in urls:
        if "/user-attachments/" in url or "user-attachments/assets" in url:
            if url not in rows:
                rows.append(url.rstrip(".,;"))
    return rows


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def publish_event(
    event: dict[str, Any],
    *,
    repo_root: Path | str | None = None,
    attachment: str | None = None,
    note: str = "",
) -> tuple[dict[str, Any], str]:
    """处理一次 ``/approve``：校验并写入站点注册表。"""

    root = Path(repo_root) if repo_root is not None else _default_root()
    issue = (event or {}).get("issue") or {}
    body = issue.get("body") or ""
    user = ((issue.get("user") or {}).get("login") or "").strip()
    result: dict[str, Any] = {
        "ok": False,
        "errors": [],
        "warnings": [],
        "plugin": {},
        "sha256": "",
        "size": 0,
        "zip_rel": "",
        "issue": {"number": issue.get("number"), "user": user},
    }

    if not attachment:
        result["errors"].append("没有找到插件附件，请在 Issue 里重新上传 ZIP。")
        return result, markdown_report(result, "publish")

    try:
        data = Path(attachment).read_bytes()
    except OSError as exc:
        result["errors"].append(f"读取插件包失败：{exc}")
        return result, markdown_report(result, "publish")

    meta, errors = parse_issue_body(body)
    if errors:
        result["errors"].extend(errors)
        return result, markdown_report(result, "publish")

    expected_sha = str((meta or {}).get("sha256") or "") or None
    expected_size = (meta or {}).get("size")
    validation = validate_zip(
        data,
        expected_sha=expected_sha,
        expected_size=int(expected_size) if expected_size is not None else None,
    )
    result["plugin"] = validation.plugin
    result["sha256"] = validation.sha256
    result["size"] = validation.size
    result["warnings"].extend(validation.warnings)
    if not validation.ok:
        result["errors"].extend(validation.errors)
        return result, markdown_report(result, "publish")

    plugin = validation.plugin
    if meta:
        if str(meta.get("id") or "") != plugin["id"]:
            result["errors"].append("元数据里的 id 与 plugin.json 不一致。")
        if str(meta.get("version") or "") != plugin["version"]:
            result["errors"].append("元数据里的 version 与 plugin.json 不一致。")
    if result["errors"]:
        return result, markdown_report(result, "publish")

    public = root / "website" / "public"
    registry_path = public / "plugins.json"
    registry = _read_json(registry_path, None)
    if not isinstance(registry, dict) or not isinstance(registry.get("plugins"), list):
        result["errors"].append(f"找不到有效的插件注册表：{registry_path}")
        return result, markdown_report(result, "publish")

    community_path = root / "website" / "community_plugins.json"
    community = _read_json(community_path, {"schema": 1, "plugins": []})
    if not isinstance(community, dict):
        community = {"schema": 1, "plugins": []}
    community.setdefault("schema", 1)
    if not isinstance(community.get("plugins"), list):
        community["plugins"] = []

    entries = registry["plugins"]
    existing_index = None
    existing = None
    for index, item in enumerate(entries):
        if isinstance(item, dict) and item.get("id") == plugin["id"]:
            existing_index = index
            existing = item
            break

    if existing is not None and not existing.get("community"):
        result["errors"].append("这个 id 属于官方插件，社区投稿不能覆盖官方条目。")
        return result, markdown_report(result, "publish")
    if existing is not None:
        old_version = str(existing.get("version") or "0")
        if _version_key(plugin["version"]) <= _version_key(old_version):
            result["errors"].append(
                f"新版本必须更高：当前站点是 {old_version}，投稿是 {plugin['version']}。"
            )
            return result, markdown_report(result, "publish")

    previous = next(
        (
            item
            for item in community["plugins"]
            if isinstance(item, dict) and item.get("id") == plugin["id"]
        ),
        None,
    )
    if previous is not None:
        owner = str(previous.get("submitted_by") or "")
        if owner and user and owner != user:
            result["warnings"].append(
                f"这个插件别人提交过（{owner}），请确认你没有覆盖他人的作品。"
            )

    zip_rel = plugin["file"]
    target = public / zip_rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)

    entry = dict(plugin)
    entry.update(
        {
            "file": zip_rel,
            "size": validation.size,
            "sha256": validation.sha256,
            "featured": False,
            "community": True,
        }
    )
    if existing_index is None:
        entries.append(entry)
    else:
        entries[existing_index] = entry
    registry["updated"] = date.today().isoformat()
    _write_json(registry_path, registry)

    record = {
        "id": plugin["id"],
        "version": plugin["version"],
        "file": zip_rel,
        "submitted_by": user,
        "issue": issue.get("number"),
        "notes": str((meta or {}).get("notes") or note or ""),
        "entry": entry,
    }
    replaced = False
    for index, item in enumerate(community["plugins"]):
        if isinstance(item, dict) and item.get("id") == plugin["id"]:
            community["plugins"][index] = record
            replaced = True
            break
    if not replaced:
        community["plugins"].append(record)
    _write_json(community_path, community)

    result["ok"] = True
    result["zip_rel"] = zip_rel
    return result, markdown_report(result, "publish")


def markdown_report(result: dict[str, Any], mode: str = "validate") -> str:
    ok = bool(result.get("ok"))
    plugin = result.get("plugin") or {}
    if mode == "publish":
        lines = [PUBLISH_MARKER, "", "### 发布结果：" + ("✅ 已发布" if ok else "❌ 未发布"), ""]
    else:
        lines = [MARKER, "", "### 自动校验：" + ("✅ 通过" if ok else "❌ 未通过"), ""]

    if plugin:
        lines.append(f"- 插件：`{plugin.get('id', '')}` {plugin.get('name', '')}".rstrip())
        lines.append(f"- 版本：`{plugin.get('version', '')}`")
        permissions = plugin.get("permissions") or []
        lines.append(
            "- 权限：" + ("、".join(f"`{item}`" for item in permissions) if permissions else "无")
        )
    if result.get("sha256"):
        lines.append(f"- SHA-256：`{result['sha256']}`")
    if result.get("size"):
        lines.append(f"- 大小：{result['size']} 字节")
    if result.get("attachment_url"):
        lines.append(f"- 附件：{result['attachment_url']}")
    if mode == "publish" and result.get("zip_rel"):
        lines.append(f"- 下载文件：`{result['zip_rel']}`（发布后见 plugins.html）")

    errors = result.get("errors") or []
    warnings = result.get("warnings") or []
    if errors:
        lines.extend(["", "#### 未通过项", ""])
        lines.extend(f"- {item}" for item in errors)
    if warnings:
        lines.extend(["", "#### 提醒", ""])
        lines.extend(f"- {item}" for item in warnings)

    if mode == "validate":
        lines.extend(
            [
                "",
                "修改后编辑本 Issue 会自动重新校验；维护者回复 `/approve` 即可发布。",
            ]
        )
    elif not ok:
        lines.extend(["", "修正上面的问题后，重新上传 ZIP 并等待维护者再次执行 `/approve`。"])
    return "\n".join(lines) + "\n"


def build_queue(
    issues: list[dict[str, Any]],
    *,
    repo: str = "",
) -> dict[str, Any]:
    """把投稿 Issue 列表整理成审核队列快照。"""

    items: list[dict[str, Any]] = []
    for issue in issues or []:
        if not isinstance(issue, dict):
            continue
        if issue.get("pull_request") is not None or "pull_request" in issue:
            continue
        labels = [
            str(item.get("name"))
            for item in (issue.get("labels") or [])
            if isinstance(item, dict) and item.get("name")
        ]
        if "plugin-submission" not in labels:
            continue
        state = str(issue.get("state") or "open")
        if "plugin-published" in labels:
            status = "published"
        elif "plugin-rejected" in labels or "plugin-invalid" in labels:
            status = "rejected"
        elif "plugin-validated" in labels:
            status = "validated"
        elif state == "closed":
            status = "closed"
        else:
            status = "pending"

        meta, _ = parse_issue_body(issue.get("body") or "")
        plugin = None
        if meta:
            plugin = {
                "id": str(meta.get("id") or ""),
                "name": str(meta.get("name") or ""),
                "version": str(meta.get("version") or ""),
                "permissions": [
                    str(item) for item in (meta.get("permissions") or [])
                ],
                "sha256": str(meta.get("sha256") or ""),
                "size": meta.get("size"),
            }

        items.append(
            {
                "number": issue.get("number"),
                "title": str(issue.get("title") or ""),
                "state": state,
                "html_url": str(issue.get("html_url") or ""),
                "created_at": str(issue.get("created_at") or ""),
                "user": str(((issue.get("user") or {}).get("login") or "")),
                "labels": labels,
                "status": status,
                "plugin": plugin,
            }
        )

    items.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return {
        "schema": QUEUE_SCHEMA,
        "repo": repo,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(items),
        "items": items,
    }


def write_queue_snapshot(
    snapshot: dict[str, Any],
    *,
    path: Path | str | None = None,
) -> bool:
    """内容没变就不写盘，返回是否真的更新了文件。"""

    target = Path(path) if path is not None else _default_root() / "website" / "public" / "plugin-queue.json"
    text = json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"
    try:
        if target.is_file() and target.read_text(encoding="utf-8") == text:
            return False
    except OSError:
        pass
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="桌宠社区插件投稿处理工具")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="校验一个插件 ZIP")
    validate.add_argument("--zip", required=True)
    validate.add_argument("--body", help="Issue 正文文件；提供时会校验 sha256/size")
    validate.add_argument("--report", help="把 Markdown 结果写到这个文件")

    publish = sub.add_parser("publish", help="发布一个已批准的投稿")
    publish.add_argument("--zip", required=True)
    publish.add_argument("--issue", required=True, help="GitHub event JSON 文件")
    publish.add_argument("--repo-root", default=str(_default_root()))
    publish.add_argument("--report", help="把 Markdown 结果写到这个文件")

    queue = sub.add_parser("queue", help="生成审核队列快照")
    queue.add_argument("--issues", required=True, help="Issue 列表 JSON 文件")
    queue.add_argument("--repo", default="")
    queue.add_argument("--out", help="输出路径")

    args = parser.parse_args(argv)

    if args.command == "validate":
        data = Path(args.zip).read_bytes()
        meta = None
        if args.body:
            meta, errors = parse_issue_body(Path(args.body).read_text(encoding="utf-8"))
            if errors:
                result = {"ok": False, "errors": errors, "plugin": {}, "warnings": []}
                text = markdown_report(result, "validate")
                if args.report:
                    Path(args.report).write_text(text, encoding="utf-8")
                else:
                    sys.stdout.write(text)
                return 1
        validation = validate_zip(
            data,
            expected_sha=(meta or {}).get("sha256"),
            expected_size=(meta or {}).get("size"),
        )
        result = {
            "ok": validation.ok,
            "errors": validation.errors,
            "warnings": validation.warnings,
            "plugin": validation.plugin,
            "sha256": validation.sha256,
            "size": validation.size,
            "attachment_url": "",
        }
        text = markdown_report(result, "validate")
        if args.report:
            Path(args.report).write_text(text, encoding="utf-8")
        else:
            sys.stdout.write(text)
        return 0 if validation.ok else 1

    if args.command == "publish":
        event = json.loads(Path(args.issue).read_text(encoding="utf-8"))
        if "issue" not in event and "number" in event:
            event = {"issue": event}
        result, text = publish_event(
            event, repo_root=Path(args.repo_root), attachment=args.zip
        )
        if args.report:
            Path(args.report).write_text(text, encoding="utf-8")
        else:
            sys.stdout.write(text)
        return 0 if result["ok"] else 1

    issues = json.loads(Path(args.issues).read_text(encoding="utf-8"))
    if isinstance(issues, dict):
        issues = issues.get("issues") or issues.get("items") or []
    snapshot = build_queue(issues, repo=args.repo)
    write_queue_snapshot(snapshot, path=args.out)
    sys.stdout.write(
        f"queue: {snapshot['count']} 条，更新于 {snapshot['generated_at']}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
