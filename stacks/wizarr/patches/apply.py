#!/usr/bin/env -S python3 -I
"""Apply a unified diff with zero fuzz, or fail.

The Wizarr image (Alpine) has no `patch` or `git`, so the Dockerfile applies its
patches with this. Each hunk's old side (context + removed lines) must occur
exactly once in the target file, with the line counts the @@ header states;
anything else (base-image bump changed the code, patch already applied) exits
non-zero and fails the image build. Lines before the first `--- ` are prose.

Usage: apply.py PATCH ROOT     (ROOT + the path after `+++ b/` is the target)
"""
import re
import sys
from pathlib import Path

HUNK = re.compile(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")


def parse(text):
    """Yield (target, [(old_lines, new_lines, want_old, want_new), ...])."""
    lines = text.splitlines(keepends=True)
    files, i = [], 0
    while i < len(lines):
        if lines[i].startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            target = lines[i + 1][4:].split("\t")[0].strip()
            files.append((target.removeprefix("b/"), []))
            i += 2
            continue
        m = HUNK.match(lines[i])
        if m:
            if not files:
                sys.exit(f"hunk before any file header: {lines[i].strip()}")
            want_old, want_new = int(m.group(1) or 1), int(m.group(2) or 1)
            old, new = [], []
            i += 1
            while i < len(lines) and lines[i][:1] in (" ", "-", "+", "\\"):
                tag, body = lines[i][:1], lines[i][1:]
                if tag in " -":
                    old.append(body)
                if tag in " +":
                    new.append(body)
                i += 1
            files[-1][1].append((old, new, want_old, want_new))
            continue
        i += 1
    if not files or not all(h for _, h in files):
        sys.exit("no file/hunk found in the patch")
    return files


def apply(patch_text, root):
    for target, hunks in parse(patch_text):
        path = Path(root) / target
        src = path.read_text()
        for n, (old, new, want_old, want_new) in enumerate(hunks, 1):
            if (len(old), len(new)) != (want_old, want_new):
                sys.exit(f"{target} hunk {n}: line counts do not match its @@ header")
            old_s, new_s = "".join(old), "".join(new)
            found = src.count(old_s)
            if found != 1:
                sys.exit(f"{target} hunk {n}: expected the old lines exactly once, found {found}"
                         " (base image changed? re-base the patch)")
            src = src.replace(old_s, new_s)
        path.write_text(src)  # in place: keeps owner and mode
        print(f"patched {path} ({len(hunks)} hunk(s))")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    apply(Path(sys.argv[1]).read_text(), sys.argv[2])
