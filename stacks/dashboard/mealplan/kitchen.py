"""Kitchen board (/meal/kitchen) and household page (/meal/home): shaping and
rendering. Pure functions over plain data: app.py gathers the data from its
caches (meal-plan snapshot, recipe cache, weather/calendar/HA/Vikunja feeds)
and hands it over, so everything here renders from fixtures in the tests.

No iframes. Every value from Tandoor, Vikunja, HA or the ICS feed goes through
`escape`. The only script is static/home.js (ticks, checklists, timers, wake
lock, idle return, night view, fragment refresh).
"""
from __future__ import annotations

import contextvars
import datetime
import re

from markupsafe import escape

import homecal
import vacuum

BASE = "/meal"                    # Traefik StripPrefix; every link is absolute
# The same pages also serve kitchen.kmkdp.com (paired kitchen devices, no
# Authelia) at the host root: /, /ingredients, /steps, /pantry. app.py sets
# this per request; the default (and every test that renders directly) is
# home.kmkdp.com under /meal.
_KITCHEN_HOST: contextvars.ContextVar[bool] = contextvars.ContextVar("kitchen_host", default=False)


def set_kitchen_host(on: bool) -> None:
    _KITCHEN_HOST.set(bool(on))


def on_kitchen_host() -> bool:
    return _KITCHEN_HOST.get()


def base() -> str:
    """Prefix for the sidecar's own routes (static, groceries, tasks)."""
    return "" if on_kitchen_host() else BASE


def kpath(sub: str = "") -> str:
    """URL of a kitchen view: kpath("/pantry") is /meal/kitchen/pantry on
    home.kmkdp.com and /pantry on kitchen.kmkdp.com (kpath() is / there)."""
    if on_kitchen_host():
        return sub if sub.startswith("/") else "/" + sub
    return f"{BASE}/kitchen{sub}"


NIGHT_START, NIGHT_END = 22, 6    # local hours (America/Chicago): 22:00-06:00

# ---- small formatting ----------------------------------------------------------
_FRACS = ((1 / 8, "⅛"), (0.25, "¼"), (1 / 3, "⅓"), (3 / 8, "⅜"), (0.5, "½"),
          (5 / 8, "⅝"), (2 / 3, "⅔"), (0.75, "¾"), (7 / 8, "⅞"))


def fmt_num(x: float) -> str:
    """1.125 -> "1⅛", 0.5 -> "½", 2.0 -> "2", 1.3 -> "1.3"."""
    if x <= 0:
        return ""
    w = int(x)
    rest = x - w
    if rest < 0.02:
        return str(w)
    if rest > 0.98:
        return str(w + 1)
    for v, glyph in _FRACS:
        if abs(rest - v) < 0.02:
            return f"{w}{glyph}" if w else glyph
    return f"{x:.1f}".rstrip("0").rstrip(".") if w < 10 else str(round(x))


def fmt_qty(amount: float, unit: str) -> str:
    n = fmt_num(amount or 0)
    if not n:
        return ""
    return f"{n} {unit}".strip()


def fmt_minutes(m: int) -> str:
    if m <= 0:
        return ""
    if m < 60:
        return f"{m} min"
    h, r = divmod(m, 60)
    return f"{h} h {r} min" if r else f"{h} h"


def fmt_clock(now: datetime.datetime) -> str:
    return now.strftime("%-I:%M %p")


def is_night(now: datetime.datetime) -> bool:
    """22:00-06:00 local: the kitchen board switches to the dim night view."""
    return now.hour >= NIGHT_START or now.hour < NIGHT_END


# ---- recipes -> dishes ----------------------------------------------------------
_TIMER_RE = re.compile(
    r"(\d+(?:\.\d+)?)(?:\s*(?:to|-|–|or)\s*(\d+(?:\.\d+)?))?\s*(hours?|hrs?|minutes?|mins?)\b", re.I)
_TITLE_RE = re.compile(r"^\s*([A-Z][^.:!?\n]{2,40}?)\s+[-–—:]\s+(.+)$", re.S)


def timers(text: str, step_time: int = 0) -> list[int]:
    """Minute values the step text names ("simmer 3 to 4 minutes" -> 4,
    "1 hour" -> 60), in order, distinct, at most 3; else Tandoor's step time."""
    out = []
    for m in _TIMER_RE.finditer(text or ""):
        v = float(m.group(2) or m.group(1))
        mins = round(v * 60) if m.group(3).lower().startswith("h") else round(v)
        if 0 < mins <= 24 * 60 and mins not in out:
            out.append(mins)
        if len(out) == 3:
            break
    if not out and step_time > 0:
        out.append(step_time)
    return out


def _plain(text: str) -> str:
    """Tandoor's markdown, reduced to plain text (emphasis markers dropped)."""
    t = re.sub(r"\*\*|__", "", text or "")
    t = re.sub(r"(?<!\w)\*(\S[^*]*?)\*(?!\w)", r"\1", t)
    t = re.sub(r"^\s*\*\s*", "", t)
    return t.strip()


def step_title(name: str, text: str, n: int) -> tuple[str, str]:
    """(title, body): the step name, else a short "Prepare Chicken - ..." lead-in
    split off the text, else "Step n"."""
    body = _plain(text)
    if name:
        return name, body
    m = _TITLE_RE.match(body)
    if m:
        rest = m.group(2).strip()
        return m.group(1).strip(), rest[:1].upper() + rest[1:]
    return f"Step {n}", body


def body_html(body: str) -> str:
    paras = [p.strip() for p in re.split(r"\n\s*\n", body or "") if p.strip()]
    return "".join(f"<p>{escape(p)}</p>".replace("\n", "<br>") for p in paras)


