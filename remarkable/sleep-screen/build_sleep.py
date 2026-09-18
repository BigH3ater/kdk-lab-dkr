#!/usr/bin/env python3
"""Kodiak sleep screen for the reMarkable Paper Pro -- PHOTO version.

Renders a 1620x2160 RGBA PNG (native suspended.png format) from the public-domain
Barometer Mountain (Kodiak Island) photo, cover-filled, with a subtle brand
overlay: a soft top gradient for legibility + "KODIAK . CODEX" wordmark, a
terracotta rule, and understated "sleeping" text. Install with
install-sleep-screen.sh. Photo asset + credit live in ../pdf-templates/assets."""
import pathlib
from PIL import Image, ImageDraw, ImageFont

W, H = 1620, 2160
WHITE = (255, 255, 255)
TERRA = (192, 118, 79)
FB = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
SRC = pathlib.Path(__file__).resolve().parent.parent / "pdf-templates" / "assets" / "barometer-mountain.jpg"

def cover_fill(im):
    s = max(W/im.width, H/im.height); nw, nh = int(im.width*s), int(im.height*s)
    im = im.resize((nw, nh), Image.LANCZOS)
    return im.crop(((nw-W)//2, (nh-H)//2, (nw-W)//2+W, (nh-H)//2+H))

def spaced(draw, xy, text, font, fill, ls):
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill); x += draw.textlength(ch, font=font) + ls

def spaced_w(draw, text, font, ls):
    return sum(draw.textlength(ch, font=font) + ls for ch in text) - ls

def sleep_screen(base="kodiak-sleep"):
    base_img = cover_fill(Image.open(SRC).convert("RGB")).convert("RGBA")
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(ov)
    # soft dark top gradient so the wordmark/status read over any photo
    top_h = 620
    for y in range(top_h):
        a = int(150 * (1 - y/top_h) ** 1.4)
        d.line([(0, y), (W, y)], fill=(18, 22, 30, a))
    img = Image.alpha_composite(base_img, ov)
    d = ImageDraw.Draw(img)
    fword = ImageFont.truetype(FB, 34)
    fsleep = ImageFont.truetype(FR, 40)
    # wordmark centered
    wm = "KODIAK  ·  CODEX"; ww = spaced_w(d, wm, fword, 10)
    spaced(d, ((W-ww)//2, 150), wm, fword, WHITE, 10)
    # terracotta rule
    d.rectangle([W//2-150, 210, W//2+150, 217], fill=TERRA)
    # understated status
    st = "sleeping"; sw = spaced_w(d, st, fsleep, 8)
    spaced(d, ((W-sw)//2, 250), st, fsleep, (235, 238, 240), 8)
    # 256-color palette PNG: ~1MB (vs ~5MB RGBA) -- kind to the tight rootfs, and
    # the Paper Pro's color e-ink gamut is limited anyway. libpng renders it fine.
    img = img.convert("RGB").quantize(colors=256, dither=Image.FLOYDSTEINBERG)
    img.save(base + ".png", optimize=True)
    print(f"sleep screen (photo): {base}.png ({W}x{H})")

if __name__ == "__main__":
    sleep_screen()
