#!/usr/bin/env python3
"""Architecture and user-flow diagrams for the YouTube API audit form (1920x1080 PNG)."""
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent
W, H = 1920, 1080
FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-{}.ttc"
NAVY, BLUE, YELLOW, RED = (11, 29, 58), (37, 99, 235), (255, 214, 0), (220, 38, 38)
GRAY, LIGHT, GREEN = (75, 85, 99), (241, 245, 249), (22, 163, 74)


def f(size, w="Bold"):
    return ImageFont.truetype(FONT.format(w), size, index=0)


def box(d, xy, title, lines=(), fill=LIGHT, edge=NAVY, tcol=NAVY):
    x0, y0, x1, y1 = xy
    d.rounded_rectangle(xy, 22, fill=fill, outline=edge, width=4)
    d.text(((x0 + x1) / 2, y0 + 20), title, font=f(30, "Black"), fill=tcol, anchor="ma")
    y = y0 + 70
    for ln in lines:
        d.text((x0 + 24, y), ln, font=f(23, "Regular"), fill=(30, 41, 59))
        y += 34


def arrow(d, p0, p1, label="", color=GRAY, lpos=0.5, loff=(0, -34)):
    d.line([p0, p1], fill=color, width=6)
    ang = math.atan2(p1[1] - p0[1], p1[0] - p0[0])
    a, L = 0.45, 26
    d.polygon([p1, (p1[0] - L * math.cos(ang - a), p1[1] - L * math.sin(ang - a)),
               (p1[0] - L * math.cos(ang + a), p1[1] - L * math.sin(ang + a))], fill=color)
    if label:
        mx = p0[0] + (p1[0] - p0[0]) * lpos + loff[0]
        my = p0[1] + (p1[1] - p0[1]) * lpos + loff[1]
        d.text((mx, my), label, font=f(22, "Bold"), fill=color, anchor="mm")


def header(d, title, sub):
    d.rectangle((0, 0, W, 110), fill=NAVY)
    d.text((48, 26), title, font=f(44, "Black"), fill=(255, 255, 255))
    d.text((W - 48, 44), sub, font=f(24, "Regular"), fill=(203, 213, 225), anchor="ra")


def architecture():
    im = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(im)
    header(d, "shorts-factory  —  Architecture", "API client: shorts-factory  |  Used only by the channel owner")

    # private GitHub environment
    d.rounded_rectangle((40, 150, 1110, 1000), 28, outline=NAVY, width=3, fill=(248, 250, 252))
    d.text((70, 165), "Channel owner's private GitHub repository  (GitHub Actions, scheduled daily job)",
           font=f(26, "Bold"), fill=NAVY)
    box(d, (80, 230, 520, 470), "1. Script", ["Gemini API writes a trivia script", "Claude API fact-checks it with", "web sources (no YouTube data)"])
    box(d, (600, 230, 1070, 470), "2. Media", ["Narration: VOICEVOX (local engine)", "Images: Pixabay / Gemini", "Subtitles & diagrams: generated"])
    box(d, (80, 560, 520, 800), "3. Render", ["ffmpeg builds the MP4 (1080x1920)", "+ thumbnail PNG", "+ title / description / tags"])
    box(d, (600, 560, 1070, 800), "4. Uploader", ["factory/youtube.py", "OAuth 2.0 refresh token →", "access token (scope: youtube.upload)"],
        fill=(239, 246, 255), edge=BLUE, tcol=BLUE)
    box(d, (80, 850, 1070, 985), "GitHub Secrets (encrypted)", ["OAuth client ID / secret and refresh token  —  never stored in code"],
        fill=(254, 252, 232), edge=(161, 98, 7), tcol=(161, 98, 7))
    arrow(d, (520, 350), (600, 350))
    arrow(d, (835, 470), (300, 560), "", GRAY)
    arrow(d, (520, 680), (600, 680))
    arrow(d, (835, 850), (835, 800), "", (161, 98, 7))

    # Google side
    box(d, (1290, 180, 1880, 380), "Google OAuth 2.0", ["oauth2.googleapis.com/token", "Refresh token → short-lived", "access token"], fill=(240, 253, 244), edge=GREEN, tcol=GREEN)
    box(d, (1290, 470, 1880, 710), "YouTube Data API v3", ["videos.insert  (resumable upload,", "  snippet + status, privacy: private)", "thumbnails.set  (custom thumbnail)"],
        fill=(254, 242, 242), edge=RED, tcol=RED)
    box(d, (1290, 790, 1880, 1000), "Owner's own YouTube channel", ["Video arrives as private", "Owner reviews in YouTube Studio", "and publishes"], fill=LIGHT)
    arrow(d, (1070, 620), (1290, 300), "", GREEN)
    arrow(d, (1070, 720), (1290, 600), "", RED)
    d.text((1185, 395), "token", font=f(22, "Bold"), fill=GREEN, anchor="mm")
    d.text((1185, 422), "request", font=f(22, "Bold"), fill=GREEN, anchor="mm")
    d.text((1190, 700), "upload", font=f(22, "Bold"), fill=RED, anchor="mm")
    arrow(d, (1585, 710), (1585, 790))
    d.text((1290, 1020), "No data of other users or channels is read, collected or stored.", font=f(20, "Bold"), fill=GRAY)
    im.save(OUT / "architecture-diagram.png")


