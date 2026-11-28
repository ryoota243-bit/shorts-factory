"""Card style (trivia channel): a deliberately different look from the main channel.

Main channel: dark gradient, content in the middle, karaoke subtitles at the bottom, crossfades.
Card style:   one flat bright colour card per fact that swipes in from the right, a big sticker number
              ("01"), the picture in a tilted white frame, and the whole one-line fact as large dark text
              (no subtitles). A segmented progress bar runs along the top. It opens with a title card and
              ends with "何個知ってた？" and a score guide.
"""
import math
import re

from PIL import Image, ImageDraw, ImageFilter

from . import visuals as V

W, H = V.W, V.H
PALETTE = ["#FFD23F", "#5BC0EB", "#FF8FA3", "#7BE0AD", "#C3A6FF", "#FFA45B"]
INK = (28, 26, 38)
PAPER = (255, 251, 240)
HI = (0, 0, 0)            # highlighted words: same dark ink, marked by a white highlighter underline
SWIPE = 0.28              # seconds for the card swipe between scenes

CARD_W, CARD_H, BORDER = 860, 620, 18
CARD_CY = 800
TEXT_CY, TEXT_BOX = 1395, (880, 400)


def _plain(s):
    return re.sub(r"[\[\]]", "", s or "")


def _striped(hex_):
    c = V.hex2rgb(hex_)
    im = Image.new("RGB", (W, H), c)
    d = ImageDraw.Draw(im, "RGBA")
    dark = tuple(max(0, int(v * 0.9)) for v in c) + (255,)
    for x in range(-H, W, 110):
        d.line([(x, H), (x + H, 0)], fill=dark, width=34)
    return im


def _frame(inner, angle_seed):
    """White photo frame + soft shadow around an RGB image of CARD_W x CARD_H."""
    w, h = CARD_W + 2 * BORDER, CARD_H + 2 * BORDER
    pad = 40
    out = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
    sh = V.rounded((w, h), 30, INK + (90,)).filter(ImageFilter.GaussianBlur(10))
    out.alpha_composite(sh, (pad + 14, pad + 18))
    out.alpha_composite(V.rounded((w, h), 30, (255, 255, 255, 255)), (pad, pad))
    mask = V.rounded((CARD_W, CARD_H), 18, (255, 255, 255, 255)).getchannel("A")
    out.paste(inner.convert("RGB"), (pad + BORDER, pad + BORDER), mask)
    return out


