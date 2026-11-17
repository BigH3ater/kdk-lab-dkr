"""Body Pod 2 (Lefu FFF0) frame codec. Pure functions, no I/O.

Confirmed on our unit (vault capture note 2026-11-14), matching the public
Blue2Scale BLE_SCALE_PROTOCOL.md reference:

* every frame ends with the XOR of all preceding bytes;
* ``CF`` (11 bytes): weight = LE uint16 at bytes 3-4 / 100 kg, byte 2 bit 0x80 =
  stable, byte 9 = measurement state (01 live, 08 just before the lock, 05 on the
  settled frame, 00 on the zero-weight "stable" frame as the load comes off);
* that zero-weight stable frame with state 00 is the scale's END-OF-MEASUREMENT
  marker (``EndMarkerFrame``), about 5 s after the lock, only when the handle was
  held. Byte 1 is the heart rate in BPM (0 = none): matched the vendor app on
  6 of 6 paired weigh-ins (first 2026-11-14, vault hume-app-results note). Byte 2
  was the stable bit plus another flag (0x40) on the first; only the stable bit is used;
* ``BE`` (13 bytes): byte 1 = index (0 ~50 kHz, 1 ~250 kHz), then five LE uint16
  / 10 ohm, in the reference's order right arm, left arm, trunk, right leg, left leg;
* ``DF`` (17 bytes): four sub-packets indexed by bytes 2-3, payload undecoded.

Weights are kept in centi-kilograms and impedances in deci-ohms (the wire
units), so nothing is rounded twice.
"""
from __future__ import annotations

from dataclasses import dataclass

SVC_UUID = "0000fff0-0000-1000-8000-00805f9b34fb"
WRITE_UUID = "0000fff1-0000-1000-8000-00805f9b34fb"     # profile, write-with-response
NOTIFY_UUID = "0000fff4-0000-1000-8000-00805f9b34fb"    # every frame arrives here
NOTIFY_ALT_UUID = "0000fff2-0000-1000-8000-00805f9b34fb"  # silent on our unit; subscribed as a fallback

CF, BE, DF, FE = 0xCF, 0xBE, 0xDF, 0xFE
LENGTHS = {CF: 11, BE: 13, DF: 17}
STABLE_BIT = 0x80
END_STATE = 0x00          # CF byte 9 on the end-of-measurement marker

# Order from the protocol reference; on our captures index 2 is the small
# (trunk-sized) value, which agrees with it.
SEGMENTS = ("right arm", "left arm", "trunk", "right leg", "left leg")
FREQUENCIES = {0: "50 kHz", 1: "250 kHz"}


class FrameError(ValueError):
    """A notification that is not a valid frame (length, checksum, empty)."""


def xor(data: bytes) -> int:
    x = 0
    for b in data:
        x ^= b
    return x


def verify_xor(frame: bytes) -> bool:
    return len(frame) >= 2 and xor(frame[:-1]) == frame[-1]


@dataclass(frozen=True)
class WeightFrame:
    stable: bool
    weight_cg: int        # centi-kilograms (wire unit)
    state: int            # byte 9
    raw: bytes

    @property
    def weight_kg(self) -> float:
        return self.weight_cg / 100


@dataclass(frozen=True)
class ImpedanceFrame:
    index: int                      # 0 = ~50 kHz, 1 = ~250 kHz
    deci_ohms: tuple[int, ...]      # five values, SEGMENTS order
    raw: bytes

    @property
    def ohms(self) -> tuple[float, ...]:
        return tuple(v / 10 for v in self.deci_ohms)


@dataclass(frozen=True)
class ExtendedFrame:
    index: tuple[int, int]          # bytes 2-3
    raw: bytes


@dataclass(frozen=True)
class UnknownFrame:
    kind: int
    raw: bytes


@dataclass(frozen=True)
class EndMarkerFrame:
    """``CF`` with the stable bit, weight 0 and state 00: the measurement is over."""
    heart_rate_raw: int             # byte 1, BPM; 0 = no heart rate
    flags: int                      # byte 2 as sent (stable bit plus whatever else)
    raw: bytes

    @property
    def heart_rate_bpm(self) -> int | None:
        return self.heart_rate_raw or None


Frame = WeightFrame | EndMarkerFrame | ImpedanceFrame | ExtendedFrame | UnknownFrame


def parse_frame(data: bytes) -> Frame:
    """Decode one notification. Raises FrameError on a bad length or checksum."""
    b = bytes(data)
    if not b:
        raise FrameError("empty frame")
    kind = b[0]
    want = LENGTHS.get(kind)
    if want is not None and len(b) != want:
        raise FrameError(f"0x{kind:02x} frame is {len(b)} bytes, expected {want}")
    if not verify_xor(b):
        raise FrameError(f"0x{kind:02x} frame checksum mismatch")
    if kind == CF:
        stable, weight, state = bool(b[2] & STABLE_BIT), int.from_bytes(b[3:5], "little"), b[9]
        if stable and weight == 0 and state == END_STATE:
            return EndMarkerFrame(heart_rate_raw=b[1], flags=b[2], raw=b)
        return WeightFrame(stable=stable, weight_cg=weight, state=state, raw=b)
    if kind == BE:
        vals = tuple(int.from_bytes(b[i:i + 2], "little") for i in range(2, 12, 2))
        return ImpedanceFrame(index=b[1], deci_ohms=vals, raw=b)
    if kind == DF:
        return ExtendedFrame(index=(b[2], b[3]), raw=b)
    return UnknownFrame(kind=kind, raw=b)


def profile_frame(sex: int, activity: int, height_cm: int, age: int) -> bytes:
    """``FE 01 <sex> <activity> <height> <age> 01 <xor of bytes 1-6>`` (slot 1, kg)."""
    for name, v in (("sex", sex), ("activity", activity), ("height_cm", height_cm), ("age", age)):
        if not 0 <= v <= 255:
            raise ValueError(f"profile {name} must fit in one byte (0-255)")
    body = bytes([0x01, sex, activity, height_cm, age, 0x01])
    return bytes([FE]) + body + bytes([xor(body)])
