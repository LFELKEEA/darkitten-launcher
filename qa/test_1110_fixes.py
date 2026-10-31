from __future__ import annotations

import json
import sys
import tempfile
import zipfile
from pathlib import Path
from types import SimpleNamespace

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from darkitten.curseforge_pack import import_curseforge_pack  # noqa: E402
import darkitten.installer as installer_module  # noqa: E402
from darkitten.installer import Installer  # noqa: E402
from darkitten.modrinth_pack import import_modrinth_pack  # noqa: E402
from darkitten.packs import loader_stage_percent  # noqa: E402


def check(label: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{label} failed {detail}")
    print(f"ok  {label}" + (f" · {detail}" if detail else ""))


class _StopAfterLoader(Exception):
    pass


def _fake_loader(_game_dir, _mc, _kind, _version, **kwargs):
    progress = kwargs.get("progress_cb")
    if progress is not None:
        progress(100, "加载器完成")
    raise _StopAfterLoader()


def _library(name: str, path: str, size: int) -> dict:
    return {
        "name": name,
        "downloads": {
            "artifact": {
                "path": path,
                "url": f"https://example.test/{path}",
                "sha1": "",
                "size": size,
            }
        },
    }


def _make_modrinth(root: Path) -> Path:
    manifest = {
        "game": "minecraft",
        "formatVersion": 1,
        "versionId": "1.0.0",
        "name": "Progress Pack",
        "dependencies": {
            "minecraft": "1.21.11",
            "fabric-loader": "0.19.5",
        },
        "files": [],
    }
    path = root / "progress.mrpack"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("modrinth.index.json", json.dumps(manifest))
        zf.writestr("overrides/config/test.txt", "test")
    return path


def _make_curseforge(root: Path) -> Path:
    manifest = {
        "manifestType": "minecraftModpack",
        "manifestVersion": 1,
        "name": "Progress CF Pack",
        "version": "1.0.0",
        "minecraft": {
            "version": "1.21.11",
            "modLoaders": [{"id": "fabric-0.19.5", "primary": True}],
        },
        "files": [],
    }
    path = root / "progress-cf.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("overrides/config/test.txt", "test")
    return path


def main() -> int:
    # 加载器安装映射到总进度的 3%–45%，不能再被 /100 压成 3%。
    check("loader stage start", loader_stage_percent(0) == 3)
    check("loader stage half", loader_stage_percent(50) == 24)
    check("loader stage 82", loader_stage_percent(82) == 37)
    check("loader stage end", loader_stage_percent(100) == 45)
    check("loader stage clamp low", loader_stage_percent(-20) == 3)
    check("loader stage clamp high", loader_stage_percent(140) == 45)

    with tempfile.TemporaryDirectory(prefix="dkl_pack_progress_") as temp:
        root = Path(temp)
        mr_pack = _make_modrinth(root)
        mr_progress: list[tuple[int, str]] = []
        try:
            import_modrinth_pack(
                mr_pack,
                root / "mr-game",
                "MR",
                progress_cb=lambda p, m: mr_progress.append((p, m)),
                loader_installer=_fake_loader,
                download_func=lambda *a, **k: 0,
            )
        except _StopAfterLoader:
            pass
        check(
            "modrinth loader progress reaches 45",
            bool(mr_progress) and mr_progress[-1][0] == 45,
            str(mr_progress[-1] if mr_progress else None),
        )

        cf_pack = _make_curseforge(root)
        cf_progress: list[tuple[int, str]] = []
        try:
            import_curseforge_pack(
                cf_pack,
                root / "cf-game",
                "CF",
                progress_cb=lambda p, m: cf_progress.append((p, m)),
                loader_installer=_fake_loader,
                file_fetcher=lambda *a, **k: {},
            )
        except _StopAfterLoader:
            pass
        check(
            "curseforge loader progress reaches 45",
            bool(cf_progress) and cf_progress[-1][0] == 45,
            str(cf_progress[-1] if cf_progress else None),
        )

    # 依赖库下载：大文件先下，单文件连接数按体积收敛。
    check("connections tiny", Installer._library_connections(100_000, 12) == 1)
    check("connections below 1M", Installer._library_connections(1_048_575, 12) == 1)
    check("connections at 1M", Installer._library_connections(1_048_576, 12) == 2)
    check("connections medium", Installer._library_connections(3_000_000, 12) == 2)
    check("connections at 4M", Installer._library_connections(4_194_304, 12) == 4)
    check("connections huge", Installer._library_connections(24_000_000, 12) == 4)
    check("connections respects low segments", Installer._library_connections(8_000_000, 2) == 2)

    with tempfile.TemporaryDirectory(prefix="dkl_lib_order_") as temp:
        game = Path(temp)
        installer = Installer(game, source="official", threads=16)
        calls: list[tuple[str, int, int]] = []
        events: list[tuple[int, str]] = []

        def fake_download(url, dest, **kwargs):
            dest = Path(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            size = int(kwargs.get("size") or 0)
            dest.write_bytes(b"x" * min(size, 16))
            calls.append(
                (
                    dest.name,
                    int(kwargs.get("connections") or 0),
                    size,
                )
            )
            return True

        installer._download_file = fake_download
        installer.progress_cb = lambda p, m: events.append((p, m))
        resolved = SimpleNamespace(
            libraries=[
                _library("test:small:1.0", "test/small-1.0.jar", 100_000),
                _library("test:huge:1.0", "test/huge-1.0.jar", 8_000_000),
                _library("test:medium:1.0", "test/medium-1.0.jar", 2_000_000),
            ]
        )

        # 单线程执行池，用于观察提交顺序；生产环境仍是 20 个并发 worker。
        real_pool = installer_module.ThreadPoolExecutor

        class _SerialPool(real_pool):
            def __init__(self, max_workers=None, *args, **kwargs):
                super().__init__(max_workers=1, *args, **kwargs)

        installer_module.ThreadPoolExecutor = _SerialPool
        try:
            installer._install_libraries(resolved)
        finally:
            installer_module.ThreadPoolExecutor = real_pool

        check("library count", len(calls) == 3, str(len(calls)))
        check("largest library first", calls[0][0] == "huge-1.0.jar", str(calls))
        check("huge library connections", calls[0][1] == 4, str(calls[0]))
        check("medium library connections", calls[1][1] == 2, str(calls[1]))
        check("small library connections", calls[2][1] == 1, str(calls[2]))
        check(
            "library progress monotonic",
            all(
                events[index][0] <= events[index + 1][0]
                for index in range(len(events) - 1)
            ),
            str(events),
        )
        check(
            "library progress finishes",
            events[-1] == (36, "依赖库就绪"),
            str(events[-1] if events else None),
        )
        check(
            "library files written",
            all(
                (game / "libraries" / relative).is_file()
                for relative in (
                    "test/small-1.0.jar",
                    "test/huge-1.0.jar",
                    "test/medium-1.0.jar",
                )
            ),
        )

    print("all 1.11.0 fix checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
