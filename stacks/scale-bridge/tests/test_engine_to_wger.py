"""Engine results to wger (SCALE_BRIDGE_WRITE_ENGINE): categories, units, replay, failure isolation.

All values are synthetic sentinels (never anyone's data): if one shows up in a log line, a value leaked.
"""
import asyncio
import dataclasses
import json
import shlex
from datetime import timedelta

import httpx
import pytest

from conftest import TOKEN_A, FakeWger
from test_engine import DF, SECRET, fake_engine, log_lines, person_json  # noqa: F401 - log_lines is a fixture
from scale_bridge import app
from scale_bridge.app import Bridge
from scale_bridge.config import from_env
from scale_bridge.wger import ENGINE_METRICS, LB_PER_KG, EngineRows, WgerClient, engine_rows, external_id
from test_wger_bridge import READING as RAW, SCALE

READING = dataclasses.replace(RAW, df_raw=DF)          # impedances + four valid DF frames
GETTERS = [g for _, g, _ in ENGINE_METRICS]
VALUES = {g: round(11.111 + 3.217 * i, 3) for i, g in enumerate(GETTERS)}   # synthetic, distinct
VALUES["getPpBMR"] = 1777.777
NOT_WRITTEN = {"getPpBodyAge": 41, "getPpBalanceFatArmLegLevel": 2, "getPpBodyFatSubCutPercentage": 9.876,
               "getPpMineralKg": 3.333, "getPpBodyFatKgTrunk": 4.444, "getPpBodySkeletal": 44.444}
OUT = {**VALUES, **NOT_WRITTEN}
UNIT = {n: u for n, _, u in ENGINE_METRICS}
NAMES = [n for n, _, _ in ENGINE_METRICS]
ENGINE_KEYS = ("engine_result", "engine_created", "engine_exists", "engine_skipped", "engine_rejected",
               "engine_categories_created")


def run(coro):
    return asyncio.run(coro)


def write(api, *readings, rows=None):
    """write_reading for each reading with a fresh client (no cache, like a restart)."""
    async def go():
        out = []
        async with api.client() as http:
            for r, rw in zip(readings, rows or [engine_rows(OUT)] * len(readings)):
                eng = None if rw is None else (lambda rw=rw: _const(rw))
                out.append(await WgerClient(http, TOKEN_A).write_reading(
                    r, external_id(SCALE, r, "alice"), engine=eng))
        return out
    return run(go())


async def _const(x):
    return x


def cat(api, name):
    return next(c for c in api.categories if c["name"] == name and c["metric_type"] == "custom")


def test_metric_table_and_getters():
    assert len(ENGINE_METRICS) == 16 and len(set(NAMES)) == 16
    assert all(n.endswith(" (scale)") for n in NAMES)
    assert not set(GETTERS) & set(NOT_WRITTEN)


def test_happy_path_writes_one_row_per_metric_at_the_weight_time():
    api = FakeWger({"alice": TOKEN_A})
    counts, = write(api, READING)
    assert (counts["created"], counts["categories_created"]) == (11, 10)
    assert {k: counts[k] for k in ENGINE_KEYS} == {
        "engine_result": "ok", "engine_created": 16, "engine_exists": 0, "engine_skipped": 0,
        "engine_rejected": 0, "engine_categories_created": 16}
    vals = api.values("alice")
    for name, getter, unit in ENGINE_METRICS:
        assert vals[name] == [round(VALUES[getter], 2)], name
        assert cat(api, name)["unit"] == ("kg" if unit == "mass" else unit)
    weight_date = next(x["date"] for x in api.measurements if x["value"] == 70.0)
    eng = [x for x in api.measurements if "engine_field" in x["extra_data"]]
    assert len(eng) == 16 and {x["date"] for x in eng} == {weight_date}
    assert {x["external_id"] for x in eng} == {str(external_id(SCALE, READING, "alice"))}
    assert {x["extra_data"]["engine_field"] for x in eng} == set(GETTERS)


