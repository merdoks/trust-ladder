"""Paper-collage (Vox-style) opener for the trust-ladder promo. Every frame drawn in code."""
from __future__ import annotations

import math
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1920, 1080
U = "/usr/share/fonts/opentype/urw-base35/"
FONTS = [U + "NimbusSans-Bold.otf", U + "C059-Bold.otf", U + "NimbusRoman-Bold.otf",
         U + "P052-Bold.otf", U + "NimbusSansNarrow-Bold.otf", U + "NimbusMonoPS-Bold.otf"]
PAPERS = [((246, 240, 226), (25, 25, 25)), ((30, 30, 30), (246, 240, 226)),
          ((247, 214, 72), (25, 25, 25)), ((214, 66, 54), (255, 248, 236)),
          ((205, 180, 140), (30, 25, 20)), ((255, 255, 255), (200, 40, 46))]
INK = (34, 30, 28)
RED = (196, 48, 44)
GOLD = (236, 190, 70)
_fc: dict = {}


def font(p, s):
    k = (p, s)
    if k not in _fc:
        _fc[k] = ImageFont.truetype(p, s)
    return _fc[k]


def _paper_bg():
    rng = np.random.default_rng(7)
    base = np.zeros((H, W, 3), np.float32) + np.array([232, 222, 200], np.float32)
    n = rng.normal(0, 1, (H // 4, W // 4)).astype(np.float32)
    n = np.array(Image.fromarray(((n * 20) + 128).clip(0, 255).astype(np.uint8)).resize((W, H), Image.BICUBIC), np.float32) - 128
    base += n[..., None] * 0.35
    img = Image.fromarray(base.clip(0, 255).astype(np.uint8))
    dr = ImageDraw.Draw(img)
    r = random.Random(3)
    for _ in range(900):  # fibers
        x, y = r.randrange(W), r.randrange(H)
        a = r.uniform(0, math.pi)
        L = r.uniform(4, 18)
        c = r.randint(195, 215)
        dr.line([(x, y), (x + L * math.cos(a), y + L * math.sin(a))], fill=(c, c - 8, c - 25), width=1)
    return img


BG = _paper_bg()


def torn_poly(x0, y0, x1, y1, seed, amp=7, step=14):
    r = random.Random(seed)
    pts = []
    for x in range(int(x0), int(x1), step):
        pts.append((x, y0 + r.uniform(-amp, amp)))
    for y in range(int(y0), int(y1), step):
        pts.append((x1 + r.uniform(-amp, amp), y))
    for x in range(int(x1), int(x0), -step):
        pts.append((x, y1 + r.uniform(-amp, amp)))
    for y in range(int(y1), int(y0), -step):
        pts.append((x0 + r.uniform(-amp, amp), y))
    return pts


def scrap(text, fpath, size, paper, ink, seed, pad=22, angle=0.0):
    f = font(fpath, size)
    l, t, rr, b = f.getbbox(text)
    w, h = rr - l + pad * 2, b - t + pad * 2
    M = 30
    im = Image.new("RGBA", (w + 2 * M, h + 2 * M), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.polygon(torn_poly(M, M, M + w, M + h, seed, amp=4, step=10), fill=paper + (255,))
    d.text((M + pad - l, M + pad - t), text, font=f, fill=ink)
    im = im.rotate(angle, resample=Image.BICUBIC, expand=True)
    sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
    sh.putalpha(im.getchannel("A").point(lambda v: int(v * 0.45)))
    sh = sh.filter(ImageFilter.GaussianBlur(7))
    out = Image.new("RGBA", (im.width + 14, im.height + 14), (0, 0, 0, 0))
    out.alpha_composite(sh, (12, 14))
    out.alpha_composite(im, (0, 0))
    return out


def place(canvas, s, cx, cy, scale=1.0):
    if scale <= 0.01:
        return
    if abs(scale - 1) > 0.01:
        s = s.resize((max(1, int(s.width * scale)), max(1, int(s.height * scale))), Image.BICUBIC)
    canvas.alpha_composite(s, (int(cx - s.width / 2), int(cy - s.height / 2)))


def pop(t, t0, d=0.18):
    """Stop-motion pop: 0 before t0, overshoot then settle."""
    if t < t0:
        return 0.0
    x = min(1.0, (t - t0) / d)
    return 1.0 + 0.25 * math.sin(x * math.pi) if x < 1 else 1.0


_cache: dict = {}


def S(key, *a, **k):
    if key not in _cache:
        _cache[key] = scrap(*a, **k)
    return _cache[key]


def ransom(word_key, text, y, t, t0, size=150, gap=0.06, seed=0):
    """Ransom-note letters: each its own scrap, own font, own paper."""
    r = random.Random(seed)
    items = []
    x = 0
    for i, ch in enumerate(text):
        if ch == " ":
            x += size * 0.45
            continue
        p, ink = PAPERS[r.randrange(len(PAPERS))]
        fp = FONTS[r.randrange(len(FONTS))]
        sz = int(size * r.uniform(0.85, 1.12))
        s = S((word_key, i), ch, fp, sz, p, ink, seed * 100 + i, pad=14, angle=r.uniform(-9, 9))
        items.append((s, x, r.uniform(-14, 14), t0 + i * gap))
        x += s.width * 0.86
    return items, x


def opener(t):
    """5.6 s collage hook. Stop-motion: quantize to 12 fps."""
    t = math.floor(t * 12) / 12
    img = BG.copy().convert("RGBA")
    # slow camera push
    if t < 2.9:
        # torn red band (big paper strip)
        if "band" not in _cache:
            b = Image.new("RGBA", (W + 200, 470), (0, 0, 0, 0))
            ImageDraw.Draw(b).polygon(torn_poly(10, 20, W + 190, 450, 11, amp=12, step=18), fill=RED + (255,))
            _cache["band"] = b.rotate(-2, resample=Image.BICUBIC, expand=True)
        slide = min(1.0, t / 0.35)
        band = _cache["band"]
        img.alpha_composite(band, (int(-band.width + slide * (band.width - 100)), 560 - band.height // 2))
        lead = S("lead", "You told your agent:", FONTS[1], 64, PAPERS[0][0], INK, 21, angle=-3)
        place(img, lead, 640, 230, pop(t, 0.25))
        rows = [("NEVER PUSH", 470, 0.55, 5), ("TO MAIN", 650, 1.05, 9)]
        main_box = None
        for txt, y, t0, sd in rows:
            items, wtot = ransom(txt, txt, 0, t, t0, size=150, gap=0.05, seed=sd)
            x0 = (W - wtot) / 2
            xs = []
            for s_, x, dy, ts in items:
                place(img, s_, x0 + x + s_.width / 2, y + dy, pop(t, ts))
                xs.append((x0 + x, x0 + x + s_.width))
            if txt == "TO MAIN":
                main_box = (xs[2][0], xs[-1][1], y)
        if t > 1.7:  # stamp
            st = S("stamp", "IT DID IT AGAIN.", FONTS[0], 96, (255, 250, 240), RED, 31, angle=6)
            place(img, st, 1420, 900, pop(t, 1.7, 0.12) * (1.0 if t > 1.82 else 1.4))
            dr = ImageDraw.Draw(img)
            # hand-drawn scribble circle around MAIN
            cx, cy = (main_box[0] + main_box[1]) / 2, main_box[2]
            rx = (main_box[1] - main_box[0]) / 2 + 40
            k = min(1.0, (t - 1.7) / 0.5)
            pts = []
            for i in range(int(80 * k)):
                a = i / 60 * 2 * math.pi
                pts.append((cx + (rx + 8 * math.sin(i * 0.7)) * math.cos(a), cy + (115 + 6 * math.cos(i)) * math.sin(a)))
            if len(pts) > 1:
                dr.line(pts, fill=(250, 240, 220, 255), width=10, joint="curve")
    else:
        u = t - 2.9
        l1 = S("c1", "Correct it once.", FONTS[1], 120, PAPERS[0][0], INK, 41, angle=-2)
        l2 = S("c2", "It stays corrected.", FONTS[0], 128, GOLD, INK, 42, angle=2)
        tag = S("tag", "trust-ladder  \u00b7  a Hermes Agent plugin", FONTS[5], 46, (30, 30, 30), (246, 240, 226), 43, angle=-1)
        place(img, l1, W / 2 - 60, 380, pop(u, 0.0))
        place(img, l2, W / 2 + 60, 560, pop(u, 0.45))
        place(img, tag, W / 2, 760, pop(u, 0.95))
        # masking tape on corners of l2
        if u > 0.6:
            tape = S("tape", "         ", FONTS[0], 60, (238, 230, 196), (0, 0, 0), 44, pad=12, angle=-35)
            place(img, tape, W / 2 + 60 - l2.width / 2 + 30, 560 - l2.height / 2 + 10)
            place(img, tape, W / 2 + 60 + l2.width / 2 - 30, 560 + l2.height / 2 - 20)
    # camera push-in
    z = 1.0 + 0.04 * (t / 5.6)
    if z > 1.001:
        cw, ch = int(W / z), int(H / z)
        img = img.crop(((W - cw) // 2, (H - ch) // 2, (W - cw) // 2 + cw, (H - ch) // 2 + ch)).resize((W, H), Image.BICUBIC)
    return img.convert("RGB")


if __name__ == "__main__":
    for tt in (0.4, 1.2, 2.2, 3.4, 5.0):
        opener(tt).resize((960, 540)).save(f"/workspace/work/vid/col_{tt}.png")
