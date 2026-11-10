"""Kitchen pantry check (/kitchen/pantry) and the tablet's grocery writes: both
sections rendered from fixtures, the on-list state, "Running low" through the
existing /groceries/reorder (ajax, refusal, duplicate taps), the manual add
box through the existing /groceries/add (ajax, empty, too long), escaping, and
the board/household links."""
import datetime
import threading
import time

import pytest

import app
import stock

TODAY = datetime.datetime.now(app.TZ).date()


def _plan():
    d = TODAY.isoformat()
    return [{"date": d, "meal_type": "Dinner", "title": "Beef <Chili>", "recipe_id": 11,
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
    """Tandoor faked at the shopping-list/meal-plan/recipe seams; any other
    upstream call fails the test."""
    state = {"entries": [], "writes": [], "fetches": 0}

    def no_net(*a, **k):
        raise AssertionError("unexpected upstream call")

    def fetch():
        state["fetches"] += 1
        return [dict(e) for e in state["entries"]]

    def write(method, path, body):
        state["writes"].append((method, path, body))
        state["entries"].append({"id": 900 + len(state["entries"]), "checked": False,
                                 "list_recipe": None, "amount": body["amount"],
                                 "food": {"name": body["food"]["name"]}})
        return True

    monkeypatch.setattr(app.requests, "get", no_net)
    monkeypatch.setattr(app, "_api_get", no_net)
    monkeypatch.setattr(app, "_fetch_shopping", fetch)
    monkeypatch.setattr(app, "_api_write", write)
    monkeypatch.setattr(app, "fetch_plan", lambda a, b: _plan())
    monkeypatch.setattr(app, "_recipe_detail", lambda rid: RECIPES[rid])
    monkeypatch.setattr(app, "THAW_FILE", str(tmp_path / "thaw.json"))
    return state


def _page(c, q=""):
    return c.get("/kitchen/pantry" + q).get_data(as_text=True)


def _sec(html, name):
    return html.split(f"data-sec='{name}'", 1)[1].split("</section>" if name == "used" else "</details>", 1)[0]


def _row(sec, food):
    return sec.split(f"<span class='fname'>{food}</span>", 1)[1].split("<div class='prow'>", 1)[0]


# ---- rendering ---------------------------------------------------------------------
def test_used_this_week_grouped_by_store_and_aisle(fake):
    html = _page(app.app.test_client())
    used = _sec(html, "used")
    assert "Penzeys · Spices" in used and "Amana · Freezer Beef" in used
    assert "Target · Oils &amp; Spices" in used
    # recipes each food is for, escaped
    assert "for Beef &lt;Chili&gt;, Chicken Fajita Bowls" in _row(used, "cumin")
    assert "<Chili>" not in html
    # ordinary list foods are not pantry foods
    assert ">kidney beans<" not in used and ">bell pepper<" not in used
    assert used.count("Running low</button>") == 5          # beef, chili powder, cumin, chicken, olive oil
    assert "data-markall='used'" in used
    assert "<span data-ptotal>5</span>" in html


def test_staples_section_is_collapsed_and_excludes_used(fake):
    html = _page(app.app.test_client())
    assert "<details class='card psec staples' data-sec='staples'>" in html   # no `open`
    st = _sec(html, "staples")
    assert "All pantry staples" in st
    assert ">cumin<" not in st and ">olive oil<" not in st                     # in section 1 already
    listed = [f for aisles in stock.catalog().values() for foods in aisles.values() for f in foods]
    assert st.count("Running low</button>") == len(listed) - 5


def test_page_shell_back_idle_and_kiosk(fake):
    c = app.app.test_client()
    html = _page(c)
    assert "data-page='pantry'" in html and "class='kiosk sub'" in html
    assert "data-home='/meal/kitchen'" in html and "class='btn ghost back' href='/meal/kitchen'" in html
    assert "name='next' value='pantry'" in html
    home = _page(c, "?from=home")
    assert "data-home='/meal/home'" in home and "name='next' value='pantry-home'" in home


def test_cached_view_makes_no_upstream_call(fake):
    c = app.app.test_client()
    _page(c)                                   # cold: the one sync refresh
    n = fake["fetches"]
    _page(c)
    assert fake["fetches"] == n


def test_on_list_state(fake):
    fake["entries"].append({"id": 1, "checked": False, "list_recipe": None, "amount": 1,
                            "food": {"name": "Cumin"}})
    fake["entries"].append({"id": 2, "checked": True, "list_recipe": None, "amount": 1,
                            "food": {"name": "olive oil"}})          # ticked: not on the list
    used = _sec(_page(app.app.test_client()), "used")
    cumin = _row(used, "cumin")
    assert "On the list" in cumin and "Running low" not in cumin
    assert "Running low</button>" in _row(used, "olive oil")


def test_escaping_of_pantry_names(fake, monkeypatch, repo_cfg):
    cleanup = dict(repo_cfg["cleanup"], pantry=list(repo_cfg["cleanup"]["pantry"]) + ["<b>x</b>'s salt"])
    monkeypatch.setattr(stock, "_cleanup", cleanup)
    html = _page(app.app.test_client())
    assert "<b>x</b>" not in html
    assert "&lt;b&gt;x&lt;/b&gt;&#39;s salt" in html                         # text, value and key


# ---- links -------------------------------------------------------------------------
def test_board_and_household_link_to_pantry_check(fake, monkeypatch):
    monkeypatch.setattr(app, "_recipe", lambda rid: {"servings": 4, "ingredients": [], "steps": []})
    c = app.app.test_client()
    board = c.get("/kitchen").get_data(as_text=True)
    assert "class='btn ghost pantry-btn' href='/meal/kitchen/pantry'>Pantry check</a>" in board
    hh = c.get("/home").get_data(as_text=True)
    assert "href='/meal/kitchen/pantry?from=home'>Pantry check</a>" in hh
    # the compact add box sits outside the refreshed fragment
    assert hh.index("class='card addbox compact'") < hh.index("id='board'")
    assert "addbox" not in c.get("/home?fragment=1").get_data(as_text=True)


# ---- Running low (the existing reorder) ------------------------------------------
def test_reorder_ajax_adds_one_entry(fake):
    r = app.app.test_client().post("/groceries/reorder", data={"food": "cumin", "ajax": "1", "next": "pantry"})
    assert r.status_code == 200 and r.get_json() == {"status": "added"}
    assert fake["writes"] == [("POST", "/api/shopping-list-entry/",
                               {"food": {"name": "cumin"}, "amount": 1, "checked": False})]


def test_reorder_ajax_refuses_non_pantry_food(fake):
    r = app.app.test_client().post("/groceries/reorder", data={"food": "kidney beans", "ajax": "1"})
    assert r.status_code == 422 and r.get_json() == {"status": "refused"}
    assert fake["writes"] == []
    r = app.app.test_client().post("/groceries/reorder", data={"food": "  ", "ajax": "1"})
    assert r.status_code == 422 and fake["writes"] == []


def test_reorder_second_tap_is_a_no_op_and_view_shows_on_list(fake):
    c = app.app.test_client()
    _page(c)                                   # the warmer has filled the plan
    first = c.post("/groceries/reorder", data={"food": "cumin", "ajax": "1"}).get_json()
    second = c.post("/groceries/reorder", data={"food": "Cumin", "ajax": "1"}).get_json()
    assert (first, second) == ({"status": "added"}, {"status": "on_list"})
    assert len(fake["writes"]) == 1
    assert "On the list" in _row(_sec(_page(c), "used"), "cumin")


def test_reorder_concurrent_taps_write_once(fake, monkeypatch):
    """Two taps at once (tablet + phone): the check and the write are serialized."""
    real = app._api_write

    def slow_write(method, path, body):
        time.sleep(0.05)                       # widen the check-to-write window
        return real(method, path, body)
    monkeypatch.setattr(app, "_api_write", slow_write)
    out = []

    def tap():
        out.append(app.app.test_client().post(
            "/groceries/reorder", data={"food": "cumin", "ajax": "1"}).get_json()["status"])
    ts = [threading.Thread(target=tap) for _ in range(3)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(fake["writes"]) == 1 and sorted(out) == ["added", "on_list", "on_list"]


def test_reorder_failed_write_reports_error(fake, monkeypatch):
    monkeypatch.setattr(app, "_api_write", lambda *a: False)
    r = app.app.test_client().post("/groceries/reorder", data={"food": "cumin", "ajax": "1"})
    assert r.status_code == 502 and r.get_json() == {"status": "error"}


def test_reorder_form_post_returns_to_the_pantry_view(fake):
    c = app.app.test_client()
    r = c.post("/groceries/reorder", data={"food": "cumin", "next": "pantry"})
    assert r.status_code == 303 and r.headers["Location"].endswith("/meal/kitchen/pantry")
    r = c.post("/groceries/reorder", data={"food": "olive oil", "next": "pantry-home"})
    assert r.headers["Location"].endswith("/meal/kitchen/pantry?from=home")
    # an unknown next is ignored (never an open redirect): groceries Pantry tab
    r = c.post("/groceries/reorder", data={"food": "cumin", "next": "https://evil.example/"})
    assert r.headers["Location"].endswith("groceries#pantry")


def test_no_on_hand_edit_from_the_tablet(fake):
    c = app.app.test_client()
    c.post("/groceries/reorder", data={"food": "cumin", "ajax": "1"})
    c.post("/groceries/add", data={"item": "paper towels", "ajax": "1"})
    assert [(m, p) for m, p, _ in fake["writes"]] == [("POST", "/api/shopping-list-entry/")] * 2


# ---- manual add box (the existing /groceries/add) --------------------------------
def test_add_box_renders_with_ipad_keyboard_hints_and_manual_items(fake):
    fake["entries"].append({"id": 5, "checked": False, "list_recipe": None, "amount": 1,
                            "food": {"name": "Paper <towels>"}})
    html = _page(app.app.test_client())
    box = html.split("class='card addbox'", 1)[1].split("</section>", 1)[0]
    assert html.index("addbox") < html.index("data-sec='used'")            # top of the view
    assert "action='/meal/groceries/add'" in box
    for attr in ("enterkeyhint='done'", "autocapitalize='sentences'", "autocomplete='off'",
                 "maxlength='80'", "required", "name='next' value='pantry'"):
        assert attr in box, attr
    assert "Add to list</button>" in box and "Added tonight" in box
    assert "On the manual list (1)" in box and "<li>1 Paper &lt;towels&gt;</li>" in box


def test_add_ajax_trims_and_returns_the_item(fake):
    r = app.app.test_client().post("/groceries/add", data={"item": "  Paper towels  ", "ajax": "1"})
    assert r.status_code == 200 and r.get_json() == {"status": "added", "item": "Paper towels"}
    assert fake["writes"] == [("POST", "/api/shopping-list-entry/",
                               {"food": {"name": "Paper towels"}, "amount": 1, "checked": False})]
    # it shows on the Manual list right away
    assert "<li>1 Paper towels</li>" in _page(app.app.test_client())


def test_add_empty_is_refused(fake):
    for v in ("", "   "):
        r = app.app.test_client().post("/groceries/add", data={"item": v, "ajax": "1"})
        assert r.status_code == 422 and r.get_json()["status"] == "empty"
    r = app.app.test_client().post("/groceries/add", data={"ajax": "1"})
    assert r.status_code == 422
    assert fake["writes"] == []


def test_add_too_long_is_capped_at_80(fake):
    r = app.app.test_client().post("/groceries/add", data={"item": "x" * 200, "ajax": "1"})
    assert r.get_json()["item"] == "x" * 80
    assert fake["writes"][0][2]["food"]["name"] == "x" * 80


def test_add_escapes_markup_on_every_view(fake):
    c = app.app.test_client()
    j = c.post("/groceries/add", data={"item": "<img src=x onerror=alert(1)>", "ajax": "1"}).get_json()
    assert j["item"] == "<img src=x onerror=alert(1)>"      # JSON; home.js inserts it as text
    for html in (_page(c), c.get("/groceries").get_data(as_text=True)):
        assert "<img src=x" not in html and "&lt;img src=x onerror=alert(1)&gt;" in html


def test_add_form_post_returns_to_where_it_came_from(fake):
    c = app.app.test_client()
    assert c.post("/groceries/add", data={"item": "a", "next": "home"}).headers["Location"].endswith("/meal/home")
    assert c.post("/groceries/add", data={"item": "b", "next": "pantry"}).headers["Location"].endswith("/meal/kitchen/pantry")
    assert c.post("/groceries/add", data={"item": "c"}).headers["Location"].endswith("groceries#manual")


def test_add_failed_write_reports_error(fake, monkeypatch):
    monkeypatch.setattr(app, "_api_write", lambda *a: False)
    r = app.app.test_client().post("/groceries/add", data={"item": "milk", "ajax": "1"})
    assert r.status_code == 502 and r.get_json()["status"] == "error"
