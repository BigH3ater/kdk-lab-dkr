#!/usr/bin/env python3
"""advisory-watch: weekly upstream-advisory check for every image the lab pins.

Reads the image tags from stacks/*/compose.yaml (and FROM lines of the local
Dockerfiles), maps each image repository to its upstream sources through
catalog.json, fetches what was published since the last run and emits one
logfmt line per (image, tag, advisory):

  ts=... image=... tag=... severity=... id=... verdict=... fixed_in=... url=...

verdict: Vulnerable (the pinned tag is inside the affected range), Patched
(outside it, or at/after the published fix), Unknown (the tag cannot be
compared: `latest`, a floating `16-alpine`, a digest, an RSS item, an OSV entry
with no fix event and no GHSA alias). An optional note= says why a verdict is
odd (contradictory-metadata, unfixed-or-unknown). Lines go to Loki
(job="advisory-watch", the ruler pages on Critical/High Vulnerable/Unknown) and
to stdout. `--selftest` runs the verdict unit cases offline.

Sources: GitHub repository advisories (token optional, only lifts the rate
limit), OSV /v1/query by package + pinned version, NVD CVE API 2.0 (optional,
throttled without a key), RSS/Atom feeds. Stdlib only (python:alpine has no
PyYAML), so compose files are read with a regex on `image:` lines.

State (/state/state.json) is {source_id: last_success_iso}. A source's
timestamp only advances when it was fetched without error, and nothing is
saved when the Loki push fails, so a bad run is repeated, never skipped.
Exit 0 = clean, 1 = some source failed (others saved), 2 = Loki push failed.
"""
from __future__ import annotations

import argparse, datetime as dt, email.utils, glob, json, os, re, sys, time
import urllib.error, urllib.parse, urllib.request
import xml.etree.ElementTree as ET

UA = "kdk-lab-advisory-watch/1 (+https://github.com/BigH3ater/kdk-lab-dkr)"
IMAGE_RE = re.compile(r"""^\s*image:\s*["']?([^\s"'#]+)""", re.M)
FROM_RE = re.compile(r"^\s*FROM\s+(?:--\S+\s+)*(\S+)", re.M | re.I)
VER_RE = re.compile(r"^v?(\d+(?:\.\d+)*)")
FEED_DEFAULT_FILTER = r"(?i)secur|cve-|vulnerab|advisory"
NOW = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def log(msg: str) -> None:
    print(f"level=info msg={q(msg)}", file=sys.stderr, flush=True)


def q(v) -> str:
    """logfmt value: bare when safe, else double-quoted with escapes."""
    s = "" if v is None else str(v)
    if s and re.fullmatch(r"[A-Za-z0-9_.:/@+,~*-]+", s):
        return s
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    try:
        t = dt.datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    except ValueError:
        try:
            t = email.utils.parsedate_to_datetime(s)
        except (TypeError, ValueError):
            return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


# ---- image references -------------------------------------------------------
def normalize(ref: str) -> tuple[str, str]:
    """'docker.io/library/traefik:v3.7' -> ('traefik', 'v3.7'); digest-only -> tag 'sha256:…'."""
    digest = ""
    if "@" in ref:
        ref, digest = ref.split("@", 1)
    tag = ""
    if ":" in ref.rsplit("/", 1)[-1]:
        ref, tag = ref.rsplit(":", 1)
    repo = ref.lower()
    for p in ("index.docker.io/", "docker.io/"):
        if repo.startswith(p):
            repo = repo[len(p):]
    if repo.startswith("library/"):
        repo = repo[len("library/"):]
    return repo, tag or (digest[:19] if digest else "latest")


def tree_images(repo_dir: str) -> dict[tuple[str, str], set[str]]:
    """(repo, tag) -> stacks using it, from compose image: lines and Dockerfile FROMs."""
    found: dict[tuple[str, str], set[str]] = {}
    # stacks/_template holds a placeholder image and is never deployed (same skip as renovate.json ignorePaths).
    files = [(p, IMAGE_RE) for p in glob.glob(f"{repo_dir}/stacks/*/compose.yaml") if "/stacks/_template/" not in p]
    files += [(p, FROM_RE) for p in glob.glob(f"{repo_dir}/stacks/**/Dockerfile", recursive=True) if "/stacks/_template/" not in p]
    for path, rx in files:
        stack = os.path.relpath(path, f"{repo_dir}/stacks").split(os.sep)[0]
        for ref in rx.findall(open(path, encoding="utf-8").read()):
            if "$" in ref or ref.lower() == "scratch":
                continue
            found.setdefault(normalize(ref), set()).add(stack)
    return found


