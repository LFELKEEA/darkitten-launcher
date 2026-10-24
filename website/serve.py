from __future__ import annotations

import argparse
import functools
import json
import os
import posixpath
import secrets
import socket
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

BASE = Path(__file__).resolve().parent
ROOT = BASE / "public"
REPORTS = Path(os.environ.get("DKL_REPORTS_DIR") or (BASE / "reports"))
MAX_BODY = 16 * 1024
RATE_LIMIT = 5
RATE_WINDOW = 10 * 60
REPORT_TYPES = {"bug", "ui", "download", "update", "suggestion", "other"}
STATIC_FILES = {"/", "/index.html", "/style.css", "/app.js", "/update.json", "/robots.txt"}
ASSET_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".ico"}
DOWNLOAD_SUFFIXES = {".zip", ".txt"}
CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; "
    "script-src 'self'; connect-src 'self'; base-uri 'none'; "
    "form-action 'self' mailto:; frame-ancestors 'none'"
)

_rate_lock = threading.Lock()
_rate_hits: dict[str, list[float]] = {}


def _rate_allowed(address: str) -> bool:
    now = time.time()
    with _rate_lock:
        hits = [stamp for stamp in _rate_hits.get(address, []) if now - stamp < RATE_WINDOW]
        if len(hits) >= RATE_LIMIT:
            _rate_hits[address] = hits
            return False
        hits.append(now)
        _rate_hits[address] = hits
        if len(_rate_hits) > 512:
            for key in [key for key, value in _rate_hits.items() if not value or now - value[-1] > RATE_WINDOW]:
                _rate_hits.pop(key, None)
    return True


def _clean(value: object, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _allowed_path(raw_path: str) -> bool:
    decoded = unquote(urlsplit(raw_path).path)
    if "\\" in decoded or "\x00" in decoded:
        return False
    path = posixpath.normpath(decoded)
    if path in STATIC_FILES:
        return True
    parts = [part for part in path.split("/") if part]
    if len(parts) != 2:
        return False
    folder, name = parts
    suffix = Path(name).suffix.lower()
    if folder == "assets":
        return suffix in ASSET_SUFFIXES
    if folder == "downloads":
        return suffix in DOWNLOAD_SUFFIXES
    return False


class DownloadHandler(SimpleHTTPRequestHandler):
    server_version = "DKLDownloadSite/1.1"

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        self.send_header("Content-Security-Policy", CSP)
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:
        print(f"[site] {self.address_string()} {fmt % args}")

    def send_head(self):
        if not _allowed_path(self.path):
            self.send_error(404)
            return None
        if urlsplit(self.path).path in ("", "/"):
            self.path = "/index.html"
        return super().send_head()

    def list_directory(self, path):
        self.send_error(404)
        return None

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if urlsplit(self.path).path != "/api/bug-report":
            self.send_error(404)
            return

        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("application/json"):
            self._json(415, {"ok": False, "error": "请使用 application/json 提交反馈。"})
            return

        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._json(411, {"ok": False, "error": "缺少有效的 Content-Length。"})
            return
        if length <= 0 or length > MAX_BODY:
            self._json(413, {"ok": False, "error": "反馈内容过长，请精简到 16 KB 以内。"})
            return

        address = self.client_address[0]
        if not _rate_allowed(address):
            self._json(429, {"ok": False, "error": "提交太频繁了，请十分钟后再试。"})
            return

        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json(400, {"ok": False, "error": "反馈内容不是有效的 JSON。"})
            return
        if not isinstance(payload, dict):
            self._json(400, {"ok": False, "error": "反馈内容格式不正确。"})
            return

        if _clean(payload.get("website"), 200):
            self._json(201, {"ok": True, "id": "ignored"})
            return

        title = _clean(payload.get("title"), 80)
        description = _clean(payload.get("description"), 4000)
        if len(title) < 2 or len(description) < 10:
            self._json(400, {"ok": False, "error": "请填写至少 2 个字的标题和 10 个字的描述。"})
            return

        kind = _clean(payload.get("type"), 20).lower()
        if kind not in REPORT_TYPES:
            kind = "other"

        now = datetime.now(timezone.utc)
        report_id = now.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
        record = {
            "id": report_id,
            "received_at": now.isoformat(timespec="seconds"),
            "type": kind,
            "version": _clean(payload.get("version"), 40),
            "title": title,
            "description": description,
            "contact": _clean(payload.get("contact"), 120),
            "page": _clean(payload.get("page"), 300),
            "user_agent": _clean(self.headers.get("User-Agent") or payload.get("user_agent"), 300),
            "remote_address": address,
        }

        try:
            REPORTS.mkdir(parents=True, exist_ok=True)
            target = REPORTS / f"report-{report_id}.json"
            target.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"[site] 反馈保存失败：{exc}")
            self._json(500, {"ok": False, "error": "反馈暂时无法保存，请改用邮件联系。"})
            return

        print(f"[site] 收到反馈 {report_id}，类型 {kind}")
        self._json(201, {"ok": True, "id": report_id})


def lan_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return str(sock.getsockname()[0])
    except Exception:
        return "127.0.0.1"
    finally:
        sock.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="DKL 下载站本地服务器")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--lan", action="store_true", help="允许局域网访问")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    bind = "0.0.0.0" if args.lan else "127.0.0.1"
    os.chdir(ROOT)
    handler = functools.partial(DownloadHandler, directory=str(ROOT))
    try:
        server = ThreadingHTTPServer((bind, args.port), handler)
    except OSError as exc:
        print(f"无法启动下载站：{exc}")
        print("端口可能已被占用。可以换一个端口，例如：")
        print(f"  python serve.py --port {args.port + 1}")
        return 1

    server.daemon_threads = True
    local_url = f"http://127.0.0.1:{args.port}/"
    print("=" * 58)
    print("Darkitten Launcher 下载站已启动")
    print(f"本机地址：{local_url}")
    print(f"反馈保存目录：{REPORTS}")
    if args.lan:
        print(f"局域网地址：http://{lan_ip()}:{args.port}/")
    print("按 Ctrl+C 停止服务")
    print("=" * 58)

    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(local_url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n下载站已停止")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
