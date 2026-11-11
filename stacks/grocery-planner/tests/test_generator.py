import datetime

import pytest

from grocery import generator as G


def make_pool(season="spring", wt=20, ic=6):
    pool = []
    i = 0

    def add(kind, variant=None, style=None, name=None, servings=2, lf=False):
        nonlocal i
        i += 1
        pool.append({"id": i, "name": name or f"{kind}-{i}", "kind": kind,
                     "season": season, "variant": variant, "beef_style": style,
                     "working_time": wt, "ingredient_count": ic, "servings": servings,
                     "lunch_friendly": lf})
        return i

    add("fish", "salmon"); add("fish", "tuna")
    for k in range(5):
        add("chicken", lf=k < 2)            # two bulk (lunch-friendly) chicken dinners
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
    assert s["dinners"] == 5            # owner 2026-11-11: Fri + Sat no dinner
    assert s["sides"] == 5
    assert s["lunches"] == 2            # Sunday + Thursday batches of 4
    assert s["leftovers"] == 2          # Wed + Thu lunch from Tue + Wed dinners
    assert s["breakfast"] == 1 and s["snacks"] == 1
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
        assert len(mains) == 5
        assert kinds.count("fish") == 1
        assert kinds.count("steak") == 1
        assert kinds.count("roast") == (1 if roast_week else 0)
        assert kinds.count("ground_beef") == (0 if roast_week else 1)
        exp_pork = 1 if flex_kind == "pork" else 0
        assert kinds.count("pork") == exp_pork
        # chicken = 1 regular + (1 if the flex is chicken)
        assert kinds.count("chicken") == 1 + (0 if flex_kind == "pork" else 1)
        assert {e.date.weekday() for e in mains} == {6, 0, 1, 2, 3}   # Sun..Thu


def test_roast_replaces_a_beef_and_leftover_thursday(cfg):
    pool = make_pool()
    weeks = _parity_sundays()
    roast_sun = weeks[cfg["roast"]["parity"]]
    e = G.generate_week(pool, roast_sun, cfg)
    kinds = [_kind(pool, x.recipe_id) for x in e if x.role == "dinner-main"]
    assert kinds.count("roast") == 1 and kinds.count("ground_beef") == 0
    roast_entry = [x for x in e if x.role == "dinner-main" and _kind(pool, x.recipe_id) == "roast"][0]
    assert roast_entry.date.weekday() == 2            # Wednesday
    lo = {x.date.weekday(): x for x in e if x.role == "leftover"}
    assert sorted(lo) == [2, 3]                       # Wed (Tue dinner) + Thu (roast)
    assert lo[3].recipe_id == roast_entry.recipe_id
    assert lo[3].note.endswith("leftover (from Wednesday dinner)")
    tue = [x for x in e if x.role == "dinner-main" and x.date.weekday() == 1][0]
    assert lo[2].recipe_id == tue.recipe_id and tue.servings == 4
    # roast + bulk dinner shopped for dinner + the leftover lunch; the rest for 2
    assert roast_entry.servings == 4
    assert all(x.servings == 2 for x in e if x.role == "dinner-main"
               and x is not roast_entry and x is not tue)


def test_flex_alternates_pork_and_chicken(cfg):
    pool = make_pool()
    for parity, sun in _parity_sundays().items():
        kinds = [_kind(pool, e.recipe_id) for e in G.generate_week(pool, sun, cfg)
                 if e.role == "dinner-main"]
        if _flex_kind(cfg, parity) == "pork":
            assert kinds.count("pork") == 1
        else:
            assert kinds.count("pork") == 0 and kinds.count("chicken") == 2


