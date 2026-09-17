#!/usr/bin/env python3
"""Render a network topology (from a hand-drawn @dia sketch) to a brand SVG via
the shared kodiak_diagram Canvas. Deterministic ranked layout (correct spacing/
orientation), distinct per-edge lines, and type stencils inferred from label
naming conventions (sw=switch, rs/sd=router, ap=access point, k8s, vm, srv...).

Input: {"title", "nodes":[{"id","label","rank"}], "edges":[{"from","to","label"?}]}
Type is inferred from the label; the model only needs to give labels + edges."""
from __future__ import annotations
import json, re, sys
from kodiak_diagram import (Canvas, INK, MUTED, RUST, GREEN, DARK, CARD, FAINT,
                            BG, escape)

EMBER, BARK, STONE, ASH = "#C9743F", "#6B6358", "#9A9183", "#8D8377"

# label keyword -> (type, accent). First match wins; order matters.
TYPE_RULES = [
    (r"^(fw|firewall)", ("firewall", EMBER)),
    (r"^(ap|wifi|wireless)", ("ap", EMBER)),
    (r"^(sw|switch)", ("switch", GREEN)),
    (r"^(rs|sd|rtr|router)", ("router", RUST)),
    (r"(k8s|kube|kubernetes)", ("k8s", INK)),
    (r"^(vm|virtual)", ("vm", ASH)),
    (r"(nas|san|storage)", ("storage", BARK)),
    (r"^(srv|server|host)", ("server", BARK)),
    (r"(isp|cloud|internet|wan)", ("cloud", STONE)),
]
def classify(label: str):
    l = label.strip().lower()
    for pat, ty in TYPE_RULES:
        if re.search(pat, l):
            return ty
    return ("generic", MUTED)

def icon(c, t, cx, cy, s, col):
    """Draw a small type stencil centred at (cx,cy), roughly s tall."""
    o = c.out.append; h = s/2
    if t == "switch":
        for dy,(ax,bx) in [(-3,(-h,h)),(4,(h,-h))]:
            o(f'<line x1="{cx-h}" y1="{cy+dy}" x2="{cx+h}" y2="{cy+dy}" stroke="{col}" stroke-width="2"/>')
            o(f'<path d="M {cx+bx} {cy+dy} l {3 if bx<0 else -3} -3 M {cx+bx} {cy+dy} l {3 if bx<0 else -3} 3" stroke="{col}" stroke-width="1.5" fill="none"/>')
    elif t == "router":
        o(f'<circle cx="{cx}" cy="{cy}" r="{h}" fill="none" stroke="{col}" stroke-width="2"/>')
        for dx,dy in [(-1,-1),(1,-1),(-1,1),(1,1)]:
            o(f'<line x1="{cx}" y1="{cy}" x2="{cx+dx*h}" y2="{cy+dy*h}" stroke="{col}" stroke-width="1.5"/>')
    elif t == "ap":
        for r in (h*0.5,h,h*1.4):
            o(f'<path d="M {cx-r} {cy+3} A {r} {r} 0 0 1 {cx+r} {cy+3}" fill="none" stroke="{col}" stroke-width="1.6"/>')
        o(f'<circle cx="{cx}" cy="{cy+3}" r="1.8" fill="{col}"/>')
    elif t in ("server","storage"):
        o(f'<rect x="{cx-h}" y="{cy-h}" width="{s}" height="{s}" rx="2" fill="none" stroke="{col}" stroke-width="2"/>')
        for i in range(3):
            o(f'<line x1="{cx-h+3}" y1="{cy-h+4+i*4}" x2="{cx+h-6}" y2="{cy-h+4+i*4}" stroke="{col}" stroke-width="1.2"/>')
    elif t == "k8s":
        import math
        pts=" ".join(f"{cx+h*math.cos(math.radians(a)):.1f},{cy+h*math.sin(math.radians(a)):.1f}" for a in range(0,360,60))
        o(f'<polygon points="{pts}" fill="none" stroke="{col}" stroke-width="2"/>')
    elif t == "vm":
        o(f'<rect x="{cx-h}" y="{cy-h}" width="{s}" height="{s}" rx="2" fill="none" stroke="{col}" stroke-width="2"/>')
        o(f'<rect x="{cx-h+3}" y="{cy-h+3}" width="{s-6}" height="{s-6}" rx="1" fill="none" stroke="{col}" stroke-width="1.2"/>')
    elif t == "firewall":
        o(f'<rect x="{cx-h}" y="{cy-h}" width="{s}" height="{s}" rx="2" fill="none" stroke="{col}" stroke-width="2"/>')
        o(f'<line x1="{cx}" y1="{cy-h}" x2="{cx}" y2="{cy+h}" stroke="{col}" stroke-width="1.2"/>')
        o(f'<line x1="{cx-h}" y1="{cy}" x2="{cx+h}" y2="{cy}" stroke="{col}" stroke-width="1.2"/>')
    elif t == "cloud":
        o(f'<path d="M {cx-h} {cy+3} a {h*0.5} {h*0.5} 0 0 1 {h*0.4} -{h*0.7} a {h*0.55} {h*0.55} 0 0 1 {h} 0 a {h*0.5} {h*0.5} 0 0 1 {h*0.4} {h*0.7} z" fill="none" stroke="{col}" stroke-width="1.6"/>')
    else:
        o(f'<circle cx="{cx}" cy="{cy}" r="3" fill="{col}"/>')

