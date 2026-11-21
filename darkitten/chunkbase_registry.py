"""运行时读取 Chunkbase 的种子地图版本列表。

Chunkbase 的种子地图页面把版本写在下拉框里，新版本上线后页面会先更新。
启动器每次打开助手页时优先读本地缓存，缓存过期就在后台重新抓取，
所以以后游戏版本更新不需要再发一版启动器。
"""

from __future__ import annotations

import html as html_module
import json
import re
import time
import urllib.request
from pathlib import Path

from .config import APP_DIR

SEED_MAP_URL = "https://www.chunkbase.com/apps/seed-map"
CACHE_PATH = APP_DIR / "cache" / "chunkbase_versions.json"
CACHE_TTL_SECONDS = 12 * 3600
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)

GROUP_JAVA = "Java 正式版"
GROUP_JAVA_EXPERIMENTAL = "Java 实验版"
GROUP_JAVA_LB = "Java 超大型生物群系 (LB)"
GROUP_BEDROCK = "基岩版 Bedrock"
GROUP_ORDER = (GROUP_JAVA, GROUP_JAVA_EXPERIMENTAL, GROUP_JAVA_LB, GROUP_BEDROCK)

_OPTGROUP_RE = re.compile(
    r"<optgroup\s+label=\"([^\"]*)\"\s*>(.*?)</optgroup>", re.S | re.I
)
_OPTION_RE = re.compile(r"<option\s+value=\"([^\"]*)\"[^>]*>(.*?)</option>", re.S | re.I)
_PLATFORM_RE = re.compile(r"^(?:java|bedrock)_[0-9a-z_]+$")
_TAG_RE = re.compile(r"<[^>]+>")

Groups = tuple[tuple[str, tuple[tuple[str, str], ...]], ...]


class RegistryError(ValueError):
    pass


def _clean_text(value: str) -> str:
    text = _TAG_RE.sub("", value or "")
    return re.sub(r"\s+", " ", html_module.unescape(text)).strip()


def _classify(group_label: str, platform: str) -> str:
    if platform.startswith("bedrock_"):
        return GROUP_BEDROCK
    if platform.endswith("_lb"):
        return GROUP_JAVA_LB
    if "experimental" in group_label.lower() or "实验" in group_label:
        return GROUP_JAVA_EXPERIMENTAL
    return GROUP_JAVA


def parse_platform_options(page: str) -> Groups:
    """从种子地图页面的 HTML 中解析版本下拉框。"""
    buckets: dict[str, list[tuple[str, str]]] = {name: [] for name in GROUP_ORDER}
    seen: set[str] = set()
    for group_match in _OPTGROUP_RE.finditer(page or ""):
        group_label = _clean_text(group_match.group(1))
        if "latest" in group_label.lower():
            # “最新版本”组只是把下面已有的版本再列一遍，跳过避免重复。
            continue
        for option_match in _OPTION_RE.finditer(group_match.group(2)):
            raw_value = html_module.unescape(option_match.group(1)).strip()
            label = _clean_text(option_match.group(2))
            platform = raw_value.split("//")[-1].strip()
            if not _PLATFORM_RE.match(platform) or platform in seen:
                continue
            seen.add(platform)
            buckets[_classify(group_label, platform)].append((label or platform, platform))

    groups = tuple(
        (name, tuple(buckets[name])) for name in GROUP_ORDER if buckets[name]
    )
    if not groups:
        raise RegistryError("页面里没有找到版本列表")
    total = sum(len(items) for _name, items in groups)
    if total < 10:
        raise RegistryError("解析到的版本数量异常")
    return groups


def platforms(groups: Groups) -> set[str]:
    return {platform for _label, items in groups for _label2, platform in items}


def _valid(groups: object) -> bool:
    if not isinstance(groups, list) or not groups:
        return False
    total = 0
    for group in groups:
        if not isinstance(group, list) or len(group) != 2:
            return False
        name, items = group
        if not isinstance(name, str) or not isinstance(items, list) or not items:
            return False
        for item in items:
            if (
                not isinstance(item, list)
                or len(item) != 2
                or not all(isinstance(part, str) for part in item)
                or not _PLATFORM_RE.match(item[1])
            ):
                return False
            total += 1
    return total >= 10


def _to_groups(raw: object) -> Groups:
    if not _valid(raw):
        raise RegistryError("缓存的版本列表格式不正确")
    return tuple(
        (str(group[0]), tuple((str(item[0]), str(item[1])) for item in group[1]))
        for group in raw
    )  # type: ignore[union-attr]


def read_cache() -> tuple[Groups, float] | None:
    try:
        payload = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        groups = _to_groups(payload.get("groups"))
        fetched_at = float(payload.get("fetched_at", 0))
    except (AttributeError, TypeError, ValueError, RegistryError):
        return None
    return groups, fetched_at


def write_cache(groups: Groups) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "source": SEED_MAP_URL,
            "fetched_at": time.time(),
            "groups": [[name, [list(item) for item in items]] for name, items in groups],
        }
        temporary = CACHE_PATH.with_name(CACHE_PATH.name + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(CACHE_PATH)
    except OSError:
        pass


def fetch_groups(timeout: float = 10.0) -> Groups:
    request = urllib.request.Request(
        SEED_MAP_URL,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        page = response.read(2_000_000).decode("utf-8", "replace")
    return parse_platform_options(page)


def builtin_groups() -> Groups:
    from .game_assistant import CHUNKBASE_VERSION_GROUPS

    return tuple(
        (str(name), tuple((str(label), str(platform)) for label, platform in items))
        for name, items in CHUNKBASE_VERSION_GROUPS
    )


def load_groups(
    *,
    force: bool = False,
    allow_network: bool = True,
    timeout: float = 10.0,
) -> tuple[Groups, str]:
    """返回 (版本分组, 来源)。来源为 remote / cache / builtin。"""
    cached = read_cache()
    fresh = bool(cached) and (time.time() - cached[1]) < CACHE_TTL_SECONDS
    if cached and fresh and not force:
        return cached[0], "cache"
    if allow_network:
        try:
            groups = fetch_groups(timeout=timeout)
        except Exception:
            groups = None
        if groups is not None:
            write_cache(groups)
            return groups, "remote"
    if cached:
        return cached[0], "cache"
    return builtin_groups(), "builtin"
