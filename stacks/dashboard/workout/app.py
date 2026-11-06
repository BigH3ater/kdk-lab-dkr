#!/usr/bin/env python3
"""dashboard-workout: the Today page — the day's exercise steps (poster, dose, cues)
with a per-set log form on each card, at home.kmkdp.com/workout/ (Traefik
StripPrefix /workout, Authelia ForwardAuth in front). Installable as a home-screen
web app (manifest + a small service worker; the page queues logs while offline).

Reads (all read-only mounts):
  /guide/workout.json        day -> steps (title, dose, picture, slug; v2 adds cues + sets);
                             written by stacks/dashboard/guides/gen-workout-guide.py on the
                             laptop (host-managed, /opt/kdk-lab/dashboard/workout). Pictures
                             are served by the guides nginx at /guides/workout/exercise/...
                             Version 1 and 2 files are both accepted.
  /catalog/workout_catalog.json  the ONE exercise list (stacks/home-assistant/tools):
                             slug -> HA helper id + kind, and who the people are.
Writes:
  /log/workout-log.csv       one row per logged set, tick or score (schema v2, see
                             history.py; a v1 file is migrated at startup, with a
                             workout-log.v1-<YYYYMMDD>.bak beside it). The durable record.
  Home Assistant             input_number.set_value / input_datetime.set_datetime on the
                             person's own helpers = the LAST filled set + the done time
                             (long-lived token, env WORKOUT_HA_TOKEN). Only for today's
                             date: a backdated log goes to the CSV only.

Person = the Authelia Remote-User header mapped through the catalog's people
(jmack -> Jacob, rach -> Rachel). Anyone else sees the page read-only.

Served:
  GET  /           Today: ?date=YYYY-MM-DD (default today, America/Chicago;
                   ?day=mon..sun = that weekday of the current week)
  POST /log        one card: weight_1..n / reps_1..n / seconds_1..n / lengths_1..n
                   (or minutes / score / done) -> one CSV row per filled set + HA,
                   then 303 back to the same date. With header X-Workout-Queue: 1
                   (the page's JS and its offline replay) it answers JSON instead.
  GET  /history    placeholder until the history views (B3)
  GET  /api/history.json  ?person=&item=&since=   one exercise's sessions (JSON)
  GET  /api/week.json     ?person=&week=2026-W45  ISO-week goal counters (JSON)
  GET  /manifest.webmanifest, /sw.js, /icon-{180,192,512}.png   the installable app
  GET  /healthz    "ok"
The JSON routes are for the catalog people only (either person's data; default = you).
"""
from __future__ import annotations

import collections
import csv
import datetime
import functools
import json
import logging
import os
import re
import struct
import threading
import zlib
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
from flask import Flask, Response, redirect, request
from markupsafe import escape
from waitress import serve

import history
from history import CSV_HEAD, DAY_IDS

HA_URL = os.environ.get("HA_URL", "https://homeassistant.kmkdp.com").rstrip("/")
HA_TOKEN = os.environ.get("WORKOUT_HA_TOKEN") or os.environ.get("HA_TOKEN", "")  # .env key: WORKOUT_HA_TOKEN
GUIDE = Path(os.environ.get("GUIDE_JSON", "/guide/workout.json"))
CATALOG = Path(os.environ.get("CATALOG_JSON", "/catalog/workout_catalog.json"))
LOG_CSV = Path(os.environ.get("LOG_CSV", "/log/workout-log.csv"))
IMG_BASE = os.environ.get("IMG_BASE", "/guides/workout/")
ALLOWED_ORIGINS = {h.strip() for h in os.environ.get("ALLOWED_HOSTS", "home.kmkdp.com").split(",") if h.strip()}
TZ_NAME = os.environ.get("TZ", "America/Chicago")
TZ = ZoneInfo(TZ_NAME)
history.TZ = TZ

# Same kinds as stacks/home-assistant/tools/gen_workout.py (keep in sync).
FIELDS = {"w": ["weight", "reps"], "r": ["reps"], "t": ["seconds"], "wt": ["weight", "seconds"],
          "wl": ["weight", "lengths"], "min": ["minutes"], "score": ["score"], "done": ["done"]}
SET_KINDS = {"w", "r", "t", "wt", "wl"}          # one form row per set
LIMITS = {"weight": (0, 200), "reps": (0, 50), "seconds": (0, 600), "lengths": (0, 20), "minutes": (0, 240),
          "score": (0, 10)}
LABEL = {"weight": "lb", "reps": "reps", "seconds": "sec", "lengths": "lengths", "minutes": "min", "score": "0–10"}
STEP = {"weight": "0.1", "reps": "1", "seconds": "1", "lengths": "1", "minutes": "1", "score": "1"}
GUIDE_VERSIONS = {1, 2}
DEFAULT_SETS, MAX_SETS = 3, 10
DOSE_SETS = re.compile(r"^\s*(\d{1,2})\s*[×x](?:\s|$)")     # "3 × 8–12" -> 3
# Bump when the page shell (CSS/JS/icons) changes, so installed apps drop the old cache.
SW_VERSION = "20261106a"
NAV_V = "nav20261104"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("workout")
app = Flask(__name__)
_csv_lock = threading.Lock()
_schema_checked = False
_seen_qids: collections.OrderedDict[str, None] = collections.OrderedDict()   # offline-replay de-dup


# ------------------------------------------------------------------ data
def _load_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def catalog() -> dict:
    """Re-read per request (tiny file) so a dashboard redeploy or catalog change
    needs no restart."""
    return _load_json(CATALOG)


def guide() -> dict | None:
    try:
        g = _load_json(GUIDE)
    except (OSError, ValueError):
        log.warning("guide data %s missing or unreadable", GUIDE)
        return None
    if g.get("version", 1) not in GUIDE_VERSIONS:
        log.warning("workout.json version %r is newer than this app knows (%s); rendering what it can",
                    g.get("version"), sorted(GUIDE_VERSIONS))
    return g


def person_for(cat: dict, login: str) -> dict | None:
    login = (login or "").strip()
    return next((p for p in cat["people"] if p.get("login") == login), None)


def entity(item: dict, field: str, suffix: str) -> str:
    domain = "input_datetime" if field == "done" else "input_number"
    return f"{domain}.ex_{item['id']}_{field}{suffix}"


