"""Pantry tab Freezer section + Thaw tab reserved list: the seed renders with
its fill-in / set-portions markers, −/+/set/portions/add/arrived/undo write the
file atomically with a .bak, refusals change nothing, the route is
home-host only and logs the Authelia user."""
import datetime
import json
import os
import shutil

import pytest

import app
import freezer

SEED = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))), "grocery-planner", "freezer-seed.json")


@pytest.fixture
def fz(tmp_path, monkeypatch):
    p = tmp_path / "freezer.json"
    shutil.copy(SEED, p)
    monkeypatch.setattr(freezer, "FILE", str(p))
    return p


def _cuts(p):
    return {c["id"]: c for c in json.loads(p.read_text())["cuts"]}


def test_seed_renders_with_markers(fz, repo_cfg):
    html = app._render_freezer(freezer.load(), repo_cfg["config"]["standing_orders"])
    assert "Filet mignon" in html and "2 portions" in html and "30 lb" in html
    rows = {k: v for k, v in ((r[r.index("name='cut' value='") + 18:].split("'", 1)[0], r)
                              for r in html.split("<div class='gi fz")[1:])}
    assert rows["tuna-steaks"].startswith(" fillin") and "fill in" in rows["tuna-steaks"]
    assert "<span class='cnt'>?</span>" in rows["tuna-steaks"]
    chick = rows["chicken-breast"]
    assert "set portions" in chick and "16 pkg" in chick and "name='n'" in chick
    assert "fillin" not in rows["ribeye"] and "mark" not in rows["ribeye"]
    assert "Order arrived: O&#39;Connell" in html and "Order arrived: Amana" in html
    assert "undo last change" in html and "style=" not in html


def test_missing_file_says_so_without_buttons(tmp_path, monkeypatch):
    monkeypatch.setattr(freezer, "FILE", str(tmp_path / "none.json"))
    html = app._render_freezer(freezer.load(), {})
    assert "No freezer inventory yet" in html and "<form" not in html


def test_adjust_set_clears_fill_in_and_keeps_bak(fz):
    freezer.change("adjust", {"cut": "filet-mignon", "delta": "-1"}, "jmack")
    assert _cuts(fz)["filet-mignon"]["count"] == 1
    bak = json.loads((fz.parent / "freezer.json.bak").read_text())
    assert {c["id"]: c for c in bak["cuts"]}["filet-mignon"]["count"] == 2
    freezer.change("set", {"cut": "tuna-steaks", "count": "6"}, "rach")
    c = _cuts(fz)["tuna-steaks"]
    assert c["count"] == 6 and "fill_in" not in c
    assert json.loads(fz.read_text())["updated_by"] == "rach"
    freezer.change("adjust", {"cut": "ground-beef", "delta": "-1"}, "jmack")
    freezer.change("adjust", {"cut": "filet-mignon", "delta": "-5"}, "jmack")
    assert _cuts(fz)["ground-beef"]["count"] == 29 and _cuts(fz)["filet-mignon"]["count"] == 0
    assert not [n for n in os.listdir(fz.parent) if n.endswith(".tmp")]


def test_portions_add_arrived_undo(fz, repo_cfg):
    freezer.change("portions", {"cut": "chicken-breast", "n": "2"}, "jmack")
    assert _cuts(fz)["chicken-breast"]["portions_per_package"] == 2
    freezer.change("add", {"name": "Short ribs", "unit": "lb", "count": "8", "package": "4 lb"}, "jmack")
    assert _cuts(fz)["short-ribs"] == {"id": "short-ribs", "name": "Short ribs", "unit": "lb",
                                       "count": 8, "package": "4 lb", "source": "manual"}
    orders = repo_cfg["config"]["standing_orders"]
    before = _cuts(fz)
    freezer.change("arrived", {"order": "oconnell"}, "jmack", orders)
    after = _cuts(fz)
    assert after["chicken-breast"]["count"] == before["chicken-breast"]["count"] + 16
    assert after["pork-chops"]["count"] == 8 and after["bacon"]["count"] == 10
    freezer.change("undo", {}, "jmack")
    assert _cuts(fz)["chicken-breast"]["count"] == before["chicken-breast"]["count"]
    freezer.change("undo", {}, "jmack")                    # undo the undo
    assert _cuts(fz)["chicken-breast"]["count"] == before["chicken-breast"]["count"] + 16


@pytest.mark.parametrize("action,form", [
    ("adjust", {"cut": "nope", "delta": "1"}),
    ("set", {"cut": "ribeye", "count": "lots"}),
    ("set", {"cut": "ribeye", "count": "-3"}),
    ("add", {"name": "Ribeye", "unit": "portion"}),          # slug clash with an existing cut
    ("add", {"name": "x", "unit": "kg"}),
    ("arrived", {"order": "costco"}),
    ("explode", {}),
])
def test_refusals_change_nothing(fz, action, form):
    raw = fz.read_text()
    with pytest.raises(freezer.FreezerError):
        freezer.change(action, form, "jmack", {})
    assert fz.read_text() == raw and not (fz.parent / "freezer.json.bak").exists()


def test_route_writes_and_logs_user(fz, repo_cfg, caplog):
    client = app.app.test_client()
    caplog.set_level("INFO")
    r = client.post("/groceries/freezer", data={"action": "adjust", "cut": "ribeye", "delta": "-1"},
                    headers={"Remote-User": "rach"})
    assert r.status_code == 303 and r.headers["Location"].endswith("#pantry")
    assert _cuts(fz)["ribeye"]["count"] == 5
    assert "freezer change by rach: ribeye 6 -> 5" in caplog.text


def test_route_is_not_on_the_kitchen_host(fz, monkeypatch):
    monkeypatch.setattr(app, "_is_kitchen_request", lambda: True)
    monkeypatch.setattr(app, "_pairing", lambda: (object(), object()))
    r = app.app.test_client().post("/groceries/freezer",
                                   data={"action": "adjust", "cut": "ribeye", "delta": "-1"})
    assert r.status_code == 404 and _cuts(fz)["ribeye"]["count"] == 6


def test_thaw_tab_lists_the_reserved_cuts(fz):
    data = json.loads(fz.read_text())
    data["reservations"] = [
        {"week": "2026-W48", "cut": "filet-mignon", "amount": 2, "unit": "portion",
         "recipe": "Filet Mignon", "date": "2026-11-28"},
        {"week": "2026-W40", "cut": "ribeye", "amount": 2, "unit": "portion",
         "recipe": "Old", "date": "2026-10-03"}]
    fz.write_text(json.dumps(data))
    today = datetime.date(2026, 11, 22)                 # Sunday of W48
    keys = app._plan_week_keys(today)
    assert keys == {"2026-W48", "2026-W49"}
    rows = freezer.reserved(freezer.load(), keys)
    html = app._render_thaw([], today, rows)
    assert "Reserved from the freezer" in html and "filet-mignon" in html
    assert "2 portions" in html and "Sat 28 · Filet Mignon" in html and "Old" not in html
