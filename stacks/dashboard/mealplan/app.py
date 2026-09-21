#!/usr/bin/env python3
"""dashboard-mealplan: render the Tandoor weekly meal plan for the family home
dashboard, and let household members submit a star rating from the dashboard.

Why a sidecar (not a Homepage widget calling Tandoor directly): Tandoor has DRF
**token auth disabled** -- only Session + OAuth2. So we log in with a dedicated
Tandoor account (username/password, op-injected) to get a session + CSRF cookie, then
call the JSON API. The account must be a member of the "Mack House" space or the plan
comes back empty (every Tandoor object is space-scoped).

Served (behind Traefik, StripPrefix /meal):
  GET  /            -> HTML week grid (embedded as an iframe on the dashboard)
  GET  /mealplan.json -> compact JSON (for a Homepage customapi widget, optional)
  POST /rate        -> create a CookLog rating, then redirect back to /
  GET  /healthz     -> "ok"

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
import os, re, json, datetime, logging
from zoneinfo import ZoneInfo
import requests
import urllib3
urllib3.disable_warnings()
from flask import Flask, request, Response, redirect
from waitress import serve

TANDOOR_URL = os.environ.get("TANDOOR_URL", "https://recipes.kmkdp.com").rstrip("/")
USER = os.environ.get("TANDOOR_USER", "")
PASS = os.environ.get("TANDOOR_PASS", "")
TZ = ZoneInfo(os.environ.get("TZ", "America/Chicago"))
# Rater identity comes from Authelia ForwardAuth (Remote-User / Remote-Name
# headers injected by the internal Traefik) -- no picker. Map lab usernames to
# household display names; unknown users fall back to Remote-Name or the login.
USER_DISPLAY = {"jmack": "Jacob", "rach": "Rachel"}

def rater_from_headers() -> str:
    login = (request.headers.get("Remote-User") or "").strip()
    if login in USER_DISPLAY:
        return USER_DISPLAY[login]
    name = (request.headers.get("Remote-Name") or "").strip()
    return name or login or "family"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mealplan")
app = Flask(__name__)

# One shared requests.Session; re-login lazily if the session goes stale (403/redirect).
_sess: requests.Session | None = None


def _login() -> requests.Session:
    """Log in via django-allauth and return an authenticated session.
    allauth's login form posts to /accounts/login/ with fields `login` + `password`
    + `csrfmiddlewaretoken`, and needs a Referer. Confirm the field name is `login`
    (not `username`) on first deploy if this fails."""
    s = requests.Session()
    s.headers["User-Agent"] = "kdk-dashboard-mealplan"
    login_url = f"{TANDOOR_URL}/accounts/login/"
    r = s.get(login_url, timeout=20)
    r.raise_for_status()
    m = re.search(r'name=["\']csrfmiddlewaretoken["\']\s+value=["\']([^"\']+)["\']', r.text)
    token = m.group(1) if m else s.cookies.get("csrftoken", "")
    r = s.post(
        login_url,
        data={"login": USER, "username": USER, "password": PASS, "csrfmiddlewaretoken": token},
        headers={"Referer": login_url},
        timeout=20,
        allow_redirects=True,
    )
    r.raise_for_status()
    # Sanity check: an authenticated JSON call should return 200, not 403.
    chk = s.get(f"{TANDOOR_URL}/api/user-preference/", timeout=20)
    if chk.status_code != 200:
        raise RuntimeError(f"login check failed: {chk.status_code}")
    log.info("logged into Tandoor as %s", USER)
    return s


def _session() -> requests.Session:
    global _sess
    if _sess is None:
        _sess = _login()
    return _sess


def _api_get(path: str) -> requests.Response:
    global _sess
    s = _session()
    r = s.get(f"{TANDOOR_URL}{path}", timeout=20)
    if r.status_code in (401, 403):           # stale session -> re-login once
        _sess = _login()
        r = _sess.get(f"{TANDOOR_URL}{path}", timeout=20)
    return r


def _week_bounds(today: datetime.date) -> tuple[datetime.date, datetime.date]:
    # Rolling week AHEAD, not calendar week: on Sunday evening the family cares
    # about tomorrow's dinner, not the week that just ended.
    return today, today + datetime.timedelta(days=6)


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


def fetch_plan(mon: datetime.date, sun: datetime.date) -> list[dict]:
    """Return normalized entries: {date, meal_type, title, recipe_id, url}.
    Tandoor meal-plan entry fields (confirm on deploy): `from_date` (date/datetime),
    nested `recipe` {id, name}, `meal_type` {name} or `meal_type_name`, `title`."""
    r = _api_get(f"/api/meal-plan/?from_date={mon.isoformat()}&to_date={sun.isoformat()}")
    r.raise_for_status()
    data = r.json()
    rows = data.get("results", data) if isinstance(data, dict) else data
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
        })
    return out


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
    r = s.post(
        f"{TANDOOR_URL}/api/cook-log/",
        json=body,
        headers={"X-CSRFToken": csrf, "Referer": f"{TANDOOR_URL}/"},
        timeout=20,
    )
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
@media(max-width:560px){.week{grid-template-columns:repeat(7,minmax(130px,1fr));overflow-x:auto}}
.day{background:var(--ink);border:1px solid var(--ink-hairline);border-radius:9px;padding:8px;min-height:120px}
.day.today{border-color:var(--ember);border-width:2px}
.dow{font-family:'JetBrains Mono',monospace;font-size:11px;font-weight:500;letter-spacing:.22em;
text-transform:uppercase;color:var(--ash)}
.day.today .dow{color:var(--ember)}
.meal{margin-top:6px;padding-top:6px;border-top:1px solid var(--ink-hairline)}
.meal a{color:var(--paper);text-decoration:none;font-weight:600}
.meal a:hover{color:var(--ember);text-decoration:underline;text-decoration-thickness:2px}
.mt{font-family:'JetBrains Mono',monospace;color:var(--bark);font-size:10px;text-transform:uppercase;letter-spacing:.12em}
.rate{margin-top:4px;display:flex;align-items:center;gap:1px}
.rate button{background:none;border:none;color:var(--ash);cursor:pointer;font-size:15px;padding:0 1px;line-height:1}
.rate button:hover{color:var(--ember)}
.rate .as{font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--ash);margin-left:6px}
:focus-visible{outline:2px solid var(--ember);outline-offset:2px}
.empty{color:var(--ash);font-size:13px;padding:24px;text-align:center;font-family:'Space Grotesk',sans-serif}
"""