# Recipe titles keep their import clutter in Tandoor ("... Recipe", "(VIDEO)",
# "(Quick & Easy)"); the pages show a clean name and keep the original in the
# title attribute. Tandoor data is never changed.
_MARKETING_RE = re.compile(
    r"\s*[(\[][^()\[\]]*\b(?:video|recipe|easy|quick|best|simple|healthy|minutes?|"
    r"one[- ]pan|one[- ]pot|sheet[- ]pan|homemade|gluten[- ]free|keto|vegan|copycat|"
    r"restaurant[- ]style|authentic)\b[^()\[\]]*[)\]]\s*$", re.I)
_TRAIL_RECIPE_RE = re.compile(r"\s+recipe\s*[!.]?\s*$", re.I)
_MID_RECIPE_RE = re.compile(r"\s+recipe(?=\s+(?:in|with)\s)", re.I)


def clean_title(title: str) -> str:
    """"Filet Mignon Recipe in Mushroom Sauce (VIDEO)" -> "Filet Mignon in
    Mushroom Sauce": trailing marketing parentheses and a trailing " Recipe"
    go; a middle " Recipe" only before " in " / " with "."""
    t = (title or "").strip()
    prev = None
    while prev != t:
        prev = t
        t = _MARKETING_RE.sub("", t)
        t = _TRAIL_RECIPE_RE.sub("", t).rstrip(" -–—:,")
    t = _MID_RECIPE_RE.sub("", t).strip()
    return t or (title or "").strip()


def title_html(title: str) -> str:
    """The clean title, escaped; the original in a title attribute when it differs."""
    clean = clean_title(title)
    if clean == (title or "").strip():
        return str(escape(clean))
    return f"<span title='{escape(title)}'>{escape(clean)}</span>"


def titles_html(titles, sep: str = " and ") -> str:
    return sep.join(title_html(t) for t in titles)


def role_of(e: dict) -> str:
    """The planner role from the meal-plan note ("[gp:2026-W46] side")."""
    note = e.get("note") or ""
    return note.rsplit("] ", 1)[-1].strip() if "[gp:" in note else ""


def make_dish(entry: dict, rec: dict, onhand=None) -> dict:
    """One planned recipe, scaled to the plan's servings like Tandoor's
    add-to-shopping: {rid, title, role, servings, total_min, image, url,
    ingredients: [{key, qty, food, note, onhand}], steps: [...]}.
    `onhand(food)` marks pantry/stock foods (the Tandoor on-hand flag counts too)."""
    try:
        scale = float(entry.get("servings") or 0) / float(rec.get("servings") or 0)
    except (TypeError, ValueError, ZeroDivisionError):
        scale = 1.0
    scale = scale or 1.0
    ings = []
    for i, ing in enumerate(rec.get("ingredients") or []):
        ings.append({"key": str(ing.get("id") if ing.get("id") is not None else f"i{i}"),
                     "qty": fmt_qty((ing.get("amount") or 0) * scale, ing.get("unit") or ""),
                     "food": ing["food"], "note": ing.get("note") or "",
                     "onhand": bool(ing.get("onhand") or (onhand and onhand(ing["food"])))})
    steps = []
    for n, st in enumerate(rec.get("steps") or [], 1):
        title, body = step_title(st.get("name") or "", st.get("instruction") or "", n)
        if not body and not st.get("ingredients") and not st.get("file_id"):
            continue                                   # empty header step
        steps.append({"title": title, "body": body,
                      "timers": timers(body, int(st.get("time") or 0)),
                      "photo": st.get("file_id"),
                      "uses": [ings[i] for i in st.get("ingredients") or [] if i < len(ings)]})
    total = int(rec.get("working_time") or 0) + int(rec.get("waiting_time") or 0)
    return {"rid": entry.get("recipe_id"), "title": entry.get("title") or rec.get("name") or "",
            "role": role_of(entry), "servings": entry.get("servings") or rec.get("servings"),
            "total_min": total, "image": entry.get("image") or rec.get("image"),
            "url": entry.get("url"), "ingredients": ings, "steps": steps}


def dinner_entries(plan: list[dict], day: datetime.date) -> list[dict]:
    """The day's Dinner entries, the main first and sides after it."""
    rows = [e for e in plan if e.get("date") == day.isoformat() and e.get("meal_type") == "Dinner"
            and not e.get("prep_copy")]
    return sorted(rows, key=lambda e: role_of(e) == "side")


def lunch_prep(plan: list[dict], week_start: datetime.date) -> dict | None:
    """This plan week's prep lunches (cooked on the prep day, eaten Mon-Sat):
    {mains, sides, servings, prep_day}. Leftover lunches are not prep."""
    rows = [e for e in plan if e.get("date") == week_start.isoformat()
            and e.get("meal_type") == "Lunch" and role_of(e) in ("lunch", "side")]
    if not rows:
        return None
    mains = [e["title"] for e in rows if role_of(e) == "lunch"]
    sides = [e["title"] for e in rows if role_of(e) == "side"]
    servings = int(sum(float(e.get("servings") or 0) for e in rows if role_of(e) == "lunch"))
    return {"mains": mains, "sides": sides, "servings": servings, "prep_day": week_start}


def thaw_hint(rows: list[dict], tomorrow: datetime.date, today: datetime.date) -> str:
    """The Tomorrow card's line from the sidecar's thaw rows (app._thaw_rows):
    the open freezer-to-fridge moves for tomorrow's meals."""
    due = [r for r in rows if not r["done"] and r["date"] == tomorrow]
    if not due:
        return ""
    foods = ", ".join(f"{r['qty'] + ' ' if r['qty'] else ''}{r['food']}" for r in due)
    when = min(r["move_by"] for r in due)
    if when.date() < today:
        return f"Thaw now: {foods}"
    if when.date() == today:
        return f"Thaw tonight: {foods}"
    return f"Thaw by {when.strftime('%a %-I %p')}: {foods}"


