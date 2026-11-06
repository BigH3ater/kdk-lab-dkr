"""Load and validate tools/workout_catalog.json, the one exercise list for wger.

    import workout_catalog
    cat = workout_catalog.load()

The catalog holds the people (login = wger user), the first person's groups/items
(the slugs gen_wger.py syncs; doses + cues for that person come from the guide output,
see gen-workout-guide.py), and per-person plans under "plans": {"<login>": {groups,
items}} with their own days, doses and cues (Rachel: plans.rach). gen_wger.py is the
only consumer. Runbook: docs/runbooks/workout-tracking-update.md.

Kinds (what one exercise logs, mapped to wger units by gen_wger.py):
  w weight + reps · r reps · t seconds · wt weight + seconds · wl weight + 10-ft lengths
  min minutes · score 0-10 rating (not synced) · done a routine tick

Stdlib only.
"""
from __future__ import annotations

import json
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
CATALOG = TOOLS / "workout_catalog.json"

KINDS = ("w", "r", "t", "wt", "wl", "min", "score", "done")
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _check_items(groups_list: list[dict], items: list[dict], where: str) -> set[str]:
    groups = {g["key"] for g in groups_list}
    ids = set()
    for it in items:
        assert it["kind"] in KINDS, f"{where}: unknown kind {it['kind']!r} for {it['id']}"
        assert it["group"] in groups, f"{where}: unknown group {it['group']!r} for {it['id']}"
        assert it["id"] not in ids, f"{where}: duplicate id {it['id']}"
        assert it["id"].replace("_", "").isalnum() and it["id"].islower(), f"{where}: bad id {it['id']}"
        ids.add(it["id"])
    return ids


def load(path: Path = CATALOG) -> dict:
    cat = json.loads(path.read_text(encoding="utf-8"))
    _check_items(cat["groups"], cat["items"], "items")
    logins = {p["login"] for p in cat["people"]}
    for login, pl in cat.get("plans", {}).items():
        where = f"plans.{login}"
        assert login in logins, f"{where}: no person with that login"
        _check_items(pl["groups"], pl["items"], where)
        for it in pl["items"]:
            days = it.get("days", [])
            assert days and (days == ["daily"] or set(days) <= set(WEEKDAYS)), f"{where} {it['id']}: bad days {days}"
            assert [s["slug"] for s in it.get("steps", [])] == it["slugs"], \
                f"{where} {it['id']}: steps must list the slugs in order"
            assert not it.get("either") or len(it["slugs"]) == 2, f"{where} {it['id']}: either needs two slugs"
    return cat


def plan(cat: dict, person: dict) -> dict:
    """The person's plan (groups, items, ...): their "plans" entry, else the top-level list."""
    return cat.get("plans", {}).get(person["login"], cat)


def item_days(it: dict) -> list[str]:
    days = it.get("days", [])
    return list(WEEKDAYS) if days == ["daily"] else [d for d in WEEKDAYS if d in days]
