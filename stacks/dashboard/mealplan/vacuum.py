"""Robot vacuum line for the kitchen board, from Home Assistant (read-only).

One GET /api/states with the dashboard's long-lived HA token (env
HOMEPAGE_VAR_HA_TOKEN, already injected for the Homepage widgets), filtered to
the roborock entities below. The room sensors are a family
(sensor.rr_room_<room>_cleaned), so one list read is cheaper and simpler than
one GET per entity. Cached 60 s by the caller.

  sensor.rr_room_*_cleaned               state = last clean (ISO), attribute `at`
                                         = "Mon Nov 9 13:32" (local)
  input_datetime.rr_next_run             next scheduled run (local)
  binary_sensor.mr_slave_water_shortage_2  water shortage flag (see water_empty)
  binary_sensor.mr_slave_dock_clean_water_box  on = clean water box empty
  sensor.mr_slave_dock_dock_error        `water_empty` = dock out of water
  sensor.rr_vacuum_error                 "no error" or the error, advice.title
  sensor.mr_slave_*_time_left*           consumable time left; < 0 = overdue
"""
from __future__ import annotations

import datetime
import re
from zoneinfo import ZoneInfo

import requests

ROOM_RE = re.compile(r"^sensor\.rr_room_.+_cleaned$")
CONSUMABLE_RE = re.compile(r"^sensor\.mr_slave_.+_time_left")
NEXT_RUN = "input_datetime.rr_next_run"
WATER = "binary_sensor.mr_slave_water_shortage_2"
WATER_BOX = "binary_sensor.mr_slave_dock_clean_water_box"
DOCK_ERROR = "sensor.mr_slave_dock_dock_error"
ERROR = "sensor.rr_vacuum_error"
_NO_ERROR = {"", "no error", "none", "ok", "unknown", "unavailable"}


def wanted(entity_id: str) -> bool:
    return bool(ROOM_RE.match(entity_id) or CONSUMABLE_RE.match(entity_id)
                or entity_id in (NEXT_RUN, WATER, WATER_BOX, DOCK_ERROR, ERROR))


def fetch_states(base: str, token: str, timeout: float = 8) -> dict[str, dict]:
    """{entity_id: {state, attributes, last_changed}} for the vacuum entities."""
    if not token:
        raise RuntimeError("HA token not set")
    r = requests.get(f"{base.rstrip('/')}/api/states",
                     headers={"Authorization": f"Bearer {token}"}, timeout=timeout)
    r.raise_for_status()
    return {s["entity_id"]: {"state": s.get("state"), "attributes": s.get("attributes") or {},
                             "last_changed": s.get("last_changed")}
            for s in r.json() if wanted(s.get("entity_id", ""))}


def _changed(s: dict | None) -> datetime.datetime | None:
    try:
        v = datetime.datetime.fromisoformat(str((s or {}).get("last_changed")))
    except ValueError:
        return None
    return v if v.tzinfo else v.replace(tzinfo=datetime.timezone.utc)


def _is(s: dict | None, value: str) -> bool:
    return str((s or {}).get("state")).strip().lower() == value


def water_empty(states: dict[str, dict]) -> bool:
    """Same rule as HA's rr_water_empty_alert (packages/roborock.yaml). The
    shortage flag only re-evaluates when the dock pumps water, so it stays on
    after a refill; it counts only if the clean water box has not changed since
    the flag came on (or the box entity is missing). The dock error
    `water_empty` and the box sensor itself count on their own."""
    if _is(states.get(DOCK_ERROR), "water_empty") or _is(states.get(WATER_BOX), "on"):
        return True
    flag = states.get(WATER)
    if not _is(flag, "on"):
        return False
    box = states.get(WATER_BOX)
    if box is None:
        return True
    box_t, flag_t = _changed(box), _changed(flag)
    if box_t is None or flag_t is None:
        return True                       # cannot compare: trust the flag
    return box_t < flag_t


def _cleaned_on(s: dict, tz: ZoneInfo, year: int) -> datetime.date | None:
    """Local date a room was last cleaned: attribute `at` ("Mon Nov 9 13:32",
    no year) first, then the ISO state."""
    at = (s.get("attributes") or {}).get("at")
    if at:
        try:
            return datetime.datetime.strptime(f"{at} {year}", "%a %b %d %H:%M %Y").date()
        except ValueError:
            pass
    try:
        v = datetime.datetime.fromisoformat(str(s.get("state")))
    except ValueError:
        return None
    return (v.astimezone(tz) if v.tzinfo else v).date()


def _next_run(s: dict | None) -> datetime.datetime | None:
    if not s:
        return None
    try:
        return datetime.datetime.strptime(str(s.get("state")), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _consumable_name(eid: str, s: dict) -> str:
    name = (s.get("attributes") or {}).get("friendly_name") or eid
    name = re.sub(r"^Mr\.? Slave\s+", "", name)
    name = re.sub(r"\s+time left.*$", "", name, flags=re.I)
    return name.strip().lower() or "consumable"


def summary(states: dict[str, dict], now: datetime.datetime, tz: ZoneInfo) -> dict:
    """{rooms_total, rooms_today, next_run: datetime|None, alerts: [str]}."""
    today = now.date()
    rooms = [s for eid, s in states.items() if ROOM_RE.match(eid)]
    today_n = sum(1 for s in rooms if _cleaned_on(s, tz, today.year) == today)
    alerts = []
    if water_empty(states):
        alerts.append("water tank empty")
    err = states.get(ERROR)
    if err and str(err.get("state") or "").strip().lower() not in _NO_ERROR:
        advice = (err.get("attributes") or {}).get("advice") or {}
        alerts.append(str(advice.get("title") or err.get("state")))
    for eid in sorted(states):
        if CONSUMABLE_RE.match(eid):
            try:
                left = float(states[eid].get("state"))
            except (TypeError, ValueError):
                continue
            if left < 0:
                alerts.append(f"{_consumable_name(eid, states[eid])} due")
    return {"rooms_total": len(rooms), "rooms_today": today_n,
            "next_run": _next_run(states.get(NEXT_RUN)), "alerts": alerts}


def fmt_next(nr: datetime.datetime | None, today: datetime.date) -> str:
    if nr is None:
        return ""
    t = nr.strftime("%-I:%M %p").replace(":00 ", " ")
    if nr.date() == today:
        return f"today {t}"
    if nr.date() == today + datetime.timedelta(days=1):
        return f"tomorrow {t}"
    return f"{nr:%a} {t}"


def line_parts(v: dict, today: datetime.date) -> tuple[str, list[str]]:
    """("all 13 rooms cleaned today · next run tomorrow 8:30 AM", [alerts])."""
    bits = []
    tot, n = v["rooms_total"], v["rooms_today"]
    if tot:
        if n == tot:
            bits.append(f"all {tot} rooms cleaned today")
        elif n == 0:
            bits.append("no rooms cleaned today")
        else:
            bits.append(f"{n} of {tot} rooms cleaned today")
    nxt = fmt_next(v["next_run"], today)
    if nxt:
        bits.append(f"next run {nxt}")
    return " · ".join(bits), list(v["alerts"])
