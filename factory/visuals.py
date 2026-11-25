"""Scene visuals drawn with Pillow. Each layer has frame(t, dur) -> RGB Image (W x H)."""
import functools
import re
import math
import random
import subprocess

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1080, 1920
FONT_DIR = "/usr/share/fonts/opentype/noto/"
EMOJI_FONT = "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"
CONTENT_Y = (450, 1170)          # area between title band and subtitles
CONTENT_CY = sum(CONTENT_Y) // 2
NO_HEAD = set("、。，．！？!?」』）)ーぁぃぅぇぉっゃゅょァィゥェォッャュョ…・%％")


@functools.lru_cache(None)
def font(size, weight="Black"):
    return ImageFont.truetype(f"{FONT_DIR}NotoSansCJK-{weight}.ttc", int(size), index=0)


def hex2rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


# ---------- easing ----------
def clamp(x):
    return max(0.0, min(1.0, x))


def ease_out(x):
    x = clamp(x)
    return 1 - (1 - x) ** 3


def ease_back(x):
    x = clamp(x)
    c = 1.70158
    return 1 + (c + 1) * (x - 1) ** 3 + c * (x - 1) ** 2


# ---------- text ----------
def parse_marked(text, base, hi):
    out, color = [], base
    for ch in text:
        if ch == "[":
            color = hi
        elif ch == "]":
            color = base
        else:
            out.append((ch, color))
    return out


SYMBOL_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


@functools.lru_cache(None)
def _cmap(path):
    try:
        from fontTools.ttLib import TTFont
        return frozenset(TTFont(path, fontNumber=0, lazy=True).getBestCmap())
    except Exception:
        return frozenset()


def _in_emoji_range(o):
    return (0x1F000 <= o <= 0x1FAFF or 0x2600 <= o <= 0x27BF or 0x2B00 <= o <= 0x2BFF
            or o in (0x203C, 0x2049, 0x2122, 0x2139, 0x231A, 0x231B, 0x23E9, 0x23F0, 0x23F3))


@functools.lru_cache(None)
def glyph_kind(ch):
    """'emoji' (color emoji font), 'symbol' (DejaVu), or 'text' (Noto CJK)."""
    o = ord(ch)
    if _in_emoji_range(o) and o in _cmap(EMOJI_FONT):
        return "emoji"
    if o in _cmap(f"{FONT_DIR}NotoSansCJK-Black.ttc"):
        return "text"
    if o in _cmap(SYMBOL_FONT):
        return "symbol"
    return "text"


def is_emoji(ch):
    return glyph_kind(ch) == "emoji"


@functools.lru_cache(None)
def symbol_font(size):
    return ImageFont.truetype(SYMBOL_FONT, int(size))


def font_for(fnt, ch):
    return symbol_font(fnt.size) if glyph_kind(ch) == "symbol" else fnt


def glen(fnt, ch):
    return fnt.size * 1.05 if is_emoji(ch) else font_for(fnt, ch).getlength(ch)


OPENERS = set("「『（(［[【〈《“‘・")
ASCIIISH = re.compile(r"[0-9０-９A-Za-zＡ-Ｚａ-ｚ.,．，%％:/+\-]+")


