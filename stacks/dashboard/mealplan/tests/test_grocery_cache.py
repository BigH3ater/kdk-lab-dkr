"""Groceries page performance: one fake Tandoor behind the real _api_get /
_api_write (so the call counter, recipe cache and snapshot cache are all the
production code). Covers call batching (one shopping page, one plan call, one
GET per recipe, recipes in parallel), the snapshot cache (fresh / stale /
forced / error), and that this page's own writes show on the very next render."""
import datetime
import threading
import time
import urllib.parse

import pytest

import app

TODAY = datetime.datetime.now(app.TZ).date()
URL = app.TANDOOR_URL


def _entry(eid, food, aisle="NewPi/weekly: Produce", checked=False):
    return {"id": eid, "checked": checked, "amount": 1, "unit": None,
            "list_recipe": None, "delay_until": None,
            "food": {"name": food, "supermarket_category": {"name": aisle}}}


class Resp:
    def __init__(self, status, data):
        self.status_code, self._data = status, data

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeTandoor:
    """Just enough of requests.Session for _api_get/_api_write."""

    def __init__(self):
        self.cookies = {"csrftoken": "x"}
        self.entries = [_entry(1, "carrots"), _entry(2, "kale"),
                        _entry(3, "old milk", checked=True)]
        d = TODAY.isoformat()
        # recipe 11 has no nested photo (needs the detail GET for it AND for the
        # ingredients -- must still be ONE GET); 12 appears twice in the plan.
        self.plan = [
            {"from_date": d, "recipe": {"id": 11, "name": "Chili"}, "servings": 4,
             "meal_type": {"name": "Dinner"}},
            {"from_date": d, "recipe": {"id": 12, "name": "Tacos", "image": "/media/t.jpg"},
             "servings": 4, "meal_type": {"name": "Lunch"}},
            {"from_date": d, "recipe": {"id": 12, "name": "Tacos", "image": "/media/t.jpg"},
             "servings": 2, "meal_type": {"name": "Dinner"}},
        ]
        self.calls: list[tuple[str, str]] = []
        self.lock = threading.Lock()
        self.recipe_delay = 0.0
        self.fail = False
        self.patch_status = 200

    def _log(self, method, url):
        with self.lock:
            self.calls.append((method, url[len(URL):]))

    def get(self, url, timeout=None):
        self._log("GET", url)
        if self.fail:
            return Resp(500, {})
        u = urllib.parse.urlparse(url)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/api/shopping-list-entry/":
            size = int(q.get("page_size", ["50"])[0])
            page = int(q.get("page", ["1"])[0])
            chunk = self.entries[(page - 1) * size: page * size]
            more = page * size < len(self.entries)
            nxt = f"{URL}/api/shopping-list-entry/?page_size={size}&page={page + 1}" if more else None
            return Resp(200, {"count": len(self.entries), "next": nxt, "results": chunk})
        if u.path == "/api/meal-plan/":
            return Resp(200, {"results": self.plan})
        if u.path.startswith("/api/recipe/"):
            time.sleep(self.recipe_delay)
            rid = int(u.path.rstrip("/").rsplit("/", 1)[1])
            return Resp(200, {"id": rid, "servings": 4, "image": f"/media/{rid}.jpg",
                              "steps": [{"ingredients": [
                                  {"food": {"name": f"food{rid}"}, "unit": {"name": "lb"},
                                   "amount": 1}]}]})
        return Resp(404, {})

    def request(self, method, url, json=None, headers=None, timeout=None):
        self._log(method, url)
        path = url[len(URL):]
        if method == "PATCH":
            if self.patch_status == 200:
                eid = int(path.rstrip("/").rsplit("/", 1)[1])
                for e in self.entries:
                    if e["id"] == eid:
                        e.update(json)
            return Resp(self.patch_status, {})
        if method == "POST":
            e = _entry(100 + len(self.entries), json["food"]["name"], aisle="")
            self.entries.append(e)
            return Resp(201, e)
        return Resp(405, {})

    def n(self, method, prefix):
        return sum(1 for m, p in self.calls if m == method and p.startswith(prefix))


@pytest.fixture
def tandoor(monkeypatch, repo_cfg, tmp_path):
    fake = FakeTandoor()
    monkeypatch.setattr(app, "_sess", fake)
    monkeypatch.setattr(app, "_login", lambda: fake)
    monkeypatch.setattr(app, "THAW_FILE", str(tmp_path / "thaw.json"))
    return fake


def _page(c=None, query=""):
    c = c or app.app.test_client()
    return c.get("/groceries" + query).get_data(as_text=True)


def test_one_refresh_is_four_calls_not_n_plus_one(tandoor):
    html = _page()
    assert "carrots" in html and "kale" in html and "old milk" not in html
    assert tandoor.n("GET", "/api/shopping-list-entry/") == 1       # one page, not two
    assert "page_size=200" in [p for _, p in tandoor.calls if "shopping" in p][0]
    assert tandoor.n("GET", "/api/meal-plan/") == 1
    # 11 (photo + ingredients) and 12 (twice in the plan): one GET each
    assert sorted(p for _, p in tandoor.calls if p.startswith("/api/recipe/")) == [
        "/api/recipe/11/", "/api/recipe/12/"]
    assert len(tandoor.calls) == 4


