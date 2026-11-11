"""Review 2026-11-11 fixes: CookLog ratings cache + bounded parallel fetch,
meal-plan page_size, and forecast staleness / past days / null codes."""
import datetime
import threading
import time

import app
import kitchen
from test_kitchen import NOW, _wx_week, home  # noqa: F401


class _R:
    def __init__(self, data, status=200):
        self._d, self.status_code = data, status

    def json(self):
        return self._d

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def _cooklog(monkeypatch, delay=0.0, ratings=None):
    calls, lock = [], threading.Lock()
    ratings = ratings if ratings is not None else {}

    def fake(path):
        with lock:
            calls.append(path)
        time.sleep(delay)
        rid = int(path.split("recipe=")[1].split("&")[0])
        r = ratings.get(rid)
        return _R({"results": [{"rating": r}] if r else []})
    monkeypatch.setattr(app, "_api_get", fake)
    return calls


# ---- 1. ratings ------------------------------------------------------------------------
def test_ratings_cached_per_recipe(monkeypatch):
    calls = _cooklog(monkeypatch, ratings={1: 4, 2: 5})
    assert app.last_ratings({1, 2, 3}) == {1: 4, 2: 5}
    assert len(calls) == 3
    assert app.last_ratings({1, 2, 3}) == {1: 4, 2: 5}       # all from cache, incl. "no rating"
    assert len(calls) == 3
    app._RATING_CACHE[1] = (time.time() - app._RATING_TTL - 1, 4)   # expired -> refetched
    app.last_ratings({1, 2})
    assert len(calls) == 4 and calls[-1].startswith("/api/cook-log/?recipe=1&")


def test_ratings_fetch_in_parallel(monkeypatch):
    _cooklog(monkeypatch, delay=0.2, ratings={i: 3 for i in range(1, 17)})
    t0 = time.monotonic()
    assert len(app.last_ratings(set(range(1, 17)))) == 16
    assert time.monotonic() - t0 < 1.0                       # 16 x 0.2 s serial would be 3.2 s


def test_ratings_wait_at_most_one_deadline(monkeypatch):
    monkeypatch.setattr(app, "_RATING_DEADLINE", 0.3)
    _cooklog(monkeypatch, delay=1.0, ratings={1: 5})
    t0 = time.monotonic()
    assert app.last_ratings({1}) == {}                        # straggler: empty stars this render
    assert time.monotonic() - t0 < 0.8
    time.sleep(1.0)
    assert app._RATING_CACHE[1][1] == 5                       # ...and fills the cache for the next


def test_ratings_failure_is_empty_and_not_cached(monkeypatch):
    def boom(path):
        raise RuntimeError("tandoor down")
    monkeypatch.setattr(app, "_api_get", boom)
    assert app.last_ratings({7}) == {}
    assert 7 not in app._RATING_CACHE


def test_rate_post_invalidates_that_recipe(monkeypatch):
    app._RATING_CACHE.update({5: (time.time(), 2), 6: (time.time(), 4)})
    posted = []
    monkeypatch.setattr(app, "post_rating", lambda rid, stars, who: posted.append((rid, stars)) or True)
    r = app.app.test_client().post("/rate", data={"recipe_id": "5", "rating": "5"})
    assert r.status_code == 303 and posted == [(5, 5)]
    assert 5 not in app._RATING_CACHE and 6 in app._RATING_CACHE


def test_fetch_plan_asks_for_one_big_page(monkeypatch):
    seen = []
    monkeypatch.setattr(app, "_api_get", lambda p: seen.append(p) or _R({"results": []}))
    app.fetch_plan(datetime.date(2026, 11, 8), datetime.date(2026, 11, 21))
    assert seen[0].startswith("/api/meal-plan/?from_date=2026-11-08&to_date=2026-11-21")
    assert "&page_size=100" in seen[0]


# ---- 2. forecast ------------------------------------------------------------------------
def _shifted(days_back):
    """The fixture grid fetched `days_back` days ago (starts before today)."""
    wx = _wx_week()
    shift = datetime.timedelta(days=days_back)
    wx["daily"]["time"] = [(datetime.date.fromisoformat(d) - shift).isoformat() for d in wx["daily"]["time"]]
    wx["hourly"]["time"] = [(datetime.datetime.fromisoformat(t) - shift).isoformat() for t in wx["hourly"]["time"]]
    return wx


def test_forecast_drops_past_days():
    fc = kitchen.forecast_view(_shifted(2), NOW, app.WMO)
    assert [d["date"] for d in fc["days"]][0] == NOW.date() and len(fc["days"]) == 5
    assert fc["days"][0]["today"]


def test_forecast_fresh_is_not_flagged():
    fc = kitchen.forecast_view(_wx_week(), NOW, app.WMO, NOW - datetime.timedelta(minutes=20))
    assert fc["stale"] is False


def test_forecast_stale_after_an_hour():
    upd = NOW - datetime.timedelta(hours=2)
    fc = kitchen.forecast_view(_wx_week(), NOW, app.WMO, upd)
    assert fc["stale"] and fc["as_of"] == "3:20 PM"
    old = kitchen.forecast_view(_shifted(2), NOW, app.WMO, NOW - datetime.timedelta(days=2))
    assert old["stale"] and old["as_of"] == "Sat 5:20 PM"


def test_forecast_stale_when_hourly_empty():
    wx = _wx_week()
    wx["hourly"] = {}
    fc = kitchen.forecast_view(wx, NOW, app.WMO, NOW)
    assert fc["stale"] and fc["hourly"] == [] and len(fc["days"]) == 7


def test_forecast_null_daily_code_keeps_the_page():
    wx = _wx_week()
    wx["daily"]["weather_code"][2] = None
    fc = kitchen.forecast_view(wx, NOW, app.WMO)
    assert fc is not None and len(fc["days"]) == 7
    assert fc["days"][2]["desc"] == "" and fc["days"][1]["desc"] == app.WMO[3]


def test_forecast_page_labels_stale_data(home):
    app.WX_FEED.set(_shifted(2))
    app.WX_FEED.ts = (NOW - datetime.timedelta(days=2)).timestamp()
    app.WX_FEED.tried = time.time()                  # no background refresh in the test
    html = home.get("/kitchen/weather").get_data(as_text=True)
    assert "<div class='card fstale' role='status'>Forecast last updated Sat 5:20 PM</div>" in html
    assert "Sat, Nov 7" not in html and "Nov 8" not in html   # past days gone
    app.WX_FEED.set(_wx_week())
    html = home.get("/kitchen/weather").get_data(as_text=True)
    assert "fstale" not in html


def test_forecast_page_never_updated_says_unavailable():
    wx = _wx_week()
    wx["hourly"] = {}
    fc = kitchen.forecast_view(wx, NOW, app.WMO, None)
    html = kitchen.forecast_page(fc, NOW, "v", "/meal/kitchen")
    assert "Forecast unavailable" in html
