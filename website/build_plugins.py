from __future__ import annotations

"""把桌宠插件打包成可下载的 ZIP，并生成站点用的 plugins.json。

用法：
    python build_plugins.py

产物：
    public/plugins/<id>-<version>.zip
    public/plugins/docs/PLUGIN_DEV.md
    public/plugins/docs/AI_PLUGIN_PROMPT.md
    public/plugins.json
"""

import hashlib
import json
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

SITE = Path(__file__).resolve().parent
PUBLIC = SITE / "public"
PET = SITE.parent / "desktop_pet"
SOURCE = PET / "plugins"
DOCS = PET / "docs"
DEST = PUBLIC / "plugins"
REGISTRY = PUBLIC / "plugins.json"

PERMISSION_LABELS = {
    "pet.say": "说话",
    "pet.action": "动作",
    "ai.chat": "AI 对话",
    "screen.capture": "截屏",
    "timer": "定时器",
    "events": "事件",
    "settings": "设置",
    "storage": "本地存储",
}

EXCLUDED_PARTS = {"__pycache__", ".state"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".log"}

FEATURED = {"lfelkeea.focus_guard", "lfelkeea.qq_bridge"}


def plugin_files(folder: Path) -> list[Path]:
    return sorted(
        path
        for path in folder.rglob("*")
        if path.is_file()
        and not any(part in EXCLUDED_PARTS for part in path.relative_to(folder).parts)
        and path.suffix.lower() not in EXCLUDED_SUFFIXES
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 256), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package(folder: Path, manifest: dict) -> dict:
    plugin_id = str(manifest["id"])
    version = str(manifest.get("version", "0.0.0"))
    target = DEST / f"{plugin_id}-{version}.zip"
    files = plugin_files(folder)
    if not files:
        raise SystemExit(f"{folder} 里没有可打包的文件")

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            archive.write(path, f"{folder.name}/{path.relative_to(folder).as_posix()}")

    permissions = [str(item) for item in manifest.get("permissions", [])]
    requires = []
    for item in manifest.get("requires", []) or []:
        if not isinstance(item, dict):
            continue
        requires.append(
            {
                "name": str(item.get("name", "")),
                "version": str(item.get("version", "")),
                "url": str(item.get("url", "")),
                "note": str(item.get("note", "")),
            }
        )
    highlights = []
    for item in manifest.get("highlights", []) or []:
        if not isinstance(item, dict):
            continue
        highlights.append(
            {"label": str(item.get("label", "")), "value": str(item.get("value", ""))}
        )
    return {
        "id": plugin_id,
        "name": str(manifest.get("name", plugin_id)),
        "version": version,
        "author": str(manifest.get("author", "")),
        "description": str(manifest.get("description", "")),
        "api_version": int(manifest.get("api_version", 1)),
        "permissions": permissions,
        "permission_labels": [PERMISSION_LABELS.get(item, item) for item in permissions],
        "requires": requires,
        "highlights": highlights,
        "settings": manifest.get("settings", {}),
        "bundled": folder.name.startswith("example."),
        "featured": plugin_id in FEATURED,
        "file": f"plugins/{target.name}",
        "size": target.stat().st_size,
        "sha256": sha256(target),
    }


def copy_docs() -> None:
    docs_dest = DEST / "docs"
    docs_dest.mkdir(parents=True, exist_ok=True)
    for name in ("PLUGIN_DEV.md", "AI_PLUGIN_PROMPT.md"):
        source = DOCS / name
        if source.is_file():
            shutil.copy2(source, docs_dest / name)


def main() -> int:
    if not SOURCE.is_dir():
        raise SystemExit(f"找不到插件目录：{SOURCE}")
    DEST.mkdir(parents=True, exist_ok=True)

    released = json.loads((SITE / "pet_release.json").read_text(encoding="utf-8"))
    pet_version = str(released.get("version", "1.1.0"))

    entries: list[dict] = []
    for folder in sorted(SOURCE.iterdir()):
        manifest_path = folder / "plugin.json"
        if not folder.is_dir() or not manifest_path.is_file():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if "id" not in manifest:
            raise SystemExit(f"{manifest_path} 缺少 id")
        entries.append(package(folder, manifest))

    entries.sort(key=lambda item: (not item["featured"], item["bundled"], item["name"]))

    registry = {
        "updated": datetime.now().strftime("%Y-%m-%d"),
        "api_version": 1,
        "pet_version": pet_version,
        "plugins": entries,
    }
    REGISTRY.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    copy_docs()

    total = sum(item["size"] for item in entries)
    for item in entries:
        print(f"{item['id']:<28} {item['version']:<8} {item['size']:>8} B  {item['file']}")
    print(f"{len(entries)} plugins, {total} B total -> {REGISTRY}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
