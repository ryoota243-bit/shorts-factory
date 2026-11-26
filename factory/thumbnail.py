"""Thumbnail (1080x1920 PNG): striking image + big catch copy.

Background: an AI image from script["thumb_prompt"] (checked like scene images), else the first
photo used in the video, else the theme gradient. Text: script["thumb_text"] (falls back to title).
"""
import re

import numpy as np
from PIL import Image, ImageFilter

from . import assets, visuals as V


def _vignette():
    y = np.linspace(-1, 1, V.H)[:, None]
    x = np.linspace(-1, 1, V.W)[None, :]
    a = np.clip((x ** 2 * 0.6 + y ** 2 * 0.5) * 255 * 0.55, 0, 200).astype(np.uint8)
    mid = np.clip(130 - np.abs(np.linspace(-1, 1, V.H) * 220), 0, 130).astype(np.uint8)[:, None]
    alpha = np.maximum(a, np.broadcast_to(mid, a.shape))
    layer = Image.new("RGBA", (V.W, V.H), (0, 0, 0, 255))
    layer.putalpha(Image.fromarray(alpha, "L"))
    return layer


def build(script, theme, cfg, root, layers, out_png):
    """Write the thumbnail and return (PIL RGB image, credit or None)."""
    text = script.get("thumb_text") or script.get("title", "")
    plain = re.sub(r"[\[\]]", "", text)
    bg, credit = None, None
    prompt = script.get("thumb_prompt")
    if prompt:
        if script.get("style") == "kamishibai":
            v = {"type": "ai", "style_suffix": V.KAMI_STYLE,
                 "ai_prompt": prompt + ". Bold, simple composition with one clear subject, uncluttered middle area"}
        else:
            v = {"type": "ai", "ai_prompt": prompt + ". Dramatic, high contrast, bold close-up subject, "
                                                     "uncluttered middle area, vertical poster composition"}
        try:
            found = assets.resolve(v, cfg, root / "cache", root, narration=f"サムネイル: {plain}",
                                   topic=re.sub(r"[\[\]]", "", f"{script.get('title', '')} / {script.get('topic', '')}"))
            if found and found["kind"] == "image":
                bg, credit = Image.open(found["path"]).convert("RGB"), found["credit"]
        except Exception as e:
            print(f"  [thumb] AI image failed: {e}")
    if bg is None:
        src = next((getattr(l, "src_path", None) for l in layers if getattr(l, "src_path", None)), None)
        if src:
            bg = Image.open(src).convert("RGB")
    if bg is None:
        img = V.gradient_bg(theme["bg"][0], theme["bg"][1], 7).crop((0, 0, V.W, V.H))
    else:
        img = V.cover(bg, V.W, V.H)
        img = Image.blend(img, img.filter(ImageFilter.GaussianBlur(2)), 0.3)
    img = img.convert("RGBA")
    img.alpha_composite(_vignette())

    accent = V.hex2rgb(theme.get("accent", "#ffd600"))
    copy = V.fit_text(text, 210, 1000, 620, min_size=120, stroke=22, fill=(255, 255, 255), hi=accent,
                      shadow=True, spacing=1.12)
    copy = copy.rotate(-3, resample=Image.BICUBIC, expand=True)
    V.paste_center(img, copy, V.W / 2, V.H * 0.47)

    tag = V.rich_text("思わず話したくなる雑学", 54, 900, fill=(20, 20, 20))
    pill = V.rounded((tag.width + 60, tag.height + 24), 40, accent + (255,))
    pill.paste(tag, (30, 12), tag)
    V.paste_center(img, pill, V.W / 2, V.H * 0.47 - copy.height / 2 - 90)

    img = img.convert("RGB")
    img.save(out_png)
    return img, credit
