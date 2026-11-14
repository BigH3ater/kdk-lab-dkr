"""wger 2.7 REST client: one reading -> body weight + ten raw impedance values.

API facts (wger 2.7 source, wger/measurements and wger/weight):
* body weight is a measurement in the user's *official* `body_weight` category
  (`measurement-category/?metric_type=body_weight&is_official=true`). A body
  weight entry may carry its own unit in `extra_data.unit` (kg or lb), so a kg
  value is correct even when the person's profile is in lb;
* that category is created by the server the first time anything writes a
  weight. If it does not exist yet, the legacy `weightentry/` endpoint is used
  once (it creates the category; it takes the weight in the profile's unit);
* custom categories are per user, created by name (`metric_type: custom`);
* `measurement.external_id` is unique per category + source, so the same
  reading posted twice lands once. There is no external_id filter, so the
  check is: list the category's entries within +-1 s of the reading, look for
  our external_id; a 400 naming the unique constraint also counts as present.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

import httpx

from .session import Reading

API = "/api/v2"
NAMESPACE = uuid.UUID("6f1f3c52-3a7e-4c1e-9f0a-5ad1e0b5ca1e")   # scale-bridge's own uuid5 namespace
IMPEDANCE_UNIT = "ohm"
LB_PER_KG = 2.20462262185
NOTE = "Body Pod 2 via scale-bridge"


class WgerError(RuntimeError):
    """A wger call failed; the message carries the status, never the token."""


def external_id(scale_id: str, reading: Reading, person: str) -> uuid.UUID:
    stamp = reading.measured_at.isoformat(timespec="milliseconds")
    return uuid.uuid5(NAMESPACE, f"{scale_id.upper()}|{stamp}|{person}")


def plan(reading: Reading) -> list[dict[str, Any]]:
    """What a write would post, in order: body weight, then the impedances."""
    rows: list[dict[str, Any]] = [{"category": "Body weight", "value": reading.weight_kg, "unit": "kg"}]
    rows += [{"category": name, "value": v, "unit": IMPEDANCE_UNIT} for name, v in reading.impedances()]
    return rows


def _iso(t: datetime) -> str:
    return t.isoformat(timespec="milliseconds")


class WgerClient:
    """One person's view of wger (their token). Caches category ids per instance."""

    def __init__(self, http: httpx.AsyncClient, token: str) -> None:
        self._http = http
        self._headers = {"Authorization": f"Token {token}", "Accept": "application/json"}
        self._categories: dict[str, str] = {}

    async def _req(self, method: str, path: str, **kw: Any) -> httpx.Response:
        try:
            r = await self._http.request(method, f"{API}/{path}", headers=self._headers, **kw)
        except httpx.HTTPError as e:
            raise WgerError(f"{method} {path.split('?')[0]}: {type(e).__name__}") from None
        return r

    async def _json(self, method: str, path: str, ok: tuple[int, ...] = (200,), **kw: Any) -> Any:
        r = await self._req(method, path, **kw)
        if r.status_code not in ok:
            raise WgerError(f"{method} {path}: HTTP {r.status_code}")
        return r.json()

    @staticmethod
    def _results(body: Any) -> list[dict[str, Any]]:
        if isinstance(body, dict) and "results" in body:
            return list(body["results"])
        return list(body) if isinstance(body, list) else [body]

    async def check(self) -> int:
        """Status code of an authenticated read (200 = the token works)."""
        return (await self._req("GET", "userprofile/")).status_code

    async def body_weight_category(self) -> str | None:
        body = await self._json("GET", "measurement-category/",
                                params={"metric_type": "body_weight", "is_official": "true", "limit": 10})
        rows = self._results(body)
        return str(rows[0]["id"]) if rows else None

    async def custom_category(self, name: str, unit: str) -> tuple[str, bool]:
        """(id, created) for the person's custom category `name`, creating it if needed."""
        if name in self._categories:
            return self._categories[name], False
        rows = [x for x in self._results(await self._json(
            "GET", "measurement-category/", params={"name": name, "limit": 10})) if x.get("name") == name]
        created = False
        if rows:
            cid = str(rows[0]["id"])
        else:
            body = await self._json("POST", "measurement-category/", ok=(201,),
                                    json={"name": name, "unit": unit, "metric_type": "custom"})
            cid, created = str(body["id"]), True
        self._categories[name] = cid
        return cid, created

    async def _present(self, category: str, when: datetime, ext: uuid.UUID, *, legacy_ok: bool) -> bool:
        params = {"category": category, "date__gte": _iso(when - timedelta(seconds=1)),
                  "date__lte": _iso(when + timedelta(seconds=1)), "limit": 50}
        for row in self._results(await self._json("GET", "measurement/", params=params)):
            rid = row.get("external_id")
            if rid and str(rid) == str(ext):
                return True
            if legacy_ok and not rid:
                return True      # our own weightentry fallback (it has no external_id)
        return False

    async def ensure_measurement(self, category: str, when: datetime, value: float, ext: uuid.UUID,
                                 extra: dict[str, Any], *, legacy_ok: bool = False) -> str:
        """'created' or 'exists'."""
        if await self._present(category, when, ext, legacy_ok=legacy_ok):
            return "exists"
        r = await self._req("POST", "measurement/", json={
            "category": category, "date": _iso(when), "value": round(value, 2), "notes": NOTE,
            "source": "user", "external_id": str(ext), "extra_data": extra})
        if r.status_code == 201:
            return "created"
        if r.status_code == 400 and "unique" in r.text.lower():
            return "exists"
        raise WgerError(f"POST measurement/: HTTP {r.status_code}")

    async def write_reading(self, reading: Reading, ext: uuid.UUID) -> dict[str, int]:
        """Write everything; safe to call again for the same reading."""
        counts = {"created": 0, "exists": 0, "categories_created": 0}
        extra: dict[str, Any] = {"unit": "kg", "device": "Body Pod 2"}
        if reading.df_raw:
            extra["df_raw"] = list(reading.df_raw)
        cat = await self.body_weight_category()
        if cat is None:
            # No weight was ever logged for this person: the legacy endpoint creates
            # the official category. It takes the profile's unit.
            prof = self._results(await self._json("GET", "userprofile/"))[0]
            unit = prof.get("weight_unit") or "kg"
            weight = reading.weight_kg * LB_PER_KG if unit == "lb" else reading.weight_kg
            await self._json("POST", "weightentry/", ok=(201,),
                             json={"date": _iso(reading.measured_at), "weight": round(weight, 2)})
            counts["created"] += 1
        else:
            counts[await self.ensure_measurement(cat, reading.measured_at, reading.weight_kg, ext, extra,
                                                 legacy_ok=True)] += 1
        for name, ohms in reading.impedances():
            cid, created = await self.custom_category(name, IMPEDANCE_UNIT)
            counts["categories_created"] += int(created)
            counts[await self.ensure_measurement(cid, reading.measured_at, ohms, ext,
                                                 {"unit": IMPEDANCE_UNIT, "device": "Body Pod 2"})] += 1
        return counts
