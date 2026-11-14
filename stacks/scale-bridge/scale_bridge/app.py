"""Reading -> person -> wger. Dry-run (the committed default) logs and posts nothing."""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import timedelta
from typing import Awaitable, Callable

import httpx

from .attribution import attribute, overlaps
from .config import Settings
from .logs import emit
from .session import Reading
from .wger import LB_PER_KG, WgerClient, WgerError, external_id, plan

log = logging.getLogger("scale_bridge.app")
FIRST_RETRY_S = 30.0


def retry_delays(tries: int, first: float = FIRST_RETRY_S) -> list[float]:
    """Waits between `tries` attempts: 30 s doubling (6 tries = 30+60+120+240+480 s = 15.5 min)."""
    return [first * 2 ** i for i in range(max(0, tries - 1))]


class Bridge:
    def __init__(self, settings: Settings, http: httpx.AsyncClient,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self.s = settings
        self.http = http
        self.sleep = sleep
        self.clients = {x.name: WgerClient(http, settings.token_for(x)) for x in settings.people}
        self.tasks: set[asyncio.Task[None]] = set()

    async def startup_checks(self) -> None:
        """Config summary + one authenticated GET per person (status only)."""
        emit(log, logging.INFO, "config", dry_run=self.s.dry_run, people=[x.name for x in self.s.people],
             wger_url=self.s.wger_url, address_filter=bool(self.s.scale_address))
        if not self.s.people:
            emit(log, logging.WARNING, "config_no_people", hint="set SCALE_BRIDGE_PEOPLE in the host env file")
        pairs = overlaps(list(self.s.people))
        if pairs:
            # Names only, never the bands (health data). Not fatal: an ambiguous
            # weight is still written nowhere at runtime.
            emit(log, logging.WARNING, "config_bands_overlap", pairs=[list(x) for x in pairs],
                 action="a weight in two bands is written nowhere", hint="fix the bands in scale-bridge.env")
        if not self.s.scale_address:
            emit(log, logging.WARNING, "config_address_filter_off",
                 accepts=f"any device named {self.s.scale_name!r}",
                 hint="set SCALE_ADDRESS in scale-bridge.env before switching dry-run off")
        for x in self.s.people:
            if not self.s.token_for(x):
                emit(log, logging.ERROR, "token_check", person=x.name, result="missing", env=x.token_env)
                continue
            try:
                status = await self.clients[x.name].check()
            except WgerError as e:
                emit(log, logging.ERROR, "token_check", person=x.name, result="error", error=str(e))
                continue
            except Exception as e:  # noqa: BLE001 - one line per person, never a dead startup
                emit(log, logging.ERROR, "token_check", person=x.name, result="error", error=type(e).__name__)
                continue
            emit(log, logging.INFO if status == 200 else logging.ERROR, "token_check",
                 person=x.name, result="ok" if status == 200 else "rejected", status=status)

    async def handle(self, reading: Reading, scale_id: str, session_id: str) -> str:
        """Returns the decision: no_match | ambiguous | dry_run | no_token | queued."""
        att = attribute(list(self.s.people), reading.weight_kg)
        if att.person is None:
            emit(log, logging.WARNING, "attribution", session=session_id, result=att.outcome,
                 candidates=list(att.candidates), action="nothing written")
            return att.outcome
        person = att.person
        ext = str(external_id(scale_id, reading, person.name))
        emit(log, logging.INFO, "attribution", session=session_id, result="matched", person=person.name,
             impedance=reading.has_impedance)
        if self.s.dry_run:
            # The only line that carries the weight (rule: logs.py).
            emit(log, logging.INFO, "would_write", session=session_id, person=person.name,
                 weight_kg=reading.weight_kg, weight_lb=round(reading.weight_kg * LB_PER_KG, 1),
                 measured_at=reading.measured_at.isoformat(timespec="seconds"),
                 impedance_values=len(reading.impedances()), extended_frames=len(reading.df_raw),
                 categories=[r["category"] for r in plan(reading)], external_id=ext)
            return "dry_run"
        if not self.s.token_for(person):
            emit(log, logging.ERROR, "write_skipped", session=session_id, person=person.name,
                 reason="token missing", env=person.token_env)
            return "no_token"
        task = asyncio.create_task(self._write(person.name, reading, ext, session_id))
        self.tasks.add(task)
        task.add_done_callback(self._task_done)
        return "queued"

    def _task_done(self, task: asyncio.Task[None]) -> None:
        self.tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:   # _write catches everything; belt and braces
            emit(log, logging.ERROR, "write_crashed", stage="task", error=type(task.exception()).__name__)

    async def _write(self, person: str, reading: Reading, ext: str, session_id: str) -> None:
        """Every attempt that fails (WgerError, or anything unexpected) waits and retries."""
        client = self.clients[person]
        delays = retry_delays(self.s.write_retries)
        window = timedelta(seconds=self.s.replay_window)
        for attempt in range(1, self.s.write_retries + 1):
            try:
                counts = await client.write_reading(reading, uuid.UUID(ext), window)
            except WgerError as e:
                emit(log, logging.WARNING, "write_failed", session=session_id, person=person,
                     attempt=attempt, error=str(e))
            except Exception as e:  # noqa: BLE001 - type only: a message could carry a body
                emit(log, logging.ERROR, "write_crashed", session=session_id, person=person,
                     attempt=attempt, error=type(e).__name__)
            else:
                emit(log, logging.INFO, "wrote", session=session_id, person=person, external_id=ext, **counts)
                return
            if attempt < self.s.write_retries:
                await self.sleep(delays[attempt - 1])
        emit(log, logging.ERROR, "write_gave_up", session=session_id, person=person, external_id=ext,
             attempts=self.s.write_retries)

    async def drain(self) -> None:
        if self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=True)
