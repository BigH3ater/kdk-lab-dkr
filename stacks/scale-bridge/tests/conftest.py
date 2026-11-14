"""Fakes: a replaying GATT client (synthetic Body Pod 2 session) and an in-memory wger."""
from __future__ import annotations

import asyncio
import json
import pathlib
import re
import uuid
from datetime import datetime
from typing import Any, Callable
from urllib.parse import parse_qs

import httpx
import pytest

from scale_bridge import protocol as p

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "bodypod2-session-synthetic.jsonl"
TOKEN_A, TOKEN_B = "a" * 40, "b" * 40          # fake 40-hex wger keys


def load_fixture() -> list[dict[str, Any]]:
    return [json.loads(line) for line in FIXTURE.read_text().splitlines() if line.strip()]


def fixture_frames() -> list[tuple[float, bytes]]:
    """(seconds since the first frame, bytes) for every notification in the capture."""
    rows = [r for r in load_fixture() if r["kind"] == "frame"]
    t0 = datetime.fromisoformat(rows[0]["t"])
    return [((datetime.fromisoformat(r["t"]) - t0).total_seconds(), bytes.fromhex(r["hex"])) for r in rows]


@pytest.fixture
def frames() -> list[tuple[float, bytes]]:
    return fixture_frames()


class FakeClient:
    """Replays frames on the FFF4 callback, time compressed by `speed`."""

    def __init__(self, frames: list[tuple[float, bytes]], speed: float = 200.0,
                 gone: asyncio.Event | None = None, disconnect_at: float | None = None) -> None:
        self.frames, self.speed, self.gone, self.disconnect_at = frames, speed, gone, disconnect_at
        self.subscribed: list[str] = []
        self.writes: list[tuple[str, bytes, bool]] = []
        self.disconnected = False
        self.task: asyncio.Task[None] | None = None

    async def start_notify(self, char: str, cb: Callable[[Any, bytearray], None]) -> None:
        self.subscribed.append(char)
        if char == p.NOTIFY_UUID:
            self.task = asyncio.create_task(self._replay(cb))

    async def _replay(self, cb: Callable[[Any, bytearray], None]) -> None:
        last = 0.0
        for t, data in self.frames:
            if self.disconnect_at is not None and t >= self.disconnect_at:
                break
            await asyncio.sleep(max(0.0, (t - last) / self.speed))
            last = t
            if self.disconnected:
                return
            cb(None, bytearray(data))
        if self.disconnect_at is not None and self.gone is not None:
            self.gone.set()

    async def write_gatt_char(self, char: str, data: bytes, response: bool = False) -> None:
        self.writes.append((char, bytes(data), response))

    async def disconnect(self) -> None:
        self.disconnected = True
        if self.task:
            self.task.cancel()


