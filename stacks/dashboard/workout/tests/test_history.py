"""history.py: CSV v1 -> v2 migration, per-exercise sessions, ISO-week goals,
planned items, treadmill minutes by source, the wl (weight + lengths) kind."""
import csv
import datetime
import json
from pathlib import Path

import pytest

import history as h
import migrate_csv

REPO = Path(__file__).resolve().parents[4]
CAT = json.loads((REPO / "stacks" / "home-assistant" / "tools" / "workout_catalog.json").read_text(encoding="utf-8"))

V1 = ("timestamp,person,item,slug,weight,reps,seconds,minutes,score,done\n"
      "2026-11-02T07:10:00-06:00,jacob,box_squat,box-squat,25,10,,,,\n"
      "2026-11-02T23:30:00-06:00,rachel,hand_routine,,,,,,,1\n"
      "2026-11-03T05:30:00+00:00,jacob,knee_pain,,,,,,2,\n")   # 05:30 UTC = 23:30 the day before in Chicago


def write(p: Path, text: str) -> Path:
    p.write_text(text, encoding="utf-8")
    return p


def rows(p: Path):
    with p.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def row(date, person, item, source="web", set_index="1", **vals):
    r = dict.fromkeys(h.CSV_HEAD, "")
    r.update(timestamp=f"{date}T08:00:00-06:00", date=date, person=person, item=item, source=source,
             set_index=set_index)
    r.update({k: str(v) for k, v in vals.items()})
    return r


# ------------------------------------------------------------------ migration
def test_v1_migrates_with_backup_and_second_run_is_noop(tmp_path):
    p = write(tmp_path / "workout-log.csv", V1)
    plan = h.migration_plan(p, datetime.date(2026, 11, 6))
    assert plan["action"] == "migrate" and plan["rows"] == 3
    assert h.ensure_schema(p, datetime.date(2026, 11, 6)) == "migrated"
    bak = tmp_path / "workout-log.v1-20261106.bak"
    assert bak.read_text(encoding="utf-8") == V1
    assert p.read_text(encoding="utf-8").splitlines()[0] == ",".join(h.CSV_HEAD)
    out = rows(p)
    assert [r["date"] for r in out] == ["2026-11-02", "2026-11-02", "2026-11-02"]
    assert {r["source"] for r in out} == {"migrated"} and {r["set_index"] for r in out} == {""}
    assert out[0]["weight"] == "25" and out[0]["reps"] == "10" and out[1]["done"] == "1" and out[2]["score"] == "2"
    before = p.read_text(encoding="utf-8")
    assert h.ensure_schema(p, datetime.date(2026, 11, 7)) == "none"
    assert p.read_text(encoding="utf-8") == before
    assert sorted(x.name for x in tmp_path.iterdir()) == ["workout-log.csv", "workout-log.v1-20261106.bak"]


def test_backup_never_overwritten_and_unknown_header_refused(tmp_path):
    p = write(tmp_path / "workout-log.csv", V1)
    write(tmp_path / "workout-log.v1-20261106.bak", "older")
    h.ensure_schema(p, datetime.date(2026, 11, 6))
    assert (tmp_path / "workout-log.v1-20261106.bak").read_text() == "older"
    assert (tmp_path / "workout-log.v1-20261106-2.bak").read_text(encoding="utf-8") == V1
    q = write(tmp_path / "other.csv", "a,b\n1,2\n")
    with pytest.raises(ValueError):
        h.ensure_schema(q)
    assert q.read_text() == "a,b\n1,2\n"
    assert h.ensure_schema(tmp_path / "missing.csv") == "none"


def test_migrate_cli_dry_run_changes_nothing(tmp_path, capsys):
    p = write(tmp_path / "workout-log.csv", V1)
    assert migrate_csv.main(["--dry-run", str(p)]) == 0
    assert "v1 -> v2, 3 rows" in capsys.readouterr().out
    assert p.read_text(encoding="utf-8") == V1 and len(list(tmp_path.iterdir())) == 1
    assert migrate_csv.main([str(p)]) == 0
    assert rows(p)[0]["source"] == "migrated"
    assert migrate_csv.main([str(p)]) == 0 and "already v2" in capsys.readouterr().out
    assert migrate_csv.main([str(write(tmp_path / "x.csv", "a\n1\n"))]) == 2


