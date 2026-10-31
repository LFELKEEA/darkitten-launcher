from __future__ import annotations

"""宣传片音频：逐场景中文旁白 + 时间线，附带背景音乐生成。"""

import asyncio
import json
import math
import subprocess
import time
from pathlib import Path

import imageio_ffmpeg
import numpy as np
import scipy.io.wavfile as wavfile

QA = Path(__file__).resolve().parent
AUDIO = QA / "promo_audio"
AUDIO.mkdir(parents=True, exist_ok=True)

VOICE = "zh-CN-XiaoxiaoNeural"
RATE = "+6%"
SAMPLE_RATE = 48000

# 每个场景：画面素材、基础时长、标题/副标题、旁白。
# 素材时长会按场景总时长等比例缩放，旁白不会压到下一段。
SCENES = [
    {
        "id": "intro",
        "kicker": "DARKITTEN LAUNCHER",
        "title": "把 Minecraft，收进一个猫窝。",
        "bullets": ["版本 · 模组 · 联机 · 服务端"],
        "shots": [],
        "min_duration": 9.0,
        "narration": (
            "如果 Minecraft 也有一个温暖的小窝，那大概就是它。"
            "Darkitten Launcher，一只住在你电脑里的猫猫启动器。"
        ),
    },
    {
        "id": "home",
        "kicker": "01 / 首页",
        "title": "所有版本，一个猫窝",
        "bullets": ["一键启动", "加载器绑定", "存档隔离"],
        "shots": [
            {"name": "home_games", "base": 8.0, "focus": "center"},
            {"name": "home_loaders", "base": 6.0, "focus": "center"},
        ],
        "min_duration": 14.0,
        "narration": (
            "打开就是熟悉的猫窝。游戏版本、Fabric、Forge、NeoForge 加载器"
            "都排得整整齐齐。选中版本，一键启动；存档、模组和材质包各归各位，"
            "互不打扰。"
        ),
    },
    {
        "id": "download",
        "kicker": "02 / 游戏版本",
        "title": "缺哪个版本，就装哪个",
        "bullets": ["原版 / 快照 / 旧版", "智能加速", "实时进度"],
        "shots": [{"name": "download", "base": 11.0, "focus": "right"}],
        "min_duration": 11.0,
        "narration": (
            "缺版本？去游戏版本仓库里挑一个。原版、快照、旧版本都能装，"
            "智能加速会自动选择更快的线路，下载进度和日志一目了然。"
        ),
    },
    {
        "id": "loaders",
        "kicker": "03 / 加载器",
        "title": "加载器，随用随切",
        "bullets": ["Fabric", "Forge", "NeoForge"],
        "shots": [{"name": "loaders", "base": 10.0, "focus": "center"}],
        "min_duration": 10.0,
        "narration": (
            "装好加载器，再决定哪个游戏版本使用它。切换加载器不用重装游戏，"
            "存档和模组始终留在原来的窝里。"
        ),
    },
    {
        "id": "mods",
        "kicker": "04 / 模组",
        "title": "找模组，不用离开启动器",
        "bullets": ["Modrinth", "CurseForge", "一键安装"],
        "shots": [
            {"name": "mods", "base": 7.0, "focus": "center"},
            {"name": "mod_browser", "base": 10.0, "focus": "center"},
        ],
        "min_duration": 16.0,
        "narration": (
            "模组抽屉里，已安装的模组一眼看清，开关、更新、环境检查都在手边。"
            "内置 Modrinth 和 CurseForge 浏览器，搜索、看介绍、选版本、"
            "下载安装，一步到位。"
        ),
    },
    {
        "id": "skin",
        "kicker": "05 / 皮肤",
        "title": "换皮肤，先看 3D 效果",
        "bullets": ["64×64 / 64×32", "实时预览", "LittleSkin"],
        "shots": [{"name": "skin", "base": 11.0, "focus": "left"}],
        "min_duration": 11.0,
        "narration": (
            "皮肤间支持六十四乘六十四和六十四乘三十二皮肤，实时 3D 预览，"
            "多套皮肤随时切换；LittleSkin 账号也能直接登录使用。"
        ),
    },
    {
        "id": "room",
        "kicker": "06 / 联机",
        "title": "把房间号发给朋友",
        "bullets": ["P2P 直连", "樱花穿透", "模组同步"],
        "shots": [
            {"name": "room_p2p", "base": 9.0, "focus": "left"},
            {"name": "room_sakura", "base": 9.0, "focus": "left"},
        ],
        "min_duration": 18.0,
        "narration": (
            "想和朋友一起玩？P2P 直连开个房间，把房间号发过去，朋友输入就能进。"
            "没有公网 IP 也没关系，樱花穿透一键建隧道，还能同步双方的模组。"
        ),
    },
    {
        "id": "server",
        "kicker": "07 / 服务端",
        "title": "服务端，也能在启动器里养",
        "bullets": ["Paper / Fabric / Forge", "控制台", "配置翻译"],
        "shots": [
            {"name": "server_console", "base": 6.0, "focus": "right"},
            {"name": "server_settings", "base": 5.0, "focus": "right"},
            {"name": "server_config_properties", "base": 5.0, "focus": "center"},
            {"name": "server_config_plugins", "base": 5.0, "focus": "center"},
        ],
        "min_duration": 20.0,
        "narration": (
            "服务端也能在启动器里养。Paper、Spigot、Fabric、Forge、NeoForge "
            "一键安装，控制台、插件和设置集中管理。server.properties "
            "和插件配置都带中文解释，改完保存，重启生效。"
        ),
    },
    {
        "id": "tools",
        "kicker": "08 / 工具箱",
        "title": "顺手的小工具，都给你备好了",
        "bullets": ["下载中心", "版本设置", "快捷命令"],
        "shots": [
            {"name": "download_tasks", "base": 3.0, "focus": "center"},
            {"name": "version_settings", "base": 3.0, "focus": "center"},
            {"name": "command_palette", "base": 3.0, "focus": "center"},
            {"name": "pet_dialog", "base": 3.0, "focus": "center"},
            {"name": "drag_import", "base": 3.0, "focus": "center"},
        ],
        "min_duration": 15.0,
        "narration": (
            "下载中心、版本设置、快捷命令面板、桌宠和拖拽导入，"
            "都藏在顺手的位置。"
        ),
    },
    {
        "id": "settings",
        "kicker": "09 / 设置与后台",
        "title": "关掉窗口，也可以继续看家",
        "bullets": ["后台运行", "世界快照", "诊断包"],
        "shots": [
            {"name": "settings_top", "base": 5.0, "focus": "center"},
            {"name": "settings_mid", "base": 5.0, "focus": "center"},
            {"name": "settings_bottom", "base": 6.0, "focus": "center"},
            {"name": "close_choice", "base": 3.0, "focus": "center"},
        ],
        "min_duration": 18.0,
        "narration": (
            "设置里可以管理账户、内存、Java 和下载源。还有世界快照、诊断包，"
            "以及后台运行。关掉窗口时，你可以选择退出，"
            "或者让它继续在托盘里看家。"
        ),
    },
    {
        "id": "outro",
        "kicker": "DARKITTEN LAUNCHER 1.11.6",
        "title": "今天也要去捣乱吗？",
        "bullets": ["下载站 · GitHub Releases", "Windows 10 / 11"],
        "shots": [],
        "min_duration": 9.0,
        "narration": (
            "Darkitten Launcher，把启动、模组、联机和服务端，"
            "都收进一个猫窝。现在就来下载吧。"
        ),
    },
]

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


