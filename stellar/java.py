from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000


@dataclass
class JavaInfo:
    path: str
    version: str = ""
    major: int = 0
    arch: str = ""
    vendor: str = ""

    @property
    def display(self) -> str:
        bits = f" · {self.arch}" if self.arch else ""
        ver = self.version or "未知版本"
        return f"Java {ver} ({Path(self.path).parent.parent.name}){bits}"


def _java_roots() -> list[Path]:
    roots: list[Path] = []
    env_home = os.environ.get("JAVA_HOME")
    if env_home:
        roots.append(Path(env_home))

    candidates = [
        Path("C:/Program Files/Java"),
        Path("C:/Program Files/Eclipse Adoptium"),
        Path("C:/Program Files/Microsoft"),
        Path("C:/Program Files/BellSoft"),
        Path("C:/Program Files/Zulu"),
        Path("C:/Program Files (x86)/Java"),
        Path("D:/developer/Java"),
        Path.home() / ".jdks",
        Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Programs/Eclipse Adoptium",
        Path(os.environ.get("APPDATA", Path.home())) / ".minecraft/runtime",
    ]
    roots.extend(candidates)
    return roots


def _candidate_executables(extra: list[str] | None = None) -> list[Path]:
    found: list[Path] = []

    for raw in extra or []:
        if raw:
            p = Path(os.path.expandvars(raw)).expanduser()
            if p.name.lower() in ("java.exe", "java"):
                found.append(p)
            else:
                found.append(p / "bin/java.exe")

    path_java = shutil.which("java")
    if path_java:
        found.append(Path(path_java))

    for root in _java_roots():
        if not root.exists():
            continue
        try:
            direct = root / "bin/java.exe"
            if direct.exists():
                found.append(direct)
            for exe in root.glob("*/bin/java.exe"):
                found.append(exe)
            for exe in root.glob("*/*/bin/java.exe"):
                found.append(exe)
            if root.name == "runtime":
                for exe in root.glob("**/bin/java.exe"):
                    found.append(exe)
        except OSError:
            continue

    unique: list[Path] = []
    seen: set[str] = set()
    for path in found:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        key = str(resolved).lower()
        if resolved.exists() and key not in seen:
            seen.add(key)
            unique.append(resolved)
    return unique


def _probe_java(exe: Path) -> JavaInfo | None:
    try:
        proc = subprocess.run(
            [str(exe), "-XshowSettings:properties", "-version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=12,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception:
        return None

    text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    major = 0
    version = ""
    arch = ""
    vendor = ""

    m = re.search(r"java\.version\s*=\s*([^\s]+)", text)
    if m:
        version = m.group(1)
    else:
        m = re.search(r'version "([^"]+)"', text)
        if m:
            version = m.group(1)

    if version:
        vm = re.match(r"(?:1\.)?(\d+)", version)
        if vm:
            major = int(vm.group(1))

    m = re.search(r"os\.arch\s*=\s*([^\s]+)", text)
    if m:
        arch = m.group(1)
    m = re.search(r"java\.vendor\s*=\s*([^\n\r]+)", text)
    if m:
        vendor = m.group(1).strip()

    return JavaInfo(path=str(exe), version=version, major=major, arch=arch, vendor=vendor)


def probe_java(path: str) -> JavaInfo | None:
    exe = Path(os.path.expandvars(path)).expanduser()
    if exe.name.lower() != "java.exe":
        exe = exe / "bin/java.exe"
    if not exe.exists():
        return None
    return _probe_java(exe)


def detect_java_installs(preferred: str = "", max_workers: int = 8) -> list[JavaInfo]:
    exes = _candidate_executables([preferred] if preferred else [])
    if not exes:
        return []

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        infos = list(pool.map(_probe_java, exes))

    result = [info for info in infos if info is not None]
    result.sort(key=lambda x: (-x.major, x.path.lower()))
    return result


def pick_java(
    installs: list[JavaInfo], required_major: int, preferred: str = ""
) -> JavaInfo | None:
    if preferred:
        pref = str(Path(os.path.expandvars(preferred)).expanduser()).lower()
        for info in installs:
            if info.path.lower() == pref:
                return info
        p = Path(os.path.expandvars(preferred)).expanduser()
        if p.exists():
            probed = _probe_java(p)
            if probed:
                return probed

    required = max(8, int(required_major or 8))
    suitable = [i for i in installs if i.major >= required]
    if not suitable:
        return installs[0] if installs else None
    return min(suitable, key=lambda i: i.major)
