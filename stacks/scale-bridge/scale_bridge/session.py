"""One weigh-in session: collect frames from a connected scale into one Reading.

The BLE layer (ble.py) connects and hands a connected client here; tests hand a
fake client that replays a capture. Only the client methods bleak's BleakClient
has are used: start_notify, write_gatt_char.

Rules (vault capture note 2026-11-14):
* the settled weight is the first CF frame with the stable bit AND a non-zero
  weight. About 5 s after the lock, as the load comes off, the scale sends a
  second stable-flagged frame with weight 0: ignored, as is every CF after the
  settled one;
* BE x2 and DF x4 follow the lock within ~0.6 s. After the lock the session
  waits up to `impedance_wait` for both BE frames, then up to `df_grace` for the
  DF frames, then ends. A settled weight without impedance is still a reading;
* no settled weight before disconnect / timeout = no reading.
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


@dataclass(frozen=True)
class Reading:
    measured_at: datetime                         # UTC, when the settled frame arrived
    weight_cg: int
    z50_deci: tuple[int, ...] | None              # five values, protocol.SEGMENTS order
    z250_deci: tuple[int, ...] | None
    df_raw: tuple[str, ...] = ()                  # hex, undecoded

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
    zero_stable_frames: int = 0
    ignored_after_lock: int = 0
    settled: p.WeightFrame | None = None
    settled_at: datetime | None = None
    settled_mono: float | None = None
    impedance: dict[int, p.ImpedanceFrame] = field(default_factory=dict)
    impedance_mono: float | None = None
    extended: dict[tuple[int, int], p.ExtendedFrame] = field(default_factory=dict)
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
                self.zero_stable_frames += 1     # the step-off trap
            else:
                self.live_weight_frames += 1
        elif isinstance(f, p.ImpedanceFrame):
            if f.index in p.FREQUENCIES and f.index not in self.impedance:
                self.impedance[f.index] = f
                if self.impedance_complete:
                    self.impedance_mono = now
        elif isinstance(f, p.ExtendedFrame):
            self.extended.setdefault(f.index, f)
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
        if not self.impedance_complete:
            return min(hard, self.settled_mono + t.impedance_wait)
        if not self.extended_complete:
            return min(hard, (self.impedance_mono or self.settled_mono) + t.df_grace)
        return None

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
        )

    def summary(self) -> dict[str, Any]:
        """Counts only: no weight or impedance values (they never leave the dry-run line)."""
        return {
            "frames": self.frames, "bad_frames": self.bad_frames,
            "live_weight_frames": self.live_weight_frames,
            "settled": self.settled is not None,
            "settled_state": f"{self.settled.state:02x}" if self.settled else None,
            "zero_stable_frames": self.zero_stable_frames,
            "ignored_after_lock": self.ignored_after_lock,
            "impedance_frames": len(self.impedance), "extended_frames": len(self.extended),
            "unknown_frames": self.unknown,
        }


class GattClient(Protocol):
    async def start_notify(self, char: str, callback: Callable[[Any, bytearray], None]) -> None: ...
    async def write_gatt_char(self, char: str, data: bytes, response: bool = ...) -> None: ...


@dataclass
class SessionResult:
    reading: Reading | None
    summary: dict[str, Any]
    ended_by: str               # complete | disconnect | timeout | impedance_timeout | df_timeout


async def run_session(client: GattClient, profile: bytes, gone: asyncio.Event,
                      timeouts: Timeouts = Timeouts(),
                      clock: Callable[[], datetime] | None = None) -> SessionResult:
    """Subscribe, write the profile, collect until done. The caller disconnects."""
    loop = asyncio.get_running_loop()
    col = Collector(clock=clock) if clock else Collector()
    changed = asyncio.Event()
    started = loop.time()

    def on_notify(_char: Any, data: bytearray) -> None:
        col.feed(bytes(data), loop.time())
        changed.set()

    await client.start_notify(p.NOTIFY_UUID, on_notify)
    try:
        await client.write_gatt_char(p.WRITE_UUID, profile, response=True)
    except Exception as e:  # noqa: BLE001 - weight still arrives without it
        emit(log, logging.WARNING, "profile_write_failed", error=type(e).__name__)
    try:  # after the profile write, like the capture script: saves ~1 s before the trigger
        await client.start_notify(p.NOTIFY_ALT_UUID, on_notify)
    except Exception as e:  # noqa: BLE001
        emit(log, logging.DEBUG, "subscribe_failed", char="fff2", error=type(e).__name__)

    ended_by = "timeout"
    while True:
        if gone.is_set():
            ended_by = "disconnect"
            break
        deadline = col.deadline(started, timeouts)
        if deadline is None:
            ended_by = "complete"
            break
        remaining = deadline - loop.time()
        if remaining <= 0:
            if col.settled is None:
                ended_by = "timeout"
            elif not col.impedance_complete:
                ended_by = "impedance_timeout"
            else:
                ended_by = "df_timeout"
            break
        changed.clear()
        waiters = [asyncio.ensure_future(changed.wait()), asyncio.ensure_future(gone.wait())]
        try:
            await asyncio.wait(waiters, timeout=remaining, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for w in waiters:
                w.cancel()
    return SessionResult(reading=col.reading(), summary=col.summary(), ended_by=ended_by)
