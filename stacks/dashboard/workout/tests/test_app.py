"""dashboard-workout sidecar: person mapping, rendering, validation, HA calls and CSV."""
import csv
import importlib
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
CATALOG = REPO / "stacks" / "home-assistant" / "tools" / "workout_catalog.json"

GUIDE = {"version": 1, "days": [], "everyday": [
    {"num": "1", "title": "Tendon glides", "dose": "5 ×", "slug": "hand-glides", "image": "exercise/ex-hand-glides.svg", "everyday": False},
    {"num": "2", "title": "Finger extensions", "dose": "2 × 15", "slug": "finger-ext", "image": "exercise/ex-finger-ext.svg", "everyday": False}]}
for d in ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]:
    GUIDE["days"].append({"id": d, "name": d, "title": f"{d} — Test <b>", "summary": "legs", "youneed": "bench", "steps": [
        {"num": "1", "title": "Box squat", "dose": "3 × 8–12", "slug": "box-squat", "image": "exercise/ex-box-squat-rom.png", "everyday": False},
        {"num": "2", "title": "Wall sit", "dose": "4 × 30 s", "slug": "wall-sit", "image": "exercise/ex-wall-sit.svg", "everyday": False},
        {"num": "3", "title": "Arm circles", "dose": "30 s", "slug": "arm-circles", "image": "exercise/ex-arm-circles.svg", "everyday": False},
        {"num": "4", "title": "Every day", "dose": "~9 min", "slug": "", "image": "", "everyday": True}]})


class FakeResp:
    def __init__(self, ok=True, data=None):
        self.ok, self.status_code, self._d = ok, 200 if ok else 500, data

    def json(self):
        return self._d

    def raise_for_status(self):
        if not self.ok:
            raise __import__("requests").HTTPError("boom")


@pytest.fixture()
def appmod(tmp_path, monkeypatch):
    g = tmp_path / "workout.json"
    g.write_text(json.dumps(GUIDE), encoding="utf-8")
    monkeypatch.setenv("GUIDE_JSON", str(g))
    monkeypatch.setenv("CATALOG_JSON", str(CATALOG))
    monkeypatch.setenv("LOG_CSV", str(tmp_path / "log" / "workout-log.csv"))
    monkeypatch.setenv("HA_TOKEN", "test-token")
    import app as m
    m = importlib.reload(m)
    calls = []

    def fake_request(method, url, json=None, timeout=None, headers=None):
        calls.append((method, url, json))
        if url.endswith("/api/states"):
            return FakeResp(data=[{"entity_id": "input_number.ex_box_squat_weight_rachel", "state": "17.5"},
                                  {"entity_id": "input_number.ex_box_squat_weight", "state": "25.0"},
                                  {"entity_id": "input_datetime.ex_hand_routine_done", "state": "2026-11-04 07:30:00"}])
        return FakeResp()
    monkeypatch.setattr(m.requests, "request", fake_request)
    m._calls = calls
    return m


def test_page_prefills_the_right_person(appmod):
    c = appmod.app.test_client()
    r = c.get("/?day=mon", headers={"Remote-User": "rach"})
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "Logging as <b>Rachel</b>" in html
    assert 'data-e="input_number.ex_box_squat_weight_rachel" value="17.5"' in html
    assert 'name="seconds"' in html                       # wall sit is timed
    assert "/guides/workout/exercise/ex-box-squat-rom.png" in html
    assert "Test &lt;b&gt;" in html                       # guide text is escaped
    assert 'id="ex-arm_circles"' not in html               # warm-ups are not logged
    assert 'id="ex-hand_routine"' in html and 'id="ex-finger_ext"' in html
    assert 'id="ex-knee_pain"' in html                     # daily check-in
    jr = c.get("/?day=mon", headers={"Remote-User": "jmack"}).get_data(as_text=True)
    assert 'data-e="input_number.ex_box_squat_weight" value="25"' in jr
    assert "2026-11-04 07:30" in jr


