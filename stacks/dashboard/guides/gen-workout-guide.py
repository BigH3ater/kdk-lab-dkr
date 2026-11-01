#!/usr/bin/env python3
"""Generate the "Workout" how-to guide for home.kmkdp.com from the Obsidian vault.

WHO RUNS THIS, AND WHEN
-----------------------
The owner runs this ON THE LAPTOP whenever the exercise plan changes, because the
source of truth lives in the private Obsidian vault (kodiak-codex), which only
exists on the laptop -- the dashboard host never sees it. The script reads the
plan, parses the markdown, copies the referenced exercise diagrams, and emits a
static page into --out.

The outputs contain personal/health content, so they are HOST-MANAGED and NEVER
committed to this PUBLIC repo. After re-running, copy the --out contents to the
dashboard host (kdk-dkr-01) and redeploy the `dashboard` stack:

    scp -r <out>/. kdkadmin@10.1.20.20:/tmp/workout
    ssh kdkadmin@10.1.20.20 'sudo install -d -m 755 /opt/kdk-lab/dashboard/workout \
      && sudo cp -rT /tmp/workout /opt/kdk-lab/dashboard/workout && rm -rf /tmp/workout'

The dashboard compose mounts /opt/kdk-lab/dashboard/workout at /guides/workout
(read-only), so the page serves at https://home.kmkdp.com/guides/workout/workout.html.

USAGE
-----
    python3 gen-workout-guide.py [--vault PATH]

  --vault   Path to the kodiak-codex vault root.
            Default: ~/Documents/kodiak-codex

Reads:
    <vault>/3. Resources/Exercise/Exercise.README.md   (the week table)
    <vault>/3. Resources/Exercise/Days/{Mon..Sun}.README.md + Every Day.README.md
    <vault>/3. Resources/Exercise/diagrams/ex-*.svg

Stdlib only (argparse, pathlib, re, shutil, html) -- nothing to install.
"""

from __future__ import annotations

import argparse
import html
import re
import shutil
import sys
from pathlib import Path

# Cache-buster shared with the other guide pages. Bump together with them.
VERSION = "nav20261101"

DAYS = [
    ("mon", "Monday"),
    ("tue", "Tuesday"),
    ("wed", "Wednesday"),
    ("thu", "Thursday"),
    ("fri", "Friday"),
    ("sat", "Saturday"),
    ("sun", "Sunday"),
]

HERE = Path(__file__).resolve().parent  # stacks/dashboard/guides/

STEP_RE = re.compile(r"^#{2,3}\s+(\d+)\s*·\s*(.+?)\s*$")
EMBED_RE = re.compile(r"!\[\[([^\]]+?)\]\]")
BOLD_LINE_RE = re.compile(r"^\*\*(.+)\*\*$")
WIKILINK_RE = re.compile(r"\[\[([^\]|]+?)(?:\|([^\]]+?))?\]\]")


def read(path: Path) -> str:
    if not path.is_file():
        sys.exit(f"ERROR: expected file not found: {path}")
    return path.read_text(encoding="utf-8")


def esc(text: str) -> str:
    """Escape for HTML text/attribute content, leaving unicode punctuation intact."""
    return html.escape(text, quote=True)


def inline_md(text: str) -> str:
    """Render a small slice of inline markdown (bold + wikilinks) as safe HTML.

    Wikilinks point at vault pages that do not exist on the dashboard, so they
    are flattened to their display text (never a dead href)."""
    text = WIKILINK_RE.sub(lambda m: (m.group(2) or m.group(1)).strip(), text)
    out = []
    i = 0
    for m in re.finditer(r"\*\*(.+?)\*\*", text):
        out.append(esc(text[i:m.start()]))
        out.append("<b>" + esc(m.group(1)) + "</b>")
        i = m.end()
    out.append(esc(text[i:]))
    return "".join(out)


