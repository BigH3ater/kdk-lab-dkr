#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Keep the Audiobookshelf reading-order collections in step with the vault Reading List.

    export ABS_TOKEN="$(op read op://kdk-ops/audiobookshelf-api-root/credential)"
    uv run stacks/audiobookshelf/tools/abs_collections.py            # dry run (default)
    uv run stacks/audiobookshelf/tools/abs_collections.py --apply    # write the plan
    ... --list "/path/to/Reading List.md"  --base-url https://.../audiobookshelf/api

Standard library only (python3 works too). Docs: docs/services/audiobookshelf.md,
"Reading-order collections".

Reads the Reading List note:
  * the tables under the "## Round 1..." heading (any heading text after "Round 1") and
    "## Then, in this order" (rows "| 1 | Make It Stick (Brown, Roediger, McDaniel) | 2014 |
    Studying | ABS epub | ... |"; the 5th cell is the status: "ABS <format>", "missing", "NEW");
  * the numbered list under "## Audio queue (commute, alongside the list)"
    ("1. *Accelerate* (Forsgren, Humble, Kim): ...", the italic part is the title).

Maintains three collections (COLLECTIONS below):
  Round 1        every Round 1 row    ebooks library
  Reading order  every table row      ebooks library
  Audio queue    the audio list       audiobooks library

Matching (match()): an exact ISBN found in the row wins; otherwise the normalised title
(subtitle after ":" dropped, parentheticals, edition words, punctuation and case removed)
must be equal, or the ABS title must be a whole-word prefix of the list title AND an
author surname must agree. Several candidates are narrowed by author, then by edition.
Anything still ambiguous, or with an explicitly different edition, is NOT matched: it is
reported and skipped ("never guess"). A row whose status is "missing" or "NEW" is skipped
too, even when ABS has a book of that title (e.g. a stray older edition): the list's status
column is the owner's word on what is in the library. Matches that rest on a title prefix
are printed as doubtful for a human to check.

Apply (plan() → apply()): a missing collection is created with the matched books in list
order; an existing one gets batch/remove for books that left the list, batch/add for new
ones, then PATCH books=[...] when the order differs. Finished books are left alone: only
list membership counts. A second --apply makes 0 writes.

ABS 2.37.2 semantics (server/controllers/CollectionController.js):
  POST  /collections                {libraryId, name, books:[itemId...]}  order = list order, books required
  PATCH /collections/<id>           {books:[itemId...]}  reorders existing members only; ids not in
                                    the list sort FIRST, so removes must run before the reorder
  POST  /collections/<id>/batch/add    {books:[...]}  appends in DB order (not request order)
  POST  /collections/<id>/batch/remove {books:[...]}  renumbers the rest
  GET   /collections                {collections:[{id, name, libraryId, books:[libraryItem...]}]}
  GET   /libraries/<id>/items       {results:[libraryItem...]}
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_LIST = Path.home() / "Documents/kodiak-codex/4. Learning/Content/Reading List.md"
DEFAULT_BASE_URL = "https://audiobookshelf.kmkdp.com/audiobookshelf/api"

EBOOKS_LIBRARY = "edfded58-4a7d-43b1-84a2-271a2798719c"
AUDIOBOOKS_LIBRARY = "da91339b-cfd5-43ca-8104-fde4271bf011"

ROUND1_HEADING = "## Round 1"            # prefix: the heading text after it may change
THEN_HEADING = "## Then, in this order"
AUDIO_HEADING = "## Audio queue (commute, alongside the list)"

ROUND1_NAME = "Round 1"
ORDER_NAME = "Reading order"
AUDIO_NAME = "Audio queue"

DESCRIPTION = "Kept in step with the Reading List by stacks/audiobookshelf/tools/abs_collections.py."

# (collection name, library id, which entries)
COLLECTIONS = [
    (ROUND1_NAME, EBOOKS_LIBRARY, "round1"),
    (ORDER_NAME, EBOOKS_LIBRARY, "table"),
    (AUDIO_NAME, AUDIOBOOKS_LIBRARY, "audio"),
]

# Statuses in the list's Status column that mean "not in the library": never matched.
NOT_OWNED = {"missing", "new"}


# --------------------------------------------------------------------------- parsing

@dataclass
class Entry:
    num: int
    title: str
    authors: list[str]                  # surnames, lower-case
    edition: str = ""
    status: str = ""
    isbns: list[str] = field(default_factory=list)


ISBN_RE = re.compile(r"\b(?:97[89][\d-]{10,14}|\d{9}[\dXx])\b")


def _isbns(text: str) -> list[str]:
    out = []
    for m in ISBN_RE.findall(text):
        digits = m.replace("-", "").upper()
        if len(digits) in (10, 13):
            out.append(digits)
    return out


def _surnames(authors: str) -> list[str]:
    authors = re.sub(r"\bet al\.?", "", authors)
    out = []
    for part in re.split(r",|&|\band\b", authors):
        words = re.findall(r"[^\W\d_]+", part)
        if words:
            out.append(_fold(words[-1]).lower())
    return out


def _split_book(cell: str) -> tuple[str, list[str]]:
    """'Make It Stick (Brown, Roediger, McDaniel)' -> ('Make It Stick', ['brown', ...])."""
    m = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", cell.strip())
    if not m:
        return cell.strip(), []
    return m.group(1).strip(), _surnames(m.group(2))


def _section(text: str, heading: str, prefix: bool = False) -> list[str]:
    lines = text.splitlines()

    def hit(ln: str) -> bool:
        ln = ln.strip()
        return ln == heading or (prefix and ln.startswith(heading + ":"))

    try:
        start = next(i for i, ln in enumerate(lines) if hit(ln))
    except StopIteration:
        raise ValueError(f"heading not found in the Reading List: {heading!r}") from None
    out = []
    for ln in lines[start + 1:]:
        if ln.startswith("## "):
            break
        out.append(ln)
    return out


def parse_table(lines: list[str]) -> list[Entry]:
    entries = []
    for ln in lines:
        if not ln.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if not cells or not cells[0].isdigit() or len(cells) < 2:
            continue                                    # header and --- separator rows
        title, authors = _split_book(cells[1])
        entries.append(Entry(
            num=int(cells[0]), title=title, authors=authors,
            edition=cells[2] if len(cells) > 2 else "",
            status=cells[4] if len(cells) > 4 else "",
            isbns=_isbns(" ".join(cells[1:])),
        ))
    return entries


AUDIO_RE = re.compile(r"^\s*(\d+)\.\s+\*([^*]+)\*\s*(?:\(([^()]*)\))?(.*)$")


def parse_audio(lines: list[str]) -> list[Entry]:
    entries = []
    for ln in lines:
        m = AUDIO_RE.match(ln)
        if not m:
            continue
        rest = m.group(4) or ""
        status = "missing" if re.search(r"\(missing\b", rest) else ""
        entries.append(Entry(num=int(m.group(1)), title=m.group(2).strip(),
                             authors=_surnames(m.group(3) or ""), status=status,
                             isbns=_isbns(rest)))
    return entries


def parse_reading_list(text: str) -> dict[str, list[Entry]]:
    round1 = parse_table(_section(text, ROUND1_HEADING, prefix=True))
    then = parse_table(_section(text, THEN_HEADING))
    table = round1 + then
    return {
        "round1": round1,
        "table": table,
        "audio": parse_audio(_section(text, AUDIO_HEADING)),
    }


# --------------------------------------------------------------------------- matching

ORDINALS = ["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth", "eleventh", "twelfth"]
EDITION_RE = re.compile(
    r"\b(?:(?:\d+(?:st|nd|rd|th)|" + "|".join(ORDINALS) + r")\s+(?:anniversary\s+)?"
    r"(?:edition|ed)\b|(?:\d+(?:st|nd|rd|th)\s+)?anniversary(?:\s+edition)?|"
    r"revised(?:\s+edition)?|(?:edition|ed)\s+\d+)\b", re.IGNORECASE)


def _fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm_title(title: str) -> str:
    t = _fold(title).lower()
    t = t.replace("&", " and ")
    t = re.sub(r"\([^()]*\)|\[[^\[\]]*\]", " ", t)     # (9780062888471), (Collection)
    t = t.split(":", 1)[0]                               # subtitle
    t = EDITION_RE.sub(" ", t)
    t = re.sub(r"\bvol\b\.?", "volume", t)
    t = re.sub(r"['’]", "", t)                      # programmer's -> programmers
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return " ".join(t.split())


def edition_number(text: str) -> int | None:
    """'2nd ed., 2021' -> 2, 'Fluent Python 2nd Edition' -> 2, 'Second Edition' -> 2."""
    t = _fold(text).lower()
    m = re.search(r"\b(\d+)(?:st|nd|rd|th)\s+(?:ed\b|edition|anniv)", t)
    if m:
        return int(m.group(1))
    m = re.search(r"\b(" + "|".join(ORDINALS) + r")\s+edition\b", t)
    if m:
        return ORDINALS.index(m.group(1)) + 1
    return None


@dataclass
class Item:
    id: str
    title: str
    author: str
    isbn: str = ""

    @classmethod
    def from_api(cls, raw: dict) -> Item:
        md = (raw.get("media") or {}).get("metadata") or {}
        isbn = re.sub(r"[^0-9Xx]", "", md.get("isbn") or "").upper()
        return cls(id=raw["id"], title=md.get("title") or "", author=md.get("authorName") or "",
                   isbn=isbn)


@dataclass
class Match:
    entry: Entry
    item: Item | None
    how: str                     # isbn | title | prefix+author | none | ambiguous | edition-mismatch
    doubts: list[str] = field(default_factory=list)


def _author_ok(entry: Entry, item: Item) -> bool:
    words = set(re.findall(r"[a-z]+", _fold(item.author).lower()))
    return any(s in words for s in entry.authors)


def match(entry: Entry, items: list[Item]) -> Match:
    if entry.status.strip().lower() in NOT_OWNED:
        return Match(entry, None, "list-says-" + entry.status.strip().lower())
    if entry.isbns:
        hits = [it for it in items if it.isbn and it.isbn in entry.isbns]
        if len(hits) == 1:
            return Match(entry, hits[0], "isbn")

    want = norm_title(entry.title)
    how = "title"
    cands = [it for it in items if norm_title(it.title) == want]
    if not cands:
        # ABS often stores a short title ("100 Go Mistakes"); accept a whole-word prefix
        # only together with an agreeing author.
        cands = [it for it in items
                 if (n := norm_title(it.title)) and len(n.split()) >= 2
                 and (want + " ").startswith(n + " ") and _author_ok(entry, it)]
        how = "prefix+author"
    if not cands:
        return Match(entry, None, "none")

    if len(cands) > 1 and entry.authors:
        by_author = [it for it in cands if _author_ok(entry, it)]
        if by_author:
            cands = by_author

    want_ed = edition_number(entry.edition) or edition_number(entry.title)
    if want_ed:
        # an explicitly different edition is never a match
        cands_ed = [it for it in cands if edition_number(it.title) in (None, want_ed)]
        if not cands_ed:
            return Match(entry, None, "edition-mismatch",
                         [f"ABS has {', '.join(repr(c.title) for c in cands)}"])
        cands = cands_ed
        if len(cands) > 1:
            exact = [it for it in cands if edition_number(it.title) == want_ed]
            if len(exact) == 1:
                cands = exact

    if len(cands) > 1:
        return Match(entry, None, "ambiguous", [", ".join(repr(c.title) for c in cands)])

    item = cands[0]
    doubts = []
    if how == "prefix+author":
        doubts.append(f"prefix match on {item.title!r}")
    if entry.authors and not _author_ok(entry, item):
        doubts.append(f"author differs ({item.author!r})")
    return Match(entry, item, how, doubts)


# --------------------------------------------------------------------------- planning

@dataclass
class Op:
    method: str
    path: str
    body: dict

    def __str__(self) -> str:
        return f"{self.method} {self.path} {json.dumps(self.body)}"


@dataclass
class Plan:
    name: str
    library_id: str
    matches: list[Match]
    desired: list[str]
    current: list[str] | None          # None = collection does not exist
    collection_id: str | None
    ops: list[Op]


def _dedupe(ids: list[str]) -> list[str]:
    seen, out = set(), []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def plan_collection(name: str, library_id: str, matches: list[Match],
                    collections: list[dict]) -> Plan:
    desired = _dedupe([m.item.id for m in matches if m.item])
    same = [c for c in collections if c.get("name") == name and c.get("libraryId") == library_id]
    if len(same) > 1:
        raise RuntimeError(f"{len(same)} collections named {name!r} in library {library_id}; "
                           "delete the extras in the ABS UI first")
    if not same:
        ops = []
        if desired:
            ops.append(Op("POST", "/collections", {"libraryId": library_id, "name": name,
                                                   "description": DESCRIPTION, "books": desired}))
        return Plan(name, library_id, matches, desired, None, None, ops)

    col = same[0]
    cid = col["id"]
    current = [b["id"] for b in col.get("books") or []]
    want = set(desired)
    remove = [i for i in current if i not in want]
    have = set(current)
    add = [i for i in desired if i not in have]
    ops = []
    if remove:
        ops.append(Op("POST", f"/collections/{cid}/batch/remove", {"books": remove}))
    if add:
        ops.append(Op("POST", f"/collections/{cid}/batch/add", {"books": add}))
    kept = [i for i in current if i in want]
    # batch/add appends in DB order, so any add needs the reorder too
    if desired and (add or kept != desired):
        ops.append(Op("PATCH", f"/collections/{cid}", {"books": desired}))
    return Plan(name, library_id, matches, desired, current, cid, ops)


def build_plans(lists: dict[str, list[Entry]], items_by_lib: dict[str, list[Item]],
                collections: list[dict]) -> list[Plan]:
    plans = []
    for name, lib, key in COLLECTIONS:
        items = items_by_lib[lib]
        matches = [match(e, items) for e in lists[key]]
        plans.append(plan_collection(name, lib, matches, collections))
    return plans


# --------------------------------------------------------------------------- API

class AbsClient:
    def __init__(self, base_url: str, token: str, timeout: float = 30):
        self.base = base_url.rstrip("/")
        self._token = token
        self.timeout = timeout
        self.writes = 0

    def _req(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read()[:200].decode(errors="replace")
            raise RuntimeError(f"{method} {path} -> HTTP {e.code}: {detail}") from None
        return json.loads(raw) if raw.strip() else None

    def get(self, path: str):
        return self._req("GET", path)

    def send(self, op: Op):
        self.writes += 1
        return self._req(op.method, op.path, op.body)

    def library_items(self, library_id: str) -> list[Item]:
        data = self.get(f"/libraries/{library_id}/items?limit=0")
        return [Item.from_api(r) for r in data.get("results", [])
                if not r.get("isMissing") and not r.get("isInvalid")]

    def collections(self) -> list[dict]:
        return self.get("/collections").get("collections", [])


def apply(client, plans: list[Plan]) -> int:
    for p in plans:
        for op in p.ops:
            client.send(op)
    return sum(len(p.ops) for p in plans)


# --------------------------------------------------------------------------- output

def report(plans: list[Plan], out=None) -> None:
    out = out or sys.stdout
    for p in plans:
        matched = [m for m in p.matches if m.item]
        missed = [m for m in p.matches if not m.item]
        state = "missing (will be created)" if p.current is None else f"{len(p.current)} books now"
        print(f"\n== {p.name} (library {p.library_id}): {len(matched)} matched, "
              f"{len(missed)} not matched; collection {state}", file=out)
        for m in p.matches:
            e = m.entry
            if m.item:
                line = f"  {e.num:>3}. {e.title!r} -> {m.item.title!r} [{m.how}] {m.item.id}"
            elif m.how == "none":
                line = f"  {e.num:>3}. {e.title!r} -> not in ABS yet"
            elif m.how.startswith("list-says-"):
                line = f"  {e.num:>3}. {e.title!r} -> skipped, list status {e.status.strip()!r}"
            else:
                line = f"  {e.num:>3}. {e.title!r} -> SKIPPED ({m.how})"
            if m.doubts:
                line += "  DOUBT: " + "; ".join(m.doubts)
            print(line, file=out)
        if p.ops:
            for op in p.ops:
                print(f"  write: {op}", file=out)
        else:
            print("  write: none (already in step)", file=out)
    print(f"\nTotal writes planned: {sum(len(p.ops) for p in plans)}", file=out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--list", type=Path, default=DEFAULT_LIST, help="Reading List note")
    ap.add_argument("--base-url", default=os.environ.get("ABS_URL", DEFAULT_BASE_URL))
    ap.add_argument("--apply", action="store_true", help="write the plan (default: dry run)")
    args = ap.parse_args(argv)

    token = os.environ.get("ABS_TOKEN", "").strip()
    if not token:
        print("ABS_TOKEN is not set (op read op://kdk-ops/audiobookshelf-api-root/credential)",
              file=sys.stderr)
        return 2

    lists = parse_reading_list(args.list.read_text(encoding="utf-8"))
    client = AbsClient(args.base_url, token)
    libs = {lib for _, lib, _ in COLLECTIONS}
    items_by_lib = {lib: client.library_items(lib) for lib in libs}
    plans = build_plans(lists, items_by_lib, client.collections())
    report(plans)

    if not args.apply:
        print("Dry run: nothing written. Re-run with --apply to write.")
        return 0
    n = apply(client, plans)
    print(f"Applied {n} writes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