def slug_map(cat: dict) -> dict[str, dict]:
    return {s: it for it in cat["items"] for s in it.get("slugs", [])}


def today_local() -> datetime.date:
    return datetime.datetime.now(TZ).date()


def page_date(args) -> datetime.date:
    """?date=YYYY-MM-DD, else ?day=mon..sun (that weekday of the current week), else today."""
    today = today_local()
    raw = (args.get("date") or "").strip()
    if raw:
        try:
            return datetime.date.fromisoformat(raw)
        except ValueError:
            pass
    day = args.get("day", "")
    if day in DAY_IDS:
        return today + datetime.timedelta(days=DAY_IDS.index(day) - today.weekday())
    return today


def sets_for(step: dict) -> int:
    """Form rows for a set kind: workout.json v2 `sets`, else the dose ("3 × 8–12"), else 3."""
    n = step.get("sets")
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        m = DOSE_SETS.match(step.get("dose") or "")
        n = int(m.group(1)) if m else DEFAULT_SETS
    return max(1, min(MAX_SETS, n))


# ------------------------------------------------------------------ Home Assistant
def _ha(method: str, path: str, body: dict | None = None) -> requests.Response:
    return requests.request(method, f"{HA_URL}{path}", json=body, timeout=8,
                            headers={"Authorization": f"Bearer {HA_TOKEN}"})


def ha_states(entity_ids: set[str]) -> dict[str, str]:
    """Current state of the given helpers (best effort: {} if HA is unreachable)."""
    if not HA_TOKEN or not entity_ids:
        return {}
    try:
        r = _ha("GET", "/api/states")
        r.raise_for_status()
        return {s["entity_id"]: s.get("state", "") for s in r.json() if s.get("entity_id") in entity_ids}
    except (requests.RequestException, ValueError):
        log.exception("HA state read failed")
        return {}


def ha_write(item: dict, values: dict[str, float], done: bool, suffix: str) -> bool:
    ok = True
    for f, v in values.items():
        r = _ha("POST", "/api/services/input_number/set_value",
                {"entity_id": entity(item, f, suffix), "value": v})
        ok &= r.ok
        if not r.ok:
            log.warning("set_value %s -> HTTP %s", entity(item, f, suffix), r.status_code)
    if done:
        r = _ha("POST", "/api/services/input_datetime/set_datetime",
                {"entity_id": entity(item, "done", suffix), "timestamp": datetime.datetime.now(TZ).timestamp()})
        ok &= r.ok
        if not r.ok:
            log.warning("set_datetime %s -> HTTP %s", entity(item, "done", suffix), r.status_code)
    return ok


# ------------------------------------------------------------------ CSV
def ensure_schema() -> None:
    """Migrate a v1 CSV to v2 once per process (startup, and again before the first
    write in case the file was swapped). Caller holds _csv_lock or is single-threaded."""
    global _schema_checked
    if _schema_checked:
        return
    LOG_CSV.parent.mkdir(parents=True, exist_ok=True)
    if history.ensure_schema(LOG_CSV) == "migrated":
        log.info("migrated %s to CSV v2 (v1 copy kept beside it)", LOG_CSV)
    _schema_checked = True


def _row(person: dict, item: dict, slug: str, values: dict[str, float], done: bool, set_index: int | None,
         date: str, source: str) -> dict:
    now = datetime.datetime.now(TZ)
    row = {"timestamp": now.isoformat(timespec="seconds"), "date": date, "person": person["key"],
           "item": item["id"], "slug": slug, "set_index": str(set_index) if set_index else "",
           "done": "1" if done else "", "source": source}
    for f in history.SET_FIELDS + ["score"]:
        row[f] = _fmt(values[f]) if f in values else ""
    return row


def _write_rows(rows: list[dict]) -> None:
    """Append rows (caller holds _csv_lock)."""
    ensure_schema()
    new = not LOG_CSV.exists() or LOG_CSV.stat().st_size == 0
    with LOG_CSV.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_HEAD)
        if new:
            w.writeheader()
        w.writerows(rows)


def csv_append(person: dict, item: dict, slug: str, values: dict[str, float], done: bool,
               set_index: int | None = None, source: str = "web", date: str | None = None) -> None:
    """One v2 row. `values` may hold weight/reps/seconds/lengths/minutes/score.
    set_index defaults to 1 when a set (any weight/reps/seconds/lengths/minutes) is
    logged, blank for a tick or a score. date defaults to today (local)."""
    if set_index is None and any(f in values for f in history.SET_FIELDS):
        set_index = 1
    with _csv_lock:
        _write_rows([_row(person, item, slug, values, done, set_index, date or today_local().isoformat(), source)])


def csv_append_sets(person: dict, item: dict, slug: str, sets: list[dict[str, float]], date: str,
                    source: str = "web") -> int:
    """One row per set, set_index continuing after any sets already logged that day
    for this person + item (a second submission adds sets 4, 5 ...). Returns the
    number of rows written."""
    with _csv_lock:
        ensure_schema()
        have = [int(r["set_index"]) for r in history.csv_rows(LOG_CSV)
                if r["person"] == person["key"] and r["item"] == item["id"] and r["date"] == date
                and r["set_index"].strip().isdigit()]
        start = max(have, default=0)
        _write_rows([_row(person, item, slug, v, False, start + i, date, source) for i, v in enumerate(sets, 1)])
    return len(sets)


