"""Turn a script JSON into a vertical MP4."""
import json
import re
import subprocess
import time
import wave
from pathlib import Path

import numpy as np
from PIL import Image

from . import assets, bgm, reading, tts, visuals as V

SR = tts.SR
DEFAULT_THEME = {"bg": ["#1e3c72", "#6a1b9a"], "accent": "#ffd600"}
XFADE = 0.18


MAX_CHUNK = 24


def _split_at(marked, plain_idx):
    """Split a "[..]"-marked string at a plain-text index, keeping highlights balanced."""
    n, inside = 0, False
    for i, ch in enumerate(marked):
        if n == plain_idx and ch != "]":
            a, b = marked[:i], marked[i:]
            return (a + "]", "[" + b) if inside else (a, b)
        if ch in "[]":
            inside = ch == "["
            continue
        if False:
            a, b = marked[:i], marked[i:]
            return (a + "]", "[" + b) if inside else (a, b)
        n += 1
    return marked, ""


def _halve(marked):
    plain = re.sub(r"[\[\]]", "", marked)
    if len(plain) <= MAX_CHUNK:
        return [marked]
    mid = len(plain) / 2
    commas = [i + 1 for i, c in enumerate(plain) if c == "、" and 6 <= i + 1 <= len(plain) - 5]
    cands = commas or sorted(p for p in (V.break_points(plain) or ()) if 6 <= p <= len(plain) - 5)
    if not cands:
        return [marked]
    a, b = _split_at(marked, min(cands, key=lambda i: abs(i - mid)))
    return _halve(a.strip()) + _halve(b.strip())


def split_sentences(text):
    """Split narration into subtitle chunks of at most ~MAX_CHUNK characters."""
    parts = [p.strip() for p in re.split(r"(?<=[。！？!?])", text) if p.strip()]
    out = []
    for p in parts:
        out += _halve(p)
    out = [o.replace("[]", "") for o in out]
    return [o for o in out if re.sub(r"[\[\]。、]", "", o)]


def tts_text(chunk, prons):
    s = re.sub(r"[\[\]]", "", chunk)
    for k, v in prons.items():
        s = s.replace(k, v)
    return s


def subtitle_text(chunk):
    return chunk.rstrip("。")


def make_layer(scene, theme, seed, cfg, root, credits, fps):
    v = dict(scene.get("visual") or {"type": "text", "text": scene.get("caption", "")})
    found = assets.resolve(v, cfg, root / "cache", root)
    if found:
        credits.append(found["credit"])
        if found["kind"] == "video":
            return V.VideoLayer(v, theme, seed, found["path"], fps)
        return V.PhotoLayer(v, theme, seed, found["path"])
    if v.get("type") in ("photo", "video", "ai"):
        v = v.get("fallback") or {"type": "illustration", "emoji": v.get("emoji", "🔍"),
                                  "text": v.get("query", "")}
    return V.LAYERS[v["type"]](v, theme, seed)


