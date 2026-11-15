"""Reading -> person -> wger. Dry-run (the committed default) logs and posts nothing."""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import timedelta
from typing import Awaitable, Callable, NamedTuple

import httpx

from . import __version__
from .attribution import attribute, overlaps
from .config import Settings
from .engine import Engine, EngineRun, build_case, log_run, profile_inputs, result_document
from .logs import emit
from .session import Reading
from .wger import LB_PER_KG, WgerClient, WgerError, external_id, plan

log = logging.getLogger("scale_bridge.app")
FIRST_RETRY_S = 30.0


class Decision(NamedTuple):
    outcome: str                # no_match | ambiguous | dry_run | no_token | queued
    person: str | None = None   # the matched person, else None


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
        self.engine = Engine(settings.engine)

    async def startup_checks(self) -> None:
        """Config summary + one authenticated GET per person (status only)."""
        emit(log, logging.INFO, "config", version=__version__, dry_run=self.s.dry_run,
             write_heart_rate=self.s.write_heart_rate, people=[x.name for x in self.s.people],
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
        self.engine.startup([x.name for x in self.s.people if x.profile is not None])

    async def handle(self, reading: Reading, scale_id: str, session_id: str) -> Decision:
        """Decide and (live) queue the write. Decision.outcome: no_match | ambiguous | dry_run | no_token | queued."""
        att = attribute(list(self.s.people), reading.weight_kg)
        if att.person is None:
            emit(log, logging.WARNING, "attribution", session=session_id, result=att.outcome,
                 candidates=list(att.candidates), action="nothing written")
            return Decision(att.outcome)
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
                 heart_rate=reading.heart_rate_bpm is not None,      # captured or not; never the value
                 categories=[r["category"] for r in plan(reading, self.s.write_heart_rate)], external_id=ext)
            return Decision("dry_run", person.name)
        if not self.s.token_for(person):
            emit(log, logging.ERROR, "write_skipped", session=session_id, person=person.name,
                 reason="token missing", env=person.token_env)
            return Decision("no_token", person.name)
        task = asyncio.create_task(self._write(person.name, reading, ext, session_id))
        self.tasks.add(task)
        task.add_done_callback(self._task_done)
        return Decision("queued", person.name)

    async def compute(self, reading: Reading, decision: Decision | None, session_id: str) -> EngineRun | None:
        """Body-composition engine for a matched person with a profile and all four DF frames.

        Never raises and never touches the wger write: results only go to the capture
        directory (`<capture>.engine.json`) in this phase. None = not run.
        """
        try:
            if not self.engine.enabled or decision is None or decision.person is None:
                return None
            person = next((x for x in self.s.people if x.name == decision.person), None)
            if person is None or person.profile is None:
                emit(log, logging.INFO, "engine_skipped", session=session_id, person=decision.person,
                     reason="no_profile")
                return None
            case = build_case(reading, person.profile, self.s.engine)
            if case is None:
                emit(log, logging.INFO, "engine_skipped", session=session_id, person=person.name,
                     reason="df_incomplete", extended_frames=len(reading.df_raw))
                return None
            run = await self.engine.run(case)
            run.profile = profile_inputs(person.profile, reading.measured_at)
            run.document = result_document(self.engine, run, session_id, person.name, reading, __version__)
            log_run(run, session_id, person.name)
            return run
        except Exception as e:  # noqa: BLE001 - type only; the weigh-in never sees it
            emit(log, logging.ERROR, "engine", session=session_id, result="crashed", error=type(e).__name__)
            return None

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
                counts = await client.write_reading(reading, uuid.UUID(ext), window,
                                                    heart_rate=self.s.write_heart_rate)
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
