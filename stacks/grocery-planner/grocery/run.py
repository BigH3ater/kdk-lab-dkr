"""Weekly grocery run — the one-shot Komodo job.

Weekly (default): generate next week's meal plan under the owner rules, write the
entries (idempotent per week key), build the two-store shopping list from the
plan minus on-hand items, create ONE combined Vikunja approval task, and — in the
poll pass — drop the non-approved store's entries for that week. Logs JSON lines to stdout and
pushes them to Loki (job=grocery-planner).

  python -m grocery.run --mode weekly            # build pass (Tue 18:03): plan + list + task
  python -m grocery.run --mode weekly --poll     # poll pass (Thu 07:05/12:05/17:05): apply decision

POLL STRATEGY (justified): the approval is asynchronous and durable (the Vikunja
task IS the record), so the build pass exits immediately after creating the task
and SEPARATE scheduled poll passes (Thu 07:05, 12:05, 17:05) read and apply
the outcome (idempotent). No long-lived container
waits. A bounded in-run poll (config approvals.poll_timeout_min) is available for
manual use but the scheduled two-pass design is the default.

No secrets are printed. Credentials come from env (op-injected .env).
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
import re
import sys

import yaml

from . import freezer as F
from . import generator as G
from . import shoplist as S

HERE = os.path.dirname(os.path.abspath(__file__))
STACK = os.path.dirname(HERE)
log = logging.getLogger("grocery.run")


# Direct Loki push (the cloudflare-watch pattern): this is a one-shot that exits
# in seconds, so the host Alloy's discovery.docker (running containers only)
# never scrapes it. Every jlog line is also pushed to Loki at the end of the run
# as {job="grocery-planner", container="grocery-planner<GROCERY_SUFFIX>",
# host=NODE_NAME}. Empty LOKI_URL disables the push; a push failure is logged
# to stderr and never changes the exit code.
_LINES: list[str] = []


def jlog(**kw):
    """One structured JSON line to stdout (and, at exit, to Loki)."""
    kw.setdefault("job", "grocery-planner")
    text = json.dumps(kw, default=str)
    print(text, flush=True)
    _LINES.append(text)


def loki_stream_labels(env=None):
    env = os.environ if env is None else env
    return {"job": "grocery-planner",
            "container": "grocery-planner" + (env.get("GROCERY_SUFFIX") or "").strip(),
            "host": (env.get("NODE_NAME") or "kdk-dkr-01").strip()}


def loki_body(lines, labels, base_ns):
    """Loki push body: one stream, one value per line, strictly increasing ns."""
    return {"streams": [{"stream": labels,
                         "values": [[str(base_ns + i), ln] for i, ln in enumerate(lines)]}]}


def loki_push(url, lines, labels, tries=3, opener=None):
    """POST the lines to <url>/loki/api/v1/push. Raises after `tries` failures."""
    import time
    import urllib.request
    opener = opener or urllib.request.urlopen
    req = urllib.request.Request(
        url.rstrip("/") + "/loki/api/v1/push",
        data=json.dumps(loki_body(lines, labels, time.time_ns())).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    for attempt in range(tries):
        try:
            opener(req, timeout=10).read()
            return
        except Exception:  # noqa: BLE001
            if attempt == tries - 1:
                raise
            time.sleep(3)


def flush_to_loki(env=None, opener=None):
    """Push every jlog line of this run. Never raises; returns lines pushed."""
    env = os.environ if env is None else env
    url = (env.get("LOKI_URL") or "").strip()
    if not url or not _LINES:
        return 0
    labels = loki_stream_labels(env)
    try:
        loki_push(url, list(_LINES), labels, opener=opener)
    except Exception as e:  # noqa: BLE001
        print(f'level=warn msg="loki push failed" container={labels["container"]} '
              f'lines={len(_LINES)} err="{type(e).__name__}: {e}"', file=sys.stderr, flush=True)
        return 0
    n = len(_LINES)
    _LINES.clear()
    return n


# ---- pure helpers (unit tested) -------------------------------------------
def next_week_window(today, start_weekday=6):
    """Return (start, end) for the NEXT plan week. start is the first upcoming
    `start_weekday` (Sun=6) strictly after today; end = start + 6 days."""
    days = (start_weekday - today.weekday()) % 7
    days = 7 if days == 0 else days          # always the NEXT one, not today
    start = today + datetime.timedelta(days=days)
    return start, start + datetime.timedelta(days=6)


def _ingredient_count(recipe):
    n = 0
    for step in recipe.get("steps") or []:
        n += len(step.get("ingredients") or [])
    return n


def _is_optional(ing):
    text = " ".join(str(ing.get(k) or "") for k in ("original_text", "note")).lower()
    return "optional" in text


def _food_names(recipe, skip_optional=True):
    out = []
    for step in recipe.get("steps") or []:
        for ing in step.get("ingredients") or []:
            name = ((ing.get("food") or {}).get("name") or "").strip().lower()
            if not name or ing.get("is_header") or (skip_optional and _is_optional(ing)):
                continue
            out.append(name)
    return out


def _word_re(words):
    """One regex matching any of `words` as whole words, plural s/es allowed:
    "carrot" hits "baby carrots", "corn" does not hit "cornstarch"."""
    alts = "|".join(re.escape(w.lower()) for w in sorted(words, key=len, reverse=True))
    return re.compile(rf"\b(?:{alts})(?:e?s)?\b") if alts else None


def veg_foods(recipe, vegetables, never_buy=None):
    """The recipe's vegetable foods (config sides.vegetables, whole-word match on
    the Tandoor food name; optional garnish lines ignored). Tandoor holds no
    vegetable flag: its food categories here are store aisles ("NewPi/weekly:
    Produce" also holds garlic, herbs and lemons), so the list lives in config."""
    rx = _word_re(vegetables or [])
    if not rx:
        return []
    # A never_buy vegetable is never bought, so it does not make the main a
    # vegetable dish (Fall Chicken Sheet Pan's brussels sprouts).
    return sorted({n for n in _food_names(recipe) if rx.search(n)
                   and not S.is_never_buy(n, never_buy)})


# "rice" as the grain, not rice vinegar / rice wine / cauliflower rice etc.
_RICE = re.compile(r"(?<!cauliflower )\brice\b(?! vinegar| wine| noodle| paper| cake)")
_OPTIONAL = re.compile(r"\boptional\b|\bif you like\b|\bif desired\b|\bif using\b"
                       r"|\bor rice\b|\brice,? or\b")   # "potatoes, noodles, or rice" = a choice


def wants_rice(recipe, rice_cfg):
    """True when the dish is served over rice the household makes separately
    (the microwave packet): the `serve-with-rice` keyword (explicit), else
    detection -- a serving-rice ingredient (`serving_foods`, e.g. "cooked rice"),
    or no rice ingredient at all while a step sentence names rice ("serve over
    hot rice", "Cook rice as per package instructions"). Sentences that make it
    optional ("over rice, or ... if you like") do not count, and a recipe that
    lists its own raw rice ("white rice") keeps it, so gets no side. The
    `no-rice-side` keyword switches detection off for one recipe."""
    rc = rice_cfg or {}
    if not rc.get("enabled", False):
        return False
    names = {(k.get("name") or "").lower() for k in (recipe.get("keywords") or [])}
    if rc.get("keyword", "serve-with-rice") in names:
        return True
    if rc.get("opt_out_keyword", "no-rice-side") in names or not rc.get("detect", True):
        return False
    foods = set(_food_names(recipe, skip_optional=False))
    serving = {f.lower() for f in rc.get("serving_foods") or ["cooked rice"]}
    if serving & foods:
        return True
    if any(_RICE.search(f) for f in foods):
        return False                       # the recipe brings (and cooks) its own rice
    for st in recipe.get("steps") or []:
        for sentence in re.split(r"[.!?]", (st.get("instruction") or "").lower()):
            if _RICE.search(sentence) and not _OPTIONAL.search(sentence):
                return True
    return False


def build_pool(recipes, season, cfg=None):
    """Turn Tandoor recipe detail dicts into generator pool items for a season.
    Variant comes from keywords (salmon/tuna, greek/asian). kind from keywords.
    With `cfg`, each item also carries `veg_foods` (no veg side when non-empty)
    and `rice` (gets the rice side), and `lunch_friendly` (lunches.leftovers
    keyword or name list). A recipe whose title holds a never_buy food (Roasted
    Cabbage Steaks) is left out."""
    cfg = cfg or {}
    never = cfg.get("never_buy") or []
    lo_cfg = (cfg.get("lunches") or {}).get("leftovers") or {}
    lo_kw = str(lo_cfg.get("keyword", "lunch-friendly")).lower()
    lo_names = {str(n).lower() for n in lo_cfg.get("recipes") or []}
    fmap = (cfg.get("freezer") or {}).get("map") or {}
    vegetables = (cfg.get("sides") or {}).get("vegetables") or []
    rice_cfg = cfg.get("rice") or {}
    pool = []
    for r in recipes:
        # Lower-case: Tandoor already had a "Pork" keyword, which the seed reused for
        # `pork`, so a case-sensitive match found no pork recipes (2026-11-01).
        names = {(k.get("name") or "").lower() for k in (r.get("keywords") or [])}
        if season not in names:
            continue
        if S.is_never_buy(r.get("name", ""), never):
            continue
        if "veg-side" in names:
            kind = "side"
        elif "lunch-prep" in names:
            kind = "lunch"
        elif "roast" in names:
            kind = "roast"
        elif "pork" in names:
            kind = "pork"
        elif "fish" in names:
            kind = "fish"
        elif "poultry" in names:
            kind = "chicken"
        elif "ground-beef" in names:
            kind = "ground_beef"
        elif "steak" in names:
            kind = "steak"
        else:
            continue
        beef_style = None
        if kind == "ground_beef":
            beef_style = next((s for s in ("comfort", "tex-mex", "asian", "italian",
                                           "greek") if s in names), "comfort")
        variant = None
        if "salmon" in names:
            variant = "salmon"
        elif "tuna" in names:
            variant = "tuna"
        pool.append({
            "id": r["id"], "name": r["name"], "kind": kind, "season": season,
            "variant": variant, "beef_style": beef_style,
            # The time and ingredient caps were already applied when the owner
            # approved the candidates (with pantry staples excluded). Re-counting raw
            # Tandoor ingredients here rejected every approved fish (2026-11-01:
            # "not enough eligible fish/tuna ... have 0"), so the run trusts the set.
            "working_time": None,
            "ingredient_count": None, "slot": "",
            "servings": r.get("servings", 2),
            "tags": sorted(names & {"beef-lunch", "short-ribs", "tri-tip"}),
            "veg_foods": [] if kind == "side" else veg_foods(r, vegetables, never),
            "lunch_friendly": (kind in ("chicken", "lunch")
                               and (lo_kw in names or r["name"].lower() in lo_names)),
            "rice": kind != "side" and wants_rice(r, rice_cfg),
            "freezer_lines": F.recipe_lines(r, fmap),
        })
    return pool


def aggregate_ingredients(plan_entries, recipe_detail_by_id):
    """Sum (food, unit) across the week's recipes, scaled to each entry's
    servings. Returns [{food_id, food_name, unit_id, unit_name, amount}]."""
    agg = {}
    for e in plan_entries:
        r = recipe_detail_by_id.get(e.recipe_id)
        if not r:
            continue
        base = r.get("servings") or 1
        scale = (e.servings or base) / base
        for step in r.get("steps") or []:
            for ing in step.get("ingredients") or []:
                food = ing.get("food") or {}
                unit = ing.get("unit") or {}
                key = (food.get("id"), unit.get("id"))
                if food.get("id") is None:
                    continue
                row = agg.setdefault(key, {
                    "food_id": food.get("id"), "food_name": food.get("name"),
                    "unit_id": unit.get("id"), "unit_name": unit.get("name"),
                    "amount": 0.0, "food_onhand": food.get("food_onhand"),
                    "supermarket_category": food.get("supermarket_category")})
                row["amount"] += (ing.get("amount") or 0) * scale
    return list(agg.values())


def stray_onhand_foods(plan_entries, recipe_detail_by_id, is_stray):
    """{food name: [recipe names]} for the week's shopping recipes whose food is
    on hand in Tandoor although no pantry/stock rule puts it there (`is_stray`,
    see cleanup.is_stray_onhand). Tandoor leaves those foods off the list, so the
    build logs them (W46: the chili's kidney beans). Leftover meals are skipped,
    like the shopping add."""
    out = {}
    for e in plan_entries:
        if getattr(e, "role", None) == "leftover":
            continue
        r = recipe_detail_by_id.get(e.recipe_id) or {}
        for step in r.get("steps") or []:
            for ing in step.get("ingredients") or []:
                food = ing.get("food") or {}
                name = food.get("name")
                if ing.get("is_header") or not name or not food.get("food_onhand"):
                    continue
                if is_stray(name):
                    names = out.setdefault(name, [])
                    if r.get("name") not in names:
                        names.append(r.get("name"))
    return out


def load_stock(cfg, env=None):
    """The freezer inventory as a generator Stock, or None (logged
    freezer_inventory_missing) so the build plans exactly as before."""
    p = F.path(env)
    if not (cfg.get("freezer") or {}).get("enabled", True):
        return None
    data, err = F.load(p)
    if data is None:
        jlog(event="freezer_inventory_missing", path=p, reason=err,
             note="planning without the freezer inventory (as before)")
        return None
    return F.Stock(data)


def unmapped_freezer_foods(entries, details, cfg):
    """{food: [recipes]} for this week's freezer-stock foods (a stock/bulk
    store's freezer aisle) that `freezer.map` does not map to a cut: they count
    as available and nothing is reserved for them."""
    from . import seed as SD
    _, foods_cfg, _, _ = SD.load_configs()
    fmap = (cfg.get("freezer") or {}).get("map") or {}
    out = {}
    for e in entries:
        r = details.get(e.recipe_id) or {}
        for name in _food_names(r):
            if F.cut_for(name, fmap):
                continue
            if F.is_freezer_cat(SD.route_food(name, foods_cfg, cfg["stores"]), cfg["stores"]):
                names = out.setdefault(name, [])
                if r.get("name") not in names:
                    names.append(r.get("name"))
    return out


def reserve_freezer(cfg, wk, entries, details, env=None):
    """After the plan is written: decrement what it takes from freezer.json and
    log freezer_reserved. Never fails the build."""
    p = F.path(env)
    try:
        data, err = F.load(p)
        if data is None:
            return None
        fmap = (cfg.get("freezer") or {}).get("map") or {}
        items, short = F.reservations(entries, details, data, fmap, wk)
        unmapped = unmapped_freezer_foods(entries, details, cfg)
        if unmapped:
            jlog(event="freezer_unmapped", week=wk, foods=unmapped,
                 note="freezer food with no freezer.map entry: counted as available, not reserved")
        if short:
            jlog(event="freezer_short", level="warn", week=wk, items=short,
                 note="planned beyond the count on the Pantry tab: check the freezer")
        status = F.apply_reservations(p, wk, items)
        jlog(event="freezer_reserved", week=wk, status=status,
             items=[{k: it[k] for k in ("cut", "amount", "unit", "recipe", "date")}
                    for it in items])
        return items
    except Exception as ex:  # noqa: BLE001 -- the inventory never fails the build
        jlog(event="freezer_reserve_error", level="warn", week=wk,
             error=f"{type(ex).__name__}: {ex}")
        return None


def _stray_checker(cfg):
    from . import cleanup as CL
    from . import seed as SD
    _, foods_cfg, _, _ = SD.load_configs()
    maps = CL.Maps(CL.load_cleanup())
    return lambda name: CL.is_stray_onhand(name, maps, foods_cfg, cfg["stores"])


# ---- orchestration --------------------------------------------------------
def load_cfg():
    return yaml.safe_load(open(os.path.join(STACK, "config.yaml")))


def _tandoor(cfg):
    from .tandoor import Tandoor
    return Tandoor(cfg["tandoor"]["url"], os.environ["TANDOOR_USER"],
                   os.environ["TANDOOR_PASS"],
                   cfg["tandoor"].get("space", "Mack House"),
                   space_id=cfg["tandoor"].get("space_id"),
                   household=cfg["tandoor"].get("household"),
                   household_id=cfg["tandoor"].get("household_id")).login()


def _week_notes(t, start, end, wk):
    marker = f"[gp:{wk}]"
    return [e.get("note") or "" for e in t.meal_plan(start.isoformat(), end.isoformat())
            if marker in (e.get("note") or "")]


def _already_planned(t, start, end, wk):
    return bool(_week_notes(t, start, end, wk))


def ensure_setup(t, cfg):
    """Idempotent Tandoor setup the weekly build needs; creates only what is
    missing, so it is a no-op once done: every configured meal type (the Snack
    type, added 2026-11), the two rice keywords, and the rice side recipe from
    recipes/fixed.yaml. Returns the names it created."""
    from . import seed as SD
    created = []
    have = t.meal_types()
    for i, name in enumerate(cfg["tandoor"]["meal_types"].values()):
        if name not in have:
            t.post("/api/meal-type/", {"name": name, "order": i})
            created.append(f"meal-type:{name}")
    created += ensure_lunch_friendly(t, cfg)
    rc = cfg.get("rice") or {}
    if not rc.get("enabled"):
        return created
    kws = t.keywords()
    for kw in (rc.get("keyword", "serve-with-rice"), rc.get("opt_out_keyword", "no-rice-side")):
        if kw not in kws:
            t.ensure_keyword(kw)
            created.append(f"keyword:{kw}")
    name = rc.get("recipe", "Brown Rice (microwave packet)")
    if not any(r.get("name") == name for r in t.get_all("/api/recipe/")):   # exact name, like seed_fixed
        _, foods_cfg, fixed_cfg, _ = SD.load_configs()
        only = [r for r in fixed_cfg.get("recipes") or [] if r["name"] == name]
        if not only:
            raise G.PlanError(f"rice recipe {name!r} is not in recipes/fixed.yaml")
        SD.seed_fixed(t, {"recipes": only}, cfg, foods_cfg,
                      t.supermarket_categories(), t.keywords())
        created.append(f"recipe:{name}")
    return created


def lunch_friendly_patches(recipes, names, keyword):
    """[(recipe_id, keywords body)] for the named recipes (exact name, any case)
    that lack `keyword`; the body MERGES it into the recipe's keywords (as the
    seed does), so a re-run is a no-op. `keyword` is {id, name}."""
    want = {str(n).lower() for n in names or []}
    out = []
    for r in recipes:
        if (r.get("name") or "").lower() not in want:
            continue
        kws = [{"id": k["id"], "name": k["name"]} for k in r.get("keywords") or []]
        if any(k["name"].lower() == keyword["name"].lower() for k in kws):
            continue
        out.append((r["id"], kws + [{"id": keyword["id"], "name": keyword["name"]}]))
    return out


def ensure_lunch_friendly(t, cfg):
    """Owner 2026-11-11: the bulk dinners (lunches.leftovers.recipes) carry the
    `lunch-friendly` keyword. Creates the keyword and tags the named recipes that
    lack it; idempotent (nothing to do once tagged). Returns what it changed."""
    lo = (cfg.get("lunches") or {}).get("leftovers") or {}
    if not lo.get("enabled") or not lo.get("recipes"):
        return []
    name = lo.get("keyword", "lunch-friendly")
    created = []
    kw = t.keywords().get(name)
    if not kw:
        kw = t.ensure_keyword(name)
        created.append(f"keyword:{name}")
    for rid, body in lunch_friendly_patches(t.get_all("/api/recipe/"), lo["recipes"], kw):
        t.patch(f"/api/recipe/{rid}/", {"keywords": body})
        created.append(f"tag:{name}:{rid}")
    return created


def run_weekly(cfg, poll=False):
    tz = cfg["cadence"]["timezone"]
    from zoneinfo import ZoneInfo
    today = datetime.datetime.now(ZoneInfo(tz)).date()
    start, end = next_week_window(today, cfg["cadence"]["plan_start_weekday"])
    wk = G.week_key(start)
    t = _tandoor(cfg)

    if poll:
        return _poll_pass(cfg, t, start, end, wk)

    notes = _week_notes(t, start, end, wk)
    if notes:
        # Never re-plan a planned week, but say so when it is short of what the
        # config asks for now (W46: built 2026-11-01 with 2 lunch preps, before
        # the 3-batch rule merged; every later build skipped it silently).
        short = G.missing_counts(notes, cfg)
        extra = ({"level": "warn",
                  "short": {k: {"have": h, "want": w} for k, (h, w) in short.items()},
                  "note": "planned before a config change; add the missing entries by hand"}
                 if short else {})
        jlog(event="skip", reason="week already planned", week=wk, **extra)
        return 0

    try:
        created = ensure_setup(t, cfg)     # Snack meal type + rice side recipe (idempotent)
    except G.PlanError as e:
        jlog(event="plan_error", week=wk, error=str(e))
        log.error("setup error: %s", e)
        return 1
    if created:
        jlog(event="setup", week=wk, created=created)

    season = G.season_for_date(start, cfg.get("seasons"))
    # pull candidate recipes (detail, for working_time + ingredients) by season book/keywords
    recipes = [t.get(f"/api/recipe/{r['id']}/")
               for r in t.get_all("/api/recipe/", keyword=_kw_id(t, season))]
    pool = build_pool(recipes, season, cfg)
    pool += _fixed_pool(t)                 # season-independent staples + snacks
    stock = load_stock(cfg)                # None: plan as before (file missing/unreadable)
    report = {}
    try:
        _require_fixed(cfg, pool)          # loud fail if a fixed recipe isn't seeded
        entries = G.generate_week(pool, start, cfg, stock=stock, report=report)
    except G.PlanError as e:
        jlog(event="plan_error", week=wk, error=str(e))
        log.error("plan error: %s", e)
        return 1

    mt = t.meal_types()
    mp_ids = []
    for e in entries:
        # addshopping=true is the native "add to shopping list" flow (confirmed in
        # the live /openapi/ MealPlan write schema): Tandoor creates the shopping
        # entries itself, scaled to `servings`, linked back via list_recipe, and
        # already excludes on-hand/ignored foods. recipe + meal_type are nested
        # objects keyed by id (RecipeOverview / MealType); Tandoor 2.7 also
        # requires `name` on both (400 "This field is required." without it).
        # Leftover lunches (the Thursday roast, the day after a lunch-friendly
        # dinner) are planned WITHOUT addshopping: the dinner was shopped at 4
        # servings for them, so nothing extra is ordered.
        mtype = mt[cfg["tandoor"]["meal_types"][e.meal_type]]
        body = {"from_date": e.date.isoformat(), "to_date": e.date.isoformat(),
                "recipe": {"id": e.recipe_id, "name": e.recipe_name}, "servings": e.servings,
                "meal_type": {"id": mtype["id"], "name": mtype["name"]},
                "note": e.note, "addshopping": e.role != "leftover"}
        created = t.post("/api/meal-plan/", body)
        mp_ids.append(created.get("id"))

    # Meal-plan sharing: this Tandoor 2.7 API exposes no `shared` field, so the
    # entries can't be shared with jmack/rach programmatically. All three users
    # are in the Mack House space (which scopes the data); per-entry sharing is
    # an owner step (see the runbook). Logged, not failed.
    jlog(event="sharing_note", week=wk,
         share_with=list((cfg["tandoor"].get("share_with") or {}).keys()),
         note="meal-plan 'shared' field absent in Tandoor 2.7 API; owner step")

    by_id = {r["id"]: r for r in recipes}
    if stock is not None:
        parity_fish = cfg["proteins"]["fish_parity"][G.parity_for_week(start)]
        if stock.fish and stock.fish != parity_fish:
            jlog(event="freezer_fish", week=wk, parity=parity_fish, planned=stock.fish,
                 note="fish parity yielded to the fish in the freezer")
        if stock.short:
            jlog(event="freezer_short_pick", level="warn", week=wk, picks=stock.short,
                 note="no recipe the freezer covers for this slot; planned anyway")
        try:
            for e in entries:
                if e.recipe_id not in by_id:
                    by_id[e.recipe_id] = t.get(f"/api/recipe/{e.recipe_id}/")
        except Exception as ex:  # noqa: BLE001
            log.warning("recipe detail fetch for the freezer failed: %s", ex)
        reserve_freezer(cfg, wk, entries, by_id)

    if report.get("lunch_gaps"):
        jlog(event="lunch_gap", level="warn", week=wk, days=report["lunch_gaps"],
             note="no lunch-friendly dinner for the night before: added to a prep batch")
    stats = {}
    lists, unrouted, skipped = S.build_store_lists(t.shopping_list_entries(), cfg, "weekly",
                                                   stats=stats)
    try:
        for e in entries:
            if e.recipe_id not in by_id:
                by_id[e.recipe_id] = t.get(f"/api/recipe/{e.recipe_id}/")
        stray = stray_onhand_foods(entries, by_id, _stray_checker(cfg))
        if stray:
            jlog(event="stray_onhand", level="warn", week=wk, foods=stray,
                 note="on hand in Tandoor but no pantry/stock rule: NOT on the list; "
                      "untick on hand or run grocery.cleanup --apply")
    except Exception as ex:  # noqa: BLE001 -- a report, never fails the build
        log.warning("stray on-hand check failed: %s", ex)
    jlog(event="planned", week=wk, season=season, meal_plan_entries=len(mp_ids),
         stores={k: v.count() for k, v in lists.items()},
         unrouted=len(unrouted), skipped_onhand=skipped,
         skipped_never_buy=stats.get("skipped_never_buy", 0),
         **{"summary": G.plan_summary(entries)})

    _create_weekly_task(cfg, wk, start, lists)
    return 0


def _kw_id(t, name):
    return (t.keywords().get(name) or {}).get("id")


def _fixed_pool(t):
    """Fetch the fixed breakfast/snack feeder recipes (season-independent) and
    return them as pool items by name, so the generator can place them."""
    out, seen = [], set()
    for kw in ("breakfast", "snack", "burger-night", "rice-side"):
        kid = _kw_id(t, kw)
        if not kid:
            continue
        for r in t.get_all("/api/recipe/", keyword=kid):
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            out.append({"id": r["id"], "name": r["name"],
                        "kind": {"breakfast": "staple", "snack": "snack"}.get(kw, "burger"),
                        "season": None, "variant": None, "working_time": None,
                        "ingredient_count": None, "servings": r.get("servings", 7)})
    return out


def _require_fixed(cfg, pool):
    """Raise loudly if any required fixed recipe (Weekly staples, Snacks to buy,
    Snack prep when snacks.prep_enabled) is missing -- they must be seeded, never skipped silently."""
    names = {r.get("name") for r in pool}
    needed = [cfg["breakfast"]["staples_recipe"]] + [cfg["snacks"][k] for k in G.snack_keys(cfg)]
    missing = [n for n in needed if n not in names]
    if missing:
        raise G.PlanError("fixed recipes not seeded: " + ", ".join(missing)
                          + " -- run `grocery.seed --apply`")


def week_mealplan_ids(meal_plan_entries, wk):
    """Ids of the meal-plan entries this job wrote for week `wk` (note marker)."""
    marker = f"[gp:{wk}]"
    return {e.get("id") for e in meal_plan_entries if marker in (e.get("note") or "")}


def entries_to_drop(entries, cfg, approved, mp_ids, now=None):
    """Shopping entries of THIS plan week (linked to one of `mp_ids`) that sit
    on a weekly store the owner did not approve: [(entry_id, store, food)].

    "New Pi only" means the Target half of this week is not being ordered (the
    pantry is stocked; design note 6c), so those entries are dropped for good.
    Older or ad-hoc entries are never touched, and neither is any store when
    `approved` is empty (skip) or holds both stores (all)."""
    if not approved:
        return []
    week = [e for e in entries if S.entry_mealplan_id(e) in mp_ids]
    lists, _, _ = S.build_store_lists(week, cfg, "weekly", now=now)
    out = []
    for store, sl in lists.items():
        if store in approved:
            continue
        for items in sl.aisles.values():
            out.extend((it.entry_id, store, it.food) for it in items)
    return out


def _poll_pass(cfg, t, start, end, wk):
    v = _vikunja(cfg)
    task = v.find_by_title(f"({wk})")
    if not task:
        jlog(event="poll", week=wk, result="no task found")
        return 0
    done, choice = v.outcome(task)
    jlog(event="poll", week=wk, task_id=task.get("id"), done=done, choice=choice)
    if not done:
        # Not decided yet: the procedure polls again later on Thursday
        # (07:05, 12:05, 17:05 CT); a later pass applies the decision.
        return 0
    approved = S.approved_stores(choice)
    # Act on the choice. A single-store approval DELETES this week's entries for
    # the other weekly store (they are not being ordered), so they can neither
    # come back on the page nor be counted in next week's list and task. Until
    # 2026-11 they were only delayed past the plan week and then resurfaced.
    # `all` drops nothing; `skipped` (task closed / deadline passed with no
    # APPROVALS-RESULT) leaves the list intact and nothing marked bought. Safe to
    # re-run: a later pass finds nothing left to drop. The Loki line lists each
    # dropped food, so an entry can be re-added by hand.
    dropped = []
    if choice in ("newpi", "target"):
        mp_ids = week_mealplan_ids(t.meal_plan(start.isoformat(), end.isoformat()), wk)
        for eid, store, food in entries_to_drop(t.shopping_list_entries(), cfg,
                                                approved, mp_ids):
            t.delete(f"/api/shopping-list-entry/{eid}/")
            dropped.append({"id": eid, "store": store, "food": food})
    jlog(event="poll_applied", week=wk, approved=sorted(approved),
         dropped=len(dropped), dropped_items=dropped)
    return 0


def _vikunja(cfg):
    from .vikunja import Vikunja
    return Vikunja(cfg["approvals"]["vikunja_url"], os.environ["VIKUNJA_TOKEN"],
                   int(os.environ["VIKUNJA_APPROVALS_PROJECT"]))


_DOW = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6}


