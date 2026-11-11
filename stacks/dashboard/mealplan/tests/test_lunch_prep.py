"""Lunch prep stays visible on the days it feeds: the planner writes each prep
batch as an entry on its prep day, and the meal page repeats it as "Lunch prep"
on the days after it. A batch feeds servings / lunches.people days, skipping
days with a planner leftover lunch, never past its plan week.

Owner 2026-11-11 shape (W47: Sun 11-15 .. Sat 11-21): a Sunday batch of 4 ->
Mon + Tue, Wed + Thu leftovers of the Tue + Wed dinners, a Thursday batch of 4
-> Fri + Sat. The older W46 shape (two Sunday batches, a Thursday roast
leftover) is kept as the regression case."""
import datetime

import app

SUN = datetime.date(2026, 11, 8)


def _e(date, mt, title, rid, note, servings=4):
    return {"date": date, "meal_type": mt, "title": title, "recipe_id": rid,
            "url": f"https://recipes.example/view/recipe/{rid}", "image": None,
            "servings": servings, "note": note}


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
    _e("2026-11-12", "Lunch", "Mississippi Pot Roast", 54, "[gp:2026-W46] leftover", 2),
]
W46_PREP = ["Brown Rice (microwave packet)", "Fall Chicken Sheet Pan Dinner",
            "Mediterranean Chicken and Orzo"]

W47 = [
    _e("2026-11-15", "Lunch", "Greek Chicken Bowls", 11,
       "[gp:2026-W47] lunch (Sunday prep, during dinner)"),
    _e("2026-11-17", "Dinner", "Chicken and Broccoli Stir Fry Recipe", 164,
       "[gp:2026-W47] dinner"),
    _e("2026-11-18", "Lunch", "Chicken and Broccoli Stir Fry Recipe", 164,
       "[gp:2026-W47] leftover (from Tuesday dinner)", 2),
    _e("2026-11-19", "Lunch", "Mediterranean Chicken and Orzo", 43,
       "[gp:2026-W47] leftover (from Wednesday dinner)", 2),
    _e("2026-11-19", "Lunch", "Ground Beef and Potatoes", 240,
       "[gp:2026-W47] lunch (Thursday prep, during dinner)"),
]


def _lunch_titles(plan, date):
    return sorted(e["title"] for e in plan if e["date"] == date and e["meal_type"] == "Lunch prep")


def test_w47_two_preps_feed_their_own_days(repo_cfg):
    sun = datetime.date(2026, 11, 15)
    out = app.with_lunch_prep(W47, sun, days=7)
    days = [(sun + datetime.timedelta(days=i)).isoformat() for i in range(7)]
    assert {d: _lunch_titles(out, d) for d in days} == {
        "2026-11-15": [],                                   # Sunday: the real entry
        "2026-11-16": ["Greek Chicken Bowls"],
        "2026-11-17": ["Greek Chicken Bowls"],
        "2026-11-18": [],                                   # Tue dinner leftovers
        "2026-11-19": [],                                   # Wed dinner leftovers + Thu prep day
        "2026-11-20": ["Ground Beef and Potatoes"],
        "2026-11-21": ["Ground Beef and Potatoes"],
    }
    assert all(e in out for e in W47)                       # real entries untouched


def test_w47_window_after_the_thursday_prep(repo_cfg, monkeypatch):
    calls = []
    fri = datetime.date(2026, 11, 20)
    sun = datetime.date(2026, 11, 15)

    def fake_fetch(a, b):
        calls.append((a, b))
        return [e for e in W47 if a.isoformat() <= e["date"] <= b.isoformat()]

    monkeypatch.setattr(app, "fetch_plan", fake_fetch)
    out = app.fetch_plan_with_prep(fri, fri + datetime.timedelta(days=6))
    assert calls == [(fri, fri + datetime.timedelta(days=6)), (sun, fri - datetime.timedelta(days=1))]
    assert _lunch_titles(out, "2026-11-20") == ["Ground Beef and Potatoes"]
    assert _lunch_titles(out, "2026-11-21") == ["Ground Beef and Potatoes"]
    assert _lunch_titles(out, "2026-11-22") == []           # next plan week


def test_w46_two_sunday_batches_feed_four_days_around_the_leftover(repo_cfg):
    mon = SUN + datetime.timedelta(days=1)
    out = app.with_lunch_prep(WEEK, mon, prep=PREP, days=7)       # Mon 11-09 .. Sun 11-15
    for d in ("2026-11-09", "2026-11-10", "2026-11-11", "2026-11-13"):   # 8 servings / 2
        assert _lunch_titles(out, d) == W46_PREP
    assert _lunch_titles(out, "2026-11-12") == []                 # Thu: the roast leftover
    assert _lunch_titles(out, "2026-11-14") == []                 # Sat: batches eaten
    assert _lunch_titles(out, "2026-11-15") == []                 # next plan week
    assert all(e in out for e in WEEK)                             # real entries untouched
    copies = [e for e in out if e.get("prep_copy")]
    assert all(e["url"] and e["recipe_id"] for e in copies)       # keep link + rating target


def test_sunday_window_keeps_real_entries_once(repo_cfg):
    out = app.with_lunch_prep(PREP + WEEK, SUN, days=7)
    assert _lunch_titles(out, "2026-11-08") == []                 # prep day: real entries only
    assert len([e for e in out if e["date"] == "2026-11-08"]) == len(PREP)
    assert len(_lunch_titles(out, "2026-11-13")) == 3             # Friday still shows it


def test_dinner_snack_leftover_and_hand_lunches_not_copied(repo_cfg):
    out = app.with_lunch_prep(WEEK, SUN + datetime.timedelta(days=1), prep=PREP)
    titles = {e["title"] for e in out if e.get("prep_copy")}
    assert titles == set(W46_PREP)


def test_window_spanning_two_plan_weeks(repo_cfg):
    nxt = [_e("2026-11-15", "Lunch", "Greek Chicken Bowls", 11, "[gp:2026-W47] lunch")]
    fri = datetime.date(2026, 11, 13)
    out = app.with_lunch_prep(nxt + WEEK, fri, prep=PREP, days=7)  # Fri 11-13 .. Thu 11-19
    assert len(_lunch_titles(out, "2026-11-13")) == 3             # Fri: W46 prep
    assert _lunch_titles(out, "2026-11-15") == []                 # Sun 11-15: W47 real entry
    assert _lunch_titles(out, "2026-11-16") == ["Greek Chicken Bowls"]   # Mon: W47 prep
    assert _lunch_titles(out, "2026-11-18") == []                 # 4 servings = Mon + Tue


def test_fetch_plan_with_prep_fetches_the_weeks_earlier_days(repo_cfg, monkeypatch):
    calls = []

    def fake_fetch(a, b):
        calls.append((a, b))
        return PREP if a == SUN else WEEK

    monkeypatch.setattr(app, "fetch_plan", fake_fetch)
    tue = SUN + datetime.timedelta(days=2)
    out = app.fetch_plan_with_prep(tue, tue + datetime.timedelta(days=6))
    assert calls == [(tue, tue + datetime.timedelta(days=6)), (SUN, tue - datetime.timedelta(days=1))]
    assert len(_lunch_titles(out, "2026-11-10")) == 3
    calls.clear()
    app.fetch_plan_with_prep(SUN, SUN + datetime.timedelta(days=6))
    assert calls == [(SUN, SUN + datetime.timedelta(days=6))]    # Sunday: one fetch
