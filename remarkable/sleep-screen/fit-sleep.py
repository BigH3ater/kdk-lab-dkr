#!/usr/bin/env python3
"""Fit an arbitrary image to the reMarkable Paper Pro sleep-screen format:
1620x2160, cover-filled (center-crop, no distortion), saved as a compact 256-color
palette PNG (~1-2MB; the color e-ink gamut is limited). libpng renders it fine.
Usage: fit-sleep.py <input-image> [output.png]   (default output: kodiak-sleep.png)"""
import sys, pathlib
from PIL import Image
W, H = 1620, 2160
def fit(src, out):
    im = Image.open(src).convert("RGB")
    s = max(W/im.width, H/im.height); nw, nh = round(im.width*s), round(im.height*s)
    im = im.resize((nw, nh), Image.LANCZOS).crop(((nw-W)//2, (nh-H)//2, (nw-W)//2+W, (nh-H)//2+H))
    im = im.quantize(colors=256, dither=Image.FLOYDSTEINBERG)
    im.save(out, optimize=True)
    print(f"fit: {src} -> {out}  ({W}x{H}, {pathlib.Path(out).stat().st_size//1024}KB)")
if __name__ == "__main__":
    if len(sys.argv) < 2: sys.exit("usage: fit-sleep.py <input-image> [output.png]")
    fit(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "kodiak-sleep.png")