def test_mass_categories_are_created_in_the_profile_unit():
    api = FakeWger({"alice": TOKEN_A}, weight_unit={"alice": "lb"})
    write(api, READING)
    vals = api.values("alice")
    for name, getter, unit in ENGINE_METRICS:
        if unit == "mass":
            assert cat(api, name)["unit"] == "lb"
            assert vals[name] == [round(VALUES[getter] * LB_PER_KG, 2)]
        else:
            assert vals[name] == [round(VALUES[getter], 2)]
    assert vals["Body weight"] == [70.0]              # the weight itself stays kg + extra_data.unit


def test_an_existing_category_keeps_its_unit_whatever_the_profile_says():
    api = FakeWger({"alice": TOKEN_A}, weight_unit={"alice": "kg"})
    api._add_cat("alice", "ECW (scale)", "lb", "custom", False)
    api._add_cat("alice", "Lean trunk (scale)", "kg", "custom", False)
    api._add_cat("alice", "Bone mineral (scale)", "stone", "custom", False)   # not kg/lb: skipped
    counts, = write(api, READING)
    vals = api.values("alice")
    assert vals["ECW (scale)"] == [round(VALUES["getPpWaterECWKg"] * LB_PER_KG, 2)]
    assert vals["Lean trunk (scale)"] == [round(VALUES["getPpMuscleKgTrunk"], 2)]
    assert "Bone mineral (scale)" not in vals
    assert (counts["engine_created"], counts["engine_skipped"], counts["engine_categories_created"]) == (15, 1, 13)
    api.weight_unit["alice"] = "lb"                  # a later profile change does not mix units
    write(api, dataclasses.replace(READING, measured_at=READING.measured_at + timedelta(days=1),
                                   weight_cg=7100))
    assert cat(api, "ECW (scale)")["unit"] == "lb" and cat(api, "Lean mass (scale)")["unit"] == "kg"
    assert api.values("alice")["Lean mass (scale)"] == [round(VALUES["getPpLoseFatWeightKg"], 2)] * 2


def test_non_custom_categories_with_similar_names_are_never_used():
    api = FakeWger({"alice": TOKEN_A}, name_contains=True)
    health = [api._add_cat("alice", n, u, m, True) for n, u, m in (
        ("Body fat", "%", "body_fat"), ("Heart rate", "bpm", "heart_rate"),
        ("Body fat (scale)", "%", "body_fat"), ("Heart rate (scale)", "bpm", "heart_rate"))]
    hr = dataclasses.replace(READING, heart_rate_bpm=66)

    async def go():
        async with api.client() as http:
            return await WgerClient(http, TOKEN_A).write_reading(
                hr, external_id(SCALE, hr, "alice"), heart_rate=True, engine=lambda: _const(engine_rows(OUT)))
    counts = run(go())
    ids = {c["id"] for c in health}
    assert not [x for x in api.measurements if x["category"] in ids]
    assert counts["created"] == 12 and counts["engine_created"] == 16
    assert cat(api, "Body fat (scale)")["id"] not in ids and cat(api, "Heart rate (scale)")["id"] not in ids


@pytest.mark.parametrize("bad", [float("nan"), "NaN", "Infinity", 0, 0.0, -1.5, None, True, "abc", [1]])
def test_bad_values_skip_that_metric_only(bad):
    out = {**OUT, "getPpFat": bad, "getPpCellMassKg": bad}
    rows = engine_rows(out)
    assert rows.skipped == 2 and len(rows.rows) == 14
    api = FakeWger({"alice": TOKEN_A})
    counts, = write(api, READING, rows=[rows])
    assert (counts["engine_created"], counts["engine_skipped"]) == (14, 2)
    assert "Body fat (scale)" not in api.values("alice") and "Body cell mass (scale)" not in api.values("alice")


def test_engine_rows_without_out_object_skip_everything():
    assert engine_rows(None) == EngineRows("ok", (), 16)


