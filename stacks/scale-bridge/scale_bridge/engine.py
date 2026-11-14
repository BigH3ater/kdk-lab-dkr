"""Lefu 8-electrode body-composition engine, run as a child program once per weigh-in.

The engine (`lefu-engine`, engine/ + Dockerfile stage `engine`) is Lefu's own calculation
library: the vendor's Java wrapper on the image's JRE, its arm64 native code emulated by
unidbg. It needs a secret (LEFU_SECRET_8), which is put into the CHILD's environment only;
the child gets nothing else from the bridge's environment except the LEFU_* paths and PATH
(no wger keys). Input goes on stdin, never argv. stdout is one JSON object; stderr is
discarded (the vendor code prints its inputs there in debug mode).

Dry-run phase (2026-11): results are NOT written to wger. They go next to the raw capture as
`<capture name>.engine.json` (capture.py), and one `engine` log line per run carries the
duration, the error type and a count of output fields, never a value.

Inputs (known-good configuration, prototype 2026-11-14, do not change unless testing):
* DF frames: four 17-byte frames, indices (sub-packet b2, frequency x b3). For each x the
  payload bytes 4..15 of (0,x) and 4..11 of (1,x) give five little-endian uint32 = the
  library's encrypted impedances for frame positions 0..4.
* LEFU_DF_X0 = "20k": DF x=0 is the library's 20 kHz input, x=1 the 100 kHz one.
* LEFU_LIMB_ORDER = "10432": the frame position for each library slot in the order
  left arm, right arm, left leg, right leg, trunk (frame position 2 = trunk).
* LEFU_VARIANT = "8_4": PPDeviceCalcuteTypeAlternate8_4.
* Height truncated to whole cm; age in whole years at the measurement date.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from .attribution import Profile
from .logs import emit
from .session import Reading

log = logging.getLogger("scale_bridge.engine")

OK = "PP_ERROR_TYPE_NONE"
VARIANT_PREFIX = "PPDeviceCalcuteType"
DEFAULT_VARIANT = "8_4"
DEFAULT_LIMB_ORDER = "10432"
DEFAULT_DF_X0 = "20k"
DF_KEYS = ((0, 0), (1, 0), (0, 1), (1, 1))
MAX_OUTPUT = 1 << 20                        # 1 MiB of stdout is far more than one result
# Child environment: these names from the bridge's own environment, plus the secret.
CHILD_ENV = ("PATH", "TZ", "LEFU_LIB_DIR", "LEFU_ASSETS", "LEFU_PACKAGE", "LEFU_XMX", "LEFU_BACKEND")


@dataclass(frozen=True)
class EngineSettings:
    switch: str = "on"                                      # SCALE_BRIDGE_ENGINE: on | off
    command: tuple[str, ...] = ("/usr/local/bin/lefu-engine",)
    timeout: float = 60.0
    variant: str = DEFAULT_VARIANT
    limb_order: str = DEFAULT_LIMB_ORDER
    df_x0: str = DEFAULT_DF_X0
    info_path: str = "/opt/lefu/engine-info.json"
    secret: str = field(default="", repr=False)             # LEFU_SECRET_8 (secrets.env)

    @property
    def variant_name(self) -> str:
        v = self.variant
        return v if v.startswith(VARIANT_PREFIX) else f"{VARIANT_PREFIX}Alternate{v}"


def parse_limb_order(text: str) -> tuple[int, ...]:
    t = text.replace(",", "").strip()
    if len(t) != 5 or sorted(t) != list("01234"):
        raise ValueError("LEFU_LIMB_ORDER must be a permutation of 01234, e.g. 10432")
    return tuple(int(c) for c in t)


def check_settings(s: EngineSettings) -> None:
    """Config-time validation (ValueError -> config_invalid at startup)."""
    if s.switch not in ("on", "off"):
        raise ValueError('SCALE_BRIDGE_ENGINE must be "on" or "off"')
    parse_limb_order(s.limb_order)
    if s.df_x0 not in ("20k", "100k"):
        raise ValueError('LEFU_DF_X0 must be "20k" or "100k"')
    if not s.variant.replace("_", "").isalnum():
        raise ValueError("LEFU_VARIANT must look like 8_4")
    if not s.command:
        raise ValueError("SCALE_BRIDGE_ENGINE_CMD is empty")
    if not s.timeout > 0:
        raise ValueError("SCALE_BRIDGE_ENGINE_TIMEOUT_S must be > 0")


def decode_df(df_raw: tuple[str, ...]) -> tuple[tuple[int, ...], tuple[int, ...]] | None:
    """(x0 values, x1 values), five each, frame positions 0..4; None unless all four frames are there."""
    frames: dict[tuple[int, int], bytes] = {}
    for h in df_raw:
        try:
            b = bytes.fromhex(h)
        except ValueError:
            return None
        if len(b) != 17 or b[0] != 0xDF:
            return None
        frames[(b[2], b[3])] = b
    if any(k not in frames for k in DF_KEYS):
        return None
    out = []
    for x in (0, 1):
        raw = frames[(0, x)][4:16] + frames[(1, x)][4:12]
        out.append(tuple(int.from_bytes(raw[4 * i:4 * i + 4], "little") for i in range(5)))
    return out[0], out[1]


def age_on(birth: date, day: date) -> int:
    return day.year - birth.year - ((day.month, day.day) < (birth.month, birth.day))


def profile_inputs(profile: Profile, measured_at: datetime) -> dict[str, Any]:
    """height (whole cm, truncated), sex, age (whole years at the measurement's local date)."""
    if profile.birth_date is not None:
        age, source = age_on(profile.birth_date, measured_at.astimezone().date()), "birth_date"
    else:
        age, source = int(profile.age or 0), "age"
    return {"height_cm": math.floor(profile.height_cm), "sex": profile.sex, "age": age, "age_from": source}


def build_case(reading: Reading, profile: Profile, s: EngineSettings) -> dict[str, Any] | None:
    """The engine's stdin case, or None if the DF frames are incomplete."""
    df = decode_df(reading.df_raw)
    if df is None:
        return None
    order = parse_limb_order(s.limb_order)
    lo, hi = (df[0], df[1]) if s.df_x0 == "20k" else (df[1], df[0])
    pi = profile_inputs(profile, reading.measured_at)
    return {"id": "scale-bridge", "type": s.variant_name, "weightKg": reading.weight_cg / 100,
            "heightCm": pi["height_cm"], "age": pi["age"], "sex": pi["sex"],
            "z20": [lo[i] for i in order], "z100": [hi[i] for i in order], "bodyAgeType": 0}


@dataclass
class EngineRun:
    """One engine call. `result` is a reason code, safe to log; `output` is health data."""
    result: str                       # ok | engine_error | timeout | nonzero_exit | calc_exception |
    #                                   malformed_output | spawn_failed | secret_in_output | crashed
    duration_s: float = 0.0
    error_type: str | None = None
    fields: int = 0
    exit_code: int | None = None
    output: dict[str, Any] | None = None          # the engine's JSON (only when it parsed)
    case: dict[str, Any] | None = None
    profile: dict[str, Any] | None = None
    document: dict[str, Any] | None = None        # what capture.py writes as <capture>.engine.json

    @property
    def has_result(self) -> bool:
        return self.output is not None and self.result in ("ok", "engine_error")


class Engine:
    def __init__(self, s: EngineSettings) -> None:
        self.s = s
        self.enabled = False
        self.reason = "not_started"
        self.info: dict[str, Any] = {}

    def startup(self, people_with_profile: list[str]) -> None:
        """Decide on/off once; one `engine_config` line either way."""
        cmd = self.s.command[0]
        if self.s.switch == "off":
            self.reason = "switched_off"
        elif not self.s.secret:
            self.reason = "no_secret"
        elif not (shutil.which(cmd) or (os.path.isfile(cmd) and os.access(cmd, os.X_OK))):
            self.reason = "no_binary"
        else:
            self.enabled, self.reason = True, "on"
        try:
            with open(self.s.info_path, encoding="utf-8") as f:
                info = json.load(f)
            self.info = info if isinstance(info, dict) else {}
        except (OSError, ValueError):
            self.info = {}
        hints = {"no_secret": "LEFU_SECRET_8 is not in secrets.env: engine off, weigh-ins unaffected",
                 "no_binary": f"{cmd} not found: engine off, weigh-ins unaffected",
                 "switched_off": "SCALE_BRIDGE_ENGINE=off"}
        emit(log, logging.INFO if self.enabled or self.reason == "switched_off" else logging.WARNING,
             "engine_config", enabled=self.enabled, reason=self.reason, hint=hints.get(self.reason),
             variant=self.s.variant_name, limb_order=self.s.limb_order, df_x0=self.s.df_x0,
             timeout_s=self.s.timeout, profiles=people_with_profile, sdk_commit=self.info.get("sdk_commit"))

    def provenance(self, run: EngineRun) -> dict[str, Any]:
        return {"variant": self.s.variant_name, "limb_order": self.s.limb_order, "df_x0": self.s.df_x0,
                "df_endian": "little", "profile": run.profile, "engine": self.info}

    def _child_env(self) -> dict[str, str]:
        env = {k: os.environ[k] for k in CHILD_ENV if k in os.environ}
        env["LEFU_SECRET_8"] = self.s.secret
        return env

    async def run(self, case: dict[str, Any]) -> EngineRun:
        """Never raises; every failure is a reason code."""
        t0 = time.monotonic()
        run = EngineRun(result="crashed", case=case)
        try:
            await self._run(case, run)
        except Exception:  # noqa: BLE001 - the weigh-in must never see an engine problem
            run.result, run.output = "crashed", None
        run.duration_s = round(time.monotonic() - t0, 3)
        return run

    async def _run(self, case: dict[str, Any], run: EngineRun) -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                *self.s.command, "--case", "-", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, env=self._child_env(), close_fds=True)
        except OSError:
            run.result = "spawn_failed"
            return
        try:
            out, _ = await asyncio.wait_for(proc.communicate(json.dumps(case).encode()), self.s.timeout)
        except TimeoutError:
            run.result = "timeout"
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
            return
        run.exit_code = proc.returncode
        text = out[:MAX_OUTPUT].decode("utf-8", "replace")
        if self.s.secret and self.s.secret in text:
            run.result = "secret_in_output"           # never keep it, never write it anywhere
            return
        try:
            data = json.loads(text) if len(out) <= MAX_OUTPUT else None
        except ValueError:
            data = None
        if proc.returncode == 3 and isinstance(data, dict) and "exception" in data:
            run.result = "calc_exception"
            return
        if proc.returncode != 0:
            run.result = "nonzero_exit"
            return
        if not isinstance(data, dict) or not isinstance(data.get("errorType"), str) \
                or not isinstance(data.get("out"), dict):
            run.result = "malformed_output"
            return
        run.output = data
        run.error_type = data["errorType"]
        run.fields = len(data["out"])
        run.result = "ok" if run.error_type == OK else "engine_error"


