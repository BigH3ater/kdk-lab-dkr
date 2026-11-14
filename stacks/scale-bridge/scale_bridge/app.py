"""Reading -> person -> wger. Dry-run (the committed default) logs and posts nothing."""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Awaitable, Callable

import httpx

from .attribution import attribute
from .config import Settings
from .logs import emit
from .session import Reading
from .wger import WgerClient, WgerError, external_id, plan

log = logging.getLogger("scale_bridge.app")


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
             wger_url=self.s.wger_url)
        if not self.s.people:
            emit(log, logging.WARNING, "config_no_people", hint="set SCALE_BRIDGE_PEOPLE in the host env file")
        for x in self.s.people:
            if not self.s.token_for(x):
                emit(log, logging.ERROR, "token_check", person=x.name, result="missing", env=x.token_env)
                continue
            try:
                status = await self.clients[x.name].check()
            except WgerError as e:
                emit(log, logging.ERROR, "token_check", person=x.name, result="error", error=str(e))
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
                 weight_kg=reading.weight_kg, measured_at=reading.measured_at.isoformat(timespec="seconds"),
                 impedance_values=len(reading.impedances()), extended_frames=len(reading.df_raw),
                 categories=[r["category"] for r in plan(reading)], external_id=ext)
            return "dry_run"
        if not self.s.token_for(person):
            emit(log, logging.ERROR, "write_skipped", session=session_id, person=person.name,
                 reason="token missing", env=person.token_env)
            return "no_token"
        task = asyncio.create_task(self._write(person.name, reading, ext, session_id))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return "queued"

    async def _write(self, person: str, reading: Reading, ext: str, session_id: str) -> None:
        client = self.clients[person]
        delay = 30.0
        for attempt in range(1, self.s.write_retries + 1):
            try:
                counts = await client.write_reading(reading, uuid.UUID(ext))
            except WgerError as e:
                emit(log, logging.WARNING, "write_failed", session=session_id, person=person,
                     attempt=attempt, error=str(e))
                if attempt < self.s.write_retries:
                    await self.sleep(delay)
                    delay *= 2
                continue
            emit(log, logging.INFO, "wrote", session=session_id, person=person, external_id=ext, **counts)
            return
        emit(log, logging.ERROR, "write_gave_up", session=session_id, person=person, external_id=ext,
             attempts=self.s.write_retries)

    async def drain(self) -> None:
        if self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=True)