def _fmt(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


def _parse_one(field: str, raw: str) -> float:
    v = float(raw)  # ValueError -> caller
    lo, hi = LIMITS[field]
    if v != v or not (lo <= v <= hi):
        raise ValueError(f"{field} out of range")
    return v


def parse_sets(item: dict, form) -> list[dict[str, float]]:
    """The filled sets in form order: <field>_<n> for n = 1..MAX_SETS (an unsuffixed
    <field> counts as set 1, for older forms; min and score have one box). A set with
    any value counts; blank rows are skipped. A bad number raises ValueError (nothing
    is written)."""
    kind = item["kind"]
    if kind == "done":
        return []
    fields = FIELDS[kind]
    n = MAX_SETS if kind in SET_KINDS else 1
    out = []
    for i in range(1, n + 1):
        vals = {}
        for f in fields:
            raw = (form.get(f"{f}_{i}") or (form.get(f) if i == 1 else "") or "").strip()
            if raw:
                vals[f] = _parse_one(f, raw)
        if vals:
            out.append(vals)
    return out


# ------------------------------------------------------------------ routes
@app.get("/healthz")
def healthz():
    return "ok"


@app.get("/")
def index():
    cat = catalog()
    person = person_for(cat, request.headers.get("Remote-User", ""))
    date = page_date(request.args)
    html = render_page(cat, guide(), date, today_local(), person, request.args.get("ok", ""),
                       request.args.get("err", ""), _history())
    return Response(html, mimetype="text/html", headers={"Cache-Control": "no-cache"})


@app.get("/history")
def history_page():
    body = ("<h2>History</h2><p class='meta'>Charts for both of you arrive with the next update. Until then the "
            "raw sessions are at <a href='api/history.json?item=box_squat'>api/history.json?item=…</a> and the "
            "week's goals at <a href='api/week.json'>api/week.json</a>.</p><p><a href='./'>← Today</a></p>")
    return Response(_page("", "", body, "", ""), mimetype="text/html")


@app.post("/log")
def log_entry():
    as_json = request.headers.get("X-Workout-Queue") == "1"
    cat = catalog()
    person = person_for(cat, request.headers.get("Remote-User", ""))
    if person is None:
        return Response("Logging is for Jacob and Rachel only.", status=403)
    origin = request.headers.get("Origin")
    if origin and urlparse(origin).hostname not in ALLOWED_ORIGINS:
        return Response("bad origin", status=403)
    items = {it["id"]: it for it in cat["items"]}
    item = items.get(request.form.get("item", ""))
    if item is None:
        return Response("unknown exercise", status=400)
    today = today_local()
    raw_date = (request.form.get("date") or "").strip()
    try:
        date = datetime.date.fromisoformat(raw_date) if raw_date else today
    except ValueError:
        date = None
    anchor = f"#ex-{item['id']}"

    def reply(flag: str | None, status: int = 200, **extra):
        d = date if date is not None and date <= today else today
        nxt = f"./?date={d.isoformat()}" + (f"&{flag}={item['id']}" if flag else "") + anchor
        if as_json:
            return _json({"ok": status < 400, "item": item["id"], "next": nxt, **extra}, status)
        return redirect(nxt, code=303)

    if date is None or date > today:
        return reply("err", 400, error="date must be today or earlier")
    qid = (request.form.get("qid") or "")[:64]
    if qid and qid in _seen_qids:              # a replay whose first attempt did land
        return reply("ok", duplicate=True)
    try:
        sets = parse_sets(item, request.form)
    except ValueError:
        return reply("err", 400, error="check the numbers")
    done = item["kind"] == "done" and request.form.get("done") == "1"
    if not sets and not done:
        return reply(None, sets=0)
    slug = (request.form.get("slug") or "")[:64]
    ds = date.isoformat()
    # history first: the CSV is the durable record
    if item["kind"] in SET_KINDS or item["kind"] == "min":
        n = csv_append_sets(person, item, slug, sets, ds)
    else:
        csv_append(person, item, slug, sets[0] if sets else {}, done, date=ds)
        n = 0
    if qid:
        _seen_qids[qid] = None
        while len(_seen_qids) > 500:
            _seen_qids.popitem(last=False)
    ok, ha = True, "skipped"
    if date == today:                          # HA = the latest values; a backdated log is CSV only
        try:
            ok = ha_write(item, sets[-1] if sets else {}, done, person["suffix"])
        except requests.RequestException:
            log.exception("HA write failed for %s", item["id"])
            ok = False
        ha = "ok" if ok else "failed"
    log.info("logged %s for %s on %s (%d sets, ha=%s)", item["id"], person["key"], ds, n, ha)
    return reply("ok" if ok else "err", sets=n, ha=ha)


# ------------------------------------------------------------------ installable app
@app.get("/manifest.webmanifest")
def manifest():
    data = {"name": "Workout", "short_name": "Workout", "description": "Today's exercises and your log",
            "start_url": "./", "scope": "./", "display": "standalone", "orientation": "portrait",
            "background_color": "#2b2825", "theme_color": "#1b1a18",
            "icons": [{"src": "icon-192.png", "sizes": "192x192", "type": "image/png"},
                      {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}]}
    return Response(json.dumps(data), mimetype="application/manifest+json", headers={"Cache-Control": "no-cache"})


@app.get("/icon-<int:size>.png")
def icon(size: int):
    if size not in (180, 192, 512):
        return Response("not found", status=404)
    return Response(icon_png(size), mimetype="image/png", headers={"Cache-Control": "max-age=86400"})


@app.get("/sw.js")
def service_worker():
    js = SW_JS.replace("__V__", SW_VERSION).replace("__NAV__", NAV_V).replace("__IMG__", IMG_BASE) \
              .replace("__TZ__", TZ_NAME)
    return Response(js, mimetype="text/javascript", headers={"Cache-Control": "no-cache"})


@functools.lru_cache(maxsize=4)
def icon_png(size: int) -> bytes:
    """A dumbbell on the ink ground, drawn with the stdlib (no Pillow in the image)."""
    bg, fg = bytes((0x1b, 0x1a, 0x18)), bytes((0xc9, 0x74, 0x3f))
    # (x0, x1, y0, y1) in unit coordinates: bar, two inner plates, two outer plates
    boxes = [(.22, .78, .46, .54), (.24, .34, .28, .72), (.66, .76, .28, .72), (.15, .24, .35, .65),
             (.76, .85, .35, .65)]
    raw = bytearray()
    for y in range(size):
        v = (y + .5) / size
        row = bytearray(bg * size)
        for x0, x1, y0, y1 in boxes:
            if y0 <= v < y1:
                a, b = int(x0 * size), int(x1 * size)
                row[a * 3:b * 3] = fg * (b - a)
        raw += b"\x00" + row

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


# ------------------------------------------------------------------ JSON (history)
def _api_person(cat: dict):
    """(viewer ok?, target person or an error Response)."""
    viewer = person_for(cat, request.headers.get("Remote-User", ""))
    if viewer is None:
        return None, Response('{"error": "history is for Jacob and Rachel only"}', status=403,
                              mimetype="application/json")
    want = (request.args.get("person") or "").strip()
    if not want:
        return viewer, None
    target = next((p for p in cat["people"] if want in (p.get("login"), p.get("key"))), None)
    if target is None:
        return None, Response('{"error": "unknown person"}', status=400, mimetype="application/json")
    return target, None


def _history() -> history.History:
    with _csv_lock:
        rows = history.csv_rows(LOG_CSV)
    return history.load(rows)


def _json(data, status=200) -> Response:
    return Response(json.dumps(data, ensure_ascii=False), status=status, mimetype="application/json",
                    headers={"Cache-Control": "no-store"})


@app.get("/api/history.json")
def api_history():
    cat = catalog()
    person, err = _api_person(cat)
    if err is not None:
        return err
    items = {it["id"]: it for it in cat["items"]}
    item = items.get(request.args.get("item", ""))
    if item is None:
        return _json({"error": "unknown item"}, 400)
    since = (request.args.get("since") or "").strip()
    if since:
        try:
            since = datetime.date.fromisoformat(since).isoformat()
        except ValueError:
            return _json({"error": "since must be YYYY-MM-DD"}, 400)
    h = _history()
    out = {"person": person["key"], "item": item["id"], "name": item["name"], "kind": item["kind"],
           "fields": FIELDS[item["kind"]], "since": since, "sessions": h.history_for(person["key"], item["id"], since)}
    if item["id"] == history.WALK_ITEM:
        out["treadmill_minutes"] = h.treadmill_minutes(person["key"], since)
    return _json(out)


@app.get("/api/week.json")
def api_week():
    cat = catalog()
    person, err = _api_person(cat)
    if err is not None:
        return err
    week = (request.args.get("week") or "").strip() or history.iso_week_of(today_local())
    try:
        history.week_monday(week)
    except ValueError:
        return _json({"error": "week must look like 2026-W45"}, 400)
    g = guide()
    out = _history().week_summary(person["key"], week, lambda d: history.planned_items(g, d, cat), cat)
    out["plan_published"] = g is not None
    return _json(out)


# ------------------------------------------------------------------ render
class Ctx:
    """What every card needs: who, which date, what is logged and what to prefill."""

    def __init__(self, cat, person, date, today, hist, ok_id, err_id):
        self.cat, self.person, self.date, self.today = cat, person, date, today
        self.ds = date.isoformat()
        self.can_log = person is not None and date <= today
        self.ok_id, self.err_id = ok_id, err_id
        self.hist = hist
        self.key = person["key"] if person else None
        self.shown: set[str] = set()
        self.rows_today: dict[str, list[dict]] = collections.defaultdict(list)
        for e in (hist.entries if person else []):
            if e["person"] == self.key and e["date"] == self.ds:
                self.rows_today[e["item"]].append(e)
        self.ha: dict[str, str] = {}
        self._last: dict[str, dict | None] = {}

    def last_session(self, item_id: str) -> dict | None:
        """The most recent session BEFORE the page's date (what you did last time)."""
        if not self.person:
            return None
        if item_id not in self._last:
            past = [s for s in self.hist.history_for(self.key, item_id) if s["date"] < self.ds]
            self._last[item_id] = past[-1] if past else None
        return self._last[item_id]


def render_page(cat, g, date, today, person, ok_id, err_id, hist=None) -> str:
    hist = hist or history.load([])
    ctx = Ctx(cat, person, date, today, hist, ok_id, err_id)
    day_id = DAY_IDS[date.weekday()]
    nav = _week_nav(date, today)
    who = (f'Logging as <b>{escape(person["name"])}</b>' if person
           else "Read-only: logging is for Jacob and Rachel.")
    if g is None:
        return _page(nav, who, '<p class="empty">The workout plan has not been published yet (workout.json missing).</p>',
                     ctx.ds, today.isoformat())
    day = next((d for d in g.get("days", []) if d.get("id") == day_id), None)
    if day is None:
        return _page(nav, who, '<p class="empty">No plan for this day.</p>', ctx.ds, today.isoformat())
    smap = slug_map(cat)
    # HA is the fallback prefill for items with no CSV session yet: read it once, only if needed
    need = set()
    if person:
        for st in _all_steps(g, day):
            it = smap.get(st.get("slug", ""))
            if it and it["kind"] in SET_KINDS | {"min", "done"} and ctx.last_session(it["id"]) is None:
                need |= {entity(it, f, person["suffix"]) for f in FIELDS[it["kind"]]}
    ctx.ha = ha_states(need)
    cards = []
    for st in day.get("steps", []):
        if st.get("everyday"):
            inner = "".join(_card(ctx, smap, s, sub=True) for s in g.get("everyday", []))
            cards.append(f'<section class="step every"><h3><span class="n">{escape(st.get("num", ""))}</span>'
                         f'{escape(st.get("title", ""))}</h3><p class="dose">{escape(st.get("dose", ""))}</p>{inner}</section>')
        else:
            cards.append(_card(ctx, smap, st))
    # items with no picture of their own (scores) and daily routines not reached above
    rest = [it for it in cat["items"] if it["id"] not in ctx.shown and it["kind"] in ("score", "done") and
            (not it.get("slugs") or it["group"] == "daily")]
    if rest:
        boxes = "".join(_logbox(ctx, it, "", {}) for it in rest)
        cards.append(f'<section class="step checkin"><h3>Daily check-in</h3>{boxes}</section>')
    planned = history.planned_items(g, day_id, cat)
    logged = {i for i, rows in ctx.rows_today.items() if rows}
    done_n = sum(1 for p in planned if p in logged)
    names = {it["id"]: it["name"] for it in cat["items"]}
    when = "today" if date == today else ("future: plan only" if date > today else "backdated: logs count for this day")
    count = f" · <span class='count'>{done_n} / {len(planned)} done</span>" if person and planned else ""
    need_html = "<br><b>You need:</b> " + str(escape(day["youneed"])) if day.get("youneed") else ""
    head = (f'<header class="dayhead"><p class="date">{date:%A} · {date.day} {date:%B %Y} · {when}</p>'
            f'<h2>{escape(day.get("title", ""))}</h2><p class="meta">{escape(day.get("summary", ""))}{need_html}</p>'
            f'<p class="who">{who}{count}</p></header>')
    if person and planned and done_n == len(planned):
        foot = '<footer class="dayfoot complete">Day complete ✓</footer>'
    elif person and planned:
        miss = ", ".join(str(escape(names.get(p, p))) for p in planned if p not in logged)
        foot = f'<footer class="dayfoot"><b>Still to do:</b> {miss}</footer>'
    else:
        foot = ""
    links = (f'<p class="links"><a href="history">History</a> · '
             f'<a href="{escape(IMG_BASE)}workout.html#{day_id}">Guide</a></p>')
    return _page(nav, "", head + "".join(cards) + foot + links, ctx.ds, today.isoformat())


def _all_steps(g, day):
    for st in day.get("steps", []):
        if st.get("everyday"):
            yield from g.get("everyday", [])
        else:
            yield st


def _week_nav(date: datetime.date, today: datetime.date) -> str:
    mon = date - datetime.timedelta(days=date.weekday())
    out = [f'<a class="wk" href="./?date={(mon - datetime.timedelta(days=7)).isoformat()}" aria-label="previous week">‹</a>']
    for i, d in enumerate(DAY_IDS):
        dd = mon + datetime.timedelta(days=i)
        cls = " ".join(c for c in ("active" if dd == date else "", "today" if dd == today else "") if c)
        out.append(f'<a href="./?date={dd.isoformat()}" class="{cls}">{d} {dd.day}</a>')
    out.append(f'<a class="wk" href="./?date={(mon + datetime.timedelta(days=7)).isoformat()}" aria-label="next week">›</a>')
    if date != today:
        out.append('<a class="wk" href="./">today</a>')
    return "".join(out)


def _card(ctx: Ctx, smap, st, sub=False) -> str:
    item = smap.get(st.get("slug", ""))
    title = st.get("title", "")
    img = (f'<img src="{escape(IMG_BASE + st["image"])}" alt="How to do: {escape(title)}" loading="lazy">'
           if st.get("image") else "")
    cues = [c for c in (st.get("cues") or []) if isinstance(c, str) and c.strip()]
    cue_html = '<ul class="cues">' + "".join(f"<li>{escape(c)}</li>" for c in cues) + "</ul>" if cues else ""
    box = ""
    if item is not None and item["id"] not in ctx.shown:
        box = _logbox(ctx, item, st.get("slug", ""), st)
    tag = "h4" if sub else "h3"
    return (f'<section class="step{" sub" if sub else ""}"><{tag}><span class="n">{escape(st.get("num", ""))}</span>'
            f'{escape(title)}</{tag}><p class="dose">{escape(st.get("dose", ""))}</p>{img}{cue_html}{box}</section>')


def _logbox(ctx: Ctx, item, slug, st) -> str:
    """Rail (what is logged for this date) + the form, under one anchor."""
    ctx.shown.add(item["id"])
    rows = ctx.rows_today.get(item["id"], [])
    rail = _rail(item, rows)
    note = ""
    if ctx.ok_id == item["id"]:
        note = '<span class="ok">saved ✓</span>'
    elif ctx.err_id == item["id"]:
        note = ('<span class="bad">saved to the log; Home Assistant not updated</span>' if rows
                else '<span class="bad">not saved — check the numbers or try again</span>')
    form = _form(ctx, item, slug, st)
    if rows and item["kind"] == "done":
        form = ""                                   # ticked: nothing more to log that day
    elif rows:
        more = "Log more sets" if item["kind"] in SET_KINDS else "Change"
        form = f'<details class="more"><summary>{more}</summary>{form}</details>'
    cls = "logbox logged" if rows else "logbox"
    return (f'<div class="{cls}" id="ex-{escape(item["id"])}" data-item="{escape(item["id"])}">'
            f'{rail}{form}{note}<span class="queued" hidden>queued, will sync</span></div>')


def _rail(item, rows) -> str:
    if not rows:
        return ""
    last = max(r["timestamp"] for r in rows)
    hhmm = last[11:16] if len(last) >= 16 else ""
    sets = [r for r in rows if any(r[f] is not None for f in history.SET_FIELDS)]
    if item["kind"] == "done":
        what = "done"
    elif item["kind"] == "score":
        sc = [r["score"] for r in rows if r["score"] is not None]
        what = f"score {_fmt(sc[-1])}" if sc else "logged"
    elif item["kind"] == "min":
        what = f"{_fmt(sum(r['minutes'] or 0 for r in sets))} min"
    else:
        what = f"{len(sets)} set{'s' if len(sets) != 1 else ''}"
    return f'<p class="rail">logged {escape(hhmm)} · {escape(what)}</p>'


def _num_input(ctx: Ctx, field: str, name: str, value: str) -> str:
    return (f'<label>{LABEL[field]}<input type="number" inputmode="decimal" name="{name}" value="{escape(value)}" '
            f'step="{STEP[field]}" min="{LIMITS[field][0]}" max="{LIMITS[field][1]}"'
            f'{"" if ctx.can_log else " disabled"}></label>')


def _ha_val(ctx: Ctx, item, field) -> str:
    v = ctx.ha.get(entity(item, field, ctx.person["suffix"])) if ctx.person else None
    if v is None:
        return ""
    try:
        f = float(v)
    except ValueError:
        return ""
    return "" if f == 0 else _fmt(f)


def _form(ctx: Ctx, item, slug, st) -> str:
    kind = item["kind"]
    hidden = (f'<input type="hidden" name="item" value="{escape(item["id"])}">'
              f'<input type="hidden" name="slug" value="{escape(slug)}">'
              f'<input type="hidden" name="date" value="{ctx.ds}">')
    last = ctx.last_session(item["id"])
    btn = ctx.can_log
    if kind == "done":
        if last:
            when = last["date"]
        else:
            when = _last_ha(ctx.ha.get(entity(item, "done", ctx.person["suffix"]), "") if ctx.person else "")
        inner = (f'<input type="hidden" name="done" value="1"><span class="lbl">{escape(item["name"])}'
                 f' — last done {escape(when)}</span>'
                 + ('<button type="submit" class="tick" title="done">✓</button><span class="ticklbl">Done</span>'
                    if btn else ""))
        return f'<form class="log tick" method="post" action="log">{hidden}{inner}</form>'
    if kind not in SET_KINDS:                       # min, score: one box
        f = FIELDS[kind][0]
        val = ""
        if kind == "min":
            if last and last["sets"] and last["sets"][-1].get("minutes"):
                val = _fmt(last["sets"][-1]["minutes"])
            elif not last:
                val = _ha_val(ctx, item, f)
        inner = (f'<span class="lbl">{escape(item["name"])}</span>{_num_input(ctx, f, f, val)}'
                 + ('<button type="submit">Log</button>' if btn else ""))
        return f'<form class="log one" method="post" action="log">{hidden}{inner}</form>'
    n = sets_for(st)
    fields = FIELDS[kind]
    prev = last["sets"] if last else []
    # a second submission continues the day's numbering (the server does the same)
    off = max((r["set_index"] or 0 for r in ctx.rows_today.get(item["id"], [])), default=0)
    rows = []
    for i in range(1, n + 1):
        src = prev[min(i, len(prev)) - 1] if prev else None
        boxes = ""
        for f in fields:
            if src is not None:
                v = src.get(f)
                val = _fmt(v) if v is not None else ""
            else:
                val = _ha_val(ctx, item, f)
            boxes += _num_input(ctx, f, f"{f}_{i}", val)
        rows.append(f'<div class="setrow"><span class="si">{off + i}</span>{boxes}</div>')
    lbl = escape(item["name"]) + (f' <span class="last">· last {escape(last["date"])}</span>' if last else "")
    actions = ('<div class="actions"><button type="button" class="copy" hidden>Copy set 1 down</button>'
               '<button type="submit">Log sets</button></div>' if btn else "")
    return (f'<form class="log sets" method="post" action="log">{hidden}<span class="lbl">{lbl}</span>'
            f'{"".join(rows)}{actions}</form>')


def _last_ha(v: str) -> str:
    if not v or v in ("unknown", "unavailable"):
        return "never"
    return v[:16].replace("T", " ")


NAV_HEAD = (f'<link rel="stylesheet" href="/guides/shared-nav.css?v={NAV_V}">'
            f'<script defer src="/guides/shared-nav.js?v={NAV_V}"></script>')

# crossorigin=use-credentials: the manifest fetch otherwise goes without the Authelia
# cookie and gets the login redirect instead of the JSON.
APP_HEAD = ('<link rel="manifest" href="manifest.webmanifest" crossorigin="use-credentials">'
            '<meta name="theme-color" content="#1b1a18">'
            '<meta name="apple-mobile-web-app-capable" content="yes">'
            '<meta name="mobile-web-app-capable" content="yes">'
            '<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">'
            '<meta name="apple-mobile-web-app-title" content="Workout">'
            '<link rel="apple-touch-icon" href="icon-180.png">')

PAGE_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap');
*{box-sizing:border-box}
:root{--ink:#1b1a18;--paper:#ece2d0;--ember:#c9743f;--ash:#8d8377;--ink-surface:#2b2825;
--ink-hairline:#423d38;--ok:#6f8f5c;--danger:#e0705a;--amber:#d9a441}
body{margin:0;font:15px/1.45 'Space Grotesk',sans-serif;background:var(--ink-surface);color:var(--paper)}
.wrap{max-width:860px;margin:0 auto;padding:12px 16px 40px}
a{color:var(--ember)}
h1{font-size:22px;margin:6px 0 2px}
.who{font-family:'JetBrains Mono',monospace;font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--ash);margin:4px 0 0}
.who .count{color:var(--paper)}
.days{display:flex;flex-wrap:wrap;gap:6px;margin:12px 0;position:sticky;top:0;background:var(--ink-surface);padding:6px 0;z-index:5}
.days a{font-family:'JetBrains Mono',monospace;font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--ash);
text-decoration:none;border:1px solid var(--ink-hairline);border-radius:999px;padding:4px 10px;background:var(--ink)}
.days a.today{border-color:var(--ember)}
.days a.active{color:var(--ink);background:var(--ember);border-color:var(--ember);font-weight:600}
.days a.wk{border-style:dashed}
.dayhead .date{font-family:'JetBrains Mono',monospace;font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--ember);margin:8px 0 0}
h2{font-size:20px;margin:2px 0 2px}
.meta{color:var(--ash);margin:0 0 6px}
.banner{border:1px solid var(--amber);color:var(--amber);border-radius:8px;padding:8px 10px;margin:8px 0;font-size:14px}
.banner button{margin-left:8px}
.step{background:var(--ink);border:1px solid var(--ink-hairline);border-radius:10px;padding:10px 12px;margin:10px 0}
.step.sub{background:transparent;border:none;border-top:1px solid var(--ink-hairline);border-radius:0;padding:8px 0 0}
.step h3,.step h4{margin:0 0 2px;font-weight:600}
.step h3{font-size:17px}.step h4{font-size:15px}
.n{display:inline-block;min-width:1.6em;color:var(--ember);font-family:'JetBrains Mono',monospace}
.dose{color:var(--ash);font-size:14px;margin:0 0 6px}
.step img{display:block;width:100%;height:auto;border-radius:8px;background:#fbfaf7;border:1px solid var(--ink-hairline)}
ul.cues{margin:8px 0 0;padding-left:20px;font-size:14px}
ul.cues li{margin:2px 0}
.logbox{margin-top:8px;border-left:4px solid transparent;padding-left:8px}
.logbox.logged{border-left-color:var(--ok)}
.logbox.queued-on{border-left-color:var(--amber)}
.rail{color:var(--ok);font:500 12px 'JetBrains Mono',monospace;letter-spacing:.06em;margin:4px 0}
.queued{color:var(--amber);font:500 12px 'JetBrains Mono',monospace}
details.more summary{cursor:pointer;color:var(--ash);font-size:13px;padding:4px 0}
form.log{display:flex;flex-wrap:wrap;align-items:flex-end;gap:8px;padding:8px;border:1px dashed var(--ink-hairline);border-radius:8px}
form.log .lbl{flex-basis:100%;font-size:13px;color:var(--ash)}
form.log label{display:flex;flex-direction:column;font-family:'JetBrains Mono',monospace;font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--ash)}
form.log input{width:92px;font:16px 'Space Grotesk',sans-serif;padding:7px 8px;border-radius:7px;border:1px solid var(--ink-hairline);background:var(--ink-surface);color:var(--paper)}
form.log button{font:600 15px 'Space Grotesk',sans-serif;padding:8px 16px;border-radius:7px;border:1px solid var(--ember);background:var(--ember);color:var(--ink);cursor:pointer;min-height:40px}
form.sets .setrow{display:flex;align-items:flex-end;gap:8px;flex-basis:100%}
form.sets .si{font:500 13px 'JetBrains Mono',monospace;color:var(--ember);min-width:1.4em;padding-bottom:10px}
form.sets .actions{display:flex;gap:8px;flex-basis:100%;flex-wrap:wrap}
form.sets button.copy{background:none;color:var(--ember)}
form.tick{align-items:center}
form.tick .lbl{flex:1 1 auto;flex-basis:auto;font-size:14px}
form.tick button.tick{width:34px;height:34px;min-height:0;padding:0;border:2px solid var(--ash);border-radius:7px;background:none;color:var(--ok);font-size:18px}
form.tick button.tick:hover{border-color:var(--ok);background:var(--ink-surface)}
form.tick .ticklbl{font-size:13px;color:var(--ash)}
.ok{color:var(--ok);font-size:13px}.bad{color:var(--danger);font-size:13px}
.empty{color:var(--ash);padding:24px;text-align:center}
.dayfoot{border:1px solid var(--ink-hairline);border-radius:10px;padding:10px 12px;margin:14px 0 6px;color:var(--ash)}
.dayfoot.complete{border-color:var(--ok);color:var(--ok);font-weight:600}
.links{color:var(--ash)}
:focus-visible{outline:2px solid var(--ember);outline-offset:2px}
"""

# Progressive: without JS the forms post normally and the server 303s back.
# With JS: submit with fetch (JSON reply); on a network failure the submission is
# queued in localStorage and replayed on load / `online` / when the app comes back
# to the foreground / every 30 s. A login redirect or 401 keeps the queue, drops the
# cached pages and asks for a reload (sign-in). A 4xx answer drops that entry (it
# would never land). Each submission carries a qid so a replay of a POST that did
# land (answer lost) is not written twice (server keeps the last 500 qids).
PAGE_JS = r"""
(function () {
  var Q = 'workout-queue-v1', body0 = document.body;
  document.documentElement.classList.add('js');
  var pageDate = body0.getAttribute('data-date');
  function load() { try { return JSON.parse(localStorage.getItem(Q) || '[]'); } catch (e) { return []; } }
  function save(q) { try { localStorage.setItem(Q, JSON.stringify(q)); return true; } catch (e) { return false; } }
  function rid() { return Date.now().toString(36) + Math.random().toString(36).slice(2, 10); }
  function banner(html) {
    var b = document.getElementById('qbanner');
    if (!b) { return; }
    if (!html) { b.hidden = true; return; }
    b.innerHTML = html; b.hidden = false;
  }
  function localToday() {
    try { return new Intl.DateTimeFormat('en-CA', {timeZone: body0.getAttribute('data-tz')}).format(new Date()); }
    catch (e) { return ''; }
  }
  function paint() {
    var q = load();
    document.querySelectorAll('.logbox').forEach(function (box) {
      var on = q.some(function (x) { return x.item === box.getAttribute('data-item') && x.date === pageDate; });
      box.classList.toggle('queued-on', on);
      box.querySelector('.queued').hidden = !on;
    });
    var t = localToday(), then = body0.getAttribute('data-today'), msg = '';
    if (t && then && t !== then) {
      msg = 'Saved copy from ' + then + '. Logs made here count for ' + pageDate + '. <a href="./">Open today</a>';
    }
    if (q.length) {
      msg += (msg ? '<br>' : '') + q.length + ' log' + (q.length === 1 ? '' : 's') + ' queued, will sync when online.';
    }
    banner(msg);
  }
  function send(url, body) {
    return fetch(url, {method: 'POST', body: body, credentials: 'same-origin', redirect: 'manual',
                       headers: {'X-Workout-Queue': '1', 'Accept': 'application/json'}})
      .then(function (r) {
        if (r.type === 'opaqueredirect' || r.status === 401 || r.status === 0) { return {kind: 'auth'}; }
        if (r.status >= 500) { return {kind: 'retry'}; }
        return r.json().then(function (d) { return {kind: r.ok ? 'ok' : 'bad', next: d.next, status: r.status}; },
                             function () { return {kind: r.ok ? 'ok' : 'bad', status: r.status}; });
      }, function () { return {kind: 'retry'}; });
  }
  function authExpired() {
    try {
      if (window.caches) {
        caches.keys().then(function (ks) { ks.forEach(function (k) { if (k.indexOf('workout-') === 0) { caches.delete(k); } }); });
      }
    } catch (e) {}
    banner('Your login has expired. Queued logs stay on this phone. ' +
           '<button type="button" onclick="location.reload()">Reload to sign in</button>');
  }
  var busy = false;
  function replay() {
    if (busy || !load().length || navigator.onLine === false) { paint(); return; }
    busy = true;
    var synced = 0, dropped = 0;
    (function next() {
      var q = load();
      if (!q.length) {
        busy = false; paint();
        if (synced || dropped) {
          banner('Synced ' + synced + ' queued log' + (synced === 1 ? '' : 's') +
                 (dropped ? ' (' + dropped + ' refused: check the numbers)' : '') + '.');
          setTimeout(function () { location.reload(); }, 900);
        }
        return;
      }
      var it = q[0];
      send(it.url, new URLSearchParams(it.body)).then(function (r) {
        if (r.kind === 'ok' || r.kind === 'bad') {
          save(load().filter(function (x) { return x.qid !== it.qid; }));
          if (r.kind === 'ok') { synced++; } else { dropped++; }
          next();
        } else if (r.kind === 'auth') { busy = false; paint(); authExpired(); }
        else { busy = false; paint(); }
      });
    })();
  }
  document.querySelectorAll('button.copy').forEach(function (b) { b.hidden = false; });
  document.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('button.copy');
    if (!b) { return; }
    var rows = b.form.querySelectorAll('.setrow');
    if (!rows.length) { return; }
    var first = rows[0].querySelectorAll('input');
    for (var i = 1; i < rows.length; i++) {
      rows[i].querySelectorAll('input').forEach(function (inp, j) { if (first[j]) { inp.value = first[j].value; } });
    }
  });
  document.addEventListener('submit', function (e) {
    var f = e.target;
    if (!f.matches || !f.matches('form.log') || !window.fetch || !window.URLSearchParams) { return; }
    e.preventDefault();
    var body = new URLSearchParams(new FormData(f));
    body.set('qid', rid());
    var btn = f.querySelector('button[type=submit]');
    if (btn) { btn.disabled = true; }
    function enqueue() {
      var q = load(), g = f.querySelector('input[name=item]'), d = f.querySelector('input[name=date]');
      q.push({qid: body.get('qid'), url: f.action, body: body.toString(), item: g ? g.value : '',
              date: d ? d.value : pageDate, at: Date.now()});
      if (!save(q)) { f.submit(); return; }      // no storage (private mode): plain post
      if (btn) { btn.disabled = false; }
      paint();
    }
    send(f.action, body).then(function (r) {
      if (r.next) { location.replace(r.next); return; }
      if (r.kind === 'auth') { enqueue(); authExpired(); return; }
      if (r.kind === 'retry') { enqueue(); return; }
      if (btn) { btn.disabled = false; }
      banner('Not saved (HTTP ' + (r.status || '?') + ').');
    });
  });
  window.addEventListener('online', replay);
  document.addEventListener('visibilitychange', function () { if (document.visibilityState === 'visible') { replay(); } });
  setInterval(function () { if (load().length) { replay(); } }, 30000);
  paint();
  replay();
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('sw.js', {scope: './'}).catch(function () {});
  }
})();
"""

# The service worker: network-first pages (the cached copy when offline), cache-first
# for the shell assets and posters. Only same-origin 200s that were not redirected are
# ever cached, so an Authelia login redirect never lands in the cache. POSTs and the
# JSON routes are not touched (the page queues the POSTs).
SW_JS = r"""
var V = 'workout-__V__';
var SHELL = ['./', 'manifest.webmanifest', 'icon-180.png', 'icon-192.png', 'icon-512.png',
             '/guides/shared-nav.css?v=__NAV__', '/guides/shared-nav.js?v=__NAV__'];
