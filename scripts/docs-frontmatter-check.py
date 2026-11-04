#!/usr/bin/env -S python3 -I
"""Frontmatter check for docs/ pages.

  - every page has a YAML frontmatter block with `title:`
  - docs/services/*.md and docs/hosts/*.md must carry `updated: YYYY-MM-DD`
    (an error); other pages without it are listed as backlog (a warning)
  - `status: X` must agree with a `status/Y` tag when both are present
  - --stale-days N lists every `updated:` older than N days (report only)
  - --git lists pages whose last commit is newer than their `updated:` (report only;
    a commit can be a typo fix, so read the diff before bumping the date)

Stdlib only (the frontmatter is read with regexes, not a YAML parser).

Usage: scripts/docs-frontmatter-check.py [--stale-days N] [--today YYYY-MM-DD] [DOCS_DIR]
Exit 1 when an error is found; warnings and the stale report never fail.
"""
import argparse
import datetime as dt
import re
import subprocess
import sys
from pathlib import Path

FM = re.compile(r"\A---\n(.*?)\n---\n", re.S)
REQUIRED_UPDATED = ("services", "hosts")


def field(fm: str, name: str):
    m = re.search(rf"^{name}:\s*(.+?)\s*$", fm, re.M)
    return m.group(1).strip("'\"") if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("docs", nargs="?", default="docs")
    ap.add_argument("--stale-days", type=int, default=0)
    ap.add_argument("--today", default=dt.date.today().isoformat())
    ap.add_argument("--git", action="store_true")
    a = ap.parse_args()
    docs = Path(a.docs)
    today = dt.date.fromisoformat(a.today)
    errors, warns, stale, behind = [], [], [], []
    for page in sorted(docs.rglob("*.md")):
        rel = page.relative_to(docs.parent)
        m = FM.match(page.read_text(encoding="utf-8"))
        if not m:
            errors.append(f"{rel}: no frontmatter")
            continue
        fm = m.group(1)
        if not field(fm, "title"):
            errors.append(f"{rel}: no title:")
        updated = field(fm, "updated")
        section = page.parent.name if page.parent != docs else ""
        if updated is None:
            if page.name == "index.md":
                pass  # section indexes carry no updated:
            elif section in REQUIRED_UPDATED:
                errors.append(f"{rel}: no updated:")
            else:
                warns.append(f"{rel}: no updated: (backlog)")
        else:
            try:
                d = dt.date.fromisoformat(updated)
            except ValueError:
                errors.append(f"{rel}: updated: {updated!r} is not YYYY-MM-DD")
            else:
                if d > today:
                    errors.append(f"{rel}: updated: {updated} is in the future")
                elif a.stale_days and (today - d).days > a.stale_days:
                    stale.append(f"{rel}: updated {updated} ({(today - d).days} days)")
                if a.git:
                    last = subprocess.run(
                        ["git", "log", "-1", "--format=%cs", "--", str(page)],
                        capture_output=True, text=True, check=False,
                    ).stdout.strip()
                    if last and last > updated:
                        behind.append(f"{rel}: updated {updated}, last commit {last}")
        status = field(fm, "status")
        tag = re.search(r"status/([a-z-]+)", fm)
        if status and tag and tag.group(1) != status:
            errors.append(f"{rel}: status: {status} but tag status/{tag.group(1)}")
    for line in errors:
        print("ERROR", line)
    for line in warns:
        print("WARN ", line)
    if a.stale_days:
        print(f"-- updated: older than {a.stale_days} days ({len(stale)}) --")
        for line in stale:
            print("STALE", line)
    if a.git:
        print(f"-- last commit newer than updated: ({len(behind)}) --")
        for line in behind:
            print("BEHIND", line)
    print(f"docs-frontmatter-check: {len(errors)} error(s), {len(warns)} warning(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
