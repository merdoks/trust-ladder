"""Social cut of the demo: title card, caption band, zoom on the block, end card, synth music.

    python promo.py out/trust-ladder-demo.cast trust-ladder-promo.mp4
Terminal text is replayed from the real cast (never edited). Music is synthesized here (royalty-free).
"""
from __future__ import annotations

import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import pyte
from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, str(Path(__file__).parent))
from render import BG, BOLD_PATHS, FONT_PATHS, _font, draw, load_cast, retime  # noqa: E402

W, H, FPS = 1920, 1080, 30
SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
SANS_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
GOLD, RED, CYAN, WHITE, GREY = (240, 200, 90), (255, 95, 95), (100, 210, 230), (240, 242, 246), (150, 156, 168)
TITLE_S, END_S, BAND = 9.0, 7.0, 190
SLOW = 1.35  # body playback stretch so captions are readable
COLS, ROWS = 118, 24
CAPTIONS = [  # (text that appears in the terminal, caption)
    ("1) The agent makes", ("1", "Your agent pushes straight to main.", WHITE)),
    ("2) You correct it", ("2", "You correct it once. It becomes a guard.", WHITE)),
    ("3) A brand-new session", ("3", "New session. Same old habit...", WHITE)),
    ("trust-ladder blocked", ("3", "Blocked before it ran. Agent pushed a branch.", RED)),
    ("4) /trust shows", ("4", "/trust: the rule, its rung, the catch.", WHITE)),
    ("Proof from the remote", ("✓", "Proof: main untouched. Fix went to a branch.", GOLD)),
]


def F(path, size):
    return _font([path], size)


def centered(dr, y, text, font, fill):
    w = dr.textlength(text, font=font)
    dr.text(((W - w) / 2, y), text, font=font, fill=fill)


def title_card(t):
    if t < 1.7:  # beat 1: the pain, on red
        img = Image.new("RGB", (W, H), (150, 30, 36))
        dr = ImageDraw.Draw(img)
        centered(dr, 380, "You told your agent:", F(SANS, 64), (255, 220, 220))
        centered(dr, 480, "\u201cnever push to main.\u201d", F(SANS_B, 104), WHITE)
        if t > 0.8:
            centered(dr, 660, "It did it again.", F(SANS_B, 72), (255, 200, 120))
        return img
    img = Image.new("RGB", (W, H), BG)  # beat 2: the promise
    dr = ImageDraw.Draw(img)
    centered(dr, 360, "Correct it once.", F(SANS_B, 104), WHITE)
    if t > 2.2:
        centered(dr, 500, "It stays corrected.", F(SANS_B, 104), GOLD)
    centered(dr, 690, "trust-ladder  \u00b7  a plugin for Hermes Agent", F(SANS, 44), GREY)
    return img


def end_card(t):
    img = Image.new("RGB", (W, H), BG)
    dr = ImageDraw.Draw(img)
    centered(dr, 120, "trust-ladder", F(SANS_B, 96), GOLD)
    centered(dr, 250, "Every correction lands on the strongest rung it can prove:", F(SANS, 40), GREY)
    rungs = [("1  BLOCK", "the call never runs", RED), ("2  CHECK", "output verified", GOLD),
             ("3  RULE", "injected into context", CYAN), ("4  HUMAN", "you approve", WHITE)]
    for i, (name, sub, col) in enumerate(rungs):
        if t < 0.5 * i:
            continue
        y, x = 350 + i * 105, 560 + i * 60
        dr.rounded_rectangle([x, y, x + 800, y + 86], 18, outline=col, width=4)
        dr.text((x + 30, y + 18), name, font=F(SANS_B, 44), fill=col)
        dr.text((x + 330, y + 26), sub, font=F(SANS, 36), fill=WHITE)
    cmd = "$ hermes plugins install merdoks/trust-ladder --enable"
    shown = cmd[: max(0, int((t - 2.0) * 25))]
    mono = _font(BOLD_PATHS, 46)
    w = dr.textlength(cmd, font=mono)
    dr.rounded_rectangle([(W - w) / 2 - 30, 800, (W + w) / 2 + 30, 880], 14, fill=(30, 33, 42))
    dr.text(((W - w) / 2, 812), shown + ("\u2588" if int(t * 3) % 2 == 0 else ""), font=mono, fill=(120, 220, 130))
    centered(dr, 905, "/teach  \u00b7  /trust  \u00b7  /verify      github.com/merdoks/trust-ladder", F(SANS, 38), WHITE)
    centered(dr, 975, "Built for the Nous Research Hermes Agent plugin hackathon", F(SANS, 32), GREY)
    return img