def _due_and_reminder(cfg, start):
    """Due + reminder on the configured weekday (default Wednesday) in the week
    BEFORE the Sunday plan start -- New Pi needs 2-3 days lead for the Saturday
    delivery, so the order must go in by Wednesday night."""
    from zoneinfo import ZoneInfo
    w = cfg["approvals"]["weekly"]
    tz = ZoneInfo(cfg["cadence"]["timezone"])
    due_wd = _DOW[w.get("due_weekday", "wednesday")]
    delta = (start.weekday() - due_wd) % 7 or 7          # the due weekday strictly before start
    dday = start - datetime.timedelta(days=delta)
    due = datetime.datetime.combine(dday, datetime.time(w["due_hour"]), tz)
    rem = datetime.datetime.combine(dday, datetime.time(w["reminder_hour"]), tz)
    return due.isoformat(), rem.isoformat()


def _create_weekly_task(cfg, wk, start, lists):
    newpi = lists.get("NewPi")
    target = lists.get("Target")
    n_np = newpi.count() if newpi else 0
    n_tg = target.count() if target else 0
    page = "https://home.kmkdp.com/meal/groceries"
    choices = cfg["approvals"]["weekly"]["choices"]
    desc = (f"<p>Week {wk}: New Pi {n_np} items, Target {n_tg} items. "
            f"<a href=\"{page}\">Open the grocery page</a></p>"
            f"<p>APPROVALS-CHOICES: {choices}</p>")
    due, reminder = _due_and_reminder(cfg, start)
    try:
        v = _vikunja(cfg)
        task = v.create_approval(f"Approve weekly grocery order ({wk})", desc,
                                 due_date=due, reminders=[reminder])
        jlog(event="task_created", week=wk, task_id=task.get("id"),
             newpi=n_np, target=n_tg, due=due)
    except Exception as e:  # noqa: BLE001
        jlog(event="task_error", week=wk, error=str(e))
        log.error("could not create approval task: %s", e)


