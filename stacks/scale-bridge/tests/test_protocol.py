"""Decoder: every frame type, checksum and length failures, the zero-weight trap."""
import pytest

from conftest import fixture_frames
from scale_bridge import protocol as p


def with_xor(body: bytes) -> bytes:
    return body + bytes([p.xor(body)])


def test_every_fixture_frame_passes_the_xor_rule():
    frames = fixture_frames()
    assert len(frames) == 73
    assert all(p.verify_xor(b) for _, b in frames)


def test_live_weight_frame():
    f = p.parse_frame(with_xor(bytes.fromhex("cf0000581b0000000101")))
    assert isinstance(f, p.WeightFrame)
    assert (f.stable, f.weight_cg, f.weight_kg, f.state) == (False, 7000, 70.0, 0x01)


def test_settled_weight_frame():
    f = p.parse_frame(bytes.fromhex("cf0080581b000000010508"))
    assert (f.stable, f.weight_cg, f.state) == (True, 7000, 0x05)


def test_end_marker_carries_the_heart_rate_in_byte_1():
    # As the load comes off: stable bit (+ another flag), weight 0, state 00. Synthetic BPM 0x4b.
    f = p.parse_frame(bytes.fromhex("cf4bc00000000000000044"))
    assert isinstance(f, p.EndMarkerFrame) and not isinstance(f, p.WeightFrame)
    assert f.heart_rate_bpm == 75 and f.heart_rate_raw == 0x4B and f.flags == 0xC0


def test_end_marker_with_heart_rate_0_means_none():
    f = p.parse_frame(with_xor(bytes.fromhex("cf00c000000000000000")))
    assert isinstance(f, p.EndMarkerFrame) and f.heart_rate_bpm is None and f.heart_rate_raw == 0


def test_end_marker_needs_only_the_stable_bit_not_the_extra_flag():
    f = p.parse_frame(with_xor(bytes.fromhex("cf4b8000000000000000")))
    assert isinstance(f, p.EndMarkerFrame) and f.heart_rate_bpm == 75 and f.flags == 0x80


@pytest.mark.parametrize("body,why", [
    ("cf4b4000000000000000", "no stable bit"),
    ("cf4bc000000000000005", "state 05, not 00"),
    ("cf4bc000000000000008", "state 08"),
    ("cf4bc0581b0000000000", "non-zero weight"),
])
def test_other_cf_frames_stay_weight_frames(body, why):
    f = p.parse_frame(with_xor(bytes.fromhex(body)))
    assert isinstance(f, p.WeightFrame), why


def test_impedance_frames():
    lo = p.parse_frame(bytes.fromhex("be00b80bea0bfa00540b220b60"))
    hi = p.parse_frame(bytes.fromhex("be018c0abe0adc00140aec09aa"))
    assert isinstance(lo, p.ImpedanceFrame) and lo.index == 0
    assert lo.ohms == (300.0, 305.0, 25.0, 290.0, 285.0)
    assert hi.index == 1 and hi.deci_ohms == (2700, 2750, 220, 2580, 2540)


def test_extended_frame_kept_raw_with_index():
    raw = bytes.fromhex("df020100bf02cd405b516efc91db348c86")
    f = p.parse_frame(raw)
    assert isinstance(f, p.ExtendedFrame) and f.index == (1, 0) and f.raw == raw


def test_unknown_type_with_valid_checksum():
    f = p.parse_frame(with_xor(bytes([0xAA, 0x01, 0x02])))
    assert isinstance(f, p.UnknownFrame) and f.kind == 0xAA


@pytest.mark.parametrize("hexstr", [
    "cf0080581b000000010509",            # settled frame, last byte off by one
    "be00b80bea0bfa00540b220b61",
    "df020100bf02cd405b516efc91db348c87",
])
def test_checksum_failure(hexstr):
    with pytest.raises(p.FrameError, match="checksum"):
        p.parse_frame(bytes.fromhex(hexstr))


@pytest.mark.parametrize("data", [b"", with_xor(bytes.fromhex("cf0080581b0000")),
                                  with_xor(bytes.fromhex("be00b80bea0b"))])
def test_bad_length_or_empty(data):
    with pytest.raises(p.FrameError):
        p.parse_frame(data)


def test_profile_frame_layout_and_checksum():
    f = p.profile_frame(sex=1, activity=1, height_cm=170, age=30)
    assert f[:2] == b"\xfe\x01" and f[2:7] == bytes([1, 1, 170, 30, 1])
    assert f[7] == p.xor(f[1:7]) and len(f) == 8
    with pytest.raises(ValueError):
        p.profile_frame(1, 1, 300, 30)
