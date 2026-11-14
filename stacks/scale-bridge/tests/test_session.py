"""Full-session replay of the synthetic capture through run_session with a fake client."""
import asyncio
from datetime import datetime, timezone

from conftest import FakeClient
from scale_bridge import protocol as p
from scale_bridge.session import IMPEDANCE_CATEGORIES, Collector, Timeouts, run_session

PROFILE = p.profile_frame(1, 1, 170, 30)
FAST = Timeouts(session=5.0, impedance_wait=0.3, df_grace=0.2)
NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def replay(frames, **kw):
    async def go():
        gone = asyncio.Event()
        client = FakeClient(frames, gone=gone, disconnect_at=kw.pop("disconnect_at", None))
        res = await run_session(client, PROFILE, gone, kw.pop("timeouts", FAST), clock=lambda: NOW)
        await client.disconnect()
        return res, client
    return asyncio.run(go())


def test_full_session_gives_one_complete_reading(frames):
    res, client = replay(frames)
    assert res.ended_by == "complete"
    r = res.reading
    assert r.weight_cg == 7000 and r.weight_kg == 70.0 and r.measured_at == NOW
    assert r.z50_deci == (3000, 3050, 250, 2900, 2850)
    assert r.z250_deci == (2700, 2750, 220, 2580, 2540)
    assert len(r.df_raw) == 4 and all(len(h) == 34 for h in r.df_raw)
    assert [n for n, _ in r.impedances()] == list(IMPEDANCE_CATEGORIES)
    # subscribe FFF4, write the profile with response, then FFF2
    assert client.subscribed == [p.NOTIFY_UUID, p.NOTIFY_ALT_UUID]
    assert client.writes == [(p.WRITE_UUID, PROFILE, True)]
    s = res.summary
    assert s["settled"] and s["settled_state"] == "05" and s["bad_frames"] == 0
    assert s["impedance_frames"] == 2 and s["extended_frames"] == 4


def test_zero_weight_stable_frame_after_lock_is_ignored(frames):
    """Feed the WHOLE capture (incl. the step-off frame and later live frames) into the collector."""
    col = Collector(clock=lambda: NOW)
    for t, data in frames:
        col.feed(data, t)
    assert col.settled.weight_cg == 7000
    assert col.ignored_after_lock >= 1 + 25      # the trap frame + the live frames after it
    assert col.reading().weight_cg == 7000


def test_zero_weight_stable_frame_alone_is_not_a_reading(frames):
    no_lock = [(t, b) for t, b in frames if not (b[0] == p.CF and b[2] & p.STABLE_BIT and b[3:5] != b"\0\0")]
    col = Collector(clock=lambda: NOW)
    for t, data in no_lock:
        col.feed(data, t)
    assert col.settled is None and col.zero_stable_frames == 1 and col.reading() is None


def test_weight_without_impedance_is_still_a_reading(frames):
    weight_only = [(t, b) for t, b in frames if b[0] == p.CF]
    res, _ = replay(weight_only)
    assert res.ended_by == "impedance_timeout"
    assert res.reading.weight_cg == 7000 and not res.reading.has_impedance
    assert res.reading.impedances() == []


def test_no_handle_no_lock_disconnect_gives_nothing(frames):
    live_only = [(t, b) for t, b in frames if b[0] == p.CF and not b[2] & p.STABLE_BIT]
    res, _ = replay(live_only, disconnect_at=frames[-1][0])
    assert res.ended_by == "disconnect" and res.reading is None


def test_disconnect_after_lock_keeps_the_weight(frames):
    lock_t = next(t for t, b in frames if b[0] == p.CF and b[2] & p.STABLE_BIT)
    res, _ = replay(frames, disconnect_at=lock_t + 0.01,
                    timeouts=Timeouts(session=5.0, impedance_wait=2.0, df_grace=0.2))
    assert res.ended_by == "disconnect" and res.reading.weight_cg == 7000 and not res.reading.has_impedance


def test_corrupt_frame_is_counted_and_skipped(frames):
    bad = list(frames)
    t, b = bad[10]
    bad[10] = (t, b[:-1] + bytes([b[-1] ^ 0xFF]))
    res, _ = replay(bad)
    assert res.summary["bad_frames"] == 1 and res.reading.weight_cg == 7000


def test_session_watchdog(frames):
    live_only = [(t, b) for t, b in frames if b[0] == p.CF and not b[2] & p.STABLE_BIT]
    res, _ = replay(live_only, timeouts=Timeouts(session=0.05, impedance_wait=0.3, df_grace=0.2))
    assert res.ended_by == "timeout" and res.reading is None
