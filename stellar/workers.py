from __future__ import annotations

import copy
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from PySide6.QtCore import QObject, Signal

from .config import APP_DIR
from .downloader import DownloadCancelled, cleanup_partial_files, download_to_file
from .fsops import recycle_path
from .images import fetch_image
from .installer import InstallCancelled, Installer, get_manifest
from .java import detect_java_installs
from .launch import LaunchError, MissingFilesError, prepare_launch
from .loaders import install_loader, loader_versions
from .microsoft_auth import (
    MicrosoftAuthCancelled,
    ensure_minecraft_token,
    login_with_browser,
    login_with_device_code,
)
from .packs import (
    PackError,
    PackOptions,
    export_pack,
    import_pack,
    inspect_pack,
)
from .modrinth_pack import import_modrinth_pack
from .version_zip import import_version_zip
from .migration import (
    MigrationCancelled,
    MigrationError,
    collect_migration_plan,
    run_migration,
)
from .modrinth import (
    download_mod_file,
    fetch_mod_versions,
    fetch_project,
    fetch_projects,
    fetch_server_addon_versions,
    search_mods,
    search_server_addons,
)
from .curseforge import (
    download_mod_file as download_curseforge_mod_file,
    fetch_mod_versions as fetch_curseforge_mod_versions,
    fetch_project as fetch_curseforge_project,
    fetch_projects as fetch_curseforge_projects,
    search_mods as search_curseforge_mods,
)
from .servers import ServerInfo, install_server
from .updater import check_for_update, download_update, prepare_update
from .sakura import (
    ROOM_TUNNEL_NAME,
    SakuraClient,
    SakuraNode,
    SakuraTunnel,
    config_server_addr,
    ensure_frpc,
    room_code_for_address,
    tunnel_public_address,
    write_tunnel_config,
    remove_tunnel_config,
)
from .skins import change_skin, fetch_account_skin, reset_skin


