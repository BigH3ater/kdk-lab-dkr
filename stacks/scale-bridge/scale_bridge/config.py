"""Settings from the environment: compose `environment` plus the two host env files.

Nothing here has a real default for a person: the profile values are fixed
placeholders that only trigger the impedance measurement (they are not anyone's
height or age), and the people table is empty until the host file sets it.
"""
from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field

from .attribution import Person, load_people
from .engine import DEFAULT_DF_X0, DEFAULT_LIMB_ORDER, DEFAULT_VARIANT, EngineSettings, check_settings
from .session import Timeouts

MAC = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")
TRUE = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Proxy:
    host: str                                   # IP address (bridge network: no mDNS)
    noise_psk: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class Settings:
    dry_run: bool = True
    scale_name: str = "Body Pod 2"
    scale_address: str | None = None            # optional filter, host env file only
    profile_sex: int = 1
    profile_activity: int = 1
    profile_height_cm: int = 170                # placeholder, see module docstring
    profile_age: int = 30                       # placeholder
    connect_attempts: int = 3
    connect_timeout: float = 30.0               # whole establish_connection, all attempts
    timeouts: Timeouts = Timeouts()
    cooldown: float = 60.0                      # ignore the scale after a reading (it replays on reconnect)
    retry_backoff: float = 2.0                  # after a failed connect
    wger_url: str = "https://wger.kmkdp.com"
    wger_timeout: float = 15.0
    write_retries: int = 6                      # per reading, waits 30 s doubling: 15.5 min in all
    replay_window: float = 600.0                # same weight within this = the same weigh-in (wger.py)
    people: tuple[Person, ...] = ()
    tokens: dict[str, str] = field(default_factory=dict, repr=False)   # token_env -> token
    proxies: tuple[Proxy, ...] = ()
    heartbeat_path: str = "/tmp/scale-bridge.alive"
    write_heart_rate: bool = False              # heart rate meaning confirmed on one capture only
    capture_dir: str = ""                       # raw capture JSONL per session; "" = off (compose: /captures)
    capture_keep_days: int = 60                 # 0 = keep forever
    log_level: str = "INFO"
    engine: EngineSettings = EngineSettings()     # body-composition engine (engine.py)

    def token_for(self, person: Person) -> str:
        return self.tokens.get(person.token_env, "")


def _bool(v: str | None, default: bool) -> bool:
    return default if v is None or v.strip() == "" else v.strip().lower() in TRUE


def parse_proxies(spec: str, env: dict[str, str]) -> tuple[Proxy, ...]:
    """`host[=NOISE_ENV][,host2[=NOISE_ENV2]]`: the key itself stays in its own env var."""
    out = []
    for part in (x.strip() for x in spec.split(",")):
        if not part:
            continue
        host, _, key_env = part.partition("=")
        host, key_env = host.strip(), key_env.strip()
        if not host:
            raise ValueError("SCALE_BRIDGE_ESPHOME_PROXIES has an empty host")
        psk = None
        if key_env:
            psk = env.get(key_env, "")
            if not psk:
                raise ValueError(f"noise key env {key_env} for an ESPHome proxy is empty")
        out.append(Proxy(host=host, noise_psk=psk))
    return tuple(out)


def from_env(env: dict[str, str] | None = None) -> Settings:
    e = dict(os.environ if env is None else env)
    people = tuple(load_people(e.get("SCALE_BRIDGE_PEOPLE")))
    addr = (e.get("SCALE_ADDRESS") or "").strip() or None
    if addr and not MAC.match(addr):
        raise ValueError("SCALE_ADDRESS must look like AA:BB:CC:DD:EE:FF")
    t = Timeouts(
        session=float(e.get("SCALE_SESSION_TIMEOUT_S", "90")),
        impedance_wait=float(e.get("SCALE_IMPEDANCE_WAIT_S", "5")),
        df_grace=float(e.get("SCALE_DF_GRACE_S", "1")),
        end_marker_wait=float(e.get("SCALE_END_MARKER_WAIT_S", "20")),
    )
    return Settings(
        dry_run=_bool(e.get("SCALE_BRIDGE_DRY_RUN"), True),
        scale_name=e.get("SCALE_NAME", "Body Pod 2"),
        scale_address=addr.upper() if addr else None,
        profile_sex=int(e.get("SCALE_PROFILE_SEX", "1")),
        profile_activity=int(e.get("SCALE_PROFILE_ACTIVITY", "1")),
        profile_height_cm=int(e.get("SCALE_PROFILE_HEIGHT_CM", "170")),
        profile_age=int(e.get("SCALE_PROFILE_AGE", "30")),
        connect_attempts=int(e.get("SCALE_CONNECT_ATTEMPTS", "3")),
        connect_timeout=float(e.get("SCALE_CONNECT_TIMEOUT_S", "30")),
        timeouts=t,
        cooldown=float(e.get("SCALE_COOLDOWN_S", "60")),
        wger_url=e.get("WGER_URL", "https://wger.kmkdp.com").rstrip("/"),
        write_retries=int(e.get("SCALE_BRIDGE_WRITE_RETRIES", "6")),
        replay_window=float(e.get("SCALE_BRIDGE_REPLAY_WINDOW_S", "600")),
        people=people,
        tokens={x.token_env: e.get(x.token_env, "") for x in people},
        proxies=parse_proxies(e.get("SCALE_BRIDGE_ESPHOME_PROXIES", ""), e),
        log_level=e.get("SCALE_BRIDGE_LOG_LEVEL", "INFO"),
        write_heart_rate=_bool(e.get("SCALE_BRIDGE_WRITE_HEART_RATE"), False),
        capture_dir=(e.get("SCALE_BRIDGE_CAPTURE_DIR") or "").strip(),
        capture_keep_days=int(e.get("SCALE_BRIDGE_CAPTURE_KEEP_DAYS", "60")),
        engine=engine_from_env(e),
    )


def engine_from_env(e: dict[str, str]) -> EngineSettings:
    s = EngineSettings(
        switch=(e.get("SCALE_BRIDGE_ENGINE") or "on").strip().lower(),
        command=tuple(shlex.split(e.get("SCALE_BRIDGE_ENGINE_CMD") or "/usr/local/bin/lefu-engine")),
        timeout=float(e.get("SCALE_BRIDGE_ENGINE_TIMEOUT_S") or "60"),
        variant=(e.get("LEFU_VARIANT") or DEFAULT_VARIANT).strip(),
        limb_order=(e.get("LEFU_LIMB_ORDER") or DEFAULT_LIMB_ORDER).strip(),
        df_x0=(e.get("LEFU_DF_X0") or DEFAULT_DF_X0).strip().lower(),
        secret=(e.get("LEFU_SECRET_8") or "").strip(),
    )
    check_settings(s)
    return s