def log_run(run: EngineRun, session_id: str, person: str) -> None:
    """One line per run: reason, duration, error type, field count. Never a value."""
    level = logging.INFO if run.result == "ok" else logging.WARNING if run.result == "engine_error" else logging.ERROR
    timing = (run.output or {}).get("timing") if run.output else None
    emit(log, level, "engine", session=session_id, person=person, result=run.result,
         duration_s=run.duration_s, error_type=run.error_type, fields=run.fields, exit_code=run.exit_code,
         init_ms=timing.get("init_ms") if isinstance(timing, dict) else None,
         calc_ms=timing.get("calc_ms") if isinstance(timing, dict) else None)


def result_document(engine: Engine, run: EngineRun, session_id: str, person: str,
                    reading: Reading, app_version: str) -> dict[str, Any]:
    """What goes into `<capture name>.engine.json` (health data, 0640, next to the capture)."""
    timing = (run.output or {}).get("timing")
    return {"kind": "engine", "schema": 1, "session": session_id, "person": person,
            "measured_at": reading.measured_at.isoformat(timespec="milliseconds"),
            "result": run.result, "error_type": run.error_type, "fields": run.fields,
            "app_version": app_version,
            "provenance": {**engine.provenance(run), "weight_kg": reading.weight_cg / 100},
            "input": run.case,
            "timings": {"wall_s": run.duration_s, **(timing if isinstance(timing, dict) else {})},
            "output": run.output}