# ---- weather -------------------------------------------------------------------
_ICON = {
    "sun": '<circle cx="12" cy="12" r="4.5"/><path d="M12 2v2.5M12 19.5V22M4.2 4.2l1.8 1.8M18 18l1.8 1.8M2 12h2.5M19.5 12H22M4.2 19.8 6 18M18 6l1.8-1.8"/>',
    "moon": '<path d="M20 15.5A8 8 0 1 1 8.5 4a6.5 6.5 0 0 0 11.5 11.5z"/>',
    "partly": '<path d="M8 3.5v1.5M3.5 8H5M4.8 4.8l1 1M12.2 4.8l-1 1"/><path d="M5.6 10.3A3.5 3.5 0 0 1 11.6 7"/><path d="M7 20h10a4 4 0 0 0 .4-8 5.5 5.5 0 0 0-10.6 1.6A3.2 3.2 0 0 0 7 20z"/>',
    "cloud": '<path d="M6.5 19h11a4.5 4.5 0 0 0 .5-9 6 6 0 0 0-11.6 1.8A3.6 3.6 0 0 0 6.5 19z"/>',
    "fog": '<path d="M4 9h16M3 13h18M5 17h14"/>',
    "rain": '<path d="M6.5 15h11a4.5 4.5 0 0 0 .5-9 6 6 0 0 0-11.6 1.8A3.6 3.6 0 0 0 6.5 15z"/><path d="M8 18l-1 3M12 18l-1 3M16 18l-1 3"/>',
    "snow": '<path d="M6.5 15h11a4.5 4.5 0 0 0 .5-9 6 6 0 0 0-11.6 1.8A3.6 3.6 0 0 0 6.5 15z"/><path d="M8 19h.01M12 21h.01M16 19h.01M10 22h.01M14 22h.01"/>',
    "storm": '<path d="M6.5 15h11a4.5 4.5 0 0 0 .5-9 6 6 0 0 0-11.6 1.8A3.6 3.6 0 0 0 6.5 15z"/><path d="M12.5 15 10 19h3l-2 3.5"/>',
}


def _icon_kind(code: int, night: bool) -> str:
    if code in (0, 1):
        return "moon" if night else "sun"
    if code == 2:
        return "partly" if not night else "cloud"
    if code == 3:
        return "cloud"
    if code in (45, 48):
        return "fog"
    if code in (71, 73, 75, 77, 85, 86):
        return "snow"
    if code in (95, 96, 99):
        return "storm"
    return "rain"


def wx_icon(code: int, night: bool, size: int = 52, label: str = "") -> str:
    aria = f' role="img" aria-label="{escape(label)}"' if label else ' aria-hidden="true"'
    return (f'<svg{aria} width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
            f'stroke="currentColor" stroke-width="1.6" stroke-linecap="round" '
            f'stroke-linejoin="round" class="wxi">{_ICON[_icon_kind(code, night)]}</svg>')


def wx_view(full: dict | None, now: datetime.datetime, wmo: dict, hours: int = 6) -> dict | None:
    """{temp, code, desc, hi, lo, night, hours: [{t, temp, pop}]} from the
    Open-Meteo forecast (local times, no tz in the strings)."""
    if not full:
        return None
    try:
        hr, d = full.get("hourly") or {}, full["daily"]
        top = now.replace(minute=0, second=0, microsecond=0, tzinfo=None)
        idx = [i for i, ts in enumerate(hr.get("time") or [])
               if datetime.datetime.fromisoformat(ts) >= top]
        today = now.date().isoformat()
        di = d["time"].index(today) if today in d["time"] else 0
        cur = idx[0] if idx else None
        code = int(hr["weather_code"][cur]) if cur is not None else int(d["weather_code"][di])
        temp = round(hr["temperature_2m"][cur]) if cur is not None else round(d["temperature_2m_max"][di])
        strip = []
        for i in idx[1:hours + 1]:
            t = datetime.datetime.fromisoformat(hr["time"][i])
            strip.append({"t": t.strftime("%-I %p"), "temp": round(hr["temperature_2m"][i]),
                          "pop": int(hr["precipitation_probability"][i] or 0)})
        return {"temp": temp, "code": code, "desc": wmo.get(code, ""),
                "hi": round(d["temperature_2m_max"][di]), "lo": round(d["temperature_2m_min"][di]),
                "night": is_night(now) or now.hour < 7 or now.hour >= 18, "hours": strip}
    except (KeyError, IndexError, TypeError, ValueError):
        return None


# ---- chores --------------------------------------------------------------------
def _due_label(due: datetime.date | None, today: datetime.date) -> tuple[str, bool]:
    if due is None:
        return "no date", False
    if due < today:
        return "late", True
    if due == today:
        return "today", False
    if due == today + datetime.timedelta(days=1):
        return "tomorrow", False
    if due < today + datetime.timedelta(days=7):
        return due.strftime("%a"), False
    return due.strftime("%b %-d"), False


def chore_counts(columns: list[dict], today: datetime.date) -> dict:
    due = late = total = 0
    for c in columns:
        total += len(c["soon"]) + len(c["later"])
        late += c["late"]
        due += sum(1 for d, _ in c["soon"] if d == today)      # late ones count apart
    return {"due": due, "late": late, "total": total}


def _chore_row(t: dict, due, today: datetime.date, project: str = "") -> str:
    lbl, late = _due_label(due, today)
    proj = f"<span class='proj'>{escape(project)}</span>" if project else ""
    return (f"<form class='chore{' late' if late else ''}' method='post' action='{base()}/tasks/complete'>"
            f"<input type='hidden' name='task_id' value='{int(t['id'])}'>"
            f"<button type='submit' class='tick-row' aria-label='Done: {escape(t.get('title') or '')}'>"
            "<span class='box' aria-hidden='true'></span>"
            f"<span class='ct'>{escape(t.get('title') or '')}{proj}</span>"
            f"<span class='due'>{escape(lbl)}</span></button></form>")


