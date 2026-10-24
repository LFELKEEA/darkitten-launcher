from __future__ import annotations

import json
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .config import APP_DIR

OFFICIAL = "official"
BMCLAPI = "bmclapi"
SOURCES = (OFFICIAL, BMCLAPI)

MANIFEST_URLS = {
    OFFICIAL: "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json",
    BMCLAPI: "https://bmclapi2.bangbang93.com/mc/game/version_manifest_v2.json",
}
SOURCE_LABELS = {
    OFFICIAL: "Mojang 官方",
    BMCLAPI: "BMCLAPI 镜像",
}

STATE_PATH = APP_DIR / "cache" / "source_health.json"
HEALTH_TTL = 10 * 60
PROBE_TIMEOUT = 2.5
FAILURE_THRESHOLD = 2
FAILURE_COOLDOWN = 5 * 60
USER_AGENT = "DarkittenLauncher/1.9"

_lock = threading.RLock()


def source_for_url(url: str) -> str:
    """把下载地址归类到官方源或镜像源，无法归类时返回空字符串。"""
    try:
        host = urlsplit(str(url)).netloc.lower()
    except Exception:
        return ""
    if not host:
        return ""
    if "bmclapi" in host or host.endswith("bangbang93.com"):
        return BMCLAPI
    if (
        host.endswith("mojang.com")
        or host.endswith("minecraft.net")
        or host.endswith("minecraftservices.com")
    ):
        return OFFICIAL
    return ""


def _default_state() -> dict[str, Any]:
    return {"updated": 0.0, "sources": {}}


