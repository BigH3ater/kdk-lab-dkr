import pytest

from grocery.cleanup import Maps, automations_plan, load_cleanup, parse_line, plan_ingredient


@pytest.fixture(scope="module")
def maps():
    return Maps(load_cleanup())


def ing(food, unit=None, amount=1, text="", note=""):
    return {"id": 1, "food": {"id": 9, "name": food}, "unit": {"name": unit} if unit else None,
            "amount": amount, "original_text": text, "note": note, "is_header": False}


@pytest.mark.parametrize("text, want", [
    ("2 garlic cloves (finely chopped)", (2, "clove", "garlic")),
    ("▢ ¼ cup extra-virgin olive oil", (0.25, "cup", "olive oil")),
    ("4 - 5 pork chops (, boneless (~ 150-200g/5-7oz each) (Note 1))", (5, None, "pork chops")),
    ("1 green bell pepper", (1, None, "bell pepper")),
    ("250g/ 8oz beef mince ((ground beef) - or chicken)", (250, "g", "ground beef")),
    ("1.5 tbsp / 25g butter (or olive oil)", (1.5, "Tbsp", "butter")),
    ("1½ cups cherry or grape tomatoes", (1.5, "cup", "cherry tomatoes")),
    ("2 large onions (, sliced into rings (brown, yellow or white))", (2, None, "onion")),
    ("Optional: crumbled feta", (0, None, "feta")),
    ("salt and pepper (to taste)", (0, None, "salt and pepper")),
    ("1 yellow onion, finely chopped", (1, None, "onion")),
])
def test_parse_line(maps, text, want):
    p = parse_line(text, maps)
    amt, unit, food = want
    assert p["amount"] == pytest.approx(amt)
    assert p["food"] == food
    if unit is not None or "large" not in text:
        assert p["unit"] == unit or (unit is None and p["unit"] in (None, "large"))


def test_rename_only(maps):
    assert plan_ingredient(ing("minced garlic", "teaspoon"), maps) == {
        "food": {"name": "garlic"}, "unit": {"name": "tsp"}}
    assert plan_ingredient(ing("garlic", "clove"), maps) is None
    assert plan_ingredient(ing("dried oregano", "tsp"), maps) is None


def test_broken_line_is_reread(maps):
    b = plan_ingredient(ing("cloves", "garlic", 3, "3 garlic cloves (minced)"), maps)
    assert b["food"] == {"name": "garlic"} and b["unit"] == {"name": "clove"} and b["amount"] == 3
    b = plan_ingredient(ing("▢ 1 pound carrots", None, 0), maps)
    assert b["food"] == {"name": "carrots"} and b["unit"] == {"name": "lb"} and b["amount"] == 1


def test_automations_cover_every_alias():
    cc = load_cleanup()
    plan = automations_plan(cc)
    names = [p[1] for p in plan]
    assert len(names) == len(set(names))
    assert ("FOOD_ALIAS", "grocery: food minced garlic -> garlic", "minced garlic", "garlic") in plan
    assert all(len(n) <= 128 for n in names)


@pytest.mark.parametrize("text, want", [
    ("1 poblano pepper", (1, "poblano pepper")),
    ("1 roma tomato", (1, "roma tomatoes")),
    ("3 large garlic cloves", (3, "garlic")),
    ("0.25 to ½ teaspoon pepper", (0.5, "black pepper")),
    ('2 x 180 - 200g (6.5 - 7oz) tuna (skinless, boneless). 2.5-3cm / 1-1.2" thick', (2, "tuna")),
    ("1.5 pounds boneless (skinless chicken breasts)", (1.5, "chicken breast")),
    ("2 celery ½ cup minced celery", (0.5, "celery")),
])
def test_parse_line_hard_cases(maps, text, want):
    p = parse_line(text, maps)
    assert (p["amount"], p["food"]) == (pytest.approx(want[0]), want[1])


def test_unknown_unit_is_reread(maps):
    b = plan_ingredient(ing("pepper", "poblano", 1, "1 poblano pepper"), maps)
    assert b["food"] == {"name": "poblano pepper"} and b["unit"] is None
    b = plan_ingredient(ing("garlic cloves", "large", 3), maps)
    assert b["food"] == {"name": "garlic"} and b["unit"] == {"name": "clove"}


def test_second_pass_is_a_no_op(maps):
    # case-only food difference (Tandoor matches names case-insensitively)
    assert plan_ingredient(ing("Salt and pepper", None, 0), maps) is None
    # a re-read that lands on the same food/unit/amount and an existing note
    once = ing("can diced tomatoes", "oz", 14.5, "", note="")
    b = plan_ingredient(once, maps)
    if b:
        again = ing(b.get("food", once["food"])["name"],
                    (b.get("unit") or {}).get("name") if "unit" in b else "oz",
                    b.get("amount", 14.5), "", note=b.get("note", ""))
        assert plan_ingredient(again, maps) is None


def test_broth_routes_to_target_not_meat(cfg):
    from grocery.seed import load_configs, route_food
    _, foods_cfg, _, _ = load_configs()
    for name in ("chicken broth", "chicken stock", "beef broth"):
        assert route_food(name, foods_cfg, cfg["stores"]).startswith("Target/"), name
