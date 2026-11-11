"""Kitchen board (/kitchen) and household page (/home): rendered from fixtures
(plan snapshot, recipe cache, weather/calendar/HA/Vikunja feeds), plus the
ingredient scaling and grouping, the step view, the step-photo proxy's access
control, the night-view clock and the chore tick."""
import datetime
import json
import re
from zoneinfo import ZoneInfo

import pytest

import app
import kitchen

TZ = ZoneInfo("America/Chicago")
NOW = datetime.datetime(2026, 11, 9, 17, 20, tzinfo=TZ)        # Monday, dinner time
SUN = "2026-11-08"
TODAY = "2026-11-09"
TOMORROW = "2026-11-10"
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def _e(date, mt, title, rid, note, servings=2):
    return {"date": date, "meal_type": mt, "title": title, "recipe_id": rid,
            "url": f"https://recipes.example/view/recipe/{rid}",
            "image": f"https://recipes.example/media/recipes/{rid}.jpg",
            "servings": servings, "note": note}


PLAN = [
    _e(SUN, "Lunch", "Fall Chicken Sheet Pan Dinner", 50, "[gp:2026-W46] lunch", 4),
    _e(SUN, "Lunch", "Mediterranean Chicken and Orzo", 43, "[gp:2026-W46] lunch", 4),
    _e(SUN, "Lunch", "Brown Rice (microwave packet)", 900, "[gp:2026-W46] side", 4),
    _e(SUN, "Breakfast", "Weekly staples", 38, "[gp:2026-W46] breakfast", 7),
    _e(SUN, "Snack", "Snacks to buy", 40, "[gp:2026-W46] snack", 21),
    # side listed first on purpose: the main must still lead
    _e(TODAY, "Dinner", "Sautéed Green Beans with Garlic", 48, "[gp:2026-W46] side"),
    _e(TODAY, "Dinner", "Chicken & Broccoli <Stir Fry>", 164, "[gp:2026-W46] dinner"),
    _e(TOMORROW, "Dinner", "Filet Mignon", 46, "[gp:2026-W46] dinner"),
    _e(TOMORROW, "Dinner", "Roasted Butternut Squash", 49, "[gp:2026-W46] side"),
]


def _ing(iid, food, amount, unit, note="", onhand=False):
    return {"id": iid, "food": food, "amount": amount, "unit": unit, "note": note, "onhand": onhand}


