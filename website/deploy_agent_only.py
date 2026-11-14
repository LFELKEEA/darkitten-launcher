from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_release as br

# GitHub Pages 单文件硬上限是 100MB；超过这个大小的包必须走 Release 资源。
MAX_PAGES_PACKAGE_BYTES = 100 * 1024 * 1024

gh = br.find_gh()
full = br.ensure_repo(gh, br.DEFAULT_REPO)
files = [
    ("index.html", br.PUBLIC / "index.html"),
    ("mod_agent_release.json", br.PUBLIC / "mod_agent_release.json"),
]
for rel, path in files:
    if not path.is_file():
        raise SystemExit(f"缺少 {path}")
    size = path.stat().st_size
    if size > br.MAX_PAGES_FILE_BYTES:
        raise SystemExit(f"{rel} 过大：{size}")
    print("upload", rel, size)

manifest = json.loads(
    (br.PUBLIC / "mod_agent_release.json").read_text(encoding="utf-8")
)
version = str(manifest.get("version") or "").strip()
if not version:
    raise SystemExit("mod_agent_release.json 缺少 version")

# 清单里的 download_url 是相对路径时，发布包随站点一起上传；
# 绝对地址（GitHub Release）则保持原样，只更新页面和清单。
url = str(manifest.get("download_url") or "").strip()
if not url.startswith(("http://", "https://")):
    package = (br.PUBLIC / url).resolve()
    if br.PUBLIC.resolve() not in package.parents:
        raise SystemExit("mod_agent_release.json 的 download_url 越出网站目录")
    if not package.is_file():
        raise SystemExit(f"缺少模组工坊发布包：{package}")
    size = package.stat().st_size
    if size > MAX_PAGES_PACKAGE_BYTES:
        raise SystemExit(
            f"发布包 {size} 字节超过 GitHub Pages 单文件上限，请改走 GitHub Releases。"
        )
    if size != int(manifest.get("size") or 0):
        raise SystemExit(
            f"发布包大小与清单不一致：清单 {manifest.get('size')}，实际 {size}"
        )
    digest = br.sha256(package)
    if digest != str(manifest.get("sha256") or "").strip().lower():
        raise SystemExit(
            f"发布包 SHA-256 与清单不一致：清单 {manifest.get('sha256')}，实际 {digest}"
        )
    files.append((package.relative_to(br.PUBLIC).as_posix(), package))
    print("upload package", url, size, digest)

br.upload_tree(
    gh,
    full,
    "gh-pages",
    files,
    f"deploy: Mod Agent {version}",
    base_tree=True,
)
print("done", full)
