from __future__ import annotations

"""生成宣传片背景音乐：温暖的合成器 Pad + 琶音 + 轻鼓点。"""

import json
from pathlib import Path

import numpy as np
import scipy.io.wavfile as wavfile

QA = Path(__file__).resolve().parent
AUDIO = QA / "promo_audio"
SR = 48000
BPM = 92.0
BEAT = 60.0 / BPM
BAR = BEAT * 4

# vi - IV - I - V，A 小调色彩，温暖但不悲伤。
CHORDS = [
    ("Am7", [110.00, 130.81, 164.81, 196.00]),
    ("Fmaj7", [87.31, 110.00, 130.81, 164.81]),
    ("Cmaj7", [65.41, 82.41, 98.00, 123.47]),
    ("G6", [98.00, 123.47, 146.83, 164.81]),
]


def adsr(length: int, attack: float, release: float) -> np.ndarray:
    env = np.ones(length, dtype=np.float32)
    a = min(int(attack * SR), length)
    r = min(int(release * SR), length)
    if a > 0:
        env[:a] = np.linspace(0.0, 1.0, a) ** 2
    if r > 0:
        env[-r:] *= np.linspace(1.0, 0.0, r) ** 1.5
    return env


def pad_note(freq: float, length: int, detune: float = 0.0) -> np.ndarray:
    t = np.arange(length, dtype=np.float32) / SR
    f = freq * (1.0 + detune)
    wave = (
        np.sin(2 * np.pi * f * t)
        + 0.35 * np.sin(2 * np.pi * f * 2 * t)
        + 0.12 * np.sin(2 * np.pi * f * 3 * t)
    )
    return wave.astype(np.float32)


def pluck(freq: float, length: int) -> np.ndarray:
    t = np.arange(length, dtype=np.float32) / SR
    env = np.exp(-t * 9.0).astype(np.float32)
    wave = (
        np.sin(2 * np.pi * freq * t)
        + 0.25 * np.sin(2 * np.pi * freq * 2 * t)
        + 0.08 * np.sin(2 * np.pi * freq * 3 * t)
    )
    return (wave * env).astype(np.float32)


def kick(length: int) -> np.ndarray:
    t = np.arange(length, dtype=np.float32) / SR
    freq = 95.0 * np.exp(-t * 18.0) + 42.0
    phase = 2 * np.pi * np.cumsum(freq) / SR
    env = np.exp(-t * 14.0).astype(np.float32)
    return (np.sin(phase) * env).astype(np.float32)


def hat(length: int, rng: np.random.Generator) -> np.ndarray:
    noise = rng.standard_normal(length).astype(np.float32)
    noise = np.diff(noise, prepend=0.0)
    env = np.exp(-np.arange(length, dtype=np.float32) / SR * 55.0)
    return (noise * env).astype(np.float32)


def main() -> None:
    timeline = json.loads((AUDIO / "timeline.json").read_text(encoding="utf-8"))
    total = float(timeline["total"]) + 0.5
    n = int(total * SR)
    left = np.zeros(n, dtype=np.float32)
    right = np.zeros(n, dtype=np.float32)
    rng = np.random.default_rng(20261030)

    pad_len = int(BAR * 1.15 * SR)
    bars = int(np.ceil(total / BAR)) + 1

    for bar in range(bars):
        start = int(bar * BAR * SR)
        if start >= n:
            break
        name, notes = CHORDS[bar % len(CHORDS)]
        # Pad：每个和弦音左右各一层轻微失谐
        for i, freq in enumerate(notes):
            length = min(pad_len, n - start)
            if length <= 0:
                continue
            env = adsr(length, 0.9, 1.4)
            a = pad_note(freq, length, -0.0012 - i * 0.0002) * env
            b = pad_note(freq, length, 0.0012 + i * 0.0002) * env
            gain = 0.045 if i == 0 else 0.032
            left[start : start + length] += a * gain
            right[start : start + length] += b * gain

        # 琶音：八分音符，左右交替
        pattern = [0, 2, 1, 3, 2, 0, 3, 1]
        for step in range(8):
            freq = notes[pattern[step]] * 2.0
            offset = start + int(step * BEAT * 0.5 * SR)
            if offset >= n:
                break
            length = min(int(0.55 * SR), n - offset)
            tone = pluck(freq, length) * 0.11
            if step % 2 == 0:
                left[offset : offset + length] += tone
            else:
                right[offset : offset + length] += tone

        # 低音：每小节第 1、3 拍
        for beat in (0, 2):
            offset = start + int(beat * BEAT * SR)
            if offset >= n:
                break
            length = min(int(BEAT * 1.4 * SR), n - offset)
            t = np.arange(length, dtype=np.float32) / SR
            env = np.exp(-t * 2.2).astype(np.float32)
            tone = np.sin(2 * np.pi * notes[0] / 2 * t) * env * 0.14
            left[offset : offset + length] += tone
            right[offset : offset + length] += tone

        # 轻鼓点
        for beat in (0, 2):
            offset = start + int(beat * BEAT * SR)
            if offset >= n:
                break
            length = min(int(0.32 * SR), n - offset)
            tone = kick(length) * 0.10
            left[offset : offset + length] += tone
            right[offset : offset + length] += tone
        for half in (1, 3, 5, 7):
            offset = start + int(half * BEAT * 0.5 * SR)
            if offset >= n:
                break
            length = min(int(0.06 * SR), n - offset)
            tone = hat(length, rng) * 0.020
            left[offset : offset + length] += tone
            right[offset : offset + length] += tone

    # 轻微黑胶底噪，让整体不那么“电子”
    noise = rng.standard_normal(n).astype(np.float32)
    noise = np.convolve(noise, np.ones(8, dtype=np.float32) / 8.0, mode="same")
    left += noise * 0.0018
    right += noise * 0.0018

    # 淡入淡出
    fade_in = int(2.0 * SR)
    fade_out = int(3.5 * SR)
    left[:fade_in] *= np.linspace(0, 1, fade_in) ** 1.5
    right[:fade_in] *= np.linspace(0, 1, fade_in) ** 1.5
    left[-fade_out:] *= np.linspace(1, 0, fade_out) ** 1.2
    right[-fade_out:] *= np.linspace(1, 0, fade_out) ** 1.2

    peak = max(float(np.max(np.abs(left))), float(np.max(np.abs(right)))) or 1.0
    left = left / peak * 0.7
    right = right / peak * 0.7
    stereo = np.stack([left, right], axis=1)
    wavfile.write(
        AUDIO / "music.wav", SR, (stereo * 32767).astype(np.int16)
    )
    print("music.wav", f"{total:.2f}s", f"peak {peak:.3f}", flush=True)


if __name__ == "__main__":
    main()
