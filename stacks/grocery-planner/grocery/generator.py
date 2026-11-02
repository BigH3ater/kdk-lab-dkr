"""Weekly meal-plan generator (pure; unit tested offline).

Enforces the owner rules from config.yaml over a candidate *pool* of imported,
selected recipes:

  * season chosen by date (Spring Mar-May, Summer Jun-Aug, Fall Sep-Nov, Winter
    Dec-Feb);
  * 7 dinners = 1 fish + 2 chicken breast + 2 ground beef + 1 flex + 1 steak;
    a roast week swaps one ground beef for a slow-cooker roast. No legume-only
    mains (beans as an ingredient are fine);
  * the flex dinner alternates pork chop / chicken breast by week (parity);
  * ground beef dishes vary: no repeat within the week, at most one Asian-style
    (e.g. beef & broccoli); feta rides on the weekend eggs;
  * fish is Sunday; odd ISO week => salmon, even ISO week => tuna;
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


def _pick_sides(pool, season, caps, rng, n, max_repeats):
    """Return n veg sides. Distinct when >= n are available; otherwise repeat,
    each side used <= max_repeats (relaxing the cap only if the pool is too small
    to reach n). Fails only if there are no eligible sides at all."""
    cands = [r for r in pool if r.get("kind") == "side" and r.get("season") == season
             and _eligible(r, caps, "side")]
    rng.shuffle(cands)
    if not cands:
        raise PlanError(f"no eligible sides for {season}")
    if len(cands) >= n:
        return cands[:n]
    cap = max_repeats if len(cands) * max_repeats >= n else -(-n // len(cands))
    chosen, counts, i = [], {}, 0
    while len(chosen) < n:
        c = cands[i % len(cands)]
        i += 1
        if counts.get(c["id"], 0) < cap:
            chosen.append(c)
            counts[c["id"]] = counts.get(c["id"], 0) + 1
    return chosen


def _pick_beef(pool, season, caps, rng, used, n, max_asian):
    """Pick n distinct ground-beef dishes for the week: no repeat (distinct
    recipe ids) and at most `max_asian` Asian-style (beef_style == 'asian')."""
    cands = [r for r in pool
             if r.get("kind") == "ground_beef" and r.get("season") == season
             and r["id"] not in used and _eligible(r, caps, "ground_beef")]
    rng.shuffle(cands)
    chosen, asian = [], 0
    for r in cands:
        if len(chosen) == n:
            break
        if r.get("beef_style") == "asian" and asian >= max_asian:
            continue
        chosen.append(r)
        if r.get("beef_style") == "asian":
            asian += 1
    if len(chosen) < n:
        raise PlanError(f"not enough eligible ground_beef for {season}: need {n}, "
                        f"have {len(chosen)} (<= {max_asian} Asian-style)")
    for r in chosen:
        used.add(r["id"])
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
    beef_cfg = config.get("beef") or {}

    fish_day = _offset(week_start, _DOW[pr["fish_day"]])   # offset of fish weekday

    # Roast week? (parity A by default, every other week). On a roast week the
    # slow-cooker roast replaces ONE GROUND-BEEF dinner: 2 beef + 1 roast.
    roast_cfg = config.get("roast") or {}
    roast_week = bool(roast_cfg.get("enabled")) and parity == roast_cfg.get("parity", "A")
    beef_n = pr["ground_beef"] - (1 if roast_week else 0)

    # Fish: Sunday. Chicken + steak are fixed counts; ground beef is the week's
    # distinct dishes (no repeat, <= max_asian Asian-style).
    fish = _pick(pool, "fish", season, caps, rng, used, 1, variant=fish_variant)[0]
    chicken = _pick(pool, "chicken", season, caps, rng, used, pr["poultry"])
    steak = _pick(pool, "steak", season, caps, rng, used, 1)[0]
    beef = _pick_beef(pool, season, caps, rng, used, beef_n,
                      beef_cfg.get("max_asian_per_week", 1))
    roast = _pick(pool, "roast", season, caps, rng, used, 1)[0] if roast_week else None

    # Flex dinner: alternates pork chop / chicken breast by week (parity picks the
    # rotation slot). A chicken flex is an extra chicken dish not already used.
    flex_cfg = config.get("flex") or {}
    flex = None
    if flex_cfg.get("enabled"):
        rotation = flex_cfg.get("rotation") or ["pork", "chicken"]
        flex_kind = rotation[(0 if parity == "A" else 1) % len(rotation)]
        pick_kind = "pork" if flex_kind == "pork" else "chicken"
        flex = _pick(pool, pick_kind, season, caps, rng, used, 1)[0]

    # Day assignment: fish on its day, roast on roast_night (if a roast week),
    # the rest fill the remaining days in a deterministic shuffled order.
    mains_by_day = {fish_day: fish}
    if roast_week:
        rd = _offset(week_start, _DOW[roast_cfg.get("night", "wednesday")])
        if rd in mains_by_day:                       # roast_night == fish day: use next free
            rd = next(d for d in range(7) if d not in mains_by_day)
        mains_by_day[rd] = roast
    other = chicken + beef + [steak]
    if flex is not None:
        other.append(flex)
    rng.shuffle(other)
    days = [d for d in range(7) if d not in mains_by_day]
    for d, rec in zip(days, other):
        mains_by_day[d] = rec

    # Sides: one per dinner. No repeat when >= 7 distinct are available; else the
    # interim rule repeats a side up to sides.max_repeats_per_week (relaxing only
    # if the approved pool is tiny) so the week always fills.
    sides = _pick_sides(pool, season, caps, rng, 7,
                        config["sides"].get("max_repeats_per_week", 2))

    for d in range(7):
        date = week_start + datetime.timedelta(days=d)
        main = mains_by_day[d]
        # The roast dinner is shopped for 4: dinner + Thursday leftover lunch for
        # two (the leftover entry itself adds no shopping).
        servings = 4 if (roast is not None and main is roast) else 2
        entries.append(PlanEntry(date, "dinner", main["id"], main["name"], servings,
                                 "dinner-main", main.get("slot", ""), note("dinner")))
        if config["sides"]["per_dinner"]:
            side = sides[d]
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

    # Roast leftovers -> Thursday lunch (role "leftover"; the run job posts it
    # WITHOUT addshopping, so no extra beef/pantry is ordered for it).
    if roast_week and roast is not None:
        tdate = week_start + datetime.timedelta(days=_offset(week_start, _DOW["thursday"]))
        entries.append(PlanEntry(tdate, "lunch", roast["id"], roast["name"], 2,
                                 "leftover", roast.get("slot", ""), note("leftover")))

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
           "leftovers": 0}
    role_key = {"dinner-main": "dinners", "side": "sides", "lunch": "lunches",
                "breakfast": "breakfast", "snack": "snacks", "leftover": "leftovers"}
    for e in entries:
        k = role_key.get(e.role)
        if k:
            out[k] += 1
    return out
