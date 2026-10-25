from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from .config import APP_DIR
from .downloader import (
    DownloadCancelled,
    download_to_file,
    http_open as _http_open,
)
from .sources import (
    BMCLAPI,
    OFFICIAL,
    SOURCE_LABELS,
    is_source_usable,
    order_sources,
    refresh_sources,
    report_failure,
    report_success,
)
from .versions import (
    ResolvedVersion,
    active_library_artifacts,
    active_natives,
    asset_index_path,
    assets_dir,
    check_installation,
    client_jar_path,
    libraries_dir,
    natives_dir,
    resolve_version,
    version_json_path,
)

MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
BMCL_MANIFEST_URL = "https://bmclapi2.bangbang93.com/mc/game/version_manifest_v2.json"
CACHE_DIR = APP_DIR / "cache"
MANIFEST_CACHE = CACHE_DIR / "version_manifest_v2.json"
ProgressCallback = Callable[[int, str], None]
LogCallback = Callable[[str], None]


class InstallCancelled(Exception):
    pass


class DownloadError(Exception):
    pass


THIRD_PARTY_MAVENS = (
    "https://maven.minecraftforge.net/",
    "https://maven.neoforged.net/releases/",
    "https://maven.fabricmc.net/",
)
BMCL_MAVEN = "https://bmclapi2.bangbang93.com/maven/"
CENTRAL_MAVEN = "https://repo1.maven.org/maven2/"
CENTRAL_FIRST_PREFIXES = (
    "org/ow2/asm/",
    "org/slf4j/",
    "org/apache/",
    "org/jetbrains/",
    "org/joml/",
    "com/google/",
    "com/mojang/",
    "net/java/",
    "io/netty/",
    "it/unimi/",
    "org/lwjgl/",
)


def mirror_urls(url: str, source: str) -> list[str]:
    """返回按优先级排列的下载地址。

    auto / official 保持官方地址优先，但保留 BMCLAPI 作为兜底，避免官方源
    缓慢或临时不可用时直接失败；bmclapi 会优先使用国内镜像。
    Forge、NeoForge、Fabric 的 Maven 在国内经常无法直连，
    因此始终优先尝试 BMCLAPI 镜像。
    """
    urls: list[str] = []
    mirrored = ""
    relative = ""
    is_mojang = False

    if url.startswith("https://libraries.minecraft.net/"):
        mirrored = BMCL_MAVEN + url.split(
            "https://libraries.minecraft.net/", 1
        )[1]
        is_mojang = True
    elif url.startswith("https://resources.download.minecraft.net/"):
        mirrored = "https://bmclapi2.bangbang93.com/assets/" + url.split(
            "https://resources.download.minecraft.net/", 1
        )[1]
        is_mojang = True
    elif url.startswith("http://resources.download.minecraft.net/"):
        mirrored = "https://bmclapi2.bangbang93.com/assets/" + url.split(
            "http://resources.download.minecraft.net/", 1
        )[1]
        is_mojang = True

    if not mirrored:
        for base in THIRD_PARTY_MAVENS:
            if url.startswith(base):
                relative = url.split(base, 1)[1]
                mirrored = BMCL_MAVEN + relative
                break

    central = CENTRAL_MAVEN + relative if relative else ""
    candidates: list[str] = []
    if mirrored and is_mojang:
        # auto 按测速结果排序；显式选择时尊重用户指定的线路。
        urls_order = order_sources(source)
        by_source = {OFFICIAL: url, BMCLAPI: mirrored}
        candidates.extend(by_source[item] for item in urls_order)
    elif mirrored:
        # Forge / NeoForge / Fabric 的官方 Maven 在部分网络下会接受连接
        # 却几乎不传数据。这里默认先走 BMCLAPI 镜像，只看熔断状态，
        # 不再被清单测速结果（只测 Mojang 清单）误判拖回官方线路。
        bmcl_ready = is_source_usable(BMCLAPI)
        if central and any(
            relative.startswith(prefix) for prefix in CENTRAL_FIRST_PREFIXES
        ):
            candidates.extend(
                [central, mirrored, url] if bmcl_ready else [central, url, mirrored]
            )
        elif bmcl_ready:
            candidates.extend([mirrored, central, url])
        else:
            candidates.extend([url, central, mirrored])
    else:
        candidates.append(url)

    for candidate in candidates:
        if candidate and candidate not in urls:
            urls.append(candidate)
    return urls


