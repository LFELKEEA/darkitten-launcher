from __future__ import annotations

"""插件投稿校验与发布工具。

站点是纯静态的 GitHub Pages，无法直接接收二进制上传。投稿流程因此是：

1. 投稿页在浏览器里先解析 ZIP、核对 plugin.json，生成结构化 Issue 正文；
2. 投稿人把 ZIP 拖进 GitHub Issue，作为附件；
3. GitHub Actions 调用本脚本下载附件、再次校验；
4. 维护者评论 `/approve` 后，再次校验并发布到 gh-pages 与 main。

这个脚本只解析 ZIP，从不执行插件代码。所有路径、大小、权限和文件名都会检查。
"""

import argparse
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
WEBSITE = ROOT / "website"
PUBLIC = WEBSITE / "public"
REGISTRY = PUBLIC / "plugins.json"
COMMUNITY = WEBSITE / "community_plugins.json"
PLUGIN_DIR = PUBLIC / "plugins"

SCHEMA = 1
MAX_BODY = 64 * 1024
MAX_ZIP = 6 * 1024 * 1024
MAX_UNCOMPRESSED = 30 * 1024 * 1024
MAX_ENTRIES = 300
MAX_MANIFEST = 64 * 1024
MAX_FILE = 8 * 1024 * 1024
MAX_SETTINGS = 8 * 1024
MAX_NAME = 80
MAX_DESCRIPTION = 4000
MAX_AUTHOR = 80
MAX_CONTACT = 120
MAX_NOTES = 2000
MAX_REQUIRES = 10
MAX_HIGHLIGHTS = 10

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,63}$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]{1,32})?$")

KNOWN_PERMISSIONS = {
    "pet.say": "让桌宠说话",
    "pet.action": "触发桌宠动作",
    "ai.chat": "调用 AI 对话",
    "screen.capture": "截取屏幕",
    "timer": "创建定时器",
    "events": "订阅事件",
    "settings": "读写插件设置",
    "storage": "读写插件存储",
}

RESERVED_PREFIXES = ("lfelkeea.", "example.")
DANGEROUS_PARTS = {"__pycache__", ".git", ".idea", ".vscode", "node_modules", "data"}
DANGEROUS_NAMES = {
    ".env",
    ".env.local",
    "config.json",
    "social.json",
    "credentials.json",
    "secrets.json",
    "id_rsa",
    "id_ed25519",
}
DANGEROUS_SUFFIXES = {
    ".pyc", ".pyo", ".log", ".pem", ".key", ".p12", ".pfx",
    ".exe", ".dll", ".so", ".dylib", ".msi", ".scr", ".com",
    ".bat", ".cmd", ".ps1", ".sh", ".jar",
}

ATTACHMENT_RE = re.compile(
    r"https://(?:"
    r"github\.com/user-attachments/assets/"
    r"|user-images\.githubusercontent\.com/"
    r"|private-user-images\.githubusercontent\.com/"
    r")[^\s<>\)\]\"']+"
)
MARKER = "<!-- dkl-plugin-submission:v1 -->"
JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.S)


@dataclass
class Validation:
    ok: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    plugin: dict[str, Any] = field(default_factory=dict)
    sha256: str = ""
    size: int = 0
    archive_name: str = ""
    files: list[str] = field(default_factory=list)
    raw: bytes = field(default=b"", repr=False)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
            "plugin": self.plugin,
            "sha256": self.sha256,
            "size": self.size,
            "archive_name": self.archive_name,
            "files": self.files,
        }


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def clean_text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _path_error(name: str) -> str:
    if not name:
        return "ZIP 里有空文件名。"
    if "\x00" in name:
        return f"文件名包含空字符：{name!r}"
    if "\\" in name:
        return f"文件名不能使用反斜杠：{name}"
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        return f"文件名不能是绝对路径：{name}"
    parts = name.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return f"文件名包含空段、`.` 或 `..`：{name}"
    if any(":" in part for part in parts):
        return f"文件名不能包含冒号：{name}"
    if any(part.endswith((" ", ".")) for part in parts):
        return f"文件名不能以空格或点结尾：{name}"
    return ""


