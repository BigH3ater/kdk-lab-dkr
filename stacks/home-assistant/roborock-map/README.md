# roborock-map

Recolours Home Assistant's Roborock map (`image.mr_slave_main_floor_2`) into
the Kodiak palette, keeps the cleaning path visible, and masks the glass-door
LiDAR spill below the kitchen. Same image size and pixel geometry as the
source, so the map card's calibration points do not change.

Service docs: `docs/services/home-assistant.md` → *Map renderer*.

## How it works

1. `GET /map.png` fetches `http://host.docker.internal:8123/api/image_proxy/<SOURCE_ENTITY>`
   with `HA_TOKEN` (cache `CACHE_SECONDS`, 20 s; renders only when the source
   bytes change; serves the last good render while HA is away).
2. `render.py` recolours each pixel. HA renders with
   `vacuum_map_parser_base` 0.1.5's fixed palette at map scale 4 with no
   anti-aliasing, so every pixel is one exact palette colour (stray colours
   within 48 RGBA units are snapped to the nearest palette entry first).
3. Rooms are identified by a seed pixel each plus a flood fill on the native
   grid (image / 4), because two pairs of rooms share a parser colour.
   Carpet stipple takes a lighter tint of the room under it.
4. `mask.polygons` are cleared to transparent; `mask.seal` lines are drawn in
   wall colour to close the outline.

`GET /healthz` returns `{"ok": true, "token_set", "rendered_at", "last_error"}`.

## Parser palette (source) → Kodiak (target)

Read from the homeassistant container 2026-11-04
(`vacuum_map_parser_base.config.color.ColorsPalette`).

| Element | Parser RGB | Target |
|---|---|---|
| Room 1 Master Bedroom Closet | 240,178,122 | `#6b5446` clay |
| Room 2 Master Bedroom | 133,193,233 (= room 12) | `#43566a` slate |
| Room 3 West Bedroom | 217,136,128 | `#5e4a5f` plum |
| Room 4 Master Bathroom | 52,152,219 | `#3f5f5a` teal |
| Room 5 Bathroom 1 | 205,97,85 | `#74483c` rust |
| Room 6 Garage Entrance | 243,156,18 | `#5d5a3c` olive |
| Room 7 Half Bath | 88,214,141 | `#4b6247` moss |
| Room 8 Laundry Room | 245,176,65 (= room 13) | `#534d6e` indigo |
| Room 9 Rachel's Office | 252,212,81 | `#6e5c38` ochre |
| Room 10 Hallway | 72,201,176 | `#3e4d4b` dusk teal |
| Room 11 Front Door Entry | 84,153,199 | `#3e5260` steel |
| Room 12 Living Room | 133,193,233 (= room 2) | `#625746` umber |
| Room 13 Kitchen | 245,176,65 (= room 8) | `#7a5038` ember-dark |
| Carpet | 169,247,169 | room fill mixed 30 % toward paper `#ece2d0` |
| Path | 147,194,238 | paper `#ece2d0` |
| Walls (grey / map wall) | 93,109,126 / 100,196,254 | ash `#8d8377` |
| Charger (and charger over wall) | 102,254,218,127 / 97,181,172 | ember `#c9743f` |
| Robot / outline | 255,255,255 / 0,0,0 | paper / ink `#1b1a18` |
| Outside | transparent | transparent |

Ember was tried for the path: it disappears on the kitchen fill. Paper is
visible on every room.

## Config (`config.json`)

- `rooms.<id>.seed` — an image pixel inside the room (here: the card's tap
  bubble positions). `fill` — the room colour.
- `colors` — `path`, `wall`, `robot`, `robot_outline`, `charger`, `obstacle`,
  `room_fallback` (room-coloured pixels no seed claims), `carpet_tint`,
  `carpet_mix`.
- `mask.polygons` — lists of `[x, y]` image pixels, cleared to transparent.
  Current polygon: the spill below the kitchen, from the living-room bottom
  wall line (y 1150) down to y 1350, x 930–1300.
- `mask.seal` — `[x1, y1, x2, y2, width]` lines in wall colour (y 1147,
  x 1040–1222: the patio-door line).

A change is a number edit + PR + deploy (the image is rebuilt). Preview:

```bash
python render.py saved-ha-map.png preview.png --bg '#1b1a18'
```

## Env

| Var | Default | |
|---|---|---|
| `HA_TOKEN` | — | long-lived HA token, from `/opt/kdk-lab/home-assistant/roborock-map.env` |
| `HA_URL` | `http://host.docker.internal:8123` | |
| `SOURCE_ENTITY` | `image.mr_slave_main_floor_2` | |
| `CACHE_SECONDS` | `20` | |
| `PORT` / `BIND` | `8125` / `0.0.0.0` | published on 127.0.0.1 only |

## Tests

```bash
pip install -r requirements.txt pytest && python -m pytest -q tests
```