def chores_columns_html(columns: list[dict], today: datetime.date) -> str:
    if not columns:
        return "<div class='empty'>No open chores.</div>"
    cols = []
    for c in columns:
        rows = "".join(_chore_row(t, d, today) for d, t in c["soon"])
        if not c["soon"]:
            rows = "<div class='none'>Nothing due in 2 weeks</div>"
        if c["later"]:
            rows += (f"<details class='later'><summary>+{len(c['later'])} later</summary>"
                     + "".join(_chore_row(t, d, today) for d, t in c["later"]) + "</details>")
        n = len(c["soon"]) + len(c["later"])
        late = f" · <span class='late-n'>{c['late']} late</span>" if c["late"] else ""
        cols.append(f"<div class='col' data-cat='{escape(c['cat'])}'>"
                    f"<div class='col-h'><span class='col-name'>{escape(c['cat'])}</span>"
                    f"<span class='col-n'>{n}{late}</span></div>"
                    f"<div class='col-list'>{rows}</div></div>")
    return f"<div class='cols' style='--ncols:{len(cols)}'>{''.join(cols)}</div>"


def chores_head(counts: dict | None) -> str:
    if counts is None:
        return "<span class='muted'>Chores unavailable right now</span>"
    late = (f" · </span><span class='late-n'>{counts['late']} late</span>" if counts["late"]
            else "</span>")
    return f"<span class='muted'>{counts['due']} due today{late}"


def vacuum_html(v: dict | None, today: datetime.date) -> str:
    if v is None:
        return ""
    text, alerts = vacuum.line_parts(v, today)
    al = "".join(f" · <span class='alert'>{escape(a)}</span>" for a in alerts)
    if not text and not al:
        return ""
    return f"<div class='vac'><span class='vac-k'>Vacuum</span> {escape(text)}{al}</div>"


# ---- calendar ------------------------------------------------------------------
def cal_line(events: list[dict] | None, day: datetime.date) -> str:
    if events is None:
        return "<span class='muted'>Calendar unavailable</span>"
    hits = homecal.on_day(events, day)
    if not hits:
        return "<span class='muted'>Nothing scheduled</span>"
    return " · ".join(escape(homecal.label(e)) for e in hits)


# ---- page shell ----------------------------------------------------------------
def head(title: str, asset_ver: str, kiosk: bool) -> str:
    app_meta = ""
    if kiosk:
        app_meta = (f"<link rel='manifest' href='{base()}/manifest.webmanifest' crossorigin='use-credentials'>"
                    "<meta name='apple-mobile-web-app-capable' content='yes'>"
                    "<meta name='mobile-web-app-capable' content='yes'>"
                    "<meta name='apple-mobile-web-app-status-bar-style' content='black-translucent'>"
                    "<meta name='apple-mobile-web-app-title' content='Kitchen'>")
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>"
            f"<title>{escape(title)}</title>"
            "<meta name='theme-color' content='#100D0B'>"
            f"<link rel='apple-touch-icon' href='{base()}/static/icon-180.png'>"
            f"<link rel='icon' type='image/png' sizes='192x192' href='{base()}/static/icon-192.png'>"
            f"{app_meta}"
            f"<link rel='stylesheet' href='{base()}/static/home.css?v={asset_ver}'>"
            f"<script defer src='{base()}/static/home.js?v={asset_ver}'></script></head>")


def _back(href: str) -> str:
    return (f"<a class='btn ghost back' href='{escape(href)}'>"
            "<svg aria-hidden='true' width='22' height='22' viewBox='0 0 24 24' fill='none' "
            "stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>"
            "<path d='M15 18l-6-6 6-6'/></svg>Back</a>")


# ---- kitchen board ---------------------------------------------------------------
def _dinner_card(dishes: list[dict], big: bool = True) -> str:
    if not dishes:
        return ("<article class='card dinner none'><div class='dinner-body'>"
                "<div class='eyebrow rust'>Dinner tonight</div>"
                "<h1 class='dish'>No dinner planned</h1>"
                "<div class='muted'>Plan one in Recipes and it shows here.</div></div></article>")
    main, sides = dishes[0], dishes[1:]
    n_ing = sum(len(d["ingredients"]) for d in dishes)
    n_on = sum(1 for d in dishes for i in d["ingredients"] if i["onhand"])
    meta = [fmt_minutes(main["total_min"])]
    if main["servings"]:
        meta.append(f"serves {fmt_num(float(main['servings']))}")
    meta.append(f"{n_ing} ingredients, {n_on} on hand" if n_on else f"{n_ing} ingredients")
    photo = (f"<img src='{escape(main['image'])}' alt=''>" if main.get("image")
             else "<div class='noimg'></div>")
    side = (f"<div class='side'>with {titles_html(d['title'] for d in sides)}</div>"
            if sides else "")
    return (f"<article class='card dinner'><div class='photo'>{photo}</div>"
            "<div class='dinner-body'><div class='eyebrow rust'>Dinner tonight</div>"
            f"<h1 class='dish'>{title_html(main['title'])}</h1>{side}"
            f"<div class='muted meta'>{escape(' · '.join(m for m in meta if m))}</div>"
            "<div class='actions'>"
            f"<a class='btn primary' href='{kpath('/ingredients')}'>Pull ingredients</a>"
            f"<a class='btn ghost' href='{kpath('/steps')}'>Steps</a></div></div></article>")