CARD_W, CARD_H = 140, 46
ROW_Y0, ROW_GAP, PAD_X = 84, 150, 44

def render(spec: dict) -> str:
    nodes, edges = spec["nodes"], spec["edges"]
    ranks = sorted({n["rank"] for n in nodes})
    by_rank = {r: [n for n in nodes if n["rank"] == r] for r in ranks}
    W = max(600, PAD_X*2 + max(len(v) for v in by_rank.values())*(CARD_W+56))
    H = ROW_Y0 + len(ranks)*ROW_GAP + 40
    c = Canvas(W, H, spec.get("title", "Network topology"))
    pos = {}
    for ri, r in enumerate(ranks):
        row = by_rank[r]; n = len(row); span = W - PAD_X*2
        for i, node in enumerate(row):
            cx = PAD_X + span*(i+1)/(n+1); y = ROW_Y0 + ri*ROW_GAP
            ty, accent = classify(node["label"])
            c.box(cx-CARD_W/2, y, CARD_W, CARD_H, stroke=accent, fill=CARD, sw=1.5)
            icon(c, ty, cx-CARD_W/2+20, y+CARD_H/2, 16, accent)
            c.text(cx+8, y+CARD_H/2+5, node["label"], 15, INK)
            pos[node["id"]] = (cx, y, y+CARD_H, CARD_W)
    # distinct per-edge lines, endpoints fanned out along card edges so crossings show
    frm_i, to_i = {}, {}
    outdeg = {}; indeg = {}
    for e in edges: outdeg[e["from"]]=outdeg.get(e["from"],0)+1; indeg[e["to"]]=indeg.get(e["to"],0)+1
    for e in edges:
        if e["from"] not in pos or e["to"] not in pos: continue
        fx,_,fb,fw = pos[e["from"]]; tx,tt,_,tw = pos[e["to"]]
        oi = frm_i[e["from"]] = frm_i.get(e["from"],0)+1
        ii = to_i[e["to"]] = to_i.get(e["to"],0)+1
        sx = fx + fw*0.5*((oi-(outdeg[e["from"]]+1)/2)/max(outdeg[e["from"]],1))
        dx = tx + tw*0.5*((ii-(indeg[e["to"]]+1)/2)/max(indeg[e["to"]],1))
        c.out.append(f'  <path d="M {sx:.1f} {fb} C {sx:.1f} {fb+40}, {dx:.1f} {tt-40}, {dx:.1f} {tt}" '
                     f'fill="none" stroke="{RUST}" stroke-width="1.4" marker-end="url(#a)"/>')
        if e.get("label"):
            c.text((sx+dx)/2, (fb+tt)/2, e["label"], 9, MUTED)
    c.mark(16, H-34, 22, MUTED, 0.5)
    return c.render()

if __name__ == "__main__":
    spec = json.load(open(sys.argv[1])) if len(sys.argv)>1 else json.load(sys.stdin)
    sys.stdout.write(render(spec))