@functools.lru_cache(4096)
def break_points(plain):
    """Indices where a line may break (word starts that are not particles/symbols).

    Digit/latin runs are kept whole (the analyzer rewrites them, e.g. 10 -> 十)."""
    try:
        import pyopenjtalk
    except Exception:
        return None
    pts, pos, prev, prev_num, prev_g1, prev_str = set(), 0, "", False, "", ""
    noun_run = 0
    for m in re.finditer(ASCIIISH.pattern + r"|[^0-9０-９A-Za-zＡ-Ｚａ-ｚ.,．，%％:/+\-]+", plain):
        seg = m.group(0)
        if ASCIIISH.fullmatch(seg):
            if pos and prev not in ("接頭詞",) and not plain[:pos].endswith(("分の", "約", "第", "No.", "万", "億", "兆", "時速", "秒速", "分速")):
                pts.add(pos)
            pos += len(seg); prev, prev_num = "名詞", True
            continue
        feats = pyopenjtalk.run_frontend(seg)
        if sum(len(f["string"]) for f in feats) != len(seg):
            feats = [{"string": c, "pos": "記号" if c in "、。！？" else "名詞"} for c in seg]
        for f in feats:
            attach = (f["pos"] in ("助詞", "助動詞", "記号") or f.get("pos_group1") in ("接尾", "非自立")
                      or prev == "接頭詞" or prev_num or (prev_g1 == "サ変接続" and f["pos"] == "動詞")
                      or (prev_str == "と" and f["string"][:1] in ("し", "い", "言"))
                      or (prev in ("形容詞", "連体詞") and f["pos"] == "名詞")
                      or (prev == "名詞" and f["pos"] == "名詞" and noun_run < 7
                          and f.get("pos_group1") not in ("代名詞",) and prev_g1 not in ("代名詞",)))
            if pos and not attach and f["string"][0] not in NO_HEAD:
                pts.add(pos)
            if f["pos"] == "記号" and f["string"] in "、。！？":
                pts.add(pos + len(f["string"]))
            noun_run = noun_run + len(f["string"]) if f["pos"] == "名詞" else 0
            prev, prev_num, prev_g1, prev_str = f["pos"], False, f.get("pos_group1", ""), f["string"]
            pos += len(f["string"])
    # never leave an opening bracket at a line end, never break inside quotes/parentheses
    def outside(p):
        head = plain[:p]
        return all(head.count(o) <= head.count(c) for o, c in ("「」", "『』", "（）", "()", "【】"))
    return {p for p in pts if p < len(plain) and plain[p - 1] not in OPENERS and outside(p)}


def _wrap_target(chars, fnt, target, pts):
    lines, w, last_ok = [[]], 0.0, None
    for idx, (ch, col) in enumerate(chars):
        if ch == "\n":
            lines.append([]); w = 0; last_ok = None; continue
        cw = glen(fnt, ch)
        ok = (pts is None or idx in pts) and ch not in NO_HEAD
        if w + cw > target and lines[-1]:
            if ok:
                lines.append([]); w = 0; last_ok = None
            elif last_ok:
                tail = lines[-1][last_ok:]
                lines[-1] = lines[-1][:last_ok]
                lines.append(tail); w = sum(glen(fnt, c) for c, _ in tail)
                last_ok = None
        if ok and lines[-1]:
            last_ok = len(lines[-1])
        lines[-1].append((ch, col)); w += cw
    return [l for l in lines if l]


def wrap(chars, fnt, max_w):
    plain = "".join(c for c, _ in chars)
    if "\n" in plain:
        return _wrap_target(chars, fnt, max_w, None)
    pts = break_points(plain)
    total = sum(glen(fnt, c) for c, _ in chars)
    n0 = max(1, math.ceil(total / max_w))
    for n in range(n0, n0 + 4):
        target = total / n + fnt.size * 0.6
        while True:
            lines = _wrap_target(chars, fnt, min(max_w, target), pts)
            if len(lines) <= n:
                return lines
            if target >= max_w:
                break
            target += fnt.size
    return _wrap_target(chars, fnt, max_w, None)


def rich_text(text, size, max_w=980, fill=(255, 255, 255), hi=(255, 214, 0), stroke=0,
              stroke_fill=(0, 0, 0), weight="Black", spacing=1.25, shadow=False):
    """Render text ("[...]" = highlight) to a tight RGBA image, centered lines."""
    f = font(size, weight)
    text = text.replace("\ufe0f", "").replace("\u200d", "")
    chars = parse_marked(text, tuple(fill), tuple(hi))
    lines = wrap(chars, f, max_w)
    lh = int(size * spacing)
    widths = [sum(glen(f, c) for c, _ in l) for l in lines]
    pad = stroke + 8 + (8 if shadow else 0)
    img_w = int(max(widths) + pad * 2)
    img_h = int(lh * (len(lines) - 1) + size * 1.2 + pad * 2)
    img = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    passes = []
    if shadow:
        passes.append(("shadow", 8))
    if stroke:
        passes.append(("stroke", 0))
    passes.append(("fill", 0))
    for kind, off in passes:
        for li, line in enumerate(lines):
            x = (img_w - widths[li]) / 2
            y = pad + li * lh - size * 0.08
            for ch, col in line:
                if is_emoji(ch):
                    if kind == "fill":
                        e = emoji(ch, int(size * 0.95))
                        img.paste(e, (int(x + (size * 1.05 - e.width) / 2), int(y + size * 0.2)), e)
                    x += size * 1.05
                    continue
                cf = font_for(f, ch)
                yy = y + (size * 0.12 if cf is not f else 0)
                if kind == "shadow":
                    d.text((x + off, yy + off), ch, font=cf, fill=(0, 0, 0, 140),
                           stroke_width=stroke, stroke_fill=(0, 0, 0, 140))
                elif kind == "stroke":
                    d.text((x, yy), ch, font=cf, fill=stroke_fill, stroke_width=stroke, stroke_fill=stroke_fill)
                else:
                    d.text((x, yy), ch, font=cf, fill=col)
                x += cf.getlength(ch)
    return img


