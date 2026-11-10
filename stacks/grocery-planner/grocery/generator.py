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
  * a rotating veg side per dinner, no repeat within the week, SKIPPED when the
    main already holds a vegetable (`veg_foods`, see config sides.vegetables);
  * a brown-rice side (microwave packet recipe) for every main served over rice
    (`rice`, from the serve-with-rice keyword or detection in run.build_pool);
  * N lunch preps (config lunches.recipes_per_week) at 4 servings, breakfast
    staples once a week, snack feeders once a week as the Snack meal type;
  * simplicity caps (working_time, ingredient count) as a fail-closed filter;
  * deterministic per week key, so a double run yields the same plan (the run
    job additionally refuses to re-POST a week that is already planned).

The pool is a list of plain dicts so tests need no Tandoor. Each recipe:
  {id, name, slot, kind, season, working_time, ingredient_count, variant?,
   veg_foods?, rice?}
kind in fish|chicken|ground_beef|steak|side|lunch|staple|snack.
veg_foods: the main's vegetable foods (non-empty -> no veg side). rice: True ->
the main gets the rice side.
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
    meal_type: str               # config key: breakfast|lunch|dinner|snack
    recipe_id: int
    recipe_name: str
    servings: int
    role: str                    # dinner-main|side|rice|lunch|leftover|breakfast|snack
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
    # Burger night: every Nth ISO week one chicken dinner becomes the fixed
    # "Burger night" recipe (Amana patties). Skipped quietly if not seeded.
    bn = config.get("burger_night") or {}
    burger = None
    if bn.get("enabled") and int(wk.split("W")[1]) % int(bn.get("every_n_weeks", 5)) == 0:
        burger = _by_name(pool, bn.get("recipe", "Burger night"))
    chicken = _pick(pool, "chicken", season, caps, rng, used,
                    pr["poultry"] - (1 if burger else 0))
    steak = _scheduled(pool, "steak", season, caps, rng, used, wk, config) or \
        _pick(_unscheduled(pool, "steak", wk, config), "steak", season, caps, rng, used, 1)[0]
    beef = _pick_beef(pool, season, caps, rng, used, beef_n,
                      beef_cfg.get("max_asian_per_week", 1))
    roast = None
    if roast_week:
        roast = _scheduled(pool, "roast", season, caps, rng, used, wk, config) or \
            _pick(_unscheduled(pool, "roast", wk, config), "roast", season, caps, rng, used, 1)[0]

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
    other = chicken + beef + [steak] + ([burger] if burger else [])
    if flex is not None:
        other.append(flex)
    rng.shuffle(other)
    days = [d for d in range(7) if d not in mains_by_day]
    for d, rec in zip(days, other):
        mains_by_day[d] = rec

    # Sides: one veg side per dinner whose main has no vegetable of its own
    # (sides.skip_when_main_has_veg; W46: the broccoli stir fry got green beans).
    # No repeat when enough distinct sides are available; else the interim rule
    # repeats a side up to sides.max_repeats_per_week (relaxing only if the
    # approved pool is tiny) so every dinner that needs one gets one.
    sides_cfg = config["sides"]
    side_days = [d for d in range(7) if sides_cfg["per_dinner"]
                 and not (sides_cfg.get("skip_when_main_has_veg", True)
                          and mains_by_day[d].get("veg_foods"))]
    sides = (_pick_sides(pool, season, caps, rng, len(side_days),
                         sides_cfg.get("max_repeats_per_week", 2)) if side_days else [])
    side_for = dict(zip(side_days, sides))
    rice = _rice_recipe(pool, config)

    for d in range(7):
        date = week_start + datetime.timedelta(days=d)
        main = mains_by_day[d]
        # The roast dinner is shopped for 4: dinner + Thursday leftover lunch for
        # two (the leftover entry itself adds no shopping).
        servings = 4 if (roast is not None and main is roast) else 2
        entries.append(PlanEntry(date, "dinner", main["id"], main["name"], servings,
                                 "dinner-main", main.get("slot", ""), note("dinner")))
        if d in side_for:
            side = side_for[d]
            entries.append(PlanEntry(date, "dinner", side["id"], side["name"], 2,
                                     "side", side.get("slot", ""), note("side")))
        if rice and main.get("rice"):
            entries.append(PlanEntry(date, "dinner", rice["id"], rice["name"], 2,
                                     "rice", rice.get("slot", ""), note("side")))

    # ---- lunches: N preps at 4 servings, on the prep day -------------------
    prep_day = _offset(week_start, _DOW[config["lunches"]["prep_day"]])
    prep_date = week_start + datetime.timedelta(days=prep_day)
    bsf = config["lunches"].get("beef_stir_fry") or {}
    beef_lunch = None
    if bsf.get("enabled") and parity == bsf.get("parity", "A"):
        names = {n.lower() for n in bsf.get("recipes") or []}
        cands = [r for r in pool if (r.get("name", "").lower() in names
                                     or "beef-lunch" in (r.get("tags") or ()))
                 and r["id"] not in used]
        rng.shuffle(cands)
        beef_lunch = cands[0] if cands else None      # none free/seeded: plain lunches
    n_lunch = config["lunches"]["recipes_per_week"] - (1 if beef_lunch else 0)
    if beef_lunch:
        # Reserve it BEFORE the lunch pick, so a beef lunch that also carries the
        # lunch-prep keyword can't be picked twice (2 distinct batches, not 3).
        used.add(beef_lunch["id"])
    lunches = _pick(pool, "lunch", season, caps, rng, used, n_lunch)
    if beef_lunch:
        lunches.append(beef_lunch)
    lunch_servings = config["lunches"]["servings_each"]
    for rec in lunches:
        entries.append(PlanEntry(prep_date, "lunch", rec["id"], rec["name"],
                                 lunch_servings, "lunch",
                                 rec.get("slot", ""), note("lunch")))
        if rice and rec.get("rice"):
            entries.append(PlanEntry(prep_date, "lunch", rice["id"], rice["name"],
                                     lunch_servings, "rice", rice.get("slot", ""),
                                     note("side")))

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
            # Filed under the Snack meal type (was Breakfast: no Snack type existed).
            entries.append(PlanEntry(prep_date, "snack", fixed["id"], fixed["name"],
                                     fixed.get("servings", 14), "snack",
                                     fixed.get("slot", ""), note("snack")))

    return entries