def fetch_json(url: str, timeout: int = 30) -> dict[str, Any]:
    with _http_open(url, timeout=timeout) as resp:
        raw = resp.read()
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise DownloadError("服务器返回了无法识别的数据")
    return data


def get_manifest(
    source: str = "official",
    force: bool = False,
    log: LogCallback | None = None,
) -> dict[str, Any]:
    """获取版本清单，优先网络，失败时回退到本地缓存。"""
    if not force and MANIFEST_CACHE.exists():
        try:
            age = time.time() - MANIFEST_CACHE.stat().st_mtime
            if age < 900:
                return json.loads(MANIFEST_CACHE.read_text(encoding="utf-8"))
        except Exception:
            pass

    by_source = {OFFICIAL: MANIFEST_URL, BMCLAPI: BMCL_MANIFEST_URL}
    urls = [
        by_source[item]
        for item in order_sources(source, allow_probe=True)
    ]

    last_error: Exception | None = None
    for url in urls:
        try:
            data = fetch_json(url, timeout=12)
            if isinstance(data.get("versions"), list):
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                MANIFEST_CACHE.write_text(
                    json.dumps(data, ensure_ascii=False), encoding="utf-8"
                )
                if log:
                    log("版本清单已更新")
                return data
        except Exception as exc:
            last_error = exc

    if MANIFEST_CACHE.exists():
        try:
            data = json.loads(MANIFEST_CACHE.read_text(encoding="utf-8"))
            if log:
                log("网络不可用，已使用本地缓存的版本清单")
            return data
        except Exception:
            pass

    raise DownloadError(f"获取版本清单失败：{last_error}")


