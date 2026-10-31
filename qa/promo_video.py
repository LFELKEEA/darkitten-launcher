from __future__ import annotations

"""把截图、旁白和音乐合成 1080p 宣传片。"""

import json
import math
import sys
from pathlib import Path

import imageio_ffmpeg
import numpy as np
import scipy.io.wavfile as wavfile
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

QA = Path(__file__).resolve().parent
SHOTS = QA / "promo_shots"
AUDIO = QA / "promo_audio"
OUT = QA / "promo_output"
OUT.mkdir(parents=True, exist_ok=True)
STILLS = QA / "promo_stills"
STILLS.mkdir(parents=True, exist_ok=True)

W, H = 1920, 1080
FPS = 30
TRANS = 0.55
SHOT_TRANS = 0.40
SR = 48000
ACCENT = (226, 58, 94)
TEXT = (246, 238, 241)
MUTED = (185, 168, 174)
DIM = (130, 114, 121)
DARK = (12, 8, 14)

FONT_DIR = Path(r"C:\Windows\Fonts")
FONT_CACHE: dict[tuple[int, bool], ImageFont.FreeTypeFont] = {}


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    key = (size, bold)
    if key not in FONT_CACHE:
        FONT_CACHE[key] = ImageFont.truetype(
            str(FONT_DIR / ("msyhbd.ttc" if bold else "msyh.ttc")), size
        )
    return FONT_CACHE[key]


def ease(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return x * x * (3.0 - 2.0 * x)


def cover(img: Image.Image, w: int, h: int) -> Image.Image:
    return ImageOps.fit(img, (w, h), method=Image.Resampling.LANCZOS)


def load_shot(name: str) -> Image.Image:
    return Image.open(SHOTS / f"{name}.png").convert("RGB")


BACKDROP_MAP = {
    "mod_browser": "mods",
    "server_config_properties": "server_console",
    "server_config_plugins": "server_console",
    "download_tasks": "home_games",
    "command_palette": "home_games",
    "version_settings": "home_games",
    "pet_dialog": "home_games",
    "close_choice": "home_games",
}

def build_vignette() -> Image.Image:
    x = np.linspace(-1.0, 1.0, W, dtype=np.float32)[None, :]
    y = np.linspace(-1.0, 1.0, H, dtype=np.float32)[:, None]
    r = np.sqrt(x * x + y * y) / 1.414
    alpha = np.clip((r - 0.35) / 0.65, 0, 1) ** 2 * 165
    layer = np.zeros((H, W, 4), dtype=np.uint8)
    layer[..., 3] = alpha.astype(np.uint8)
    return Image.fromarray(layer, "RGBA")


VIGNETTE = build_vignette()


def build_accent_glow(size: tuple[int, int], color=ACCENT, strength=55) -> Image.Image:
    w, h = size
    x = np.linspace(-1, 1, w, dtype=np.float32)[None, :]
    y = np.linspace(-1, 1, h, dtype=np.float32)[:, None]
    r = np.sqrt(x * x + y * y)
    alpha = np.clip(1.0 - r, 0, 1) ** 2.2 * strength
    layer = np.zeros((h, w, 4), dtype=np.uint8)
    layer[..., 0] = color[0]
    layer[..., 1] = color[1]
    layer[..., 2] = color[2]
    layer[..., 3] = alpha.astype(np.uint8)
    return Image.fromarray(layer, "RGBA")


BACKDROP_CACHE: dict[str, Image.Image] = {}


def backdrop(name: str) -> Image.Image:
    if name in BACKDROP_CACHE:
        return BACKDROP_CACHE[name]
    source = BACKDROP_MAP.get(name, name)
    img = cover(load_shot(source), W, H).filter(ImageFilter.GaussianBlur(34))
    img = Image.blend(img, Image.new("RGB", (W, H), DARK), 0.70)
    rgba = Image.alpha_composite(img.convert("RGBA"), VIGNETTE)
    glow = build_accent_glow((1100, 760), strength=38)
    rgba.alpha_composite(glow, (410, 150))
    result = rgba.convert("RGB")
    BACKDROP_CACHE[name] = result
    return result


def rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    w, h = size
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, w - 1, h - 1], radius=radius, fill=255
    )
    return mask


def make_shadow(
    size: tuple[int, int], radius: int, blur: int = 26, alpha: int = 175
) -> Image.Image:
    w, h = size
    pad = 70
    shadow = Image.new("RGBA", (w + pad * 2, h + pad * 2), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        [pad, pad, pad + w, pad + h], radius=radius, fill=(0, 0, 0, alpha)
    )
    return shadow.filter(ImageFilter.GaussianBlur(blur))


