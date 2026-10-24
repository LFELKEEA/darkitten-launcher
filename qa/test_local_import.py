from __future__ import annotations

import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from darkitten.local_import import (
    LocalImportError,
    detect_local_import,
    import_version_folder,
    inspect_content_path,
    inspect_version_folder,
    install_local_contents,
)


def check(label: str, value: bool, detail: str = "") -> bool:
    print(f"{label}: {'OK' if value else 'FAIL'}" + (f" ({detail})" if detail else ""))
    return value


def version_data(version_id: str, inherits: str = "") -> dict:
    data = {
        "id": version_id,
        "type": "release",
        "mainClass": "net.minecraft.client.main.Main",
        "assets": "1.21",
        "assetIndex": {"id": "1.21", "url": "http://example.invalid/a.json"},
        "downloads": {"client": {"url": "", "size": 0, "sha1": ""}},
        "javaVersion": {"majorVersion": 21},
        "libraries": [],
    }
    if inherits:
        data["inheritsFrom"] = inherits
    return data


class FakeInstaller:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def install(self, version_id: str) -> None:
        self.calls.append(version_id)


def make_version_zip(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "versions/ZipCat/ZipCat.json",
            json.dumps(version_data("ZipCat"), ensure_ascii=False),
        )
        zf.writestr("versions/ZipCat/ZipCat.jar", b"jar")
        zf.writestr("versions/ZipCat/mods/demo.jar", b"mod")


