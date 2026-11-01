"""H2 (longest-key store routing), M3 (skip = done without result), and the
approved-store mapping."""
import os

import yaml

from grocery.seed import route_food
from grocery.shoplist import approved_stores
from grocery.vikunja import Vikunja

STACK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _foods():
    return yaml.safe_load(open(os.path.join(STACK, "recipes", "foods.yaml")))


def _route(name, cfg):
    return route_food(name, _foods(), cfg["stores"])


def test_longest_key_routing(cfg):
    # Karen's five: each must route to the specific, not the first-substring, store.
    assert _route("ground beef", cfg) == "Amana/bulk: Freezer Beef"      # not Target
    assert _route("crushed tomatoes", cfg) == "Target/weekly: Canned"    # not NewPi Produce
    assert _route("popcorn", cfg) == "Target/weekly: Snacks"             # not Produce (corn)
    assert _route("olive oil", cfg) == "Target/weekly: Oils & Spices"    # not Canned (oil/olive)
    assert _route("rice cakes", cfg) == "Target/weekly: Snacks"          # not Grains (rice)


def test_beef_and_steak_are_amana(cfg):
    assert _route("sirloin steak", cfg) == "Amana/bulk: Freezer Beef"
    assert _route("chicken breast", cfg) == "NewPi/weekly: Meat & Seafood"
    assert _route("salmon fillet", cfg) == "NewPi/weekly: Meat & Seafood"


def test_fish_steaks_are_not_beef(cfg):
    # Karen pass 2: "steak" (beef, Amana) must not swallow fish sold as steaks.
    for name in ("tuna steak", "Tuna steaks", "ahi tuna steak", "salmon steak"):
        assert _route(name, cfg) == "NewPi/weekly: Meat & Seafood", name
    assert _route("ribeye steak", cfg) == "Amana/bulk: Freezer Beef"


def test_unmatched_food_defaults_shelf_stable(cfg):
    assert _route("flux capacitor", cfg) == "Target/weekly: Other"


def test_outcome_skip_is_done_without_result():
    # done + APPROVALS-RESULT -> that choice
    t = {"done": True, "description": "x <p>APPROVALS-RESULT: newpi</p>"}
    assert Vikunja.outcome(t) == (True, "newpi")
    # done + NO result marker -> skipped (the 3-button limit leaves no Skip button)
    assert Vikunja.outcome({"done": True, "description": "nothing here"}) == (True, "skipped")
    # not done -> pending
    assert Vikunja.outcome({"done": False}) == (False, None)


def test_approved_stores_mapping():
    assert approved_stores("all") == {"NewPi", "Target"}
    assert approved_stores("newpi") == {"NewPi"}
    assert approved_stores("target") == {"Target"}
    assert approved_stores("skipped") == set()
    assert approved_stores(None) == set()