def _check_dangerous(name: str) -> str:
    parts = name.split("/")
    lowered = [part.lower() for part in parts]
    if any(part in DANGEROUS_PARTS for part in lowered):
        return f"ZIP 不能包含运行数据或开发缓存：{name}"
    if lowered[-1] in DANGEROUS_NAMES:
        return f"ZIP 不能包含可能带密钥的配置文件：{name}"
    suffix = Path(lowered[-1]).suffix
    if suffix in DANGEROUS_SUFFIXES:
        return f"ZIP 不能包含可执行文件、脚本或密钥：{name}"
    return ""


def _read_manifest(zf: zipfile.ZipFile, name: str) -> tuple[dict[str, Any] | None, str]:
    try:
        info = zf.getinfo(name)
    except KeyError:
        return None, f"缺少 {name}"
    if info.file_size > MAX_MANIFEST:
        return None, "plugin.json 不能超过 64 KB"
    try:
        raw = zf.read(info)
    except Exception as exc:  # noqa: BLE001 - zipfile 可能抛运行期异常
        return None, f"读取 plugin.json 失败：{exc}"
    try:
        manifest = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        return None, f"plugin.json 不是合法的 UTF-8 JSON：{exc}"
    if not isinstance(manifest, dict):
        return None, "plugin.json 顶层必须是对象"
    return manifest, ""


def _validate_requires(value: Any) -> tuple[list[dict[str, str]], str]:
    if value in (None, ""):
        return [], ""
    if not isinstance(value, list):
        return [], "requires 必须是数组"
    if len(value) > MAX_REQUIRES:
        return [], f"requires 最多 {MAX_REQUIRES} 项"
    result: list[dict[str, str]] = []
    for index, item in enumerate(value, 1):
        if not isinstance(item, dict):
            return [], f"requires 第 {index} 项必须是对象"
        name = clean_text(item.get("name"), 60)
        if not name:
            return [], f"requires 第 {index} 项缺少 name"
        version = clean_text(item.get("version"), 40)
        url = clean_text(item.get("url"), 300)
        note = clean_text(item.get("note"), 200)
        if url and not re.match(r"^https?://", url, re.I):
            return [], f"requires 第 {index} 项的 url 只能是 http(s)"
        result.append({"name": name, "version": version, "url": url, "note": note})
    return result, ""


def _validate_highlights(value: Any) -> tuple[list[dict[str, str]], str]:
    if value in (None, ""):
        return [], ""
    if not isinstance(value, list):
        return [], "highlights 必须是数组"
    if len(value) > MAX_HIGHLIGHTS:
        return [], f"highlights 最多 {MAX_HIGHLIGHTS} 项"
    result: list[dict[str, str]] = []
    for index, item in enumerate(value, 1):
        if not isinstance(item, dict):
            return [], f"highlights 第 {index} 项必须是对象"
        label = clean_text(item.get("label"), 40)
        value_text = clean_text(item.get("value"), 80)
        if label or value_text:
            result.append({"label": label or "说明", "value": value_text})
    return result, ""


