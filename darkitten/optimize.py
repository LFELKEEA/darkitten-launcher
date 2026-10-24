from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from . import modrinth
from .game_context import (
    LOADER_NAMES,
    LoaderContext,
    all_mod_files,
    enabled_mod_files,
    file_sha1,
    filename_matches,
    installed_meta_keys,
    loader_candidates,
    normalize_token,
    resolve_context,
)

ProgressCallback = Callable[[int, str], None]
OptimizeProgress = Callable[[int, str], None]

STATE_DIR_NAME = ".dkl-optimize"
STATE_FILE_NAME = "state.json"
OPTIONS_BACKUP_NAME = "options-backup.txt"
IRIS_BACKUP_NAME = "iris-backup.properties"


class OptimizeError(Exception):
    pass


# ---------------------------------------------------------------------------
# 硬件探测与推荐


@dataclass(frozen=True)
class GpuInfo:
    name: str
    vram_mb: int = 0
    kind: str = "other"

    @property
    def label(self) -> str:
        memory = f" · {self.vram_mb // 1024} GB" if self.vram_mb >= 1024 else ""
        return f"{self.name}{memory}"


_TOTAL_RAM: int | None = None
_GPU_CACHE: list[GpuInfo] | None = None
_SYSTEM_CACHE: dict[str, Any] = {}


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def system_memory_mb() -> int:
    """读取物理内存；失败时退回 8 GB，保证推荐值永远可用。"""
    global _TOTAL_RAM
    if _TOTAL_RAM is not None:
        return _TOTAL_RAM
    total = 0
    try:
        status = _MemoryStatusEx()
        status.dwLength = ctypes.sizeof(_MemoryStatusEx)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            total = int(status.ullTotalPhys // (1024 * 1024))
    except Exception:
        total = 0
    if not total:
        try:
            total = int(os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") // (1024 * 1024))
        except Exception:
            total = 0
    _TOTAL_RAM = total if total > 0 else 8192
    return _TOTAL_RAM


def _gpu_kind(name: str) -> str:
    low = name.lower()
    if "nvidia" in low or "geforce" in low or "rtx" in low or "gtx" in low:
        return "nvidia"
    if "amd" in low or "radeon" in low or "rx " in low:
        return "amd"
    if "intel" in low or "arc" in low or "iris xe" in low or "uhd" in low:
        return "intel"
    return "other"


def detect_gpus(refresh: bool = False) -> list[GpuInfo]:
    """通过 PowerShell 读取显卡名称与显存；结果在进程内缓存。"""
    global _GPU_CACHE
    if _GPU_CACHE is not None and not refresh:
        return _GPU_CACHE
    if sys.platform != "win32":
        _GPU_CACHE = []
        return _GPU_CACHE
    command = (
        "Get-CimInstance Win32_VideoController | "
        "Select-Object Name,AdapterRAM | ConvertTo-Json -Compress"
    )
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            timeout=12,
            creationflags=flags,
            check=False,
        )
        raw = completed.stdout.decode("utf-8", errors="ignore").strip()
        if not raw:
            raw = completed.stdout.decode("gbk", errors="ignore").strip()
        data = json.loads(raw) if raw else []
    except Exception:
        data = []
    if isinstance(data, dict):
        data = [data]
    gpus: list[GpuInfo] = []
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            name = str(item.get("Name") or "").strip()
            if not name:
                continue
            try:
                vram = max(0, int(item.get("AdapterRAM") or 0) // (1024 * 1024))
            except Exception:
                vram = 0
            gpus.append(GpuInfo(name=name, vram_mb=vram, kind=_gpu_kind(name)))
    _GPU_CACHE = gpus
    return gpus


def recommend_memory_mb(
    total_ram_mb: int, mod_count: int = 0, loader: str = ""
) -> int:
    total = max(2048, int(total_ram_mb or 8192))
    if total <= 4096:
        budget = max(1536, int(total * 0.55))
    elif total <= 8192:
        budget = int(total * 0.50)
    elif total <= 16384:
        budget = int(total * 0.45)
    else:
        budget = int(total * 0.40)
    base = 3072 if loader else 2048
    mods_bonus = min(4096, (max(0, int(mod_count)) // 20) * 512) if loader else 0
    target = max(base + mods_bonus, base)
    target = min(target, budget, 16384)
    target = int(round(target / 512.0) * 512)
    return max(1024, min(target, total - 1024))


def recommended_jvm_args() -> str:
    """Aikar 风格的 G1 参数，适合现代 Minecraft 服务端/客户端。"""
    return (
        "-XX:+UseG1GC -XX:+ParallelRefProcEnabled -XX:MaxGCPauseMillis=200 "
        "-XX:+UnlockExperimentalVMOptions -XX:+DisableExplicitGC "
        "-XX:+AlwaysPreTouch -XX:G1NewSizePercent=30 "
        "-XX:G1MaxNewSizePercent=40 -XX:G1HeapRegionSize=8M "
        "-XX:G1ReservePercent=20 -XX:G1HeapWastePercent=5 "
        "-XX:G1MixedGCCountTarget=4 -XX:InitiatingHeapOccupancyPercent=15 "
        "-XX:G1MixedGCLiveThresholdPercent=90 "
        "-XX:G1RSetUpdatingPauseTimePercent=5 -XX:SurvivorRatio=32 "
        "-XX:+PerfDisableSharedMem -XX:MaxTenuringThreshold=1"
    )


def suggest_graphics_profile(gpus: Iterable[GpuInfo], total_ram_mb: int = 0) -> str:
    items = list(gpus or [])
    names = " ".join(item.name.lower() for item in items)
    if not items:
        return "balanced"
    if any(keyword in names for keyword in ("rtx", "rx 6", "rx 7", "rx 9")):
        return "quality"
    if any(
        keyword in names
        for keyword in ("gtx 16", "gtx 10", "rx 5", "arc a", "rtx")
    ):
        return "quality"
    if any(item.kind in ("nvidia", "amd") for item in items):
        return "balanced"
    if "intel" in names:
        return "performance"
    if total_ram_mb and total_ram_mb <= 8192:
        return "performance"
    return "balanced"


# ---------------------------------------------------------------------------
# 优化 Mod 目录


@dataclass(frozen=True)
class OptimizationMod:
    slug: str
    title: str
    note: str
    group: str = ""
    tokens: tuple[str, ...] = ()

    @property
    def is_renderer(self) -> bool:
        return self.group == "renderer"


_FABRIC_MODS = (
    OptimizationMod(
        "sodium",
        "Sodium",
        "重写渲染管线，大幅提升帧率，是 Fabric 端最核心的优化 Mod。",
        group="renderer",
        tokens=("sodium",),
    ),
    OptimizationMod("lithium", "Lithium", "优化服务端与客户端逻辑刻，减少卡顿。", tokens=("lithium",)),
    OptimizationMod(
        "ferrite-core",
        "FerriteCore",
        "降低模型与方块状态的内存占用，适合大型整合包。",
        tokens=("ferrite",),
    ),
    OptimizationMod(
        "modernfix",
        "ModernFix",
        "缩短启动时间并修复部分内存泄漏，兼容性较好。",
        tokens=("modernfix",),
    ),
    OptimizationMod(
        "immediatelyfast",
        "ImmediatelyFast",
        "优化界面、实体与粒子渲染的提交路径，提升低配机帧率。",
        tokens=("immediatelyfast",),
    ),
    OptimizationMod(
        "entityculling",
        "Entity Culling",
        "跳过视野外实体的渲染计算，大型场景收益明显。",
        tokens=("entityculling", "entitycull"),
    ),
)

_FORGE_MODS = (
    OptimizationMod(
        "embeddium",
        "Embeddium",
        "Forge / NeoForge 端的 Sodium 移植，重写渲染管线提升帧率。",
        group="renderer",
        tokens=("embeddium",),
    ),
    OptimizationMod(
        "ferrite-core",
        "FerriteCore",
        "降低模型与方块状态的内存占用。",
        tokens=("ferrite",),
    ),
    OptimizationMod(
        "modernfix",
        "ModernFix",
        "加快启动并处理部分内存泄漏。",
        tokens=("modernfix",),
    ),
    OptimizationMod(
        "immediatelyfast",
        "ImmediatelyFast",
        "优化界面与实体渲染提交路径。",
        tokens=("immediatelyfast",),
    ),
    OptimizationMod(
        "entityculling",
        "Entity Culling",
        "跳过视野外实体的渲染计算。",
        tokens=("entityculling", "entitycull"),
    ),
    OptimizationMod(
        "canary",
        "Canary",
        "Forge / NeoForge 端的逻辑刻优化，减少不必要的方块与实体更新。",
        tokens=("canary",),
    ),
)

CATALOG: dict[str, tuple[OptimizationMod, ...]] = {
    "fabric": _FABRIC_MODS,
    "quilt": _FABRIC_MODS,
    "neoforge": _FORGE_MODS,
    "forge": _FORGE_MODS,
    "": (),
}


def catalog_for(loader: str) -> tuple[OptimizationMod, ...]:
    from .game_context import loader_key

    return CATALOG.get(loader_key(loader), ())


def is_optifine_installed(mods_dir: Path) -> bool:
    for path in all_mod_files(mods_dir):
        if "optifine" in normalize_token(path.name):
            return True
    return False


def _present_mods(
    mods_dir: Path,
    catalog: Iterable[OptimizationMod],
) -> tuple[list[OptimizationMod], list[Path]]:
    files = all_mod_files(mods_dir)
    keys = installed_meta_keys(mods_dir)
    present: list[OptimizationMod] = []
    for mod in catalog:
        hit = any(key in keys for key in (mod.slug.lower(),))
        if not hit:
            tokens = mod.tokens or (mod.slug,)
            hit = any(filename_matches(path.name, *tokens) for path in files)
        if hit:
            present.append(mod)
    return present, files


# ---------------------------------------------------------------------------
# 优化计划与执行


@dataclass
class OptimizationPlan:
    context: LoaderContext
    total_ram_mb: int
    gpus: list[GpuInfo]
    mod_count: int
    optifine: bool
    present: list[OptimizationMod]
    missing: list[OptimizationMod]
    warnings: list[str]
    current_memory_mb: int
    recommended_memory_mb: int
    current_jvm_args: str
    recommended_jvm_args: str

    @property
    def loader_label(self) -> str:
        return self.context.loader_label

    @property
    def mc_version(self) -> str:
        return self.context.mc_version

    @property
    def game_dir(self) -> Path:
        return self.context.game_dir

    @property
    def mods_dir(self) -> Path:
        return self.context.mods_dir

    @property
    def has_loader(self) -> bool:
        return self.context.has_loader


@dataclass
class InstalledOptimization:
    kind: str
    slug: str
    title: str
    filename: str
    sha1: str = ""
    version_id: str = ""
    project_id: str = ""


@dataclass
class OptimizationResult:
    memory_mb: int = 0
    jvm_args: str = ""
    installed: list[InstalledOptimization] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False


def build_optimization_plan(
    version_id: str,
    config,
    versions: Iterable,
    *,
    total_ram_mb: int | None = None,
    gpus: list[GpuInfo] | None = None,
) -> OptimizationPlan:
    context = resolve_context(version_id, versions, config)
    mods = enabled_mod_files(context.mods_dir)
    optifine = is_optifine_installed(context.mods_dir)
    catalog = catalog_for(context.loader)
    present, _files = _present_mods(context.mods_dir, catalog)

    warnings: list[str] = []
    if not context.has_loader:
        warnings.append("原版客户端没有可安装的优化 Mod，只会调整内存与 JVM 参数。")
    elif not catalog:
        warnings.append(f"{context.loader_label} 暂未提供自动优化 Mod。")

    missing: list[OptimizationMod] = []
    for mod in catalog:
        if mod in present:
            continue
        if mod.is_renderer and optifine:
            warnings.append("检测到 OptiFine，已跳过 Sodium/Embeddium 渲染优化，避免冲突。")
            continue
        missing.append(mod)

    if optifine:
        warnings.append("OptiFine 与部分优化 Mod 可能冲突；启动器不会卸载或改动 OptiFine。")
    if context.loader_key == "forge" and missing:
        warnings.append("Forge 的优化 Mod 适配版本较少，安装时会自动跳过不兼容项。")

    total = int(total_ram_mb or system_memory_mb())
    gpu_list = list(gpus if gpus is not None else detect_gpus())
    current_memory = int(config.memory_for(version_id))
    return OptimizationPlan(
        context=context,
        total_ram_mb=total,
        gpus=gpu_list,
        mod_count=len(mods),
        optifine=optifine,
        present=present,
        missing=missing,
        warnings=_dedupe(warnings),
        current_memory_mb=current_memory,
        recommended_memory_mb=recommend_memory_mb(
            total, len(mods), context.loader
        ),
        current_jvm_args=str(config.jvm_args_for(version_id) or ""),
        recommended_jvm_args=recommended_jvm_args(),
    )


def _dedupe(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))


def _state_dir(game_dir: Path) -> Path:
    return Path(game_dir) / STATE_DIR_NAME


def _state_path(game_dir: Path) -> Path:
    return _state_dir(game_dir) / STATE_FILE_NAME


def load_state(game_dir: Path) -> dict[str, Any]:
    try:
        data = json.loads(_state_path(game_dir).read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(game_dir: Path, state: dict[str, Any]) -> None:
    directory = _state_dir(game_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / STATE_FILE_NAME
    temp = target.with_name(target.name + ".tmp")
    temp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temp.replace(target)


def _ensure_state(game_dir: Path, version_id: str) -> dict[str, Any]:
    state = load_state(game_dir)
    if state.get("active"):
        return state
    return {
        "version": 1,
        "active": True,
        "version_id": version_id,
        "created_at": time.time(),
        "installed": [],
    }


def _snapshot_settings(
    state: dict[str, Any], config, version_id: str
) -> None:
    if isinstance(state.get("settings"), dict):
        return
    memory_exists = version_id in config.version_memory
    jvm_exists = version_id in config.version_jvm_args
    state["settings"] = {
        "memory": {
            "existed": memory_exists,
            "value": int(config.version_memory.get(version_id, config.memory_mb)),
        },
        "jvm_args": {
            "existed": jvm_exists,
            "value": str(config.version_jvm_args.get(version_id, config.jvm_args)),
        },
    }


def metadata_for(
    project: dict[str, Any],
    version: dict[str, Any],
    path: Path,
    context: LoaderContext,
    content_type: str,
) -> dict[str, Any]:
    file = modrinth.primary_file(version) or {}
    hashes = file.get("hashes") if isinstance(file.get("hashes"), dict) else {}
    slug = str(project.get("slug") or project.get("id") or "")
    kind = "shader" if content_type == "shader" else "resourcepack" if content_type == "resourcepack" else "mod"
    page_kind = "shader" if kind == "shader" else "resourcepack" if kind == "resourcepack" else "mod"
    return {
        "title": str(project.get("title") or path.stem),
        "project_id": str(project.get("id") or ""),
        "slug": slug,
        "source": "modrinth",
        "source_label": "Modrinth",
        "version_id": str(version.get("id") or ""),
        "file_id": str(version.get("id") or ""),
        "file_name": path.name,
        "sha1": str(hashes.get("sha1") or ""),
        "content_type": kind,
        "icon_url": str(project.get("icon_url") or ""),
        "author": "",
        "categories": project.get("categories") or [],
        "loaders": version.get("loaders") or [],
        "game_versions": version.get("game_versions") or [],
        "downloads": project.get("downloads") or 0,
        "description": str(project.get("description") or ""),
        "project_url": f"https://modrinth.com/{page_kind}/{slug}",
        "game_version": context.mc_version,
        "loader": context.loader,
    }


class _ModInstaller:
    """串行安装 Mod 与其前置依赖，重复项目只下载一次。"""

    def __init__(
        self,
        context: LoaderContext,
        *,
        threads: int = 8,
        cancel_event=None,
        progress_cb: ProgressCallback | None = None,
    ) -> None:
        self.context = context
        self.threads = max(1, min(16, int(threads or 8)))
        self.cancel = cancel_event
        self.progress = progress_cb or (lambda p, m: None)
        self.known = installed_meta_keys(context.mods_dir)
        self.claimed: set[str] = set()
        self.installed: list[InstalledOptimization] = []
        self.errors: list[str] = []
        self._count = 0
        self._limit = 28

    # ------------------------------------------------------------------
    def _cancelled(self) -> bool:
        return bool(self.cancel is not None and self.cancel.is_set())

    def _error(self, message: str) -> None:
        if message and message not in self.errors:
            self.errors.append(message)

    def _mark(self, project: dict[str, Any]) -> None:
        for key in (project.get("id"), project.get("slug")):
            value = str(key or "").strip().lower()
            if value:
                self.claimed.add(value)
                self.known.add(value)

    def _project_known(self, project: dict[str, Any]) -> bool:
        values = [
            str(project.get("id") or "").lower(),
            str(project.get("slug") or "").lower(),
        ]
        if any(value and value in self.known for value in values):
            return True
        tokens = [value for value in values if value]
        for path in all_mod_files(self.context.mods_dir):
            if filename_matches(path.name, *tokens):
                self.known.add(path.name.lower())
                return True
        return False

    def _pick_versions(
        self,
        project: dict[str, Any],
        content_type: str,
        preferred_version_id: str = "",
    ) -> list[dict[str, Any]]:
        project_id = str(project.get("id") or "")
        if content_type in ("shader", "resourcepack"):
            versions = modrinth.fetch_mod_versions(
                project_id, "", self.context.mc_version, content_type
            )
            if not versions:
                versions = modrinth.fetch_mod_versions(
                    project_id, "", "", content_type
                )
        else:
            versions = []
            for key in loader_candidates(self.context.loader):
                loader_name = LOADER_NAMES.get(key, key)
                found = modrinth.fetch_mod_versions(
                    project_id,
                    loader_name,
                    self.context.mc_version,
                    "mod",
                )
                if found:
                    versions = found
                    break
        if preferred_version_id:
            exact = [
                item
                for item in versions
                if str(item.get("id") or "") == str(preferred_version_id)
            ]
            if exact:
                return exact + [item for item in versions if item not in exact]
        return versions

    def install_project(
        self,
        ident: str,
        *,
        title: str = "",
        content_type: str = "mod",
        preferred_version_id: str = "",
        depth: int = 0,
    ) -> Path | None:
        if self._cancelled():
            return None
        ident = str(ident or "").strip()
        if not ident or ident.lower() in self.claimed:
            return None
        if self._count >= self._limit:
            self._error("依赖数量超过上限，已停止继续安装前置 Mod")
            return None
        try:
            project = modrinth.fetch_project(ident)
        except Exception as exc:
            self._error(f"{title or ident} 查询失败：{exc}")
            return None
        slug = str(project.get("slug") or ident)
        name = str(title or project.get("title") or slug)
        self._mark(project)
        if content_type == "mod" and self._project_known(project):
            self.progress(100, f"{name} 已安装")
            return None

        try:
            versions = self._pick_versions(
                project, content_type, preferred_version_id
            )
        except Exception as exc:
            self._error(f"{name} 版本列表读取失败：{exc}")
            return None
        if not versions:
            self._error(
                f"{name} 没有适配 {self.context.loader_label} "
                f"{self.context.mc_version} 的文件，已跳过"
            )
            return None
        version = versions[0]
        version_id = str(version.get("id") or "")

        if content_type == "mod":
            target_dir = self.context.mods_dir
        elif content_type == "shader":
            target_dir = self.context.shaderpacks_dir
        else:
            target_dir = self.context.resourcepacks_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        try:
            path = modrinth.download_mod_file(
                version,
                target_dir,
                progress_cb=self.progress,
                cancel_event=self.cancel,
                threads=self.threads,
            )
        except Exception as exc:
            if self._cancelled():
                return None
            self._error(f"{name} 下载失败：{exc}")
            return None

        self._count += 1
        metadata = metadata_for(project, version, path, self.context, content_type)
        sha1 = str(metadata.get("sha1") or "")
        expected = str(sha1 or "").lower()
        if expected and file_sha1(path).lower() != expected:
            self._error(f"{name} 文件校验失败，已保留下载文件")
        if content_type == "mod":
            try:
                from .mods import remember_downloaded_mod

                remember_downloaded_mod(target_dir, path, metadata)
            except Exception:
                pass
        self.installed.append(
            InstalledOptimization(
                kind=content_type,
                slug=slug,
                title=name,
                filename=path.name,
                sha1=sha1 or file_sha1(path),
                version_id=version_id,
                project_id=str(project.get("id") or ""),
            )
        )
        self.progress(100, f"{name} 安装完成")

        if content_type == "mod" and depth < 2:
            for dep in modrinth.required_dependencies(version):
                if self._cancelled():
                    break
                dep_id = str(dep.get("project_id") or "").strip()
                if not dep_id:
                    continue
                try:
                    self.install_project(
                        dep_id,
                        content_type="mod",
                        preferred_version_id=str(dep.get("version_id") or ""),
                        depth=depth + 1,
                    )
                except Exception as exc:
                    self._error(f"{name} 的前置 Mod 安装失败：{exc}")
        return path


def apply_optimization(
    plan: OptimizationPlan,
    config,
    *,
    install_mods: bool = True,
    apply_memory: bool = True,
    apply_jvm: bool = True,
    threads: int = 8,
    progress_cb: OptimizeProgress | None = None,
    cancel_event=None,
) -> OptimizationResult:
    progress = progress_cb or (lambda p, m: None)
    context = plan.context
    state = _ensure_state(context.game_dir, context.version_id)
    result = OptimizationResult()

    if apply_memory or apply_jvm:
        _snapshot_settings(state, config, context.version_id)
    if apply_memory:
        config.version_memory[context.version_id] = int(plan.recommended_memory_mb)
        result.memory_mb = int(plan.recommended_memory_mb)
    if apply_jvm:
        config.version_jvm_args[context.version_id] = plan.recommended_jvm_args
        result.jvm_args = plan.recommended_jvm_args
    if apply_memory or apply_jvm:
        config.save()

    if install_mods and plan.has_loader and plan.missing:
        installer = _ModInstaller(
            context,
            threads=threads,
            cancel_event=cancel_event,
            progress_cb=None,
        )
        total = len(plan.missing)
        for index, mod in enumerate(plan.missing):
            if cancel_event is not None and cancel_event.is_set():
                result.cancelled = True
                break

            def on_file_progress(
                percent: int, message: str, i: int = index
            ) -> None:
                overall = int((i + max(0, min(100, int(percent))) / 100) / total * 100)
                progress(
                    overall,
                    f"[{i + 1}/{total}] {message}",
                )

            installer.progress = on_file_progress
            progress(int(index / total * 100), f"正在准备 {mod.title}…")
            installer.install_project(mod.slug, title=mod.title, content_type="mod")
        result.installed = list(installer.installed)
        result.errors = list(installer.errors)
        if installer.installed:
            state.setdefault("installed", []).extend(
                {
                    "kind": item.kind,
                    "slug": item.slug,
                    "title": item.title,
                    "filename": item.filename,
                    "sha1": item.sha1,
                    "version_id": item.version_id,
                    "project_id": item.project_id,
                }
                for item in installer.installed
            )
            state["installed"] = _dedupe_installed(state["installed"])
    elif install_mods and not plan.has_loader:
        progress(100, "原版客户端无需安装优化 Mod")
    else:
        progress(100, "优化 Mod 已经齐全")

    if not apply_memory and not apply_jvm and not result.installed:
        progress(100, "没有需要应用的优化")
        return result
    state["updated_at"] = time.time()
    _save_state(context.game_dir, state)
    progress(100, "性能优化完成" if not result.cancelled else "性能优化已取消")
    return result


def _dedupe_installed(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in reversed(list(items)):
        if not isinstance(item, dict):
            continue
        key = (str(item.get("kind") or ""), str(item.get("filename") or ""))
        if not key[1] or key in seen:
            continue
        seen.add(key)
        result.append(item)
    result.reverse()
    return result


# ---------------------------------------------------------------------------
# 画面一键升级


@dataclass(frozen=True)
class GraphicsProfile:
    key: str
    title: str
    subtitle: str
    options: dict[str, str]
    color: str = "green"


GRAPHICS_PROFILES: tuple[GraphicsProfile, ...] = (
    GraphicsProfile(
        key="performance",
        title="流畅",
        subtitle="优先帧率，适合核显与老显卡",
        options={
            "graphics": "fast",
            "renderDistance": "6",
            "simulationDistance": "5",
            "maxFps": "120",
            "particles": "minimal",
            "ao": "false",
            "entityShadows": "false",
            "mipmapLevels": "2",
            "biomeBlendRadius": "0",
            "clouds": "false",
            "enableVsync": "false",
        },
        color="orange",
    ),
    GraphicsProfile(
        key="balanced",
        title="均衡",
        subtitle="画质与帧率的折中，大多数电脑推荐",
        options={
            "graphics": "fancy",
            "renderDistance": "10",
            "simulationDistance": "8",
            "maxFps": "144",
            "particles": "decreased",
            "ao": "true",
            "entityShadows": "true",
            "mipmapLevels": "4",
            "biomeBlendRadius": "2",
            "clouds": "true",
            "enableVsync": "false",
        },
        color="green",
    ),
    GraphicsProfile(
        key="quality",
        title="精美",
        subtitle="更远视野与完整特效，适合独显",
        options={
            "graphics": "fancy",
            "renderDistance": "16",
            "simulationDistance": "12",
            "maxFps": "0",
            "particles": "all",
            "ao": "true",
            "entityShadows": "true",
            "mipmapLevels": "4",
            "biomeBlendRadius": "4",
            "clouds": "true",
            "enableVsync": "false",
        },
        color="purple",
    ),
)

SHADER_SLUG = "complementary-reimagined"
SHADER_TITLE = "Complementary Reimagined"
IRIS_SLUG = "iris"


def graphics_profile(key: str) -> GraphicsProfile:
    for profile in GRAPHICS_PROFILES:
        if profile.key == key:
            return profile
    return GRAPHICS_PROFILES[1]


def read_options(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return values
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if key:
            values[key] = value.strip()
    return values


def write_options(path: Path, updates: dict[str, str]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        lines = []
    pending = dict(updates)
    output: list[str] = []
    for line in lines:
        if ":" in line:
            key = line.split(":", 1)[0].strip()
            if key in pending:
                output.append(f"{key}:{pending.pop(key)}")
                continue
        output.append(line)
    for key, value in pending.items():
        output.append(f"{key}:{value}")
    path.write_text("\n".join(output) + "\n", encoding="utf-8")


@dataclass
class GraphicsPlan:
    context: LoaderContext
    profile: GraphicsProfile
    current_options: dict[str, str]
    recommended_profile: str
    gpus: list[GpuInfo]
    optifine: bool
    iris_installed: bool
    shader_installed: bool
    can_install_iris: bool
    warnings: list[str]

    @property
    def loader_label(self) -> str:
        return self.context.loader_label

    @property
    def mc_version(self) -> str:
        return self.context.mc_version

    @property
    def game_dir(self) -> Path:
        return self.context.game_dir

    @property
    def shaderpacks_dir(self) -> Path:
        return self.context.shaderpacks_dir


@dataclass
class GraphicsResult:
    profile: str = ""
    options_written: bool = False
    installed: list[InstalledOptimization] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    shader_file: str = ""
    iris_ready: bool = False
    cancelled: bool = False


def _has_file(directory: Path, *tokens: str) -> bool:
    try:
        files = [
            path
            for path in Path(directory).iterdir()
            if path.is_file()
        ]
    except OSError:
        return False
    return any(filename_matches(path.name, *tokens) for path in files)


def build_graphics_plan(
    version_id: str,
    config,
    versions: Iterable,
    profile_key: str = "",
    *,
    gpus: list[GpuInfo] | None = None,
) -> GraphicsPlan:
    context = resolve_context(version_id, versions, config)
    gpu_list = list(gpus if gpus is not None else detect_gpus())
    recommended = suggest_graphics_profile(gpu_list, system_memory_mb())
    profile = graphics_profile(profile_key or recommended)
    optifine = is_optifine_installed(context.mods_dir)
    iris = _has_file(context.mods_dir, "iris")
    shader = _has_file(context.shaderpacks_dir, "complementary", "reimagined")
    can_iris = context.loader_key in ("fabric", "quilt", "neoforge") and not optifine
    warnings: list[str] = []
    if not context.has_loader:
        warnings.append("原版客户端只能调整画面参数；光影需要 Iris，而 Iris 需要 Fabric/Quilt/NeoForge。")
    elif context.loader_key == "forge":
        warnings.append("Forge 暂不自动安装 Iris。画面参数仍会生效，光影请手动使用 OptiFine 或改用 NeoForge。")
    if optifine:
        warnings.append("检测到 OptiFine，已保留它作为光影加载方案，不安装 Iris。")
    if not gpu_list:
        warnings.append("没有读取到显卡信息，已按均衡档推荐。")
    return GraphicsPlan(
        context=context,
        profile=profile,
        current_options=read_options(context.options_path),
        recommended_profile=recommended,
        gpus=gpu_list,
        optifine=optifine,
        iris_installed=iris,
        shader_installed=shader,
        can_install_iris=can_iris,
        warnings=_dedupe(warnings),
    )


def _backup_options(state: dict[str, Any], context: LoaderContext) -> None:
    if "options_backup" in state:
        return
    directory = _state_dir(context.game_dir)
    directory.mkdir(parents=True, exist_ok=True)
    existed = context.options_path.is_file()
    state["options_existed"] = existed
    if existed:
        backup = directory / OPTIONS_BACKUP_NAME
        shutil.copy2(context.options_path, backup)
        state["options_backup"] = str(backup)


def _backup_iris(state: dict[str, Any], path: Path) -> None:
    if "iris_backup" in state:
        return
    directory = _state_dir(path.parent.parent)
    directory.mkdir(parents=True, exist_ok=True)
    existed = path.is_file()
    state["iris_existed"] = existed
    if existed:
        backup = directory / IRIS_BACKUP_NAME
        shutil.copy2(path, backup)
        state["iris_backup"] = str(backup)


def _write_iris_selection(game_dir: Path, shader_file: str) -> None:
    path = Path(game_dir) / "config" / "iris.properties"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        lines = []
    updates = {
        "shaderPack": shader_file,
        "enableShaders": "true",
    }
    output: list[str] = []
    pending = dict(updates)
    for line in lines:
        if "=" in line:
            key = line.split("=", 1)[0].strip()
            if key in pending:
                output.append(f"{key}={pending.pop(key)}")
                continue
        output.append(line)
    for key, value in pending.items():
        output.append(f"{key}={value}")
    path.write_text("\n".join(output) + "\n", encoding="utf-8")


def apply_graphics(
    plan: GraphicsPlan,
    config,
    *,
    apply_options: bool = True,
    install_iris: bool = True,
    install_shader: bool = True,
    threads: int = 8,
    progress_cb: OptimizeProgress | None = None,
    cancel_event=None,
) -> GraphicsResult:
    progress = progress_cb or (lambda p, m: None)
    context = plan.context
    state = _ensure_state(context.game_dir, context.version_id)
    result = GraphicsResult(profile=plan.profile.key)

    if apply_options:
        progress(5, "正在备份并写入画面参数…")
        _backup_options(state, context)
        write_options(context.options_path, plan.profile.options)
        result.options_written = True
        progress(20, f"已应用「{plan.profile.title}」画面参数")

    installer = _ModInstaller(
        context,
        threads=threads,
        cancel_event=cancel_event,
        progress_cb=None,
    )
    iris_ready = plan.iris_installed
    if install_iris and plan.can_install_iris and not iris_ready:
        if cancel_event is not None and cancel_event.is_set():
            result.cancelled = True
        else:
            def iris_progress(percent: int, message: str) -> None:
                progress(20 + int(max(0, min(100, percent)) * 0.45), message)

            installer.progress = iris_progress
            progress(22, "正在安装 Iris 光影加载器…")
            installer.install_project(IRIS_SLUG, title="Iris Shaders", content_type="mod")
            iris_ready = bool(installer.installed) or _has_file(context.mods_dir, "iris")
    result.iris_ready = iris_ready

    shader_file = ""
    if install_shader and iris_ready:
        if cancel_event is not None and cancel_event.is_set():
            result.cancelled = True
        else:
            def shader_progress(percent: int, message: str) -> None:
                progress(68 + int(max(0, min(100, percent)) * 0.28), message)

            installer.progress = shader_progress
            progress(70, f"正在安装光影 {SHADER_TITLE}…")
            path = installer.install_project(
                SHADER_SLUG,
                title=SHADER_TITLE,
                content_type="shader",
            )
            if path is not None:
                shader_file = path.name
            else:
                existing = [
                    item
                    for item in _list_files(context.shaderpacks_dir)
                    if "complementary" in normalize_token(item.name)
                ]
                if existing:
                    shader_file = existing[0].name
            if shader_file:
                iris_path = context.game_dir / "config" / "iris.properties"
                _backup_iris(state, iris_path)
                _write_iris_selection(context.game_dir, shader_file)
                result.shader_file = shader_file

    result.installed = list(installer.installed)
    result.errors = list(installer.errors)
    if installer.installed:
        state.setdefault("installed", []).extend(
            {
                "kind": item.kind,
                "slug": item.slug,
                "title": item.title,
                "filename": item.filename,
                "sha1": item.sha1,
                "version_id": item.version_id,
                "project_id": item.project_id,
            }
            for item in installer.installed
        )
        state["installed"] = _dedupe_installed(state["installed"])
    state["graphics_profile"] = plan.profile.key
    state["updated_at"] = time.time()
    _save_state(context.game_dir, state)
    progress(100, "画面升级完成" if not result.cancelled else "画面升级已取消")
    return result


def _list_files(directory: Path) -> list[Path]:
    try:
        return [path for path in Path(directory).iterdir() if path.is_file()]
    except OSError:
        return []


# ---------------------------------------------------------------------------
# 回滚


@dataclass
class RestoreResult:
    settings_restored: bool = False
    options_restored: bool = False
    iris_restored: bool = False
    moved_files: list[str] = field(default_factory=list)
    kept_files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def restore_optimization(version_id: str, config) -> RestoreResult:
    game_dir = Path(config.version_game_path(version_id))
    state = load_state(game_dir)
    if not state.get("active"):
        raise OptimizeError("这个版本没有可恢复的优化记录。")
    result = RestoreResult()

    settings = state.get("settings")
    if isinstance(settings, dict):
        memory = settings.get("memory") if isinstance(settings.get("memory"), dict) else {}
        jvm = settings.get("jvm_args") if isinstance(settings.get("jvm_args"), dict) else {}
        if memory:
            if memory.get("existed"):
                try:
                    config.version_memory[version_id] = int(memory.get("value") or 0)
                except Exception:
                    pass
            else:
                config.version_memory.pop(version_id, None)
        if jvm:
            if jvm.get("existed"):
                config.version_jvm_args[version_id] = str(jvm.get("value") or "")
            else:
                config.version_jvm_args.pop(version_id, None)
        config.save()
        result.settings_restored = True

    options_backup = str(state.get("options_backup") or "")
    options_path = game_dir / "options.txt"
    if options_backup and Path(options_backup).is_file():
        try:
            shutil.copy2(options_backup, options_path)
            result.options_restored = True
        except OSError as exc:
            result.errors.append(f"恢复 options.txt 失败：{exc}")
    elif state.get("options_existed") is False and options_path.is_file():
        target_dir = _state_dir(game_dir) / "restored"
        target_dir.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(options_path), str(target_dir / "options-after-optimize.txt"))
            result.options_restored = True
        except OSError as exc:
            result.errors.append(f"移走生成的 options.txt 失败：{exc}")

    iris_backup = str(state.get("iris_backup") or "")
    iris_path = game_dir / "config" / "iris.properties"
    if iris_backup and Path(iris_backup).is_file():
        try:
            iris_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(iris_backup, iris_path)
            result.iris_restored = True
        except OSError as exc:
            result.errors.append(f"恢复 iris.properties 失败：{exc}")
    elif state.get("iris_existed") is False and iris_path.is_file():
        target_dir = _state_dir(game_dir) / "restored"
        target_dir.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(
                str(iris_path), str(target_dir / "iris-after-optimize.properties")
            )
            result.iris_restored = True
        except OSError as exc:
            result.errors.append(f"移走生成的 iris.properties 失败：{exc}")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    for item in state.get("installed") or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "mod")
        directory = (
            game_dir / "shaderpacks"
            if kind == "shader"
            else game_dir / "resourcepacks"
            if kind == "resourcepack"
            else game_dir / "mods"
        )
        filename = Path(str(item.get("filename") or "")).name
        if not filename:
            continue
        target = directory / filename
        if not target.is_file():
            continue
        expected = str(item.get("sha1") or "").lower()
        if expected and file_sha1(target).lower() != expected:
            result.kept_files.append(filename)
            continue
        backup_dir = _state_dir(game_dir) / "restored" / stamp / directory.name
        backup_dir.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(target), str(backup_dir / filename))
            result.moved_files.append(filename)
        except OSError as exc:
            result.errors.append(f"移走 {filename} 失败：{exc}")

    state["active"] = False
    state["restored_at"] = time.time()
    _save_state(game_dir, state)
    return result


def state_summary(version_id: str, config) -> dict[str, Any]:
    game_dir = Path(config.version_game_path(version_id))
    state = load_state(game_dir)
    if not state.get("active"):
        return {}
    return state
