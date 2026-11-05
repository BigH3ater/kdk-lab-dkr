"""Recolour Home Assistant's Roborock map PNG into the Kodiak palette.

HA's Roborock integration (python-roborock -> vacuum-map-parser-roborock) draws
the map with a FIXED palette and exposes no colour options. Its output is
deterministic, though: every pixel is one exact colour from
vacuum_map_parser_base.config.color.ColorsPalette (no anti-aliasing at
map_scale 4), so the image can be recoloured pixel by pixel.

Two room-colour pairs collide in that palette (rooms 2/12 and 8/13 share an
RGB). Rooms are therefore told apart by a seed point per room and a
multi-source flood fill over the native map grid (the image is the native grid
upscaled by MAP_SCALE = 4), not by colour alone.

Geometry is never changed: same width, height and pixel positions, so the
map card's calibration points keep working.

Pure functions only (numpy + Pillow); app.py does the HTTP side.
"""

from __future__ import annotations

import io
import json
from collections import deque
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

RGBA = tuple[int, int, int, int]

# vacuum_map_parser_base 0.1.5 ColorsPalette.ROOM_COLORS (ids 17-32 repeat 1-16).
# Read from the homeassistant container on 2026-11-04; see README.md.
PARSER_ROOM_COLORS: dict[int, tuple[int, int, int]] = {
    1: (240, 178, 122), 2: (133, 193, 233), 3: (217, 136, 128), 4: (52, 152, 219),
    5: (205, 97, 85), 6: (243, 156, 18), 7: (88, 214, 141), 8: (245, 176, 65),
    9: (252, 212, 81), 10: (72, 201, 176), 11: (84, 153, 199), 12: (133, 193, 233),
    13: (245, 176, 65), 14: (82, 190, 128), 15: (72, 201, 176), 16: (165, 105, 189),
}

# ColorsPalette.COLORS entries that actually reach the PNG with HA's drawables
# (charger, path, vacuum position) plus walls/carpets. Keys are roles used by
# the config's "colors" section.
PARSER_COLORS: dict[str, list[RGBA]] = {
    "path": [(147, 194, 238, 255)],
    "wall": [(93, 109, 126, 255), (100, 196, 254, 255)],   # GREY_WALL/MAP_WALL_V2, MAP_WALL
    "carpet": [(169, 247, 169, 255)],
    "robot": [(255, 255, 255, 255)],
    "robot_outline": [(0, 0, 0, 255)],
    # CHARGER is (102,254,218,127); drawn over a wall it composites to (97,181,172).
    "charger": [(102, 254, 218, 127), (97, 181, 172, 255)],
    "obstacle": [(127, 127, 127, 255), (127, 127, 127, 127)],
    "inside": [(32, 115, 185, 255)],     # MAP_INSIDE (only if a room has no segment)
    "outside": [(19, 87, 148, 255)],     # MAP_OUTSIDE (only with show_background)
}

TRANSPARENT: RGBA = (0, 0, 0, 0)
SNAP_MAX_DIST = 48  # RGBA euclidean distance for snapping stray (anti-aliased) pixels


def hex_rgba(value: str | list[int], alpha: int = 255) -> RGBA:
    if isinstance(value, (list, tuple)):
        v = list(value) + [alpha] * (4 - len(value))
        return (int(v[0]), int(v[1]), int(v[2]), int(v[3]))
    s = value.lstrip("#")
    if len(s) == 6:
        s += f"{alpha:02x}"
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16), int(s[6:8], 16))


def mix(a: RGBA, b: RGBA, t: float) -> RGBA:
    return tuple(round(a[i] * (1 - t) + b[i] * t) for i in range(3)) + (a[3],)  # type: ignore[return-value]


