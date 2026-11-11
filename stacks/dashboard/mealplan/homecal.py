"""Household calendar for the kitchen board and the household page.

Reads the Proton "Share via link" ICS feed (env HOMEPAGE_VAR_ICS_URL, the same
secret the Homepage calendar widget uses) server-side and expands recurring
events with recurring-ical-events. The URL is a bearer secret: it is never
logged or rendered, and fetch errors log only the exception type (a requests
exception message carries the URL).
"""
from __future__ import annotations

import datetime
import logging
from zoneinfo import ZoneInfo

import icalendar
import recurring_ical_events
import requests

log = logging.getLogger("mealplan")


def fetch_ics(url: str, timeout: float = 20) -> bytes:
    """The raw ICS body. Raises RuntimeError (no URL in the message) on failure."""
    if not url:
        raise RuntimeError("calendar not configured")
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
    except Exception as e:                       # keep the URL out of logs
        raise RuntimeError(f"calendar fetch failed: {type(e).__name__}") from None
    return r.content


def _as_local(v, tz: ZoneInfo):
    """(local date, local datetime or None) for an ICS date or datetime."""
    if isinstance(v, datetime.datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=tz)
        v = v.astimezone(tz)
        return v.date(), v
    return v, None


def expand(ics: bytes, start: datetime.date, days: int, tz: ZoneInfo) -> list[dict]:
    """Every event occurrence touching [start, start+days), recurring series
    expanded: [{title, start: date, end: date (exclusive), at: datetime|None,
    until: datetime|None (a timed event's end), all_day}], sorted by start
    then time. A broken series is skipped, not fatal."""
    cal = icalendar.Calendar.from_ical(ics)
    end = start + datetime.timedelta(days=days)
    out = []
    for ev in recurring_ical_events.of(cal, skip_bad_series=True).between(start, end):
        if str(ev.get("STATUS", "")).upper() == "CANCELLED":
            continue
        dts = ev.get("DTSTART")
        if dts is None:
            continue
        d0, at = _as_local(dts.dt, tz)
        dte = ev.get("DTEND")
        if dte is not None:
            d1, at1 = _as_local(dte.dt, tz)
        else:
            d1, at1 = d0, None
        if at is None:                              # all-day: DTEND is exclusive
            d1 = d1 if d1 > d0 else d0 + datetime.timedelta(days=1)
        else:                                       # timed: through its last day
            last = (at1 - datetime.timedelta(seconds=1)).date() if at1 and at1 > at else d0
            d1 = last + datetime.timedelta(days=1)
        title = str(ev.get("SUMMARY") or "").strip() or "(no title)"
        until = at1 if at is not None and at1 is not None and at1 > at else None
        out.append({"title": title, "start": d0, "end": d1, "at": at, "until": until,
                    "all_day": at is None})
    out.sort(key=lambda e: (e["start"], _ts(e), e["title"]))
    return out


def _ts(e: dict) -> float:
    """Sort key: all-day events (no time) before timed ones."""
    return e["at"].timestamp() if e["at"] else float("-inf")


def on_day(events: list[dict], day: datetime.date) -> list[dict]:
    """Events running on `day`: every event whose span covers it (all-day,
    multi-day, and timed events that cross midnight or last several days).
    The day's own events come first (all-day, then timed by start time), then
    the ones carried over from an earlier day, so a long-running event never
    pushes today's times off a one-line summary."""
    hits = [e for e in events if e["start"] <= day < e["end"]]
    return sorted(hits, key=lambda e: (e["start"] < day, _ts(e), e["title"]))


def fmt_time(at: datetime.datetime | None) -> str:
    if at is None:
        return ""
    return at.strftime("%-I:%M %p").replace(":00 ", " ")


def _until(e: dict, day: datetime.date) -> str:
    """"until Fri" for an event carried over from an earlier day; "until 8 AM"
    on the last day of a timed one, "last day" on the last day of an all-day one."""
    last = e["end"] - datetime.timedelta(days=1)
    if last <= day:
        return f"until {fmt_time(e['until'])}" if e.get("until") else "last day"
    if (last - day).days < 7:
        return f"until {last:%a}"
    return f"until {last:%b} {last.day}"


def label(e: dict, day: datetime.date | None = None) -> str:
    """"9:30 AM Vet" (time first, so a cut-off line loses the end of the title,
    not the time), "Trash day" (all-day), or "Jacob On Call (until Fri)" on the
    days after an event's first day."""
    if day is not None and day > e["start"]:
        return f"{e['title']} ({_until(e, day)})"
    t = fmt_time(e["at"])
    return f"{t} {e['title']}" if t else e["title"]


def first_event(events: list[dict], day: datetime.date) -> str:
    """The night view line: the first event that starts on `day` (timed before
    all-day), else one still running from an earlier day, or ""."""
    hits = on_day(events, day)
    if not hits:
        return ""
    starting = [e for e in hits if e["start"] == day]
    timed = [e for e in starting if not e["all_day"]]
    pick = timed[0] if timed else (starting[0] if starting else hits[0])
    return label(pick, day)