def _read_state() -> dict[str, Any]:
    try:
        data = json.loads(Path(STATE_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return _default_state()
    if not isinstance(data, dict) or not isinstance(data.get("sources"), dict):
        return _default_state()
    return data


def _write_state(state: dict[str, Any]) -> None:
    path = Path(STATE_PATH)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".tmp")
        temp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        temp.replace(path)
    except OSError:
        pass


def _entry(state: dict[str, Any], source: str) -> dict[str, Any]:
    entry = state.setdefault("sources", {}).setdefault(source, {})
    return entry if isinstance(entry, dict) else {}


def _fresh(entry: dict[str, Any], now: float) -> bool:
    if not entry:
        return False
    blocked_until = float(entry.get("blocked_until") or 0)
    if blocked_until > now:
        return True
    if entry.get("ok") is False:
        # 上次探测或下载失败，允许下次重新探测，避免永久拉黑。
        return False
    checked = float(entry.get("checked_at") or 0)
    return now - checked < HEALTH_TTL


def _probe_latency(source: str, timeout: float) -> float:
    url = MANIFEST_URLS.get(source)
    if not url:
        raise ValueError(f"未知下载源：{source}")
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Range": "bytes=0-0"},
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        response.read(1)
    return (time.perf_counter() - started) * 1000


def _probe_one(source: str, timeout: float) -> dict[str, Any]:
    try:
        latency = _probe_latency(source, timeout)
        return {
            "ok": True,
            "latency_ms": round(latency),
            "checked_at": time.time(),
            "failures": 0,
            "blocked_until": 0.0,
            "last_error": "",
        }
    except Exception as exc:
        return {
            "ok": False,
            "latency_ms": None,
            "checked_at": time.time(),
            "failures": FAILURE_THRESHOLD,
            "blocked_until": time.time() + FAILURE_COOLDOWN,
            "last_error": str(exc)[:200],
        }


def refresh_sources(
    sources: tuple[str, ...] = SOURCES,
    *,
    force: bool = False,
    allow_probe: bool = True,
    timeout: float = PROBE_TIMEOUT,
) -> dict[str, Any]:
    """检查下载源可用性；结果缓存 10 分钟，失败源熔断 5 分钟。"""
    with _lock:
        state = _read_state()
    now = time.time()
    pending = [
        source
        for source in sources
        if force or not _fresh(_entry(state, source), now)
    ]
    if not pending:
        return state

    if allow_probe:
        with ThreadPoolExecutor(max_workers=max(1, len(pending))) as pool:
            results = list(
                pool.map(lambda item: (item, _probe_one(item, timeout)), pending)
            )
        with _lock:
            state = _read_state()
            for source, entry in results:
                state.setdefault("sources", {})[source] = entry
            state["updated"] = time.time()
            _write_state(state)
        return state

    # 不允许联网时只把缺失项标记为未知，排序仍可继续。
    with _lock:
        state = _read_state()
        for source in pending:
            _entry(state, source)
        return state


def order_sources(
    preferred: str = "auto",
    *,
    allow_probe: bool = False,
    force_probe: bool = False,
) -> list[str]:
    """返回按优先级排列的下载源。

    auto 会使用测速结果，延迟更低且未被熔断的源排在前面；
    official / bmclapi 固定尊重用户选择，另一条线路排在后面兜底。
    """
    if preferred in (OFFICIAL, BMCLAPI):
        other = BMCLAPI if preferred == OFFICIAL else OFFICIAL
        return [preferred, other]

    state = refresh_sources(force=force_probe, allow_probe=allow_probe)
    now = time.time()
    entries = {source: _entry(state, source) for source in SOURCES}

    def usable(source: str) -> bool:
        entry = entries[source]
        if float(entry.get("blocked_until") or 0) > now:
            return False
        return entry.get("ok") is not False

    available = [source for source in SOURCES if usable(source)]
    if not available:
        available = list(SOURCES)

    def rank(source: str) -> tuple[int, float]:
        entry = entries[source]
        latency = entry.get("latency_ms")
        if entry.get("ok") is False or latency is None:
            return (1, float("inf"))
        return (0, float(latency))

    available.sort(key=lambda source: (rank(source), SOURCES.index(source)))
    # 未被探测过的源也放入兜底，避免因为缓存缺失只能走一条线路。
    for source in SOURCES:
        if source not in available:
            available.append(source)
    return available


def is_source_usable(source: str) -> bool:
    """读取缓存中的熔断状态，供逐文件选择镜像时快速判断。"""
    state = _read_state()
    entry = _entry(state, source)
    now = time.time()
    if float(entry.get("blocked_until") or 0) > now:
        return False
    return entry.get("ok") is not False


def report_success(url: str) -> None:
    """下载成功后清除对应源的失败计数。"""
    source = source_for_url(url)
    if not source:
        return
    with _lock:
        state = _read_state()
        entry = _entry(state, source)
        if (
            entry.get("failures")
            or entry.get("ok") is not True
            or entry.get("blocked_until")
        ):
            entry.update(
                {
                    "ok": True,
                    "failures": 0,
                    "blocked_until": 0.0,
                    "checked_at": time.time(),
                    "last_error": "",
                }
            )
            _write_state(state)


def report_failure(url: str, error: object = "") -> None:
    """记录一次下载失败；连续失败会暂时熔断该源。"""
    source = source_for_url(url)
    if not source:
        return
    message = str(error or "")
    # 校验失败、取消、磁盘空间不足不是线路问题，不能误熔断。
    ignored = ("SHA1", "校验", "取消", "cancel", "空间", "No space")
    if any(token.lower() in message.lower() for token in ignored):
        return
    now = time.time()
    with _lock:
        state = _read_state()
        entry = _entry(state, source)
        failures = int(entry.get("failures") or 0) + 1
        entry["failures"] = failures
        entry["checked_at"] = now
        entry["last_error"] = message[:200]
        if failures >= FAILURE_THRESHOLD:
            entry["ok"] = False
            entry["latency_ms"] = None
            entry["blocked_until"] = now + FAILURE_COOLDOWN
        _write_state(state)


def reset_health() -> None:
    """清空测速与熔断记录（设置页/诊断用）。"""
    with _lock:
        _write_state(_default_state())


def health_summary() -> str:
    """给日志/诊断使用的一行摘要。"""
    state = _read_state()
    now = time.time()
    parts: list[str] = []
    for source in SOURCES:
        entry = _entry(state, source)
        label = SOURCE_LABELS[source]
        if float(entry.get("blocked_until") or 0) > now:
            parts.append(f"{label}=熔断")
        elif entry.get("ok") is True:
            latency = entry.get("latency_ms")
            parts.append(f"{label}={latency}ms" if latency else f"{label}=可用")
        elif entry.get("ok") is False:
            parts.append(f"{label}=不可用")
        else:
            parts.append(f"{label}=未测速")
    return " · ".join(parts)
