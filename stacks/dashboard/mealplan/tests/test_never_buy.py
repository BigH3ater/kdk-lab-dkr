"""Owner 2026-11-11: planner config `never_buy` foods (brussels sprouts,
cabbage, red cabbage) are on no grocery list and show struck through with a
"skip" mark on the kitchen ingredient + step views. The list comes from the
mounted planner config (stock.planner_cfg), never hardcoded here."""
import datetime

import app
import kitchen


def _ing(iid, food, amount, unit, note=""):
    return {"id": iid, "food": food, "amount": amount, "unit": unit, "note": note, "onhand": False}


REC = {"name": "Fall Chicken Sheet Pan Dinner", "servings": 4, "image": None,
       "working_time": 20, "waiting_time": 25,
       "ingredients": [_ing(1, "chicken breast", 1, "lb"), _ing(2, "brussels sprouts", 2, "cup", "halved"),
                       _ing(3, "sweet potato", 1, "")],
       "steps": [{"id": 1, "name": "", "instruction": "Roast it all 25 minutes.", "time": 0,
                  "file_id": None, "ingredients": [0, 1, 2]}]}
ENTRY = {"date": "2026-11-17", "meal_type": "Dinner", "title": REC["name"], "recipe_id": 50,
         "url": None, "image": None, "servings": 4, "note": "[gp:2026-W47] dinner"}


def test_terms_come_from_the_planner_config(repo_cfg):
    assert app._never_buy_terms() == ["brussels sprouts", "cabbage", "red cabbage"]
    assert app._never_buy("Red Cabbage") and app._never_buy("brussels sprout")
    assert not app._never_buy("broccoli")


def test_dish_marks_skip_and_views_strike_it(repo_cfg):
    d = kitchen.make_dish(ENTRY, REC, never_buy=app._never_buy_terms())
    assert [i["food"] for i in d["ingredients"] if i["skip"]] == ["brussels sprouts"]
    html = kitchen.ingredient_groups([d], datetime.date(2026, 11, 17))
    assert "<s class='fname'>brussels sprouts</s>" in html and "pill skip'>skip<" in html
    assert html.count("data-check=") == 2                     # not a checklist row
    page = kitchen.ingredients_page([d], datetime.date(2026, 11, 17), "v", "/kitchen")
    assert "<span data-pulled>0</span> / 2" in page            # only what is pulled
    step = kitchen.steps_page(d, [d], "v", "/kitchen")
    assert "<s>brussels sprouts</s> <span class='pill skip'>skip</span>" in step
    assert "<span>chicken breast</span>" in step


def test_without_terms_nothing_is_skipped():
    d = kitchen.make_dish(ENTRY, REC)
    assert not any(i["skip"] for i in d["ingredients"])


def test_groceries_page_hides_recipe_lines_only(repo_cfg):
    def entry(eid, food, list_recipe):
        return {"id": eid, "checked": False, "list_recipe": list_recipe,
                "food": {"name": food, "food_onhand": False,
                         "supermarket_category": {"name": "NewPi/weekly: Produce"}}}
    assert app._route(entry(1, "brussels sprouts", 9)) is None
    assert app._route(entry(2, "cabbage", None)) == ("NewPi", "Produce")   # a hand add
    assert app._route(entry(3, "broccoli", 9)) == ("NewPi", "Produce")


def test_lunch_card_shows_the_current_prep(repo_cfg):
    sun = datetime.date(2026, 11, 15)
    plan = [
        {"date": "2026-11-15", "meal_type": "Lunch", "title": "Greek Chicken Bowls", "servings": 4,
         "note": "[gp:2026-W47] lunch (Sunday prep, during dinner)"},
        {"date": "2026-11-18", "meal_type": "Lunch", "title": "Chicken Stir Fry", "servings": 2,
         "note": "[gp:2026-W47] leftover (from Tuesday dinner)"},
        {"date": "2026-11-19", "meal_type": "Lunch", "title": "Ground Beef and Potatoes",
         "servings": 4, "note": "[gp:2026-W47] lunch (Thursday prep, during dinner)"},
    ]
    assert kitchen.lunch_prep(plan, sun, sun + datetime.timedelta(days=2))["mains"] == [
        "Greek Chicken Bowls"]
    thu = kitchen.lunch_prep(plan, sun, datetime.date(2026, 11, 19))
    assert thu["mains"] == ["Ground Beef and Potatoes"] and thu["servings"] == 4
    assert thu["prep_day"] == datetime.date(2026, 11, 19)
    assert kitchen.lunch_prep(plan, sun)["mains"] == ["Greek Chicken Bowls"]   # default: week start


def test_leftover_lunch_adds_no_thaw_or_pantry_lines():
    rec = {"servings": 4, "ingredients": [{"food": "chicken breast", "amount": 1, "unit": "lb"}]}
    plan = [dict(ENTRY, servings=4),
            {"date": "2026-11-18", "meal_type": "Lunch", "title": REC["name"], "recipe_id": 50,
             "servings": 2, "note": "[gp:2026-W47] leftover (from Tuesday dinner)"}]
    lines = app._plan_lines(plan, lambda rid: rec)
    assert [(ln["date"], ln["amount"]) for ln in lines] == [("2026-11-17", 1.0)]