def validate_zip(
    data: bytes,
    *,
    expected_sha: str = "",
    expected_size: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> Validation:
    result = Validation(size=len(data), sha256=sha256_bytes(data), raw=data)
    metadata = metadata or {}

    if not data:
        result.errors.append("ZIP 是空文件。")
        return result
    if len(data) > MAX_ZIP:
        result.errors.append(f"ZIP 不能超过 {MAX_ZIP // 1024 // 1024} MB。")
        return result
    if not data.startswith(b"PK\x03\x04"):
        result.errors.append("文件不是 ZIP（缺少 PK 文件头）。")
        return result

    if expected_sha:
        wanted = expected_sha.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", wanted):
            result.errors.append("投稿正文里的 SHA-256 格式不正确。")
        elif wanted != result.sha256:
            result.errors.append("附件 SHA-256 与投稿正文不一致，请重新生成投稿内容。")
    if expected_size is not None and expected_size != len(data):
        result.errors.append("附件大小与投稿正文不一致，请重新生成投稿内容。")

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        result.errors.append(f"ZIP 结构损坏：{exc}")
        return result

    with zf:
        infos = zf.infolist()
        if not infos:
            result.errors.append("ZIP 里没有文件。")
            return result
        if len(infos) > MAX_ENTRIES:
            result.errors.append(f"ZIP 最多 {MAX_ENTRIES} 个条目。")
            return result

        names: list[str] = []
        total = 0
        for info in infos:
            name = info.filename
            is_dir = name.endswith("/")
            error = _path_error(name[:-1] if is_dir else name)
            if error:
                result.errors.append(error)
                continue
            if is_dir:
                continue
            if info.flag_bits & 0x1:
                result.errors.append(f"ZIP 不能包含加密文件：{name}")
            if stat.S_ISLNK((info.external_attr >> 16) & 0xFFFF):
                result.errors.append(f"ZIP 不能包含符号链接：{name}")
            if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                result.errors.append(f"只支持 stored/deflate 压缩：{name}")
            if info.file_size > MAX_FILE:
                result.errors.append(f"单个文件不能超过 {MAX_FILE // 1024 // 1024} MB：{name}")
            total += info.file_size
            if total > MAX_UNCOMPRESSED:
                result.errors.append("ZIP 解压后总大小超过 30 MB。")
                total = 0
                break
            danger = _check_dangerous(name)
            if danger:
                result.errors.append(danger)
            if name in names:
                result.errors.append(f"ZIP 里有重名文件：{name}")
            names.append(name)
            result.files.append(name)

        if result.errors:
            return result

        roots = {name.split("/")[0] for name in names}
        if len(roots) != 1:
            result.errors.append("ZIP 里必须只有一个顶层插件文件夹。")
            return result
        root = roots.pop()
        if any(not name.startswith(root + "/") for name in names):
            result.errors.append("所有文件都必须放在插件 id 文件夹里。")
            return result

        manifest_name = f"{root}/plugin.json"
        manifest, error = _read_manifest(zf, manifest_name)
        if error:
            result.errors.append(error)
            return result
        assert manifest is not None

        plugin_id = clean_text(manifest.get("id"), 64)
        if not ID_RE.fullmatch(plugin_id):
            result.errors.append("id 只能使用小写字母、数字、点、下划线、短横线，长度 2-64。")
        if plugin_id != root:
            result.errors.append(f"ZIP 根目录名必须等于插件 id：{plugin_id or '未知'}")
        if any(plugin_id.startswith(prefix) for prefix in RESERVED_PREFIXES):
            result.errors.append("lfelkeea. 和 example. 前缀由维护者保留，投稿请换一个 id。")

        version = clean_text(manifest.get("version"), 40)
        if not VERSION_RE.fullmatch(version):
            result.errors.append("version 必须是 1.0.0 这样的语义化版本。")

        try:
            api_version = int(manifest.get("api_version", 1))
        except (TypeError, ValueError):
            api_version = -1
        if api_version != 1:
            result.errors.append("api_version 必须是 1。")

        entry = clean_text(manifest.get("entry"), 120) or "main.py"
        entry_error = _path_error(entry)
        if entry_error:
            result.errors.append(f"entry 不合法：{entry_error}")
        elif not entry.lower().endswith(".py"):
            result.errors.append("entry 必须是 Python 文件（.py）。")
        elif f"{root}/{entry}" not in names:
            result.errors.append(f"找不到入口文件：{entry}")

        name = clean_text(manifest.get("name"), MAX_NAME)
        if not name:
            result.errors.append("name 不能为空。")
        description = clean_text(manifest.get("description"), MAX_DESCRIPTION)
        author = clean_text(manifest.get("author"), MAX_AUTHOR)

        permissions_raw = manifest.get("permissions", [])
        permissions: list[str] = []
        if not isinstance(permissions_raw, list):
            result.errors.append("permissions 必须是数组。")
        else:
            for index, item in enumerate(permissions_raw, 1):
                permission = clean_text(item, 40)
                if not permission:
                    continue
                if permission not in KNOWN_PERMISSIONS:
                    result.errors.append(f"permissions 第 {index} 项不是 v1 已知权限：{permission}")
                if permission in permissions:
                    result.errors.append(f"permissions 重复：{permission}")
                permissions.append(permission)
            if len(permissions) > len(KNOWN_PERMISSIONS):
                result.errors.append("permissions 数量超出范围。")

        settings = manifest.get("settings", {})
        if not isinstance(settings, dict):
            result.errors.append("settings 必须是对象。")
            settings = {}
        else:
            try:
                if len(json.dumps(settings, ensure_ascii=False).encode("utf-8")) > MAX_SETTINGS:
                    result.errors.append("settings 不能超过 8 KB。")
            except (TypeError, ValueError):
                result.errors.append("settings 必须是可以 JSON 序列化的对象。")

        requires, requires_error = _validate_requires(manifest.get("requires"))
        if requires_error:
            result.errors.append(requires_error)
        highlights, highlights_error = _validate_highlights(manifest.get("highlights"))
        if highlights_error:
            result.errors.append(highlights_error)

        for key, expected in (("id", plugin_id), ("version", version)):
            if metadata.get(key) not in (None, "") and clean_text(metadata.get(key), 80) != expected:
                result.errors.append(f"投稿正文的 {key} 与 plugin.json 不一致。")

        if result.errors:
            return result

        result.ok = True
        result.plugin = {
            "id": plugin_id,
            "name": name or plugin_id,
            "version": version,
            "author": author,
            "description": description,
            "api_version": 1,
            "permissions": permissions,
            "permission_labels": [KNOWN_PERMISSIONS[item] for item in permissions],
            "requires": requires,
            "highlights": highlights,
            "settings": settings,
            "entry": entry,
            "bundled": False,
            "featured": False,
            "file": f"plugins/{plugin_id}-{version}.zip",
            "size": len(data),
            "sha256": result.sha256,
            "community": True,
        }
        if not permissions:
            result.warnings.append("插件没有声明任何权限，运行时只能使用 api.log()。")
        return result


def parse_issue_body(body: str) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    body = body or ""
    if len(body) > MAX_BODY:
        return None, ["Issue 正文过长，请精简到 64 KB 以内。"]
    if MARKER not in body:
        return None, ["没有找到投稿标记，请用站点的「投稿插件」页生成 Issue。"]
    match = JSON_BLOCK_RE.search(body, body.index(MARKER))
    if not match:
        return None, ["没有找到投稿用的 JSON 元数据块。"]
    try:
        meta = json.loads(match.group(1))
    except ValueError as exc:
        return None, [f"投稿 JSON 不是合法格式：{exc}"]
    if not isinstance(meta, dict):
        return None, ["投稿 JSON 顶层必须是对象。"]
    if meta.get("schema") not in (None, 1):
        return None, ["不支持的投稿格式版本。"]
    return meta, errors


def attachment_urls(body: str) -> list[str]:
    seen: list[str] = []
    for match in ATTACHMENT_RE.finditer(body or ""):
        url = match.group(0).rstrip(".,;:!?)")
        if url not in seen:
            seen.append(url)
    # 优先尝试文件名看起来是 ZIP 的附件。
    return sorted(seen, key=lambda item: (0 if ".zip" in item.lower() else 1))


def _download(url: str, token: str = "") -> bytes:
    if url.startswith("file://"):
        path = Path(urllib.request.url2pathname(url[7:]))
        data = path.read_bytes()
        if len(data) > MAX_ZIP:
            raise ValueError("文件超过 6 MB。")
        return data

    headers = {"User-Agent": "DKL-Plugin-Review/1.0"}
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            data = response.read(MAX_ZIP + 1)
    except urllib.error.HTTPError as exc:
        # 公开 Issue 的附件不需要令牌；先匿名读取，避免重定向到对象存储时带上 Authorization。
        if token and exc.code in (401, 403):
            headers["Authorization"] = f"Bearer {token}"
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=45) as response:
                data = response.read(MAX_ZIP + 1)
        else:
            raise
    if len(data) > MAX_ZIP:
        raise ValueError("附件超过 6 MB。")
    return data


