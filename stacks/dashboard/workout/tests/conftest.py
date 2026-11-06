"""Shared fixtures for the workout sidecar tests: a test workout.json (v1), the real
catalog, a temp CSV and a fake Home Assistant (no network)."""
import importlib
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
