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
    assert _route("chuck roast", cfg) == "Amana/bulk: Freezer Beef"


def test_tuna_is_newpi_weekly_salmon_fallback_costco(cfg):
    # Tuna at New Pi is frozen but weekly (not a Costco bulk item); salmon's
    # frozen fallback stays Costco.
    assert _route("tuna steak", cfg) == "NewPi/weekly: Meat & Seafood"
    assert _route("frozen tuna", cfg) == "NewPi/weekly: Meat & Seafood"
    assert _route("ahi", cfg) == "NewPi/weekly: Meat & Seafood"
    assert _route("frozen salmon", cfg) == "Costco/bulk: Freezer"


def test_pork_and_target_comfort_items(cfg):
    assert _route("boneless pork chop", cfg) == "NewPi/weekly: Meat & Seafood"
    assert _route("pork loin chop", cfg) == "NewPi/weekly: Meat & Seafood"
    assert _route("flour tortillas", cfg) == "Target/weekly: Pantry & Grains"
    assert _route("taco shells", cfg) == "Target/weekly: Pantry & Grains"
    assert _route("shredded cheese", cfg) == "Target/weekly: Dairy"
    assert _route("provolone cheese", cfg) == "Target/weekly: Dairy"
    assert _route("velveeta", cfg) == "Target/weekly: Dairy"
    assert _route("jarred pasta sauce", cfg) == "Target/weekly: Canned"
    # fresh produce for the comfort dishes stays at New Pi
    assert _route("green bell pepper", cfg) == "NewPi/weekly: Produce"
    # feta stays fresh at New Pi (longest-match beats the Target cheese rules)
    assert _route("feta", cfg) == "NewPi/weekly: Dairy & Eggs"


def test_breakfast_shakes_route(cfg):
    assert _route("Nurri protein shake", cfg) == "Costco/bulk: Snacks Bulk"
    assert _route("Jocko Fuel protein shake, chocolate", cfg) == "Target/weekly: Snacks"


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


def test_pet_food_excluded_from_lists(cfg):
    from grocery import shoplist as S
    entries = [
        {"id": 1, "amount": 1, "unit": None, "checked": False,
         "food": {"name": "spinach", "food_onhand": False,
                  "supermarket_category": {"name": "NewPi/weekly: Produce"}}},
        {"id": 2, "amount": 1, "unit": None, "checked": False,
         "food": {"name": "dog food", "food_onhand": False,
                  "supermarket_category": {"name": "NewPi/weekly: Produce"}}},
    ]
    lists, unrouted, _ = S.build_store_lists(entries, cfg, "weekly")
    foods = [it.food for sl in lists.values() for items in sl.aisles.values() for it in items]
    foods += [it.food for it in unrouted]
    assert "spinach" in foods and "dog food" not in foods
    assert S.is_excluded("Grain-Free Dog Food", cfg["exclude"]) is True
    assert S.is_excluded("ground beef", cfg["exclude"]) is False


def test_bulk_pack_rounding(cfg):
    import os
    import yaml
    from grocery import shoplist as S
    stack = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    foods = yaml.safe_load(open(os.path.join(stack, "recipes", "foods.yaml")))
    # 7/week * 8 weeks / 12-pack -> ceil(56/12) = 5 packs
    assert S.packs_needed(7, 8, 12) == 5
    assert S.packs_needed(7, 8, 15) == 4          # ceil(56/15)
    assert S.packs_needed(0, 8, 12) == 0
    assert S.packs_needed(7, 8, None) is None
    assert S.food_pack_size("Nurri protein shake", foods) == 12
    assert S.food_pack_size("spinach", foods) is None


def test_approved_stores_mapping():
    assert approved_stores("all") == {"NewPi", "Target"}
    assert approved_stores("newpi") == {"NewPi"}
    assert approved_stores("target") == {"Target"}
    assert approved_stores("skipped") == set()
    assert approved_stores(None) == set()