def write_wav(path, x):
    st = np.repeat((np.clip(x, -1, 1) * 32767).astype(np.int16)[:, None], 2, axis=1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(st.tobytes())


def render(script_path, cfg, root, out_dir):
    t0 = time.time()
    root = Path(root)
    script = json.loads(Path(script_path).read_text(encoding="utf-8"))
    name = Path(script_path).stem
    theme = {**DEFAULT_THEME, **script.get("theme", {})}
    prons = {**cfg.get("pronunciations", {}), **script.get("pronunciations", {})}
    fps = cfg["video"]["fps"]
    seed = abs(hash(name)) % 10000
    (root / "cache").mkdir(exist_ok=True)
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)

    # ---- 1. narration audio + timeline ----
    print(f"[{name}] TTS ({cfg['tts']['backend']})...")
    tts.USED.clear()
    spoken = [tts_text(ch, prons) for sc in script["scenes"] for ch in split_sentences(sc.get("narration", ""))]
    try:
        fixed = reading.prepare(spoken, cfg)
    except Exception as e:
        print(f"  [reading] check skipped: {e}")
        fixed = {}
    voice_parts, subs, scenes_t = [], [], []
    t = 0.0
    for si, sc in enumerate(script["scenes"]):
        start = t
        lead = 0.12 if si else 0.05
        voice_parts.append(np.zeros(int(lead * SR), np.float32)); t += lead
        for ch in split_sentences(sc.get("narration", "")):
            line = tts_text(ch, prons)
            a = tts.synth(fixed.get(line, line), cfg)
            subs.append((t, t + len(a) / SR, subtitle_text(ch)))
            voice_parts.append(a); t += len(a) / SR
            voice_parts.append(np.zeros(int(0.12 * SR), np.float32)); t += 0.12
        tail = max(sc.get("hold", 0.25), (sc.get("min_duration", 1.6) - (t - start)))
        voice_parts.append(np.zeros(int(tail * SR), np.float32)); t += tail
        scenes_t.append((start, t))
    total = t
    voice = np.concatenate(voice_parts)
    voice = voice / (np.abs(voice).max() + 1e-9) * 0.9

    # ---- 2. mix BGM + sfx with ducking ----
    music, bgm_name = bgm.get(total, root / cfg["bgm"]["dir"], seed)
    n = len(voice)
    music = np.pad(music, (0, max(0, n - len(music))))[:n]
    env = np.convolve(np.abs(voice), np.ones(4800) / 4800, mode="same")
    env = np.convolve((env > 0.02).astype(np.float32), np.ones(9600) / 9600, mode="same")
    vol = cfg["bgm"]["volume"]
    mix = voice + music * vol * (1 - 0.45 * env)
    pop = bgm.pop() * 0.25
    for st, _ in scenes_t[1:]:
        s = int(st * SR); mix[s:s + len(pop)] += pop[: max(0, min(len(pop), n - s))]
    mix = np.tanh(mix * 1.1) / np.tanh(1.1)
    wav = out_dir / f"{name}.wav"
    write_wav(wav, mix)

    # ---- 3. layers / overlays ----
    credits = []
    layers = [make_layer(sc, theme, seed + i, cfg, root, credits, fps) for i, sc in enumerate(script["scenes"])]
    title = V.rich_text(script.get("title", ""), 76, 960, hi=V.hex2rgb(theme["accent"]), stroke=0)
    band = V.rounded((min(1040, title.width + 70), title.height + 40), 30, (0, 0, 0, 170))
    band.paste(title, ((band.width - title.width) // 2, 20), title)
    sub_cache = {}

    def sub_img(s):
        if s not in sub_cache:
            sub_cache[s] = V.rich_text(s, 74, 960, hi=V.hex2rgb(theme["accent"]), stroke=11)
        return sub_cache[s]

    # ---- 4. render frames -> ffmpeg ----
    mp4 = out_dir / f"{name}.mp4"
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{V.W}x{V.H}",
           "-r", str(fps), "-i", "-", "-i", str(wav), "-c:v", "libx264", "-preset", "medium",
           "-crf", str(cfg["video"]["crf"]), "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
           "-shortest", "-movflags", "+faststart", str(mp4)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    nframes = int(total * fps)
    print(f"[{name}] rendering {total:.1f}s / {nframes} frames...")
    si, prev_last = 0, None
    for fi in range(nframes):
        tt = fi / fps
        while si < len(scenes_t) - 1 and tt >= scenes_t[si][1]:
            si += 1
        s0, s1 = scenes_t[si]
        img = layers[si].frame(tt - s0, s1 - s0)
        if si > 0 and tt - s0 < XFADE and prev_last is not None:
            img = Image.blend(prev_last, img, (tt - s0) / XFADE)
        if si < len(scenes_t) - 1 and s1 - tt <= 1.5 / fps:
            prev_last = img.copy()
        img = img.copy() if img is getattr(layers[si], "last", None) else img
        if script.get("title"):
            img.paste(band, ((V.W - band.width) // 2, 150), band)
        for a, b, s in subs:
            if a - 0.05 <= tt < b + 0.1:
                si_img = sub_img(s)
                k = 0.88 + 0.12 * V.ease_back((tt - a + 0.05) / 0.15)
                V.paste_center(img, V.scaled(si_img, k), V.W / 2, 1330)
                break
        proc.stdin.write(img.tobytes())
    proc.stdin.close()
    proc.wait()
    for l in layers:
        if hasattr(l, "close"):
            l.close()
    wav.unlink()

    desc = script.get("description", "")
    if "voicevox" in tts.USED:
        credit = cfg["tts"]["voicevox"].get("credit", "VOICEVOX")
        if credit not in desc:
            desc += f"\n\n{credit}"
    meta = {
        "title": script.get("youtube_title") or script.get("title"),
        "description": desc,
        "tts": sorted(tts.USED),
        "writer": script.get("writer", ""),
        "sources": script.get("sources", []),
        "tags": script.get("tags", []),
        "duration": round(total, 2),
        "bgm": bgm_name,
        "credits": sorted(set(credits)),
    }
    (out_dir / f"{name}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[{name}] done -> {mp4} ({time.time() - t0:.0f}s)")
    return mp4