def parse_steps(body: str):
    """Parse '## N · Title' (or '### N · Title') blocks into step dicts.

    Each step: {num, title, dose, embed}. 'dose' is the bold sets/reps line (may
    be empty); 'embed' is the ![[...]] target (an SVG name, or the Every Day
    transclusion target). 'Log in today's Daily note:' lines are dropped."""
    lines = body.splitlines()
    steps = []
    cur = None
    for line in lines:
        m = STEP_RE.match(line)
        if m:
            if cur is not None:
                steps.append(cur)
            cur = {"num": m.group(1), "title": m.group(2).strip(),
                   "dose": "", "embed": None}
            continue
        if cur is None:
            continue
        stripped = line.strip()
        if stripped.lower().startswith("log in today's daily note"):
            continue  # vault-only logging hint
        em = EMBED_RE.search(stripped)
        if em and cur["embed"] is None:
            cur["embed"] = em.group(1).strip()
            continue
        # The dose is the first line that opens with bold -- usually a pure
        # '**sets x reps - start**', sometimes with a trailing note like
        # '**~9 min** -- any time today'. Keep it whole; inline_md renders it.
        if stripped.startswith("**") and not cur["dose"]:
            cur["dose"] = stripped
    if cur is not None:
        steps.append(cur)
    return steps


def parse_everyday(text: str):
    """Parse the '## The routine' section of Every Day.README.md into steps."""
    idx = text.find("## The routine")
    if idx < 0:
        sys.exit("ERROR: '## The routine' section not found in Every Day.README.md")
    return parse_steps(text[idx:])


def parse_day(text: str):
    """Parse one day page: title, summary line, 'You need' line, steps."""
    title = ""
    summary = ""
    youneed = ""
    seen_h1 = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("# ") and not s.startswith("## "):
            title = s[2:].strip()
            seen_h1 = True
            continue
        if s.startswith("## "):
            break  # preamble ends at the first step
        if not seen_h1:
            continue
        if s.startswith("**You need:**"):
            youneed = s[len("**You need:**"):].strip()
            continue
        bm = BOLD_LINE_RE.match(s)
        if bm and not summary and not s.startswith("**You need:**"):
            summary = bm.group(1).strip()
    return {"title": title, "summary": summary, "youneed": youneed,
            "steps": parse_steps(text)}


def parse_week_table(readme: str):
    """Parse the markdown table under '## The week' into cleaned HTML cell rows."""
    lines = readme.splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == "## The week")
    except StopIteration:
        sys.exit("ERROR: '## The week' heading not found in Exercise.README.md")
    rows = []
    in_table = False
    for l in lines[start + 1:]:
        s = l.strip()
        if s.startswith("|"):
            in_table = True
            inner = s[1:] if s.startswith("|") else s
            if inner.endswith("|"):
                inner = inner[:-1]
            # Split on unescaped pipes only; a '\|' is a literal pipe inside a cell
            # (e.g. the display part of a [[target\|display]] wikilink).
            cells = [c.strip().replace("\\|", "|")
                     for c in re.split(r"(?<!\\)\|", inner)]
            if all(set(c) <= set("-: ") for c in cells):
                continue  # separator row
            rows.append(cells)
        elif in_table:
            break
    if not rows:
        sys.exit("ERROR: no table rows found under '## The week'")
    return rows[0], rows[1:]


def day_anchor_for_cell(day_cell_html: str):
    """Map a week-table day cell ('Mon', 'Tue', ... 'Every day') to a section id."""
    plain = re.sub(r"<[^>]+>", "", day_cell_html).lower()
    for aid, name in DAYS:
        if name[:3].lower() in plain:
            return aid
    if "every" in plain:
        return "everyday"
    return None


def copy_svg(name: str, diagrams: Path, out_dir: Path, copied: set) -> str:
    """Copy diagrams/<name> into guides/exercise/ and return the relative href."""
    src = diagrams / name
    if not src.is_file():
        sys.exit(f"ERROR: diagram referenced but missing in vault: {src}")
    dst = out_dir / name
    shutil.copyfile(src, dst)
    copied.add(name)
    return f"exercise/{name}"


