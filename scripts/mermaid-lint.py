#!/usr/bin/env python3
"""Regex lint for Mermaid flowcharts in Markdown (docs/ pages and the vault's ELI5
notes). Obsidian renders Mermaid 11, which rejects some label text that older
renderers accepted; one bad label blanks the whole diagram. This catches the
cases that bit on 2026-10-28 without needing a browser:

  - unquoted edge label  -->|forwardauth@file|  (`@` is edge-id syntax)
  - unquoted label or node text containing ( ) { } # @  (parsed as shapes/ids)

`:`, `/`, `<br/>` and digits are fine unquoted; a labelled chain on one line
(A -->|x| B -->|y| C) renders, so neither is flagged.

Usage: scripts/mermaid-lint.py FILE_OR_DIR ...   (exit 1 when anything is flagged)
"""
import re
import sys
from pathlib import Path

BLOCK = re.compile(r"```mermaid[^\n]*\n(.*?)```", re.S)
EDGE_LABEL = re.compile(r"\|([^|\n]*)\|")
NODE_TEXT = re.compile(r"[A-Za-z0-9_]+(\[\(|\[\[|\(\(|\{\{|\[|\(|\{|>)([^\]\)\}\n]*)")
RISKY = re.compile(r"[@(){}#]")


def problems(body: str):
    out = []
    for n, line in enumerate(body.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("%%") or s.startswith(("flowchart", "graph", "subgraph", "end", "classDef", "class ", "style ", "linkStyle", "direction")):
            continue
        for m in EDGE_LABEL.finditer(s):
            txt = m.group(1).strip()
            if txt.startswith('"') and txt.endswith('"'):
                continue
            if RISKY.search(txt):
                out.append((n, f"quote the edge label |{txt}|"))
        for m in NODE_TEXT.finditer(s):
            txt = m.group(2).strip()
            if not txt or (txt.startswith('"') and txt.endswith('"')):
                continue
            inner = txt[1:] if m.group(1) == "[(" and txt.startswith("(") else txt
            if RISKY.search(inner):
                out.append((n, f"quote the node text {m.group(1)}{txt}"))
    return out


def main(paths):
    bad = 0
    files = []
    for p in map(Path, paths):
        files += sorted(p.rglob("*.md")) if p.is_dir() else [p]
    for f in files:
        try:
            text = f.read_text(errors="ignore")
        except OSError:
            continue
        for m in BLOCK.finditer(text):
            base = text[: m.start()].count("\n") + 1
            for n, msg in problems(m.group(1)):
                bad += 1
                print(f"{f}:{base + n}: {msg}")
    print(f"mermaid-lint: {bad} problem(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["docs"]))
