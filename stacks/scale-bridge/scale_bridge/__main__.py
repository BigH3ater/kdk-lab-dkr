"""Entry point: python -I -m scale_bridge (long-running; see README)."""
from __future__ import annotations

import asyncio
import logging
import sys

import httpx

from . import logs
from .app import Bridge
from .attribution import ConfigError
from .config import from_env

log = logging.getLogger("scale_bridge")


async def amain() -> int:
    try:
        settings = from_env()
    except (ConfigError, ValueError) as e:
        logs.setup()
        logs.emit(log, logging.ERROR, "config_invalid", error=str(e))
        return 2
    logs.setup(settings.log_level)
    async with httpx.AsyncClient(base_url=settings.wger_url, timeout=settings.wger_timeout) as http:
        bridge = Bridge(settings, http)
        await bridge.startup_checks()
        from . import ble                 # imports bleak; after config so a bad env fails fast
        try:
            await ble.run(settings, bridge.handle, on_engine=bridge.compute)
        except Exception as e:  # noqa: BLE001 - e.g. no D-Bus / bluetoothd: one line, then restart
            logs.emit(log, logging.ERROR, "fatal", error=type(e).__name__, detail=str(e)[:200])
            return 1
        finally:
            await bridge.drain()
    return 0


def main() -> None:
    try:
        sys.exit(asyncio.run(amain()))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
