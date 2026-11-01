import datetime

from grocery import shoplist as S
from grocery import run as R


def _entry(eid, food, cat, onhand=False, amount=1, unit="pcs"):
    return {"id": eid, "amount": amount,
            "unit": {"name": unit} if unit else None, "checked": False,
            "food": {"name": food, "food_onhand": onhand,
                     "supermarket_category": {"name": cat} if cat else None}}


def test_parse_category():
    assert S.parse_category("NewPi/weekly: Produce") == ("NewPi", "weekly", "Produce")
    assert S.parse_category("Target: Pantry") == ("Target", None, "Pantry")
    assert S.parse_category("Costco/bulk: Freezer") == ("Costco", "bulk", "Freezer")
    assert S.parse_category("")[0] is None


def test_split_skips_onhand_and_other_stores(cfg):
    entries = [
        _entry(1, "salmon", "NewPi/weekly: Meat & Seafood"),
        _entry(2, "spinach", "NewPi/weekly: Produce"),
        _entry(3, "orzo", "Target/weekly: Pantry & Grains"),
        _entry(4, "frozen salmon", "Costco/bulk: Freezer"),   # bulk, not on weekly
        _entry(5, "olive oil", "Target/weekly: Oils & Spices", onhand=True),  # on-hand
        _entry(6, "mystery", None),                            # unrouted
    ]
    lists, unrouted, skipped = S.build_store_lists(entries, cfg, "weekly")
    assert set(lists) == {"NewPi", "Target"}
    assert lists["NewPi"].count() == 2
    assert lists["Target"].count() == 1           # orzo only; olive oil on-hand skipped
    assert skipped == 1
    assert len(unrouted) == 1


def test_aisle_ordering(cfg):
    entries = [
        _entry(1, "a", "NewPi/weekly: Bakery"),
        _entry(2, "b", "NewPi/weekly: Produce"),
        _entry(3, "c", "NewPi/weekly: Meat & Seafood"),
    ]
    lists, _, _ = S.build_store_lists(entries, cfg, "weekly")
    aisles = list(lists["NewPi"].aisles.keys())
    # Produce before Meat & Seafood before Bakery, per config aisle order
    assert aisles.index("Produce") < aisles.index("Meat & Seafood") < aisles.index("Bakery")


def test_bulk_mode_selects_costco(cfg):
    entries = [_entry(1, "frozen salmon", "Costco/bulk: Freezer"),
               _entry(2, "spinach", "NewPi/weekly: Produce")]
    lists, _, _ = S.build_store_lists(entries, cfg, "bulk")
    assert set(lists) == {"Costco"}


def test_item_label_and_links():
    import urllib.parse
    it = S.Item(1, "spinach", 2, "bunch", "Produce", "NewPi", "weekly")
    assert it.label() == "2 bunch spinach"
    # Parse the URL and assert the exact hostname + query param (not an
    # `in`-substring host check, which CodeQL flags as incomplete sanitization).
    tu = urllib.parse.urlparse(S.target_search_url("orzo"))
    assert tu.scheme == "https" and tu.hostname == "www.target.com"
    assert urllib.parse.parse_qs(tu.query).get("searchTerm") == ["orzo"]
    nu = urllib.parse.urlparse(S.newpi_search_url("spinach"))
    assert nu.scheme == "https" and nu.hostname == "shop.newpi.coop"
    assert "q=spinach" in nu.fragment      # shop.newpi.coop uses #! hash routing


def test_next_week_window():
    # Thursday 2026-03-05 -> next plan week starts Sunday 2026-03-08
    start, end = R.next_week_window(datetime.date(2026, 3, 5), 6)
    assert start == datetime.date(2026, 3, 8)
    assert end == datetime.date(2026, 3, 14)
    assert start.weekday() == 6
    # TUESDAY build (new schedule) 2026-03-03 -> still the upcoming Sunday 03-08
    tstart, tend = R.next_week_window(datetime.date(2026, 3, 3), 6)
    assert tstart == datetime.date(2026, 3, 8) and tend == datetime.date(2026, 3, 14)


def test_due_is_wednesday_before_the_plan_week(cfg):
    # plan week starts Sunday 2026-03-08 -> order due Wednesday 2026-03-04
    due, rem = R._due_and_reminder(cfg, datetime.date(2026, 3, 8))
    assert due.startswith("2026-03-04T20:00")      # Wednesday 20:00
    assert rem.startswith("2026-03-04T12:00")      # Wednesday noon


def test_bulk_due_first_thursday_of_odd_month(cfg):
    assert R.bulk_due(datetime.date(2026, 1, 1), cfg) is True    # Jan (odd), day 1
    assert R.bulk_due(datetime.date(2026, 1, 15), cfg) is False  # odd month but 2nd+ week
    assert R.bulk_due(datetime.date(2026, 2, 5), cfg) is False   # even month
    assert R.bulk_due(datetime.date(2026, 3, 5), cfg) is True    # Mar (odd), first week


def test_aggregate_ingredients_scales_and_sums():
    class E:
        def __init__(self, rid, servings):
            self.recipe_id, self.servings = rid, servings
    detail = {10: {"servings": 2, "steps": [{"ingredients": [
        {"food": {"id": 1, "name": "onion"}, "unit": {"id": 9, "name": "pcs"}, "amount": 1},
    ]}]}}
    # entry at 4 servings -> scale 2x -> 2 onions
    rows = R.aggregate_ingredients([E(10, 4)], detail)
    assert len(rows) == 1 and rows[0]["amount"] == 2.0
