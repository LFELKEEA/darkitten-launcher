from __future__ import annotations

import json
import os
import platform
import re
import sys
import time
import zipfile
from pathlib import Path
from typing import Any, Iterable

from .config import APP_DIR, APP_NAME, APP_VERSION, LOG_FILE
from .sources import health_summary
from .versions import check_installation, resolve_version

MAX_LOG_BYTES = 512 * 1024
MAX_CRASH_REPORTS = 5


def redact(text: str) -> str:
    """移除常见令牌与密钥，诊断包可以安全地发给开发者。"""
    patterns = (
        (
            r"(?i)(access_token|refresh_token|id_token|client_secret|sakura_token)"
            r"([\"']?\s*[:=]\s*[\"']?)[^\s\"',}]+",
            r"\1\2<已隐藏>",
        ),
        (r"(?i)(authorization\s*:\s*bearer\s+)[^\s]+", r"\1<已隐藏>"),
        (r"(?i)(token=)[^\s&]+", r"\1<已隐藏>"),
    )
    result = text
    for pattern, replacement in patterns:
        result = re.sub(pattern, replacement, result)
    return result


def _read_tail(path: Path, limit: int = MAX_LOG_BYTES) -> str:
    try:
        size = path.stat().st_size
        with path.open("rb") as fp:
            if size > limit:
                fp.seek(-limit, os.SEEK_END)
            raw = fp.read()
    except OSError:
        return ""
    return redact(raw.decode("utf-8", errors="replace"))


def _system_info() -> dict[str, Any]:
    return {
        "app": APP_NAME,
        "version": APP_VERSION,
        "python": sys.version.split()[0],
        "frozen": bool(getattr(sys, "frozen", False)),
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cwd": os.getcwd(),
        "app_dir": str(APP_DIR),
    }


