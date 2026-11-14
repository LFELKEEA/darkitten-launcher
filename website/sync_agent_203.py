"""把 mod_agent/dist 的模组工坊发布包同步到站点目录（不部署）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_release as br

manifest_path = br.MOD_AGENT_DIST / "mod_agent_release.json"
data = json.loads(manifest_path.read_text(encoding="utf-8"))
archive = br.MOD_AGENT_DIST / f"DarkittenModAgent-{data['version']}.zip"
data["download_url"] = f"downloads/{archive.name}"
manifest_path.write_text(
    json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print("dist manifest updated:", data["download_url"])
print("sync:", br.load_mod_agent_release())
