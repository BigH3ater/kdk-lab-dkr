#!/usr/bin/env -S python3 -I
"""Flag host `docker` commands written without sudo in docs/ pages.

Since 2026-10-28 kdkadmin is not in the docker group on any host, so a command a
reader copies from a page must say `sudo docker ...` (or `sudo -n docker ...`).
Checked: every line inside a fenced code block, and every inline `ssh ... 'docker`
one-liner. Prose and table mentions (`docker logs x` as a hint) are reported only
with --all, because many describe what a tool runs rather than a command to type.
A line can opt out with the comment `# no-sudo-ok` (e.g. a laptop-side command).

Usage: scripts/docs-sudo-check.py [--all] [FILE_OR_DIR ...]   (default: docs; exit 1 on a hit)
"""
import argparse
import re
import sys
from pathlib import Path

CMD = re.compile(r"(?<![\w./-])docker (exec|logs|inspect|ps|run|compose|network|images|info|stop|start|restart|rm)\b")
SUDO = re.compile(r"sudo (-n )?docker \w+$")
SSH = re.compile(r"ssh [^`]*?['\"][^`]*?docker ")


def hits(line):
    for m in CMD.finditer(line):
        before = line[: m.end()]
        if not SUDO.search(before[-(len(m.group(0)) + 9):]):
            # xargs docker / $(docker ...) inside a sudo'd pipeline still needs its own sudo
            yield m.start()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("paths", nargs="*", default=["docs"])
    a = ap.parse_args()
    files = []
    for p in map(Path, a.paths):
        files.extend(sorted(p.rglob("*.md")) if p.is_dir() else [p])
    n = 0
    for f in files:
        fenced = False
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("```"):
                fenced = not fenced
                continue
            if "no-sudo-ok" in line:
                continue
            check = fenced or SSH.search(line) or a.all
            code = re.split(r"\s#\s", line, maxsplit=1)[0] if fenced else line  # drop shell comments
            if check and any(True for _ in hits(code)):
                print(f"{f}:{i}: {line.strip()[:140]}")
                n += 1
    print(f"docs-sudo-check: {n} docker command(s) without sudo")
    return 1 if n else 0


if __name__ == "__main__":
    sys.exit(main())
