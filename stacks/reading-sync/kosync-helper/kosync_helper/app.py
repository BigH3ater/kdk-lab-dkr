"""kosync-helper: the KOReader Progress sync server the tablet talks to.

PDF documents (hash found in the ABS ebooks library map) are answered here against
Audiobookshelf; everything else goes unchanged to BookBridge's KOSync port. Only the
six kosync routes exist; any other path is a 404 and never reaches BookBridge.

Auth is BookBridge's: before a PDF read or write, the device's x-auth-user/x-auth-key
are checked with BookBridge GET /users/auth (its per-address failure limit applies),
and a success is cached for AUTH_CACHE_SECONDS. Only KOSYNC_USER (jmack) may use the
PDF path, because the ABS key acts as that user.

Logs carry the event type, item id and status only: never keys, users or hashes.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
from collections import OrderedDict

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from . import progress as prog
from .abs_client import AbsClient, AbsError
from .config import Settings
from .library import PdfLibrary

log = logging.getLogger("kosync_helper")

DOC_RE = re.compile(r"^[0-9a-fA-F]{32}$")
USER_RE = re.compile(r"^[A-Za-z0-9._@+-]{1,64}$")
KEY_RE = re.compile(r"^[\x21-\x7e]{1,256}$")
KOSYNC_JSON = "application/json"
FORWARD_HEADERS = ("accept", "content-type", "x-auth-user", "x-auth-key", "user-agent")
ABS_DEVICE = "Audiobookshelf"
ABS_DEVICE_ID = "audiobookshelf"


class JsonFormatter(logging.Formatter):
    FIELDS = ("event", "item", "status", "route", "count")

    def format(self, record: logging.LogRecord) -> str:
        out = {"ts": int(record.created), "level": record.levelname.lower()}
        for f in self.FIELDS:
            v = getattr(record, f, None)
            if v is not None:
                out[f] = v
        out.setdefault("event", record.getMessage())
        return json.dumps(out)


def event(name: str, level: int = logging.INFO, **fields) -> None:
    log.log(level, name, extra={"event": name, **fields})


class BodyTooLarge(Exception):
    pass


async def read_body(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None:
        if not declared.isdigit() or int(declared) > limit:
            raise BodyTooLarge()
    chunks, total = [], 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise BodyTooLarge()
        chunks.append(chunk)
    return b"".join(chunks)


def kosync_error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"message": message}, status_code=status)


class Helper:
    def __init__(self, settings: Settings, abs_client: AbsClient, bb_client: httpx.AsyncClient,
                 library: PdfLibrary, clock=time.time):
        self.s = settings
        self.abs = abs_client
        self.bb = bb_client
        self.library = library
        self.clock = clock
        self._auth_ok: dict[tuple[str, str], float] = {}
        # doc hash -> (page, device, device_id) of this helper's last PDF write
        self._last_push: OrderedDict[str, tuple[int, str, str]] = OrderedDict()
        self.started = clock()
        self.last_refresh_ok: float | None = None
        self.last_refresh_error = False

    # ---------------- PDF map ----------------
    async def refresh(self) -> None:
        try:
            items = await self.abs.list_pdf_items(self.s.abs_library_id)
            count = await asyncio.to_thread(self.library.rebuild, items)
            self.last_refresh_ok = self.clock()
            self.last_refresh_error = False
            event("pdf_map_refreshed", count=count)
        except Exception:  # keep the loop alive; the map keeps its last good state
            self.last_refresh_error = True
            event("pdf_map_refresh_failed", logging.WARNING)

    async def refresh_loop(self) -> None:
        while True:
            await self.refresh()
            await asyncio.sleep(self.s.refresh_seconds)

    def health(self) -> tuple[int, dict]:
        now = self.clock()
        body = {"pdfs": len(self.library), "map_loaded": self.last_refresh_ok is not None}
        stale_after = 2 * self.s.refresh_seconds + 300
        if self.last_refresh_ok is None:
            ok = now - self.started < 300 or not self.last_refresh_error
        else:
            ok = now - self.last_refresh_ok < stale_after
        body["status"] = "ok" if ok else "degraded"
        return (200 if ok else 503), body

    # ---------------- auth ----------------
    def _cache_key(self, user: str, key: str) -> tuple[str, str]:
        # In-memory only, entries expire after auth_cache_seconds. The kosync
        # key is already KOReader's digest of the password, so it is kept as
        # given rather than re-hashed (a fast hash of a credential is what
        # CodeQL py/weak-sensitive-data-hashing flags).
        return (user.lower(), key)

    async def authenticate(self, request: Request) -> Response | None:
        """None when the device may use the PDF path, else the response to send."""
        user = request.headers.get("x-auth-user", "")
        key = request.headers.get("x-auth-key", "")
        if not USER_RE.match(user) or not KEY_RE.match(key):
            return kosync_error(401, "Unauthorized")
        ck = self._cache_key(user, key)
        now = self.clock()
        exp = self._auth_ok.get(ck)
        if exp and exp > now:
            return None
        try:
            r = await self.bb.get("/users/auth", headers=self._forward_headers(request))
        except httpx.TimeoutException:
            event("auth_timeout", logging.WARNING, status=504)
            return kosync_error(504, "Sync server timeout")
        except httpx.HTTPError:
            event("auth_unreachable", logging.WARNING, status=502)
            return kosync_error(502, "Sync server unavailable")
        if r.status_code != 200:
            event("auth_refused", status=r.status_code)
            status = r.status_code if r.status_code in (401, 429) else 401
            return kosync_error(status, "Unauthorized" if status == 401 else "Too many authentication attempts")
        self._auth_ok = {k: v for k, v in self._auth_ok.items() if v > now}
        self._auth_ok[ck] = now + self.s.auth_cache_seconds
        return None

    def is_pdf_user(self, request: Request) -> bool:
        return request.headers.get("x-auth-user", "").lower() == self.s.kosync_user.lower()

    # ---------------- pass-through ----------------
    def _forward_headers(self, request: Request) -> dict[str, str]:
        headers = {h: request.headers[h] for h in FORWARD_HEADERS if h in request.headers}
        # Traefik (websecure) replaces any client-sent X-Forwarded-For, so the chain is
        # "<tablet>"; append Traefik's address so BookBridge (KOSYNC_TRUSTED_PROXIES =
        # Traefik + this network) rate-limits per tablet, not per proxy.
        chain = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
        if request.client and request.client.host:
            chain.append(request.client.host)
        if chain:
            headers["x-forwarded-for"] = ", ".join(chain[-5:])
        return headers

    async def forward(self, request: Request, route: str, path: str, body: bytes | None = None) -> Response:
        try:
            r = await self.bb.request(request.method, path, headers=self._forward_headers(request), content=body)
        except httpx.TimeoutException:
            event("passthrough_timeout", logging.WARNING, route=route, status=504)
            return kosync_error(504, "Sync server timeout")
        except httpx.HTTPError:
            event("passthrough_unreachable", logging.WARNING, route=route, status=502)
            return kosync_error(502, "Sync server unavailable")
        event("passthrough", route=route, status=r.status_code)
        media = r.headers.get("content-type", KOSYNC_JSON).split(";")[0] or KOSYNC_JSON
        return Response(content=r.content, status_code=r.status_code, media_type=media)

    # ---------------- PDF progress ----------------
    def _remember(self, doc: str, page: int, device: str, device_id: str) -> None:
        self._last_push[doc] = (page, device, device_id)
        self._last_push.move_to_end(doc)
        while len(self._last_push) > 2000:
            self._last_push.popitem(last=False)

    async def put_pdf(self, doc: str, entry, data: dict) -> Response:
        try:
            page = prog.parse_page(data.get("progress"))
            percentage = prog.parse_percentage(data.get("percentage"))
        except prog.InvalidInput:
            event("pdf_put_invalid", item=entry.item_id, status=400)
            return kosync_error(400, "Invalid progress")
        device = data.get("device") if isinstance(data.get("device"), str) else ""
        device_id = data.get("device_id") if isinstance(data.get("device_id"), str) else ""
        pages = entry.pages or prog.pages_from_percentage(page, percentage)
        payload, clamped = prog.kosync_to_abs(page, pages)
        try:
            await self.abs.patch_progress(entry.item_id, payload)
        except httpx.TimeoutException:
            event("pdf_put_timeout", logging.WARNING, item=entry.item_id, status=504)
            return kosync_error(504, "Audiobookshelf timeout")
        except (AbsError, httpx.HTTPError):
            event("pdf_put_failed", logging.WARNING, item=entry.item_id, status=502)
            return kosync_error(502, "Audiobookshelf unavailable")
        self._remember(doc, int(payload["ebookLocation"]), device[:128], device_id[:128])
        event("pdf_put_finished" if payload.get("isFinished") else ("pdf_put_clamped" if clamped else "pdf_put"),
              item=entry.item_id, status=200)
        return JSONResponse({"document": doc, "timestamp": int(self.clock())})

    async def get_pdf(self, doc: str, entry) -> Response:
        try:
            abs_progress = await self.abs.get_progress(entry.item_id)
        except httpx.TimeoutException:
            event("pdf_get_timeout", logging.WARNING, item=entry.item_id, status=504)
            return kosync_error(504, "Audiobookshelf timeout")
        except (AbsError, httpx.HTTPError):
            event("pdf_get_failed", logging.WARNING, item=entry.item_id, status=502)
            return kosync_error(502, "Audiobookshelf unavailable")
        fields = prog.abs_to_kosync(abs_progress, entry.pages)
        if fields is None:
            event("pdf_get_empty", item=entry.item_id, status=200)
            return JSONResponse({})  # KOReader: "No progress found for this document."
        device, device_id = ABS_DEVICE, ABS_DEVICE_ID
        last = self._last_push.get(doc)
        if last and str(last[0]) == fields["progress"]:
            # ABS still holds the page this tablet wrote: report it as the tablet's own,
            # so KOReader does not offer to "sync" to where it already is.
            device, device_id = last[1] or ABS_DEVICE, last[2] or ABS_DEVICE_ID
        event("pdf_get", item=entry.item_id, status=200)
        return JSONResponse({"document": doc, **fields, "device": device, "device_id": device_id})


def create_app(settings: Settings, abs_transport: httpx.AsyncBaseTransport | None = None,
               bb_transport: httpx.AsyncBaseTransport | None = None, start_refresh: bool = True,
               clock=time.time) -> FastAPI:
    abs_client = AbsClient(settings.abs_url, settings.abs_api_key, settings.upstream_timeout, abs_transport)
    bb_client = httpx.AsyncClient(base_url=settings.bookbridge_url, timeout=httpx.Timeout(settings.upstream_timeout),
                                  follow_redirects=False, transport=bb_transport)
    helper = Helper(settings, abs_client, bb_client, PdfLibrary(settings.ebooks_root), clock=clock)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(helper.refresh_loop()) if start_refresh else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await abs_client.aclose()
            await bb_client.aclose()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.helper = helper

    @app.get("/health")
    async def health():
        status, body = helper.health()
        return JSONResponse(body, status_code=status)

    @app.get("/healthcheck")
    async def healthcheck(request: Request):
        return await helper.forward(request, "healthcheck", "/healthcheck")

    @app.get("/users/auth")
    async def users_auth(request: Request):
        return await helper.forward(request, "users_auth", "/users/auth")

    @app.post("/users/create")
    async def users_create(request: Request):
        try:
            body = await read_body(request, settings.max_body_bytes)
        except BodyTooLarge:
            return kosync_error(413, "Request too large")
        return await helper.forward(request, "users_create", "/users/create", body)

    @app.post("/users/login")
    async def users_login(request: Request):
        try:
            body = await read_body(request, settings.max_body_bytes)
        except BodyTooLarge:
            return kosync_error(413, "Request too large")
        return await helper.forward(request, "users_login", "/users/login", body)

    @app.put("/syncs/progress")
    async def put_progress(request: Request):
        try:
            body = await read_body(request, settings.max_body_bytes)
        except BodyTooLarge:
            return kosync_error(413, "Request too large")
        try:
            data = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return kosync_error(400, "Invalid JSON")
        if not isinstance(data, dict):
            return kosync_error(400, "Invalid JSON")
        doc = data.get("document")
        if not isinstance(doc, str) or not DOC_RE.match(doc):
            return kosync_error(400, "Invalid document")
        entry = helper.library.lookup(doc)
        if entry is None or not helper.is_pdf_user(request):
            return await helper.forward(request, "put_progress", "/syncs/progress", body)
        denied = await helper.authenticate(request)
        if denied is not None:
            return denied
        return await helper.put_pdf(doc.lower(), entry, data)

    @app.get("/syncs/progress/{doc}")
    async def get_progress(doc: str, request: Request):
        if not DOC_RE.match(doc):
            return kosync_error(400, "Invalid document")
        entry = helper.library.lookup(doc)
        if entry is None or not helper.is_pdf_user(request):
            return await helper.forward(request, "get_progress", f"/syncs/progress/{doc}")
        denied = await helper.authenticate(request)
        if denied is not None:
            return denied
        return await helper.get_pdf(doc.lower(), entry)

    return app