def _sha1_file(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as fp:
        while True:
            chunk = fp.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


class Installer:
    STAGE_CLIENT = (3, 12)
    STAGE_LIBS = (12, 36)
    STAGE_ASSETS = (36, 94)
    STAGE_FINISH = (94, 100)

    def __init__(
        self,
        game_dir: Path,
        source: str = "official",
        progress_cb: ProgressCallback | None = None,
        log_cb: LogCallback | None = None,
        cancel_event: threading.Event | None = None,
        threads: int = 8,
    ) -> None:
        self.game_dir = Path(game_dir)
        self.source = source
        self.effective_source = source
        self.progress_cb = progress_cb or (lambda p, m: None)
        self.log_cb = log_cb or (lambda m: None)
        self.cancel_event = cancel_event or threading.Event()
        self.threads = max(1, min(16, int(threads)))
        self.segments = min(12, max(3, self.threads))

        self._last_percent = 0
        self._downloaded_bytes = 0
        self._speed_mark_time = time.time()
        self._speed_mark_bytes = 0
        self._speed = 0.0
        self._speed_lock = threading.Lock()
        self._progress_lock = threading.Lock()

    # ------------------------------------------------------------------
    # 基础回调 / 进度

    def log(self, message: str) -> None:
        self.log_cb(message)

    def _check_cancel(self) -> None:
        if self.cancel_event.is_set():
            raise InstallCancelled()

    def _resolve_source(self) -> None:
        """auto 按测速结果选线，显式选择则尊重用户并保留兜底。"""
        if self.source not in ("auto", OFFICIAL, BMCLAPI):
            self.source = "auto"
        if self.source == BMCLAPI:
            refresh_sources(sources=(BMCLAPI,), allow_probe=True)
            if is_source_usable(BMCLAPI):
                self.effective_source = BMCLAPI
                self.log("下载线路：BMCLAPI 镜像")
            else:
                self.effective_source = OFFICIAL
                self.log("BMCLAPI 当前不可用，已自动切换为 Mojang 官方源")
            return
        if self.source == OFFICIAL:
            self.effective_source = OFFICIAL
            self.log("下载线路：Mojang 官方")
            return
        order = order_sources("auto", allow_probe=True)
        self.effective_source = order[0]
        self.log(f"下载线路：{SOURCE_LABELS[self.effective_source]}（自动测速）")

    def _set_progress(self, percent: int, message: str) -> None:
        with self._progress_lock:
            percent = max(self._last_percent, min(100, int(percent)))
            self._last_percent = percent
            self.progress_cb(percent, message)

    def _stage_percent(
        self, stage: tuple[int, int], done: int, total: int
    ) -> int:
        start, end = stage
        if total <= 0:
            return end
        return int(start + (end - start) * min(1.0, done / total))

    def _add_bytes(self, count: int) -> None:
        if count <= 0:
            return
        with self._speed_lock:
            self._downloaded_bytes += count
            now = time.time()
            elapsed = now - self._speed_mark_time
            if elapsed >= 0.8:
                recent = self._downloaded_bytes - self._speed_mark_bytes
                instant = recent / elapsed
                self._speed = instant if self._speed <= 0 else (
                    self._speed * 0.6 + instant * 0.4
                )
                self._speed_mark_time = now
                self._speed_mark_bytes = self._downloaded_bytes

    @property
    def speed_text(self) -> str:
        with self._speed_lock:
            speed = self._speed
        if speed <= 0:
            return ""
        if speed >= 1024 * 1024:
            return f"{speed / 1024 / 1024:.1f} MB/s"
        return f"{speed / 1024:.0f} KB/s"

    # ------------------------------------------------------------------
    # 单文件下载

    def _download_file(
        self,
        url: str,
        dest: Path,
        sha1: str = "",
        size: int = 0,
        verify: bool = False,
        label: str = "",
        skipped_bytes: list[int] | None = None,
        connections: int | None = None,
    ) -> bool:
        """下载单个文件。返回是否真正执行了下载。"""
        self._check_cancel()
        dest.parent.mkdir(parents=True, exist_ok=True)

        if dest.exists():
            try:
                existing_size = dest.stat().st_size
                size_ok = (not size) or existing_size == size
                if size_ok and (not verify or not sha1 or _sha1_file(dest) == sha1):
                    if skipped_bytes is not None:
                        skipped_bytes[0] += existing_size
                    return False
                if size_ok and not sha1:
                    return False
            except OSError:
                pass

        errors: list[str] = []
        file_connections = (
            self.segments
            if connections is None
            else max(1, min(16, int(connections)))
        )
        for candidate in mirror_urls(url, self.effective_source):
            self._check_cancel()
            last = [0]

            def on_bytes(done: int, _total: int) -> None:
                delta = done - last[0]
                last[0] = done
                self._add_bytes(delta)

            try:
                download_to_file(
                    candidate,
                    dest,
                    sha1=sha1,
                    size=size,
                    cancel_event=self.cancel_event,
                    progress_cb=on_bytes,
                    connections=file_connections,
                )
                report_success(candidate)
                return True
            except DownloadCancelled:
                raise InstallCancelled() from None
            except Exception as exc:
                report_failure(candidate, exc)
                errors.append(str(exc))
                time.sleep(0.25)
                continue

        raise DownloadError(
            f"{label or dest.name} 下载失败：{errors[-1] if errors else '未知错误'}"
        )

    # ------------------------------------------------------------------
    # 版本 JSON

    def _ensure_json(self, version_id: str, url: str = "") -> Path:
        path = version_json_path(self.game_dir, version_id)
        if path.exists():
            return path

        sources: list[str] = []
        if self.effective_source == "bmclapi":
            sources.append(
                f"https://bmclapi2.bangbang93.com/version/{version_id}/json"
            )
        if url:
            sources.append(url)
        sources.append(
            f"https://bmclapi2.bangbang93.com/version/{version_id}/json"
        )

        last_error: Exception | None = None
        for src in dict.fromkeys(sources):
            self._check_cancel()
            try:
                data = fetch_json(src)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                report_success(src)
                return path
            except InstallCancelled:
                raise
            except Exception as exc:
                report_failure(src, exc)
                last_error = exc
                self._check_cancel()
        raise DownloadError(f"获取版本 {version_id} 的 JSON 失败：{last_error}")

    def _manifest_version_url(self, version_id: str) -> str:
        try:
            manifest = get_manifest(self.effective_source)
            for item in manifest.get("versions") or []:
                if str(item.get("id")) == version_id:
                    return str(item.get("url") or "")
        except Exception:
            pass
        return ""

    # ------------------------------------------------------------------
    # 安装入口

    def install(
        self,
        version_id: str,
        version_url: str = "",
        verify: bool = False,
        check_assets: bool = True,
    ) -> None:
        self._set_progress(1, "准备安装…")
        self.log(f"开始处理版本 {version_id}")
        self._resolve_source()
        self.log(
            "下载线路：BMCLAPI 镜像"
            if self.effective_source == "bmclapi"
            else "下载线路：Mojang 官方"
        )

        self._ensure_json(version_id, version_url)
        # 逐级补齐父版本 JSON（例如 Forge 的父级原版）
        current_id = version_id
        seen: set[str] = set()
        while current_id and current_id not in seen:
            seen.add(current_id)
            path = version_json_path(self.game_dir, current_id)
            if not path.exists():
                self._ensure_json(current_id, self._manifest_version_url(current_id))
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise DownloadError(f"版本 JSON 损坏：{exc}") from exc
            current_id = str(data.get("inheritsFrom") or "")

        resolved = resolve_version(self.game_dir, version_id)

        if resolved.client_download.get("url"):
            self._install_client(resolved, verify=verify)
        elif not client_jar_path(self.game_dir, resolved).exists():
            raise DownloadError(
                "版本缺少客户端 jar，且没有可用的下载地址。请先安装对应的原版。"
            )

        self._install_libraries(resolved, verify=verify)
        self._install_natives(resolved)
        self._install_assets(resolved, verify=verify)
        self._install_logging(resolved, verify=verify)

        remaining = check_installation(
            self.game_dir, resolved, check_assets=check_assets
        )
        if remaining:
            raise DownloadError(
                "安装未完成，仍缺少：" + "、".join(remaining[:5])
            )

        self._set_progress(100, "安装完成")
        self.log(f"{version_id} 已准备就绪")

    # ------------------------------------------------------------------
    # 客户端 / 依赖库 / natives

    def _install_client(self, resolved: ResolvedVersion, verify: bool = False) -> None:
        info = resolved.client_download
        url = str(info.get("url", ""))
        dest = self.game_dir / "versions" / resolved.jar_id / f"{resolved.jar_id}.jar"
        fallback = (
            f"https://bmclapi2.bangbang93.com/version/{resolved.jar_id}/client"
        )
        urls = mirror_urls(url, self.effective_source)
        if self.effective_source == "bmclapi":
            urls.insert(0, fallback)
        elif fallback not in urls:
            urls.append(fallback)
        self.log(f"检查客户端 jar：{resolved.jar_id}.jar")
        existing = dest.stat().st_size if dest.exists() else 0
        total = int(info.get("size") or 0) or 1
        self._set_progress(
            self._stage_percent(self.STAGE_CLIENT, min(existing, total), total),
            "检查客户端文件…",
        )
        last_error: Exception | None = None
        for candidate in dict.fromkeys(urls):
            try:
                self._download_file(
                    candidate,
                    dest,
                    sha1=str(info.get("sha1", "")),
                    size=int(info.get("size") or 0),
                    verify=verify,
                    label="客户端 jar",
                )
                self._set_progress(self.STAGE_CLIENT[1], "客户端文件就绪")
                return
            except InstallCancelled:
                raise
            except Exception as exc:
                last_error = exc
        raise DownloadError(f"客户端 jar 下载失败：{last_error}")

    @staticmethod
    def _library_connections(size: int, segments: int) -> int:
        """依赖库单文件的分段数：小文件别为几十 KB 反复握手。"""
        size = max(0, int(size))
        if size < 1024 * 1024:
            return 1
        if size < 4 * 1024 * 1024:
            return min(segments, 2)
        return min(segments, 4)

    def _install_libraries(self, resolved: ResolvedVersion, verify: bool = False) -> None:
        arts = active_library_artifacts(resolved)
        native_arts = [n.artifact for n in active_natives(resolved)]
        all_arts = []
        seen: set[str] = set()
        for art in arts + native_arts:
            if art.path and art.path not in seen:
                seen.add(art.path)
                all_arts.append(art)

        # 大文件先下，避免最后只剩一个几十 MB 的依赖库拖住整个进度。
        all_arts.sort(key=lambda art: max(0, int(art.size)), reverse=True)
        total = len(all_arts)
        total_bytes = sum(max(0, int(art.size)) for art in all_arts)
        unknown_sizes = sum(1 for art in all_arts if int(art.size) <= 0)
        self.log(
            f"检查依赖库（{total} 个文件，"
            f"{total_bytes / 1024 / 1024:.1f} MB）"
        )
        done = 0
        done_bytes = 0
        failures: list[str] = []
        lock = threading.Lock()

        def worker(art) -> None:
            nonlocal done, done_bytes
            self._check_cancel()
            dest = libraries_dir(self.game_dir) / art.path
            succeeded = False
            try:
                if not (dest.exists() and not verify):
                    self._download_file(
                        art.url,
                        dest,
                        sha1=art.sha1,
                        size=art.size,
                        verify=verify,
                        label=art.name or Path(art.path).name,
                        connections=self._library_connections(
                            art.size, self.segments
                        ),
                    )
                succeeded = True
            except InstallCancelled:
                raise
            except Exception as exc:
                with lock:
                    if len(failures) < 50:
                        failures.append(str(exc))
            finally:
                with lock:
                    done += 1
                    # 失败的文件没有真正写完，不能把它的完整大小记进
                    # 已完成字节，否则最后一个文件还在重试时进度就先满了。
                    if succeeded:
                        done_bytes += max(0, int(art.size))
                    current = done
                    current_bytes = done_bytes
                if total_bytes:
                    percent = self._stage_percent(
                        self.STAGE_LIBS, current_bytes, total_bytes
                    )
                    if current < total:
                        # 有文件大小未知时，字节进度可能先到 100；
                        # 文件计数进度兜底，避免最后一个文件还在下载
                        # 时进度条已经走满。
                        percent = min(
                            percent,
                            self._stage_percent(
                                self.STAGE_LIBS, current, total
                            ),
                        )
                else:
                    percent = self._stage_percent(
                        self.STAGE_LIBS, current, total
                    )
                detail = ""
                if total_bytes and not unknown_sizes:
                    detail = (
                        f" · {current_bytes / 1024 / 1024:.1f}"
                        f"/{total_bytes / 1024 / 1024:.1f} MB"
                    )
                self._set_progress(
                    percent,
                    f"下载依赖库 {current}/{total}" + detail,
                )

        if total:
            with ThreadPoolExecutor(
                max_workers=max(8, min(20, self.threads + 4))
            ) as pool:
                futures = [pool.submit(worker, art) for art in all_arts]
                try:
                    for future in as_completed(futures):
                        future.result()
                except InstallCancelled:
                    for future in futures:
                        future.cancel()
                    raise
        else:
            self.log("没有需要下载的依赖库")

        if failures:
            raise DownloadError(
                f"{len(failures)} 个依赖库下载失败：{failures[0]}"
            )
        self._set_progress(self.STAGE_LIBS[1], "依赖库就绪")

    def _install_natives(self, resolved: ResolvedVersion) -> None:
        natives = active_natives(resolved)
        target = natives_dir(self.game_dir, resolved)
        target.mkdir(parents=True, exist_ok=True)
        for sub in ("java", "jna", "lwjgl", "netty"):
            (target / sub).mkdir(parents=True, exist_ok=True)

        if not natives:
            return

        self.log(f"解压运行库（{len(natives)} 个）")
        for native in natives:
            self._check_cancel()
            art = native.artifact
            jar_path = libraries_dir(self.game_dir) / art.path
            if not jar_path.exists():
                self._download_file(
                    art.url,
                    jar_path,
                    sha1=art.sha1,
                    size=art.size,
                    label=f"运行库 {art.name}",
                )
            try:
                with zipfile.ZipFile(jar_path) as zf:
                    for item in zf.infolist():
                        if item.is_dir():
                            continue
                        name = item.filename.replace("\\", "/")
                        if name.startswith("META-INF/"):
                            continue
                        if any(name.startswith(prefix) for prefix in native.excludes):
                            continue
                        relative = Path(name)
                        if relative.is_absolute() or ".." in relative.parts:
                            continue
                        dest = target / relative
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(item) as src, dest.open("wb") as out:
                            shutil.copyfileobj(src, out)
            except Exception as exc:
                raise DownloadError(f"解压运行库失败：{exc}") from exc
        self._set_progress(self.STAGE_LIBS[1], "运行库就绪")

    # ------------------------------------------------------------------
    # 资源文件

    def _install_assets(self, resolved: ResolvedVersion, verify: bool = False) -> None:
        index_path = asset_index_path(self.game_dir, resolved)
        if not index_path.exists():
            url = resolved.asset_index_url
            if not url:
                raise DownloadError("版本缺少资源索引下载地址")
            self._set_progress(
                self._stage_percent(self.STAGE_ASSETS, 0, 100),
                "下载资源索引…",
            )
            self._download_file(
                url,
                index_path,
                sha1=resolved.asset_index_sha1,
                size=resolved.asset_index_size,
                label="资源索引",
            )

        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise DownloadError(f"资源索引损坏：{exc}") from exc

        objects = index.get("objects") or {}
        total = len(objects)
        self.log(f"检查资源文件（{total} 个）")
        if total == 0:
            return

        objects_root = assets_dir(self.game_dir) / "objects"
        tasks: list[tuple[str, int, Path]] = []
        completed = 0
        for _name, obj in objects.items():
            h = str(obj.get("hash", ""))
            if not h:
                continue
            dest = objects_root / h[:2] / h
            size = int(obj.get("size") or 0)
            if dest.exists():
                try:
                    size_ok = not size or dest.stat().st_size == size
                    if size_ok and not verify:
                        completed += 1
                        continue
                    if size_ok and _sha1_file(dest) == h:
                        completed += 1
                        continue
                except OSError:
                    pass
            tasks.append((h, size, dest))

        self._set_progress(
            self._stage_percent(self.STAGE_ASSETS, completed, total),
            f"资源文件 {completed}/{total}",
        )

        failures: list[str] = []
        failure_lock = threading.Lock()
        progress_lock = threading.Lock()

        def worker(task: tuple[str, int, Path]) -> None:
            h, size, dest = task
            self._check_cancel()
            url = f"https://resources.download.minecraft.net/{h[:2]}/{h}"
            try:
                self._download_file(
                    url,
                    dest,
                    sha1=h,
                    size=size,
                    verify=verify,
                    label="资源文件",
                )
            except InstallCancelled:
                raise
            except Exception as exc:
                with failure_lock:
                    if len(failures) < 50:
                        failures.append(str(exc))
            finally:
                nonlocal completed
                with progress_lock:
                    completed += 1
                    current = completed
                self._set_progress(
                    self._stage_percent(self.STAGE_ASSETS, current, total),
                    f"正在补全资源文件 {current}/{total}",
                )

        if tasks:
            self.log(f"需要下载 {len(tasks)} 个资源文件")
            with ThreadPoolExecutor(
                max_workers=max(8, min(32, self.threads * 3))
            ) as pool:
                futures = [pool.submit(worker, task) for task in tasks]
                try:
                    for future in as_completed(futures):
                        future.result()
                except InstallCancelled:
                    for future in futures:
                        future.cancel()
                    raise
        else:
            self.log("资源文件完整")

        if failures:
            raise DownloadError(
                f"{len(failures)} 个资源文件下载失败：{failures[0]}"
            )

        self._copy_legacy_assets(index, objects, verify=verify)
        self._set_progress(self.STAGE_ASSETS[1], "资源文件就绪")

    def _copy_legacy_assets(
        self,
        index: dict[str, Any],
        objects: dict[str, Any],
        verify: bool = False,
    ) -> None:
        asset_id = str(index.get("id") or "")
        virtual = bool(index.get("virtual"))
        map_resources = bool(index.get("map_to_resources"))
        if not virtual and not map_resources:
            return

        self.log("正在生成旧版本资源目录…")
        objects_root = assets_dir(self.game_dir) / "objects"
        destinations: list[Path] = []
        if virtual and asset_id:
            destinations.append(assets_dir(self.game_dir) / "virtual" / asset_id)
        if map_resources:
            destinations.append(self.game_dir / "resources")

        for base in destinations:
            for name, obj in objects.items():
                h = str(obj.get("hash", ""))
                if not h:
                    continue
                src = objects_root / h[:2] / h
                if not src.exists():
                    continue
                relative = Path(str(name).replace("\\", "/"))
                if relative.is_absolute() or ".." in relative.parts:
                    continue
                dest = base / relative
                if dest.exists():
                    if not verify:
                        continue
                    try:
                        if _sha1_file(dest) == h:
                            continue
                    except OSError:
                        pass
                    try:
                        dest.unlink()
                    except OSError:
                        continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.link(src, dest)
                except OSError:
                    shutil.copy2(src, dest)

    # ------------------------------------------------------------------
    # 日志配置

    def _install_logging(self, resolved: ResolvedVersion, verify: bool = False) -> None:
        client = (resolved.logging or {}).get("client") or {}
        file_info = client.get("file") or {}
        file_id = str(file_info.get("id") or "")
        if not file_id:
            return
        dest = assets_dir(self.game_dir) / "log_configs" / file_id
        if dest.exists() and not verify:
            return
        self._download_file(
            str(file_info.get("url", "")),
            dest,
            sha1=str(file_info.get("sha1", "")),
            size=int(file_info.get("size") or 0),
            verify=verify,
            label="日志配置",
        )
        self._set_progress(self.STAGE_FINISH[0], "日志配置就绪")
