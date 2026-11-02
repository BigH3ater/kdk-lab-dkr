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
    assert s["lunches"] == 3            # 6 lunch days x 2 people = 3 batches x 4
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
    # the roast dinner is shopped for dinner + the leftover lunch; other dinners for 2
    assert roast_entry.servings == 4
    assert all(x.servings == 2 for x in e if x.role == "dinner-main" and x is not roast_entry)


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


def test_no_repeat_sides_when_pool_is_full(cfg):
    # make_pool has 8 sides (>= 7) -> all 7 distinct, no repeat.
    entries = G.generate_week(make_pool(), SUNDAY, cfg)
    sides = [e.recipe_id for e in entries if e.role == "side"]
    assert len(sides) == 7 and len(set(sides)) == 7
    assert all(e.servings == 4 for e in entries if e.role == "lunch")


def test_interim_side_repeat_when_pool_is_short(cfg):
    # only 3 sides approved -> 7 slots filled by repeating, each <= max_repeats
    # (interim rule relaxes further only if 3*max < 7).
    pool = [r for r in make_pool() if r["kind"] != "side"]
    nxt = max(r["id"] for r in pool) + 1
    for k in range(3):
        pool.append({"id": nxt + k, "name": f"side{k}", "kind": "side", "season": "spring",
                     "variant": None, "beef_style": None, "working_time": 20,
                     "ingredient_count": 6, "servings": 2})
    entries = G.generate_week(pool, SUNDAY, cfg)
    sides = [e.recipe_id for e in entries if e.role == "side"]
    assert len(sides) == 7                 # always fills the week
    assert set(sides) <= {nxt, nxt + 1, nxt + 2}   # only from the short pool


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


def test_beef_stir_fry_lunch_on_roast_weeks_only(cfg):
    pool = make_pool()
    names = cfg["lunches"]["beef_stir_fry"]["recipes"]
    ids = {}
    for n in names:                      # the two stir fries, as ground-beef dinners too
        ids[n] = len(pool) + 1
        pool.append({"id": ids[n], "name": n, "kind": "ground_beef", "season": "spring",
                     "variant": None, "beef_style": "asian", "working_time": 20,
                     "ingredient_count": 6, "servings": 4})
    for parity, sun in _parity_sundays().items():
        e = G.generate_week(pool, sun, cfg)
        lunches = [x for x in e if x.role == "lunch"]
        assert len(lunches) == 3 and all(x.servings == 4 for x in lunches)
        beef_l = [x for x in lunches if x.recipe_id in ids.values()]
        dinners = {x.recipe_id for x in e if x.role == "dinner-main"}
        if parity == cfg["lunches"]["beef_stir_fry"]["parity"]:
            assert len(beef_l) == 1 and beef_l[0].recipe_id not in dinners
        else:
            assert not beef_l


def test_burger_night_replaces_one_chicken_every_5th_week(cfg):
    import datetime as dt
    pool = make_pool()
    pool.append({"id": 999, "name": "Burger night", "kind": "burger", "season": None,
                 "variant": None, "working_time": None, "ingredient_count": None, "servings": 2})
    sun = dt.date(2027, 3, 7)              # spring Sundays (make_pool is spring-only)
    seen_burger = seen_plain = False
    for w in range(10):
        s = sun + dt.timedelta(weeks=w)
        e = G.generate_week(pool, s, cfg)
        mains = [x for x in e if x.role == "dinner-main"]
        assert len(mains) == 7
        burger = [x for x in mains if x.recipe_id == 999]
        n_chicken = sum(1 for x in mains if _kind(pool, x.recipe_id) == "chicken")
        if int(G.week_key(s).split("W")[1]) % cfg["burger_night"]["every_n_weeks"] == 0:
            seen_burger = True
            assert len(burger) == 1 and burger[0].servings == 2
        else:
            seen_plain = True
            assert not burger
        # burger weeks have one chicken dinner fewer than the same-parity plain week
        base = 2 + (0 if _flex_kind(cfg, G.parity_for_week(s)) == "pork" else 1)
        assert n_chicken == base - (1 if burger else 0)
    assert seen_burger and seen_plain


def test_scheduled_short_ribs_and_tri_tip(cfg):
    """Short ribs only on their scheduled roast weeks, tri-tip only on its steak
    weeks; never otherwise (the freezer holds a fixed number of each)."""
    import datetime as dt
    pool = make_pool()
    pool.append({"id": 901, "name": "Short ribs", "kind": "roast", "season": "spring",
                 "variant": None, "working_time": None, "ingredient_count": None,
                 "servings": 5, "tags": ["short-ribs"]})
    pool.append({"id": 902, "name": "Tri-tip fajitas", "kind": "steak", "season": "spring",
                 "variant": None, "working_time": None, "ingredient_count": None,
                 "servings": 4, "tags": ["tri-tip"]})
    rules = {r["tag"]: r for r in cfg["scheduled"]}
    sun = dt.date(2027, 3, 7)
    hits = {"short-ribs": 0, "tri-tip": 0}
    for w in range(13):                                   # spring only (make_pool)
        s = sun + dt.timedelta(weeks=w)
        n = int(G.week_key(s).split("W")[1])
        mains = {x.recipe_id for x in G.generate_week(pool, s, cfg) if x.role == "dinner-main"}
        sr, tt = rules["short-ribs"], rules["tri-tip"]
        roast_week = G.parity_for_week(s) == cfg["roast"]["parity"]
        want_sr = roast_week and n % sr["every_n_weeks"] == sr["offset"]
        want_tt = n % tt["every_n_weeks"] == tt["offset"]
        assert (901 in mains) == want_sr, (s, n)
        assert (902 in mains) == want_tt, (s, n)
        hits["short-ribs"] += want_sr
        hits["tri-tip"] += want_tt
    assert hits["tri-tip"] >= 2


def test_owner_override_lifts_only_time_and_ingredient_caps(cfg):
    from grocery.scoring import evaluate_candidate
    long = {"rating": 4.9, "count": 50, "total_time_min": 65, "site": "x",
            "ingredients": ["1 lb flank steak"], "has_beans_lentils": False,
            "non_olive_fats": []}
    assert evaluate_candidate(dict(long), "steak", cfg).verdict == "rejected"
    assert evaluate_candidate(dict(long, owner_override=True), "steak", cfg).verdict != "rejected"
    radish = dict(long, total_time_min=20, ingredients=["1 lb flank steak", "4 radishes"],
                  owner_override=True, owner_pick=True)
    assert evaluate_candidate(radish, "steak", cfg).verdict == "rejected"   # excluded food still rejects