class CardAsset:
    def __init__(self, name: str) -> None:
        self.name = name
        src = load_shot(name)
        self.is_full = src.size == (1600, 1000)
        if self.is_full:
            self.cw, self.ch = 1536, 960
            self.pre = src.resize(
                (self.cw, self.ch), Image.Resampling.LANCZOS
            )
            self.pos = ((W - self.cw) // 2, 30)
        else:
            # 去掉截图最外圈的蓝色焦点光晕（固定 24px）和阴影，圆角由遮罩统一处理。
            src = src.crop((24, 24, src.width - 24, src.height - 24))
            w0, h0 = src.size
            scale = min(1440 / w0, 880 / h0, 1.8)
            self.cw, self.ch = int(w0 * scale), int(h0 * scale)
            self.pre = src.resize(
                (self.cw, self.ch), Image.Resampling.LANCZOS
            )
            self.pos = ((W - self.cw) // 2, 30 + (960 - self.ch) // 2)
        self.radius = 18
        self.mask = rounded_mask((self.cw, self.ch), self.radius)
        self.shadow = make_shadow((self.cw, self.ch), self.radius)

    def frame(self, progress: float, focus: str) -> Image.Image:
        # 产品界面不做裁切式运镜，保证每一处 UI 都完整可见。
        return self.pre


CARD_CACHE: dict[str, CardAsset] = {}
CAPTIONS: dict[str, Image.Image] = {}


def card_asset(name: str) -> CardAsset:
    if name not in CARD_CACHE:
        CARD_CACHE[name] = CardAsset(name)
    return CARD_CACHE[name]


def draw_callout(img: Image.Image, x: int, y: int, text: str) -> Image.Image:
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    f = font(20, True)
    tw = int(draw.textlength(text, font=f))
    cw, ch = tw + 34, 40
    x = max(20, min(img.width - cw - 20, x))
    y = max(20, min(img.height - ch - 20, y))
    draw.rounded_rectangle(
        [x, y, x + cw, y + ch],
        radius=20,
        fill=(20, 12, 17, 232),
        outline=(*ACCENT, 190),
        width=1,
    )
    draw.ellipse([x + 12, y + 16, x + 20, y + 24], fill=ACCENT)
    draw.text((x + 26, y + 7), text, font=f, fill=TEXT)
    return Image.alpha_composite(img, layer)


def build_caption(scene: dict) -> Image.Image:
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.line([(192, 1000), (1728, 1000)], fill=(255, 255, 255, 20), width=1)
    draw.rounded_rectangle([192, 1010, 197, 1058], radius=2, fill=ACCENT)
    draw.text((212, 1006), scene["kicker"], font=font(16, True), fill=ACCENT)
    draw.text((210, 1028), scene["title"], font=font(32, True), fill=TEXT)
    # 右侧功能标签
    chip_font = font(18)
    x = 1728
    for text in reversed(scene["bullets"]):
        tw = int(draw.textlength(text, font=chip_font))
        cw = tw + 26
        x -= cw
        draw.rounded_rectangle(
            [x, 1030, x + cw, 1062],
            radius=16,
            fill=(255, 255, 255, 10),
            outline=(255, 255, 255, 34),
            width=1,
        )
        draw.text((x + 13, 1033), text, font=chip_font, fill=MUTED)
        x -= 10
    return layer


def build_topbar() -> Image.Image:
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    try:
        icon = Image.open(QA.parent / "assets" / "dkl.ico").convert("RGBA")
        icon = icon.resize((18, 18), Image.Resampling.LANCZOS)
        layer.alpha_composite(icon, (192, 6))
    except Exception:
        pass
    draw.text(
        (218, 6), "DARKITTEN LAUNCHER", font=font(11, True), fill=(180, 160, 168)
    )
    label = "PROMO · 2026"
    tw = int(draw.textlength(label, font=font(11)))
    draw.text((1728 - tw, 6), label, font=font(11), fill=(120, 104, 112))
    return layer


TOPBAR = build_topbar()


def caption_with_alpha(caption: Image.Image, alpha: float) -> Image.Image:
    if alpha >= 0.999:
        return caption
    out = caption.copy()
    a = out.getchannel("A").point(lambda v: int(v * alpha))
    out.putalpha(a)
    return out


def compose_base(shot: dict, scene: dict, cap_alpha: float) -> Image.Image:
    """只保留背景、品牌条和字幕，用于镜头之间的干净淡入淡出。"""
    frame = backdrop(shot["name"]).convert("RGBA")
    if not card_asset(shot["name"]).is_full:
        frame.alpha_composite(TOPBAR)
    frame.alpha_composite(caption_with_alpha(CAPTIONS[scene["id"]], cap_alpha))
    return frame.convert("RGB")


def compose_shot(shot: dict, scene: dict, progress: float, cap_alpha: float) -> Image.Image:
    canvas = compose_base(shot, scene, cap_alpha).convert("RGBA")
    asset = card_asset(shot["name"])
    x, y = asset.pos
    canvas.paste(asset.shadow, (x - 70, y - 70), asset.shadow)
    card = asset.frame(progress, shot.get("focus", "center"))
    canvas.paste(card, (x, y), asset.mask)
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(
        [x, y, x + asset.cw - 1, y + asset.ch - 1],
        radius=asset.radius,
        outline=(255, 255, 255, 26),
        width=1,
    )
    return canvas.convert("RGB")


def find_scene(scenes: list[dict], t: float) -> tuple[int, dict, float]:
    for index, scene in enumerate(scenes):
        if scene["start"] <= t < scene["start"] + scene["duration"]:
            return index, scene, t - scene["start"]
    last = scenes[-1]
    return len(scenes) - 1, last, last["duration"] - 1e-6


def render_scene(scene: dict, local: float) -> Image.Image:
    if scene["id"] in ("intro", "outro"):
        return render_special(scene, local)
    shots = scene["shots"]
    elapsed = 0.0
    active = 0
    for index, shot in enumerate(shots):
        if elapsed <= local < elapsed + shot["duration"]:
            active = index
            break
        elapsed += shot["duration"]
    else:
        active = len(shots) - 1
        elapsed = sum(s["duration"] for s in shots[:-1])
    shot = shots[active]
    p = max(0.0, min(1.0, (local - elapsed) / max(shot["duration"], 1e-6)))
    fade_in = min(1.0, local / 0.55)
    fade_out = min(1.0, (scene["duration"] - local) / 0.55)
    cap_alpha = ease(min(fade_in, fade_out))
    frame = compose_shot(shot, scene, p, cap_alpha)
    if active > 0 and local - elapsed < SHOT_TRANS:
        prev = shots[active - 1]
        prev_p = 1.0 - 1e-6
        prev_frame = compose_shot(prev, scene, prev_p, cap_alpha)
        base = compose_base(shot, scene, cap_alpha)
        a = ease((local - elapsed) / SHOT_TRANS)
        # 先把旧卡片淡回背景，再让新卡片浮出来，避免两张界面叠在一起。
        if a < 0.5:
            frame = Image.blend(prev_frame, base, a * 2)
        else:
            frame = Image.blend(base, frame, (a - 0.5) * 2)
    return frame


def gradient_background() -> Image.Image:
    y = np.linspace(0, 1, H, dtype=np.float32)[:, None]
    x = np.linspace(0, 1, W, dtype=np.float32)[None, :]
    base = np.zeros((H, W, 3), dtype=np.float32)
    base[..., 0] = 14 + 18 * y + 6 * x
    base[..., 1] = 8 + 9 * y + 3 * x
    base[..., 2] = 16 + 14 * y + 8 * x
    img = Image.fromarray(np.clip(base, 0, 255).astype(np.uint8), "RGB").convert("RGBA")

    grid = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(grid)
    for gx in range(0, W, 48):
        for gy in range(0, H, 48):
            d.ellipse([gx, gy, gx + 1, gy + 1], fill=(255, 255, 255, 12))
    img.alpha_composite(grid)

    glow = build_accent_glow((1300, 1000), strength=62)
    img.alpha_composite(glow, (700, 40))
    img.alpha_composite(build_accent_glow((800, 800), color=(110, 70, 180), strength=34), (0, 520))
    img.alpha_composite(VIGNETTE)
    return img


def circle_avatar(size: int) -> Image.Image:
    src = Image.open(QA.parent / "assets" / "chibi_face.png").convert("RGBA")
    src = cover(src, size, size)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size - 1, size - 1], fill=255)
    out = Image.new("RGBA", (size + 12, size + 12), (0, 0, 0, 0))
    out.paste(src, (6, 6), mask)
    d = ImageDraw.Draw(out)
    d.ellipse([2, 2, size + 9, size + 9], outline=(*ACCENT, 255), width=3)
    return out