@functools.lru_cache(None)
def emoji(s, size):
    f = ImageFont.truetype(EMOJI_FONT, 109)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    b = probe.textbbox((0, 0), s, font=f, embedded_color=True)
    im = Image.new("RGBA", (b[2] - b[0] + 8, b[3] - b[1] + 8), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((4 - b[0], 4 - b[1]), s, font=f, embedded_color=True)
    k = size / im.height
    return im.resize((max(1, round(im.width * k)), size), Image.LANCZOS)


def scaled(img, k):
    if abs(k - 1) < 0.01:
        return img
    return img.resize((max(1, int(img.width * k)), max(1, int(img.height * k))), Image.BILINEAR)


def paste_center(dst, img, cx, cy, alpha=1.0):
    if alpha < 1:
        img = img.copy()
        img.putalpha(img.getchannel("A").point(lambda a: int(a * alpha)))
    dst.paste(img, (int(cx - img.width / 2), int(cy - img.height / 2)), img)


def view(src, x, y, w, h):
    """Sub-pixel crop+resize of src to the full frame (no 1-px stepping in slow pans/zooms)."""
    return src.resize((W, H), Image.BILINEAR, box=(x, y, x + w, y + h))


def place(dst, img, cx, cy, k=1.0, rot=0.0, alpha=1.0):
    """Paste img scaled by k and rotated by rot degrees, centred at (cx, cy), at sub-pixel precision.

    Continuous motion (sway, bob, breathing) drawn this way moves smoothly instead of
    snapping between whole pixels, which is what made animated elements look jittery."""
    if k <= 0.01:
        return
    w, h = img.width, img.height
    a = math.radians(rot)
    ca, sa = math.cos(a), math.sin(a)
    bw = (abs(w * ca) + abs(h * sa)) * k
    bh = (abs(w * sa) + abs(h * ca)) * k
    x0, y0 = cx - bw / 2, cy - bh / 2
    ix, iy = math.floor(x0), math.floor(y0)
    ow, oh = math.ceil(bw + (x0 - ix)) + 2, math.ceil(bh + (y0 - iy)) + 2
    c, s_ = ca / k, sa / k
    ox, oy = ix - cx, iy - cy
    coeffs = (c, s_, c * ox + s_ * oy + w / 2, -s_, c, -s_ * ox + c * oy + h / 2)
    out = img.transform((ow, oh), Image.AFFINE, coeffs, resample=Image.BICUBIC)
    if alpha < 1:
        out.putalpha(out.getchannel("A").point(lambda v: int(v * alpha)))
    dst.paste(out, (ix, iy), out)


def rounded(size, radius, fill):
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius, fill=fill)
    return im


# ---------- backgrounds ----------
@functools.lru_cache(16)
def gradient_bg(c1, c2, seed):
    """Tall gradient with soft bokeh circles; scrolled slowly for motion."""
    hh = int(H * 1.25)
    a, b = np.array(hex2rgb(c1), np.float32), np.array(hex2rgb(c2), np.float32)
    yy = np.linspace(0, 1, hh)[:, None, None]
    xx = np.linspace(0, 1, W)[None, :, None]
    t = np.clip(yy * 0.8 + xx * 0.2, 0, 1)
    arr = (a * (1 - t) + b * t).astype(np.uint8).repeat(1, 1)
    img = Image.fromarray(np.broadcast_to(arr, (hh, W, 3)).copy(), "RGB")
    over = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(over)
    rng = random.Random(seed)
    for _ in range(14):
        r = rng.randint(60, 260)
        x, y = rng.randint(-100, W + 100), rng.randint(-100, hh + 100)
        d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 255, rng.randint(10, 28)))
    over = over.filter(ImageFilter.GaussianBlur(18))
    img = img.convert("RGBA")
    img.alpha_composite(over)
    return img.convert("RGB")