def test_csv_rows_reads_both_headers(tmp_path):
    v1 = h.csv_rows(write(tmp_path / "v1.csv", V1))
    assert v1[0]["date"] == "2026-11-02" and v1[0]["lengths"] == "" and set(v1[0]) == set(h.CSV_HEAD)
    assert h.csv_rows(tmp_path / "none.csv") == []


# ------------------------------------------------------------------ read side
def test_history_sessions_sets_and_top_set():
    hist = h.load([row("2026-11-02", "jacob", "box_squat", weight=25, reps=10),
                   row("2026-11-02", "jacob", "box_squat", set_index="2", weight=30, reps=8),
                   row("2026-11-02", "rachel", "box_squat", weight=15, reps=12),
                   row("2026-10-26", "jacob", "box_squat", source="migrated", set_index="", weight=20, reps=12)])
    s = hist.history_for("jacob", "box_squat")
    assert [x["date"] for x in s] == ["2026-10-26", "2026-11-02"]
    assert len(s[1]["sets"]) == 2 and s[1]["top"]["weight"] == 30 and s[1]["sets"][0]["set_index"] == 1
    assert s[0]["sets"][0]["set_index"] is None and s[0]["sets"][0]["source"] == "migrated"
    assert hist.history_for("jacob", "box_squat", since="2026-11-01")[0]["date"] == "2026-11-02"
    assert hist.history_for("rachel", "box_squat")[0]["top"]["weight"] == 15


def test_wl_rows_keep_weight_and_lengths():
    hist = h.load([row("2026-11-06", "rachel", "suitcase", weight=20, lengths=4),
                   row("2026-11-06", "rachel", "suitcase", set_index="2", weight=20, lengths=6)])
    s = hist.history_for("rachel", "suitcase")[0]
    assert [(x["weight"], x["lengths"], x["seconds"]) for x in s["sets"]] == [(20, 4, None), (20, 6, None)]
    assert s["top"]["lengths"] == 6
    item = next(it for it in CAT["items"] if it["id"] == "suitcase")
    assert item["kind"] == "wl"


def test_treadmill_minutes_split_by_source():
    hist = h.load([row("2026-11-02", "jacob", "treadmill_walk", minutes=15),
                   row("2026-11-02", "jacob", "treadmill_walk", source="treadmill", minutes=22.5),
                   row("2026-11-03", "jacob", "treadmill_walk", source="migrated", set_index="", minutes=10),
                   row("2026-11-02", "rachel", "treadmill_walk", source="treadmill", minutes=30)])
    t = hist.treadmill_minutes("jacob")
    assert t == [{"date": "2026-11-02", "total": 37.5, "by_source": {"web": 15, "treadmill": 22.5}},
                 {"date": "2026-11-03", "total": 10, "by_source": {"migrated": 10}}]
    assert hist.treadmill_minutes("rachel")[0]["by_source"] == {"treadmill": 30}


# ------------------------------------------------------------------ plan + week
WORKOUT = {"version": 1, "everyday": [
    {"num": "1", "title": "Glides", "dose": "", "slug": "hand-glides", "image": "", "everyday": False},
    {"num": "2", "title": "Hamstring", "dose": "", "slug": "pt-hamstring", "image": "", "everyday": False}],
    "days": []}
PLAN = {"mon": ["pt-slr", "pt-bridge", "pt-abduction", "box-squat", "rdl"], "tue": ["incline-press", "cs-row"],
        "wed": ["flex-cat-cow"], "thu": ["pt-slr", "pt-bridge", "pt-abduction", "hip-thrust"],
        "fri": ["one-arm-row", "suitcase"], "sat": ["pt-slr", "pt-bridge", "pt-abduction"], "sun": ["flex-sphinx"]}
for _d, _slugs in PLAN.items():
    WORKOUT["days"].append({"id": _d, "steps": [{"slug": s, "everyday": False} for s in _slugs] +
                            [{"slug": "treadmill-walk", "everyday": False}, {"slug": "", "everyday": True}]})


