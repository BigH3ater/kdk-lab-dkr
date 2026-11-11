#!/usr/bin/env python3
"""dashboard-mealplan: render the Tandoor weekly meal plan for the family home
dashboard, and let household members submit a star rating from the dashboard.

Why a sidecar (not the browser calling Tandoor directly): Tandoor has DRF
**token auth disabled** -- only Session + OAuth2. So we log in with a dedicated
Tandoor account (username/password, op-injected) to get a session + CSRF cookie, then
call the JSON API. The account must be a member of the "Mack House" space or the plan
comes back empty (every Tandoor object is space-scoped).

Served (behind Traefik, StripPrefix /meal):
  GET  /            -> HTML week grid (the sidebar's "This Week" page)
  GET  /mealplan.json -> compact JSON (optional; nothing in the lab reads it now)
  POST /rate        -> create a CookLog rating, then redirect back to /
  GET  /healthz     -> "ok"
  GET  /kitchen, /kitchen/ingredients, /kitchen/steps, /kitchen/pantry,
       /kitchen/step-photo/<id>
                    -> the kitchen board (iPad) and its sub-views (kitchen.py)
  POST /groceries/add, /groceries/reorder (ajax=1, next=pantry|home)
                    -> the only grocery writes the kitchen pages make
  GET  /home        -> the household page (phone/laptop)
  GET  /kitchen/devices, POST /kitchen/devices/{pair,revoke,revoke-all}
                    -> kitchen device pairing (home.kmkdp.com, KITCHEN_ADMINS only)

admin.kmkdp.com (internal Traefik, forwardauth, Authelia user:jmack) reaches this
same app for the lab admin page only: GET / and /static/*, ADMIN_USERS only
(403 otherwise), everything else 404 there (admin.py).

kitchen.kmkdp.com (internal Traefik, NO forwardauth) reaches this same app for
paired kitchen devices only: /, /ingredients, /steps, /pantry, /chores, /weather,
/step-photo/<id>,
/manifest.webmanifest, /static/*, POST /tasks/complete, /groceries/add,
/groceries/reorder, and /pair. Everything else is a 404 there, Remote-* headers
are ignored, and every other request needs the signed device cookie (devices.py).

RATING ATTRIBUTION CAVEAT (by design, per the approved plan): Tandoor ratings are
per-user (CookLog), so every rating sent through this shared account is recorded as
that account's rating. We ask "who's rating?" (Jacob/Rachel) and stamp the name into the
CookLog comment so it's not lost. For a true per-user rating, the "open recipe" link
goes to the recipe page where each person rates under their own login.

NOTE: this could not be tested against the live Tandoor 2.6.15 at authoring time.
The login flow (django-allauth) and the meal-plan / cook-log field names are the
load-bearing bits to confirm on first deploy -- they are called out inline below.
"""
from __future__ import annotations
import os, re, json, datetime, logging, threading, time
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo
import requests
import urllib3
urllib3.disable_warnings()
from flask import Flask, request, Response, redirect, g
from markupsafe import escape  # HTML-escape any value rendered into a page (CodeQL py/reflective-xss)
from waitress import serve
import newpi  # New Pi product-link lookup (reads the mounted foods.yaml)
import stock  # pantry list + freezer/thaw rules (mounted cleanup.yaml + planner config)
import homecal  # household ICS calendar (kitchen board + household page)
import kitchen  # kitchen board + household page rendering
import vacuum  # robot vacuum line from Home Assistant
import devices  # kitchen.kmkdp.com device pairing (signed cookie + registry)
import forced  # the add box's "Manual list instead" registry (/state)
import admin  # admin.kmkdp.com: status strip, alerts, sign-ins, syslog, charts

TANDOOR_URL = os.environ.get("TANDOOR_URL", "https://recipes.kmkdp.com").rstrip("/")
USER = os.environ.get("TANDOOR_USER", "")
PASS = os.environ.get("TANDOOR_PASS", "")
TZ = ZoneInfo(os.environ.get("TZ", "America/Chicago"))
# Rater identity comes from Authelia ForwardAuth (Remote-User / Remote-Name
# headers injected by the internal Traefik) -- no picker. Map lab usernames to
# household display names; unknown users fall back to Remote-Name or the login.
USER_DISPLAY = {"jmack": "Jacob", "rach": "Rachel"}

def remote_user() -> str:
    """The Authelia login (Remote-User) on home.kmkdp.com. Always "" on
    kitchen.kmkdp.com: that host has no forwardauth, so a Remote-* header
    there can only come from the client (Traefik strips them as well)."""
    if kitchen.on_kitchen_host():
        return ""
    return (request.headers.get("Remote-User") or "").strip()


def rater_from_headers() -> str:
    login = remote_user()
    if login in USER_DISPLAY:
        return USER_DISPLAY[login]
    name = "" if kitchen.on_kitchen_host() else (request.headers.get("Remote-Name") or "").strip()
    return name or login or "family"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mealplan")
app = Flask(__name__)
# /static (served at /meal/static after the StripPrefix): home.css, home.js,
# fonts, icons. css/js carry ?v=<content hash>; the rest changes rarely.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 86400

# One shared requests.Session (keep-alive: every call reuses the same TLS
# connection pool); re-login lazily if the session goes stale (403/redirect).
_sess: requests.Session | None = None
_login_lock = threading.Lock()

# Upstream call counter for the timing log lines. A refresh sets _tl.stats to a
# dict; every Tandoor call made on that thread -- or on a recipe prefetch worker
# handed the same dict -- adds one call and its milliseconds.
_tl = threading.local()
_stats_lock = threading.Lock()


def _count(t0: float) -> None:
    st = getattr(_tl, "stats", None)
    if st is not None:
        with _stats_lock:
            st["calls"] += 1
            st["ms"] += (time.monotonic() - t0) * 1000


def _new_stats() -> dict:
    return {"calls": 0, "ms": 0.0}


class Feed:
    """A small server-side cache for one upstream (weather, calendar, HA,
    Vikunja), so a page render never waits on it when a value exists:
      * fresh (younger than `ttl`)  -> served as is;
      * stale                       -> served, and one background refresh runs;
      * none yet                    -> fetched on the request (with the fetch's
                                       own timeout), or None with block=False.
    A failed refresh keeps the last good value (logged by type only: some
    upstream URLs are secrets). The home warmer thread refreshes every feed
    when it goes stale, so the request path is normally cache-only."""

    def __init__(self, name: str, ttl: float, fetch, retry: float | None = None):
        self.name, self.ttl, self.fetch = name, ttl, fetch
        self.retry = min(ttl, 60) if retry is None else retry   # wait after a failed try
        self.value, self.ts, self.error = None, 0.0, ""
        self.tried = 0.0                  # last attempt, good or bad
        self._lock = threading.Lock()

    def fresh(self) -> bool:
        return self.value is not None and time.time() - self.ts < self.ttl

    def due(self) -> bool:
        """Stale, and not retried in the last `retry` s (default min(ttl, 60):
        a dead upstream is retried once a minute, not on every request)."""
        return not self.fresh() and time.time() - self.tried >= self.retry

    def refresh(self):
        with self._lock:
            self.tried = time.time()
            try:
                v = self.fetch()
            except Exception as e:
                self.error = type(e).__name__
                log.warning("feed %s refresh failed: %s", self.name, self.error)
                return self.value
            self.value, self.ts, self.error = v, time.time(), ""
            return v

    def kick(self) -> None:
        if self._lock.locked():
            return
        threading.Thread(target=self.refresh, daemon=True, name=f"feed-{self.name}").start()

    def get(self, block: bool = True):
        if self.fresh():
            return self.value
        if self.value is not None or not block or self.tried:
            if self.due():
                self.kick()
            return self.value             # stale, or None after a failed try
        return self.refresh()             # never tried yet: fetch now

    def set(self, value) -> None:
        """Seed a value (tests, fixtures)."""
        self.value, self.ts, self.error = value, time.time(), ""
        self.tried = self.ts


def _login() -> requests.Session:
    """Log in via django-allauth's headless *browser* API and return a session.
    Tandoor v2 replaced the server-rendered /accounts/login/ form with the Vue SPA,
    so that page no longer sets a csrftoken cookie and the old form POST got
    "CSRF cookie not set" 403s (2026-10-25). The config GET sets the cookie; the
    login POST must echo it as X-CSRFToken. A sessionid cookie comes back, which
    DRF SessionAuthentication accepts for /api/ (ratings re-read the rotated
    csrftoken cookie)."""
    s = requests.Session()
    s.headers["User-Agent"] = "kdk-dashboard-mealplan"
    auth = f"{TANDOOR_URL}/_allauth/browser/v1"
    r = s.get(f"{auth}/config", timeout=20)
    r.raise_for_status()
    r = s.post(
        f"{auth}/auth/login",
        json={"username": USER, "password": PASS},
        headers={"X-CSRFToken": s.cookies.get("csrftoken", ""), "Referer": f"{TANDOOR_URL}/"},
        timeout=20,
    )
    if r.status_code != 200:
        codes = [e.get("code") for e in (r.json().get("errors") or [])] if r.headers.get(
            "content-type", "").startswith("application/json") else []
        raise RuntimeError(f"login failed: {r.status_code} {codes}")
    # Sanity check: an authenticated JSON call should return 200, not 403.
    chk = s.get(f"{TANDOOR_URL}/api/user-preference/", timeout=20)
    if chk.status_code != 200:
        raise RuntimeError(f"login check failed: {chk.status_code}")
    log.info("logged into Tandoor as %s", USER)
    return s


def _session() -> requests.Session:
    global _sess
    if _sess is None:
        with _login_lock:                     # prefetch workers must not all log in
            if _sess is None:
                _sess = _login()
    return _sess


def _relogin(stale: requests.Session) -> requests.Session:
    """Replace a stale session once, even if several threads hit the 403 together."""
    global _sess
    with _login_lock:
        if _sess is stale or _sess is None:
            _sess = _login()
    return _sess


def _api_get(path: str) -> requests.Response:
    s = _session()
    t0 = time.monotonic()
    r = s.get(f"{TANDOOR_URL}{path}", timeout=20)
    _count(t0)
    if r.status_code in (401, 403):           # stale session -> re-login once
        s = _relogin(s)
        t0 = time.monotonic()
        r = s.get(f"{TANDOOR_URL}{path}", timeout=20)
        _count(t0)
    return r


def _week_bounds(today: datetime.date) -> tuple[datetime.date, datetime.date]:
    # The plan week holding today (planner cadence: Sunday -> Saturday), not a
    # rolling 7 days: owner decision 2026-11-11, so the grid matches the week
    # the planner built and past days stay visible (what we ate). Next week
    # shows as a second row once the planner builds it (Tue 18:03).
    start = _plan_week_start(today)
    return start, start + datetime.timedelta(days=6)