def _offset(week_start, target_weekday):
    """Day offset (0..6) within the plan week whose weekday() == target_weekday."""
    for d in range(7):
        if (week_start + datetime.timedelta(days=d)).weekday() == target_weekday:
            return d
    return 0


def _week_no(wk):
    return int(wk.split("W")[1])


def _rules(config, kind):
    return [r for r in (config.get("scheduled") or []) if r.get("kind") == kind]


def _scheduled(pool, kind, season, caps, rng, used, wk, config):
    """Freezer-limited recipes (config `scheduled`): on a scheduled week the
    `kind` slot IS one of the tagged recipes (e.g. short ribs on a roast week).
    Returns the pick, or None when this isn't a scheduled week / none is seeded."""
    for rule in _rules(config, kind):
        if _week_no(wk) % int(rule["every_n_weeks"]) != int(rule.get("offset", 0)):
            continue
        cands = [r for r in pool if r.get("kind") == kind and r.get("season") == season
                 and rule["tag"] in (r.get("tags") or ()) and r["id"] not in used]
        if cands:
            rng.shuffle(cands)
            used.add(cands[0]["id"])
            return cands[0]
    return None


def _unscheduled(pool, kind, wk, config):
    """The pool without the freezer-limited recipes of `kind`, so they only appear
    on their scheduled weeks (the freezer holds a fixed number of them)."""
    tags = {r["tag"] for r in _rules(config, kind)}
    return [r for r in pool if not (r.get("kind") == kind and tags & set(r.get("tags") or ()))]


def _rice_recipe(pool, config):
    """The rice side recipe (config rice.recipe), or None when rice sides are
    off or the recipe is not in Tandoor yet (the build pass creates it)."""
    rc = config.get("rice") or {}
    if not rc.get("enabled", False):
        return None
    return _by_name(pool, rc.get("recipe", "Brown Rice (microwave packet)"))


def missing_counts(existing_notes, config):
    """Compare an ALREADY planned week (the `[gp:<wk>] <role>` notes of its
    meal-plan entries) with what the config asks for now. Returns {role: (have,
    want)} for each short role. The build never re-plans a planned week, so a
    config change made after a week was built (W46: written 2026-11-01 with 2
    lunch preps, hours before the 3-batch rule merged) stays short; the build
    logs this so the gap is visible instead of silent."""
    have = {}
    for n in existing_notes:
        role = (n or "").rsplit("] ", 1)[-1].strip()
        have[role] = have.get(role, 0) + 1
    want = {"dinner": int(config["proteins"]["dinners_per_week"]),
            "lunch": int(config["lunches"]["recipes_per_week"])}
    return {r: (have.get(r, 0), w) for r, w in want.items() if have.get(r, 0) < w}


def _by_name(pool, name):
    for r in pool:
        if r.get("name") == name:
            return r
    return None


def plan_summary(entries: list[PlanEntry]) -> dict:
    """Counts used by tests and the log line."""
    out = {"dinners": 0, "sides": 0, "rice": 0, "lunches": 0, "breakfast": 0,
           "snacks": 0, "leftovers": 0}
    role_key = {"dinner-main": "dinners", "side": "sides", "rice": "rice",
                "lunch": "lunches",
                "breakfast": "breakfast", "snack": "snacks", "leftover": "leftovers"}
    for e in entries:
        k = role_key.get(e.role)
        if k:
            out[k] += 1
    return out
