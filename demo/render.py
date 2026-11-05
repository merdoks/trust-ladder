"""Render an asciinema v2 cast to a 1920x1080 MP4 (pyte terminal emulator + Pillow + ffmpeg).

    python render.py demo/out/trust-ladder-demo.cast demo/trust-ladder-demo.mp4 --idle 1.6

Output starts at the first clear-screen (setup output before it is skipped). Pauses longer than
--idle seconds are shortened to --idle (Hermes start-up waits), like `agg --idle-time-limit`;
the terminal text itself is never edited (block-notice lines are only tinted red).
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import pyte
from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 1920, 1080, 30
BG = (16, 18, 24)
FG = (220, 223, 228)
PALETTE = {
    "black": (40, 42, 54), "red": (255, 110, 110), "green": (120, 220, 130), "brown": (240, 200, 90),
    "yellow": (240, 200, 90), "blue": (110, 160, 255), "magenta": (220, 140, 255), "cyan": (100, 210, 230),
    "white": (230, 230, 230), "default": FG,
}
FONT_PATHS = ["/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"]
BOLD_PATHS = ["/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"]
# DejaVu Sans Mono has no emoji; map Hermes's/the plugin's icons to glyphs it has.
EMOJI_FALLBACK = {"🪜": "▲", "💻": "▸", "⚡": "▸", "☤": "◆", "✗": "✗", "✓": "✓"}
HIGHLIGHT = (("trust-ladder blocked", (255, 120, 120)), ("BLOCKED", (255, 120, 120)))


def _font(paths, size):
    for p in paths:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def load_cast(path: Path):
    lines = path.read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    events = [json.loads(l) for l in lines[1:] if l.strip()]
    return header, [(t, d) for t, kind, d in events if kind == "o"]


def retime(events, idle: float):
    # drop everything before the first clear screen
    start = next((i for i, (_, d) in enumerate(events) if "\x1b[2J" in d), 0)
    events = events[start:]
    out, clock, prev = [], 0.0, events[0][0] if events else 0.0
    for t, d in events:
        clock += min(t - prev, idle)
        prev = t
        out.append((clock, d))
    return out


def draw(screen, font, bold, cell_w, cell_h, x0, y0):
    img = Image.new("RGB", (W, H), BG)
    dr = ImageDraw.Draw(img)
    for y in range(screen.lines):
        row = screen.buffer[y]
        line = "".join(row[c].data for c in range(screen.columns))
        tint = next((rgb for needle, rgb in HIGHLIGHT if needle in line), None)
        x = 0
        while x < screen.columns:
            ch = row[x]
            text = ch.data or " "
            if text.strip():
                text = EMOJI_FALLBACK.get(text, text)
                color = PALETTE.get(ch.fg, FG) if ch.fg != "default" else FG
                if tint is not None and ch.fg == "default":
                    color = tint  # colour only: lines with a block notice are tinted red
                dr.text((x0 + x * cell_w, y0 + y * cell_h), text, font=bold if (ch.bold or tint) else font,
                        fill=color)
            x += 1
    return img


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cast", type=Path)
    ap.add_argument("mp4", type=Path)
    ap.add_argument("--idle", type=float, default=1.6)
    ap.add_argument("--tail", type=float, default=3.0, help="seconds to hold the last frame")
    args = ap.parse_args()

    header, events = load_cast(args.cast)
    cols, rows = header["width"], header["height"]
    events = retime(events, args.idle)
    duration = (events[-1][0] if events else 0) + args.tail

    margin = 60
    size = 10
    while True:  # largest font whose grid fits the frame
        f = _font(FONT_PATHS, size + 1)
        cw = f.getlength("M")
        ch = int((size + 1) * 1.32)
        if cw * cols > W - 2 * margin or ch * rows > H - 2 * margin:
            break
        size += 1
    font, bold = _font(FONT_PATHS, size), _font(BOLD_PATHS, size)
    cell_w, cell_h = font.getlength("M"), int(size * 1.32)
    x0 = int((W - cell_w * cols) / 2)
    y0 = int((H - cell_h * rows) / 2)

    screen = pyte.Screen(cols, rows)
    stream = pyte.Stream(screen)
    total = int(duration * FPS) + 1
    ffmpeg = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
         "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18",
         "-preset", "medium", "-movflags", "+faststart", str(args.mp4)], stdin=subprocess.PIPE)
    i, frame = 0, None
    for n in range(total):
        t = n / FPS
        while i < len(events) and events[i][0] <= t:
            stream.feed(events[i][1])
            i += 1
        if frame is None or screen.dirty:
            frame = draw(screen, font, bold, cell_w, cell_h, x0, y0).tobytes()
            screen.dirty.clear()
        ffmpeg.stdin.write(frame)
    ffmpeg.stdin.close()
    if ffmpeg.wait() != 0:
        raise SystemExit("ffmpeg failed")
    print(f"{args.mp4}: {duration:.1f}s, {total} frames, font {size}px")


if __name__ == "__main__":
    main()
