"""Bluetooth side: scan for the scale, connect, hand the client to session.run_session.

Two transports, same code path:
* local (default): bleak's BlueZ backend over the host's D-Bus system bus
  (/run/dbus bind-mounted read-only); bluetoothd owns the radio.
* relay (SCALE_BRIDGE_ESPHOME_PROXIES set): ESPHome Bluetooth proxies through
  bleak-esphome. habluetooth's manager swaps bleak.BleakScanner / BleakClient and
  bleak_retry_connector.BleakClientWithServiceCache for routing wrappers, so this
  module looks those names up at call time (never `from bleak import ...`).
  Only the proxies are used in relay mode, not the local adapter. Live since
  2026-11-15 through one ESPHome proxy (scale-relay).
"""
from __future__ import annotations

import asyncio
import logging
import pathlib
import time
from typing import Any, Awaitable, Callable

import bleak
import bleak_retry_connector

from . import protocol as p
from .capture import Captures
from .config import Proxy, Settings
from .logs import emit
from .session import SessionLog, SessionResult, run_session

log = logging.getLogger("scale_bridge.ble")


def mask(address: str) -> str:
    """Last two octets only, so logs identify the scale without carrying its address."""
    parts = address.split(":")
    return "..:" + ":".join(parts[-2:]) if len(parts) == 6 else "?"


def is_scale(settings: Settings, address: str, name: str | None, service_uuids: list[str]) -> bool:
    if settings.scale_address and address.upper() != settings.scale_address:
        return False
    if name:
        return name.strip().lower() == settings.scale_name.lower()
    # A passive scan (e.g. through a proxy) may not carry the name, which lives
    # in the scan response; the service UUID is in the advert itself.
    return p.SVC_UUID in [u.lower() for u in service_uuids]


async def start_relays(proxies: tuple[Proxy, ...]) -> Callable[[], Awaitable[None]] | None:
    """Bring up ESPHome proxies (bleak-esphome). None when relay mode is off."""
    if not proxies:
        return None
    import habluetooth                       # relay mode only
    from bleak_esphome import APIConnectionManager

    await habluetooth.BluetoothManager().async_setup()
    conns = [(x, APIConnectionManager({"address": x.host, "noise_psk": x.noise_psk})) for x in proxies]
    tasks = {asyncio.create_task(c.start()): x for x, c in conns}
    done, pending = await asyncio.wait(tasks, timeout=10)
    for t in done:
        exc = t.exception()
        emit(log, logging.ERROR if exc else logging.INFO, "relay_start", proxy=tasks[t].host,
             result="failed" if exc else "connected", error=type(exc).__name__ if exc else None)
    for t in pending:   # ReconnectLogic keeps trying in the background
        emit(log, logging.WARNING, "relay_start", proxy=tasks[t].host, result="waiting")

    async def stop() -> None:
        for t in pending:
            t.cancel()
        await asyncio.gather(*(c.stop() for _, c in conns), return_exceptions=True)
    return stop


