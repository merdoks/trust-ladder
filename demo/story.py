"""'Launch video' style (black stage, giant outline words, gradient tracked caps, orbs, dots).
All frames drawn in code. Used by promo.py when STYLE='story'."""
from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1920, 1080
GOTH = "/usr/share/fonts/opentype/urw-base35/URWGothic-Demi.otf"
GOTH_B = "/usr/share/fonts/opentype/urw-base35/URWGothic-Book.otf"
MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
ORANGE, TEAL, MAGENTA = (240, 160, 96), (58, 170, 196), (226, 48, 140)
WHITE, GREY = (244, 244, 246), (120, 120, 128)
_fc: dict = {}
_cache: dict = {}


def font(p, s):
    if (p, s) not in _fc:
        _fc[(p, s)] = ImageFont.truetype(p, s)
    return _fc[(p, s)]


def ease(x):
    x = min(max(x, 0.0), 1.0)
    return 1 - (1 - x) ** 3


def _orb(r, col, a):
    im = Image.new("RGBA", (r * 4, r * 4), (0, 0, 0, 0))
    ImageDraw.Draw(im).ellipse([r, r, r * 3, r * 3], fill=col + (a,))
    return im.filter(ImageFilter.GaussianBlur(r * 0.45))


ORB_T = _orb(260, (20, 110, 100), 120)
ORB_B = _orb(240, (120, 80, 40), 110)
GLOW = _orb(26, (255, 255, 255), 255)
DOTS = [(0.07, 0.14, 6, (90, 90, 96)), (0.13, 0.72, 22, ORANGE), (0.31, 0.78, 8, (90, 90, 96)),
        (0.82, 0.41, 10, MAGENTA), (0.71, 0.75, 9, TEAL), (0.95, 0.5, 5, (90, 90, 96)), (0.55, 0.93, 6, ORANGE)]


