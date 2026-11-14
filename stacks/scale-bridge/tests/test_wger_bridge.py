"""wger client against an in-memory API, and the Bridge decision path (dry-run, attribution)."""
import asyncio
import io
import json
import logging
import uuid
from datetime import datetime, timezone

from conftest import TOKEN_A, TOKEN_B, FakeWger
from scale_bridge import logs
from scale_bridge.app import Bridge
from scale_bridge.config import from_env
from scale_bridge.session import IMPEDANCE_CATEGORIES, Reading
from scale_bridge.wger import WgerClient, external_id, plan

WHEN = datetime(2026, 1, 1, 7, 30, 0, 123000, tzinfo=timezone.utc)
SCALE = "AA:BB:CC:DD:EE:01"
READING = Reading(measured_at=WHEN, weight_cg=7000, z50_deci=(3000, 3050, 250, 2900, 2850),
                  z250_deci=(2700, 2750, 220, 2580, 2540), df_raw=("df" + "00" * 16,) * 4)
PEOPLE = json.dumps([
    {"name": "alice", "min_kg": 60, "max_kg": 75, "token_env": "WGER_API_KEY_ALICE"},
    {"name": "bob", "min_kg": 80, "max_kg": 95, "token_env": "WGER_API_KEY_BOB"},
])


def env(**kw):
    return {"SCALE_BRIDGE_PEOPLE": PEOPLE, "WGER_API_KEY_ALICE": TOKEN_A, "WGER_API_KEY_BOB": TOKEN_B, **kw}


def run(coro):
    return asyncio.run(coro)


def test_external_id_is_stable_and_per_person():
    a = external_id(SCALE, READING, "alice")
    assert a == external_id(SCALE.lower(), READING, "alice")
    assert a != external_id(SCALE, READING, "bob")
    assert isinstance(a, uuid.UUID)


def test_first_write_creates_categories_and_eleven_entries():
    api = FakeWger({"alice": TOKEN_A})

    async def go():
        async with api.client() as http:
            return await WgerClient(http, TOKEN_A).write_reading(READING, external_id(SCALE, READING, "alice"))
    counts = run(go())
    assert counts == {"created": 11, "exists": 0, "categories_created": 10}
    vals = api.values("alice")
    assert vals["Body weight"] == [70.0]
    assert vals["Impedance 50 kHz trunk"] == [25.0] and vals["Impedance 250 kHz left leg"] == [254.0]
    assert sorted(c["name"] for c in api.categories if not c["is_official"]) == sorted(IMPEDANCE_CATEGORIES)
    new = [json.loads(r.content) for r in api.posts("measurement-category")]
    assert all(c["metric_type"] == "custom" and c["unit"] == "ohm" for c in new)
    weight = next(json.loads(r.content) for r in api.posts("measurement")
                  if json.loads(r.content)["value"] == 70.0)
    assert weight["extra_data"]["unit"] == "kg" and len(weight["extra_data"]["df_raw"]) == 4
    assert weight["source"] == "user" and weight["date"] == "2026-01-01T07:30:00.123+00:00"


def test_repost_is_idempotent():
    api = FakeWger({"alice": TOKEN_A})
    ext = external_id(SCALE, READING, "alice")

    async def go():
        async with api.client() as http:
            await WgerClient(http, TOKEN_A).write_reading(READING, ext)
            return await WgerClient(http, TOKEN_A).write_reading(READING, ext)   # fresh client: no cache
    assert run(go()) == {"created": 0, "exists": 11, "categories_created": 0}
    assert len(api.measurements) == 11 and len(api.posts("measurement")) == 11
    assert len(api.posts("measurement-category")) == 10


def test_unique_constraint_400_counts_as_present():
    api = FakeWger({"alice": TOKEN_A})
    ext = external_id(SCALE, READING, "alice")

    async def go():
        async with api.client() as http:
            c = WgerClient(http, TOKEN_A)
            cat = await c.body_weight_category()
            await c.ensure_measurement(cat, WHEN, 70.0, ext, {"unit": "kg"})
            # the GET window misses (a different date), the POST hits the constraint
            return await c.ensure_measurement(cat, WHEN.replace(hour=9), 70.0, ext, {"unit": "kg"})
    assert run(go()) == "exists"


def test_weight_only_reading_writes_one_entry():
    api = FakeWger({"alice": TOKEN_A})
    r = Reading(measured_at=WHEN, weight_cg=7000, z50_deci=None, z250_deci=None)

    async def go():
        async with api.client() as http:
            return await WgerClient(http, TOKEN_A).write_reading(r, external_id(SCALE, r, "alice"))
    assert run(go()) == {"created": 1, "exists": 0, "categories_created": 0}
    assert plan(r) == [{"category": "Body weight", "value": 70.0, "unit": "kg"}]


def test_no_official_category_uses_legacy_endpoint_in_profile_unit():
    api = FakeWger({"alice": TOKEN_A}, official={"alice": False}, weight_unit={"alice": "lb"})
    ext = external_id(SCALE, READING, "alice")

    async def go():
        async with api.client() as http:
            first = await WgerClient(http, TOKEN_A).write_reading(READING, ext)
            again = await WgerClient(http, TOKEN_A).write_reading(READING, ext)
            return first, again
    first, again = run(go())
    assert api.weightentries == [{"date": "2026-01-01T07:30:00.123+00:00", "weight": 154.32}]
    assert first["created"] == 11 and again == {"created": 0, "exists": 11, "categories_created": 0}