def test_planned_items_from_workout_json_and_real_catalog():
    mon = h.planned_items(WORKOUT, "mon", CAT)
    assert mon[:5] == ["slr", "bridge_ext", "hip_abduction", "box_squat", "rdl"]
    assert mon[5:8] == ["treadmill_walk", "hand_routine", "stretches"]        # everyday expanded, deduped
    assert mon[8:] == ["knee_pain", "hand_pain"]                                 # daily check-ins
    assert len(mon) == len(set(mon))
    assert "suitcase" in h.planned_items(WORKOUT, "fri", CAT)
    assert h.planned_items(None, "mon", CAT) == [] and h.planned_items(WORKOUT, "xxx", CAT) == []


def _full_day(date, person, items, **extra):
    out = []
    for it in items:
        if it in ("hand_routine", "stretches", "flex_flow", "yoga"):
            out.append(row(date, person, it, set_index="", done=1))
        elif it in ("knee_pain", "hand_pain"):
            out.append(row(date, person, it, set_index="", score=0))
        elif it == "treadmill_walk":
            out.append(row(date, person, it, minutes=extra.get("minutes", 20)))
        else:
            out.append(row(date, person, it, weight=10, reps=10))
    return out


def test_week_summary_goal_counts_for_both_people():
    plan = lambda d: h.planned_items(WORKOUT, d, CAT)
    monday = datetime.date(2026, 11, 2)                          # ISO 2026-W45
    rs = []
    for i, d in enumerate(h.DAY_IDS):                            # Jacob: every planned item every day
        rs += _full_day((monday + datetime.timedelta(days=i)).isoformat(), "jacob", plan(d))
    rs += _full_day("2026-11-02", "rachel", ["slr", "bridge_ext", "hand_routine", "knee_pain", "treadmill_walk"],
                    minutes=15)
    rs += [row("2026-11-03", "rachel", "treadmill_walk", source="treadmill", minutes=30),
           row("2026-11-09", "jacob", "box_squat", weight=99, reps=1)]          # next week: ignored
    hist = h.load(rs)
    j = hist.week_summary("jacob", "2026-W45", plan, CAT)
    assert j["start"] == "2026-11-02" and j["end"] == "2026-11-08"
    g = {k: v["count"] for k, v in j["goals"].items()}
    assert g == {"walks": 7, "pt": 3, "hands": 7, "stretches": 7, "flexibility": 2, "strength": 4, "pain": 7}
    assert j["goals"]["walks"]["minutes"] == 140 and j["goals"]["walks"]["met"]
    assert j["goals"]["strength"]["met"] and not j["goals"]["flexibility"]["met"]
    assert all(d["day_complete"] for d in j["days"])
    r = hist.week_summary("rachel", "2026-W45", plan, CAT)
    g = {k: v["count"] for k, v in r["goals"].items()}
    assert g == {"walks": 2, "pt": 0, "hands": 1, "stretches": 0, "flexibility": 0, "strength": 0, "pain": 0}
    assert r["goals"]["walks"]["minutes"] == 45 and not r["goals"]["walks"]["met"]
    mon = r["days"][0]
    assert not mon["day_complete"] and "hip_abduction" in mon["missing"] and "slr" in mon["done"]


def test_strength_needs_every_lift_on_a_strength_day():
    plan = lambda d: h.planned_items(WORKOUT, d, CAT)
    hist = h.load([row("2026-11-02", "jacob", "box_squat", weight=20, reps=8),     # rdl missing on Mon
                   row("2026-11-03", "jacob", "incline_press", weight=20, reps=8),
                   row("2026-11-03", "jacob", "cs_row", weight=20, reps=8)])
    w = hist.week_summary("jacob", "2026-W45", plan, CAT)
    assert w["goals"]["strength"]["count"] == 1 and [d["strength_session"] for d in w["days"]][:2] == [False, True]


def test_week_helpers():
    assert h.week_monday("2026-W01") == datetime.date(2025, 12, 29)
    assert h.iso_week_of(datetime.date(2026, 11, 8)) == "2026-W45"
    with pytest.raises(ValueError):
        h.week_monday("2026-W60")
