"""Polish items from the 2026-11-10 page review: the groceries page on the
shared home.css (no-store, h1, labelled add box, 44 px tabs), one h1 on every
page, the devices page's headings and phone rows, admin touch targets and the
small copy fixes. Fixtures only, no network."""
import datetime
import os
import re
from zoneinfo import ZoneInfo

import admin
import app
import kitchen
import test_admin as TA
from test_admin import adm, aget  # noqa: F401  (admin feeds wired to fixtures)
from test_kitchen import home  # noqa: F401  (the fixture-wired sidecar)
from test_kitchen_pairing import ADMIN, kit, pair  # noqa: F401  (pairing wired to a temp registry)

TZ = ZoneInfo("America/Chicago")
CSS = open(os.path.join(os.path.dirname(app.__file__), "static", "home.css")).read()


def _rule(css, sel):
    return css.split(sel + "{", 1)[1].split("}", 1)[0]


# ---- groceries ---------------------------------------------------------------------------
def test_groceries_is_no_store_on_home_css(home):  # noqa: F811
    r = home.get("/groceries")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    assert "/meal/static/home.css" in html and "shared-nav" not in html and "home.js" not in html
    assert "<body class='groceries'>" in html and html.count("<h1") == 1 and "<h1>Groceries</h1>" in html
    # tabs keep their hash behaviour; the add box has a real label
    assert "<nav class='gtabs' aria-label='Shopping lists'>" in html and "data-tab='manual'" in html
    assert "<label class='sr' for='g-item'>Add an item</label><input id='g-item' name='item'" in html
    assert "location.hash" in app.GROCERIES_JS and "'.gtabs button'" in app.GROCERIES_JS


def test_groceries_error_page_is_no_store_too(home, monkeypatch):  # noqa: F811
    def boom(force=False):
        raise RuntimeError("tandoor down")
    monkeypatch.setattr(app, "_grocery_data", boom)
    r = home.get("/groceries")
    html = r.get_data(as_text=True)
    assert r.headers["Cache-Control"] == "no-store" and "Couldn't load the grocery list" in html
    assert "/meal/static/home.css" in html and "<h1>Groceries</h1>" in html


def test_groceries_css_uses_tokens_and_44px_targets():
    css = app.GROCERIES_CSS
    # the old palette (and its 3.9:1 ash) is gone; colours come from home.css tokens
    for old in ("--ash", "--ink", "--ember", "JetBrains Mono", "#8d8377"):
        assert old not in css
    assert "min-height:44px" in _rule(css, ".gtabs button")
    assert "width:44px;height:44px" in _rule(css, ".gi button.tick")
    assert "min-height:44px" in _rule(css, ".gi a.srch")
    assert "min-height:44px" in _rule(css, ".gi form.reorderform button.reorder")
    # its class names stay clear of home.css's steps tabs, household add card and board meals
    for cls in (".tabs{", ".tab{", ".addbox{", ".meals{"):
        assert cls not in css


# ---- headings -----------------------------------------------------------------------------
def test_every_page_has_exactly_one_h1(home, adm, kit):  # noqa: F811
    pages = {"household": home.get("/home").get_data(as_text=True),
             "board": home.get("/kitchen").get_data(as_text=True),
             "steps": home.get("/kitchen/steps").get_data(as_text=True),
             "devices": home.get("/kitchen/devices", headers=ADMIN).get_data(as_text=True),
             "admin": aget(adm).get_data(as_text=True)}
    for name, html in pages.items():
        assert html.count("<h1") == 1, name
    assert "<h1 class='brand'>Mack House</h1>" in pages["household"]
    assert "<h1 class='brand'>Mack House</h1>" in pages["admin"]


def test_steps_page_headings(home):  # noqa: F811
    html = home.get("/kitchen/steps").get_data(as_text=True)
    assert "<h1 class='sr'>Steps: " in html
    assert re.search(r"<h2 class='eyebrow tan'>Step 1 of \d+", html)
    empty = kitchen.steps_page(None, [], "v", "/meal/kitchen")
    assert "<h1 class='sr'>Steps</h1>" in empty


