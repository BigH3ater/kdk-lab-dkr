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


# ------------------------------------------------------------------ CSV v2 + JSON routes
def _rows(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_log_writes_v2_columns(appmod):
    c = appmod.app.test_client()
    c.post("/log", data={"item": "box_squat", "slug": "box-squat", "weight": "22.5", "reps": "12", "day": "mon"},
           headers={"Remote-User": "rach"})
    c.post("/log", data={"item": "hand_routine", "done": "1", "day": "mon"}, headers={"Remote-User": "rach"})
    head = appmod.LOG_CSV.read_text(encoding="utf-8").splitlines()[0].split(",")
    assert head == appmod.CSV_HEAD and "lengths" in head and head[-1] == "source"
    rows = _rows(appmod.LOG_CSV)
    assert rows[0]["set_index"] == "1" and rows[0]["source"] == "web" and rows[0]["date"] == rows[0]["timestamp"][:10]
    assert rows[1]["set_index"] == "" and rows[1]["done"] == "1"


def test_suitcase_logs_weight_and_lengths(appmod):
    c = appmod.app.test_client()
    r = c.post("/log", data={"item": "suitcase", "slug": "suitcase", "weight": "25", "lengths": "6", "day": "fri"},
               headers={"Remote-User": "jmack"})
    assert r.status_code == 303 and "ok=suitcase" in r.headers["Location"]
    posts = [b for m, u, b in appmod._calls if m == "POST"]
    assert {"entity_id": "input_number.ex_suitcase_lengths", "value": 6} in posts
    assert {"entity_id": "input_number.ex_suitcase_weight", "value": 25} in posts
    row = _rows(appmod.LOG_CSV)[0]
    assert row["lengths"] == "6" and row["weight"] == "25" and row["seconds"] == ""
    r = c.post("/log", data={"item": "suitcase", "lengths": "25", "day": "fri"}, headers={"Remote-User": "jmack"})
    assert "err=suitcase" in r.headers["Location"]           # 0-20 lengths


def test_v1_log_is_migrated_before_the_first_write(appmod):
    appmod.LOG_CSV.parent.mkdir(parents=True)
    appmod.LOG_CSV.write_text("timestamp,person,item,slug,weight,reps,seconds,minutes,score,done\n"
                              "2026-11-02T07:10:00-06:00,jacob,box_squat,box-squat,25,10,,,,\n", encoding="utf-8")
    appmod.app.test_client().post("/log", data={"item": "wall_sit", "seconds": "40", "day": "mon"},
                                  headers={"Remote-User": "jmack"})
    rows = _rows(appmod.LOG_CSV)
    assert [(r["item"], r["source"]) for r in rows] == [("box_squat", "migrated"), ("wall_sit", "web")]
    assert len(list(appmod.LOG_CSV.parent.glob("workout-log.v1-*.bak"))) == 1


def test_history_json(appmod):
    c = appmod.app.test_client()
    for w in ("20", "25"):
        c.post("/log", data={"item": "box_squat", "weight": w, "reps": "10", "day": "mon"}, headers={"Remote-User": "rach"})
    c.post("/log", data={"item": "treadmill_walk", "minutes": "15", "day": "mon"}, headers={"Remote-User": "rach"})
    r = c.get("/api/history.json?item=box_squat", headers={"Remote-User": "rach"})
    d = r.get_json()
    assert r.status_code == 200 and d["person"] == "rachel" and d["kind"] == "w"
    assert len(d["sessions"]) == 1 and [s["weight"] for s in d["sessions"][0]["sets"]] == [20, 25]
    assert d["sessions"][0]["top"]["weight"] == 25
    # the viewer may read the other person (by login or key); Jacob has nothing yet
    assert c.get("/api/history.json?item=box_squat&person=jmack", headers={"Remote-User": "rach"}).get_json()["sessions"] == []
    j = c.get("/api/history.json?item=box_squat&person=rachel", headers={"Remote-User": "jmack"}).get_json()
    assert len(j["sessions"]) == 1
    t = c.get("/api/history.json?item=treadmill_walk", headers={"Remote-User": "rach"}).get_json()
    assert t["treadmill_minutes"][0]["by_source"] == {"web": 15}
    assert c.get("/api/history.json?item=box_squat&since=2999-01-01", headers={"Remote-User": "rach"}).get_json()["sessions"] == []
    assert c.get("/api/history.json?item=nope", headers={"Remote-User": "rach"}).status_code == 400
    assert c.get("/api/history.json?item=box_squat&since=x", headers={"Remote-User": "rach"}).status_code == 400
    assert c.get("/api/history.json?item=box_squat&person=guest", headers={"Remote-User": "rach"}).status_code == 400
    assert c.get("/api/history.json?item=box_squat", headers={"Remote-User": "guest"}).status_code == 403
    assert c.get("/api/history.json?item=box_squat").status_code == 403


def test_week_json(appmod):
    c = appmod.app.test_client()
    c.post("/log", data={"item": "knee_pain", "score": "2", "day": "mon"}, headers={"Remote-User": "jmack"})
    c.post("/log", data={"item": "hand_pain", "score": "0", "day": "mon"}, headers={"Remote-User": "jmack"})
    r = c.get("/api/week.json", headers={"Remote-User": "rach"})
    d = r.get_json()
    assert r.status_code == 200 and d["person"] == "rachel" and len(d["days"]) == 7 and d["plan_published"]
    assert d["goals"]["pain"]["count"] == 0 and d["days"][0]["day"] == "mon"
    # the test guide plans box squat + wall sit + hand routine + daily check-ins every day
    assert d["days"][0]["planned"][:2] == ["box_squat", "wall_sit"] and "knee_pain" in d["days"][0]["planned"]
    j = c.get("/api/week.json?person=jmack", headers={"Remote-User": "rach"}).get_json()
    assert j["goals"]["pain"]["count"] == 1 and j["goals"]["pain"]["target"] == 7
    assert c.get("/api/week.json?week=2026-W45", headers={"Remote-User": "jmack"}).get_json()["start"] == "2026-11-02"
    assert c.get("/api/week.json?week=bad", headers={"Remote-User": "jmack"}).status_code == 400
    assert c.get("/api/week.json", headers={"Remote-User": "guest"}).status_code == 403