def test_at_most_one_asian_beef(cfg):
    # three asian beef, nothing else -> still only 1 asian can be chosen -> two
    # beef dishes fail (the 5-dinner week needs one; the rule still holds).
    import random
    pool = [r for r in make_pool() if r["kind"] != "ground_beef"]
    nxt = max(r["id"] for r in pool) + 1
    for k in range(3):
        pool.append({"id": nxt + k, "name": f"gb{k}", "kind": "ground_beef",
                     "season": "spring", "variant": None, "beef_style": "asian",
                     "working_time": 20, "ingredient_count": 6, "servings": 2})
    with pytest.raises(G.PlanError):          # need 2 beef, only 1 may be asian
        G._pick_beef(pool, "spring", cfg["caps"], random.Random(1), set(), 2, 1)
    assert len(G._pick_beef(pool, "spring", cfg["caps"], random.Random(1), set(), 1, 1)) == 1


def test_fish_variant_by_parity(cfg):
    pool = make_pool()
    for parity, sun in _parity_sundays().items():
        fish = [e for e in G.generate_week(pool, sun, cfg)
                if e.role == "dinner-main" and _kind(pool, e.recipe_id) == "fish"][0]
        assert _variant(pool, fish.recipe_id) == cfg["proteins"]["fish_parity"][parity]


def test_no_repeat_sides_when_pool_is_full(cfg):
    # make_pool has 8 sides (>= 5) -> all 5 distinct, no repeat.
    entries = G.generate_week(make_pool(), SUNDAY, cfg)
    sides = [e.recipe_id for e in entries if e.role == "side"]
    assert len(sides) == 5 and len(set(sides)) == 5
    assert all(e.servings == 4 for e in entries if e.role == "lunch")


def test_interim_side_repeat_when_pool_is_short(cfg):
    # only 3 sides approved -> 5 slots filled by repeating, each <= max_repeats
    # (interim rule relaxes further only if 3*max < 5).
    pool = [r for r in make_pool() if r["kind"] != "side"]
    nxt = max(r["id"] for r in pool) + 1
    for k in range(3):
        pool.append({"id": nxt + k, "name": f"side{k}", "kind": "side", "season": "spring",
                     "variant": None, "beef_style": None, "working_time": 20,
                     "ingredient_count": 6, "servings": 2})
    entries = G.generate_week(pool, SUNDAY, cfg)
    sides = [e.recipe_id for e in entries if e.role == "side"]
    assert len(sides) == 5                 # always fills the week
    assert len(set(sides)) == 3            # all three used, so something repeats
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
        assert len(lunches) == 2 and all(x.servings == 4 for x in lunches)
        beef_l = [x for x in lunches if x.recipe_id in ids.values()]
        dinners = {x.recipe_id for x in e if x.role == "dinner-main"}
        if parity == cfg["lunches"]["beef_stir_fry"]["parity"]:
            assert len(beef_l) == 1 and beef_l[0].recipe_id not in dinners
            assert beef_l[0].date.weekday() == 3          # the Thursday batch
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
        assert len(mains) == 5
        burger = [x for x in mains if x.recipe_id == 999]
        n_chicken = sum(1 for x in mains if _kind(pool, x.recipe_id) == "chicken")
        if int(G.week_key(s).split("W")[1]) % cfg["burger_night"]["every_n_weeks"] == 0:
            seen_burger = True
            assert len(burger) == 1 and burger[0].servings == 2
        else:
            seen_plain = True
            assert not burger
        # burger weeks have one chicken dinner fewer than the same-parity plain week
        base = 1 + (0 if _flex_kind(cfg, G.parity_for_week(s)) == "pork" else 1)
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


