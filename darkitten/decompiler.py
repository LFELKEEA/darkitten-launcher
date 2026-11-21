"""把 JAR 反编译成可浏览的源码目录，用于模组学习与排错。

只依赖 JDK 和 Vineflower（下载到 cache 目录，不随程序分发）。
所有输出都写在用户指定的目录里，不会修改原始 JAR。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Callable

from .config import APP_DIR
from .downloader import download_to_file
from . import java as java_mod

ProgressCallback = Callable[[int, str], None]

VINEFLOWER_VERSION = "1.11.1"
VINEFLOWER_URL = (
    "https://repo1.maven.org/maven2/org/vineflower/vineflower/"
    f"{VINEFLOWER_VERSION}/vineflower-{VINEFLOWER_VERSION}.jar"
)
VINEFLOWER_SHA1 = ""
MAX_JAR_BYTES = 512 * 1024 * 1024
MAX_NESTED_JARS = 24
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

CLASS_ENTRY_RE = re.compile(r"^[\w/$-]+\.class$")
SAFE_NAME_RE = re.compile(r"[^\w.\-]+", re.UNICODE)


class DecompileError(RuntimeError):
    pass


def _progress(callback: ProgressCallback | None, percent: int, message: str) -> None:
    if callback is None:
        return
    try:
        callback(max(0, min(100, int(percent))), str(message))
    except Exception:  # noqa: BLE001 - 进度回调不应该影响主流程
        pass


def cache_dir() -> Path:
    path = APP_DIR / "cache" / "vineflower"
    path.mkdir(parents=True, exist_ok=True)
    return path


def vineflower_jar() -> Path:
    return cache_dir() / f"vineflower-{VINEFLOWER_VERSION}.jar"


def ensure_vineflower(
    progress: ProgressCallback | None = None,
    cancel: object | None = None,
) -> Path:
    target = vineflower_jar()
    if target.is_file() and target.stat().st_size > 1024 * 1024:
        return target
    _progress(progress, 2, "正在下载反编译器 Vineflower…")
    temp = target.with_name(target.name + ".part")
    try:
        download_to_file(
            VINEFLOWER_URL,
            temp,
            cancel_event=cancel,  # type: ignore[arg-type]
            connections=4,
        )
    except Exception as exc:  # noqa: BLE001 - 统一成可读错误
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise DecompileError(f"反编译器下载失败：{exc}") from exc
    temp.replace(target)
    _progress(progress, 8, "反编译器已就绪")
    return target


def java_executable(config=None) -> str:
    """挑一个能用的 Java：配置优先，其次 JAVA_HOME，最后自动探测。"""
    preferred = str(getattr(config, "java_path", "") or "").strip()
    candidates: list[str] = []
    if preferred:
        candidates.append(preferred)
        candidates.append(str(Path(preferred) / "bin" / "java.exe"))
    env_home = os.environ.get("JAVA_HOME", "").strip()
    if env_home:
        candidates.append(str(Path(env_home) / "bin" / "java.exe"))
        candidates.append(str(Path(env_home) / "bin" / "java"))
    which = shutil.which("java")
    if which:
        candidates.append(which)
    for item in candidates:
        if item and Path(item).is_file():
            return str(Path(item))
    try:
        installs = java_mod.detect_java_installs(preferred, max_workers=4)
    except Exception:  # noqa: BLE001
        installs = []
    usable = [item for item in installs if getattr(item, "major", 0) >= 17]
    if not usable:
        usable = list(installs)
    if usable:
        return max(usable, key=lambda item: getattr(item, "major", 0)).path
    raise DecompileError("没有找到可用的 Java。请先在启动器设置里选择 JDK，或安装 JDK 17+。")


def safe_name(value: str, fallback: str = "mod") -> str:
    text = SAFE_NAME_RE.sub("-", str(value or "").strip()).strip(".-_")
    return text or fallback


def _class_count(jar: Path) -> int:
    try:
        with zipfile.ZipFile(jar) as archive:
            return sum(1 for name in archive.namelist() if CLASS_ENTRY_RE.match(name))
    except (OSError, zipfile.BadZipFile):
        return 0


def _nested_jars(jar: Path) -> list[tuple[str, bytes]]:
    """提取 META-INF/jarjar 等位置的嵌套 JAR，用于一起反编译。"""
    result: list[tuple[str, bytes]] = []
    try:
        with zipfile.ZipFile(jar) as archive:
            for name in archive.namelist():
                lower = name.lower()
                if not lower.endswith(".jar"):
                    continue
                if not (lower.startswith("meta-inf/jarjar/") or lower.startswith("meta-inf/jars/")):
                    continue
                try:
                    data = archive.read(name)
                except Exception:  # noqa: BLE001
                    continue
                if len(data) <= MAX_JAR_BYTES:
                    result.append((name, data))
                if len(result) >= MAX_NESTED_JARS:
                    break
    except (OSError, zipfile.BadZipFile):
        return []
    return result


def _run_vineflower(
    java: str,
    vineflower: Path,
    jar: Path,
    out_dir: Path,
    *,
    log_path: Path,
    timeout: int = 1800,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    command = [
        java,
        "-Xmx1g",
        "-jar",
        str(vineflower),
        "-dgs=1",
        "-hdc=0",
        "-rsy=1",
        "-asc=1",
        "-jrt=1",
        "-log=WARN",
        str(jar),
        str(out_dir),
    ]
    try:
        with log_path.open("w", encoding="utf-8", errors="replace") as log:
            result = subprocess.run(
                command,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                creationflags=CREATE_NO_WINDOW,
            )
    except subprocess.TimeoutExpired as exc:
        raise DecompileError("反编译超时（超过 30 分钟），已停止。") from exc
    except OSError as exc:
        raise DecompileError(f"无法启动 Java：{exc}") from exc
    if result.returncode != 0 and not any(out_dir.rglob("*.java")):
        detail = ""
        try:
            detail = log_path.read_text(encoding="utf-8", errors="replace")[-800:]
        except OSError:
            pass
        raise DecompileError(f"反编译失败：{detail.strip() or '未知错误'}")


def decompile_jar(
    jar: str | Path,
    out_dir: str | Path,
    *,
    config=None,
    progress: ProgressCallback | None = None,
    timeout: int = 1800,
) -> dict:
    """反编译 JAR，返回源码目录、类数量和索引文件。"""
    source = Path(str(jar)).expanduser()
    if not source.is_file():
        raise DecompileError(f"JAR 不存在：{source}")
    if source.stat().st_size > MAX_JAR_BYTES:
        raise DecompileError("JAR 超过 512MB，无法在这个工具里反编译。")
    target = Path(str(out_dir)).expanduser()
    target.mkdir(parents=True, exist_ok=True)

    vineflower = ensure_vineflower(progress)
    java = java_executable(config)

    _progress(progress, 12, f"正在反编译 {source.name}…")
    log_path = target / "_decompile.log"
    nested = _nested_jars(source)
    _run_vineflower(java, vineflower, source, target, log_path=log_path, timeout=timeout)

    nested_info: list[dict] = []
    for index, (name, data) in enumerate(nested, start=1):
        label = safe_name(Path(name).stem, f"nested-{index}")
        sub = target / "_nested" / label
        temp_jar = sub / f"{label}.jar"
        sub.mkdir(parents=True, exist_ok=True)
        temp_jar.write_bytes(data)
        _progress(
            progress,
            12 + int(70 * index / max(1, len(nested))),
            f"正在反编译嵌套包 {index}/{len(nested)}：{Path(name).name}",
        )
        try:
            _run_vineflower(
                java,
                vineflower,
                temp_jar,
                sub / "src",
                log_path=sub / "_decompile.log",
                timeout=timeout,
            )
            nested_info.append({"name": name, "dir": str(sub / "src")})
        except DecompileError as exc:
            nested_info.append({"name": name, "dir": "", "error": str(exc)})

    sources = sorted(target.rglob("*.java"))
    classes = _class_count(source)
    index = target / "INDEX.md"
    lines = [
        f"# {source.name} 反编译索引",
        "",
        f"- 原始 JAR：`{source}`",
        f"- Java 类文件：{classes} 个",
        f"- 生成源码：{len(sources)} 个 .java",
        "- 反编译器：Vineflower " + VINEFLOWER_VERSION,
        "",
        "> 反编译源码仅用于本地学习与排错，请遵守原模组许可证，不要重新分发。",
        "",
    ]
    if nested_info:
        lines.append("## 嵌套 JAR")
        for item in nested_info:
            if item.get("dir"):
                lines.append(f"- `{item['name']}` → `{item['dir']}`")
            else:
                lines.append(f"- `{item['name']}` 失败：{item.get('error') or '未知错误'}")
        lines.append("")
    lines.append("## 源码目录")
    for item in sources[:400]:
        lines.append(f"- `{item.relative_to(target).as_posix()}`")
    if len(sources) > 400:
        lines.append(f"- ……另有 {len(sources) - 400} 个文件")
    index.write_text("\n".join(lines), encoding="utf-8")
    _progress(progress, 100, f"反编译完成：{len(sources)} 个源码文件")
    return {
        "jar": str(source),
        "source_dir": str(target),
        "index": str(index),
        "classes": classes,
        "files": len(sources),
        "nested": nested_info,
    }


def write_learn_index(root: Path, entry: dict) -> Path:
    """维护学习库索引，启动器桥和界面都从这里读取历史记录。"""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "index.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = []
    if not isinstance(data, list):
        data = []
    entry = dict(entry)
    entry["updated"] = entry.get("updated") or ""
    key = (entry.get("project") or entry.get("jar") or "").lower()
    data = [item for item in data if str(item.get("project") or item.get("jar") or "").lower() != key]
    data.insert(0, entry)
    path.write_text(json.dumps(data[:200], ensure_ascii=False, indent=2), encoding="utf-8")
    return path