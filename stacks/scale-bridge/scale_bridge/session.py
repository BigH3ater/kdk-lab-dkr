"""One weigh-in session: collect frames from a connected scale into one Reading.

The BLE layer (ble.py) connects and hands a connected client here; tests hand a
fake client that replays a capture. Only the client methods bleak's BleakClient
has are used: start_notify, write_gatt_char.

Rules (vault capture note 2026-11-14):
* the settled weight is the first CF frame with the stable bit AND a non-zero
  weight. Every weight CF after it is ignored;
* BE x2 and DF x4 follow the lock within ~0.6 s. After the lock the session
  waits up to `impedance_wait` for both BE frames, then up to `df_grace` for the
  DF frames. A settled weight without impedance is still a reading (and ends
  the session there: no handle, no end marker);
* with the impedance in, the session stays open up to `end_marker_wait` for the
  scale's end-of-measurement marker (protocol.EndMarkerFrame: stable, weight 0,
  state 00, ~5 s after the lock, handle held only), which carries the heart
  rate. It ends early on the marker or a disconnect; without the marker the
  reading is still complete, heart rate None (`ended_by` says which way);
* a marker before any settled frame (a step-off with no lock) is counted and
  changes nothing: no reading, the session keeps listening;
* no settled weight before disconnect / timeout = no reading.

Every notification (both characteristics, bad frames included) can be recorded
byte for byte into a caller-owned SessionLog for the capture file (capture.py).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from . import protocol as p
from .logs import emit

log = logging.getLogger("scale_bridge.session")


@dataclass(frozen=True)
class Timeouts:
    session: float = 90.0         # watchdog for the whole connection
    impedance_wait: float = 5.0   # after the lock, for both BE frames
    df_grace: float = 1.0         # after both BE, for the DF frames
    end_marker_wait: float = 20.0  # after the DF frames, for the end-of-measurement marker


@dataclass(frozen=True)
class Reading:
    measured_at: datetime                         # UTC, when the settled frame arrived
    weight_cg: int
    z50_deci: tuple[int, ...] | None              # five values, protocol.SEGMENTS order
    z250_deci: tuple[int, ...] | None
    df_raw: tuple[str, ...] = ()                  # hex, undecoded
    heart_rate_bpm: int | None = None             # from the end marker; None = no marker or 0

    @property
    def weight_kg(self) -> float:
        return self.weight_cg / 100

    @property
    def has_impedance(self) -> bool:
        return self.z50_deci is not None and self.z250_deci is not None

    def impedances(self) -> list[tuple[str, float]]:
        """[(category name, ohms)] for the ten raw values; empty if incomplete."""
        if not self.has_impedance:
            return []
        out = []
        for idx, vals in ((0, self.z50_deci), (1, self.z250_deci)):
            for seg, v in zip(p.SEGMENTS, vals, strict=True):
                out.append((impedance_category(idx, seg), v / 10))
        return out


def impedance_category(index: int, segment: str) -> str:
    return f"Impedance {p.FREQUENCIES[index]} {segment}"


IMPEDANCE_CATEGORIES = tuple(impedance_category(i, s) for i in (0, 1) for s in p.SEGMENTS)


@dataclass
class Collector:
    """Frame-by-frame state machine. `now` values are loop.time() seconds."""

    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    frames: int = 0
    bad_frames: int = 0
    live_weight_frames: int = 0
    zero_stable_frames: int = 0         # stable, weight 0, state not 00 (not the end marker)
    early_end_markers: int = 0          # end marker before any settled frame: step-off, no lock
    ignored_after_lock: int = 0
    settled: p.WeightFrame | None = None
    settled_at: datetime | None = None
    settled_mono: float | None = None
    impedance: dict[int, p.ImpedanceFrame] = field(default_factory=dict)
    impedance_mono: float | None = None
    extended: dict[tuple[int, int], p.ExtendedFrame] = field(default_factory=dict)
    extended_mono: float | None = None
    end_marker: p.EndMarkerFrame | None = None
    unknown: int = 0

    def feed(self, data: bytes, now: float) -> None:
        self.frames += 1
        try:
            f = p.parse_frame(data)
        except p.FrameError as e:
            self.bad_frames += 1
            emit(log, logging.DEBUG, "frame_rejected", reason=str(e))
            return
        if isinstance(f, p.WeightFrame):
            if self.settled is not None:
                self.ignored_after_lock += 1
            elif f.stable and f.weight_cg > 0:
                self.settled, self.settled_at, self.settled_mono = f, self.clock(), now
            elif f.stable:
                self.zero_stable_frames += 1
            else:
                self.live_weight_frames += 1
        elif isinstance(f, p.EndMarkerFrame):
            if self.settled is None:
                self.early_end_markers += 1      # step-off without a lock: not a reading
            elif self.end_marker is None:
                self.end_marker = f
        elif isinstance(f, p.ImpedanceFrame):
            if f.index in p.FREQUENCIES and f.index not in self.impedance:
                self.impedance[f.index] = f
                if self.impedance_complete:
                    self.impedance_mono = now
        elif isinstance(f, p.ExtendedFrame):
            self.extended.setdefault(f.index, f)
            if self.extended_complete and self.extended_mono is None:
                self.extended_mono = now
        else:
            self.unknown += 1

    @property
    def impedance_complete(self) -> bool:
        return all(i in self.impedance for i in p.FREQUENCIES)

    @property
    def extended_complete(self) -> bool:
        return len(self.extended) >= 4

    def deadline(self, started: float, t: Timeouts) -> float | None:
        """loop time at which the session should end; None = end now."""
        hard = started + t.session
        if self.settled_mono is None:
            return hard
        if self.end_marker is not None:
            return None
        if not self.impedance_complete:
            return min(hard, self.settled_mono + t.impedance_wait)
        if self.extended_complete and self.extended_mono is not None:
            phase = self.extended_mono
        else:   # DF frames still missing: their grace, then the marker wait
            phase = (self.impedance_mono or self.settled_mono) + t.df_grace
        return min(hard, phase + t.end_marker_wait)

    def reading(self) -> Reading | None:
        if self.settled is None or self.settled_at is None:
            return None
        z = self.impedance
        return Reading(
            measured_at=self.settled_at,
            weight_cg=self.settled.weight_cg,
            z50_deci=z[0].deci_ohms if self.impedance_complete else None,
            z250_deci=z[1].deci_ohms if self.impedance_complete else None,
            df_raw=tuple(self.extended[k].raw.hex() for k in sorted(self.extended)),
            heart_rate_bpm=self.end_marker.heart_rate_bpm if self.end_marker else None,
        )

    def summary(self) -> dict[str, Any]:
        """Counts and flags only: no weight, impedance or heart-rate values."""
        return {
            "frames": self.frames, "bad_frames": self.bad_frames,
            "live_weight_frames": self.live_weight_frames,
            "settled": self.settled is not None,
            "settled_state": f"{self.settled.state:02x}" if self.settled else None,
            "zero_stable_frames": self.zero_stable_frames,
            "end_marker": self.end_marker is not None,
            "early_end_markers": self.early_end_markers,
            "heart_rate": bool(self.end_marker and self.end_marker.heart_rate_bpm),
            "ignored_after_lock": self.ignored_after_lock,
            "impedance_frames": len(self.impedance), "extended_frames": len(self.extended),
            "unknown_frames": self.unknown,
        }


class GattClient(Protocol):
    async def start_notify(self, char: str, callback: Callable[[Any, bytearray], None]) -> None: ...
    async def write_gatt_char(self, char: str, data: bytes, response: bool = ...) -> None: ...


@dataclass
class SessionLog:
    """Caller-owned record of one connection, filled as it happens (survives a hung session).

    `notifications`: (UTC arrival time, short char uuid "fff4"/"fff2", exact bytes),
    in arrival order, bad frames included. Health data: only capture.py writes it out.
    """
    started_at: datetime | None = None
    profile_hex: str | None = None
    profile_written: bool | None = None
    notifications: list[tuple[datetime, str, bytes]] = field(default_factory=list)


@dataclass
class SessionResult:
    reading: Reading | None
    summary: dict[str, Any]
    # end_marker | end_marker_timeout | disconnect | timeout | impedance_timeout
    ended_by: str


def _ended_by(col: Collector) -> str:
    """Why the deadline expired (the phase the session was in)."""
    if col.settled is None:
        return "timeout"
    if not col.impedance_complete:
        return "impedance_timeout"
    return "end_marker_timeout"


async def run_session(client: GattClient, profile: bytes, gone: asyncio.Event,
                      timeouts: Timeouts = Timeouts(),
                      clock: Callable[[], datetime] | None = None,
                      record: SessionLog | None = None) -> SessionResult:
    """Subscribe, write the profile, collect until done. The caller disconnects."""
    loop = asyncio.get_running_loop()
    now_utc = clock or (lambda: datetime.now(timezone.utc))
    col = Collector(clock=now_utc)
    changed = asyncio.Event()
    started = loop.time()
    rec = record if record is not None else SessionLog()
    rec.started_at = rec.started_at or now_utc()
    rec.profile_hex = profile.hex()

    def handler(short: str) -> Callable[[Any, bytearray], None]:
        def on_notify(_char: Any, data: bytearray) -> None:
            b = bytes(data)
            rec.notifications.append((now_utc(), short, b))
            col.feed(b, loop.time())
            changed.set()
        return on_notify

    await client.start_notify(p.NOTIFY_UUID, handler("fff4"))
    try:
        await client.write_gatt_char(p.WRITE_UUID, profile, response=True)
        rec.profile_written = True
    except Exception as e:  # noqa: BLE001 - weight still arrives without it
        rec.profile_written = False
        emit(log, logging.WARNING, "profile_write_failed", error=type(e).__name__)
    try:  # after the profile write, like the capture script: saves ~1 s before the trigger
        await client.start_notify(p.NOTIFY_ALT_UUID, handler("fff2"))
    except Exception as e:  # noqa: BLE001
        emit(log, logging.DEBUG, "subscribe_failed", char="fff2", error=type(e).__name__)

    while True:
        deadline = col.deadline(started, timeouts)
        if deadline is None:            # the end marker came (checked first: it may race the disconnect)
            ended_by = "end_marker"
            break
        if gone.is_set():
            ended_by = "disconnect"
            break
        remaining = deadline - loop.time()
        if remaining <= 0:
            ended_by = _ended_by(col)
            break
        changed.clear()
        waiters = [asyncio.ensure_future(changed.wait()), asyncio.ensure_future(gone.wait())]
        try:
            await asyncio.wait(waiters, timeout=remaining, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for w in waiters:
                w.cancel()
    return SessionResult(reading=col.reading(), summary=col.summary(), ended_by=ended_by)
