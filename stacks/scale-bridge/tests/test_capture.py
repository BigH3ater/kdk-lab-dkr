"""Raw capture file per session: byte-exact frames, emulator-compatible records, failure modes, pruning."""
import asyncio
import io
import json
import logging
import os
import stat
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import bleak
import bleak_retry_connector
import pytest

from conftest import FakeClient
from scale_bridge import __version__, ble, logs
from scale_bridge import protocol as p
from scale_bridge.app import Decision
from scale_bridge.capture import Captures, file_name
from scale_bridge.config import from_env
from scale_bridge.session import SessionLog, Timeouts

NOW = datetime(2026, 1, 2, 3, 4, 5, 678000, tzinfo=timezone.utc)
ADDR = "AA:BB:CC:DD:EE:01"


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


def session_log(frames, bad_at=None):
    rec = SessionLog(started_at=NOW, profile_hex=p.profile_frame(1, 1, 170, 30).hex(), profile_written=True)
    for i, (t, b) in enumerate(frames):
        if i == bad_at:
            b = b[:-1] + bytes([b[-1] ^ 0xFF])
        rec.notifications.append((NOW + timedelta(seconds=t), "fff2" if i == 3 else "fff4", b))
    return rec


def read_jsonl(path):
    return [json.loads(x) for x in path.read_text().splitlines()]


def test_file_content_is_the_notifications_byte_exact_in_order(tmp_path, frames, log_lines):
    rec = session_log(frames, bad_at=10)
    cap = Captures(str(tmp_path), 60, clock=lambda: NOW)
    path = cap.write(rec, "1767323045-1", {"scale": "..:EE:01", "rssi": -70},
                     {"ended_by": "end_marker", "attribution": "matched", "person": "alice", "heart_rate": True})
    assert path.name == "20260102T030405Z-1767323045-1.jsonl" == file_name(NOW, "1767323045-1")
    rows = read_jsonl(path)
    head, body, foot = rows[0], rows[1:-1], rows[-1]
    assert head["kind"] == "header" and head["schema"] == 1 and head["session"] == "1767323045-1"
    assert head["scale"] == "..:EE:01" and head["rssi"] == -70 and head["app_version"] == __version__
    assert head["profile_hex"] == rec.profile_hex and head["profile_written"] is True
    assert head["started"] == "2026-01-02T03:04:05.678+00:00"
    # byte-exact, same order, the bad-checksum frame included and flagged
    assert [r["kind"] for r in body] == ["frame"] * len(rec.notifications)
    assert [bytes.fromhex(r["hex"]) for r in body] == [b for _, _, b in rec.notifications]
    assert [r["char"] for r in body] == [c for _, c, _ in rec.notifications]
    assert [r["valid"] for r in body].count(False) == 1 and body[10]["valid"] is False
    assert "checksum" in body[10]["error"]
    assert [datetime.fromisoformat(r["t"]) for r in body] == [t for t, _, _ in rec.notifications]
    assert all(len(r["t"].split(".")[1]) == len("678+00:00") for r in body)      # milliseconds
    assert foot["kind"] == "footer" and foot["frames"] == len(rec.notifications)
    assert foot["person"] == "alice" and foot["heart_rate"] is True and foot["end_marker_byte2"] == "c0"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o640
    written = [x for x in log_lines() if x["event"] == "capture_written"]
    assert written == [{**written[0], "file": path.name, "frames": len(rec.notifications)}]
    assert set(written[0]) == {"ts", "level", "logger", "event", "file", "frames"}   # name + count only


def test_frame_records_load_like_the_emulator_and_test_fixture_do(tmp_path, frames):
    """Same reader as tests/conftest.fixture_frames: kind == frame, t, hex."""
    path = Captures(str(tmp_path), 60).write(session_log(frames), "s-1", {}, {})
    rows = [r for r in read_jsonl(path) if r["kind"] == "frame"]
    t0 = datetime.fromisoformat(rows[0]["t"])
    replayed = [((datetime.fromisoformat(r["t"]) - t0).total_seconds(), bytes.fromhex(r["hex"])) for r in rows]
    assert [b for _, b in replayed] == [b for _, b in frames]
    assert [round(t, 3) for t, _ in replayed] == [round(t, 3) for t, _ in frames]


def test_no_notifications_no_file(tmp_path):
    assert Captures(str(tmp_path), 60).write(SessionLog(started_at=NOW), "s-1", {}, {}) is None
    assert list(tmp_path.iterdir()) == []


def test_capture_off_when_dir_empty(tmp_path, frames, log_lines):
    cap = Captures("", 60)
    cap.startup()
    assert not cap.enabled and cap.write(session_log(frames), "s-1", {}, {}) is None
    assert not [x for x in log_lines() if x["level"] == "error"]