def synthesize(text: str, target: Path) -> None:
    import edge_tts

    async def run() -> None:
        communicator = edge_tts.Communicate(text, VOICE, rate=RATE)
        await communicator.save(str(target))

    last_error: Exception | None = None
    for attempt in range(4):
        try:
            asyncio.run(run())
            if target.exists() and target.stat().st_size > 1024:
                return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        time.sleep(1.5 + attempt * 1.5)
    raise RuntimeError(f"TTS 失败：{target.name} {last_error}")


def decode(src: Path, dst: Path) -> None:
    subprocess.run(
        [
            FFMPEG,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(src),
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            str(dst),
        ],
        check=True,
    )


def read_wav(path: Path) -> np.ndarray:
    rate, data = wavfile.read(path)
    if rate != SAMPLE_RATE:
        raise RuntimeError(f"采样率不对：{path} {rate}")
    if data.ndim > 1:
        data = data.mean(axis=1)
    return data.astype(np.float32) / 32768.0


def main() -> None:
    total = 0.0
    cursor = 0.0
    timeline = []
    voices: list[tuple[float, np.ndarray]] = []

    for index, scene in enumerate(SCENES, start=1):
        mp3 = AUDIO / f"voice_{index:02d}_{scene['id']}.mp3"
        wav = AUDIO / f"voice_{index:02d}_{scene['id']}.wav"
        print(f"[{index}/{len(SCENES)}] {scene['id']} 生成旁白…", flush=True)
        if not (wav.exists() and wav.stat().st_size > 1024):
            synthesize(scene["narration"], mp3)
            decode(mp3, wav)
        voice = read_wav(wav)
        voice_duration = len(voice) / SAMPLE_RATE
        shots = scene["shots"]
        base_total = sum(shot["base"] for shot in shots)
        duration = max(scene["min_duration"], voice_duration + 2.0, base_total)
        duration = round(duration, 2)

        if shots:
            scale = duration / base_total
            allocated = []
            for shot in shots:
                allocated.append(
                    {
                        "name": shot["name"],
                        "focus": shot.get("focus", "center"),
                        "duration": round(shot["base"] * scale, 3),
                    }
                )
        else:
            allocated = []

        voice_start = cursor + min(0.9, max(0.5, (duration - voice_duration) / 2))
        voices.append((voice_start, voice))
        timeline.append(
            {
                "id": scene["id"],
                "kicker": scene["kicker"],
                "title": scene["title"],
                "bullets": scene["bullets"],
                "start": round(cursor, 3),
                "duration": duration,
                "voice_start": round(voice_start, 3),
                "voice_duration": round(voice_duration, 3),
                "voice_file": wav.name,
                "shots": allocated,
            }
        )
        print(
            f"    旁白 {voice_duration:.2f}s，场景 {duration:.2f}s", flush=True
        )
        cursor += duration

    total = cursor + 1.2
    voice_track = np.zeros(int(total * SAMPLE_RATE) + 1, dtype=np.float32)
    for start, voice in voices:
        offset = int(start * SAMPLE_RATE)
        end = min(len(voice_track), offset + len(voice))
        voice_track[offset:end] += voice[: end - offset]

    peak = float(np.max(np.abs(voice_track))) or 1.0
    voice_track = voice_track / peak * 0.86
    wavfile.write(
        AUDIO / "voice_track.wav",
        SAMPLE_RATE,
        (voice_track * 32767).astype(np.int16),
    )

    payload = {"total": round(total, 2), "scenes": timeline}
    (AUDIO / "timeline.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"总时长 {total:.2f}s（{total/60:.2f} 分钟）", flush=True)


if __name__ == "__main__":
    main()
