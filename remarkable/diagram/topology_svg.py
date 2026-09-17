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

EMBER, BARK, STONE, ASH, GREENB = "#C9743F", "#6B6358", "#9A9183", "#8D8377", "#9BBD7E"

# label keyword -> (type, accent). First match wins; order matters (specific
# before short/ambiguous -- e.g. `app` before `ap`).
TYPE_RULES = [
    (r"firewall|^fw\d*$|^fw[-_]", ("firewall", EMBER)),
    (r"load.?balanc|haproxy|^lb\d*$|^lb[-_]", ("lb", RUST)),
    (r"database|postgres|mysql|mariadb|mongo|\bsql\b|^pg\d*$|^db\d*$|^db[-_]", ("database", BARK)),
    (r"cache|redis|memcache", ("cache", EMBER)),
    (r"queue|kafka|rabbit|broker|nats|^mq\d*$", ("queue", ASH)),
    (r"proxy|gateway|ingress|nginx|traefik|^gw\d*$", ("proxy", EMBER)),
    (r"\bdns|adguard|pihole|unbound|^bind", ("dns", GREEN)),
    (r"grafana|prometheus|monitor|loki|metrics|^mon\d*$", ("monitoring", RUST)),
    (r"docker|container|podman|^ct\d*$", ("container", STONE)),
    (r"client|laptop|workstation|^pc\d*$|^user", ("client", MUTED)),
    (r"application|webapp|\bsvc\b|service|^app", ("app", GREENB)),
    (r"wifi|wireless|access.?point|^ap\d*$|^ap[-_]", ("ap", EMBER)),
    (r"switch|^sw\d*$|^sw[-_]", ("switch", GREEN)),
    (r"router|^rtr|^rs\d*$|^sd\d*$", ("router", RUST)),
    (r"k8s|kube|kubernetes", ("k8s", INK)),
    (r"virtual|^vm\d*$|^vm[-_]", ("vm", ASH)),
    (r"nas|san|storage", ("storage", BARK)),
    (r"server|^srv|^host", ("server", BARK)),
    (r"isp|cloud|internet|wan", ("cloud", STONE)),
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
    elif t == "app":                       # window tile
        o(f'<rect x="{cx-h}" y="{cy-h}" width="{s}" height="{s}" rx="2" fill="none" stroke="{col}" stroke-width="2"/>')
        o(f'<line x1="{cx-h}" y1="{cy-h+5}" x2="{cx+h}" y2="{cy-h+5}" stroke="{col}" stroke-width="1.5"/>')
        o(f'<circle cx="{cx-h+3}" cy="{cy-h+2.5}" r="1" fill="{col}"/>')
    elif t == "lb":                        # distributor: node fanning to 3
        o(f'<circle cx="{cx}" cy="{cy-h}" r="2.4" fill="{col}"/>')
        for tx in (cx-h, cx, cx+h):
            o(f'<line x1="{cx}" y1="{cy-h+2}" x2="{tx}" y2="{cy+h}" stroke="{col}" stroke-width="1.5"/>')
    elif t == "database":                  # cylinder
        o(f'<ellipse cx="{cx}" cy="{cy-h+3}" rx="{h}" ry="3.4" fill="none" stroke="{col}" stroke-width="1.8"/>')
        o(f'<path d="M {cx-h} {cy-h+3} V {cy+h-3} A {h} 3.4 0 0 0 {cx+h} {cy+h-3} V {cy-h+3}" fill="none" stroke="{col}" stroke-width="1.8"/>')
    elif t == "cache":                     # lightning bolt
        o(f'<path d="M {cx+2} {cy-h} L {cx-h+2} {cy+1} L {cx} {cy+1} L {cx-2} {cy+h} L {cx+h-1} {cy-2} L {cx} {cy-2} Z" fill="none" stroke="{col}" stroke-width="1.5"/>')
    elif t == "queue":                     # 3 bars
        for dx in (-5,0,5):
            o(f'<line x1="{cx+dx}" y1="{cy-h}" x2="{cx+dx}" y2="{cy+h}" stroke="{col}" stroke-width="2"/>')
    elif t == "proxy":                     # gate + arrow through
        o(f'<line x1="{cx}" y1="{cy-h}" x2="{cx}" y2="{cy+h}" stroke="{col}" stroke-width="2"/>')
        o(f'<path d="M {cx-h} {cy} H {cx+h} M {cx+h-3} {cy-3} L {cx+h} {cy} L {cx+h-3} {cy+3}" fill="none" stroke="{col}" stroke-width="1.4"/>')
    elif t == "dns":                       # globe
        o(f'<circle cx="{cx}" cy="{cy}" r="{h}" fill="none" stroke="{col}" stroke-width="1.6"/>')
        o(f'<ellipse cx="{cx}" cy="{cy}" rx="{h*0.45}" ry="{h}" fill="none" stroke="{col}" stroke-width="1"/>')
        o(f'<line x1="{cx-h}" y1="{cy}" x2="{cx+h}" y2="{cy}" stroke="{col}" stroke-width="1"/>')
    elif t == "monitoring":                # line chart
        o(f'<polyline points="{cx-h},{cy+3} {cx-h/2},{cy-2} {cx},{cy+1} {cx+h/2},{cy-h+2} {cx+h},{cy-1}" fill="none" stroke="{col}" stroke-width="1.8"/>')
    elif t == "container":                 # box with two inner units
        o(f'<rect x="{cx-h}" y="{cy-h}" width="{s}" height="{s}" rx="1" fill="none" stroke="{col}" stroke-width="2"/>')
        o(f'<rect x="{cx-h+2.5}" y="{cy-2}" width="{h-3.5}" height="{h-2}" fill="none" stroke="{col}" stroke-width="1"/>')
        o(f'<rect x="{cx+1}" y="{cy-2}" width="{h-3.5}" height="{h-2}" fill="none" stroke="{col}" stroke-width="1"/>')
    elif t == "client":                    # person
        o(f'<circle cx="{cx}" cy="{cy-h+3}" r="2.8" fill="none" stroke="{col}" stroke-width="1.8"/>')
        o(f'<path d="M {cx-h+2} {cy+h} A {h*0.9} {h*0.9} 0 0 1 {cx+h-2} {cy+h}" fill="none" stroke="{col}" stroke-width="1.8"/>')
    else:
        o(f'<circle cx="{cx}" cy="{cy}" r="3" fill="{col}"/>')

CARD_W, CARD_H = 140, 46
ROW_Y0, ROW_GAP, PAD_X = 84, 150, 44


def compute_ranks(nodes, edges):
    """Longest-path layering from sources so every edge points strictly downward
    and nodes sit one level below their predecessors (min crossings, no node on
    the same rank as something it connects to)."""
    ids=[n["id"] for n in nodes]
    preds={i:[] for i in ids}; succ={i:[] for i in ids}
    for e in edges:
        if e["from"] in preds and e["to"] in preds:
            preds[e["to"]].append(e["from"]); succ[e["from"]].append(e["to"])
    rank={}; 
    def r(i, seen=()):
        if i in rank: return rank[i]
        if i in seen or not preds[i]: rank[i]=0; return 0
        rank[i]=1+max(r(p, seen+(i,)) for p in preds[i]); return rank[i]
    for i in ids: r(i)
    for n in nodes: n["rank"]=rank[n["id"]]
    return nodes

def render(spec: dict) -> str:
    nodes, edges = spec["nodes"], spec["edges"]
    compute_ranks(nodes, edges)   # derive levels from connectivity
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