def _load_event(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _issue_from_event(event: dict[str, Any]) -> dict[str, Any]:
    issue = event.get("issue") or {}
    return {
        "number": issue.get("number"),
        "title": clean_text(issue.get("title"), 200),
        "body": issue.get("body") or "",
        "user": ((issue.get("user") or {}).get("login") or ""),
        "created_at": issue.get("created_at") or "",
        "labels": [
            item.get("name", "")
            for item in (issue.get("labels") or [])
            if isinstance(item, dict)
        ],
    }


def _download_attachment(body: str, token: str) -> tuple[bytes, str]:
    urls = attachment_urls(body)
    if not urls:
        raise ValueError("没有找到 ZIP 附件。请把插件包拖进 Issue 正文并等待上传完成。")
    errors: list[str] = []
    for url in urls:
        try:
            data = _download(url, token)
        except Exception as exc:  # noqa: BLE001 - 逐个附件尝试
            errors.append(f"{url}: {exc}")
            continue
        if not data.startswith(b"PK\x03\x04"):
            errors.append(f"{url}: 不是 ZIP 文件")
            continue
        return data, url
    raise ValueError("附件下载失败：" + "；".join(errors[:3]))


def validate_event(event: dict[str, Any], token: str = "", attachment: str = "") -> tuple[Validation, dict[str, Any]]:
    issue = _issue_from_event(event)
    meta, errors = parse_issue_body(issue["body"])
    if errors or meta is None:
        result = Validation(errors=errors or ["投稿正文解析失败。"])
        return result, {"issue": issue, "meta": meta or {}, "attachment_url": ""}

    if attachment:
        data = Path(attachment).read_bytes()
        url = f"file://{Path(attachment).resolve()}"
    else:
        try:
            data, url = _download_attachment(issue["body"], token)
        except Exception as exc:  # noqa: BLE001 - 转成可评论的错误
            result = Validation(errors=[str(exc)])
            return result, {"issue": issue, "meta": meta, "attachment_url": ""}

    expected_size = meta.get("size")
    try:
        expected_size_int = int(expected_size) if expected_size not in (None, "") else None
    except (TypeError, ValueError):
        expected_size_int = -1
    result = validate_zip(
        data,
        expected_sha=clean_text(meta.get("sha256"), 64),
        expected_size=expected_size_int,
        metadata=meta,
    )
    result.archive_name = clean_text(meta.get("archive_name"), 200)
    return result, {"issue": issue, "meta": meta, "attachment_url": url}


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return default


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _registry_entry(plugin: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "id", "name", "version", "author", "description", "api_version",
        "permissions", "permission_labels", "requires", "highlights",
        "settings", "bundled", "featured", "file", "size", "sha256", "community",
    )
    return {key: plugin.get(key) for key in keys}


def _community_record(plugin: dict[str, Any], issue: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": plugin["id"],
        "version": plugin["version"],
        "submitted_by": issue.get("user", ""),
        "issue": issue.get("number"),
        "submitted_at": issue.get("created_at", ""),
        "approved_at": date.today().isoformat(),
        "sha256": plugin["sha256"],
        "size": plugin["size"],
        "file": plugin["file"],
        # 保存完整注册表条目，便于 build_plugins.py 在重建 plugins.json 时恢复名称、描述和权限。
        "entry": _registry_entry(plugin),
    }


def _version_key(value: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", value)
    return tuple(int(item) for item in numbers[:3]) or (0,)


def publish_event(
    event: dict[str, Any],
    *,
    repo_root: Path = ROOT,
    token: str = "",
    attachment: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    validation, context = validate_event(event, token=token, attachment=attachment)
    issue = context["issue"]
    meta = context.get("meta") or {}
    base = {
        "ok": False,
        "issue": issue,
        "attachment_url": context.get("attachment_url", ""),
        "errors": list(validation.errors),
        "warnings": list(validation.warnings),
        "plugin": validation.plugin,
        "sha256": validation.sha256,
        "size": validation.size,
        "files": [],
    }
    if not validation.ok:
        return base, context

    plugin = validation.plugin
    plugin_id = plugin["id"]
    version = plugin["version"]

    registry_path = repo_root / "website" / "public" / "plugins.json"
    community_path = repo_root / "website" / "community_plugins.json"
    plugin_dir = repo_root / "website" / "public" / "plugins"
    registry = _load_json(registry_path, {"plugins": []})
    community = _load_json(community_path, {"schema": 1, "plugins": []})
    entries = registry.get("plugins")
    if not isinstance(entries, list):
        entries = []
    community_entries = community.get("plugins")
    if not isinstance(community_entries, list):
        community_entries = []

    existing = next((item for item in entries if item.get("id") == plugin_id), None)
    community_existing = next((item for item in community_entries if item.get("id") == plugin_id), None)
    if existing is not None and community_existing is None:
        base["errors"].append("这个插件 id 已被官方或内置插件占用，不能覆盖。")
        return base, context
    if existing is not None:
        old_version = str(existing.get("version", "0.0.0"))
        if _version_key(version) <= _version_key(old_version):
            base["errors"].append(f"已发布版本 {old_version}，新版本必须更高。")
            return base, context
        if community_existing and community_existing.get("submitted_by") not in ("", issue.get("user")):
            base["warnings"].append(
                "这是别人提交过的插件 id（上次投稿者："
                + str(community_existing.get("submitted_by") or "未知")
                + "）。请确认你有权继续维护它。"
            )

    zip_rel = f"plugins/{plugin_id}-{version}.zip"
    zip_path = plugin_dir / f"{plugin_id}-{version}.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    zip_path.write_bytes(validation.raw)

    entry = _registry_entry(plugin)
    entry["file"] = zip_rel
    entry["size"] = validation.size
    entry["sha256"] = validation.sha256
    if existing is not None:
        index = entries.index(existing)
        entries[index] = entry
    else:
        entries.append(entry)
    registry["plugins"] = entries
    registry["updated"] = date.today().isoformat()
    registry.setdefault("api_version", 1)
    if "pet_version" not in registry:
        registry["pet_version"] = ""

    record = _community_record(plugin, issue)
    if community_existing is not None:
        index = community_entries.index(community_existing)
        community_entries[index] = record
    else:
        community_entries.append(record)
    community["schema"] = 1
    community["plugins"] = community_entries

    _write_json(registry_path, registry)
    _write_json(community_path, community)

    base.update(
        {
            "ok": True,
            "plugin": entry,
            "zip_rel": zip_rel,
            "zip_path": str(zip_path),
            "files": [
                f"website/public/{zip_rel}",
                "website/public/plugins.json",
                "website/community_plugins.json",
            ],
        }
    )
    return base, context


def _run_gh(*args: str) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["gh", *args],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"gh {' '.join(args)} 失败：{detail}")
    return result


def deploy_result(result: dict[str, Any], repo: str = "LFELKEEA/darkitten-launcher", repo_root: Path = ROOT) -> None:
    if not result.get("ok"):
        raise RuntimeError("发布结果不是成功状态，拒绝部署。")
    zip_rel = str(result.get("zip_rel") or "")
    if not re.fullmatch(r"plugins/[a-z0-9][a-z0-9_.-]{1,63}-\d+\.\d+\.\d+[0-9A-Za-z.+-]*\.zip", zip_rel):
        raise RuntimeError("发布结果里的 ZIP 路径不合法。")
    website = repo_root / "website"
    public = website / "public"
    full = repo if "/" in repo else f"LFELKEEA/{repo}"
    issue = result.get("issue", {}).get("number")
    plugin = result.get("plugin", {})
    message = f"plugin: publish {plugin.get('id')} v{plugin.get('version')}"
    if issue:
        message += f" (#{issue})"

    sys.path.insert(0, str(website))
    from build_release import ensure_repo, find_gh, upload_tree  # noqa: E402

    gh = find_gh()
    target = ensure_repo(gh, full)
    main_files = [
        (f"website/public/{zip_rel}", public / zip_rel),
        ("website/public/plugins.json", public / "plugins.json"),
        ("website/community_plugins.json", website / "community_plugins.json"),
    ]
    pages_files = [
        (zip_rel, public / zip_rel),
        ("plugins.json", public / "plugins.json"),
    ]
    upload_tree(gh, target, "main", main_files, message, base_tree=True)
    upload_tree(gh, target, "gh-pages", pages_files, message, base_tree=True)


def _human_size(value: Any) -> str:
    try:
        size = int(value)
    except (TypeError, ValueError):
        return "未知"
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.2f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"


def _md_text(value: Any, limit: int = 200) -> str:
    text = " ".join(str(value or "").split())
    return text.replace("`", "'").replace("|", "\\|")[:limit]


def _md_permissions(plugin: dict[str, Any]) -> str:
    permissions = plugin.get("permissions") or []
    if not isinstance(permissions, list) or not permissions:
        return "未声明权限"
    labels = []
    for permission in permissions:
        name = str(permission)
        labels.append(f"`{name}`（{KNOWN_PERMISSIONS.get(name, '未知权限')}）")
    return "、".join(labels)


def _report_header(result: dict[str, Any], title: str) -> list[str]:
    plugin = result.get("plugin") or {}
    issue = result.get("issue") or {}
    submitter = _md_text(issue.get("user"), 80)
    archive = _md_text(result.get("archive_name") or "", 120)
    attachment = str(result.get("attachment_url") or "")
    if not attachment.startswith("https://"):
        attachment = ""
    lines = [
        "<!-- dkl-plugin-check:v1 -->",
        f"## {title}",
        "",
        "| 项目 | 内容 |",
        "| --- | --- |",
        f"| 插件 | **{_md_text(plugin.get('name'), 80) or '未命名'}** |",
        f"| 插件 id | `{_md_text(plugin.get('id'), 64) or '未知'}` |",
        f"| 版本 | `{_md_text(plugin.get('version'), 40) or '未知'}` |",
        f"| 作者 | **{_md_text(plugin.get('author'), 80) or '未署名'}** |",
        f"| 权限 | {_md_permissions(plugin)} |",
        f"| 附件大小 | {_human_size(result.get('size'))} |",
        f"| SHA-256 | `{_md_text(result.get('sha256'), 64) or '未知'}` |",
    ]
    if archive:
        lines.append(f"| 附件名 | `{archive}` |")
    if submitter:
        lines.append(f"| 投稿人 | `@{submitter}` |")
    if attachment:
        lines.append(f"| 附件 | {attachment} |")
    lines.append("")
    return lines


def _report_validate(result: dict[str, Any]) -> str:
    ok = bool(result.get("ok"))
    lines = _report_header(result, "自动校验：✅ 通过" if ok else "自动校验：❌ 未通过")
    errors = [str(item) for item in (result.get("errors") or [])]
    warnings = [str(item) for item in (result.get("warnings") or [])]
    lines.append("### 检查结果")
    if errors:
        lines.extend(f"- ❌ {_md_text(item, 500)}" for item in errors)
    else:
        lines.append("- ✅ ZIP 结构、清单、权限和危险文件检查均通过。")
    if warnings:
        lines.append("")
        lines.append("### 提醒")
        lines.extend(f"- ⚠️ {_md_text(item, 500)}" for item in warnings)
    lines.append("")
    if ok:
        lines.append("### 下一步")
        lines.append("- 等待维护者阅读代码并确认权限。")
        lines.append("- 维护者通过后评论 `/approve`，Actions 会再次校验并自动发布。")
        lines.append("- 需要退回时评论 `/reject 原因`，Issue 会关闭并记录原因。")
    else:
        lines.append("### 下一步")
        lines.append("- 投稿人请按上面的提示修改插件包。")
        lines.append("- 修改后编辑本 Issue，重新拖入新的 ZIP 并同步正文里的 SHA-256 / 大小。")
        lines.append("- 自动检查会重新运行，不需要关闭本 Issue。")
    lines.append("")
    lines.append("> 自动校验只解析 ZIP，不会执行插件代码。人工审核仍然是必需环节。")
    lines.append("<!-- dkl-plugin-check:end -->")
    return "\n".join(lines) + "\n"


def _report_publish(result: dict[str, Any]) -> str:
    ok = bool(result.get("ok"))
    lines = _report_header(result, "发布结果：✅ 已发布" if ok else "发布结果：❌ 失败")
    lines[0] = "<!-- dkl-plugin-publish:v1 -->"
    errors = [str(item) for item in (result.get("errors") or [])]
    warnings = [str(item) for item in (result.get("warnings") or [])]
    if ok:
        plugin = result.get("plugin") or {}
        zip_rel = str(result.get("zip_rel") or "")
        lines.append("### 发布内容")
        lines.append(f"- 插件：**{_md_text(plugin.get('name'), 80)}**（`{_md_text(plugin.get('id'), 64)}`）")
        lines.append(f"- 版本：`{_md_text(plugin.get('version'), 40)}`")
        if zip_rel:
            lines.append(f"- ZIP：`{_md_text(zip_rel, 200)}`")
        lines.append("- 已更新 `plugins.json`、`community_plugins.json` 和 gh-pages 下载包。")
        lines.append("")
        lines.append("### 入口")
        lines.append("- 插件库：https://lfelkeea.github.io/darkitten-launcher/plugins.html")
        lines.append("- 插件市场：桌宠 → 插件管理 → 插件市场，刷新后可见。")
    else:
        lines.append("### 失败原因")
        if errors:
            lines.extend(f"- ❌ {_md_text(item, 500)}" for item in errors)
        else:
            lines.append("- ❌ 未返回具体原因，请查看 Actions 日志。")
        lines.append("")
        lines.append("修正后可以重新评论 `/approve`，或按提示更新 Issue 后再试。")
    if warnings:
        lines.append("")
        lines.append("### 提醒")
        lines.extend(f"- ⚠️ {_md_text(item, 500)}" for item in warnings)
    lines.append("")
    lines.append("> 发布由 GitHub Actions 执行；人工审核记录保留在本 Issue。")
    lines.append("<!-- dkl-plugin-check:end -->")
    return "\n".join(lines) + "\n"


def markdown_report(result: dict[str, Any], kind: str = "validate") -> str:
    if kind == "publish":
        return _report_publish(result)
    return _report_validate(result)


def write_stdout_utf8(text: str) -> None:
    data = text.encode("utf-8")
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None:
        buffer.write(data)
        buffer.flush()
        return
    sys.stdout.write(text)


def _print_result(result: dict[str, Any]) -> None:
    if result.get("ok"):
        plugin = result.get("plugin") or {}
        print(f"OK {plugin.get('id')} v{plugin.get('version')} {result.get('size')} B")
        for warning in result.get("warnings", []):
            print("WARN " + warning)
        return
    print("FAILED")
    for error in result.get("errors", []):
        print(" - " + error)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DKL 插件投稿校验与发布")
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("validate", "publish"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--event", required=True, help="GitHub event JSON 路径")
        cmd.add_argument("--result", required=True, help="输出结果 JSON")
        cmd.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""))
        cmd.add_argument("--attachment", default="", help="本地 ZIP，仅用于调试")
        if name == "publish":
            cmd.add_argument("--repo-root", default=str(ROOT))

    deploy = sub.add_parser("deploy")
    deploy.add_argument("--result", required=True, help="publish 生成的结果 JSON")
    deploy.add_argument("--repo", default="LFELKEEA/darkitten-launcher")
    deploy.add_argument("--repo-root", default=str(ROOT))

    report = sub.add_parser("report")
    report.add_argument("--result", required=True, help="validate/publish 生成的结果 JSON")
    report.add_argument("--kind", choices=("validate", "publish"), default="validate")

    args = parser.parse_args(argv)
    if args.command == "report":
        result = _load_json(Path(args.result), {})
        write_stdout_utf8(markdown_report(result, args.kind))
        return 0
    if args.command == "deploy":
        result = _load_json(Path(args.result), {})
        deploy_result(result, repo=args.repo, repo_root=Path(args.repo_root))
        print("deployed")
        return 0

    event = _load_event(args.event)
    if args.command == "validate":
        validation, context = validate_event(event, args.token, args.attachment)
        result = {
            "ok": validation.ok,
            "errors": validation.errors,
            "warnings": validation.warnings,
            "plugin": validation.plugin,
            "sha256": validation.sha256,
            "size": validation.size,
            "archive_name": validation.archive_name,
            "files": validation.files,
            "issue": context["issue"],
            "meta": context.get("meta") or {},
            "attachment_url": context.get("attachment_url", ""),
        }
    else:
        result, _ = publish_event(
            event,
            repo_root=Path(args.repo_root),
            token=args.token,
            attachment=args.attachment,
        )
    _write_json(Path(args.result), result)
    _print_result(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
