"""Review findings on 0.1.1 (PR after #506/#509): lost writes, replay duplicates,
timeouts after an accepted POST, the retry window, startup warnings, category lookup."""
import asyncio
import dataclasses
import io
import json
import logging
from datetime import timedelta

import httpx
import pytest

from conftest import TOKEN_A, TOKEN_B, FakeWger
from scale_bridge import logs
from scale_bridge.app import Bridge, retry_delays
from scale_bridge.config import from_env
from scale_bridge.session import Reading
from scale_bridge.wger import WgerClient, WgerError, external_id
from test_wger_bridge import READING, SCALE, WHEN, bridge_run, env, run

LIVE = {"SCALE_BRIDGE_DRY_RUN": "false"}
FRESH_DF = tuple("df" + f"{i:02x}" * 16 for i in range(1, 5))     # other bytes, same weight + impedance


def later(reading: Reading, **delta) -> Reading:
    return dataclasses.replace(reading, measured_at=reading.measured_at + timedelta(**delta))


def write(api: FakeWger, *readings: Reading, person: str = "alice", token: str = TOKEN_A) -> list[dict]:
    """Each reading through a FRESH client (no cache, no lock): what a restart sees."""
    async def go():
        out = []
        async with api.client() as http:
            for r in readings:
                out.append(await WgerClient(http, token).write_reading(r, external_id(SCALE, r, person)))
        return out
    return run(go())


def events(lines, name):
    return [x for x in lines if x["event"] == name]


# --- finding 1: a malformed response never loses a weigh-in silently ---------------------------

def test_html_200_is_a_wger_error_and_the_write_retries():
    api = FakeWger({"alice": TOKEN_A, "bob": TOKEN_B}, html_next=1)
    out, api, lines, sleeps = bridge_run(env(**LIVE), wger=api)
    failed = events(lines, "write_failed")
    assert out == "queued" and len(failed) == 1 and sleeps == [30.0]
    assert failed[0]["error"] == "GET measurement-category/: HTTP 200, body is not JSON"
    assert events(lines, "wrote")[0]["created"] == 11 and len(api.measurements) == 11
    text = json.dumps(lines)
    assert "MARKER-NOT-FOR-LOGS" not in text and TOKEN_A not in text


