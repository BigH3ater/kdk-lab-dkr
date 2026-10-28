#!/usr/bin/env python3
"""advisory-watch: weekly upstream-advisory check for every image the lab pins.

Reads the image tags from stacks/*/compose.yaml (and FROM lines of the local
Dockerfiles), maps each image repository to its upstream sources through
catalog.json, fetches what was published since the last run and emits one
logfmt line per (image, tag, advisory):

  ts=... image=... tag=... severity=... id=... verdict=... fixed_in=... url=...

verdict: Vulnerable (the pinned tag is inside the affected range), Patched
(outside it), Unknown (the tag cannot be compared: `latest`, a floating
`16-alpine`, a digest, an RSS item). Lines go to Loki (job="advisory-watch",
the ruler pages on Critical/High Vulnerable/Unknown) and to stdout.

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


def in_range(bounds, rng: str) -> bool | None:
    """Is the tag's interval inside a GHSA range like '>= 1.0.0, < 1.2.3'?
    True = every version the tag can be is affected, False = none is, None = some
    are (a floating tag straddling the fix) or the range is unparsable."""
    conds = [c.strip() for c in (rng or "").split(",") if c.strip()]
    if bounds is None or not conds:
        return None
    parsed = []
    for c in conds:
        m = re.match(r"^(>=|<=|>|<|=)?\s*(v?\d+(?:\.\d+)*)", c)
        if not m:
            return None
        parsed.append((m.group(1) or "=", vtuple(m.group(2))))

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
    for adv in json.loads(http(url, headers=gh_headers())):
        pub = parse_time(adv.get("published_at"))
        if not pub or pub <= since:
            continue
        vulns = adv.get("vulnerabilities") or []
        all_fixes = sorted({v["patched_versions"] for v in vulns if v.get("patched_versions")})
        for tag, bounds in tags.items():
            res = [in_range(bounds, v.get("vulnerable_version_range")) for v in vulns]
            verdict = ("Vulnerable" if any(r is True for r in res) else
                       "Patched" if res and all(r is False for r in res) else "Unknown")
            hit = [v.get("patched_versions") for v, r in zip(vulns, res) if r is not False]
            fixed = [f for f in hit if f] or all_fixes   # the fix for this tag's line, else all lines
            out.append({"tag": tag, "id": adv.get("ghsa_id"), "aliases": [adv.get("cve_id")],
                         "severity": sev(adv.get("severity")), "verdict": verdict,
                         "fixed_in": "|".join(sorted(set(fixed))[:4]), "url": adv.get("html_url"),
                         "title": adv.get("summary", ""), "source": "ghsa"})
    return out


def osv_severity(v: dict) -> str:
    s = (v.get("database_specific") or {}).get("severity")
    if s:
        return sev(s)
    for alias in v.get("aliases") or []:
        if alias.startswith("GHSA-"):
            try:
                g = json.loads(http(f"https://api.osv.dev/v1/vulns/{alias}"))
                return sev((g.get("database_specific") or {}).get("severity"))
            except Exception:
                break
    return "Unknown"


def src_osv(eco: str, pkg: str, tag: str, ver: str, since: dt.datetime | None) -> list[dict]:
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
        fixes = sorted({ev["fixed"] for a in v.get("affected") or []
                        if (a.get("package") or {}).get("name") == pkg
                        for r in a.get("ranges") or [] for ev in r.get("events") or [] if "fixed" in ev},
                       key=lambda f: vtuple(f) or (0,))
        later = [f for f in fixes if vtuple(f) and vcmp(vtuple(f), vtuple(ver)) > 0]
        out.append({"tag": tag, "id": v["id"], "aliases": v.get("aliases") or [],
                    "severity": osv_severity(v), "verdict": "Vulnerable",
                    "fixed_in": (later or fixes or [""])[0],
                    "url": f"https://osv.dev/vulnerability/{v['id']}",
                    "title": v.get("summary", ""), "source": "osv"})
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
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", default=os.environ.get("REPO", "/repo"))
    ap.add_argument("--catalog", default=os.environ.get("CATALOG"))
    ap.add_argument("--state", default=os.environ.get("STATE", "/state/state.json"))
    ap.add_argument("--loki", default=os.environ.get("LOKI_URL", ""))
    ap.add_argument("--dry-run", action="store_true", help="print only: no Loki push, no state write")
    ap.add_argument("--limit", type=int, default=0, help="check only the first N catalog images")
    ap.add_argument("--only", action="append", default=[], help="check only this image repository")
    ap.add_argument("--first-run-days", type=int, default=int(os.environ.get("FIRST_RUN_DAYS", "30")))
    a = ap.parse_args()
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
    repos = [r for r in sorted(by_repo) if (not a.only or r in a.only)]
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
                                 lambda s, t=tag, v=ver: src_osv(o["ecosystem"], o["package"], t, v, s)))
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
            for f in res:   # one line per (tag, advisory); the same id via GHSA+OSV is kept once
                keys = {f["id"], *[x for x in f["aliases"] if x]}
                prev = next((k for k in findings if k[0] == repo and k[1] == f["tag"] and keys & k[2]), None)
                if prev and findings[prev]["verdict"] != "Unknown":
                    continue
                findings.pop(prev, None)
                findings[(repo, f["tag"], frozenset(keys))] = {**f, "stacks": by_repo[repo][f["tag"]]}

    lines = []
    for (repo, tag, _), f in sorted(findings.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[1]["id"])):
        lines.append(" ".join(f"{k}={q(v)}" for k, v in [
            ("ts", iso(NOW)), ("image", repo), ("tag", tag), ("severity", f["severity"]), ("id", f["id"]),
            ("verdict", f["verdict"]), ("fixed_in", f["fixed_in"]), ("source", f["source"]),
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


if __name__ == "__main__":
    sys.exit(main())
