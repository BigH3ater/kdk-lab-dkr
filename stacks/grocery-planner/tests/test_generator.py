import datetime

import pytest

from grocery import generator as G


def make_pool(season="spring", wt=20, ic=6):
    pool = []
    i = 0

    def add(kind, variant=None, name=None, servings=2):
        nonlocal i
        i += 1
        pool.append({"id": i, "name": name or f"{kind}-{i}", "kind": kind,
                     "season": season, "variant": variant, "working_time": wt,
                     "ingredient_count": ic, "servings": servings})
        return i

    add("fish", "salmon"); add("fish", "tuna")
    for _ in range(5):
        add("chicken")
    add("ground_beef", "greek"); add("ground_beef", "asian")
    add("steak"); add("steak")
    add("roast"); add("roast")
    for _ in range(8):
        add("side")
    for _ in range(3):
        add("lunch")
    add("staple", name="Weekly staples", servings=7)
    add("snack", name="Snacks to buy", servings=14)
    add("snack", name="Snack prep", servings=14)
    return pool


SUNDAY = datetime.date(2026, 3, 1)   # a Sunday in spring


def test_counts_and_fish_on_sunday(cfg):
    pool = make_pool()
    entries = G.generate_week(pool, SUNDAY, cfg)
    s = G.plan_summary(entries)
    assert s["dinners"] == 7
    assert s["sides"] == 7
    assert s["lunches"] == 2
    assert s["breakfast"] == 1       # Weekly staples
    assert s["snacks"] == 2          # Snacks to buy + Snack prep
    fish = [e for e in entries if e.role == "dinner-main"
            and _kind(pool, e.recipe_id) == "fish"][0]
    assert fish.date == SUNDAY


def test_protein_mix(cfg):
    pool = make_pool()
    entries = G.generate_week(pool, SUNDAY, cfg)
    mains = [e for e in entries if e.role == "dinner-main"]
    kinds = [_kind(pool, e.recipe_id) for e in mains]
    roast_week = cfg["roast"]["enabled"] and G.parity_for_week(SUNDAY) == cfg["roast"]["parity"]
    assert len(mains) == 7
    assert kinds.count("fish") == 1
    assert kinds.count("ground_beef") == 1
    assert kinds.count("steak") == 1
    assert kinds.count("roast") == (1 if roast_week else 0)
    assert kinds.count("chicken") == (3 if roast_week else 4)


def test_roast_replaces_a_chicken_on_roast_weeks(cfg):
    pool = make_pool()
    # find a roast-week Sunday and a non-roast-week Sunday (consecutive Sundays
    # flip parity)
    import datetime
    d = datetime.date(2026, 3, 1)
    weeks = {G.parity_for_week(d): d, G.parity_for_week(d + datetime.timedelta(7)):
             d + datetime.timedelta(7)}
    roast_sun = weeks[cfg["roast"]["parity"]]
    other_sun = weeks["B" if cfg["roast"]["parity"] == "A" else "A"]
    # roast week: 3 chicken + roast, a roast on Wednesday, a Thursday leftover lunch
    e = G.generate_week(pool, roast_sun, cfg)
    kinds = [_kind(pool, x.recipe_id) for x in e if x.role == "dinner-main"]
    assert kinds.count("roast") == 1 and kinds.count("chicken") == 3
    roast_entry = [x for x in e if x.role == "dinner-main" and _kind(pool, x.recipe_id) == "roast"][0]
    assert roast_entry.date.weekday() == 2            # Wednesday
    leftovers = [x for x in e if x.role == "leftover"]
    assert len(leftovers) == 1 and leftovers[0].date.weekday() == 3   # Thursday
    assert leftovers[0].recipe_id == roast_entry.recipe_id
    # non-roast week: 4 chicken, no roast, no leftover
    e2 = G.generate_week(pool, other_sun, cfg)
    kinds2 = [_kind(pool, x.recipe_id) for x in e2 if x.role == "dinner-main"]
    assert kinds2.count("roast") == 0 and kinds2.count("chicken") == 4
    assert not [x for x in e2 if x.role == "leftover"]


def test_parity_drives_fish_and_beef_variant(cfg):
    pool = make_pool()
    entries = G.generate_week(pool, SUNDAY, cfg)
    parity = G.parity_for_week(SUNDAY)
    exp_fish = cfg["proteins"]["fish_parity"][parity]
    exp_beef = cfg["proteins"]["beef_parity"][parity]
    fish = [e for e in entries if e.role == "dinner-main" and _kind(pool, e.recipe_id) == "fish"][0]
    beef = [e for e in entries if e.role == "dinner-main" and _kind(pool, e.recipe_id) == "ground_beef"][0]
    assert _variant(pool, fish.recipe_id) == exp_fish
    assert _variant(pool, beef.recipe_id) == exp_beef


def test_no_repeat_sides_and_lunch_servings(cfg):
    pool = make_pool()
    entries = G.generate_week(pool, SUNDAY, cfg)
    sides = [e.recipe_id for e in entries if e.role == "side"]
    assert len(sides) == len(set(sides))      # no repeat within the week
    lunches = [e for e in entries if e.role == "lunch"]
    assert all(e.servings == 4 for e in lunches)


def test_deterministic_per_week(cfg):
    pool = make_pool()
    a = [(e.date, e.recipe_id, e.role) for e in G.generate_week(pool, SUNDAY, cfg)]
    b = [(e.date, e.recipe_id, e.role) for e in G.generate_week(pool, SUNDAY, cfg)]
    assert a == b


def test_fail_closed_when_not_enough(cfg):
    pool = [r for r in make_pool() if r["kind"] != "chicken"]  # no chicken at all
    with pytest.raises(G.PlanError):
        G.generate_week(pool, SUNDAY, cfg)


def test_caps_reject_over_time(cfg):
    pool = make_pool(wt=999)        # everything over the 30-min active cap
    with pytest.raises(G.PlanError):
        G.generate_week(pool, SUNDAY, cfg)


def test_season_by_date(cfg):
    assert G.season_for_date(datetime.date(2026, 4, 15), cfg["seasons"]) == "spring"
    assert G.season_for_date(datetime.date(2026, 7, 15), cfg["seasons"]) == "summer"
    assert G.season_for_date(datetime.date(2026, 10, 15), cfg["seasons"]) == "fall"
    assert G.season_for_date(datetime.date(2026, 1, 15), cfg["seasons"]) == "winter"


def _kind(pool, rid):
    return next(r["kind"] for r in pool if r["id"] == rid)


def _variant(pool, rid):
    return next(r["variant"] for r in pool if r["id"] == rid)
