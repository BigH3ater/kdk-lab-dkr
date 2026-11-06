#!/usr/bin/env python3
"""dashboard-workout: the day's exercise instructions with a log form next to each
exercise, at home.kmkdp.com/workout/ (Traefik StripPrefix /workout, Authelia
ForwardAuth in front).

Reads (all read-only mounts):
  /guide/workout.json        day -> steps (title, dose, picture, slug); written by
                             stacks/dashboard/guides/gen-workout-guide.py on the laptop
                             (host-managed, /opt/kdk-lab/dashboard/workout). Pictures are
                             served by the guides nginx at /guides/workout/exercise/...
  /catalog/workout_catalog.json  the ONE exercise list (stacks/home-assistant/tools):
                             slug -> HA helper id + kind, and who the people are.
Writes:
  Home Assistant             input_number.set_value / input_datetime.set_datetime on the
                             person's own helpers (long-lived token, env HA_TOKEN)
  /log/workout-log.csv       one line per logged set or tick, so history outlives the
                             10-day recorder (host /opt/kdk-lab/dashboard/workout-log).
                             Schema v2 (see history.py); a v1 file is migrated at
                             startup, with a workout-log.v1-<YYYYMMDD>.bak beside it.

Person = the Authelia Remote-User header mapped through the catalog's people
(jmack -> Jacob, rach -> Rachel). Anyone else sees the page read-only.

Served:
  GET  /           ?day=mon..sun (default today)   instructions + forms
  POST /log        one exercise's values -> HA + CSV, then 303 back to the day
  GET  /api/history.json  ?person=&item=&since=   one exercise's sessions (JSON)
  GET  /api/week.json     ?person=&week=2026-W45  ISO-week goal counters (JSON)
  GET  /healthz    "ok"
The JSON routes are for the catalog people only (either person's data; default = you).
"""
from __future__ import annotations

import csv
import datetime
import json
import logging
import os
import threading
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
TZ = ZoneInfo(os.environ.get("TZ", "America/Chicago"))
history.TZ = TZ

# Same kinds as stacks/home-assistant/tools/gen_workout.py (keep in sync).
FIELDS = {"w": ["weight", "reps"], "r": ["reps"], "t": ["seconds"], "wt": ["weight", "seconds"],
          "wl": ["weight", "lengths"], "min": ["minutes"], "score": ["score"], "done": ["done"]}
LIMITS = {"weight": (0, 200), "reps": (0, 50), "seconds": (0, 600), "lengths": (0, 20), "minutes": (0, 240),
          "score": (0, 10)}
