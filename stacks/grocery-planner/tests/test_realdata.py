"""The anti-regression test Karen asked for: feed the REAL candidate files
through the generator for BOTH parities in EVERY season. This would have caught
the shipping blocker (no season could build a week)."""
import datetime
import os

import yaml
import pytest

from grocery import generator as G
from grocery.candidates import (load_candidate_files, score_all,
                                pool_from_selected)

STACK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _real(cfg):
    cf = load_candidate_files(os.path.join(STACK, "recipes", "candidates"))
    return score_all(cf, cfg)


def _fixed_pool(start_id):
    fixed = yaml.safe_load(open(os.path.join(STACK, "recipes", "fixed.yaml")))
    pool, i = [], start_id
    for r in fixed["recipes"]:
        kind = "staple" if "breakfast" in (r.get("keywords") or []) else "snack"
        pool.append({"id": i, "name": r["name"], "kind": kind, "season": None,
                     "variant": None, "working_time": None,
                     "ingredient_count": None, "servings": r.get("servings", 7)})
        i += 1
    return pool


def _two_parity_sundays(season, cfg):
    """A Sunday in the season and the next Sunday (consecutive Sundays always
    flip ISO-week parity), so both A and B weeks are exercised."""
    month = cfg["seasons"][season][0]
    d = datetime.date(2026, month, 1)
    while d.weekday() != 6:               # first Sunday of the season's first month
        d += datetime.timedelta(days=1)
    return d, d + datetime.timedelta(days=7)


@pytest.mark.parametrize("season", ["spring", "summer", "fall", "winter"])
def test_real_candidates_build_a_week_both_parities(cfg, season):
    scored = _real(cfg)
    pool = pool_from_selected(scored[season], season)
    pool += _fixed_pool(start_id=10_000)
    kind = {r["id"]: r["kind"] for r in pool}
    d1, d2 = _two_parity_sundays(season, cfg)
    assert G.parity_for_week(d1) != G.parity_for_week(d2)   # sanity: opposite parities
    for start in (d1, d2):
        entries = G.generate_week(pool, start, cfg)          # must NOT raise PlanError
        s = G.plan_summary(entries)
        assert s["dinners"] == 7 and s["sides"] == 7 and s["lunches"] == cfg["lunches"]["recipes_per_week"]
        assert s["breakfast"] == 1 and s["snacks"] == 2      # fixed feeders placed
        mains = [kind.get(e.recipe_id) for e in entries if e.role == "dinner-main"]
        parity = G.parity_for_week(start)
        roast_week = cfg["roast"]["enabled"] and parity == cfg["roast"]["parity"]
        rot = cfg["flex"]["rotation"]
        flex_kind = rot[(0 if parity == "A" else 1) % len(rot)]
        # mix: fish 1, steak 1, flex 1 (pork or extra chicken), ground beef 2
        # (1 on a roast week), roast 0/1.
        assert mains.count("fish") == 1
        assert mains.count("steak") == 1
        assert mains.count("roast") == (1 if roast_week else 0)
        assert mains.count("ground_beef") == (1 if roast_week else 2)
        assert mains.count("pork") == (1 if flex_kind == "pork" else 0)
        assert mains.count("chicken") == 2 + (0 if flex_kind == "pork" else 1)
        assert s["leftovers"] == (1 if roast_week else 0)


def test_every_season_meets_generator_minimums(cfg):
    """The preflight contract: no season is short of the minimums."""
    from grocery.candidates import coverage_shortfalls
    assert coverage_shortfalls(_real(cfg)) == {}
