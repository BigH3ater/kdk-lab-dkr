"""Body-composition engine: profiles, DF decode, the child process (fake engine), results next to the capture."""
import asyncio
import io
import json
import logging
import os
import shlex
import stat
import sys
import time
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import bleak
import bleak_retry_connector
import httpx
import pytest

from conftest import FakeClient, fixture_frames
from scale_bridge import ble, logs
from scale_bridge import protocol as p
from scale_bridge.app import Bridge, Decision
from scale_bridge.attribution import ConfigError, Profile, load_people
from scale_bridge.capture import Captures, file_name
from scale_bridge.config import engine_from_env, from_env
from scale_bridge.engine import Engine, EngineSettings, age_on, build_case, decode_df, profile_inputs
from scale_bridge.session import Reading, Timeouts

SECRET = "f" * 24 + "SECRETMARKER" + "e" * 28           # dummy, never a real key
NOW = datetime(2026, 1, 2, 3, 4, 5, 678000, tzinfo=timezone.utc)
ADDR = "AA:BB:CC:DD:EE:01"
# sentinel values: if any of these strings shows up in a log line, a value leaked
FAT, BMR, MUSCLE = 23.4567, 1789.25, 31.0987
HEIGHT, BIRTH = 181.9, "1971-06-15"
OK_OUTPUT = {"id": "scale-bridge", "detailModel": "ok", "errorType": "PP_ERROR_TYPE_NONE",
             "out": {"getPpFat": FAT, "getPpBMR": BMR, "getPpBodySkeletalKg": MUSCLE, "getPpBodyAge": 41},
             "base": {"getZ20KhzTrunkDeCode": 456.7}, "timing": {"init_ms": 1234, "calc_ms": 88}}


def person_json(**profile):
    return json.dumps([{"name": "alice", "min_kg": 60, "max_kg": 80, "token_env": "WGER_API_KEY_ALICE", **profile}])


@pytest.fixture
def log_lines():
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(logs.JsonFormatter())
    lg = logging.getLogger("scale_bridge")
    lg.addHandler(h)
    lg.setLevel(logging.DEBUG)
    yield lambda: [json.loads(x) for x in buf.getvalue().splitlines()]
    lg.removeHandler(h)


def fake_engine(tmp_path, mode="ok", payload=None):
    """A stand-in for /usr/local/bin/lefu-engine; records what it was given in seen.json."""
    script, seen = tmp_path / f"fake_engine_{mode}.py", tmp_path / "seen.json"
    script.write_text(f"""
import json, os, sys, time
mode, payload = {mode!r}, {json.dumps(payload if payload is not None else OK_OUTPUT)!r}
data = sys.stdin.read()
json.dump({{"argv": sys.argv[1:], "env": dict(os.environ), "stdin": data}}, open({str(seen)!r}, "w"))
if mode == "sleep":
    time.sleep(30)
elif mode == "garbage":
    print("this is not json {{")
elif mode == "exit1":
    sys.exit(1)
elif mode == "exception":
    print(json.dumps({{"id": "x", "exception": "java.lang.IllegalArgumentException: some input 777.77"}}))
    sys.exit(3)
elif mode == "echo_secret":
    print(json.dumps({{"errorType": "PP_ERROR_TYPE_NONE", "out": {{"x": os.environ.get("LEFU_SECRET_8")}}}}))
else:
    print(payload)
""")
    return (sys.executable, "-I", str(script)), seen


def settings(cmd=("/nonexistent/lefu-engine",), secret=SECRET, timeout=10.0, **kw):
    return EngineSettings(command=tuple(cmd), secret=secret, timeout=timeout, **kw)


def started(s: EngineSettings) -> Engine:
    e = Engine(s)
    e.startup(["alice"])
    return e


def df_frame(sub, x, values):
    payload = b"".join(v.to_bytes(4, "little") for v in values).ljust(12, b"\0")
    body = bytes([0xDF, 0x02, sub, x]) + payload
    return (body + bytes([p.xor(body)])).hex()


X0 = [314159011, 314159012, 314159013, 314159014, 314159015]
X1 = [271828021, 271828022, 271828023, 271828024, 271828025]
DF = tuple(df_frame(s, x, (X0 if x == 0 else X1)[:3] if s == 0 else (X0 if x == 0 else X1)[3:])
           for s in (0, 1) for x in (0, 1))


