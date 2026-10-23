from __future__ import annotations

import ctypes
import shlex
import subprocess
import sys
import threading
import time
import uuid
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from .accounts import Account, offline_uuid, valid_player_name
from .config import APP_NAME, APP_VERSION, Config
from .java import JavaInfo, detect_java_installs, pick_java
from .paths import version_game_dir
from .versions import (
    OS_NAME,
    ResolvedVersion,
    active_library_artifacts,
    active_natives,
    asset_index_path,
    assets_dir,
    check_installation,
    client_jar_path,
    features_for,
    libraries_dir,
    natives_dir,
    resolve_version,
    rules_allowed,
)

CREATE_NO_WINDOW = 0x08000000 if OS_NAME == "windows" else 0
SYSTEM_SEPARATOR = ";" if OS_NAME == "windows" else ":"


class LaunchError(Exception):
    pass


class MissingFilesError(LaunchError):
    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__("游戏文件不完整：" + "、".join(missing[:5]))


@dataclass
class LaunchPlan:
    command: list[str]
    cwd: str
    java: JavaInfo
    version: ResolvedVersion
    log_file: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def command_line(self) -> str:
        return subprocess.list2cmdline(self.command)


def redacted_command_line(command: list[str]) -> str:
    """生成可以安全写入日志的命令行，隐藏访问令牌。"""
    result: list[str] = []
    hide_next = False
    secret_flags = {"--accessToken", "--auth_session", "--clientid"}
    for arg in command:
        if hide_next:
            result.append("***")
            hide_next = False
            continue
        result.append(arg)
        if arg in secret_flags:
            hide_next = True
    return subprocess.list2cmdline(result)


