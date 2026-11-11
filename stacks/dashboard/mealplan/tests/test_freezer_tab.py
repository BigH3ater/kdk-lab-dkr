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


def _with_unknowns(p):
    """The seed plus a fill-in cut and a package cut without portions (the seed
    has neither since 2026-11-11: no fish rows, O'Connell packs set to 2)."""
    data = json.loads(p.read_text())
    data["cuts"] += [{"id": "tuna-steaks", "name": "Tuna steaks", "unit": "portion", "count": 0,
                      "package": "", "fill_in": True, "source": "NewPi"},
                     {"id": "bacon-x", "name": "Bacon X", "unit": "package", "count": 3,
                      "package": "", "portions_per_package": None, "source": "OConnell"}]
    p.write_text(json.dumps(data))


def _cuts(p):
    return {c["id"]: c for c in json.loads(p.read_text())["cuts"]}


def test_seed_renders_with_markers(fz, repo_cfg):
    _with_unknowns(fz)
    html = app._render_freezer(freezer.load(), repo_cfg["config"]["standing_orders"])
    assert "Filet mignon" in html and "2 portions" in html and "30 lb" in html
    rows = {k: v for k, v in ((r[r.index("name='cut' value='") + 18:].split("'", 1)[0], r)
                              for r in html.split("<div class='gi fz")[1:])}
    assert rows["tuna-steaks"].startswith(" fillin") and "fill in" in rows["tuna-steaks"]
    assert "<span class='cnt'>?</span>" in rows["tuna-steaks"]
    chick = rows["chicken-breast"]                       # owner 2026-11-11: 2 per package
    assert "set portions" not in chick and "16 pkg" in chick and "2 per package" in chick
    assert "set portions" in rows["bacon-x"] and "name='n'" in rows["bacon-x"]
    assert "fillin" not in rows["ribeye"] and "mark" not in rows["ribeye"]
    assert "Order arrived: O&#39;Connell" in html and "Order arrived: Amana" in html
    assert "undo my last tap" in html and "style=" not in html
    # review 2026-11-11 #6/#7: the page says what undo does; the buttons post once
    assert "Undo reverts only your own last tap" in html and "weekly build" in html
    assert html.count("class='fzonce'") == 2 + 1               # two orders + undo
    assert "name='package_lb'" in html
    assert "form.fzonce" in app.GROCERIES_JS and "disabled = true" in app.GROCERIES_JS


def test_missing_file_says_so_without_buttons(tmp_path, monkeypatch):
    monkeypatch.setattr(freezer, "FILE", str(tmp_path / "none.json"))
    html = app._render_freezer(freezer.load(), {})
    assert "No freezer inventory yet" in html and "<form" not in html


def test_adjust_set_clears_fill_in_and_keeps_bak(fz):
    _with_unknowns(fz)
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
    freezer.change("portions", {"cut": "chicken-breast", "n": "3"}, "jmack")
    assert _cuts(fz)["chicken-breast"]["portions_per_package"] == 3
    freezer.change("add", {"name": "Short ribs", "unit": "lb", "count": "8", "package": "4 lb"}, "jmack")
    assert _cuts(fz)["short-ribs"] == {"id": "short-ribs", "name": "Short ribs", "unit": "lb",
                                       "count": 8, "package": "4 lb", "source": "manual"}
    freezer.change("add", {"name": "Lamb chops", "unit": "package", "count": "2",
                           "package": "2 x 8 oz", "package_lb": "1"}, "jmack")
    assert _cuts(fz)["lamb-chops"]["package_lb"] == 1
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
    # review 2026-11-11 #8: a portion/package cut needs its weight (no guessing)
    ("add", {"name": "Lamb chops", "unit": "package", "count": "2"}),
    ("add", {"name": "Duck legs", "unit": "portion", "count": "2", "package_lb": ""}),
    ("add", {"name": "Duck legs", "unit": "portion", "count": "2", "package_lb": "lots"}),
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


