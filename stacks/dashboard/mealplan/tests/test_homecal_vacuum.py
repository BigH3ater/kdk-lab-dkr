"""Calendar expansion (recurring series, exceptions, all-day spans, time zones)
and the vacuum line built from Home Assistant states."""
import datetime
from zoneinfo import ZoneInfo

import pytest

import homecal
import vacuum
from test_kitchen import HA_STATES, ICS, NOW

TZ = ZoneInfo("America/Chicago")
MON = datetime.date(2026, 11, 9)


@pytest.fixture(scope="module")
def events():
    return homecal.expand(ICS, MON, 21, TZ)          # Nov 9 .. Nov 29


def _titles(events, day):
    return [homecal.label(e, day) for e in homecal.on_day(events, day)]


def test_weekly_all_day_series_expands_with_exdate(events):
    thursdays = [e["start"] for e in events if e["title"] == "Trash and recycling"]
    # the series began in October; Nov 19 is an EXDATE
    assert thursdays == [datetime.date(2026, 11, 12), datetime.date(2026, 11, 26)]


def test_timed_series_and_moved_occurrence(events):
    assert _titles(events, MON) == ["4 PM Piano lesson", "8 PM Late call"]
    assert _titles(events, MON + datetime.timedelta(days=7)) == ["6 PM Piano lesson (moved)"]


def test_multi_day_all_day_event_shows_each_day(events):
    assert "Trip" in _titles(events, datetime.date(2026, 11, 11))          # first day: plain title
    assert "Trip (until Fri)" in _titles(events, datetime.date(2026, 11, 12))
    assert "Trip (last day)" in _titles(events, datetime.date(2026, 11, 13))
    assert not any("Trip" in t for t in _titles(events, datetime.date(2026, 11, 14)))  # DTEND is exclusive


