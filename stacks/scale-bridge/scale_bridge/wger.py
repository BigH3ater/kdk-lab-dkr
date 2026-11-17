"""wger 2.7 REST client: one reading -> body weight + ten raw impedance values (+ engine metrics).

API facts (wger 2.7 source, wger/measurements and wger/weight):
* body weight is a measurement in the user's *official* `body_weight` category
  (`measurement-category/?metric_type=body_weight&is_official=true`). A body
  weight entry may carry its own unit in `extra_data.unit` (kg or lb), so a kg
  value is correct even when the person's profile is in lb;
* that category is created by the server the first time anything writes a
  weight. If it does not exist yet, the legacy `weightentry/` endpoint is used
  once: it runs `Category.get_or_create_body_weight` and saves an ordinary
  Measurement (DateTimeField, the posted time as given, the value in the
  profile's unit, `extra_data.unit` = that unit, no external_id) in the same
  request, so a retry finds the category and the entry;
* custom categories are per user, created by name (`metric_type: custom`). The lookup
  only reuses a `custom` one, never an official or Apple Health category (`Body fat`,
  `Heart rate`, ...); a custom category's unit is a label, the wger apps never convert it;
* `measurement.external_id` is unique per category + source (a POST that
  repeats one is a 400 naming the unique set). There is no external_id filter;
  `measurement/` filters on category and date (gt/gte/lt/lte).

Replay rule (the scale re-sends the settled weight on a reconnect, and the
bridge keeps no state across a restart): a reading is the same weigh-in as a
body-weight entry of the same person with the same weight (to 5 g) dated within
REPLAY_WINDOW of it; it is then written at that entry's time, and each
impedance (heart-rate, engine) category counts as done when it has an entry within 1 s of that time.
The external_id (DF bytes when the scale sent all four DF frames, else lock
time) is a second net: the unique constraint turns a repeat into "exists".

Every malformed or unexpected response is a WgerError (method, path, kind of
problem; never the body, never the token), so the caller's retry handles it.
"""
from __future__ import annotations

import asyncio
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, NamedTuple

import httpx

from .session import Reading

API = "/api/v2"
NAMESPACE = uuid.UUID("6f1f3c52-3a7e-4c1e-9f0a-5ad1e0b5ca1e")   # scale-bridge's own uuid5 namespace
IMPEDANCE_UNIT = "ohm"
HEART_RATE_CATEGORY = "Heart rate (scale)"   # only with SCALE_BRIDGE_WRITE_HEART_RATE (one-capture confirmation)
HEART_RATE_UNIT = "bpm"
LB_PER_KG = 2.20462262185
NOTE = "Body Pod 2 via scale-bridge"
REPLAY_WINDOW = timedelta(minutes=10)
MASS = "mass"                   # an engine metric in kg or lb: see _write_engine
# Body-composition engine -> custom category (SCALE_BRIDGE_WRITE_ENGINE). The getters are
# the vendor result model's (com.lefu.ppcalculate.PPBodyFatModel, ppcalculatekit 4.6.28),
# which engine/src/kdk/lefu/Main.java scalars() exports by name into the result's `out`.
# Not written (yet): getPpBodyAge (metabolic age, not comparable to the app), the balance
# levels, subcutaneous and per-limb fat, mineral, skeletal %.
ENGINE_METRICS: tuple[tuple[str, str, str], ...] = (
    ("Body fat (scale)", "getPpFat", "%"),
    ("Skeletal muscle (scale)", "getPpBodySkeletalKg", MASS),
    ("Body water (scale)", "getPpWaterPercentage", "%"),
    ("Visceral fat (scale)", "getPpVisceralFat", "index"),
    ("BMR (scale)", "getPpBMR", "kcal"),
    ("Bone mineral (scale)", "getPpBoneKg", MASS),
    ("ECW (scale)", "getPpWaterECWKg", MASS),
    ("ICW (scale)", "getPpWaterICWKg", MASS),
    ("Lean mass (scale)", "getPpLoseFatWeightKg", MASS),
    ("Body cell mass (scale)", "getPpCellMassKg", MASS),
    ("BMI (scale)", "getPpBMI", "index"),
    ("Lean right arm (scale)", "getPpMuscleKgRightArm", MASS),
    ("Lean left arm (scale)", "getPpMuscleKgLeftArm", MASS),
    ("Lean trunk (scale)", "getPpMuscleKgTrunk", MASS),
    ("Lean right leg (scale)", "getPpMuscleKgRightLeg", MASS),
    ("Lean left leg (scale)", "getPpMuscleKgLeftLeg", MASS),
)
SAME_WEIGHT_KG = 0.005          # below the scale's 10 g step; covers the lb round trip
SAME_TIME = timedelta(seconds=1)
PAGE = 100
MAX_PAGES = 20


