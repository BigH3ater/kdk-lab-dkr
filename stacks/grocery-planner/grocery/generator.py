"""Weekly meal-plan generator (pure; unit tested offline).

Enforces the owner rules from config.yaml over a candidate *pool* of imported,
selected recipes:

  * season chosen by date (Spring Mar-May, Summer Jun-Aug, Fall Sep-Nov, Winter
    Dec-Feb);
  * 5 dinners (owner 2026-11-11; proteins.off_nights Fri + Sat get none) =
    1 fish + 1 chicken breast + 1 ground beef + 1 flex + 1 steak; a roast week
    swaps the ground beef for a slow-cooker roast. No legume-only mains (beans
    as an ingredient are fine);
  * the flex dinner alternates pork chop / chicken breast by week (parity);
  * ground beef dishes vary: no repeat within the week, at most one Asian-style
    (e.g. beef & broccoli); feta rides on the weekend eggs;
  * fish is Sunday; odd ISO week => salmon, even ISO week => tuna;
  * a rotating veg side per dinner, no repeat within the week, SKIPPED when the
    main already holds a vegetable (`veg_foods`, see config sides.vegetables);
  * a brown-rice side (microwave packet recipe) for every main served over rice
    (`rice`, from the serve-with-rice keyword or detection in run.build_pool);
  * N lunch preps (config lunches.recipes_per_week) at 4 servings, one per
    lunches.prep_days (Sunday + Thursday); the other lunch days are leftovers:
    a `lunch_friendly` chicken dinner cooked at 4 the night before (its next-day
    Lunch entry has role "leftover"), or the roast's Thursday lunch;
  * breakfast staples once a week, ONE snack entry (snacks.prep_enabled adds
    the Snack prep entry back) as the Snack meal type;
  * simplicity caps (working_time, ingredient count) as a fail-closed filter;
  * deterministic per week key, so a double run yields the same plan (the run
    job additionally refuses to re-POST a week that is already planned);
  * with a freezer inventory (`stock`, grocery/freezer.py): NEVER a dinner or
    lunch the freezer cannot cover (owner 2026-11-11). A short dinner slot is
    tried with another protein in stock (FALLBACK_DEFAULT / freezer.fallback);
    nothing fits -> that night has no dinner (stock.short_slots; the skipped
    recipes are in stock.short). The most-stocked cut first where a slot has a
    choice, and fish parity yields to the fish that is in stock. stock=None =
    the plan as before;
  * config `skip_days` (ISO dates: holidays, travel): no dinner, no lunch prep
    and no leftovers on those days; the lunch days shrink with them.

The pool is a list of plain dicts so tests need no Tandoor. Each recipe:
  {id, name, slot, kind, season, working_time, ingredient_count, variant?,
   veg_foods?, rice?, lunch_friendly?}
kind in fish|chicken|ground_beef|steak|side|lunch|staple|snack.
veg_foods: the main's vegetable foods (non-empty -> no veg side). rice: True ->
the main gets the rice side.
variant: fish -> salmon|tuna ; ground_beef -> greek|asian (else None).
"""
from __future__ import annotations

import datetime
import random
from dataclasses import dataclass

from . import freezer as F


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


def _pick(pool, kind, season, caps, rng, used_ids, n=1, variant=None,
          stock=None, servings=2):
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
    chosen = F.choose(cands, n, stock, servings, kind + (f"/{variant}" if variant else ""))
    for r in chosen:                 # with a stock this may be < n (the freezer is short)
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


