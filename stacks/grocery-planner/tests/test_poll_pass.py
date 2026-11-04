"""Poll pass: a single-store approval drops the other store's entries for THIS
week; checked / delayed entries never reach a list; repeat passes are no-ops."""
import datetime

from grocery import run as R
from grocery import shoplist as S

CT = datetime.timezone(datetime.timedelta(hours=-6))
NOW = datetime.datetime(2026, 11, 12, 7, 5, tzinfo=CT)       # Thu poll, W47


def _e(eid, food, cat, mp=None, checked=False, delay=None):
    return {"id": eid, "amount": 1, "unit": None, "checked": checked,
            "delay_until": delay,
            "list_recipe_data": {"mealplan": mp} if mp is not None else None,
            "food": {"name": food, "food_onhand": False,
                     "supermarket_category": {"name": cat}}}


NP, TG = "NewPi/weekly: Produce", "Target/weekly: Canned"


def test_checked_and_future_delayed_entries_are_not_listed(cfg):
    entries = [
        _e(1, "spinach", NP),
        _e(2, "kale", NP, checked=True),                                 # bought last week
        _e(3, "beans", TG, delay="2026-11-15T00:00:00-06:00"),           # hidden until Sun
        _e(4, "corn", TG, delay="2026-11-01T00:00:00Z"),                 # delay passed
        _e(5, "rice", TG, delay="2026-11-15T00:00:00"),                  # naive -> read in now's tz
    ]
    lists, _, _ = S.build_store_lists(entries, cfg, "weekly", now=NOW)
    assert [i.food for a in lists["NewPi"].aisles.values() for i in a] == ["spinach"]
    assert [i.food for a in lists["Target"].aisles.values() for i in a] == ["corn"]


def test_newpi_only_drops_this_weeks_target_entries(cfg):
    entries = [
        _e(1, "spinach", NP, mp=101),
        _e(2, "beans", TG, mp=101),
        _e(3, "tomatoes", TG, mp=102),
        _e(4, "olives", TG, mp=55),        # an older week's entry: untouched
        _e(5, "paper towels", TG),         # ad-hoc add-box entry: untouched
        _e(6, "corn", TG, mp=102, checked=True),
    ]
    drop = R.entries_to_drop(entries, cfg, S.approved_stores("newpi"), {101, 102}, now=NOW)
    assert sorted(eid for eid, _, _ in drop) == [2, 3]
    assert {store for _, store, _ in drop} == {"Target"}


def test_target_only_drops_newpi(cfg):
    entries = [_e(1, "spinach", NP, mp=101), _e(2, "beans", TG, mp=101)]
    drop = R.entries_to_drop(entries, cfg, S.approved_stores("target"), {101}, now=NOW)
    assert drop == [(1, "NewPi", "spinach")]


def test_all_and_skip_drop_nothing(cfg):
    entries = [_e(1, "spinach", NP, mp=101), _e(2, "beans", TG, mp=101)]
    assert R.entries_to_drop(entries, cfg, S.approved_stores("all"), {101}, now=NOW) == []
    assert R.entries_to_drop(entries, cfg, S.approved_stores("skipped"), {101}, now=NOW) == []


def test_week_mealplan_ids_use_the_marker():
    mps = [{"id": 101, "note": "x [gp:2026-W47]"}, {"id": 9, "note": "[gp:2026-W46]"},
           {"id": 10, "note": None}]
    assert R.week_mealplan_ids(mps, "2026-W47") == {101}


class _FakeTandoor:
    def __init__(self, entries, mps):
        self.entries, self.mps, self.deleted = entries, mps, []

    def meal_plan(self, a, b):
        return self.mps

    def shopping_list_entries(self):
        return [e for e in self.entries if e["id"] not in self.deleted]

    def delete(self, path):
        self.deleted.append(int(path.rstrip("/").rsplit("/", 1)[1]))


class _FakeVikunja:
    def __init__(self, task):
        self.task = task

    def find_by_title(self, s):
        return self.task

    outcome = staticmethod(__import__("grocery.vikunja", fromlist=["Vikunja"]).Vikunja.outcome)


def _run_poll(cfg, monkeypatch, task, t):
    monkeypatch.setattr(R, "_vikunja", lambda cfg: _FakeVikunja(task))
    start = datetime.date(2026, 11, 15)
    return R._poll_pass(cfg, t, start, start + datetime.timedelta(days=6), "2026-W47")


def test_poll_pass_deletes_once_and_rerun_is_noop(cfg, monkeypatch, capsys):
    t = _FakeTandoor([_e(1, "spinach", NP, mp=101), _e(2, "beans", TG, mp=101)],
                     [{"id": 101, "note": "[gp:2026-W47]"}])
    task = {"id": 91, "done": True, "description": "<p>APPROVALS-RESULT: newpi</p>"}
    assert _run_poll(cfg, monkeypatch, task, t) == 0
    assert t.deleted == [2]
    assert _run_poll(cfg, monkeypatch, task, t) == 0      # 12:05 pass
    assert t.deleted == [2]
    out = capsys.readouterr().out
    assert '"dropped": 1' in out and '"dropped": 0' in out


def test_open_task_changes_nothing(cfg, monkeypatch):
    t = _FakeTandoor([_e(2, "beans", TG, mp=101)], [{"id": 101, "note": "[gp:2026-W47]"}])
    assert _run_poll(cfg, monkeypatch, {"id": 91, "done": False}, t) == 0
    assert t.deleted == []
