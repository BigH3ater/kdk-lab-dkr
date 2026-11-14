"""Full-session replay of the synthetic capture through run_session with a fake client."""
import asyncio
from datetime import datetime, timezone

from conftest import FakeClient
from scale_bridge import protocol as p
from scale_bridge.session import IMPEDANCE_CATEGORIES, Collector, SessionLog, Timeouts, run_session

PROFILE = p.profile_frame(1, 1, 170, 30)
FAST = Timeouts(session=5.0, impedance_wait=0.3, df_grace=0.2, end_marker_wait=0.5)
SYNTH_HR = 0x4B                     # the fixture's made-up heart-rate byte


def is_marker(b):
    return b[0] == p.CF and b[2] & p.STABLE_BIT and b[3:5] == b"\0\0" and b[9] == 0


def is_lock(b):
    return b[0] == p.CF and b[2] & p.STABLE_BIT and b[3:5] != b"\0\0"


def with_marker_byte(frames, hr):
    out = []
    for t, b in frames:
        if is_marker(b):
            body = b[:1] + bytes([hr]) + b[2:-1]
            b = body + bytes([p.xor(body)])
        out.append((t, b))
    return out
NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def replay(frames, **kw):
    async def go():
        gone = asyncio.Event()
        client = FakeClient(frames, gone=gone, disconnect_at=kw.pop("disconnect_at", None))
        res = await run_session(client, PROFILE, gone, kw.pop("timeouts", FAST), clock=lambda: NOW,
                                record=kw.pop("record", None))
        await client.disconnect()
        return res, client
    return asyncio.run(go())


def test_full_session_gives_one_complete_reading(frames):
    res, client = replay(frames)
    assert res.ended_by == "end_marker"
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
    assert r.heart_rate_bpm == SYNTH_HR and s["end_marker"] and s["heart_rate"]
    assert str(SYNTH_HR) not in str(s)          # the summary carries flags, never the value


def test_zero_weight_stable_frame_after_lock_is_ignored(frames):
    """Feed the WHOLE capture (incl. the end marker and the second step-on) into the collector."""
    col = Collector(clock=lambda: NOW)
    for t, data in frames:
        col.feed(data, t)
    assert col.settled.weight_cg == 7000 and col.settled.state == 0x05
    assert col.end_marker is not None and col.zero_stable_frames == 0
    assert col.ignored_after_lock >= 25          # the live frames after the marker
    assert col.reading().weight_cg == 7000 and col.reading().heart_rate_bpm == SYNTH_HR


def test_zero_weight_stable_frame_alone_is_not_a_reading(frames):
    no_lock = [(t, b) for t, b in frames if not is_lock(b)]
    col = Collector(clock=lambda: NOW)
    for t, data in no_lock:
        col.feed(data, t)
    assert col.settled is None and col.early_end_markers == 1 and col.end_marker is None
    assert col.reading() is None


def test_marker_before_any_lock_does_not_end_the_session_or_make_a_reading(frames):
    """Step-off with no lock: the marker arrives, the session keeps listening, no reading."""
    no_lock = [(t, b) for t, b in frames if not is_lock(b)]
    res, _ = replay(no_lock, disconnect_at=no_lock[-1][0])
    assert res.ended_by == "disconnect" and res.reading is None
    assert res.summary["early_end_markers"] == 1 and not res.summary["end_marker"]


def test_session_ends_on_the_marker_not_on_the_df_frames(frames):
    """The marker is ~4.7 s (capture time) after the DF frames; the session waits for it."""
    res, _ = replay(frames, timeouts=Timeouts(session=5.0, impedance_wait=0.3, df_grace=0.2,
                                              end_marker_wait=3.0))
    assert res.ended_by == "end_marker" and res.reading.heart_rate_bpm == SYNTH_HR


def lock_and_after(frames, seconds=1.0):
    """Up to `seconds` (capture time) after the lock: live frames, lock, BE, DF; no marker."""
    lock_t = next(t for t, b in frames if is_lock(b))
    return [(t, b) for t, b in frames if t <= lock_t + seconds and not is_marker(b)]


def test_no_marker_times_out_with_a_complete_reading(frames):
    head = lock_and_after(frames)

    async def go():
        loop = asyncio.get_running_loop()
        gone = asyncio.Event()
        client = FakeClient(head, speed=1000.0, gone=gone)
        t0 = loop.time()
        res = await run_session(client, PROFILE, gone, Timeouts(session=5.0, impedance_wait=0.3,
                                df_grace=0.2, end_marker_wait=0.4), clock=lambda: NOW)
        await client.disconnect()
        return res, loop.time() - t0
    res, took = asyncio.run(go())
    assert res.ended_by == "end_marker_timeout" and took >= 0.4
    r = res.reading
    assert r.weight_cg == 7000 and r.has_impedance and len(r.df_raw) == 4 and r.heart_rate_bpm is None
    assert not res.summary["end_marker"] and not res.summary["heart_rate"]


def test_marker_with_heart_rate_0_is_no_heart_rate(frames):
    res, _ = replay(with_marker_byte(frames, 0))
    assert res.ended_by == "end_marker" and res.reading.heart_rate_bpm is None
    assert res.summary["end_marker"] and not res.summary["heart_rate"]


def test_disconnect_while_waiting_for_the_marker_keeps_the_full_reading(frames):
    marker_t = next(t for t, b in frames if is_marker(b))
    res, _ = replay(frames, disconnect_at=marker_t - 0.5,
                    timeouts=Timeouts(session=5.0, impedance_wait=0.3, df_grace=0.2, end_marker_wait=5.0))
    assert res.ended_by == "disconnect" and res.reading.has_impedance and res.reading.heart_rate_bpm is None


def test_hard_watchdog_still_caps_the_marker_wait(frames):
    res, _ = replay(lock_and_after(frames), timeouts=Timeouts(session=0.3, impedance_wait=0.3, df_grace=0.2,
                                                              end_marker_wait=60.0))
    assert res.ended_by == "end_marker_timeout" and res.reading.has_impedance


def test_record_holds_every_notification_byte_exact_in_order(frames):
    bad = list(frames)
    t, b = bad[10]
    bad[10] = (t, b[:-1] + bytes([b[-1] ^ 0xFF]))
    rec = SessionLog()
    res, _ = replay(bad, record=rec)
    marker_i = next(i for i, (_, b) in enumerate(bad) if is_marker(b))
    assert [x[2] for x in rec.notifications] == [b for _, b in bad[:marker_i + 1]]
    assert {x[1] for x in rec.notifications} == {"fff4"}
    assert rec.profile_hex == PROFILE.hex() and rec.profile_written is True and rec.started_at == NOW
    assert res.summary["bad_frames"] == 1


def test_weight_without_impedance_is_still_a_reading(frames):
    weight_only = [(t, b) for t, b in frames if b[0] == p.CF and not is_marker(b)]   # no handle: no marker
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
