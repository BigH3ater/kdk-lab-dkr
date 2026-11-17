"""Derived values to wger (SCALE_BRIDGE_WRITE_DERIVED): formulas, sex guard, value guards, replay.

All values are synthetic sentinels (never anyone's data): if one shows up in a log line, a value leaked.
"""
import dataclasses
import json
import math
from datetime import timedelta

import httpx
import pytest

from conftest import TOKEN_A, FakeWger
from test_engine import log_lines, person_json  # noqa: F401 - log_lines is a fixture
from test_engine_to_wger import OK_PAYLOAD, OUT as ENGINE_OUT, READING, bridge_run, cat, write, wrote
from scale_bridge.wger import (ANDROID_PER_TRUNK_FAT, DERIVED_METRICS, ENGINE_METRICS, GYNOID_PER_LEG_FAT,
                               LB_PER_KG, TORSO_SHARE_OF_WEIGHT, derived_rows, engine_rows, external_id)
from test_wger_bridge import SCALE

INPUTS = {"getPpMineralKg": 31.234, "getPpBodyFatKgTrunk": 5.678, "getPpBodyFatKgLeftLeg": 1.357,
          "getPpBodyFatKgRightLeg": 1.468, "getPpWeightKg": 69.999}       # synthetic; bone comes from ENGINE_OUT
OUT = {**ENGINE_OUT, **INPUTS}
BONE = OUT["getPpBoneKg"]
ANDROID = ANDROID_PER_TRUNK_FAT * INPUTS["getPpBodyFatKgTrunk"]
GYNOID = GYNOID_PER_LEG_FAT * (INPUTS["getPpBodyFatKgLeftLeg"] + INPUTS["getPpBodyFatKgRightLeg"])
EXPECTED = {
    "Mineral (scale est.)": INPUTS["getPpMineralKg"] - BONE,
    "Android fat (scale est.)": ANDROID,
    "Gynoid fat (scale est.)": GYNOID,
    "A/G ratio (scale est.)": 100 * ANDROID / GYNOID,
    "Torso fat (scale est.)": 100 * INPUTS["getPpBodyFatKgTrunk"] / (TORSO_SHARE_OF_WEIGHT * INPUTS["getPpWeightKg"]),
}
NAMES = [n for n, *_ in DERIVED_METRICS]
UNIT = {n: u for n, u, *_ in DERIVED_METRICS}
DERIVED_KEYS = ("derived_created", "derived_exists", "derived_skipped", "derived_skipped_sex",
                "derived_rejected", "derived_categories_created")
MALE = person_json(height_cm=180, sex="male", age=40)
FEMALE = person_json(height_cm=170, sex="female", age=40)
PAYLOAD = {**OK_PAYLOAD, "out": OUT}


def test_table():
    assert NAMES == list(EXPECTED) and all(n.endswith(" (scale est.)") for n in NAMES)
    assert not set(NAMES) & {n for n, _, _ in ENGINE_METRICS}
    formulas = [f for _, _, f, _, _ in DERIVED_METRICS]
    assert len(set(formulas)) == 5
    assert [m for _, _, _, m, _ in DERIVED_METRICS] == [False, True, True, True, True]
    assert "0.780" in formulas[1] and "1.422" in formulas[2] and "0.529" in formulas[4]


def test_male_gets_all_five_equal_to_the_formulas():
    d = derived_rows(OUT, male=True)
    assert (d.skipped, d.skipped_sex) == (0, 0)
    assert {n: v for n, _, v, _ in d.rows} == pytest.approx(EXPECTED)


def test_not_male_gets_mineral_only():
    d = derived_rows(OUT, male=False)
    assert [r[0] for r in d.rows] == ["Mineral (scale est.)"] and (d.skipped, d.skipped_sex) == (0, 4)


@pytest.mark.parametrize("change,missing", [
    ({"getPpBodyFatKgTrunk": None}, {"Android fat (scale est.)", "A/G ratio (scale est.)", "Torso fat (scale est.)"}),
    ({"getPpBodyFatKgLeftLeg": 0}, {"Gynoid fat (scale est.)", "A/G ratio (scale est.)"}),
    ({"getPpBodyFatKgRightLeg": -1.0}, {"Gynoid fat (scale est.)", "A/G ratio (scale est.)"}),
    ({"getPpWeightKg": float("nan")}, {"Torso fat (scale est.)"}),
    ({"getPpWeightKg": "Infinity"}, {"Torso fat (scale est.)"}),
    ({"getPpMineralKg": BONE}, {"Mineral (scale est.)"}),               # result zero
    ({"getPpMineralKg": BONE / 2}, {"Mineral (scale est.)"}),           # result negative
    ({"getPpBoneKg": True}, {"Mineral (scale est.)"}),
    ({"getPpBodyFatKgTrunk": "abc"}, {"Android fat (scale est.)", "A/G ratio (scale est.)", "Torso fat (scale est.)"}),
])
def test_bad_inputs_skip_only_the_rows_that_use_them(change, missing):
    out = {k: v for k, v in {**OUT, **change}.items() if v is not None}
    d = derived_rows(out, male=True)
    assert {r[0] for r in d.rows} == set(NAMES) - missing and d.skipped == len(missing)
    assert all(math.isfinite(v) and v > 0 for _, _, v, _ in d.rows)


def test_no_out_object_skips_everything():
    assert derived_rows(None, True).skipped == 5 and derived_rows(None, False)[1:] == (1, 4)


def test_engine_rows_carries_derived_only_when_asked():
    assert engine_rows(OUT).derived is None
    assert len(engine_rows(OUT, derived_male=True).derived.rows) == 5


