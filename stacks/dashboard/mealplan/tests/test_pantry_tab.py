"""Pantry tab: the cleanup.yaml pantry list + stock-store foods grouped by the
store/aisle they route to, "Used this week" from the planned recipes, and the
reorder button (ONE recipe-less entry, buy unit, duplicate guard, on hand kept)."""
import datetime

import pytest

import app
import newpi
import stock

TODAY = datetime.datetime.now(app.TZ).date()


def _plan():
    d = TODAY.isoformat()
    return [{"date": d, "meal_type": "Dinner", "title": "Beef Chili", "recipe_id": 11,
             "url": None, "image": None, "servings": 4},
            {"date": d, "meal_type": "Lunch", "title": "Chicken Fajita Bowls", "recipe_id": 12,
             "url": None, "image": None, "servings": 4}]


RECIPES = {
    11: {"servings": 4, "ingredients": [
        {"food": "ground beef", "amount": 1, "unit": "lb"},
        {"food": "kidney beans", "amount": 15.5, "unit": "oz"},
        {"food": "chili powder", "amount": 2, "unit": "tbsp"},
        {"food": "cumin", "amount": 1, "unit": "tsp"}]},
    12: {"servings": 4, "ingredients": [
        {"food": "chicken breast", "amount": 1, "unit": "lb"},
        {"food": "cumin", "amount": 1, "unit": "tsp"},
        {"food": "olive oil", "amount": 1, "unit": "tbsp"},
        {"food": "bell pepper", "amount": 2, "unit": ""}]},
}


@pytest.fixture
def fake(monkeypatch, repo_cfg, tmp_path):
    state = {"entries": [], "writes": []}

    def write(method, path, body):
        state["writes"].append((method, path, body))
        state["entries"].append({"id": 900 + len(state["entries"]), "checked": False,
                                 "list_recipe": None, "amount": body["amount"],
                                 "food": {"name": body["food"]["name"], "food_onhand": True}})
        return True

    monkeypatch.setattr(app, "_fetch_shopping", lambda: state["entries"])
    monkeypatch.setattr(app, "_api_write", write)
    monkeypatch.setattr(app, "fetch_plan", lambda a, b: _plan())
    monkeypatch.setattr(app, "_recipe_detail", lambda rid: RECIPES[rid])
    monkeypatch.setattr(app, "THAW_FILE", str(tmp_path / "thaw.json"))
    return state


def test_catalog_groups_pantry_and_stock_foods_by_route(repo_cfg):
    cat = stock.catalog()
    assert "olive oil" in cat["Target"]["Oils & Spices"]
    assert "cumin" in cat["Penzeys"]["Spices"]
    assert "ground beef" in cat["Amana"]["Freezer Beef"]          # stock-store rule
    assert "chicken breast" in cat["OConnell"]["Freezer"]
    # every cleanup.yaml pantry food is listed exactly once
    listed = [f for aisles in cat.values() for foods in aisles.values() for f in foods]
    for food in stock.pantry_names():
        assert listed.count(food) == 1, food
    # store order follows config stores.order
    assert list(cat).index("Target") < list(cat).index("Penzeys")


def test_used_this_week_lists_pantry_and_stock_with_recipes(repo_cfg):
    lines = app._plan_lines(_plan(), detail=RECIPES.__getitem__)
    used = app._used_this_week(lines, stock.plan_week_start(TODAY))
    by = {u["food"]: u["recipes"] for u in used}
    assert by["cumin"] == ["Beef Chili", "Chicken Fajita Bowls"]
    assert by["chili powder"] == ["Beef Chili"]
    assert by["olive oil"] == ["Chicken Fajita Bowls"]
    assert by["ground beef"] == ["Beef Chili"]                     # Amana stock
    assert "kidney beans" not in by and "bell pepper" not in by    # ordinary list foods


def test_page_renders_pantry_tab_with_used_group_first(fake):
    html = app.app.test_client().get("/groceries").get_data(as_text=True)
    tab = html.split("id='pantry'>", 1)[1].split("</section>", 1)[0]
    assert tab.index("Used this week") < tab.index("Target · Oils &amp; Spices")
    used = tab.split("Used this week", 1)[1].split("<div class='aisle'>", 1)[0]
    assert "cumin<span class='src'>Beef Chili, Chicken Fajita Bowls</span>" in used
    assert "Penzeys · Spices" in tab and "Amana · Freezer Beef" in tab
    assert tab.count("action='groceries/reorder'") > 50
    assert "data-tab='pantry'" in html and "data-tab='thaw'" in html


def test_reorder_creates_one_entry_with_the_buy_unit(fake, monkeypatch, repo_cfg):
    # a buy map on the olive-oil rule (first, so it wins the equal-length tie)
    foods = {"rules": [{"match": "olive oil", "category": "Target/weekly: Oils & Spices",
                        "buy": {"unit": "bottle", "cup": 4}}] + repo_cfg["foods"]["rules"]}
    monkeypatch.setattr(newpi, "_cache", foods)
    r = app.app.test_client().post("/groceries/reorder", data={"food": "olive oil"})
    assert r.status_code == 303 and r.headers["Location"].endswith("#pantry")
    assert fake["writes"] == [("POST", "/api/shopping-list-entry/",
                               {"food": {"name": "olive oil"}, "amount": 1, "checked": False,
                                "unit": {"name": "bottle"}})]


def test_reorder_without_buy_map_has_no_unit(fake):
    app.app.test_client().post("/groceries/reorder", data={"food": "cumin"})
    (_, _, body), = fake["writes"]
    assert body == {"food": {"name": "cumin"}, "amount": 1, "checked": False}


def test_reorder_duplicate_guard_and_on_the_list(fake):
    c = app.app.test_client()
    c.post("/groceries/reorder", data={"food": "cumin"})
    c.post("/groceries/reorder", data={"food": "cumin"})
    assert len(fake["writes"]) == 1                     # second press: no new entry
    tab = c.get("/groceries").get_data(as_text=True).split("id='pantry'>", 1)[1]
    row = tab.split(">cumin<", 1)[1].split("</div>", 1)[0]
    assert "on the list" in row and "reorder</button>" not in row
    # ticked (checked) entry clears it -> the button is back
    fake["entries"][0]["checked"] = True
    tab = c.get("/groceries").get_data(as_text=True).split("id='pantry'>", 1)[1]
    assert "reorder</button>" in tab.split(">cumin<", 1)[1].split("</div>", 1)[0]


def test_reorder_leaves_on_hand_alone_and_shows_on_store_tab(fake):
    app.app.test_client().post("/groceries/reorder", data={"food": "cumin"})
    # only the shopping-list POST: no PATCH to /api/food/ (on hand untouched)
    assert [(m, p) for m, p, _ in fake["writes"]] == [("POST", "/api/shopping-list-entry/")]
    # the recipe-less entry for an on-hand food is not hidden on the store tabs
    e = dict(fake["entries"][0])
    e["food"] = {"name": "olive oil", "food_onhand": True,
                 "supermarket_category": {"name": "Target/weekly: Oils & Spices"}}
    recipe_line = dict(e, id=1, list_recipe=36)
    stores, manual = app._split_groceries([e, recipe_line])
    assert [x["id"] for x in stores["Target"]["Oils & Spices"]] == [e["id"]]


def test_reorder_refuses_non_pantry_food(fake):
    app.app.test_client().post("/groceries/reorder", data={"food": "kidney beans"})
    assert fake["writes"] == []
