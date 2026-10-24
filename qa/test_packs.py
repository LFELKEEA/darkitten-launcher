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

from darkitten.packs import (
    PackError,
    PackOptions,
    export_pack,
    import_pack,
    inspect_pack,
    normalize_version_id,
    unique_version_id,
)
from darkitten.ui.skin_preview import rotate_model_point


def make_version(game_dir: Path, version_id: str) -> Path:
    path = game_dir / "versions" / version_id / f"{version_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "id": version_id,
                "type": "release",
                "mainClass": "net.minecraft.client.main.Main",
                "assets": "1.21",
                "assetIndex": {"id": "1.21", "url": "http://example.invalid/a.json"},
                "downloads": {
                    "client": {
                        "url": "http://example.invalid/client.jar",
                        "size": 10,
                        "sha1": "",
                    }
                },
                "javaVersion": {"majorVersion": 21},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


class FakeInstaller:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def install(self, version_id: str) -> None:
        self.calls.append(version_id)


def check(label: str, value: bool, detail: str = "") -> bool:
    print(f"{label}: {'OK' if value else 'FAIL'}" + (f" ({detail})" if detail else ""))
    return value


def main() -> None:
    ok = True
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        source = root / "source-game"
        make_version(source, "TestPack")
        run = source / "versions" / "TestPack"
        (run / "mods").mkdir(parents=True, exist_ok=True)
        (run / "mods" / "demo.jar").write_bytes(b"jar-data")
        (run / "mods" / "disabled.jar.disabled").write_bytes(b"off")
        (run / "config").mkdir(parents=True, exist_ok=True)
        (run / "config" / "demo.toml").write_text("value=1", encoding="utf-8")
        (run / "defaultconfigs").mkdir(parents=True, exist_ok=True)
        (run / "defaultconfigs" / "default.toml").write_text("x=1", encoding="utf-8")
        (run / "resourcepacks").mkdir(parents=True, exist_ok=True)
        (run / "resourcepacks" / "pack.zip").write_bytes(b"zip")
        (run / "saves" / "world").mkdir(parents=True, exist_ok=True)
        (run / "saves" / "world" / "level.dat").write_bytes(b"level")

        pack_path = root / "pack.dklpack"
        export_pack(
            source,
            "TestPack",
            pack_path,
            name="猫猫测试包",
            description="只有一只猫",
            options=PackOptions(),
            isolated=True,
        )
        ok &= check("pack_created", pack_path.is_file())
        info = inspect_pack(pack_path)
        ok &= check("pack_name", info.name == "猫猫测试包", info.name)
        ok &= check("mods_count", info.counts.get("mods") == 2, str(info.counts))
        ok &= check("config_count", info.counts.get("config") == 1, str(info.counts))
        ok &= check(
            "defaultconfigs_count",
            info.counts.get("defaultconfigs") == 1,
            str(info.counts),
        )
        ok &= check("optional_excluded", not info.counts.get("resourcepacks"), str(info.counts))
        ok &= check("saves_excluded", not info.counts.get("saves"), str(info.counts))
        ok &= check("total_files", info.total_files == 5, str(info.total_files))

        target = root / "target-game"
        installer = FakeInstaller()
        result = import_pack(
            pack_path,
            target,
            "RestoredCat",
            isolated=True,
            installer=installer,
        )
        target_run = target / "versions" / "RestoredCat"
        ok &= check("installer_called", installer.calls == ["RestoredCat"], str(installer.calls))
        ok &= check("mod_restored", (target_run / "mods" / "demo.jar").read_bytes() == b"jar-data")
        ok &= check("disabled_restored", (target_run / "mods" / "disabled.jar.disabled").is_file())
        ok &= check("config_restored", (target_run / "config" / "demo.toml").is_file())
        ok &= check("optional_not_restored", not (target_run / "resourcepacks").exists())
        ok &= check("imported_count", result.imported_files == 4, str(result.imported_files))
        restored_json = json.loads(
            (target / "versions" / "RestoredCat" / "RestoredCat.json").read_text(
                encoding="utf-8"
            )
        )
        ok &= check("version_id_rewritten", restored_json.get("id") == "RestoredCat")

        # 重名导入 + 覆盖策略
        overwrite_installer = FakeInstaller()
        result2 = import_pack(
            pack_path,
            target,
            "RestoredCat",
            resume=True,
            overwrite=True,
            isolated=True,
            installer=overwrite_installer,
        )
        ok &= check("resume_overwrite", result2.imported_files == 4, str(result2.imported_files))

        # 路径穿越拒绝
        bad = root / "bad.dklpack"
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr("manifest.json", json.dumps({"format": "dklpack", "format_version": 1}))
            zf.writestr("version/version.json", json.dumps({"id": "Bad"}))
            zf.writestr("../evil.txt", "oops")
        try:
            inspect_pack(bad)
            ok &= check("path_traversal_rejected", False)
        except PackError:
            ok &= check("path_traversal_rejected", True)

        # 版本名安全处理
        ok &= check(
            "normalize_name",
            normalize_version_id("猫猫 1.21/../x") == "猫猫 1.21-..-x",
            normalize_version_id("猫猫 1.21/../x"),
        )
        unique = unique_version_id(target, "RestoredCat")
        ok &= check("unique_name", unique != "RestoredCat" and unique.startswith("RestoredCat"))

    # 3D 预览旋转中心
    pivot = rotate_model_point((0.0, 16.0, 0.0), 37.0, 22.0)
    ok &= check("pivot_fixed", all(abs(v) < 1e-9 for v in pivot), str(pivot))
    top = rotate_model_point((0.0, 24.0, 0.0), 90.0, 0.0)
    ok &= check("pivot_yaw", abs(top[0]) < 1e-9 and abs(top[1] - 8.0) < 1e-9, str(top))
    head = rotate_model_point((0.0, 8.0, 0.0), 0.0, 90.0)
    ok &= check("pivot_pitch", abs(head[1]) < 1e-9 and abs(head[2] + 8.0) < 1e-9, str(head))

    print("RESULT:", "PASS" if ok else "FAIL")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