class WgerError(RuntimeError):
    """A wger call failed; the message carries the status, never the token."""


class WgerRejected(WgerError):
    """wger refused a measurement with a 400 that is not the unique constraint (permanent)."""


class EngineRows(NamedTuple):
    """What the engine gives a write. `result` is a reason code (safe to log); `rows` is health data."""
    result: str                                          # ok, or why there are no rows
    rows: tuple[tuple[str, str, float, str], ...] = ()   # (category, getter, kg or value, unit or MASS)
    skipped: int = 0                                     # metrics missing, non-finite, zero or negative


def engine_rows(out: Any) -> EngineRows:
    """The engine's `out` object (output.out) -> one row per ENGINE_METRICS entry with a usable value."""
    rows, skipped = [], 0
    for name, getter, unit in ENGINE_METRICS:
        v = out.get(getter) if isinstance(out, dict) else None
        try:
            f = float(v) if v is not None and not isinstance(v, bool) else math.nan
        except (TypeError, ValueError):                  # e.g. "NaN" strings are fine, "abc" is not
            f = math.nan
        if math.isfinite(f) and f > 0:
            rows.append((name, getter, f, unit))
        else:
            skipped += 1
    return EngineRows("ok", tuple(rows), skipped)


def external_id(scale_id: str, reading: Reading, person: str) -> uuid.UUID:
    """DF bytes identify a measurement when all four frames came; else the lock time."""
    if len(reading.df_raw) >= 4:
        key = "df:" + ",".join(reading.df_raw)
    else:
        key = "t:" + reading.measured_at.isoformat(timespec="milliseconds")
    return uuid.uuid5(NAMESPACE, f"{scale_id.upper()}|{person}|{reading.weight_cg}|{key}")


def custom_rows(reading: Reading, heart_rate: bool = False) -> list[tuple[str, float, str]]:
    """(category, value, unit) for the custom categories: impedances, then the heart rate if enabled."""
    rows = [(name, v, IMPEDANCE_UNIT) for name, v in reading.impedances()]
    if heart_rate and reading.heart_rate_bpm:
        rows.append((HEART_RATE_CATEGORY, float(reading.heart_rate_bpm), HEART_RATE_UNIT))
    return rows


def plan(reading: Reading, heart_rate: bool = False) -> list[dict[str, Any]]:
    """What a write would post, in order: body weight, the impedances, the heart rate (if enabled)."""
    rows: list[dict[str, Any]] = [{"category": "Body weight", "value": reading.weight_kg, "unit": "kg"}]
    rows += [{"category": n, "value": v, "unit": u} for n, v, u in custom_rows(reading, heart_rate)]
    return rows


def _iso(t: datetime) -> str:
    return t.isoformat(timespec="milliseconds")


def _where(method: str, path: str) -> str:
    return f"{method} {path.split('?')[0]}"


def _field(row: dict[str, Any], key: str, where: str) -> Any:
    v = row.get(key)
    if v is None or v == "":
        raise WgerError(f"{where}: unexpected body (no {key})")
    return v


def _row_date(row: dict[str, Any], where: str) -> datetime:
    try:
        d = datetime.fromisoformat(str(_field(row, "date", where)))
    except ValueError:
        raise WgerError(f"{where}: unexpected body (date is not ISO 8601)") from None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _row_kg(row: dict[str, Any], default_unit: str, where: str) -> float:
    try:
        v = float(_field(row, "value", where))
    except (TypeError, ValueError):
        raise WgerError(f"{where}: unexpected body (value is not a number)") from None
    extra = row.get("extra_data")
    unit = (extra.get("unit") if isinstance(extra, dict) else None) or default_unit
    return v / LB_PER_KG if unit == "lb" else v