def render_steps(steps, diagrams, out_dir, copied, everyday_steps, level="h3"):
    """Render a list of step dicts to HTML. The Every Day transclusion step is
    expanded inline into its own sub-steps."""
    parts = []
    for st in steps:
        embed = st["embed"] or ""
        if embed.startswith("Every Day.README"):
            # Inline the transcluded Every Day routine.
            parts.append('    <div class="step">')
            parts.append(f'      <{level}><span class="n">{esc(st["num"])}</span> '
                         f'{inline_md(st["title"])}</{level}>')
            if st["dose"]:
                parts.append(f'      <p class="dose">{inline_md(st["dose"])}</p>')
            parts.append('      <div class="everyday">')
            parts.append(render_steps(everyday_steps, diagrams, out_dir, copied,
                                       everyday_steps, level="h4"))
            parts.append('      </div>')
            parts.append('    </div>')
            continue
        parts.append('    <div class="step">')
        parts.append(f'      <{level}><span class="n">{esc(st["num"])}</span> '
                     f'{inline_md(st["title"])}</{level}>')
        if st["dose"]:
            parts.append(f'      <p class="dose">{inline_md(st["dose"])}</p>')
        if embed and embed.lower().endswith(".svg"):
            href = copy_svg(embed, diagrams, out_dir, copied)
            parts.append(f'      <img class="ex" src="{esc(href)}" alt="How to do: '
                         f'{esc(st["title"])}" loading="lazy">')
        parts.append('    </div>')
    return "\n".join(parts)


def main():
    ap = argparse.ArgumentParser(description="Generate the Workout guide page.")
    ap.add_argument("--vault", default=str(Path.home() / "Documents" / "kodiak-codex"),
                    help="Path to the kodiak-codex vault root.")
    ap.add_argument("--out", default=str(Path.cwd() / "workout-guide-dist"),
                    help="Output dir for the page + diagrams. HOST-MANAGED: copy its "
                         "contents to /opt/kdk-lab/dashboard/workout on the dashboard "
                         "host; never commit them (this repo is public).")
    args = ap.parse_args()
    OUT = Path(args.out).expanduser()
    OUT.mkdir(parents=True, exist_ok=True)

    vault = Path(args.vault).expanduser()
    ex_dir = vault / "3. Resources" / "Exercise"
    days_dir = ex_dir / "Days"
    diagrams = ex_dir / "diagrams"
    if not diagrams.is_dir():
        sys.exit(f"ERROR: vault diagrams dir not found: {diagrams}\n"
                 f"Pass --vault if the vault is elsewhere.")

    out_dir = OUT / "exercise"
    # Start clean so stale diagrams from a removed exercise do not linger.
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    readme = read(ex_dir / "Exercise.README.md")
    week_header, week_rows = parse_week_table(readme)
    everyday_steps = parse_everyday(read(days_dir / "Every Day.README.md"))

    copied: set = set()

    # --- week overview table ---
    week_html = ["  <table class=\"week\">", "    <thead><tr>"]
    for h in week_header:
        week_html.append(f"      <th>{inline_md(h)}</th>")
    week_html.append("    </tr></thead>")
    week_html.append("    <tbody>")
    for row in week_rows:
        cells = [inline_md(c) for c in row]
        anchor = day_anchor_for_cell(cells[0])
        week_html.append("      <tr>")
        if anchor:
            week_html.append(f'        <td><a href="#{anchor}">{cells[0]}</a></td>')
        else:
            week_html.append(f"        <td>{cells[0]}</td>")
        for c in cells[1:]:
            week_html.append(f"        <td>{c}</td>")
        week_html.append("      </tr>")
    week_html.append("    </tbody>")
    week_html.append("  </table>")

    # --- day quick-nav ---
    nav_html = ['  <nav class="daynav" aria-label="Jump to a day">']
    for aid, name in DAYS:
        nav_html.append(f'    <a href="#{aid}" data-day="{aid}">{name[:3]}</a>')
    nav_html.append('    <a href="#everyday" data-day="everyday">Every&nbsp;day</a>')
    nav_html.append('  </nav>')

    # --- each day ---
    day_sections = []
    for aid, name in DAYS:
        day = parse_day(read(days_dir / f"{name}.README.md"))
        day_sections.append(f'  <section class="day" id="{aid}" data-day="{aid}">')
        day_sections.append(f'    <h2 class="dayhead">{inline_md(day["title"])}</h2>')
        meta = []
        if day["summary"]:
            meta.append(f'<span class="sum">{inline_md(day["summary"])}</span>')
        if day["youneed"]:
            meta.append(f'<span class="need"><b>You need:</b> '
                        f'{inline_md(day["youneed"])}</span>')
        if meta:
            day_sections.append('    <p class="daymeta">' + "<br>".join(meta) + "</p>")
        day_sections.append(render_steps(day["steps"], diagrams, out_dir, copied,
                                         everyday_steps, level="h3"))
        day_sections.append('  </section>')

    # --- standalone Every Day section ---
    everyday_section = ['  <section class="day" id="everyday" data-day="everyday">']
    everyday_section.append(f'    <h2 class="dayhead">Every day — PT stretches + '
                            f'hand routine</h2>')
    everyday_section.append('    <p class="daymeta"><span class="sum">~9 minutes, '
                            'any time — a good break from the keyboard.</span></p>')
    everyday_section.append(render_steps(everyday_steps, diagrams, out_dir, copied,
                                         everyday_steps, level="h3"))
    everyday_section.append('  </section>')

    page = PAGE_TEMPLATE.format(
        v=VERSION,
        week="\n".join(week_html),
        daynav="\n".join(nav_html),
        days="\n".join(day_sections),
        everyday="\n".join(everyday_section),
    )

    out_file = OUT / "workout.html"
    out_file.write_text(page, encoding="utf-8")
    print(f"Wrote {out_file}")
    print(f"Copied {len(copied)} diagram(s) into {out_dir}")


PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<base href="/guides/workout/">
<title>Workout -- Mack House</title>
<link rel="stylesheet" href="../style.css?v={v}">
<link rel="stylesheet" href="../shared-nav.css?v={v}">
<style>
  /* Workout guide extras (exercise diagrams are light-ground images, so they sit
     on a paper card to stay readable on the dark page). */
  .daynav {{ display:flex; flex-wrap:wrap; gap:8px; margin:16px 0 8px;
    position:sticky; top:0; background:var(--bg); padding:8px 0; z-index:10; }}
  .daynav a {{ font-family:'JetBrains Mono',monospace; font-size:12px;
    letter-spacing:.06em; text-transform:uppercase; color:var(--muted);
    text-decoration:none; border:1px solid var(--line); border-radius:999px;
    padding:4px 11px; background:var(--card); }}
  .daynav a:hover {{ border-color:var(--accent); color:var(--text); }}
  .daynav a.today {{ color:var(--ink); background:var(--accent);
    border-color:var(--accent); font-weight:600; }}
  table.week {{ border-collapse:collapse; width:100%; margin:12px 0 8px;
    font-size:14px; }}
  table.week th, table.week td {{ border:1px solid var(--line);
    padding:7px 9px; text-align:left; vertical-align:top; }}
  table.week th {{ font-family:'JetBrains Mono',monospace; font-size:11px;
    letter-spacing:.1em; text-transform:uppercase; color:var(--muted);
    font-weight:500; }}
  table.week td a {{ font-weight:600; }}
  .day {{ scroll-margin-top:60px; margin-top:22px; }}
  .day > .dayhead {{ font-family:'Space Grotesk',sans-serif; font-size:22px;
    letter-spacing:0; text-transform:none; color:var(--text); font-weight:600;
    margin:4px 0 4px; padding-top:10px; border-top:1px solid var(--line); }}
  .day.today > .dayhead {{ color:var(--accent); }}
  .day.today > .dayhead::after {{ content:"Today"; margin-left:10px;
    font-family:'JetBrains Mono',monospace; font-size:10px; letter-spacing:.14em;
    text-transform:uppercase; color:var(--ink); background:var(--accent);
    border-radius:999px; padding:2px 8px; vertical-align:middle; }}
  .daymeta {{ color:var(--muted); font-size:15px; margin:0 0 6px; }}
  .daymeta .need {{ font-size:14px; }}
  .step h3, .step h4 {{ font-family:'Space Grotesk',sans-serif;
    text-transform:none; letter-spacing:0; color:var(--text); font-weight:600;
    margin:0 0 2px; }}
  .step h3 {{ font-size:17px; }}
  .step h4 {{ font-size:15px; }}
  .step .n {{ display:inline-block; min-width:1.6em; color:var(--accent);
    font-family:'JetBrains Mono',monospace; font-weight:600; }}
  .step .dose {{ color:var(--muted); font-size:14px; margin:0 0 8px; }}
  img.ex {{ display:block; width:100%; height:auto; border-radius:9px;
    background:#fbfaf7; border:1px solid var(--line); margin-top:4px; }}
  .everyday {{ margin-top:10px; padding-left:12px;
    border-left:2px solid var(--line); }}
  .everyday .step {{ background:transparent; border:none; padding:6px 0; margin:0; }}
</style>
</head>
<body>
<div class="wrap">
  <header class="site"><span class="bear"><svg viewBox="19 31 82 64" width="34" aria-hidden="true"><path fill="currentColor" fill-rule="evenodd" d="M60 40 C53 33 45 31 38 32 C28 33 19 43 19 56 C19 71 28 85 42 91 C50 95 56 93 60 90 C64 93 70 95 78 91 C92 85 101 71 101 56 C101 43 92 33 82 32 C75 31 67 33 60 40 Z M47 56 C40 54 33 58 32 66 C31 73 36 77 42 75 C38 72 39 65 46 62 C50 60 50 57 47 56 Z M73 56 C80 54 87 58 88 66 C89 73 84 77 78 75 C82 72 81 65 74 62 C70 60 70 57 73 56 Z M58.5 90 C58.5 82 58.5 70 58.5 60 C58.5 57 61.5 57 61.5 60 C61.5 70 61.5 82 61.5 90 C61.5 92 58.5 92 58.5 90 Z"/></svg></span><a href="/">The Mack House</a> &nbsp;&middot;&nbsp; <a href="../index.html">Guides</a></header>
  <h1>&#128170; Workout</h1>
  <p class="lead">Your plan for the week, step by step with a picture for every move. Today's day opens first.</p>

  <div class="tip">
    <b>Knee pain rule:</b> keep pain &le; 3/10 during a move and no worse the next
    morning. Your PT's instructions override this page. Light figure = start, dark
    = working position, red arrow = the movement.
  </div>

  <h2>This week</h2>
{week}

{daynav}

{days}

{everyday}

  <footer>
    Generated from the exercise plan in the vault. When the plan changes, the
    owner re-runs <code>gen-workout-guide.py</code> on the laptop and redeploys
    the dashboard stack.
  </footer>
  </div>
<script>
  /* Highlight and open today's section by default. Date handling mirrors the
     defensive style in shared-nav.js (wrap in try/catch, bail quietly). */
  (function () {{
    try {{
      var ids = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"];
      var today = ids[new Date().getDay()];
      if (!today) {{ return; }}
      var sec = document.getElementById(today);
      if (sec) {{ sec.classList.add("today"); }}
      document.querySelectorAll('.daynav a[data-day="' + today + '"]').forEach(
        function (a) {{ a.classList.add("today"); }});
      // Jump to today's section on first load only when no explicit #hash was
      // requested, so a shared "#fri" link still wins.
      if (!location.hash && sec) {{
        sec.scrollIntoView({{ behavior: "auto", block: "start" }});
      }}
    }} catch (e) {{ /* no-op: the full plan is still there, just not pre-scrolled */ }}
  }})();
</script>
<script src="../shared-nav.js?v={v}" defer></script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
