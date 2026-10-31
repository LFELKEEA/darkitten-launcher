from __future__ import annotations

import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from darkitten import config as config_module
from darkitten.config import Config
from darkitten.optimize import (
    GRAPHICS_PROFILES,
    apply_graphics,
    apply_optimization,
    build_graphics_plan,
    build_optimization_plan,
    catalog_for,
    graphics_profile,
    read_options,
    recommend_memory_mb,
    recommended_jvm_args,
    restore_optimization,
    state_summary,
    suggest_graphics_profile,
)
from darkitten.versions import VersionSummary

CHECKS: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name} FAILED {detail}")
    CHECKS.append(name)
    print(f"{name}: OK")


def make_versions(base: str = "1.21.1") -> list[VersionSummary]:
    profile = f"fabric-loader-0.19.5-{base}"
    return [
        VersionSummary(id=base, base_id=base),
        VersionSummary(
            id=profile,
            base_id=base,
            loader="Fabric",
            is_loader_profile=True,
        ),
    ]


def main() -> None:
    original_path = config_module.CONFIG_PATH
    root = Path(tempfile.mkdtemp(prefix="dkl_opt_test_"))
    config_module.CONFIG_PATH = root / "config.json"
    try:
        game = root / "game"
        game.mkdir()
        config = Config(game_dir=str(game), update_auto_check=False)
        versions = make_versions()
        config.version_loaders["1.21.1"] = "fabric-loader-0.19.5-1.21.1"

        # 推荐值范围
        check("memory_low", recommend_memory_mb(4096) >= 1024)
        check("memory_high", recommend_memory_mb(65536, 200, "Fabric") <= 16384)
        check("memory_bounded", recommend_memory_mb(8192, 0, "") < 8192)
        check("jvm_g1", "UseG1GC" in recommended_jvm_args())

        class Gpu:
            def __init__(self, name: str, kind: str) -> None:
                self.name = name
                self.vram_mb = 8192
                self.kind = kind

        check(
            "gpu_quality",
            suggest_graphics_profile([Gpu("NVIDIA GeForce RTX 5060", "nvidia")])
            == "quality",
        )
        check(
            "gpu_performance",
            suggest_graphics_profile([Gpu("Intel UHD Graphics", "intel")])
            == "performance",
        )
        check("profile_fallback", graphics_profile("missing").key == "balanced")
        check("graphics_profiles", len(GRAPHICS_PROFILES) == 3)

        check("catalog_fabric", any(item.slug == "sodium" for item in catalog_for("Fabric")))
        check("catalog_forge", any(item.slug == "embeddium" for item in catalog_for("Forge")))
        check(
            "catalog_neoforge",
            any(item.slug == "embeddium" for item in catalog_for("NeoForge")),
        )
        check("catalog_vanilla", catalog_for("") == ())

        mods = config.version_game_path("1.21.1") / "mods"
        mods.mkdir(parents=True, exist_ok=True)
        (mods / "sodium-fabric-0.6.13.jar").write_bytes(b"sodium")
        (mods / "lithium-fabric-0.15.jar.disabled").write_bytes(b"lithium")

        plan = build_optimization_plan(
            "1.21.1", config, versions, total_ram_mb=16384, gpus=[]
        )
        check("plan_loader", plan.loader_label == "Fabric", plan.loader_label)
        check("plan_mc", plan.mc_version == "1.21.1")
        check("plan_present_sodium", any(item.slug == "sodium" for item in plan.present))
        check(
            "plan_disabled_present",
            any(item.slug == "lithium" for item in plan.present),
        )
        check("plan_missing", any(item.slug == "modernfix" for item in plan.missing))
        check("plan_memory", 1024 <= plan.recommended_memory_mb <= 16384)

        # 只写版本级启动参数，不联网安装 Mod
        result = apply_optimization(
            plan,
            config,
            install_mods=False,
            apply_memory=True,
            apply_jvm=True,
            threads=4,
        )
        check("apply_memory_value", config.version_memory.get("1.21.1") == plan.recommended_memory_mb)
        check("apply_jvm_value", config.version_jvm_args.get("1.21.1") == plan.recommended_jvm_args)
        check("apply_result", result.memory_mb == plan.recommended_memory_mb)
        check("config_persisted", (root / "config.json").is_file())
        state = state_summary("1.21.1", config)
        check("state_active", bool(state.get("active")))
        check("state_settings", isinstance(state.get("settings"), dict))

        # 画面参数：保留无关设置
        options_path = config.version_game_path("1.21.1") / "options.txt"
        options_path.write_text(
            "gamma:0.5\nrenderDistance:4\nfov:0.0\n", encoding="utf-8"
        )
        graphics = build_graphics_plan(
            "1.21.1", config, versions, "performance", gpus=[]
        )
        gr = apply_graphics(
            graphics,
            config,
            apply_options=True,
            install_iris=False,
            install_shader=False,
            threads=4,
        )
        values = read_options(options_path)
        check("graphics_written", gr.options_written and values.get("graphics") == "fast")
        check("graphics_distance", values.get("renderDistance") == "6")
        check("graphics_kept_gamma", values.get("gamma") == "0.5")
        check("graphics_kept_fov", values.get("fov") == "0.0")
        check("graphics_state", state_summary("1.21.1", config).get("graphics_profile") == "performance")

        # 撤销：恢复参数与 options.txt，且不删除文件
        restored = restore_optimization("1.21.1", config)
        check("restore_settings", restored.settings_restored)
        check("restore_options", restored.options_restored)
        check("restore_memory_key", "1.21.1" not in config.version_memory)
        check("restore_jvm_key", "1.21.1" not in config.version_jvm_args)
        check("restore_config_default", config.memory_for("1.21.1") == config.memory_mb)
        check(
            "restore_options_content",
            options_path.read_text(encoding="utf-8")
            == "gamma:0.5\nrenderDistance:4\nfov:0.0\n",
        )
        check("restore_inactive", not state_summary("1.21.1", config))

        # 原版实例：不安装 Mod，但仍能写参数
        vanilla_versions = [VersionSummary(id="1.20.6", base_id="1.20.6")]
        vanilla_plan = build_optimization_plan(
            "1.20.6", config, vanilla_versions, total_ram_mb=8192, gpus=[]
        )
        check("vanilla_no_mods", not vanilla_plan.missing and not vanilla_plan.present)
        check("vanilla_warning", any("原版" in item for item in vanilla_plan.warnings))
        vanilla_result = apply_optimization(
            vanilla_plan, config, install_mods=True, apply_memory=True, apply_jvm=False
        )
        check("vanilla_memory", config.version_memory.get("1.20.6") == vanilla_plan.recommended_memory_mb)
        check("vanilla_no_installed", not vanilla_result.installed)
        restore_optimization("1.20.6", config)

        print(f"ALL {len(CHECKS)} OPTIMIZE CHECKS PASSED")
    finally:
        config_module.CONFIG_PATH = original_path


if __name__ == "__main__":
    main()
