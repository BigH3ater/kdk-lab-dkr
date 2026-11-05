"""Remap + mask tests on small synthetic maps (no HA, no network)."""

import io
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import render  # noqa: E402

ROOM2 = render.PARSER_ROOM_COLORS[2] + (255,)   # shared with room 12
ROOM5 = render.PARSER_ROOM_COLORS[5] + (255,)
WALL = (93, 109, 126, 255)
PATH = (147, 194, 238, 255)
CARPET = (169, 247, 169, 255)
CLEAR = (0, 0, 0, 0)

CFG = {
    "map_scale": 4,
    "colors": {"path": "#ece2d0", "wall": "#8d8377", "carpet_mix": 0.5, "carpet_tint": "#ffffff"},
    "rooms": {
        "2": {"seed": [8, 8], "fill": "#112233"},
        "5": {"seed": [8, 40], "fill": "#445566"},
        "12": {"seed": [56, 8], "fill": "#778899"},
    },
    "mask": {"polygons": [[[48, 32], [63, 32], [63, 47], [48, 47]]], "seal": []},
}


def synthetic() -> Image.Image:
    """64x48: room 2 | wall | room 12 on top (same parser colour), room 5 below
    with a carpet checkerboard, a path across, and a 'spill' area bottom-right."""
    im = Image.new("RGBA", (64, 48), CLEAR)
    px = im.load()
    for y in range(0, 28):
        for x in range(0, 64):
            px[x, y] = ROOM2
    for y in range(0, 28):
        for x in range(28, 32):
            px[x, y] = WALL                     # 1-cell wall splits 2 | 12
    for y in range(28, 32):
        for x in range(0, 64):
            px[x, y] = WALL
    for y in range(32, 48):
        for x in range(0, 64):
            px[x, y] = ROOM5
            if ((x // 4) + (y // 4)) % 2 and x < 24:
                px[x, y] = CARPET                # carpet stipple on the native grid
    for x in range(4, 60):
        px[x, 14] = PATH
        px[x, 15] = PATH
    return im


def test_size_and_colours():
    src = synthetic()
    out = render.render(src, CFG)
    assert out.size == src.size and out.mode == "RGBA"
    o = out.load()
    assert o[8, 8] == (0x11, 0x22, 0x33, 255)            # room 2
    assert o[56, 8] == (0x77, 0x88, 0x99, 255)           # room 12: same source colour, own fill
    assert o[8, 40] == (0x44, 0x55, 0x66, 255)           # room 5
    assert o[29, 4] == (0x8D, 0x83, 0x77, 255)           # wall -> ash
    assert o[20, 14] == (0xEC, 0xE2, 0xD0, 255)          # path -> paper (room 2 side)
    assert o[50, 15] == (0xEC, 0xE2, 0xD0, 255)          # path -> paper (room 12 side)
    # carpet stipple cell inside room 5 = room 5 fill mixed 50% with white
    assert o[4, 32] == (0xA2, 0xAA, 0xB2, 255)


def test_mask_polygon_cleared():
    out = render.render(synthetic(), CFG).load()
    assert out[56, 40] == CLEAR                          # inside the mask polygon
    assert out[40, 40] == (0x44, 0x55, 0x66, 255)        # just outside it


def test_seal_line_drawn():
    cfg = dict(CFG, mask={"polygons": [], "seal": [[0, 20, 63, 20, 2]]})
    out = render.render(synthetic(), cfg).load()
    assert out[10, 20] == (0x8D, 0x83, 0x77, 255)


def test_snap_stray_colour():
    im = synthetic()
    im.putpixel((8, 4), (135, 191, 235, 255))           # 1-step-off "anti-aliased" room 2 pixel
    out = render.render(im, CFG).load()
    assert out[8, 4] == (0x11, 0x22, 0x33, 255)


def test_render_bytes_roundtrip():
    buf = io.BytesIO()
    synthetic().save(buf, format="PNG")
    out = Image.open(io.BytesIO(render.render_bytes(buf.getvalue(), CFG)))
    assert out.size == (64, 48)


def test_shipped_config_is_valid():
    cfg = render.load_config(Path(__file__).resolve().parents[1] / "config.json")
    assert len(cfg["rooms"]) == 13
    fills = [r["fill"].lower() for r in cfg["rooms"].values()]
    assert len(set(fills)) == 13
    for poly in cfg["mask"]["polygons"]:
        assert len(poly) >= 3



def test_path_does_not_bridge_same_colour_rooms():
    """A path running from room 2 through a doorway into room 12 (same parser
    colour) must not let room 2's seed race along it and claim room 12."""
    im = synthetic()
    px = im.load()
    for y in range(12, 16):                 # path a full native cell tall, through the 2|12 wall
        for x in range(4, 60):
            px[x, y] = PATH
    cfg = dict(CFG, rooms={"2": {"seed": [4, 8], "fill": "#112233"},
                            "12": {"seed": [60, 24], "fill": "#778899"}})
    out = render.render(im, cfg).load()
    for y in range(0, 28):
        for x in range(32, 64):
            if px[x, y] == ROOM2:
                assert out[x, y] == (0x77, 0x88, 0x99, 255), (x, y)
    assert out[20, 8] == (0x11, 0x22, 0x33, 255)