def bridge_run(e, reading=READING, wger=None):
    api = wger or FakeWger({"alice": TOKEN_A, "bob": TOKEN_B})
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(logs.JsonFormatter())
    lg = logging.getLogger("scale_bridge")
    lg.addHandler(handler)
    lg.setLevel(logging.DEBUG)
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    async def go():
        async with api.client() as http:
            b = Bridge(from_env(e), http, sleep=fake_sleep)
            out = await b.handle(reading, SCALE, "s1")
            await b.drain()
            return out
    try:
        out = run(go())
    finally:
        lg.removeHandler(handler)
    lines = [json.loads(x) for x in buf.getvalue().splitlines()]
    return out, api, lines, sleeps


def test_dry_run_posts_nothing_and_logs_weight_only_in_would_write():
    out, api, lines, _ = bridge_run(env())          # SCALE_BRIDGE_DRY_RUN unset -> dry-run
    assert out == "dry_run" and api.requests == []
    ww = [x for x in lines if x["event"] == "would_write"]
    assert len(ww) == 1 and ww[0]["person"] == "alice" and ww[0]["weight_kg"] == 70.0
    assert ww[0]["weight_lb"] == 154.3                 # 70 kg, rounded to 0.1 lb for the display
    assert ww[0]["impedance_values"] == 10 and len(ww[0]["categories"]) == 11
    others = json.dumps([x for x in lines if x["event"] != "would_write"])
    assert "70.0" not in others and "154.3" not in others and "305" not in others
    assert "weight_lb" not in others and "weight_kg" not in others


def test_dry_run_with_a_pound_band_matches_and_logs_lb():
    people = json.dumps([
        {"name": "alice", "min_lb": 150, "max_lb": 160, "token_env": "WGER_API_KEY_ALICE"},   # 68.0-72.6 kg
        {"name": "bob", "min_kg": 80, "max_kg": 95, "token_env": "WGER_API_KEY_BOB"},
    ])
    out, api, lines, _ = bridge_run(env(SCALE_BRIDGE_PEOPLE=people))
    assert out == "dry_run" and api.requests == []
    ww = [x for x in lines if x["event"] == "would_write"]
    assert len(ww) == 1 and ww[0]["person"] == "alice" and ww[0]["weight_lb"] == 154.3
    assert TOKEN_A not in json.dumps(lines)


def test_live_write_goes_to_the_matched_person_only():
    out, api, lines, _ = bridge_run(env(SCALE_BRIDGE_DRY_RUN="false"))
    assert out == "queued"
    assert len(api.values("alice")["Body weight"]) == 1 and api.values("bob") == {}
    assert all(r.headers["authorization"] == f"Token {TOKEN_A}" for r in api.requests)
    wrote = [x for x in lines if x["event"] == "wrote"]
    assert wrote and wrote[0]["created"] == 11
    assert "70.0" not in json.dumps(lines) and TOKEN_A not in json.dumps(lines)


def test_no_match_and_ambiguous_write_nothing():
    for kg_cg, outcome in ((5000, "no_match"), (7800, "no_match")):
        r = Reading(measured_at=WHEN, weight_cg=kg_cg, z50_deci=None, z250_deci=None)
        out, api, lines, _ = bridge_run(env(SCALE_BRIDGE_DRY_RUN="false"), reading=r)
        assert out == outcome and api.requests == []
        assert any(x["event"] == "attribution" and x["level"] == "warning" for x in lines)
    overlap = json.dumps([
        {"name": "alice", "min_kg": 60, "max_kg": 75, "token_env": "WGER_API_KEY_ALICE"},
        {"name": "bob", "min_kg": 70, "max_kg": 95, "token_env": "WGER_API_KEY_BOB"},
    ])
    out, api, lines, _ = bridge_run(env(SCALE_BRIDGE_DRY_RUN="false", SCALE_BRIDGE_PEOPLE=overlap))
    assert out == "ambiguous" and api.requests == []
    att = next(x for x in lines if x["event"] == "attribution")
    assert att["candidates"] == ["alice", "bob"]


def test_missing_token_skips_the_write():
    e = env(SCALE_BRIDGE_DRY_RUN="false")
    del e["WGER_API_KEY_ALICE"]
    out, api, _, _ = bridge_run(e)
    assert out == "no_token" and api.requests == []


def test_write_retries_after_upstream_errors():
    api = FakeWger({"alice": TOKEN_A, "bob": TOKEN_B}, fail_next=2)
    out, api, lines, sleeps = bridge_run(env(SCALE_BRIDGE_DRY_RUN="false"), wger=api)
    assert [x["event"] for x in lines].count("write_failed") == 2
    assert sleeps == [30.0, 60.0] and len(api.measurements) == 11


def test_startup_token_check_reports_status_not_token():
    api = FakeWger({"alice": TOKEN_A})          # bob's token unknown to wger -> 401
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(logs.JsonFormatter())
    lg = logging.getLogger("scale_bridge")
    lg.addHandler(h)
    lg.setLevel(logging.INFO)

    async def go():
        async with api.client() as http:
            await Bridge(from_env(env()), http).startup_checks()
    try:
        run(go())
    finally:
        lg.removeHandler(h)
    checks = {x["person"]: x["result"] for x in map(json.loads, buf.getvalue().splitlines())
              if x["event"] == "token_check"}
    assert checks == {"alice": "ok", "bob": "rejected"}
    assert TOKEN_A not in buf.getvalue() and TOKEN_B not in buf.getvalue()
    assert all(r.method == "GET" for r in api.requests)
