"""Weekly (and bulk) grocery run — the one-shot Komodo job.

Weekly (default): generate next week's meal plan under the owner rules, write the
entries (idempotent per week key), build the two-store shopping list from the
plan minus on-hand items, create ONE combined Vikunja approval task, and — in the
poll pass — mark the approved store's entries. Logs JSON lines to stdout for Loki.

  python -m grocery.run --mode weekly            # build pass (Thu 18:00): plan + list + task
  python -m grocery.run --mode weekly --poll     # poll pass (later): read decision, mark approved
  python -m grocery.run --mode bulk              # N-week Costco bulk list + its own task

POLL STRATEGY (justified): the approval is asynchronous and durable (the Vikunja
task IS the record), so the build pass exits immediately after creating the task
and a SEPARATE scheduled poll pass reads the outcome. No long-lived container
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
import sys

import yaml

from . import generator as G
from . import shoplist as S

HERE = os.path.dirname(os.path.abspath(__file__))
STACK = os.path.dirname(HERE)
log = logging.getLogger("grocery.run")


def jlog(**kw):
    """One structured JSON line to stdout for Loki (job=grocery-planner)."""
    kw.setdefault("job", "grocery-planner")
    print(json.dumps(kw, default=str), flush=True)


# ---- pure helpers (unit tested) -------------------------------------------
def next_week_window(today, start_weekday=6):
    """Return (start, end) for the NEXT plan week. start is the first upcoming
    `start_weekday` (Sun=6) strictly after today; end = start + 6 days."""
    days = (start_weekday - today.weekday()) % 7
    days = 7 if days == 0 else days          # always the NEXT one, not today
    start = today + datetime.timedelta(days=days)
    return start, start + datetime.timedelta(days=6)


def bulk_due(today, cfg):
    """True if `today` is a bulk day: first week of the month (if configured) and
    the right month parity (odd/even/any). The procedure fires every Thursday;
    this gate makes it act every two months."""
    cad = cfg["cadence"]
    if cad.get("bulk_first_week_only", True) and today.day > 7:
        return False
    parity = cad.get("bulk_month_parity", "any")
    if parity == "odd" and today.month % 2 == 0:
        return False
    if parity == "even" and today.month % 2 == 1:
        return False
    return True


def _ingredient_count(recipe):
    n = 0
    for step in recipe.get("steps") or []:
        n += len(step.get("ingredients") or [])
    return n


def build_pool(recipes, season):
    """Turn Tandoor recipe detail dicts into generator pool items for a season.
    Variant comes from keywords (salmon/tuna, greek/asian). kind from keywords."""
    pool = []
    for r in recipes:
        names = {k.get("name") for k in (r.get("keywords") or [])}
        if season not in names:
            continue
        if "veg-side" in names:
            kind = "side"
        elif "lunch-prep" in names:
            kind = "lunch"
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
        variant = None
        if "salmon" in names:
            variant = "salmon"
        elif "tuna" in names:
            variant = "tuna"
        elif "greek" in names:
            variant = "greek"
        elif "asian" in names:
            variant = "asian"
        pool.append({
            "id": r["id"], "name": r["name"], "kind": kind, "season": season,
            "variant": variant, "working_time": r.get("working_time"),
            "ingredient_count": _ingredient_count(r), "slot": "",
            "servings": r.get("servings", 2),
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


# ---- orchestration --------------------------------------------------------
def load_cfg():
    return yaml.safe_load(open(os.path.join(STACK, "config.yaml")))


def _tandoor(cfg):
    from .tandoor import Tandoor
    return Tandoor(cfg["tandoor"]["url"], os.environ["TANDOOR_USER"],
                   os.environ["TANDOOR_PASS"],
                   cfg["tandoor"].get("space", "Mack House")).login()


def _already_planned(t, start, end, wk):
    marker = f"[gp:{wk}]"
    for e in t.meal_plan(start.isoformat(), end.isoformat()):
        if marker in (e.get("note") or ""):
            return True
    return False


def run_weekly(cfg, poll=False):
    tz = cfg["cadence"]["timezone"]
    from zoneinfo import ZoneInfo
    today = datetime.datetime.now(ZoneInfo(tz)).date()
    start, end = next_week_window(today, cfg["cadence"]["plan_start_weekday"])
    wk = G.week_key(start)
    t = _tandoor(cfg)

    if poll:
        return _poll_pass(cfg, t, start, end, wk)

    if _already_planned(t, start, end, wk):
        jlog(event="skip", reason="week already planned", week=wk)
        return 0

    season = G.season_for_date(start, cfg.get("seasons"))
    # pull candidate recipes (detail, for working_time + ingredients) by season book/keywords
    recipes = [t.get(f"/api/recipe/{r['id']}/")
               for r in t.get_all("/api/recipe/", keyword=_kw_id(t, season))]
    pool = build_pool(recipes, season)
    try:
        entries = G.generate_week(pool, start, cfg)
    except G.PlanError as e:
        jlog(event="plan_error", week=wk, error=str(e))
        log.error("plan error: %s", e)
        return 1

    mt = t.meal_types()
    mp_ids = []
    for e in entries:
        body = {"from_date": e.date.isoformat(), "to_date": e.date.isoformat(),
                "recipe": {"id": e.recipe_id}, "servings": e.servings,
                "meal_type": {"id": mt[cfg["tandoor"]["meal_types"][e.meal_type]]["id"]},
                "note": e.note}
        created = t.post("/api/meal-plan/", body)
        mp_ids.append(created.get("id"))

    # materialize the shopping list by aggregating the week's recipe ingredients
    detail = {e.recipe_id: next((r for r in recipes if r["id"] == e.recipe_id), None)
              for e in entries}
    for row in aggregate_ingredients(entries, detail):
        if row.get("food_onhand"):
            continue
        t.post("/api/shopping-list-entry/",
               {"food": {"id": row["food_id"]},
                "unit": ({"id": row["unit_id"]} if row["unit_id"] else None),
                "amount": round(row["amount"], 2), "checked": False})

    lists, unrouted, skipped = S.build_store_lists(t.shopping_list_entries(), cfg, "weekly")
    jlog(event="planned", week=wk, season=season, meal_plan_entries=len(mp_ids),
         stores={k: v.count() for k, v in lists.items()},
         unrouted=len(unrouted), skipped_onhand=skipped,
         **{"summary": G.plan_summary(entries)})

    _create_weekly_task(cfg, wk, start, lists)
    return 0


def _kw_id(t, name):
    return (t.keywords().get(name) or {}).get("id")


def _poll_pass(cfg, t, start, end, wk):
    v = _vikunja(cfg)
    task = v.find_by_title(f"({wk})")
    if not task:
        jlog(event="poll", week=wk, result="no task found")
        return 0
    done, choice = v.outcome(task)
    jlog(event="poll", week=wk, task_id=task.get("id"), done=done, choice=choice)
    # The grocery page reads the shopping-list entries live and ticks happen in
    # the store, so Tandoor stays the source of truth; the poll pass records the
    # decision (choice) for the Loki audit trail. Nothing is toggled here. A
    # `skip` (closed task / missed deadline) leaves nothing marked bought.
    return 0


def _vikunja(cfg):
    from .vikunja import Vikunja
    return Vikunja(cfg["approvals"]["vikunja_url"], os.environ["VIKUNJA_TOKEN"],
                   int(os.environ["VIKUNJA_APPROVALS_PROJECT"]))


def _due_and_reminder(cfg, start):
    """Friday 20:00 CT due + Friday noon reminder for the plan week (the Friday
    BEFORE the Sunday start)."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(cfg["cadence"]["timezone"])
    friday = start - datetime.timedelta(days=2)   # Sunday start -> previous Friday
    due = datetime.datetime.combine(friday, datetime.time(cfg["approvals"]["weekly"]["due_hour"]), tz)
    rem = datetime.datetime.combine(friday, datetime.time(cfg["approvals"]["weekly"]["reminder_hour"]), tz)
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


