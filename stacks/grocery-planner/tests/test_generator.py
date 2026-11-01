import datetime

import pytest

from grocery import generator as G


def make_pool(season="spring", wt=20, ic=6):
    pool = []
    i = 0

    def add(kind, variant=None, style=None, name=None, servings=2):
        nonlocal i
        i += 1
        pool.append({"id": i, "name": name or f"{kind}-{i}", "kind": kind,
                     "season": season, "variant": variant, "beef_style": style,
                     "working_time": wt, "ingredient_count": ic, "servings": servings})
        return i

    add("fish", "salmon"); add("fish", "tuna")
    for _ in range(5):
        add("chicken")
    for st in ("comfort", "tex-mex", "asian", "italian"):   # ground beef, styles
        add("ground_beef", style=st)
    for _ in range(3):
        add("pork")
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


def _parity_sundays():
    """Return {parity: a Sunday with that parity} using two consecutive Sundays."""
    d = datetime.date(2026, 3, 1)
    return {G.parity_for_week(d): d,
            G.parity_for_week(d + datetime.timedelta(7)): d + datetime.timedelta(7)}


def _flex_kind(cfg, parity):
    rot = cfg["flex"]["rotation"]
    return rot[(0 if parity == "A" else 1) % len(rot)]


def test_counts_and_fish_on_sunday(cfg):
    pool = make_pool()
    s = G.plan_summary(G.generate_week(pool, SUNDAY, cfg))
    assert s["dinners"] == 7
    assert s["sides"] == 7
    assert s["lunches"] == 2
    assert s["breakfast"] == 1 and s["snacks"] == 2
    fish = [e for e in G.generate_week(pool, SUNDAY, cfg)
            if e.role == "dinner-main" and _kind(pool, e.recipe_id) == "fish"][0]
    assert fish.date == SUNDAY


def test_new_mix_both_parities(cfg):
    pool = make_pool()
    for parity, sun in _parity_sundays().items():
        mains = [e for e in G.generate_week(pool, sun, cfg) if e.role == "dinner-main"]
        kinds = [_kind(pool, e.recipe_id) for e in mains]
        roast_week = cfg["roast"]["enabled"] and parity == cfg["roast"]["parity"]
        flex_kind = _flex_kind(cfg, parity)
        assert len(mains) == 7
        assert kinds.count("fish") == 1
        assert kinds.count("steak") == 1
        assert kinds.count("roast") == (1 if roast_week else 0)
        assert kinds.count("ground_beef") == (1 if roast_week else 2)
        exp_pork = 1 if flex_kind == "pork" else 0
        assert kinds.count("pork") == exp_pork
        # chicken = 2 regular + (1 if the flex is chicken)
        assert kinds.count("chicken") == 2 + (0 if flex_kind == "pork" else 1)


def test_roast_replaces_a_beef_and_leftover_thursday(cfg):
    pool = make_pool()
    weeks = _parity_sundays()
    roast_sun = weeks[cfg["roast"]["parity"]]
    e = G.generate_week(pool, roast_sun, cfg)
    kinds = [_kind(pool, x.recipe_id) for x in e if x.role == "dinner-main"]
    assert kinds.count("roast") == 1 and kinds.count("ground_beef") == 1
    roast_entry = [x for x in e if x.role == "dinner-main" and _kind(pool, x.recipe_id) == "roast"][0]
    assert roast_entry.date.weekday() == 2            # Wednesday
    lo = [x for x in e if x.role == "leftover"]
    assert len(lo) == 1 and lo[0].date.weekday() == 3 and lo[0].recipe_id == roast_entry.recipe_id


def test_flex_alternates_pork_and_chicken(cfg):
    pool = make_pool()
    for parity, sun in _parity_sundays().items():
        kinds = [_kind(pool, e.recipe_id) for e in G.generate_week(pool, sun, cfg)
                 if e.role == "dinner-main"]
        if _flex_kind(cfg, parity) == "pork":
            assert kinds.count("pork") == 1
        else:
            assert kinds.count("pork") == 0 and kinds.count("chicken") == 3


def test_at_most_one_asian_beef(cfg):
    # three asian beef, nothing else -> still only 1 asian can be chosen -> fails
    pool = [r for r in make_pool() if r["kind"] != "ground_beef"]
    nxt = max(r["id"] for r in pool) + 1
    for k in range(3):
        pool.append({"id": nxt + k, "name": f"gb{k}", "kind": "ground_beef",
                     "season": "spring", "variant": None, "beef_style": "asian",
                     "working_time": 20, "ingredient_count": 6, "servings": 2})
    with pytest.raises(G.PlanError):          # need 2 beef, only 1 may be asian
        G.generate_week(pool, _parity_sundays()["B"], cfg)   # B = non-roast, needs 2 beef


def test_fish_variant_by_parity(cfg):
    pool = make_pool()
    for parity, sun in _parity_sundays().items():
        fish = [e for e in G.generate_week(pool, sun, cfg)
                if e.role == "dinner-main" and _kind(pool, e.recipe_id) == "fish"][0]
        assert _variant(pool, fish.recipe_id) == cfg["proteins"]["fish_parity"][parity]


def test_no_repeat_sides_and_lunch_servings(cfg):
    entries = G.generate_week(make_pool(), SUNDAY, cfg)
    sides = [e.recipe_id for e in entries if e.role == "side"]
    assert len(sides) == len(set(sides))
    assert all(e.servings == 4 for e in entries if e.role == "lunch")


def test_deterministic_per_week(cfg):
    pool = make_pool()
    a = [(e.date, e.recipe_id, e.role) for e in G.generate_week(pool, SUNDAY, cfg)]
    b = [(e.date, e.recipe_id, e.role) for e in G.generate_week(pool, SUNDAY, cfg)]
    assert a == b


def test_fail_closed_when_not_enough(cfg):
    pool = [r for r in make_pool() if r["kind"] != "ground_beef"]   # no beef at all
    with pytest.raises(G.PlanError):
        G.generate_week(pool, SUNDAY, cfg)


def test_caps_reject_over_time(cfg):
    with pytest.raises(G.PlanError):
        G.generate_week(make_pool(wt=999), SUNDAY, cfg)


def test_season_by_date(cfg):
    assert G.season_for_date(datetime.date(2026, 4, 15), cfg["seasons"]) == "spring"
    assert G.season_for_date(datetime.date(2026, 7, 15), cfg["seasons"]) == "summer"
    assert G.season_for_date(datetime.date(2026, 10, 15), cfg["seasons"]) == "fall"
    assert G.season_for_date(datetime.date(2026, 1, 15), cfg["seasons"]) == "winter"


def _kind(pool, rid):
    return next(r["kind"] for r in pool if r["id"] == rid)


def _variant(pool, rid):
    return next(r["variant"] for r in pool if r["id"] == rid)