def _java_info(java_installs: Iterable[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in java_installs or []:
        result.append(
            {
                "path": str(getattr(item, "path", "")),
                "version": str(getattr(item, "version", "")),
                "major": int(getattr(item, "major", 0) or 0),
                "arch": str(getattr(item, "arch", "")),
                "vendor": str(getattr(item, "vendor", "")),
            }
        )
    return result


def _mods_list(config, version_id: str) -> list[dict[str, Any]]:
    if not version_id:
        return []
    try:
        from .mods import list_mods

        mods = list_mods(config.mods_path(version_id))
    except Exception:
        return []
    result: list[dict[str, Any]] = []
    for mod in mods:
        result.append(
            {
                "file": mod.filename,
                "name": mod.name,
                "version": mod.version,
                "loader": mod.loader,
                "enabled": bool(mod.enabled),
            }
        )
    return result


def _install_problems(config, version_id: str) -> list[str]:
    if not version_id:
        return []
    try:
        resolved = resolve_version(config.game_path, version_id)
        return list(check_installation(config.game_path, resolved))
    except Exception as exc:
        return [f"检查失败：{exc}"]


def build_summary(
    config,
    java_installs: Iterable[Any] = (),
    selected_version: str = "",
    loader_id: str = "",
) -> tuple[str, dict[str, Any]]:
    version_id = selected_version or config.last_version
    game_path = config.version_game_path(version_id) if version_id else config.game_path
    info: dict[str, Any] = {
        "app": APP_NAME,
        "version": APP_VERSION,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "system": _system_info(),
        "game_dir": str(config.game_path),
        "version_game_dir": str(game_path),
        "selected_version": version_id,
        "loader": loader_id,
        "memory_mb": config.memory_mb,
        "jvm_args": config.jvm_args,
        "version_isolation": bool(config.version_isolation),
        "download_source": config.download_source,
        "download_threads": config.download_threads,
        "theme": config.theme,
        "update_url": config.update_url,
        "java": _java_info(java_installs),
        "source_health": health_summary(),
        "mods": _mods_list(config, version_id),
        "install_problems": _install_problems(config, version_id),
    }
    lines = [
        f"{APP_NAME} 诊断信息",
        f"生成时间：{info['generated_at']}",
        f"启动器版本：{APP_VERSION}",
        f"Python：{info['system']['python']}",
        f"系统：{info['system']['platform']}",
        f"运行目录：{APP_DIR}",
        f"游戏目录：{config.game_path}",
        f"当前版本：{version_id or '未选择'}",
        f"加载器：{loader_id or '原版'}",
        f"内存：{config.memory_mb} MB",
        f"下载源：{config.download_source}（{info['source_health']}）",
        f"版本隔离：{'开启' if config.version_isolation else '关闭'}",
        "",
        "Java：",
    ]
    if info["java"]:
        for item in info["java"]:
            lines.append(
                f"  - Java {item['version']} ({item['arch']}) · {item['path']}"
            )
    else:
        lines.append("  - 未检测到")
    lines.extend(["", f"Mod 数量：{len(info['mods'])}"])
    for item in info["mods"]:
        flag = "启用" if item["enabled"] else "禁用"
        lines.append(f"  - {item['file']} · {item['name']} {item['version']} · {flag}")
    lines.extend(["", "文件检查："])
    if info["install_problems"]:
        lines.extend(f"  - {item}" for item in info["install_problems"])
    else:
        lines.append("  - 未发现缺失文件")
    lines.extend(
        [
            "",
            "隐私说明：诊断包已隐藏访问令牌、刷新令牌、樱花 Token 等敏感信息；",
            "不包含账户密码、皮肤文件、世界存档和 Mod 本体。",
        ]
    )
    return "\n".join(lines) + "\n", info


def _crash_reports(game_path: Path) -> list[Path]:
    folder = game_path / "crash-reports"
    if not folder.is_dir():
        return []
    files = [path for path in folder.glob("*.txt") if path.is_file()]
    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return files[:MAX_CRASH_REPORTS]


def create_bundle(
    config,
    java_installs: Iterable[Any] = (),
    selected_version: str = "",
    loader_id: str = "",
) -> tuple[Path, str]:
    """生成诊断 ZIP，返回（压缩包路径，摘要文本）。"""
    summary, info = build_summary(
        config, java_installs, selected_version, loader_id
    )
    out_dir = APP_DIR / "diagnostics"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"dkl-diagnostics-{stamp}.zip"
    index = 1
    while path.exists():
        path = out_dir / f"dkl-diagnostics-{stamp}-{index}.zip"
        index += 1

    version_id = info["selected_version"]
    game_path = Path(info["version_game_dir"])
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("summary.txt", summary)
        archive.writestr(
            "system.json",
            json.dumps(info, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        if LOG_FILE.exists():
            archive.writestr("launcher.log", _read_tail(LOG_FILE))
        latest_log = game_path / "logs" / "latest.log"
        if latest_log.exists():
            archive.writestr("game-latest.log", _read_tail(latest_log))
        crash_lines: list[str] = []
        for crash in _crash_reports(game_path):
            text = _read_tail(crash, 256 * 1024)
            archive.writestr(f"crash-reports/{crash.name}", text)
            crash_lines.append(f"{crash.name} · {crash.stat().st_size} 字节")
        if crash_lines:
            archive.writestr(
                "crash-reports/index.txt", "\n".join(crash_lines) + "\n"
            )
        archive.writestr(
            "config-safe.json",
            json.dumps(
                {
                    "memory_mb": config.memory_mb,
                    "jvm_args": redact(config.jvm_args),
                    "download_source": config.download_source,
                    "download_threads": config.download_threads,
                    "version_isolation": config.version_isolation,
                    "last_version": config.last_version,
                    "version_loaders": config.version_loaders,
                    "update_url": config.update_url,
                    "theme": config.theme,
                },
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8"),
        )
        if version_id:
            archive.writestr("version.txt", f"{version_id}\n{loader_id}\n")
    return path, summary
