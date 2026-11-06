"""Workout log history: the CSV schema, its v1 -> v2 migration, and the read side
(per-exercise sessions, ISO-week goal counters, planned items, treadmill minutes).

Pure Python (stdlib only, no Flask) so app.py, migrate_csv.py and the tests share it.

CSV v2 (one row per logged set or tick; /log/workout-log.csv on kdk-dkr-01):
  timestamp  ISO time with offset, America/Chicago
  date       the local day the row counts for (YYYY-MM-DD)
  person     catalog person key (jacob, rachel)
  item       catalog item id (box_squat, treadmill_walk, ...)
  slug       vault picture slug the form was next to ("" for check-ins)
  set_index  1, 2, ... for a set; blank for done/score rows and migrated rows
  weight reps seconds lengths minutes score   blank when not logged
  done       "1" for a ticked routine
  source     web | treadmill | migrated

v1 (before 2026-11) had no date, set_index, lengths or source. ensure_schema()
rewrites a v1 file in place (atomic) after copying it to workout-log.v1-<YYYYMMDD>.bak.
"""
from __future__ import annotations

import csv
import datetime
import os
import shutil
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo(os.environ.get("TZ", "America/Chicago"))

CSV_HEAD_V1 = ["timestamp", "person", "item", "slug", "weight", "reps", "seconds", "minutes", "score", "done"]
CSV_HEAD = ["timestamp", "date", "person", "item", "slug", "set_index", "weight", "reps", "seconds", "lengths",
            "minutes", "score", "done", "source"]
SET_FIELDS = ["weight", "reps", "seconds", "lengths", "minutes"]
DAY_IDS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

# Weekly goals (vault: 3. Resources/Exercise). Strength days are Mon/Tue/Thu/Fri.
STRENGTH_GROUPS = {"lower_a", "upper_a", "lower_b", "upper_b"}
STRENGTH_DAYS = {"mon", "tue", "thu", "fri"}
PT_BLOCK = ("slr", "bridge_ext", "hip_abduction")
PAIN = ("knee_pain", "hand_pain")
WALK_ITEM = "treadmill_walk"
GOALS = {"walks": 7, "walk_minutes": 125, "pt": 3, "hands": 3, "stretches": 7, "flexibility": 3,
         "strength": 4, "pain": 7}


# ------------------------------------------------------------------ schema / migration
def date_of(timestamp: str) -> str:
    """Local (America/Chicago) date of an ISO timestamp; naive times are taken as local."""
    try:
        t = datetime.datetime.fromisoformat(timestamp.strip())
    except (ValueError, AttributeError):
        return ""
    t = t.replace(tzinfo=TZ) if t.tzinfo is None else t.astimezone(TZ)
    return t.date().isoformat()


def read_header(path: Path) -> list[str] | None:
    """The file's header row, or None when the file is missing or empty."""
    if not path.exists() or path.stat().st_size == 0:
        return None
    with path.open(newline="", encoding="utf-8") as fh:
        return next(csv.reader(fh), None)


def upgrade_row(row: dict) -> dict:
    """A v1 row as a v2 row: date from the timestamp, no set index, no lengths, source=migrated."""
    out = {k: (row.get(k) or "") for k in CSV_HEAD}
    out["date"] = date_of(out["timestamp"])
    out["set_index"] = ""
    out["lengths"] = ""
    out["source"] = "migrated"
    return out


def migration_plan(path: Path, today: datetime.date | None = None) -> dict:
    """What ensure_schema() would do, without touching anything."""
    head = read_header(path)
    if head is None:
        return {"action": "none", "reason": "missing or empty (the header is written on the first log)"}
    if head == CSV_HEAD:
        return {"action": "none", "reason": "already v2"}
    if head != CSV_HEAD_V1:
        return {"action": "refuse", "reason": f"unknown header {head!r}"}
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    return {"action": "migrate", "rows": len(rows), "backup": str(_backup_path(path, today)),
            "undated": sum(1 for r in rows if not date_of(r.get("timestamp", "")))}


def _backup_path(path: Path, today: datetime.date | None = None) -> Path:
    today = today or datetime.datetime.now(TZ).date()
    bak = path.with_name(f"{path.stem}.v1-{today:%Y%m%d}.bak")
    n = 2
    while bak.exists():                     # never overwrite an earlier backup
        bak = path.with_name(f"{path.stem}.v1-{today:%Y%m%d}-{n}.bak")
        n += 1
    return bak


