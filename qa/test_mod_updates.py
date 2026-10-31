from __future__ import annotations

import hashlib
import json
import tempfile
import zipfile
from pathlib import Path
from unittest import mock
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from darkitten.mod_updates import (  # noqa: E402
    ModUpdate,
    ModUpdateError,
    apply_mod_update,
    check_mod_update,
    check_mod_updates,
)
from darkitten.mods import (  # noqa: E402
    ModInfo,
    load_mod_meta,
    mod_meta_icon_path,
    mod_meta_json_path,
    read_mod,
    write_mod_meta,
)

CHECKS: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name} FAILED {detail}")
    CHECKS.append(name)
    print(f"{name}: OK")


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def make_mod_jar(path: Path, modid: str = "coolmod", *, icon: bytes = b"") -> None:
    payload = {
        "schemaVersion": 1,
        "id": modid,
        "version": "1.0.0",
        "name": "Cool Mod",
        "depends": {"minecraft": ">=1.20"},
    }
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("fabric.mod.json", json.dumps(payload))
        if icon:
            zf.writestr("icon.png", icon)


def modrinth_version(
    version_id: str,
    filename: str,
    content: bytes,
    *,
    number: str = "1.1.0",
    dependencies: list[dict] | None = None,
) -> dict:
    return {
        "id": version_id,
        "project_id": "proj-1",
        "version_number": number,
        "files": [
            {
                "primary": True,
                "url": "https://example.invalid/file.jar",
                "filename": filename,
                "size": len(content),
                "hashes": {"sha1": sha1(content)},
            }
        ],
        "dependencies": dependencies or [],
        "page_url": f"https://modrinth.com/mod/coolmod/version/{version_id}",
    }


def make_info(path: Path, *, enabled: bool = True) -> ModInfo:
    return ModInfo(
        path=path,
        name="Cool Mod",
        version="1.0.0",
        loader="Fabric",
        enabled=enabled,
        size=path.stat().st_size,
    )


def test_check_modrinth_update() -> None:
    work = Path(tempfile.mkdtemp(prefix="dkl-update-check-"))
    old_path = work / "coolmod-1.0.jar"
    old_bytes = b"old-mod-content"
    old_path.write_bytes(old_bytes)
    write_mod_meta(
        work,
        old_path.name,
        {
            "title": "Cool Mod",
            "project_id": "proj-1",
            "source": "modrinth",
            "slug": "coolmod",
            "version_id": "v1",
            "file_id": "v1",
            "file_name": old_path.name,
            "sha1": sha1(old_bytes),
        },
        b"OLDICON",
    )
    info = read_mod(old_path)

    new_bytes = b"new-mod-content"
    latest = modrinth_version(
        "v2",
        "coolmod-1.1.jar",
        new_bytes,
        dependencies=[{"project_id": "dep-1", "dependency_type": "required"}],
    )
    with mock.patch(
        "darkitten.mod_updates.modrinth.fetch_mod_versions",
        return_value=[latest],
    ):
        update = check_mod_update(info, "1.20.1", "Fabric")
    check("check-update-found", update is not None)
    assert update is not None
    check("check-update-source", update.source == "modrinth")
    check("check-update-label", update.version_label == "1.1.0")
    check("check-update-file", update.file_name == "coolmod-1.1.jar")
    check("check-update-size", update.size == len(new_bytes))
    check("check-update-sha1", update.sha1 == sha1(new_bytes))
    check("check-update-dependencies", len(update.dependencies) == 1)

    same_hash_path = work / "coolmod-1.0-copy.jar"
    same_hash_path.write_bytes(new_bytes)
    write_mod_meta(
        work,
        same_hash_path.name,
        {"project_id": "proj-1", "source": "modrinth"},
    )
    same_hash_info = make_info(same_hash_path)
    with mock.patch(
        "darkitten.mod_updates.modrinth.fetch_mod_versions",
        return_value=[latest],
    ):
        hash_match = check_mod_update(same_hash_info, "1.20.1", "Fabric")
    check("check-update-hash-equal", hash_match is None)

    # 已是最新版：当前 version_id 与最新版本 id 相同。
    meta = load_mod_meta(work, old_path.name)
    meta["file_id"] = "v2"
    write_mod_meta(work, old_path.name, meta)
    info_latest = read_mod(old_path)
    with mock.patch(
        "darkitten.mod_updates.modrinth.fetch_mod_versions",
        return_value=[latest],
    ):
        none_update = check_mod_update(info_latest, "1.20.1", "Fabric")
    check("check-update-up-to-date", none_update is None)


