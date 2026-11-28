"""BGM: use a random file from bgm/ if present, otherwise synthesize a light loop."""
import random
import subprocess
from pathlib import Path

import numpy as np

SR = 48000
PROGRESSIONS = [
    [[0, 4, 7], [7, 11, 14], [9, 12, 16], [5, 9, 12]],    # I V vi IV
    [[9, 12, 16], [5, 9, 12], [0, 4, 7], [7, 11, 14]],    # vi IV I V
    [[0, 4, 7], [9, 12, 16], [5, 9, 12], [7, 11, 14]],    # I vi IV V
    [[5, 9, 12], [7, 11, 14], [4, 7, 11], [9, 12, 16]],   # IV V iii vi
]


def _hz(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def _add(buf, start, sig):
    s = int(start * SR)
    if s >= len(buf):
        return
    e = min(len(buf), s + len(sig))
    buf[s:e] += sig[: e - s]


def synthesize(duration, seed=None, style="standard"):
    if style == "bouncy":
        return synthesize_bouncy(duration, seed)
    rng = random.Random(seed)
    bpm = rng.choice([92, 96, 100, 104, 110])
    key = rng.choice([0, 2, 3, 5, 7, -2])
    prog = rng.choice(PROGRESSIONS)
    beat = 60 / bpm
    bar = beat * 4
    n = int((duration + 1) * SR)
    out = np.zeros(n, np.float32)
    nrng = np.random.default_rng(rng.randrange(1 << 30))

    t_bar = np.arange(int(bar * SR)) / SR
    pad_env = np.minimum(1, t_bar / 0.25) * np.minimum(1, (bar - t_bar) / 0.3)
    t8 = np.arange(int(beat / 2 * SR * 1.6)) / SR
    pluck_env = np.exp(-t8 * 9)
    tk = np.arange(int(0.35 * SR)) / SR
    kick = np.sin(2 * np.pi * np.cumsum(45 + 90 * np.exp(-tk * 30)) / SR) * np.exp(-tk * 9)
    th = np.arange(int(0.05 * SR)) / SR
    hat = np.diff(nrng.standard_normal(len(th) + 1)).astype(np.float32) * np.exp(-th * 80)

    t, i = 0.0, 0
    while t < duration + 1:
        chord = [c + key + 60 for c in prog[i % 4]]
        pad = sum(np.sin(2 * np.pi * _hz(m) * t_bar) + 0.3 * np.sin(2 * np.pi * _hz(m) * 2.003 * t_bar)
                  for m in chord)
        _add(out, t, 0.035 * pad * pad_env)
        for b in range(4):
            bt = t + b * beat
            if b in (0, 2):
                tb = np.arange(int(beat * SR)) / SR
                _add(out, bt, 0.12 * np.sin(2 * np.pi * _hz(chord[0] - 24) * tb) * np.exp(-tb * 4))
                _add(out, bt, 0.22 * kick)
            _add(out, bt + beat / 2, 0.035 * hat)
            for k in range(2):
                m = chord[(b * 2 + k) % 3] + 12 + (12 if (b * 2 + k) % 4 == 3 else 0)
                f = _hz(m)
                sig = (np.sin(2 * np.pi * f * t8) + 0.25 * np.sin(4 * np.pi * f * t8)) * pluck_env
                _add(out, bt + k * beat / 2, 0.045 * sig)
        t += bar
        i += 1
    out = out[: int(duration * SR)]
    fade = int(1.5 * SR)
    out[-fade:] *= np.linspace(1, 0, fade)
    out[: int(0.3 * SR)] *= np.linspace(0, 1, int(0.3 * SR))
    return out / (np.abs(out).max() + 1e-9) * 0.8


def synthesize_bouncy(duration, seed=None):
    """Trivia channel: faster, playful marimba-like loop with off-beat bass and claps."""
    rng = random.Random(seed)
    bpm = rng.choice([122, 126, 130])
    key = rng.choice([0, 2, 5, 7])
    prog = rng.choice(PROGRESSIONS)
    beat = 60 / bpm
    bar = beat * 4
    n = int((duration + 1) * SR)
    out = np.zeros(n, np.float32)
    nrng = np.random.default_rng(rng.randrange(1 << 30))
    t16 = np.arange(int(beat / 4 * SR * 2)) / SR
    tc = np.arange(int(0.12 * SR)) / SR
    clap = (np.diff(nrng.standard_normal(len(tc) + 1)) * np.exp(-tc * 38)).astype(np.float32)
    tk = np.arange(int(0.25 * SR)) / SR
    kick = np.sin(2 * np.pi * np.cumsum(50 + 110 * np.exp(-tk * 40)) / SR) * np.exp(-tk * 14)
    pattern = rng.choice([[0, 2, 3, 5, 6], [0, 3, 4, 6, 7], [0, 1, 3, 4, 6]])  # 8th-note slots per bar
    t, i = 0.0, 0
    while t < duration + 1:
        chord = [c + key + 72 for c in prog[i % 4]]
        for b in range(4):
            bt = t + b * beat
            _add(out, bt, 0.2 * kick)
            if b in (1, 3):
                _add(out, bt, 0.06 * clap)
            tb = np.arange(int(beat / 2 * SR)) / SR  # off-beat bass
            _add(out, bt + beat / 2, 0.11 * np.sin(2 * np.pi * _hz(chord[0] - 36) * tb) * np.exp(-tb * 10))
        for k, slot in enumerate(pattern):
            m = chord[(k + i) % 3] + (12 if k == len(pattern) - 1 else 0)
            f = _hz(m)
            sig = (np.sin(2 * np.pi * f * t16) + 0.35 * np.sin(2 * np.pi * f * 4.0 * t16) * np.exp(-t16 * 30)) \
                * np.exp(-t16 * 16)
            _add(out, t + slot * beat / 2, 0.05 * sig)
        t += bar
        i += 1
    out = out[: int(duration * SR)]
    fade = int(1.2 * SR)
    out[-fade:] *= np.linspace(1, 0, fade)
    return out / (np.abs(out).max() + 1e-9) * 0.8


def _decode(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "1",
                          "-ar", str(SR), "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).copy()


def get(duration, bgm_dir, seed=None, style="standard"):
    files = [p for p in Path(bgm_dir).glob("*") if p.suffix.lower() in (".mp3", ".wav", ".m4a", ".ogg")]
    if not files:
        return synthesize(duration, seed, style), f"generated ({style})"
    path = random.Random(seed).choice(files)
    x = _decode(path)
    reps = int(np.ceil(duration * SR / len(x)))
    x = np.tile(x, reps)[: int(duration * SR)]
    fade = int(1.5 * SR)
    x[-fade:] *= np.linspace(1, 0, fade)
    return x / (np.abs(x).max() + 1e-9) * 0.8, path.name


def pop(sr=SR):
    t = np.arange(int(0.12 * sr)) / sr
    f = 300 + 900 * np.exp(-t * 40)
    return (np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-t * 35)).astype(np.float32)


def swipe(sr=SR, seed=3):
    """Card swipe: short filtered-noise whoosh."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(0.22 * sr)) / sr
    noise = rng.standard_normal(len(t)).astype(np.float32)
    k = 60
    smooth = np.convolve(noise, np.ones(k) / k, mode="same")  # darker noise
    env = np.sin(np.pi * np.clip(t / 0.22, 0, 1)) ** 2
    x = (noise - smooth) * 0.35 + smooth * 3.0
    return (x * env / (np.abs(x * env).max() + 1e-9)).astype(np.float32)


def impact(sr=SR, seed=1):
    """Opening hit: low boom + rising whoosh into a short crack."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(0.9 * sr)) / sr
    boom = np.sin(2 * np.pi * np.cumsum(38 + 70 * np.exp(-t * 9)) / sr) * np.exp(-t * 4.5)
    noise = rng.standard_normal(len(t)).astype(np.float32)
    hiss = np.diff(np.concatenate([[0], noise])) * np.exp(-t * 7) * 0.35
    crack = np.zeros_like(t)
    n = int(0.03 * sr)
    crack[:n] = rng.standard_normal(n) * np.linspace(1, 0, n) * 0.5
    x = boom * 0.9 + hiss + crack
    return (x / (np.abs(x).max() + 1e-9)).astype(np.float32)


def reveal(sr=SR):
    """Answer reveal: a bright two-note 'ding-dong' (up a fourth)."""
    out = np.zeros(int(0.55 * sr), np.float32)
    for k, (f, st) in enumerate(((1046.5, 0.0), (1396.9, 0.09))):
        t = np.arange(int(0.45 * sr)) / sr
        x = (np.sin(2 * np.pi * f * t) + 0.3 * np.sin(2 * np.pi * f * 2 * t)) * np.exp(-t * 7)
        s0 = int(st * sr)
        out[s0:s0 + len(x)] += x[: len(out) - s0].astype(np.float32)
    return out / (np.abs(out).max() + 1e-9)


def tick(sr=SR):
    """Soft accent for emphasised words: short bright blip."""
    t = np.arange(int(0.09 * sr)) / sr
    x = np.sin(2 * np.pi * 1320 * t) * 0.6 + np.sin(2 * np.pi * 1980 * t) * 0.3
    return (x * np.exp(-t * 45)).astype(np.float32)


def title_se(sr=SR, seed=5):
    """Opening title: a short rising whoosh into a bright two-note chime."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(0.35 * sr)) / sr
    n = rng.standard_normal(len(t)).astype(np.float32)
    n = n - np.convolve(n, np.ones(40) / 40, mode="same")
    whoosh = n * (t / 0.35) ** 2 * np.exp(-((t - 0.3) / 0.08) ** 2) * 0.6
    tc = np.arange(int(0.9 * sr)) / sr
    chime = (np.sin(2 * np.pi * 1568 * tc) + 0.5 * np.sin(2 * np.pi * 2349 * tc) * (tc > 0.07)) * np.exp(-tc * 6)
    out = np.zeros(int(0.3 * sr) + len(tc), np.float32)
    out[:len(whoosh)] += whoosh
    out[int(0.3 * sr):] += chime * 0.5
    return (out / (np.abs(out).max() + 1e-9)).astype(np.float32)