GRADIENT_BG = gradient_background()
AVATAR_BIG = circle_avatar(350)
AVATAR_SMALL = circle_avatar(132)
TRANSITION_BASE = GRADIENT_BG.copy()
TRANSITION_BASE.alpha_composite(TOPBAR)
TRANSITION_BASE = TRANSITION_BASE.convert("RGB")


def render_special(scene: dict, local: float) -> Image.Image:
    duration = scene["duration"]
    p = local / max(duration, 1e-6)
    img = GRADIENT_BG.copy()
    if scene["id"] == "intro":
        push = int(30 * ease(min(1.0, local / 1.2)))
        reveal = ease(min(1.0, local / 0.9))
        # 右侧头像与光环
        cy = 470 + int(math.sin(local * 0.8) * 7)
        img.alpha_composite(AVATAR_BIG, (1270, cy - 175))
        ring = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        rd = ImageDraw.Draw(ring)
        for radius in (300, 360, 430):
            rd.ellipse(
                [1470 - radius, cy - radius, 1470 + radius, cy + radius],
                outline=(255, 255, 255, 14),
                width=1,
            )
        img.alpha_composite(ring)
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(overlay)
        y = 322 - push
        d.text((152, y), "DARKITTEN · MINECRAFT JAVA", font=font(22, True), fill=ACCENT)
        d.text((150, y + 48), "把 Minecraft，", font=font(86, True), fill=TEXT)
        d.text((150, y + 148), "收进一个猫窝。", font=font(86, True), fill=TEXT)
        d.rounded_rectangle([152, y + 266, 272, y + 270], radius=2, fill=ACCENT)
        d.text(
            (150, y + 296),
            "版本 · 模组 · 联机 · 服务端，一只猫猫全部打理。",
            font=font(28),
            fill=MUTED,
        )
        chips = ["v1.11.6", "Windows 10 / 11", "免费"]
        x = 150
        for text in chips:
            tw = int(d.textlength(text, font=font(19, True)))
            cw = tw + 30
            d.rounded_rectangle(
                [x, y + 360, x + cw, y + 398],
                radius=19,
                fill=(255, 255, 255, 10),
                outline=(255, 255, 255, 38),
                width=1,
            )
            d.text((x + 15, y + 368), text, font=font(19, True), fill=TEXT)
            x += cw + 12
        if reveal < 1:
            alpha = int(255 * reveal)
            a = overlay.getchannel("A").point(lambda v: min(v, alpha))
            overlay.putalpha(a)
        img.alpha_composite(overlay)
    else:
        reveal = ease(min(1.0, (local - 0.1) / 0.9))
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(overlay)
        logo = AVATAR_SMALL
        lx = (W - logo.width) // 2
        ly = 214 + int((1 - reveal) * 12)
        img.alpha_composite(logo, (lx, ly))
        title = "今天也要去捣乱吗？"
        tw = int(d.textlength(title, font=font(72, True)))
        d.text(((W - tw) // 2, 408), title, font=font(72, True), fill=TEXT)
        sub = "DARKITTEN LAUNCHER 1.11.6"
        sw = int(d.textlength(sub, font=font(24, True)))
        d.text(((W - sw) // 2, 512), sub, font=font(24, True), fill=ACCENT)
        url = "lfelkeea.github.io/darkitten-launcher"
        uw = int(d.textlength(url, font=font(30)))
        d.text(((W - uw) // 2, 574), url, font=font(30), fill=(236, 220, 226))
        chips = ["下载站", "GitHub Releases", "Windows 10 / 11"]
        total = 0
        widths = []
        for text in chips:
            cw = int(d.textlength(text, font=font(19, True))) + 34
            widths.append(cw)
            total += cw + 12
        total -= 12
        x = (W - total) // 2
        for text, cw in zip(chips, widths):
            d.rounded_rectangle(
                [x, 650, x + cw, 688],
                radius=19,
                fill=(255, 255, 255, 10),
                outline=(255, 255, 255, 38),
                width=1,
            )
            d.text((x + 17, 658), text, font=font(19, True), fill=TEXT)
            x += cw + 12
        tag = "把启动、模组、联机和服务端，都收进一个猫窝。"
        tw = int(d.textlength(tag, font=font(24)))
        d.text(((W - tw) // 2, 748), tag, font=font(24), fill=MUTED)
        if reveal < 1:
            alpha = int(255 * reveal)
            a = overlay.getchannel("A").point(lambda v: min(v, alpha))
            overlay.putalpha(a)
        img.alpha_composite(overlay)
    # 片头片尾也画一点点品牌条
    img.alpha_composite(TOPBAR)
    return img.convert("RGB")


def render_frame(scenes: list[dict], t: float) -> Image.Image:
    index, scene, local = find_scene(scenes, t)
    frame = render_scene(scene, local)
    if index > 0 and local < TRANS:
        prev = scenes[index - 1]
        prev_frame = render_scene(prev, prev["duration"] - 1e-6)
        a = ease(local / TRANS)
        # 章节之间先淡到品牌底色，再进入下一段，避免两个页面互相透叠。
        if a < 0.5:
            frame = Image.blend(prev_frame, TRANSITION_BASE, a * 2)
        else:
            frame = Image.blend(TRANSITION_BASE, frame, (a - 0.5) * 2)
    total = scenes[-1]["start"] + scenes[-1]["duration"]
    if t < 0.55:
        frame = Image.blend(Image.new("RGB", (W, H), (0, 0, 0)), frame, ease(t / 0.55))
    if t > total - 1.1:
        frame = Image.blend(
            frame, Image.new("RGB", (W, H), (0, 0, 0)), ease((t - total + 1.1) / 1.1)
        )
    # 底部进度条
    draw = ImageDraw.Draw(frame)
    draw.rectangle([0, H - 4, W, H], fill=(44, 36, 42))
    draw.rectangle([0, H - 4, int(W * min(1.0, t / total)), H], fill=ACCENT)
    return frame


def build_mix(total: float) -> Path:
    _, voice = wavfile.read(AUDIO / "voice_track.wav")
    _, music = wavfile.read(AUDIO / "music.wav")
    voice = voice.astype(np.float32) / 32768.0
    music = music.astype(np.float32) / 32768.0
    if music.ndim == 1:
        music = np.stack([music, music], axis=1)
    n = int(total * SR)
    v = np.zeros(n, dtype=np.float32)
    v[: min(n, len(voice))] = voice[:n]
    m = np.zeros((n, 2), dtype=np.float32)
    m[: min(n, len(music))] = music[:n]
    mix = v[:, None] + m * 0.14
    # 结尾跟随画面淡出
    fade = int(1.1 * SR)
    mix[-fade:] *= np.linspace(1, 0, fade)[:, None] ** 1.3
    peak = float(np.max(np.abs(mix))) or 1.0
    mix = mix / peak * 0.94
    path = AUDIO / "final_mix.wav"
    wavfile.write(path, SR, (mix * 32767).astype(np.int16))
    print("final_mix.wav", f"{len(mix)/SR:.2f}s", flush=True)
    return path


def main() -> None:
    timeline = json.loads((AUDIO / "timeline.json").read_text(encoding="utf-8"))
    scenes = timeline["scenes"]
    total = float(timeline["total"])
    global CAPTIONS
    CAPTIONS = {scene["id"]: build_caption(scene) for scene in scenes if scene["shots"]}

    still_times = []
    for scene in scenes:
        if scene["shots"]:
            still_times.append((scene["start"] + scene["duration"] * 0.55, scene["id"]))
        elif scene["id"] in ("intro", "outro"):
            still_times.append((scene["start"] + scene["duration"] * 0.65, scene["id"]))
    for t, name in still_times:
        frame = render_frame(scenes, t)
        frame.save(STILLS / f"{name}.png")
        print("still", name, f"{t:.1f}s", flush=True)

    if "--stills" in sys.argv:
        return

    mix_path = build_mix(total)
    output = OUT / "Darkitten_Launcher_Promo_1080p.mp4"
    writer = imageio_ffmpeg.write_frames(
        str(output),
        (W, H),
        fps=FPS,
        codec="libx264",
        quality=6,
        pix_fmt_in="rgb24",
        pix_fmt_out="yuv420p",
        audio_path=str(mix_path),
        audio_codec="aac",
        macro_block_size=1,
        output_params=[
            "-preset",
            "slow",
            "-movflags",
            "+faststart",
            "-shortest",
        ],
        ffmpeg_log_level="warning",
    )
    writer.send(None)
    frames = int(round(total * FPS))
    for i in range(frames):
        t = i / FPS
        frame = render_frame(scenes, t)
        writer.send(np.asarray(frame, dtype=np.uint8))
        if i % 150 == 0:
            print(f"frame {i}/{frames} ({t:.1f}s)", flush=True)
    writer.close()
    print("done", output, output.stat().st_size, flush=True)


if __name__ == "__main__":
    main()