def test_replay_adds_only_the_missing_engine_rows_and_a_full_replay_creates_nothing():
    api = FakeWger({"alice": TOKEN_A})
    replay = dataclasses.replace(READING, measured_at=READING.measured_at + timedelta(minutes=3))
    first, second, third = write(api, READING, replay, replay, rows=[None, engine_rows(OUT), engine_rows(OUT)])
    assert first == {"created": 11, "exists": 0, "categories_created": 10}       # switch off: no engine keys
    assert (second["created"], second["exists"], second["engine_created"], second["engine_exists"]) == (0, 11, 16, 0)
    assert (third["created"], third["exists"], third["engine_created"], third["engine_exists"]) == (0, 11, 0, 16)
    assert third["engine_categories_created"] == 0 and len(api.measurements) == 27
    weight_date = next(x["date"] for x in api.measurements if x["value"] == 70.0)
    assert {x["date"] for x in api.measurements} == {weight_date}               # all at the first weigh-in


class RejectingWger(FakeWger):
    """wger refuses one engine value with a plain 400 (as a too-large number would be)."""

    def _handle(self, req):
        if req.method == "POST" and req.url.path.rstrip("/").endswith("measurement") and req.content:
            body = json.loads(req.content)
            if body.get("extra_data", {}).get("engine_field") == "getPpBMR":
                self.requests.append(req)
                return httpx.Response(400, json={"value": ["Ensure that there are no more than 6 digits."]})
        return super()._handle(req)


def test_a_rejected_engine_row_is_counted_and_the_rest_still_written():
    api = RejectingWger({"alice": TOKEN_A})
    counts, = write(api, READING)
    assert (counts["created"], counts["engine_created"], counts["engine_rejected"]) == (11, 15, 1)
    assert "BMR (scale)" not in api.values("alice") and api.values("alice")["Body weight"] == [70.0]


# --- through the bridge: handle -> compute -> write task ---------------------------------------

def bridge_run(tmp_path, people, write_engine="true", mode="ok", payload=None, secret=SECRET, reading=READING,
               compute=True, api=None, **env):
    cmd, _ = fake_engine(tmp_path, mode, payload)
    api = api or FakeWger({"alice": TOKEN_A})
    s = from_env({"SCALE_BRIDGE_PEOPLE": people, "WGER_API_KEY_ALICE": TOKEN_A, "SCALE_BRIDGE_DRY_RUN": "false",
                  "SCALE_BRIDGE_WRITE_ENGINE": write_engine, "LEFU_SECRET_8": secret,
                  "SCALE_BRIDGE_ENGINE_CMD": shlex.join(cmd), "SCALE_BRIDGE_ENGINE_TIMEOUT_S": "5", **env})

    async def no_sleep(_):
        pass

    async def go():
        async with api.client() as http:
            b = Bridge(s, http, sleep=no_sleep)
            b.engine.startup([x.name for x in s.people if x.profile])
            d = await b.handle(reading, SCALE, "s1")
            if compute:
                await b.compute(reading, d, "s1")
            await b.drain()
            return d.outcome
    return run(go()), api


PROFILE = person_json(height_cm=180, sex="male", age=40)
OK_PAYLOAD = {"id": "scale-bridge", "errorType": "PP_ERROR_TYPE_NONE", "out": OUT, "base": {"getZ20KhzTrunkDeCode": 1.5}}


def wrote(lines):
    w = [x for x in lines() if x["event"] == "wrote"]
    assert len(w) == 1
    return w[0]


