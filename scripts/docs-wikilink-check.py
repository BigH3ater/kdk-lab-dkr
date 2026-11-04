#!/usr/bin/env -S python3 -I
"""Dead-wikilink check for docs/ pages and vault notes.

A wikilink [[target]], [[target|text]] or [[target#heading]] is resolved the way
Obsidian/Quartz do it: by file name (case-insensitive, `.md` optional) anywhere
under the resolve roots, or by a root-relative path. Links inside code fences and
inline code are ignored.

Usage:
  scripts/docs-wikilink-check.py [--root DIR ...] FILE_OR_DIR ...
Default root: docs (the published site). For vault notes pass the vault as the
root, e.g. --root ~/Documents/kodiak-codex (it holds the Reference mirror).
Exit 1 when a dead link is found. Stdlib only.
"""
import argparse
import re
import sys
from pathlib import Path

WIKI = re.compile(r"!?\[\[([^\]|#\n]*)(?:#[^\]|\n]*)?(?:\|[^\]\n]*)?\]\]")
FENCE = re.compile(r"```.*?```", re.S)
INLINE = re.compile(r"`[^`\n]*`")


def names(roots):
    by_name, by_path = set(), set()
    for root in roots:
        for p in root.rglob("*"):
            if p.is_file() and "/.obsidian/" not in str(p) and "/.git/" not in str(p):
                by_name.add(p.name.lower())
                if p.suffix == ".md":
                    by_name.add(p.stem.lower())
                rel = p.relative_to(root).as_posix().lower()
                by_path.add(rel)
                by_path.add(rel[:-3] if rel.endswith(".md") else rel)
    return by_name, by_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", action="append", type=Path)
    ap.add_argument("paths", nargs="+", type=Path)
    a = ap.parse_args()
    roots = [r.expanduser() for r in (a.root or [Path("docs")])]
    by_name, by_path = names(roots)
    files = []
    for p in a.paths:
        p = p.expanduser()
        files.extend(sorted(p.rglob("*.md")) if p.is_dir() else [p])
    dead = 0
    for f in files:
        text = INLINE.sub("", FENCE.sub("", f.read_text(encoding="utf-8")))
        for n, line in enumerate(text.splitlines(), 1):
            for target in WIKI.findall(line):
                t = target.strip().replace("\\", "")
                if not t:
                    continue  # [[#heading]] in the same note
                low = t.lower()
                if low in by_name or low in by_path or Path(low).name in by_name and "/" not in low:
                    continue
                print(f"{f}:{n}: dead wikilink [[{t}]]")
                dead += 1
    print(f"docs-wikilink-check: {dead} dead link(s) in {len(files)} file(s)")
    return 1 if dead else 0


if __name__ == "__main__":
    sys.exit(main())