def user_flow():
    im = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(im)
    header(d, "shorts-factory  —  User Flow", "Single user: the channel owner")
    steps = [
        ("1", "Authorize once", ["Owner signs in with", "Google and grants", "youtube.upload to", "shorts-factory"], GREEN),
        ("2", "Store credentials", ["Refresh token is kept", "only in encrypted", "GitHub Secrets of the", "owner's private repo"], (161, 98, 7)),
        ("3", "Daily job runs", ["05:17 JST: script,", "fact-check, narration,", "render → MP4,", "thumbnail, metadata"], NAVY),
        ("4", "Upload", ["Access token from the", "refresh token, then", "videos.insert (private)", "and thumbnails.set"], RED),
        ("5", "Review & publish", ["Owner checks the", "video in YouTube", "Studio and makes it", "public or schedules it"], BLUE),
    ]
    bw, gap, top = 340, 34, 230
    x = (W - (bw * 5 + gap * 4)) // 2
    for i, (n, t, lines, col) in enumerate(steps):
        x0 = x + i * (bw + gap)
        d.rounded_rectangle((x0, top, x0 + bw, top + 430), 24, fill=LIGHT, outline=col, width=5)
        d.ellipse((x0 + bw / 2 - 42, top - 42, x0 + bw / 2 + 42, top + 42), fill=col)
        d.text((x0 + bw / 2, top), n, font=f(46, "Black"), fill=(255, 255, 255), anchor="mm")
        d.text((x0 + bw / 2, top + 75), t, font=f(32, "Black"), fill=col, anchor="ma")
        y = top + 150
        for ln in lines:
            d.text((x0 + bw / 2, y), ln, font=f(24, "Regular"), fill=(30, 41, 59), anchor="ma")
            y += 44
        if i < 4:
            arrow(d, (x0 + bw + 4, top + 215), (x0 + bw + gap - 4, top + 215), "", GRAY)
    d.text((x + 2 * (bw + gap) + bw / 2, top + 470), "steps 3–5 repeat every day", font=f(26, "Bold"), fill=GRAY, anchor="ma")
    notes = ["• Only the channel owner uses this tool; it has no public sign-up, UI or other users.",
             "• The owner can revoke access at any time: myaccount.google.com/permissions",
             "• Privacy policy: https://ryoota243-bit.github.io/privacy.html"]
    y = 820
    for nt in notes:
        d.text((90, y), nt, font=f(28, "Regular"), fill=(30, 41, 59))
        y += 52
    im.save(OUT / "user-flow-diagram.png")


if __name__ == "__main__":
    architecture()
    user_flow()
    print("saved:", *(p.name for p in sorted(OUT.glob("*.png"))))