# ---- owner 2026-11-11: 5 dinners, 2 lunch preps, leftovers, one snack ---------
def _lunch_days(entries, people=2):
    """{weekday: source} for Mon..Sat from the plan: a prep covers the next
    servings/people days after its date that have no leftover entry."""
    lo = {e.date.weekday(): "leftover" for e in entries if e.role == "leftover"}
    out = dict(lo)
    order = [0, 1, 2, 3, 4, 5]                       # Mon..Sat (weekday())
    for p in sorted((e for e in entries if e.role == "lunch"), key=lambda e: e.date):
        start = (p.date.weekday() + 1) % 7
        free = [d for d in order[order.index(start):] if d not in out] if start in order else []
        for d in free[:p.servings // people]:
            out[d] = f"prep:{p.date:%a}"
    return out


@pytest.mark.parametrize("parity", ["A", "B"])
def test_every_lunch_day_covered_once_without_overshoot(cfg, parity):
    sun = _parity_sundays()[parity]
    e = G.generate_week(make_pool(), sun, cfg)
    cover = _lunch_days(e)
    assert sorted(cover) == [0, 1, 2, 3, 4, 5]                  # Mon..Sat fed
    preps = [x for x in e if x.role == "lunch"]
    lo = [x for x in e if x.role == "leftover"]
    # 6 lunch days x 2 people = 12 servings, never more
    assert sum(x.servings for x in preps) + sum(x.servings for x in lo) == 12
    assert [x.date.weekday() for x in sorted(preps, key=lambda x: x.date)] == [6, 3]
    sunday = [x for x in preps if x.date == sun][0]
    assert sunday.note.endswith("lunch (Sunday prep, during dinner)")
    assert G.note_role(sunday.note) == "lunch"
    # each dinner leftover is the night-before dinner, cooked at 4
    dinners = {x.date: x for x in e if x.role == "dinner-main"}
    for x in lo:
        d = dinners[x.date - datetime.timedelta(days=1)]
        assert d.recipe_id == x.recipe_id and d.servings == 4 and x.servings == 2


def test_no_dinner_on_off_nights(cfg):
    for sun in _parity_sundays().values():
        e = G.generate_week(make_pool(), sun, cfg)
        assert not [x for x in e if x.meal_type == "dinner" and x.date.weekday() in (4, 5)]


def test_no_lunch_friendly_dinner_goes_to_a_prep(cfg):
    pool = [dict(r, lunch_friendly=False) for r in make_pool()]
    report = {}
    sun = _parity_sundays()["B"]                      # non-roast: Wed + Thu need leftovers
    e = G.generate_week(pool, sun, cfg, report=report)
    assert report["lunch_gaps"] == ["wednesday", "thursday"]
    assert not [x for x in e if x.role == "leftover"]
    preps = sorted((x for x in e if x.role == "lunch"), key=lambda x: x.date)
    assert [x.servings for x in preps] == [8, 4]      # Sunday prep feeds Mon-Thu
    assert all(x.servings == 2 for x in e if x.role == "dinner-main")


def test_lunch_prep_recipe_can_be_the_bulk_dinner(cfg):
    pool = [dict(r, lunch_friendly=False) for r in make_pool()]
    lunch_ids = [r["id"] for r in pool if r["kind"] == "lunch"]
    for r in pool:
        if r["id"] == lunch_ids[0]:
            r["lunch_friendly"] = True
    sun = _parity_sundays()["A"]                      # roast week: one leftover (Wed)
    e = G.generate_week(pool, sun, cfg)
    tue = [x for x in e if x.role == "dinner-main" and x.date.weekday() == 1][0]
    assert tue.recipe_id == lunch_ids[0] and tue.servings == 4
    assert lunch_ids[0] not in {x.recipe_id for x in e if x.role == "lunch"}


def test_snack_prep_is_a_toggle(cfg):
    import copy
    s = G.plan_summary(G.generate_week(make_pool(), SUNDAY, cfg))
    assert s["snacks"] == 1
    c = copy.deepcopy(cfg)
    c["snacks"]["prep_enabled"] = True
    names = [x.recipe_name for x in G.generate_week(make_pool(), SUNDAY, c) if x.role == "snack"]
    assert names == ["Snacks to buy", "Snack prep"]


def test_dinner_mix_must_fit_the_cooked_nights(cfg):
    import copy
    c = copy.deepcopy(cfg)
    c["proteins"]["off_nights"] = ["saturday"]        # 6 nights, 5 mains
    with pytest.raises(G.PlanError):
        G.generate_week(make_pool(), SUNDAY, c)


def test_note_role_reads_the_first_word():
    assert G.note_role("[gp:2026-W47] lunch (Sunday prep, during dinner)") == "lunch"
    assert G.note_role("[gp:2026-W47] leftover (from Tuesday dinner)") == "leftover"
    assert G.note_role("[gp:2026-W46] side") == "side"
    assert G.note_role("") == ""