class Layer:
    def __init__(self, spec, theme, seed=0):
        self.spec, self.theme, self.seed = spec, theme, seed
        self.accent = hex2rgb(theme.get("accent", "#ffd600"))

    bgimg = None  # optional blurred photo backdrop (set by the renderer)

    def background(self, t, dur):
        if self.bgimg is not None:
            off = (self.bgimg.height - H) * min(1.0, t / max(dur, 0.1))
            return view(self.bgimg, 0, off, W, H)
        bg = gradient_bg(self.theme["bg"][0], self.theme["bg"][1], self.seed)
        off = (bg.height - H) * (0.5 + 0.5 * math.sin(self.seed + t * 0.25))
        return view(bg, 0, off, W, H)

    def frame(self, t, dur):
        return self.background(t, dur)


class TextLayer(Layer):
    def __init__(self, spec, theme, seed=0):
        super().__init__(spec, theme, seed)
        self.txt = rich_text(spec.get("text", ""), spec.get("size", 120), 940, hi=self.accent,
                             stroke=10, shadow=True)
        self.emo = emoji(spec["emoji"], spec.get("emoji_size", 300)) if spec.get("emoji") else None

    def frame(self, t, dur):
        im = self.background(t, dur)
        cy = CONTENT_CY
        if self.emo:
            k = ease_back(t / 0.45)
            place(im, self.emo, W / 2, cy - 170 + math.sin(t * 3) * 12 * ease_out(t / 0.6), max(0.01, k))
            cy += 170
        paste_center(im, scaled(self.txt, max(0.01, ease_back((t - 0.15) / 0.4))), W / 2, cy)
        return im


class IllustrationLayer(Layer):
    def __init__(self, spec, theme, seed=0):
        super().__init__(spec, theme, seed)
        self.emo = emoji(spec.get("emoji", "❓"), spec.get("size", 480))
        self.cap = rich_text(spec["text"], 84, 900, hi=self.accent, stroke=8) if spec.get("text") else None
        self.glow = Image.new("RGBA", (760, 760), (0, 0, 0, 0))
        ImageDraw.Draw(self.glow).ellipse((80, 80, 680, 680), fill=(255, 255, 255, 60))
        self.glow = self.glow.filter(ImageFilter.GaussianBlur(40))

    def frame(self, t, dur):
        im = self.background(t, dur)
        cy = CONTENT_CY - (60 if self.cap else 0)
        paste_center(im, self.glow, W / 2, cy)
        settle = ease_out(t / 0.6)  # idle motion fades in after the pop
        k = ease_back(t / 0.5) * (1 + 0.03 * math.sin(t * 4) * settle)
        rot = math.sin(t * 2.2) * 4 * settle
        place(im, self.emo, W / 2, cy + math.sin(t * 2.5) * 14 * settle, max(0.01, k), rot)
        if self.cap:
            paste_center(im, self.cap, W / 2, CONTENT_Y[1] - 50, alpha=ease_out((t - 0.3) / 0.3))
        return im


