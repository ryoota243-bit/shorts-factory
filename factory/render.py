"""Turn a script JSON into a vertical MP4."""
import json
import math
import os
import re
import subprocess
import time
import wave
from pathlib import Path

import numpy as np
from PIL import Image

from . import assets, bgm, reading, thumbnail, tts, visuals as V

SR = tts.SR
DEFAULT_THEME = {"bg": ["#1e3c72", "#6a1b9a"], "accent": "#ffd600"}
XFADE = 0.18


FULLWIDTH = re.compile(r"[０-９Ａ-Ｚａ-ｚ．％]")


def normalize(obj):
    """Full-width digits/latin -> half-width everywhere (３０日 -> 30日)."""
    if isinstance(obj, str):
        return FULLWIDTH.sub(lambda m: chr(ord(m.group(0)) - 0xFEE0), obj)
    if isinstance(obj, list):
        return [normalize(x) for x in obj]
    if isinstance(obj, dict):
        return {k: normalize(v) for k, v in obj.items()}
    return obj


JA = r"[ぁ-んァ-ヶー一-龯々〆、。！？「」『』（）\[\]]"


def tidy(text):
    """Drop spaces next to Japanese characters/brackets; keep spaces between latin words."""
    text = re.sub(rf"\s+(?={JA})", "", text)
    return re.sub(rf"(?<={JA})\s+", "", text).strip()


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
    inside_hi, n, depth = set(), 0, 0  # plain indices inside a [highlight]
    for ch in marked:
        if ch == "[":
            depth = 1
        elif ch == "]":
            depth = 0
        else:
            n += 1
            if depth:
                inside_hi.add(n)  # a split *after* this char would cut the highlight

    def outside_brackets(p):
        if p in inside_hi and (p + 1) in inside_hi:
            return False
        head = plain[:p]
        return all(head.count(o) <= head.count(c) for o, c in ("「」", "『』", "（）", "()"))
    commas = [p for p in commas if outside_brackets(p)]
    cands = commas or sorted(p for p in (V.break_points(plain) or ())
                             if 6 <= p <= len(plain) - 5 and outside_brackets(p))
    if not cands:
        return [marked]
    # prefer clause ends (after は/が/を/に/で/、) and avoid splitting "AのB" phrases
    def cost(i):
        prev = plain[i - 1]
        return abs(i - mid) + (6 if prev == "の" else 0) - (2 if prev in "はがをにでも、" else 0)
    a, b = _split_at(marked, min(cands, key=cost))
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


def make_layer(scene, theme, seed, cfg, root, credits, fps, topic=""):
    v = dict(scene.get("visual") or {"type": "text", "text": scene.get("caption", "")})
    found = assets.resolve(v, cfg, root / "cache", root,
                           narration=re.sub(r"[\[\]]", "", scene.get("narration", "")), topic=topic)
    if found:
        credits.append(found["credit"])
        if found["kind"] == "video":
            return V.VideoLayer(v, theme, seed, found["path"], fps)
        layer = V.PhotoLayer(v, theme, seed, found["path"])
        layer.src_path = found["path"]
        return layer
    if v.get("type") in ("photo", "video", "ai"):
        v = v.get("fallback") or {"type": "illustration", "emoji": v.get("emoji", "🔍"),
                                  "text": v.get("query", "")}
    return V.LAYERS[v["type"]](v, theme, seed)


def build_description(script, cfg, voicevox=True):
    """Video description: summary + main sources + channel footer + voice credit."""
    ch = cfg.get("channel", {})
    credit = cfg["tts"]["voicevox"].get("credit", "VOICEVOX")
    body = script.get("description", "").replace(credit, "").strip()
    parts = [body]
    srcs = [u for u in script.get("sources", []) if u.startswith("http")][: ch.get("max_sources", 3)]
    if srcs:
        parts.append("▼主な出典\n" + "\n".join(srcs))
    if ch.get("footer"):
        parts.append(ch["footer"])
    if voicevox:
        parts.append(f"音声：{credit}")
    return "\n\n".join(p for p in parts if p)[:4900]