def render_week(plan: list[dict], today: datetime.date, mon: datetime.date, rater: str) -> str:
    by_day: dict[str, list[dict]] = {}
    for e in plan:
        if e["date"]:
            by_day.setdefault(e["date"], []).append(e)
    cells = []
    for i in range(7):
        d = mon + datetime.timedelta(days=i)
        iso = d.isoformat()
        is_today = " today" if d == today else ""
        parts = [f'<div class="day{is_today}"><div class="dow">{d.strftime("%a %-d")}</div>']
        for e in by_day.get(iso, []):
            mt = f'<div class="mt">{e["meal_type"]}</div>' if e["meal_type"] else ""
            if e["url"]:
                name = f'<a href="{e["url"]}" target="_top">{e["title"]}</a>'
                stars = "".join(
                    f'<button type="submit" name="rating" value="{n}" title="{n} star">&#9733;</button>'
                    for n in range(1, 6)
                )
                rate = (
                    f'<form class="rate" method="post" action="rate">'
                    f'<input type="hidden" name="recipe_id" value="{e["recipe_id"]}">'
                    f'{stars}<span class="as">as {rater}</span></form>'
                )
            else:
                name = e["title"]
                rate = ""
            parts.append(f'<div class="meal">{mt}{name}{rate}</div>')
        parts.append("</div>")
        cells.append("".join(parts))
    body = f'<div class="week">{"".join(cells)}</div>'
    if not plan:
        body = '<div class="empty">No meals planned for this week yet.<br>Open the recipe book to plan some!</div>'
    return f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><style>{PAGE_CSS}</style></head><body>{body}</body></html>"


@app.get("/healthz")
def healthz():
    return "ok"


@app.get("/mealplan.json")
def mealplan_json():
    today = datetime.datetime.now(TZ).date()
    mon, sun = _week_bounds(today)
    try:
        return Response(json.dumps(fetch_plan(mon, sun)), mimetype="application/json")
    except Exception as e:
        log.exception("mealplan.json failed")
        return Response(json.dumps({"error": str(e)}), status=502, mimetype="application/json")


@app.get("/")
def index():
    today = datetime.datetime.now(TZ).date()
    mon, sun = _week_bounds(today)
    try:
        plan = fetch_plan(mon, sun)
    except Exception:
        log.exception("meal plan fetch failed")
        return Response(
            f"<!doctype html><body style='background:#2b2825;color:#8d8377;font-family:sans-serif;padding:20px'>"
            f"Couldn't load the meal plan right now. Try again shortly.</body>",
            mimetype="text/html",
        )
    return Response(render_week(plan, today, mon, rater_from_headers()), mimetype="text/html")


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

def _vik_token() -> tuple[str, str]:
    """(login, token) for the authenticated user; empty token if none stored."""
    login = (request.headers.get("Remote-User") or "").strip()
    return login, VIKUNJA_TOKENS.get(login, "")

def _vik(method: str, path: str, token: str, body=None):
    r = requests.request(method, f"{VIKUNJA_URL}/api/v1{path}",
        headers={"Authorization": f"Bearer {token}"}, json=body, timeout=15, verify=False)
    r.raise_for_status()
    return r.json() if r.text else None

