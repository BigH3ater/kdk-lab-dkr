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
from flask import Flask, request, Response, redirect
from waitress import serve

TANDOOR_URL = os.environ.get("TANDOOR_URL", "https://recipes.kmkdp.com").rstrip("/")
USER = os.environ.get("TANDOOR_USER", "")
PASS = os.environ.get("TANDOOR_PASS", "")
TZ = ZoneInfo(os.environ.get("TZ", "America/Chicago"))
RATERS = [r.strip() for r in os.environ.get("RATERS", "Jacob,Rachel").split(",") if r.strip()]

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
*{box-sizing:border-box}body{margin:0;font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
background:#1c1917;color:#e7e5e4}
.week{display:grid;grid-template-columns:repeat(7,1fr);gap:6px;padding:8px}
@media(max-width:640px){.week{grid-template-columns:1fr}}
.day{background:#292524;border:1px solid #44403c;border-radius:10px;padding:8px;min-height:110px}
.day.today{border-color:#d97706}
.dow{font-weight:700;color:#fbbf24;font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.meal{margin-top:6px;padding-top:6px;border-top:1px solid #3a3532}
.meal a{color:#e7e5e4;text-decoration:none;font-weight:600}
.mt{color:#a8a29e;font-size:11px;text-transform:uppercase}
.rate{margin-top:4px;display:flex;align-items:center;gap:2px;flex-wrap:wrap}
.rate select{background:#1c1917;color:#e7e5e4;border:1px solid #44403c;border-radius:5px;font-size:11px;padding:1px 2px}
.rate button{background:none;border:none;color:#78716c;cursor:pointer;font-size:15px;padding:0 1px;line-height:1}
.rate button:hover{color:#fbbf24}
.empty{color:#a8a29e;font-size:12px;padding:20px;text-align:center}
"""


def render_week(plan: list[dict], today: datetime.date, mon: datetime.date) -> str:
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
                who = "".join(f'<option>{r}</option>' for r in RATERS)
                rate = (
                    f'<form class="rate" method="post" action="rate">'
                    f'<input type="hidden" name="recipe_id" value="{e["recipe_id"]}">'
                    f'<select name="who">{who}</select>{stars}</form>'
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
            f"<!doctype html><body style='background:#1c1917;color:#a8a29e;font-family:sans-serif;padding:20px'>"
            f"Couldn't load the meal plan right now. Try again shortly.</body>",
            mimetype="text/html",
        )
    return Response(render_week(plan, today, mon), mimetype="text/html")


@app.post("/rate")
def rate():
    try:
        rid = int(request.form["recipe_id"])
        stars = int(request.form["rating"])
        who = request.form.get("who", RATERS[0] if RATERS else "family")
        post_rating(rid, stars, who)
    except Exception:
        log.exception("rating failed")
    return redirect("./", code=303)


if __name__ == "__main__":
    if not (USER and PASS):
        log.warning("TANDOOR_USER/TANDOOR_PASS not set -- meal plan will fail to load")
    serve(app, host="0.0.0.0", port=8080)