def _parse_date(v: str) -> datetime.date | None:
    if not v:
        return None
    try:
        return datetime.datetime.fromisoformat(v.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return datetime.date.fromisoformat(v[:10])
        except ValueError:
            return None


# One cache for everything the pages need from /api/recipe/<id>/ (photo,
# servings, ingredients), so a recipe is fetched once per TTL, not once for the
# photo and again for the ingredients. Misses are fetched in parallel
# (_prefetch_recipes) instead of one ~300 ms round trip after another.
_RECIPE_CACHE: dict[int, tuple[float, dict]] = {}
_RECIPE_TTL = int(os.environ.get("RECIPE_TTL_S", "1800"))   # recipes change rarely
_RECIPE_WORKERS = 6


def _recipe(rid: int) -> dict:
    """{servings, image, ingredients: [{food, amount, unit}]} for a recipe (cached).
    Tandoor 2.x recipe JSON: steps[].ingredients[] with food{name}, unit{name}|null,
    amount, is_header, no_amount."""
    hit = _RECIPE_CACHE.get(rid)
    if hit and time.time() - hit[0] < _RECIPE_TTL:
        return hit[1]
    r = _api_get(f"/api/recipe/{rid}/")
    r.raise_for_status()
    out = _shape_recipe(r.json())
    _RECIPE_CACHE[rid] = (time.time(), out)
    return out


def _shape_recipe(d: dict) -> dict:
    """The cached recipe view. `ingredients` (flat, recipe order) feeds the
    grocery pages; `steps` feeds the kitchen step view: {id, name, instruction,
    time, file_id, ingredients: [index into `ingredients`]}. A step file is a
    Tandoor UserFile (step photo), served through /kitchen/step-photo."""
    ings, steps = [], []
    for step in d.get("steps") or []:
        idx = []
        for ing in step.get("ingredients") or []:
            fd = ing.get("food") or {}
            food = fd.get("name")
            if not food or ing.get("is_header"):
                continue
            unit = ing.get("unit")
            unit = unit.get("name") if isinstance(unit, dict) else (unit or "")
            idx.append(len(ings))
            ings.append({"id": ing.get("id"), "food": food,
                         "amount": 0 if ing.get("no_amount") else float(ing.get("amount") or 0),
                         "unit": unit or "", "note": (ing.get("note") or "").strip(),
                         "onhand": bool(fd.get("food_onhand"))})
        f = step.get("file")
        fid = f.get("id") if isinstance(f, dict) else None
        steps.append({"id": step.get("id"), "name": (step.get("name") or "").strip(),
                      "instruction": step.get("instruction") or "",
                      "time": int(step.get("time") or 0),
                      "file_id": int(fid) if isinstance(fid, int) else None,
                      "ingredients": idx})
    return {"name": d.get("name") or "", "servings": d.get("servings"), "image": d.get("image"),
            "working_time": int(d.get("working_time") or 0),
            "waiting_time": int(d.get("waiting_time") or 0),
            "ingredients": ings, "steps": steps}


def _prefetch_recipes(rids, fetch=None) -> None:
    """Warm _RECIPE_CACHE for every uncached/expired recipe id, in parallel
    (`fetch` defaults to _recipe). Failures are left to the caller's own
    (logged) fallback."""
    fetch = fetch or _recipe
    now = time.time()
    todo = sorted({r for r in rids if r and not (
        r in _RECIPE_CACHE and now - _RECIPE_CACHE[r][0] < _RECIPE_TTL)})
    if not todo:
        return
    stats = getattr(_tl, "stats", None)

    def one(rid):
        _tl.stats = stats                      # count the worker's calls too
        try:
            fetch(rid)
        except Exception:
            log.warning("recipe %s prefetch failed", rid)

    with ThreadPoolExecutor(max_workers=min(_RECIPE_WORKERS, len(todo))) as pool:
        list(pool.map(one, todo))


def _recipe_image(rid: int, nested: dict) -> str | None:
    """Recipe photo URL. Tandoor's /media/ files are UUID-named and served without
    a login, so the browser loads them directly. Falls back to the recipe detail
    when the nested meal-plan recipe has no image field (shared recipe cache)."""
    if nested.get("image"):
        return nested["image"]
    try:
        return _recipe(rid).get("image")
    except Exception:
        return None


def fetch_plan(mon: datetime.date, sun: datetime.date) -> list[dict]:
    """Return normalized entries: {date, meal_type, title, recipe_id, url, image}.
    Tandoor meal-plan entry fields (confirm on deploy): `from_date` (date/datetime),
    nested `recipe` {id, name}, `meal_type` {name} or `meal_type_name`, `title`."""
    r = _api_get(f"/api/meal-plan/?from_date={mon.isoformat()}&to_date={sun.isoformat()}")
    r.raise_for_status()
    data = r.json()
    rows = data.get("results", data) if isinstance(data, dict) else data
    # Photos missing from the nested recipe come from the recipe detail: fetch
    # those in parallel up front, not one by one inside the loop.
    _prefetch_recipes(e["recipe"].get("id") for e in rows or []
                      if isinstance(e.get("recipe"), dict) and not e["recipe"].get("image"))
    out = []
    for e in rows or []:
        d = _parse_date(e.get("from_date") or e.get("date") or "")
        rec = e.get("recipe") or {}
        rid = rec.get("id") if isinstance(rec, dict) else None
        rname = (rec.get("name") if isinstance(rec, dict) else None) or e.get("title") or "(untitled)"
        mt = e.get("meal_type") or {}
        mtname = (mt.get("name") if isinstance(mt, dict) else None) or e.get("meal_type_name") or ""
        out.append({
            "date": d.isoformat() if d else None,
            "meal_type": mtname,
            "title": rname,
            "recipe_id": rid,
            "url": f"{TANDOOR_URL}/view/recipe/{rid}" if rid else None,
            "image": _recipe_image(rid, rec) if rid else None,
            "servings": e.get("servings"),
            "note": e.get("note") or "",
        })
    return out


# Lunch prep is cooked on the plan week's prep day (Sunday) and eaten Mon-Sat,
# but Tandoor holds it as one Sunday entry, so it vanished from the rolling week
# on Monday. Tandoor 2.7.2 filters /api/meal-plan/ on to_date only (to_date >=
# from_date param AND to_date <= to_date param), so a Sun->Sat range would drop
# out of any window that holds the Sunday but not the Saturday; the sidecar
# instead repeats the planner's prep lunches (note "[gp:<wk>] lunch" or
# "... side" on Lunch, never the roast "leftover") on each later day of that
# plan week, labelled "Lunch prep". The prep day is the plan week's first day
# (planner config: cadence Sunday, lunches.prep_day sunday).
LUNCH_PREP_LABEL = "Lunch prep"


def _plan_week_start(d: datetime.date) -> datetime.date:
    return stock.plan_week_start(d)


def _is_prep_lunch(e: dict) -> bool:
    note = e.get("note") or ""
    return (e.get("meal_type") == "Lunch" and "[gp:" in note
            and note.rsplit("] ", 1)[-1].strip() in ("lunch", "side"))


def with_lunch_prep(plan: list[dict], start: datetime.date, prep: list[dict] | None = None,
                    days: int = 7) -> list[dict]:
    """`plan` plus a "Lunch prep" copy of each prep-day lunch on every later day
    of its plan week inside [start, start+days). `prep` holds extra entries for a
    prep day before `start` (fetched separately). Pure; the copies keep the
    recipe link/photo and carry `prep_copy: True`."""
    src = [e for e in list(plan) + list(prep or []) if e.get("date") and _is_prep_lunch(e)]
    seen, by_day = set(), {}
    for e in src:
        key = (e["date"], e.get("recipe_id"), e.get("title"))
        if key not in seen:
            seen.add(key)
            by_day.setdefault(e["date"], []).append(e)
    out = list(plan)
    for i in range(days):
        d = start + datetime.timedelta(days=i)
        ws = _plan_week_start(d)
        if d == ws:
            continue                       # the prep day shows the real entries
        for e in by_day.get(ws.isoformat(), []):
            out.append(dict(e, date=d.isoformat(), meal_type=LUNCH_PREP_LABEL, prep_copy=True))
    return out


def fetch_plan_with_prep(start: datetime.date, end: datetime.date) -> list[dict]:
    """fetch_plan for [start, end] plus the lunch-prep copies; fetches the
    current plan week's prep day too when it falls before `start`."""
    plan = fetch_plan(start, end)
    ws = _plan_week_start(start)
    prep = fetch_plan(ws, ws) if ws < start else []
    return with_lunch_prep(plan, start, prep, (end - start).days + 1)


def post_rating(recipe_id: int, rating: int, who: str) -> bool:
    """Create a CookLog rating. DRF SessionAuthentication requires the X-CSRFToken
    header (matching the csrftoken cookie) + a Referer on unsafe methods.
    Confirm the endpoint `/api/cook-log/` and fields {recipe, rating, comment} on
    first deploy."""
    s = _session()
    csrf = s.cookies.get("csrftoken", "")
    body = {
        "recipe": recipe_id,
        "rating": max(1, min(5, int(rating))),
        "comment": f"Rated {'★' * int(rating)} by {who} via the home dashboard",
    }
    hdrs = {"X-CSRFToken": csrf, "Referer": f"{TANDOOR_URL}/"}
    # If this account already logged this recipe, UPDATE that entry (re-rating);
    # only the first rating creates a new cook-log row.
    existing = None
    try:
        q = s.get(f"{TANDOOR_URL}/api/cook-log/?recipe={recipe_id}&page_size=1&ordering=-created_at", timeout=20)
        results = q.json().get("results") or []
        if results:
            existing = results[0]["id"]
    except Exception:
        pass
    if existing:
        r = s.patch(f"{TANDOOR_URL}/api/cook-log/{existing}/", json=body, headers=hdrs, timeout=20)
    else:
        r = s.post(f"{TANDOOR_URL}/api/cook-log/", json=body, headers=hdrs, timeout=20)
    if r.status_code in (401, 403):
        _login_and_retry = _login()
        globals()["_sess"] = _login_and_retry
        csrf = _login_and_retry.cookies.get("csrftoken", "")
        r = _login_and_retry.post(
            f"{TANDOOR_URL}/api/cook-log/", json=body,
            headers={"X-CSRFToken": csrf, "Referer": f"{TANDOOR_URL}/"}, timeout=20,
        )
    ok = r.status_code in (200, 201)
    log.info("rating recipe=%s stars=%s who=%s -> %s", recipe_id, rating, who, r.status_code)
    return ok


# ---- rendering ------------------------------------------------------------
PAGE_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap');
*{box-sizing:border-box}
:root{--ink:#1b1a18;--rust:#9d4a25;--paper:#ece2d0;--paper-warm:#f7f1e6;--paper-dark:#dfd4c0;
--bark:#6b6358;--stone:#9a9183;--ember:#c9743f;--ash:#8d8377;--ink-surface:#2b2825;
--ink-hairline:#423d38;--ok:#6f8f5c;--danger:#e0705a}
body{margin:0;font:14px/1.45 'Space Grotesk',sans-serif;background:var(--ink-surface);color:var(--paper)}
.week{display:grid;grid-template-columns:repeat(7,1fr);gap:8px;padding:10px}
.day{background:var(--ink);border:1px solid var(--ink-hairline);border-radius:9px;padding:8px;min-height:120px}
.day.today{border-color:var(--ember);border-width:2px;background:#33261d}
.day.past{opacity:.5}
.day.past img{filter:grayscale(.7)}
.day.past:hover,.day.past:focus-within{opacity:.85}
.dow{font-family:'JetBrains Mono',monospace;font-size:11px;font-weight:500;letter-spacing:.22em;
text-transform:uppercase;color:var(--ash)}
.day.today .dow{color:var(--ember)}
.meal{margin-top:6px;padding-top:6px;border-top:1px solid var(--ink-hairline)}
.meal img{display:block;width:100%;aspect-ratio:4/3;object-fit:cover;border-radius:6px;margin:4px 0;background:var(--ink-hairline)}
.meal a{color:var(--paper);text-decoration:none;font-weight:600}
.meal a:hover{color:var(--ember);text-decoration:underline;text-decoration-thickness:2px}
.mt{font-family:'JetBrains Mono',monospace;color:var(--bark);font-size:10px;text-transform:uppercase;letter-spacing:.12em}
.rate{margin-top:4px;display:flex;align-items:center;gap:1px}
.rate button{background:none;border:none;color:var(--ash);cursor:pointer;font-size:15px;padding:0 1px;line-height:1}
.rate button:hover{color:var(--ember)}
.rate button.on{color:var(--ember)}
.rate .as{font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--ash);margin-left:6px}
:focus-visible{outline:2px solid var(--ember);outline-offset:2px}
.ahead{font-family:'JetBrains Mono',monospace;font-size:11px;letter-spacing:.14em;text-transform:uppercase;
color:var(--ember);padding:10px 12px 0}
.empty{color:var(--ash);font-size:13px;padding:24px;text-align:center;font-family:'Space Grotesk',sans-serif}
/* phone: one column, thumbnail photos (after .meal img so it wins) */
@media(max-width:560px){.week{grid-template-columns:1fr}.day{min-height:0}
.meal img{width:96px;float:right;margin:0 0 4px 8px}.meal{overflow:hidden}}
"""

# The shared Kodiak sidebar, same source the guides use. Served same-origin from
# /guides (behind Authelia -- no CDN). The week grid, tasks, weather and
# groceries pages all get it (shared-nav.js still skips drawing it inside a
# frame). The household and kitchen pages have their own header instead.
NAV_HEAD = ('<link rel="stylesheet" href="/guides/shared-nav.css?v=nav20261110">'
            '<script defer src="/guides/shared-nav.js?v=nav20261110"></script>')


def last_ratings(recipe_ids: set[int]) -> dict[int, int]:
    """Latest CookLog rating per recipe (any household member; the dashboard
    account sees the space's logs). Best-effort - failures mean empty stars."""
    out: dict[int, int] = {}
    for rid in recipe_ids:
        try:
            r = _api_get(f"/api/cook-log/?recipe={rid}&page_size=1&ordering=-created_at")
            results = r.json().get("results") or []
            if results and results[0].get("rating"):
                out[rid] = int(results[0]["rating"])
        except Exception:
            pass
    return out


def _week_grid(plan: list[dict], today: datetime.date, start: datetime.date, rater: str,
               rated: dict[int, int]) -> str:
    """One Sun->Sat row: days before `today` get class "past" (dimmed, still
    rateable: that is when a meal gets rated), today gets "today"."""
    by_day: dict[str, list[dict]] = {}
    for e in plan:
        if e["date"]:
            by_day.setdefault(e["date"], []).append(e)
    cells = []
    for i in range(7):
        d = start + datetime.timedelta(days=i)
        iso = d.isoformat()
        state = " today" if d == today else (" past" if d < today else "")
        parts = [f'<div class="day{state}"><div class="dow">{d.strftime("%a %-d")}</div>']
        for e in by_day.get(iso, []):
            mt = f'<div class="mt">{escape(e["meal_type"])}</div>' if e["meal_type"] else ""
            if e["url"]:
                img = (f'<a href="{escape(e["url"])}" target="_top"><img src="{escape(e["image"])}" alt="" loading="lazy"></a>'
                       if e.get("image") else "")
                name = img + f'<a href="{escape(e["url"])}" target="_top">{escape(e["title"])}</a>'
                cur = rated.get(e["recipe_id"], 0)
                star_btns = []
                for n in range(1, 6):
                    cls = ' class="on"' if n <= cur else ""
                    glyph = "&#9733;" if n <= cur else "&#9734;"
                    star_btns.append(f'<button type="submit" name="rating" value="{n}" title="{n} star"{cls}>{glyph}</button>')
                stars = "".join(star_btns)
                rate = (
                    f'<form class="rate" method="post" action="rate">'
                    f'<input type="hidden" name="recipe_id" value="{e["recipe_id"]}">'
                    f'{stars}<span class="as">as {escape(rater)}</span></form>'
                )
            else:
                name = escape(e["title"])
                rate = ""
            parts.append(f'<div class="meal">{mt}{name}{rate}</div>')
        parts.append("</div>")
        cells.append("".join(parts))
    return f'<div class="week">{"".join(cells)}</div>'


def render_week(plan: list[dict], today: datetime.date, mon: datetime.date, rater: str,
                note: str = "", next_plan: list[dict] | None = None) -> str:
    """The plan week starting `mon`, plus a "Next week" row when `next_plan`
    (entries of the following plan week) is non-empty."""
    nxt = [e for e in next_plan or [] if e.get("date")]
    rated = last_ratings({e["recipe_id"] for e in list(plan) + nxt if e.get("recipe_id")})
    body = _week_grid(plan, today, mon, rater, rated)
    if nxt:
        n0 = mon + datetime.timedelta(days=7)
        n6 = n0 + datetime.timedelta(days=6)
        body = (f'<div class="ahead">This week</div>{body}'
                f'<div class="ahead">Next week · {n0:%b %-d} – {n6:%b %-d}</div>'
                + _week_grid(nxt, today, n0, rater, rated))
    if note:
        body = f'<div class="ahead">{escape(note)}</div>' + body
    if not plan and not nxt:
        body = '<div class="empty">No meals planned for this week yet.<br>Open the recipe book to plan some!</div>'
    return f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>{NAV_HEAD}<style>{PAGE_CSS}</style></head><body>{body}</body></html>"


@app.get("/healthz")
def healthz():
    return "ok"


@app.get("/mealplan.json")
def mealplan_json():
    today = datetime.datetime.now(TZ).date()
    mon, sun = _week_bounds(today)
    try:
        return Response(json.dumps(fetch_plan(mon, sun)), mimetype="application/json")
    except Exception:
        # Do not leak the exception/stack text to the client (CodeQL py/stack-trace-exposure).
        log.exception("mealplan.json failed")
        return Response(json.dumps({"error": "meal plan unavailable"}), status=502, mimetype="application/json")


@app.get("/")
def index():
    today = datetime.datetime.now(TZ).date()
    start, end = _week_bounds(today)
    try:
        # One fetch for this plan week and next; the lunch-prep copies follow
        # each plan week's own Sunday prep day.
        both = fetch_plan_with_prep(start, end + datetime.timedelta(days=7))
    except Exception:
        log.exception("meal plan fetch failed")
        return Response(
            f"<!doctype html><body style='background:#2b2825;color:#8d8377;font-family:sans-serif;padding:20px'>"
            f"Couldn't load the meal plan right now. Try again shortly.</body>",
            mimetype="text/html",
        )
    last = end.isoformat()
    plan = [e for e in both if e["date"] and e["date"] <= last]
    nxt = [e for e in both if e["date"] and e["date"] > last]
    # Next week's row appears only once the planner has built it (Tue 18:03):
    # lunch-prep copies alone come from a real Sunday entry, so they count too.
    return Response(render_week(plan, today, start, rater_from_headers(), next_plan=nxt),
                    mimetype="text/html")


@app.post("/rate")
def rate():
    try:
        rid = int(request.form["recipe_id"])
        stars = int(request.form["rating"])
        who = rater_from_headers()
        post_rating(rid, stars, who)
    except Exception:
        log.exception("rating failed")
    return redirect("./", code=303)



# ---- Vikunja tasks (write-back attributed via SSO) -------------------------
VIKUNJA_URL = os.environ.get("VIKUNJA_URL", "https://tasks.kmkdp.com")
VIKUNJA_TOKENS = {
    "jmack": os.environ.get("VIKUNJA_TOKEN_JMACK", ""),
    "rach": os.environ.get("VIKUNJA_TOKEN_RACH", ""),
}
# Chore ticks from kitchen.kmkdp.com are credited to a Vikunja "Kitchen"
# account when its token is set; until then they use the jmack token.
VIKUNJA_TOKEN_KITCHEN = os.environ.get("VIKUNJA_TOKEN_KITCHEN", "")


def kitchen_tick_token() -> tuple[str, bool]:
    """(token, fallback): the Kitchen account's token, else jmack's (fallback True)."""
    if VIKUNJA_TOKEN_KITCHEN:
        return VIKUNJA_TOKEN_KITCHEN, False
    return VIKUNJA_TOKENS.get("jmack", ""), True


def log_kitchen_token_mode() -> None:
    if kitchen_tick_token()[1]:
        log.warning("VIKUNJA_TOKEN_KITCHEN not set: kitchen.kmkdp.com chore ticks use "
                    "the jmack token (fallback active)")
    else:
        log.info("kitchen.kmkdp.com chore ticks use the Kitchen Vikunja account")

def _vik_token() -> tuple[str, str]:
    """(login, token) for the authenticated user; empty token if none stored."""
    if kitchen.on_kitchen_host():
        dev = getattr(g, "kitchen_device", None) or {}
        return f"kitchen:{dev.get('name') or 'device'}", kitchen_tick_token()[0]
    login = remote_user()
    return login, VIKUNJA_TOKENS.get(login, "")

def _vik(method: str, path: str, token: str, body=None):
    r = requests.request(method, f"{VIKUNJA_URL}/api/v1{path}",
        headers={"Authorization": f"Bearer {token}"}, json=body, timeout=15, verify=False)
    r.raise_for_status()
    return r.json() if r.text else None

def _vik_all(path: str, token: str, per_page: int = 50) -> list:
    """Every row of a paginated Vikunja list. The server caps a page at
    service.maxitemsperpage (default 50) whatever per_page asks for, so follow
    x-pagination-total-pages instead of trusting one big page."""
    rows: list = []
    sep = "&" if "?" in path else "?"
    page = 1
    while page <= 100:  # hard stop: never loop forever on a bad header
        r = requests.get(f"{VIKUNJA_URL}/api/v1{path}{sep}per_page={per_page}&page={page}",
            headers={"Authorization": f"Bearer {token}"}, timeout=15, verify=False)
        r.raise_for_status()
        batch = (r.json() if r.text else None) or []
        rows.extend(batch)
        try:
            total = int(r.headers.get("x-pagination-total-pages") or 1)
        except ValueError:
            total = 1
        if not batch or page >= total:
            break
        page += 1
    return rows

def _task_due(t: dict) -> datetime.date | None:
    """Local due date of a Vikunja task, None when it has none. Vikunja sends an
    unset date as Go's zero time (0001-01-01T00:00:00Z), not null."""
    d = _parse_date((t.get("due_date") or "")[:10])
    return d if d and d.year > 1900 else None

TASK_HORIZON_DAYS = 14
TASK_CAT_ORDER = ["Cleaning", "Dogs", "Robot Vacuum", "Yard", "Water Filtration",
                  "House Maintenance", "General"]

def _task_row(t: dict, due: datetime.date | None, today: datetime.date) -> str:
    overdue = " over" if (due and due < today) else ""
    if due is None:
        due_s = "no date"
    elif due == today:
        due_s = "today"
    else:
        due_s = due.strftime("%b %-d")
    return (f"<div class='task{overdue}'>"
            f"<form method='post' action='tasks/complete'>"
            f"<input type='hidden' name='task_id' value='{int(t['id'])}'>"
            f"<button title='done'></button></form>"
            f"<span class='tw'><span class='tt'>{escape(t.get('title') or '')}</span>"
            f"<span class='due'>{due_s}</span></span></div>")

def _task_columns(projects: dict, tasks: list, today: datetime.date,
                  skip: tuple = ()) -> list[dict]:
    """Open tasks per project, in board order: [{cat, soon: [(due, task)],
    later: [(due, task)], late}]. `soon` = overdue, undated and due within the
    horizon (dated first, oldest overdue on top, then undated, ties by title);
    `later` = due past the horizon. Nothing is dropped. Projects named in
    `skip` are left out."""
    horizon = today + datetime.timedelta(days=TASK_HORIZON_DAYS)
    by_cat: dict[str, tuple[list, list]] = {}
    for t in tasks:
        if t.get("done"):
            continue
        cat = projects.get(t.get("project_id"), "Other")
        if cat in skip:
            continue
        due = _task_due(t)
        soon, later = by_cat.setdefault(cat, ([], []))
        (later if (due and due > horizon) else soon).append((due, t))
    key = lambda dt: (dt[0] is None, dt[0] or today, (dt[1].get("title") or "").lower())
    names = [c for c in TASK_CAT_ORDER if c in by_cat] + sorted(c for c in by_cat if c not in TASK_CAT_ORDER)
    cols = []
    for cat in names:
        soon, later = (sorted(x, key=key) for x in by_cat[cat])
        cols.append({"cat": cat, "soon": soon, "later": later,
                     "late": sum(1 for d, _ in soon if d and d < today)})
    return cols

def _tasks_board(projects: dict, tasks: list, today: datetime.date) -> str:
    """Board body: one column per project with EVERY open task. Overdue, undated
    and due-within-the-horizon tasks are listed (the column scrolls); anything
    due later sits in a collapsed 'later' fold with its count, so nothing is
    dropped. (Before 2026-11-09 each column was cut at 6 rows and later tasks
    were discarded outright: 28 of 83 open chores reached the page.)"""
    columns = _task_columns(projects, tasks, today)
    if not columns:
        return "<div class='note'>No open chores. 🎉</div>"
    cols = []
    for c in columns:
        cat, soon, later, late_n = c["cat"], c["soon"], c["later"], c["late"]
        counts = f"<span class='n'>{len(soon)}</span>"
        if late_n:
            counts += f"<span class='n late'>{late_n} late</span>"
        rows = "".join(_task_row(t, d, today) for d, t in soon)
        if not soon:
            rows = "<div class='none'>nothing due in 2 weeks</div>"
        fold = ""
        if later:
            fold = (f"<details class='later'><summary>+{len(later)} later</summary>"
                    + "".join(_task_row(t, d, today) for d, t in later) + "</details>")
        cols.append(f"<div class='cat' data-cat='{escape(cat)}'><h3><span>{escape(cat)}</span>{counts}</h3>"
                    f"<div class='list'>{rows}{fold}</div></div>")
    return f"<div class='cats'>{''.join(cols)}</div>"

TASKS_CSS = PAGE_CSS + """
.cats{display:flex;gap:8px;padding:10px;overflow-x:auto;align-items:flex-start}
.cat{flex:1 0 160px;background:var(--ink);border:1px solid var(--ink-hairline);border-radius:9px;padding:8px;
min-height:90px;max-height:calc(100vh - 34px);box-sizing:border-box;display:flex;flex-direction:column}
.cat .list{overflow-y:auto;min-height:0;flex:1 1 auto;scrollbar-width:thin;scrollbar-color:var(--ink-hairline) transparent}
.cats{scrollbar-width:thin;scrollbar-color:var(--ink-hairline) transparent}
.cat h3{font-family:'JetBrains Mono',monospace;font-size:10px;font-weight:500;letter-spacing:.18em;
text-transform:uppercase;color:var(--ash);margin:0 0 6px;border-bottom:1px solid var(--ink-hairline);padding-bottom:5px;
display:flex;gap:6px;align-items:baseline;flex:0 0 auto}
.cat h3 span:first-child{flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cat h3 .n{letter-spacing:0;color:var(--stone);white-space:nowrap}
.cat h3 .n.late{color:var(--danger)}
.task{display:flex;align-items:flex-start;gap:6px;padding:5px 0;border-bottom:1px solid var(--ink-hairline)}
.task:last-child{border-bottom:none}
.task form{display:flex;margin:1px 0 0}
.task button{width:16px;height:16px;border:2px solid var(--ash);border-radius:5px;background:none;cursor:pointer;flex:0 0 auto}
.task button:hover{border-color:var(--ok);background:var(--ink-surface)}
.tw{display:flex;flex-direction:column;min-width:0}
.tt{font-weight:600;font-size:12px;line-height:1.25}
.due{font-family:'JetBrains Mono',monospace;font-size:9px;color:var(--ash);white-space:nowrap}
.task.over .due{color:var(--danger);font-weight:500}
.task.over .tt{color:var(--danger)}
.later summary{font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--ash);cursor:pointer;padding:5px 0}
.none{font-size:11px;color:var(--ash);padding:4px 0}
.note{padding:10px;font-size:12px;color:var(--ash)}
.task.busy{opacity:.45}
"""

# Tick a task without reloading the page: a plain form POST + 303 reload snapped
# the horizontally scrolled category row (and the page) back to the start. POST in
# the background, then swap in the re-rendered board (recurring chores come back
# with a new due date, so it has to be re-rendered, not just removed) and restore
# the scroll -- the row's, each column's list, and which 'later' folds were open.
# Without JS the form still posts and redirects as before.
TASKS_JS = """
document.addEventListener('submit', async function (e) {
  var f = e.target;
  if (!f.matches('.task form')) return;
  e.preventDefault();
  var row = f.closest('.task');
  if (row.classList.contains('busy')) return;
  row.classList.add('busy');
  var cats = document.querySelector('.cats');
  var x = cats ? cats.scrollLeft : 0, y = window.scrollY, keep = {};
  document.querySelectorAll('.cat').forEach(function (c) {
    var l = c.querySelector('.list'), d = c.querySelector('details');
    keep[c.dataset.cat] = {top: l ? l.scrollTop : 0, open: !!(d && d.open)};
  });
  try {
    var r = await fetch(f.action, {method: 'POST', body: new FormData(f)});
    if (!r.ok) throw new Error(r.status);
    var doc = new DOMParser().parseFromString(await r.text(), 'text/html');
    document.body.replaceWith(doc.body);
    document.querySelectorAll('.cat').forEach(function (c) {
      var k = keep[c.dataset.cat]; if (!k) return;
      var d = c.querySelector('details'); if (d && k.open) d.open = true;
      var l = c.querySelector('.list'); if (l) l.scrollTop = k.top;
    });
    var c = document.querySelector('.cats');
    if (c) c.scrollLeft = x;
    window.scrollTo(0, y);
  } catch (err) {
    // never re-POST: the tick may already have landed (a recurring chore would
    // advance twice) -- just reload to show the real state
    location.reload();
  }
});
"""

def _fetch_tasks(token: str) -> tuple[dict, list]:
    """({project id: title}, [every task]) from Vikunja."""
    projects = {p["id"]: p["title"] for p in _vik_all("/projects", token)}
    # per-project route: /tasks/all needs a token permission this one lacks
    tasks = []
    for pid in projects:
        tasks.extend(_vik_all(f"/projects/{pid}/tasks", token))
    return projects, tasks

@app.get("/tasks")
def tasks_view():
    # Shared family board: the LIST always comes from the jmack account (single
    # source of truth); completion still uses the viewer's own token when stored
    # so Vikunja attributes the done-by correctly.
    token = VIKUNJA_TOKENS.get("jmack", "")
    if not token:
        return Response(f"<!doctype html><style>{TASKS_CSS}</style><div class='note'>Task board not connected yet - store the family token as the 1Password item vikunja-api-jmack and redeploy.</div>", mimetype="text/html")
    try:
        projects, tasks = _fetch_tasks(token)
    except Exception:
        log.exception("vikunja fetch failed")
        return Response(f"<!doctype html><style>{TASKS_CSS}</style><div class='note'>Couldn't reach the task list right now.</div>", mimetype="text/html")
    body = _tasks_board(projects, tasks, datetime.datetime.now(TZ).date())
    return Response(f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>{NAV_HEAD}<style>{TASKS_CSS}</style><script>{TASKS_JS}</script></head><body>{body}</body></html>", mimetype="text/html")

@app.post("/tasks/complete")
def tasks_complete():
    login, token = _vik_token()
    ok = False
    try:
        tid = int(request.form["task_id"])
        if not token:
            token = VIKUNJA_TOKENS.get("jmack", "")
        if token:
            # Vikunja ignores a bare {"done": true} -- the update endpoint wants
            # the full task object (verified live). Fetch, flip, send back whole.
            t = _vik("GET", f"/tasks/{tid}", token)
            t["done"] = True
            _vik("POST", f"/tasks/{tid}", token, t)
            log.info("task %s completed by %s", tid, login)
            ok = True
    except Exception:
        log.exception("task completion failed")
    if request.form.get("ajax") == "1":
        # Kitchen/household pages: tick in the background, then re-read the
        # board so the next fragment shows the real state (a recurring chore
        # comes back with its next due date).
        try:
            TASKS_FEED.refresh()
        except Exception:
            log.exception("task board refresh after tick failed")
        return Response(status=204 if ok else 502)
    if request.form.get("next") == "chores":        # the All chores view (no-JS tick)
        return redirect(kitchen.kpath("/chores"), code=303)
    if kitchen.on_kitchen_host():
        return redirect(kitchen.kpath(), code=303)
    # relative to /meal/tasks/complete -> ../tasks == /meal/tasks (survives the
    # StripPrefix; a bare "tasks" resolved to /meal/tasks/tasks -> 404)
    return redirect("../tasks", code=303)



# ---- Weather: Tiffin, IA 7-day strip (Open-Meteo, keyless) ------------------
WX_URL = ("https://api.open-meteo.com/v1/forecast?latitude=41.706&longitude=-91.663"
          "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
          ",precipitation_sum,wind_speed_10m_max,sunrise,sunset"
          "&hourly=temperature_2m,precipitation_probability,weather_code"
          "&temperature_unit=fahrenheit&wind_speed_unit=mph&precipitation_unit=inch"
          "&timezone=America%2FChicago&forecast_days=7")      # 7 days = 168 hourly rows


def _wx_fetch() -> dict:
    """The full Open-Meteo forecast: {"daily": {...}, "hourly": {...}}."""
    full = requests.get(WX_URL, timeout=15).json()
    if "daily" not in full:
        raise RuntimeError("open-meteo: no daily block")
    return full


WX_FEED = Feed("weather", 900, _wx_fetch)      # 15 min, shared by /weather and the boards

WMO = {0:"Clear",1:"Mostly clear",2:"Partly cloudy",3:"Overcast",45:"Fog",48:"Fog",
       51:"Drizzle",53:"Drizzle",55:"Drizzle",61:"Rain",63:"Rain",65:"Heavy rain",
       66:"Icy rain",67:"Icy rain",71:"Snow",73:"Snow",75:"Heavy snow",77:"Snow",
       80:"Showers",81:"Showers",82:"Heavy showers",85:"Snow showers",86:"Snow showers",
       95:"Storms",96:"Storms",99:"Hail storms"}
WMO_ICON = {0:"☀️",1:"🌤️",2:"⛅",3:"☁️",45:"🌫️",48:"🌫️",51:"🌦️",53:"🌦️",55:"🌦️",
            61:"🌧️",63:"🌧️",65:"🌧️",66:"🌧️",67:"🌧️",71:"🌨️",73:"🌨️",75:"🌨️",77:"🌨️",
            80:"🌦️",81:"🌧️",82:"⛈️",85:"🌨️",86:"🌨️",95:"⛈️",96:"⛈️",99:"⛈️"}

WX_CSS = PAGE_CSS + """
.wx{min-height:0;text-align:center;padding:8px 4px}
.wx .icon{font-size:22px;line-height:1.3}
.wx .desc{font-size:11px;color:var(--ash)}
.wx .temps{font-weight:600;font-size:13px}
.wx .temps .lo{color:var(--ash);font-weight:400}
.wx .pop{font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--ash)}
.hours{display:flex;gap:6px;padding:8px 10px 0;overflow-x:auto}
.hr{flex:0 0 60px;text-align:center;background:var(--ink);border:1px solid var(--ink-hairline);border-radius:9px;padding:5px 2px}
.hr .hh{font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--ash);text-transform:lowercase}
.hr .icon{font-size:17px}
.hr .temps{font-size:12px;font-weight:600}
.hr .pop{font-family:'JetBrains Mono',monospace;font-size:9px;color:var(--ash)}
"""

@app.get("/weather")
def weather():
    full = WX_FEED.get()
    if not full:
        return Response(f"<!doctype html><style>{WX_CSS}</style><div class='empty'>Weather unavailable right now.</div>", mimetype="text/html")
    d = full["daily"]
    today = datetime.datetime.now(TZ).date()
    cells = []
    for i, day in enumerate(d["time"]):
        dt = datetime.date.fromisoformat(day)
        code = int(d["weather_code"][i])
        is_today = " today" if dt == today else ""
        cells.append(
            f"<div class='day wx{is_today}'><div class='dow'>{dt.strftime('%a %-d')}</div>"
            f"<div class='icon' role='img' aria-label='{WMO.get(code,'')}'>{WMO_ICON.get(code,'·')}</div>"
            f"<div class='desc'>{WMO.get(code,'—')}</div>"
            f"<div class='temps'>{round(d['temperature_2m_max'][i])}° <span class='lo'>/ {round(d['temperature_2m_min'][i])}°</span></div>"
            f"<div class='pop'>{int(d['precipitation_probability_max'][i] or 0)}% rain</div></div>")
    # hourly strip: the next 12 hours from now
    hours = []
    hr = full.get("hourly") or {}
    now_dt = datetime.datetime.now(TZ)
    if hr:
        for i, ts in enumerate(hr["time"]):
            t = datetime.datetime.fromisoformat(ts)
            if t < now_dt.replace(minute=0, second=0, microsecond=0, tzinfo=None): continue
            code = int(hr["weather_code"][i])
            hours.append(
                f"<div class='hr'><div class='hh'>{t.strftime('%-I%p').lower()}</div>"
                f"<div class='icon' role='img' aria-label='{WMO.get(code,'')}'>{WMO_ICON.get(code,'·')}</div>"
                f"<div class='temps'>{round(hr['temperature_2m'][i])}°</div>"
                f"<div class='pop'>{int(hr['precipitation_probability'][i] or 0)}%</div></div>")
            if len(hours) >= 12: break
    hourly_html = f"<div class='hours'>{''.join(hours)}</div>" if hours else ""
    return Response(f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>{NAV_HEAD}<style>{WX_CSS}</style></head><body>{hourly_html}<div class='week'>{''.join(cells)}</div></body></html>", mimetype="text/html")


# ---- Groceries page (home.kmkdp.com/meal/groceries) ------------------------
# The weekly grocery-planner one-shot builds the shopping list in Tandoor
# (stacks/grocery-planner). This page RENDERS it so the list stays usable in the
# store: New Pi as a tap-to-tick checklist, Target with per-item target.com
# search links for the Drive Up cart, and a manual list (entries not tied to a
# recipe) with an add box. Ticks PATCH `checked` back to Tandoor, so Tandoor is
# the source of truth. Items group by aisle via the supermarket-category name,
# namespaced "<store>/<cadence>: <aisle>" (see stacks/grocery-planner).
import urllib.parse as _ulib

_AISLE_ORDER = {
    "NewPi": ["Produce", "Meat & Seafood", "Dairy & Eggs", "Bakery", "Bulk", "Other"],
    "Target": ["Pantry & Grains", "Canned", "Oils & Spices", "Condiments", "Dairy",
               "Snacks", "Household", "Other"],
}
# Foods that must never show on a store list (dog/pet food is a JustFoodForDogs
# subscription, budgeted in Actual). Mirrors the grocery-planner config `exclude`.
_GROCERY_EXCLUDE = ("dog food", "pet food", "dog treats")


def _api_send(method: str, path: str, body: dict) -> requests.Response:
    """POST/PATCH to Tandoor with the CSRF header + Referer, re-login once on 401/403."""
    s = _session()
    hdrs = {"X-CSRFToken": s.cookies.get("csrftoken", ""), "Referer": f"{TANDOOR_URL}/"}
    t0 = time.monotonic()
    r = s.request(method, f"{TANDOOR_URL}{path}", json=body, headers=hdrs, timeout=20)
    _count(t0)
    if r.status_code in (401, 403):
        s = _relogin(s)
        hdrs["X-CSRFToken"] = s.cookies.get("csrftoken", "")
        t0 = time.monotonic()
        r = s.request(method, f"{TANDOOR_URL}{path}", json=body, headers=hdrs, timeout=20)
        _count(t0)
    return r


def _api_write(method: str, path: str, body: dict) -> bool:
    return _api_send(method, path, body).status_code in (200, 201, 204)


def _api_create(path: str, body: dict) -> dict | None:
    """POST that returns the created object (Tandoor answers 201 + the entry,
    food and its supermarket_category included); None on failure, {} when the
    answer carried no JSON object."""
    r = _api_send("POST", path, body)
    if r.status_code not in (200, 201):
        return None
    try:
        d = r.json()
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


def _parse_cat(name: str):
    """'<store>/<cadence>: <aisle>' -> (store, aisle). Lenient."""
    if not name:
        return None, "Other"
    head, _, aisle = name.partition(":")
    aisle = aisle.strip() or "Other"
    store = head.split("/", 1)[0].strip()
    return (store or None), aisle


def _fetch_shopping():
    """All shopping-list entries. One page of up to 200 (the weekly list plus
    Tandoor's recently-checked tail is ~100; the default page of 50 cost a second
    ~350 ms round trip), still following `next` if a list ever outgrows it."""
    out, path = [], "/api/shopping-list-entry/?page_size=200"
    while path:
        r = _api_get(path)
        r.raise_for_status()
        d = r.json()
        if isinstance(d, dict) and "results" in d:
            out.extend(d["results"])
            nxt = d.get("next")
            path = nxt[len(TANDOOR_URL):] if nxt and nxt.startswith(TANDOOR_URL) else None
        else:
            out.extend(d or [])
            path = None
    return out


def _delayed(e) -> bool:
    """True if the entry is delayed into the future (the weekly poll pass sets
    delay_until on the non-approved store's entries to hide them)."""
    d = _parse_date((e.get("delay_until") or "")[:10])
    return bool(d and d > datetime.datetime.now(TZ).date())


def _from_recipe(e) -> bool:
    """True if a recipe/meal plan added the entry. A recipe-less entry (the add
    box, a Pantry-tab reorder) is deliberate, so it shows even when the food is
    a pantry/stock food kept on hand."""
    return bool(e.get("list_recipe") or e.get("list_recipe_data"))


# The add box's "Manual list instead" toggle: entry ids kept on the Manual list
# although their food has a store category (forced.py; /state/manual-forced.json).
FORCED = forced.Registry(os.path.join(os.environ.get("KITCHEN_STATE_DIR", "/state"),
                                      "manual-forced.json"))
_STORES = ("NewPi", "Target")


def _route(e, forced_ids=frozenset()) -> tuple[str, str] | None:
    """Where an entry is listed: (store, aisle) for NewPi/Target, ("Manual", "")
    for an unrouted or forced-manual entry, None when it is on no list."""
    if e.get("checked") or _delayed(e):
        return None
    food = e.get("food") or {}
    if food.get("food_onhand") and _from_recipe(e):
        return None               # pantry/stock line a recipe pulled in: on hand
    fname = (food.get("name") or "").lower()
    if any(x in fname for x in _GROCERY_EXCLUDE):
        return None               # pets are budgeted in Actual, never ordered here
    if e.get("id") in forced_ids:
        return "Manual", ""       # the add box's "Manual list instead"
    cat = food.get("supermarket_category")
    cat_name = cat.get("name") if isinstance(cat, dict) else (cat or "")
    store, aisle = _parse_cat(cat_name)
    if store in _STORES:
        return store, aisle
    return "Manual", ""           # ad-hoc / unrouted (an unknown food from the add box)


def _forced_ids(entries) -> set[int]:
    """The forced-manual ids, pruned against this list read. Never raises: a
    missing or broken /state only means no forced items."""
    try:
        return FORCED.ids(entries)
    except Exception:
        log.exception("grocery: forced-manual registry unreadable")
        return set()


def _split_groceries(entries, forced_ids=None):
    """(stores, manual): stores = {store: {aisle: [entry]}} bucketed by the
    supermarket-category store prefix (regardless of whether the entry is tied to
    a recipe); manual = entries with no routable store (the add box's unknown
    foods) plus the ids in the forced-manual registry. Skips checked and
    poll-delayed entries, and on-hand foods a recipe pulled in (a recipe-less
    reorder of one still shows). Every Manual-vs-store decision on the page goes
    through here (store tabs, Manual tab, pantry view); `forced_ids` defaults to
    the registry, pruned against `entries`."""
    if forced_ids is None:
        forced_ids = _forced_ids(entries)
    stores, manual = {}, []
    for e in entries:
        where = _route(e, forced_ids)
        if where is None:
            continue
        store, aisle = where
        if store == "Manual":
            manual.append(e)
        else:
            stores.setdefault(store, {}).setdefault(aisle, []).append(e)
    for store, aisles in stores.items():
        order = _AISLE_ORDER.get(store, [])
        stores[store] = {a: aisles[a] for a in sorted(
            aisles, key=lambda a: (order.index(a) if a in order else len(order), a))}
    return stores, manual


def _item_label(e) -> str:
    food = (e.get("food") or {}).get("name", "?")
    amt = e.get("amount")
    unit = e.get("unit")
    unit = unit.get("name") if isinstance(unit, dict) else (unit or "")
    qty = ""
    if amt:
        a = int(amt) if float(amt).is_integer() else amt
        qty = f"{a} {unit} ".strip() + " "
    return (qty + food).strip()


# One line per food: entries for the same food are added up. Units in the same
# family convert (tsp/Tbsp/cup, oz/lb/g); different families are listed side by
# side ("garlic: 7 clove + 1 Tbsp"). Names come pre-cleaned by grocery.cleanup.
_UNIT_FAMILY = {
    "tsp": ("vol", 1), "teaspoon": ("vol", 1), "teaspoons": ("vol", 1),
    "tbsp": ("vol", 3), "tablespoon": ("vol", 3), "tablespoons": ("vol", 3),
    "cup": ("vol", 48), "cups": ("vol", 48),
    "oz": ("wt", 1), "ounce": ("wt", 1), "ounces": ("wt", 1),
    "lb": ("wt", 16), "lbs": ("wt", 16), "pound": ("wt", 16), "pounds": ("wt", 16),
    "g": ("wt", 0.035274), "gram": ("wt", 0.035274), "grams": ("wt", 0.035274),
    "kg": ("wt", 35.274),
    "": ("each", 1), "each": ("each", 1), "whole": ("each", 1), "small": ("each", 1),
    "medium": ("each", 1), "large": ("each", 1),
}
_UNIT_SINGULAR = {"cloves": "clove", "slices": "slice", "cans": "can", "heads": "head",
                  "bunches": "bunch", "ears": "ear", "sprigs": "sprig", "pints": "pint"}
_WEIGHT_UNITS = {"lb", "oz", "kg", "g"}           # rounded up to the next quarter
_NO_PLURAL = _WEIGHT_UNITS | {"tsp", "tbsp", "cup", "dozen"}   # "2 dozen", not "2 dozens"


def _fmt_num(x: float) -> str:
    for whole, frac in ((0.25, "¼"), (0.5, "½"), (0.75, "¾"), (1 / 3, "⅓"), (2 / 3, "⅔")):
        w = int(x)
        if abs(x - w - whole) < 0.02:
            return f"{w}{frac}" if w else frac
    return f"{x:.1f}".rstrip("0").rstrip(".") if not float(x).is_integer() else str(int(x))


def _fmt_family(fam: str, base: float) -> str:
    if fam == "vol":
        if base >= 12:                        # >= 1/4 cup
            return f"{_fmt_num(base / 48)} cup"
        if base >= 3:
            return f"{_fmt_num(base / 3)} Tbsp"
        return f"{_fmt_num(base)} tsp"
    if fam == "wt":
        return f"{_fmt_num(base / 16)} lb" if base >= 16 else f"{_fmt_num(base)} oz"
    return _fmt_num(base)


def _fmt_buy(n: float, unit: str) -> str:
    """'2 heads', '1½ lb', '3' (each renders as a bare number)."""
    if unit.lower() == "each":
        return _fmt_num(n)
    if n > 1 and unit.lower() not in _NO_PLURAL:
        unit += "es" if unit.endswith(("ch", "sh", "s", "x")) else "s"
    return f"{_fmt_num(n)} {unit}"


def _to_buy(g: dict, buy: dict) -> tuple[str, list[str]]:
    """(buy amount, unconverted parts) for one summed food line."""
    total, left = 0.0, []
    for fam, v in g["fam"].items():
        if v and buy.get(fam):
            total += v / buy[fam]
        elif v:
            left.append(_fmt_family(fam, v))
    for u, v in g["other"].items():
        if v and buy.get(u.lower()):
            total += v / buy[u.lower()]
        elif v:
            left.append(f"{_fmt_num(v)} {u}")
    if not total:
        return "", left
    step = 4 if buy["unit"].lower() in _WEIGHT_UNITS else 1
    n = -(-round(total * step, 6) // 1) / step        # round UP to the step
    return _fmt_buy(n, buy["unit"]), left


def _combine(entries, foods_cfg=None) -> list[dict]:
    """[{ids, food, qty, raw, recipes}] -- one per food, in first-seen order.
    qty is what to buy; raw is the recipe-unit sum it came from ("" when the
    food has no buy-unit rule, in which case qty IS the recipe-unit sum)."""
    out: dict[str, dict] = {}
    for e in entries:
        food = (e.get("food") or {}).get("name") or "?"
        key = food.lower()
        unit = e.get("unit")
        unit = (unit.get("name") if isinstance(unit, dict) else (unit or "")) or ""
        unit = _UNIT_SINGULAR.get(unit.lower(), unit)
        amt = float(e.get("amount") or 0)
        g = out.setdefault(key, {"ids": [], "food": food, "fam": {}, "other": {}, "recipes": []})
        g["ids"].append(e["id"])
        rname = (((e.get("list_recipe_data") or {}).get("recipe_data")) or {}).get("name")
        if rname and rname not in g["recipes"]:
            g["recipes"].append(rname)
        fam = _UNIT_FAMILY.get(unit.lower())
        if fam:
            g["fam"][fam[0]] = g["fam"].get(fam[0], 0) + amt * fam[1]
        else:
            g["other"][unit] = g["other"].get(unit, 0) + amt
    items = []
    for key, g in out.items():
        parts = [_fmt_family(f, v) for f, v in g["fam"].items() if v]
        parts += [f"{_fmt_num(v)} {u}" for u, v in g["other"].items() if v]
        qty, raw = " + ".join(parts), ""
        buy = newpi.buy_for(g["food"], foods_cfg)
        if buy:
            amount, left = _to_buy(g, buy)
            if amount:
                qty, raw = " + ".join([amount] + left), qty
        items.append({"ids": g["ids"], "food": g["food"], "qty": qty, "raw": raw,
                      "recipes": g["recipes"]})
    return items


def _grocery_row(item, title: str, link: str = "", name_html: str = "") -> str:
    ids = ",".join(str(i) for i in item["ids"])
    raw = f" <span class='raw'>({escape(item['raw'])})</span>" if item.get("raw") else ""
    qty = f"<span class='qty'>{escape(item['qty'])}{raw}</span>" if item["qty"] else ""
    src = (f"<span class='src'>{escape(', '.join(item['recipes']))}</span>"
           if item["recipes"] else "")
    # name_html lets a store tab render the food name as a link (New Pi tap-to-order);
    # default is the plain escaped name.
    name = name_html or str(escape(item["food"]))
    return ("<div class='gi'>"
            f"<form class='tickform' method='post' action='groceries/tick'>"
            f"<input type='hidden' name='id' value='{ids}'>"
            f"<button class='tick' type='submit' title='{title}'></button></form>"
            f"<span class='lbl'>{name}{qty}{src}</span>{link}</div>")


# The groceries page sits on the shared home.css (Bernese tokens, fonts, focus
# ring, .card, .btn, .sr, the "Manual list instead" .mtog). These rules are
# scoped to body.groceries; the class names avoid home.css's own .tabs/.tab
# (the steps view), .addbox (the household add card) and .meals (the board).
GROCERIES_CSS = """
body.groceries{min-height:100vh;-webkit-hyphens:none;hyphens:none}
.gr-top{flex-wrap:wrap;row-gap:0}
.gr-top h1{font-size:22px}
.gr-top .brand{color:var(--text);text-decoration:none}
.gr-top .gr-links{margin-left:auto;display:flex;align-items:center;gap:4px 14px;flex-wrap:wrap;justify-content:flex-end}
.gr-top a.gr-home,.gr-top a.upd{display:inline-flex;align-items:center;min-height:44px;padding:0 4px;font-size:14px;text-decoration:none}
.gr-top a.upd{color:var(--muted)}
.gr-top a.upd:hover{color:var(--text)}
.gwrap{padding:0 16px 24px;max-width:760px;margin:0 auto}
.gtabs{display:flex;gap:8px;position:sticky;top:0;z-index:2;background:var(--ground);padding:12px 0;
overflow-x:auto;overscroll-behavior-x:contain;scrollbar-width:none}
.gtabs::-webkit-scrollbar{display:none}
.gtabs button{flex:0 0 auto;min-height:44px;min-width:44px;padding:0 16px;border-radius:999px;border:1px solid var(--hairline);
background:var(--surface);color:var(--text);font:600 15px var(--body);cursor:pointer;white-space:nowrap}
.gtabs button:hover{border-color:var(--muted)}
.gtabs button[aria-selected=true]{border-color:var(--rust);background:var(--raised)}
.gtabs .n{margin-left:8px;color:var(--muted);font-weight:500}
.js .gtab{display:none}
.js .gtab.on{display:block}
.store{background:var(--surface);border:1px solid var(--hairline);border-radius:18px;padding:14px 16px;margin-bottom:16px}
.store h2{font-size:20px;margin:0 0 6px;overflow-wrap:anywhere}
.aisle{font-family:var(--head);font-size:13px;letter-spacing:.14em;text-transform:uppercase;font-weight:600;
color:var(--muted);margin:14px 0 2px}
.gi{display:flex;align-items:center;gap:8px;padding:4px 0;border-bottom:1px solid var(--hairline);min-height:52px;min-width:0}
.gi:last-child{border-bottom:none}
.gi form{display:flex;margin:0;flex:0 0 auto}
.gi .lbl{font-size:16px;flex:1 1 auto;min-width:0;overflow-wrap:anywhere}
.gi .lbl .qty{color:var(--tan);margin-left:8px;font-size:14px;font-weight:600}
.gi .lbl .qty .raw{color:var(--muted);font-weight:400;font-size:13px}
.gi .lbl .src{display:block;color:var(--muted);font-size:13px;margin-top:1px}
.gi .lbl a.np{color:var(--text);text-decoration:none;font-weight:600;display:inline-flex;align-items:center;min-height:44px}
.gi .lbl a.np:hover{color:var(--tan);text-decoration:underline;text-decoration-thickness:2px}
.gi .lbl a.np .ext{font-size:12px;color:var(--tan);margin-left:3px;align-self:flex-start;margin-top:10px}
.gi .lbl .nolink{color:var(--muted);font-size:12px;margin-left:4px;vertical-align:super}
/* tick: a 44 px hit area around a 26 px box */
.gi button.tick{position:relative;width:44px;height:44px;flex:0 0 44px;border:0;border-radius:12px;background:none;padding:0;
cursor:pointer;color:var(--ok);font-size:16px}
.gi button.tick::before{content:'';position:absolute;inset:9px;border:2px solid var(--box);border-radius:7px}
.gi button.tick:hover::before{border-color:var(--ok)}
.gi a.srch{display:inline-flex;align-items:center;min-height:44px;padding:0 6px;color:var(--tan);text-decoration:none;font-size:14px;white-space:nowrap}
.gi a.srch:hover{text-decoration:underline}
.gi.busy{opacity:.4}
.gadd{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0 2px}
.gadd input[name=item]{flex:1 1 160px;min-width:0;min-height:48px;padding:0 14px;border-radius:12px;border:1px solid var(--hairline);
background:var(--ground);color:var(--text);font:inherit;font-size:17px}
.gadd input[name=item]::placeholder{color:var(--muted)}
.gadd input[name=item]:focus{border-color:var(--tan)}
.gadd button{min-height:48px;min-width:64px;padding:0 18px;border:0;border-radius:12px;background:var(--rust);color:var(--ground);
font-size:16px;font-weight:600;cursor:pointer}
.gadd button:hover{background:#DB7C46}
/* phone: the item box and Add share the first row, the toggle gets its own */
@media(max-width:480px){.gadd .mtog{order:3;flex-basis:100%}}
.addflash{color:var(--ok);font-size:15px;padding:6px 2px}
.addflash.err{color:var(--late)}
.addnote{color:var(--muted);font-size:13px;padding:2px 2px 4px}
.empty2{color:var(--muted);font-size:15px;padding:14px 0}
.gi form.reorderform button.reorder{min-height:44px;padding:0 14px;border-radius:12px;border:2px solid var(--rust);background:none;
color:var(--text);font-size:14px;font-weight:600;cursor:pointer}
.gi form.reorderform button.reorder:hover{background:var(--raised)}
.gi .onlist{min-height:44px;padding:0 6px;font-size:14px}
.gi.thaw{color:var(--text);font-size:inherit;font-weight:inherit}
.gi .by{color:var(--tan);font-size:14px;font-weight:600;text-align:right;flex:0 1 auto}
.gi.thaw.late .by{color:var(--late)}
.gi.thaw.done{opacity:.55}
.gi.thaw.done button.tick::before{border-color:var(--ok)}
.thawtoday{color:var(--tan);font-weight:600;padding:2px 0 12px}
.gmeals{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}
.mcard{background:var(--raised);border:1px solid var(--hairline);border-radius:14px;overflow:hidden;color:var(--text);text-decoration:none;min-width:0}
.mcard img,.mcard .gnoimg{display:block;width:100%;aspect-ratio:4/3;object-fit:cover;background:var(--hairline)}
.mcard .mi{padding:8px 10px;font-weight:600;font-size:14px;overflow-wrap:anywhere}
.mcard .mi .mt{display:block;font-weight:500;font-size:12px;color:var(--muted);letter-spacing:.06em;text-transform:uppercase}
.mcard:hover{border-color:var(--muted);color:var(--text)}
@media(max-width:480px){.gi .by{max-width:40%}}
"""

GROCERIES_JS = """
document.addEventListener('submit', async function (e) {
  var f = e.target;
  if (!f.matches('.gi form.tickform')) return;
  e.preventDefault();
  var row = f.closest('.gi');
  if (row.classList.contains('busy')) return;
  row.classList.add('busy');
  try {
    var r = await fetch(f.action, {method: 'POST', body: new FormData(f)});
    if (!r.ok) throw new Error(r.status);
    row.style.display = 'none';   // ticked -> drop from the in-store view
    var tab = row.closest('.gtab');
    var n = tab && document.querySelector('.gtabs [data-tab="' + tab.id + '"] .n');
    if (n) { n.textContent = Math.max(0, (parseInt(n.textContent, 10) || 1) - 1); }
  } catch (err) { location.reload(); }
});
// Store tabs: one list at a time. The tab lives in the URL hash so a reload,
// a bookmark (#target) or the add-box redirect (#manual) opens the same tab.
// Without JS every section shows (the .js class is never set).
document.documentElement.classList.add('js');
function showTab(id) {
  var tabs = document.querySelectorAll('.gtab');
  if (!document.getElementById(id)) { id = tabs.length ? tabs[0].id : ''; }
  tabs.forEach(function (t) { t.classList.toggle('on', t.id === id); });
  document.querySelectorAll('.gtabs button').forEach(function (b) {
    b.setAttribute('aria-selected', b.dataset.tab === id ? 'true' : 'false');
  });
}
document.addEventListener('click', function (e) {
  var b = e.target.closest('.gtabs button');
  if (!b) return;
  history.replaceState(null, '', '#' + b.dataset.tab);
  showTab(b.dataset.tab);
});
// "refresh" keeps the open tab, and the ?refresh=1 is dropped from the URL
// afterwards so a plain reload is served from the cache again.
document.addEventListener('click', function (e) {
  var a = e.target.closest('a.upd');
  if (a) { a.href = 'groceries?refresh=1' + location.hash; }
});
document.addEventListener('DOMContentLoaded', function () {
  if (location.search) { history.replaceState(null, '', 'groceries' + location.hash); }
  showTab(location.hash.slice(1) || 'newpi');
});
"""


def _render_meal_cards(plan: list[dict] | None, today: datetime.date, thaw_line: str = "") -> str:
    """The planned meals the list was built from, with photos, so the weekly
    approval can be reviewed by eye. The planner fills next Sun-Sat on Tuesday,
    so the caller fetches two weeks ahead. `thaw_line` ("Thaw today: ...") sits
    on top. plan None = the fetch failed."""
    if plan is None:
        return ""
    head = f"<div class='thawtoday'>{escape(thaw_line)}</div>" if thaw_line else ""
    plan = sorted((e for e in plan if e["date"] and e["date"] >= today.isoformat()),
                  key=lambda e: (e["date"], e["meal_type"]))
    if not plan:
        return (f"{head}<div class='store'><h2>Planned meals</h2>"
                "<div class='empty2'>No meals planned in the next two weeks.</div></div>")
    cards = []
    for e in plan:
        d = datetime.date.fromisoformat(e["date"]).strftime("%a %-d")
        img = (f"<img src='{escape(e['image'])}' alt='' loading='lazy'>" if e.get("image")
               else "<div class='gnoimg'></div>")
        label = (f"<div class='mi'><span class='mt'>{escape(d)} · {escape(e['meal_type'])}</span>"
                 f"{escape(e['title'])}</div>")
        if e["url"]:
            cards.append(f"<a class='mcard' href='{escape(e['url'])}' target='_blank' rel='noopener'>{img}{label}</a>")
        else:
            cards.append(f"<div class='mcard'>{img}{label}</div>")
    return f"{head}<div class='store'><h2>Planned meals</h2><div class='gmeals'>{''.join(cards)}</div></div>"


# ---- Pantry + Thaw tabs ------------------------------------------------------
# Pantry: the foods the planner keeps permanently on hand (cleanup.yaml `pantry:`
# + the stock-store foods) so they never reach a weekly list. "reorder" adds ONE
# recipe-less Tandoor entry for the food; its on-hand flag is not touched, so the
# next weekly build is unaffected. Thaw: the week's freezer-stock ingredients
# (Amana beef, O'Connell chicken/pork, Costco frozen salmon) with the time to
# move each to the fridge, from the `thaw:` lead table in the planner config.
DATA_DIR = os.environ.get("MEALPLAN_DATA", "/data")
THAW_FILE = os.path.join(DATA_DIR, "thaw.json")


def _recipe_detail(rid: int) -> dict:
    """{servings, ingredients} for a recipe, from the shared recipe cache."""
    return _recipe(rid)


def _plan_lines(plan: list[dict], detail=None) -> list[dict]:
    """One row per ingredient of each planned meal: {date, meal_type, recipe,
    recipe_id, food, amount, unit}. Amounts are scaled to the meal plan's
    servings like Tandoor's own add-to-shopping. A recipe that fails to load is
    skipped (logged), not fatal."""
    detail = detail or _recipe_detail
    out = []
    for e in plan:
        rid = e.get("recipe_id")
        if not rid or not e.get("date"):
            continue
        try:
            rec = detail(rid)
        except Exception:
            log.exception("recipe %s ingredients fetch failed", rid)
            continue
        try:
            scale = float(e.get("servings") or 0) / float(rec.get("servings") or 0)
        except (TypeError, ValueError, ZeroDivisionError):
            scale = 1.0
        scale = scale or 1.0
        for ing in rec.get("ingredients") or []:
            out.append({"date": e["date"], "meal_type": e.get("meal_type") or "",
                        "recipe": e.get("title") or "", "recipe_id": rid,
                        "food": ing["food"], "amount": (ing.get("amount") or 0) * scale,
                        "unit": ing.get("unit") or ""})
    return out


def _open_reorders(entries) -> set[str]:
    """Lower-cased foods with an open (unchecked) recipe-less entry."""
    return {((e.get("food") or {}).get("name") or "").lower()
            for e in entries if not e.get("checked") and not _from_recipe(e)}


def _used_this_week(lines: list[dict], week_start: datetime.date, foods_cfg=None,
                    config=None, cleanup=None) -> list[dict]:
    """[{food, recipes}] for the pantry/stock foods this plan week's recipes call
    for, in first-seen order (by meal date)."""
    end = (week_start + datetime.timedelta(days=6)).isoformat()
    out: dict[str, dict] = {}
    for ln in sorted(lines, key=lambda x: x["date"]):
        if not (week_start.isoformat() <= ln["date"] <= end):
            continue
        if not stock.is_pantry_or_stock(ln["food"], foods_cfg, config, cleanup):
            continue
        g = out.setdefault(ln["food"].lower(), {"food": ln["food"], "recipes": []})
        if ln["recipe"] and ln["recipe"] not in g["recipes"]:
            g["recipes"].append(ln["recipe"])
    return list(out.values())


def _pantry_row(food: str, recipes: list[str], on_list: bool) -> str:
    src = (f"<span class='src'>{escape(', '.join(recipes))}</span>" if recipes else "")
    if on_list:
        act = "<span class='onlist'>on the list</span>"
    else:
        act = ("<form class='reorderform' method='post' action='groceries/reorder'>"
               f"<input type='hidden' name='food' value='{escape(food)}'>"
               "<button class='reorder' type='submit'>reorder</button></form>")
    return f"<div class='gi pantry'><span class='lbl'>{escape(food)}{src}</span>{act}</div>"


_STORE_LABEL = {"NewPi": "New Pi", "OConnell": "O'Connell"}


def _render_pantry(entries, lines, today, foods_cfg=None, config=None, cleanup=None) -> str:
    on = _open_reorders(entries)
    used = _used_this_week(lines, stock.plan_week_start(today, config), foods_cfg, config, cleanup)
    parts = ["<h2>Pantry — reorder what is running low</h2>",
             "<div class='aisle'>Used this week</div>"]
    if not used:
        parts.append("<div class='empty2'>No pantry or stock foods in this week's recipes.</div>")
    for u in used:
        parts.append(_pantry_row(u["food"], u["recipes"], u["food"].lower() in on))
    for store, aisles in stock.catalog(foods_cfg, config, cleanup).items():
        for aisle, foods in aisles.items():
            parts.append(f"<div class='aisle'>{escape(_STORE_LABEL.get(store, store))} · "
                         f"{escape(aisle)}</div>")
            parts.extend(_pantry_row(f, [], f in on) for f in foods)
    return "".join(parts)


# Thaw ticks live in a small JSON file on the sidecar's data volume:
# {"done": {"<meal date>|<recipe id>|<food>": "<meal date>"}}. A tick for a meal
# before the current plan week is dropped on load, so the tab starts clean each
# week (a Saturday tick for Sunday's dinner survives into that Sunday).
def _thaw_key(date: str, rid, food: str) -> str:
    return f"{date}|{rid}|{food.lower()}"


def _load_thaw(today: datetime.date, config=None, path: str | None = None) -> dict[str, str]:
    path = path or THAW_FILE
    try:
        with open(path) as fh:
            done = (json.load(fh) or {}).get("done") or {}
    except (OSError, ValueError, AttributeError):
        return {}
    start = stock.plan_week_start(today, config).isoformat()
    return {k: v for k, v in done.items() if isinstance(v, str) and v >= start}


def _save_thaw(done: dict[str, str], path: str | None = None) -> None:
    path = path or THAW_FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump({"done": done}, fh)
    os.replace(tmp, path)


def _thaw_rows(lines: list[dict], today: datetime.date, done: dict[str, str] | None = None,
               foods_cfg=None, config=None) -> list[dict]:
    """Freezer-stock ingredients of meals from today through a week out, one row
    per (meal, food): {key, date, meal_type, recipe, food, qty, move_by, done},
    sorted by move_by."""
    done = done or {}
    last = (today + datetime.timedelta(days=7)).isoformat()
    groups: dict[str, dict] = {}
    for ln in lines:
        if not (today.isoformat() <= ln["date"] <= last):
            continue
        if not stock.is_freezer(stock.route_for(ln["food"], foods_cfg, config), config):
            continue
        key = _thaw_key(ln["date"], ln["recipe_id"], ln["food"])
        g = groups.setdefault(key, {"ln": ln, "entries": []})
        g["entries"].append({"id": len(g["entries"]), "food": {"name": ln["food"]},
                             "amount": ln["amount"], "unit": {"name": ln["unit"]}})
    rows = []
    for key, g in groups.items():
        ln = g["ln"]
        d = datetime.date.fromisoformat(ln["date"])
        qty = _combine(g["entries"], foods_cfg)[0]["qty"]
        rows.append({"key": key, "date": d, "meal_type": ln["meal_type"],
                     "recipe": ln["recipe"], "food": ln["food"], "qty": qty,
                     "move_by": stock.move_by(d, ln["meal_type"], ln["food"], config),
                     "done": key in done})
    return sorted(rows, key=lambda r: (r["move_by"], r["date"], r["food"]))


def _fmt_when(dt: datetime.datetime) -> str:
    return dt.strftime("%a %-I %p").replace(" 12 PM", " noon")


def _thaw_today_line(rows: list[dict], today: datetime.date) -> str:
    """"Thaw today: 1 lb ground beef (Wed chili), …" -- open rows due by today."""
    due = [r for r in rows if not r["done"] and r["move_by"].date() <= today]
    if not due:
        return ""
    bits = [f"{(r['qty'] + ' ') if r['qty'] else ''}{r['food']} "
            f"({r['date']:%a} {r['recipe']})" for r in due]
    return "Thaw today: " + ", ".join(bits)


def _render_thaw(rows: list[dict], today: datetime.date) -> str:
    parts = ["<h2>Thaw — freezer to fridge</h2>"]
    if not rows:
        return "".join(parts) + "<div class='empty2'>No freezer stock in the next week's meals.</div>"
    for r in rows:
        late = " late" if not r["done"] and r["move_by"].date() < today else ""
        cls = " done" if r["done"] else late
        meta = (f"{r['date']:%a %-d} · {r['meal_type'] + ' · ' if r['meal_type'] else ''}"
                f"{r['recipe']}")
        qty = f"<span class='qty'>{escape(r['qty'])}</span>" if r["qty"] else ""
        parts.append(
            f"<div class='gi thaw{cls}'>"
            "<form class='thawform' method='post' action='groceries/thaw-tick'>"
            f"<input type='hidden' name='key' value='{escape(r['key'])}'>"
            f"<button class='tick' type='submit' title='{'undo' if r['done'] else 'in the fridge'}'>"
            f"{'✓' if r['done'] else ''}</button></form>"
            f"<span class='lbl'>{escape(r['food'])}{qty}"
            f"<span class='src'>{escape(meta)}</span></span>"
            f"<span class='by'>move to the fridge by {escape(_fmt_when(r['move_by']))}</span></div>")
    return "".join(parts)




# ---- Grocery data cache -----------------------------------------------------
# Before this cache every page load fetched the whole shopping list (2 pages,
# ~300 KB), the 2-week meal plan, and -- every 10 min -- each planned recipe one
# by one: ~1 s warm, ~5 s cold (2026-11-08, 16 recipes). Now one snapshot
# {entries, plan, lines} lives in memory and the page renders from it:
#   * a warmer thread refreshes it every GROCERY_REFRESH_S (default 120 s), so a
#     page view never waits on Tandoor;
#   * a snapshot older than GROCERY_TTL_S is still served, and a background
#     refresh is kicked; older than _GROCERY_MAX_STALE (or none yet, or a new
#     day) the request refreshes synchronously;
#   * this page's own writes update the snapshot at once: a tick marks the
#     entries checked in place, an add/reorder re-reads the list before the
#     redirect -- so the page never shows a ticked item again;
#   * ?refresh=1 (the "refresh" link) forces a synchronous refresh, for changes
#     made in Tandoor itself that should show before the next warm cycle.
# Every render and refresh logs one logfmt line (ms, upstream_calls) for Loki.
_GROCERY_TTL = int(os.environ.get("GROCERY_TTL_S", "120"))
_GROCERY_REFRESH = int(os.environ.get("GROCERY_REFRESH_S", "120"))   # 0 = no warmer
_GROCERY_MAX_STALE = 900
_gcache: dict = {"ts": 0.0, "day": None, "entries": None, "plan": None, "lines": []}
_gcache_lock = threading.Lock()      # guards _gcache
_refresh_lock = threading.Lock()     # one refresh at a time (single flight)


def _with_stats(stats, fn, *a):
    _tl.stats = stats
    try:
        return fn(*a)
    finally:
        _tl.stats = None


def _plan_and_lines(today: datetime.date):
    """One plan fetch feeds Meals, Pantry ("used this week") and Thaw: from the
    start of this plan week (Sun) to two weeks out (the planner fills next week
    on Tuesday). Recipe details are fetched in parallel, then expanded."""
    plan = fetch_plan(stock.plan_week_start(today), today + datetime.timedelta(days=13))
    _prefetch_recipes((e.get("recipe_id") for e in plan), fetch=_recipe_detail)
    return plan, _plan_lines(plan)


def _snapshot() -> dict:
    with _gcache_lock:
        return dict(_gcache)


def _grocery_refresh(plan: bool = True, reason: str = "sync") -> dict:
    """Re-read the shopping list (and, with plan=True, the meal plan + recipes)
    from Tandoor and swap the snapshot in. A failed shopping fetch raises and
    leaves the old snapshot alone; a failed plan fetch keeps the old plan for
    the same day, else plan=None (the tabs say "couldn't load")."""
    with _refresh_lock:
        stats, t0 = _new_stats(), time.monotonic()
        today = datetime.datetime.now(TZ).date()
        old = _snapshot()
        new_plan, lines = old["plan"], old["lines"]
        if plan:
            with ThreadPoolExecutor(max_workers=2) as pool:
                fs = pool.submit(_with_stats, stats, _fetch_shopping)
                fp = pool.submit(_with_stats, stats, _plan_and_lines, today)
                try:
                    new_plan, lines = fp.result()
                except Exception:
                    log.exception("groceries: plan fetch failed")
                    if old["day"] != today:
                        new_plan, lines = None, []
                entries = fs.result()
        else:
            entries = _with_stats(stats, _fetch_shopping)
        with _gcache_lock:
            _gcache.update(ts=time.time(), day=today, entries=entries,
                           plan=new_plan, lines=lines)
            snap = dict(_gcache)
        log.info("grocery refresh reason=%s ms=%d upstream_calls=%d upstream_ms=%d "
                 "entries=%d plan_entries=%s", reason, (time.monotonic() - t0) * 1000,
                 stats["calls"], stats["ms"], len(entries),
                 "none" if new_plan is None else len(new_plan))
        return snap


def _kick_refresh(reason: str) -> None:
    """Refresh in the background unless one is already running."""
    if _refresh_lock.locked():
        return

    def run():
        try:
            _grocery_refresh(reason=reason)
        except Exception:
            log.exception("grocery %s refresh failed", reason)
    threading.Thread(target=run, daemon=True, name=f"grocery-{reason}").start()


def _grocery_data(force: bool = False) -> tuple[dict, str]:
    """(snapshot, source) where source is cache | stale | sync | error-stale.
    Raises only if Tandoor is unreachable AND there is no snapshot yet."""
    def usable(sn):
        return sn["entries"] is not None and sn["day"] == datetime.datetime.now(TZ).date()

    snap = _snapshot()
    age = time.time() - snap["ts"]
    if usable(snap) and not force:
        if age < _GROCERY_TTL:
            return snap, "cache"
        if age < _GROCERY_MAX_STALE:
            _kick_refresh("stale")
            return snap, "stale"
    if not force and _refresh_lock.locked():
        with _refresh_lock:                 # a refresh is in flight: wait for it
            pass
        fresh = _snapshot()
        if usable(fresh) and time.time() - fresh["ts"] < _GROCERY_TTL:
            return fresh, "cache"
    try:
        return _grocery_refresh(reason="force" if force else "sync"), "sync"
    except Exception:
        if snap["entries"] is None:
            raise
        log.exception("grocery refresh failed; serving the last snapshot")
        return snap, "error-stale"


def _grocery_mark_checked(ids) -> None:
    """A tick from this page: mark the cached entries checked right away."""
    ids = set(ids)
    with _gcache_lock:
        for e in _gcache["entries"] or []:
            if e.get("id") in ids:
                e["checked"] = True


def _grocery_warmer() -> None:
    while True:
        try:
            _grocery_refresh(reason="warm")
        except Exception:
            log.exception("grocery warm refresh failed")
        time.sleep(_GROCERY_REFRESH)


def start_grocery_warmer() -> None:
    if _GROCERY_REFRESH > 0:
        threading.Thread(target=_grocery_warmer, daemon=True, name="grocery-warmer").start()


def _ago(sec: float) -> str:
    sec = max(0, int(sec))
    return f"{sec}s ago" if sec < 90 else f"{sec // 60} min ago"


def _groceries_top(upd: str) -> str:
    """The household-style header: the page title, the way home and the
    "updated · refresh" link (left out on the error page)."""
    return ("<header class='hh-top gr-top'><h1>Groceries</h1><div class='gr-links'>"
            f"{upd}<a class='gr-home' href='{kitchen.BASE}/home'>Household home</a></div></header>")


def _render_groceries(force: bool = False, flash: dict | None = None):
    t0 = time.monotonic()
    today = datetime.datetime.now(TZ).date()
    try:
        snap, source = _grocery_data(force)
        entries = snap["entries"]
        stores, manual = _split_groceries(entries)
    except Exception:
        log.exception("groceries fetch failed")
        return (kitchen.head("Groceries", ASSET_VER, kiosk=False, js=False)
                + f"<body class='groceries'>{_groceries_top('')}<main class='gwrap'>"
                "<div class='store'><div class='empty2'>Couldn't load the grocery list right now.</div>"
                "</div></main></body></html>")
    np = {a: _combine(v) for a, v in (stores.get("NewPi") or {}).items()}
    tg = {a: _combine(v) for a, v in (stores.get("Target") or {}).items()}
    n_np = sum(len(v) for v in np.values())
    n_tg = sum(len(v) for v in tg.values())
    plan, lines = snap["plan"], snap["lines"]
    thaw = _thaw_rows(lines, today, _load_thaw(today))
    n_thaw = sum(1 for r in thaw if not r["done"])
    tabs = [("newpi", "New Pi", n_np), ("target", "Target", n_tg),
            ("manual", "Manual", len(manual)), ("pantry", "Pantry", None),
            ("thaw", "Thaw", n_thaw), ("meals", "Meals", None)]
    bar = "".join(
        f"<button type='button' data-tab='{tid}' aria-selected='false'>{label}"
        + (f"<span class='n'>{n}</span>" if n is not None else "") + "</button>"
        for tid, label, n in tabs)
    upd = (f"<a class='upd' href='groceries?refresh=1' title='re-read the list from Tandoor'>"
           f"updated {_ago(time.time() - snap['ts'])} · refresh</a>")
    parts = [_groceries_top(upd), "<main class='gwrap'>",
             f"<nav class='gtabs' aria-label='Shopping lists'>{bar}</nav>"]
    # New Pi -- in-store tap-to-tick checklist; each name links to the New Pi
    # product (opens in a new tab) when foods.yaml carries a newpi_url for it.
    parts.append("<section class='gtab store' id='newpi'><h2>New Pi — tap a name to order</h2>")
    if not np:
        parts.append("<div class='empty2'>Nothing to pick up at New Pi.</div>")
    for aisle, items in np.items():
        parts.append(f"<div class='aisle'>{escape(aisle)}</div>")
        for item in items:
            name_html = newpi.name_cell_html(item["food"], newpi.url_for(item["food"]))
            parts.append(_grocery_row(item, "got it", name_html=name_html))
    parts.append("</section>")
    # Target -- list with per-item target.com search links for the Drive Up cart
    parts.append("<section class='gtab store' id='target'><h2>Target — Drive Up cart</h2>")
    if not tg:
        parts.append("<div class='empty2'>Nothing to add at Target.</div>")
    for aisle, items in tg.items():
        parts.append(f"<div class='aisle'>{escape(aisle)}</div>")
        for item in items:
            link = "https://www.target.com/s?searchTerm=" + _ulib.quote(item["food"])
            parts.append(_grocery_row(
                item, "in cart",
                f"<a class='srch' href='{escape(link)}' target='_blank' rel='noopener'>search ↗</a>"))
    parts.append("</section>")
    # Manual -- ad-hoc items not tied to a recipe, with an add box
    parts.append("<section class='gtab store' id='manual'><h2>Manual list</h2>"
                 "<form class='gadd' method='post' action='groceries/add'>"
                 "<label class='sr' for='g-item'>Add an item</label>"
                 "<input id='g-item' name='item' placeholder='Add an item…' maxlength='80' autocomplete='off'>"
                 f"{kitchen.manual_toggle(FORCED.writable(), 'g-manual')}"
                 "<button type='submit'>Add</button></form>")
    if not FORCED.writable():
        parts.append(f"<div class='addnote'>{kitchen.MANUAL_OFF_NOTE}</div>")
    if flash:
        parts.append(f"<div class='addflash{'' if flash['ok'] else ' err'}' role='status'>"
                     f"{escape(flash['text'])}: {escape(flash['item'])}</div>")
    if not manual:
        parts.append("<div class='empty2'>No ad-hoc items.</div>")
    for e in manual:
        parts.append(
            "<div class='gi'>"
            f"<form class='tickform' method='post' action='groceries/tick'>"
            f"<input type='hidden' name='id' value='{e['id']}'>"
            "<button class='tick' type='submit' title='got it'></button></form>"
            f"<span class='lbl'>{escape(_item_label(e))}</span></div>")
    parts.append("</section>")
    parts.append(f"<section class='gtab store' id='pantry'>{_render_pantry(entries, lines, today)}</section>")
    thaw_html = (_render_thaw(thaw, today) if plan is not None else
                 "<h2>Thaw — freezer to fridge</h2><div class='empty2'>Couldn't load the meal plan.</div>")
    parts.append(f"<section class='gtab store' id='thaw'>{thaw_html}</section>")
    parts.append(f"<section class='gtab' id='meals'>"
                 f"{_render_meal_cards(plan, today, _thaw_today_line(thaw, today))}</section>")
    parts.append("</main>")
    body = "".join(parts)
    html = (kitchen.head("Groceries", ASSET_VER, kiosk=False, js=False).replace(
                "</head>", f"<style>{GROCERIES_CSS}</style><script>{GROCERIES_JS}</script></head>", 1)
            + f"<body class='groceries'>{body}</body></html>")
    log.info("groceries render ms=%d source=%s age_s=%d entries=%d bytes=%d",
             (time.monotonic() - t0) * 1000, source, time.time() - snap["ts"],
             len(entries), len(html))
    return html


@app.get("/groceries")
def groceries():
    return _html(_render_groceries(force=request.args.get("refresh") == "1", flash=_added_flash()))


@app.post("/groceries/tick")
def groceries_tick():
    try:
        # One row can stand for several entries of the same food ("12,57,90").
        for eid in [int(x) for x in request.form["id"].split(",") if x.strip()]:
            if _api_write("PATCH", f"/api/shopping-list-entry/{eid}/", {"checked": True}):
                _grocery_mark_checked([eid])        # the next render drops it at once
            log.info("grocery entry %s checked", eid)
    except Exception:
        log.exception("grocery tick failed")
    return redirect("../groceries", code=303)


# The kitchen pages (pantry check, household add box) post to these same two
# routes with `next` (a fixed key, never a URL) to land back where they were,
# and with ajax=1 to get a small JSON answer instead of a redirect.
_NEXT = {"pantry": "/kitchen/pantry", "pantry-home": "/kitchen/pantry?from=home",
         "home": "/home"}
MANUAL_MAX = 80


def _back_to(default: str) -> str:
    key = request.form.get("next") or ""
    if kitchen.on_kitchen_host():          # only the kitchen views exist there
        return kitchen.kpath("/pantry") if key.startswith("pantry") else kitchen.kpath()
    nxt = _NEXT.get(key)
    return f"{kitchen.BASE}{nxt}" if nxt else default


def _json(body: dict, status: int = 200) -> Response:
    r = Response(json.dumps(body), status=status, mimetype="application/json")
    r.headers["Cache-Control"] = "no-store"
    return r


_ADD_LABEL = {"NewPi": "New Pi", "Target": "Target"}


def _dest_text(store: str, aisle: str) -> str:
    """The add box's confirmation: where the item went (plain text; every view
    escapes it or sets it as textContent)."""
    if store in _ADD_LABEL:
        return f"Added to {_ADD_LABEL[store]}" + (f" · {aisle}" if aisle else "")
    if store == "Manual":
        return "Added to Manual list"
    if store == "Off":
        return "Added (pet food stays off the store lists)"
    return "Added to the list"


def _manual_add(raw: str, to_manual: bool = False) -> dict:
    """{status, item, store, aisle}: status is added | empty | error. The item
    is trimmed and capped at MANUAL_MAX characters. Tandoor reuses an existing
    food by name, so a known food lands on its store list (NewPi/Target by its
    supermarket category) and an unknown name on the Manual list. `to_manual`
    (the "Manual list instead" toggle) records the new entry id in the
    forced-manual registry, so it lists under Manual even for a known food;
    Tandoor itself is not changed. `store` is NewPi | Target | Manual | Off
    (on no list: pet food) | "" (unknown)."""
    item = (raw or "").strip()[:MANUAL_MAX].strip()
    out = {"status": "empty", "item": item, "store": "", "aisle": ""}
    if not item:
        return out
    # Recipe-less entry by food name, amount 1 (Tandoor reuses or creates the food).
    created = _api_create("/api/shopping-list-entry/",
                          {"food": {"name": item}, "amount": 1, "checked": False})
    if created is None:
        log.warning("grocery manual add failed: %s", item)
        return dict(out, status="error")
    eid = created.get("id")
    forced_ok = False
    if to_manual and isinstance(eid, int):
        try:
            forced_ok = FORCED.add(eid)
        except Exception:
            log.exception("grocery: forced-manual registry write failed")
        if not forced_ok:
            log.warning("grocery add: /state not writable, %s keeps its normal list", item)
    snap = None
    try:
        snap = _grocery_refresh(plan=False, reason="add")    # new entry shows on redirect
    except Exception:
        log.exception("grocery refresh after add failed")
    entry = created
    if "supermarket_category" not in (created.get("food") or {}):
        # no category in the POST answer: look the entry up in the re-read list
        entry = next((e for e in (snap or {}).get("entries") or [] if e.get("id") == eid), created)
    where = None
    if "supermarket_category" in (entry.get("food") or {}) or forced_ok:
        where = _route(entry, {eid} if forced_ok else frozenset())
        if where is None:
            where = ("Off", "")
    store, aisle = where or ("", "")
    log.info("grocery manual add: %s -> %s%s", item, store or "?", " (forced)" if forced_ok else "")
    return {"status": "added", "item": item, "store": store, "aisle": aisle}


# The form fallback (no JS) carries the result back in ONE fixed query key,
# `added`: "<store>|<aisle>|<item>", URL-encoded, re-validated and HTML-escaped
# when the page renders it. The redirect target itself stays the fixed `next`
# map above (never a URL from the request).
ADDED_KEY = "added"
_FLASH_STORES = {"NewPi", "Target", "Manual", "Off", "", "error"}


def _added_query(res: dict) -> str:
    store = res["store"] if res["status"] == "added" else "error"
    val = f"{store}|{res['aisle'][:60]}|{res['item'][:MANUAL_MAX]}"
    return _ulib.urlencode({ADDED_KEY: " ".join(val.splitlines())})


def _with_query(url: str, q: str) -> str:
    path, hash_, frag = url.partition("#")
    return f"{path}{'&' if '?' in path else '?'}{q}{hash_}{frag}"


def _added_flash() -> dict | None:
    """The `added` query value from a form-fallback redirect: {item, text, ok}
    (plain text, escaped by the renderer), or None."""
    raw = request.args.get(ADDED_KEY)
    if not raw:
        return None
    store, _, rest = raw[:200].partition("|")
    aisle, _, item = rest.partition("|")
    if store not in _FLASH_STORES or not item.strip():
        return None
    item = item.strip()[:MANUAL_MAX]
    if store == "error":
        return {"item": item, "text": "Couldn't add it right now, try again", "ok": False}
    return {"item": item, "text": _dest_text(store, aisle.strip()[:60]), "ok": True}


@app.post("/groceries/add")
def groceries_add():
    try:
        res = _manual_add(request.form.get("item"), request.form.get("manual") == "1")
    except Exception:
        log.exception("grocery add failed")
        res = {"status": "error", "item": "", "store": "", "aisle": ""}
    if request.form.get("ajax") == "1":
        code = {"added": 200, "empty": 422}.get(res["status"], 502)
        text = _dest_text(res["store"], res["aisle"]) if res["status"] == "added" else ""
        return _json(dict(res, text=text), code)
    back = _back_to("../groceries#manual")                      # back to the Manual tab
    if res["status"] in ("added", "error") and res["item"]:
        back = _with_query(back, _added_query(res))
    return redirect(back, code=303)


# One reorder at a time: the duplicate check and the write must not interleave,
# or two quick taps (or the tablet and a phone) could both pass the check.
_reorder_lock = threading.Lock()


def _pantry_reorder(raw: str) -> str:
    """ONE recipe-less shopping entry for a pantry/stock food: amount 1, unit =
    the foods.yaml `buy:` unit if any. The food's on-hand flag is not touched.
    Returns added | on_list (an open recipe-less entry exists: no write) |
    refused (not a pantry/stock food, or empty) | error."""
    food = (raw or "").strip()[:80]
    if not food or not stock.is_pantry_or_stock(food):
        if food:
            log.info("grocery reorder refused (not a pantry/stock food): %s", food)
        return "refused"
    with _reorder_lock:
        # Duplicate guard on a LIVE read (not the snapshot): it decides a write.
        snap = _grocery_refresh(plan=False, reason="reorder-check")
        if food.lower() in _open_reorders(snap["entries"]):
            log.info("grocery reorder: %s already on the list", food)
            return "on_list"
        body = {"food": {"name": food}, "amount": 1, "checked": False}
        buy = newpi.buy_for(food)
        if buy:
            body["unit"] = {"name": buy["unit"]}
        if not _api_write("POST", "/api/shopping-list-entry/", body):
            log.warning("grocery reorder failed: %s", food)
            return "error"
        log.info("grocery reorder: %s", food)
        try:
            _grocery_refresh(plan=False, reason="reorder")
        except Exception:                 # the entry exists; the next read shows it
            log.exception("grocery refresh after reorder failed")
        return "added"


@app.post("/groceries/reorder")
def groceries_reorder():
    """Pantry reorder (groceries Pantry tab, kitchen pantry check). Refuses
    non-pantry foods; a second press while the entry is open is a no-op."""
    try:
        status = _pantry_reorder(request.form.get("food"))
    except Exception:
        log.exception("grocery reorder failed")
        status = "error"
    if request.form.get("ajax") == "1":
        code = {"added": 200, "on_list": 200, "refused": 422}.get(status, 502)
        return _json({"status": status}, code)
    return redirect(_back_to("../groceries#pantry"), code=303)


@app.post("/groceries/thaw-tick")
def groceries_thaw_tick():
    """Toggle a Thaw row (in the fridge / not yet). Local state only."""
    try:
        key = (request.form.get("key") or "").strip()[:200]
        date = key.split("|", 1)[0]
        datetime.date.fromisoformat(date)              # reject junk keys
        today = datetime.datetime.now(TZ).date()
        done = _load_thaw(today)
        if key in done:
            done.pop(key)
        else:
            done[key] = date
        _save_thaw(done)
    except Exception:
        log.exception("thaw tick failed")
    return redirect("../groceries#thaw", code=303)



# ---- Kitchen board (/meal/kitchen) + household page (/meal/home) -------------
# Native pages, no iframes (owner design 2026-11-10). Everything renders from
# server-side caches, so a page view does not wait on an upstream:
#   meal plan + recipes  the groceries snapshot (_grocery_data, warmed every 120 s)
#                        and the shared recipe cache (30 min)
#   weather              WX_FEED, Open-Meteo, 15 min (shared with /weather)
#   calendar             CAL_FEED, the Proton ICS feed, 15 min, recurring expanded
#   chores               TASKS_FEED, Vikunja (jmack token), 2 min; a tick re-reads it
#   vacuum               HA_FEED, Home Assistant /api/states, 60 s
# The home warmer thread refreshes each feed when it goes stale.
ICS_URL = os.environ.get("HOMEPAGE_VAR_ICS_URL", "")          # a secret: never log it
HA_URL = os.environ.get("HA_URL", "http://10.1.30.21:8123")
HA_TOKEN = os.environ.get("HOMEPAGE_VAR_HA_TOKEN", "")
HOME_REFRESH_S = int(os.environ.get("HOME_REFRESH_S", "30"))   # 0 = no warmer
KITCHEN_SKIP_PROJECTS = ("Approvals",)    # decision tasks, not chores
CAL_DAYS = 9                              # today .. a week out, plus the night view


def _cal_fetch() -> list[dict]:
    today = datetime.datetime.now(TZ).date()
    return homecal.expand(homecal.fetch_ics(ICS_URL), today, CAL_DAYS, TZ)


CAL_FEED = Feed("calendar", 900, _cal_fetch)
HA_FEED = Feed("vacuum", 60, lambda: vacuum.fetch_states(HA_URL, HA_TOKEN))
TASKS_FEED = Feed("tasks", 120, lambda: _fetch_tasks(VIKUNJA_TOKENS.get("jmack", "")))
HOME_FEEDS = (WX_FEED, CAL_FEED, HA_FEED, TASKS_FEED)


def _home_warmer() -> None:
    while True:
        for f in HOME_FEEDS:
            if f.due():
                f.kick()                  # one thread per feed: a slow one can't hold the rest
        time.sleep(HOME_REFRESH_S)


def start_home_warmer() -> None:
    if HOME_REFRESH_S > 0:
        threading.Thread(target=_home_warmer, daemon=True, name="home-warmer").start()


def _asset_ver() -> str:
    import hashlib
    h = hashlib.sha256()
    for name in ("home.css", "home.js"):
        try:
            with open(os.path.join(app.static_folder, name), "rb") as fh:
                h.update(fh.read())
        except OSError:
            pass
    return h.hexdigest()[:10]


ASSET_VER = _asset_ver()


def _onhand(food: str) -> bool:
    try:
        return stock.is_pantry_or_stock(food)
    except Exception:
        return False


def _dishes(entries: list[dict]) -> list[dict]:
    out = []
    for e in entries:
        if not e.get("recipe_id"):
            continue
        try:
            out.append(kitchen.make_dish(e, _recipe(e["recipe_id"]), _onhand))
        except Exception:
            log.warning("kitchen: recipe %s unavailable", e.get("recipe_id"))
    return out


def _now() -> datetime.datetime:
    return datetime.datetime.now(TZ)


def _home_data(now: datetime.datetime | None = None) -> dict:
    """Everything both pages show, from the caches."""
    now = now or _now()
    today = now.date()
    tomorrow = today + datetime.timedelta(days=1)
    plan, lines = [], []
    try:
        snap, _src = _grocery_data()
        plan, lines = snap["plan"] or [], snap["lines"] or []
    except Exception:
        log.exception("kitchen: meal plan unavailable")
    tasks = TASKS_FEED.get()
    cols = (_task_columns(tasks[0], tasks[1], today, KITCHEN_SKIP_PROJECTS)
            if tasks is not None else None)
    ha = HA_FEED.get()
    thaw = ""
    try:
        thaw = kitchen.thaw_hint(_thaw_rows(lines, today, _load_thaw(today)), tomorrow, today)
    except Exception:
        log.exception("kitchen: thaw rows failed")
    return {
        "now": now, "today": today,
        "wx": kitchen.wx_view(WX_FEED.get(), now, WMO),
        "cal": CAL_FEED.get(),
        "dinner": _dishes(kitchen.dinner_entries(plan, today)),
        "lunch": kitchen.lunch_prep(plan, stock.plan_week_start(today)),
        "tomorrow_titles": [e["title"] for e in kitchen.dinner_entries(plan, tomorrow)],
        "thaw": thaw,
        "chores": cols,
        "vacuum": vacuum.summary(ha, now, TZ) if ha is not None else None,
    }


def _back_href() -> str:
    if kitchen.on_kitchen_host():
        return kitchen.kpath()
    return f"{kitchen.BASE}/home" if request.args.get("from") == "home" else f"{kitchen.BASE}/kitchen"


def _html(body: str) -> Response:
    r = Response(body, mimetype="text/html")
    r.headers["Cache-Control"] = "no-store"
    return r


@app.get("/kitchen")
def kitchen_board():
    d = _home_data()
    if request.args.get("fragment") == "1":
        return _html(kitchen.board_fragment(d))
    return _html(kitchen.kitchen_page(d, ASSET_VER))


@app.get("/kitchen/chores")
def kitchen_chores():
    d = _home_data()
    if request.args.get("fragment") == "1":
        return _html(kitchen.chores_fragment(d))
    return _html(kitchen.chores_page(d, ASSET_VER, kitchen.kpath()))


@app.get("/kitchen/weather")
def kitchen_weather():
    """The weekly forecast, from the 15-min WX_FEED cache (no upstream call
    here once the warmer has filled it)."""
    now = _now()
    fc = kitchen.forecast_view(WX_FEED.get(), now, WMO)
    return _html(kitchen.forecast_page(fc, now, ASSET_VER, _back_href()))


@app.get("/kitchen/ingredients")
def kitchen_ingredients():
    d = _home_data()
    return _html(kitchen.ingredients_page(d["dinner"], d["today"], ASSET_VER, _back_href()))


@app.get("/kitchen/steps")
def kitchen_steps():
    d = _home_data()
    dishes = d["dinner"]
    try:
        rid = int(request.args.get("r") or 0)
    except ValueError:
        rid = 0
    dish = next((x for x in dishes if x["rid"] == rid), dishes[0] if dishes else None)
    return _html(kitchen.steps_page(dish, dishes, ASSET_VER, _back_href()))


def _pantry_check_data(now: datetime.datetime | None = None) -> dict:
    """The pantry check view, from the groceries snapshot (no extra upstream
    call): this plan week's pantry/stock foods by store and aisle, the rest of
    the pantry catalog, what is already on the list, and the Manual items."""
    now = now or _now()
    today = now.date()
    entries, lines, plan_ok = None, [], False
    try:
        snap, _src = _grocery_data()
        entries, lines, plan_ok = snap["entries"], snap["lines"] or [], snap["plan"] is not None
    except Exception:
        log.exception("pantry check: groceries snapshot unavailable")
    on = _open_reorders(entries or [])

    def label(store, aisle):
        return f"{_STORE_LABEL.get(store, store)} · {aisle}"

    used = _used_this_week(lines, stock.plan_week_start(today))
    recipes = {u["food"]: u["recipes"] for u in used}
    used_groups = [{"label": label(st, a), "foods": [
        {"food": f, "recipes": recipes[f], "on_list": f.lower() in on} for f in foods]}
        for st, aisles in stock.group(list(recipes), sort_names=False).items()
        for a, foods in aisles.items()]
    used_low = {f.lower() for f in recipes}
    staples = []
    for st, aisles in stock.catalog().items():
        for a, foods in aisles.items():
            rest = [{"food": f, "recipes": [], "on_list": f.lower() in on}
                    for f in foods if f.lower() not in used_low]
            if rest:
                staples.append({"label": label(st, a), "foods": rest})
    manual = None
    if entries is not None:
        manual = [_item_label(e) for e in _split_groceries(entries)[1]]
    return {"today": today, "plan_ok": plan_ok, "used": used_groups,
            "staples": staples, "manual": manual}


@app.get("/kitchen/pantry")
def kitchen_pantry():
    d = dict(_pantry_check_data(), flash=_added_flash(), manual_ok=FORCED.writable())
    return _html(kitchen.pantry_page(d, ASSET_VER, _back_href()))


@app.get("/home")
def household():
    d = _home_data()
    all_chores = request.args.get("chores") == "all"
    if request.args.get("fragment") == "1":
        return _html(kitchen.household_fragment(d, all_chores))
    d = dict(d, flash=_added_flash(), manual_ok=FORCED.writable(),
             admin_url=ADMIN_ORIGIN + "/" if remote_user() in ADMIN_USERS else "")
    return _html(kitchen.household_page(d, ASSET_VER, all_chores))


# Step photos are Tandoor UserFiles: /api/download-file/<id>/ needs the
# dashboard's session (anonymous = 403), so the browser cannot load them
# directly. This proxy serves ONLY file ids attached to a step of a planned
# recipe (the meal-plan snapshot: this plan week to two weeks out), caches the
# bytes in memory and sends them as an image.
_PHOTO_CACHE: dict[int, tuple[float, bytes, str]] = {}
_PHOTO_TTL = 86400
_PHOTO_MAX = 80                       # ~80 x 30-150 KB
_PHOTO_MAX_BYTES = 8 * 1024 * 1024
_photo_lock = threading.Lock()


def _planned_photo_ids() -> set[int]:
    try:
        snap, _src = _grocery_data()
    except Exception:
        return set()
    ids: set[int] = set()
    for rid in {e.get("recipe_id") for e in snap["plan"] or [] if e.get("recipe_id")}:
        try:
            ids |= {s["file_id"] for s in _recipe(rid).get("steps") or [] if s.get("file_id")}
        except Exception:
            continue
    return ids


def _image_type(b: bytes) -> str | None:
    if b[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp"
    if b[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None


def _fetch_photo(fid: int) -> tuple[bytes, str] | None:
    with _photo_lock:
        hit = _PHOTO_CACHE.get(fid)
    if hit and time.time() - hit[0] < _PHOTO_TTL:
        return hit[1], hit[2]
    r = _api_get(f"/api/download-file/{fid}/")
    if r.status_code != 200 or len(r.content) > _PHOTO_MAX_BYTES:
        return None
    data = _unzip_one(r.content)
    ctype = _image_type(data) if data else None
    if not ctype:                     # only images leave this route
        return None
    with _photo_lock:
        if len(_PHOTO_CACHE) >= _PHOTO_MAX:
            _PHOTO_CACHE.pop(min(_PHOTO_CACHE, key=lambda k: _PHOTO_CACHE[k][0]))
        _PHOTO_CACHE[fid] = (time.time(), data, ctype)
    return data, ctype


def _unzip_one(body: bytes) -> bytes | None:
    """Tandoor 2.x's download-file sends the UserFile wrapped in a zip
    (attachment "<name>.zip", one member "files/<uuid>.jpg", seen live
    2026-11-10). Return that member; a bare file passes through."""
    if body[:4] != b"PK\x03\x04":
        return body
    import io
    import zipfile
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            for info in z.infolist():
                if not info.is_dir() and info.file_size <= _PHOTO_MAX_BYTES:
                    return z.read(info)
    except zipfile.BadZipFile:
        return None
    return None


@app.get("/kitchen/step-photo/<int:fid>")
def kitchen_step_photo(fid: int):
    if fid not in _planned_photo_ids():
        return Response("not found", status=404, mimetype="text/plain")
    try:
        got = _fetch_photo(fid)
    except Exception:
        log.warning("step photo %s fetch failed", fid)
        got = None
    if not got:
        return Response("not found", status=404, mimetype="text/plain")
    r = Response(got[0], mimetype=got[1])
    r.headers["Cache-Control"] = "private, max-age=86400"
    r.headers["X-Content-Type-Options"] = "nosniff"
    return r


@app.get("/manifest.webmanifest")
def manifest():
    b = kitchen.base()
    start, scope = f"{b}/kitchen", f"{b}/"
    if kitchen.on_kitchen_host():
        start = scope = f"{KITCHEN_ORIGIN}/"
    body = {
        "name": "Mack House Kitchen", "short_name": "Kitchen",
        "start_url": start, "scope": scope, "display": "standalone",
        "orientation": "landscape", "background_color": "#100D0B", "theme_color": "#100D0B",
        "icons": [{"src": f"{b}/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
                  {"src": f"{b}/static/icon-512.png", "sizes": "512x512", "type": "image/png"}],
    }
    return Response(json.dumps(body), mimetype="application/manifest+json")


# ---- kitchen.kmkdp.com: paired kitchen devices, no Authelia ------------------
# The internal Traefik routes kitchen.kmkdp.com here with NO forwardauth; its
# middleware strips client-sent Remote-* headers and sets X-Kdk-Kitchen: 1.
# This app treats a request as the kitchen host when the Host matches OR that
# header is set (either one only ever narrows what a request can do), drops
# Remote-* again (other containers can reach :8080 directly), serves only the
# kitchen allowlist and wants the signed device cookie for all of it except
# /static, the manifest and /pair. Pairing codes are issued on home.kmkdp.com
# (Authelia two_factor) by a KITCHEN_ADMINS user.
KITCHEN_HOST = os.environ.get("KITCHEN_HOST", "kitchen.kmkdp.com").strip().lower()
KITCHEN_ORIGIN = f"https://{KITCHEN_HOST}"
HOME_ORIGIN = os.environ.get("HOME_ORIGIN", "https://home.kmkdp.com").rstrip("/")
KITCHEN_ADMINS = {u.strip() for u in os.environ.get("KITCHEN_ADMINS", "jmack").split(",") if u.strip()}
KITCHEN_DEVICE_KEY = os.environ.get("KITCHEN_DEVICE_KEY", "")        # a secret: never log it
KITCHEN_STATE = os.path.join(os.environ.get("KITCHEN_STATE_DIR", "/state"), "devices.json")
_REMOTE_ENV = ("HTTP_REMOTE_USER", "HTTP_REMOTE_NAME", "HTTP_REMOTE_EMAIL", "HTTP_REMOTE_GROUPS")
PAIR_LIMIT = devices.RateLimiter(limit=10, window=600)
_pair_state: dict = {"sig": None, "signer": None, "registry": None}
_pair_lock = threading.Lock()


def _pairing():
    """(Signer, Registry), or None when KITCHEN_DEVICE_KEY is missing/short."""
    if not devices.key_ok(KITCHEN_DEVICE_KEY):
        return None
    import hashlib
    sig = (hashlib.sha256(KITCHEN_DEVICE_KEY.encode()).hexdigest(), KITCHEN_STATE)
    with _pair_lock:
        if _pair_state["sig"] != sig:
            signer = devices.Signer(KITCHEN_DEVICE_KEY)
            try:
                reg = devices.Registry(KITCHEN_STATE, signer.kid)
            except OSError:                    # /state not mounted or not writable
                log.exception("kitchen: device registry not writable; kitchen host answers 503")
                return None
            _pair_state.update(sig=sig, signer=signer, registry=reg)
        return _pair_state["signer"], _pair_state["registry"]


def _norm_host(h: str) -> str:
    h = (h or "").strip().lower()
    if h.startswith("["):                      # [v6]:port
        return h
    return h.rsplit(":", 1)[0].rstrip(".") if ":" in h else h.rstrip(".")


def _is_kitchen_request() -> bool:
    return (_norm_host(request.host) == KITCHEN_HOST
            or request.headers.get("X-Kdk-Kitchen") == "1")


def _client_ip() -> str:
    """The client as Traefik saw it: the last X-Forwarded-For hop (Traefik
    replaces any client-sent X-Forwarded-For, untrusted by default)."""
    xff = [x.strip() for x in (request.headers.get("X-Forwarded-For") or "").split(",") if x.strip()]
    return xff[-1] if xff else (request.remote_addr or "?")


def _text(msg: str, status: int) -> Response:
    r = Response(msg, status=status, mimetype="text/plain")
    r.headers["Cache-Control"] = "no-store"
    return r


def _page(body: str, status: int = 200) -> Response:
    r = _html(body)
    r.status_code = status
    return r


def _current_device(p) -> dict | None:
    signer, reg = p
    now = time.time()
    claims, why = signer.verify(request.cookies.get(devices.COOKIE), now)
    dev = reg.active(claims) if claims else None
    if claims and dev is None:
        why = "unknown or revoked device"
    if dev is None:
        if why != "no cookie":
            log.info("kitchen: device cookie refused (%s) ip=%s", why, _client_ip())
        return None
    reg.touch(dev["id"], now)
    return dev


_KITCHEN_GET = {"/": "kitchen_board", "/ingredients": "kitchen_ingredients",
                "/steps": "kitchen_steps", "/pantry": "kitchen_pantry", "/chores": "kitchen_chores",
                "/weather": "kitchen_weather"}
_KITCHEN_POST = {"/tasks/complete", "/groceries/add", "/groceries/reorder"}
_STEP_PHOTO = re.compile(r"^/step-photo/(\d{1,9})$")


def _kitchen_gate():
    p = _pairing()
    if p is None:
        return _text("kitchen pairing not configured", 503)
    path, method = request.path, request.method
    get = method in ("GET", "HEAD")
    photo = _STEP_PHOTO.match(path) if get else None
    if path == "/pair" and method in ("GET", "HEAD", "POST"):
        return None                                  # pair(): rate limit + Origin check
    if get and (path.startswith("/static/") or path == "/manifest.webmanifest"):
        return None                                  # not secret; the Home Screen icon fetch has no cookie
    allowed = (get and (path in _KITCHEN_GET or photo)) or (method == "POST" and path in _KITCHEN_POST)
    if not allowed:
        return _text("not found", 404)
    dev = _current_device(p)
    if dev is None:
        if get and not photo:
            return _page(devices.unpaired_page(kitchen.head("Pair this screen", ASSET_VER, kiosk=False)), 401)
        return _text("this device is not paired", 401)
    g.kitchen_device = dev
    if method == "POST":
        if not devices.origin_ok(request.headers.get("Origin"), request.headers.get("Referer"), KITCHEN_ORIGIN):
            log.warning("kitchen: POST %s refused (origin) device=%s", path, dev["id"])
            return _text("forbidden", 403)
        if request.form.get("ajax") == "1" and request.headers.get("X-Kitchen") != "1":
            log.warning("kitchen: POST %s refused (no X-Kitchen) device=%s", path, dev["id"])
            return _text("forbidden", 403)
        return None                                  # the existing handler
    if photo:
        return kitchen_step_photo(int(photo.group(1)))
    return app.view_functions[_KITCHEN_GET[path]]()


@app.before_request
def _host_gate():
    on = _is_kitchen_request()
    kitchen.set_kitchen_host(on)          # every request sets it: waitress reuses threads
    if not on:
        # admin.kmkdp.com (the kitchen check wins: it only ever narrows)
        return _admin_host_gate() if _is_admin_request() else None
    for k in _REMOTE_ENV:                 # also catches Remote_User (same WSGI key)
        request.environ.pop(k, None)
    return _kitchen_gate()


ADMIN_CSP = ("default-src 'none'; style-src 'self'; img-src 'self'; font-src 'self'; "
             "form-action 'self'; base-uri 'none'; frame-ancestors 'none'")


@app.after_request
def _kitchen_headers(resp: Response) -> Response:
    admin_host = not kitchen.on_kitchen_host() and _is_admin_request()
    if admin_host:
        resp.headers["Content-Security-Policy"] = ADMIN_CSP
        if not request.path.startswith("/static/"):
            resp.headers["Cache-Control"] = "no-store"
    if kitchen.on_kitchen_host() or admin_host:
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
    return resp


@app.route("/pair", methods=["GET", "POST"])
def pair():
    if not kitchen.on_kitchen_host():
        return _text("not found", 404)
    signer, reg = _pairing()                         # the gate already answered 503 without a key
    ip = _client_ip()
    head = kitchen.head("Pair this screen", ASSET_VER, kiosk=False)
    if request.method != "POST":
        # The link is /pair#code=...: the code stays in the browser and the page
        # script fills it in. A ?code= query is ignored on purpose (not read,
        # not logged), so a code never travels in a URL the server sees.
        return _page(devices.pair_page(head))
    if not PAIR_LIMIT.hit(ip, time.time()):
        log.warning("kitchen: pair rate-limited ip=%s", ip)
        return _page(devices.message_page(head, "Too many tries", "Wait ten minutes and try again."), 429)
    if not devices.origin_ok(request.headers.get("Origin"), request.headers.get("Referer"), KITCHEN_ORIGIN):
        log.warning("kitchen: pair POST refused (origin) ip=%s", ip)
        return _text("forbidden", 403)
    now = time.time()
    if not reg.consume_code(devices.normalize_code(request.form.get("code")), now):
        log.info("kitchen: pair refused (unknown, used or expired code) ip=%s", ip)
        return _page(devices.pair_page(head, error="That code is used or expired. Make a new one."), 400)
    dev = reg.add_device(request.form.get("name"), now)
    token = signer.issue(dev["id"], dev["issued"], dev["expires"])
    log.info("kitchen: device paired id=%s name=%r ip=%s", dev["id"], dev["name"], ip)
    r = redirect("/", code=303)
    # host-only (no Domain), so the cookie never reaches another *.kmkdp.com host
    r.set_cookie(devices.COOKIE, token, max_age=devices.TOKEN_TTL, path="/",
                 secure=True, httponly=True, samesite="Strict")
    r.headers["Cache-Control"] = "no-store"
    return r


# ---- device admin on home.kmkdp.com (Authelia two_factor, KITCHEN_ADMINS) ------
def _admin_gate(post: bool):
    if kitchen.on_kitchen_host():
        return _text("not found", 404)
    user = remote_user()
    if user not in KITCHEN_ADMINS:
        log.warning("kitchen devices: refused for %r", user or "anonymous")
        return _text("forbidden", 403)
    if post and not devices.origin_ok(request.headers.get("Origin"), request.headers.get("Referer"), HOME_ORIGIN):
        log.warning("kitchen devices: POST refused (origin) user=%s", user)
        return _text("forbidden", 403)
    return None


def _devices_view(code: str = "") -> Response:
    p = _pairing()
    devs = p[1].devices() if p else []
    link = f"{KITCHEN_ORIGIN}/pair#code={code}" if code else ""   # fragment: never sent to a server
    body = devices.devices_page(kitchen.head("Kitchen devices", ASSET_VER, kiosk=False), devs, TZ,
                                p is not None, kitchen.BASE, code=code, link=link,
                                qr=devices.qr_svg(link) if code else "")
    return _page(body)


@app.get("/kitchen/devices")
def kitchen_devices():
    return _admin_gate(post=False) or _devices_view()


@app.post("/kitchen/devices/pair")
def kitchen_devices_pair():
    refused = _admin_gate(post=True)
    if refused:
        return refused
    p = _pairing()
    if p is None:
        return _devices_view()
    code = p[1].new_code(remote_user(), time.time())
    log.info("kitchen: pairing code issued by %s (valid %d min)", remote_user(), devices.CODE_TTL // 60)
    return _devices_view(code)


@app.post("/kitchen/devices/revoke")
def kitchen_devices_revoke():
    refused = _admin_gate(post=True)
    if refused:
        return refused
    p = _pairing()
    did = request.form.get("id") or ""
    if p and devices.device_id_ok(did):
        dev = p[1].revoke(did, remote_user(), time.time())
        if dev:
            log.info("kitchen: device revoked id=%s name=%r by %s", did, dev.get("name"), remote_user())
    return redirect(f"{kitchen.BASE}/kitchen/devices", code=303)


@app.post("/kitchen/devices/revoke-all")
def kitchen_devices_revoke_all():
    refused = _admin_gate(post=True)
    if refused:
        return refused
    p = _pairing()
    if p:
        n = p[1].revoke_all(remote_user(), time.time())
        log.info("kitchen: all devices revoked (%d) by %s", n, remote_user())
    return redirect(f"{kitchen.BASE}/kitchen/devices", code=303)


# ---- admin.kmkdp.com: the lab admin page (Authelia two_factor, user:jmack) ------
# The internal Traefik routes admin.kmkdp.com here WITH forwardauth; Authelia's
# admin.kmkdp.com rule admits only jmack, and the sidecar checks Remote-User
# against ADMIN_USERS again (403 otherwise). On that host only GET / (the page,
# with the syslog search as query args) and /static/* exist; every other path
# is a 404, and the admin page exists on no other host. Read-only: no POSTs.
# Every source sits in a Feed warmed by its own thread, so a page view only
# reads caches; the syslog search is the one on-demand upstream call (timeout,
# rate limit, capped at 200 lines).
ADMIN_HOST = os.environ.get("ADMIN_HOST", "admin.kmkdp.com").strip().lower()
ADMIN_ORIGIN = f"https://{ADMIN_HOST}"
ADMIN_USERS = {u.strip() for u in os.environ.get("ADMIN_USERS", "jmack").split(",") if u.strip()}
ADMIN_REFRESH_S = int(os.environ.get("ADMIN_REFRESH_S", "30"))          # 0 = no warmer
PROM_URL = os.environ.get("PROM_URL", "http://10.1.20.30:9090").rstrip("/")
AM_URL = os.environ.get("ALERTMANAGER_URL", "http://10.1.20.30:9093").rstrip("/")
AM_UI = os.environ.get("ALERTMANAGER_UI", "https://alertmanager.kmkdp.com").rstrip("/")
LOKI_URL = os.environ.get("LOKI_URL", "http://10.1.20.21:3100").rstrip("/")
GRAFANA_UI = os.environ.get("GRAFANA_UI", "https://grafana.kmkdp.com").rstrip("/")
HA_UI = os.environ.get("HA_UI", "https://homeassistant.kmkdp.com").rstrip("/")
KOMODO_URL = os.environ.get("KOMODO_URL", "https://komodo.kmkdp.com").rstrip("/")
KOMODO_API_KEY = os.environ.get("KOMODO_API_KEY", "")          # a secret: never log it
KOMODO_API_SECRET = os.environ.get("KOMODO_API_SECRET", "")    # a secret: never log it
UPS_PREFIX = os.environ.get("UPS_ENTITY_PREFIX", "sensor.computer_room_eaton_5px_")
NAS_INSTANCE = os.environ.get("NAS_INSTANCE", "10.1.3.5:9100")
NAS_POOL = os.environ.get("NAS_POOL", "tankz3")
SYSLOG_HOSTS_FALLBACK = ["kdk-dkr-01", "kdk-dkr-02", "kdk-dkr-03", "kdk-dkr-04", "kdk-dkr-dmz-01",
                         "kdk-dns-01", "kdk-gw-01", "kdk-hyp-01", "kdk-log-01", "kdk-mon-01"]
SYSLOG_LIMIT = devices.RateLimiter(limit=30, window=600)        # per admin user
CHART_DEFS = admin.chart_defs(GRAFANA_UI, HA_UI, NAS_INSTANCE, NAS_POOL, UPS_PREFIX)

ADMIN_FEEDS = {
    "alerts": Feed("admin-alerts", 30, lambda: admin.fetch_alerts(AM_URL)),
    "probes": Feed("admin-probes", 60, lambda: admin.fetch_probes(PROM_URL)),
    "backups": Feed("admin-backups", 300, lambda: admin.fetch_backups(LOKI_URL)),
    "komodo": Feed("admin-komodo", 300, lambda: admin.fetch_komodo(KOMODO_URL, KOMODO_API_KEY, KOMODO_API_SECRET)),
    # anonymous GitHub API = 60 requests/h per IP: 15 min cache, 5 min retry
    "github": Feed("admin-github", 900, lambda: admin.fetch_github(admin.GITHUB_REPO), retry=300),
    # hourly: the 8-day limit-1 query leaves cancelled Loki sub-queries behind
    # (most of its warnings and a ~4 s stall on the next query); 15 min was too often
    "advisories": Feed("admin-advisories", 3600, lambda: admin.fetch_advisories(LOKI_URL)),
    "ups": Feed("admin-ups", 60, lambda: admin.fetch_ups(HA_URL, HA_TOKEN, UPS_PREFIX)),
    "charts": Feed("admin-charts", 300, lambda: admin.fetch_charts(PROM_URL, HA_URL, HA_TOKEN, CHART_DEFS)),
    "signins": Feed("admin-signins", 300, lambda: admin.fetch_signins(LOKI_URL)),
    "hosts": Feed("admin-syslog-hosts", 3600, lambda: admin.fetch_syslog_hosts(LOKI_URL)),
}


def _admin_warmer() -> None:
    while True:
        for f in ADMIN_FEEDS.values():
            if f.due():
                f.kick()
        time.sleep(ADMIN_REFRESH_S)


def start_admin_warmer() -> None:
    if ADMIN_REFRESH_S > 0:
        threading.Thread(target=_admin_warmer, daemon=True, name="admin-warmer").start()


def _feed_state(f: Feed) -> tuple:
    """(value, 'ok' | 'loading' | 'failed') from the cache only (never blocks)."""
    v = f.get(block=False)
    if v is not None:
        return v, "ok"
    return None, ("failed" if f.tried and f.error else "loading")


def _is_admin_request() -> bool:
    return _norm_host(request.host) == ADMIN_HOST


def _syslog_hosts() -> list[str]:
    return ADMIN_FEEDS["hosts"].get(block=False) or SYSLOG_HOSTS_FALLBACK


def _syslog_search(user: str) -> tuple[dict, dict | None]:
    args = request.args
    rng = args.get("range") if args.get("range") in admin.RANGES else "1h"
    host = (args.get("host") or "").strip()
    form = {"q": (args.get("q") or "")[:admin.QUERY_MAX * 2], "host": host, "range": rng}
    if "q" not in args:
        return form, None
    try:
        q = admin.clean_query(args.get("q"))
    except ValueError as e:
        return form, {"error": str(e), "rows": []}
    form["q"] = q
    if host and (not admin.HOST_RE.match(host) or host not in _syslog_hosts()):
        return form, {"error": "Unknown host.", "rows": []}
    note = ""
    if not host and q and q in _syslog_hosts():
        # the whole query is a host name: filter by host, not by line text
        host, q = q, ""
        form.update(host=host, q="")
        note = f"“{host}” is a host: showing that host's newest lines, not lines containing it."
    elif not q:
        note = "No search text: the newest lines."
    if not SYSLOG_LIMIT.hit(user, time.time()):
        log.warning("admin: syslog search rate-limited user=%s", user)
        return form, {"error": "Too many searches: wait a few minutes.", "rows": []}
    t0 = time.monotonic()
    try:
        rows = admin.search_syslog(LOKI_URL, q, host, rng)
    except (requests.Timeout, TimeoutError) as e:
        log.warning("admin: syslog search timed out: %s", type(e).__name__)
        return form, {"error": "Loki did not answer in time. Try a shorter range or open Grafana Explore.", "rows": []}
    except admin.UpstreamError as e:
        log.warning("admin: syslog search failed: Loki HTTP %s", e.status)
        msg = " ".join(e.body.split())[:200] or "no details"
        return form, {"error": f"Loki returned an error ({e.status}): {msg}", "rows": []}
    except Exception as e:
        log.warning("admin: syslog search failed: %s", type(e).__name__)
        return form, {"error": f"Loki search failed ({type(e).__name__}). Try again or open Grafana Explore.",
                      "rows": []}
    # the query text is the admin's own and may be anything: its length only in the log
    log.info("admin: syslog search user=%s host=%s range=%s len=%d lines=%d ms=%.0f",
             user, host or "all", rng, len(q), len(rows), (time.monotonic() - t0) * 1000)
    return form, {"rows": rows, "note": note}


def admin_page(user: str) -> Response:
    now = time.time()
    st = {k: _feed_state(f) for k, f in ADMIN_FEEDS.items()}
    form, result = _syslog_search(user)
    ok = result is not None and not result.get("error")
    expr = admin.syslog_logql(form["q"] if ok else "", form["host"] if ok else "")
    d = {"tiles": admin.status_tiles(st, now, TZ, KOMODO_URL), "alerts": st["alerts"], "signins": st["signins"],
         "charts": st["charts"], "chart_defs": CHART_DEFS, "probes": st["probes"],
         "form": form, "syslog": result, "hosts": _syslog_hosts(), "user": user,
         "home_url": f"{HOME_ORIGIN}{kitchen.BASE}/home", "am_ui": AM_UI,
         "explore": admin.explore_url(GRAFANA_UI, expr, form["range"]), "now": now, "tz": TZ,
         "admin_host": ADMIN_HOST}
    return _html(admin.page(d, ASSET_VER))


def _admin_host_gate():
    """admin.kmkdp.com: ADMIN_USERS only, GET / and /static/* only."""
    user = (request.headers.get("Remote-User") or "").strip()
    if user not in ADMIN_USERS:
        log.warning("admin: refused for %r path=%s", user or "anonymous", request.path)
        return _text("forbidden", 403)
    if request.method not in ("GET", "HEAD"):
        return _text("not found", 404)
    if request.path.startswith("/static/"):
        return None                                  # Flask's static route
    if request.path == "/":
        return admin_page(user)
    return _text("not found", 404)


if __name__ == "__main__":
    if not (USER and PASS):
        log.warning("TANDOOR_USER/TANDOOR_PASS not set -- meal plan will fail to load")
    start_grocery_warmer()      # first refresh right away: the first view is hot too
    start_home_warmer()         # weather, calendar, chores, vacuum for the kitchen pages
    start_admin_warmer()        # admin.kmkdp.com status sources (first refresh right away)
    log_kitchen_token_mode()    # once: is the Kitchen Vikunja account in use yet?
    if _pairing() is None:
        log.warning("KITCHEN_DEVICE_KEY missing or shorter than %d bytes: "
                    "kitchen.kmkdp.com answers 503", devices.MIN_KEY_BYTES)
    serve(app, host="0.0.0.0", port=8080)