class NumberLayer(Layer):
    def __init__(self, spec, theme, seed=0):
        super().__init__(spec, theme, seed)
        self.value = spec["value"]
        unit = spec.get("unit", "")
        self.count = spec.get("count", not (unit.startswith("年") and isinstance(self.value, (int, float))
                                            and self.value > 1000))
        self.unit = rich_text(spec.get("unit", ""), 130, stroke=10, fill=self.accent) if spec.get("unit") else None
        self.label = rich_text(spec.get("label", ""), 66, 900, stroke=0, fill=(30, 30, 30), hi=(220, 30, 60))
        self.pill = rounded((self.label.width + 70, self.label.height + 30), 50, (255, 255, 255, 240))
        self.cache = {}
        self.delay = 0.0  # set by the renderer: seconds until the narration mentions the number

    def num_img(self, s):
        if s not in self.cache:
            im = rich_text(s, 360, max_w=5000, stroke=14, fill=self.accent, shadow=True, spacing=1.0)
            limit = 900 - (self.unit.width if self.unit else 0)
            self.cache[s] = scaled(im, min(1.0, limit / im.width))
        return self.cache[s]

    def frame(self, t, dur):
        im = self.background(t, dur)
        paste_center(im, self.pill, W / 2, CONTENT_Y[0] + 80, alpha=ease_out(t / 0.3))
        paste_center(im, self.label, W / 2, CONTENT_Y[0] + 80, alpha=ease_out(t / 0.3))
        v = self.value
        t = t - self.delay
        if t < 0:
            q = fit_text("？", 300, 400, 400, stroke=14, fill=self.accent, shadow=True)
            paste_center(im, q, W / 2, CONTENT_CY + 60, alpha=0.85)
            return im
        if isinstance(v, (int, float)):
            p = ease_out((t - 0.1) / 0.9)
            cur = v * p
            if not self.count:
                cur = v
            sep = "," if abs(v) >= 10000 else ""
            s = f"{cur:{sep}.0f}" if float(v).is_integer() else f"{cur:{sep}.1f}"
        else:
            s = str(v)
        n = self.num_img(s)
        k = ease_back((t - 0.1) / 0.5) if not (isinstance(v, (int, float)) and self.count) else 1 + 0.08 * (1 - ease_out((t - 1.0) / 0.3)) * (t > 1.0)
        n = scaled(n, max(0.01, k))
        uw = self.unit.width if self.unit else 0
        cx = W / 2 - uw / 2
        cy = CONTENT_CY + 60
        paste_center(im, n, cx, cy)
        if self.unit:
            paste_center(im, self.unit, cx + n.width / 2 + uw / 2 - 10, cy + 70, alpha=ease_out((t - 0.1) / 0.3))
        return im


def fit_text(text, size, max_w, max_h, min_size=24, **kw):
    """rich_text shrunk (font size first, then scale) until it fits max_w x max_h."""
    sz = size
    while True:
        im = rich_text(text, sz, max_w - 20, **kw)
        if (im.width <= max_w and im.height <= max_h) or sz <= min_size:
            break
        sz = int(sz * 0.9)
    k = min(1.0, max_w / im.width, max_h / im.height)
    return scaled(im, k)