def test_write_converts_mass_to_lb_and_marks_extra_data():
    api = FakeWger({"alice": TOKEN_A}, weight_unit={"alice": "lb"})
    counts, = write(api, READING, rows=[engine_rows(OUT, derived_male=True)])
    assert counts["engine_created"] == 16
    assert {k: counts[k] for k in DERIVED_KEYS} == {
        "derived_created": 5, "derived_exists": 0, "derived_skipped": 0, "derived_skipped_sex": 0,
        "derived_rejected": 0, "derived_categories_created": 5}
    vals = api.values("alice")
    for name, v in EXPECTED.items():
        if UNIT[name] == "mass":
            assert cat(api, name)["unit"] == "lb" and vals[name] == [round(v * LB_PER_KG, 2)], name
        else:
            assert cat(api, name)["unit"] == "%" and vals[name] == [round(v, 2)], name
    der = [x for x in api.measurements if x["extra_data"].get("derived")]
    assert len(der) == 5 and not any("engine_field" in x["extra_data"] for x in der)
    assert {x["extra_data"]["formula"] for x in der} == {f for _, _, f, _, _ in DERIVED_METRICS}
    weight_date = next(x["date"] for x in api.measurements if x["value"] == 70.0)
    assert {x["date"] for x in der} == {weight_date}
    assert {x["external_id"] for x in der} == {str(external_id(SCALE, READING, "alice"))}


def test_replay_adds_only_the_derived_rows_and_a_full_replay_creates_nothing():
    api = FakeWger({"alice": TOKEN_A})
    replay = dataclasses.replace(READING, measured_at=READING.measured_at + timedelta(minutes=3))
    first, second, third = write(api, READING, replay, replay,
                                 rows=[engine_rows(OUT), engine_rows(OUT, True), engine_rows(OUT, True)])
    assert (first["created"], first["engine_created"]) == (11, 16) and "derived_created" not in first
    assert (second["created"], second["engine_created"], second["engine_exists"]) == (0, 0, 16)
    assert (second["derived_created"], second["derived_exists"]) == (5, 0)
    assert (third["created"], third["engine_created"], third["derived_created"], third["derived_exists"]) == (0, 0, 0, 5)
    assert third["derived_categories_created"] == 0 and len(api.measurements) == 32
    assert len({x["date"] for x in api.measurements}) == 1


class RejectingWger(FakeWger):
    def _handle(self, req):
        if req.method == "POST" and req.url.path.rstrip("/").endswith("measurement") and req.content:
            if json.loads(req.content).get("extra_data", {}).get("formula", "").startswith("android-over"):
                self.requests.append(req)
                return httpx.Response(400, json={"value": ["Ensure that there are no more than 6 digits."]})
        return super()._handle(req)


def test_a_rejected_derived_row_is_counted_and_the_rest_still_written():
    api = RejectingWger({"alice": TOKEN_A})
    counts, = write(api, READING, rows=[engine_rows(OUT, True)])
    assert (counts["engine_created"], counts["derived_created"], counts["derived_rejected"]) == (16, 4, 1)
    assert "A/G ratio (scale est.)" not in api.values("alice")


# --- through the bridge ------------------------------------------------------------------------

def test_bridge_male_writes_five_and_logs_counts_only(tmp_path, log_lines):
    out, api = bridge_run(tmp_path, MALE, payload=PAYLOAD, SCALE_BRIDGE_WRITE_DERIVED="true")
    w = wrote(log_lines)
    assert out == "queued" and w["engine_created"] == 16
    assert (w["derived_created"], w["derived_skipped"], w["derived_skipped_sex"]) == (5, 0, 0)
    assert set(NAMES) <= set(api.values("alice"))
    text = json.dumps(log_lines())
    for v in [*INPUTS.values(), *EXPECTED.values(), *(round(v, 2) for v in EXPECTED.values())]:
        assert str(v) not in text, v
    assert '"male"' not in text and '"sex"' not in text


def test_bridge_female_writes_mineral_only(tmp_path, log_lines):
    out, api = bridge_run(tmp_path, FEMALE, payload=PAYLOAD, SCALE_BRIDGE_WRITE_DERIVED="true")
    w = wrote(log_lines)
    assert (w["engine_created"], w["derived_created"], w["derived_skipped_sex"], w["derived_skipped"]) == (16, 1, 4, 0)
    assert [n for n in NAMES if n in api.values("alice")] == ["Mineral (scale est.)"]
    assert {c["name"] for c in api.categories} & set(NAMES) == {"Mineral (scale est.)"}


@pytest.mark.parametrize("env", [{}, {"SCALE_BRIDGE_WRITE_DERIVED": "false"}])
def test_bridge_switch_off_writes_no_derived_rows(tmp_path, log_lines, env):
    out, api = bridge_run(tmp_path, MALE, payload=PAYLOAD, **env)
    w = wrote(log_lines)
    assert w["engine_created"] == 16 and not set(DERIVED_KEYS) & set(w)
    assert not set(NAMES) & {c["name"] for c in api.categories}


@pytest.mark.parametrize("kw", [
    {"write_engine": "false"},
    {"payload": {**PAYLOAD, "errorType": "PP_ERROR_TYPE_SECRET"}},
    {"mode": "exit1"},
    {"reading": dataclasses.replace(READING, df_raw=())},
])
def test_bridge_no_derived_rows_without_an_ok_engine_run(tmp_path, log_lines, kw):
    out, api = bridge_run(tmp_path, MALE, **{"payload": PAYLOAD, **kw}, SCALE_BRIDGE_WRITE_DERIVED="true")
    w = wrote(log_lines)
    assert out == "queued" and w["created"] == 11 and not set(DERIVED_KEYS) & set(w)
    assert not set(NAMES) & {c["name"] for c in api.categories}
