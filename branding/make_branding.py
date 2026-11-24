#!/usr/bin/env python3
"""Channel icon (800x800) and banner (2560x1440) in the same style as the videos.

  python branding/make_branding.py "思わず話したくなる雑学"
"""
import math
import random
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from factory import visuals as V  # noqa: E402

OUT = Path(__file__).resolve().parent
NAVY, INDIGO = (11, 29, 58), (49, 27, 110)
YELLOW, CYAN, WHITE = (255, 214, 0), (56, 189, 248), (255, 255, 255)


def gradient(w, h, c1, c2, radial=False):
    if radial:
        y, x = np.ogrid[:h, :w]
        d = np.sqrt(((x - w / 2) / (w / 2)) ** 2 + ((y - h / 2) / (h / 2)) ** 2)
        t = np.clip(d / 1.2, 0, 1)[..., None]
    else:
        t = np.clip(np.linspace(0, 1, w)[None, :, None] * 0.6 + np.linspace(0, 1, h)[:, None, None] * 0.4, 0, 1)
    a, b = np.array(c1, np.float32), np.array(c2, np.float32)
    return Image.fromarray(np.broadcast_to(a * (1 - t) + b * t, (h, w, 3)).astype(np.uint8), "RGB")


def bubble(size, fill, tail=True):
    """Speech bubble (rounded rect + tail) as RGBA."""
    w, h = size
    im = Image.new("RGBA", (w, int(h * 1.25)), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, w - 1, h - 1), int(h * 0.32), fill=fill)
    if tail:
        d.polygon([(w * 0.22, h - 2), (w * 0.42, h - 2), (w * 0.16, h * 1.22)], fill=fill)
    return im


def glow(im, radius, color):
    a = im.getchannel("A").filter(ImageFilter.GaussianBlur(radius))
    g = Image.new("RGBA", im.size, color + (0,))
    g.putalpha(a.point(lambda v: int(v * 0.8)))
    return g


def icon(path):
    S = 800
    img = gradient(S, S, (35, 52, 110), NAVY, radial=True).convert("RGBA")
    # soft light rays
    rays = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(rays)
    for k in range(12):
        a = k * math.pi / 6
        d.polygon([(S / 2, S / 2), (S / 2 + 600 * math.cos(a - 0.12), S / 2 + 600 * math.sin(a - 0.12)),
                   (S / 2 + 600 * math.cos(a + 0.12), S / 2 + 600 * math.sin(a + 0.12))], fill=(255, 255, 255, 14))
    img.alpha_composite(rays)
    b = bubble((560, 380), YELLOW + (255,))
    pad = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    pad.paste(b, (120, 190), b)
    img.alpha_composite(glow(pad, 30, YELLOW))
    img.alpha_composite(pad)
    word = V.fit_text("雑学", 260, 470, 300, fill=NAVY, weight="Black")
    img.paste(word, (int(400 - word.width / 2), int(380 - word.height / 2)), word)
    # "!?" badge
    badge = Image.new("RGBA", (220, 220), (0, 0, 0, 0))
    ImageDraw.Draw(badge).ellipse((0, 0, 219, 219), fill=CYAN + (255,), outline=WHITE + (255,), width=10)
    q = V.fit_text("!?", 150, 160, 160, fill=WHITE, stroke=8, stroke_fill=NAVY)
    badge.paste(q, (110 - q.width // 2, 110 - q.height // 2), q)
    badge = badge.rotate(-12, resample=Image.BICUBIC, expand=True)
    img.paste(badge, (520, 110), badge)
    bulb = V.emoji("💡", 150)
    img.paste(bulb, (70, 520), bulb)
    img = img.convert("RGB")
    img.save(path)
    # preview: how it looks in YouTube's circle crop
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, S - 1, S - 1), fill=255)
    prev = Image.new("RGB", (S, S), (240, 240, 240))
    prev.paste(img, (0, 0), mask)
    return prev


def banner(path, name):
    W, H = 2560, 1440
    img = gradient(W, H, NAVY, INDIGO).convert("RGBA")
    rng = random.Random(3)
    deco = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(deco)
    for _ in range(26):  # soft bokeh
        r = rng.randint(40, 180)
        x, y = rng.randint(0, W), rng.randint(0, H)
        d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 255, rng.randint(8, 22)))
    deco = deco.filter(ImageFilter.GaussianBlur(14))
    img.alpha_composite(deco)
    # floating topic emojis outside / around the safe area
    safe = (507, 508, 2053, 931)  # 1546 x 423, visible on every device
    spots = [("🐙", 330, 360), ("🪐", 2230, 330), ("🧠", 300, 1080), ("🍣", 2260, 1100),
             ("🦥", 640, 1180), ("☁️", 1900, 1190), ("🌍", 1750, 250), ("🦠", 820, 250)]
    for ch, x, y in spots:
        e = V.emoji(ch, 150).rotate(rng.uniform(-15, 15), resample=Image.BICUBIC, expand=True)
        e.putalpha(e.getchannel("A").point(lambda v: int(v * 0.85)))
        img.paste(e, (x - e.width // 2, y - e.height // 2), e)
    # question / exclamation marks
    for ch, x, y, s, c in [("?", 470, 640, 200, CYAN), ("!", 2110, 600, 220, YELLOW),
                           ("?", 2000, 900, 130, YELLOW), ("!", 560, 900, 120, CYAN)]:
        t = V.rich_text(ch, s, fill=c, stroke=0)
        img.paste(t, (x - t.width // 2, y - t.height // 2), t)

    # safe-area content
    cx, cy = (safe[0] + safe[2]) // 2, (safe[1] + safe[3]) // 2
    title = V.fit_text(name, 185, 1500, 230, fill=WHITE, hi=YELLOW, stroke=14, stroke_fill=NAVY, shadow=True)
    img.paste(title, (cx - title.width // 2, cy - 115 - title.height // 2), title)
    sub = V.fit_text("毎日1本・約40秒で「へぇ！」が増えるショート", 60, 1300, 84, fill=WHITE, weight="Bold")
    img.paste(sub, (cx - sub.width // 2, cy + 45 - sub.height // 2), sub)
    tags = ["動物", "宇宙", "人体", "歴史", "食べ物", "身近なモノ"]
    pills = []
    for tg in tags:
        t = V.rich_text(tg, 46, fill=NAVY)
        p = V.rounded((t.width + 56, t.height + 18), 36, YELLOW + (255,))
        p.paste(t, (28, 9), t)
        pills.append(p)
    total = sum(p.width for p in pills) + 24 * (len(pills) - 1)
    x = cx - total // 2
    for p in pills:
        img.paste(p, (x, cy + 150 - p.height // 2), p)
        x += p.width + 24
    img = img.convert("RGB")
    img.save(path)
    # preview with the safe areas outlined
    prev = img.copy()
    d = ImageDraw.Draw(prev)
    d.rectangle(safe, outline=(255, 60, 60), width=6)              # all devices
    d.rectangle((0, 509, 2560, 931), outline=(60, 255, 120), width=4)  # desktop max width
    return prev


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "思わず話したくなる[雑学]"
    icon_prev = icon(OUT / "icon_800.png")
    icon_prev.save(OUT / "preview_icon_circle.png")
    banner_prev = banner(OUT / "banner_2560x1440.png", name)
    banner_prev.resize((1280, 720)).save(OUT / "preview_banner_safearea.png")
    print("saved:", *sorted(p.name for p in OUT.glob("*.png")))