def _lunch_card(lunch: dict | None, today: datetime.date) -> str:
    if not lunch:
        return ("<article class='card lunch'><div class='eyebrow tan'>Lunch · meal prep</div>"
                "<div class='muted'>No lunch prep this week</div></article>")
    rows = "".join(f"<div class='ltitle'>{title_html(t)}</div>" for t in lunch["mains"])
    sides = (f"<div class='muted'>with {titles_html(lunch['sides'], ', ')}</div>"
             if lunch["sides"] else "")
    when = ("Prep today" if lunch["prep_day"] == today
            else f"Prepped {lunch['prep_day']:%A}")
    serv = f" · {lunch['servings']} servings" if lunch["servings"] else ""
    return (f"<article class='card lunch'><div class='eyebrow tan'>Lunch · meal prep</div>"
            f"{rows}{sides}<div class='muted small'>{escape(when + serv)}</div></article>")


def _tomorrow_card(titles: list[str], thaw: str) -> str:
    main = title_html(titles[0]) if titles else "Nothing planned"
    side = f"<div class='muted'>with {titles_html(titles[1:])}</div>" if len(titles) > 1 else ""
    hint = f"<div class='thaw'>{escape(thaw)}</div>" if thaw else ""
    return (f"<article class='card tomorrow'><div class='eyebrow'>Tomorrow</div>"
            f"<div class='ltitle'>{main}</div>{side}{hint}</article>")


def board_fragment(d: dict) -> str:
    """The kitchen board's swappable body (the 2-min refresh replaces it)."""
    now, today, tomorrow = d["now"], d["today"], d["today"] + datetime.timedelta(days=1)
    wx = d.get("wx")
    if wx:
        hours = "".join(f"<div class='hr'><div class='muted small'>{escape(h['t'])}</div>"
                        f"<div class='hv'>{h['temp']}°</div><div class='pop'>{h['pop']}%</div></div>"
                        for h in wx["hours"])
        wx_html = (f"<div class='card wx'><div class='wx-now'>{wx_icon(wx['code'], wx['night'], 52, wx['desc'])}"
                   f"<div><div class='big'>{wx['temp']}°</div>"
                   f"<div class='muted'>{escape(wx['desc'])} · H {wx['hi']}° L {wx['lo']}°</div></div></div>"
                   f"<div class='hours'>{hours}</div></div>")
    else:
        wx_html = "<div class='card wx'><div class='muted'>Weather unavailable right now</div></div>"
    cal = d.get("cal")
    cal_html = ("<div class='card cal'><div class='label'>Calendar</div>"
                f"<div class='calrow'><span class='calday'>Today</span><span class='calev'>{cal_line(cal, today)}</span></div>"
                f"<div class='calrow'><span class='calday'>{tomorrow:%a}</span><span class='calev'>{cal_line(cal, tomorrow)}</span></div></div>")
    clock = (f"<div class='card clock'><div class='big' data-clock>{fmt_clock(now)}</div>"
             f"<div class='muted date'>{now:%A, %B %-d}</div></div>")
    cols = d.get("chores")
    counts = chore_counts(cols, today) if cols is not None else None
    chores = ("<section class='card chores'><div class='chores-h'><h2>Chores</h2>"
              f"{chores_head(counts)}{vacuum_html(d.get('vacuum'), today)}</div>"
              + (chores_columns_html(cols, today) if cols is not None
                 else "<div class='empty'>Couldn't reach the task list right now.</div>")
              + "</section>")
    night_ev = homecal.first_event(cal, tomorrow) if cal else ""
    return (f"<div class='board-in' data-night-event='{escape(night_ev)}' data-date='{today.isoformat()}'>"
            f"<header class='hdr'>{clock}{wx_html}{cal_html}</header>"
            f"<section class='meals'>{_dinner_card(d.get('dinner') or [])}"
            f"<div class='meal-side'>{_lunch_card(d.get('lunch'), today)}"
            f"{_tomorrow_card(d.get('tomorrow_titles') or [], d.get('thaw') or '')}"
            f"<a class='btn ghost pantry-btn' href='{kpath('/pantry')}'>Pantry check</a></div></section>"
            f"{chores}</div>")


def kitchen_page(d: dict, asset_ver: str) -> str:
    night = is_night(d["now"])
    night_ev = homecal.first_event(d.get("cal") or [], d["today"] + datetime.timedelta(days=1))
    ev = (f"<div class='night-ev'><span class='muted'>Tomorrow</span> {escape(night_ev)}</div>"
          if night_ev else "")
    return (head("Kitchen", asset_ver, kiosk=True)
            + f"<body data-page='kitchen' class='kiosk{' is-night' if night else ''}'>"
            f"<div class='shell' id='board' data-fragment='{kpath('?fragment=1')}'>{board_fragment(d)}</div>"
            f"<div class='night' id='night' role='button' tabindex='0' aria-label='Show the board'>"
            f"<div class='night-clock' data-clock>{fmt_clock(d['now'])}</div>{ev}</div>"
            "</body></html>")


# ---- ingredients view ------------------------------------------------------------
def _ing_row(i: dict, scope: str) -> str:
    note = f"<span class='note'>{escape(i['note'])}</span>" if i["note"] else ""
    pantry = " <span class='pill'>on hand</span>" if i["onhand"] else ""
    return (f"<button type='button' class='ing' data-check='{escape(scope)}:{escape(i['key'])}' aria-pressed='false'>"
            "<span class='box' aria-hidden='true'></span>"
            f"<span class='qty'>{escape(i['qty'])}</span>"
            f"<span class='food'><span class='fname'>{escape(i['food'])}</span>{pantry}{note}</span></button>")


