"""Weekly meal-plan generator (pure; unit tested offline).

Enforces the owner rules from config.yaml over a candidate *pool* of imported,
selected recipes:

  * season chosen by date (Spring Mar-May, Summer Jun-Aug, Fall Sep-Nov, Winter
    Dec-Feb);
  * 7 dinners = 1 fish + 4 chicken breast + 1 ground beef + 1 steak, no legumes;
  * fish is Sunday; odd ISO week => salmon + Greek beef (feta in beef); even ISO
    week => tuna + Asian beef (feta in the weekend scramble);
  * a rotating veg side per dinner, no repeat within the week;
  * 2 lunch preps at 4 servings, breakfast + snack feeder recipes once a week;
  * simplicity caps (working_time, ingredient count) as a fail-closed filter;
  * deterministic per week key, so a double run yields the same plan (the run
    job additionally refuses to re-POST a week that is already planned).

The pool is a list of plain dicts so tests need no Tandoor. Each recipe:
  {id, name, slot, kind, season, working_time, ingredient_count, variant?}
kind in fish|chicken|ground_beef|steak|side|lunch|staple|snack.
variant: fish -> salmon|tuna ; ground_beef -> greek|asian (else None).
"""
from __future__ import annotations

import datetime
import random
from dataclasses import dataclass


SEASON_DEFAULT = {3: "spring", 4: "spring", 5: "spring",
                  6: "summer", 7: "summer", 8: "summer",
                  9: "fall", 10: "fall", 11: "fall",
                  12: "winter", 1: "winter", 2: "winter"}
_DOW = {"sunday": 6, "monday": 0, "tuesday": 1, "wednesday": 2,
        "thursday": 3, "friday": 4, "saturday": 5}


class PlanError(Exception):
    """Raised when the selected pool cannot satisfy the week's rules."""


@dataclass
class PlanEntry:
    date: datetime.date
    meal_type: str               # config key: breakfast|lunch|dinner
    recipe_id: int
    recipe_name: str
    servings: int
    role: str                    # dinner-main|side|lunch|breakfast|snack
    slot: str
    note: str


def season_for_date(d: datetime.date, seasons: dict | None = None) -> str:
    if not seasons:
        return SEASON_DEFAULT[d.month]
    for name, months in seasons.items():
        if d.month in months:
            return name
    return SEASON_DEFAULT[d.month]


def week_key(week_start: datetime.date) -> str:
    """Stable key for the plan week. Keys off the Monday inside the Sun->Sat
    span so parity is stable per calendar week."""
    monday = week_start + datetime.timedelta(days=(7 - week_start.weekday()) % 7)
    if week_start.weekday() == 6:          # Sunday start -> the next day is Monday
        monday = week_start + datetime.timedelta(days=1)
    iso = monday.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def parity_for_week(week_start: datetime.date) -> str:
    """'A' for odd ISO week, 'B' for even."""
    wk = int(week_key(week_start).split("W")[1])
    return "A" if wk % 2 == 1 else "B"


def _eligible(rec: dict, caps: dict, kind: str) -> bool:
    cap = (caps.get("lunch_working_time_max", caps["working_time_max"])
           if kind == "lunch" else caps["working_time_max"])
    wt = rec.get("working_time")
    if wt is not None and wt > cap:
        return False
    ic = rec.get("ingredient_count")
    if ic is not None and ic > caps["ingredient_max"]:
        return False
    return True


def _pick(pool, kind, season, caps, rng, used_ids, n=1, variant=None):
    cands = [r for r in pool
             if r.get("kind") == kind
             and r.get("season") == season
             and r["id"] not in used_ids
             and (variant is None or r.get("variant") == variant)
             and _eligible(r, caps, kind)]
    rng.shuffle(cands)
    if len(cands) < n:
        raise PlanError(
            f"not enough eligible {kind}"
            + (f"/{variant}" if variant else "")
            + f" recipes for {season}: need {n}, have {len(cands)}")
    chosen = cands[:n]
    for r in chosen:
        used_ids.add(r["id"])
    return chosen


