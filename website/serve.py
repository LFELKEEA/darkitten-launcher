from __future__ import annotations

import argparse
import functools
import os
import posixpath
import socket
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

BASE = Path(__file__).resolve().parent
ROOT = BASE / "public"
STATIC_FILES = {
    "/",
    "/index.html",
    "/feedback.html",
    "/style.css",
    "/app.js",
    "/feedback-admin.css",
    "/feedback-admin.js",
    "/update.json",
    "/robots.txt",
}
ASSET_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".ico"}
DOWNLOAD_SUFFIXES = {".zip", ".txt"}
CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; "
    "script-src 'self'; connect-src 'self' https://api.github.com; "
    "base-uri 'none'; form-action 'self' mailto:; frame-ancestors 'none'"
)


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
    print("反馈入口：GitHub Issues；开发者反馈表需从启动器打开")
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