def test_hash_lookup_and_curseforge() -> None:
    work = Path(tempfile.mkdtemp(prefix="dkl-update-lookup-"))
    old_path = work / "mystery.jar"
    old_bytes = b"mystery-content"
    old_path.write_bytes(old_bytes)
    info = make_info(old_path)
    latest = modrinth_version("v9", "mystery-2.0.jar", b"new-mystery")

    with (
        mock.patch(
            "darkitten.mod_updates.modrinth.lookup_version_by_hash",
            return_value={"project_id": "proj-9", "id": "v8"},
        ),
        mock.patch(
            "darkitten.mod_updates.modrinth.fetch_mod_versions",
            return_value=[latest],
        ),
    ):
        update = check_mod_update(info, "1.20.1", "Fabric")
    check("lookup-update-found", update is not None)
    assert update is not None
    check("lookup-update-source", update.source == "modrinth")

    cf_work = Path(tempfile.mkdtemp(prefix="dkl-update-cf-"))
    cf_path = cf_work / "cfmod-1.0.jar"
    cf_path.write_bytes(b"cf-old")
    write_mod_meta(
        cf_work,
        cf_path.name,
        {
            "title": "CF Mod",
            "project_id": "12345",
            "source": "curseforge",
            "file_id": "100",
            "sha1": sha1(b"cf-old"),
        },
    )
    cf_info = read_mod(cf_path)
    cf_latest = {
        "id": "200",
        "version_number": "2.0.0",
        "files": [
            {
                "primary": True,
                "url": "https://example.invalid/cf.jar",
                "filename": "cfmod-2.0.jar",
                "size": 6,
                "hashes": {"sha1": sha1(b"cf-new")},
            }
        ],
        "dependencies": [],
        "page_url": "https://www.curseforge.com/minecraft/mc-mods/cfmod/files/200",
    }
    with mock.patch(
        "darkitten.mod_updates.cf.fetch_mod_versions",
        return_value=[cf_latest],
    ):
        cf_update = check_mod_update(cf_info, "1.20.1", "Forge")
    check("curseforge-update-found", cf_update is not None)
    assert cf_update is not None
    check("curseforge-update-source", cf_update.source == "curseforge")
    check("curseforge-update-label", cf_update.version_label == "2.0.0")
    check("curseforge-update-file", cf_update.file_name == "cfmod-2.0.jar")


def test_check_batch_errors() -> None:
    work = Path(tempfile.mkdtemp(prefix="dkl-update-batch-"))
    good = work / "good.jar"
    good.write_bytes(b"good")
    bad = work / "bad.jar"
    bad.write_bytes(b"bad")
    info_good = make_info(good)
    info_bad = make_info(bad)
    latest = modrinth_version("v2", "good-2.jar", b"good-new")

    def fake_fetch(project_id, *args, **kwargs):
        if project_id == "bad-project":
            raise RuntimeError("网络错误")
        return [latest]

    write_mod_meta(work, good.name, {"project_id": "good-project", "source": "modrinth"})
    write_mod_meta(work, bad.name, {"project_id": "bad-project", "source": "modrinth"})
    with mock.patch(
        "darkitten.mod_updates.modrinth.fetch_mod_versions",
        side_effect=fake_fetch,
    ):
        updates, errors = check_mod_updates([info_good, info_bad], "1.20.1", "Fabric")
    check("batch-update-count", len(updates) == 1, str(len(updates)))
    check("batch-error-count", len(errors) == 1, str(errors))
    check("batch-error-text", "网络错误" in errors[0], str(errors))


def jar_bytes(*, icon: bytes = b"") -> bytes:
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(
            "fabric.mod.json",
            json.dumps(
                {
                    "schemaVersion": 1,
                    "id": "coolmod",
                    "version": "1.2.0",
                    "name": "Cool Mod",
                }
            ),
        )
        if icon:
            zf.writestr("icon.png", icon)
    return buffer.getvalue()


def fake_downloader(content: bytes):
    def fake(version, dest_dir, *, progress_cb=None, cancel_event=None, threads=8):
        file = (version.get("files") or [{}])[0]
        dest = Path(dest_dir) / str(file.get("filename") or "downloaded.jar")
        dest.write_bytes(content)
        if progress_cb:
            progress_cb(100, "fake download")
        return dest

    return fake


def test_apply_same_name() -> None:
    work = Path(tempfile.mkdtemp(prefix="dkl-update-apply-same-"))
    old_path = work / "coolmod-1.1.jar"
    old_bytes = b"old-jar"
    old_path.write_bytes(old_bytes)
    write_mod_meta(
        work,
        old_path.name,
        {
            "title": "Cool Mod",
            "project_id": "proj-1",
            "source": "modrinth",
            "version_id": "v1",
            "file_id": "v1",
            "sha1": sha1(old_bytes),
            "icon_url": "",
        },
        b"OLDICON",
    )
    info = read_mod(old_path)
    new_bytes = b"new-jar"
    version = modrinth_version("v2", old_path.name, new_bytes, number="1.1.0")
    update = ModUpdate(
        mod=info,
        version=version,
        source="modrinth",
        version_label="1.1.0",
        file_name=old_path.name,
        size=len(new_bytes),
        sha1=sha1(new_bytes),
        page_url="https://modrinth.com/mod/coolmod/version/v2",
    )
    with mock.patch(
        "darkitten.mod_updates.modrinth.download_mod_file",
        fake_downloader(new_bytes),
    ):
        target = apply_mod_update(update, work)
    check("apply-same-path", target == old_path)
    check("apply-same-content", old_path.read_bytes() == new_bytes)
    meta = load_mod_meta(work, old_path.name)
    check("apply-same-meta-version", meta.get("file_id") == "v2", str(meta))
    check("apply-same-meta-sha1", meta.get("sha1") == sha1(new_bytes))
    check("apply-same-meta-source", meta.get("source") == "modrinth")
    icon = mod_meta_icon_path(work, old_path.name)
    check("apply-same-icon-kept", icon.is_file() and icon.read_bytes() == b"OLDICON")
    backups = list((work / ".dkl-meta" / "backups" / "mods").glob("*/*.jar"))
    check("apply-same-backup", len(backups) == 1, str(backups))
    check("apply-same-backup-content", backups[0].read_bytes() == old_bytes)
    check("apply-same-no-temp", not any((work / ".dkl-updates").glob("*")))


