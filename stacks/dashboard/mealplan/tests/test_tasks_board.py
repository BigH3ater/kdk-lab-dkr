"""Household tasks strip (/meal/tasks): every open Vikunja task must reach the
board. Regression for 2026-11-09, when 28 of 83 open chores rendered: each
project column was cut at 6 rows and anything due past 14 days was dropped."""
import datetime
import re

import app

TODAY = datetime.date(2026, 11, 9)
PROJECTS = {4: "Cleaning", 5: "Dogs", 8: "Water Filtration", 12: "Approvals"}


def _t(tid, title, due, pid=4, done=False):
    return {"id": tid, "title": title, "project_id": pid, "done": done,
            "due_date": f"{due}T17:00:00-06:00" if due else "0001-01-01T00:00:00Z"}


def _ids(html):
    return [int(i) for i in re.findall(r"name='task_id' value='(\d+)'", html)]


def test_no_per_column_cap():
    # 34 overdue cleaning chores, as live on 2026-11-09: all of them render
    tasks = [_t(i, f"Chore {i}", "2026-10-01") for i in range(1, 35)]
    html = app._tasks_board(PROJECTS, tasks, TODAY)
    assert sorted(_ids(html)) == list(range(1, 35))
    assert "<span class='n'>34</span>" in html
    assert "34 late" in html


def test_later_tasks_are_folded_not_dropped():
    tasks = [_t(1, "Clean 50 micron filter", "2026-12-04", pid=8),
             _t(2, "Replace pre-filter 1", "2027-06-04", pid=8),
             _t(3, "Brush the beasts", "2026-11-16", pid=5)]
    html = app._tasks_board(PROJECTS, tasks, TODAY)
    assert sorted(_ids(html)) == [1, 2, 3]
    water = html.split("data-cat='Water Filtration'")[1]
    assert "+2 later" in water and "<details class='later'>" in water
    assert "nothing due in 2 weeks" in water
    dogs = html.split("data-cat='Dogs'")[1].split("data-cat=")[0]
    assert "<details" not in dogs


def test_undated_task_is_not_overdue():
    # Vikunja sends an unset due date as Go's zero time
    html = app._tasks_board(PROJECTS, [_t(1, "Someday", None)], TODAY)
    assert _ids(html) == [1]
    assert "no date" in html
    assert "task over" not in html


def test_done_skipped_and_order():
    tasks = [_t(1, "Later one", "2026-11-20"), _t(2, "Done", "2026-10-01", done=True),
             _t(3, "Undated", None), _t(4, "Oldest", "2026-09-27"), _t(5, "Today", "2026-11-09")]
    html = app._tasks_board(PROJECTS, tasks, TODAY)
    assert _ids(html) == [4, 5, 1, 3]
    assert ">today<" in html


def test_titles_and_project_names_escaped():
    html = app._tasks_board({4: "<b>Cleaning</b>"}, [_t(1, "<img src=x onerror=alert(1)>", "2026-11-10")], TODAY)
    assert "<img" not in html and "<b>" not in html
    assert "&lt;img" in html


def test_empty_board():
    assert "No open chores" in app._tasks_board(PROJECTS, [], TODAY)


class _Resp:
    def __init__(self, rows, pages):
        self._rows, self.headers, self.text = rows, {"x-pagination-total-pages": str(pages)}, "x"

    def raise_for_status(self):
        pass

    def json(self):
        return self._rows


def test_vik_all_follows_pagination(monkeypatch):
    # Vikunja caps a page at maxitemsperpage (50) whatever per_page asks for
    pages = {1: [{"id": i} for i in range(50)], 2: [{"id": i} for i in range(50, 60)]}
    seen = []

    def fake_get(url, **kw):
        page = int(re.search(r"page=(\d+)$", url).group(1))
        seen.append(url)
        return _Resp(pages[page], 2)

    monkeypatch.setattr(app.requests, "get", fake_get)
    rows = app._vik_all("/projects/4/tasks", "tok")
    assert [r["id"] for r in rows] == list(range(60))
    assert len(seen) == 2 and "?per_page=50&page=2" in seen[1]


def test_tasks_view_reads_every_project(monkeypatch):
    calls = []

    def fake_all(path, token):
        calls.append(path)
        if path == "/projects":
            return [{"id": 2, "title": "Inbox"}, {"id": 4, "title": "Cleaning"}]
        return [_t(7 if "2" in path else 8, "x", "2026-11-10", pid=int(path.split("/")[2]))]

    monkeypatch.setattr(app, "_vik_all", fake_all)
    monkeypatch.setitem(app.VIKUNJA_TOKENS, "jmack", "tok")
    html = app.app.test_client().get("/tasks").get_data(as_text=True)
    assert calls == ["/projects", "/projects/2/tasks", "/projects/4/tasks"]
    assert sorted(_ids(html)) == [7, 8]
