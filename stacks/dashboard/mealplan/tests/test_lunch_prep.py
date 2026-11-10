"""Lunch prep stays visible Mon-Sat: the planner writes the week's prep lunches
as Sunday entries, and the meal page now repeats them as "Lunch prep" on each
later day of that plan week (W46 shape: Sun 11-08 .. Sat 11-14)."""
import datetime

import app

SUN = datetime.date(2026, 11, 8)


def _e(date, mt, title, rid, note):
    return {"date": date, "meal_type": mt, "title": title, "recipe_id": rid,
            "url": f"https://recipes.example/view/recipe/{rid}", "image": None,
            "servings": 4, "note": note}


PREP = [
    _e("2026-11-08", "Lunch", "Fall Chicken Sheet Pan Dinner", 50, "[gp:2026-W46] lunch"),
    _e("2026-11-08", "Lunch", "Mediterranean Chicken and Orzo", 43, "[gp:2026-W46] lunch"),
    _e("2026-11-08", "Lunch", "Brown Rice (microwave packet)", 900, "[gp:2026-W46] side"),
    _e("2026-11-08", "Dinner", "Seared Ahi Tuna", 65, "[gp:2026-W46] dinner"),
    _e("2026-11-08", "Snack", "Snacks to buy", 40, "[gp:2026-W46] snack"),
    _e("2026-11-08", "Lunch", "Leftover pizza", 7, ""),                  # hand-added, not prep
]
WEEK = [
    _e("2026-11-09", "Dinner", "Chicken and Broccoli Stir Fry", 164, "[gp:2026-W46] dinner"),
    _e("2026-11-12", "Lunch", "Mississippi Pot Roast", 54, "[gp:2026-W46] leftover"),
]


def _lunch_titles(plan, date):
    return sorted(e["title"] for e in plan if e["date"] == date and e["meal_type"] == "Lunch prep")


def test_monday_window_shows_sunday_prep_every_day(repo_cfg):
    mon = SUN + datetime.timedelta(days=1)
    out = app.with_lunch_prep(WEEK, mon, prep=PREP, days=7)       # Mon 11-09 .. Sun 11-15
    want = ["Brown Rice (microwave packet)", "Fall Chicken Sheet Pan Dinner",
            "Mediterranean Chicken and Orzo"]
    for i in range(6):                                             # Mon..Sat
        assert _lunch_titles(out, (mon + datetime.timedelta(days=i)).isoformat()) == want
    assert _lunch_titles(out, "2026-11-15") == []                 # next plan week: not W46's prep
    assert all(e in out for e in WEEK)                             # real entries untouched
    copies = [e for e in out if e.get("prep_copy")]
    assert all(e["url"] and e["recipe_id"] for e in copies)       # keep link + rating target


def test_sunday_window_keeps_real_entries_once(repo_cfg):
    out = app.with_lunch_prep(PREP + WEEK, SUN, days=7)
    assert _lunch_titles(out, "2026-11-08") == []                 # prep day: real entries only
    assert len([e for e in out if e["date"] == "2026-11-08"]) == len(PREP)
    assert len(_lunch_titles(out, "2026-11-14")) == 3             # Saturday still shows it


def test_dinner_snack_leftover_and_hand_lunches_not_copied(repo_cfg):
    out = app.with_lunch_prep(WEEK, SUN + datetime.timedelta(days=1), prep=PREP)
    titles = {e["title"] for e in out if e.get("prep_copy")}
    assert titles == {"Fall Chicken Sheet Pan Dinner", "Mediterranean Chicken and Orzo",
                      "Brown Rice (microwave packet)"}


def test_window_spanning_two_plan_weeks(repo_cfg):
    nxt = [_e("2026-11-15", "Lunch", "Greek Chicken Bowls", 11, "[gp:2026-W47] lunch")]
    fri = datetime.date(2026, 11, 13)
    out = app.with_lunch_prep(nxt, fri, prep=PREP, days=7)        # Fri 11-13 .. Thu 11-19
    assert len(_lunch_titles(out, "2026-11-14")) == 3             # Sat: W46 prep
    assert _lunch_titles(out, "2026-11-15") == []                 # Sun 11-15: W47 real entry
    assert _lunch_titles(out, "2026-11-16") == ["Greek Chicken Bowls"]   # Mon: W47 prep


def test_fetch_plan_with_prep_fetches_prep_day(repo_cfg, monkeypatch):
    calls = []

    def fake_fetch(a, b):
        calls.append((a, b))
        return PREP if a == SUN else WEEK

    monkeypatch.setattr(app, "fetch_plan", fake_fetch)
    tue = SUN + datetime.timedelta(days=2)
    out = app.fetch_plan_with_prep(tue, tue + datetime.timedelta(days=6))
    assert calls == [(tue, tue + datetime.timedelta(days=6)), (SUN, SUN)]
    assert len(_lunch_titles(out, "2026-11-10")) == 3
    calls.clear()
    app.fetch_plan_with_prep(SUN, SUN + datetime.timedelta(days=6))
    assert calls == [(SUN, SUN + datetime.timedelta(days=6))]    # Sunday: one fetch