def load_config(path: str | Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _pack(arr: np.ndarray) -> np.ndarray:
    """(H,W,4) uint8 -> (H,W) uint32 RGBA key."""
    a = arr.astype(np.uint32)
    return (a[..., 0] << 24) | (a[..., 1] << 16) | (a[..., 2] << 8) | a[..., 3]


def _key(c: RGBA) -> int:
    return (c[0] << 24) | (c[1] << 16) | (c[2] << 8) | c[3]


def _unpack(k: int) -> RGBA:
    return ((k >> 24) & 255, (k >> 16) & 255, (k >> 8) & 255, k & 255)


def _known_colours() -> list[RGBA]:
    known = [TRANSPARENT] + [c + (255,) for c in PARSER_ROOM_COLORS.values()]
    for cols in PARSER_COLORS.values():
        known.extend(cols)
    return list(dict.fromkeys(known))


def snap_unknown(keys: np.ndarray) -> np.ndarray:
    """Map any colour not in the parser palette to its nearest palette colour.

    At map_scale 4 the parser does not anti-alias, so this is normally a no-op;
    it keeps a future resampling/AA change from leaving unmapped fringe pixels.
    Colours farther than SNAP_MAX_DIST from every palette entry pass through.
    """
    known = _known_colours()
    known_keys = {_key(c) for c in known}
    known_arr = np.array(known, dtype=np.int32)
    out = keys
    for k in np.unique(keys):
        k = int(k)
        if k in known_keys:
            continue
        c = np.array(_unpack(k), dtype=np.int32)
        d = np.sqrt(((known_arr - c) ** 2).sum(axis=1))
        i = int(d.argmin())
        if d[i] <= SNAP_MAX_DIST:
            if out is keys:
                out = keys.copy()
            out[keys == k] = _key(known[i])
    return out


def label_rooms(keys: np.ndarray, rooms: dict[int, dict[str, Any]], scale: int) -> np.ndarray:
    """Return an (H,W) int16 room-id label per pixel (0 = none).

    Works on the native grid (every `scale`-th pixel). Phase 1 is a
    multi-source BFS from each room's seed over its own parser colour and
    overlay cells (path, robot, charger); two rooms that share a parser colour
    split their area by whichever seed reaches a cell first. Phase 2 hands
    cut-off fragments to the nearest room through the floor plan if that room
    has their colour; phase 3 sends leftovers to the nearest same-colour seed;
    phase 4 grows each room into the carpet stipple that covers it.
    """
    h, w = keys.shape
    grid = keys[::scale, ::scale]
    gh, gw = grid.shape
    carpet = np.isin(grid, [_key(c) for c in PARSER_COLORS["carpet"]])
    overlay_keys = [_key(c) for role in ("path", "robot", "robot_outline", "charger", "obstacle")
                    for c in PARSER_COLORS[role]]
    labels = np.zeros((gh, gw), dtype=np.int16)

    room_key = {rid: _key(PARSER_ROOM_COLORS[(rid - 1) % 16 + 1] + (255,)) for rid in rooms}
    q: deque[tuple[int, int, int]] = deque()
    for rid, spec in sorted(rooms.items()):
        seed = spec.get("seed")
        if not seed:
            continue
        sx, sy = int(seed[0]) // scale, int(seed[1]) // scale
        own = grid == room_key[rid]
        found = None
        for r in range(0, 16):  # seed may sit on a wall/carpet: search outward up to 16 cells
            y0, y1 = max(0, sy - r), min(gh, sy + r + 1)
            x0, x1 = max(0, sx - r), min(gw, sx + r + 1)
            ys, xs = np.nonzero(own[y0:y1, x0:x1] & (labels[y0:y1, x0:x1] == 0))
            if len(ys):
                found = (int(ys[0]) + y0, int(xs[0]) + x0)
                break
        if found is None:
            continue
        labels[found] = rid
        q.append((found[0], found[1], rid))

    # Phase 1: 8-connected over the room's own colour and overlays only. Carpet
    # is NOT walkable here: carpet stipple bridges rooms, and a seed racing
    # through it would steal a neighbour's rug. Room pixels between carpet
    # stipple are diagonal neighbours, hence 8-connectivity.
    grid_l = grid.tolist()
    overlay_l = np.isin(grid, overlay_keys).tolist()
    lab = labels.tolist()
    steps8 = ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1))
    while q:
        y, x, rid = q.popleft()
        rk = room_key[rid]
        for dy, dx in steps8:
            ny, nx = y + dy, x + dx
            if 0 <= ny < gh and 0 <= nx < gw and lab[ny][nx] == 0:
                if grid_l[ny][nx] == rk or overlay_l[ny][nx]:
                    lab[ny][nx] = rid
                    q.append((ny, nx, rid))
    # Phase 2: room-coloured cells cut off from their seed by walls/furniture
    # (isolated fragments) take the room that is nearest THROUGH the floor plan
    # (BFS over every non-transparent cell from all phase-1 labels), but only if
    # that room has the cell's parser colour. Anything else stays 0 (fallback).
    near = [row[:] for row in lab]
    q2: deque[tuple[int, int]] = deque((y, x) for y in range(gh) for x in range(gw) if lab[y][x])
    opaque_l = (grid != _key(TRANSPARENT)).tolist()
    while q2:
        y, x = q2.popleft()
        rid = near[y][x]
        for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
            if 0 <= ny < gh and 0 <= nx < gw and near[ny][nx] == 0 and opaque_l[ny][nx]:
                near[ny][nx] = rid
                q2.append((ny, nx))
    for y in range(gh):
        row, nrow, grow, orow = lab[y], near[y], grid_l[y], overlay_l[y]
        for x in range(gw):
            if row[x] == 0 and nrow[x] and (grow[x] == room_key[nrow[x]] or orow[x]):
                row[x] = nrow[x]
    # Phase 3: room-coloured cells still unlabelled (cut off and nearest to a
    # different-coloured room) go to the closest seed among rooms sharing
    # their parser colour, so a stray kitchen fragment never turns laundry.
    seeds_by_key: dict[int, list[tuple[int, int, int]]] = {}
    for rid, spec in rooms.items():
        if spec.get("seed"):
            seeds_by_key.setdefault(room_key[rid], []).append(
                (int(spec["seed"][1]) // scale, int(spec["seed"][0]) // scale, rid))
    for y in range(gh):
        row, grow = lab[y], grid_l[y]
        for x in range(gw):
            if row[x] == 0 and grow[x] in seeds_by_key:
                row[x] = min(seeds_by_key[grow[x]], key=lambda s: (s[0] - y) ** 2 + (s[1] - x) ** 2)[2]
    # Phase 4: carpet. Stipple cells sit between cells of the room they cover,
    # so grow labels from room/overlay cells into carpet only (8-connected);
    # carpet unreachable that way falls back to the phase-2 nearest room.
    carpet_l = carpet.tolist()
    q3: deque[tuple[int, int]] = deque(
        (y, x) for y in range(gh) for x in range(gw) if lab[y][x] and not carpet_l[y][x])
    while q3:
        y, x = q3.popleft()
        rid = lab[y][x]
        for dy, dx in steps8:
            ny, nx = y + dy, x + dx
            if 0 <= ny < gh and 0 <= nx < gw and lab[ny][nx] == 0 and carpet_l[ny][nx]:
                lab[ny][nx] = rid
                q3.append((ny, nx))
    for y in range(gh):
        row, nrow, crow = lab[y], near[y], carpet_l[y]
        for x in range(gw):
            if row[x] == 0 and crow[x]:
                row[x] = nrow[x]
    labels = np.array(lab, dtype=np.int16)
    full = np.repeat(np.repeat(labels, scale, axis=0), scale, axis=1)[:h, :w]
    return full


def render(src: Image.Image, cfg: dict[str, Any]) -> Image.Image:
    """Recolour + mask one HA map image. Output has the source's exact size."""
    img = src.convert("RGBA")
    arr = np.asarray(img, dtype=np.uint8).copy()
    keys = snap_unknown(_pack(arr))
    out = arr.copy()
    scale = int(cfg.get("map_scale", 4))
    colors = cfg.get("colors", {})
    rooms = {int(k): v for k, v in cfg.get("rooms", {}).items()}

    labels = label_rooms(keys, rooms, scale)
    fallback_room = hex_rgba(colors.get("room_fallback", "#4d4842"))
    paper = hex_rgba(colors.get("carpet_tint", "#ece2d0"))
    carpet_mix = float(colors.get("carpet_mix", 0.3))

    # Room id -> fill. Index 0 = unlabelled.
    max_id = max([0, *rooms.keys()])
    fill = np.zeros((max_id + 1, 4), dtype=np.uint8)
    carpet_fill = np.zeros((max_id + 1, 4), dtype=np.uint8)
    fill[0] = fallback_room
    carpet_fill[0] = mix(fallback_room, paper, carpet_mix)
    for rid, spec in rooms.items():
        f = hex_rgba(spec["fill"])
        fill[rid] = f
        carpet_fill[rid] = mix(f, paper, carpet_mix)

    # Parser colour -> configured room ids with that colour (for unlabelled pixels).
    room_keys: dict[int, list[int]] = {}
    for rid in range(1, 33):
        room_keys.setdefault(_key(PARSER_ROOM_COLORS[(rid - 1) % 16 + 1] + (255,)), []).append(rid)

    for k, rids in room_keys.items():
        m = keys == k
        if not m.any():
            continue
        lab = labels[m]
        default = next((r for r in rids if r in rooms), 0)
        lab = np.where((lab > 0) & np.isin(lab, rids), lab, default)
        out[m] = fill[lab]

    m = np.isin(keys, [_key(c) for c in PARSER_COLORS["carpet"]])
    if m.any():
        lab = labels[m]
        lab = np.where(lab <= max_id, lab, 0)
        out[m] = carpet_fill[lab]

    for role, default in (("path", "#ece2d0"), ("wall", "#8d8377"), ("robot", "#ece2d0"),
                          ("robot_outline", "#1b1a18"), ("charger", "#c9743f"),
                          ("obstacle", "#6b6358"), ("inside", "#4d4842"), ("outside", "#00000000")):
        target = hex_rgba(colors.get(role, default))
        m = np.isin(keys, [_key(c) for c in PARSER_COLORS[role]])
        out[m] = target

    result = Image.fromarray(out, "RGBA")
    return apply_mask(result, cfg.get("mask", {}), hex_rgba(colors.get("wall", "#8d8377")))


def apply_mask(img: Image.Image, mask_cfg: dict[str, Any], wall: RGBA) -> Image.Image:
    """Clear each polygon to transparent, then draw the 'seal' lines in wall colour."""
    polys = mask_cfg.get("polygons", [])
    seals = mask_cfg.get("seal", [])
    if not polys and not seals:
        return img
    m = Image.new("L", img.size, 0)
    d = ImageDraw.Draw(m)
    for poly in polys:
        d.polygon([tuple(p) for p in poly], fill=255)
    clear = Image.new("RGBA", img.size, TRANSPARENT)
    img = Image.composite(clear, img, m)
    if seals:
        dd = ImageDraw.Draw(img)
        for s in seals:
            x1, y1, x2, y2 = s[:4]
            width = int(s[4]) if len(s) > 4 else 4
            dd.line([(x1, y1), (x2, y2)], fill=wall, width=width)
    return img


def render_bytes(png: bytes, cfg: dict[str, Any]) -> bytes:
    out = render(Image.open(io.BytesIO(png)), cfg)
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=False)
    return buf.getvalue()


def main() -> None:  # CLI: python render.py in.png out.png [--config c.json] [--bg #1b1a18]
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--config", default=str(Path(__file__).with_name("config.json")))
    ap.add_argument("--bg", help="composite onto this colour (preview only)")
    a = ap.parse_args()
    out = render(Image.open(a.src), load_config(a.config))
    if a.bg:
        bg = Image.new("RGBA", out.size, hex_rgba(a.bg))
        bg.alpha_composite(out)
        out = bg
    out.save(a.dst)


if __name__ == "__main__":
    main()
