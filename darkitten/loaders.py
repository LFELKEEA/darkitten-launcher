from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from .config import APP_DIR
from .installer import (
    BMCL_MAVEN,
    InstallCancelled,
    Installer,
    _http_open,
    mirror_urls,
)
from .versions import scan_versions, version_json_path

ProgressCallback = Callable[[int, str], None]
LogCallback = Callable[[str], None]
ProcessCallback = Callable[[subprocess.Popen | None], None]

FABRIC_META_BMCL = "https://bmclapi2.bangbang93.com/fabric-meta/v2"
FABRIC_META = "https://meta.fabricmc.net/v2"
FORGE_LIST_BMCL = "https://bmclapi2.bangbang93.com/forge/minecraft/{mc}"
FORGE_LIST_OFFICIAL = (
    "https://files.minecraftforge.net/net/minecraftforge/forge/maven-metadata.json"
)
NEOFORGE_META_BMCL = (
    "https://bmclapi2.bangbang93.com/neoforge/meta/api/maven/details/"
    "releases/net/neoforged/{group}"
)
NEOFORGE_OFFICIAL_MAVEN = "https://maven.neoforged.net/releases"
FORGE_OFFICIAL_MAVEN = "https://maven.minecraftforge.net"
INSTALLER_CACHE = APP_DIR / "cache" / "loaders"
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
CENTRAL_MAVEN = "https://repo1.maven.org/maven2/"
CREEPER_MAVEN = "https://maven.creeperhost.net/"

LOADER_NAMES = {"fabric": "Fabric", "neoforge": "NeoForge", "forge": "Forge"}


class LoaderError(Exception):
    pass


# ---------------------------------------------------------------------------
# 基础网络工具