# A timed on-call week (live: "Jacob On Call", Nov 5 08:00 -> Nov 13 08:00), a
# timed event across midnight, and one ending exactly at midnight.
SPANS = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:oncall@test
DTSTART;TZID=America/Chicago:20261105T080000
DTEND;TZID=America/Chicago:20261113T080000
SUMMARY:Jacob On Call
END:VEVENT
BEGIN:VEVENT
UID:night@test
DTSTART;TZID=America/Chicago:20261110T220000
DTEND;TZID=America/Chicago:20261111T013000
SUMMARY:Night shift
END:VEVENT
BEGIN:VEVENT
UID:midnight@test
DTSTART;TZID=America/Chicago:20261110T200000
DTEND;TZID=America/Chicago:20261111T000000
SUMMARY:Movie
END:VEVENT
BEGIN:VEVENT
UID:class@test
DTSTART;TZID=America/Chicago:20261110T180000
DTEND;TZID=America/Chicago:20261110T190000
SUMMARY:Manners Class
END:VEVENT
BEGIN:VEVENT
UID:long@test
DTSTART;VALUE=DATE:20261109
DTEND;VALUE=DATE:20261201
SUMMARY:Holiday lights up
END:VEVENT
END:VCALENDAR
"""


@pytest.fixture(scope="module")
def spans():
    return homecal.expand(SPANS, MON, 21, TZ)


def test_multi_day_timed_event_shows_every_day_it_runs(spans):
    # it began before the window (Nov 5) and still shows on each day until it ends
    for d in (9, 10, 11, 12, 13):
        assert any(t.startswith("Jacob On Call") for t in _titles(spans, datetime.date(2026, 11, d))), d
    assert "Jacob On Call (until Fri)" in _titles(spans, datetime.date(2026, 11, 10))
    assert "Jacob On Call (until 8 AM)" in _titles(spans, datetime.date(2026, 11, 13))
    assert not any("Jacob" in t for t in _titles(spans, datetime.date(2026, 11, 14)))


def test_timed_event_across_midnight_shows_on_both_days(spans):
    assert "10 PM Night shift" in _titles(spans, datetime.date(2026, 11, 10))
    assert "Night shift (until 1:30 AM)" in _titles(spans, datetime.date(2026, 11, 11))
    # ending at exactly midnight does not spill into the next day
    assert "8 PM Movie" in _titles(spans, datetime.date(2026, 11, 10))
    assert not any("Movie" in t for t in _titles(spans, datetime.date(2026, 11, 11)))


def test_days_own_events_come_before_carried_over_ones(spans):
    assert _titles(spans, datetime.date(2026, 11, 10)) == [
        "6 PM Manners Class", "8 PM Movie", "10 PM Night shift",
        "Holiday lights up (until Nov 30)", "Jacob On Call (until Fri)"]
    # the night view picks the day's own first timed event, not a carried one
    assert homecal.first_event(spans, datetime.date(2026, 11, 10)) == "6 PM Manners Class"
    assert homecal.first_event(spans, datetime.date(2026, 11, 12)) == "Holiday lights up (until Nov 30)"
    assert homecal.label(spans[0]) == "8 AM Jacob On Call"                  # no day: plain label


def test_utc_event_lands_on_its_chicago_day(events):
    # 2026-11-10 02:00Z = Mon Nov 9, 8 PM in Chicago
    assert "8 PM Late call" in _titles(events, MON)
    assert all("Late call" not in t for t in _titles(events, MON + datetime.timedelta(days=1)))


def test_all_day_first_then_by_time(events):
    thu = _titles(events, datetime.date(2026, 11, 12))     # Thursday: trash (all-day) + trip (day 2)
    assert thu == ["Trash and recycling", "Trip (until Fri)"]
    assert _titles(events, datetime.date(2026, 11, 10)) == ["9:30 AM Vet <Bear>"]


def test_first_event_for_the_night_view(events):
    assert homecal.first_event(events, datetime.date(2026, 11, 10)) == "9:30 AM Vet <Bear>"
    assert homecal.first_event(events, datetime.date(2026, 11, 12)) == "Trash and recycling"
    assert homecal.first_event(events, datetime.date(2026, 11, 15)) == ""          # a Sunday, nothing on


def test_fetch_error_never_carries_the_url(monkeypatch):
    def boom(url, timeout):
        raise ConnectionError(f"cannot reach {url}")
    monkeypatch.setattr(homecal.requests, "get", boom)
    with pytest.raises(RuntimeError) as e:
        homecal.fetch_ics("https://calendar.example/secret-token.ics")
    assert "secret-token" not in str(e.value) and "ConnectionError" in str(e.value)
    with pytest.raises(RuntimeError):
        homecal.fetch_ics("")


def test_broken_series_is_skipped():
    bad = ICS.replace(b"RRULE:FREQ=WEEKLY;BYDAY=MO", b"RRULE:FREQ=WEEKLY;BYDAY=XX")
    got = homecal.expand(bad, MON, 7, TZ)
    assert any(e["title"] == "Vet <Bear>" for e in got)


# ---- vacuum ---------------------------------------------------------------------
def test_vacuum_summary_and_line():
    v = vacuum.summary(HA_STATES, NOW, TZ)
    assert (v["rooms_total"], v["rooms_today"]) == (3, 2)
    assert v["next_run"] == datetime.datetime(2026, 11, 10, 8, 30)
    assert v["alerts"] == ["water tank empty", "side brush due"]
    text, alerts = vacuum.line_parts(v, MON)
    assert text == "2 of 3 rooms cleaned today · next run tomorrow 8:30 AM"


def test_vacuum_all_rooms_and_no_alerts():
    st = {k: v for k, v in HA_STATES.items() if "garage" not in k}
    st["binary_sensor.mr_slave_water_shortage_2"] = {"state": "off", "attributes": {}}
    st["sensor.mr_slave_side_brush_time_left_2"] = {"state": "100", "attributes": {}}
    st["input_datetime.rr_next_run"] = {"state": "2026-11-11 10:00:00", "attributes": {}}
    v = vacuum.summary(st, NOW, TZ)
    assert vacuum.line_parts(v, MON) == ("all 2 rooms cleaned today · next run Wed 10 AM", [])


def test_vacuum_error_and_room_from_state_when_no_at():
    st = {"sensor.rr_room_kitchen_cleaned": {"state": "2026-11-09T19:32:13+00:00", "attributes": {}},
          "sensor.rr_vacuum_error": {"state": "brush jammed", "attributes": {"advice": {"title": "Clear the main brush"}}},
          "sensor.mr_slave_dock_strainer_time_left": {"state": "unavailable", "attributes": {}}}
    v = vacuum.summary(st, NOW, TZ)
    assert v["rooms_today"] == 1 and v["alerts"] == ["Clear the main brush"] and v["next_run"] is None


def test_vacuum_entity_filter():
    assert vacuum.wanted("sensor.rr_room_hallway_cleaned")
    assert vacuum.wanted("sensor.mr_slave_filter_time_left_2")
    assert not vacuum.wanted("sensor.mr_slave_battery")
    assert not vacuum.wanted("light.kitchen")


def test_fetch_states_requires_token():
    with pytest.raises(RuntimeError):
        vacuum.fetch_states("http://ha.example:8123", "")


# ---- water tank: same rule as HA's rr_water_empty_alert ---------------------------
FLAG, BOX, DOCK = ("binary_sensor.mr_slave_water_shortage_2",
                   "binary_sensor.mr_slave_dock_clean_water_box", "sensor.mr_slave_dock_dock_error")


def _st(state, changed="2026-11-09T19:14:00+00:00"):
    return {"state": state, "attributes": {}, "last_changed": changed}


def test_water_flag_stale_after_refill_is_not_empty():
    """Live 2026-11-10: flag on since 19:14Z, box off and changed at 01:45Z the
    next day (refilled after the flag) -> no alert."""
    st = {FLAG: _st("on", "2026-11-09T19:14:00.123456+00:00"),
          BOX: _st("off", "2026-11-10T01:45:00.5+00:00"), DOCK: _st("ok")}
    assert vacuum.water_empty(st) is False
    assert "water tank empty" not in vacuum.summary(st, NOW, TZ)["alerts"]


def test_water_flag_on_and_box_unchanged_since_is_empty():
    st = {FLAG: _st("on", "2026-11-09T19:14:00+00:00"), BOX: _st("off", "2026-11-08T10:00:00+00:00")}
    assert vacuum.water_empty(st) is True


def test_water_flag_on_and_box_missing_is_empty():
    assert vacuum.water_empty({FLAG: _st("on")}) is True


def test_water_box_on_is_empty_even_without_flag():
    assert vacuum.water_empty({FLAG: _st("off"), BOX: _st("on", "2026-11-10T01:45:00+00:00")}) is True


def test_water_dock_error_is_empty():
    assert vacuum.water_empty({DOCK: _st("water_empty"), FLAG: _st("off"), BOX: _st("off")}) is True


def test_water_all_clear():
    assert vacuum.water_empty({FLAG: _st("off"), BOX: _st("off"), DOCK: _st("ok")}) is False
    assert vacuum.water_empty({}) is False


def test_fetch_states_keeps_last_changed(monkeypatch):
    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return [{"entity_id": BOX, "state": "off", "attributes": {}, "last_changed": "2026-11-10T01:45:00+00:00"},
                    {"entity_id": DOCK, "state": "ok", "attributes": {}, "last_changed": "x"},
                    {"entity_id": "light.kitchen", "state": "on"}]
    monkeypatch.setattr(vacuum.requests, "get", lambda *a, **k: R())
    got = vacuum.fetch_states("http://ha", "tok")
    assert set(got) == {BOX, DOCK} and got[BOX]["last_changed"] == "2026-11-10T01:45:00+00:00"