RECIPES = {
    164: {"name": "Chicken and Broccoli Stir Fry", "servings": 4, "image": None,
          "working_time": 15, "waiting_time": 15,
          "ingredients": [_ing(1, "chicken broth", 0.66667, "cup"), _ing(2, "soy sauce", 3, "Tbsp", onhand=True),
                          _ing(3, "chicken breast", 1, "lb", 'cut into 3/4" pieces'),
                          _ing(4, "cooking oil", 2, "Tbsp"), _ing(5, "black pepper", 0.25, "tsp")],
          "steps": [
              {"id": 1, "name": "", "instruction": "*Start the rice first.*", "time": 0, "file_id": None, "ingredients": []},
              {"id": 2, "name": "Make the sauce", "instruction": "Whisk the broth and soy sauce.", "time": 0,
               "file_id": 16, "ingredients": [0, 1]},
              {"id": 3, "name": "", "instruction": "Prepare Chicken - season with pepper and stir fry 5 minutes.",
               "time": 0, "file_id": None, "ingredients": [2, 3, 4]},
              {"id": 4, "name": "", "instruction": "Simmer 3 to 4 minutes, then rest 1 hour.", "time": 0,
               "file_id": None, "ingredients": []},
          ]},
    48: {"name": "Green Beans", "servings": 2, "image": None, "working_time": 10, "waiting_time": 0,
         "ingredients": [_ing(10, "green beans", 250, "g", "trimmed"), _ing(11, "olive oil", 1, "Tbsp"),
                         _ing(12, "salt", 1.125, "tsp")],
         "steps": [{"id": 9, "name": "", "instruction": "Saute.", "time": 8, "file_id": 17, "ingredients": [0, 1, 2]}]},
    46: {"name": "Filet", "servings": 2, "image": None, "working_time": 0, "waiting_time": 0,
         "ingredients": [_ing(30, "chicken breast", 1, "lb")], "steps": []},
}

ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:vet@test
DTSTART;TZID=America/Chicago:20261110T093000
DTEND;TZID=America/Chicago:20261110T103000
SUMMARY:Vet <Bear>
END:VEVENT
BEGIN:VEVENT
UID:trash@test
DTSTART;VALUE=DATE:20261029
DTEND;VALUE=DATE:20261030
RRULE:FREQ=WEEKLY;BYDAY=TH
EXDATE;VALUE=DATE:20261119
SUMMARY:Trash and recycling
END:VEVENT
BEGIN:VEVENT
UID:piano@test
DTSTART;TZID=America/Chicago:20261002T160000
DTEND;TZID=America/Chicago:20261002T170000
RRULE:FREQ=WEEKLY;BYDAY=MO
SUMMARY:Piano lesson
END:VEVENT
BEGIN:VEVENT
UID:piano@test
RECURRENCE-ID;TZID=America/Chicago:20261116T160000
DTSTART;TZID=America/Chicago:20261116T180000
DTEND;TZID=America/Chicago:20261116T190000
SUMMARY:Piano lesson (moved)
END:VEVENT
BEGIN:VEVENT
UID:trip@test
DTSTART;VALUE=DATE:20261111
DTEND;VALUE=DATE:20261114
SUMMARY:Trip
END:VEVENT
BEGIN:VEVENT
UID:utc@test
DTSTART:20261110T020000Z
DTEND:20261110T030000Z
SUMMARY:Late call
END:VEVENT
END:VCALENDAR
"""

HA_STATES = {
    "sensor.rr_room_kitchen_cleaned": {"state": "2026-11-09T19:32:13+00:00", "attributes": {"at": "Mon Nov 9 13:32"}},
    "sensor.rr_room_hallway_cleaned": {"state": "2026-11-09T19:32:13+00:00", "attributes": {"at": "Mon Nov 9 13:32"}},
    "sensor.rr_room_garage_entrance_cleaned": {"state": "2026-11-07T19:32:13+00:00", "attributes": {"at": "Sat Nov 7 13:32"}},
    "input_datetime.rr_next_run": {"state": "2026-11-10 08:30:00", "attributes": {}},
    "binary_sensor.mr_slave_water_shortage_2": {"state": "on", "attributes": {}},
    "sensor.rr_vacuum_error": {"state": "no error", "attributes": {"advice": {"title": "Mr. Slave is OK"}}},
    "sensor.mr_slave_side_brush_time_left_2": {"state": "-11085", "attributes": {"friendly_name": "Mr. Slave Side brush time left"}},
    "sensor.mr_slave_filter_time_left_2": {"state": "540000", "attributes": {"friendly_name": "Mr. Slave Filter time left"}},
}

PROJECTS = {4: "Cleaning", 5: "Dogs", 12: "Approvals"}
TASKS = [
    {"id": 1, "title": "Clean fridge <now>", "project_id": 4, "done": False, "due_date": "2026-11-01T17:00:00-06:00"},
    {"id": 2, "title": "Clean stove top", "project_id": 4, "done": False, "due_date": "2026-11-09T17:00:00-06:00"},
    {"id": 3, "title": "Dog baths", "project_id": 5, "done": False, "due_date": "2026-11-14T17:00:00-06:00"},
    {"id": 4, "title": "Approve groceries", "project_id": 12, "done": False, "due_date": "2026-11-09T17:00:00-06:00"},
    {"id": 5, "title": "Furnace filter", "project_id": 4, "done": False, "due_date": "2027-01-09T17:00:00-06:00"},
]


def _wx():
    hours = [f"2026-11-09T{h:02d}:00" for h in range(24)] + [f"2026-11-10T{h:02d}:00" for h in range(24)]
    return {"daily": {"time": [TODAY, TOMORROW], "weather_code": [0, 3],
                      "temperature_2m_max": [51.2, 48], "temperature_2m_min": [31, 30],
                      "precipitation_probability_max": [0, 10]},
            "hourly": {"time": hours, "temperature_2m": [30 + (i % 10) for i in range(48)],
                       "precipitation_probability": [5] * 48, "weather_code": [0] * 48}}


@pytest.fixture
def home(monkeypatch, repo_cfg, tmp_path):
    """The sidecar wired to fixtures only: any real upstream call fails the test."""
    def no_net(*a, **k):
        raise AssertionError("unexpected upstream call")
    monkeypatch.setattr(app.requests, "get", no_net)
    monkeypatch.setattr(app, "_api_get", no_net)
    monkeypatch.setattr(app, "_now", lambda: NOW)
    monkeypatch.setattr(app, "THAW_FILE", str(tmp_path / "thaw.json"))
    snap = {"ts": 0, "day": NOW.date(), "entries": [], "plan": PLAN, "lines": app._plan_lines(PLAN, lambda rid: RECIPES.get(rid, {"servings": 1, "ingredients": []}))}
    monkeypatch.setattr(app, "_grocery_data", lambda force=False: (snap, "cache"))
    monkeypatch.setattr(app, "_recipe", lambda rid: RECIPES[rid])
    import homecal
    app.WX_FEED.set(_wx())
    app.CAL_FEED.set(homecal.expand(ICS, NOW.date(), 9, TZ))
    app.HA_FEED.set(HA_STATES)
    app.TASKS_FEED.set((PROJECTS, TASKS))
    app._PHOTO_CACHE.clear()
    yield app.app.test_client()
    for f in app.HOME_FEEDS:
        f.value, f.ts, f.tried = None, 0.0, 0.0


# ---- scaling + grouping ------------------------------------------------------------
def test_dish_scaled_to_planned_servings():
    d = kitchen.make_dish(PLAN[6], RECIPES[164])            # plan 2, recipe 4 -> half
    qty = {i["food"]: i["qty"] for i in d["ingredients"]}
    assert qty == {"chicken broth": "⅓ cup", "soy sauce": "1½ Tbsp", "chicken breast": "½ lb",
                   "cooking oil": "1 Tbsp", "black pepper": "⅛ tsp"}
    assert d["total_min"] == 30 and d["servings"] == 2 and d["role"] == "dinner"


def test_onhand_from_tandoor_flag_or_pantry_list():
    d = kitchen.make_dish(PLAN[6], RECIPES[164], onhand=lambda f: f == "black pepper")
    on = {i["food"] for i in d["ingredients"] if i["onhand"]}
    assert on == {"soy sauce", "black pepper"}


def test_dinner_main_first_then_side_and_no_prep_copies():
    rows = kitchen.dinner_entries(PLAN + [dict(PLAN[7], date=TODAY, prep_copy=True)], NOW.date())
    assert [e["recipe_id"] for e in rows] == [164, 48]


def test_ingredient_groups_main_then_side():
    dishes = [kitchen.make_dish(e, RECIPES[e["recipe_id"]]) for e in kitchen.dinner_entries(PLAN, NOW.date())]
    html = kitchen.ingredient_groups(dishes, NOW.date())
    assert html.index("Chicken &amp; Broccoli &lt;Stir Fry&gt;") < html.index("Sautéed Green Beans")
    keys = re.findall(r"data-check='([^']+)'", html)
    assert keys[0] == "2026-11-09:164:1" and keys[-1] == "2026-11-09:48:12"
    assert "1⅛ tsp" in html and "<span class='note'>trimmed</span>" in html


def test_lunch_prep_is_this_plan_weeks_prep_day():
    lp = kitchen.lunch_prep(PLAN, datetime.date(2026, 11, 8))
    assert lp["mains"] == ["Fall Chicken Sheet Pan Dinner", "Mediterranean Chicken and Orzo"]
    assert lp["sides"] == ["Brown Rice (microwave packet)"] and lp["servings"] == 8
    assert kitchen.lunch_prep(PLAN, datetime.date(2026, 11, 15)) is None


def test_timers_from_step_text():
    assert kitchen.timers("stir fry 5 minutes") == [5]
    assert kitchen.timers("Simmer 3 to 4 minutes, then rest 1 hour.") == [4, 60]
    assert kitchen.timers("Bake 25-30 mins") == [30]
    assert kitchen.timers("Season to taste.") == []
    assert kitchen.timers("Saute.", step_time=8) == [8]


def test_step_title_from_lead_in():
    assert kitchen.step_title("", "Prepare Chicken - cut it up.", 2) == ("Prepare Chicken", "Cut it up.")
    assert kitchen.step_title("", "Whisk it all.", 3) == ("Step 3", "Whisk it all.")
    assert kitchen.step_title("Sauce", "x", 1) == ("Sauce", "x")


# ---- pages ---------------------------------------------------------------------------
def test_kitchen_board_renders_from_fixtures(home):
    r = home.get("/kitchen")
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    html = r.get_data(as_text=True)
    assert "<iframe" not in html
    assert "Chicken &amp; Broccoli &lt;Stir Fry&gt;" in html and "<Stir Fry>" not in html
    assert "with Sautéed Green Beans with Garlic" in html
    assert "30 min · serves 2 · 8 ingredients, 6 on hand" in html          # repo pantry list + Tandoor flag
    assert "Fall Chicken Sheet Pan Dinner" in html and "Prepped Sunday · 8 servings" in html
    assert "Filet Mignon" in html and "with Roasted Butternut Squash" in html
    tomorrow = html.split("class='card tomorrow'")[1].split("</article>")[0]
    assert "Thaw tonight: 1 lb chicken breast" in tomorrow       # 24 h before Tue 6 PM
    assert "Mon" not in tomorrow                                 # tonight's own thaw is not repeated
    assert "Weekly staples" not in html and "Snacks to buy" not in html      # breakfast/snack dropped
    # header: clock, weather, calendar today/tomorrow
    assert "5:20 PM" in html and "Monday, November 9" in html
    assert "H 51° L 31°" in html and html.count("class='hr'") == 6
    assert "Vet &lt;Bear&gt;, 9:30 AM" in html
    assert "Late call, 8 PM" in html                                     # UTC event shown in Chicago time
    # chores: every task except Approvals, counts, escaped titles, tick forms
    assert "Clean fridge &lt;now&gt;" in html and "Approve groceries" not in html
    assert "1 due today" in html and "1 late" in html
    assert "+1 later" in html
    assert html.count("action='/meal/tasks/complete'") == 4
    # vacuum line
    assert "2 of 3 rooms cleaned today · next run tomorrow 8:30 AM" in html
    assert "water tank empty" in html and "side brush due" in html and "filter due" not in html
    # kiosk bits
    assert "manifest.webmanifest" in html and "apple-mobile-web-app-capable" in html
    assert "black-translucent" in html and "icon-180.png" in html
    assert "data-night-event='Vet &lt;Bear&gt;, 9:30 AM'" in html
    assert "/meal/static/home.css?v=" in html


def test_board_fragment_is_swappable(home):
    html = home.get("/kitchen?fragment=1").get_data(as_text=True)
    assert html.startswith("<div class='board-in'") and "<html" not in html


def test_nothing_personal_on_the_kitchen_board(home):
    html = home.get("/kitchen").get_data(as_text=True).lower()
    for word in ("budget", "workout", "wger"):
        assert word not in html


def test_ingredients_view(home):
    html = home.get("/kitchen/ingredients").get_data(as_text=True)
    assert "Pull ingredients" in html and "/ 8" in html
    assert html.count("data-check=") == 8
    assert html.index("Stir Fry") < html.index("Sautéed Green Beans")
    assert "<span class='pill'>on hand</span>" in html
    assert "href='/meal/kitchen'" in html                                    # Back
    assert "data-home='/meal/home'" in home.get("/kitchen/ingredients?from=home").get_data(as_text=True)


def test_steps_view(home):
    html = home.get("/kitchen/steps").get_data(as_text=True)
    assert html.count("class='tab'") == 4
    assert "Step 3 of 4 · Prepare Chicken" in html
    assert "data-timer='5'" in html and "data-timer='4'" in html and "data-timer='60'" in html
    assert "src='/meal/kitchen/step-photo/16'" in html
    assert "This step uses" in html and "⅓ cup" in html
    assert "Done cooking" in html
    side = home.get("/kitchen/steps?r=48").get_data(as_text=True)
    assert "step-photo/17" in side and "data-timer='8'" in side


def test_household_page(home):
    html = home.get("/home").get_data(as_text=True)
    assert "<iframe" not in html and "manifest.webmanifest" not in html
    for name in ("Recipes", "Groceries", "Tasks", "Watch", "Listen &amp; read", "Photos",
                 "Request TV", "Request books", "Workouts", "Budget", "Home control", "Search", "Guides"):
        assert f">{name}</a>" in html
    assert "href='/meal/groceries'" in html and "href='https://wger.kmkdp.com/'" in html
    # due/late first (with project), then everything behind the fold
    due = html.split("class='hh-due'")[1].split("class='hh-link'")[0]
    assert due.index("Clean fridge") < due.index("Clean stove top") and "Dog baths" not in due
    assert "All chores (4)" in html
    # this week's calendar, recurring expanded
    assert "Trash and recycling" in html and "Trip" in html
    assert "Chicken &amp; Broccoli" in html and "Ingredients (8)" in html
    assert "<div class='hh-grid'>" in home.get("/home?fragment=1").get_data(as_text=True)


def test_pages_survive_missing_feeds(home):
    for f in app.HOME_FEEDS:
        f.value, f.tried = None, 1.0          # tried and failed: never blocks
    html = home.get("/kitchen").get_data(as_text=True)
    assert "Weather unavailable" in html and "Calendar unavailable" in html
    assert "Couldn't reach the task list" in html
    assert home.get("/home").status_code == 200


def test_manifest(home):
    m = json.loads(home.get("/manifest.webmanifest").get_data(as_text=True))
    assert m["display"] == "standalone" and m["start_url"] == "/meal/kitchen"
    assert {i["sizes"] for i in m["icons"]} == {"192x192", "512x512"}


def test_static_assets_served(home):
    assert home.get("/static/home.css").status_code == 200
    assert home.get("/static/icon-180.png").headers["Content-Type"] == "image/png"


# ---- step photo proxy -----------------------------------------------------------------
def test_step_photo_only_for_planned_steps(home, monkeypatch):
    calls = []

    class R:
        status_code, content = 200, JPEG

    monkeypatch.setattr(app, "_api_get", lambda path: calls.append(path) or R())
    r = home.get("/kitchen/step-photo/16")
    assert r.status_code == 200 and r.headers["Content-Type"] == "image/jpeg"
    assert r.data == JPEG and calls == ["/api/download-file/16/"]
    assert home.get("/kitchen/step-photo/16").status_code == 200 and len(calls) == 1   # cached
    # a file id that is not on a planned step never reaches Tandoor
    assert home.get("/kitchen/step-photo/99").status_code == 404 and len(calls) == 1


def test_step_photo_unwraps_tandoors_zip(home, monkeypatch):
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("files/de096105.jpg", JPEG)

    class R:
        status_code, content = 200, buf.getvalue()

    monkeypatch.setattr(app, "_api_get", lambda path: R())
    r = home.get("/kitchen/step-photo/17")
    assert r.status_code == 200 and r.data == JPEG and r.headers["Content-Type"] == "image/jpeg"


def test_step_photo_rejects_non_images(home, monkeypatch):
    class R:
        status_code, content = 200, b"<html>not an image</html>"

    monkeypatch.setattr(app, "_api_get", lambda path: R())
    assert home.get("/kitchen/step-photo/17").status_code == 404


# ---- chore tick -------------------------------------------------------------------------
def test_ajax_tick_completes_and_rereads_board(home, monkeypatch):
    sent = []
    monkeypatch.setattr(app, "VIKUNJA_TOKENS", {"jmack": "tok", "rach": ""})
    monkeypatch.setattr(app, "_vik", lambda m, p, t, b=None: sent.append((m, p)) or {"id": 2, "done": False})
    reads = []
    monkeypatch.setattr(app.TASKS_FEED, "fetch", lambda: reads.append(1) or (PROJECTS, TASKS[2:]))
    r = home.post("/tasks/complete", data={"task_id": "2", "ajax": "1"})
    assert r.status_code == 204
    assert sent == [("GET", "/tasks/2"), ("POST", "/tasks/2")] and reads == [1]


def test_plain_tick_still_redirects(home, monkeypatch):
    monkeypatch.setattr(app, "_vik", lambda *a, **k: {"id": 2})
    r = home.post("/tasks/complete", data={"task_id": "2"})
    assert r.status_code == 303 and r.headers["Location"].endswith("/tasks")


# ---- night view -------------------------------------------------------------------------
@pytest.mark.parametrize("hour,night", [(21, False), (22, True), (23, True), (0, True), (5, True), (6, False), (12, False)])
def test_night_hours(hour, night):
    assert kitchen.is_night(NOW.replace(hour=hour, minute=59 if hour == 21 else 0)) is night


def test_night_view_shows_tomorrows_first_event(home, monkeypatch):
    monkeypatch.setattr(app, "_now", lambda: NOW.replace(hour=22, minute=30))
    html = home.get("/kitchen").get_data(as_text=True)
    assert "class='kiosk is-night'" in html
    night = html.split("id='night'")[1]
    assert "Vet &lt;Bear&gt;, 9:30 AM" in night


# ---- feeds ------------------------------------------------------------------------------
def test_feed_keeps_last_value_and_does_not_block_after_a_failure():
    n = []

    def boom():
        n.append(1)
        raise ValueError("https://secret.example/ics-token")

    f = app.Feed("t", 60, boom)
    assert f.get() is None and len(n) == 1 and f.error == "ValueError"
    assert f.get() is None and len(n) == 1                   # tried a moment ago: no retry yet
    f.set("good")
    f.ts = 0                                                 # stale
    f.tried = 0
    f.fetch = boom
    assert f.refresh() == "good"                             # failure keeps the last value


# ---- household chore cap (review: 47 late chores made the phone page ~3,400 px) -------
def _many_late(n):
    late = [{"id": 200 + i, "title": f"Late chore {i:02d}", "project_id": 4, "done": False,
             "due_date": f"2026-10-{1 + i % 28:02d}T17:00:00-06:00"} for i in range(n)]
    today = [{"id": 300, "title": "Today chore", "project_id": 5, "done": False,
              "due_date": "2026-11-09T17:00:00-06:00"}]
    return late + today


def test_household_chore_list_is_capped(home):
    app.TASKS_FEED.set((PROJECTS, _many_late(47)))
    html = home.get("/home").get_data(as_text=True)
    due = html.split("class='hh-due'")[1].split("hh-more")[0]
    assert due.count("<form class='chore") == kitchen.HH_CHORE_CAP == 8
    assert "Today chore" not in due                              # late first: 47 late fill the 8
    assert "40 more late or due today" in html
    assert "href='/meal/home?chores=all#all-chores'>All chores (48)</a>" in html
    assert "id='all-chores'" not in html                         # the full board stays out


def test_household_cap_shows_due_today_after_late(home):
    app.TASKS_FEED.set((PROJECTS, _many_late(5)))
    due = home.get("/home").get_data(as_text=True).split("class='hh-due'")[1].split("class='hh-link'")[0]
    assert due.count("<form class='chore") == 6 and due.index("Late chore") < due.index("Today chore")
    assert "more late or due today" not in due


def test_household_all_chores_view(home):
    app.TASKS_FEED.set((PROJECTS, _many_late(47)))
    html = home.get("/home?chores=all").get_data(as_text=True)
    board = html.split("id='all-chores'")[1]
    assert board.count("name='task_id'") == 48                   # every open chore
    assert "Hide all chores" in html
    assert "data-fragment='/meal/home?fragment=1&amp;chores=all'" in html
    assert "id='all-chores'" in home.get("/home?fragment=1&chores=all").get_data(as_text=True)


# ---- clean recipe titles -----------------------------------------------------------------
@pytest.mark.parametrize("raw,clean", [
    ("Chicken and Broccoli Stir Fry Recipe", "Chicken and Broccoli Stir Fry"),
    ("Filet Mignon Recipe in Mushroom Sauce (VIDEO)", "Filet Mignon in Mushroom Sauce"),
    ("Garlic Butter Salmon (Easy Recipe)", "Garlic Butter Salmon"),
    ("Beef Stroganoff (Quick & Easy)", "Beef Stroganoff"),
    ("Pad Thai RECIPE", "Pad Thai"),
    ("Shrimp Recipe with Lemon (video)", "Shrimp with Lemon"),
    ("Tacos (Best Ever) [Video]", "Tacos"),
    ("Chili (Mild)", "Chili (Mild)"),                          # not marketing: kept
    ("Recipe Box Soup", "Recipe Box Soup"),                    # middle "Recipe" not before in/with
    ("Recipe", "Recipe"),                                      # never empty
    ("Sautéed Green Beans with Garlic", "Sautéed Green Beans with Garlic"),
])
def test_clean_title(raw, clean):
    assert kitchen.clean_title(raw) == clean


def test_title_html_keeps_the_original():
    assert kitchen.title_html("Filet Mignon Recipe in Mushroom Sauce (VIDEO)") == \
        "<span title='Filet Mignon Recipe in Mushroom Sauce (VIDEO)'>Filet Mignon in Mushroom Sauce</span>"
    assert kitchen.title_html("Tom & Jerry's <Pie> Recipe") == \
        "<span title='Tom &amp; Jerry&#39;s &lt;Pie&gt; Recipe'>Tom &amp; Jerry&#39;s &lt;Pie&gt;</span>"
    assert kitchen.title_html("Roasted Carrots") == "Roasted Carrots"


def test_clean_titles_on_every_page(home, monkeypatch):
    plan = [dict(e) for e in PLAN]
    plan[6]["title"] = "Chicken and Broccoli Stir Fry Recipe"
    plan[7]["title"] = "Filet Mignon Recipe in Mushroom Sauce (VIDEO)"
    plan[0]["title"] = "Fall Chicken Sheet Pan Dinner (Easy Recipe)"
    snap = {"ts": 0, "day": NOW.date(), "entries": [], "plan": plan, "lines": []}
    monkeypatch.setattr(app, "_grocery_data", lambda force=False: (snap, "cache"))
    for path in ("/kitchen", "/home", "/kitchen/ingredients", "/kitchen/steps"):
        html = home.get(path).get_data(as_text=True)
        assert ">Chicken and Broccoli Stir Fry</span>" in html, path
        assert "title='Chicken and Broccoli Stir Fry Recipe'" in html, path
    for path in ("/kitchen", "/home"):
        html = home.get(path).get_data(as_text=True)
        assert ">Filet Mignon in Mushroom Sauce</span>" in html and ">Fall Chicken Sheet Pan Dinner</span>" in html


def test_heading_only_step_becomes_next_step_title():
    from kitchen import make_dish, is_heading, heading_title
    rec = {"servings": 2, "ingredients": [{"id": 1, "food": "steak", "amount": 2, "unit": ""}],
           "steps": [{"name": "", "instruction": "How to Cook Filet Mignon Recipe:", "ingredients": []},
                     {"name": "", "instruction": "Sear the steaks 4 minutes a side.", "ingredients": [0]},
                     {"name": "", "instruction": "Serve hot.", "ingredients": []}]}
    dish = make_dish({"recipe_id": 46, "servings": 2, "title": "Filet"}, rec)
    assert [s["title"] for s in dish["steps"]][0] == "Cook Filet Mignon"
    assert len(dish["steps"]) == 2                      # heading folded, "Serve hot." kept
    assert dish["steps"][1]["body"].startswith("Serve hot")
    assert is_heading("For the sauce") and is_heading("Instructions:")
    assert not is_heading("Serve hot.") and not is_heading("Rest for 5 minutes.")
    assert heading_title("How to Cook Filet Mignon Recipe:") == "Cook Filet Mignon"


def test_unnamed_steps_get_an_excerpt_tab_not_step_n(home):
    html = home.get("/kitchen/steps").get_data(as_text=True)
    tabs = re.findall(r"<button type='button' class='tab'[^>]*>(.*?)</button>", html)
    # step 1 has no name: its opening words, not a second "Step 1" next to the number
    assert tabs[0] == "<span class='tn'>1</span> Start the rice first"
    assert tabs[1] == "<span class='tn'>2</span> Make the sauce"
    assert "Step 1 of 4</div>" in html and "Step 1 of 4 · Step 1" not in html
    assert "Step 2 of 4 · Make the sauce" in html
    assert kitchen.tab_label("Pour in the broth. Simmer 2 minutes.", 4) == "Pour in the broth"
    assert kitchen.tab_label("", 4) == "Step 4"


def _css_rules(css: str) -> dict:
    """selector -> declarations for the top-level rules (comments and @media blocks dropped)."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    css = re.sub(r"@media[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}", "", css)
    out = {}
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        out[sel.strip()] = out.get(sel.strip(), "") + body + ";"
    return out


def test_kitchen_css_sizes_to_the_viewport():
    """The board fits whatever height Safari leaves (100dvh after a 100vh
    fallback); header and meals rows size to their content; the side cards
    (lunch, tomorrow, pantry button) never get a fixed pixel height again."""
    import os
    with open(os.path.join(os.path.dirname(kitchen.__file__), "static", "home.css")) as fh:
        rules = _css_rules(fh.read())
    for sel in ("body.kiosk", ".shell"):
        assert "height:100vh;height:100dvh" in rules[sel].replace(" ", ""), sel
    assert "grid-template-rows:auto auto minmax(0,1fr)" in rules[".board-in"]
    side = {k: v for k, v in rules.items() if re.search(r"\.(meal-side|lunch|tomorrow|pantry-btn)\b", k)}
    assert len(side) >= 4
    for sel, decl in side.items():
        assert not re.search(r"(^|;)\s*(max-|min-)?height\s*:\s*\d+px", decl), (sel, decl)
    assert "clamp(" in rules[".step-body p"] and "clamp(" in rules[".dish"]
