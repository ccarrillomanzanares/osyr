#!/usr/bin/env python3
"""OSYR — generates web/og.png (1200x630), the image social networks show when a
ccmai.org/osyr link is shared. Social networks do not show SVG, hence PNG.

Usage: scripts/make-og.py   (regenerates it; the PNG is kept in the repo)
Uses the real screenshot tests/data/osyr-session.png (cropped) and DejaVu Sans Mono.
"""
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W, H = 1200, 630
BG, PANEL, BRIGHT, INK, DIM, LINE = "#000000", "#001100", "#00ff00", "#33ff33", "#008800", "#006400"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"


def font(size, bold=False):
    return ImageFont.truetype(BOLD if bold else FONT, size)


def hexagon(draw, cx, cy, s):
    # the same hexagon as hex.svg (viewBox 100x116), scaled by s
    pts = [(50, 4), (92, 30), (92, 86), (50, 112), (8, 86), (8, 30)]
    pts = [(cx + (x - 50) * s, cy + (y - 58) * s) for x, y in pts]
    draw.polygon(pts, fill=PANEL, outline=BRIGHT, width=int(8 * s))
    r = 16 * s
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=BRIGHT)


def main():
    img = Image.new("RGB", (W, H), BG)

    # green glow behind the hexagon, like the website's drop-shadow
    glow = Image.new("RGB", (W, H), BG)
    hexagon(ImageDraw.Draw(glow), 130, 175, 0.95)
    img = Image.blend(img, glow.filter(ImageFilter.GaussianBlur(14)), 0.6)
    d = ImageDraw.Draw(img)
    hexagon(d, 130, 175, 0.95)

    # identity on the left
    d.text((205, 108), "OSYR", font=font(110, True), fill=BRIGHT)
    d.text((84, 265), "Artificial Intelligence", font=font(28), fill=DIM)
    d.text((84, 300), "Operating System", font=font(28), fill=DIM)
    for i, line in enumerate(["> local AI assistant, offline", "> voice + an agent that runs it",
                              "> ordinary 8 GB laptops"]):
        d.text((84, 365 + i * 34), line, font=font(23), fill=INK)
    d.text((84, 488), "ccmai.org/osyr", font=font(24, True), fill=BRIGHT)

    # real screenshot on the right, cropped at actual size so the text is readable
    shot = Image.open(os.path.join(ROOT, "tests/data/osyr-session.png")).convert("RGB")
    sw, sh = 520, 330
    shot = shot.crop((0, 0, sw, sh))
    x, y = 630, (H - sh + 30) // 2
    d.rectangle([x - 2, y - 30, x + sw + 1, y + sh + 1], fill=PANEL, outline=LINE, width=2)
    for i, c in enumerate(("#ff5f56", "#ffbd2e", BRIGHT)):
        d.ellipse([x + 12 + i * 20, y - 21, x + 24 + i * 20, y - 9], fill=c)
    d.text((x + 80, y - 24), "osyr — local terminal", font=font(15), fill=DIM)
    img.paste(shot, (x, y))

    out = os.path.join(ROOT, "web/og.png")
    img.save(out, optimize=True)
    print(out, img.size, os.path.getsize(out), "bytes")


if __name__ == "__main__":
    main()