def ensure_schema(path: Path, today: datetime.date | None = None) -> str:
    """Make `path` a v2 file. Returns "none" (missing/empty/already v2) or "migrated".

    v1: copy to <stem>.v1-<YYYYMMDD>.bak beside it, then write the v2 rows to a temp
    file in the same directory and os.replace() it over the original, so a crash
    leaves either the old or the new file, never half of one. Idempotent.
    An unknown header raises ValueError (nothing is changed)."""
    plan = migration_plan(path, today)
    if plan["action"] == "none":
        return "none"
    if plan["action"] == "refuse":
        raise ValueError(f"{path}: {plan['reason']}")
    with path.open(newline="", encoding="utf-8") as fh:
        rows = [upgrade_row(r) for r in csv.DictReader(fh)]
    shutil.copy2(path, Path(plan["backup"]))
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=CSV_HEAD)
            w.writeheader()
            w.writerows(rows)
            fh.flush()
            os.fsync(fh.fileno())
        shutil.copymode(path, tmp)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return "migrated"


def csv_rows(path: Path) -> list[dict]:
    """Every row as a v2 dict, whichever header the file has (missing columns -> "",
    a missing date derived from the timestamp)."""
    if read_header(path) is None:
        return []
    out = []
    with path.open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            row = {k: (r.get(k) or "") for k in CSV_HEAD}
            if not row["date"]:
                row["date"] = date_of(row["timestamp"])
            out.append(row)
    return out


# ------------------------------------------------------------------ read side
def _num(v: str) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _clean(v: float | None):
    return int(v) if v is not None and float(v).is_integer() else v


def top_set(sets: list[dict]) -> dict | None:
    """The best set: heaviest, then most reps, seconds, lengths, minutes."""
    if not sets:
        return None
    return max(sets, key=lambda s: tuple((s.get(f) or 0) for f in ("weight", "reps", "seconds", "lengths", "minutes")))


class History:
    """Parsed log rows. Build with load(csv_rows(path))."""

    def __init__(self, entries: list[dict]):
        self.entries = entries

    # -- per exercise
    def history_for(self, person: str, item: str, since: str = "") -> list[dict]:
        """Sessions for one person + exercise, oldest first:
        [{date, sets: [{set_index, weight, reps, seconds, lengths, minutes, source, timestamp}],
          top, done, score}] (score = the day's last score)."""
        days: dict[str, dict] = {}
        for e in self.entries:
            if e["person"] != person or e["item"] != item or not e["date"] or (since and e["date"] < since):
                continue
            s = days.setdefault(e["date"], {"date": e["date"], "sets": [], "top": None, "done": False, "score": None})
            if any(e[f] is not None for f in SET_FIELDS):
                s["sets"].append({"set_index": e["set_index"], **{f: _clean(e[f]) for f in SET_FIELDS},
                                  "source": e["source"], "timestamp": e["timestamp"]})
            if e["done"]:
                s["done"] = True
            if e["score"] is not None:
                s["score"] = _clean(e["score"])
        out = [days[d] for d in sorted(days)]
        for s in out:
            s["top"] = top_set(s["sets"])
        return out

    # -- per day
    def logged_on(self, person: str, date: str) -> set[str]:
        """Item ids with anything logged (a value, a score or a tick) that day."""
        return {e["item"] for e in self.entries if e["person"] == person and e["date"] == date and
                (e["done"] or e["score"] is not None or any(e[f] is not None for f in SET_FIELDS))}

    def treadmill_minutes(self, person: str, since: str = "") -> list[dict]:
        """Daily treadmill-walk minutes, oldest first: [{date, total, by_source: {web: .., treadmill: ..}}]."""
        days: dict[str, dict] = {}
        for e in self.entries:
            if e["person"] != person or e["item"] != WALK_ITEM or e["minutes"] is None or not e["date"]:
                continue
            if since and e["date"] < since:
                continue
            d = days.setdefault(e["date"], {"date": e["date"], "total": 0.0, "by_source": {}})
            src = e["source"] or "web"
            d["total"] += e["minutes"]
            d["by_source"][src] = d["by_source"].get(src, 0.0) + e["minutes"]
        out = [days[d] for d in sorted(days)]
        for d in out:
            d["total"] = _clean(d["total"])
            d["by_source"] = {k: _clean(v) for k, v in d["by_source"].items()}
        return out

    # -- per ISO week
    def week_summary(self, person: str, iso_week: str, planned_fn, cat: dict | None = None) -> dict:
        """Goal counters + day_complete per day for an ISO week ("2026-W45", Mon-Sun).

        planned_fn(day_id) -> ordered item ids planned that weekday (see planned_items).
        cat (optional) tells which planned items are lifts (strength groups); without it
        every planned item outside the PT/hands/daily/cardio items counts as a lift."""
        monday = week_monday(iso_week)
        groups = {it["id"]: it["group"] for it in (cat or {}).get("items", [])}
        walk_min = {d["date"]: d["total"] for d in self.treadmill_minutes(person)}
        days, counts, minutes = [], dict.fromkeys(["walks", "pt", "hands", "stretches", "flexibility",
                                                   "strength", "pain"], 0), 0.0
        for i, day_id in enumerate(DAY_IDS):
            date = (monday + datetime.timedelta(days=i)).isoformat()
            planned = list(planned_fn(day_id))
            logged = self.logged_on(person, date)
            m = walk_min.get(date, 0) or 0
            minutes += m
            lifts = [p for p in planned if _is_lift(p, groups)]
            strength = day_id in STRENGTH_DAYS and bool(lifts) and all(p in logged for p in lifts)
            hits = {"walks": m > 0, "pt": all(p in logged for p in PT_BLOCK), "hands": "hand_routine" in logged,
                    "stretches": "stretches" in logged, "flexibility": bool({"flex_flow", "yoga"} & logged),
                    "strength": strength, "pain": all(p in logged for p in PAIN)}
            for k, v in hits.items():
                counts[k] += int(v)
            days.append({"date": date, "day": day_id, "planned": planned,
                         "done": [p for p in planned if p in logged],
                         "missing": [p for p in planned if p not in logged],
                         "walk_minutes": _clean(float(m)), "strength_session": strength,
                         "day_complete": bool(planned) and all(p in logged for p in planned)})
        goals = {k: {"count": counts[k], "target": GOALS[k], "met": counts[k] >= GOALS[k]} for k in counts}
        goals["walks"].update({"minutes": _clean(minutes), "minutes_target": GOALS["walk_minutes"],
                               "met": counts["walks"] >= GOALS["walks"] and minutes >= GOALS["walk_minutes"]})
        return {"person": person, "week": iso_week, "start": monday.isoformat(),
                "end": (monday + datetime.timedelta(days=6)).isoformat(), "days": days, "goals": goals}


