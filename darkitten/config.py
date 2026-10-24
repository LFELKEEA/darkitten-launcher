from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

APP_NAME = "Darkitten Launcher"
APP_NAME_EN = "Darkitten Launcher"
APP_SHORT_NAME = "DKL"
APP_TAGLINE = "恶魔小猫的秘密基地"
APP_AUTHOR = "LFELKEEA"
APP_EMAIL = "lfel278623@outlook.com"
APP_VERSION = "1.10.0"
APP_ID = "DarkittenLauncher"
DEFAULT_UPDATE_URL = "https://lfelkeea.github.io/darkitten-launcher/update.json"

if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent.parent

CONFIG_PATH = APP_DIR / "config.json"
LOG_DIR = APP_DIR / "logs"
LOG_FILE = LOG_DIR / "launcher.log"


def default_game_dir() -> str:
    """新安装默认把游戏目录放在启动器文件夹内。"""
    return str(APP_DIR / "minecraft")


@dataclass
class Config:
    game_dir: str = ""
    java_path: str = ""
    memory_mb: int = 4096
    jvm_args: str = ""
    # 游戏版本级覆盖：一键优化只影响当前实例，不改变全局默认值。
    version_memory: dict[str, int] = field(default_factory=dict)
    version_jvm_args: dict[str, str] = field(default_factory=dict)
    download_source: str = "auto"
    download_threads: int = 8
    theme: str = "dark"
    last_version: str = ""
    player_name: str = "Steve"
    window_width: int = 1180
    window_height: int = 760
    auto_complete: bool = True
    version_isolation: bool = True
    shortcut_offered: bool = False
    microsoft_client_id: str = ""
    microsoft_email: str = ""
    account: dict[str, Any] = field(default_factory=dict)
    skin_path: str = ""
    skin_variant: str = "classic"
    sakura_token: str = ""
    sakura_frpc_path: str = ""
    sakura_node_id: int = 0
    sakura_tunnel_id: int = 0
    update_url: str = DEFAULT_UPDATE_URL
    update_auto_check: bool = True
    update_skipped_version: str = ""
    update_last_check: str = ""
    feedback_last_seen: str = ""
    developer_unlocked: bool = False
    developer_page_token: str = ""
    # 游戏版本 -> 纯加载器配置。没有条目表示原版启动。
    version_loaders: dict[str, str] = field(default_factory=dict)
    # 1.9.14 之前加载器版本直接出现在首页。首次运行时把旧选择迁移成绑定，
    # 之后尊重用户手动选择的“原版启动”，不再自动改回来。
    loaders_migrated: bool = False

    def __post_init__(self) -> None:
        if not self.game_dir:
            self.game_dir = default_game_dir()

    @property
    def game_path(self) -> Path:
        return Path(os.path.expandvars(self.game_dir)).expanduser()

    def version_game_path(self, version_id: str) -> Path:
        from .paths import version_game_dir

        return version_game_dir(
            self.game_path, version_id, self.version_isolation
        )

    def mods_path(self, version_id: str = "") -> Path:
        return self.version_game_path(version_id) / "mods"

    def resourcepacks_path(self, version_id: str = "") -> Path:
        return self.version_game_path(version_id) / "resourcepacks"

    def shaderpacks_path(self, version_id: str = "") -> Path:
        return self.version_game_path(version_id) / "shaderpacks"

    def loader_for(self, version_id: str) -> str:
        return str(self.version_loaders.get(version_id) or "").strip()

    def memory_for(self, version_id: str) -> int:
        """当前实例实际使用的内存；没有单独优化时沿用全局设置。"""
        try:
            value = int(self.version_memory.get(str(version_id or ""), 0) or 0)
        except Exception:
            value = 0
        return value if value >= 512 else int(self.memory_mb)

    def jvm_args_for(self, version_id: str) -> str:
        key = str(version_id or "")
        if key in self.version_jvm_args:
            return str(self.version_jvm_args.get(key) or "")
        return str(self.jvm_args or "")

    def bind_loader(self, version_id: str, loader_id: str) -> None:
        version_id = str(version_id or "").strip()
        loader_id = str(loader_id or "").strip()
        if not version_id:
            return
        if loader_id:
            self.version_loaders[version_id] = loader_id
        else:
            self.version_loaders.pop(version_id, None)

    @classmethod
    def load(cls) -> "Config":
        data: dict = {}
        try:
            if CONFIG_PATH.exists():
                data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            data = {}

        known = {f.name for f in fields(cls)}
        clean = {k: v for k, v in data.items() if k in known}
        cfg = cls(**clean)

        # 1.6.x 及更早版本没有线程设置；旧配置里的 BMCLAPI 单源容易在
        # 镜像不稳定时拖慢整个安装，这里一次性迁移到智能加速。
        if "download_threads" not in data and cfg.download_source == "bmclapi":
            cfg.download_source = "auto"

        if not str(cfg.player_name).strip():
            cfg.player_name = "Steve"
        try:
            cfg.memory_mb = max(512, min(65536, int(cfg.memory_mb)))
        except Exception:
            cfg.memory_mb = 4096
        if cfg.theme not in ("light", "dark"):
            cfg.theme = "light"
        if cfg.download_source not in ("auto", "official", "bmclapi"):
            cfg.download_source = "auto"
        try:
            cfg.download_threads = max(1, min(16, int(cfg.download_threads)))
        except Exception:
            cfg.download_threads = 8
        cfg.version_isolation = bool(cfg.version_isolation)
        cfg.shortcut_offered = bool(cfg.shortcut_offered)
        cfg.microsoft_client_id = str(cfg.microsoft_client_id or "").strip()
        cfg.microsoft_email = str(cfg.microsoft_email or "").strip()
        if not isinstance(cfg.account, dict):
            cfg.account = {}
        cfg.skin_path = str(cfg.skin_path or "").strip()
        cfg.skin_variant = (
            "slim" if str(cfg.skin_variant or "").lower() == "slim" else "classic"
        )
        cfg.sakura_token = str(cfg.sakura_token or "").strip()
        cfg.sakura_frpc_path = str(cfg.sakura_frpc_path or "").strip()
        try:
            cfg.sakura_node_id = max(0, int(cfg.sakura_node_id))
        except Exception:
            cfg.sakura_node_id = 0
        try:
            cfg.sakura_tunnel_id = max(0, int(cfg.sakura_tunnel_id))
        except Exception:
            cfg.sakura_tunnel_id = 0
        # 旧版本留下过本机、临时托管和测试源。现在只保留官方 GitHub Pages
        # 地址，避免升级后仍然去请求已经失效的旧地址。
        cfg.update_url = DEFAULT_UPDATE_URL
        cfg.update_auto_check = bool(cfg.update_auto_check)
        cfg.update_skipped_version = str(
            cfg.update_skipped_version or ""
        ).strip()
        cfg.update_last_check = str(cfg.update_last_check or "").strip()
        cfg.feedback_last_seen = str(cfg.feedback_last_seen or "").strip()
        cfg.developer_unlocked = bool(cfg.developer_unlocked)
        cfg.developer_page_token = str(
            cfg.developer_page_token or ""
        ).strip()
        if not isinstance(cfg.version_loaders, dict):
            cfg.version_loaders = {}
        cfg.version_loaders = {
            str(key): str(value)
            for key, value in cfg.version_loaders.items()
            if str(key).strip() and str(value).strip()
        }
        if not isinstance(cfg.version_memory, dict):
            cfg.version_memory = {}
        clean_memory: dict[str, int] = {}
        for key, value in cfg.version_memory.items():
            try:
                size = int(value)
            except Exception:
                continue
            if str(key).strip() and 512 <= size <= 65536:
                clean_memory[str(key)] = size
        cfg.version_memory = clean_memory
        if not isinstance(cfg.version_jvm_args, dict):
            cfg.version_jvm_args = {}
        cfg.version_jvm_args = {
            str(key): str(value)
            for key, value in cfg.version_jvm_args.items()
            if str(key).strip()
        }
        cfg.loaders_migrated = bool(cfg.loaders_migrated)
        return cfg

    @property
    def sakura_access_token(self) -> str:
        from .secret import unprotect

        return unprotect(self.sakura_token)

    def set_sakura_token(self, token: str) -> None:
        from .secret import protect

        self.sakura_token = protect(str(token or "").strip())

    def save(self) -> None:
        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = CONFIG_PATH.with_name(CONFIG_PATH.name + ".tmp")
            tmp.write_text(
                json.dumps(asdict(self), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, CONFIG_PATH)
        except Exception:
            pass


def ensure_dirs() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    config = Config.load()
    try:
        config.game_path.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def append_log(message: str) -> None:
    """把启动器日志写入文件；失败时静默忽略。"""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        from datetime import datetime

        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with LOG_FILE.open("a", encoding="utf-8") as fp:
            fp.write(f"[{stamp}] {message}\n")
    except Exception:
        pass