def ingredient_groups(dishes: list[dict], today: datetime.date) -> str:
    """One section per dish (main first, then sides), checklist rows keyed for
    the browser's tick store (date + recipe + ingredient)."""
    out = []
    for n, dsh in enumerate(dishes):
        scope = f"{today.isoformat()}:{dsh['rid']}"
        rows = "".join(_ing_row(i, scope) for i in dsh["ingredients"]) or "<div class='muted'>No ingredients listed</div>"
        wide = " wide" if n == 0 and len(dsh["ingredients"]) > 8 else ""
        out.append(f"<section class='card dishlist{wide}'><h2>{title_html(dsh['title'])}</h2>"
                   f"<div class='ings'>{rows}</div></section>")
    return "".join(out)


def ingredients_page(dishes: list[dict], today: datetime.date, asset_ver: str, back: str) -> str:
    total = sum(len(d["ingredients"]) for d in dishes)
    serv = f" · serves {fmt_num(float(dishes[0]['servings']))}" if dishes and dishes[0]["servings"] else ""
    body = ingredient_groups(dishes, today) if dishes else "<div class='card empty'>No dinner planned tonight.</div>"
    return (head("Pull ingredients", asset_ver, kiosk=True)
            + f"<body data-page='ingredients' data-home='{escape(back)}' class='kiosk sub'><div class='shell'>"
            f"<header class='subhdr'>{_back(back)}<div class='subtitle'>"
            f"<div class='eyebrow rust'>Dinner tonight{escape(serv)}</div><h1>Pull ingredients</h1></div>"
            f"<div class='tally'><div class='big'><span data-pulled>0</span> / {total}</div>"
            "<div class='muted small'>pulled</div></div></header>"
            f"<div class='dishes n{min(len(dishes), 3)}'>{body}</div></div></body></html>")


# ---- steps view ------------------------------------------------------------------
def _step_html(s: dict, n: int, total: int, dish: dict) -> str:
    tm = "".join(
        f"<button type='button' class='btn timer' data-timer='{m}'>"
        "<svg aria-hidden='true' width='22' height='22' viewBox='0 0 24 24' fill='none' stroke='currentColor' "
        "stroke-width='2' stroke-linecap='round'><circle cx='12' cy='13' r='8'/><path d='M12 9v4l2 2M9 2h6'/></svg>"
        f"Start {escape(fmt_minutes(m))} timer</button>" for m in s["timers"])
    photo = (f"<img class='step-photo' src='{kpath('/step-photo')}/{int(s['photo'])}' alt='' loading='lazy'>"
             if s.get("photo") else "")
    uses = "".join(f"<div class='use'><span class='qty'>{escape(u['qty'])}</span><span>{escape(u['food'])}</span></div>"
                   for u in s["uses"])
    aside = ""
    if photo or uses:
        aside = (f"<aside class='card step-aside'>{photo}"
                 + (f"<div class='label'>This step uses</div>{uses}" if uses else "")
                 + "</aside>")
    return (f"<section class='step{' has-aside' if aside else ''}' data-step='{n}' {'hidden' if n else ''}>"
            f"<article class='card step-main'><div class='eyebrow tan'>Step {n + 1} of {total} · {escape(s['title'])}</div>"
            f"<div class='step-body'>{body_html(s['body'])}</div>"
            f"<div class='timers'>{tm}</div></article>{aside}</section>")


def steps_page(dish: dict | None, dishes: list[dict], asset_ver: str, back: str) -> str:
    if not dish or not dish["steps"]:
        msg = "No dinner planned tonight." if not dish else "This recipe has no steps in Tandoor."
        return (head("Steps", asset_ver, kiosk=True)
                + f"<body data-page='steps' data-home='{escape(back)}' class='kiosk sub'><div class='shell'>"
                f"<header class='subhdr'>{_back(back)}</header><div class='card empty'>{escape(msg)}</div></div></body></html>")
    steps = dish["steps"]
    tabs = "".join(f"<button type='button' class='tab' data-go='{n}' aria-selected='{'true' if n == 0 else 'false'}'>"
                   f"<span class='tn'>{n + 1}</span> {escape(s['title'])}</button>" for n, s in enumerate(steps))
    others = ""
    if len(dishes) > 1:
        q = "&from=home" if back.endswith("/home") else ""
        others = "".join(
            f"<a class='btn ghost dishsw{' on' if d['rid'] == dish['rid'] else ''}' "
            f"href='{kpath('/steps')}?r={int(d['rid'])}{q}'>{title_html(d['title'])}</a>"
            for d in dishes if d.get("rid"))
    nexts = [f"Next: {s['title']}" for s in steps[1:]] + ["Done cooking"]
    serv = f" · serves {fmt_num(float(dish['servings']))}" if dish["servings"] else ""
    ing_href = f"{kpath('/ingredients')}" + ("?from=home" if back.endswith("/home") else "")
    return (head("Steps", asset_ver, kiosk=True)
            + f"<body data-page='steps' data-home='{escape(back)}' class='kiosk sub'><div class='shell steps-shell'>"
            f"<header class='subhdr'>{_back(back)}<div class='subtitle'>"
            f"<div class='eyebrow rust'>{title_html(dish['title'])}{escape(serv)}</div>"
            f"<div class='dishsws'>{others}</div></div>"
            f"<div id='timerbar' class='timerbar' aria-live='polite'></div>"
            f"<a class='btn ghost' href='{escape(ing_href)}'>All ingredients</a></header>"
            f"<nav class='tabs' aria-label='Steps'>{tabs}</nav>"
            + "".join(_step_html(s, n, len(steps), dish) for n, s in enumerate(steps))
            + "<footer class='stepnav'><button type='button' class='btn ghost prev' data-prev>Previous</button>"
            f"<button type='button' class='btn primary next' data-next data-labels='{escape('|'.join(nexts))}'>{escape(nexts[0])}</button>"
            "</footer></div></body></html>")


# ---- pantry check view -------------------------------------------------------------
# The kitchen tablet's two grocery writes both post to the groceries page's own
# routes (no second write path): "Running low" -> /groceries/reorder (pantry and
# stock foods only, no duplicate while one is open), the add box -> /groceries/add
# (the Manual list). `next` brings a plain form POST back here; home.js sends
# ajax=1 instead. "Checked" ticks live in the browser for the evening only.
ADD_MAX = 80