class InstallWorker(QObject):
    progress = Signal(int, str)
    log_line = Signal(str)
    finished = Signal(bool, str)

    def __init__(
        self,
        game_dir: Path,
        version_id: str,
        version_url: str = "",
        source: str = "official",
        verify: bool = False,
        threads: int = 8,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.game_dir = Path(game_dir)
        self.version_id = version_id
        self.version_url = version_url
        self.source = source
        self.verify = verify
        self.threads = max(1, min(16, int(threads)))
        self.cancel_event = threading.Event()
        self.installer: Installer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            self.installer = Installer(
                self.game_dir,
                source=self.source,
                progress_cb=lambda p, m: self.progress.emit(p, m),
                log_cb=lambda m: self.log_line.emit(m),
                cancel_event=self.cancel_event,
                threads=self.threads,
            )
            self.installer.install(
                self.version_id,
                version_url=self.version_url,
                verify=self.verify,
                check_assets=True,
            )
        except InstallCancelled:
            removed, freed = cleanup_partial_files(self.game_dir)
            if removed:
                self.log_line.emit(
                    f"已清理 {removed} 个下载临时文件（{freed / 1024 / 1024:.1f} MB）"
                )
            self.finished.emit(False, "下载已取消")
        except Exception as exc:
            self.log_line.emit(str(exc))
            self.finished.emit(False, str(exc))
        else:
            self.finished.emit(True, "安装完成")

    def cancel(self) -> None:
        self.cancel_event.set()


class LaunchWorker(QObject):
    """在后台完成正版验证、文件检查与启动参数构建。"""

    status = Signal(str)
    ready = Signal(object, object)
    missing = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        game_dir: Path,
        version_id: str,
        cfg,
        installs: list,
        account,
        server_address: str = "",
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.game_dir = Path(game_dir)
        self.version_id = version_id
        self.cfg = copy.deepcopy(cfg)
        self.installs = list(installs or [])
        self.account = copy.deepcopy(account)
        self.server_address = str(server_address or "")
        self.cancel_event = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def cancel(self) -> None:
        self.cancel_event.set()

    def _cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def _run(self) -> None:
        try:
            account = self.account
            if account.is_msa:
                self.status.emit("正在验证正版账户…")
                try:
                    account = ensure_minecraft_token(
                        account, self.cfg.microsoft_client_id
                    )
                except Exception as exc:
                    if not self._cancelled():
                        self.failed.emit(f"正版账户验证失败：{exc}")
                    else:
                        self.cancelled.emit()
                    return
                if self._cancelled():
                    self.cancelled.emit()
                    return

            self.status.emit("正在检查游戏文件…")
            plan = prepare_launch(
                self.game_dir,
                self.version_id,
                self.cfg,
                self.installs,
                account=account,
                server_address=self.server_address,
            )
            if self._cancelled():
                self.cancelled.emit()
                return
        except MissingFilesError as exc:
            if self._cancelled():
                self.cancelled.emit()
            else:
                self.missing.emit(list(exc.missing))
        except LaunchError as exc:
            if self._cancelled():
                self.cancelled.emit()
            else:
                self.failed.emit(str(exc))
        except Exception as exc:
            if self._cancelled():
                self.cancelled.emit()
            else:
                self.failed.emit(f"启动失败：{exc}")
        else:
            self.ready.emit(plan, account)


class DeleteWorker(QObject):
    finished = Signal(bool, str)

    def __init__(self, path: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.path = Path(path)

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            recycle_path(self.path)
        except Exception as exc:
            self.finished.emit(False, str(exc))
        else:
            self.finished.emit(True, "")


class CleanupWorker(QObject):
    finished = Signal(int, int)

    def __init__(self, root: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.root = Path(root)

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            removed, freed = cleanup_partial_files(self.root)
        except Exception:
            removed, freed = 0, 0
        self.finished.emit(removed, freed)


class UpdateCheckWorker(QObject):
    ready = Signal(object, str)

    def __init__(
        self,
        current: str,
        url: str,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.current = str(current)
        self.url = str(url)

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            release = check_for_update(self.current, self.url)
        except Exception as exc:
            self.ready.emit(None, str(exc))
        else:
            self.ready.emit(release, "")


class UpdateDownloadWorker(QObject):
    progress = Signal(int, str)
    finished = Signal(bool, str, str)

    def __init__(
        self,
        release,
        dest_dir: Path,
        threads: int = 4,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.release = release
        self.dest_dir = Path(dest_dir)
        self.threads = max(1, min(8, int(threads)))
        self.cancel_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self.cancel_event.set()

    def _run(self) -> None:
        try:
            archive = download_update(
                self.release,
                self.dest_dir,
                progress_cb=lambda p, m: self.progress.emit(p, m),
                cancel_event=self.cancel_event,
                threads=self.threads,
            )
            payload = prepare_update(
                archive, self.dest_dir, self.release.version
            )
        except DownloadCancelled:
            self.finished.emit(False, "更新下载已取消", "")
        except Exception as exc:
            if self.cancel_event.is_set():
                self.finished.emit(False, "更新下载已取消", "")
            else:
                self.finished.emit(False, str(exc), "")
        else:
            self.finished.emit(True, "更新包已准备就绪", str(payload))


class PackExportWorker(QObject):
    progress = Signal(int, str)
    log_line = Signal(str)
    finished = Signal(bool, str, str)

    def __init__(
        self,
        game_dir: Path,
        version_id: str,
        output_path: Path,
        name: str = "",
        description: str = "",
        options: PackOptions | None = None,
        isolated: bool = True,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.game_dir = Path(game_dir)
        self.version_id = version_id
        self.output_path = Path(output_path)
        self.name = name
        self.description = description
        self.options = options or PackOptions()
        self.isolated = isolated
        self.cancel_event = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def cancel(self) -> None:
        self.cancel_event.set()

    def _run(self) -> None:
        try:
            path = export_pack(
                self.game_dir,
                self.version_id,
                self.output_path,
                name=self.name,
                description=self.description,
                options=self.options,
                isolated=self.isolated,
                progress_cb=lambda p, m: self.progress.emit(p, m),
                log_cb=lambda m: self.log_line.emit(m),
                cancel_event=self.cancel_event,
            )
        except Exception as exc:
            if self.cancel_event.is_set():
                self.finished.emit(False, "打包已取消", "")
            else:
                self.finished.emit(False, str(exc), "")
        else:
            self.finished.emit(True, f"整合包已保存到 {path}", str(path))


class PackImportWorker(QObject):
    progress = Signal(int, str)
    log_line = Signal(str)
    finished = Signal(bool, str, str)

    def __init__(
        self,
        pack_path: Path,
        game_dir: Path,
        version_id: str,
        overwrite: bool = False,
        resume: bool = False,
        isolated: bool = True,
        source: str = "official",
        threads: int = 8,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.pack_path = Path(pack_path)
        self.game_dir = Path(game_dir)
        self.version_id = version_id
        self.overwrite = bool(overwrite)
        self.resume = bool(resume)
        self.isolated = bool(isolated)
        self.source = source
        self.threads = max(1, min(16, int(threads)))
        self.cancel_event = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def cancel(self) -> None:
        self.cancel_event.set()

    def _run(self) -> None:
        try:
            result = import_pack(
                self.pack_path,
                self.game_dir,
                self.version_id,
                overwrite=self.overwrite,
                resume=self.resume,
                isolated=self.isolated,
                source=self.source,
                threads=self.threads,
                progress_cb=lambda p, m: self.progress.emit(p, m),
                log_cb=lambda m: self.log_line.emit(m),
                cancel_event=self.cancel_event,
            )
        except Exception as exc:
            if self.cancel_event.is_set():
                self.finished.emit(False, "导入已取消", "")
            else:
                self.finished.emit(False, str(exc), "")
        else:
            self.finished.emit(
                True,
                f"已导入 {result.imported_files} 个文件"
                + (
                    f"，跳过 {result.skipped_files} 个已存在文件"
                    if result.skipped_files
                    else ""
                ),
                result.version_id,
            )


class ModrinthPackImportWorker(QObject):
    progress = Signal(int, str)
    log_line = Signal(str)
    finished = Signal(bool, str, str)

    def __init__(
        self,
        pack_path: Path,
        game_dir: Path,
        version_id: str,
        java_path: str = "",
        overwrite: bool = False,
        resume: bool = False,
        isolated: bool = True,
        source: str = "official",
        threads: int = 8,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.pack_path = Path(pack_path)
        self.game_dir = Path(game_dir)
        self.version_id = version_id
        self.java_path = str(java_path or "")
        self.overwrite = bool(overwrite)
        self.resume = bool(resume)
        self.isolated = bool(isolated)
        self.source = source
        self.threads = max(1, min(16, int(threads)))
        self.cancel_event = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def cancel(self) -> None:
        self.cancel_event.set()

    def _run(self) -> None:
        try:
            result = import_modrinth_pack(
                self.pack_path,
                self.game_dir,
                self.version_id,
                java_path=self.java_path,
                overwrite=self.overwrite,
                resume=self.resume,
                isolated=self.isolated,
                source=self.source,
                threads=self.threads,
                progress_cb=lambda p, m: self.progress.emit(p, m),
                log_cb=lambda m: self.log_line.emit(m),
                cancel_event=self.cancel_event,
            )
        except Exception as exc:
            if self.cancel_event.is_set():
                self.finished.emit(False, "导入已取消", "")
            else:
                self.finished.emit(False, str(exc), "")
        else:
            self.finished.emit(
                True,
                f"已下载 {result.downloaded_files} 个文件，"
                f"导入 {result.imported_files} 个文件"
                + (
                    f"，跳过 {result.skipped_files} 个已存在文件"
                    if result.skipped_files
                    else ""
                ),
                result.version_id,
            )


class VersionZipImportWorker(QObject):
    progress = Signal(int, str)
    log_line = Signal(str)
    finished = Signal(bool, str, str)

    def __init__(
        self,
        zip_path: Path,
        game_dir: Path,
        version_id: str,
        candidate_json: str = "",
        overwrite: bool = False,
        resume: bool = False,
        isolated: bool = True,
        complete: bool = True,
        source: str = "official",
        threads: int = 8,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.zip_path = Path(zip_path)
        self.game_dir = Path(game_dir)
        self.version_id = version_id
        self.candidate_json = candidate_json
        self.overwrite = bool(overwrite)
        self.resume = bool(resume)
        self.isolated = bool(isolated)
        self.complete = bool(complete)
        self.source = source
        self.threads = max(1, min(16, int(threads)))
        self.cancel_event = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def cancel(self) -> None:
        self.cancel_event.set()

    def _run(self) -> None:
        try:
            result = import_version_zip(
                self.zip_path,
                self.game_dir,
                self.version_id,
                candidate_json=self.candidate_json,
                overwrite=self.overwrite,
                resume=self.resume,
                isolated=self.isolated,
                complete=self.complete,
                source=self.source,
                threads=self.threads,
                progress_cb=lambda p, m: self.progress.emit(p, m),
                log_cb=lambda m: self.log_line.emit(m),
                cancel_event=self.cancel_event,
            )
        except Exception as exc:
            if self.cancel_event.is_set():
                self.finished.emit(False, "导入已取消", "")
            else:
                self.finished.emit(False, str(exc), "")
        else:
            self.finished.emit(
                True,
                f"已导入 {result.imported_files} 个文件"
                + (
                    f"，跳过 {result.skipped_files} 个已存在文件"
                    if result.skipped_files
                    else ""
                ),
                result.version_id,
            )


class PackFetchWorker(QObject):
    """从链接下载压缩包到缓存目录，随后由对话框读取并确认导入。"""

    progress = Signal(int, str)
    finished = Signal(str, str)

    def __init__(
        self,
        url: str,
        threads: int = 8,
        parent: QObject | None = None,
        inspect_result: bool = True,
        default_suffix: str = ".dklpack",
    ) -> None:
        super().__init__(parent)
        self.url = str(url or "").strip()
        self.threads = max(1, min(16, int(threads)))
        self.inspect_result = bool(inspect_result)
        self.default_suffix = str(default_suffix or ".dklpack")
        self.cancel_event = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def cancel(self) -> None:
        self.cancel_event.set()

    def _run(self) -> None:
        parsed = urlparse(self.url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            self.finished.emit("", "请输入 http:// 或 https:// 开头的下载链接")
            return
        filename = Path(unquote(parsed.path or "")).name
        if not filename.lower().endswith((".dklpack", ".mrpack", ".zip")):
            filename = f"Darkitten-Archive-{int(time.time())}{self.default_suffix}"
        safe_name = "".join(
            ch if ch not in r'<>:"/\|?*' and ord(ch) >= 32 else "-"
            for ch in filename
        ) or f"Darkitten-Archive{self.default_suffix}"
        cache = APP_DIR / "cache" / "packs"
        cache.mkdir(parents=True, exist_ok=True)
        target = cache / f"{uuid.uuid4().hex[:8]}-{safe_name}"
        last = [0]

        def report(done: int, total: int) -> None:
            if self.cancel_event.is_set():
                return
            percent = int(done * 100 / total) if total else min(95, last[0] + 1)
            if percent != last[0]:
                last[0] = percent
                self.progress.emit(
                    percent,
                    f"下载压缩包 {percent}%"
                    + (f" · {done / 1024 / 1024:.1f}/{total / 1024 / 1024:.1f} MB" if total else ""),
                )

        try:
            self.progress.emit(0, "连接下载服务器…")
            download_to_file(
                self.url,
                target,
                cancel_event=self.cancel_event,
                progress_cb=report,
                connections=min(6, max(2, self.threads // 2)),
            )
            if self.cancel_event.is_set():
                self.finished.emit("", "下载已取消")
                return
            if self.inspect_result:
                info = inspect_pack(target)
                label = info.name
            else:
                label = Path(safe_name).stem
        except DownloadCancelled:
            self.finished.emit("", "下载已取消")
        except Exception as exc:
            if self.cancel_event.is_set():
                self.finished.emit("", "下载已取消")
            else:
                self.finished.emit("", str(exc))
        else:
            self.progress.emit(100, f"下载完成 · {label}")
            self.finished.emit(str(target), "")


class ManifestWorker(QObject):
    ready = Signal(object, str)

    def __init__(self, source: str = "official", force: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.source = source
        self.force = force

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            data = get_manifest(self.source, force=self.force)
            self.ready.emit(data, "")
        except Exception as exc:
            self.ready.emit(None, str(exc))


class JavaDetectWorker(QObject):
    ready = Signal(object)

    def __init__(self, preferred: str = "", parent=None) -> None:
        super().__init__(parent)
        self.preferred = preferred

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            data = detect_java_installs(self.preferred)
        except Exception:
            data = []
        self.ready.emit(data)


class BrowserLoginWorker(QObject):
    url_ready = Signal(str)
    status = Signal(str)
    finished = Signal(object, str)

    def __init__(
        self, client_id: str, email: str = "", parent=None
    ) -> None:
        super().__init__(parent)
        self.client_id = client_id
        self.email = email
        self.cancel_event = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            account = login_with_browser(
                self.client_id,
                login_hint=self.email,
                on_url=self.url_ready.emit,
                on_status=self.status.emit,
                cancel_event=self.cancel_event,
            )
        except MicrosoftAuthCancelled:
            self.finished.emit(None, "登录已取消")
        except Exception as exc:
            self.finished.emit(None, str(exc))
        else:
            self.finished.emit(account, "")

    def cancel(self) -> None:
        self.cancel_event.set()


class DeviceLoginWorker(QObject):
    code_ready = Signal(object)
    status = Signal(str)
    finished = Signal(object, str)

    def __init__(self, client_id: str, parent=None) -> None:
        super().__init__(parent)
        self.client_id = client_id
        self.cancel_event = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            account = login_with_device_code(
                self.client_id,
                on_code=self.code_ready.emit,
                on_status=self.status.emit,
                cancel_event=self.cancel_event,
            )
        except MicrosoftAuthCancelled:
            self.finished.emit(None, "登录已取消")
        except Exception as exc:
            self.finished.emit(None, str(exc))
        else:
            self.finished.emit(account, "")

    def cancel(self) -> None:
        self.cancel_event.set()


class LoaderVersionsWorker(QObject):
    ready = Signal(object, str)

    def __init__(self, kind: str, mc_version: str, parent=None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.mc_version = mc_version

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            data = loader_versions(self.kind, self.mc_version)
        except Exception as exc:
            self.ready.emit([], str(exc))
        else:
            self.ready.emit(data, "")


class LoaderInstallWorker(QObject):
    progress = Signal(int, str)
    log_line = Signal(str)
    finished = Signal(bool, str, str)

    def __init__(
        self,
        game_dir: Path,
        mc_version: str,
        kind: str,
        loader_version: str,
        java_path: str = "",
        source: str = "official",
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.game_dir = Path(game_dir)
        self.mc_version = mc_version
        self.kind = kind
        self.loader_version = loader_version
        self.java_path = java_path
        self.source = source
        self.cancel_event = threading.Event()
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            version_id = install_loader(
                self.game_dir,
                self.mc_version,
                self.kind,
                self.loader_version,
                java_path=self.java_path,
                source=self.source,
                progress_cb=lambda p, m: self.progress.emit(p, m),
                log_cb=lambda m: self.log_line.emit(m),
                cancel_event=self.cancel_event,
                process_cb=self._set_process,
            )
        except InstallCancelled:
            self.finished.emit(False, "安装已取消", "")
        except Exception as exc:
            self.log_line.emit(str(exc))
            self.finished.emit(False, str(exc), "")
        else:
            self.finished.emit(True, "安装完成", version_id)

    def _set_process(self, proc: subprocess.Popen | None) -> None:
        with self._lock:
            self._process = proc
            cancelled = self.cancel_event.is_set()
        if proc is not None and cancelled:
            try:
                proc.terminate()
            except Exception:
                pass

    def cancel(self) -> None:
        self.cancel_event.set()
        with self._lock:
            proc = self._process
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass


class MigrationWorker(QObject):
    """先按需安装加载器，再把存档/配置/Mod 迁移到目标版本。"""

    progress = Signal(int, str)
    log_line = Signal(str)
    finished = Signal(bool, str, str)

    def __init__(
        self,
        game_dir: Path,
        source_id: str,
        target_id: str = "",
        categories=None,
        match_mods: bool = True,
        copy_unmatched: bool = True,
        overwrite: bool = False,
        isolated: bool = True,
        loader_kind: str = "",
        loader_version: str = "",
        java_path: str = "",
        source: str = "auto",
        threads: int = 8,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.game_dir = Path(game_dir)
        self.source_id = source_id
        self.target_id = target_id
        self.categories = list(categories or [])
        self.match_mods = bool(match_mods)
        self.copy_unmatched = bool(copy_unmatched)
        self.overwrite = bool(overwrite)
        self.isolated = bool(isolated)
        self.loader_kind = loader_kind
        self.loader_version = loader_version
        self.java_path = java_path
        self.source = source
        self.threads = max(1, min(16, int(threads)))
        self.cancel_event = threading.Event()
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def cancel(self) -> None:
        self.cancel_event.set()
        with self._lock:
            proc = self._process
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass

    def _set_process(self, proc: subprocess.Popen | None) -> None:
        with self._lock:
            self._process = proc
            cancelled = self.cancel_event.is_set()
        if proc is not None and cancelled:
            try:
                proc.terminate()
            except Exception:
                pass

    def _run(self) -> None:
        target_id = self.target_id
        try:
            loader_span = 45 if self.loader_kind else 0
            if self.loader_kind:
                from .loaders import LOADER_NAMES, install_loader

                name = LOADER_NAMES.get(self.loader_kind, self.loader_kind)
                self.progress.emit(1, f"正在安装 {name} {self.loader_version}…")
                target_id = install_loader(
                    self.game_dir,
                    self._source_mc(),
                    self.loader_kind,
                    self.loader_version,
                    java_path=self.java_path,
                    source=self.source,
                    progress_cb=lambda p, m: self.progress.emit(
                        max(1, int(p * loader_span / 100)), m
                    ),
                    log_cb=self.log_line.emit,
                    cancel_event=self.cancel_event,
                    process_cb=self._set_process,
                )
                self.log_line.emit(f"加载器安装完成：{target_id}")
                self.progress.emit(loader_span, "加载器已就绪，开始迁移内容…")

            if self.cancel_event.is_set():
                raise MigrationCancelled()
            plan = collect_migration_plan(
                self.game_dir,
                self.source_id,
                target_id,
                self.categories,
                isolated=self.isolated,
            )
            for key, count in plan.file_counts.items():
                if count:
                    from .migration import CATEGORY_NAMES

                    self.log_line.emit(
                        f"{CATEGORY_NAMES.get(key, key)}：{count} 项"
                    )
            base = loader_span
            span = 100 - base
            report = run_migration(
                plan,
                match_mods=self.match_mods,
                copy_unmatched=self.copy_unmatched,
                overwrite=self.overwrite,
                progress_cb=lambda p, m: self.progress.emit(
                    max(base, min(99, base + int(p * span / 100))), m
                ),
                log_cb=self.log_line.emit,
                cancel_event=self.cancel_event,
                threads=self.threads,
            )
            for warning in report.warnings:
                self.log_line.emit("提醒：" + warning)
            self.progress.emit(100, "迁移完成")
            self.finished.emit(True, report.summary(), target_id)
        except MigrationCancelled:
            self.finished.emit(False, "迁移已取消", target_id)
        except (MigrationError, Exception) as exc:
            if self.cancel_event.is_set():
                self.finished.emit(False, "迁移已取消", target_id)
            else:
                self.log_line.emit(str(exc))
                self.finished.emit(False, str(exc), target_id)

    def _source_mc(self) -> str:
        from .versions import resolve_version

        resolved = resolve_version(self.game_dir, self.source_id)
        return str(resolved.chain[0] if resolved.chain else resolved.id)


class ModSearchWorker(QObject):
    ready = Signal(object, str, int)

    def __init__(
        self,
        query: str,
        loader: str,
        game_version: str,
        offset: int = 0,
        limit: int = 20,
        parent=None,
        source: str = "modrinth",
    ) -> None:
        super().__init__(parent)
        self.query = query
        self.loader = loader
        self.game_version = game_version
        self.offset = offset
        self.limit = limit
        self.source = str(source or "modrinth").lower()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            search = (
                search_curseforge_mods
                if self.source == "curseforge"
                else search_mods
            )
            data = search(
                self.query,
                loader=self.loader,
                game_version=self.game_version,
                limit=self.limit,
                offset=self.offset,
            )
        except Exception as exc:
            self.ready.emit(None, str(exc), self.offset)
        else:
            self.ready.emit(data, "", self.offset)


class ModProjectWorker(QObject):
    ready = Signal(str, object, str)

    def __init__(
        self, project_id: str, parent=None, source: str = "modrinth"
    ) -> None:
        super().__init__(parent)
        self.project_id = project_id
        self.source = str(source or "modrinth").lower()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            fetch = (
                fetch_curseforge_project
                if self.source == "curseforge"
                else fetch_project
            )
            data = fetch(self.project_id)
        except Exception as exc:
            self.ready.emit(self.project_id, None, str(exc))
        else:
            self.ready.emit(self.project_id, data, "")


class ModProjectsWorker(QObject):
    ready = Signal(object, str)

    def __init__(
        self, project_ids: list[str], parent=None, source: str = "modrinth"
    ) -> None:
        super().__init__(parent)
        self.project_ids = list(dict.fromkeys(project_ids))
        self.source = str(source or "modrinth").lower()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            fetch = (
                fetch_curseforge_projects
                if self.source == "curseforge"
                else fetch_projects
            )
            data = fetch(self.project_ids)
        except Exception as exc:
            self.ready.emit({}, str(exc))
        else:
            self.ready.emit(data, "")


class ImageFetchWorker(QObject):
    ready = Signal(str, object)

    def __init__(self, url: str, parent=None) -> None:
        super().__init__(parent)
        self.url = url

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            image = fetch_image(self.url)
        except Exception:
            image = None
        self.ready.emit(self.url, image)


class ModVersionsWorker(QObject):
    ready = Signal(object, str)

    def __init__(
        self,
        project_id: str,
        loader: str,
        game_version: str,
        parent=None,
        source: str = "modrinth",
    ) -> None:
        super().__init__(parent)
        self.project_id = project_id
        self.loader = loader
        self.game_version = game_version
        self.source = str(source or "modrinth").lower()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            fetch = (
                fetch_curseforge_mod_versions
                if self.source == "curseforge"
                else fetch_mod_versions
            )
            data = fetch(
                self.project_id, self.loader, self.game_version
            )
        except Exception as exc:
            self.ready.emit([], str(exc))
        else:
            self.ready.emit(data, "")


class ModDownloadWorker(QObject):
    progress = Signal(int, str)
    finished = Signal(bool, str, str)

    def __init__(
        self,
        version: dict[str, Any],
        mods_dir: Path,
        threads: int = 8,
        parent=None,
        source: str = "",
    ) -> None:
        super().__init__(parent)
        self.version = version
        self.mods_dir = Path(mods_dir)
        self.threads = max(1, min(16, int(threads)))
        self.cancel_event = threading.Event()
        self.source = str(
            source or version.get("source") or "modrinth"
        ).lower()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            download = (
                download_curseforge_mod_file
                if self.source == "curseforge"
                else download_mod_file
            )
            path = download(
                self.version,
                self.mods_dir,
                progress_cb=lambda p, m: self.progress.emit(p, m),
                cancel_event=self.cancel_event,
                threads=self.threads,
            )
        except DownloadCancelled:
            self.finished.emit(False, "下载已取消", "")
        except Exception as exc:
            self.finished.emit(False, str(exc), "")
        else:
            self.finished.emit(True, "下载完成", path.name)

    def cancel(self) -> None:
        self.cancel_event.set()


class ServerInstallWorker(QObject):
    progress = Signal(int, str)
    log_line = Signal(str)
    finished = Signal(bool, str)

    def __init__(
        self,
        info: ServerInfo,
        java_path: str = "",
        accept_eula: bool = False,
        source: str = "official",
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.info = info
        self.java_path = java_path
        self.accept_eula = accept_eula
        self.source = source
        self.cancel_event = threading.Event()
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            install_server(
                self.info,
                java_path=self.java_path,
                accept_eula=self.accept_eula,
                source=self.source,
                progress_cb=lambda p, m: self.progress.emit(p, m),
                log_cb=lambda m: self.log_line.emit(m),
                cancel_event=self.cancel_event,
                process_cb=self._set_process,
            )
        except InstallCancelled:
            self.finished.emit(False, "安装已取消")
        except Exception as exc:
            self.log_line.emit(str(exc))
            self.finished.emit(False, str(exc))
        else:
            self.finished.emit(True, "安装完成")

    def _set_process(self, proc: subprocess.Popen | None) -> None:
        with self._lock:
            self._process = proc
            cancelled = self.cancel_event.is_set()
        if proc is not None and cancelled:
            try:
                proc.terminate()
            except Exception:
                pass

    def cancel(self) -> None:
        self.cancel_event.set()
        with self._lock:
            proc = self._process
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass


class ServerAddonSearchWorker(QObject):
    ready = Signal(object, str, int)

    def __init__(
        self,
        query: str,
        server_type: str,
        game_version: str,
        offset: int = 0,
        limit: int = 20,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.query = query
        self.server_type = server_type
        self.game_version = game_version
        self.offset = offset
        self.limit = limit

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            data = search_server_addons(
                self.query,
                server_type=self.server_type,
                game_version=self.game_version,
                limit=self.limit,
                offset=self.offset,
            )
        except Exception as exc:
            self.ready.emit(None, str(exc), self.offset)
        else:
            self.ready.emit(data, "", self.offset)


class ServerAddonVersionsWorker(QObject):
    ready = Signal(object, str)

    def __init__(
        self,
        project_id: str,
        server_type: str,
        game_version: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.project_id = project_id
        self.server_type = server_type
        self.game_version = game_version

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            data = fetch_server_addon_versions(
                self.project_id, self.server_type, self.game_version
            )
        except Exception as exc:
            self.ready.emit([], str(exc))
        else:
            self.ready.emit(data, "")


class SkinFetchWorker(QObject):
    """验证正版令牌并下载账户当前使用的皮肤。"""

    status = Signal(str)
    finished = Signal(object, object, str)

    def __init__(self, account, client_id: str, parent=None) -> None:
        super().__init__(parent)
        self.account = copy.deepcopy(account)
        self.client_id = client_id

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            self.status.emit("正在验证正版账户…")
            skin, account = fetch_account_skin(self.account, self.client_id)
        except Exception as exc:
            self.finished.emit(None, self.account, str(exc))
        else:
            self.finished.emit(skin, account, "")


class SkinUploadWorker(QObject):
    status = Signal(str)
    finished = Signal(bool, str, object)

    def __init__(
        self,
        account,
        client_id: str,
        png_data: bytes,
        variant: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.account = copy.deepcopy(account)
        self.client_id = client_id
        self.png_data = png_data
        self.variant = variant

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        account = self.account
        try:
            self.status.emit("正在验证正版账户…")
            account = ensure_minecraft_token(account, self.client_id)
            self.status.emit("正在上传皮肤到 Minecraft…")
            change_skin(account.mc_access_token, self.png_data, self.variant)
        except Exception as exc:
            self.finished.emit(False, str(exc), account)
        else:
            self.finished.emit(True, "皮肤已上传并生效", account)


class SkinResetWorker(QObject):
    status = Signal(str)
    finished = Signal(bool, str, object)

    def __init__(self, account, client_id: str, parent=None) -> None:
        super().__init__(parent)
        self.account = copy.deepcopy(account)
        self.client_id = client_id

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        account = self.account
        try:
            self.status.emit("正在验证正版账户…")
            account = ensure_minecraft_token(account, self.client_id)
            self.status.emit("正在恢复默认皮肤…")
            reset_skin(account.mc_access_token)
        except Exception as exc:
            self.finished.emit(False, str(exc), account)
        else:
            self.finished.emit(True, "已恢复默认皮肤", account)


class SakuraLoginWorker(QObject):
    ready = Signal(object, str)

    def __init__(self, token: str, parent=None) -> None:
        super().__init__(parent)
        self._token = str(token or "").strip()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            client = SakuraClient(self._token)
            user = client.user_info()
            nodes = client.nodes()
            tunnels = client.tunnels()
        except Exception as exc:
            self.ready.emit(None, str(exc))
        else:
            self.ready.emit(
                {"user": user, "nodes": nodes, "tunnels": tunnels}, ""
            )


class SakuraRoomWorker(QObject):
    """准备（复用/创建）隧道、下载 frpc 并生成房间号。"""

    progress = Signal(int, str)
    log_line = Signal(str)
    ready = Signal(object, str)
    failed = Signal(str)

    def __init__(
        self,
        token: str,
        local_port: int,
        node_id: int = 0,
        preferred_tunnel_id: int = 0,
        frpc_path: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._token = str(token or "").strip()
        self.local_port = int(local_port)
        self.node_id = int(node_id or 0)
        self.preferred_tunnel_id = int(preferred_tunnel_id or 0)
        self.frpc_path = str(frpc_path or "").strip()
        self.cancel_event = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def cancel(self) -> None:
        self.cancel_event.set()

    def _progress(self, value: int, message: str) -> None:
        if not self.cancel_event.is_set():
            self.progress.emit(max(0, min(100, value)), message)

    def _log(self, message: str) -> None:
        if message and not self.cancel_event.is_set():
            self.log_line.emit(message)

    def _pick_tunnel(
        self, tunnels: list[SakuraTunnel]
    ) -> tuple[SakuraTunnel | None, bool]:
        if self.preferred_tunnel_id:
            for tunnel in tunnels:
                if tunnel.id == self.preferred_tunnel_id:
                    return tunnel, True
        candidates = [
            tunnel
            for tunnel in tunnels
            if tunnel.name.startswith(ROOM_TUNNEL_NAME) and tunnel.type == "tcp"
        ]
        if candidates:
            return max(candidates, key=lambda item: item.id), True
        return None, False

    def _pick_node(
        self,
        nodes: dict[int, SakuraNode],
        user,
        tunnel: SakuraTunnel | None,
    ) -> SakuraNode:
        if tunnel and tunnel.node in nodes:
            return nodes[tunnel.node]
        if self.node_id and self.node_id in nodes:
            node = nodes[self.node_id]
            if not node.can_create:
                raise Exception(f"节点“{node.label}”当前已满载，请换一个节点。")
            return node
        level = getattr(user, "group_level", 0)
        candidates = [
            node
            for node in nodes.values()
            if node.selectable and node.vip <= max(0, level)
        ]
        if not candidates:
            candidates = [node for node in nodes.values() if node.selectable]
        if not candidates:
            raise Exception("没有可用节点，请稍后重试或到樱花穿透面板查看节点状态。")
        candidates.sort(key=lambda node: (node.vip, not node.mainland, node.name))
        return candidates[0]

    def _run(self) -> None:
        config_path = None
        try:
            self._progress(3, "正在验证樱花穿透账户…")
            client = SakuraClient(self._token)
            user = client.user_info()
            if self.cancel_event.is_set():
                return
            self._progress(10, "正在读取节点与隧道…")
            nodes = client.nodes()
            tunnels = client.tunnels()

            tunnel, reused = self._pick_tunnel(tunnels)
            if reused and tunnel is not None:
                if tunnel.node not in nodes:
                    raise Exception(
                        "现有 StellarRoom 隧道所在节点已不存在，"
                        "请到樱花穿透面板迁移或删除该隧道后重试。"
                    )
                node = nodes[tunnel.node]
            else:
                node = self._pick_node(nodes, user, None)
            if not node.online:
                raise Exception(f"节点“{node.label}”当前离线，请换一个节点。")

            if tunnel is not None:
                if tunnel.type != "tcp":
                    raise Exception("现有 StellarRoom 隧道不是 TCP 类型，请到面板改为 TCP。")
                if tunnel.status == 2:
                    raise Exception("现有 StellarRoom 隧道已被封禁，请到樱花穿透面板处理。")
                self._log(
                    f"复用隧道 {tunnel.name}（#{tunnel.id}）· 节点 {node.label}"
                )
                if (
                    tunnel.local_port != self.local_port
                    or tunnel.local_ip not in ("", "127.0.0.1")
                ):
                    self._progress(22, "正在更新隧道本地端口…")
                    client.edit_tunnel(tunnel.id, self.local_port)
                    self._log(f"隧道本地端口已更新为 {self.local_port}")
            else:
                if not node.can_create:
                    raise Exception(f"节点“{node.label}”当前无法创建隧道，请换一个节点。")
                self._progress(20, "正在创建 StellarRoom 隧道…")
                created = client.create_tunnel(
                    ROOM_TUNNEL_NAME, node.id, self.local_port
                )
                warning = str(created.get("important_warning_for_ai") or "").strip()
                if warning:
                    self._log(warning)
                tunnel_id = int(created.get("id") or 0)
                self._log(f"已创建隧道 #{tunnel_id} · 节点 {node.label}")
                tunnel = SakuraTunnel(
                    id=tunnel_id,
                    name=str(created.get("name") or ROOM_TUNNEL_NAME),
                    type="tcp",
                    node=node.id,
                    remote=str(created.get("remote") or ""),
                    local_ip="127.0.0.1",
                    local_port=self.local_port,
                )
                for attempt in range(4):
                    if tunnel.remote_port:
                        break
                    if attempt:
                        threading.Event().wait(0.8)
                    refreshed = client.tunnels()
                    for item in refreshed:
                        if item.id == tunnel_id:
                            tunnel = item
                            break
                if not tunnel.remote_port:
                    raise Exception("隧道已创建，但樱花穿透还没有分配远程端口，请稍后重试。")

            if self.cancel_event.is_set():
                return
            address = tunnel_public_address(node, tunnel)

            self._progress(42, "正在准备内置 frpc…")
            from .config import Config

            shim = Config()
            shim.sakura_frpc_path = self.frpc_path
            frpc = ensure_frpc(
                shim,
                progress_cb=lambda p, m: self._progress(42 + int(p * 0.36), m),
                log_cb=self._log,
            )
            if self.cancel_event.is_set():
                return

            self._progress(82, "正在获取隧道配置…")
            config_text = client.tunnel_config(tunnel.id, frpc.version)
            config_path = write_tunnel_config(config_text, tunnel.id)
            server_addr = config_server_addr(config_text)
            if not server_addr and not node.host:
                raise Exception("隧道配置里没有服务器地址，请稍后重试。")

            self._progress(92, "正在生成房间号…")
            room_code = ""
            code_error = ""
            direct_address = address
            try:
                room_code, direct_address = room_code_for_address(address)
            except Exception as exc:
                code_error = str(exc)
                self._log(code_error)

            payload = {
                "room_code": room_code,
                "address": direct_address,
                "share_address": address,
                "code_error": code_error,
                "tunnel_id": tunnel.id,
                "tunnel_name": tunnel.name,
                "node_id": node.id,
                "node_name": node.label,
                "local_port": self.local_port,
                "config_path": str(config_path),
                "frpc_path": str(frpc.path),
                "frpc_version": frpc.version,
                "reused": reused,
                "user": user,
            }
            if self.cancel_event.is_set():
                remove_tunnel_config(config_path)
                return
            self._progress(100, "房间已创建")
            self.ready.emit(payload, "")
        except Exception as exc:
            remove_tunnel_config(config_path)
            self.failed.emit(str(exc))