var SCOPE = new URL('./', self.location).pathname;
function cacheable(r) { return r && r.status === 200 && r.type === 'basic' && !r.redirected; }
function pageKey(u) {
  var k = new URL(u); k.hash = ''; k.searchParams.delete('ok'); k.searchParams.delete('err');
  return k.href;
}
function localToday() {
  try { return new Intl.DateTimeFormat('en-CA', {timeZone: '__TZ__'}).format(new Date()); } catch (e) { return ''; }
}
self.addEventListener('install', function (e) {
  e.waitUntil(caches.open(V).then(function (c) {
    return Promise.all(SHELL.map(function (u) {
      return fetch(u, {credentials: 'same-origin', redirect: 'manual'})
        .then(function (r) { if (cacheable(r)) { return c.put(u, r); } }).catch(function () {});
    }));
  }).then(function () { return self.skipWaiting(); }));
});
self.addEventListener('activate', function (e) {
  e.waitUntil(caches.keys().then(function (ks) {
    return Promise.all(ks.filter(function (k) { return k.indexOf('workout-') === 0 && k !== V; })
                         .map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});
self.addEventListener('fetch', function (e) {
  var req = e.request;
  if (req.method !== 'GET') { return; }
  var url = new URL(req.url);
  if (url.origin !== self.location.origin) { return; }
  if (req.mode === 'navigate') {
    if (url.pathname.indexOf(SCOPE) !== 0) { return; }
    e.respondWith(fetch(req).then(function (r) {
      if (cacheable(r)) { var copy = r.clone(); caches.open(V).then(function (c) { c.put(pageKey(req.url), copy); }); }
      return r;
    }).catch(function () {
      return caches.open(V).then(function (c) {
        var tries = [pageKey(req.url)];
        if (!url.searchParams.get('date')) { tries.push(new URL('./?date=' + localToday(), self.location).href); }
        tries.push(new URL('./', self.location).href);
        return (function first(i) {
          if (i >= tries.length) {
            return new Response('<!doctype html><meta name=viewport content="width=device-width">' +
              '<body style="font:16px sans-serif;background:#2b2825;color:#ece2d0;padding:24px">' +
              'Offline, and this page has not been saved yet. Open it once while online.',
              {headers: {'Content-Type': 'text/html'}});
          }
          return c.match(tries[i]).then(function (m) { return m || first(i + 1); });
        })(0);
      });
    }));
    return;
  }
  if (url.pathname.indexOf(SCOPE + 'api/') === 0 || url.pathname === SCOPE + 'log') { return; }
  var mine = url.pathname.indexOf(SCOPE) === 0 || url.pathname.indexOf('__IMG__') === 0 ||
             url.pathname.indexOf('/guides/shared-nav.') === 0;
  if (!mine) { return; }
  e.respondWith(caches.open(V).then(function (c) {
    return c.match(req).then(function (hit) {
      var net = fetch(req).then(function (r) { if (cacheable(r)) { c.put(req, r.clone()); } return r; });
      if (hit) { net.catch(function () {}); return hit; }
      return net;
    });
  }));
});
"""


def _page(nav: str, who: str, body: str, date: str, today: str) -> str:
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'><title>Workout</title>"
            f"{APP_HEAD}{NAV_HEAD}<style>{PAGE_CSS}</style></head>"
            f"<body data-date='{escape(date)}' data-today='{escape(today)}' data-tz='{escape(TZ_NAME)}'><div class='wrap'>"
            "<h1>Workout</h1>"
            + (f"<div class='who'>{who}</div>" if who else "")
            + (f"<nav class='days'>{nav}</nav>" if nav else "")
            + "<div id='qbanner' class='banner' hidden></div>"
            + body
            + "<p class='meta'>Every set goes to the long-term log; today's last set also goes to Home Assistant "
              "(your Workout page).</p>"
            f"</div><script>{PAGE_JS}</script></body></html>")


if __name__ == "__main__":
    ensure_schema()            # v1 -> v2 before the first request (raises on an unknown header)
    serve(app, host="0.0.0.0", port=8080, threads=4)