_NOT_LIFTS = set(PT_BLOCK) | set(PAIN) | {"adductor", "hand_routine", "finger_ext", "stretches", "flex_flow",
                                          "yoga", WALK_ITEM}


def _is_lift(item_id: str, groups: dict[str, str]) -> bool:
    if groups:
        return groups.get(item_id) in STRENGTH_GROUPS
    return item_id not in _NOT_LIFTS


def load(rows: list[dict]) -> History:
    """Parse v2 dicts (csv_rows) into a History; numbers become floats or None."""
    entries = []
    for r in rows:
        e = {k: (r.get(k) or "").strip() for k in ("timestamp", "date", "person", "item", "slug", "source")}
        if not e["date"]:
            e["date"] = date_of(e["timestamp"])
        si = _num(r.get("set_index", ""))
        e["set_index"] = int(si) if si is not None else None
        for f in SET_FIELDS + ["score"]:
            e[f] = _num(r.get(f, ""))
        e["done"] = (r.get("done") or "").strip() == "1"
        entries.append(e)
    return History(entries)


def week_monday(iso_week: str) -> datetime.date:
    """'2026-W45' -> the Monday of that ISO week (ValueError on a bad string)."""
    y, _, w = iso_week.upper().partition("-W")
    return datetime.date.fromisocalendar(int(y), int(w), 1)


def iso_week_of(d: datetime.date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


# ------------------------------------------------------------------ plan
def planned_items(workout_json: dict | None, day_id: str, cat: dict) -> list[str]:
    """Ordered unique catalog item ids planned on a weekday: the day's steps mapped
    slug -> item through the catalog, the "Every day" step expanded, then the daily
    check-ins (score items and daily-group routines), the same list /workout/ shows."""
    if not workout_json:
        return []
    smap = {s: it for it in cat["items"] for s in it.get("slugs", [])}
    day = next((d for d in workout_json.get("days", []) if d.get("id") == day_id), None)
    if day is None:
        return []
    out: list[str] = []

    def add(st):
        it = smap.get(st.get("slug", ""))
        if it is not None and it["id"] not in out:
            out.append(it["id"])

    for st in day.get("steps", []):
        if st.get("everyday"):
            for sub in workout_json.get("everyday", []):
                add(sub)
        else:
            add(st)
    for it in cat["items"]:
        if it["id"] not in out and it["kind"] in ("score", "done") and (not it.get("slugs") or it["group"] == "daily"):
            out.append(it["id"])
    return out