def body_frame(term, cap, zoom, focus_y, flash, stamp=0.0):
    img = Image.new("RGB", (W, H), BG)
    dr = ImageDraw.Draw(img)
    # caption band
    num, text, col = cap if cap else ("", "", WHITE)
    if text:
        dr.rounded_rectangle([60, 40, 160, 140], 24, fill=col)
        nf = F(SANS_B, 64)
        dr.text((110 - dr.textlength(num, font=nf) / 2, 52), num, font=nf, fill=BG)
        dr.text((200, 55), text, font=F(SANS_B, 62), fill=col)
    # terminal area
    area = (0, BAND, W, H)
    aw, ah = area[2] - area[0], area[3] - area[1]
    t = term
    if zoom > 1.001:
        cw, ch = term.width / zoom, term.height / zoom
        cy = min(max(focus_y, 0), term.height - ch)
        t = term.crop((0, int(cy), int(cw), int(cy + ch))).resize(term.size, Image.LANCZOS)
    img.paste(t.crop((0, 0, aw, ah)), (area[0], area[1]))
    if stamp > 0:
        a = min(1.0, stamp * 3)
        sf = F(SANS_B, 120)
        txt = "BLOCKED BEFORE IT RAN"
        tw = dr.textlength(txt, font=sf)
        ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        od = ImageDraw.Draw(ov)
        od.rounded_rectangle([(W - tw) / 2 - 50, 520, (W + tw) / 2 + 50, 700], 26, fill=(200, 40, 46, int(235 * a)))
        od.text(((W - tw) / 2, 540), txt, font=sf, fill=(255, 255, 255, int(255 * a)))
        img.paste(ov, (0, 0), ov)
        dr = ImageDraw.Draw(img)
    if flash > 0:
        w = int(10 * flash)
        dr.rectangle([w // 2, BAND + w // 2, W - w // 2, H - w // 2], outline=RED, width=w)
    return img


def ease(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


def music(path, dur, hit_t):
    sr = 44100
    n = int(dur * sr)
    t = np.arange(n) / sr
    out = np.zeros(n)
    bar = 3.0  # 80 bpm, 4/4
    chords = [[57, 60, 64], [53, 57, 60], [48, 52, 55, 60], [55, 59, 62]]  # Am F C G
    hz = lambda m: 440 * 2 ** ((m - 69) / 12)
    k = 0
    while k * bar < dur:
        ch = chords[k % 4]
        s, e = int(k * bar * sr), min(n, int((k + 1) * bar * sr + 0.5 * sr))
        tt = t[s:e] - k * bar
        env = np.minimum(1, tt / 0.8) * np.exp(-tt * 0.35)
        for m in ch:
            for det in (-0.12, 0.12):
                out[s:e] += 0.05 * env * np.sin(2 * np.pi * hz(m + det) * tt)
        out[s:e] += 0.06 * env * np.sin(2 * np.pi * hz(ch[0] - 12) * tt)
        for j in range(8):  # soft arpeggio, 8th notes
            m = (ch + [ch[0] + 12])[j % (len(ch) + 1)] + 12
            a0 = int((k * bar + j * bar / 8) * sr)
            a1 = min(n, a0 + int(0.6 * sr))
            if a0 >= n:
                break
            ta = t[a0:a1] - t[a0]
            out[a0:a1] += 0.035 * np.exp(-ta * 6) * np.sin(2 * np.pi * hz(m) * ta)
        k += 1
    h0 = int(hit_t * sr)
    th = t[h0:h0 + int(1.2 * sr)] - t[h0]
    out[h0:h0 + len(th)] += 0.5 * np.exp(-th * 5) * np.sin(2 * np.pi * (55 + 60 * np.exp(-th * 20)) * th)
    fade = np.minimum(1, np.minimum(t / 1.5, (dur - t) / 2.0))
    out = out * fade
    out = out / max(1e-9, np.abs(out).max()) * 0.6
    pcm = (np.repeat(out[:, None], 2, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2), wf.setsampwidth(2), wf.setframerate(sr), wf.writeframes(pcm.tobytes())


def main():
    cast, mp4 = Path(sys.argv[1]), Path(sys.argv[2])
    _, events = load_cast(cast)
    events = [(t * SLOW, d) for t, d in retime(events, 2.2)]
    body = events[-1][0] + 3.5
    dur = TITLE_S + body + END_S

    size = 10
    while True:
        f = _font(FONT_PATHS, size + 1)
        if f.getlength("M") * COLS > W - 80 or int((size + 1) * 1.32) * ROWS > H - BAND - 30:
            break
        size += 1
    font, bold = _font(FONT_PATHS, size), _font(BOLD_PATHS, size)
    cw, chh = font.getlength("M"), int(size * 1.32)
    import render
    render.W, render.H = W, H - BAND
    screen = pyte.Screen(COLS, ROWS)
    stream = pyte.Stream(screen)

    hit_t, i, cap, term, block_at, focus, cap_idx = None, 0, None, None, None, 0, -1
    zc, yc, lines = 1.32, 0.0, ['']
    ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset",
                           "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", str(mp4) + ".v.mp4"],
                          stdin=subprocess.PIPE)
    for nfr in range(int(dur * FPS)):
        tg = nfr / FPS
        if tg < TITLE_S:
            from collage import opener
            frame = opener(tg * 0.6)
        elif tg < TITLE_S + body:
            tb = tg - TITLE_S
            while i < len(events) and events[i][0] <= tb:
                stream.feed(events[i][1])
                i += 1
            if term is None or screen.dirty:
                term = draw(screen, font, bold, cw, chh, 40, 10)
                screen.dirty.clear()
                lines = ["".join(screen.buffer[y][c].data for c in range(COLS)) for y in range(ROWS)]
                joined = "\n".join(lines)
                for k, (needle, c) in enumerate(CAPTIONS):
                    if needle in joined and k > cap_idx:
                        cap_idx, cap = k, c
                        if needle == "trust-ladder blocked":
                            block_at, hit_t = tb, tg
                            focus = 10 + next(y for y, l in enumerate(lines) if needle in l) * chh
            fl = 0.0
            if block_at is not None and tb - block_at < 1.2:
                fl = 1 - (tb - block_at) / 1.2
            used = [y for y, l in enumerate(lines) if l.strip()]
            last = used[-1] if used else 0
            ztarget = 1.0 if max((len(lines[y].rstrip()) for y in used), default=0) > COLS / 1.32 else 1.32
            zc += (ztarget - zc) * 0.12
            view_rows = ROWS / zc
            ytarget = max(0.0, (last + 2 - view_rows)) * chh
            yc += (ytarget - yc) * 0.15
            st = 0.0
            if block_at is not None and 0.3 < tb - block_at < 2.3:
                st = min(1.0, (tb - block_at - 0.3) * 2, (2.3 - (tb - block_at)) * 3)
            frame = body_frame(term, cap, zc, yc, fl, st)
        else:
            frame = end_card(tg - TITLE_S - body)
        ff.stdin.write(frame.tobytes())
    ff.stdin.close()
    ff.wait()
    wav = mp4.with_suffix(".wav")
    music(wav, dur, hit_t or TITLE_S + 20)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp4) + ".v.mp4", "-i", str(wav),
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-af", "loudnorm=I=-16:TP=-1.5",
                    "-shortest", "-movflags", "+faststart", str(mp4)], check=True)
    Path(str(mp4) + ".v.mp4").unlink()
    wav.unlink()
    print(f"{mp4}: {dur:.1f}s font {size}px block at {hit_t}")


if __name__ == "__main__":
    main()