def reading(df=DF, at=NOW):
    return Reading(measured_at=at, weight_cg=7000, z50_deci=None, z250_deci=None, df_raw=df)


# --- profiles ----------------------------------------------------------------------------------

def test_profile_is_optional_and_parsed_with_birth_date_or_age():
    a, = load_people(person_json())
    assert a.profile is None
    b, = load_people(person_json(height_cm=HEIGHT, sex="male", birth_date=BIRTH))
    assert b.profile == Profile(height_cm=HEIGHT, sex="male", birth_date=date(1971, 6, 15))
    c, = load_people(person_json(height_cm=160, sex="female", age=33))
    assert c.profile == Profile(height_cm=160.0, sex="female", age=33)
    assert "181" not in repr(b) and "1971" not in repr(b) and "hidden" in repr(b.profile)


@pytest.mark.parametrize("profile,msg", [
    ({"height_cm": 180}, "missing sex"),
    ({"sex": "male", "age": 30}, "missing height_cm"),
    ({"height_cm": 180, "sex": "male"}, "exactly one of birth_date"),
    ({"height_cm": 180, "sex": "male", "age": 30, "birth_date": "1990-01-01"}, "exactly one of birth_date"),
    ({"height_cm": 180, "sex": "m", "age": 30}, "sex must be"),
    ({"height_cm": "180", "sex": "male", "age": 30}, "height_cm must be"),
    ({"height_cm": True, "sex": "male", "age": 30}, "height_cm must be"),
    ({"height_cm": 400, "sex": "male", "age": 30}, "height_cm must be"),
    ({"height_cm": 180, "sex": "male", "age": 30.5}, "age must be"),
    ({"height_cm": 180, "sex": "male", "birth_date": "15/06/1987"}, "birth_date must be a date"),
    ({"height_cm": 180, "sex": "male", "birth_date": "1987-02-30"}, "birth_date must be a date"),
    ({"height_cm": 180, "sex": "male", "birth_date": "2999-01-01"}, "in the past"),
])
def test_bad_profiles_are_config_errors_that_name_the_field_not_the_value(profile, msg):
    with pytest.raises(ConfigError) as e:
        load_people(person_json(**profile))
    assert msg in str(e.value)
    for v in profile.values():
        if isinstance(v, str) and v not in ("male", "female", "m"):
            assert v not in str(e.value)


def test_age_at_measurement_from_birth_date():
    assert age_on(date(1971, 6, 15), date(2026, 6, 14)) == 54
    assert age_on(date(1971, 6, 15), date(2026, 6, 15)) == 55
    pr = Profile(height_cm=HEIGHT, sex="male", birth_date=date(1971, 6, 15))
    got = profile_inputs(pr, datetime(2026, 7, 1, 12, tzinfo=timezone.utc))
    assert got == {"height_cm": 181, "sex": "male", "age": 55, "age_from": "birth_date"}   # truncated, not rounded
    assert profile_inputs(Profile(height_cm=170.99, sex="female", age=50), NOW)["height_cm"] == 170


# --- settings ----------------------------------------------------------------------------------

def test_engine_settings_defaults_and_validation():
    s = engine_from_env({})
    assert (s.switch, s.variant_name, s.limb_order, s.df_x0, s.timeout, s.command) == (
        "on", "PPDeviceCalcuteTypeAlternate8_4", "10432", "20k", 60.0, ("/usr/local/bin/lefu-engine",))
    assert s.secret == ""
    s = engine_from_env({"LEFU_SECRET_8": SECRET, "SCALE_BRIDGE_ENGINE": "OFF"})
    assert s.switch == "off" and SECRET not in repr(s) and SECRET not in repr(from_env({"LEFU_SECRET_8": SECRET}))
    for env in ({"SCALE_BRIDGE_ENGINE": "maybe"}, {"LEFU_LIMB_ORDER": "10433"}, {"LEFU_DF_X0": "50k"},
                {"LEFU_VARIANT": "8;4"}, {"SCALE_BRIDGE_ENGINE_TIMEOUT_S": "0"}):
        with pytest.raises(ValueError):
            engine_from_env(env)