def _get_json(url: str, timeout: int = 30) -> Any:
    with _http_open(url, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8"))


def _first_json(urls: list[str], timeout: int = 30) -> Any:
    errors: list[str] = []
    for url in urls:
        try:
            return _get_json(url, timeout=timeout)
        except Exception as exc:
            errors.append(f"{url}：{exc}")
    raise LoaderError("；".join(errors[-2:]) or "网络请求失败")


def _version_key(value: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", str(value)))


def _check_cancel(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise InstallCancelled()


def _sha1_file(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as fp:
        while True:
            chunk = fp.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _download(
    urls: list[str],
    dest: Path,
    *,
    sha1: str = "",
    size: int = 0,
    label: str = "",
    cancel_event: threading.Event | None = None,
) -> bool:
    """按顺序尝试多个地址下载文件；返回是否真正下载。"""
    _check_cancel(cancel_event)
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.exists():
        try:
            size_ok = (not size) or dest.stat().st_size == size
            if size_ok and (not sha1 or _sha1_file(dest) == sha1):
                return False
        except OSError:
            pass

    part = dest.with_name(dest.name + ".part")
    errors: list[str] = []
    for url in dict.fromkeys(urls):
        # 同一地址重试一次，利用 .part 断点续传，慢速镜像中途断开也能续上。
        for _attempt in range(2):
            _check_cancel(cancel_event)
            offset = part.stat().st_size if part.exists() else 0
            headers = {"Range": f"bytes={offset}-"} if offset else None
            try:
                with _http_open(url, timeout=45, headers=headers) as resp:
                    status = getattr(resp, "status", 200)
                    if offset and status != 206:
                        offset = 0
                    if status == 206:
                        match = re.search(
                            r"/(\d+)\s*$", resp.headers.get("Content-Range", "")
                        )
                        total = int(match.group(1)) if match else 0
                    else:
                        total = int(resp.headers.get("Content-Length") or 0)
                    expected = size or total
                    if expected and offset > expected:
                        offset = 0
                    digest = hashlib.sha1()
                    if offset:
                        with part.open("rb") as fp:
                            while True:
                                chunk = fp.read(1024 * 1024)
                                if not chunk:
                                    break
                                digest.update(chunk)
                    written = offset
                    with part.open("ab" if offset else "wb") as fp:
                        while True:
                            _check_cancel(cancel_event)
                            chunk = resp.read(256 * 1024)
                            if not chunk:
                                break
                            fp.write(chunk)
                            digest.update(chunk)
                            written += len(chunk)
                    if expected and written != expected:
                        raise LoaderError(
                            f"文件大小不一致（期望 {expected}，实际 {written}）"
                        )
                    if sha1 and digest.hexdigest().lower() != sha1.lower():
                        raise LoaderError("SHA1 校验失败")
                os.replace(part, dest)
                return True
            except InstallCancelled:
                try:
                    part.unlink(missing_ok=True)
                except OSError:
                    pass
                raise
            except Exception as exc:
                errors.append(str(exc))
                text = str(exc)
                if "SHA1" in text or "大小不一致" in text:
                    try:
                        part.unlink(missing_ok=True)
                    except OSError:
                        pass
                time.sleep(0.15)

    try:
        part.unlink(missing_ok=True)
    except OSError:
        pass
    raise LoaderError(f"{label or dest.name} 下载失败：{errors[-1] if errors else '未知错误'}")


# ---------------------------------------------------------------------------
# 版本列表


def fetch_fabric_versions(mc_version: str) -> list[dict[str, Any]]:
    try:
        data = _first_json(
            [
                f"{FABRIC_META_BMCL}/versions/loader/{mc_version}",
                f"{FABRIC_META}/versions/loader/{mc_version}",
            ]
        )
    except LoaderError as exc:
        text = str(exc)
        if "HTTP Error 400" in text or "HTTP Error 404" in text:
            raise LoaderError(
                f"Fabric 目前没有适配 Minecraft {mc_version} 的版本，"
                "请确认游戏版本是否正确"
            ) from exc
        raise
    if not isinstance(data, list):
        raise LoaderError("Fabric 版本列表格式不正确")
    result: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        loader = item.get("loader") or {}
        version = str(loader.get("version") or "")
        if version:
            result.append(
                {
                    "version": version,
                    "stable": bool(loader.get("stable")),
                }
            )
    result.sort(key=lambda x: _version_key(x["version"]), reverse=True)
    return result


def _short_forge_version(mc_version: str, value: str) -> str:
    prefix = f"{mc_version}-"
    return value[len(prefix) :] if value.startswith(prefix) else value


def fetch_forge_versions(mc_version: str) -> list[str]:
    try:
        data = _first_json([FORGE_LIST_BMCL.format(mc=mc_version)])
        result: list[str] = []
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    version = str(item.get("version") or "")
                    if version:
                        result.append(_short_forge_version(mc_version, version))
        if result:
            return sorted(dict.fromkeys(result), key=_version_key, reverse=True)
    except LoaderError:
        pass

    data = _first_json([FORGE_LIST_OFFICIAL])
    if isinstance(data, dict):
        values = data.get(mc_version) or []
        if isinstance(values, list):
            return sorted(
                {
                    _short_forge_version(mc_version, str(v))
                    for v in values
                    if v
                },
                key=_version_key,
                reverse=True,
            )
    raise LoaderError(f"没有找到 Minecraft {mc_version} 的 Forge 版本")


def _neoforge_group(mc_version: str) -> tuple[str, str]:
    """返回 (maven group, 版本前缀)。"""
    if mc_version == "1.20.1":
        return "forge", "1.20.1-"
    parts = mc_version.split(".")
    if len(parts) >= 2 and parts[0] == "1":
        major = parts[1]
        minor = parts[2] if len(parts) >= 3 else "0"
    else:
        major = parts[0] if parts else "0"
        minor = parts[1] if len(parts) >= 2 else "0"
    return "neoforge", f"{major}.{minor}."


def fetch_neoforge_versions(mc_version: str) -> list[str]:
    group, prefix = _neoforge_group(mc_version)
    data = _first_json([NEOFORGE_META_BMCL.format(group=group)])
    if not isinstance(data, dict):
        raise LoaderError("NeoForge 版本列表格式不正确")
    names: list[str] = []
    for item in data.get("files") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        if item.get("type") != "DIRECTORY" and not name:
            continue
        if name.startswith(prefix) or (group == "forge" and name.startswith(prefix)):
            names.append(name)
    names.sort(key=_version_key, reverse=True)
    if not names:
        raise LoaderError(
            f"NeoForge 目前没有适用于 Minecraft {mc_version} 的版本"
        )
    return names


def loader_versions(kind: str, mc_version: str) -> list[dict[str, Any]]:
    if kind == "fabric":
        return fetch_fabric_versions(mc_version)
    if kind == "forge":
        return [
            {"version": v, "stable": False}
            for v in fetch_forge_versions(mc_version)
        ]
    if kind == "neoforge":
        return [
            {"version": v, "stable": False}
            for v in fetch_neoforge_versions(mc_version)
        ]
    raise LoaderError("未知的加载器类型")


# ---------------------------------------------------------------------------
# 安装器 / 依赖预下载


def _ensure_launcher_profiles(game_dir: Path) -> None:
    path = game_dir / "launcher_profiles.json"
    if path.exists():
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"profiles": {}, "settings": {}, "version": 3}),
            encoding="utf-8",
        )
    except OSError:
        pass


def _installer_urls(kind: str, mc_version: str, loader_version: str) -> tuple[list[str], str]:
    if kind == "forge":
        if loader_version.startswith(f"{mc_version}-"):
            loader_version = loader_version[len(mc_version) + 1 :]
        name = f"forge-{mc_version}-{loader_version}-installer.jar"
        rel = f"net/minecraftforge/forge/{mc_version}-{loader_version}/{name}"
        return (
            [
                BMCL_MAVEN + rel,
                CREEPER_MAVEN + rel,
                f"{FORGE_OFFICIAL_MAVEN}/{rel}",
            ],
            name,
        )
    if loader_version.startswith("1.20.1-"):
        # NeoForge 1.20.1 的构件发布在 net/neoforged/forge 下，
        # 文件名沿用 Forge 的 forge-<版本>-installer.jar。
        name = f"forge-{loader_version}-installer.jar"
        rel = f"net/neoforged/forge/{loader_version}/{name}"
    else:
        name = f"neoforge-{loader_version}-installer.jar"
        rel = f"net/neoforged/neoforge/{loader_version}/{name}"
    urls = [
        BMCL_MAVEN + rel,
        CREEPER_MAVEN + rel,
        f"{NEOFORGE_OFFICIAL_MAVEN}/{rel}",
        f"https://bmclapi2.bangbang93.com/neoforge/version/{loader_version}/download/installer.jar",
    ]
    return urls, name


def _patch_installer(source: Path, dest: Path, kind: str) -> None:
    """把安装器内的 Maven 地址替换为国内可用的镜像。"""
    try:
        with zipfile.ZipFile(source) as zin, zipfile.ZipFile(
            dest, "w", zipfile.ZIP_DEFLATED
        ) as zout:
            for info in zin.infolist():
                name = info.filename
                # 修改内容会让原 jar 的签名校验失败，重打包时移除签名文件。
                if name.upper().startswith("META-INF/") and name.upper().endswith(
                    (".SF", ".RSA", ".DSA", ".EC")
                ):
                    continue
                data = zin.read(info.filename)
                if info.filename.endswith((".json", ".txt", ".properties")):
                    text = data.decode("utf-8", "replace")
                    if kind == "neoforge":
                        text = text.replace(
                            "https://maven.neoforged.net/releases/", BMCL_MAVEN
                        )
                        text = text.replace(
                            "https://maven.neoforged.net/", BMCL_MAVEN
                        )
                        text = text.replace(
                            '"mirrorList": "https://mirrors.neoforged.net"',
                            '"mirrorList": ""',
                        )
                        text = text.replace(
                            '"mirrorList":"https://mirrors.neoforged.net"',
                            '"mirrorList":""',
                        )
                    else:
                        text = text.replace(
                            "https://maven.minecraftforge.net/", BMCL_MAVEN
                        )
                        text = text.replace(
                            "http://maven.minecraftforge.net/", BMCL_MAVEN
                        )
                        text = text.replace(
                            "http://files.minecraftforge.net/maven/", BMCL_MAVEN
                        )
                        text = text.replace(
                            '"mirrorList": "https://files.minecraftforge.net/mirrors-2.0.json"',
                            '"mirrorList": ""',
                        )
                        text = text.replace(
                            '"mirrorList":"https://files.minecraftforge.net/mirrors-2.0.json"',
                            '"mirrorList":""',
                        )
                    data = text.encode("utf-8")
                zout.writestr(info, data)
    except Exception as exc:
        raise LoaderError(f"准备 {LOADER_NAMES[kind]} 安装器失败：{exc}") from exc


def _installer_libraries(jar: Path) -> list[dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    try:
        with zipfile.ZipFile(jar) as zf:
            for name in ("install_profile.json", "version.json"):
                if name not in zf.namelist():
                    continue
                try:
                    data = json.loads(zf.read(name).decode("utf-8"))
                except Exception:
                    continue
                for lib in data.get("libraries") or []:
                    if not isinstance(lib, dict):
                        continue
                    art = (lib.get("downloads") or {}).get("artifact") or {}
                    path = str(art.get("path") or "")
                    url = str(art.get("url") or "")
                    if not path or not url:
                        continue
                    entries[path] = {
                        "path": path,
                        "url": url,
                        "sha1": str(art.get("sha1") or ""),
                        "size": int(art.get("size") or 0),
                        "name": str(lib.get("name") or path),
                    }
    except Exception as exc:
        raise LoaderError(f"读取安装器依赖失败：{exc}") from exc
    return list(entries.values())


def _preseed_libraries(
    game_dir: Path,
    jar: Path,
    *,
    progress_cb: ProgressCallback,
    log_cb: LogCallback,
    cancel_event: threading.Event | None,
) -> int:
    libs = _installer_libraries(jar)
    if not libs:
        return 0

    total = len(libs)
    log_cb(f"预下载加载器依赖（{total} 个）")
    done = 0
    failed = 0
    lock = threading.Lock()

    def task(lib: dict[str, Any]) -> bool:
        path = str(lib["path"])
        dest = game_dir / "libraries" / path
        bmcl_url = BMCL_MAVEN + path
        creeper_url = CREEPER_MAVEN + path
        central_url = CENTRAL_MAVEN + path
        # Forge/NeoForge 自有构件在 Central 上没有，先走 BMCL；
        # 其余第三方库优先 Central，速度通常更快。
        if path.startswith(
            ("net/minecraftforge/", "net/neoforged/", "de/oceanlabs/",
             "com/nothome/", "trove/")
        ):
            urls = [bmcl_url, creeper_url]
        else:
            urls = [central_url, bmcl_url, creeper_url]
        urls += mirror_urls(str(lib["url"]), "bmclapi")
        urls.append(central_url)
        if not str(lib["url"]).startswith("https://libraries.minecraft.net/"):
            urls.append(FORGE_OFFICIAL_MAVEN + "/" + path)
            urls.append(f"{NEOFORGE_OFFICIAL_MAVEN}/{path}")
        seen: set[str] = set()
        urls = [u for u in urls if not (u in seen or seen.add(u))]
        try:
            _download(
                urls,
                dest,
                sha1=str(lib.get("sha1") or ""),
                size=int(lib.get("size") or 0),
                label=str(lib.get("name") or path),
                cancel_event=cancel_event,
            )
            return True
        except InstallCancelled:
            raise
        except Exception as exc:
            log_cb(f"依赖预下载失败：{path}（{exc}）")
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(task, lib) for lib in libs]
        try:
            for future in as_completed(futures):
                try:
                    ok = future.result()
                except InstallCancelled:
                    for f in futures:
                        f.cancel()
                    raise
                with lock:
                    done += 1
                    if not ok:
                        failed += 1
                    current = done
                progress_cb(
                    12 + int(43 * current / total),
                    f"准备加载器依赖 {current}/{total}",
                )
        except InstallCancelled:
            for f in futures:
                f.cancel()
            raise

    if failed:
        log_cb(f"{failed} 个依赖未能预下载，安装器会再尝试一次")
    return failed


def _run_installer(
    java_path: str,
    jar: Path,
    game_dir: Path,
    *,
    log_cb: LogCallback,
    cancel_event: threading.Event | None,
    process_cb: ProcessCallback | None,
) -> None:
    cmd = [
        java_path,
        "-jar",
        str(jar),
        "--installClient",
        str(game_dir),
    ]
    log_cb("运行安装器：" + subprocess.list2cmdline(cmd))
    startupinfo = None
    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(game_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=CREATE_NO_WINDOW,
            startupinfo=startupinfo,
        )
    except Exception as exc:
        raise LoaderError(f"无法启动安装器：{exc}") from exc

    if process_cb:
        process_cb(proc)
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            _check_cancel(cancel_event)
            text = line.rstrip("\r\n")
            if text:
                log_cb(text)
        code = proc.wait()
    finally:
        if process_cb:
            process_cb(None)

    if cancel_event is not None and cancel_event.is_set():
        raise InstallCancelled()
    if code != 0:
        raise LoaderError(f"安装器退出代码 {code}，请查看上方日志")


def _find_loader_version(
    game_dir: Path,
    mc_version: str,
    kind: str,
    loader_version: str,
    before: set[str],
) -> str:
    candidates = []
    for summary in scan_versions(game_dir):
        if summary.id == mc_version:
            continue
        if summary.id in before:
            continue
        if summary.loader != LOADER_NAMES[kind]:
            continue
        score = 0
        if loader_version in summary.id:
            score += 2
        candidates.append((score, summary.folder_mtime, summary.id))
    if candidates:
        candidates.sort(reverse=True)
        return candidates[0][2]

    # 安装器生成的版本 ID 不一定带完整的加载器版本（例如 NeoForge 1.20.1
    # 会生成 1.20.1-forge-47.1.106），重装时该目录也已在 before 集合里，
    # 因此这里用兼容的短版本号做一次确定性查找。
    short_version = loader_version
    if short_version.startswith(f"{mc_version}-"):
        short_version = short_version[len(mc_version) + 1 :]
    expected: list[str] = []
    if kind == "forge":
        expected.append(f"{mc_version}-forge-{short_version}")
    else:
        expected.extend(
            [
                f"neoforge-{loader_version}",
                f"{mc_version}-neoforge-{loader_version}",
                f"{mc_version}-forge-{short_version}",
                f"{mc_version}-neoforge-{short_version}",
            ]
        )
    for version_id in dict.fromkeys(expected):
        if version_json_path(game_dir, version_id).exists():
            return version_id
    raise LoaderError("安装器已结束，但没有找到生成的版本，请查看日志")


# ---------------------------------------------------------------------------
# 安装入口


def _install_fabric(
    game_dir: Path,
    mc_version: str,
    loader_version: str,
    *,
    source: str,
    progress_cb: ProgressCallback,
    log_cb: LogCallback,
    cancel_event: threading.Event | None,
) -> str:
    progress_cb(3, "获取 Fabric 配置…")
    try:
        data = _first_json(
            [
                f"{FABRIC_META_BMCL}/versions/loader/{mc_version}/{loader_version}/profile/json",
                f"{FABRIC_META}/versions/loader/{mc_version}/{loader_version}/profile/json",
            ]
        )
    except LoaderError as exc:
        text = str(exc)
        if "HTTP Error 400" in text or "HTTP Error 404" in text:
            raise LoaderError(
                f"Fabric 没有找到 Minecraft {mc_version} 与加载器 "
                f"{loader_version} 的组合，请重新选择版本"
            ) from exc
        raise
    if not isinstance(data, dict) or not data.get("libraries"):
        raise LoaderError("Fabric 返回的版本配置不完整")
    version_id = str(data.get("id") or f"fabric-loader-{loader_version}-{mc_version}")
    data["id"] = version_id
    data.setdefault("inheritsFrom", mc_version)

    path = version_json_path(game_dir, version_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    log_cb(f"已生成 Fabric 版本配置：{version_id}")

    progress_cb(8, "补全原版与 Fabric 依赖…")
    installer = Installer(
        game_dir,
        source=source,
        progress_cb=lambda p, m: progress_cb(8 + int(p * 0.92), m),
        log_cb=log_cb,
        cancel_event=cancel_event,
    )
    installer.install(version_id, verify=False, check_assets=True)
    return version_id


def _install_forge_family(
    game_dir: Path,
    mc_version: str,
    loader_version: str,
    kind: str,
    *,
    java_path: str,
    source: str,
    progress_cb: ProgressCallback,
    log_cb: LogCallback,
    cancel_event: threading.Event | None,
    process_cb: ProcessCallback | None,
) -> str:
    name = LOADER_NAMES[kind]
    INSTALLER_CACHE.mkdir(parents=True, exist_ok=True)
    urls, filename = _installer_urls(kind, mc_version, loader_version)
    installer_path = INSTALLER_CACHE / f"{kind}-{mc_version}-{loader_version}.jar"

    progress_cb(2, f"下载 {name} 安装器…")
    log_cb(f"下载 {name} 安装器 {loader_version}")
    _download(
        urls,
        installer_path,
        label=f"{name} 安装器",
        cancel_event=cancel_event,
    )

    run_path = INSTALLER_CACHE / f"{kind}-{mc_version}-{loader_version}-bmcl.jar"
    _patch_installer(installer_path, run_path, kind)
    log_cb(f"已将 {name} 安装器切换到 BMCLAPI 镜像")

    _ensure_launcher_profiles(game_dir)
    before = {v.id for v in scan_versions(game_dir)}
    _preseed_libraries(
        game_dir,
        run_path,
        progress_cb=progress_cb,
        log_cb=log_cb,
        cancel_event=cancel_event,
    )

    progress_cb(57, f"运行 {name} 安装器…")
    _run_installer(
        java_path,
        run_path,
        game_dir,
        log_cb=log_cb,
        cancel_event=cancel_event,
        process_cb=process_cb,
    )

    version_id = _find_loader_version(
        game_dir, mc_version, kind, loader_version, before
    )
    log_cb(f"{name} 安装器已生成版本：{version_id}")

    progress_cb(88, "补全加载器与游戏文件…")
    installer = Installer(
        game_dir,
        source=source,
        progress_cb=lambda p, m: progress_cb(88 + int(p * 0.12), m),
        log_cb=log_cb,
        cancel_event=cancel_event,
    )
    installer.install(version_id, verify=False, check_assets=True)
    return version_id


def install_loader(
    game_dir: Path,
    mc_version: str,
    kind: str,
    loader_version: str,
    *,
    java_path: str = "",
    source: str = "official",
    progress_cb: ProgressCallback | None = None,
    log_cb: LogCallback | None = None,
    cancel_event: threading.Event | None = None,
    process_cb: ProcessCallback | None = None,
) -> str:
    game_dir = Path(game_dir)
    progress = progress_cb or (lambda p, m: None)
    log = log_cb or (lambda m: None)

    if kind not in LOADER_NAMES:
        raise LoaderError("未知的加载器类型")
    if kind != "fabric" and not java_path:
        raise LoaderError(f"安装 {LOADER_NAMES[kind]} 需要 Java，请先在设置中选择 Java")

    progress(1, f"准备安装 {LOADER_NAMES[kind]}…")
    if kind == "fabric":
        return _install_fabric(
            game_dir,
            mc_version,
            loader_version,
            source=source,
            progress_cb=progress,
            log_cb=log,
            cancel_event=cancel_event,
        )
    return _install_forge_family(
        game_dir,
        mc_version,
        loader_version,
        kind,
        java_path=java_path,
        source=source,
        progress_cb=progress,
        log_cb=log,
        cancel_event=cancel_event,
        process_cb=process_cb,
    )