def test_bridge_writes_engine_rows_and_heart_rate_and_logs_counts_only(tmp_path, log_lines):
    hr = dataclasses.replace(READING, heart_rate_bpm=66)
    out, api = bridge_run(tmp_path, PROFILE, payload=OK_PAYLOAD, reading=hr, SCALE_BRIDGE_WRITE_HEART_RATE="true")
    w = wrote(log_lines)
    assert out == "queued" and w["created"] == 12 and w["engine_result"] == "ok"
    assert (w["engine_created"], w["engine_categories_created"], w["engine_skipped"]) == (16, 16, 0)
    assert api.values("alice")["Heart rate (scale)"] == [66.0]
    text = json.dumps(log_lines())
    for v in [*VALUES.values(), *(round(v, 2) for v in VALUES.values()),
              *(round(v * LB_PER_KG, 2) for v in VALUES.values()), *NOT_WRITTEN.values()]:
        if isinstance(v, float):
            assert str(v) not in text, v
    assert '"weight' not in text.replace('"would_write"', "")


@pytest.mark.parametrize("people,kw,reason", [
    (person_json(), {}, "no_profile"),
    (PROFILE, {"secret": ""}, "engine_off"),
    (PROFILE, {"SCALE_BRIDGE_ENGINE": "off"}, "engine_off"),
    (PROFILE, {"payload": {**OK_PAYLOAD, "errorType": "PP_ERROR_TYPE_SECRET"}}, "engine_error"),
    (PROFILE, {"mode": "exit1"}, "nonzero_exit"),
    (PROFILE, {"reading": dataclasses.replace(READING, df_raw=())}, "not_run"),
])
def test_no_engine_rows_but_weight_and_impedance_are_written(tmp_path, log_lines, people, kw, reason):
    out, api = bridge_run(tmp_path, people, **kw)
    w = wrote(log_lines)
    assert out == "queued" and w["created"] == 11 and w["engine_result"] == reason and w["engine_created"] == 0
    assert not set(NAMES) & set(api.values("alice"))


def test_switch_off_writes_nothing_new(tmp_path, log_lines):
    out, api = bridge_run(tmp_path, PROFILE, write_engine="false", payload=OK_PAYLOAD)
    w = wrote(log_lines)
    assert w["created"] == 11 and not set(ENGINE_KEYS) & set(w)
    assert not set(NAMES) & {c["name"] for c in api.categories}


def test_an_engine_that_never_answers_does_not_hold_back_the_weight(tmp_path, log_lines, monkeypatch):
    monkeypatch.setattr(app, "ENGINE_WAIT_MARGIN_S", 0.0)
    out, api = bridge_run(tmp_path, PROFILE, compute=False, SCALE_BRIDGE_ENGINE_TIMEOUT_S="0.2")
    w = wrote(log_lines)
    assert w["created"] == 11 and w["engine_result"] == "no_result" and w["engine_created"] == 0
    assert api.values("alice")["Body weight"] == [70.0]


def test_weight_is_posted_before_the_engine_answers(tmp_path):
    """The write task posts weight + impedance while compute() is still running the engine."""
    api = FakeWger({"alice": TOKEN_A})
    seen = {}
    cmd, _ = fake_engine(tmp_path, "ok", OK_PAYLOAD)
    s = from_env({"SCALE_BRIDGE_PEOPLE": PROFILE, "WGER_API_KEY_ALICE": TOKEN_A, "SCALE_BRIDGE_DRY_RUN": "false",
                  "SCALE_BRIDGE_WRITE_ENGINE": "true", "LEFU_SECRET_8": SECRET,
                  "SCALE_BRIDGE_ENGINE_CMD": shlex.join(cmd), "SCALE_BRIDGE_ENGINE_TIMEOUT_S": "5"})

    async def go():
        async with api.client() as http:
            b = Bridge(s, http)
            b.engine.startup(["alice"])
            real = b.engine.run

            async def slow(case):
                await asyncio.sleep(0.3)
                seen["posts_before_engine_done"] = len(api.posts("measurement"))
                return await real(case)
            b.engine.run = slow
            d = await b.handle(READING, SCALE, "s1")
            await b.compute(READING, d, "s1")
            await b.drain()
    run(go())
    assert seen["posts_before_engine_done"] == 11 and len(api.posts("measurement")) == 27