def readable_on_white(rgb):
    lum = (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255
    return rgb if lum < 0.5 else tuple(int(c * 0.5 / lum) for c in rgb)


class CompareLayer(Layer):
    CW, CH = 440, 640

    def __init__(self, spec, theme, seed=0):
        super().__init__(spec, theme, seed)
        self.cols = []
        CW, CH = self.CW, self.CH

        def put(card, img, top, h):
            card.paste(img, ((CW - img.width) // 2, int(top + (h - img.height) / 2)), img)

        for it in spec["items"][:2]:
            col = hex2rgb(it.get("color", "#333333"))
            card = rounded((CW, CH), 40, (255, 255, 255, 235))
            d = ImageDraw.Draw(card)
            put(card, fit_text(it.get("label", ""), 64, 410, 112, min_size=46, fill=(40, 40, 40)), 16, 112)
            if it.get("emoji"):
                put(card, emoji(it["emoji"], 150), 130, 160)
            else:
                d.ellipse((CW / 2 - 75, 135, CW / 2 + 75, 285), fill=col)
            put(card, fit_text(it.get("value", ""), 120, 410, 190, fill=readable_on_white(col)), 300, 190)
            if it.get("note"):
                note = fit_text(it["note"], 44, 400, 135, min_size=40, fill=(70, 70, 70), weight="Bold")
                if note.height >= 40 * 0.9:   # still readable on a phone; otherwise leave it out
                    put(card, note, 495, 135)
                else:
                    print(f"  [visuals] compare note too long, omitted: {it['note']}")
            self.cols.append(card)
        self.vs = rich_text(spec.get("vs", "VS"), 100, stroke=10, fill=self.accent, shadow=True)

    def frame(self, t, dur):
        im = self.background(t, dur)
        for i, card in enumerate(self.cols):
            k = ease_back((t - i * 0.25) / 0.45)
            x = W / 2 + (-1 if i == 0 else 1) * 245
            paste_center(im, scaled(card, max(0.01, k)), x, CONTENT_CY)
        vs_y = CONTENT_CY - self.CH / 2 + 210   # level with the icons, clear of the card text
        paste_center(im, scaled(self.vs, max(0.01, ease_back((t - 0.5) / 0.4))), W / 2, vs_y)
        return im


class ListLayer(Layer):
    def __init__(self, spec, theme, seed=0):
        super().__init__(spec, theme, seed)
        self.rows = []
        items = spec["items"][:5]
        rh = min(170, (CONTENT_Y[1] - CONTENT_Y[0] - 40) // len(items) - 20)
        for it in items:
            row = rounded((940, rh), 30, (255, 255, 255, 235))
            x = 30
            if it.get("emoji"):
                e = emoji(it["emoji"], int(rh * 0.62))
                row.paste(e, (x, (rh - e.height) // 2), e)
                x += e.width + 30
            txt = rich_text(it.get("text", ""), int(rh * 0.36), 940 - x - 30, fill=(30, 30, 30), hi=(220, 30, 60))
            row.paste(txt, (x, (rh - txt.height) // 2), txt)
            self.rows.append(row)
        self.rh = rh
        self.stagger = spec.get("stagger")

    def frame(self, t, dur):
        im = self.background(t, dur)
        n = len(self.rows)
        gap = 24
        total = n * self.rh + (n - 1) * gap
        y0 = CONTENT_CY - total / 2 + self.rh / 2
        st = self.stagger or min(0.8, dur * 0.6 / max(1, n))
        for i, row in enumerate(self.rows):
            p = ease_out((t - i * st) / 0.35)
            if p <= 0:
                continue
            paste_center(im, row, W / 2 + (1 - p) * 300, y0 + i * (self.rh + gap), alpha=p)
        return im


class PieLayer(Layer):
    def __init__(self, spec, theme, seed=0):
        super().__init__(spec, theme, seed)
        self.segs = spec["segments"]
        tot = sum(s["value"] for s in self.segs)
        self.fracs = [s["value"] / tot for s in self.segs]
        self.center_text = spec.get("center", "")
        texts = [rich_text(s.get("label", ""), 52, 5000, stroke=6) for s in self.segs]
        # side by side when labels are short, otherwise one per line
        self.vertical = len(texts) > 2 or any(t.width > 370 for t in texts)
        self.R = 230 if self.vertical else 270
        rw = 900 if self.vertical else 440
        self.legend = []
        for s, txt in zip(self.segs, texts):
            row = Image.new("RGBA", (rw, 80), (0, 0, 0, 0))
            d = ImageDraw.Draw(row)
            d.rounded_rectangle((0, 15, 50, 65), 10, fill=hex2rgb(s.get("color", "#ffffff")))
            txt = scaled(txt, min(1.0, (rw - 70) / txt.width))
            row.paste(txt, (65, (80 - txt.height) // 2), txt)
            if self.vertical:
                row = row.crop((0, 0, 65 + txt.width, 80))
            self.legend.append(row)

    def frame(self, t, dur):
        im = self.background(t, dur)
        R = self.R
        n = len(self.legend)
        cy = CONTENT_CY - (40 + 40 * n if self.vertical else 70)
        sweep = ease_out(t / 1.1) * 360
        layer = Image.new("RGBA", (R * 2 + 20, R * 2 + 20), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        a0 = -90
        for s, f in zip(self.segs, self.fracs):
            a1 = a0 + f * 360
            end = min(a1, -90 + sweep)
            if end > a0:
                d.pieslice((10, 10, 10 + 2 * R, 10 + 2 * R), a0, end, fill=hex2rgb(s.get("color", "#fff")))
            a0 = a1
        hole = int(R * 0.55)
        d.ellipse((10 + R - hole, 10 + R - hole, 10 + R + hole, 10 + R + hole), fill=(0, 0, 0, 0))
        paste_center(im, layer, W / 2, cy)
        if not hasattr(self, "center"):
            hole_d = int(R * 0.55 * 2 * 0.85)
            self.center = fit_text(self.center_text, 120, hole_d, hole_d, stroke=8, fill=self.accent)
        paste_center(im, self.center, W / 2, cy, alpha=ease_out((t - 0.9) / 0.3))
        for i, row in enumerate(self.legend):
            a = ease_out((t - 0.4 - i * 0.2) / 0.3)
            if self.vertical:
                paste_center(im, row, W / 2, cy + R + 80 + i * 85, alpha=a)
            else:
                paste_center(im, row, W / 2 + (i - (n - 1) / 2) * 460, cy + R + 90, alpha=a)
        return im


def backdrop(path, tint):
    """Blurred, darkened, tinted version of a photo for diagram scenes (taller for a slow pan)."""
    img = Image.open(path).convert("RGB")
    small = cover(img, W // 6, int(H * 1.12) // 6).filter(ImageFilter.GaussianBlur(3))
    big = small.resize((W, int(H * 1.12)), Image.BICUBIC)
    shade = Image.new("RGB", big.size, tint)
    return Image.blend(big, shade, 0.62)


class QuizLayer(Layer):
    """3-choice quiz. reveal=False: question + choices (+ 3-2-1 countdown at the end).
    reveal=True: the correct choice lights up, the others fade."""
    LETTERS = "ABC"
    COLORS = [(37, 99, 235), (217, 119, 6), (22, 163, 74)]

    def __init__(self, spec, theme, seed=0):
        super().__init__(spec, theme, seed)
        self.reveal = spec.get("reveal", False)
        self.countdown = spec.get("countdown", False)
        self.answer = max(1, min(3, int(spec.get("answer", 1)))) - 1
        q = fit_text(spec.get("question", ""), 72, 940, 190, min_size=48, fill=(255, 255, 255), stroke=8)
        self.q = q
        self.cards = []
        for i, ch in enumerate(spec.get("choices", [])[:3]):
            card = rounded((900, 132), 34, (255, 255, 255, 240))
            d = ImageDraw.Draw(card)
            d.ellipse((22, 22, 110, 110), fill=self.COLORS[i])
            letter = rich_text(self.LETTERS[i], 60, fill=(255, 255, 255))
            card.paste(letter, (66 - letter.width // 2, 66 - letter.height // 2), letter)
            txt = fit_text(ch, 60, 740, 110, min_size=40, fill=(25, 25, 25))
            card.paste(txt, (140, (132 - txt.height) // 2), txt)
            self.cards.append(card)
        self.ok_badge = emoji("⭕", 120)
        self.nums = {n: rich_text(str(n), 150, stroke=12, fill=self.accent, shadow=True) for n in (1, 2, 3)}
        ring = Image.new("RGBA", (240, 240), (0, 0, 0, 0))
        ImageDraw.Draw(ring).ellipse((6, 6, 234, 234), fill=(0, 0, 0, 150), outline=self.accent + (255,), width=10)
        self.ring = ring

    def frame(self, t, dur):
        im = self.background(t, dur)
        place(im, self.q, W / 2, CONTENT_Y[0] + 70, max(0.01, ease_back(t / 0.4)))
        y0 = CONTENT_Y[0] + 230
        for i, card in enumerate(self.cards):
            cy = y0 + i * 160
            if self.reveal:
                if i == self.answer:
                    k = 1 + 0.06 * ease_back(t / 0.35)
                    hl = rounded((card.width + 20, card.height + 20), 42, (34, 197, 94, 255))
                    place(im, hl, W / 2, cy, k)
                    place(im, card, W / 2, cy, k)
                    place(im, self.ok_badge, W / 2 + 400 * k, cy - 10, ease_back((t - 0.15) / 0.35))
                else:
                    place(im, card, W / 2, cy, 0.96, alpha=0.4)
            else:
                p = ease_out((t - 0.2 - i * 0.25) / 0.3)
                if p > 0:
                    place(im, card, W / 2 + (1 - p) * 260, cy, 1.0, alpha=p)
        if self.countdown and not self.reveal:
            left = dur - t
            if 0 < left <= 3.0:
                n = int(math.ceil(left))
                frac = n - left  # 0 -> 1 within each second
                k = 1.25 - 0.25 * ease_out(frac / 0.25)
                place(im, self.ring, W / 2, CONTENT_Y[1] - 10, 1.0)
                place(im, self.nums[n], W / 2, CONTENT_Y[1] - 10, k)
        return im


def cover(img, w, h):
    k = max(w / img.width, h / img.height)
    img = img.resize((math.ceil(img.width * k), math.ceil(img.height * k)), Image.LANCZOS)
    x, y = (img.width - w) // 2, (img.height - h) // 2
    return img.crop((x, y, x + w, y + h))


def _shade():
    a = np.zeros((H, 1), np.float32)
    y = np.arange(H)
    a[:, 0] = np.clip((520 - y) / 520, 0, 1) * 150 + np.clip((y - 1050) / 450, 0, 1) * 170
    alpha = Image.fromarray(np.repeat(np.clip(a, 0, 255).astype(np.uint8), W, axis=1), "L")
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    sh.putalpha(alpha)
    return sh


SHADE = None


def shade():
    global SHADE
    if SHADE is None:
        SHADE = _shade()
    return SHADE


class PhotoLayer(Layer):
    """Portrait image -> full-bleed Ken Burns. Landscape -> card over blurred copy."""

    def __init__(self, spec, theme, seed=0, path=None):
        super().__init__(spec, theme, seed)
        img = Image.open(path).convert("RGB")
        self.full = img.width / img.height < 0.8
        rng = random.Random(seed)
        self.dir = rng.choice([(-1, -1), (1, -1), (-1, 1), (1, 1), (0, 0)])
        self.punch = False  # hook scene: start zoomed in and pull back fast
        if self.full:
            self.big = cover(img, int(W * 1.15), int(H * 1.15))
        else:
            bg = cover(img, W // 4, H // 4).filter(ImageFilter.GaussianBlur(6)).resize((W, H))
            self.bg = Image.blend(bg, Image.new("RGB", (W, H), (0, 0, 0)), 0.35)
            cw = 1000
            card = cover(img, cw, min(int(cw * img.height / img.width), 760))
            mask = rounded(card.size, 36, (255, 255, 255, 255)).getchannel("A")
            card.putalpha(mask)
            self.card = card

    def frame(self, t, dur):
        p = t / max(dur, 0.1)
        if self.full:
            z = 1.15 - 0.12 * p
            if self.punch:
                z /= 1 + 0.28 * (1 - ease_out(t / 1.4))
            cw, ch = self.big.width * z / 1.15, self.big.height * z / 1.15
            mx, my = (self.big.width - cw) / 2, (self.big.height - ch) / 2
            x = mx + self.dir[0] * mx * (p - 0.5)
            y = my + self.dir[1] * my * (p - 0.5)
            im = view(self.big, x, y, cw, ch)
            im.paste(shade(), (0, 0), shade())
            return im
        im = self.bg.copy()
        k = 1.0 + 0.05 * p
        if self.punch:
            k *= 1 + 0.25 * (1 - ease_out(t / 1.2))
        kk = k * ease_back(t / 0.45)
        if t < 0.45:
            paste_center(im, scaled(self.card, max(0.01, kk)), W / 2, CONTENT_CY)
        else:
            place(im, self.card, W / 2, CONTENT_CY, kk)
        return im


class VideoLayer(Layer):
    def __init__(self, spec, theme, seed=0, path=None, fps=30):
        super().__init__(spec, theme, seed)
        vf = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={fps}"
        self.proc = subprocess.Popen(
            ["ffmpeg", "-v", "error", "-stream_loop", "-1", "-i", str(path), "-an", "-vf", vf,
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
        self.last = None

    def frame(self, t, dur):
        buf = self.proc.stdout.read(W * H * 3)
        if len(buf) == W * H * 3:
            self.last = Image.frombytes("RGB", (W, H), buf)
            self.last.paste(shade(), (0, 0), shade())
        return self.last or self.background(t, dur)

    def close(self):
        self.proc.kill()


LAYERS = {"text": TextLayer, "illustration": IllustrationLayer, "number": NumberLayer,
          "compare": CompareLayer, "list": ListLayer, "pie": PieLayer, "quiz": QuizLayer}
