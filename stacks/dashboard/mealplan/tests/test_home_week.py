"""The week grid at /meal/ shows the fixed plan week (Sun -> Sat, planner
cadence), past days dimmed but rateable, today highlighted, and next week as a
second row only once the planner has built it (owner decision 2026-11-11)."""
import datetime
import re
import types

import pytest

import app

W46 = datetime.date(2026, 11, 8)        # Sun 11-08 .. Sat 11-14
W47 = datetime.date(2026, 11, 15)       # Sun 11-15 .. Sat 11-21


def _e(date, mt, title, rid, note=""):
    return {"date": date, "meal_type": mt, "title": title, "recipe_id": rid,
            "url": f"https://recipes.example/view/recipe/{rid}", "image": None,
            "servings": 4, "note": note}


THIS = [
    _e("2026-11-08", "Lunch", "Fall Chicken Sheet Pan Dinner", 50, "[gp:2026-W46] lunch"),
    _e("2026-11-08", "Dinner", "Seared Ahi Tuna", 65, "[gp:2026-W46] dinner"),
    _e("2026-11-10", "Dinner", "Chicken and Broccoli Stir Fry", 164, "[gp:2026-W46] dinner"),
    _e("2026-11-11", "Dinner", "Mississippi Pot Roast", 54, "[gp:2026-W46] dinner"),
    _e("2026-11-14", "Dinner", "Pizza Night", 70, "[gp:2026-W46] dinner"),
]
NEXT = [
    _e("2026-11-15", "Lunch", "Mediterranean Chicken and Orzo", 43, "[gp:2026-W47] lunch"),
    _e("2026-11-17", "Dinner", "Beef Tacos", 80, "[gp:2026-W47] dinner"),
]


@pytest.fixture
def home(repo_cfg, monkeypatch):
    """Client with a frozen clock and a fake Tandoor meal plan; returns a
    function today -> (html, fetch calls)."""
    state = {"plan": list(THIS), "calls": []}

    def fake_fetch(a, b):
        state["calls"].append((a, b))
        return [e for e in state["plan"] if a.isoformat() <= e["date"] <= b.isoformat()]

    monkeypatch.setattr(app, "fetch_plan", fake_fetch)
    monkeypatch.setattr(app, "last_ratings", lambda ids: {})
    c = app.app.test_client()

    def get(today):
        class _DT(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(today.year, today.month, today.day, 12, tzinfo=tz)
        shim = types.SimpleNamespace(**{k: getattr(datetime, k) for k in dir(datetime)
                                        if not k.startswith("__")})
        shim.datetime = _DT
        monkeypatch.setattr(app, "datetime", shim)
        try:
            return c.get("/").get_data(as_text=True)
        finally:
            monkeypatch.setattr(app, "datetime", datetime)

    get.state = state
    return get


def _days(html):
    """[(classes, 'Sun 8'), ...] in page order."""
    return re.findall(r'<div class="day([^"]*)"><div class="dow">([^<]+)</div>', html)


def test_week_bounds_is_the_plan_week(repo_cfg):
    for d in range(8, 15):
        assert app._week_bounds(datetime.date(2026, 11, d)) == (W46, W46 + datetime.timedelta(days=6))
    assert app._week_bounds(W47)[0] == W47


def test_midweek_shows_sunday_to_saturday_with_past_days_marked(home):
    html = home(datetime.date(2026, 11, 11))                 # Wednesday
    days = _days(html)
    assert [d for _, d in days] == ["Sun 8", "Mon 9", "Tue 10", "Wed 11", "Thu 12", "Fri 13", "Sat 14"]
    assert [c for c, _ in days] == [" past"] * 3 + [" today"] + [""] * 3
    # past meals stay visible and rateable (that is when a meal gets rated)
    assert "Seared Ahi Tuna" in html and "Chicken and Broccoli Stir Fry" in html
    assert html.count('<form class="rate"') == html.count('<div class="meal">')
    assert "Next week" not in html


def test_lunch_prep_copies_fill_the_week(home):
    html = home(datetime.date(2026, 11, 12))
    # a batch of 4 feeds 4 / lunches.people = 2 days (owner 2026-11-11):
    # Sunday + the Mon and Tue "Lunch prep" copies
    assert html.count("Fall Chicken Sheet Pan Dinner") == 3
    assert html.count(">Lunch prep<") == 2


def test_next_week_row_only_when_entries_exist(home):
    assert "Next week" not in home(datetime.date(2026, 11, 10))  # Tuesday before the build
    home.state["plan"] = THIS + NEXT                          # planner ran Tue 18:03
    html = home(datetime.date(2026, 11, 11))
    assert "Next week · Nov 15 – Nov 21" in html and "This week" in html
    days = _days(html)
    assert len(days) == 14
    assert [d for _, d in days[7:]] == ["Sun 15", "Mon 16", "Tue 17", "Wed 18", "Thu 19", "Fri 20", "Sat 21"]
    assert all(c == "" for c, _ in days[7:])
    nxt = html[html.index("Next week"):]
    assert "Beef Tacos" in nxt and nxt.count("Mediterranean Chicken and Orzo") == 3   # W47 prep + 2 copies
    assert "Mediterranean Chicken and Orzo" not in html[:html.index("Next week")]
    assert "Beef Tacos" not in html[:html.index("Next week")]
    # one Tandoor read covers both weeks
    assert home.state["calls"][-1] == (W46, W47 + datetime.timedelta(days=6))


def test_sunday_shows_the_new_week(home):
    home.state["plan"] = THIS + NEXT
    html = home(W47)
    days = _days(html)
    assert [d for _, d in days] == ["Sun 15", "Mon 16", "Tue 17", "Wed 18", "Thu 19", "Fri 20", "Sat 21"]
    assert days[0][0] == " today" and all(c == "" for c, _ in days[1:])
    assert "Beef Tacos" in html and "Seared Ahi Tuna" not in html and "Next week" not in html


def test_empty_this_week_but_next_built_shows_both_rows(home):
    home.state["plan"] = list(NEXT)
    html = home(datetime.date(2026, 11, 13))
    assert "No meals planned" not in html and "Next week" in html and "Beef Tacos" in html


def test_nothing_planned_shows_the_empty_message(home):
    home.state["plan"] = []
    assert "No meals planned for this week yet." in home(datetime.date(2026, 11, 11))


def test_tandoor_down_keeps_the_fallback(home, monkeypatch):
    def boom(a, b):
        raise RuntimeError("down")
    monkeypatch.setattr(app, "fetch_plan", boom)
    assert "Couldn't load the meal plan right now" in home(datetime.date(2026, 11, 11))


def test_phone_width_stacks_one_column():
    assert re.search(r"@media\(max-width:560px\)\{\.week\{grid-template-columns:1fr\}", app.PAGE_CSS)
    assert ".day.past{" in app.PAGE_CSS and ".day.today{" in app.PAGE_CSS
