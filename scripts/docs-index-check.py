#!/usr/bin/env -S python3 -I
"""Index completeness check for docs/.

Every page an index links must exist, and every page must be listed by its index:
  docs/index.md            lists the top-level pages, docs/hosts/*.md and the section indexes
  docs/services/index.md   lists docs/services/*.md
  docs/runbooks/index.md   lists docs/runbooks/*.md
  docs/reference/index.md  lists docs/reference/*.md

Only Markdown links ([text](page.md)) count; wikilinks are checked by
docs-wikilink-check.py. Stdlib only.

Usage: scripts/docs-index-check.py [DOCS_DIR]   (default: docs; exit 1 on any problem)
"""
import re
import sys
from pathlib import Path

LINK = re.compile(r"\]\(([^)#\s]+\.md)(?:#[^)]*)?\)")
FENCE = re.compile(r"```.*?```", re.S)


def links(index: Path):
    text = FENCE.sub("", index.read_text(encoding="utf-8"))
    out = set()
    for target in LINK.findall(text):
        if "://" in target:
            continue
        out.add((index.parent / target).resolve())
    return out


def main(argv):
    docs = Path(argv[1] if len(argv) > 1 else "docs").resolve()
    if not (docs / "index.md").is_file():
        print(f"no index.md under {docs}", file=sys.stderr)
        return 2
    # index file -> pages it must list
    plan = {
        docs / "index.md": sorted(p for p in docs.glob("*.md") if p.name != "index.md")
        + sorted(docs.glob("hosts/*.md"))
        + sorted(p for p in docs.glob("*/index.md")),
    }
    for section in ("services", "runbooks", "reference"):
        idx = docs / section / "index.md"
        if idx.is_file():
            plan[idx] = sorted(p for p in (docs / section).glob("*.md") if p.name != "index.md")
    bad = 0
    for idx, expected in plan.items():
        found = links(idx)
        rel = idx.relative_to(docs.parent)
        for target in sorted(found):
            if not target.is_file():
                print(f"{rel}: links missing page {target.relative_to(docs.parent) if docs.parent in target.parents else target}")
                bad += 1
        for page in expected:
            if page.resolve() not in found:
                print(f"{rel}: does not list {page.relative_to(docs.parent)}")
                bad += 1
    print(f"docs-index-check: {bad} problem(s) in {len(plan)} index page(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
