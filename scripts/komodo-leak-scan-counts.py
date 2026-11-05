#!/usr/bin/env python3
"""Count-only secret leak scan of the Komodo Core database (FerretDB / Mongo wire).

Repeats the 2026-10-31 check after the "Compose Config" log leak (upstream
moghtech/komodo#1636, our fix = the kdk-compose-config-redact wrapper, PR #247):
walks the Update logs and the resource documents (Stack deployed_config etc.)
and prints COUNTS per collection only. It never prints a matched value, a key
name next to a value, a document id or a stack name -- only numbers.

Read-only: it issues find() with projections and nothing else.

Credentials: two lines on stdin (username, then password), e.g. from
`op read op://kdk-cluster/komodo-db/username` and `.../password`. Never pass the
password on the command line. Runbook: docs/runbooks/komodo-leak-scan.md.

Exit status: 0 = no secret-shaped values, 1 = at least one, 2 = error.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from typing import Any, Iterable, Iterator

# Collections scanned by default. Update = deploy/run logs; the rest are resource
# documents that could carry an inline environment or a stored compose config.
DEFAULT_COLLECTIONS = [
    "Update", "Stack", "Deployment", "Build", "Repo", "Procedure",
    "Action", "ResourceSync", "Server", "Builder", "Alerter",
]

REDACTED = "<redacted>"

# KEY=value / KEY: value where the key name says "secret". Value must look real
# (8+ chars, not a placeholder, reference, file path or boolean).
_ASSIGN = re.compile(
    r"""(?ix)
    (?<![A-Za-z0-9_])
    [A-Za-z0-9_]*(?:PASSWORD|PASSWD|SECRET|TOKEN|API_?KEY|PASSKEY|CREDENTIAL|PRIVATE_KEY|AUTH_?KEY)[A-Za-z0-9_]*
    ["']?\s*[:=]\s*["']?
    (?P<val>[^\s"',}]{8,})
    """
)
_PLACEHOLDER = re.compile(
    r"""^(?:<redacted>|\$\{|\$\$|\{\{|\[\[|op://|/|\./|\*+$|x+$|true$|false$|null$|none$|changeme$)""",
    re.I,
)
_KNOWN = [
    re.compile(r"\b(?:ghp|gho|ghs|ghu|ghr)_[A-Za-z0-9]{30,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\btskey-[A-Za-z0-9-]{20,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]
# scheme://user:pass@host (skip ${VAR} interpolation and <redacted>)
_URL_CRED = re.compile(r"[a-z][a-z0-9+.-]*://[^/\s:@]+:(?P<pw>[^/\s@]{6,})@", re.I)

CATEGORIES = ("assign", "known_token", "url_credential")


def classify(text: str) -> dict[str, int]:
    """Return {category: count} for one string, plus the redacted-marker count."""
    out = {c: 0 for c in CATEGORIES}
    for m in _ASSIGN.finditer(text):
        if not _PLACEHOLDER.match(m.group("val")):
            out["assign"] += 1
    for rx in _KNOWN:
        out["known_token"] += len(rx.findall(text))
    for m in _URL_CRED.finditer(text):
        pw = m.group("pw")
        if not (pw.startswith("${") or pw.startswith(REDACTED) or pw.startswith("{{")):
            out["url_credential"] += 1
    out["redacted_markers"] = text.count(REDACTED)
    return out


def _strings(obj: Any) -> Iterator[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _strings(v)


def _new_stats() -> dict[str, int]:
    s = {"docs": 0, "docs_with_hits": 0, "redacted_markers": 0}
    s.update({c: 0 for c in CATEGORIES})
    return s


def scan_collection(name: str, docs: Iterable[dict]) -> dict[str, int]:
    """Aggregate counts for one collection. Only integers leave this function."""
    st = _new_stats()
    if name == "Update":
        st["compose_config_logs"] = 0
    for doc in docs:
        st["docs"] += 1
        hit = False
        if name == "Update":
            for log in doc.get("logs") or []:
                if isinstance(log, dict) and log.get("stage") == "Compose Config":
                    st["compose_config_logs"] += 1
        for s in _strings(doc):
            c = classify(s)
            st["redacted_markers"] += c["redacted_markers"]
            for cat in CATEGORIES:
                if c[cat]:
                    st[cat] += c[cat]
                    hit = True
        if hit:
            st["docs_with_hits"] += 1
    return st


def secret_total(st: dict[str, int]) -> int:
    return sum(st[c] for c in CATEGORIES)


# ---------------------------------------------------------------- sources

def fixture_source(path: str, collections: list[str]):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    for name in collections:
        yield name, data.get(name, [])


def mongo_source(args, collections: list[str]):
    try:
        from pymongo import MongoClient  # imported lazily: the self-test needs no driver
    except ImportError:
        raise SystemExit("pymongo not installed (pip install pymongo)")
    lines = sys.stdin.read().splitlines()
    if len(lines) < 2 or not lines[0] or not lines[1]:
        raise SystemExit("expected two lines on stdin: username, password")
    client = MongoClient(
        host=args.host, port=args.port, username=lines[0], password=lines[1],
        authSource=args.auth_source, serverSelectionTimeoutMS=10000,
        directConnection=True,
    )
    del lines
    db = client[args.db]
    present = set(db.list_collection_names())
    for name in collections:
        if name not in present:
            yield name, None
            continue
        query: dict = {}
        projection = None
        if name == "Update":
            projection = {"logs": 1, "_id": 0}
            if args.since:
                ms = int(_dt.datetime.fromisoformat(args.since).replace(
                    tzinfo=_dt.timezone.utc).timestamp() * 1000)
                query = {"start_ts": {"$gte": ms}}
        else:
            projection = {"_id": 0}
        yield name, db[name].find(query, projection, batch_size=500)


# ---------------------------------------------------------------- output

def render(results: dict[str, dict | None], as_json: bool) -> str:
    if as_json:
        return json.dumps(results, indent=2, sort_keys=True)
    cols = ["docs", "compose_config_logs", "redacted_markers", *CATEGORIES, "docs_with_hits"]
    rows = [["collection", *cols]]
    for name, st in results.items():
        if st is None:
            rows.append([name, *(["absent"] + ["-"] * (len(cols) - 1))])
        else:
            rows.append([name, *[str(st.get(c, "-")) for c in cols]])
    w = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    lines = ["  ".join(v.rjust(w[i]) if i else v.ljust(w[i]) for i, v in enumerate(r)) for r in rows]
    total = sum(secret_total(st) for st in results.values() if st)
    lines.append(f"secret-shaped values total: {total}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Count-only secret leak scan of the Komodo Core DB. Prints counts per "
                    "collection, never matched values. Credentials: username and password "
                    "as two lines on stdin. Exit 0 clean, 1 secret-shaped values found, 2 error.",
        epilog="Runbook: docs/runbooks/komodo-leak-scan.md")
    p.add_argument("--fixture", metavar="JSON",
                   help="scan a local JSON file {collection: [docs]} instead of the DB (self-test)")
    p.add_argument("--host", default="ferretdb", help="Mongo/FerretDB host (default: ferretdb)")
    p.add_argument("--port", type=int, default=27017)
    p.add_argument("--db", default="komodo", help="database (default: komodo)")
    p.add_argument("--auth-source", default="admin")
    p.add_argument("--collections", default=",".join(DEFAULT_COLLECTIONS),
                   help="comma list (default: %(default)s)")
    p.add_argument("--since", metavar="YYYY-MM-DD[THH:MM]",
                   help="only Update records with start_ts at/after this UTC time")
    p.add_argument("--json", action="store_true", help="JSON output (counts only)")
    args = p.parse_args(argv)
    colls = [c.strip() for c in args.collections.split(",") if c.strip()]
    try:
        src = fixture_source(args.fixture, colls) if args.fixture else mongo_source(args, colls)
        results: dict[str, dict | None] = {}
        for name, docs in src:
            results[name] = None if docs is None else scan_collection(name, docs)
    except SystemExit as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # never echo driver messages verbatim (may carry a URI)
        print(f"error: {type(e).__name__}", file=sys.stderr)
        return 2
    print(render(results, args.json))
    return 1 if any(secret_total(st) for st in results.values() if st) else 0


if __name__ == "__main__":
    sys.exit(main())