# ---- versions ---------------------------------------------------------------
BIG = 10 ** 9


def tag_bounds(tag: str) -> tuple[tuple[int, ...], tuple[int, ...]] | None:
    """Version interval a tag stands for: '4.39.20' -> (4.39.20, 4.39.20); a floating
    'v3.3' or '16-alpine' -> (3.3.0, 3.3.inf) / (16.0.0, 16.inf.inf). None for
    latest/stable/digests/date tags (homebridge 2026-08-17)."""
    t = re.sub(r"-ls\d+$", "", tag)
    m = VER_RE.match(t)
    if not m or re.match(r"^\d{4}-\d{2}-\d{2}", t):
        return None
    parts = tuple(int(x) for x in m.group(1).split("."))
    if len(parts) >= 3:
        return parts, parts
    return parts + (0,) * (3 - len(parts)), parts + (BIG,) * (4 - len(parts))


def tag_version(tag: str) -> str | None:
    """Exact x.y.z[.n] version string (what OSV needs), else None."""
    b = tag_bounds(tag)
    return ".".join(map(str, b[0])) if b and b[0] == b[1] else None


def vtuple(s: str) -> tuple[int, ...] | None:
    m = VER_RE.match(s.strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def vcmp(a: tuple[int, ...], b: tuple[int, ...]) -> int:
    n = max(len(a), len(b))
    a, b = a + (0,) * (n - len(a)), b + (0,) * (n - len(b))
    return (a > b) - (a < b)


def split_range(rng: str) -> list[str]:
    """'>=v1.68.0 <= 1.75.1' or '>= 1.0.0, < 1.2.3' -> one string per condition.
    GitHub uses commas, but also plain spaces before the next operator."""
    return [c.strip() for c in re.split(r",|\s+(?=[<>=])", rng or "") if c.strip()]


def parse_range(rng: str) -> list[tuple[str, tuple[int, ...]]] | None:
    """[(op, version)] for each condition; None when empty or any part is unparsable."""
    parsed = []
    for c in split_range(rng):
        m = re.match(r"^(>=|<=|>|<|=)?\s*(v?\d+(?:\.\d+)*)", c)
        if not m:
            return None
        parsed.append((m.group(1) or "=", vtuple(m.group(2))))
    return parsed or None


def in_range(bounds, rng: str) -> bool | None:
    """Is the tag's interval inside a GHSA range like '>= 1.0.0, < 1.2.3'?
    True = every version the tag can be is affected, False = none is, None = some
    are (a floating tag straddling the fix) or the range is unparsable."""
    parsed = parse_range(rng)
    if bounds is None or not parsed:
        return None

    def ok(v):
        return all({">=": r >= 0, "<=": r <= 0, ">": r > 0, "<": r < 0, "=": r == 0}[op]
                   for op, x in parsed for r in [vcmp(v, x)])
    lo, hi = bounds
    if ok(lo) and ok(hi):
        return True
    # Disjoint when the range ends below the tag's interval or starts above it.
    below = any(op in ("<", "<=", "=") and (vcmp(lo, x) > 0 or (op == "<" and vcmp(lo, x) == 0))
                for op, x in parsed)
    above = any(op in (">", ">=", "=") and (vcmp(hi, x) < 0 or (op == ">" and vcmp(hi, x) == 0))
                for op, x in parsed)
    return False if below or above else None


def entry_verdict(bounds, rng: str, patched: str | None) -> tuple[bool | None, str]:
    """in_range() for one (vulnerable range, patched versions) pair, plus the fix:
    a tag at or after the patched version is not affected even when the range is
    open-ended ('>= 1.75.0' patched '1.75.2'). When the range's own upper bound
    also covers the fix ('<= v2.36.1' patched '>= v2.36.1') the metadata
    contradicts itself: keep the range's answer and say so."""
    r = in_range(bounds, rng)
    fixes = [vtuple(x) for x in re.findall(r"v?\d+(?:\.\d+)*", patched or "")]
    if r is False or bounds is None or not fixes:
        return r, ""
    fix = max(fixes)                  # several lines listed: only the newest is safe for all
    if vcmp(bounds[0], fix) < 0:
        return r, ""
    if any(op in ("<", "<=", "=") for op, _ in parse_range(rng) or []):
        return r, "contradictory-metadata"
    return False, ""


def judge(bounds, entries: list[tuple[str, str]]) -> tuple[str, str, str]:
    """(verdict, fixed_in, note) for an advisory given its [(range, patched)] entries."""
    res = [entry_verdict(bounds, rng, pat) for rng, pat in entries]
    verdict = ("Vulnerable" if any(r is True for r, _ in res) else
               "Patched" if res and all(r is False for r, _ in res) else "Unknown")
    all_fixes = sorted({p for _, p in entries if p})
    hit = [p for (_, p), (r, _) in zip(entries, res) if r is not False and p]
    note = "contradictory-metadata" if any(r is not False and n for r, n in res) else ""
    return verdict, "|".join(sorted(set(hit or all_fixes))[:4]), note


def osv_entries(affected: list[dict], pkg: str) -> list[tuple[str, str]]:
    """OSV affected[].ranges[].events -> [(GHSA-style range, fixed)] for `pkg`
    (all packages when none matches). GIT ranges (commit hashes) are skipped."""
    aff = [a for a in affected if (a.get("package") or {}).get("name") == pkg] or affected
    out = []
    for a in aff:
        for r in a.get("ranges") or []:
            if r.get("type") not in ("SEMVER", "ECOSYSTEM"):
                continue
            lo = None
            for ev in r.get("events") or []:
                if "introduced" in ev:
                    if lo is not None:
                        out.append((f">= {lo}", ""))
                    lo = ev["introduced"]
                elif "fixed" in ev:
                    out.append((f">= {lo or 0}, < {ev['fixed']}", ev["fixed"]))
                    lo = None
                elif "last_affected" in ev:
                    out.append((f">= {lo or 0}, <= {ev['last_affected']}", ""))
                    lo = None
            if lo is not None:
                out.append((f">= {lo}", ""))
    return out


def sev(s: str | None) -> str:
    s = (s or "").strip().lower()
    return {"moderate": "Medium"}.get(s, s.capitalize() if s else "Unknown")


# ---- HTTP -------------------------------------------------------------------
def http(url: str, data: bytes | None = None, headers: dict | None = None, tries: int = 3):
    h = {"User-Agent": UA, "Accept": "application/json"}
    h.update(headers or {})
    last: Exception | None = None
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=h), timeout=40) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            last = e
            reset = e.headers.get("X-RateLimit-Reset")
            if e.code in (403, 429) and e.headers.get("X-RateLimit-Remaining") == "0" and reset:
                wait = max(0, int(reset) - int(time.time())) + 5
                if wait <= 3900:   # unauthenticated GitHub: 60/h; wait for the window
                    log(f"rate limited by {urllib.parse.urlsplit(url).netloc}, sleeping {wait}s")
                    time.sleep(wait)
                    continue
            if e.code in (403, 429, 500, 502, 503, 504):
                time.sleep(10 * (attempt + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"{url}: {last}")


def gh_headers() -> dict:
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    tok = os.environ.get("GITHUB_TOKEN", "").strip()
    if tok and not tok.startswith("op://"):
        h["Authorization"] = f"Bearer {tok}"
    return h


# ---- sources: each returns a list of finding dicts --------------------------
def src_ghsa(gh: str, since: dt.datetime, tags: dict[str, tuple | None]) -> list[dict]:
    url = (f"https://api.github.com/repos/{gh}/security-advisories"
           "?per_page=100&sort=published&direction=desc")
    out = []
    advs = json.loads(http(url, headers=gh_headers()))
    GHSA_REPO[gh] = {a.get("ghsa_id"): a for a in advs}   # all of them, for OSV alias lookups
    for adv in advs:
        pub = parse_time(adv.get("published_at"))
        if not pub or pub <= since:
            continue
        entries = ghsa_entries(adv)
        for tag, bounds in tags.items():
            verdict, fixed, note = judge(bounds, entries)
            out.append({"tag": tag, "id": adv.get("ghsa_id"), "aliases": [adv.get("cve_id")],
                         "severity": sev(adv.get("severity")), "verdict": verdict,
                         "fixed_in": fixed, "url": adv.get("html_url"), "note": note,
                         "title": adv.get("summary", ""), "source": "ghsa"})
    return out


def ghsa_entries(adv: dict, pkg: str | None = None) -> list[tuple[str, str]]:
    """GitHub advisory vulnerabilities[] -> [(range, patched)], only `pkg`'s when it is listed."""
    vulns = adv.get("vulnerabilities") or []
    if pkg:
        vulns = [v for v in vulns if (v.get("package") or {}).get("name") == pkg] or vulns
    return [(v.get("vulnerable_version_range") or "", v.get("patched_versions") or "") for v in vulns]


GHSA_REPO: dict[str, dict[str, dict]] = {}   # github repo -> {ghsa_id: advisory}, filled by src_ghsa
OSV_VULN: dict[str, dict | None] = {}        # OSV id -> record (None = fetch failed)


def osv_vuln(vid: str) -> dict | None:
    if vid not in OSV_VULN:
        try:
            OSV_VULN[vid] = json.loads(http(f"https://api.osv.dev/v1/vulns/{vid}"))
        except Exception as e:
            log(f"osv lookup {vid} failed: {e}")
            OSV_VULN[vid] = None
    return OSV_VULN[vid]


def judge_osv(v: dict, pkg: str, ver: str, gh: str | None) -> dict:
    """Verdict fields for one OSV hit. OSV's Go entries often carry only an
    `introduced` event, which OSV reads as 'every later version'; the GHSA alias
    usually has the real fix, so it wins: the repo's advisory list when the
    catalog has `github`, else the OSV mirror of the GHSA record. No alias and
    no fix event = Unknown (note=unfixed-or-unknown), not Vulnerable."""
    bounds = tag_bounds(ver)
    for alias in v.get("aliases") or []:
        if not alias.startswith("GHSA-"):
            continue
        adv = GHSA_REPO.get(gh or "", {}).get(alias)
        entries = ghsa_entries(adv, pkg) if adv else []
        if not entries:
            g = osv_vuln(alias)
            entries = osv_entries(g.get("affected") or [], pkg) if g else []
        if entries:
            verdict, fixed, note = judge(bounds, entries)
            return {"verdict": verdict, "fixed_in": fixed, "note": note, "source": "osv+ghsa"}
    entries = osv_entries(v.get("affected") or [], pkg)
    if not any(parse_range(r) and any(op in ("<", "<=", "=") for op, _ in parse_range(r))
               for r, _ in entries):
        return {"verdict": "Unknown", "fixed_in": "", "note": "unfixed-or-unknown", "source": "osv"}
    verdict, fixed, note = judge(bounds, entries)
    return {"verdict": verdict, "fixed_in": fixed, "note": note, "source": "osv"}


def osv_severity(v: dict) -> str:
    s = (v.get("database_specific") or {}).get("severity")
    if s:
        return sev(s)
    for alias in v.get("aliases") or []:
        if alias.startswith("GHSA-"):
            g = osv_vuln(alias)
            return sev((g.get("database_specific") or {}).get("severity")) if g else "Unknown"
    return "Unknown"


def src_osv(eco: str, pkg: str, tag: str, ver: str, since: dt.datetime | None,
            gh: str | None = None) -> list[dict]:
    body = {"package": {"ecosystem": eco, "name": pkg}, "version": ver}
    vulns, token = [], None
    while True:
        if token:
            body["page_token"] = token
        res = json.loads(http("https://api.osv.dev/v1/query", data=json.dumps(body).encode(),
                              headers={"Content-Type": "application/json"}))
        vulns += res.get("vulns") or []
        token = res.get("next_page_token")
        if not token:
            break
    out = []
    for v in vulns:
        pub = parse_time(v.get("published"))
        if since and pub and pub <= since:
            continue
        out.append({"tag": tag, "id": v["id"], "aliases": v.get("aliases") or [],
                    "severity": osv_severity(v), **judge_osv(v, pkg, ver, gh),
                    "url": f"https://osv.dev/vulnerability/{v['id']}",
                    "title": v.get("summary", "")})
    return out


def src_nvd(cpe: str, tags: dict[str, str | None], since: dt.datetime) -> list[dict]:
    """New CVEs for a product CPE (vendor:product, any version). Always verdict Unknown:
    image tags rarely equal the product version (wrapper images, floating tags)."""
    start = max(since, NOW - dt.timedelta(days=119))   # NVD caps the window at 120 days
    fmt = "%Y-%m-%dT%H:%M:%S.000"
    params = {"virtualMatchString": cpe, "pubStartDate": start.strftime(fmt),
              "pubEndDate": NOW.strftime(fmt), "resultsPerPage": "200"}
    key = os.environ.get("NVD_API_KEY", "").strip()
    time.sleep(1 if key else 6.5)   # 5 requests / 30 s without a key
    url = "https://services.nvd.nist.gov/rest/json/cves/2.0?" + urllib.parse.urlencode(params)
    res = json.loads(http(url, headers={"apiKey": key} if key else None))
    out = []
    for item in res.get("vulnerabilities") or []:
        cve = item.get("cve") or {}
        m = cve.get("metrics") or {}
        s = next((sev(m[k][0]["cvssData"].get("baseSeverity"))
                  for k in ("cvssMetricV40", "cvssMetricV31", "cvssMetricV30") if m.get(k)), "Unknown")
        desc = next((d["value"] for d in cve.get("descriptions") or [] if d.get("lang") == "en"), "")
        for tag in tags:
            out.append({"tag": tag, "id": cve.get("id"), "aliases": [], "severity": s,
                        "verdict": "Unknown", "fixed_in": "",
                        "url": f"https://nvd.nist.gov/vuln/detail/{cve.get('id')}",
                        "title": desc[:160], "source": "nvd"})
    return out


def src_feed(url: str, rx: str, tags: dict[str, str | None], since: dt.datetime) -> list[dict]:
    root = ET.fromstring(http(url, headers={"Accept": "application/rss+xml, application/atom+xml, */*"}))
    atom = "{http://www.w3.org/2005/Atom}"
    items = []
    for it in root.iter("item"):
        items.append((it.findtext("title"), it.findtext("link"), it.findtext("pubDate"), it.findtext("guid")))
    for it in root.iter(f"{atom}entry"):
        link = it.find(f"{atom}link")
        items.append((it.findtext(f"{atom}title"), link.get("href") if link is not None else "",
                      it.findtext(f"{atom}published") or it.findtext(f"{atom}updated"), it.findtext(f"{atom}id")))
    out = []
    for title, link, when, guid in items:
        t = parse_time(when)
        if not t or t <= since or not re.search(rx, title or ""):
            continue
        for tag in tags:
            out.append({"tag": tag, "id": (guid or link or title or "")[:120], "aliases": [],
                        "severity": "Unknown", "verdict": "Unknown", "fixed_in": "", "url": link,
                        "title": (title or "").strip()[:160], "source": "feed"})
    return out


# ---- Loki / Pushover ----------------------------------------------------------
def loki_push(url: str, lines: list[str]) -> None:
    base = time.time_ns()
    for i in range(0, len(lines), 500):
        chunk = lines[i:i + 500]
        body = {"streams": [{"stream": {"job": "advisory-watch"},
                             "values": [[str(base + i + n), ln] for n, ln in enumerate(chunk)]}]}
        http(url.rstrip("/") + "/loki/api/v1/push", data=json.dumps(body).encode(),
             headers={"Content-Type": "application/json"}, tries=3)


def pushover(title: str, msg: str) -> None:
    env = {}
    try:
        for ln in open(os.environ.get("PUSHOVER_FILE", "/pushover.env"), encoding="utf-8"):
            k, _, v = ln.strip().removeprefix("export ").partition("=")
            env[k.strip()] = v.strip().strip("'\"")
    except OSError:
        return
    if env.get("PUSHOVER_TOKEN") and env.get("PUSHOVER_USER"):
        data = urllib.parse.urlencode({"token": env["PUSHOVER_TOKEN"], "user": env["PUSHOVER_USER"],
                                       "title": title, "message": msg[:1000], "priority": 0}).encode()
        try:
            http("https://api.pushover.net/1/messages.json", data=data, tries=2)
        except Exception as e:  # never fail the run on the fallback channel
            log(f"pushover failed: {e}")


# ---- main -------------------------------------------------------------------
def merge(findings: dict, repo: str, f: dict, stacks) -> None:
    """Keep one line per (image, tag, advisory): the same advisory reached via
    GHSA and OSV (shared id or alias) is emitted once, the ghsa record preferred;
    between two non-ghsa records the first non-Unknown one wins."""
    f = {"note": "", **f}
    keys = {f["id"], *[x for x in f["aliases"] if x]}
    prev = next((k for k in findings if k[0] == repo and k[1] == f["tag"] and keys & k[2]), None)
    if prev:
        old = findings[prev]
        if old["source"] == "ghsa" and f["source"] != "ghsa":
            return
        if f["source"] != "ghsa" and old["verdict"] != "Unknown":
            return
        keys |= prev[2]
        findings.pop(prev)
    findings[(repo, f["tag"], frozenset(keys))] = {**f, "stacks": stacks}


def parse_only(values: list[str]) -> set[str]:
    """--only a,b --only c -> {a, b, c}, each normalised like a compose image ref."""
    return {normalize(x.strip())[0] for v in values for x in v.split(",") if x.strip()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", default=os.environ.get("REPO", "/repo"))
    ap.add_argument("--catalog", default=os.environ.get("CATALOG"))
    ap.add_argument("--state", default=os.environ.get("STATE", "/state/state.json"))
    ap.add_argument("--loki", default=os.environ.get("LOKI_URL", ""))
    ap.add_argument("--dry-run", action="store_true", help="print only: no Loki push, no state write")
    ap.add_argument("--limit", type=int, default=0, help="check only the first N catalog images")
    ap.add_argument("--only", action="append", default=[],
                    help="check only these catalog keys (comma-separated and/or repeated)")
    ap.add_argument("--first-run-days", type=int, default=int(os.environ.get("FIRST_RUN_DAYS", "30")))
    ap.add_argument("--selftest", action="store_true", help="run the offline verdict unit cases and exit")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    catalog = json.load(open(a.catalog or f"{a.repo}/stacks/advisory-watch/catalog.json"))["images"]
    state = {}
    if os.path.exists(a.state):
        state = json.load(open(a.state))
    new_state, findings, errors = dict(state), {}, 0
    feed_rx = os.environ.get("FEED_FILTER", FEED_DEFAULT_FILTER)
    default_since = NOW - dt.timedelta(days=a.first_run_days)

    by_repo: dict[str, dict[str, set[str]]] = {}
    for (repo, tag), stacks in tree_images(a.repo).items():
        by_repo.setdefault(repo, {})[tag] = stacks
    only = parse_only(a.only)
    for o in sorted(only - set(by_repo)):
        log(f"--only {o}: no stack uses this image repository")
    repos = [r for r in sorted(by_repo) if (not only or r in only)]
    checked = 0
    for repo in repos:
        ent = catalog.get(repo)
        if ent is None:
            log(f"no catalog entry for {repo} (scripts/advisory-catalog-check.py)")
            continue
        if ent.get("local") and not ent.get("github"):
            continue
        if a.limit and checked >= a.limit:
            break
        checked += 1
        use_tag = ent.get("tag_is_version", True)   # false for wrapper images (tag != upstream version)
        tags = {t: (tag_bounds(t) if use_tag else None) for t in by_repo[repo]}
        jobs = []
        if ent.get("github"):
            jobs.append((f"ghsa:{ent['github']}", None, lambda s: src_ghsa(ent["github"], s, tags)))
        if ent.get("osv"):
            o = ent["osv"]
            for tag in tags:
                ver = tag_version(tag) if use_tag else None
                if ver:   # OSV matches by exact version only; the first run returns all
                    jobs.append((f"osv:{o['ecosystem']}:{o['package']}@{ver}", "all",
                                 lambda s, t=tag, v=ver: src_osv(o["ecosystem"], o["package"], t, v, s,
                                                                     ent.get("github"))))
        cpes = ent.get("nvd_cpe") or []
        for cpe in [cpes] if isinstance(cpes, str) else cpes:
            if os.environ.get("NVD_DISABLE") != "1":
                jobs.append((f"nvd:{cpe}", None, lambda s, c=cpe: src_nvd(c, tags, s)))
        if ent.get("feed"):
            jobs.append((f"feed:{ent['feed']}", None,
                         lambda s: src_feed(ent["feed"], ent.get("feed_filter", feed_rx), tags, s)))
        for sid, first, fn in jobs:
            since = parse_time(state.get(sid)) or (None if first == "all" else default_since)
            try:
                res = fn(since)
            except Exception as e:
                errors += 1
                print(f"level=error source={q(sid)} image={q(repo)} err={q(e)}", flush=True)
                continue
            new_state[sid] = iso(NOW)
            for f in res:
                merge(findings, repo, f, by_repo[repo][f["tag"]])

    lines = []
    for (repo, tag, _), f in sorted(findings.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[1]["id"])):
        lines.append(" ".join(f"{k}={q(v)}" for k, v in [
            ("ts", iso(NOW)), ("image", repo), ("tag", tag), ("severity", f["severity"]), ("id", f["id"]),
            ("verdict", f["verdict"]), ("fixed_in", f["fixed_in"]), ("source", f["source"]),
            *([("note", f["note"])] if f["note"] else []),
            ("stacks", ",".join(sorted(f["stacks"]))), ("url", f["url"]), ("title", f["title"][:160])]))
    count = lambda v: sum(1 for f in findings.values() if f["verdict"] == v)
    lines.append(f"ts={iso(NOW)} summary=1 images_checked={checked} advisories={len(findings)} "
                 f"vulnerable={count('Vulnerable')} unknown={count('Unknown')} patched={count('Patched')} "
                 f"source_errors={errors}")
    for ln in lines:
        print(ln, flush=True)
    if a.dry_run:
        return 1 if errors else 0
    if a.loki:
        try:
            loki_push(a.loki, lines)
        except Exception as e:
            print(f"level=error msg=\"loki push failed\" err={q(e)}", flush=True)
            hot = [f for f in findings.values() if f["verdict"] in ("Vulnerable", "Unknown")
                   and f["severity"] in ("Critical", "High")]
            pushover("kdk advisory-watch: Loki push failed",
                     f"{len(hot)} Critical/High advisories not delivered to Loki:\n" +
                     "\n".join(f"{f['id']} {f['severity']} {f['verdict']} ({f['tag']})" for f in hot[:15]))
            return 2
    os.makedirs(os.path.dirname(a.state) or ".", exist_ok=True)
    tmp = a.state + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(new_state, fh, indent=1, sort_keys=True)
    os.replace(tmp, a.state)
    return 1 if errors else 0


# ---- self-test (offline, canned upstream JSON) -------------------------------
def selftest() -> int:
    def offline(*_a, **_k):
        raise RuntimeError("selftest is offline")
    globals()["http"] = offline
    GHSA_REPO.clear()
    OSV_VULN.clear()

    def ex(v):
        return tag_bounds(v)

    def osv_rec(vid, pkg, events, aliases=()):
        return {"id": vid, "aliases": list(aliases),
                "affected": [{"package": {"name": pkg}, "ranges": [{"type": "SEMVER", "events": events}]}]}

    # vikunja 2.6.0: GO-2026-6749 only says introduced 0.24.0; GHSA-rj9j (repo list) is patched in 2.4.0.
    GHSA_REPO["go-vikunja/vikunja"] = {"GHSA-rj9j-8772-4h6c": {"ghsa_id": "GHSA-rj9j-8772-4h6c", "vulnerabilities": [
        {"package": {"name": "code.vikunja.io/api"}, "vulnerable_version_range": ">= 0.24.0", "patched_versions": "2.4.0"}]}}
    vik = osv_rec("GO-2026-6749", "code.vikunja.io/api", [{"introduced": "0.24.0"}], ["GHSA-rj9j-8772-4h6c", "CVE-2026-0001"])
    # trivy 0.76.1: GO-2026-4919 says introduced 0.69.4; the GHSA (OSV mirror) is =0.69.4, <0.35.0, <0.2.6.
    OSV_VULN["GHSA-trvy-0000-0000"] = {"id": "GHSA-trvy-0000-0000", "affected": [
        {"package": {"name": "github.com/aquasecurity/trivy"}, "ranges": [
            {"type": "ECOSYSTEM", "events": [{"introduced": "0.69.4"}, {"last_affected": "0.69.4"}]},
            {"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "0.35.0"}]},
            {"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "0.2.6"}]}]}]}
    trv = osv_rec("GO-2026-4919", "github.com/aquasecurity/trivy", [{"introduced": "0.69.4"}], ["GHSA-trvy-0000-0000"])
    cdn = osv_rec("GO-2026-0002", "github.com/coredns/coredns", [{"introduced": "0"}, {"fixed": "1.14.3"}])
    bare = osv_rec("GO-2026-0003", "example.org/x", [{"introduced": "1.0.0"}])
    gone = osv_rec("GO-2026-0004", "example.org/x", [{"introduced": "1.0.0"}], ["GHSA-none-0000-0000"])

    dd: dict = {}
    merge(dd, "img", {"tag": "1", "id": "GHSA-aaaa", "aliases": ["CVE-1"], "verdict": "Patched", "source": "ghsa"}, set())
    merge(dd, "img", {"tag": "1", "id": "GO-1", "aliases": ["GHSA-aaaa"], "verdict": "Vulnerable", "source": "osv+ghsa"}, set())
    dd2: dict = {}
    merge(dd2, "img", {"tag": "1", "id": "GO-1", "aliases": ["GHSA-aaaa"], "verdict": "Unknown", "source": "osv"}, set())
    merge(dd2, "img", {"tag": "1", "id": "GHSA-aaaa", "aliases": [None], "verdict": "Patched", "source": "ghsa"}, set())

    cases = [
        ("split space-separated", split_range(">=v1.68.0 <= 1.75.1"), [">=v1.68.0", "<= 1.75.1"]),
        ("split comma", split_range(">= 1.0.0, < 1.2.3"), [">= 1.0.0", "< 1.2.3"]),
        ("split exact", split_range("=0.69.4"), ["=0.69.4"]),
        ("in_range rclone 1.75.2 vs >=v1.68.0 <= 1.75.1", in_range(ex("1.75.2"), ">=v1.68.0 <= 1.75.1"), False),
        ("in_range rclone 1.75.1 vs >=v1.68.0 <= 1.75.1", in_range(ex("1.75.1"), ">=v1.68.0 <= 1.75.1"), True),
        ("in_range traefik 3.3.7 vs < 3.6.14", in_range(ex("v3.3.7"), "< 3.6.14"), True),
        ("in_range trivy 0.76.1 vs =0.69.4", in_range(ex("0.76.1"), "=0.69.4"), False),
        ("in_range floating v3.3 straddles < 3.3.5", in_range(ex("v3.3"), ">= 3.0.0, < 3.3.5"), None),
        ("rclone GHSA-35g6 1.75.2", judge(ex("1.75.2"), [(">=v1.68.0 <= 1.75.1", "1.75.2")])[0], "Patched"),
        ("rclone GHSA-qjw8 1.75.2 (open range, patched 1.75.2)", judge(ex("1.75.2"), [(">= 1.75.0", "1.75.2")])[0], "Patched"),
        ("rclone GHSA-qjw8 1.75.1 still vulnerable", judge(ex("1.75.1"), [(">= 1.75.0", "1.75.2")])[0], "Vulnerable"),
        ("ABS 2.36.1 contradictory", judge(ex("2.36.1"), [("<= v2.36.1", ">= v2.36.1")]),
         ("Vulnerable", ">= v2.36.1", "contradictory-metadata")),
        ("traefik 3.3.7 vs < 3.6.14", judge(ex("v3.3.7"), [("< 3.6.14", "3.6.14")]), ("Vulnerable", "3.6.14", "")),
        ("trivy 0.76.1 GHSA ranges", judge(ex("0.76.1"), [("=0.69.4", ""), ("<0.35.0", "0.35.0"), ("<0.2.6", "0.2.6")])[0], "Patched"),
        ("osv_entries events", osv_entries(cdn["affected"], "github.com/coredns/coredns"), [(">= 0, < 1.14.3", "1.14.3")]),
        ("vikunja 2.6.0 via repo GHSA alias", judge_osv(vik, "code.vikunja.io/api", "2.6.0", "go-vikunja/vikunja"),
         {"verdict": "Patched", "fixed_in": "2.4.0", "note": "", "source": "osv+ghsa"}),
        ("vikunja 2.3.0 via repo GHSA alias", judge_osv(vik, "code.vikunja.io/api", "2.3.0", "go-vikunja/vikunja")["verdict"], "Vulnerable"),
        ("trivy 0.76.1 via OSV-mirror GHSA alias", judge_osv(trv, "github.com/aquasecurity/trivy", "0.76.1", None)[
            "verdict"], "Patched"),
        ("coredns 1.12.0 vs fixed 1.14.3", judge_osv(cdn, "github.com/coredns/coredns", "1.12.0", None),
         {"verdict": "Vulnerable", "fixed_in": "1.14.3", "note": "", "source": "osv"}),
        ("osv no fix, no alias", judge_osv(bare, "example.org/x", "1.2.0", None),
         {"verdict": "Unknown", "fixed_in": "", "note": "unfixed-or-unknown", "source": "osv"}),
        ("osv no fix, alias unresolvable", judge_osv(gone, "example.org/x", "1.2.0", None)["note"], "unfixed-or-unknown"),
        ("dedupe: ghsa first, osv dropped", [f["source"] for f in dd.values()], ["ghsa"]),
        ("dedupe: osv first, ghsa replaces", [f["source"] for f in dd2.values()], ["ghsa"]),
        ("--only comma list", parse_only(["vikunja/vikunja,rclone/rclone", "docker.io/library/traefik"]),
         {"vikunja/vikunja", "rclone/rclone", "traefik"}),
    ]
    failed = 0
    for name, got, want in cases:
        ok = got == want
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'} {name}" + ("" if ok else f": got {got!r}, want {want!r}"))
    print(f"selftest: {len(cases) - failed}/{len(cases)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
