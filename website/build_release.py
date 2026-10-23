from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import zipfile
from datetime import date, datetime
from pathlib import Path

SITE = Path(__file__).resolve().parent
PUBLIC = SITE / "public"
DOWNLOADS = PUBLIC / "downloads"
DEFAULT_SOURCE = Path(os.environ.get("DKL_SOURCE", r"C:\tmp\dkl_bug_work\DarkittenLauncher-1.9.8-source"))
DELIVERY = Path(os.environ.get("DKL_DELIVERY", str(Path.home() / "Tianxi")))
DEFAULT_REPO = "darkitten-launcher"

EXCLUDED_NAMES = {
    "config.json",
    "social.json",
    "frpc.exe",
}
EXCLUDED_PARTS = {
    "__pycache__",
    "cache",
    "logs",
    "minecraft",
    "servers",
    "rooms",
    "skins",
    "test_game",
    "updates",
    ".git",
    ".idea",
    ".vscode",
    ".ai-memory",
    "node_modules",
    "reports",
    ".deploy",
}
TEXT_SUFFIXES = {
    ".py", ".txt", ".json", ".md", ".bat", ".cmd", ".ps1", ".sh",
    ".html", ".css", ".js", ".svg", ".yml", ".yaml", ".toml", ".ini",
    ".cfg", ".properties", ".xml", ".java", ".gradle", ".kts", ".csv",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_version(source: Path) -> str:
    text = (source / "stellar" / "config.py").read_text(encoding="utf-8")
    match = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', text)
    if not match:
        raise SystemExit("无法从 stellar/config.py 读取 APP_VERSION")
    return match.group(1)


def source_include(relative: Path) -> bool:
    if any(part in EXCLUDED_PARTS for part in relative.parts):
        return False
    if relative.name in EXCLUDED_NAMES:
        return False
    if relative.suffix.lower() in (".pyc", ".pyo", ".log"):
        return False
    if relative.parts and relative.parts[0] == "qa" and relative.suffix.lower() == ".png":
        return False
    return True


def website_include(relative: Path) -> bool:
    if any(part in EXCLUDED_PARTS for part in relative.parts):
        return False
    if "downloads" in relative.parts:
        return False
    if relative.suffix.lower() in (".pyc", ".pyo", ".log"):
        return False
    return True


def write_zip(target: Path, entries: list[tuple[Path, str]]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for source, arcname in entries:
            archive.write(source, arcname)


def source_files(source: Path) -> list[Path]:
    return sorted(
        path.relative_to(source)
        for path in source.rglob("*")
        if path.is_file() and source_include(path.relative_to(source))
    )


def website_files() -> list[Path]:
    return sorted(
        path.relative_to(SITE)
        for path in SITE.rglob("*")
        if path.is_file() and website_include(path.relative_to(SITE))
    )


def decode_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def text_entries(source: Path) -> list[tuple[str, Path]]:
    entries: list[tuple[str, Path]] = []
    for relative in source_files(source):
        if relative.suffix.lower() in TEXT_SUFFIXES:
            entries.append((relative.as_posix(), source / relative))
    for relative in website_files():
        if relative.suffix.lower() in TEXT_SUFFIXES or relative.name == "_headers":
            entries.append((f"website/{relative.as_posix()}", SITE / relative))
    return entries


def write_source_txt(target: Path, source: Path, version: str) -> None:
    entries = text_entries(source)
    lines = [
        f"Darkitten Launcher {version} 完整源代码",
        f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"文本文件：{len(entries)}",
        "=" * 80,
        "目录",
        "=" * 80,
    ]
    lines.extend(f"  {name}" for name, _ in entries)
    lines.extend(["", "=" * 80, ""])
    for name, path in entries:
        lines.append("#" * 80)
        lines.append(f"# FILE: {name}")
        lines.append("#" * 80)
        lines.append(decode_text(path).rstrip())
        lines.append("")
        lines.append("")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def sync_site_version(version: str) -> None:
    index = PUBLIC / "index.html"
    text = index.read_text(encoding="utf-8")
    text = re.sub(r"1\.9\.\d+", version, text)
    index.write_text(text, encoding="utf-8")

    script = PUBLIC / "app.js"
    text = script.read_text(encoding="utf-8")
    text = re.sub(r'(\|\| ")\d+\.\d+\.\d+(")', rf"\g<1>{version}\g<2>", text)
    script.write_text(text, encoding="utf-8")


def find_gh() -> str:
    for candidate in (shutil.which("gh"), r"C:\Program Files\GitHub CLI\gh.exe"):
        if candidate and Path(candidate).is_file():
            return candidate
    raise SystemExit("未找到 GitHub CLI（gh），请先安装并登录。")


def run_gh(gh: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        [gh, *args], text=True, encoding="utf-8", errors="replace", capture_output=True
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise SystemExit(f"GitHub 命令失败：gh {' '.join(args)}\n{detail}")
    return result


def gh_api(
    gh: str,
    endpoint: str,
    method: str | None = None,
    payload: dict | None = None,
    check: bool = True,
) -> dict:
    args = ["api"]
    if method:
        args.extend(["-X", method])
    args.append(endpoint)
    input_text = None
    if payload is not None:
        args.extend(["--input", "-"])
        input_text = json.dumps(payload, ensure_ascii=False)
    result = subprocess.run(
        [gh, *args],
        input=input_text,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise SystemExit(f"GitHub API 调用失败：{endpoint}\n{detail}")
    if result.returncode != 0:
        return {}
    text = result.stdout.strip()
    if not text:
        if check:
            raise SystemExit(f"GitHub API 返回空响应：{endpoint}")
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        if check:
            raise SystemExit(f"GitHub API 返回无效 JSON：{endpoint}\n{text[:400]}") from exc
        return {}


def ensure_repo(gh: str, repo: str) -> str:
    if "/" in repo:
        target = repo
    else:
        probe = run_gh(gh, "repo", "view", repo, "--json", "nameWithOwner", "--jq", ".nameWithOwner", check=False)
        if probe.returncode == 0:
            return probe.stdout.strip()
        user = run_gh(gh, "api", "user", "--jq", ".login").stdout.strip()
        target = f"{user}/{repo}"

    probe = run_gh(gh, "repo", "view", target, "--json", "nameWithOwner", "--jq", ".nameWithOwner", check=False)
    if probe.returncode == 0:
        return probe.stdout.strip()

    created = run_gh(
        gh, "repo", "create", target, "--public",
        "--description", "Darkitten Launcher 下载站与完整源码",
        check=False,
    )
    if created.returncode != 0:
        detail = (created.stderr or created.stdout).strip()
        raise SystemExit(f"创建 GitHub 仓库失败：{detail}")
    return target


def repo_is_empty(gh: str, full: str) -> bool:
    return not bool(gh_api(gh, f"repos/{full}/git/refs", check=False))


def upload_tree(
    gh: str,
    full: str,
    branch: str,
    files: list[tuple[str, Path]],
    message: str,
) -> None:
    if repo_is_empty(gh, full):
        # Git Data API 在完全空的仓库上会返回 409，先用 Contents API 建立首个提交。
        gh_api(
            gh, f"repos/{full}/contents/.nojekyll", "PUT",
            {
                "message": "chore: initialize repository",
                "content": base64.b64encode(b"\n").decode("ascii"),
            },
        )

    ref = gh_api(gh, f"repos/{full}/git/ref/heads/{branch}", check=False)
    parent = str(ref.get("object", {}).get("sha") or "") or None

    entries: list[dict] = []
    for relative, path in files:
        content = base64.b64encode(path.read_bytes()).decode("ascii")
        blob: dict = {}
        for attempt in range(3):
            try:
                blob = gh_api(
                    gh, f"repos/{full}/git/blobs", "POST",
                    {"content": content, "encoding": "base64"},
                )
                break
            except SystemExit:
                if attempt == 2:
                    raise
                time.sleep(2)
        entries.append(
            {"path": relative, "mode": "100644", "type": "blob", "sha": blob["sha"]}
        )

    # 不传 base_tree，整棵树按本次文件列表重建，旧版本文件不会残留。
    tree = gh_api(gh, f"repos/{full}/git/trees", "POST", {"tree": entries})
    commit = gh_api(
        gh, f"repos/{full}/git/commits", "POST",
        {
            "message": message,
            "tree": tree["sha"],
            "parents": [parent] if parent else [],
        },
    )
    if parent:
        gh_api(
            gh, f"repos/{full}/git/refs/heads/{branch}", "PATCH",
            {"sha": commit["sha"], "force": False},
        )
    else:
        gh_api(
            gh, f"repos/{full}/git/refs", "POST",
            {"ref": f"refs/heads/{branch}", "sha": commit["sha"]},
        )


def deploy_github(version: str, repo: str, source: Path) -> str:
    gh = find_gh()
    auth = run_gh(gh, "auth", "status", check=False)
    if auth.returncode != 0:
        raise SystemExit("GitHub CLI 尚未登录，请先运行 gh auth login 完成授权。")

    full = ensure_repo(gh, repo)
    (PUBLIC / ".nojekyll").write_text("", encoding="utf-8")

    public_files = [
        (path.relative_to(PUBLIC).as_posix(), path)
        for path in sorted(PUBLIC.rglob("*"))
        if path.is_file() and ".git" not in path.relative_to(PUBLIC).parts
    ]
    upload_tree(gh, full, "gh-pages", public_files, f"deploy: Darkitten Launcher {version}")

    source_repo_files = [
        (relative.as_posix(), source / relative) for relative in source_files(source)
    ]
    source_repo_files.extend(
        (f"website/{relative.as_posix()}", SITE / relative)
        for relative in website_files()
    )
    upload_tree(gh, full, "main", source_repo_files, f"source: Darkitten Launcher {version}")

    pages_payload = {"source": {"branch": "gh-pages", "path": "/"}}
    pages = gh_api(gh, f"repos/{full}/pages", check=False)
    if not pages:
        # 仓库首次推送 gh-pages 时 GitHub 可能已经自动启用 Pages，409 视为成功。
        gh_api(gh, f"repos/{full}/pages", "POST", pages_payload, check=False)
    gh_api(gh, f"repos/{full}/pages", "PUT", pages_payload, check=False)

    info = gh_api(gh, f"repos/{full}/pages", check=False)
    owner, name = full.split("/", 1)
    return str(info.get("html_url") or f"https://{owner.lower()}.github.io/{name}/")


def build(source: Path, version: str, deploy: bool, repo: str) -> int:
    notes_map = json.loads((SITE / "release_notes.json").read_text(encoding="utf-8"))
    if version not in notes_map:
        raise SystemExit(f"release_notes.json 缺少 {version} 的更新说明，请先补充。")

    sync_site_version(version)
    source_entries: list[tuple[Path, str]] = []
    root = f"DarkittenLauncher-{version}-source"
    for relative in source_files(source):
        source_entries.append((source / relative, f"{root}/{relative.as_posix()}"))

    for relative in website_files():
        arc = Path("website") / relative
        source_entries.append((SITE / relative, f"{root}/{arc.as_posix()}"))

    update_entries: list[tuple[Path, str]] = []
    update_root = f"DKL-{version}"
    for name in ("app.py", "requirements.txt", "launch.bat", "启动.bat"):
        path = source / name
        if path.is_file():
            update_entries.append((path, f"{update_root}/{name}"))
    for name in ("stellar", "assets"):
        folder = source / name
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(source)
            if any(part == "__pycache__" for part in relative.parts):
                continue
            if path.suffix.lower() in (".pyc", ".pyo", ".log"):
                continue
            update_entries.append((path, f"{update_root}/{relative.as_posix()}"))

    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    source_zip = DOWNLOADS / f"DarkittenLauncher-{version}-source.zip"
    update_zip = DOWNLOADS / f"DKL-{version}-update.zip"
    source_txt = DOWNLOADS / f"DarkittenLauncher-{version}-source.txt"
    write_zip(source_zip, source_entries)
    write_zip(update_zip, update_entries)
    write_source_txt(source_txt, source, version)

    manifest = {
        "version": version,
        "released": date.today().isoformat(),
        "notes": notes_map[version],
        "download_url": f"downloads/{update_zip.name}",
        "page_url": "index.html",
        "source_url": f"downloads/{source_zip.name}",
        "source_txt_url": f"downloads/{source_txt.name}",
        "sha256": sha256(update_zip),
        "size": update_zip.stat().st_size,
        "source_sha256": sha256(source_zip),
        "source_size": source_zip.stat().st_size,
        "source_txt_sha256": sha256(source_txt),
        "source_txt_size": source_txt.stat().st_size,
    }
    (PUBLIC / "update.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    DELIVERY.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_zip, DELIVERY / source_zip.name)
    shutil.copy2(source_txt, DELIVERY / source_txt.name)

    for archive_path in (source_zip, update_zip):
        with zipfile.ZipFile(archive_path) as archive:
            bad = archive.testzip()
            if bad:
                raise SystemExit(f"{archive_path.name} 校验失败：{bad}")

    print("version:", version)
    print("source zip:", len(source_entries), "entries,", source_zip.stat().st_size, "bytes")
    print("update zip:", len(update_entries), "entries,", update_zip.stat().st_size, "bytes")
    print("source txt:", len(text_entries(source)), "files,", source_txt.stat().st_size, "bytes")
    print("update sha256:", manifest["sha256"])
    print("source sha256:", manifest["source_sha256"])
    print("source txt sha256:", manifest["source_txt_sha256"])

    if deploy:
        pages_url = deploy_github(version, repo, source)
        print("pages url:", pages_url)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="构建并发布 Darkitten Launcher")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="启动器源码目录")
    parser.add_argument("--version", default="", help="默认读取 stellar/config.py")
    parser.add_argument("--deploy", action="store_true", help="构建后部署到 GitHub Pages")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="GitHub 仓库名或 owner/repo")
    args = parser.parse_args()
    source = args.source.resolve()
    version = args.version or read_version(source)
    return build(source, version, args.deploy, args.repo)


if __name__ == "__main__":
    raise SystemExit(main())