def _pick_beef(pool, season, caps, rng, used, n, max_asian, stock=None):
    """Pick n distinct ground-beef dishes for the week: no repeat (distinct
    recipe ids) and at most `max_asian` Asian-style (beef_style == 'asian').
    With a stock it may return fewer (the freezer is short): the caller fills
    the hole with another protein."""
    cands = [r for r in pool
             if r.get("kind") == "ground_beef" and r.get("season") == season
             and r["id"] not in used and _eligible(r, caps, "ground_beef")]
    rng.shuffle(cands)

    def ok(chosen, r):
        return not (r.get("beef_style") == "asian"
                    and sum(c.get("beef_style") == "asian" for c in chosen) >= max_asian)
    chosen = F.choose(cands, n, stock, 2, "ground_beef", ok)
    if len(chosen) < n and stock is None:
        raise PlanError(f"not enough eligible ground_beef for {season}: need {n}, "
                        f"have {len(chosen)} (<= {max_asian} Asian-style)")
    for r in chosen:
        used.add(r["id"])
    return chosen


# Owner 2026-11-11: when no recipe for a dinner slot fits the freezer, try the
# slot with another protein that has stock, walking the ladder steak -> ground
# beef -> poultry -> fish (the other slots walk it in reverse first). Config
# `freezer.fallback` overrides; nothing fits -> the dinner is left unplanned.
FALLBACK_DEFAULT = {
    "steak": ["ground_beef", "chicken", "fish"],
    "ground_beef": ["steak", "chicken", "fish"],
    "chicken": ["ground_beef", "steak", "fish"],
    "fish": ["chicken", "ground_beef", "steak"],
    "pork": ["chicken", "ground_beef", "steak", "fish"],
    "roast": ["ground_beef", "steak", "chicken", "fish"],
}


def skip_offsets(week_start, config):
    """Day offsets (0..6) of the week that are in config `skip_days` (ISO dates:
    holidays / travel; no dinner, no lunch prep, no leftovers that day)."""
    days = set()
    for v in config.get("skip_days") or []:
        try:
            days.add(v if isinstance(v, datetime.date) else datetime.date.fromisoformat(str(v)))
        except ValueError:
            continue
    return {d for d in range(7) if week_start + datetime.timedelta(days=d) in days}