def preview(cfg, week_of=None):
    """Read-only dry run of a weekly build: the meals the generator would plan for
    the week starting `week_of` (a Sunday; default = the next run's week) and the
    store lists Tandoor would create from them. Nothing is written."""
    from . import seed as SD
    tz = cfg["cadence"]["timezone"]
    from zoneinfo import ZoneInfo
    today = datetime.datetime.now(ZoneInfo(tz)).date()
    if week_of:
        start = week_of
        end = start + datetime.timedelta(days=6)
    else:
        start, end = next_week_window(today, cfg["cadence"]["plan_start_weekday"])
    wk = G.week_key(start)
    t = _tandoor(cfg)
    season = G.season_for_date(start, cfg.get("seasons"))
    if _already_planned(t, start, end, wk):
        print(f"NOTE: {wk} is already planned in Tandoor; the real run will skip it.")
    recipes = [t.get(f"/api/recipe/{r['id']}/")
               for r in t.get_all("/api/recipe/", keyword=_kw_id(t, season))]
    pool = build_pool(recipes, season, cfg) + _fixed_pool(t)
    stock = load_stock(cfg)                # read-only here: nothing is reserved
    report = {}
    try:
        _require_fixed(cfg, pool)
        entries = G.generate_week(pool, start, cfg, stock=stock, report=report)
    except G.PlanError as e:
        print(f"PLAN ERROR {wk}: {e}")
        return 1
    if stock is None:
        print("NOTE: no freezer inventory; planned without it.")
    elif stock.short:
        print("NOTE: planned beyond the freezer for: "
              + ", ".join(f"{s['recipe']} {s['need']}" for s in stock.short))
    print(f"== {wk} ({season}) {start:%a %b %-d} - {end:%a %b %-d}: {len(entries)} meal-plan entries"
          f"; no dinner: {', '.join(report.get('off_nights') or []) or '-'}")
    if report.get("lunch_gaps"):
        print("NOTE: lunch days no leftover covers (added to a prep): "
              + ", ".join(report["lunch_gaps"]))
    for e in sorted(entries, key=lambda e: (e.date, e.meal_type)):
        print(f"  {e.date:%a %d}  {e.meal_type:<9} {e.recipe_name[:55]} x{e.servings}"
              f"{'  (leftovers, no shopping)' if e.role == 'leftover' else ''}")
    never, skipped_never = cfg.get("never_buy") or [], set()
    _, foods_cfg, _, _ = SD.load_configs()
    is_stray = _stray_checker(cfg)
    food_cache, lines, stray = {}, {}, {}
    for e in entries:
        if e.role == "leftover":
            continue
        r = t.get(f"/api/recipe/{e.recipe_id}/")
        factor = float(e.servings or 2) / float(r.get("servings") or 1)
        for st in r.get("steps") or []:
            for i in st.get("ingredients") or []:
                f = i.get("food") or {}
                if i.get("is_header") or not f.get("id"):
                    continue
                if f["id"] not in food_cache:
                    food_cache[f["id"]] = t.get(f"/api/food/{f['id']}/")
                full = food_cache[f["id"]]
                if full.get("food_onhand") and is_stray(full["name"]):
                    stray.setdefault(full["name"], set()).add(r["name"][:30])
                if full.get("food_onhand") or full.get("ignore_shopping"):
                    continue
                if S.is_excluded(full["name"], cfg.get("exclude") or []):
                    continue
                if S.is_never_buy(full["name"], never):
                    skipped_never.add(full["name"])
                    continue
                cat = (full.get("supermarket_category") or {}).get("name") or \
                    SD.route_food(full["name"], foods_cfg, cfg["stores"])
                store, _cad, aisle = S.parse_category(cat)
                unit = (i.get("unit") or {}).get("name") or ""
                key = (store or "UNROUTED", aisle or "Other", full["name"].lower())
                d = lines.setdefault(key, {"name": full["name"], "amt": {}, "recipes": set()})
                d["amt"][unit] = d["amt"].get(unit, 0) + float(i.get("amount") or 0) * factor
                d["recipes"].add(r["name"][:30])
    weekly = set(cfg["stores"].get("weekly_stores") or [])
    for store in sorted({k[0] for k in lines}):
        keys = sorted(k for k in lines if k[0] == store)
        note = "" if store in weekly else " (not on the weekly list: bought by hand / on hand)"
        print(f"\n-- {store}: {len(keys)} lines{note}")
        for k in keys:
            d = lines[k]
            qty = " + ".join(f"{v:g} {u}".strip() for u, v in d["amt"].items() if v)
            print(f"  [{k[1]}] {d['name']}  {qty}")
    if skipped_never:
        print("\n-- NOT LISTED: never_buy: " + ", ".join(sorted(skipped_never, key=str.lower)))
    if stray:
        print("\n-- NOT LISTED: on hand in Tandoor but no pantry/stock rule "
              "(untick on hand, or grocery.cleanup --apply)")
        for name in sorted(stray, key=str.lower):
            print(f"  {name}  ({', '.join(sorted(stray[name]))})")
    return 0


