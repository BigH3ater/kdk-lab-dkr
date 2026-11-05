"""Thaw tab: freezer-stock ingredients of the week's meals with a move-to-the-
fridge time from the planner config's `thaw:` table, sorted, tickable, and
reset each plan week. W46-style plan."""
import datetime

import app
import stock

SUN = datetime.date(2026, 11, 8)          # W46 plan week: Sun 11-08 .. Sat 11-14

PLAN = [
    {"date": "2026-11-08", "meal_type": "Dinner", "title": "Pan-Seared Salmon", "recipe_id": 1, "servings": 2},
    {"date": "2026-11-09", "meal_type": "Dinner", "title": "Chicken Fajita Bowls", "recipe_id": 2, "servings": 2},
    {"date": "2026-11-11", "meal_type": "Dinner", "title": "Beef Chili", "recipe_id": 3, "servings": 4},
    {"date": "2026-11-12", "meal_type": "Dinner", "title": "Mississippi Pot Roast", "recipe_id": 4, "servings": 4},
    {"date": "2026-11-10", "meal_type": "Lunch", "title": "Salmon Salad", "recipe_id": 5, "servings": 2},
]
RECIPES = {
    1: {"servings": 2, "ingredients": [{"food": "frozen salmon", "amount": 1, "unit": "lb"},
                                       {"food": "olive oil", "amount": 1, "unit": "tbsp"}]},
    2: {"servings": 4, "ingredients": [{"food": "chicken breast", "amount": 2, "unit": "lb"},
                                       {"food": "cumin", "amount": 1, "unit": "tsp"}]},
    3: {"servings": 4, "ingredients": [{"food": "ground beef", "amount": 1, "unit": "lb"},
                                       {"food": "kidney beans", "amount": 15.5, "unit": "oz"}]},
    4: {"servings": 4, "ingredients": [{"food": "chuck roast", "amount": 3.5, "unit": "lb"},
                                       {"food": "pepperoncini", "amount": 8, "unit": ""}]},
    5: {"servings": 2, "ingredients": [{"food": "salmon", "amount": 0.5, "unit": "lb"}]},
}


def _rows(repo_cfg, today=SUN, done=None):
    lines = app._plan_lines(PLAN, detail=RECIPES.__getitem__)
    return app._thaw_rows(lines, today, done)


def test_lead_table_longest_match(repo_cfg):
    assert stock.thaw_hours("ground beef") == 24
    assert stock.thaw_hours("chuck roast") == 48
    assert stock.thaw_hours("frozen salmon") == 12
    assert stock.thaw_hours("boneless pork chops") == 24
    assert stock.thaw_hours("tri-tip") == 24                  # default


def test_w46_rows_freezer_only_with_move_by(repo_cfg):
    rows = _rows(repo_cfg)
    got = [(r["food"], r["recipe"], r["qty"], r["move_by"]) for r in rows]
    D = datetime.datetime
    assert got == [
        ("frozen salmon", "Pan-Seared Salmon", "1 lb", D(2026, 11, 8, 6)),       # Costco, 12 h
        ("chicken breast", "Chicken Fajita Bowls", "1 lb", D(2026, 11, 8, 18)),  # O'Connell, scaled 2->4
        ("ground beef", "Beef Chili", "1 lb", D(2026, 11, 10, 18)),             # Amana, 24 h
        ("chuck roast", "Mississippi Pot Roast", "3½ lb", D(2026, 11, 10, 18)), # Amana, 48 h
    ]
    # fresh New Pi salmon, Penzeys cumin, pantry olive oil: not freezer stock
    assert {"salmon", "cumin", "olive oil", "kidney beans"}.isdisjoint(r["food"] for r in rows)


def test_rendered_row_and_sorting(repo_cfg):
    html = app._render_thaw(_rows(repo_cfg), SUN)
    assert html.index("frozen salmon") < html.index("chicken breast") < html.index("ground beef")
    assert "Wed 11 · Dinner · Beef Chili" in html
    assert "move to the fridge by Tue 6 PM" in html
    assert "move to the fridge by Sun 6 AM" in html


def test_thaw_today_line(repo_cfg):
    line = app._thaw_today_line(_rows(repo_cfg), SUN)
    assert line == ("Thaw today: 1 lb frozen salmon (Sun Pan-Seared Salmon), "
                    "1 lb chicken breast (Mon Chicken Fajita Bowls)")
    beef = app._thaw_today_line(_rows(repo_cfg, today=datetime.date(2026, 11, 10)),
                                datetime.date(2026, 11, 10))
    assert beef.startswith("Thaw today: 1 lb ground beef (Wed Beef Chili)")


def test_tick_greys_row_and_resets_next_week(repo_cfg, tmp_path):
    path = str(tmp_path / "thaw.json")
    key = next(r["key"] for r in _rows(repo_cfg) if r["food"] == "ground beef")
    sat_key = app._thaw_key("2026-11-15", 9, "ground beef")      # next Sunday's meal
    app._save_thaw({key: "2026-11-11", sat_key: "2026-11-15"}, path)
    done = app._load_thaw(SUN, path=path)
    rows = _rows(repo_cfg, done=done)
    assert [r["done"] for r in rows if r["food"] == "ground beef"] == [True]
    assert "gi thaw done" in app._render_thaw(rows, SUN)
    assert "ground beef" not in app._thaw_today_line(rows, datetime.date(2026, 11, 10))
    # the next plan week (Sun 11-15): last week's ticks are gone, a tick made on
    # Saturday for Sunday's meal is kept
    nxt = app._load_thaw(datetime.date(2026, 11, 15), path=path)
    assert nxt == {sat_key: "2026-11-15"}


def test_thaw_tick_route_toggles(repo_cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(app, "THAW_FILE", str(tmp_path / "d" / "thaw.json"))
    today = datetime.datetime.now(app.TZ).date().isoformat()
    key = app._thaw_key(today, 3, "ground beef")
    c = app.app.test_client()
    r = c.post("/groceries/thaw-tick", data={"key": key})
    assert r.status_code == 303 and r.headers["Location"].endswith("#thaw")
    assert key in app._load_thaw(datetime.date.fromisoformat(today))
    c.post("/groceries/thaw-tick", data={"key": key})
    assert key not in app._load_thaw(datetime.date.fromisoformat(today))
    c.post("/groceries/thaw-tick", data={"key": "junk"})            # ignored, no crash
