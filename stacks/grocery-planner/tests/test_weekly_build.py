"""The weekly build pass end to end against a fake Tandoor (owner 2026-11-11
rules): leftover lunches are posted without addshopping, and the `planned`
event carries the 5/2/1 summary and skipped_never_buy."""
import datetime

from grocery import run as R

from test_generator import make_pool


class FakeT:
    def __init__(self):
        self.posts = []

    def get_all(self, path, **kw):
        return []

    def get(self, path):
        return {"id": int(path.rstrip("/").split("/")[-1]), "name": "x", "steps": []}

    def keywords(self):
        return {}

    def meal_types(self):
        return {n: {"id": i, "name": n} for i, n in
                enumerate(("Breakfast", "Lunch", "Dinner", "Snack"), 1)}

    def post(self, path, body):
        self.posts.append((path, body))
        return {"id": len(self.posts)}

    def shopping_list_entries(self):
        food = lambda n: {"name": n, "food_onhand": False,
                          "supermarket_category": {"name": "NewPi/weekly: Produce"}}
        return [{"id": 1, "amount": 2, "unit": None, "checked": False, "list_recipe": 9,
                 "food": food("brussels sprouts")},
                {"id": 2, "amount": 1, "unit": None, "checked": False, "list_recipe": 9,
                 "food": food("broccoli")}]


def test_build_pass(cfg, monkeypatch):
    t = FakeT()
    logs = []
    sun = datetime.date(2026, 3, 1)                   # parity B: two bulk dinners
    monkeypatch.setattr(R, "_tandoor", lambda c: t)
    monkeypatch.setattr(R, "next_week_window", lambda today, wd: (sun, sun + datetime.timedelta(6)))
    monkeypatch.setattr(R, "_week_notes", lambda *a: [])
    monkeypatch.setattr(R, "ensure_setup", lambda *a: [])
    monkeypatch.setattr(R, "build_pool", lambda *a: make_pool())
    monkeypatch.setattr(R, "_fixed_pool", lambda t: [])
    monkeypatch.setattr(R, "load_stock", lambda c: None)
    monkeypatch.setattr(R, "_create_weekly_task", lambda *a: None)
    monkeypatch.setattr(R, "jlog", lambda **kw: logs.append(kw))
    assert R.run_weekly(cfg) == 0
    plans = [b for p, b in t.posts if p == "/api/meal-plan/"]
    leftovers = [b for b in plans if "] leftover" in b["note"]]
    assert len(leftovers) == 2 and not any(b["addshopping"] for b in leftovers)
    assert all(b["addshopping"] for b in plans if b not in leftovers)
    planned = [x for x in logs if x.get("event") == "planned"][0]
    assert planned["skipped_never_buy"] == 1
    assert planned["stores"] == {"NewPi": 1}
    s = planned["summary"]
    assert (s["dinners"], s["lunches"], s["snacks"], s["leftovers"]) == (5, 2, 1, 2)