def write_wav(path, x):
    st = np.repeat((np.clip(x, -1, 1) * 32767).astype(np.int16)[:, None], 2, axis=1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(st.tobytes())


def render(script_path, cfg, root, out_dir):
    t0 = time.time()
    root = Path(root)
    script = normalize(json.loads(Path(script_path).read_text(encoding="utf-8")))
    for sc in script["scenes"]:  # "約 [16回] も" -> "約[16回]も" (spaces break word detection)
        sc["narration"] = tidy(sc.get("narration", ""))
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
    # With VOICEVOX, readings are checked against the engine (script pronunciations are only hints;
    # blind text replacement broke word boundaries, e.g. 朝 read as チョウ). Other engines: replace.
    use_check = cfg["tts"]["backend"] == "voicevox"
    to_speak = (lambda ch: tts_text(ch, {})) if use_check else (lambda ch: tts_text(ch, prons))
    spoken = [to_speak(ch) for sc in script["scenes"] for ch in split_sentences(sc.get("narration", ""))]
    fixed = {}
    if use_check:
        try:
            fixed = reading.prepare(spoken, cfg, hints=prons)
        except Exception as e:
            print(f"  [reading] check skipped: {e}")
    voice_parts, subs, scenes_t = [], [], []
    t = 0.0
    for si, sc in enumerate(script["scenes"]):
        start = t
        lead = 0.12 if si else 0.3  # let the opening hit land first
        voice_parts.append(np.zeros(int(lead * SR), np.float32)); t += lead
        for ch in split_sentences(sc.get("narration", "")):
            line = to_speak(ch)
            a = tts.synth(fixed.get(line, line), cfg)
            subs.append((t, t + len(a) / SR, subtitle_text(ch)))
            voice_parts.append(a); t += len(a) / SR
            voice_parts.append(np.zeros(int(0.12 * SR), np.float32)); t += 0.12
        tail = max(sc.get("hold", 0.25), (sc.get("min_duration", 1.6) - (t - start)))
        voice_parts.append(np.zeros(int(tail * SR), np.float32)); t += tail
        scenes_t.append((start, t))
    total = t
    # keep each subtitle on screen through short pauses (no blank flicker between lines)
    held = []
    for i, (a, b, txt) in enumerate(subs):
        scene_end = next(e for st, e in scenes_t if st <= a < e)
        nxt = subs[i + 1][0] if i + 1 < len(subs) else total
        held.append((a, min(scene_end, nxt) - 0.02 if min(scene_end, nxt) - b < 0.8 else b + 0.1, txt))
    subs = held
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
    beats = []
    for a, b, txt in subs:
        if "[" in txt and a > 3.0 and (not beats or a - beats[-1] >= 1.5):
            beats.append(a)
    print(f"  [beats] {', '.join(f'{b:.1f}s' for b in beats) or 'none'}")
    tk = bgm.tick() * 0.16
    for a in beats:  # soft accent on emphasised words
        s_ = int(a * SR); mix[s_:s_ + len(tk)] += tk[: max(0, min(len(tk), n - s_))]
    hit = bgm.impact() * 0.55  # opening impact
    mix[: len(hit)] += hit[: len(mix)]
    pop = bgm.pop() * 0.25
    for st, _ in scenes_t[1:]:
        s = int(st * SR); mix[s:s + len(pop)] += pop[: max(0, min(len(pop), n - s))]
    mix = np.tanh(mix * 1.1) / np.tanh(1.1)
    fps_ = cfg["video"]["fps"]
    T0 = round(float(cfg["video"].get("thumb_seconds", 0.5)) * fps_) / fps_  # whole frames of thumbnail
    mix = np.concatenate([np.zeros(int(T0 * SR), np.float32), mix])
    wav = out_dir / f"{name}.wav"
    write_wav(wav, mix)

    # ---- 3. layers / overlays ----
    credits = []
    topic = re.sub(r"[\[\]]", "", f"{script.get('title', '')} / {script.get('topic', '')}")
    layers = [make_layer(sc, theme, seed + i, cfg, root, credits, fps, topic) for i, sc in enumerate(script["scenes"])]
    for i, layer in enumerate(layers):  # reveal numbers when the narration says them
        if isinstance(layer, V.NumberLayer):
            s0, s1 = scenes_t[i]
            key = re.sub(r"\.0$", "", str(layer.value))
            hit = next((a for a, b, txt in subs if s0 <= a < s1 and key in txt.replace(",", "")), None)
            if hit is not None and 0.6 < hit - s0 <= 2.5:  # a long "?" looks broken
                layer.delay = hit - s0
    title = V.rich_text(script.get("title", ""), 76, 960, hi=V.hex2rgb(theme["accent"]), stroke=0)
    band = V.rounded((min(1040, title.width + 70), title.height + 40), 30, (0, 0, 0, 170))
    band.paste(title, ((band.width - title.width) // 2, 20), title)
    # diagram scenes borrow a blurred photo from the video so the middle is not one flat colour
    photos = [(i, l.src_path) for i, l in enumerate(layers) if getattr(l, "src_path", None)]
    if photos:
        tint = V.hex2rgb(theme["bg"][0])
        cache_bd = {}
        for i, l in enumerate(layers):
            if getattr(l, "src_path", None) or isinstance(l, V.VideoLayer):
                continue
            src = min(photos, key=lambda p: (abs(p[0] - i), p[0] > i))[1]
            if src not in cache_bd:
                cache_bd[src] = V.backdrop(src, tint)
            l.bgimg = cache_bd[src]
    hook_copy = re.sub(r"\s+", "", script.get("thumb_text") or "")
    hook_img = (V.fit_text(hook_copy, 170, 1000, 520, min_size=100, stroke=20, fill=(255, 255, 255),
                           hi=V.hex2rgb(theme["accent"]), shadow=True, spacing=1.1) if hook_copy else None)
    HOOK = 2.7  # seconds the catch copy stays up at the start
    if isinstance(layers[0], V.PhotoLayer):
        layers[0].punch = True
    thumb_img = None
    try:
        thumb_img, tcredit = thumbnail.build(script, theme, cfg, root, layers, out_dir / f"{name}.thumb.png")
        if tcredit:
            credits.append(tcredit)
    except Exception as e:
        print(f"  [thumb] skipped: {e}")
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
    nframes = int((total + T0) * fps)
    print(f"[{name}] rendering {total + T0:.1f}s / {nframes} frames...")
    si, prev_last = 0, None
    thumb_frames = round(T0 * fps)
    for fi in range(nframes):
        if fi < thumb_frames:
            proc.stdin.write((thumb_img or layers[0].frame(0, 1)).tobytes())
            continue
        tt = (fi - thumb_frames) / fps
        while si < len(scenes_t) - 1 and tt >= scenes_t[si][1]:
            si += 1
        s0, s1 = scenes_t[si]
        img = layers[si].frame(tt - s0, s1 - s0)
        z = 1.0
        if not isinstance(layers[si], (V.PhotoLayer, V.VideoLayer)):  # slow push-in keeps diagrams alive
            z = 1 + 0.04 * max(0.0, tt - s0) / max(s1 - s0, 0.1)
        bt = next((tt - a for a in beats if 0 <= tt - a < 0.45), None)
        if bt is not None:  # "beat": quick 3 % punch-in when an emphasised word appears
            z *= 1 + 0.03 * math.sin(math.pi * bt / 0.45)
        if z > 1.0001:
            cw, ch = V.W / z, V.H / z
            img = V.view(img, (V.W - cw) / 2, (V.H - ch) / 2, cw, ch)  # sub-pixel: no jitter
        if si > 0 and tt - s0 < XFADE and prev_last is not None:
            img = Image.blend(prev_last, img, (tt - s0) / XFADE)
        if si < len(scenes_t) - 1 and s1 - tt <= 1.5 / fps:
            prev_last = img.copy()
        img = img.copy() if img is getattr(layers[si], "last", None) else img
        in_hook = si == 0 and hook_img is not None and tt < HOOK
        if in_hook:
            k = 1.35 - 0.35 * V.ease_back(tt / 0.35)
            a = 1.0 - V.clamp((tt - (HOOK - 0.35)) / 0.35)
            shake = math.sin(tt * 90) * 14 * max(0.0, 1 - tt / 0.35)
            V.paste_center(img, V.scaled(hook_img, max(0.01, k)), V.W / 2 + shake, V.H * 0.42, alpha=a)
            if tt < 0.12:  # white flash on the first frames
                img = Image.blend(img, Image.new("RGB", img.size, (255, 255, 255)), 0.7 * (1 - tt / 0.12))
        if script.get("title") and not in_hook:
            b = band
            if si == 0 and hook_img is not None and tt < HOOK + 0.3:
                b = band.copy()
                b.putalpha(b.getchannel("A").point(lambda v, f=V.clamp((tt - HOOK) / 0.3): int(v * f)))
            img.paste(b, ((V.W - b.width) // 2, 150), b)
        for a, b, s in subs:
            if a - 0.05 <= tt < b:
                si_img = sub_img(s)
                if isinstance(layers[si], (V.PhotoLayer, V.VideoLayer)):  # dark backing on busy photos
                    box = V.rounded((min(V.W - 20, si_img.width + 40), si_img.height + 10), 28, (0, 0, 0, 110))
                    img.paste(box, ((V.W - box.width) // 2, int(1330 - box.height / 2)), box)
                if "[" in s and a in beats:  # emphasised line lands a little bigger, then settles
                    k = 1.0 + 0.2 * (1 - V.ease_out((tt - a + 0.05) / 0.25))
                else:
                    k = 0.88 + 0.12 * V.ease_back((tt - a + 0.05) / 0.15)
                if abs(k - 1) > 0.002:
                    V.place(img, si_img, V.W / 2, 1330, k)
                else:
                    V.paste_center(img, si_img, V.W / 2, 1330)
                break
        proc.stdin.write(img.tobytes())
    proc.stdin.close()
    proc.wait()
    for l in layers:
        if hasattr(l, "close"):
            l.close()
    wav.unlink()

    desc = build_description(script, cfg, voicevox="voicevox" in tts.USED)
    qa_issues = []
    if os.environ.get("ANTHROPIC_API_KEY") and cfg.get("qa", True):
        try:
            from . import qa
            qa_issues = qa.review(mp4, script, scenes_t, subs, offset=T0)
            for q in qa_issues:
                print(f"  [qa] {q['severity']} シーン{q['scene']}: {q['issue']}")
        except Exception as e:
            print(f"  [qa] skipped: {e}")
    meta = {
        "title": script.get("youtube_title") or script.get("title"),
        "description": desc,
        "tts": sorted(tts.USED),
        "writer": script.get("writer", ""),
        "factcheck": script.get("factcheck", []),
        "qa": qa_issues,
        "sources": script.get("sources", []),
        "tags": script.get("tags", []),
        "duration": round(total, 2),
        "bgm": bgm_name,
        "credits": sorted(set(credits)),
        "thumbnail": f"{name}.thumb.png" if thumb_img is not None else "",
        "thumb_text": re.sub(r"[\[\]]", "", script.get("thumb_text") or script.get("title", "")),
    }
    (out_dir / f"{name}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[{name}] done -> {mp4} ({time.time() - t0:.0f}s)")
    return mp4
