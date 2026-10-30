#!/usr/bin/env python3
"""Re-layout the Tdarr hevc-pipeline flow for readability (positions only).

The Tdarr flow editor stores an (x, y) per node. Hand-edits and UI drags left
the nodes overlapping, with the two resolution branches (<=1440p / 4K+) sitting
on top of each other. This script recomputes *only* the node `position` values
from the flow graph and writes them back, leaving every other byte of the JSON
(logic, edges, plugin config, key order) untouched.

Layout rules (see docs/runbooks/tdarr-troubleshooting.md):
  * rank = longest path from the Input node -> vertical position (top to bottom)
  * two resolution branches in separate vertical lanes: left = <=1440p (ids
    ending "1080"), right = 4K+ (ids ending "4k")
  * shared pre-branch nodes and the shared tail (7/7b/7c, 8 PASS, 8 FAIL) are
    centered between the lanes
  * error / "hold for review" nodes go in a right-hand column
  * within a rank, a simple barycenter pass orders nodes that share it so edges
    cross as little as possible
  * grid: 320 px between columns, 170 px between ranks (nodes are ~200x60 px);
    an assertion guarantees no two nodes overlap

Re-run after any flow edit:  python3 scripts/tdarr-flow-layout.py
then import with:            scripts/tdarr-flow-sync.sh import
Stdlib only, so it keeps working without any extra dependencies.
"""

import copy
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FLOW = os.path.join(HERE, "..", "stacks", "media", "tdarr-flows", "hevc-pipeline.json")

# Grid geometry (px). Node box is ~200 wide x ~60 tall in the Tdarr editor.
COL_W = 320
ROW_H = 170
X0 = 40
Y0 = 40
NODE_W = 200
NODE_H = 60

# Base column per lane. Left lane sits left of centre, right lane right of it,
# with the error/hold column far right (a gap at col 5 keeps it clear of 4K+).
COL_LEFT = 1
COL_CENTER = 2
COL_RIGHT = 3
COL_ERROR = 6

# Nodes that are error entry points / "hold for review" sinks -> right column.
ERROR_IDS = {"err1", "review1", "review2", "review3"}


def lane(node_id):
    if node_id in ERROR_IDS:
        return "ERROR"
    if node_id.endswith("1080"):
        return "LEFT"
    if node_id.endswith("4k"):
        return "RIGHT"
    return "CENTER"


def base_col(node_id):
    return {"LEFT": COL_LEFT, "CENTER": COL_CENTER,
            "RIGHT": COL_RIGHT, "ERROR": COL_ERROR}[lane(node_id)]


def short_label(node):
    """Compact label for the ASCII sketch."""
    name = (node.get("name") or node["id"]).strip()
    # Cut at the first parenthesis / em-dash explanation, keep the step code.
    for sep in (" (", " --", " -", ":"):
        i = name.find(sep)
        if i > 0:
            name = name[:i]
            break
    name = name.strip()
    return name[:22] if name else node["id"]