class WgerClient:
    """One person's view of wger (their token). Caches category ids per instance.

    write_reading holds a per-person lock, so two writes for the same person
    (a retrying write and a replay of it) never interleave, and category
    creation is serialised by its own lock.
    """

    def __init__(self, http: httpx.AsyncClient, token: str) -> None:
        self._http = http
        self._headers = {"Authorization": f"Token {token}", "Accept": "application/json"}
        self._categories: dict[str, tuple[str, str]] = {}     # name -> (id, unit)
        self._write_lock = asyncio.Lock()
        self._cat_lock = asyncio.Lock()

    async def _req(self, method: str, path: str, **kw: Any) -> httpx.Response:
        try:
            r = await self._http.request(method, f"{API}/{path}", headers=self._headers, **kw)
        except httpx.HTTPError as e:
            raise WgerError(f"{_where(method, path)}: {type(e).__name__}") from None
        return r

    async def _json(self, method: str, path: str, ok: tuple[int, ...] = (200,), **kw: Any) -> Any:
        r = await self._req(method, path, **kw)
        if r.status_code not in ok:
            raise WgerError(f"{_where(method, path)}: HTTP {r.status_code}")
        try:
            return r.json()
        except ValueError:          # JSONDecodeError, UnicodeDecodeError
            raise WgerError(f"{_where(method, path)}: HTTP {r.status_code}, body is not JSON") from None

    @staticmethod
    def _rows(body: Any, where: str) -> list[dict[str, Any]]:
        if isinstance(body, dict) and isinstance(body.get("results"), list):
            rows = body["results"]
        elif isinstance(body, list):
            rows = body
        else:
            raise WgerError(f"{where}: unexpected body (no results list)")
        if not all(isinstance(x, dict) for x in rows):
            raise WgerError(f"{where}: unexpected body (a row is not an object)")
        return rows

    async def _list(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Every row of a list endpoint, paging with limit/offset."""
        out: list[dict[str, Any]] = []
        for _ in range(MAX_PAGES):
            body = await self._json("GET", path, params={**params, "limit": PAGE, "offset": len(out)})
            rows = self._rows(body, _where("GET", path))
            out += rows
            if not rows or not isinstance(body, dict) or not body.get("next"):
                return out
        raise WgerError(f"{_where('GET', path)}: more than {MAX_PAGES} pages")

    async def check(self) -> int:
        """Status code of an authenticated read (200 = the token works, and the body is JSON)."""
        r = await self._req("GET", "userprofile/")
        if r.status_code == 200:
            try:
                r.json()
            except ValueError:
                raise WgerError("GET userprofile/: HTTP 200, body is not JSON") from None
        return r.status_code

    async def profile(self) -> dict[str, Any]:
        """The person's profile (wger returns the object itself; a one-row page is accepted too)."""
        body = await self._json("GET", "userprofile/")
        if isinstance(body, dict) and "results" not in body:
            return body
        rows = self._rows(body, "GET userprofile/")
        if not rows:
            raise WgerError("GET userprofile/: unexpected body (no profile)")
        return rows[0]

    async def body_weight_category(self) -> tuple[str, str] | None:
        """(id, unit) of the official body-weight category, or None."""
        where = "GET measurement-category/"
        rows = [x for x in await self._list("measurement-category/",
                                            {"metric_type": "body_weight", "is_official": "true"})
                if x.get("metric_type", "body_weight") == "body_weight" and x.get("is_official", True)]
        if not rows:
            return None
        return str(_field(rows[0], "id", where)), str(rows[0].get("unit") or "kg")

    async def custom_category(self, name: str, unit: str) -> tuple[str, str, bool]:
        """(id, unit, created) for the person's custom category `name`, creating it in `unit` if needed.

        The lookup pages through every match and compares the name exactly here
        (the server filter is not relied on for that) and only takes a `custom`
        category; creation is serialised. The returned unit is the category's own.
        """
        async with self._cat_lock:
            if name in self._categories:
                cid, have = self._categories[name]
                return cid, have, False
            rows = [x for x in await self._list("measurement-category/", {"name": name})
                    if x.get("name") == name and x.get("metric_type", "custom") == "custom"]
            created = False
            if rows:
                cid, unit = str(_field(rows[0], "id", "GET measurement-category/")), str(rows[0].get("unit") or "")
            else:
                body = await self._json("POST", "measurement-category/", ok=(201,),
                                        json={"name": name, "unit": unit, "metric_type": "custom"})
                if not isinstance(body, dict):
                    raise WgerError("POST measurement-category/: unexpected body (not an object)")
                cid, created = str(_field(body, "id", "POST measurement-category/")), True
            self._categories[name] = (cid, unit)
            return cid, unit, created

    async def _entries(self, category: str, lo: datetime, hi: datetime) -> list[dict[str, Any]]:
        return await self._list("measurement/", {"category": category, "date__gte": _iso(lo),
                                                 "date__lte": _iso(hi)})

    async def find_weight(self, category: str, unit: str, reading: Reading,
                          window: timedelta = REPLAY_WINDOW) -> datetime | None:
        """Time of the closest entry with this reading's weight within `window`, or None."""
        where = "GET measurement/"
        t = reading.measured_at
        hits = []
        for row in await self._entries(category, t - window, t + window):
            if abs(_row_kg(row, unit, where) - reading.weight_kg) < SAME_WEIGHT_KG:
                d = _row_date(row, where)
                hits.append((abs(d - t), d))
        return min(hits)[1] if hits else None

    async def _post(self, category: str, when: datetime, value: float, ext: uuid.UUID,
                    extra: dict[str, Any]) -> str:
        r = await self._req("POST", "measurement/", json={
            "category": category, "date": _iso(when), "value": round(value, 2), "notes": NOTE,
            "source": "user", "external_id": str(ext), "extra_data": extra})
        if r.status_code == 201:
            return "created"
        if r.status_code == 400 and "unique" in r.text.lower():
            return "exists"
        if r.status_code == 400:
            raise WgerRejected("POST measurement/: HTTP 400")
        raise WgerError(f"POST measurement/: HTTP {r.status_code}")

    async def ensure_measurement(self, category: str, when: datetime, value: float, ext: uuid.UUID,
                                 extra: dict[str, Any]) -> str:
        """'created' or 'exists' (an entry within 1 s of `when`, or the unique constraint)."""
        if await self._entries(category, when - SAME_TIME, when + SAME_TIME):
            return "exists"
        return await self._post(category, when, value, ext, extra)

    async def write_reading(self, reading: Reading, ext: uuid.UUID,
                            window: timedelta = REPLAY_WINDOW, heart_rate: bool = False,
                            engine: Callable[[], Awaitable[EngineRows]] | None = None) -> dict[str, Any]:
        """Write everything; safe to call again for the same reading, or for a replay of it.

        `engine` (SCALE_BRIDGE_WRITE_ENGINE) is awaited only after the weight and the
        custom rows are written, so a slow or failed engine never holds them back.
        """
        async with self._write_lock:
            counts: dict[str, Any] = {"created": 0, "exists": 0, "categories_created": 0}
            extra: dict[str, Any] = {"unit": "kg", "device": "Body Pod 2"}
            if reading.df_raw:
                extra["df_raw"] = list(reading.df_raw)
            when = reading.measured_at
            found = await self.body_weight_category()
            if found is None:
                # No weight was ever logged for this person: the legacy endpoint creates
                # the official category. It takes the profile's unit.
                unit = "lb" if (await self.profile()).get("weight_unit") == "lb" else "kg"
                weight = reading.weight_kg * LB_PER_KG if unit == "lb" else reading.weight_kg
                r = await self._req("POST", "weightentry/",
                                    json={"date": _iso(when), "weight": round(weight, 2)})
                if r.status_code != 201:
                    raise WgerError(f"POST weightentry/: HTTP {r.status_code}")
                counts["created"] += 1
            else:
                cat, unit = found
                anchor = await self.find_weight(cat, unit, reading, window)
                if anchor is not None:
                    when = anchor                   # this weigh-in is already there
                    counts["exists"] += 1
                else:
                    counts[await self._post(cat, when, reading.weight_kg, ext, extra)] += 1
            for name, value, unit in custom_rows(reading, heart_rate):
                cid, _, created = await self.custom_category(name, unit)
                counts["categories_created"] += int(created)
                counts[await self.ensure_measurement(cid, when, value, ext,
                                                     {"unit": unit, "device": "Body Pod 2"})] += 1
            if engine is not None:
                await self._write_engine(await engine(), when, ext, counts)
            return counts

    async def _write_engine(self, er: EngineRows, when: datetime, ext: uuid.UUID,
                            counts: dict[str, Any]) -> None:
        """Engine rows at the weight's time, same idempotence as the impedance rows.

        A mass category is created in the profile's weight unit; an existing one keeps its
        own unit (kg or lb) and the value is converted to it, so a later profile change never
        mixes units in one category. A 400 for one row (wger refused that value: permanent)
        is counted as rejected and the others go on; any other failure raises (the retry).
        """
        counts.update(engine_result=er.result, engine_created=0, engine_exists=0,
                      engine_skipped=er.skipped, engine_rejected=0, engine_categories_created=0)
        profile_unit: str | None = None
        for name, getter, value, unit in er.rows:
            if unit == MASS:
                if name not in self._categories and profile_unit is None:
                    profile_unit = "lb" if (await self.profile()).get("weight_unit") == "lb" else "kg"
                cid, have, created = await self.custom_category(name, profile_unit or "kg")
                counts["engine_categories_created"] += int(created)
                if have not in ("kg", "lb"):
                    counts["engine_skipped"] += 1            # a mass category in some other unit
                    continue
                unit, value = have, (value * LB_PER_KG if have == "lb" else value)
            else:
                cid, _, created = await self.custom_category(name, unit)
                counts["engine_categories_created"] += int(created)
            try:
                counts["engine_" + await self.ensure_measurement(
                    cid, when, value, ext, {"unit": unit, "device": "Body Pod 2", "engine_field": getter})] += 1
            except WgerRejected:
                counts["engine_rejected"] += 1