def _flatten(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(_flatten(item))
        return result
    return []


def _substitute(value: str, mapping: dict[str, str]) -> str:
    result = value
    for key, replacement in mapping.items():
        result = result.replace("${" + key + "}", replacement)
    return result


def _unresolved(value: str) -> list[str]:
    import re

    return re.findall(r"\$\{([^}]+)\}", value)


def _split_legacy_args(template: str, mapping: dict[str, str]) -> list[str]:
    """先分词再替换，避免 Windows 路径中的反斜杠被 shlex 当成转义符。"""
    try:
        tokens = shlex.split(template, posix=False)
    except ValueError:
        tokens = template.split()
    result: list[str] = []
    for token in tokens:
        if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
            token = token[1:-1]
        result.append(_substitute(token, mapping))
    return result


def _extract_args(arguments: list[Any], mapping: dict[str, str]) -> list[str]:
    result: list[str] = []
    features = features_for()
    for item in arguments:
        if isinstance(item, str):
            result.append(_substitute(item, mapping))
            continue
        if not isinstance(item, dict):
            continue
        if not rules_allowed(item.get("rules"), features):
            continue
        for value in _flatten(item.get("value")):
            result.append(_substitute(value, mapping))
    return result


def _ignore_client_jar(args: list[str], jar_name: str) -> list[str]:
    """把真实的客户端 jar 补进 Forge/NeoForge 的 ignoreList。

    安装器写入的是 ``-DignoreList=client-extra,${version_name}.jar``，官方
    启动器会把客户端 jar 复制成 ``<版本名>.jar`` 来匹配。我们直接复用父版本
    的 ``1.21.1.jar``，若不补上真实文件名，ModLauncher 会把它和 NeoForge 的
    ``minecraft`` 模块同时加载，导致两个模块导出相同包而启动失败。
    """
    if not jar_name:
        return args
    prefix = "-DignoreList="
    lowered = jar_name.lower()
    result = list(args)
    changed = False
    for index, arg in enumerate(args):
        if not arg.startswith(prefix):
            continue
        values = [item for item in arg[len(prefix) :].split(",") if item]
        if any(item.lower() == lowered for item in values):
            continue
        values.append(jar_name)
        result[index] = prefix + ",".join(values)
        changed = True
    return result if changed else args


def _java_info_for(
    required_major: int, cfg: Config, installs: list[JavaInfo] | None = None
) -> JavaInfo:
    pool = installs
    if not pool:
        pool = detect_java_installs(cfg.java_path)
    chosen = pick_java(pool, required_major, cfg.java_path) if pool else None
    if chosen is None:
        raise LaunchError(
            "没有找到可用的 Java。请到“设置”中选择 java.exe，或安装 Java 运行环境。"
        )
    if required_major and chosen.major and chosen.major < required_major:
        raise LaunchError(
            f"该版本需要 Java {required_major} 或更高版本，"
            f"当前选择的是 Java {chosen.major}。请到设置中切换 Java。"
        )
    return chosen


def _check_files(game_dir: Path, resolved: ResolvedVersion) -> list[str]:
    missing = check_installation(game_dir, resolved, check_assets=False)
    for native in active_natives(resolved):
        if not (libraries_dir(game_dir) / native.artifact.path).exists():
            missing.append(f"运行库 {native.artifact.name}")
    return missing


def prepare_launch(
    game_dir: Path,
    version_id: str,
    cfg: Config,
    installs: list[JavaInfo] | None = None,
    java_info: JavaInfo | None = None,
    account: Account | None = None,
    server_address: str = "",
) -> LaunchPlan:
    game_dir = Path(game_dir)
    run_dir = version_game_dir(game_dir, version_id, cfg.version_isolation)
    account = account or Account.offline(cfg.player_name.strip())

    if account.is_msa:
        name = account.name.strip()
        if not name or not account.uuid or not account.mc_access_token:
            raise LaunchError("正版登录状态无效，请重新登录 Microsoft 账户。")
        auth_uuid = account.uuid
        access_token = account.mc_access_token
        auth_xuid = account.xuid
        user_type = "msa"
        client_id = cfg.microsoft_client_id
    else:
        name = cfg.player_name.strip()
        if not valid_player_name(name):
            raise LaunchError("玩家名称需为 3-16 位字母、数字或下划线。")
        auth_uuid = offline_uuid(name)
        access_token = uuid.uuid4().hex
        auth_xuid = ""
        user_type = "legacy"
        client_id = ""

    resolved = resolve_version(game_dir, version_id)
    if not resolved.main_class:
        raise LaunchError("版本缺少主类信息，无法启动。")

    missing = _check_files(game_dir, resolved)
    if missing:
        raise MissingFilesError(missing)

    java = java_info or _java_info_for(resolved.java_major, cfg, installs)
    early_warnings: list[str] = []
    if resolved.java_major and resolved.java_major <= 8 and java.major > 8:
        early_warnings.append(
            f"该版本较旧，建议使用 Java 8；当前使用 Java {java.major}，可能无法正常启动。"
        )

    client_jar = client_jar_path(game_dir, resolved)
    libs_root = libraries_dir(game_dir)
    entries: list[str] = []
    seen: set[str] = set()
    for art in active_library_artifacts(resolved):
        path = str(libs_root / art.path)
        if path not in seen:
            seen.add(path)
            entries.append(path)
    entries.append(str(client_jar))
    classpath = SYSTEM_SEPARATOR.join(entries)

    native_dir = natives_dir(game_dir, resolved)
    native_dir.mkdir(parents=True, exist_ok=True)
    for sub in ("java", "jna", "lwjgl", "netty"):
        (native_dir / sub).mkdir(parents=True, exist_ok=True)

    assets_root = assets_dir(game_dir)
    index_file = asset_index_path(game_dir, resolved)
    log_config = ""
    client_log = ((resolved.logging or {}).get("client") or {}).get("file") or {}
    if client_log.get("id"):
        candidate = assets_root / "log_configs" / str(client_log["id"])
        if candidate.exists():
            log_config = str(candidate)

    placeholder = {
        "auth_player_name": name,
        "version_name": resolved.id,
        "game_directory": str(run_dir),
        "game_assets": str(assets_root),
        "assets_root": str(assets_root),
        "assets_index_name": resolved.asset_index_id,
        "auth_uuid": auth_uuid,
        "auth_access_token": access_token,
        "auth_session": f"token:{access_token}:{auth_uuid}",
        "clientid": client_id,
        "auth_xuid": auth_xuid,
        "user_type": user_type,
        "version_type": resolved.type or "release",
        "user_properties": "{}",
        "natives_directory": str(native_dir),
        "launcher_name": APP_NAME.replace(" ", ""),
        "launcher_version": APP_VERSION,
        "classpath": classpath,
        "classpath_separator": SYSTEM_SEPARATOR,
        "library_directory": str(libs_root),
        "resolution_width": "854",
        "resolution_height": "480",
        "quickPlayPath": "",
        "quickPlaySingleplayer": "",
        "quickPlayMultiplayer": "",
        "quickPlayRealms": "",
    }

    command: list[str] = [java.path]
    command.append(f"-Xmx{cfg.memory_mb}M")
    command.append(f"-Xms{min(cfg.memory_mb, 2048)}M")

    default_jvm = _extract_args(resolved.default_user_jvm, placeholder)
    default_jvm = _ignore_client_jar(default_jvm, client_jar.name)
    for arg in default_jvm:
        low = arg.lower()
        if low.startswith("-xmx") or low.startswith("-xms") or low.startswith("-xmn"):
            continue
        command.append(arg)

    version_jvm = _extract_args(resolved.arguments.get("jvm", []), placeholder)
    version_jvm = _ignore_client_jar(version_jvm, client_jar.name)
    command.extend(version_jvm)

    has_cp = False
    has_library_path = False
    for index, arg in enumerate(command):
        if arg == "-cp" or arg == "-classpath":
            has_cp = True
            if index + 1 < len(command):
                command[index + 1] = classpath
        if arg.startswith("-Djava.library.path="):
            has_library_path = True
            command[index] = "-Djava.library.path=" + str(native_dir)
    if not has_cp:
        command.extend(["-cp", classpath])
    if not has_library_path:
        command.append("-Djava.library.path=" + str(native_dir))
    if log_config and not any(a.startswith("-Dlog4j.configurationFile=") for a in command):
        command.append("-Dlog4j.configurationFile=" + log_config)

    if cfg.jvm_args.strip():
        try:
            extra = shlex.split(cfg.jvm_args, posix=True)
        except ValueError:
            extra = cfg.jvm_args.split()
        command.extend(extra)

    # 防止 JVM 参数中的未知占位符漏到命令行
    command = [
        _substitute(arg, placeholder) for arg in command
    ]

    warnings: list[str] = list(early_warnings)
    unresolved = []
    for arg in command:
        unresolved.extend(_unresolved(arg))
    if unresolved:
        warnings.append("存在未解析的启动参数占位符：" + ", ".join(sorted(set(unresolved))))

    game_arguments: list[str]
    if resolved.arguments.get("game"):
        game_arguments = _extract_args(resolved.arguments["game"], placeholder)
    elif resolved.minecraft_arguments:
        game_arguments = _split_legacy_args(
            resolved.minecraft_arguments, placeholder
        )
    else:
        raise LaunchError("版本缺少游戏启动参数。")

    for arg in game_arguments:
        unresolved.extend(_unresolved(arg))
    if any(_unresolved(a) for a in game_arguments):
        warnings.append("游戏参数中存在未解析的占位符。")

    command.append(resolved.main_class)
    command.extend(game_arguments)

    if str(server_address or "").strip():
        from .sakura import parse_server_address

        server_host, server_port = parse_server_address(server_address)
        command.extend(["--server", server_host, "--port", str(server_port)])

    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "mods").mkdir(parents=True, exist_ok=True)

    return LaunchPlan(
        command=command,
        cwd=str(run_dir),
        java=java,
        version=resolved,
        log_file=log_config,
        warnings=warnings,
    )


