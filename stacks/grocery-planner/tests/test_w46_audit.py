"""Week-46 audit regressions (2026-11-05). Fixtures are the live Tandoor
ingredient rows of the W46 plan (recipe ids and strings as read from Tandoor).

Owner trigger: "chili not having a shopping list for kidney beans". The food
"kidney beans" (id 177) was ON HAND in Tandoor, set outside this code, so
Tandoor's meal-plan add left it off; nothing in the planner ever cleared it.
Fresh rosemary (id 142) was the same. The orzo's kalamata olives were stored as
the food "diced", which cleanup.yaml marks ignore_shopping.
"""
import pytest

from grocery import cleanup as C
from grocery import run as R
from grocery import seed as S
from grocery.generator import PlanEntry


@pytest.fixture(scope="module")
def maps():
    return C.Maps(C.load_cleanup())


@pytest.fixture(scope="module")
def foods_cfg():
    return S.load_configs()[1]


def _food(fid, name, onhand=False):
    return {"id": fid, "name": name, "food_onhand": onhand}


def _ing(iid, food, amount, unit, text, note=""):
    return {"id": iid, "food": food, "amount": amount, "unit": {"name": unit} if unit else None,
            "note": note, "is_header": False, "original_text": text}


# Recipe 61 "Beef Chili Recipe" as Tandoor holds it (servings 8, planned for 2).
CHILI = {"id": 61, "name": "Beef Chili Recipe", "servings": 8, "steps": [{"ingredients": [
    _ing(865, _food(11, "ground beef", True), 2, "lb", "2 lbs ground beef"),
    _ing(866, _food(121, "onion"), 1, "medium", "1 medium onion (finely cubed)"),
    _ing(870, _food(522, "can diced tomatoes"), 14.5, "oz", "14.5 oz can diced tomatoes"),
    _ing(871, _food(177, "kidney beans", True), 15.5, "oz",
         "15.5 oz can kidney beans (, drained and rinsed)", ", drained and rinsed; drained and rinsed"),
    _ing(873, _food(119, "cumin", True), 1.5, "tsp", "1 ½ tsp ground cumin"),
    _ing(875, _food(18, "salt", True), 2, "tsp", "2 tsp salt"),
]}]}
# Recipe 49 "Roasted Butternut Squash": fresh rosemary was on hand too.
SQUASH = {"id": 49, "name": "Roasted Butternut Squash", "servings": 4, "steps": [{"ingredients": [
    _ing(729, _food(300, "butternut squash"), 1, "large",
         "1 large butternut squash (about 3 pounds, peeled, seeded, and cut into 1-inch cubes)"),
    _ing(735, _food(142, "rosemary", True), 1, "Tbsp", "1 tablespoon chopped fresh rosemary"),
]}]}


# ---- 1. kidney beans / rosemary: stray on-hand foods -----------------------
@pytest.mark.parametrize("name, stray", [
    ("kidney beans", True),        # W46 chili: on hand, no pantry/stock rule
    ("rosemary", True),            # W46 squash: fresh herb on hand
    ("salt", False),               # pantry (cleanup.yaml)
    ("cumin", False),              # pantry
    ("ground beef", False),        # Amana freezer stock
    ("chicken breast", False),     # O'Connell freezer stock
    ("filet mignon steaks", False),  # Amana (steak)
    ("diced", False),              # not_food (ignore_shopping), not a list line
])
def test_w46_stray_onhand(name, stray, maps, foods_cfg, cfg):
    assert C.is_stray_onhand(name, maps, foods_cfg, cfg["stores"]) is stray


def test_w46_weekly_build_reports_kidney_beans(maps, foods_cfg, cfg):
    entries = [PlanEntry(date=None, meal_type="dinner", recipe_id=61, recipe_name=CHILI["name"],
                         servings=2, role="dinner", slot="", note=""),
               PlanEntry(date=None, meal_type="dinner", recipe_id=49, recipe_name=SQUASH["name"],
                         servings=2, role="side", slot="", note="")]
    got = R.stray_onhand_foods(entries, {61: CHILI, 49: SQUASH},
                               lambda n: C.is_stray_onhand(n, maps, foods_cfg, cfg["stores"]))
    assert got == {"kidney beans": ["Beef Chili Recipe"], "rosemary": ["Roasted Butternut Squash"]}


def test_w46_leftover_meal_not_reported(maps, foods_cfg, cfg):
    entries = [PlanEntry(date=None, meal_type="lunch", recipe_id=61, recipe_name=CHILI["name"],
                         servings=2, role="leftover", slot="", note="")]
    assert R.stray_onhand_foods(entries, {61: CHILI}, lambda n: True) == {}


class FakeTandoor:
    """Just enough of grocery.tandoor.Tandoor for cleanup.run (dry run + apply)."""

    def __init__(self, recipes, foods, cats):
        self.recipes, self.foods, self.cats, self.patches = recipes, foods, cats, []

    def get_all(self, path, **kw):
        return {"/api/automation/": [], "/api/recipe/": [],
                "/api/recipe-book-entry/": [{"book": 1, "recipe": r} for r in self.recipes],
                "/api/food/": list(self.foods.values())}[path]

    def get(self, path):
        return self.recipes[int(path.rstrip("/").split("/")[-1])]

    def supermarket_categories(self):
        return {c: {"id": i, "name": c} for i, c in enumerate(self.cats)}

    def ensure_supermarket_category(self, name):
        return {"id": 99, "name": name}

    def post(self, path, body):
        return {}

    def patch(self, path, body):
        self.patches.append((path, body))


