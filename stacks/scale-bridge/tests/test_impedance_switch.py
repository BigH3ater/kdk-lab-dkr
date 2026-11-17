"""SCALE_BRIDGE_WRITE_IMPEDANCE off (compose since 0.3.1): no impedance categories or rows;
weight, heart rate, engine and derived rows, replay and retry unchanged. Synthetic values only."""
import dataclasses
from datetime import timedelta

from conftest import TOKEN_A, TOKEN_B, FakeWger
from scale_bridge.config import from_env
from scale_bridge.session import IMPEDANCE_CATEGORIES, Reading
from scale_bridge.wger import WgerClient, engine_rows, external_id, plan
from test_derived_values import MALE, NAMES as DERIVED, OUT, PAYLOAD
from test_engine import log_lines  # noqa: F401 - fixture
from test_engine_to_wger import NAMES as ENGINE, READING, bridge_run, wrote
from test_wger_bridge import SCALE, WHEN, env, run
import test_wger_bridge

HR = dataclasses.replace(READING, heart_rate_bpm=66)


def write(api, *readings, rows=None, heart_rate=True):
    """write_reading with the switch off, a fresh client each time (like a restart)."""
    async def go():
        out = []
        async with api.client() as http:
            for r, rw in zip(readings, rows or [None] * len(readings)):
                eng = None if rw is None else (lambda rw=rw: _const(rw))
                out.append(await WgerClient(http, TOKEN_A).write_reading(
                    r, external_id(SCALE, r, "alice"), heart_rate=heart_rate, engine=eng, impedance=False))
        return out
    return run(go())


async def _const(x):
    return x


def no_impedance(api):
    names = {c["name"] for c in api.categories}
    assert not names & set(IMPEDANCE_CATEGORIES)
    assert not [r for r in api.posts("measurement-category") if b"ohm" in r.content]


def later(r, **d):
    return dataclasses.replace(r, measured_at=r.measured_at + timedelta(**d))


def test_config_default_on_and_switch_off():
    assert from_env(env()).write_impedance is True
    assert from_env(env(SCALE_BRIDGE_WRITE_IMPEDANCE="false")).write_impedance is False


def test_plan_without_impedance():
    assert [x["category"] for x in plan(HR, heart_rate=True, impedance=False)] == ["Body weight", "Heart rate (scale)"]
    assert len(plan(HR, heart_rate=True)) == 12                       # default unchanged


def test_full_reading_with_heart_rate_creates_two_and_engine_and_derived_rows():
    api = FakeWger({"alice": TOKEN_A})
    counts, = write(api, HR, rows=[engine_rows(OUT, derived_male=True)])
    assert (counts["created"], counts["exists"], counts["categories_created"]) == (2, 0, 1)
    assert (counts["engine_created"], counts["derived_created"]) == (16, 5)
    no_impedance(api)
    vals = api.values("alice")
    assert vals["Body weight"] == [70.0] and vals["Heart rate (scale)"] == [66.0]
    assert set(ENGINE) <= set(vals) and set(DERIVED) <= set(vals)
    assert len(api.measurements) == 2 + 16 + 5


def test_replay_and_retry_stay_idempotent_and_add_only_missing_rows():
    api = FakeWger({"alice": TOKEN_A})
    full = engine_rows(OUT, derived_male=True)
    # first write: weight only (no heart rate, no engine); then a replay adds HR + engine + derived;
    # then a full replay with fresh DF bytes (other external_id) creates nothing.
    first, second, third = write(api, READING, later(HR, minutes=3),
                                 dataclasses.replace(later(HR, minutes=5), df_raw=tuple("df" + "11" * 16 for _ in range(4))),
                                 rows=[None, full, full])
    assert (first["created"], first["exists"]) == (1, 0)
    assert (second["created"], second["exists"], second["engine_created"], second["derived_created"]) == (1, 1, 16, 5)
    assert (third["created"], third["exists"], third["engine_exists"], third["derived_exists"]) == (0, 2, 16, 5)
    assert third["engine_created"] == third["derived_created"] == 0
    assert len(api.measurements) == 23 and len({x["date"] for x in api.measurements}) == 1
    no_impedance(api)


def test_weight_only_then_full_reading_adds_no_impedance():
    api = FakeWger({"alice": TOKEN_A})
    w = Reading(measured_at=WHEN, weight_cg=7000, z50_deci=None, z250_deci=None)
    _, full = write(api, w, later(READING, minutes=2), heart_rate=False)
    assert full == {"created": 0, "exists": 1, "categories_created": 0} and len(api.measurements) == 1
    no_impedance(api)


def test_bridge_live_switch_off_engine_and_derived_still_written(tmp_path, log_lines):
    out, api = bridge_run(tmp_path, MALE, payload=PAYLOAD, reading=HR, SCALE_BRIDGE_WRITE_HEART_RATE="true",
                          SCALE_BRIDGE_WRITE_DERIVED="true", SCALE_BRIDGE_WRITE_IMPEDANCE="false")
    w = wrote(log_lines)
    assert out == "queued" and (w["created"], w["categories_created"]) == (2, 1)
    assert (w["engine_result"], w["engine_created"], w["derived_created"]) == ("ok", 16, 5)
    no_impedance(api)


def test_bridge_retry_after_an_error_does_not_duplicate():
    api = FakeWger({"alice": TOKEN_A, "bob": TOKEN_B}, timeout_after={"POST measurement": 1})
    _, api, lines, _ = test_wger_bridge.bridge_run(
        env(SCALE_BRIDGE_DRY_RUN="false", SCALE_BRIDGE_WRITE_IMPEDANCE="false"), wger=api)
    w = [x for x in lines if x["event"] == "wrote"]
    assert len(w) == 1 and (w[0]["created"], w[0]["exists"]) == (0, 1) and len(api.measurements) == 1
    no_impedance(api)


def test_dry_run_categories_follow_the_switch_and_the_count_describes_the_reading():
    _, api, lines, _ = test_wger_bridge.bridge_run(env(SCALE_BRIDGE_WRITE_IMPEDANCE="false"))
    ww = [x for x in lines if x["event"] == "would_write"][0]
    assert ww["impedance_values"] == 10 and ww["categories"] == ["Body weight"] and api.requests == []