def test_devices_page_headings_rows_and_way_home(kit):  # noqa: F811
    pair(kit)
    html = kit.get("/kitchen/devices", headers=ADMIN).get_data(as_text=True)
    assert html.count("<h1") == 1 and "<h2>Paired</h2>" in html
    assert "<a class='home' href='/meal/home'>Household home</a>" in html
    assert "<thead>" in html and "<td data-label='Last seen'>" in html
    # phone: stacked rows, never a table wider than the card
    assert "@media(max-width:600px)" in html and ".pair table,.pair tbody,.pair tr,.pair td{display:block}" in html


# ---- admin on a phone ----------------------------------------------------------------------
def test_admin_touch_targets_and_wrapping_alert_names():
    touch = CSS.split("@media (max-width:1180px),(pointer:coarse){", 1)[1].split("\n}", 1)[0]
    for sel in (".ad-svc{min-height:44px}", ".ad-act", ".ad-home", ".ad-explore", ".ad-btn{min-height:44px}"):
        assert sel in touch
    name = _rule(CSS, ".ad-name")
    assert "overflow-wrap:anywhere" in name and "overflow:hidden" not in name


def test_failed_deploys_tile_links_to_komodo():
    src = {"alerts": (admin.parse_alerts(TA.AM_ALERTS), "ok"), "probes": (TA.PROBES, "ok"),
           "backups": (admin.parse_backups(TA.BACKUP_LINES), "ok"),
           "komodo": ({"configured": True, "failed": 1, "deploys": 68, "more": True}, "ok"),
           "github": (admin.parse_github(TA.GH_PULLS), "ok"),
           "advisories": (admin.parse_advisory_summary(TA.ADV_ROWS), "ok"),
           "ups": (admin.parse_ups(TA.HA_STATES, TA.P), "ok")}
    t = {x["label"]: x for x in admin.status_tiles(src, 0, TZ, "https://komodo.example")}
    assert t["Deploys failed"]["href"] == "https://komodo.example/"
    html = admin._stats_html(list(t.values()))
    assert "<a class='ad-stat bad' href='https://komodo.example/' target='_blank' rel='noopener'>" in html
    src["komodo"] = ({"configured": True, "failed": 0, "deploys": 68}, "ok")
    t = {x["label"]: x for x in admin.status_tiles(src, 0, TZ, "https://komodo.example")}
    assert t["Deploys failed"]["href"] == ""


def test_signins_from_an_earlier_day_show_the_weekday():
    now = datetime.datetime.now(TZ)
    earlier = now - datetime.timedelta(days=1)
    rows = [{"ts": now.timestamp(), "user": "jmack", "app": "a", "ok": True, "result": "ok"},
            {"ts": earlier.timestamp(), "user": "rach", "app": "b", "ok": True, "result": "ok"}]
    html = admin._signins_html({"rows": rows}, "ok", TZ)
    assert f"<span class='muted'>{now:%H:%M}</span>" in html
    assert f"<span class='muted'>{earlier:%a %H:%M}</span>" in html


# ---- titles and copy -------------------------------------------------------------------------
def test_long_titles_break_instead_of_scrolling_the_page():
    titles = CSS.split("/* a title with one very long word", 1)[1].split("\n", 2)[1]
    assert titles == ".ltitle,.dish,.hh-grid h2{overflow-wrap:anywhere}"


def test_add_field_keeps_the_focus_ring_and_spares_the_checkbox():
    assert "outline:none" not in _rule(CSS, ".additem>input:focus")
    assert ".additem input{" not in CSS          # the checkbox inside .mtog is not a text field


def test_count_units_take_a_plural():
    assert kitchen.fmt_qty(2, "clove") == "2 cloves"
    assert kitchen.fmt_qty(1, "clove") == "1 clove"
    assert kitchen.fmt_qty(0.5, "clove") == "½ clove"
    assert kitchen.fmt_qty(2, "Tbsp") == "2 Tbsp"


def test_empty_board_says_chores_are_unreachable_once(home, monkeypatch):  # noqa: F811
    assert kitchen.chores_head(None) == ""
    d = app._home_data()
    d["chores"] = None
    html = kitchen.board_fragment(d)
    assert html.count("Couldn't reach the task list right now.") == 1 and "Chores unavailable" not in html