def add_box(next_key: str, compact: bool = False, manual: list[str] | None = None) -> str:
    """Text entry for a manual grocery item, "Added tonight" (filled by home.js)
    and, unless compact, the Manual list's current items."""
    cur = ""
    if manual is not None and not compact:
        items = "".join(f"<li>{escape(m)}</li>" for m in manual)
        cur = (f"<div class='label'>On the manual list ({len(manual)})</div>"
               + (f"<ul class='addlist cur'>{items}</ul>" if items
                  else "<div class='muted small'>Nothing yet.</div>"))
    return (f"<section class='card addbox{' compact' if compact else ''}'>"
            f"<form class='additem' method='post' action='{base()}/groceries/add'>"
            f"<input type='hidden' name='next' value='{escape(next_key)}'>"
            "<label class='sr' for='add-item'>Add to the grocery list</label>"
            f"<input id='add-item' name='item' type='text' required maxlength='{ADD_MAX}' "
            "placeholder='Add a grocery item…' enterkeyhint='done' autocapitalize='sentences' "
            "autocomplete='off' autocorrect='on' spellcheck='true'>"
            "<button class='btn primary' type='submit'>Add to list</button></form>"
            "<div class='added' hidden><div class='label'>Added tonight</div><ul class='addlist' data-added></ul></div>"
            f"{cur}</section>")


def _pantry_row(f: dict, today: datetime.date, next_key: str) -> str:
    food = f["food"]
    src = (f"<span class='src'>for {titles_html(f['recipes'], ', ')}</span>" if f["recipes"] else "")
    if f["on_list"]:
        act = "<span class='onlist'>On the list</span>"
    else:
        act = (f"<form class='reorder' method='post' action='{base()}/groceries/reorder'>"
               f"<input type='hidden' name='food' value='{escape(food)}'>"
               f"<input type='hidden' name='next' value='{escape(next_key)}'>"
               "<button class='btn low' type='submit'>Running low</button></form>")
    key = f"{today.isoformat()}:{food.lower()}"
    return (f"<div class='prow'><button type='button' class='pcheck' data-pcheck='{escape(key)}' "
            f"aria-pressed='false' aria-label='Checked: {escape(food)}'><span class='box' aria-hidden='true'></span>"
            f"<span class='pname'><span class='fname'>{escape(food)}</span>{src}</span></button>{act}</div>")


def _pantry_groups(groups: list[dict], today: datetime.date, next_key: str) -> str:
    return "".join(f"<div class='pgroup'><div class='label'>{escape(g['label'])}</div>"
                   f"<div class='prows'>{''.join(_pantry_row(f, today, next_key) for f in g['foods'])}</div></div>"
                   for g in groups)


def pantry_page(d: dict, asset_ver: str, back: str) -> str:
    today = d["today"]
    next_key = "pantry-home" if back.endswith("/home") else "pantry"
    used = d.get("used") or []
    staples = d.get("staples") or []
    n_used = sum(len(g["foods"]) for g in used)
    n_st = sum(len(g["foods"]) for g in staples)
    if not d.get("plan_ok"):
        used_html = "<div class='muted'>Couldn't load this week's meal plan right now.</div>"
    elif not used:
        used_html = "<div class='muted'>No pantry or stock foods in this week's recipes.</div>"
    else:
        used_html = _pantry_groups(used, today, next_key)
    mark = "<button type='button' class='btn ghost markall' data-markall='{}'>Mark all checked</button>"
    staples_html = (_pantry_groups(staples, today, next_key)
                    or "<div class='muted'>No other pantry staples configured.</div>")
    return (head("Pantry check", asset_ver, kiosk=True)
            + f"<body data-page='pantry' data-home='{escape(back)}' class='kiosk sub'><div class='shell'>"
            f"<header class='subhdr'>{_back(back)}<div class='subtitle'>"
            "<div class='eyebrow rust'>This plan week</div><h1>Pantry check</h1></div>"
            "<div class='tally'><div class='big'><span data-pchecked>0</span> / "
            f"<span data-ptotal>{n_used}</span></div><div class='muted small'>checked</div></div></header>"
            "<div class='pantry-scroll'>"
            f"{add_box(next_key, manual=d.get('manual'))}"
            f"<section class='card psec' data-sec='used'><div class='psec-h'><h2>Used this week</h2>"
            f"<span class='muted'>{n_used}</span>{mark.format('used') if n_used else ''}</div>{used_html}</section>"
            f"<details class='card psec staples' data-sec='staples'><summary><h2>All pantry staples</h2>"
            f"<span class='muted'>{n_st}</span><span class='chev' aria-hidden='true'></span></summary>"
            f"<div class='psec-h'>{mark.format('staples') if n_st else ''}</div>"
            f"{staples_html}</details></div></div></body></html>")


# ---- household page ----------------------------------------------------------------
APPS = [  # household only; hrefs from guides/shared-nav.js
    ("Recipes", "https://recipes.kmkdp.com/"),
    ("Groceries", f"{BASE}/groceries"),
    ("Tasks", "https://tasks.kmkdp.com/"),
    ("Watch", "https://jellyfin.kmkdp.com/"),
    ("Listen & read", "https://audiobookshelf.kmkdp.com/"),
    ("Photos", "https://immich.kmkdp.com/"),
    ("Request TV", "https://seerr.kmkdp.com/"),
    ("Request books", "https://libreseerr.kmkdp.com/"),
    ("Workouts", "https://wger.kmkdp.com/"),
    ("Budget", "https://budget.kmkdp.com/"),
    ("Home control", "https://homeassistant.kmkdp.com/home-controls"),
    ("Guides", "/guides/"),
]