def generate_week(pool: list[dict], week_start: datetime.date, config: dict) -> list[PlanEntry]:
    caps = config["caps"]
    pr = config["proteins"]
    seasons = config.get("seasons")
    season = season_for_date(week_start, seasons)
    parity = parity_for_week(week_start)
    wk = week_key(week_start)
    rng = random.Random(wk)              # deterministic per week
    used: set[int] = set()
    entries: list[PlanEntry] = []

    def note(role):
        return f"[gp:{wk}] {role}"

    # ---- dinners -----------------------------------------------------------
    fish_variant = pr["fish_parity"][parity]       # salmon | tuna
    beef_variant = pr["beef_parity"][parity]       # greek | asian

    fish_day = _offset(week_start, _DOW[pr["fish_day"]])   # offset of fish weekday
    # Fish: Sunday.
    fish = _pick(pool, "fish", season, caps, rng, used, 1, variant=fish_variant)[0]
    # The remaining 6 dinners: 4 chicken + 1 beef + 1 steak on the other days.
    chicken = _pick(pool, "chicken", season, caps, rng, used, pr["poultry"])
    beef = _pick(pool, "ground_beef", season, caps, rng, used, 1, variant=beef_variant)[0]
    steak = _pick(pool, "steak", season, caps, rng, used, 1)[0]

    # Day assignment: fish on its day; the rest fill the other 6 days in a
    # deterministic shuffled order.
    other = chicken + [beef, steak]
    rng.shuffle(other)
    days = [d for d in range(7) if d != fish_day]
    mains_by_day = {fish_day: fish}
    for d, rec in zip(days, other):
        mains_by_day[d] = rec

    # Sides: one per dinner, no repeat within the week.
    side_used: set[int] = set()

    for d in range(7):
        date = week_start + datetime.timedelta(days=d)
        main = mains_by_day[d]
        entries.append(PlanEntry(date, "dinner", main["id"], main["name"], 2,
                                 "dinner-main", main.get("slot", ""), note("dinner")))
        if config["sides"]["per_dinner"]:
            side = _pick(pool, "side", season, caps, rng,
                         side_used if config["sides"]["no_repeat_within_week"] else set(),
                         1)[0]
            entries.append(PlanEntry(date, "dinner", side["id"], side["name"], 2,
                                     "side", side.get("slot", ""), note("side")))

    # ---- lunches: 2 preps at 4 servings, on the prep day -------------------
    prep_day = _offset(week_start, _DOW[config["lunches"]["prep_day"]])
    prep_date = week_start + datetime.timedelta(days=prep_day)
    lunches = _pick(pool, "lunch", season, caps, rng, used,
                    config["lunches"]["recipes_per_week"])
    for rec in lunches:
        entries.append(PlanEntry(prep_date, "lunch", rec["id"], rec["name"],
                                 config["lunches"]["servings_each"], "lunch",
                                 rec.get("slot", ""), note("lunch")))

    # ---- breakfast staples + snack feeders (once a week, prep day) ---------
    for name_key, role in (("staples_recipe", "breakfast"),):
        fixed = _by_name(pool, config["breakfast"][name_key])
        if fixed:
            entries.append(PlanEntry(prep_date, "breakfast", fixed["id"], fixed["name"],
                                     fixed.get("servings", 7), role,
                                     fixed.get("slot", ""), note("breakfast")))
    for key in ("buy_recipe", "prep_recipe"):
        fixed = _by_name(pool, config["snacks"][key])
        if fixed:
            entries.append(PlanEntry(prep_date, "breakfast", fixed["id"], fixed["name"],
                                     fixed.get("servings", 14), "snack",
                                     fixed.get("slot", ""), note("snack")))

    return entries


def _offset(week_start, target_weekday):
    """Day offset (0..6) within the plan week whose weekday() == target_weekday."""
    for d in range(7):
        if (week_start + datetime.timedelta(days=d)).weekday() == target_weekday:
            return d
    return 0


def _by_name(pool, name):
    for r in pool:
        if r.get("name") == name:
            return r
    return None


def plan_summary(entries: list[PlanEntry]) -> dict:
    """Counts used by tests and the log line."""
    out = {"dinners": 0, "sides": 0, "lunches": 0, "breakfast": 0, "snacks": 0,
           "dinner_kinds": {}}
    for e in entries:
        if e.role == "dinner-main":
            out["dinners"] += 1
        elif e.role == "side":
            out["sides"] += 1
        elif e.role == "lunch":
            out["lunches"] += 1
        elif e.role == "breakfast":
            out["breakfast"] += 1
        elif e.role == "snack":
            out["snacks"] += 1
    return out
