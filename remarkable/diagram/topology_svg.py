#!/usr/bin/env python3
"""Render a network topology (extracted from a hand-drawn @dia sketch) to a
brand SVG using the shared kodiak_diagram Canvas -- deterministic layered
layout so spacing and orientation are correct regardless of the model.

Input JSON: {"title": str,
  "nodes":[{"id","label","rank"}],           # rank 0 = top row
  "edges":[{"from","to","label"?}]}
Layout: one row per rank, nodes centred + evenly spaced; edges are elbows
(down -> shared bus -> down) with the rust arrowhead."""
from __future__ import annotations
import json, sys
from kodiak_diagram import Canvas, INK, MUTED, RUST, GREEN, DARK, CARD, FAINT

CARD_W = 132
ROW_Y0, ROW_GAP = 88, 150     # first row y, vertical gap between ranks
PAD_X = 40

def render(spec: dict) -> str:
    nodes = spec["nodes"]; edges = spec["edges"]
    ranks = sorted({n["rank"] for n in nodes})
    by_rank = {r: [n for n in nodes if n["rank"] == r] for r in ranks}
    W = max(560, PAD_X*2 + max(len(v) for v in by_rank.values()) * (CARD_W + 60))
    H = ROW_Y0 + len(ranks)*ROW_GAP + 40
    c = Canvas(W, H, spec.get("title", "Network topology"))
    pos = {}                                   # id -> (cx, top_y, bot_y)
    for ri, r in enumerate(ranks):
        row = by_rank[r]; n = len(row)
        span = W - PAD_X*2
        for i, node in enumerate(row):
            cx = PAD_X + span*(i+1)/(n+1)
            y = ROW_Y0 + ri*ROW_GAP
            h = c.card(cx - CARD_W/2, y, CARD_W,
                       [(node["label"], 15, INK)], stroke=RUST, fill=CARD)
            pos[node["id"]] = (cx, y, y + h)
    for e in edges:
        if e["from"] in pos and e["to"] in pos:
            fx, _, fb = pos[e["from"]]; tx, tt, _ = pos[e["to"]]
            c.elbow(fx, fb, tx, tt, color=RUST, label=e.get("label"))
    # brand mark, bottom-left
    c.mark(16, H-34, 22, MUTED, 0.5)
    return c.render()

if __name__ == "__main__":
    spec = json.load(open(sys.argv[1])) if len(sys.argv) > 1 else json.load(sys.stdin)
    sys.stdout.write(render(spec))