_TO_TSP = {"tsp": 1.0, "teaspoon": 1.0, "teaspoons": 1.0, "tbsp": 3.0, "tablespoon": 3.0,
           "tablespoons": 3.0, "cup": 48.0, "cups": 48.0, "pinch": 1 / 16, "dash": 1 / 8}
_JAR_TSP = 12.0     # a Penzeys 1/4-cup jar is 12 tsp

_TO_LB = {"lb": 1.0, "lbs": 1.0, "pound": 1.0, "pounds": 1.0, "oz": 1 / 16, "ounce": 1 / 16,
          "ounces": 1 / 16, "g": 1 / 453.6, "kg": 2.2046}


def _food_rule(name, foods_cfg):
    """The longest foods.yaml rule matching `name` (same rule route_food uses)."""
    low, best = (name or "").lower(), None
    for rule in foods_cfg.get("rules", []):
        m = rule["match"].lower()
        if m in low and (best is None or len(m) > len(best["match"])):
            best = rule
    return best or {}


def freezer_usage(cfg, weeks=4, week_of=None):
    """Read-only: plan the next `weeks` weeks the way the Tuesday build would and
    total the freezer-stock foods they use (Amana beef, O'Connell chicken breast
    and pork chops), in pounds and in packs where foods.yaml gives pack_lb. This
    sizes the monthly O'Connell order and shows how far the beef share goes.
    Each week is planned independently, as the real runs are."""
    import math
    from . import seed as SD
    tz = cfg["cadence"]["timezone"]
    from zoneinfo import ZoneInfo
    today = datetime.datetime.now(ZoneInfo(tz)).date()
    start = week_of or next_week_window(today, cfg["cadence"]["plan_start_weekday"])[0]
    _, foods_cfg, _, _ = SD.load_configs()
    t = _tandoor(cfg)
    recipes_by_season, rcache = {}, {}
    totals, other_units, tsp_totals = {}, {}, {}
    for w in range(weeks):
        s = start + datetime.timedelta(weeks=w)
        season = G.season_for_date(s, cfg.get("seasons"))
        if season not in recipes_by_season:
            recipes_by_season[season] = [t.get(f"/api/recipe/{r['id']}/")
                                         for r in t.get_all("/api/recipe/", keyword=_kw_id(t, season))]
        pool = build_pool(recipes_by_season[season], season, cfg) + _fixed_pool(t)
        try:
            entries = G.generate_week(pool, s, cfg)
        except G.PlanError as e:
            print(f"PLAN ERROR {G.week_key(s)}: {e}")
            continue
        for e in entries:
            if e.role == "leftover":
                continue
            r = rcache.get(e.recipe_id) or t.get(f"/api/recipe/{e.recipe_id}/")
            rcache[e.recipe_id] = r
            factor = float(e.servings or 2) / float(r.get("servings") or 1)
            for st in r.get("steps") or []:
                for i in st.get("ingredients") or []:
                    name = (i.get("food") or {}).get("name") or ""
                    cat = SD.route_food(name, foods_cfg, cfg["stores"]) if name else ""
                    if not name or not SD.is_freezer_stock(cat, cfg["stores"]):
                        continue
                    store = cat.split("/", 1)[0]
                    rule = _food_rule(name, foods_cfg)
                    amt = float(i.get("amount") or 0) * factor
                    unit = ((i.get("unit") or {}).get("name") or "").lower()
                    if store == "Penzeys" and unit in _TO_TSP:      # spices: by volume
                        k = (store, rule.get("match", name))
                        tsp_totals[k] = tsp_totals.get(k, 0) + amt * _TO_TSP[unit]
                        continue
                    if unit in _TO_LB:
                        lb = amt * _TO_LB[unit]
                    elif unit in ("", "each", "whole", "small", "medium", "large") and rule.get("each_lb"):
                        lb = amt * rule["each_lb"]
                    else:
                        k = (store, rule.get("match", name))
                        other_units.setdefault(k, {}).setdefault(unit, 0)
                        other_units[k][unit] += amt
                        continue
                    k = (store, rule.get("match", name))
                    totals[k] = totals.get(k, 0) + lb
    end = start + datetime.timedelta(weeks=weeks) - datetime.timedelta(days=1)
    print(f"== stock used {start:%b %-d} - {end:%b %-d %Y} ({weeks} weeks)")
    for store in sorted({k[0] for k in list(totals) + list(other_units) + list(tsp_totals)}):
        print(f"-- {store}")
        for (st, food), lb in sorted(totals.items()):
            if st != store:
                continue
            pack = _food_rule(food, foods_cfg).get("pack_lb")
            packs = f"  -> {math.ceil(lb / pack)} pack(s) of ~{pack} lb" if pack else ""
            print(f"  {food:<22} {lb:6.1f} lb{packs}")
        for (st, food), tsp in sorted(tsp_totals.items(), key=lambda x: -x[1]):
            if st == store:
                jars = math.ceil(tsp / _JAR_TSP)
                print(f"  {food:<22} {tsp:6.1f} tsp  -> ~{jars} x 1/4-cup jar")
        for (st, food), units in sorted(other_units.items()):
            if st == store:
                print(f"  {food:<22} " + " + ".join(f"{v:g} {u}" for u, v in units.items()))
    return 0


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--mode", choices=["weekly"],
                    default=os.environ.get("GROCERY_MODE", "weekly"))
    ap.add_argument("--poll", action="store_true",
                    default=os.environ.get("GROCERY_POLL", "") == "1",
                    help="weekly poll pass (read the decision)")
    ap.add_argument("--preview", action="store_true",
                    help="read-only: print the week the next run would plan, and its store lists")
    ap.add_argument("--week-of", metavar="YYYY-MM-DD",
                    help="with --preview/--freezer: the Sunday the (first) week starts on")
    ap.add_argument("--stock", "--freezer", dest="freezer", action="store_true",
                    help="read-only: stock-store use (Amana beef, O'Connell chicken/pork, Penzeys "
                         "spices) over --weeks")
    ap.add_argument("--weeks", type=int, default=4, help="with --freezer: how many weeks (default 4)")
    a = ap.parse_args(argv)
    try:
        return _dispatch(a)
    except Exception as e:  # noqa: BLE001 -- record the crash in Loki, then re-raise
        jlog(event="run_error", mode=a.mode, poll=a.poll, error=f"{type(e).__name__}: {e}")
        raise
    finally:
        flush_to_loki()


def _dispatch(a):
    cfg = load_cfg()
    if a.freezer:
        wo = datetime.date.fromisoformat(a.week_of) if a.week_of else None
        return freezer_usage(cfg, a.weeks, wo)
    if a.preview:
        wo = datetime.date.fromisoformat(a.week_of) if a.week_of else None
        return preview(cfg, wo)
    return run_weekly(cfg, poll=a.poll)


if __name__ == "__main__":
    sys.exit(main())
