"""admin.kmkdp.com: the lab admin page (jmack only), served by this sidecar.

Read-only. Every source is fetched server-side by a cached Feed (app.py) with a
timeout; the browser never talks to Prometheus, Alertmanager, Loki, Home
Assistant, Komodo or GitHub. This module holds the fetchers, the parsers and the
HTML; app.py owns the feeds, the warmer, the host gate and the routes.

Sources (all verified read-only on 2026-11-10 unless marked):
  alerts      Alertmanager v2 API on kdk-mon-01 :9093 (firing, not silenced)
  probes      Prometheus on kdk-mon-01 :9090: probe_success + up
  backups     Loki on kdk-log-01: docker-event-watch "action=die code=N" lines
              of the backup-* one-shots (the Grafana "Backups" dashboard source)
  komodo      Komodo Core /read ListUpdates (needs a read-only API key; the
              tile says "not configured" until KOMODO_API_KEY/SECRET exist;
              not exercised live: no key yet)
  github      GitHub REST /pulls (public repo, no token, cached 15 min)
  advisories  Loki job="advisory-watch": the weekly run's summary line
  ups         Home Assistant NUT sensors (Prometheus has no NUT exporter)
  charts      Prometheus query_range; UPS load from HA history
  signins     Loki: Authelia's JSON log on kdk-dkr-dmz-01
  syslog      Loki {job="syslog"}, on demand only (search box)
"""
from __future__ import annotations

import datetime
import json
import re
import time
import urllib.parse

import requests
from markupsafe import escape

UA = "kdk-dashboard-admin/1 (+https://github.com/BigH3ater/kdk-lab-dkr)"
DAY = 86400


# ---- small helpers ---------------------------------------------------------------
class UpstreamError(RuntimeError):
    """An HTTP error status from a source, with the start of its response body
    (Loki puts the reason, e.g. a parse error or a limit, in the body)."""

    def __init__(self, status: int, body: str):
        self.status, self.body = status, (body or "").strip()[:500]
        super().__init__(f"HTTP {status}")


def _get_json(url: str, timeout: float = 6, **kw):
    r = requests.get(url, timeout=timeout, headers={"User-Agent": UA, **kw.pop("headers", {})}, **kw)
    if r.status_code >= 400:
        raise UpstreamError(r.status_code, r.text)
    return r.json()


def ago(sec: float) -> str:
    sec = max(0, int(sec))
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m"
    if sec < 2 * DAY:
        return f"{sec // 3600}h"
    return f"{sec // DAY}d"


def parse_ts(v) -> float | None:
    """RFC 3339 (Alertmanager, HA, Authelia) -> epoch seconds."""
    if not v:
        return None
    s = str(v).strip().replace("Z", "+00:00")
    m = re.match(r"^(.*T\d\d:\d\d:\d\d)(\.\d+)?(.*)$", s)   # fromisoformat wants <= 6 fraction digits
    if m:
        s = m.group(1) + (m.group(2) or "")[:7] + m.group(3)
    try:
        dt = datetime.datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.timestamp()


# ---- Prometheus / Loki clients ---------------------------------------------------
def prom_query(base: str, q: str, timeout: float = 6) -> list[dict]:
    d = _get_json(f"{base}/api/v1/query", timeout, params={"query": q})
    if d.get("status") != "success":
        raise RuntimeError("prometheus query failed")
    return d["data"]["result"]


def prom_range(base: str, q: str, start: float, end: float, step: int, timeout: float = 8) -> list[tuple[float, float]]:
    d = _get_json(f"{base}/api/v1/query_range", timeout,
                  params={"query": q, "start": f"{start:.0f}", "end": f"{end:.0f}", "step": str(step)})
    res = d.get("data", {}).get("result") or []
    if not res:
        return []
    out = []
    for ts, v in res[0].get("values") or []:
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f == f:                       # drop NaN
            out.append((float(ts), f))
    return out


def loki_range(base: str, q: str, seconds: int, limit: int, timeout: float = 10, now: float | None = None) -> list[tuple[int, dict, str]]:
    """[(ts_ns, stream labels, line)] newest first."""
    now = now or time.time()
    d = _get_json(f"{base}/loki/api/v1/query_range", timeout,
                  params={"query": q, "start": str(int((now - seconds) * 1e9)), "end": str(int(now * 1e9)),
                          "limit": str(limit), "direction": "backward"})
    out = []
    for s in (d.get("data") or {}).get("result") or []:
        for ts, line in s.get("values") or []:
            out.append((int(ts), s.get("stream") or {}, line))
    out.sort(key=lambda x: -x[0])
    return out[:limit]


# ---- LogQL literal escaping (syslog search) --------------------------------------
QUERY_MAX = 200
_RE2_META = set("\\.+*?()|[]{}^$")
HOST_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,62}$")
RANGES = {"1h": 3600, "6h": 6 * 3600, "24h": DAY}
RANGE_LABEL = {"1h": "Last 1 hour", "6h": "Last 6 hours", "24h": "Last 24 hours"}