class FakeWger:
    """Just enough of wger 2.7's measurement API, with its unique external_id rule.

    Failure knobs: `fail_next` (502s), `html_next` (200 with an HTML page, as a
    proxy or maintenance page would), `timeout_after` ({"METHOD path": n}: the
    next n such requests are processed server-side, then the client sees a
    ReadTimeout), `name_contains` (the category name filter matches substrings).
    List endpoints page with limit/offset like DRF's LimitOffsetPagination.
    """

    HTML = "<html><body>maintenance page MARKER-NOT-FOR-LOGS</body></html>"

    def __init__(self, tokens: dict[str, str], official: dict[str, bool] | None = None,
                 weight_unit: dict[str, str] | None = None, fail_next: int = 0, html_next: int = 0,
                 timeout_after: dict[str, int] | None = None, name_contains: bool = False) -> None:
        self.users = {v: k for k, v in tokens.items()}          # token -> user
        self.categories: list[dict[str, Any]] = []
        self.measurements: list[dict[str, Any]] = []
        self.weightentries: list[dict[str, Any]] = []
        self.weight_unit = weight_unit or {}
        self.requests: list[httpx.Request] = []
        self.fail_next = fail_next
        self.html_next = html_next
        self.timeout_after = dict(timeout_after or {})
        self.name_contains = name_contains
        for user, has in (official or {u: True for u in tokens}).items():
            if has:
                self._add_cat(user, "Body weight", self.weight_unit.get(user, "kg"), "body_weight", True)

    def _add_cat(self, user: str, name: str, unit: str, metric: str, official: bool) -> dict[str, Any]:
        c = {"id": str(uuid.uuid4()), "user": user, "name": name, "unit": unit,
             "metric_type": metric, "is_official": official}
        self.categories.append(c)
        return c

    @staticmethod
    def _page(rows: list[dict[str, Any]], q: dict[str, str] | None = None) -> httpx.Response:
        q = q or {}
        limit, offset = int(q.get("limit", 20)), int(q.get("offset", 0))
        page = rows[offset:offset + limit]
        nxt = f"https://wger.test/more?offset={offset + limit}" if offset + limit < len(rows) else None
        return httpx.Response(200, json={"count": len(rows), "next": nxt, "previous": None, "results": page})

    def handler(self, req: httpx.Request) -> httpx.Response:
        resp = self._handle(req)
        key = f"{req.method} {req.url.path.removeprefix('/api/v2/').rstrip('/')}"
        if self.timeout_after.get(key):
            self.timeout_after[key] -= 1
            raise httpx.ReadTimeout("timed out", request=req)     # wger did the work, the client never knew
        return resp

    def _handle(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        m = re.match(r"Token (\w+)$", req.headers.get("authorization", ""))
        user = self.users.get(m.group(1)) if m else None
        if user is None:
            return httpx.Response(401, json={"detail": "Invalid token."})
        if self.fail_next:
            self.fail_next -= 1
            return httpx.Response(502, text="bad gateway")
        if self.html_next:
            self.html_next -= 1
            return httpx.Response(200, text=self.HTML, headers={"content-type": "text/html"})
        path = req.url.path.removeprefix("/api/v2/").rstrip("/")
        q = {k: v[0] for k, v in parse_qs(req.url.query.decode()).items()}
        body = json.loads(req.content) if req.content else {}
        mine = [c for c in self.categories if c["user"] == user]
        if path == "userprofile" and req.method == "GET":
            return httpx.Response(200, json={"username": user, "weight_unit": self.weight_unit.get(user, "kg")})
        if path == "measurement-category" and req.method == "GET":
            rows = mine
            if "name" in q:
                rows = [c for c in rows if (q["name"].lower() in c["name"].lower() if self.name_contains
                                            else c["name"] == q["name"])]
            if "metric_type" in q:
                rows = [c for c in rows if c["metric_type"] == q["metric_type"]]
            if "is_official" in q:
                rows = [c for c in rows if c["is_official"] == (q["is_official"] == "true")]
            return self._page(rows, q)
        if path == "measurement-category" and req.method == "POST":
            return httpx.Response(201, json=self._add_cat(user, body["name"], body.get("unit", ""),
                                                          body.get("metric_type", "custom"), False))
        if path == "measurement" and req.method == "GET":
            ids = {c["id"] for c in mine}
            rows = [x for x in self.measurements if x["category"] in ids and x["category"] == q.get("category")]
            lo, hi = datetime.fromisoformat(q["date__gte"]), datetime.fromisoformat(q["date__lte"])
            return self._page([x for x in rows if lo <= datetime.fromisoformat(x["date"]) <= hi], q)
        if path == "measurement" and req.method == "POST":
            if body["category"] not in {c["id"] for c in mine}:
                return httpx.Response(400, json={"category": ["Invalid pk"]})
            for x in self.measurements:
                key = (body["category"], body["source"], body["external_id"])
                if (x["category"], x["source"], x["external_id"]) == key:
                    return httpx.Response(400, json={"non_field_errors": [
                        "The fields category, source, external_id must make a unique set."]})
            row = {"id": str(uuid.uuid4()), **body}
            self.measurements.append(row)
            return httpx.Response(201, json=row)
        if path == "weightentry" and req.method == "POST":
            cat = next((c for c in mine if c["metric_type"] == "body_weight"), None) or self._add_cat(
                user, "Body weight", self.weight_unit.get(user, "kg"), "body_weight", True)
            row = {"id": str(uuid.uuid4()), "category": cat["id"], "date": body["date"], "value": body["weight"],
                   "source": "user", "external_id": None,
                   "extra_data": {"unit": self.weight_unit.get(user, "kg")}}
            self.measurements.append(row)
            self.weightentries.append(body)
            return httpx.Response(201, json={"id": row["id"], "date": body["date"], "weight": body["weight"]})
        return httpx.Response(404, json={"detail": "not found"})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url="https://wger.test", transport=httpx.MockTransport(self.handler))

    def posts(self, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == "POST" and r.url.path.rstrip("/").endswith(path)]

    def values(self, user: str) -> dict[str, list[float]]:
        names = {c["id"]: c["name"] for c in self.categories if c["user"] == user}
        out: dict[str, list[float]] = {}
        for x in self.measurements:
            if x["category"] in names:
                out.setdefault(names[x["category"]], []).append(float(x["value"]))
        return out
