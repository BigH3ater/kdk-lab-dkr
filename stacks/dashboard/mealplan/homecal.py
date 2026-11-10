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
    all_day}], sorted by start then time. A broken series is skipped, not fatal."""
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
        out.append({"title": title, "start": d0, "end": d1, "at": at, "all_day": at is None})
    out.sort(key=lambda e: (e["start"], _ts(e), e["title"]))
    return out


def _ts(e: dict) -> float:
    """Sort key: all-day events (no time) before timed ones."""
    return e["at"].timestamp() if e["at"] else float("-inf")


def on_day(events: list[dict], day: datetime.date) -> list[dict]:
    """Events that fall on `day`: all-day and multi-day events while they run,
    timed events on their start day; all-day first, then by time."""
    hits = [e for e in events if e["start"] <= day < e["end"] and (e["all_day"] or e["start"] == day)]
    return sorted(hits, key=lambda e: (_ts(e), e["title"]))


def fmt_time(at: datetime.datetime | None) -> str:
    if at is None:
        return ""
    return at.strftime("%-I:%M %p").replace(":00 ", " ")


def label(e: dict) -> str:
    """"Vet, 9:30 AM" or "Trash day" (all-day)."""
    t = fmt_time(e["at"])
    return f"{e['title']}, {t}" if t else e["title"]


def first_event(events: list[dict], day: datetime.date) -> str:
    """The night view line: the first event of `day`, or ""."""
    hits = on_day(events, day)
    if not hits:
        return ""
    timed = [e for e in hits if not e["all_day"]]
    return label(timed[0] if timed else hits[0])