def logql_string(s: str) -> str:
    """A LogQL double-quoted string literal (Go strconv.Unquote rules): only
    backslash and the double quote need escaping once control characters are
    refused (clean_query does that)."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def re2_literal(s: str) -> str:
    """Match `s` literally inside an RE2 regex (every metacharacter escaped)."""
    return "".join("\\" + c if c in _RE2_META else c for c in s)


def clean_query(q: str | None) -> str:
    q = (q or "").strip()
    if len(q) > QUERY_MAX:
        raise ValueError(f"Search text is limited to {QUERY_MAX} characters.")
    if any(ord(c) < 32 or ord(c) == 127 for c in q):
        raise ValueError("Search text cannot contain control characters.")
    return q


def syslog_logql(q: str, host: str = "") -> str:
    """{job="syslog"[,host="h"]} |~ "(?i)<literal q>". The user text only ever
    lands inside a string literal, never in a label matcher or a pipeline stage."""
    sel = '{job="syslog"' + (f", host={logql_string(host)}" if host else "") + "}"
    if not q:
        return sel
    return f"{sel} |~ {logql_string('(?i)' + re2_literal(q))}"


def explore_url(grafana: str, expr: str, rng: str, ds_uid: str = "loki-dkr01") -> str:
    panes = {"a": {"datasource": ds_uid,
                   "queries": [{"refId": "A", "expr": expr, "queryType": "range", "editorMode": "code",
                                "datasource": {"type": "loki", "uid": ds_uid}}],
                   "range": {"from": f"now-{rng}", "to": "now"}}}
    return f"{grafana}/explore?" + urllib.parse.urlencode(
        {"schemaVersion": "1", "panes": json.dumps(panes, separators=(",", ":")), "orgId": "1"})


_ANSI = re.compile(r"(?:\x1b|#033)\[[0-9;]*[A-Za-z]")   # raw ESC or rsyslog's #033 escape


def search_syslog(loki: str, q: str, host: str, rng: str, limit: int = 200, timeout: float = 10) -> list[dict]:
    rows = []
    for ts, stream, line in loki_range(loki, syslog_logql(q, host), RANGES[rng], limit, timeout):
        rows.append({"ts": ts / 1e9, "host": stream.get("host") or "", "line": _ANSI.sub("", line)})
    return rows


def fetch_syslog_hosts(loki: str) -> list[str]:
    d = _get_json(f"{loki}/loki/api/v1/label/host/values", 6,
                  params={"query": '{job="syslog"}', "start": str(int((time.time() - DAY) * 1e9))})
    return sorted(h for h in d.get("data") or [] if HOST_RE.match(h))


# ---- status sources -------------------------------------------------------------
def fetch_alerts(am: str) -> list[dict]:
    raw = _get_json(f"{am}/api/v2/alerts", 6,
                    params={"active": "true", "silenced": "false", "inhibited": "false"})
    return parse_alerts(raw)


_SEV_ORDER = {"critical": 0, "warning": 1, "info": 2}


def parse_alerts(raw: list) -> list[dict]:
    out = []
    for a in raw or []:
        lab = a.get("labels") or {}
        if (a.get("status") or {}).get("state", "active") != "active":
            continue
        out.append({"name": lab.get("alertname") or "?",
                    "severity": (lab.get("severity") or "none").lower(),
                    "instance": lab.get("instance") or lab.get("host") or lab.get("job") or "",
                    "labels": lab,
                    "summary": (a.get("annotations") or {}).get("summary") or "",
                    "since": parse_ts(a.get("startsAt"))})
    out.sort(key=lambda x: (_SEV_ORDER.get(x["severity"], 3), -(x["since"] or 0)))
    return out


def am_filter_link(am_ui: str, alert: dict) -> str:
    """The alert in the Alertmanager UI (silence it there)."""
    def m(k, v):
        return f'{k}="' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'
    lab = alert.get("labels") or {}
    parts = [m("alertname", alert["name"])]
    for k in ("instance", "host", "job"):
        if lab.get(k):
            parts.append(m(k, lab[k]))
            break
    return (f"{am_ui}/#/alerts?silenced=false&inhibited=false&active=true&filter="
            + urllib.parse.quote("{" + ",".join(parts) + "}", safe=""))


def fetch_probes(prom: str) -> dict:
    def rows(q):
        return [(r["metric"].get("instance", ""), r["metric"].get("job", ""), float(r["value"][1]))
                for r in prom_query(prom, q)]
    return {"probe": rows("probe_success"), "up": rows('up{job!~"blackbox-.*"}')}


def services_down(probes: dict) -> list[str]:
    down = [inst for inst, _job, v in probes.get("probe", []) if v == 0]
    down += [f"{inst} ({job})" for inst, job, v in probes.get("up", []) if v == 0]
    return down


def probe_status(probes: dict | None) -> dict[str, bool]:
    """{key: up}. A blackbox probe's key is its instance (the probed URL or IP,
    as in stacks/monitoring/prometheus/prometheus.yml); a scrape target's is
    "job@instance" ("loki@10.1.20.21:3100"), so an exporter scraping a device
    (snmp@10.1.3.1) never colours that device's ICMP probe. Several series for
    one key are ANDed."""
    st: dict[str, bool] = {}
    p = probes or {}
    rows = [(inst, v) for inst, _job, v in p.get("probe", [])]
    rows += [(f"{job}@{inst}", v) for inst, job, v in p.get("up", [])]
    for key, v in rows:
        st[key] = st.get(key, True) and v == 1
    return st


BACKUP_RE = re.compile(r"event name=(backup-[a-z0-9-]+) action=die code=(\d+)")


def fetch_backups(loki: str) -> list[tuple[int, str, int]]:
    rows = loki_range(loki, '{container="docker-event-watch"} |~ "event name=backup-[a-z0-9-]+ action=die "',
                      26 * 3600, 500)
    return parse_backups(rows)


def parse_backups(rows) -> list[tuple[int, str, int]]:
    """Latest exit per backup container: [(ts_ns, name, code)], newest first."""
    seen: dict[str, tuple[int, str, int]] = {}
    for ts, _stream, line in rows:
        m = BACKUP_RE.search(line)
        if m and (m.group(1) not in seen or seen[m.group(1)][0] < ts):
            seen[m.group(1)] = (ts, m.group(1), int(m.group(2)))
    return sorted(seen.values(), key=lambda x: -x[0])


def fetch_komodo(url: str, key: str, secret: str, now: float | None = None) -> dict:
    if not (key and secret):
        return {"configured": False}
    now = now or time.time()
    r = requests.post(f"{url}/read", timeout=8,
                      headers={"X-Api-Key": key, "X-Api-Secret": secret, "User-Agent": UA,
                               "Content-Type": "application/json"},
                      json={"type": "ListUpdates",
                            "params": {"query": {"start_ts": {"$gte": int((now - DAY) * 1000)}}}})
    r.raise_for_status()
    return {"configured": True, **parse_komodo(r.json())}


def parse_komodo(body: dict) -> dict:
    ups = (body or {}).get("updates") or []
    deploys = [u for u in ups if "Deploy" in str(u.get("operation") or "")]
    failed = [u for u in deploys if not u.get("success") and str(u.get("status") or "Complete") == "Complete"]
    return {"deploys": len(deploys), "failed": len(failed),
            "failed_targets": [str((u.get("target") or {}).get("id") or "?") for u in failed][:5],
            "more": bool((body or {}).get("next_page"))}


def fetch_github(repo: str) -> dict:
    pulls = _get_json(f"https://api.github.com/repos/{repo}/pulls", 8,
                      params={"state": "open", "per_page": "100"},
                      headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
    return parse_github(pulls)


def parse_github(pulls: list) -> dict:
    drafts = [p for p in pulls or [] if p.get("draft")]
    renovate = [p for p in pulls or [] if str((p.get("user") or {}).get("login", "")).startswith("renovate")]
    return {"open": len(pulls or []), "drafts": len(drafts), "renovate": len(renovate),
            "draft_titles": [str(p.get("title") or "") for p in drafts][:5]}


SUMMARY_RE = re.compile(r"\b(vulnerable|unknown|images_checked)=(\d+)")


def fetch_advisories(loki: str) -> dict | None:
    rows = loki_range(loki, '{job="advisory-watch"} |= "summary=1"', 8 * DAY, 1)
    return parse_advisory_summary(rows)


def parse_advisory_summary(rows) -> dict | None:
    if not rows:
        return None
    ts, _s, line = rows[0]
    vals = {k: int(v) for k, v in SUMMARY_RE.findall(line)}
    return {"ts": ts / 1e9, "vulnerable": vals.get("vulnerable", 0), "unknown": vals.get("unknown", 0)}


def fetch_ups(ha: str, token: str, prefix: str) -> dict:
    if not token:
        raise RuntimeError("HA token not set")
    states = _get_json(f"{ha.rstrip('/')}/api/states", 8, headers={"Authorization": f"Bearer {token}"})
    return parse_ups(states, prefix)


def parse_ups(states: list, prefix: str) -> dict:
    s = {x["entity_id"][len(prefix):]: x.get("state") for x in states or []
         if str(x.get("entity_id", "")).startswith(prefix)}
    return {"found": bool(s), "status": s.get("status") or "", "flags": s.get("status_data") or "",
            "load": s.get("load"), "charge": s.get("battery_charge"), "runtime": s.get("battery_runtime")}


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


# ---- sign-ins (Authelia JSON log) -------------------------------------------------
# At level info Authelia 4.39 logs: failed first factors ("Unsuccessful 1FA
# authentication attempt by user 'x'"), unknown usernames ("Error occurred
# getting details for user with username input 'x' ..."), failed 2FA
# ("Unsuccessful TOTP|WebAuthn ..."), and an accepted first factor only while 2FA
# is still due: "<url> requires 2FA, cannot be redirected yet" (no username) or
# "OpenID Connect 1.0 client requires 2FA" (username + client_id). A completed
# 2FA and a remembered session write no line, so this is the sign-in *attempt*
# list, not every session; authentication_logs in authelia-postgres has the rest.
SIGNIN_LOGQL = ('{container="authelia"} |~ "Unsuccessful|getting details for user|requires 2FA"')
EDGE_PROBE_USER = "edge-probe-nonexistent"      # scripts/edge-probe.sh, excluded like the alert rule
_UNSUCC = re.compile(r"^Unsuccessful (\S+) authentication attempt by user '([^']*)'(.*)$")
_UNKNOWN = re.compile(r"^Error occurred getting details for user with username input '([^']*)'")
_REDIRECT = re.compile(r"^(https?://\S+) requires 2FA, cannot be redirected yet")


def fetch_signins(loki: str) -> dict:
    return parse_signins(loki_range(loki, SIGNIN_LOGQL, DAY, 400))


def parse_signins(rows) -> dict:
    out, hidden = [], 0
    for ts, _stream, line in rows:
        try:
            j = json.loads(line)
        except ValueError:
            continue
        msg = str(j.get("msg") or "")
        t = parse_ts(j.get("time")) or ts / 1e9
        m = _UNSUCC.match(msg)
        if m:
            if not m.group(2):
                continue                          # the unknown-user twin line carries the name
            what = "banned" if "banned until" in m.group(3) else "failed"
            out.append({"ts": t, "user": m.group(2), "app": f"{m.group(1)} sign-in", "ok": False, "result": what})
            continue
        m = _UNKNOWN.match(msg)
        if m:
            if m.group(1) == EDGE_PROBE_USER:
                hidden += 1
                continue
            out.append({"ts": t, "user": m.group(1), "app": "unknown user", "ok": False, "result": "failed"})
            continue
        m = _REDIRECT.match(msg)
        if m:
            host = urllib.parse.urlsplit(m.group(1)).hostname or m.group(1)
            out.append({"ts": t, "user": "", "app": host, "ok": True, "result": "ok"})
            continue
        if msg.startswith("OpenID Connect 1.0 client requires 2FA"):
            out.append({"ts": t, "user": str(j.get("username") or ""), "app": f"OIDC {j.get('client_id') or ''}".strip(),
                        "ok": True, "result": "ok"})
    out.sort(key=lambda x: -x["ts"])
    return {"rows": out, "hidden_probe": hidden}


# ---- charts --------------------------------------------------------------------------
_NODE = 'job="node-external"'
_ROOT = f'{_NODE},mountpoint="/",fstype!~"tmpfs|overlay|squashfs"'
_NIC = f'{_NODE},device=~"(en|eth)[^.]*"'


def chart_defs(grafana: str, ha_ui: str, nas: str, pool: str, ups_prefix: str) -> list[dict]:
    zfs = f'instance="{nas}",fstype="zfs",device=~"{re2_literal(pool)}(/.*)?"'
    used = f"(node_filesystem_size_bytes{{{zfs}}} - node_filesystem_avail_bytes{{{zfs}}})"
    node_dash = f"{grafana}/d/rYdddlPWk"
    return [
        {"key": "cpu", "label": "Host CPU", "unit": "%", "href": node_dash, "title": "average busy, all lab hosts",
         "q": f'100 * (1 - avg(rate(node_cpu_seconds_total{{{_NODE},mode="idle"}}[5m])))'},
        {"key": "mem", "label": "Host memory", "unit": "%", "href": node_dash, "title": "used, all lab hosts",
         "q": f"100 * (1 - sum(node_memory_MemAvailable_bytes{{{_NODE}}}) / sum(node_memory_MemTotal_bytes{{{_NODE}}}))"},
        {"key": "disk", "label": "Disk used", "unit": "%", "href": node_dash, "title": "fullest root disk",
         "q": f"max(100 * (1 - node_filesystem_avail_bytes{{{_ROOT}}} / node_filesystem_size_bytes{{{_ROOT}}}))"},
        {"key": "nas", "label": "NAS space", "unit": "%", "href": f"{grafana}/d/zfs-zpool-health",
         "title": f"{pool} pool used",
         "q": f"100 * sum({used}) / (sum({used}) + max(node_filesystem_avail_bytes{{{zfs}}}))"},
        {"key": "net", "label": "Network", "unit": "bps", "href": node_dash, "title": "all lab hosts, in + out",
         "q": (f"sum(rate(node_network_receive_bytes_total{{{_NIC}}}[5m]) "
               f"+ rate(node_network_transmit_bytes_total{{{_NIC}}}[5m])) * 8")},
        {"key": "ups", "label": "UPS load", "unit": "%", "title": "Eaton 5PX, Home Assistant history",
         "href": f"{ha_ui}/history?entity_id={urllib.parse.quote(ups_prefix + 'load')}", "ha": ups_prefix + "load"},
    ]


def ha_history(ha: str, token: str, entity: str, seconds: int, now: float | None = None) -> list[tuple[float, float]]:
    if not token:
        raise RuntimeError("HA token not set")
    now = now or time.time()
    start = datetime.datetime.fromtimestamp(now - seconds, datetime.timezone.utc).isoformat()
    d = _get_json(f"{ha.rstrip('/')}/api/history/period/{urllib.parse.quote(start)}", 10,
                  headers={"Authorization": f"Bearer {token}"},
                  params={"filter_entity_id": entity, "minimal_response": "", "no_attributes": ""})
    pts = []
    for s in (d[0] if d else []):
        t, v = parse_ts(s.get("last_changed") or s.get("last_updated")), _num(s.get("state"))
        if t is not None and v is not None:
            pts.append((t, v))
    return bucket(pts, now - seconds, 900)


def bucket(pts: list[tuple[float, float]], start: float, step: int) -> list[tuple[float, float]]:
    """Average per `step` seconds (HA history is one row per change, about one
    a minute for the UPS load), so the sparkline matches the Prometheus ones."""
    acc: dict[int, list[float]] = {}
    for t, v in pts:
        acc.setdefault(int((t - start) // step), []).append(v)
    return [(start + k * step, sum(vs) / len(vs)) for k, vs in sorted(acc.items())]


def fetch_charts(prom: str, ha: str, token: str, defs: list[dict], seconds: int = DAY, step: int = 900,
                 now: float | None = None) -> dict:
    """{key: [(t, v)] or None}. One failed series never blanks the others."""
    now = now or time.time()
    out = {}
    for c in defs:
        try:
            if c.get("ha"):
                out[c["key"]] = ha_history(ha, token, c["ha"], seconds, now)
            else:
                out[c["key"]] = prom_range(prom, c["q"], now - seconds, now, step)
        except Exception:
            out[c["key"]] = None
    return out


def fmt_value(v: float | None, unit: str) -> str:
    if v is None:
        return "–"
    if unit == "bps":
        for div, suf in ((1e9, "Gb/s"), (1e6, "Mb/s"), (1e3, "kb/s")):
            if v >= div:
                return f"{v / div:.1f} {suf}" if v / div < 10 else f"{v / div:.0f} {suf}"
        return f"{v:.0f} b/s"
    return f"{v:.0f}%"


def spark_points(pts: list[tuple[float, float]], w: int = 120, h: int = 36, pad: int = 3) -> str:
    """SVG polyline points for a series; a flat line in the middle when constant."""
    if not pts:
        return ""
    if len(pts) == 1:
        pts = [pts[0], (pts[0][0] + 1, pts[0][1])]
    t0, t1 = pts[0][0], pts[-1][0]
    vs = [v for _t, v in pts]
    lo, hi = min(vs), max(vs)
    span_t = (t1 - t0) or 1
    out = []
    for t, v in pts:
        x = (t - t0) / span_t * w
        y = h / 2 if hi == lo else pad + (hi - v) / (hi - lo) * (h - 2 * pad)
        out.append(f"{x:.1f},{y:.1f}")
    return " ".join(out)


# ---- service tiles (hrefs from stacks/dashboard/config/services.yaml) -------------
# (label, href, host, probe): `probe` is the exact Prometheus `instance` that
# gives the tile its dot -- a blackbox target from
# stacks/monitoring/prometheus/prometheus.yml, or a scrape target (Loki) -- or
# None for no dot. Explicit, so a tile never borrows another service's probe on
# a shared host (Grafana vs the Loki Explore link) and an IP-probed app can still
# be linked by name.
GITHUB_REPO = "BigH3ater/kdk-lab-dkr"
TILES = [
    ("Media ops", [
        ("Radarr", "https://radarr.kmkdp.com/", "dkr-01", "https://radarr.kmkdp.com/ping"),
        ("Sonarr", "https://sonarr.kmkdp.com/", "dkr-01", "https://sonarr.kmkdp.com/ping"),
        ("Prowlarr", "https://prowlarr.kmkdp.com/", "dkr-01", "https://prowlarr.kmkdp.com/ping"),
        ("SABnzbd", "https://sabnzbd.kmkdp.com/", "dkr-01", "https://sabnzbd.kmkdp.com"),
        ("Bazarr", "https://bazarr.kmkdp.com/", "dkr-01", "https://bazarr.kmkdp.com"),
        ("Tdarr", "https://tdarr.kmkdp.com/", "dkr-01", "https://tdarr.kmkdp.com"),
        ("Trailarr", "https://trailarr.kmkdp.com/", "dkr-01", "https://trailarr.kmkdp.com"),
        ("Jellystat", "https://jellystat.kmkdp.com/", "dmz-01", "https://jellystat.kmkdp.com"),
    ]),
    ("Books", [
        ("LazyLibrarian", "https://lazylibrarian.kmkdp.com/", "dkr-01", "https://lazylibrarian.kmkdp.com"),
        ("Calibre (reMarkable)", "https://calibre.kmkdp.com/", "dkr-01", None),
        ("Audiobookshelf admin", "https://audiobookshelf.kmkdp.com/", "dmz-01",
         "https://audiobookshelf.kmkdp.com/healthcheck"),
        ("Wizarr invites", "https://wizarr.kmkdp.com/admin", "dmz-01", "https://wizarr.kmkdp.com"),
        # both hosts answer 404 at /, so the tiles link the working paths
        ("Reading catalogue", "https://opds.kmkdp.com/opds", "dmz-01", "https://opds.kmkdp.com/opds"),
        ("Reading sync", "https://kosync.kmkdp.com/healthcheck", "dmz-01",
         "https://kosync.kmkdp.com/healthcheck"),
    ]),
    ("Home automation", [
        ("Home Assistant", "https://homeassistant.kmkdp.com/", "dkr-02", "https://homeassistant.kmkdp.com"),
        ("Node-RED", "https://nodered.kmkdp.com/", "dkr-02", "https://nodered.kmkdp.com"),
        ("Zigbee2MQTT", "https://zigbee.kmkdp.com/", "dkr-02", "https://zigbee.kmkdp.com"),
        ("Scrypted", "https://scrypted.kmkdp.com/", "dkr-03", "https://scrypted.kmkdp.com"),
    ]),
    ("Identity", [
        ("Authelia", "https://sso.kmkdp.com/", "dmz-01", "https://sso.kmkdp.com"),
        ("LLDAP users", "https://users.kmkdp.com/", "dmz-01", "https://users.kmkdp.com"),
    ]),
    ("Deploy", [
        ("Komodo", "https://komodo.kmkdp.com/", "dkr-01", "https://komodo.kmkdp.com"),
        ("GitHub repo", f"https://github.com/{GITHUB_REPO}", "github", None),
        ("Docs", "https://docs.kmkdp.com/", "dkr-01", "https://docs.kmkdp.com/healthz"),
    ]),
    ("Infrastructure", [
        ("Proxmox", "https://10.1.1.100:8006/", "10.1.1.100", "10.1.1.100"),
        ("Backup server", "https://10.1.1.102:8007/", "10.1.1.102", "10.1.1.102"),
        ("TrueNAS", "https://10.1.3.5/", "10.1.3.5", "10.1.3.5"),
        ("UniFi", "https://10.1.3.1/", "10.1.3.1", "10.1.3.1"),
        ("AdGuard DNS", "http://10.1.1.120/", "10.1.1.120", "10.1.1.120"),
    ]),
    ("Monitoring", [
        ("Grafana", "https://grafana.kmkdp.com/", "mon-01", "https://grafana.kmkdp.com/api/health"),
        ("Prometheus", "http://10.1.20.30:9090/", "mon-01", "http://10.1.20.30:9090/-/healthy"),
        ("Alertmanager", "https://alertmanager.kmkdp.com/", "mon-01", "https://alertmanager.kmkdp.com"),
        ("Loki (Grafana Explore)", "https://grafana.kmkdp.com/explore", "log-01", "loki@10.1.20.21:3100"),
    ]),
    ("Household data", [
        ("Tandoor admin", "https://recipes.kmkdp.com/", "dmz-01", "https://recipes.kmkdp.com"),
        ("Vikunja admin", "https://tasks.kmkdp.com/", "dkr-01", "https://tasks.kmkdp.com"),
        ("wger admin", "https://wger.kmkdp.com/", "dkr-01", "https://wger.kmkdp.com"),
        ("Actual Budget", "https://budget.kmkdp.com/", "dkr-01", "https://budget.kmkdp.com"),
    ]),
]


def tile_dot(probe: str | None, status: dict[str, bool]) -> str | None:
    """'up' / 'down' from the tile's own probe instance, or None (no probe, or
    Prometheus has no series for it yet)."""
    if probe and probe in status:
        return "up" if status[probe] else "down"
    return None


# ---- status strip --------------------------------------------------------------------
def _tile(label, value, sub, state="ok"):
    return {"label": label, "value": value, "sub": sub, "state": state}


def _missing(label, feed_state: str, what: str):
    if feed_state == "loading":
        return _tile(label, "…", "loading", "off")
    return _tile(label, "?", f"{what} unreachable", "bad")


def status_tiles(src: dict, now: float, tz) -> list[dict]:
    """src: {name: (value, state)} where state is 'ok', 'loading' or 'failed'."""
    tiles = []
    alerts, st = src["alerts"]
    if alerts is None:
        tiles.append(_missing("Alerts firing", st, "Alertmanager"))
    else:
        crit = sum(1 for a in alerts if a["severity"] == "critical")
        tiles.append(_tile("Alerts firing", str(len(alerts)),
                           f"{crit} critical" if crit else "Alertmanager",
                           "bad" if crit else ("warn" if alerts else "ok")))
    probes, st = src["probes"]
    if probes is None:
        tiles.append(_missing("Services down", st, "Prometheus"))
    else:
        down = services_down(probes)
        total = len(probes.get("probe", [])) + len(probes.get("up", []))
        tiles.append(_tile("Services down", str(len(down)),
                           ", ".join(down[:2]) + (" …" if len(down) > 2 else "") if down else f"{total} targets up",
                           "bad" if down else "ok"))
    backups, st = src["backups"]
    if backups is None:
        tiles.append(_missing("Backups", st, "Loki"))
    elif not backups:
        tiles.append(_tile("Backups", "No run", "nothing in 26 h", "bad"))
    else:
        bad = [b for b in backups if b[2] != 0]
        last = datetime.datetime.fromtimestamp(backups[0][0] / 1e9, tz)
        tiles.append(_tile("Backups", f"{len(bad)} failed" if bad else "OK",
                           f"last nightly {last:%H:%M} · {len(backups)} steps", "bad" if bad else "ok"))
    kom, st = src["komodo"]
    if kom is not None and not kom.get("configured"):
        tiles.append(_tile("Deploys failed", "–", "not configured", "off"))
    elif kom is None:
        tiles.append(_missing("Deploys failed", st, "Komodo"))
    else:
        tiles.append(_tile("Deploys failed", str(kom["failed"]),
                           f"of {kom['deploys']}{'+' if kom.get('more') else ''} in 24 h",
                           "bad" if kom["failed"] else "ok"))
    gh, st = src["github"]
    if gh is None:
        tiles.append(_missing("Draft PRs", st, "GitHub"))
    else:
        tiles.append(_tile("Draft PRs", str(gh["drafts"]), "waiting on you" if gh["drafts"] else "none open",
                           "warn" if gh["drafts"] else "ok"))
    adv, st_adv = src["advisories"]
    if gh is None and adv is None:
        tiles.append(_missing("Updates", st, "GitHub and Loki"))
    else:
        reno = gh["renovate"] if gh else None
        vul = adv["vulnerable"] if adv else None
        unk = adv["unknown"] if adv else 0
        n = (reno or 0) + (vul or 0) + unk          # "to review" needs a look too, so it counts
        parts = [f"{reno} Renovate" if reno is not None else "Renovate ?",
                 f"{vul} advisories" if vul is not None else "advisories ?"]
        if unk:
            parts.append(f"{unk} to review")
        tiles.append(_tile("Updates", str(n), " · ".join(parts), "warn" if vul or unk else "ok"))
    ups, st = src["ups"]
    if ups is None:
        tiles.append(_missing("UPS", st, "Home Assistant"))
    elif not ups["found"]:
        tiles.append(_tile("UPS", "?", "no NUT sensors in HA", "bad"))
    else:
        flags = ups["flags"].split()
        word = ("Low battery" if "LB" in flags else "On battery" if "OB" in flags
                else "Online" if "OL" in flags else (ups["status"] or "?"))
        bits = []
        if _num(ups["load"]) is not None:
            bits.append(f"load {_num(ups['load']):.0f}%")
        if _num(ups["charge"]) is not None:
            bits.append(f"charge {_num(ups['charge']):.0f}%")
        if _num(ups["runtime"]) is not None:
            bits.append(f"{_num(ups['runtime']) / 60:.0f} min")
        tiles.append(_tile("UPS", word, " · ".join(bits) or "NUT via Home Assistant",
                           "ok" if word == "Online" else "bad"))
    return tiles


# ---- HTML ----------------------------------------------------------------------------
def head(asset_ver: str, refresh: bool) -> str:
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>Lab admin</title><meta name='theme-color' content='#100D0B'>"
            "<meta name='robots' content='noindex'>"
            + ("<meta http-equiv='refresh' content='60'>" if refresh else "")
            + "<link rel='icon' type='image/png' sizes='192x192' href='/static/icon-192.png'>"
            f"<link rel='stylesheet' href='/static/home.css?v={escape(asset_ver)}'></head>")


def _sub_html(sub: str) -> str:
    """The sub-line split at " · " into unbreakable parts: a narrow tile wraps
    between parts instead of hiding the last numbers behind an ellipsis."""
    parts = sub.split(" · ")
    return " ".join(f"<span>{escape(p)}{' ·' if i < len(parts) - 1 else ''}</span>"
                    for i, p in enumerate(parts))


def _stats_html(tiles: list[dict]) -> str:
    return "".join(
        f"<div class='ad-stat {escape(t['state'])}'><div class='ad-lbl'>{escape(t['label'])}</div>"
        f"<div class='ad-val'>{escape(t['value'])}</div>"
        f"<div class='ad-sub' title='{escape(t['sub'])}'>{_sub_html(t['sub'])}</div></div>"
        for t in tiles)


def _alerts_html(alerts, state: str, am_ui: str, now: float) -> str:
    if alerts is None:
        body = "<div class='ad-empty'>Loading…</div>" if state == "loading" else \
            "<div class='ad-empty bad'>Alertmanager is unreachable right now.</div>"
    elif not alerts:
        body = "<div class='ad-empty'>Nothing firing.</div>"
    else:
        rows = []
        for a in alerts[:12]:
            age = ago(now - a["since"]) if a["since"] else ""
            rows.append(f"<div class='ad-row ad-alert'><span class='sev {escape(a['severity'])}'>{escape(a['severity'])}</span>"
                        f"<span class='ad-name' title='{escape(a['summary'])}'>{escape(a['name'])}"
                        f"<span class='ad-inst'>{escape(a['instance'])}</span></span>"
                        f"<span class='muted'>{escape(age)}</span>"
                        f"<a href='{escape(am_filter_link(am_ui, a))}' target='_blank' rel='noopener'>Silence</a></div>")
        more = f"<div class='ad-empty'>{len(alerts) - 12} more in Alertmanager</div>" if len(alerts) > 12 else ""
        body = "".join(rows) + more
    return ("<div class='card ad-card'><div class='ad-h'><h2>Firing alerts</h2>"
            f"<span class='muted'>read-only · silence in <a href='{escape(am_ui)}/' target='_blank' rel='noopener'>"
            f"Alertmanager</a></span></div>{body}</div>")


def _signins_html(si, state: str, tz) -> str:
    if si is None:
        body = "<div class='ad-empty'>Loading…</div>" if state == "loading" else \
            "<div class='ad-empty bad'>Loki is unreachable right now.</div>"
    elif not si["rows"]:
        body = "<div class='ad-empty'>No sign-in attempts in 24 h.</div>"
    else:
        rows = []
        for r in si["rows"][:10]:
            t = datetime.datetime.fromtimestamp(r["ts"], tz)
            rows.append(f"<div class='ad-row ad-login'><span class='muted'>{t:%H:%M}</span>"
                        f"<span>{escape(r['user'] or '–')}</span><span class='muted ad-ell'>{escape(r['app'])}</span>"
                        f"<span class='res {'ok' if r['ok'] else 'bad'}'>{escape(r['result'])}</span></div>")
        body = "".join(rows)
    note = ""
    if si and si.get("hidden_probe"):
        note = f"<div class='ad-note'>{si['hidden_probe']} edge-probe attempts hidden.</div>"
    return ("<div class='card ad-card'><div class='ad-h'><h2>Recent sign-ins</h2>"
            "<span class='muted' title='Authelia logs failures and first factors that still need 2FA; "
            "a finished 2FA or a remembered session writes no line'>Authelia · 24 h · attempts</span></div>"
            f"{body}{note}</div>")


def _syslog_html(form: dict, hosts: list[str], result: dict | None, tz, explore: str) -> str:
    q, host, rng = form.get("q", ""), form.get("host", ""), form.get("range", "1h")
    opts = "<option value=''>All hosts</option>" + "".join(
        f"<option value='{escape(h)}'{' selected' if h == host else ''}>{escape(h)}</option>" for h in hosts)
    ropts = "".join(f"<option value='{k}'{' selected' if k == rng else ''}>{v}</option>"
                    for k, v in RANGE_LABEL.items())
    if result is None:
        body = ""
    elif result.get("error"):
        body = f"<div class='ad-empty bad'>{escape(result['error'])}</div>"
    elif not result["rows"]:
        span = RANGE_LABEL.get(rng, "").replace("Last ", "last ")
        wider = ""
        if rng != "24h":
            qs = urllib.parse.urlencode({"q": q, "host": host, "range": "24h"})
            wider = f" <a href='/?{escape(qs)}'>Search the last 24 hours</a>"
        body = f"<div class='ad-empty'>No matching lines in the {escape(span)}.{wider}</div>"
    else:
        lines = []
        for r in result["rows"]:
            t = datetime.datetime.fromtimestamp(r["ts"], tz)
            lines.append(f"<div class='ad-log'><span class='muted'>{t:%m-%d %H:%M:%S}</span>"
                         f"<span class='tan'>{escape(r['host'])}</span><span class='ad-ell' title='{escape(r['line'][:2000])}'>"
                         f"{escape(r['line'][:600])}</span></div>")
        cap = (f"<div class='ad-note'>First {len(result['rows'])} lines (newest first, capped at 200).</div>"
               if len(result["rows"]) >= 200 else "")
        body = "<div class='ad-logs'>" + "".join(lines) + "</div>" + cap
    if result and result.get("note") and not result.get("error"):
        body = f"<div class='ad-note'>{escape(result['note'])}</div>" + body
    return ("<section class='card ad-card ad-syslog'><form class='ad-search' method='get' action='/'>"
            f"<h2>Syslog search</h2><span class='muted'>Loki · {len(hosts)} hosts</span>"
            "<label class='sr' for='q'>Search syslog</label>"
            f"<input id='q' name='q' value='{escape(q)}' maxlength='{QUERY_MAX}' placeholder='text to find (any case)' autocomplete='off'>"
            f"<label class='sr' for='h'>Host</label><select id='h' name='host'>{opts}</select>"
            f"<label class='sr' for='r'>Time range</label><select id='r' name='range'>{ropts}</select>"
            "<button type='submit' class='ad-btn'>Search</button></form>"
            f"{body}<a class='ad-explore' href='{escape(explore)}' target='_blank' rel='noopener'>Open in Grafana Explore</a></section>")


def _charts_html(defs: list[dict], series, state: str) -> str:
    out = []
    for c in defs:
        pts = (series or {}).get(c["key"])
        val = fmt_value(pts[-1][1] if pts else None, c["unit"])
        if pts:
            svg = (f"<svg aria-hidden='true' viewBox='0 0 120 36' preserveAspectRatio='none'>"
                   f"<polyline points='{spark_points(pts)}'/></svg>")
        else:
            # None = the fetch failed (the whole feed, or this one source);
            # [] = the source answered with an empty series
            src = "Home Assistant" if c.get("ha") else "Prometheus"
            if series is None and state == "loading":
                msg = "loading"
            elif pts is None and (series is None or c["key"] in series):
                msg = f"{src} unreachable"
            else:
                msg = "no data"
            svg = f"<div class='ad-nodata{' bad' if msg.endswith('unreachable') else ''}'>{msg}</div>"
        out.append(f"<a class='card ad-chart' href='{escape(c['href'])}' target='_blank' rel='noopener' title='{escape(c['title'])}'>"
                   f"<div class='ad-ch'><span class='muted'>{escape(c['label'])}</span><b>{escape(val)}</b></div>{svg}</a>")
    return "<section class='ad-charts'>" + "".join(out) + "</section>"


def _groups_html(status: dict[str, bool]) -> str:
    out = []
    for name, items in TILES:
        rows = []
        for label, href, host, probe in items:
            dot = tile_dot(probe, status)
            dot_html = (f"<span class='dot {dot}' role='img' aria-label='{'up' if dot == 'up' else 'down'}'></span>"
                        if dot else "<span class='dot none' aria-hidden='true'></span>")
            rows.append(f"<a class='ad-svc' href='{escape(href)}' target='_blank' rel='noopener'>{dot_html}"
                        f"<span class='ad-svc-n'>{escape(label)}</span><span class='ad-host'>{escape(host)}</span></a>")
        out.append(f"<div class='card ad-group'><h3>{escape(name)}</h3>{''.join(rows)}</div>")
    return "<section class='ad-groups'>" + "".join(out) + "</section>"


def page(d: dict, asset_ver: str) -> str:
    """d: tiles, alerts, signins, charts, chart_defs, probes, syslog form/result,
    hosts, user, home_url, am_ui, explore, now, tz, admin_host."""
    tz, now = d["tz"], d["now"]
    searching = d.get("syslog") is not None
    return (head(asset_ver, refresh=not searching)
            + "<body class='admin'><header class='ad-top'><div class='brand'>Mack House</div>"
            f"<span class='ad-badge'>Admin</span><span class='muted ad-hostname'>{escape(d['admin_host'])}</span>"
            f"<a class='ad-home' href='{escape(d['home_url'])}'>Household home</a>"
            f"<span class='muted'>{escape(d['user'])}</span></header><main class='ad-main'>"
            f"<section class='ad-stats'>{_stats_html(d['tiles'])}</section>"
            "<section class='ad-two'>"
            f"{_alerts_html(d['alerts'][0], d['alerts'][1], d['am_ui'], now)}"
            f"{_signins_html(d['signins'][0], d['signins'][1], tz)}</section>"
            f"{_syslog_html(d.get('form') or {}, d.get('hosts') or [], d.get('syslog'), tz, d['explore'])}"
            f"{_charts_html(d['chart_defs'], d['charts'][0], d['charts'][1])}"
            f"{_groups_html(probe_status(d['probes'][0]))}"
            "</main></body></html>")
