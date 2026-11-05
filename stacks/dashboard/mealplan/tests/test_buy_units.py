"""Buy-unit conversion on the groceries page (app._combine + newpi.buy_for).

A food whose foods.yaml rule has a `buy:` map renders ONE store amount with the
raw recipe-unit sum after it; a food with no `buy:` renders exactly as before.
The owner's week-46 lines are the regression cases.
"""
import app
import newpi

FOODS = {"rules": [
    {"match": "garlic", "category": "NewPi/weekly: Produce",
     "buy": {"unit": "head", "clove": 10, "tsp": 10}},
    {"match": "garlic powder", "category": "Penzeys/restock: Spices"},
    {"match": "carrot", "category": "NewPi/weekly: Produce",
     "buy": {"unit": "lb", "cup": 3.5, "each": 8, "oz": 16}},
    {"match": "spinach", "category": "NewPi/weekly: Produce",
     "buy": {"unit": "bag", "cup": 5, "oz": 5}},
    {"match": "mushroom", "category": "NewPi/weekly: Produce",
     "buy": {"unit": "lb", "oz": 16, "cup": 4}},
    {"match": "onion", "category": "NewPi/weekly: Produce",
     "buy": {"unit": "each", "cup": 1}},
    {"match": "kale", "category": "NewPi/weekly: Produce"},
]}


def _entries(food, *parts):
    """[(amount, unit)] -> fake Tandoor shopping entries for one food."""
    return [{"id": i, "food": {"name": food}, "amount": a, "unit": {"name": u} if u else None}
            for i, (a, u) in enumerate(parts)]


def _one(food, *parts):
    items = app._combine(_entries(food, *parts), FOODS)
    assert len(items) == 1
    return items[0]


def test_garlic_tablespoon_joins_the_clove_total():
    # Owner: "garlic  1 Tbsp + 8¾ clove (≈ 1 head)". 1 clove = 1 tsp minced, so
    # 1 Tbsp = 3 cloves and the total is 11¾ cloves -> 2 heads (10 cloves a head).
    it = _one("garlic", (1, "tbsp"), (8.75, "cloves"))
    assert it["qty"] == "2 heads"
    assert it["raw"] == "1 Tbsp + 8¾ clove"


def test_garlic_over_one_head_pluralises():
    it = _one("garlic", (1, "tbsp"), (12, "clove"))
    assert it["qty"] == "2 heads"


def test_carrots_three_fragments_become_pounds():
    # Owner: "carrots  1¾ cup + 1 lb + ¼". 1¾ cup = ½ lb, 1 lb, ¼ carrot = 1/32 lb
    # -> 1.53 lb, rounded UP to the next quarter.
    it = _one("carrots", (1.75, "cup"), (1, "lb"), (0.25, ""))
    assert it["qty"] == "1¾ lb"
    assert it["raw"] == "1¾ cup + 1 lb + ¼"


def test_weight_rounds_up_to_quarter():
    assert _one("mushrooms", (8, "oz"))["qty"] == "½ lb"
    assert _one("mushrooms", (9, "oz"))["qty"] == "¾ lb"
    assert _one("mushrooms", (2, "cup"), (8, "oz"))["qty"] == "1 lb"
    assert _one("carrots", (2, "lb"))["qty"] == "2 lb"         # lb never pluralises


def test_other_units_round_up_to_whole():
    assert _one("baby spinach", (6, "cup"))["qty"] == "2 bags"
    assert _one("baby spinach", (5, "oz"))["qty"] == "1 bag"


def test_each_renders_as_bare_number():
    it = _one("red onion", (1, ""), (1.5, "cup"))
    # 1 onion + 1½ cup chopped (1 a cup) = 2.5 -> 3
    assert it["qty"] == "3"
    assert it["raw"] == "1 + 1½ cup"


def test_unconverted_unit_is_listed_after():
    it = _one("garlic", (10, "clove"), (2, "slice"))
    assert it["qty"] == "1 head + 2 slice"


def test_no_buy_rule_renders_exactly_as_before():
    it = _one("kale", (2, "cup"), (1, "lb"), (3, "bunches"))
    assert it["qty"] == "2 cup + 1 lb + 3 bunch"
    assert it["raw"] == ""
    # longest match: garlic powder's rule has no buy map, so no conversion
    assert _one("garlic powder", (1, "tbsp"))["qty"] == "1 Tbsp"
    # unmatched food
    assert _one("flux capacitor", (2, ""))["qty"] == "2"


def test_row_shows_raw_muted_and_escaped():
    it = _one("garlic", (1, "tbsp"), (8.75, "clove"))
    html = app._grocery_row(it, "tick")
    assert "<span class='qty'>2 heads <span class='raw'>(1 Tbsp + 8¾ clove)</span></span>" in html
    plain = app._grocery_row(_one("kale", (2, "cup")), "tick")
    assert "class='raw'" not in plain


def test_buy_for_normalises_units():
    b = newpi.buy_for("garlic", FOODS)
    assert b == {"unit": "head", "head": 1, "clove": 10, "vol": 10}
    c = newpi.buy_for("carrots", FOODS)
    assert c == {"unit": "lb", "wt": 16, "vol": 3.5 * 48, "each": 8}
    assert newpi.buy_for("kale", FOODS) is None
    assert newpi.buy_for("frozen carrots", FOODS) is None   # frozen veg -> Target