def main():
    raw = open(FLOW, "rb").read()
    doc = json.loads(raw)
    original = copy.deepcopy(doc)

    nodes = doc["flowPlugins"]
    edges = doc["flowEdges"]
    ids = [n["id"] for n in nodes]
    assert len(ids) == len(set(ids)), "duplicate node ids"
    idset = set(ids)

    succ = {i: [] for i in ids}
    pred = {i: [] for i in ids}
    for e in edges:
        s, t = e["source"], e["target"]
        assert s in idset and t in idset, f"edge references unknown node: {e}"
        succ[s].append(t)
        pred[t].append(s)

    # Longest-path rank from the roots (nodes with no predecessors). The flow is
    # a DAG; a Kahn topological sort both orders the ranks and proves acyclicity.
    indeg = {i: len(pred[i]) for i in ids}
    queue = [i for i in ids if indeg[i] == 0]
    rank = {i: 0 for i in ids}
    topo = []
    while queue:
        u = queue.pop()
        topo.append(u)
        for w in succ[u]:
            if rank[u] + 1 > rank[w]:
                rank[w] = rank[u] + 1
            indeg[w] -= 1
            if indeg[w] == 0:
                queue.append(w)
    assert len(topo) == len(ids), "flow graph has a cycle; cannot layer it"

    # Downstream depth (longest path to a sink) -> tiebreak for lane sub-splits:
    # the node on the longer onward path keeps the lane's main column.
    depth = {i: 0 for i in ids}
    for u in reversed(topo):
        for w in succ[u]:
            if depth[w] + 1 > depth[u]:
                depth[u] = depth[w] + 1

    # Barycenter of each node from its neighbours' base columns (one pass) ->
    # decides left/right order of nodes that share a rank in the centre lane.
    def barycenter(i):
        nb = pred[i] + succ[i]
        if not nb:
            return base_col(i)
        return sum(base_col(j) for j in nb) / len(nb)

    # Assign a grid column to every node.
    col = {}
    by_rank = {}
    for i in ids:
        by_rank.setdefault(rank[i], []).append(i)

    for r, group in by_rank.items():
        for ln in ("LEFT", "RIGHT", "CENTER", "ERROR"):
            members = [i for i in group if lane(i) == ln]
            if not members:
                continue
            if ln == "CENTER":
                # Spread around centre by barycenter: lowest -> left of centre.
                members.sort(key=lambda i: (barycenter(i), i))
                if len(members) == 1:
                    col[members[0]] = COL_CENTER
                else:
                    # e.g. 2 nodes -> columns 1 and 3 straddling centre col 2.
                    span = list(range(COL_CENTER - (len(members) - 1),
                                       COL_CENTER + len(members), 2))
                    for i, c in zip(members, span):
                        col[i] = c
            elif ln == "LEFT":
                # Deepest onward path keeps main col; siblings step outward (left).
                members.sort(key=lambda i: (-depth[i], i))
                for k, i in enumerate(members):
                    col[i] = COL_LEFT - k
            elif ln == "RIGHT":
                members.sort(key=lambda i: (-depth[i], i))
                for k, i in enumerate(members):
                    col[i] = COL_RIGHT + k
            else:  # ERROR
                members.sort(key=lambda i: (rank[i], i))
                for k, i in enumerate(members):
                    col[i] = COL_ERROR + k

    # Pixel positions.
    pos = {i: (X0 + col[i] * COL_W, Y0 + rank[i] * ROW_H) for i in ids}

    # No two nodes may share a cell or overlap (box = NODE_W x NODE_H).
    cells = {}
    for i in ids:
        key = (col[i], rank[i])
        assert key not in cells, f"grid collision: {i} and {cells[key]} at {key}"
        cells[key] = i
    for a in range(len(ids)):
        for b in range(a + 1, len(ids)):
            xa, ya = pos[ids[a]]
            xb, yb = pos[ids[b]]
            assert not (abs(xa - xb) < NODE_W and abs(ya - yb) < NODE_H), \
                f"overlap: {ids[a]} and {ids[b]}"

    # Write new positions in place; prove nothing but positions changes.
    for n in nodes:
        x, y = pos[n["id"]]
        n["position"]["x"] = x
        n["position"]["y"] = y

    def strip_positions(d):
        d = copy.deepcopy(d)
        for n in d["flowPlugins"]:
            n["position"]["x"] = 0
            n["position"]["y"] = 0
        return json.dumps(d, indent=2, ensure_ascii=False, sort_keys=True)

    assert strip_positions(doc) == strip_positions(original), \
        "non-position fields changed -- refusing to write"
    assert [n["id"] for n in doc["flowPlugins"]] == [n["id"] for n in original["flowPlugins"]], \
        "node order changed"
    assert doc["flowEdges"] == original["flowEdges"], "edge list changed"

    out = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    changed = sum(
        1 for o, n in zip(original["flowPlugins"], doc["flowPlugins"])
        if o["position"] != n["position"]
    )

    if "--check" in sys.argv:
        print(f"[check] layout valid; {changed} node position(s) would change")
    else:
        open(FLOW, "wb").write(out.encode())
        print(f"wrote {FLOW}")
        print(f"{changed} node position(s) changed (logic untouched)")

    print()
    print(sketch(ids, rank, col, nodes))


def sketch(ids, rank, col, nodes):
    """ASCII sketch grouped by LANE (not raw grid column) so lane membership is
    unambiguous. Centre-lane branch pairs (e.g. 3b/4, 7b/8-FAIL) straddle the
    shared column into the lane columns on the canvas; here they stay under
    'shared' with a '/' so the reviewer reads intent, not pixels."""
    label = {n["id"]: short_label(n) for n in nodes}
    order = ["LEFT", "CENTER", "RIGHT", "ERROR"]
    header = {"LEFT": "<=1440p", "CENTER": "shared",
              "RIGHT": "4K+", "ERROR": "error/hold"}
    width = 26
    lines = []
    head = "rank | " + "".join(f"{header[ln]:<{width}}" for ln in order)
    lines.append(head)
    lines.append("-" * len(head.rstrip()))
    for r in sorted(set(rank.values())):
        cells = ""
        for ln in order:
            here = [label[i] for i in ids if rank[i] == r and lane(i) == ln]
            here.sort(key=lambda s: s)
            cells += f"{' / '.join(here):<{width}}"
        lines.append(f"{r:>4} | {cells.rstrip()}")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