def test_startup_self_disables_without_secret_with_one_line(log_lines):
    cmd = (sys.executable, "-I", "-c", "pass")
    e = started(settings(cmd=cmd, secret=""))
    assert not e.enabled and e.reason == "no_secret"
    lines = [x for x in log_lines() if x["event"].startswith("engine")]
    assert len(lines) == 1 and lines[0]["event"] == "engine_config" and lines[0]["enabled"] is False
    assert lines[0]["level"] == "warning" and "LEFU_SECRET_8" in lines[0]["hint"]
    assert not started(settings(secret=SECRET)).enabled                 # binary missing
    assert started(settings(cmd=cmd, secret=SECRET, switch="off")).reason == "switched_off"
    on = started(settings(cmd=cmd))
    assert on.enabled and on.reason == "on"
    assert all(SECRET not in json.dumps(x) for x in log_lines())


# --- DF decode and the case ------------------------------------------------------------------

def test_decode_df_little_endian_by_index():
    assert decode_df(DF) == (tuple(X0), tuple(X1))
    assert decode_df(tuple(reversed(DF))) == (tuple(X0), tuple(X1))           # order of arrival irrelevant
    assert decode_df(DF[:3]) is None and decode_df(()) is None
    assert decode_df(DF[:3] + ("zz",)) is None


def test_decode_df_on_the_synthetic_fixture_has_four_frames():
    df = tuple(b.hex() for _, b in fixture_frames() if b[0] == 0xDF)
    x0, x1 = decode_df(df)
    assert len(x0) == len(x1) == 5


def test_case_applies_limb_order_and_frequency_mapping():
    pr = Profile(height_cm=HEIGHT, sex="male", birth_date=date(1971, 6, 15))
    c = build_case(reading(), pr, settings())
    assert c["type"] == "PPDeviceCalcuteTypeAlternate8_4" and c["weightKg"] == 70.0
    assert c["z20"] == [X0[i] for i in (1, 0, 4, 3, 2)] and c["z100"] == [X1[i] for i in (1, 0, 4, 3, 2)]  # 10432
    assert c["heightCm"] == 181 and c["age"] == 54 and c["sex"] == "male"
    c = build_case(reading(), pr, settings(limb_order="01234", df_x0="100k"))
    assert c["z20"] == X1 and c["z100"] == X0
    assert build_case(reading(df=DF[:2]), pr, settings()) is None


# --- the child process -----------------------------------------------------------------------

def run_engine(e, case=None):
    return asyncio.run(e.run(case or {"id": "scale-bridge", "weightKg": 70.0}))


def test_success_secret_only_in_child_env_input_on_stdin(tmp_path, monkeypatch):
    monkeypatch.setenv("WGER_API_KEY_ALICE", "a" * 40)
    monkeypatch.setenv("LEFU_ASSETS", "/opt/lefu/assets")
    cmd, seen = fake_engine(tmp_path)
    e = started(settings(cmd=cmd))
    r = run_engine(e, {"id": "scale-bridge", "z20": [12345678]})
    assert r.result == "ok" and r.error_type == "PP_ERROR_TYPE_NONE" and r.fields == 4 and r.exit_code == 0
    assert r.output["out"]["getPpFat"] == FAT and r.has_result
    got = json.loads(seen.read_text())
    assert got["argv"] == ["--case", "-"]                                  # no values on the command line
    assert json.loads(got["stdin"])["z20"] == [12345678]
    assert got["env"]["LEFU_SECRET_8"] == SECRET and got["env"]["LEFU_ASSETS"] == "/opt/lefu/assets"
    assert "WGER_API_KEY_ALICE" not in got["env"]                          # allowlist only
    assert "LEFU_SECRET_8" not in os.environ


def test_error_type_is_engine_error(tmp_path):
    out = {**OK_OUTPUT, "errorType": "PP_ERROR_TYPE_SECRET", "out": {"getPpFat": 0.0}}
    cmd, _ = fake_engine(tmp_path, payload=out)
    r = run_engine(started(settings(cmd=cmd)))
    assert (r.result, r.error_type, r.fields, r.has_result) == ("engine_error", "PP_ERROR_TYPE_SECRET", 1, True)


def test_timeout_kills_the_child(tmp_path):
    cmd, _ = fake_engine(tmp_path, "sleep")
    t0 = time.monotonic()
    r = run_engine(started(settings(cmd=cmd, timeout=1.0)))
    assert r.result == "timeout" and r.output is None and not r.has_result
    assert time.monotonic() - t0 < 10


@pytest.mark.parametrize("mode,result", [("garbage", "malformed_output"), ("exit1", "nonzero_exit"),
                                         ("exception", "calc_exception"), ("echo_secret", "secret_in_output")])