def test_apply_renamed() -> None:
    work = Path(tempfile.mkdtemp(prefix="dkl-update-apply-renamed-"))
    old_path = work / "coolmod-1.1.jar"
    old_bytes = b"old-jar"
    old_path.write_bytes(old_bytes)
    write_mod_meta(
        work,
        old_path.name,
        {
            "title": "Cool Mod",
            "project_id": "proj-1",
            "source": "modrinth",
            "file_id": "v1",
            "sha1": sha1(old_bytes),
            "icon_url": "",
        },
        b"OLDICON",
    )
    info = read_mod(old_path)
    new_bytes = jar_bytes(icon=b"NEWICON")
    new_name = "coolmod-1.2.jar"
    version = modrinth_version("v3", new_name, new_bytes, number="1.2.0")
    update = ModUpdate(
        mod=info,
        version=version,
        source="modrinth",
        version_label="1.2.0",
        file_name=new_name,
        size=len(new_bytes),
        sha1=sha1(new_bytes),
    )
    with mock.patch(
        "darkitten.mod_updates.modrinth.download_mod_file",
        fake_downloader(new_bytes),
    ):
        target = apply_mod_update(update, work)
    check("apply-renamed-path", target == work / new_name)
    check("apply-renamed-old-gone", not old_path.exists())
    check("apply-renamed-new-content", target.read_bytes() == new_bytes)
    check(
        "apply-renamed-old-meta-gone",
        not mod_meta_json_path(work, old_path.name).exists(),
    )
    meta = load_mod_meta(work, new_name)
    check("apply-renamed-meta", meta.get("file_id") == "v3", str(meta))
    icon = mod_meta_icon_path(work, new_name)
    check("apply-renamed-icon-new", icon.is_file())


def test_apply_disabled_and_rollback() -> None:
    work = Path(tempfile.mkdtemp(prefix="dkl-update-apply-disabled-"))
    old_path = work / "disabledmod.jar.disabled"
    old_bytes = b"disabled-old"
    old_path.write_bytes(old_bytes)
    info = make_info(old_path, enabled=False)
    new_bytes = b"disabled-new"
    version = modrinth_version("v5", "disabledmod-2.jar", new_bytes)
    update = ModUpdate(
        mod=info,
        version=version,
        source="modrinth",
        version_label="2.0",
        file_name="disabledmod-2.jar",
        size=len(new_bytes),
        sha1=sha1(new_bytes),
    )
    with mock.patch(
        "darkitten.mod_updates.modrinth.download_mod_file",
        fake_downloader(new_bytes),
    ):
        target = apply_mod_update(update, work)
    check("apply-disabled-name", target.name == "disabledmod-2.jar.disabled")
    check("apply-disabled-content", target.read_bytes() == new_bytes)
    check("apply-disabled-old-gone", not old_path.exists())

    fail_work = Path(tempfile.mkdtemp(prefix="dkl-update-apply-fail-"))
    fail_path = fail_work / "failmod.jar"
    fail_bytes = b"fail-old"
    fail_path.write_bytes(fail_bytes)
    fail_info = make_info(fail_path)
    bad_version = modrinth_version("v6", "failmod-2.jar", b"bad-new")
    bad_update = ModUpdate(
        mod=fail_info,
        version=bad_version,
        source="modrinth",
        version_label="2.0",
        file_name="failmod-2.jar",
        size=8,
        sha1="0" * 40,
    )
    try:
        with mock.patch(
            "darkitten.mod_updates.modrinth.download_mod_file",
            fake_downloader(b"bad-new"),
        ):
            apply_mod_update(bad_update, fail_work)
    except ModUpdateError as exc:
        check("apply-rollback-raises", "校验失败" in str(exc), str(exc))
    else:
        raise AssertionError("apply_mod_update should have failed")
    check("apply-rollback-old-restored", fail_path.is_file())
    check("apply-rollback-old-content", fail_path.read_bytes() == fail_bytes)
    check("apply-rollback-no-new", not (fail_work / "failmod-2.jar").exists())
    check("apply-rollback-no-temp", not any((fail_work / ".dkl-updates").glob("*")))


def main() -> None:
    test_check_modrinth_update()
    test_hash_lookup_and_curseforge()
    test_check_batch_errors()
    test_apply_same_name()
    test_apply_renamed()
    test_apply_disabled_and_rollback()
    print(f"\n{len(CHECKS)} checks passed")


if __name__ == "__main__":
    main()
