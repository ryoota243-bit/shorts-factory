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

from . import assets, bgm, llm, reading, thumbnail, tts, visuals as V

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


def merge_cost(gen, render_part):
    """Script-writing cost (recorded when the script was generated) + rendering-time checks."""
    by = dict((gen or {}).get("by_task", {}))
    for k, v in render_part["by_task"].items():
        by[k] = round(by.get(k, 0) + v, 4)
    usd = round(sum(by.values()), 4)
    return {"usd": usd, "jpy": round(usd * llm.USD_JPY), "by_task": by}


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
    llm.reset()
    script = normalize(json.loads(Path(script_path).read_text(encoding="utf-8")))
    for sc in script["scenes"]:  # "約 [16回] も" -> "約[16回]も" (spaces break word detection)
        sc["narration"] = tidy(sc.get("narration", ""))
        v = sc.get("visual") or {}
        if v.get("type") == "quiz" and not v.get("reveal"):  # a highlighted choice would give the answer away
            sc["narration"] = re.sub(r"[\[\]]", "", sc["narration"])
    name = Path(script_path).stem
    theme = {**DEFAULT_THEME, **script.get("theme", {})}
    prons = {**cfg.get("pronunciations", {}), **script.get("pronunciations", {})}
    fps = cfg["video"]["fps"]
    HOOK_ON = bool(cfg["video"].get("hook", False))  # old opening (flash / slam / impact) - kept off
    INTRO_ON = bool(cfg["video"].get("title_intro", True))  # catch copy pops in, ~1 s before the narration
    if cfg["video"].get("style") == "card":
        INTRO_ON = False  # the card style opens with its own spoken title card
    INTRO_LEAD = float(cfg["video"].get("title_intro_lead", 2.0))  # minimum seconds before the main part
    intro_copy = re.sub(r"\s+", "", script.get("thumb_text") or script.get("title") or "")
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
    TEASE_PAUSE = float((cfg.get("rapid") or {}).get("tease_pause", 2.0))

    def scene_chunks(sc):
        """Narration chunks; for a teased fact the set-up line comes first, and the pause follows its last chunk."""
        n = sc.get("narration", "")
        tz = sc.get("tease")
        if tz and n.startswith(tz) and len(n) > len(tz):
            head = split_sentences(tz)
            return head + split_sentences(n[len(tz):]), len(head) - 1
        return split_sentences(n), None

    spoken = [to_speak(ch) for sc in script["scenes"] for ch in scene_chunks(sc)[0]]
    title_line = to_speak(intro_copy) if INTRO_ON and intro_copy else ""
    if title_line:
        spoken = [title_line] + spoken
    fixed = {}
    if use_check:
        try:
            fixed = reading.prepare(spoken, cfg, hints=prons)
        except Exception as e:
            print(f"  [reading] check skipped: {e}")
    voice_parts, subs, scenes_t = [], [], []
    t = 0.0
    intro_len = 0.0
    if title_line:  # Zundamon reads the title; the main part starts only after the title has gone
        ta = tts.synth(fixed.get(title_line, title_line), cfg)
        intro_len = max(INTRO_LEAD, 0.35 + len(ta) / SR + 0.6)
        seg = np.zeros(int(intro_len * SR), np.float32)
        s0_ = int(0.35 * SR)
        seg[s0_:s0_ + len(ta)] = ta[: len(seg) - s0_]
        voice_parts.append(seg)
        t = intro_len
    for si, sc in enumerate(script["scenes"]):
        start = 0.0 if si == 0 else t  # scene 0's picture is the title's backdrop
        lead = 0.12 if (si or title_line) else (0.3 if HOOK_ON else 0.05)
        voice_parts.append(np.zeros(int(lead * SR), np.float32)); t += lead
        chunks, tease_last = scene_chunks(sc)
        for ci, ch in enumerate(chunks):
            line = to_speak(ch)
            a = tts.synth(fixed.get(line, line), cfg)
            subs.append((t, t + len(a) / SR, subtitle_text(ch)))
            voice_parts.append(a); t += len(a) / SR
            gap = 0.12
            if ci == tease_last:  # the set-up line has been read: hold the answer back
                gap = TEASE_PAUSE
                sc["tease_end"] = t - start
                sc["reveal_at"] = t + gap - start
            voice_parts.append(np.zeros(int(gap * SR), np.float32)); t += gap
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
    STYLE = cfg["video"].get("style", "standard")  # "card": trivia channel look (factory/cardstyle.py)
    music, bgm_name = bgm.get(total, root / cfg["bgm"]["dir"], seed, cfg["bgm"].get("style", "standard"))
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
    for sc, (st, en) in zip(script["scenes"], scenes_t):  # quiz countdown: 3 ticks
        v = sc.get("visual") or {}
        if v.get("type") == "quiz" and v.get("countdown") and not v.get("reveal"):
            for k in (3, 2, 1):
                s_ = int((en - k) * SR)
                if s_ > 0:
                    mix[s_:s_ + len(tk)] += (tk * 2.2)[: max(0, min(len(tk), n - s_))]
    for a in beats:  # soft accent on emphasised words
        s_ = int(a * SR); mix[s_:s_ + len(tk)] += tk[: max(0, min(len(tk), n - s_))]
    if HOOK_ON:
        hit = bgm.impact() * 0.55  # opening impact
        mix[: len(hit)] += hit[: len(mix)]
    if INTRO_ON:
        se = bgm.title_se() * 0.5  # light whoosh + chime as the title pops in
        mix[: len(se)] += se[: len(mix)]
    pop = bgm.swipe() * 0.3 if STYLE == "card" else bgm.pop() * 0.25
    for sc, (st, en) in zip(script["scenes"], scenes_t):  # teased facts: soft ticking, then a chime on the answer
        if sc.get("reveal_at"):
            a0, a1 = st + sc["tease_end"], st + sc["reveal_at"]
            k = a0 + 0.25
            while k < a1 - 0.2:
                s_ = int(k * SR); mix[s_:s_ + len(tk)] += (tk * 0.9)[: max(0, min(len(tk), n - s_))]
                k += 0.5
            ch_ = bgm.reveal() * 0.45
            s_ = int(a1 * SR); mix[s_:s_ + len(ch_)] += ch_[: max(0, min(len(ch_), n - s_))]
    for st, _ in scenes_t[1:]:
        s = int(st * SR); mix[s:s + len(pop)] += pop[: max(0, min(len(pop), n - s))]
    mix = np.tanh(mix * 1.1) / np.tanh(1.1)
    fps_ = cfg["video"]["fps"]
    T0 = round(float(cfg["video"].get("thumb_seconds", 0.5)) * fps_) / fps_  # whole frames of thumbnail
    mix = np.concatenate([mix, np.zeros(int(T0 * SR), np.float32)])  # thumbnail frame comes at the end
    wav = out_dir / f"{name}.wav"
    write_wav(wav, mix)

    # ---- 3. layers / overlays ----
    credits = []
    topic = re.sub(r"[\[\]]", "", f"{script.get('title', '')} / {script.get('topic', '')}")
    layers = [make_layer(sc, theme, seed + i, cfg, root, credits, fps, topic) for i, sc in enumerate(script["scenes"])]
    for i, layer in enumerate(layers):  # quiz: show each choice when the narration reads it
        if isinstance(layer, V.QuizLayer) and not layer.reveal:
            s0, s1 = scenes_t[i]
            ch = (script["scenes"][i].get("visual") or {}).get("choices", [])
            sc_subs = [(a, re.sub(r"[\[\]]", "", txt)) for a, b, txt in subs if s0 <= a < s1]
            times, last = [], 0.0
            for c in ch:
                key = re.sub(r"\s", "", c)[:4]
                hit = next((a - s0 for a, txt in sc_subs if key and key in txt and a - s0 >= last), None)
                if hit is None:
                    times = None
                    break
                times.append(hit)
                last = hit
            layer.choice_times = times
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
    hook_img = None if not HOOK_ON else (V.fit_text(hook_copy, 170, 1000, 520, min_size=100, stroke=20, fill=(255, 255, 255),
                           hi=V.hex2rgb(theme["accent"]), shadow=True, spacing=1.1) if hook_copy else None)
    HOOK = 2.7  # seconds the catch copy stays up at the start
    intro_img = (V.fit_text(intro_copy, 150, 980, 460, min_size=90, stroke=18, fill=(255, 255, 255),
                            hi=V.hex2rgb(theme["accent"]), shadow=True, spacing=1.12)
                 if INTRO_ON and intro_copy else None)
    INTRO_END = intro_len - 0.35  # the title has fully faded by intro_len, when the main part starts
    chara = None
    cp = root / cfg["video"].get("intro_character", "assets/characters/zundamon.png")
    if intro_img is not None and cp.exists():
        ch_img = Image.open(cp).convert("RGBA")
        ch_img = ch_img.crop(ch_img.getbbox())
        chara = V.scaled(ch_img, 760 / ch_img.height)  # standing figure, about 40 % of the screen height
    if HOOK_ON and isinstance(layers[0], V.PhotoLayer):
        layers[0].punch = True
    thumb_img = None
    card = None
    if STYLE == "card":
        from .cardstyle import CardStyle
        card = CardStyle(script, layers, cfg)
        thumb_img = card.thumbnail(out_dir / f"{name}.thumb.png")
    else:
        try:
            thumb_img, tcredit = thumbnail.build(script, theme, cfg, root, layers, out_dir / f"{name}.thumb.png")
            if tcredit:
                credits.append(tcredit)
        except Exception as e:
            print(f"  [thumb] skipped: {e}")
    sub_cache = {}

    # rapid format: "雑学 1/5" badge under the title band, popping in with each fact
    def make_badge(label):
        acc = V.hex2rgb(theme["accent"])
        txt = V.rich_text(label, 52, 600, fill=(24, 24, 24), hi=(24, 24, 24), stroke=0)
        b = V.rounded((txt.width + 48, txt.height + 12), (txt.height + 12) // 2, (*acc, 240))
        b.paste(txt, (24, 6), txt)
        return b
    badges = [make_badge(sc["badge"]) if sc.get("badge") else None for sc in script["scenes"]]
    BADGE_Y = (150 + band.height + 18) if script.get("title") else 150

    mcol = V.hex2rgb(theme["marker"]) if theme.get("marker") else V.marker_color(V.hex2rgb(theme["accent"]),
                                                                                 script.get("mood", ""))
    print(f"  [marker] mood={script.get('mood', '-')} colour=#{bytes(mcol).hex()}")

    def sub_img(s, m=None):
        key = (s, None if m is None else round(m * 12))
        if key not in sub_cache:
            sub_cache[key] = V.rich_text(s, 74, 960, hi=V.hex2rgb(theme["accent"]), stroke=11,
                                         marker=None if m is None else key[1] / 12, marker_color=mcol)
        return sub_cache[key]

    SUB_TOP = 1240  # top of a 3-line subtitle (centred at y=1330)
    # camera cuts: long scenes change framing at sentence boundaries so the screen keeps developing
    FRAMINGS = [(1.0, 0, 0), (1.07, 0, 18), (1.06, 28, 10), (1.08, -26, 24)]  # dy >= 0: never push content down
    CUT_MIN_SCENE, CUT_GAP = 4.5, 1.8
    cuts = []
    for i, (st, en) in enumerate(scenes_t):
        c = []
        if en - st >= CUT_MIN_SCENE and not script["scenes"][i].get("no_subtitle"):
            last = st
            for a, b, txt in subs:
                if st + 0.8 < a < en - 0.8 and a - last >= CUT_GAP and not (i == 0 and a < HOOK):
                    c.append(a)
                    last = a
        cuts.append(c)
    if not cfg["video"].get("camera_cuts", False):
        cuts = [[] for _ in scenes_t]

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
        if fi >= nframes - thumb_frames:  # last frames: the thumbnail (pick it as the cover in the app)
            proc.stdin.write((thumb_img or layers[-1].frame(0, 1)).tobytes())
            continue
        tt = fi / fps
        while si < len(scenes_t) - 1 and tt >= scenes_t[si][1]:
            si += 1
        s0, s1 = scenes_t[si]
        if card is not None:  # card style draws the whole screen itself (no title band / subtitles)
            img = card.frame(si, tt - s0, s1 - s0)
            if si > 0 and tt - s0 < card.SWIPE and prev_last is not None:
                img = card.swipe(prev_last, img, (tt - s0) / card.SWIPE)
            if si < len(scenes_t) - 1 and s1 - tt <= 1.5 / fps:
                prev_last = img.copy()
            proc.stdin.write(img.tobytes())
            continue
        if si == 0 and intro_len and not isinstance(layers[0], (V.PhotoLayer, V.VideoLayer)):
            # during the title only the backdrop shows; the diagram/illustration appears with the main part
            lt = tt - intro_len
            img = (layers[0].background(tt, s1 - s0) if lt < 0 else layers[0].frame(lt, s1 - intro_len))
        else:
            img = layers[si].frame(tt - s0, s1 - s0)
        z = 1.0  # the screen itself no longer moves (no push-in, punch-in or camera cuts)
        n_cut = sum(1 for a in cuts[si] if a <= tt)
        fz, fx, fy = FRAMINGS[n_cut % len(FRAMINGS)] if n_cut else FRAMINGS[0]
        if n_cut:  # ease from the previous framing over 0.22 s
            pz, px, py = FRAMINGS[(n_cut - 1) % len(FRAMINGS)]
            e = V.ease_out((tt - cuts[si][n_cut - 1]) / 0.22)
            fz, fx, fy = pz + (fz - pz) * e, px + (fx - px) * e, py + (fy - py) * e
        if isinstance(layers[si], (V.IllustrationLayer, V.TextLayer, V.NumberLayer, V.PhotoLayer)):
            fz, fx, fy = 1 + (fz - 1) * 2.0, fx * 1.4, fy * 1.6  # centred content: a clearly visible cut
        z *= fz
        if z > 1.0001 or fx or fy:
            full_photo = isinstance(layers[si], V.PhotoLayer) and layers[si].full
            if not full_photo:
                # keep the content block [CONTENT_Y] between the title band and the subtitles:
                # cap the zoom to the free height and place the block inside it
                top_free = (150 + band.height + 14) if script.get("title") else 120
                bottom_free = SUB_TOP - 14
                c0, c1 = V.CONTENT_Y
                z = min(z, max(1.0, (bottom_free - top_free) / (c1 - c0)))
                lo, hi = top_free, bottom_free - (c1 - c0) * z      # allowed range for the block's top
                want = c0 - (V.CONTENT_CY - c0) * (z - 1) - fy * z  # zoom about the content centre, then nudge
                top = min(max(want, lo), max(lo, hi))
                y0 = c0 - top / z
            else:
                y0 = V.CONTENT_CY - V.CONTENT_CY / z + fy
            cw, ch = V.W / z, V.H / z
            x0 = min(max(V.W / 2 - (V.W / 2) / z + fx, 0.0), V.W - cw)
            y0 = min(max(y0, 0.0), V.H - ch)
            img = V.view(img, x0, y0, cw, ch)  # sub-pixel: no jitter
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
        in_intro = si == 0 and intro_img is not None and tt < INTRO_END + 0.3
        if in_intro:
            k = 0.55 + 0.45 * V.ease_back(tt / 0.4)              # pops in and lands
            k *= 1 + 0.025 * math.sin(math.pi * V.clamp((tt - 0.4) / 0.8))  # one gentle breath
            a = 1.0 - V.clamp((tt - INTRO_END) / 0.3)
            dim = Image.new("RGB", img.size, (0, 0, 0))
            img = Image.blend(img, dim, 0.35 * a)                  # darken the picture behind the title
            title_y = V.H * (0.30 if chara is not None else 0.42)
            V.place(img, intro_img, V.W / 2, title_y, max(0.01, k), alpha=a * V.clamp(tt / 0.12))
            if chara is not None:  # slides up from the bottom, bobs while reading, leaves with the title
                p_ = V.ease_back((tt - 0.15) / 0.45)
                bob = math.sin(tt * 5.0) * 6 * V.clamp((tt - 0.6) / 0.3)
                cy = V.H + chara.height / 2 - (chara.height * 0.95) * p_ + bob
                V.place(img, chara, V.W / 2 + 230, cy, 1.0, alpha=a)
        if script.get("title") and not in_hook and not (in_intro and tt < INTRO_END):
            b = band
            if in_intro:  # the top title band fades in as the big title leaves
                b = band.copy()
                b.putalpha(b.getchannel("A").point(lambda v, f=V.clamp((tt - INTRO_END) / 0.3): int(v * f)))
            if si == 0 and hook_img is not None and tt < HOOK + 0.3:
                b = band.copy()
                b.putalpha(b.getchannel("A").point(lambda v, f=V.clamp((tt - HOOK) / 0.3): int(v * f)))
            img.paste(b, ((V.W - b.width) // 2, 150), b)
        if badges[si] is not None:
            bk = 0.6 + 0.4 * V.ease_back((tt - s0) / 0.25)
            V.place(img, badges[si], V.W / 2, BADGE_Y + badges[si].height / 2, max(0.02, bk))
        show_subs = not script["scenes"][si].get("no_subtitle")
        for a, b, s in (subs if show_subs else ()):
            if a - 0.05 <= tt < b:
                mk = V.ease_out((tt - a - 0.12) / 0.4) if "[" in s else None  # highlighter sweep
                si_img = sub_img(s, mk)
                if isinstance(layers[si], (V.PhotoLayer, V.VideoLayer)):  # dark backing on busy photos
                    box = V.rounded((min(V.W - 20, si_img.width + 40), si_img.height + 10), 28, (0, 0, 0, 110))
                    img.paste(box, ((V.W - box.width) // 2, int(1330 - box.height / 2)), box)
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
    if chara is not None and cfg["video"].get("intro_character_credit"):
        desc += "\n" + cfg["video"]["intro_character_credit"]  # credit required by the art's terms
    qa_issues = []
    if os.environ.get("ANTHROPIC_API_KEY") and cfg.get("qa", True):
        try:
            from . import qa
            qa_issues = qa.review(mp4, script, scenes_t, subs, offset=0.0)
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
        "cost": merge_cost(script.get("gen_cost"), llm.cost_summary()),
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
