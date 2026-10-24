from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from darkitten import installer, sources  # noqa: E402
from darkitten.installer import Installer, mirror_urls  # noqa: E402

CHECKS: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name} FAILED {detail}")
    CHECKS.append(name)
    print(f"{name}: OK")


def write_state(path: Path, official_ms: int | None, bmcl_ms: int | None) -> None:
    now = time.time()
    data = {
        "updated": now,
        "sources": {
            "official": {
                "ok": official_ms is not None,
                "latency_ms": official_ms,
                "checked_at": now,
                "failures": 0,
                "blocked_until": 0,
            },
            "bmclapi": {
                "ok": bmcl_ms is not None,
                "latency_ms": bmcl_ms,
                "checked_at": now,
                "failures": 0,
                "blocked_until": 0,
            },
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def main() -> None:
    temp = Path(tempfile.mkdtemp(prefix="dkl-source-"))
    state_path = temp / "source_health.json"
    sources.STATE_PATH = state_path

    check(
        "source-url-official",
        sources.source_for_url(
            "https://libraries.minecraft.net/org/ow2/asm/asm/9.7/asm-9.7.jar"
        )
        == "official",
    )
    check(
        "source-url-bmcl",
        sources.source_for_url(
            "https://bmclapi2.bangbang93.com/maven/org/ow2/asm/asm/9.7/asm-9.7.jar"
        )
        == "bmclapi",
    )
    check("source-url-unknown", sources.source_for_url("https://repo1.maven.org/a.jar") == "")

    write_state(state_path, official_ms=800, bmcl_ms=120)
    mojang_url = "https://libraries.minecraft.net/org/ow2/asm/asm/9.7/asm-9.7.jar"
    auto_urls = mirror_urls(mojang_url, "auto")
    check("auto-picks-faster-mirror", auto_urls[0].startswith("https://bmclapi"), str(auto_urls))
    official_urls = mirror_urls(mojang_url, "official")
    check("official-stays-first", official_urls[0] == mojang_url, str(official_urls))
    bmcl_urls = mirror_urls(mojang_url, "bmclapi")
    check("bmcl-stays-first", bmcl_urls[0].startswith("https://bmclapi"), str(bmcl_urls))

    write_state(state_path, official_ms=80, bmcl_ms=600)
    check(
        "auto-picks-official-when-faster",
        mirror_urls(mojang_url, "auto")[0] == mojang_url,
    )

    # 断熔：连续两次网络失败后，该源应被跳过并进入冷却。
    sources.reset_health()
    sources.report_failure("https://bmclapi2.bangbang93.com/a.jar", "timed out")
    check("failure-count-one", sources.is_source_usable("bmclapi"))
    sources.report_failure("https://bmclapi2.bangbang93.com/b.jar", "connection reset")
    check("failure-opens-circuit", not sources.is_source_usable("bmclapi"))
    order = sources.order_sources("auto")
    check("auto-avoids-blocked-source", order[0] == "official", str(order))
    third_party = (
        "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-47.2.0/"
        "forge-1.20.1-47.2.0-universal.jar"
    )
    third_urls = mirror_urls(third_party, "auto")
    check("third-party-skips-blocked-mirror", third_urls[0] == third_party, str(third_urls))

    # 校验失败不是线路问题，不应触发熔断。
    sources.reset_health()
    sources.report_failure("https://bmclapi2.bangbang93.com/a.jar", "SHA1 校验失败")
    check("sha1-failure-not-counted", sources.is_source_usable("bmclapi"))
    state = json.loads(state_path.read_text(encoding="utf-8"))
    check(
        "sha1-failure-no-counter",
        int(state.get("sources", {}).get("bmclapi", {}).get("failures") or 0) == 0,
    )

    # 成功一次清除失败计数。
    sources.report_failure("https://bmclapi2.bangbang93.com/a.jar", "timeout")
    sources.report_success("https://bmclapi2.bangbang93.com/a.jar")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    check(
        "success-clears-failures",
        int(state["sources"]["bmclapi"].get("failures") or 0) == 0
        and sources.is_source_usable("bmclapi"),
    )

    # 熔断到期后重新探测并恢复。
    now = time.time()
    data = {
        "updated": now,
        "sources": {
            "official": {"ok": True, "latency_ms": 500, "checked_at": now},
            "bmclapi": {
                "ok": False,
                "latency_ms": None,
                "checked_at": now,
                "failures": 2,
                "blocked_until": now - 1,
            },
        },
    }
    state_path.write_text(json.dumps(data), encoding="utf-8")
    probes: list[str] = []

    def fake_probe(source: str, timeout: float) -> float:
        probes.append(source)
        return 30.0 if source == "bmclapi" else 300.0

    original_probe = sources._probe_latency
    sources._probe_latency = fake_probe
    try:
        order = sources.order_sources("auto", allow_probe=True)
        check("cooldown-recovers", order[0] == "bmclapi", str(order))
        check("cooldown-probes-expired-source", "bmclapi" in probes, str(probes))

        # 10 分钟内复用缓存，不重复探测。
        before = len(probes)
        sources.order_sources("auto", allow_probe=True)
        check("probe-cache-reused", len(probes) == before, str(probes))
        sources.order_sources("auto", allow_probe=True, force_probe=True)
        check("force-probe-refreshes", len(probes) > before, str(probes))
    finally:
        sources._probe_latency = original_probe

    # get_manifest 应把更快的线路放在第一位。
    manifest_calls: list[str] = []
    original_fetch = installer.fetch_json
    original_cache = installer.MANIFEST_CACHE
    original_cache_dir = installer.CACHE_DIR
    try:
        installer.MANIFEST_CACHE = temp / "manifest.json"
        installer.CACHE_DIR = temp

        def fake_fetch(url: str, timeout: int = 30) -> dict:
            manifest_calls.append(url)
            return {"versions": [{"id": "1.20.1", "url": "https://example.invalid/v.json"}]}

        installer.fetch_json = fake_fetch
        write_state(state_path, official_ms=900, bmcl_ms=50)
        data = installer.get_manifest("auto", force=True)
        check("manifest-uses-fastest", manifest_calls[0] == sources.MANIFEST_URLS["bmclapi"], str(manifest_calls))
        check("manifest-returns-data", data["versions"][0]["id"] == "1.20.1")
    finally:
        installer.fetch_json = original_fetch
        installer.MANIFEST_CACHE = original_cache
        installer.CACHE_DIR = original_cache_dir

    # Installer auto 选线 + bmclapi 不可用时回退。
    write_state(state_path, official_ms=900, bmcl_ms=60)
    inst = Installer(temp / "game", source="auto")
    inst._resolve_source()
    check("installer-auto-selects-fastest", inst.effective_source == "bmclapi")

    original_probe = sources._probe_latency

    def failed_bmcl(source: str, timeout: float) -> float:
        if source == "bmclapi":
            raise OSError("unreachable")
        return 200.0

    sources._probe_latency = failed_bmcl
    try:
        sources.reset_health()
        inst = Installer(temp / "game", source="bmclapi")
        inst._resolve_source()
        check("installer-falls-back", inst.effective_source == "official")
    finally:
        sources._probe_latency = original_probe

    summary = sources.health_summary()
    check("health-summary-readable", "官方" in summary and "BMCLAPI" in summary, summary)

    print(f"RESULT: PASS ({len(CHECKS)} checks)")


if __name__ == "__main__":
    main()