LABEL = {"weight": "lb", "reps": "reps", "seconds": "sec", "lengths": "lengths", "minutes": "min", "score": "0–10"}
STEP = {"weight": "0.1", "reps": "1", "seconds": "1", "lengths": "1", "minutes": "1", "score": "1"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("workout")
app = Flask(__name__)
_csv_lock = threading.Lock()
_schema_checked = False


# ------------------------------------------------------------------ data
def _load_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def catalog() -> dict:
    """Re-read per request (tiny file) so a dashboard redeploy or catalog change
    needs no restart."""
    return _load_json(CATALOG)


def guide() -> dict | None:
    try:
        return _load_json(GUIDE)
    except (OSError, ValueError):
        log.warning("guide data %s missing or unreadable", GUIDE)
        return None


def person_for(cat: dict, login: str) -> dict | None:
    login = (login or "").strip()
    return next((p for p in cat["people"] if p.get("login") == login), None)


def entity(item: dict, field: str, suffix: str) -> str:
    domain = "input_datetime" if field == "done" else "input_number"
    return f"{domain}.ex_{item['id']}_{field}{suffix}"


def slug_map(cat: dict) -> dict[str, dict]:
    return {s: it for it in cat["items"] for s in it.get("slugs", [])}


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


def csv_append(person: dict, item: dict, slug: str, values: dict[str, float], done: bool,
               set_index: int | None = None, source: str = "web") -> None:
    """One v2 row. `values` may hold weight/reps/seconds/lengths/minutes/score.
    set_index defaults to 1 when a set (any weight/reps/seconds/lengths/minutes) is
    logged, blank for a tick or a score."""
    now = datetime.datetime.now(TZ)
    if set_index is None and any(f in values for f in history.SET_FIELDS):
        set_index = 1
    row = {"timestamp": now.isoformat(timespec="seconds"), "date": now.date().isoformat(), "person": person["key"],
           "item": item["id"], "slug": slug, "set_index": str(set_index) if set_index else "",
           "done": "1" if done else "", "source": source}
    for f in history.SET_FIELDS + ["score"]:
        row[f] = _fmt(values[f]) if f in values else ""
    with _csv_lock:
        ensure_schema()
        new = not LOG_CSV.exists() or LOG_CSV.stat().st_size == 0
        with LOG_CSV.open("a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=CSV_HEAD)
            if new:
                w.writeheader()
            w.writerow(row)


def _fmt(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


def parse_values(item: dict, form) -> dict[str, float]:
    """Validated numbers for the item's numeric fields; blank fields are skipped."""
    out = {}
    for f in FIELDS[item["kind"]]:
        if f == "done":
            continue
        raw = (form.get(f) or "").strip()
        if not raw:
            continue
        v = float(raw)  # ValueError -> 400
        lo, hi = LIMITS[f]
        if not (lo <= v <= hi) or v != v:
            raise ValueError(f"{f} out of range")
        out[f] = v
    return out


# ------------------------------------------------------------------ routes
@app.get("/healthz")
def healthz():
    return "ok"


@app.get("/")
def index():
    cat = catalog()
    person = person_for(cat, request.headers.get("Remote-User", ""))
    today = DAY_IDS[datetime.datetime.now(TZ).weekday()]
    day_id = request.args.get("day", today)
    if day_id not in DAY_IDS:
        day_id = today
    g = guide()
    return Response(render_page(cat, g, day_id, today, person, request.args.get("ok", ""),
                                request.args.get("err", "")), mimetype="text/html")


@app.post("/log")
def log_entry():
    cat = catalog()
    person = person_for(cat, request.headers.get("Remote-User", ""))
    if person is None:
        return Response("Logging is for Jacob and Rachel only.", status=403)
    origin = request.headers.get("Origin")
    if origin and urlparse(origin).hostname not in ALLOWED_ORIGINS:
        return Response("bad origin", status=403)
    items = {it["id"]: it for it in cat["items"]}
    item = items.get(request.form.get("item", ""))
    day = request.form.get("day", "")
    day = day if day in DAY_IDS else ""
    back = f"./?day={day}" if day else "./"
    if item is None:
        return Response("unknown exercise", status=400)
    try:
        values = parse_values(item, request.form)
    except ValueError:
        return redirect(f"{back}&err={item['id']}#ex-{item['id']}" if day else f"./?err={item['id']}", code=303)
    done = item["kind"] == "done" and request.form.get("done") == "1"
    if not values and not done:
        return redirect(f"{back}#ex-{item['id']}", code=303)
    slug = (request.form.get("slug") or "")[:64]
    csv_append(person, item, slug, values, done)        # history first: it is the durable record
    ok = False
    try:
        ok = ha_write(item, values, done, person["suffix"])
    except requests.RequestException:
        log.exception("HA write failed for %s", item["id"])
    log.info("logged %s for %s (ha=%s)", item["id"], person["key"], ok)
    flag = "ok" if ok else "err"
    sep = "&" if day else "?"
    return redirect(f"{back}{sep}{flag}={item['id']}#ex-{item['id']}", code=303)


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
    week = (request.args.get("week") or "").strip() or history.iso_week_of(datetime.datetime.now(TZ).date())
    try:
        history.week_monday(week)
    except ValueError:
        return _json({"error": "week must look like 2026-W45"}, 400)
    g = guide()
    out = _history().week_summary(person["key"], week, lambda d: history.planned_items(g, d, cat), cat)
    out["plan_published"] = g is not None
    return _json(out)


# ------------------------------------------------------------------ render
def render_page(cat, g, day_id, today, person, ok_id, err_id) -> str:
    smap = slug_map(cat)
    nav = "".join(
        f'<a href="./?day={d}" class="{"active" if d == day_id else ""}{" today" if d == today else ""}">{d}</a>'
        for d in DAY_IDS)
    who = (f'Logging as <b>{escape(person["name"])}</b>' if person
           else "Read-only: logging is for Jacob and Rachel.")
    if g is None:
        body = '<p class="empty">The workout plan has not been published yet (workout.json missing).</p>'
        return _page(nav, who, body)
    day = next((d for d in g["days"] if d["id"] == day_id), None)
    if day is None:
        return _page(nav, who, '<p class="empty">No plan for this day.</p>')
    shown: set[str] = set()
    steps = []
    for st in day["steps"]:
        if st.get("everyday"):
            inner = "".join(_step(cat, smap, s, day_id, person, shown, ok_id, err_id, sub=True) for s in g["everyday"])
            steps.append(f'<section class="step every"><h3><span class="n">{escape(st["num"])}</span>'
                         f'{escape(st["title"])}</h3><p class="dose">{escape(st["dose"])}</p>{inner}</section>')
        else:
            steps.append(_step(cat, smap, st, day_id, person, shown, ok_id, err_id))
    # items with no picture of their own (scores) and routines not reached above
    rest = [it for it in cat["items"] if it["id"] not in shown and (it["kind"] in ("score", "done") and
            (not it.get("slugs") or it["group"] == "daily"))]
    if rest:
        forms = "".join(_form(it, "", day_id, person, ok_id, err_id) for it in rest)
        steps.append(f'<section class="step"><h3>Daily check-in</h3>{forms}</section>')
    head = (f'<h2>{escape(day["title"])}</h2><p class="meta">{escape(day["summary"])}'
            f'{"<br><b>You need:</b> " + str(escape(day["youneed"])) if day["youneed"] else ""}</p>')
    states = ha_states({entity(it, f, person["suffix"]) for it in cat["items"] for f in FIELDS[it["kind"]]}) if person else {}
    html = head + "".join(steps)
    for eid, val in states.items():                    # pre-fill the boxes with the current HA values
        html = html.replace(f'data-e="{eid}" value=""', f'data-e="{eid}" value="{escape(_clean(val))}"')
        html = html.replace(f'data-last="{eid}"></span>', f'data-last="{eid}">{escape(_last(val))}</span>')
    return _page(nav, who, html)


def _clean(v: str) -> str:
    try:
        f = float(v)
    except ValueError:
        return ""
    return "" if f == 0 else _fmt(f)


def _last(v: str) -> str:
    if not v or v in ("unknown", "unavailable"):
        return "never"
    return v[:16].replace("T", " ")


def _step(cat, smap, st, day_id, person, shown, ok_id, err_id, sub=False) -> str:
    item = smap.get(st.get("slug", ""))
    img = (f'<img src="{escape(IMG_BASE + st["image"])}" alt="How to do: {escape(st["title"])}" loading="lazy">'
           if st.get("image") else "")
    form = ""
    if item is not None and item["id"] not in shown:
        shown.add(item["id"])
        form = _form(item, st.get("slug", ""), day_id, person, ok_id, err_id)
    tag = "h4" if sub else "h3"
    return (f'<section class="step{" sub" if sub else ""}"><{tag}><span class="n">{escape(st["num"])}</span>'
            f'{escape(st["title"])}</{tag}><p class="dose">{escape(st["dose"])}</p>{img}{form}</section>')


def _form(item, slug, day_id, person, ok_id, err_id) -> str:
    sfx = person["suffix"] if person else ""
    note = ""
    if ok_id == item["id"]:
        note = '<span class="ok">saved ✓</span>'
    elif err_id == item["id"]:
        note = '<span class="bad">not saved — check the numbers or try again</span>'
    if item["kind"] == "done":
        e = entity(item, "done", sfx)
        btn = '<button type="submit" name="done" value="1">Done ✓</button>' if person else ""
        inner = f'<span class="lbl">{escape(item["name"])} — last done <span data-last="{e}"></span></span>{btn}'
    else:
        boxes = "".join(
            f'<label>{LABEL[f]}<input type="number" inputmode="decimal" name="{f}" step="{STEP[f]}" '
            f'min="{LIMITS[f][0]}" max="{LIMITS[f][1]}" data-e="{entity(item, f, sfx)}" value=""'
            f'{"" if person else " disabled"}></label>'
            for f in FIELDS[item["kind"]])
        btn = '<button type="submit">Log</button>' if person else ""
        inner = f'<span class="lbl">{escape(item["name"])}</span>{boxes}{btn}'
    return (f'<form class="log" id="ex-{escape(item["id"])}" method="post" action="log">'
            f'<input type="hidden" name="item" value="{escape(item["id"])}">'
            f'<input type="hidden" name="slug" value="{escape(slug)}">'
            f'<input type="hidden" name="day" value="{escape(day_id)}">{inner}{note}</form>')


NAV_HEAD = ('<link rel="stylesheet" href="/guides/shared-nav.css?v=nav20261104">'
            '<script defer src="/guides/shared-nav.js?v=nav20261104"></script>')

PAGE_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap');
*{box-sizing:border-box}
:root{--ink:#1b1a18;--paper:#ece2d0;--ember:#c9743f;--ash:#8d8377;--ink-surface:#2b2825;
--ink-hairline:#423d38;--ok:#6f8f5c;--danger:#e0705a}
body{margin:0;font:15px/1.45 'Space Grotesk',sans-serif;background:var(--ink-surface);color:var(--paper)}
.wrap{max-width:860px;margin:0 auto;padding:12px 16px 40px}
h1{font-size:22px;margin:6px 0 2px}
.who{font-family:'JetBrains Mono',monospace;font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--ash)}
.days{display:flex;flex-wrap:wrap;gap:6px;margin:12px 0;position:sticky;top:0;background:var(--ink-surface);padding:6px 0;z-index:5}
.days a{font-family:'JetBrains Mono',monospace;font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--ash);
text-decoration:none;border:1px solid var(--ink-hairline);border-radius:999px;padding:4px 11px;background:var(--ink)}
.days a.today{border-color:var(--ember)}
.days a.active{color:var(--ink);background:var(--ember);border-color:var(--ember);font-weight:600}
h2{font-size:20px;margin:10px 0 2px}
.meta{color:var(--ash);margin:0 0 10px}
.step{background:var(--ink);border:1px solid var(--ink-hairline);border-radius:10px;padding:10px 12px;margin:10px 0}
.step.sub{background:transparent;border:none;border-top:1px solid var(--ink-hairline);border-radius:0;padding:8px 0 0}
.step h3,.step h4{margin:0 0 2px;font-weight:600}
.step h3{font-size:17px}.step h4{font-size:15px}
.n{display:inline-block;min-width:1.6em;color:var(--ember);font-family:'JetBrains Mono',monospace}
.dose{color:var(--ash);font-size:14px;margin:0 0 6px}
.step img{display:block;width:100%;height:auto;border-radius:8px;background:#fbfaf7;border:1px solid var(--ink-hairline)}
form.log{display:flex;flex-wrap:wrap;align-items:flex-end;gap:8px;margin-top:8px;padding:8px;border:1px dashed var(--ink-hairline);border-radius:8px}
form.log .lbl{flex-basis:100%;font-size:13px;color:var(--ash)}
form.log label{display:flex;flex-direction:column;font-family:'JetBrains Mono',monospace;font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--ash)}
form.log input{width:92px;font:16px 'Space Grotesk',sans-serif;padding:7px 8px;border-radius:7px;border:1px solid var(--ink-hairline);background:var(--ink-surface);color:var(--paper)}
form.log button{font:600 15px 'Space Grotesk',sans-serif;padding:8px 16px;border-radius:7px;border:1px solid var(--ember);background:var(--ember);color:var(--ink);cursor:pointer}
.ok{color:var(--ok);font-size:13px}.bad{color:var(--danger);font-size:13px}
.empty{color:var(--ash);padding:24px;text-align:center}
:focus-visible{outline:2px solid var(--ember);outline-offset:2px}
"""


def _page(nav: str, who: str, body: str) -> str:
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'><title>Workout log</title>"
            f"{NAV_HEAD}<style>{PAGE_CSS}</style></head><body><div class='wrap'>"
            f"<h1>Workout</h1><div class='who'>{who}</div><nav class='days'>{nav}</nav>{body}"
            "<p class='meta'>Values go to Home Assistant (your Workout page) and to the long-term log. "
            "Instructions and pictures: <a href='/guides/workout/workout.html' style='color:var(--ember)'>Workout guide</a>.</p>"
            "</div></body></html>")


if __name__ == "__main__":
    ensure_schema()            # v1 -> v2 before the first request (raises on an unknown header)
    serve(app, host="0.0.0.0", port=8080, threads=4)