def test_unknown_user_is_read_only(appmod):
    c = appmod.app.test_client()
    html = c.get("/?day=mon", headers={"Remote-User": "guest"}).get_data(as_text=True)
    assert "Read-only" in html and "<button" not in html
    r = c.post("/log", data={"item": "box_squat", "weight": "20", "reps": "10", "day": "mon"},
               headers={"Remote-User": "guest"})
    assert r.status_code == 403
    assert not any(m == "POST" for m, *_ in appmod._calls)


def test_log_writes_ha_and_csv(appmod):
    c = appmod.app.test_client()
    r = c.post("/log", data={"item": "box_squat", "slug": "box-squat", "weight": "22.5", "reps": "12", "day": "mon"},
               headers={"Remote-User": "rach", "Origin": "https://home.kmkdp.com"})
    assert r.status_code == 303 and "ok=box_squat" in r.headers["Location"]
    posts = [(u, b) for m, u, b in appmod._calls if m == "POST"]
    assert ("https://homeassistant.kmkdp.com/api/services/input_number/set_value",
            {"entity_id": "input_number.ex_box_squat_weight_rachel", "value": 22.5}) in posts
    assert any(b["entity_id"] == "input_number.ex_box_squat_reps_rachel" and b["value"] == 12 for _, b in posts)
    rows = list(csv.DictReader(open(appmod.LOG_CSV, encoding="utf-8")))
    assert rows[0]["person"] == "rachel" and rows[0]["item"] == "box_squat" and rows[0]["weight"] == "22.5"
    assert rows[0]["reps"] == "12" and rows[0]["seconds"] == ""


def test_done_and_timed_items(appmod):
    c = appmod.app.test_client()
    c.post("/log", data={"item": "hand_routine", "done": "1", "day": "tue"}, headers={"Remote-User": "jmack"})
    c.post("/log", data={"item": "wall_sit", "seconds": "40", "day": "mon"}, headers={"Remote-User": "jmack"})
    posts = [(u, b) for m, u, b in appmod._calls if m == "POST"]
    assert any(u.endswith("input_datetime/set_datetime") and b["entity_id"] == "input_datetime.ex_hand_routine_done"
               for u, b in posts)
    assert any(b.get("entity_id") == "input_number.ex_wall_sit_seconds" and b["value"] == 40 for _, b in posts)
    rows = list(csv.DictReader(open(appmod.LOG_CSV, encoding="utf-8")))
    assert [r["item"] for r in rows] == ["hand_routine", "wall_sit"] and rows[0]["done"] == "1"


@pytest.mark.parametrize("data", [{"item": "box_squat", "weight": "999"}, {"item": "box_squat", "reps": "abc"},
                                  {"item": "box_squat", "weight": "nan"}])
def test_bad_values_rejected(appmod, data):
    c = appmod.app.test_client()
    r = c.post("/log", data={**data, "day": "mon"}, headers={"Remote-User": "jmack"})
    assert r.status_code == 303 and "err=box_squat" in r.headers["Location"]
    assert not appmod.LOG_CSV.exists()


def test_bad_origin_and_unknown_item(appmod):
    c = appmod.app.test_client()
    assert c.post("/log", data={"item": "box_squat", "weight": "5"},
                  headers={"Remote-User": "jmack", "Origin": "https://evil.example"}).status_code == 403
    assert c.post("/log", data={"item": "nope"}, headers={"Remote-User": "jmack"}).status_code == 400


def test_missing_guide_and_ha_down(appmod, monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "GUIDE", tmp_path / "missing.json")
    monkeypatch.setattr(appmod.requests, "request", lambda *a, **k: (_ for _ in ()).throw(appmod.requests.ConnectionError()))
    c = appmod.app.test_client()
    assert "not been published" in c.get("/", headers={"Remote-User": "jmack"}).get_data(as_text=True)
    r = c.post("/log", data={"item": "box_squat", "weight": "20", "day": "mon"}, headers={"Remote-User": "jmack"})
    assert r.status_code == 303 and "err=box_squat" in r.headers["Location"]
    assert appmod.LOG_CSV.exists()          # the CSV still has the set even when HA is down
