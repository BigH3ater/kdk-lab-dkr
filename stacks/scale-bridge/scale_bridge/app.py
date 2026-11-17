"""Reading -> person -> wger. Dry-run (the committed default) logs and posts nothing.

Engine to wger (SCALE_BRIDGE_WRITE_ENGINE, live only): `handle` queues the write at once
with a future for this session's engine result; `compute` (run by ble.py right after
`handle`) resolves it. The write task posts the weight and the impedances first and only
then waits for that future (bounded: engine timeout + ENGINE_WAIT_MARGIN_S), so the engine
never delays the weight; the engine rows go into the same attempt, retry and `wrote` line.
"""
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
from .engine import OK, Engine, EngineRun, build_case, log_run, profile_inputs, result_document
from .logs import emit
from .session import Reading
from .wger import LB_PER_KG, EngineRows, WgerClient, WgerError, engine_rows, external_id, plan

log = logging.getLogger("scale_bridge.app")
FIRST_RETRY_S = 30.0
ENGINE_WAIT_MARGIN_S = 30.0     # on top of the engine timeout, then the write goes on without it


class Decision(NamedTuple):
    outcome: str                # no_match | ambiguous | dry_run | no_token | queued
    person: str | None = None   # the matched person, else None


def rows_for(run: EngineRun | None, derived_male: bool | None = None) -> EngineRows:
    """The engine run -> what the write gets: rows only for `ok` with errorType PP_ERROR_TYPE_NONE.

    `derived_male`: None = no derived values (SCALE_BRIDGE_WRITE_DERIVED off), else whether
    the person's profile sex is male (the fitted-constant rows need it).
    """
    if run is None:
        return EngineRows("not_run")
    if run.result != "ok" or run.error_type != OK or not isinstance(run.output, dict):
        return EngineRows(run.result)
    return engine_rows(run.output.get("out"), derived_male)


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
        self.engine_waits: dict[str, asyncio.Future[EngineRows]] = {}    # session -> engine result

    async def startup_checks(self) -> None:
        """Config summary + one authenticated GET per person (status only)."""
        emit(log, logging.INFO, "config", version=__version__, dry_run=self.s.dry_run,
             write_heart_rate=self.s.write_heart_rate, write_engine=self.s.write_engine,
             write_derived=self.s.write_derived,
             people=[x.name for x in self.s.people],
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
        wait: asyncio.Future[EngineRows] | None = None
        if self.s.write_engine:
            wait = asyncio.get_running_loop().create_future()
            if not self.engine.enabled:
                wait.set_result(EngineRows("engine_off"))
            elif person.profile is None:
                wait.set_result(EngineRows("no_profile"))
            else:
                self.engine_waits[session_id] = wait          # compute() resolves it
        task = asyncio.create_task(self._write(person.name, reading, ext, session_id, wait))
        self.tasks.add(task)
        task.add_done_callback(self._task_done)
        return Decision("queued", person.name)

    async def compute(self, reading: Reading, decision: Decision | None, session_id: str) -> EngineRun | None:
        """Body-composition engine for a matched person with a profile and all four DF frames.

        Never raises. The result goes to the capture directory (`<capture>.engine.json`) and,
        with SCALE_BRIDGE_WRITE_ENGINE, to the waiting write task. None = not run.
        """
        run = None
        try:
            run = await self._compute(reading, decision, session_id)
            return run
        finally:
            wait = self.engine_waits.pop(session_id, None)
            if wait is not None and not wait.done():
                wait.set_result(rows_for(run, self._derived_male(decision)))

    def _derived_male(self, decision: Decision | None) -> bool | None:
        """None unless SCALE_BRIDGE_WRITE_DERIVED; else whether the matched person's profile is male."""
        if not self.s.write_derived or decision is None:
            return None
        person = next((x for x in self.s.people if x.name == decision.person), None)
        return person is not None and person.profile is not None and person.profile.sex == "male"

    async def _compute(self, reading: Reading, decision: Decision | None, session_id: str) -> EngineRun | None:
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

    async def _write(self, person: str, reading: Reading, ext: str, session_id: str,
                     wait: asyncio.Future[EngineRows] | None = None) -> None:
        """Every attempt that fails (WgerError, or anything unexpected) waits and retries."""
        client = self.clients[person]
        delays = retry_delays(self.s.write_retries)
        window = timedelta(seconds=self.s.replay_window)
        engine = None
        if wait is not None:
            bound = self.s.engine.timeout + ENGINE_WAIT_MARGIN_S

            async def engine() -> EngineRows:          # waits once; a retry reuses the answer
                if not wait.done():
                    try:
                        await asyncio.wait_for(asyncio.shield(wait), bound)
                    except TimeoutError:
                        self.engine_waits.pop(session_id, None)
                        wait.set_result(EngineRows("no_result"))
                return wait.result()
        for attempt in range(1, self.s.write_retries + 1):
            try:
                counts = await client.write_reading(reading, uuid.UUID(ext), window,
                                                    heart_rate=self.s.write_heart_rate, engine=engine)
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