def make_server_zip(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("CatServer/server.jar", b"server")
        zf.writestr("CatServer/server.properties", "server-port=25566\n")
        zf.writestr("CatServer/world/level.dat", b"level")


def make_mrpack(path: Path) -> None:
    manifest = {
        "game": "minecraft",
        "formatVersion": 1,
        "versionId": "1.0",
        "name": "PackCat",
        "dependencies": {"minecraft": "1.21.1"},
        "files": [],
    }
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("modrinth.index.json", json.dumps(manifest))
        zf.writestr("overrides/config/demo.toml", "x=1\n")


def make_mod(path: Path, name: str = "CatMod.jar") -> Path:
    target = path / name
    with zipfile.ZipFile(target, "w") as zf:
        zf.writestr("fabric.mod.json", json.dumps({"id": "catmod", "version": "1"}))
    return target


def main() -> None:
    ok = True
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        game = root / "game"
        version_dir = game / "versions" / "Base"
        version_dir.mkdir(parents=True)
        (version_dir / "Base.json").write_text(
            json.dumps(version_data("Base")), encoding="utf-8"
        )
        (version_dir / "Base.jar").write_bytes(b"base-jar")
        (version_dir / "mods").mkdir()
        (version_dir / "resourcepacks").mkdir()
        (version_dir / "shaderpacks").mkdir()

        mod = make_mod(root)
        result = install_local_contents([mod], game, "Base", isolated=True)
        ok &= check("mod_copied", (version_dir / "mods" / "CatMod.jar").is_file())
        ok &= check("mod_source_kept", mod.is_file())
        ok &= check("mod_count", result.imported_files == 1, str(result.imported_files))
        install_local_contents([mod], game, "Base", isolated=True)
        ok &= check(
            "mod_unique",
            (version_dir / "mods" / "CatMod (2).jar").is_file(),
            str(list((version_dir / "mods").iterdir())),
        )

        rp = root / "CatPack.zip"
        with zipfile.ZipFile(rp, "w") as zf:
            zf.writestr("pack.mcmeta", json.dumps({"pack": {"pack_format": 34}}))
            zf.writestr("assets/cat/textures/x.png", b"PNG")
        plan = inspect_content_path(rp)
        ok &= check("rp_plan_copy", plan.mode == "copy", plan.mode)
        install_local_contents([rp], game, "Base", isolated=True)
        ok &= check("rp_zip_kept", (version_dir / "resourcepacks" / "CatPack.zip").is_file())
        ok &= check(
            "rp_not_extracted",
            not (version_dir / "resourcepacks" / "assets").exists(),
        )

        sp = root / "CatShaders.zip"
        with zipfile.ZipFile(sp, "w") as zf:
            zf.writestr("shaders/final.fsh", "void main() {}")
        install_local_contents([sp], game, "Base", isolated=True)
        ok &= check("shader_zip_kept", (version_dir / "shaderpacks" / "CatShaders.zip").is_file())

        nested = root / "NestedPack.zip"
        with zipfile.ZipFile(nested, "w") as zf:
            zf.writestr("NestedPack/pack.mcmeta", "{}")
            zf.writestr("NestedPack/assets/a.txt", "A")
        plan = inspect_content_path(nested)
        ok &= check("nested_plan_extract", plan.mode == "extract_package", plan.mode)
        install_local_contents([nested], game, "Base", isolated=True)
        ok &= check(
            "nested_extracted",
            (version_dir / "resourcepacks" / "NestedPack" / "pack.mcmeta").is_file(),
        )

        bundle = root / "ModsOnly.zip"
        with zipfile.ZipFile(bundle, "w") as zf:
            zf.writestr("mods/a.jar", b"A")
            zf.writestr("mods/b.litemod", b"B")
            zf.writestr("readme.txt", "hello")
        plan = inspect_content_path(bundle)
        ok &= check("bundle_mod_mode", plan.mode == "extract_mods", plan.mode)
        install_local_contents([bundle], game, "Base", isolated=True)
        ok &= check("bundle_mod_a", (version_dir / "mods" / "a.jar").is_file())
        ok &= check("bundle_mod_b", (version_dir / "mods" / "b.litemod").is_file())

        content_bundle = root / "ContentBundle.zip"
        with zipfile.ZipFile(content_bundle, "w") as zf:
            zf.writestr("mods/bundle.jar", b"MOD")
            zf.writestr("resourcepacks/bundle.zip", b"RP")
            zf.writestr("shaderpacks/bundle.zip", b"SH")
            zf.writestr("config/skip.toml", "new=2\n")
        existing = version_dir / "config" / "skip.toml"
        existing.parent.mkdir(exist_ok=True)
        existing.write_text("old=1\n", encoding="utf-8")
        plan = inspect_content_path(content_bundle)
        ok &= check("content_bundle_mode", plan.mode == "extract_bundle", plan.mode)
        result = install_local_contents([content_bundle], game, "Base", isolated=True)
        ok &= check("content_bundle_mod", (version_dir / "mods" / "bundle.jar").is_file())
        ok &= check(
            "content_bundle_rp", (version_dir / "resourcepacks" / "bundle.zip").is_file()
        )
        ok &= check("content_bundle_skip", existing.read_text(encoding="utf-8") == "old=1\n")
        ok &= check("content_bundle_skip_count", result.skipped_files == 1, str(result.skipped_files))

        evil = root / "evil.zip"
        with zipfile.ZipFile(evil, "w") as zf:
            zf.writestr("../escape.txt", b"bad")
        try:
            inspect_content_path(evil)
            ok &= check("zip_slip_rejected", False)
        except LocalImportError:
            ok &= check("zip_slip_rejected", True)

        # 版本文件夹：直接复制、重写 JSON、重命名客户端 jar。
        source = root / "MyCatVersion"
        source.mkdir()
        (source / "MyCatVersion.json").write_text(
            json.dumps(version_data("MyCatVersion")), encoding="utf-8"
        )
        (source / "MyCatVersion.jar").write_bytes(b"client")
        (source / "mods").mkdir()
        (source / "mods" / "inside.jar").write_bytes(b"inside")
        folder_info = inspect_version_folder(source)
        ok &= check("folder_candidate", folder_info.primary.version_id == "MyCatVersion")
        installer = FakeInstaller()
        imported = import_version_folder(
            source, game, "FolderCat", installer=installer
        )
        target = game / "versions" / "FolderCat"
        ok &= check("folder_imported", target.is_dir())
        ok &= check("folder_installer", installer.calls == ["FolderCat"], str(installer.calls))
        ok &= check("folder_json_renamed", (target / "FolderCat.json").is_file())
        ok &= check("folder_json_id", json.loads((target / "FolderCat.json").read_text(encoding="utf-8"))["id"] == "FolderCat")
        ok &= check("folder_jar_renamed", (target / "FolderCat.jar").read_bytes() == b"client")
        ok &= check("folder_mods", (target / "mods" / "inside.jar").is_file())
        ok &= check("folder_no_staging", not any((game / "versions").glob(".dkl-folder-*")))

        try:
            import_version_folder(source, game, "FolderCat", installer=FakeInstaller())
            ok &= check("folder_collision_rejected", False)
        except LocalImportError:
            ok &= check("folder_collision_rejected", True)

        # 完整游戏目录：共享 libraries/assets 直接合并。
        full = root / "FullGame"
        full_version = full / "versions" / "SharedCat"
        full_version.mkdir(parents=True)
        (full_version / "SharedCat.json").write_text(
            json.dumps(version_data("SharedCat")), encoding="utf-8"
        )
        (full_version / "SharedCat.jar").write_bytes(b"jar")
        (full / "libraries" / "a" / "b.jar").parent.mkdir(parents=True)
        (full / "libraries" / "a" / "b.jar").write_bytes(b"lib")
        (full / "assets" / "indexes" / "21.json").parent.mkdir(parents=True)
        (full / "assets" / "indexes" / "21.json").write_bytes(b"{}")
        import_version_folder(full, game, "SharedCopy", installer=FakeInstaller())
        ok &= check("full_version", (game / "versions" / "SharedCopy" / "SharedCopy.json").is_file())
        ok &= check("full_lib", (game / "libraries" / "a" / "b.jar").is_file())
        ok &= check("full_asset", (game / "assets" / "indexes" / "21.json").is_file())

        # 统一识别
        kinds = {
            "jar": detect_local_import(mod).kind,
            "folder": detect_local_import(source).kind,
        }
        vz = root / "version.zip"
        make_version_zip(vz)
        kinds["version_zip"] = detect_local_import(vz).kind
        sv = root / "server.zip"
        make_server_zip(sv)
        kinds["server"] = detect_local_import(sv).kind
        mr = root / "pack.mrpack"
        make_mrpack(mr)
        kinds["modpack"] = detect_local_import(mr).kind
        ok &= check("detect_kinds", kinds == {
            "jar": "content",
            "folder": "version_folder",
            "version_zip": "version_zip",
            "server": "server",
            "modpack": "modpack",
        }, str(kinds))

        try:
            detect_local_import(root / "missing.jar")
            ok &= check("missing_rejected", False)
        except LocalImportError:
            ok &= check("missing_rejected", True)

    print("RESULT:", "PASS" if ok else "FAIL")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