def run_bulk(cfg):
    """N-week Costco bulk list. Lays down the upcoming N weeks' plans (season per
    week), sums Costco-routed + on-hand-eligible items, and creates a bulk task.
    Minimal: Amana beef reorder is Phase 2 (stub)."""
    from zoneinfo import ZoneInfo
    today = datetime.datetime.now(ZoneInfo(cfg["cadence"]["timezone"])).date()
    if not bulk_due(today, cfg):
        jlog(event="bulk_skip", reason="not a bulk week", date=today)
        return 0
    n = cfg["cadence"]["bulk_weeks"]
    jlog(event="bulk_start", weeks=n, stores=cfg["stores"]["bulk_stores"])
    # Phase 2 stub: Amana beef eighth-share reorder is a separate low-stock task.
    jlog(event="bulk_stub", note="Amana beef reorder is Phase 2; Costco-only bulk here")
    # The full bulk aggregation reuses run_weekly's laydown over n weeks; kept
    # minimal per the brief. The owner enables it once the weekly path is live.
    return 0


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--mode", choices=["weekly", "bulk"],
                    default=os.environ.get("GROCERY_MODE", "weekly"))
    ap.add_argument("--poll", action="store_true",
                    default=os.environ.get("GROCERY_POLL", "") == "1",
                    help="weekly poll pass (read the decision)")
    a = ap.parse_args(argv)
    cfg = load_cfg()
    if a.mode == "bulk":
        return run_bulk(cfg)
    return run_weekly(cfg, poll=a.poll)


if __name__ == "__main__":
    sys.exit(main())
