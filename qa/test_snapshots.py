from __future__ import annotations

import json
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from darkitten.snapshots import (  # noqa: E402
    SnapshotError,
    create_snapshot,
    list_snapshots,
    restore_snapshot,
    snapshot_dir,
)

CHECKS: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name} FAILED {detail}")
    CHECKS.append(name)
    print(f"{name}: OK")


def main() -> None:
    temp = Path(tempfile.mkdtemp(prefix="dkl-snapshot-"))
    game = temp / "versions" / "1.20.1-Fabric"
    (game / "saves" / "生存世界").mkdir(parents=True)
    (game / "saves" / "生存世界" / "level.dat").write_bytes(b"level-data")
    (game / "saves" / "生存世界" / "session.lock").write_bytes(b"lock")
    (game / "saves" / "生存世界" / "region").mkdir()
    (game / "saves" / "生存世界" / "region" / "r.0.0.mca").write_bytes(b"region" * 100)
    (game / "config").mkdir()
    (game / "config" / "sodium-options.json").write_text('{"quality": 2}', encoding="utf-8")
    (game / "options.txt").write_text("fov:0.0\nlang:zh_cn\n", encoding="utf-8")
    (game / "servers.dat").write_bytes(b"servers")

    empty = temp / "empty-version"
    empty.mkdir()
    try:
        create_snapshot(empty, "empty")
        empty_ok = False
    except SnapshotError:
        empty_ok = True
    check("empty-dir-rejected", empty_ok)

    info = create_snapshot(game, "1.20.1-Fabric", note="测试快照")
    check("snapshot-created", info.path.is_file())
    check("snapshot-in-version", info.path.parent == snapshot_dir(game))
    check("snapshot-counts", info.files == 5, str(info.files))
    check("snapshot-note", info.note == "测试快照")

    with zipfile.ZipFile(info.path) as archive:
        names = set(archive.namelist())
        meta = json.loads(archive.read("snapshot.json").decode("utf-8"))
    check("snapshot-has-world", "saves/生存世界/level.dat" in names, str(names))
    check("snapshot-has-config", "config/sodium-options.json" in names)
    check("snapshot-has-options", "options.txt" in names)
    check("snapshot-skips-session-lock", "saves/生存世界/session.lock" not in names)
    check("snapshot-meta-version", meta.get("version_id") == "1.20.1-Fabric")

    listed = list_snapshots(game)
    check("list-snapshots", len(listed) == 1 and listed[0].path == info.path)
    check("list-snapshot-size", listed[0].bytes > 0)
    check("created-text", ":" in listed[0].created_text)

    # 恢复：修改、删除后应还原，快照外的新文件保留。
    (game / "saves" / "生存世界" / "level.dat").write_bytes(b"broken")
    (game / "config" / "sodium-options.json").unlink()
    (game / "config" / "new-file.json").write_text("keep", encoding="utf-8")
    restored = restore_snapshot(info.path, game)
    check("restore-count", restored == 5, str(restored))
    check(
        "restore-world",
        (game / "saves" / "生存世界" / "level.dat").read_bytes() == b"level-data",
    )
    check(
        "restore-config",
        (game / "config" / "sodium-options.json").read_text(encoding="utf-8")
        == '{"quality": 2}',
    )
    check("restore-keeps-new-files", (game / "config" / "new-file.json").is_file())
    check("restore-no-metadata-written", not (game / "snapshot.json").exists())

    # 新快照应排在旧快照前面。
    time.sleep(0.02)
    info2 = create_snapshot(game, "1.20.1-Fabric", note="second")
    listed = list_snapshots(game)
    check("list-snapshots-sorted", listed[0].path == info2.path, str([p.path.name for p in listed]))

    # 恶意路径必须拒绝，不能在游戏目录外写文件。
    bad = temp / "bad.zip"
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr("../evil.txt", b"evil")
    try:
        restore_snapshot(bad, game)
        unsafe_ok = False
    except SnapshotError:
        unsafe_ok = True
    check("unsafe-path-rejected", unsafe_ok)
    check("unsafe-path-not-written", not (temp / "evil.txt").exists())

    # 非 ZIP 文件按空快照处理，不应崩溃列表。
    (snapshot_dir(game) / "broken.zip").write_bytes(b"not a zip")
    listed = list_snapshots(game)
    check("broken-snapshot-listed", len(listed) == 3, str(len(listed)))

    # 自定义日志回调。
    logs: list[str] = []
    create_snapshot(game, "1.20.1-Fabric", log=logs.append)
    check("log-callback", any("快照已创建" in line for line in logs), str(logs))

    print(f"RESULT: PASS ({len(CHECKS)} checks)")


if __name__ == "__main__":
    main()
