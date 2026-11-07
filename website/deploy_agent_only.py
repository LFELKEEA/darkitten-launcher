from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_release as br

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

br.upload_tree(gh, full, "gh-pages", files, "deploy: Mod Agent 1.3.1", base_tree=True)
print("done", full)