def generate_week(pool: list[dict], week_start: datetime.date, config: dict,
                  stock: "F.Stock | None" = None, report: dict | None = None) -> list[PlanEntry]:
    """The week's plan entries. `report` (optional dict) receives what the run
    logs besides the entries: lunch_gaps (lunch days only a prep could cover,
    added to that prep) and off_nights."""
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
    # Cooked nights: every day but proteins.off_nights (owner 2026-11-11: Friday +
    # Saturday are leftovers / eat out and get no dinner entry).
    off = {_offset(week_start, _DOW[n]) for n in pr.get("off_nights") or []}
    cook_days = [d for d in range(7) if d not in off]
    # skip_days (holidays / travel): no lunch prep, no leftovers and no lunch
    # day on a skipped day; the dinner mix is picked as usual and the dinner that
    # lands on a skipped day is dropped.
    skip = skip_offsets(week_start, config)
    if fish_day in off:
        raise PlanError(f"fish_day {pr['fish_day']} is one of the off_nights")

    # Roast week? (parity A by default, every other week). On a roast week the
    # slow-cooker roast replaces ONE GROUND-BEEF dinner.
    roast_cfg = config.get("roast") or {}
    roast_week = bool(roast_cfg.get("enabled")) and parity == roast_cfg.get("parity", "A")
    beef_n = pr["ground_beef"] - (1 if roast_week else 0)
    rd = None
    if roast_week:
        rd = _offset(week_start, _DOW[roast_cfg.get("night", "wednesday")])
        if rd == fish_day or rd not in cook_days or rd in skip:   # next free cooked night
            rd = next((d for d in cook_days if d != fish_day and d not in skip),
                      next(d for d in cook_days if d != fish_day))

    # ---- lunch coverage (owner 2026-11-11), decided before the dinners -------
    # Each lunch day (lunches.days) is fed by, in order: the roast's Thursday
    # leftovers (roast week), a prep batch (servings_each / people days, the days
    # right after its prep day), or a `lunch-friendly` dinner cooked the night
    # before at leftovers.servings. A day none of these can cover goes to the
    # latest prep before it (+people servings) and is reported as a gap; with
    # no prep before it the day is left unfed (report lunch_unfed).
    lcfg = config["lunches"]
    people = int(lcfg.get("people", 2))
    lunch_days = [d for d in (_offset(week_start, _DOW[n]) for n in
                  (lcfg.get("days") or ["monday", "tuesday", "wednesday", "thursday",
                                        "friday", "saturday"])) if d not in skip]
    prep_names = lcfg.get("prep_days") or [lcfg.get("prep_day", "sunday")]
    all_preps = [_offset(week_start, _DOW[prep_names[i % len(prep_names)]])
                 for i in range(int(lcfg["recipes_per_week"]))]
    first_prep = all_preps[0] if all_preps else 0
    prep_offsets = [p for p in all_preps if p not in skip]   # no prep on a skip day
    covered: dict[int, str] = {}
    roast_lunch_day = None
    if roast_week:
        roast_lunch_day = _offset(week_start, _DOW["thursday"])
        if roast_lunch_day not in skip:
            covered[roast_lunch_day] = "roast"
    per_prep = max(1, int(lcfg["servings_each"]) // people)
    for i, p in enumerate(prep_offsets):
        for d in [d for d in lunch_days if d > p and d not in covered][:per_prep]:
            covered[d] = f"prep{i}"
    lo_cfg = lcfg.get("leftovers") or {}
    lo_on = bool(lo_cfg.get("enabled"))
    lo_servings = int(lo_cfg.get("servings", 4))
    need_lo = [d for d in lunch_days if d not in covered]
    lo_nights = [d - 1 for d in need_lo
                 if lo_on and d - 1 in cook_days and d - 1 not in (fish_day, rd)
                 and d - 1 not in skip]

    # Fish: Sunday. Chicken + steak are fixed counts; ground beef is the week's
    # distinct dishes (no repeat, <= max_asian Asian-style).
    if stock is not None:
        # Fish parity yields to the fish that is in the freezer; neither in stock
        # -> the parity fish as before (bought fresh / Costco fallback).
        other = next((v for v in pr["fish_parity"].values() if v != fish_variant), fish_variant)

        def _fish(v):
            return [r for r in pool if r.get("kind") == "fish" and r.get("season") == season
                    and r.get("variant") == v and _eligible(r, caps, "fish")]
        if not stock.has(_fish(fish_variant), 2) and stock.has(_fish(other), 2):
            fish_variant = other
        stock.fish = fish_variant
    fish = (_pick(pool, "fish", season, caps, rng, used, 1, variant=fish_variant,
                  stock=stock) or [None])[0]
    # Burger night: every Nth ISO week one chicken dinner becomes the fixed
    # "Burger night" recipe (Amana patties). Skipped quietly if not seeded.
    bn = config.get("burger_night") or {}
    burger = None
    if bn.get("enabled") and int(wk.split("W")[1]) % int(bn.get("every_n_weeks", 5)) == 0:
        burger = _by_name(pool, bn.get("recipe", "Burger night"))
    if burger and stock is not None:
        need = stock.need_for(burger, 2)
        if stock.fits(need):
            stock.take(need)
        else:                            # patties short: a chicken dinner instead
            stock.short.append({"slot": "burger_night", "recipe": burger.get("name"),
                                "need": need, "have": {k: stock.remaining(k) for k in need}})
            burger = None

    # Lunch-friendly dinners fill the chicken slots first (a lunch-prep recipe
    # carrying the keyword may be drawn as the dinner), cooked at lo_servings.
    def _lf_pick(n):
        if n <= 0:
            return []
        cands = [r for r in pool if r.get("lunch_friendly") and r.get("season") == season
                 and r.get("kind") in ("chicken", "lunch") and r["id"] not in used
                 and _eligible(r, caps, r["kind"])]
        rng.shuffle(cands)
        chosen = F.choose(cands, n, stock, lo_servings, "chicken/lunch-friendly")
        for r in chosen:
            used.add(r["id"])
        return chosen

    n_chicken = pr["poultry"] - (1 if burger else 0)
    lf = _lf_pick(min(len(lo_nights), n_chicken))
    chicken = lf + _pick(pool, "chicken", season, caps, rng, used, n_chicken - len(lf),
                         stock=stock)
    steak = _scheduled(pool, "steak", season, caps, rng, used, wk, config, stock) or \
        (_pick(_unscheduled(pool, "steak", wk, config), "steak", season, caps, rng, used, 1,
               stock=stock) or [None])[0]
    beef = _pick_beef(pool, season, caps, rng, used, beef_n,
                      beef_cfg.get("max_asian_per_week", 1), stock)
    roast = None
    if roast_week:
        roast = _scheduled(pool, "roast", season, caps, rng, used, wk, config, stock, 4) or \
            (_pick(_unscheduled(pool, "roast", wk, config), "roast", season, caps, rng, used, 1,
                   stock=stock, servings=4) or [None])[0]

    # Flex dinner: alternates pork chop / chicken breast by week (parity picks the
    # rotation slot). A chicken flex is an extra chicken dish not already used.
    flex_cfg = config.get("flex") or {}
    flex = None
    if flex_cfg.get("enabled"):
        rotation = flex_cfg.get("rotation") or ["pork", "chicken"]
        flex_kind = rotation[(0 if parity == "A" else 1) % len(rotation)]
        pick_kind = "pork" if flex_kind == "pork" else "chicken"
        if stock is not None:
            # The week's rotation kind unless the freezer can't cover it and the
            # other kind can (2 pork-chop packages left, chicken plenty: chicken).
            def _kind(k):
                return [r for r in pool if r.get("kind") == k and r.get("season") == season
                        and r["id"] not in used and _eligible(r, caps, k)]
            alt = "chicken" if pick_kind == "pork" else "pork"
            if (not any(stock.fits(stock.need_for(r, 2)) for r in _kind(pick_kind))
                    and stock.has(_kind(alt), 2)):
                pick_kind = alt
        if pick_kind == "chicken" and len(lf) < len(lo_nights):
            more = _lf_pick(1)
            lf += more
            flex = more[0] if more else None
        if flex is None:
            flex = (_pick(pool, pick_kind, season, caps, rng, used, 1, stock=stock) or [None])[0]

    # ---- freezer short: another protein, else no dinner (owner 2026-11-11) ----
    # Each dinner slot the freezer could not cover is tried with the protein
    # ladder (FALLBACK_DEFAULT / freezer.fallback), keeping the week's rules: no
    # repeat (used), steak without the off-week scheduled cuts, ground beef with
    # <= max_asian Asian-style. Nothing fits -> None: that night has no dinner,
    # logged as freezer_short_slot. Without a stock nothing is ever short here.
    fb_cfg = {**FALLBACK_DEFAULT, **((config.get("freezer") or {}).get("fallback") or {})}
    max_asian = beef_cfg.get("max_asian_per_week", 1)

    def _fill(slot, servings=2, day=None):
        kinds = [slot] + [k for k in fb_cfg.get(slot) or [] if k != slot]
        checked = set()
        for k in kinds:
            src = _unscheduled(pool, k, wk, config) if k in ("steak", "roast") else pool
            cands = [r for r in src if r.get("kind") == k and r.get("season") == season
                     and r["id"] not in used and _eligible(r, caps, k)]
            checked |= F.cuts_of(cands, stock, servings)
            if k == slot:
                continue                      # the slot itself was already tried
            rng.shuffle(cands)
            beef_now = [r for r in chicken + beef + [steak, flex, fish, roast]
                        if r is not None and r.get("kind") == "ground_beef"]

            def ok(chosen, r):
                return not (r.get("beef_style") == "asian" and
                            sum(c.get("beef_style") == "asian" for c in beef_now + chosen)
                            >= max_asian)
            got = F.choose(cands, 1, stock, servings, f"{slot}->{k}", ok)
            if got:
                used.add(got[0]["id"])
                stock.fallbacks.append({"slot": slot, "planned": k, "recipe": got[0]["name"]})
                return got[0]
        stock.short_slots.append({
            "slot": slot, "day": None if day is None else _dayname(week_start, day),
            "checked": sorted(checked),
            "counts": {c: stock.remaining(c) for c in sorted(checked)}})
        return None

    if stock is not None:
        if fish is None:
            fish = _fill("fish", day=fish_day)
        while len(chicken) < n_chicken:
            r = _fill("chicken")
            if r is None:
                break
            chicken.append(r)
        if steak is None:
            steak = _fill("steak")
        while len(beef) < beef_n:
            r = _fill("ground_beef")
            if r is None:
                break
            beef.append(r)
        if roast_week and roast is None:
            roast = _fill("roast", servings=4, day=rd)
        if flex_cfg.get("enabled") and flex is None:
            flex = _fill(pick_kind)
    holes = ((n_chicken - len(chicken)) + (1 if steak is None else 0)
             + (beef_n - len(beef)) + (1 if flex_cfg.get("enabled") and flex is None else 0))

    # Day assignment: fish on its day, roast on roast_night (if a roast week),
    # each lunch-friendly dinner on a night before a leftover lunch day, the rest
    # fill the remaining cooked nights in a deterministic shuffled order.
    mains_by_day = {fish_day: fish}
    if roast_week:
        mains_by_day[rd] = roast
    lo_by_night = dict(zip(lo_nights, lf))
    mains_by_day.update(lo_by_night)
    lf_ids = {id(r) for r in lo_by_night.values()}
    other = [r for r in chicken + beef + ([steak] if steak is not None else [])
             + ([burger] if burger else []) + ([flex] if flex is not None else [])
             if id(r) not in lf_ids] + [None] * holes     # None = no dinner (freezer short)
    rng.shuffle(other)
    days = [d for d in cook_days if d not in mains_by_day]
    if len(other) != len(days) or len(cook_days) != int(pr["dinners_per_week"]):
        raise PlanError(
            f"dinner mix does not fit the week: {len(mains_by_day) + len(other)} mains for "
            f"{len(cook_days)} cooked nights (dinners_per_week {pr['dinners_per_week']}, "
            f"off_nights {pr.get('off_nights') or []})")
    for d, rec in zip(days, other):
        mains_by_day[d] = rec
    if stock is not None:            # name the night each unfilled slot left empty
        empty = iter(d for d in days if mains_by_day[d] is None)
        for s in stock.short_slots:
            d = next(empty, None) if s["day"] is None else None
            if d is not None:
                s["day"] = _dayname(week_start, d)
    # Freezer-short nights and skip_days get no dinner. A main dropped for a skip
    # day gives its cuts back to the scratch stock (the reservation is computed
    # from the planned entries, so this only keeps later picks honest).
    no_dinner = sorted(d for d in cook_days if mains_by_day.get(d) is None)
    for d in sorted(skip & set(cook_days)):
        m = mains_by_day.get(d)
        if m is not None and stock is not None:
            sv = 4 if (roast is not None and m is roast) else lo_servings if d in lo_by_night else 2
            stock.give(stock.need_for(m, sv))
        mains_by_day[d] = None
        lo_by_night.pop(d, None)
    dinner_days = [d for d in cook_days if mains_by_day.get(d) is not None]

    # Leftover days nothing covers -> the latest prep before them (+people each).
    extra = [0] * len(prep_offsets)
    gaps = []
    if roast_week and roast_lunch_day in covered and mains_by_day.get(rd) is None:
        need_lo = sorted(need_lo + [roast_lunch_day])   # no roast cooked: no Thursday leftovers
    unfed = []
    for d in need_lo:
        if d - 1 in lo_by_night:
            continue
        before = [i for i, p in enumerate(prep_offsets) if p < d]
        if not before:
            # No prep before this day (e.g. the Sunday prep is a skip day): a
            # later batch is not cooked yet, so the day stays unfed (logged).
            unfed.append(_dayname(week_start, d))
            continue
        extra[before[-1]] += people
        gaps.append(_dayname(week_start, d))
    if report is not None:
        report["lunch_gaps"] = gaps
        report["lunch_unfed"] = unfed
        report["off_nights"] = [_dayname(week_start, d) for d in sorted(off)]
        report["skip_days"] = [_dayname(week_start, d) for d in sorted(skip)]
        report["no_dinner"] = [_dayname(week_start, d) for d in no_dinner if d not in skip]

    # Sides: one veg side per dinner whose main has no vegetable of its own
    # (sides.skip_when_main_has_veg; W46: the broccoli stir fry got green beans).
    # No repeat when enough distinct sides are available; else the interim rule
    # repeats a side up to sides.max_repeats_per_week (relaxing only if the
    # approved pool is tiny) so every dinner that needs one gets one.
    sides_cfg = config["sides"]
    side_days = [d for d in dinner_days if sides_cfg["per_dinner"]
                 and not (sides_cfg.get("skip_when_main_has_veg", True)
                          and mains_by_day[d].get("veg_foods"))]
    sides = (_pick_sides(pool, season, caps, rng, len(side_days),
                         sides_cfg.get("max_repeats_per_week", 2)) if side_days else [])
    side_for = dict(zip(side_days, sides))
    rice = _rice_recipe(pool, config)

    for d in dinner_days:
        date = week_start + datetime.timedelta(days=d)
        main = mains_by_day[d]
        # The roast dinner is shopped for 4: dinner + Thursday leftover lunch for
        # two; a lunch-friendly dinner for lo_servings (dinner + next-day lunch).
        # The leftover entries themselves add no shopping.
        servings = (4 if (roast is not None and main is roast)
                    else lo_servings if d in lo_by_night else 2)
        entries.append(PlanEntry(date, "dinner", main["id"], main["name"], servings,
                                 "dinner-main", main.get("slot", ""), note("dinner")))
        if d in side_for:
            side = side_for[d]
            entries.append(PlanEntry(date, "dinner", side["id"], side["name"], 2,
                                     "side", side.get("slot", ""), note("side")))
        if rice and main.get("rice"):
            entries.append(PlanEntry(date, "dinner", rice["id"], rice["name"], servings,
                                     "rice", rice.get("slot", ""), note("side")))
        if d in lo_by_night and d + 1 not in skip:
            # Next day's lunch = this dinner's leftovers (role "leftover"; the run
            # job posts it WITHOUT addshopping: the dinner already ordered for it).
            entries.append(PlanEntry(date + datetime.timedelta(days=1), "lunch", main["id"],
                                     main["name"], people, "leftover", main.get("slot", ""),
                                     note(f"leftover (from {date:%A} dinner)")))

    # ---- lunches: N preps at servings_each, one per prep day ---------------
    bsf = lcfg.get("beef_stir_fry") or {}
    beef_lunch = None
    if bsf.get("enabled") and parity == bsf.get("parity", "A"):
        names = {n.lower() for n in bsf.get("recipes") or []}
        cands = [r for r in pool if (r.get("name", "").lower() in names
                                     or "beef-lunch" in (r.get("tags") or ()))
                 and r["id"] not in used]
        rng.shuffle(cands)
        if not prep_offsets or prep_offsets[-1] != all_preps[-1]:
            cands = []                                # its (Thursday) prep day is skipped
        if stock is not None:                         # only a stir fry the freezer covers
            lsv = lcfg["servings_each"]
            cands = [r for r in cands if stock.fits(stock.need_for(r, lsv))]
            if cands:
                stock.take(stock.need_for(cands[0], lsv))
        beef_lunch = cands[0] if cands else None      # none free/seeded: plain lunches
    n_lunch = len(prep_offsets) - (1 if beef_lunch else 0)
    if beef_lunch:
        # Reserve it BEFORE the lunch pick, so a beef lunch that also carries the
        # lunch-prep keyword can't be picked twice (2 distinct batches, not 3).
        used.add(beef_lunch["id"])
    lunches = (_pick(pool, "lunch", season, caps, rng, used, n_lunch, stock=stock,
                     servings=lcfg["servings_each"]) if n_lunch > 0 else [])
    if stock is not None and len(lunches) < n_lunch:
        cands = [r for r in pool if r.get("kind") == "lunch" and r.get("season") == season
                 and r["id"] not in used]
        checked = sorted(F.cuts_of(cands, stock, lcfg["servings_each"]))
        for i in range(len(lunches), n_lunch):        # no prep batch: freezer short
            stock.short_slots.append({"slot": "lunch", "day": _dayname(week_start, prep_offsets[i]),
                                      "checked": checked,
                                      "counts": {c: stock.remaining(c) for c in checked}})
    slots = list(enumerate(lunches))
    if beef_lunch:
        slots.append((len(prep_offsets) - 1, beef_lunch))   # the last (mid-week) batch
    for i, rec in slots:
        p = prep_offsets[i]
        pdate = week_start + datetime.timedelta(days=p)
        lsv = int(lcfg["servings_each"]) + (extra[i] if i < len(extra) else 0)
        label = f"lunch ({pdate:%A} prep, during dinner)"
        entries.append(PlanEntry(pdate, "lunch", rec["id"], rec["name"],
                                 lsv, "lunch", rec.get("slot", ""), note(label)))
        if rice and rec.get("rice"):
            entries.append(PlanEntry(pdate, "lunch", rice["id"], rice["name"],
                                     lsv, "rice", rice.get("slot", ""), note("side")))

    # Roast leftovers -> Thursday lunch (role "leftover"; the run job posts it
    # WITHOUT addshopping, so no extra beef/pantry is ordered for it).
    if roast_week and roast is not None and mains_by_day.get(rd) is roast \
            and roast_lunch_day not in skip:
        tdate = week_start + datetime.timedelta(days=roast_lunch_day)
        rdate = week_start + datetime.timedelta(days=rd)
        entries.append(PlanEntry(tdate, "lunch", roast["id"], roast["name"], people,
                                 "leftover", roast.get("slot", ""),
                                 note(f"leftover (from {rdate:%A} dinner)")))

    # ---- breakfast staples + snack feeders (once a week, first prep day) ---
    prep_date = week_start + datetime.timedelta(days=first_prep)
    for name_key, role in (("staples_recipe", "breakfast"),):
        fixed = _by_name(pool, config["breakfast"][name_key])
        if fixed:
            entries.append(PlanEntry(prep_date, "breakfast", fixed["id"], fixed["name"],
                                     fixed.get("servings", 7), role,
                                     fixed.get("slot", ""), note("breakfast")))
    for key in snack_keys(config):
        fixed = _by_name(pool, config["snacks"][key])
        if fixed:
            # Filed under the Snack meal type (was Breakfast: no Snack type existed).
            entries.append(PlanEntry(prep_date, "snack", fixed["id"], fixed["name"],
                                     fixed.get("servings", 14), "snack",
                                     fixed.get("slot", ""), note("snack")))

    return entries


def snack_keys(config):
    """The snacks config keys planned each week: "Snacks to buy", plus "Snack
    prep" only when snacks.prep_enabled (owner 2026-11-11: off, one entry)."""
    sn = config.get("snacks") or {}
    return ["buy_recipe"] + (["prep_recipe"] if sn.get("prep_enabled", True) else [])


def note_role(note):
    """The planner role from a meal-plan note: "[gp:2026-W47] lunch (Sunday prep,
    during dinner)" -> "lunch". The first word after the week marker; the rest is
    a label for people."""
    rest = (note or "").rsplit("] ", 1)[-1].strip()
    return rest.split(" ", 1)[0] if rest else ""


def _dayname(week_start, d):
    return (week_start + datetime.timedelta(days=d)).strftime("%A").lower()


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


def _scheduled(pool, kind, season, caps, rng, used, wk, config, stock=None, servings=2):
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
            if stock is not None:
                stock.take(stock.need_for(cands[0], servings))
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
        role = note_role(n)
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