class CardStyle:
    SWIPE = SWIPE

    def __init__(self, script, layers, cfg):
        self.scenes = script["scenes"]
        self.layers = layers
        self.title = script.get("title", "")
        ch = cfg.get("channel", {})
        self.channel = ch.get("name", "")
        self.follow = (cfg.get("outro") or {}).get("sub", "")
        self.facts = [i for i, sc in enumerate(self.scenes) if sc.get("badge")]
        self.n = len(self.facts)
        # "補足" cards (explaining an unfamiliar word) belong to the fact just before them
        self.notes = {}
        for i, sc in enumerate(self.scenes):
            if sc.get("card") == "note":
                prev = [f for f in self.facts if f < i]
                if prev:
                    self.notes[i] = prev[-1]
        self.bgs, self.text_cache = {}, {}
        self.header = self._header()
        self.static_cards = {}
        for i in self.facts + list(self.notes):
            l = layers[i]
            if getattr(l, "src_path", None):
                self.static_cards[i] = _frame(V.cover(Image.open(l.src_path).convert("RGB"), CARD_W, CARD_H), i)
        self.num_imgs = {k: self._sticker(k) for k in range(1, self.n + 1)}
        self.note_sticker = self._note_sticker()
        self.mystery = self._mystery()
        self.outro_img = self._outro(cfg)
        # the hook (thumb_text) is the big line of the title call and the thumbnail; the title goes in a pill
        self.hook = script.get("thumb_text") or self.title
        self.intro_img = V.fit_text(self.hook, 150, 980, 520, min_size=90, fill=(255, 255, 255), hi=(255, 214, 0),
                                    stroke=18, stroke_fill=INK, spacing=1.15)
        tag = V.rich_text(_plain(self.title), 54, 900, fill=(255, 255, 255), hi=(255, 255, 255))
        self.title_pill = V.rounded((tag.width + 64, tag.height + 24), 40, INK + (255,))
        self.title_pill.paste(tag, (32, 12), tag)
        self.first_card = self.static_cards.get(self.facts[0]) if self.facts else None

    # ---------- parts ----------
    def _header(self):
        txt = V.rich_text(_plain(self.title), 50, 860, fill=INK, hi=INK)
        pill = V.rounded((txt.width + 56, txt.height + 18), (txt.height + 18) // 2, (255, 255, 255, 235))
        pill.paste(txt, (28, 9), txt)
        return pill

    def _sticker(self, k):
        num = V.rich_text(f"{k:02d}", 190, fill=(255, 255, 255), hi=(255, 255, 255), stroke=16, stroke_fill=INK)
        tot = V.rich_text(f"/{self.n:02d}", 70, fill=INK, hi=INK, stroke=0)
        im = Image.new("RGBA", (num.width + tot.width - 10, num.height), (0, 0, 0, 0))
        im.alpha_composite(num, (0, 0))
        im.alpha_composite(tot, (num.width - 20, num.height - tot.height - 60))
        return im

    def _mystery(self):
        """The card shown while the answer is held back."""
        im = Image.new("RGB", (CARD_W, CARD_H), PAPER)
        d = ImageDraw.Draw(im)
        for x in range(-CARD_H, CARD_W, 70):  # faint diagonal hatching
            d.line([(x, CARD_H), (x + CARD_H, 0)], fill=(242, 234, 214), width=24)
        q = V.rich_text("？", 330, 400, fill=INK, hi=INK)
        im.paste(q, ((CARD_W - q.width) // 2, (CARD_H - q.height) // 2 - 20), q)
        return _frame(im, 0)

    def _text_size(self, i):
        """Font size the full line ends up with, so the set-up line is drawn at the same size (no jump)."""
        key = ("size", i)
        if key not in self.text_cache:
            sz = 92
            while True:
                im = V.rich_text(self.scenes[i].get("narration", ""), sz, TEXT_BOX[0] - 20, fill=INK, hi=HI, spacing=1.3)
                if (im.width <= TEXT_BOX[0] and im.height <= TEXT_BOX[1]) or sz <= 58:
                    break
                sz = int(sz * 0.9)
            self.text_cache[key] = sz
        return self.text_cache[key]

    def _tease_text(self, i, dots):
        key = ("tease", i, dots)
        if key not in self.text_cache:
            s = self.scenes[i]["tease"] + "・" * dots
            im = V.rich_text(s, self._text_size(i), TEXT_BOX[0] - 20, fill=INK, hi=INK, stroke=0, spacing=1.3)
            self.text_cache[key] = V.scaled(im, min(1.0, TEXT_BOX[0] / im.width, TEXT_BOX[1] / im.height))
        return self.text_cache[key]

    def _note_sticker(self):
        txt = V.rich_text("補足", 92, 400, fill=INK, hi=INK)
        im = V.rounded((txt.width + 70, txt.height + 26), 34, (255, 255, 255, 255))
        edge = V.rounded((im.width + 16, im.height + 16), 42, INK + (255,))
        edge.alpha_composite(im, (8, 8))
        edge.alpha_composite(txt, (8 + 35, 8 + 13))
        return edge

    def _bg(self, i):
        if i not in self.bgs:
            k = self.facts.index(i) + 1 if i in self.facts else 0
            self.bgs[i] = _striped(PALETTE[k % len(PALETTE)])
        return self.bgs[i]

    def _text(self, i, m):
        key = (i, round(m * 10))
        if key not in self.text_cache:
            s = self.scenes[i].get("narration", "")
            self.text_cache[key] = V.fit_text(s, 92, *TEXT_BOX, min_size=58, fill=INK, hi=HI, stroke=0,
                                              marker=key[1] / 10, marker_color=(255, 255, 255), spacing=1.3)
        return self.text_cache[key]

    def _progress(self, im, done, cur_p):
        n = max(1, self.n)
        gap, x0, x1, y, h = 12, 70, W - 70, 96, 14
        w = (x1 - x0 - gap * (n - 1)) / n
        d = ImageDraw.Draw(im, "RGBA")
        for k in range(n):
            a = x0 + k * (w + gap)
            d.rounded_rectangle((a, y, a + w, y + h), h // 2, fill=(255, 255, 255, 150))
            f = 1.0 if k < done else (cur_p if k == done else 0.0)
            if f > 0:
                d.rounded_rectangle((a, y, a + max(h, w * f), y + h), h // 2, fill=INK + (255,))

    def _inner(self, i, t, dur):
        """Picture for a fact without a photo: diagrams keep their own look inside the frame."""
        l = self.layers[i]
        if isinstance(l, (V.IllustrationLayer, V.TextLayer)):
            im = Image.new("RGB", (CARD_W, CARD_H), PAPER)
            spec = l.spec
            e = V.emoji(spec.get("emoji") or "💡", 330)
            V.place(im, e, CARD_W / 2, CARD_H / 2 - 50 + math.sin(t * 2.5) * 10, max(0.01, V.ease_back(t / 0.45)))
            if spec.get("text"):
                cap = V.fit_text(spec["text"].replace("\n", ""), 70, CARD_W - 80, 100, min_size=44, fill=INK, hi=INK)
                V.paste_center(im, cap, CARD_W / 2, CARD_H - 90)
            return im
        full = l.frame(t, dur)
        return V.cover(full.crop((0, V.CONTENT_Y[0] - 20, W, V.CONTENT_Y[1] + 20)), CARD_W, CARD_H)

    def _outro(self, cfg):
        n = max(1, self.n)
        mid = math.ceil(n / 2)
        labels = (cfg.get("card") or {}).get("ranks") or [["👑", "トリビア王"], ["🎓", "物知り"], ["🌱", "伸びしろ大"]]
        rows = [(f"{n}個", *labels[0]), (f"{mid}〜{n - 1}個" if n - 1 > mid else f"{mid}個", *labels[1]),
                (f"0〜{mid - 1}個" if mid > 1 else "0個", *labels[2])]
        out = []
        for cnt, emo, name in rows:
            row = V.rounded((820, 150), 40, (255, 255, 255, 245))
            c = V.rich_text(cnt, 66, 300, fill=INK, hi=INK)
            row.paste(c, (50, (150 - c.height) // 2), c)
            e = V.emoji(emo, 90)
            row.paste(e, (380, 30), e)
            nm = V.fit_text(name, 70, 320, 120, min_size=48, fill=INK, hi=INK)
            row.paste(nm, (490, (150 - nm.height) // 2), nm)
            out.append(row)
        return out

    # ---------- frames ----------
    def frame(self, i, t, dur):
        sc = self.scenes[i]
        if sc.get("card") == "intro":
            return self._intro_frame(i, t, dur)
        if sc.get("no_subtitle") and i == len(self.scenes) - 1:
            return self._outro_frame(i, t, dur)
        if i in self.notes:  # same colour as its fact, "補足" sticker instead of the number
            f = self.notes[i]
            k = self.facts.index(f)
            return self._card_frame(i, t, dur, k, self._bg(f), 1.0, self.note_sticker, 120 + self.note_sticker.width / 2)
        if i not in self.facts:  # anything else: plain card with the narration
            im = self._bg(i).copy()
            V.paste_center(im, self._text(i, 1.0), W / 2, H / 2)
            return im
        k = self.facts.index(i)
        st = self.num_imgs[k + 1]
        return self._card_frame(i, t, dur, k, self._bg(i), min(1.0, t / max(dur, 0.1)), st, 95 + st.width / 2 - 30)

    def _card_frame(self, i, t, dur, k, bg, prog, st, st_x):
        im = bg.copy()
        self._progress(im, k, prog)
        V.paste_center(im, self.header, W / 2, 190)
        ang = -2.5 if k % 2 == 0 else 2.5
        sc = self.scenes[i]
        rv = sc.get("reveal_at") if sc.get("tease") else None  # teased fact: answer (picture + text) held back
        if rv is not None and t < rv:
            card = self.mystery
            if t > sc.get("tease_end", 0):  # waiting: the "?" card wobbles
                ang += math.sin((t - sc["tease_end"]) * 9) * 2.2
        else:
            t_in = t - (rv or 0)
            card = self.static_cards.get(i) or _frame(self._inner(i, t_in, dur - (rv or 0)), i)
        if rv is not None and abs(t - rv) < 0.16:  # flip: squeeze the "?" card, open the picture
            sx = max(0.03, abs(t - rv) / 0.16)
            card = card.resize((max(2, int(card.width * sx)), card.height), Image.BILINEAR)
        kk = 0.86 + 0.14 * V.ease_back(t / 0.4)
        if rv is not None and t >= rv:
            kk *= 1 + 0.05 * (1 - V.ease_out((t - rv) / 0.35))
        V.place(im, card, W / 2, CARD_CY + (1 - V.ease_out(t / 0.35)) * 60, kk * (1 + 0.02 * t / max(dur, 0.1)), ang)
        V.place(im, st, st_x, CARD_CY - CARD_H / 2 - 100, max(0.01, V.ease_back((t - 0.08) / 0.35)),
                -6 if k % 2 == 0 else 4)
        p = V.ease_out((t - 0.12) / 0.3)
        if p > 0:
            if rv is not None and t < rv:  # only the set-up line, then dots while we wait
                dots = 0 if t < sc.get("tease_end", 0) else min(3, 1 + int((t - sc["tease_end"]) / 0.5))
                tz = self._tease_text(i, dots)
                top = TEXT_CY - self._text(i, 0.0).height / 2  # same top as the full line: the answer just adds on
                V.place(im, tz, W / 2, top + tz.height / 2 + (1 - p) * 50, 1.0, alpha=p)
            elif rv is not None:  # the answer: full line pops, highlighter sweeps under the key words
                kt = 1 + 0.06 * (1 - V.ease_out((t - rv) / 0.3))
                V.place(im, self._text(i, V.ease_out((t - rv - 0.1) / 0.45)), W / 2, TEXT_CY, kt)
            else:
                txt = self._text(i, V.ease_out((t - 0.5) / 0.45))
                V.place(im, txt, W / 2, TEXT_CY + (1 - p) * 50, 1.0, alpha=p)
        return im

    def _cover(self, im, t):
        """Title call / thumbnail layout: first picture on top, the hook big, the title in a pill below."""
        has_card = self.first_card is not None
        hook_y = 1180 if has_card else H * 0.42
        if has_card:
            V.place(im, self.first_card, W / 2, 640, 0.8 * max(0.01, V.ease_back(t / 0.4)), -4)
        V.place(im, self.intro_img, W / 2, hook_y, max(0.01, V.ease_back((t - 0.1) / 0.45)),
                3 * V.ease_out(t / 0.6))
        V.place(im, self.title_pill, W / 2, hook_y + self.intro_img.height / 2 + 80, 1.0,
                alpha=V.ease_out((t - 0.35) / 0.3))

    def _intro_frame(self, i, t, dur):
        im = self._bg(i).copy()
        self._progress(im, 0, 0.0)
        self._cover(im, t)
        return im

    def _outro_frame(self, i, t, dur):
        im = Image.new("RGB", (W, H), INK)
        self._progress(im, self.n, 0.0)
        q = V.fit_text(self.scenes[i].get("narration", "") or "何個知ってた？", 120, 960, 260, min_size=70,
                       fill=(255, 255, 255), hi=(255, 214, 0))
        V.place(im, q, W / 2, 420, max(0.01, V.ease_back(t / 0.4)))
        for r, row in enumerate(self.outro_img):
            p = V.ease_out((t - 0.25 - r * 0.15) / 0.3)
            if p > 0:
                V.place(im, row, W / 2 + (1 - p) * 300, 700 + r * 185, 1.0, alpha=p)
        if self.follow:
            f = V.rich_text(self.follow, 56, 900, fill=(255, 214, 0), hi=(255, 214, 0))
            V.place(im, f, W / 2, 1330, 1.0, alpha=V.ease_out((t - 0.8) / 0.3))
        return im

    @staticmethod
    def swipe(prev, cur, p):
        """New card slides in from the right while the old one slides out to the left."""
        e = V.ease_out(p)
        out = Image.new("RGB", (W, H), INK)
        out.paste(prev, (int(-e * W * 0.6), 0))
        x = int((1 - e) * W)
        out.paste(cur.crop((0, 0, W - x, H)), (x, 0))
        return out

    def thumbnail(self, out_png, first_frame_src=None):
        """Same layout as the title call, settled. Shown in the last frames of the video (pick it as the cover)."""
        im = _striped(PALETTE[0])
        self._cover(im, 5.0)
        im.save(out_png)
        return im