def test_missing_dir_logs_one_error_and_carries_on(tmp_path, frames, log_lines):
    cap = Captures(str(tmp_path / "nope"), 60)
    cap.startup()
    assert cap.write(session_log(frames), "s-1", {}, {}) is None
    assert cap.write(session_log(frames), "s-2", {}, {}) is None
    errors = [x for x in log_lines() if x["level"] == "error"]
    assert len(errors) == 1 and errors[0]["event"] == "capture_unavailable" and errors[0]["problem"] == "missing"


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_unwritable_dir_logs_one_error_then_recovers(tmp_path, frames, log_lines):
    d = tmp_path / "ro"
    d.mkdir(mode=0o500)
    cap = Captures(str(d), 60)
    assert cap.write(session_log(frames), "s-1", {}, {}) is None
    assert cap.write(session_log(frames), "s-2", {}, {}) is None
    errors = [x for x in log_lines() if x["level"] == "error"]
    assert len(errors) == 1 and errors[0]["problem"] == "not_writable"
    d.chmod(0o750)
    assert cap.write(session_log(frames), "s-3", {}, {}) is not None


def test_prune_by_name_stamp_and_keep_forever(tmp_path):
    old = tmp_path / file_name(NOW - timedelta(days=61), "1-1")
    new = tmp_path / file_name(NOW - timedelta(days=59), "1-2")
    other = tmp_path / "notes.txt"
    for f in (old, new, other):
        f.write_text("{}\n")
    assert Captures(str(tmp_path), 0, clock=lambda: NOW).prune() == 0          # 0 = keep forever
    assert old.exists()
    cap = Captures(str(tmp_path), 60, clock=lambda: NOW)
    assert cap.maybe_prune(mono=1000.0) == 1
    assert not old.exists() and new.exists() and other.exists()
    old.write_text("{}\n")
    assert cap.maybe_prune(mono=1000.0 + 3600) == 0                  # once a day only
    assert cap.maybe_prune(mono=1000.0 + 24 * 3600) == 1


# --- through ble.run: a missing capture dir never stops a session ------------------------------

def run_one_session(monkeypatch, frames, capture_dir, heartbeat):
    """ble.run with bleak faked: one advert, one session, then stop. Returns the decisions seen."""
    class FakeScanner:
        def __init__(self, detection_callback):
            self.cb = detection_callback

        async def start(self):
            self.cb(SimpleNamespace(address=ADDR, name="Body Pod 2"),
                    SimpleNamespace(local_name="Body Pod 2", service_uuids=[p.SVC_UUID], rssi=-70))

        async def stop(self):
            pass

    async def fake_establish(cls, device, name, disconnected_callback=None, max_attempts=4, **kw):
        return FakeClient(frames)

    monkeypatch.setattr(bleak, "BleakScanner", FakeScanner)
    monkeypatch.setattr(bleak_retry_connector, "establish_connection", fake_establish)
    s = from_env({"SCALE_BRIDGE_CAPTURE_DIR": capture_dir})
    s = type(s)(**{**s.__dict__, "heartbeat_path": str(heartbeat),
                   "timeouts": Timeouts(session=5, impedance_wait=0.3, df_grace=0.2, end_marker_wait=0.5)})
    seen, done = [], asyncio.Event()
    real_write = ble.write_capture

    def write_and_stop(*a, **kw):
        real_write(*a, **kw)
        done.set()

    monkeypatch.setattr(ble, "write_capture", write_and_stop)

    async def on_reading(reading, scale_id, session_id):
        seen.append(reading)
        return Decision("dry_run", "alice")

    async def go():
        task = asyncio.create_task(ble.run(s, on_reading))
        await asyncio.wait_for(done.wait(), 10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(go())
    return seen


def test_session_completes_and_capture_is_written(monkeypatch, frames, tmp_path, log_lines):
    (tmp_path / "cap").mkdir()
    seen = run_one_session(monkeypatch, frames, str(tmp_path / "cap"), tmp_path / "alive")
    assert len(seen) == 1 and seen[0].weight_cg == 7000
    files = list((tmp_path / "cap").iterdir())
    assert len(files) == 1
    foot = read_jsonl(files[0])[-1]
    assert foot["ended_by"] == "end_marker" and foot["attribution"] == "matched" and foot["person"] == "alice"
    assert foot["decision"] == "dry_run" and foot["heart_rate"] is True and foot["result"] == "reading"
    assert read_jsonl(files[0])[0]["scale"] == "..:EE:01"
    # nothing in the logs carries the frames
    assert not any("hex" in x for x in log_lines())


def test_missing_capture_dir_session_still_completes(monkeypatch, frames, tmp_path, log_lines):
    seen = run_one_session(monkeypatch, frames, str(tmp_path / "missing"), tmp_path / "alive")
    assert len(seen) == 1 and seen[0].weight_cg == 7000
    lines = log_lines()
    errors = [x for x in lines if x["level"] == "error"]
    assert [x["event"] for x in errors] == ["capture_unavailable"]               # once (startup), not per session
    assert [x for x in lines if x["event"] == "session"][0]["result"] == "reading"


def test_new_settings_defaults_and_env():
    d = from_env({})
    assert (d.capture_dir, d.capture_keep_days, d.write_heart_rate, d.timeouts.end_marker_wait) == ("", 60, False, 20.0)
    s = from_env({"SCALE_BRIDGE_CAPTURE_DIR": "/captures", "SCALE_BRIDGE_CAPTURE_KEEP_DAYS": "0",
                  "SCALE_BRIDGE_WRITE_HEART_RATE": "true", "SCALE_END_MARKER_WAIT_S": "12.5"})
    assert (s.capture_dir, s.capture_keep_days, s.write_heart_rate, s.timeouts.end_marker_wait) == (
        "/captures", 0, True, 12.5)