def test_w46_cleanup_apply_unticks_kidney_beans(maps, foods_cfg, cfg):
    cat = lambda n: {"name": S.route_food(n, foods_cfg, cfg["stores"])}  # noqa: E731
    foods = {}
    for r in (CHILI, SQUASH):
        for i in r["steps"][0]["ingredients"]:
            f = dict(i["food"], supermarket_category=cat(i["food"]["name"]))
            foods[f["name"].lower()] = f
    t = FakeTandoor({61: CHILI, 49: SQUASH}, foods, sorted({f["supermarket_category"]["name"]
                                                             for f in foods.values()}))
    C.run(t, C.load_cleanup(), cfg, foods_cfg, {"recipes": []}, apply=True)
    food_patches = {p: b for p, b in t.patches if p.startswith("/api/food/")}
    assert food_patches["/api/food/177/"] == {"food_onhand": False}     # kidney beans back on
    assert food_patches["/api/food/142/"] == {"food_onhand": False}     # rosemary back on
    for fid in (11, 18, 119):                                           # beef, salt, cumin stay
        assert "food_onhand" not in food_patches.get(f"/api/food/{fid}/", {})


# ---- 2. kalamata olives stored as the food "diced" --------------------------
def test_w46_diced_olives_reread(maps):
    ing = _ing(683, _food(400, "diced"), 0.5, "cup", "½ cup diced, pitted kalamata olives")
    body = C.plan_ingredient(ing, maps)
    assert body["food"] == {"name": "kalamata olives"}
    assert body["unit"] == {"name": "cup"}
    assert body["amount"] == pytest.approx(0.5)


@pytest.mark.parametrize("text", [
    "1 yellow onion, finely chopped",          # food before the comma stays the food
    "1 green bell pepper, diced",
])
def test_w46_comma_tail_only_used_for_prep_word_food(maps, text):
    assert C.parse_line(text, maps)["food"] in ("onion", "bell pepper")


# ---- 3. names that split one buy into two lines ------------------------------
@pytest.mark.parametrize("raw, want", [
    ("can diced tomatoes", "diced tomatoes"),  # chili: canned, not fresh tomatoes
    ("Merlot wine", "red wine"),               # filet + fusilli: one bottle
    ("seared ahi tuna", "tuna"),
])
def test_w46_food_aliases(maps, raw, want):
    assert maps.canon_food(raw) == want


def test_w46_chili_tomato_line_reread(maps):
    ing = _ing(870, _food(522, "can diced tomatoes"), 14.5, "oz", "14.5 oz can diced tomatoes")
    assert C.plan_ingredient(ing, maps)["food"] == {"name": "diced tomatoes"}


# ---- 4. routing of the W46 foods ---------------------------------------------
@pytest.mark.parametrize("name, cat", [
    ("kidney beans", "Target/weekly: Canned"),
    ("diced tomatoes", "Target/weekly: Canned"),
    ("can diced tomatoes", "Target/weekly: Canned"),
    ("kalamata olives", "Target/weekly: Canned"),
    ("red wine", "Target/weekly: Pantry & Grains"),
    ("rosemary", "NewPi/weekly: Produce"),
    ("Salt and pepper", "Target/weekly: Oils & Spices"),   # was NewPi produce via "pepper"
    ("minced ginger", "Target/weekly: Condiments"),        # the jar, not fresh ginger
    ("ginger", "NewPi/weekly: Produce"),
    ("bell pepper", "NewPi/weekly: Produce"),
    ("black pepper", "Penzeys/restock: Spices"),
    ("low-sugar beef jerky", "Target/weekly: Snacks"),
    ("butter", "NewPi/weekly: Dairy & Eggs"),
    ("whipping cream", "NewPi/weekly: Dairy & Eggs"),
])
def test_w46_routing(name, cat, foods_cfg, cfg):
    assert S.route_food(name, foods_cfg, cfg["stores"]) == cat


# ---- 5. buy maps for the W46 lines (rule picked by the same longest match) ----
@pytest.mark.parametrize("name, unit", [
    ("kidney beans", "can"), ("can diced tomatoes", "can"), ("beef broth", "carton"),
    ("chicken broth", "carton"), ("marinara sauce", "jar"), ("fusilli", "box"),
    ("whole wheat orzo", "box"), ("red wine", "bottle"), ("white wine", "bottle"),
    ("eggs", "dozen"), ("bell pepper", "each"), ("broccoli", "lb"), ("green beans", "lb"),
    ("cauliflower florets", "head"), ("red cabbage", "head"), ("parsley", "bunch"),
    ("cilantro", "bunch"), ("basil", "bunch"), ("thyme", "bunch"), ("rosemary", "bunch"),
    ("green onions", "bunch"), ("whipping cream", "pint"),
])
def test_w46_buy_units(name, unit, foods_cfg):
    rule = R._food_rule(name, foods_cfg)
    assert (rule.get("buy") or {}).get("unit") == unit, name


@pytest.mark.parametrize("name", ["eggplant", "dried thyme", "dried basil", "garlic powder",
                                  "dill pickles", "black pepper", "sour cream"])
def test_w46_buy_units_not_leaked(name, foods_cfg):
    """A buy map on a short rule must not reach a longer, different product."""
    assert "buy" not in R._food_rule(name, foods_cfg), name