class GameProcess(QObject):
    """用守护线程读取游戏输出，关闭启动器也不会杀掉游戏进程。"""

    output = Signal(str)
    started_ok = Signal(int)
    game_finished = Signal(int)

    def __init__(self, plan: LaunchPlan, parent=None) -> None:
        super().__init__(parent)
        self.plan = plan
        self._process: subprocess.Popen[str] | None = None
        self._thread: threading.Thread | None = None
        self._finished = threading.Event()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        startupinfo = None
        flags = CREATE_NO_WINDOW
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE

        try:
            self._process = subprocess.Popen(
                self.plan.command,
                cwd=self.plan.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=flags,
                startupinfo=startupinfo,
            )
        except Exception as exc:
            self.output.emit(f"[启动失败] {exc}")
            self._finish(-1)
            return

        process = self._process
        self.started_ok.emit(process.pid)

        # 输出读取必须和进程等待分开：某些子进程（如 Forge 安装器或游戏内
        # 拉起的辅助进程）会继承 stdout 管道，父进程退出后管道仍被占住。
        # 如果在这里同步迭代 stdout，就会永远等不到 EOF，UI 便一直停在
        # “游戏运行中”。wait() 只看游戏主进程本身，能第一时间拿到退出码。
        reader = threading.Thread(
            target=self._pump_output, args=(process,), daemon=True
        )
        reader.start()

        try:
            code = process.wait()
        except Exception as exc:
            self.output.emit(f"[进程错误] {exc}")
            code = -1

        # 给读取线程一点时间把缓冲区里剩余的日志吐完，然后立刻通知界面。
        reader.join(0.25)
        self._finish(code)

    def _pump_output(self, process: subprocess.Popen[str]) -> None:
        stream = process.stdout
        if stream is None:
            return
        try:
            for line in stream:
                if line:
                    self.output.emit(line.rstrip("\r\n"))
        except Exception:
            pass

    def _finish(self, code: int) -> None:
        if self._finished.is_set():
            return
        self._finished.set()
        # 先通知界面，再尝试关闭管道。TextIOWrapper.close() 会等待读取线程
        # 持有的缓冲区锁；如果子进程还占着管道，这里会一直阻塞。
        self.game_finished.emit(code)
        if self._process and self._process.stdout:
            threading.Thread(target=self._close_stream, daemon=True).start()

    def _close_stream(self) -> None:
        try:
            if self._process and self._process.stdout:
                self._process.stdout.close()
        except Exception:
            pass

    @property
    def running(self) -> bool:
        return bool(self._process and self._process.poll() is None)

    @property
    def pid(self) -> int:
        return self._process.pid if self._process else 0

    def stop(self, graceful: bool = True) -> None:
        process = self._process
        if not process or process.poll() is not None:
            return
        if not graceful or sys.platform != "win32":
            try:
                process.terminate()
            except Exception:
                pass
            return
        threading.Thread(
            target=self._close_gracefully, args=(process,), daemon=True
        ).start()

    def _close_gracefully(self, process: subprocess.Popen) -> None:
        """先向游戏窗口发送 WM_CLOSE，超时后再结束进程。"""
        try:
            user32 = ctypes.windll.user32
            callback_type = ctypes.WINFUNCTYPE(
                wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
            )
            user32.GetWindowThreadProcessId.argtypes = [
                wintypes.HWND,
                ctypes.POINTER(wintypes.DWORD),
            ]
            user32.GetWindowThreadProcessId.restype = wintypes.DWORD
            user32.IsWindowVisible.argtypes = [wintypes.HWND]
            user32.IsWindowVisible.restype = wintypes.BOOL
            user32.PostMessageW.argtypes = [
                wintypes.HWND,
                wintypes.UINT,
                wintypes.WPARAM,
                wintypes.LPARAM,
            ]
            user32.PostMessageW.restype = wintypes.BOOL
            user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
            user32.EnumWindows.restype = wintypes.BOOL
            pid = process.pid
            found = [0]

            def enum_window(hwnd, _lparam) -> bool:
                window_pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(
                    hwnd, ctypes.byref(window_pid)
                )
                if window_pid.value == pid and user32.IsWindowVisible(hwnd):
                    found[0] += 1
                    user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
                return True

            callback = callback_type(enum_window)
            user32.EnumWindows(callback, 0)
        except Exception:
            found = [0]

        deadline = time.time() + (12 if found[0] else 5)
        while time.time() < deadline and process.poll() is None:
            time.sleep(0.2)
        if process.poll() is not None:
            return
        try:
            process.terminate()
        except Exception:
            return
        try:
            process.wait(timeout=4)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass
