"""Raw capture file per weigh-in: every notification, byte for byte, as JSONL.

Purpose: paired tests. A weigh-in captured here is later replayed to the vendor
phone app from an emulator, so the frames are kept exactly as they arrived.
The frame records use the same shape as the capture script / emulator
(vault `scale-sniff/sniff.py`): `{"t": <UTC ISO, ms>, "kind": "frame",
"char": "fff4", "hex": "..."}`; the emulator replays records with
`kind == "frame"` and ignores the rest.

File `<yyyymmddThhmmssZ>-<session>.jsonl` in SCALE_BRIDGE_CAPTURE_DIR:

    {"kind": "header", "schema": 1, "session", "started", "scale" (masked),
     "rssi", "profile_hex", "profile_written", "app_version", "t"}
    {"kind": "frame", "t", "char", "hex", "valid"[, "error"]}    one per notification
    {"kind": "footer", "t", "ended_by", "result", counts..., "attribution",
     "decision", "person", "heart_rate", "end_marker_byte2"}

The files are health data (weight, impedance, heart rate in the hex): mode
0640, never logged (the log line names the file and the frame count only),
never in git. A missing or unwritable directory logs ONE error and capture
stays off until it works again; the weigh-in itself is never affected.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import __version__
from . import protocol as p
from .logs import emit
from .session import SessionLog

log = logging.getLogger("scale_bridge.capture")
SCHEMA = 1
NAME = re.compile(r"^(\d{8}T\d{6}Z)-[A-Za-z0-9_-]+\.jsonl$")
PRUNE_EVERY_S = 24 * 3600


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def file_name(started: datetime, session_id: str) -> str:
    return f"{started.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}-{session_id}.jsonl"


def frame_record(t: datetime, char: str, data: bytes) -> dict[str, Any]:
    rec: dict[str, Any] = {"t": _iso(t), "kind": "frame", "char": char, "hex": data.hex(), "valid": True}
    try:
        p.parse_frame(data)
    except p.FrameError as e:
        rec["valid"], rec["error"] = False, str(e)
    return rec


def end_marker_byte2(notifications: list[tuple[datetime, str, bytes]]) -> str | None:
    """Byte 2 of the first end marker, raw (only its stable bit is relied on)."""
    for _t, _c, b in notifications:
        try:
            f = p.parse_frame(b)
        except p.FrameError:
            continue
        if isinstance(f, p.EndMarkerFrame):
            return f"{f.flags:02x}"
    return None


class Captures:
    def __init__(self, directory: str, keep_days: int,
                 clock: Any = lambda: datetime.now(timezone.utc)) -> None:
        self.dir = Path(directory) if directory else None
        self.keep_days = keep_days
        self.clock = clock
        self._broken = False            # an error was logged; stay quiet until it works again
        self._last_prune = float("-inf")

    @property
    def enabled(self) -> bool:
        return self.dir is not None

    def _fail(self, problem: str, error: str) -> None:
        if not self._broken:
            emit(log, logging.ERROR, "capture_unavailable", dir=str(self.dir), problem=problem, error=error,
                 action="capture off, weigh-ins unaffected",
                 hint="create it owned by uid 65534, mode 0750 (runbook scale-bridge step 1)")
        self._broken = True

    def check(self) -> bool:
        """Directory exists and this uid can write into it. Logs once on failure."""
        if self.dir is None:
            return False
        if not self.dir.is_dir():
            self._fail("missing", "not a directory")
            return False
        if not os.access(self.dir, os.W_OK | os.X_OK):
            self._fail("not_writable", "permission denied")
            return False
        return True

    def startup(self) -> None:
        if self.dir is None:
            emit(log, logging.INFO, "capture", enabled=False)
            return
        ok = self.check()
        emit(log, logging.INFO, "capture", enabled=ok, dir=str(self.dir), keep_days=self.keep_days)
        self.maybe_prune()

    def write(self, rec: SessionLog, session_id: str, header: dict[str, Any],
              footer: dict[str, Any]) -> Path | None:
        """One file per session with at least one notification; None if skipped or failed."""
        if self.dir is None or not rec.notifications:
            return None
        if not self.check():
            return None
        started = rec.started_at or rec.notifications[0][0]
        path = self.dir / file_name(started, session_id)
        lines = [{"t": _iso(started), "kind": "header", "schema": SCHEMA, "session": session_id,
                  "started": _iso(started), "profile_hex": rec.profile_hex,
                  "profile_written": rec.profile_written, "app_version": __version__, **header}]
        lines += [frame_record(t, c, b) for t, c, b in rec.notifications]
        lines.append({"t": _iso(self.clock()), "kind": "footer", "frames": len(rec.notifications),
                      "end_marker_byte2": end_marker_byte2(rec.notifications), **footer})
        body = "".join(json.dumps(x, separators=(",", ":")) + "\n" for x in lines).encode()
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
            try:
                os.fchmod(fd, 0o640)     # whatever the umask
                os.write(fd, body)
            finally:
                os.close(fd)
        except OSError as e:
            self._fail("write_failed", type(e).__name__)
            return None
        self._broken = False
        emit(log, logging.INFO, "capture_written", file=path.name, frames=len(rec.notifications))
        return path

    def maybe_prune(self, mono: float | None = None) -> int:
        """At startup and then at most once a day (call it from the main loop)."""
        mono = time.monotonic() if mono is None else mono
        if mono - self._last_prune < PRUNE_EVERY_S:
            return 0
        self._last_prune = mono
        return self.prune()

    def prune(self) -> int:
        """Delete capture files older than keep_days (by the UTC stamp in the name). 0 = keep forever."""
        if self.dir is None or self.keep_days <= 0 or not self.dir.is_dir():
            return 0
        cutoff = self.clock() - timedelta(days=self.keep_days)
        n = 0
        try:
            entries = list(self.dir.iterdir())
        except OSError as e:
            self._fail("not_readable", type(e).__name__)
            return 0
        for f in entries:
            m = NAME.match(f.name)
            if not m:
                continue                          # not ours: leave it
            stamp = datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            if stamp < cutoff:
                try:
                    f.unlink()
                    n += 1
                except OSError:
                    pass
        if n:
            emit(log, logging.INFO, "capture_pruned", files=n, keep_days=self.keep_days)
        return n
