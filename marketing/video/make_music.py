"""紹介動画の背景音楽を、数値計算でその場で合成する (他人の曲や音源を使わないので、権利の心配がない).

    python marketing/video/make_music.py <秒数> <出力.wav>

落ち着いた分散和音 (Cmaj7 - Am7 - Fmaj7 - G) と柔らかい伴奏、テンポ 90。最初と最後はフェードする。
"""

from __future__ import annotations

import sys
import wave

import numpy as np

SR = 44100
BPM = 90
BEAT = 60 / BPM


def note(freq: float, dur: float, kind: str = "pluck") -> np.ndarray:
    t = np.arange(int(SR * dur)) / SR
    if kind == "pluck":   # ピアノ風: 倍音が早く減る
        w = sum(np.sin(2 * np.pi * freq * k * t) * a * np.exp(-t * (2.2 + 1.6 * k)) for k, a in ((1, 1.0), (2, 0.45), (3, 0.18), (4, 0.08)))
        env = np.minimum(1, t / 0.006)
    else:                 # 伴奏: ゆっくり立ち上がる柔らかい音
        w = sum(np.sin(2 * np.pi * freq * d * t + p) for d, p in ((1.0, 0), (1.003, 1.3), (0.997, 2.1), (2.0, 0.4))) / 4
        env = np.minimum(1, t / 0.8) * np.minimum(1, (dur - t) / 0.9).clip(0)
    return w * env


def freq(midi: int) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def add(buf: np.ndarray, start: float, sound: np.ndarray, gain: float = 1.0) -> None:
    """buf の start 秒の位置に sound を足す (曲の長さを超える分は捨てる)."""
    s = int(SR * start)
    if s >= len(buf):
        return
    e = min(len(buf), s + len(sound))
    buf[s:e] += sound[: e - s] * gain


CHORDS = [[60, 64, 67, 71], [57, 60, 64, 67], [53, 57, 60, 64], [55, 59, 62, 67]]   # Cmaj7, Am7, Fmaj7, G
BASS = [36, 33, 29, 31]


def main() -> int:
    secs, out = float(sys.argv[1]), sys.argv[2]
    n = int(SR * (secs + 2))
    left, right = np.zeros(n), np.zeros(n)
    bar = 4 * BEAT
    i = 0
    t0 = 0.0
    rng = np.random.default_rng(7)
    while t0 < secs + 1:
        ch, bass = CHORDS[(i // 2) % 4], BASS[(i // 2) % 4]
        # 伴奏 (2 小節ずつ同じ和音)
        if i % 2 == 0:
            pad = sum(note(freq(m), bar * 2, "pad") for m in ch) * 0.10 + note(freq(bass + 12), bar * 2, "pad") * 0.10
            add(left, t0, pad)
            add(right, t0, pad)
        # 分散和音 (8 分音符)、左右に少し振る
        pattern = [0, 1, 2, 3, 2, 1, 2, 3] if i % 2 == 0 else [0, 2, 1, 3, 2, 3, 1, 2]
        for k, idx in enumerate(pattern):
            m = ch[idx] + (12 if k in (3, 7) and i % 4 == 3 else 0)
            v = note(freq(m), 1.6, "pluck") * (0.16 + 0.03 * rng.random())
            pan = 0.5 + 0.25 * np.sin(k * 1.1 + i)
            add(left, t0 + k * BEAT / 2, v, (1 - pan) * 1.4)
            add(right, t0 + k * BEAT / 2, v, pan * 1.4)
        # 低音 (1 拍目)
        b = note(freq(bass), bar, "pluck") * 0.22
        add(left, t0, b)
        add(right, t0, b)
        t0 += bar
        i += 1
    # 簡単な残響 (遅れて小さくなる音を重ねる)
    for d, g in ((0.11, 0.25), (0.23, 0.16), (0.37, 0.1)):
        k = int(SR * d)
        left[k:] += right[:-k] * g
        right[k:] += left[:-k] * g
    st = np.stack([left, right], axis=1)[: int(SR * secs)]
    t = np.arange(len(st)) / SR
    fade = np.minimum(1, t / 2.5) * np.minimum(1, (secs - t) / 3.5).clip(0)
    st *= fade[:, None]
    st /= np.abs(st).max() + 1e-9
    st *= 0.6
    with wave.open(out, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((st * 32767).astype("<i2").tobytes())
    print("作りました:", out, f"{secs:.1f} 秒")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