class Watcher:
    """Advert callback -> at most one queued scale device; ignores the scale while busy/cooling down."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.queue: asyncio.Queue[tuple[Any, int | None]] = asyncio.Queue(maxsize=1)
        self.busy = False
        self.quiet_until = 0.0

    def on_advert(self, device: Any, adv: Any) -> None:
        name = getattr(adv, "local_name", None) or getattr(device, "name", None)
        if not is_scale(self.settings, device.address, name, list(getattr(adv, "service_uuids", []) or [])):
            return
        if self.busy or time.monotonic() < self.quiet_until or self.queue.full():
            return
        self.queue.put_nowait((device, getattr(adv, "rssi", None)))


async def connect_and_run(settings: Settings, device: Any, profile: bytes,
                          record: SessionLog | None = None) -> tuple[SessionResult | None, dict[str, Any]]:
    """Connect (bounded, retried), run one session, always disconnect.

    `record` (caller-owned) collects every notification, also when the session hangs or errors.
    """
    loop = asyncio.get_running_loop()
    gone = asyncio.Event()
    t0 = loop.time()
    info: dict[str, Any] = {}
    try:
        async with asyncio.timeout(settings.connect_timeout):
            client = await bleak_retry_connector.establish_connection(
                bleak_retry_connector.BleakClientWithServiceCache, device, settings.scale_name,
                disconnected_callback=lambda _c: gone.set(), max_attempts=settings.connect_attempts)
    except (TimeoutError, bleak.exc.BleakError, bleak_retry_connector.BleakNotFoundError,
            bleak_retry_connector.BleakConnectionError, bleak_retry_connector.BleakAbortedError,
            EOFError, BrokenPipeError, OSError) as e:
        info.update(result="connect_failed", error=type(e).__name__, connect_s=round(loop.time() - t0, 1))
        return None, info
    info["connect_s"] = round(loop.time() - t0, 1)
    try:
        async with asyncio.timeout(settings.timeouts.session + 15):
            result = await run_session(client, profile, gone, settings.timeouts, record=record)
    except TimeoutError:
        info.update(result="session_hung")
        return None, info
    except (bleak.exc.BleakError, EOFError, BrokenPipeError, OSError) as e:
        info.update(result="session_error", error=type(e).__name__)
        return None, info
    finally:
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001 - already gone is fine
            pass
    info["result"] = "reading" if result.reading else "no_reading"
    return result, info


def touch(path: str) -> None:
    try:
        pathlib.Path(path).touch()
    except OSError:
        pass


def footer(result: SessionResult | None, info: dict[str, Any], decision: Any,
           engine: Any = None) -> dict[str, Any]:
    """Capture-file footer: end reason, counts, attribution, engine reason code.

    No weight/impedance/heart-rate values.
    """
    outcome = getattr(decision, "outcome", None)
    reading = result.reading if result else None
    return {
        "ended_by": result.ended_by if result else None,
        "result": info.get("result"), "error": info.get("error"),
        "counts": result.summary if result else {},
        "attribution": None if outcome is None else (outcome if outcome in ("no_match", "ambiguous") else "matched"),
        "decision": outcome,
        "person": getattr(decision, "person", None),
        "heart_rate": bool(reading and reading.heart_rate_bpm is not None),
        "engine": getattr(engine, "result", None),          # reason code; None = not run
    }


def write_capture(captures: Captures, record: SessionLog, session_id: str, device: Any, rssi: int | None,
                  result: SessionResult | None, info: dict[str, Any], decision: Any, engine: Any = None) -> None:
    try:
        doc = engine.document if engine is not None and engine.has_result else None
        captures.write(record, session_id, {"scale": mask(device.address), "rssi": rssi},
                       footer(result, info, decision, engine), engine=doc)
    except Exception as e:  # noqa: BLE001 - a capture must never stop the bridge
        emit(log, logging.ERROR, "capture_failed", session=session_id, error=type(e).__name__)


async def run(settings: Settings, on_reading: Callable[[Any, str, str], Awaitable[Any]],
              captures: Captures | None = None,
              on_engine: Callable[[Any, Any, str], Awaitable[Any]] | None = None) -> None:
    """Forever: wait for the scale's advert, run a session, pass the reading on, save the capture.

    `on_engine(reading, decision, session_id)` (Bridge.compute) runs after the decision and
    before the capture is written; it never raises (and is guarded here as well).
    """
    if captures is None:
        captures = Captures(settings.capture_dir, settings.capture_keep_days)
    captures.startup()
    stop_relays = await start_relays(settings.proxies)
    watcher = Watcher(settings)
    profile = p.profile_frame(settings.profile_sex, settings.profile_activity,
                              settings.profile_height_cm, settings.profile_age)
    scanner = bleak.BleakScanner(detection_callback=watcher.on_advert)
    await scanner.start()
    emit(log, logging.INFO, "scanning", transport="esphome" if settings.proxies else "local",
         proxies=len(settings.proxies), name=settings.scale_name,
         address_filter=bool(settings.scale_address), dry_run=settings.dry_run)
    n = 0
    try:
        while True:
            touch(settings.heartbeat_path)
            captures.maybe_prune()
            try:
                device, rssi = await asyncio.wait_for(watcher.queue.get(), timeout=30)
            except TimeoutError:
                continue
            n += 1
            session_id = f"{int(time.time())}-{n}"
            watcher.busy = True
            started = time.monotonic()
            record = SessionLog()
            try:
                result, info = await connect_and_run(settings, device, profile, record)
            finally:
                watcher.busy = False
            summary = result.summary if result else {}
            emit(log, logging.INFO if info.get("result") == "reading" else logging.WARNING, "session",
                 session=session_id, scale=mask(device.address), rssi=rssi,
                 ended_by=result.ended_by if result else None,
                 duration_s=round(time.monotonic() - started, 1), **info, **summary)
            decision = engine = None
            if result and result.reading:
                watcher.quiet_until = time.monotonic() + settings.cooldown
                decision = await on_reading(result.reading, device.address, session_id)
                if on_engine is not None:
                    touch(settings.heartbeat_path)
                    try:
                        engine = await on_engine(result.reading, decision, session_id)
                    except Exception as e:  # noqa: BLE001 - the engine never stops the bridge
                        emit(log, logging.ERROR, "engine", session=session_id, result="crashed",
                             error=type(e).__name__)
            else:
                watcher.quiet_until = time.monotonic() + settings.retry_backoff
            write_capture(captures, record, session_id, device, rssi, result, info, decision, engine)
            touch(settings.heartbeat_path)
    finally:
        await scanner.stop()
        if stop_relays:
            await stop_relays()