# ---- review 2026-11-11 #6/#7/#9/#5 ------------------------------------------------
def test_undo_never_reverts_the_planners_reservation(fz):
    freezer.change("adjust", {"cut": "ribeye", "delta": "-1"}, "jmack")       # bak: ribeye 6
    data = json.loads(fz.read_text())
    data["updated_by"] = freezer.PLANNER                                      # the weekly build
    {c["id"]: c for c in data["cuts"]}["ribeye"]["count"] = 3
    fz.write_text(json.dumps(data))
    raw, bak = fz.read_text(), (fz.parent / "freezer.json.bak").read_text()
    with pytest.raises(freezer.FreezerError, match="weekly build"):
        freezer.change("undo", {}, "jmack")
    assert fz.read_text() == raw and (fz.parent / "freezer.json.bak").read_text() == bak
    # a tap after the build: undo reverts that tap to the reserved count
    freezer.change("adjust", {"cut": "ribeye", "delta": "-1"}, "jmack")
    freezer.change("undo", {}, "jmack")
    assert _cuts(fz)["ribeye"]["count"] == 3
    freezer.change("undo", {}, "jmack")                                       # redo is allowed
    assert _cuts(fz)["ribeye"]["count"] == 2
    freezer.change("adjust", {"cut": "ribeye", "delta": "1"}, "jmack")
    assert "restored_by" not in json.loads(fz.read_text())


def test_order_arrived_twice_within_12h_is_refused(fz, repo_cfg):
    orders = repo_cfg["config"]["standing_orders"]
    t0 = datetime.datetime(2026, 11, 12, 18, 0, tzinfo=datetime.timezone.utc)
    freezer.change("arrived", {"order": "amana"}, "jmack", orders, now=t0)
    after = fz.read_text()
    assert json.loads(after)["arrivals"] == [{"order": "amana", "at": "2026-11-12T18:00:00+00:00"}]
    with pytest.raises(freezer.FreezerError, match="already recorded"):
        freezer.change("arrived", {"order": "amana"}, "jmack", orders,
                       now=t0 + datetime.timedelta(minutes=1))
    assert fz.read_text() == after
    freezer.change("arrived", {"order": "oconnell"}, "jmack", orders,     # another order is fine
                   now=t0 + datetime.timedelta(minutes=2))
    freezer.change("arrived", {"order": "amana"}, "jmack", orders,        # the next delivery
                   now=t0 + datetime.timedelta(hours=13))
    assert [a["order"] for a in json.loads(fz.read_text())["arrivals"]] == ["amana", "oconnell", "amana"]


def test_undo_writes_bak_before_the_file(fz, monkeypatch):
    freezer.change("adjust", {"cut": "ribeye", "delta": "-1"}, "jmack")
    order = []
    real = freezer._write
    monkeypatch.setattr(freezer, "_write", lambda p, raw: (order.append(os.path.basename(p)), real(p, raw)))
    freezer.change("undo", {}, "jmack")
    assert order == ["freezer.json.bak", "freezer.json"]


def test_change_takes_the_shared_file_lock_and_unique_tmp(fz):
    import fcntl
    import threading
    lock = open(fz.parent / freezer.LOCK_NAME, "a+")
    fcntl.flock(lock, fcntl.LOCK_EX)                       # the planner mid-reservation
    th = threading.Thread(target=lambda: freezer.change(
        "adjust", {"cut": "ribeye", "delta": "-1"}, "jmack"))
    th.start()
    th.join(0.3)
    assert th.is_alive() and _cuts(fz)["ribeye"]["count"] == 6
    data = json.loads(fz.read_text())                      # the planner's write lands
    {c["id"]: c for c in data["cuts"]}["ribeye"]["count"] = 4
    fz.write_text(json.dumps(data))
    fcntl.flock(lock, fcntl.LOCK_UN)
    lock.close()
    th.join(5)
    assert _cuts(fz)["ribeye"]["count"] == 3               # applied on top, not lost
    src = open(freezer.__file__).read()
    assert "mkstemp" in src and "getpid()" not in src