def stage(t, word=None, wt=0.0):
    img = Image.new("RGBA", (W, H), (6, 6, 8, 255))
    img.alpha_composite(ORB_T, (int(W * 0.55 + 60 * math.sin(t * 0.3)), -ORB_T.height // 2 - 80))
    img.alpha_composite(ORB_B, (int(W * 0.08 + 50 * math.cos(t * 0.25)), H - ORB_B.height // 2 + 40))
    if word:
        ow = outline_word(word)
        a = ease(wt / 0.6)
        x = (W - ow.width) / 2 - 40 * wt
        layer = ow.copy()
        layer.putalpha(layer.getchannel("A").point(lambda v: int(v * a)))
        img.alpha_composite(layer, (int(x), (H - ow.height) // 2))
    dr = ImageDraw.Draw(img)
    for i, (fx, fy, r, c) in enumerate(DOTS):
        x = fx * W + 18 * math.sin(t * 0.6 + i)
        y = fy * H + 14 * math.cos(t * 0.5 + i * 1.7)
        dr.ellipse([x - r, y - r, x + r, y + r], fill=c)
    gx = W * 0.9 + 30 * math.sin(t * 0.4)
    img.alpha_composite(GLOW, (int(gx - GLOW.width / 2), int(H * 0.84 - GLOW.height / 2)))
    return img


def outline_word(word):
    k = ("ow", word)
    if k not in _cache:
        f = font(GOTH_B, 640)
        l, tp, r, b = f.getbbox(word, stroke_width=3)
        im = Image.new("RGBA", (r - l + 40, b - tp + 40), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.text((20 - l, 20 - tp), word, font=f, fill=(6, 6, 8, 255), stroke_width=4, stroke_fill=(70, 70, 76, 255))
        # keep only stroke: knock out fill by alpha of fill-colour pixels
        arr = np.array(im)
        fill = (arr[..., 0] < 20) & (arr[..., 3] > 0)
        arr[fill, 3] = 0
        _cache[k] = Image.fromarray(arr)
    return _cache[k]


def grad(w, h, cols=(ORANGE, TEAL, MAGENTA)):
    k = ("g", w, h, cols)
    if k not in _cache:
        x = np.linspace(0, 1, w)[None, :, None]
        c = np.array(cols, np.float32)
        seg = np.clip(x * (len(cols) - 1), 0, len(cols) - 1 - 1e-6)
        i = seg.astype(int)
        f = seg - i
        row = c[i[0, :, 0]] * (1 - f[0]) + c[i[0, :, 0] + 1] * f[0]
        g = np.repeat(row[None, :, :], h, axis=0).astype(np.uint8)
        _cache[k] = Image.fromarray(g, "RGB")
    return _cache[k]


def tracked(img, text, cx, cy, size, t, t0, track=0.18, fill=None, cols=(ORANGE, TEAL, MAGENTA),
            per=0.035, fnt=GOTH, rise=26):
    """Wide-tracked caps, letters rise in one by one; gradient fill unless fill given."""
    f = font(fnt, size)
    adv = [f.getlength(ch) + size * track for ch in text]
    tw = sum(adv) - size * track
    asc, desc = f.getmetrics()
    mask = Image.new("L", (int(tw) + 4, asc + desc + rise + 4), 0)
    md = ImageDraw.Draw(mask)
    x = 0.0
    for i, ch in enumerate(text):
        p = ease((t - t0 - i * per) / 0.35)
        if p > 0:
            md.text((x, rise * (1 - p)), ch, font=f, fill=int(255 * p))
        x += adv[i]
    col = Image.new("RGB", mask.size, fill) if fill else grad(mask.width, mask.height, cols)
    img.paste(col, (int(cx - tw / 2), int(cy - (asc + desc) / 2)), mask)
    return tw


def line(img, x0, x1, y, col, p):
    if p <= 0:
        return
    ImageDraw.Draw(img).line([(x0, y), (x0 + (x1 - x0) * ease(p), y)], fill=col, width=3)


def opener(t):
    """~14 s launch-style hook."""
    if t < 3.6:
        img = stage(t, "AGENT", t)
        tracked(img, "YOU TOLD YOUR AGENT", W / 2, H / 2, 92, t, 0.2)
        line(img, 120, 620, H / 2 - 80, ORANGE, (t - 0.8) / 0.8)
        line(img, W - 120, W - 620, H / 2 + 80, MAGENTA, (t - 1.0) / 0.8)
    elif t < 7.4:
        u = t - 3.6
        img = stage(t, "MAIN", u)
        words = ["NEVER", "PUSH", "TO", "MAIN."]
        f = font(GOTH, 130)
        sp = 50
        tot = sum(f.getlength(w) for w in words) + sp * 3
        x = (W - tot) / 2
        dr = ImageDraw.Draw(img)
        for i, w in enumerate(words):
            p = ease((u - 0.25 - i * 0.45) / 0.3)
            if p > 0:
                c = tuple(int(v * p) for v in (WHITE if w != "MAIN." else ORANGE))
                dr.text((x, H / 2 - 75 + 30 * (1 - p)), w, font=f, fill=c)
            x += f.getlength(w) + sp
        tracked(img, "\u201cPLEASE.\u201d", W / 2, H / 2 + 140, 40, u, 2.3, fill=GREY)
    elif t < 10.4:
        u = t - 7.4
        img = stage(t, "AGAIN", u)
        jit = 8 * math.sin(u * 60) if 0.3 < u < 0.7 else 0
        tracked(img, "IT DID IT AGAIN.", W / 2 + jit, H / 2, 120, u, 0.1, cols=(MAGENTA, (255, 90, 90), ORANGE), per=0.02)
    else:
        u = t - 10.4
        img = stage(t, "TRUST", u)
        tracked(img, "TRUST-LADDER", W / 2, H / 2 - 40, 132, u, 0.1)
        tracked(img, "CORRECT IT ONCE.  IT STAYS CORRECTED.", W / 2, H / 2 + 100, 40, u, 1.0, fill=WHITE, per=0.015)
        line(img, 140, 560, H / 2 - 40, ORANGE, (u - 0.6) / 0.6)
        line(img, W - 140, W - 560, H / 2 + 100, MAGENTA, (u - 1.2) / 0.6)
    return img.convert("RGB")


OPENER_S = 14.0
CAP_COL = {"RED": (MAGENTA, (255, 90, 90), ORANGE), "GOLD": (ORANGE, (240, 200, 90), TEAL)}


def body(t, term, cap, zoom, focus_y, flash, stamp, cap_t):
    img = stage(t)
    num, text, kind = cap if cap else ("", "", "")
    if text:
        cols = CAP_COL.get(kind, (ORANGE, TEAL, MAGENTA))
        dr = ImageDraw.Draw(img)
        dr.ellipse([90, 62, 154, 126], fill=cols[0])
        nf = font(GOTH, 40)
        dr.text((122 - dr.textlength(num, font=nf) / 2, 70), num, font=nf, fill=(6, 6, 8))
        f = font(GOTH, 52)
        tw = sum(f.getlength(c) + 52 * 0.08 for c in text.upper())
        tracked(img, text.upper(), 190 + tw / 2, 96, 52, cap_t, 0.0, track=0.08, cols=cols, per=0.012)
    # terminal card
    tv = term
    if zoom > 1.001:
        cw, ch = term.width / zoom, term.height / zoom
        cy = min(max(focus_y, 0), term.height - ch)
        tv = term.crop((0, int(cy), int(cw), int(cy + ch))).resize(term.size, Image.LANCZOS)
    cw, ch = 1760, int(1760 * tv.height / tv.width)
    ch = min(ch, H - 200)
    tv = tv.resize((cw, int(cw * tv.height / tv.width)), Image.LANCZOS).crop((0, 0, cw, ch))
    x0, y0 = (W - cw) // 2, 170
    sh = Image.new("RGBA", (cw + 120, ch + 120), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([60, 70, cw + 60, ch + 70], 28, fill=(0, 0, 0, 200))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(24)), (x0 - 60, y0 - 60))
    m = Image.new("L", (cw, ch), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, cw - 1, ch - 1], 24, fill=255)
    img.paste(tv, (x0, y0), m)
    dr = ImageDraw.Draw(img)
    bc = (int(60 + 180 * flash), 60, int(66 + 40 * flash)) if flash > 0 else (52, 52, 60)
    dr.rounded_rectangle([x0, y0, x0 + cw - 1, y0 + ch - 1], 24, outline=bc, width=3 + int(5 * flash))
    if stamp > 0:
        a = min(1.0, stamp * 3)
        txt = "BLOCKED BEFORE IT RAN"
        f = font(GOTH, 110)
        tw = f.getlength(txt) + 110 * 0.1 * (len(txt) - 1)
        pw, ph = int(tw + 120), 190
        pill = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
        pm = Image.new("L", (pw, ph), 0)
        ImageDraw.Draw(pm).rounded_rectangle([0, 0, pw - 1, ph - 1], 95, fill=int(240 * a))
        pill.paste(grad(pw, ph, (MAGENTA, (230, 60, 70), ORANGE)).convert("RGBA"), (0, 0), pm)
        img.alpha_composite(pill, ((W - pw) // 2, 560))
        tmp = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        tracked(tmp, txt, W / 2, 560 + ph / 2, 110, 10, 0, track=0.1, fill=(255, 255, 255))
        tmp.putalpha(tmp.getchannel("A").point(lambda v: int(v * a)))
        img.alpha_composite(tmp)
    return img.convert("RGB")


def end_card(t):
    img = stage(t + 40, "LADDER", t)
    tracked(img, "TRUST-LADDER", W / 2, 150, 110, t, 0.0)
    tracked(img, "EVERY CORRECTION LANDS ON THE STRONGEST RUNG IT CAN PROVE", W / 2, 255, 30, t, 0.4,
            fill=(170, 170, 178), per=0.008)
    rungs = [("1", "BLOCK", "the call never runs", MAGENTA), ("2", "CHECK", "output verified", ORANGE),
             ("3", "RULE", "injected into context", TEAL), ("4", "HUMAN", "you approve", WHITE)]
    dr = ImageDraw.Draw(img)
    for i, (n, name, sub, col) in enumerate(rungs):
        p = ease((t - 0.8 - 0.45 * i) / 0.4)
        if p <= 0:
            continue
        y = 330 + i * 98
        x = 560 + i * 60 + int(40 * (1 - p))
        c = tuple(int(v * p) for v in col)
        dr.rounded_rectangle([x, y, x + 800, y + 78], 39, outline=c, width=3)
        dr.ellipse([x + 18, y + 14, x + 68, y + 64], fill=c)
        nf = font(GOTH, 34)
        dr.text((x + 43 - dr.textlength(n, font=nf) / 2, y + 19), n, font=nf, fill=(6, 6, 8))
        dr.text((x + 92, y + 18), name, font=font(GOTH, 38), fill=c)
        dr.text((x + 330, y + 22), sub, font=font(GOTH_B, 34), fill=tuple(int(v * p) for v in WHITE))
    cmd = "$ hermes plugins install merdoks/trust-ladder --enable"
    mono = font(MONO, 44)
    shown = cmd[: max(0, int((t - 2.8) * 25))]
    w = dr.textlength(cmd, font=mono)
    dr.rounded_rectangle([(W - w) / 2 - 34, 760, (W + w) / 2 + 34, 846], 43, fill=(24, 24, 30), outline=(60, 60, 70), width=2)
    dr.text(((W - w) / 2, 778), shown + ("\u2588" if int(t * 3) % 2 == 0 else ""), font=mono, fill=(130, 230, 150))
    tracked(img, "/TEACH   /TRUST   /VERIFY      GITHUB.COM/MERDOKS/TRUST-LADDER", W / 2, 905, 32, t, 1.5,
            fill=WHITE, per=0.006, track=0.1)
    tracked(img, "BUILT FOR THE NOUS RESEARCH HERMES AGENT PLUGIN HACKATHON", W / 2, 970, 26, t, 1.8,
            fill=(140, 140, 148), per=0.006, track=0.12)
    return img.convert("RGB")


if __name__ == "__main__":
    for tt in (1.5, 5.6, 8.2, 12.5):
        opener(tt).resize((960, 540)).save(f"/workspace/work/vid/st_{tt}.png")
    end_card(6).resize((960, 540)).save("/workspace/work/vid/st_end.png")