HH_CHORE_CAP = 8      # the phone list: late first, then due today; the rest behind "All chores"


def household_fragment(d: dict, all_chores: bool = False) -> str:
    today = d["today"]
    dishes = d.get("dinner") or []
    # dinner + its ingredients
    if dishes:
        main = dishes[0]
        photo = (f"<img src='{escape(main['image'])}' alt=''>" if main.get("image") else "")
        sides = f"with {titles_html(x['title'] for x in dishes[1:])}" if len(dishes) > 1 else ""
        meta = " · ".join(m for m in (fmt_minutes(main["total_min"]),
                                      f"serves {fmt_num(float(main['servings']))}" if main["servings"] else "") if m)
        n_ing = sum(len(x["ingredients"]) for x in dishes)
        dinner = (f"<article class='card hh-dinner'>{'<div class=hh-photo>' + photo + '</div>' if photo else ''}"
                  "<div class='pad'><div class='eyebrow rust'>Dinner tonight</div>"
                  f"<h2 class='dish'>{title_html(main['title'])}</h2>"
                  f"<div class='muted'>{sides}{' · ' if sides and meta else ''}{escape(meta)}</div>"
                  f"<details class='hh-ings'><summary class='btn primary'>Ingredients ({n_ing})</summary>"
                  f"{ingredient_groups(dishes, today)}</details>"
                  f"<a class='btn ghost' href='{kpath('/steps')}?from=home'>Steps</a>"
                  f"<a class='btn ghost' href='{kpath('/pantry')}?from=home'>Pantry check</a></div></article>")
    else:
        dinner = ("<article class='card hh-dinner'><div class='pad'><div class='eyebrow rust'>Dinner tonight</div>"
                  "<h2 class='dish'>No dinner planned</h2>"
                  f"<a class='btn ghost' href='{kpath('/pantry')}?from=home'>Pantry check</a></div></article>")
    lunch = _lunch_card(d.get("lunch"), today)
    tomorrow = _tomorrow_card(d.get("tomorrow_titles") or [], d.get("thaw") or "")
    # chores: due + late first, everything behind a fold
    cols = d.get("chores")
    if cols is None:
        chores = ("<section class='card hh-chores'><h2>Chores</h2>"
                  "<div class='muted'>Couldn't reach the task list right now.</div></section>")
    else:
        counts = chore_counts(cols, today)
        due = sorted(((dt, t, c["cat"]) for c in cols for dt, t in c["soon"] if dt and dt <= today),
                     key=lambda x: (x[0], (x[1].get("title") or "").lower()))
        shown = due[:HH_CHORE_CAP]
        rows = "".join(_chore_row(t, dt, today, cat) for dt, t, cat in shown) or "<div class='muted'>Nothing due today.</div>"
        more = (f"<div class='muted small hh-more'>{len(due) - len(shown)} more late or due today</div>"
                if len(due) > len(shown) else "")
        if all_chores:
            board = (f"<div class='hh-all' id='all-chores'>{chores_columns_html(cols, today)}</div>"
                     f"<a class='hh-link' href='{base()}/home'>Hide all chores</a>")
        else:
            board = (f"<a class='hh-link' href='{base()}/home?chores=all#all-chores'>"
                     f"All chores ({counts['total']})</a>")
        chores = (f"<section class='card hh-chores'><div class='chores-h'><h2>Chores</h2>{chores_head(counts)}</div>"
                  f"<div class='hh-due'>{rows}</div>{more}{board}</section>")
    # calendar: this week
    cal = d.get("cal")
    if cal is None:
        week = "<div class='muted'>Calendar unavailable right now.</div>"
    else:
        rows = []
        for i in range(7):
            day = today + datetime.timedelta(days=i)
            for k, e in enumerate(homecal.on_day(cal, day)):
                lbl = ("Today" if i == 0 else day.strftime("%a")) if k == 0 else ""
                rows.append(f"<div class='calrow'><span class='calday'>{lbl}</span>"
                            f"<span class='calev'>{escape(homecal.label(e))}</span></div>")
        week = "".join(rows) or "<div class='muted'>Nothing scheduled this week.</div>"
    calendar = f"<section class='card hh-cal'><h2>This week</h2>{week}</section>"
    apps = "".join(
        f"<a class='app' href='{escape(h)}'{'' if h.startswith('/') else ' target=_blank rel=noopener'}>{escape(n)}</a>"
        for n, h in APPS)
    return (f"<div class='hh-grid'><div class='hh-a'>{dinner}{lunch}{tomorrow}</div>"
            f"<div class='hh-b'>{chores}{calendar}</div>"
            f"<section class='hh-apps'><h2>Apps</h2><div class='apps'>{apps}</div></section></div>")


def household_page(d: dict, asset_ver: str, all_chores: bool = False) -> str:
    now = d["now"]
    wx = d.get("wx")
    wxs = (f"{wx_icon(wx['code'], wx['night'], 28, wx['desc'])}<span><b>{wx['temp']}°</b> {escape(wx['desc'])}"
           f"<span class='muted'> · H {wx['hi']}° L {wx['lo']}°</span></span>") if wx else ""
    return (head("Mack House", asset_ver, kiosk=False)
            + "<body data-page='home' class='household'>"
            "<header class='hh-top'><div class='brand'>Mack House</div>"
            f"<div class='hh-when'><span>{now:%a, %b %-d}</span><span class='hh-wx'>{wxs}</span></div></header>"
            f"<div class='hh-add'>{add_box('home', compact=True)}</div>"
            f"<main class='hh-main' id='board' data-fragment='{base()}/home?fragment=1{'&amp;chores=all' if all_chores else ''}'>"
            f"{household_fragment(d, all_chores)}</main>"
            "</body></html>")