def test_failures_are_reason_codes_without_output(tmp_path, mode, result):
    cmd, _ = fake_engine(tmp_path, mode)
    r = run_engine(started(settings(cmd=cmd)))
    assert r.result == result and r.output is None and not r.has_result


@pytest.mark.parametrize("payload", ["[]", '{"errorType": 3, "out": {}}', '{"errorType": "PP_ERROR_TYPE_NONE"}'])
def test_wrong_shape_is_malformed(tmp_path, payload):
    script = tmp_path / "shape.py"
    script.write_text(f"import sys; sys.stdin.read(); print({payload!r})")
    cmd = (sys.executable, "-I", str(script))
    assert run_engine(started(settings(cmd=cmd))).result == "malformed_output"


def test_spawn_failure(tmp_path):
    e = Engine(settings(cmd=(str(tmp_path / "missing"),)))
    e.enabled = True
    assert run_engine(e).result == "spawn_failed"


# --- through the bridge ----------------------------------------------------------------------

def bridge(tmp_path, people, mode="ok", secret=SECRET, **env):
    cmd, seen = fake_engine(tmp_path, mode)
    s = from_env({"SCALE_BRIDGE_PEOPLE": people, "LEFU_SECRET_8": secret,
                  "SCALE_BRIDGE_ENGINE_CMD": shlex.join(cmd), "SCALE_BRIDGE_ENGINE_TIMEOUT_S": "1", **env})
    b = Bridge(s, httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    b.engine.startup([x.name for x in s.people if x.profile])
    return b, seen


def test_person_without_profile_or_without_df_or_no_match_is_not_run(tmp_path, log_lines):
    b, seen = bridge(tmp_path, person_json())
    assert asyncio.run(b.compute(reading(), Decision("dry_run", "alice"), "s-1")) is None
    b, seen = bridge(tmp_path, person_json(height_cm=HEIGHT, sex="male", age=40))
    assert asyncio.run(b.compute(reading(df=()), Decision("dry_run", "alice"), "s-2")) is None
    assert asyncio.run(b.compute(reading(), Decision("no_match"), "s-3")) is None
    assert asyncio.run(b.compute(reading(), None, "s-4")) is None
    assert not seen.exists()                                               # never started
    skipped = [x for x in log_lines() if x["event"] == "engine_skipped"]
    assert [x["reason"] for x in skipped] == ["no_profile", "df_incomplete"]


def test_engine_off_or_no_secret_never_runs(tmp_path):
    for kw in ({"secret": ""}, {"SCALE_BRIDGE_ENGINE": "off"}):
        b, seen = bridge(tmp_path, person_json(height_cm=HEIGHT, sex="male", age=40), **kw)
        assert asyncio.run(b.compute(reading(), Decision("dry_run", "alice"), "s-1")) is None
        assert not seen.exists()


def test_compute_runs_once_and_logs_one_line_without_values(tmp_path, log_lines):
    b, seen = bridge(tmp_path, person_json(height_cm=HEIGHT, sex="male", birth_date=BIRTH))
    run = asyncio.run(b.compute(reading(), Decision("dry_run", "alice"), "s-1"))
    assert run.result == "ok" and run.document["provenance"]["profile"]["age_from"] == "birth_date"
    assert run.document["provenance"]["limb_order"] == "10432" and run.document["output"] == OK_OUTPUT
    lines = [x for x in log_lines() if x["event"] == "engine"]
    assert len(lines) == 1
    ln = lines[0]
    assert (ln["result"], ln["error_type"], ln["fields"], ln["init_ms"], ln["calc_ms"]) == (
        "ok", "PP_ERROR_TYPE_NONE", 4, 1234, 88)
    assert_no_values(log_lines())


def assert_no_values(lines):
    text = "\n".join(json.dumps(x) for x in lines)
    fixture_df = decode_df(tuple(b.hex() for _, b in fixture_frames() if b[0] == 0xDF))
    for v in (SECRET, str(FAT), str(BMR), str(MUSCLE), str(HEIGHT), BIRTH, "456.7",
              *(str(v) for v in X0 + X1), *(str(v) for v in fixture_df[0] + fixture_df[1])):
        assert v not in text, v


# --- end to end: ble.run -> bridge -> capture + engine file ----------------------------------

def run_session(monkeypatch, tmp_path, b, capture_dir):
    class FakeScanner:
        def __init__(self, detection_callback):
            self.cb = detection_callback

        async def start(self):
            self.cb(SimpleNamespace(address=ADDR, name="Body Pod 2"),
                    SimpleNamespace(local_name="Body Pod 2", service_uuids=[p.SVC_UUID], rssi=-70))

        async def stop(self):
            pass

    async def fake_establish(cls, device, name, disconnected_callback=None, max_attempts=4, **kw):
        return FakeClient(fixture_frames())

    monkeypatch.setattr(bleak, "BleakScanner", FakeScanner)
    monkeypatch.setattr(bleak_retry_connector, "establish_connection", fake_establish)
    s = type(b.s)(**{**b.s.__dict__, "heartbeat_path": str(tmp_path / "alive"), "capture_dir": capture_dir,
                     "timeouts": Timeouts(session=5, impedance_wait=0.3, df_grace=0.2, end_marker_wait=0.5)})
    b.s = s
    done = asyncio.Event()
    real_write = ble.write_capture

    def write_and_stop(*a, **kw):
        real_write(*a, **kw)
        done.set()

    monkeypatch.setattr(ble, "write_capture", write_and_stop)

    async def go():
        task = asyncio.create_task(ble.run(s, b.handle, Captures(capture_dir, 60), on_engine=b.compute))
        await asyncio.wait_for(done.wait(), 15)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(go())


def test_weigh_in_writes_engine_json_next_to_capture(monkeypatch, tmp_path, log_lines):
    cap = tmp_path / "cap"
    cap.mkdir()
    b, _ = bridge(tmp_path, person_json(height_cm=HEIGHT, sex="male", birth_date=BIRTH))
    run_session(monkeypatch, tmp_path, b, str(cap))
    files = sorted(x.name for x in cap.iterdir())
    assert len(files) == 2
    jsonl = next(cap / f for f in files if f.endswith(".jsonl"))
    eng = cap / jsonl.name.replace(".jsonl", ".engine.json")
    assert eng.exists() and stat.S_IMODE(os.stat(eng).st_mode) == 0o640
    foot = [json.loads(x) for x in jsonl.read_text().splitlines()][-1]
    assert foot["engine"] == "ok" and foot["engine_result"] is True and foot["decision"] == "dry_run"
    doc = json.loads(eng.read_text())
    assert doc["capture"] == jsonl.name and doc["output"] == OK_OUTPUT and doc["result"] == "ok"
    pv = doc["provenance"]
    assert (pv["variant"], pv["limb_order"], pv["df_x0"], pv["df_endian"]) == (
        "PPDeviceCalcuteTypeAlternate8_4", "10432", "20k", "little")
    assert pv["profile"]["height_cm"] == 181 and pv["profile"]["sex"] == "male" and pv["weight_kg"] == 70.0
    assert doc["timings"]["wall_s"] > 0 and doc["timings"]["calc_ms"] == 88
    assert SECRET not in eng.read_text() and SECRET not in jsonl.read_text()
    written = [x for x in log_lines() if x["event"] == "capture_written"]
    assert written[0]["engine_file"] == eng.name
    assert_no_values(log_lines())


def test_engine_failure_leaves_the_weigh_in_and_capture_alone(monkeypatch, tmp_path, log_lines):
    cap = tmp_path / "cap"
    cap.mkdir()
    b, _ = bridge(tmp_path, person_json(height_cm=HEIGHT, sex="male", age=40), mode="sleep")
    run_session(monkeypatch, tmp_path, b, str(cap))
    files = list(cap.iterdir())
    assert len(files) == 1 and files[0].suffix == ".jsonl"
    foot = [json.loads(x) for x in files[0].read_text().splitlines()][-1]
    assert foot["engine"] == "timeout" and foot["engine_result"] is False and foot["decision"] == "dry_run"
    errs = [x for x in log_lines() if x["level"] == "error"]
    assert [(x["event"], x["result"]) for x in errs] == [("engine", "timeout")]
    assert [x for x in log_lines() if x["event"] == "would_write"]            # the weigh-in went on


def test_engine_files_are_pruned_like_captures(tmp_path):
    old = tmp_path / file_name(NOW - timedelta(days=61), "1-1", ".engine.json")
    new = tmp_path / file_name(NOW - timedelta(days=59), "1-2", ".engine.json")
    for f in (old, new):
        f.write_text("{}\n")
    assert Captures(str(tmp_path), 60, clock=lambda: NOW).prune() == 1
    assert not old.exists() and new.exists()