def mangled(api: FakeWger, key: str, status: int, body) -> httpx.AsyncClient:
    """FakeWger, except `key` ("METHOD path") answers with `body`."""
    def handler(req: httpx.Request) -> httpx.Response:
        if f"{req.method} {req.url.path.removeprefix('/api/v2/').rstrip('/')}" == key:
            return httpx.Response(status, json=body)
        return api.handler(req)
    return httpx.AsyncClient(base_url="https://wger.test", transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("key,status,body,official,problem", [
    ("GET measurement-category", 200, {"detail": "x"}, True, "no results list"),
    ("GET measurement-category", 200, {"results": ["x"], "next": None}, True, "a row is not an object"),
    ("GET measurement-category", 200, {"results": [{"name": "Body weight"}], "next": None}, True, "no id"),
    ("GET measurement", 200, {"results": [{"value": 70.0}], "next": None}, True, "no date"),
    ("GET measurement", 200, {"results": [{"value": "x", "date": "2026-01-01"}], "next": None}, True,
     "value is not a number"),
    ("POST measurement-category", 201, ["x"], True, "not an object"),
    ("POST measurement-category", 201, {"name": "x"}, True, "no id"),
    ("GET userprofile", 200, [], False, "no profile"),
])
def test_unexpected_bodies_are_wger_errors(key, status, body, official, problem):
    api = FakeWger({"alice": TOKEN_A}, official={"alice": official})

    async def go():
        async with mangled(api, key, status, body) as http:
            await WgerClient(http, TOKEN_A).write_reading(READING, external_id(SCALE, READING, "alice"))
    with pytest.raises(WgerError) as e:
        run(go())
    assert problem in str(e.value) and key.split()[0] in str(e.value)
    assert "?" not in str(e.value) and TOKEN_A not in str(e.value)


def test_unexpected_exception_is_logged_as_write_crashed_and_retried(monkeypatch):
    real = WgerClient.write_reading
    calls = []

    async def flaky(self, *a, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise KeyError("id")
        return await real(self, *a, **kw)
    monkeypatch.setattr(WgerClient, "write_reading", flaky)
    _, api, lines, sleeps = bridge_run(env(**LIVE))
    crashed = events(lines, "write_crashed")
    assert len(crashed) == 1 and crashed[0]["level"] == "error" and crashed[0]["error"] == "KeyError"
    assert sleeps == [30.0] and events(lines, "wrote") and len(api.measurements) == 11


def startup_lines(e, api=None):
    api = api or FakeWger({"alice": TOKEN_A, "bob": TOKEN_B})
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(logs.JsonFormatter())
    lg = logging.getLogger("scale_bridge")
    lg.addHandler(h)
    lg.setLevel(logging.INFO)

    async def go():
        async with api.client() as http:
            await Bridge(from_env(e), http).startup_checks()
    try:
        run(go())
    finally:
        lg.removeHandler(h)
    return [json.loads(x) for x in buf.getvalue().splitlines()]


def test_startup_check_reports_an_html_200_as_an_error():
    lines = startup_lines(env(), FakeWger({"alice": TOKEN_A, "bob": TOKEN_B}, html_next=1))
    checks = {x["person"]: x["result"] for x in events(lines, "token_check")}
    assert checks == {"alice": "error", "bob": "ok"}
    assert "MARKER-NOT-FOR-LOGS" not in json.dumps(lines)


# --- finding 2: the same weigh-in maps to the same entries, across a restart -------------------

def test_replay_after_restart_with_identical_bytes_is_exists():
    first, replay = write(FakeWger({"alice": TOKEN_A}), READING, later(READING, minutes=3))
    assert first["created"] == 11
    assert replay == {"created": 0, "exists": 11, "categories_created": 0}


def test_replay_with_fresh_df_bytes_within_the_window_is_exists():
    api = FakeWger({"alice": TOKEN_A})
    r2 = dataclasses.replace(later(READING, minutes=3), df_raw=FRESH_DF)
    assert external_id(SCALE, r2, "alice") != external_id(SCALE, READING, "alice")
    _, replay = write(api, READING, r2)
    assert replay == {"created": 0, "exists": 11, "categories_created": 0} and len(api.measurements) == 11


def test_weight_only_replay_within_the_window_is_exists():
    api = FakeWger({"alice": TOKEN_A})
    w = Reading(measured_at=WHEN, weight_cg=7000, z50_deci=None, z250_deci=None)
    _, replay = write(api, w, later(w, minutes=4))
    assert replay == {"created": 0, "exists": 1, "categories_created": 0} and len(api.measurements) == 1


def test_identical_df_bytes_outside_the_window_are_still_exists():
    """Rests on the scale re-sending identical DF bytes: the external_id + unique constraint."""
    api = FakeWger({"alice": TOKEN_A})
    _, replay = write(api, READING, later(READING, hours=2))
    assert replay == {"created": 0, "exists": 11, "categories_created": 0} and len(api.measurements) == 11


def test_genuine_later_weigh_ins_are_written():
    api = FakeWger({"alice": TOKEN_A})
    w = Reading(measured_at=WHEN, weight_cg=7000, z50_deci=None, z250_deci=None)
    fresh = dataclasses.replace(later(READING, minutes=11), df_raw=FRESH_DF)
    res = write(api, w, later(w, hours=4), fresh)       # identical weight, same day
    assert [x["created"] for x in res] == [1, 1, 11]
    assert len(api.values("alice")["Body weight"]) == 3


def test_reweigh_with_handle_after_a_weight_only_lock_adds_the_impedance():
    api = FakeWger({"alice": TOKEN_A})
    w = Reading(measured_at=WHEN, weight_cg=7000, z50_deci=None, z250_deci=None)
    _, full = write(api, w, dataclasses.replace(later(READING, minutes=2), df_raw=FRESH_DF))
    assert full == {"created": 10, "exists": 1, "categories_created": 10}
    assert {x["date"] for x in api.measurements} == {"2026-01-01T07:30:00.123+00:00"}


def test_replay_after_the_legacy_fallback_is_exists():
    api = FakeWger({"alice": TOKEN_A}, official={"alice": False}, weight_unit={"alice": "lb"})
    _, replay = write(api, READING, dataclasses.replace(later(READING, minutes=5), df_raw=FRESH_DF))
    assert replay == {"created": 0, "exists": 11, "categories_created": 0} and len(api.measurements) == 11


def test_bridge_restart_then_replay_writes_once():
    api = FakeWger({"alice": TOKEN_A, "bob": TOKEN_B})
    bridge_run(env(**LIVE), wger=api)                                            # first container
    replay = dataclasses.replace(later(READING, seconds=90), df_raw=FRESH_DF)
    _, api, lines, _ = bridge_run(env(**LIVE), reading=replay, wger=api)          # after a restart
    assert events(lines, "wrote")[0]["exists"] == 11 and len(api.measurements) == 11


# --- finding 3: a POST wger accepted but the client saw time out -------------------------------

def test_timeout_after_an_accepted_measurement_post_does_not_duplicate():
    api = FakeWger({"alice": TOKEN_A, "bob": TOKEN_B}, timeout_after={"POST measurement": 1})
    _, api, lines, _ = bridge_run(env(**LIVE), wger=api)
    assert [x["error"] for x in events(lines, "write_failed")] == ["POST measurement/: ReadTimeout"]
    assert len(api.measurements) == 11 and len(api.posts("measurement")) == 11
    assert {k: events(lines, "wrote")[0][k] for k in ("created", "exists")} == {"created": 10, "exists": 1}


def test_timeout_after_an_accepted_weightentry_post_does_not_duplicate():
    api = FakeWger({"alice": TOKEN_A, "bob": TOKEN_B}, official={"alice": False, "bob": True},
                   weight_unit={"alice": "lb"}, timeout_after={"POST weightentry": 1})
    _, api, lines, _ = bridge_run(env(**LIVE), wger=api)
    assert [x["error"] for x in events(lines, "write_failed")] == ["POST weightentry/: ReadTimeout"]
    assert len(api.weightentries) == 1 and len(api.values("alice")["Body weight"]) == 1
    assert len(api.measurements) == 11


# --- finding 4: the retry window the docs state ------------------------------------------------

def test_retry_schedule_is_six_tries_over_15_5_minutes():
    assert retry_delays(6) == [30.0, 60.0, 120.0, 240.0, 480.0] and sum(retry_delays(6)) == 930.0
    api = FakeWger({"alice": TOKEN_A, "bob": TOKEN_B}, fail_next=100)
    _, _, lines, sleeps = bridge_run(env(**LIVE), wger=api)
    assert sum(sleeps) == 930.0 and len(events(lines, "write_failed")) == 6
    assert events(lines, "write_gave_up")[0]["attempts"] == 6


# --- finding 5: startup warnings, category lookup ---------------------------------------------

def test_overlapping_bands_warn_at_startup_by_name_only():
    overlap = json.dumps([
        {"name": "alice", "min_kg": 60, "max_kg": 75, "token_env": "WGER_API_KEY_ALICE"},
        {"name": "bob", "min_kg": 74.5, "max_kg": 95, "token_env": "WGER_API_KEY_BOB"},
    ])
    w = events(startup_lines(env(SCALE_BRIDGE_PEOPLE=overlap)), "config_bands_overlap")
    assert len(w) == 1 and w[0]["level"] == "warning" and w[0]["pairs"] == [["alice", "bob"]]
    no_ts = [{k: v for k, v in x.items() if k != "ts"} for x in w]   # the timestamp can contain "75"
    assert not any(n in json.dumps(no_ts) for n in ("60", "74", "75", "95"))
    assert events(startup_lines(env()), "config_bands_overlap") == []


def test_address_filter_off_warns_at_startup():
    w = events(startup_lines(env()), "config_address_filter_off")
    assert len(w) == 1 and w[0]["level"] == "warning"
    assert events(startup_lines(env(SCALE_ADDRESS="AA:BB:CC:DD:EE:01")), "config_address_filter_off") == []


def test_custom_category_lookup_pages_and_matches_the_name_exactly():
    api = FakeWger({"alice": TOKEN_A}, name_contains=True)       # filter = substring, like icontains
    for i in range(150):
        api._add_cat("alice", f"Impedance 50 kHz trunk old {i}", "ohm", "custom", False)
    want = api._add_cat("alice", "Impedance 50 kHz trunk", "ohm", "custom", False)

    async def go():
        async with api.client() as http:
            return await WgerClient(http, TOKEN_A).custom_category("Impedance 50 kHz trunk", "ohm")
    assert run(go()) == (want["id"], False) and api.posts("measurement-category") == []


def test_concurrent_first_writes_create_each_category_once():
    api = FakeWger({"alice": TOKEN_A})
    other = dataclasses.replace(later(READING, hours=1), weight_cg=7100, df_raw=FRESH_DF)

    async def go():
        async with api.client() as http:
            c = WgerClient(http, TOKEN_A)
            return await asyncio.gather(*(c.write_reading(r, external_id(SCALE, r, "alice"))
                                          for r in (READING, other)))
    res = run(go())
    assert sorted(x["categories_created"] for x in res) == [0, 10]
    assert len([c for c in api.categories if not c["is_official"]]) == 10 and len(api.measurements) == 22