def test_second_view_is_served_from_memory(tandoor):
    c = app.app.test_client()
    _page(c)
    before = len(tandoor.calls)
    assert "carrots" in _page(c)
    assert len(tandoor.calls) == before                               # zero upstream calls


def test_shopping_still_follows_next_page(tandoor):
    tandoor.entries = [_entry(i, f"item{i}") for i in range(1, 251)]
    entries = app._fetch_shopping()
    assert len(entries) == 250 and tandoor.n("GET", "/api/shopping-list-entry/") == 2


def test_recipe_details_fetch_in_parallel(tandoor):
    d = TODAY.isoformat()
    tandoor.plan = [{"from_date": d, "recipe": {"id": 20 + i, "name": f"R{i}"}, "servings": 4}
                    for i in range(6)]
    tandoor.recipe_delay = 0.3
    t0 = time.monotonic()
    snap = app._grocery_refresh()
    took = time.monotonic() - t0
    assert tandoor.n("GET", "/api/recipe/") == 6
    assert len(snap["lines"]) == 6
    assert took < 1.0, took                   # sequential would be >= 1.8 s


def test_recipe_cache_shared_by_index_and_groceries(tandoor):
    app.fetch_plan(TODAY, TODAY)               # the week grid: photo lookup for 11
    tandoor.calls.clear()
    app._grocery_refresh()
    assert tandoor.n("GET", "/api/recipe/11/") == 0                    # reused
    assert tandoor.n("GET", "/api/recipe/12/") == 1


def test_tick_drops_the_item_on_the_next_render_without_a_reread(tandoor):
    c = app.app.test_client()
    assert ">carrots" in _page(c)
    tandoor.calls.clear()
    r = c.post("/groceries/tick", data={"id": "1"})
    assert r.status_code == 303
    html = _page(c)
    assert ">carrots" not in html and "kale" in html
    assert tandoor.calls == [("PATCH", "/api/shopping-list-entry/1/")]   # no list re-read


def test_failed_tick_keeps_the_item(tandoor):
    c = app.app.test_client()
    _page(c)
    tandoor.patch_status = 500
    c.post("/groceries/tick", data={"id": "1"})
    assert "carrots" in _page(c)


def test_add_shows_on_the_redirected_page(tandoor):
    c = app.app.test_client()
    _page(c)
    tandoor.calls.clear()
    c.post("/groceries/add", data={"item": "birthday candles"})
    assert "birthday candles" in _page(c)
    assert tandoor.n("GET", "/api/shopping-list-entry/") == 1          # re-read once
    assert tandoor.n("GET", "/api/meal-plan/") == 0                    # plan untouched


def test_stale_snapshot_is_served_and_refreshed_in_background(tandoor, monkeypatch):
    c = app.app.test_client()
    _page(c)
    tandoor.entries.append(_entry(9, "leeks"))
    app._gcache["ts"] -= app._GROCERY_TTL + 1                          # past the TTL
    assert "leeks" not in _page(c)                                     # served stale, fast
    for _ in range(50):                                                # background refresh
        if not app._refresh_lock.locked() and "leeks" in str(app._gcache["entries"]):
            break
        time.sleep(0.02)
    assert "leeks" in _page(c)


def test_too_old_or_new_day_refreshes_synchronously(tandoor):
    c = app.app.test_client()
    _page(c)
    tandoor.entries.append(_entry(9, "leeks"))
    app._gcache["day"] = TODAY - datetime.timedelta(days=1)            # midnight passed
    assert "leeks" in _page(c)


def test_refresh_link_forces_a_reread(tandoor):
    c = app.app.test_client()
    html = _page(c)
    assert "groceries?refresh=1" in html and "updated " in html
    tandoor.entries.append(_entry(9, "leeks"))
    assert "leeks" not in _page(c)
    assert "leeks" in _page(c, "?refresh=1")


def test_tandoor_down_keeps_the_last_snapshot(tandoor):
    c = app.app.test_client()
    _page(c)
    tandoor.fail = True
    html = _page(c, "?refresh=1")
    assert "carrots" in html                                           # last good list


def test_tandoor_down_with_no_snapshot_says_so(tandoor):
    tandoor.fail = True
    assert "Couldn't load the grocery list" in _page()


def test_render_and_refresh_log_timing_lines(tandoor, caplog):
    caplog.set_level("INFO", logger="mealplan")
    _page()
    msgs = [r.getMessage() for r in caplog.records]
    refresh = [m for m in msgs if m.startswith("grocery refresh ")]
    render = [m for m in msgs if m.startswith("groceries render ")]
    assert refresh and "upstream_calls=4" in refresh[0] and "ms=" in refresh[0]
    assert render and "source=sync" in render[0]