TASKS_CSS = PAGE_CSS + """
.tl{display:flex;gap:8px;margin:0;padding:10px;overflow-x:auto;list-style:none}
.tl li{flex:0 0 170px;background:var(--ink);border:1px solid var(--ink-hairline);border-radius:9px;
padding:8px;display:flex;flex-direction:column;gap:4px}
.tl li.over{border-color:var(--danger)}
.tl form{display:flex;margin:0}
.tl button{width:20px;height:20px;border:2px solid var(--ash);border-radius:6px;background:none;cursor:pointer}
.tl button:hover{border-color:var(--ok);background:var(--ink-surface)}
.tt{font-weight:600;font-size:13px;line-height:1.25}
.due{font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--ash);white-space:nowrap}
li.over .due{color:var(--danger);font-weight:500}
.proj{font-family:'JetBrains Mono',monospace;font-size:9px;letter-spacing:.12em;text-transform:uppercase;color:var(--stone)}
.note{padding:10px;font-size:12px;color:var(--ash)}
"""

@app.get("/tasks")
def tasks_view():
    # Shared family board: the LIST always comes from the jmack account (single
    # source of truth); completion still uses the viewer's own token when stored
    # so Vikunja attributes the done-by correctly.
    token = VIKUNJA_TOKENS.get("jmack", "")
    who = rater_from_headers()
    if not token:
        return Response(f"<!doctype html><style>{TASKS_CSS}</style><div class='note'>No Vikunja token stored for {who} yet - open <a href='{VIKUNJA_URL}' target='_top'>tasks.kmkdp.com</a>, create an API token, and have it added as vikunja-api-{login or 'user'}.</div>", mimetype="text/html")
    try:
        projects = {p["id"]: p["title"] for p in _vik("GET", "/projects", token)}
        tasks = _vik("GET", "/tasks/all?sort_by=due_date&order_by=asc&filter=done%3Dfalse&per_page=60", token) or []
    except Exception:
        log.exception("vikunja fetch failed")
        return Response(f"<!doctype html><style>{TASKS_CSS}</style><div class='note'>Couldn't reach the task list right now.</div>", mimetype="text/html")
    today = datetime.datetime.now(TZ).date()
    horizon = today + datetime.timedelta(days=14)
    rows = []
    for t in tasks:
        if t.get("done"): continue
        due = _parse_date((t.get("due_date") or "")[:10])
        if due and due > horizon: continue
        overdue = " over" if (due and due < today) else ""
        due_s = due.strftime("%b %-d") if due else ""
        proj = projects.get(t.get("project_id"), "")
        rows.append(
            f"<li class='{overdue.strip()}'><div style='display:flex;align-items:center;gap:6px'>"
            f"<form method='post' action='tasks/complete'>"
            f"<input type='hidden' name='task_id' value='{t['id']}'>"
            f"<button title='done'></button></form>"
            f"<span class='due{overdue}'>{due_s}</span></div>"
            f"<span class='tt'>{t['title']}</span><span class='proj'>{proj}</span></li>")
        if len(rows) >= 20: break
    body = f"<ul class='tl'>{''.join(rows)}</ul>" if rows else "<div class='note'>Nothing due in the next two weeks. 🎉</div>"
    return Response(f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><style>{TASKS_CSS}</style></head><body>{body}</body></html>", mimetype="text/html")

@app.post("/tasks/complete")
def tasks_complete():
    login, token = _vik_token()
    try:
        tid = int(request.form["task_id"])
        if not token:
            token = VIKUNJA_TOKENS.get("jmack", "")
        if token:
            _vik("POST", f"/tasks/{tid}/done", token)
            log.info("task %s completed by %s", tid, login)
    except Exception:
        log.exception("task completion failed")
    return redirect("tasks", code=303)



# ---- Weather: Tiffin, IA 7-day strip (Open-Meteo, keyless) ------------------
WX_URL = ("https://api.open-meteo.com/v1/forecast?latitude=41.706&longitude=-91.663"
          "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
          "&hourly=temperature_2m,precipitation_probability,weather_code"
          "&temperature_unit=fahrenheit&timezone=America%2FChicago&forecast_days=7")
_wx_cache: tuple[float, dict] | None = None
_wx_hourly: dict | None = None

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
    global _wx_cache
    import time as _t
    now = _t.time()
    if _wx_cache and now - _wx_cache[0] < 900:
        d = _wx_cache[1]
    else:
        try:
            _full = requests.get(WX_URL, timeout=15).json()
            d = _full["daily"]
            globals()["_wx_hourly"] = _full.get("hourly")
            _wx_cache = (now, d)
        except Exception:
            log.exception("open-meteo fetch failed")
            if _wx_cache: d = _wx_cache[1]
            else:
                return Response(f"<!doctype html><style>{WX_CSS}</style><div class='empty'>Weather unavailable right now.</div>", mimetype="text/html")
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
    try:
        h = _wx_cache[1]["__hourly"] if isinstance(_wx_cache[1], dict) and "__hourly" in _wx_cache[1] else None
    except Exception:
        h = None
    hr = _wx_hourly or {}
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
    return Response(f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><style>{WX_CSS}</style></head><body>{hourly_html}<div class='week'>{''.join(cells)}</div></body></html>", mimetype="text/html")


if __name__ == "__main__":
    if not (USER and PASS):
        log.warning("TANDOOR_USER/TANDOOR_PASS not set -- meal plan will fail to load")
    serve(app, host="0.0.0.0", port=8080)
