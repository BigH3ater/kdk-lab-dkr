"""The BookBridge web UI calls the helper makes to create an EPUB ebook-only link.

BookBridge (7.12.0, 7.13.0) has no API token for mappings (ADMIN_API_TOKEN only manages
reader accounts), so the helper signs in like a browser. Checked in the 7.12.0 and v7.13.0
source (src/web_server.py):

  POST /login              form username, password. 302 on success, 401 bad login,
                           429 after 5 failures per (username, address) in 60 s.
  GET  /suggestions?tab=search
                           any signed-in HTML page that answers 200; the after_request hook
                           (inject_csrf_script) injects the per-session CSRF token
                           (`var t = "<token>"`). This is the Discover > Search tab, the old
                           Add Book page.
                           The route exists in 7.12.0 as well.
  GET  /add-book           fallback only, used when the page above has no token (the page
                           the helper read up to 7.12.0). From v7.13.0 it is a 302 to
                           /suggestions?tab=search (_legacy_search_redirect), and the hook
                           only injects into 200 text/html responses.
  GET  /api/me/books       the signed-in user's books: abs_id, kosync_doc_id.
  GET  /api/me/kosync-documents   the user's device hashes: document_hash, linked_abs_id.
  POST /match              the Add Book submit with no audiobook: audiobook_id empty,
                           ebook_filename `<item>_abs.<ext>`, ebook_source ABS,
                           ebook_source_id <item>. Runs _upsert_storyteller_mapping(
                           mode_hint="ebook_only_create"): sync_mode ebook_only,
                           abs_ebook_item_id <item>, kosync_doc_id = BookBridge's own
                           hash of the ABS file. 302 to / on success.

State-changing requests carry the token as X-CSRF-Token and the csrf_token form field.
Only the six (method, path) pairs above can be sent; anything else raises.
Nothing here logs: the caller logs the event with the item id only.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

import httpx

from .config import UUID_RE

CSRF_RE = re.compile(r'var t = "([A-Za-z0-9_\-]{16,128})";')
ALLOWED = {
    ("POST", "/login"),
    ("GET", "/suggestions"),
    ("GET", "/add-book"),
    ("GET", "/api/me/books"),
    ("GET", "/api/me/kosync-documents"),
    ("POST", "/match"),
}


class BookBridgeWebError(Exception):
    """reason is a fixed word for the log (never response text)."""

    def __init__(self, reason: str, status: int | None = None):
        super().__init__(reason)
        self.reason = reason
        self.status = status


class SessionLost(BookBridgeWebError):
    def __init__(self, status: int | None = None):
        super().__init__("session_lost", status)


def _location_path(r: httpx.Response) -> str:
    return urlsplit(r.headers.get("location", "")).path


class BookBridgeWeb:
    def __init__(self, base_url: str, username: str, password: str, timeout: float,
                 transport: httpx.AsyncBaseTransport | None = None):
        self._username = username
        self._password = password
        self._cookies: dict[str, str] = {}
        self._csrf: str | None = None
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            transport=transport,
        )

    def __repr__(self) -> str:  # never show the password or the session
        return "BookBridgeWeb()"

    async def aclose(self) -> None:
        await self._client.aclose()

    @property
    def signed_in(self) -> bool:
        return self._csrf is not None

    def forget_session(self) -> None:
        self._cookies.clear()
        self._csrf = None

    # Cookies by hand: the host name `bookbridge` has no dot, which the stdlib
    # cookie jar treats specially; one host, one session cookie, so a dict is enough.
    def _store_cookies(self, r: httpx.Response) -> None:
        for raw in r.headers.get_list("set-cookie"):
            pair = raw.split(";", 1)[0]
            name, sep, value = pair.partition("=")
            name = name.strip()
            if not sep or not name:
                continue
            value = value.strip()
            expired = "max-age=0" in raw.lower() or "expires=thu, 01 jan 1970" in raw.lower()
            if not value or expired:
                self._cookies.pop(name, None)
            else:
                self._cookies[name] = value

    async def _send(self, method: str, path: str, **kwargs) -> httpx.Response:
        if (method, path) not in ALLOWED:
            raise BookBridgeWebError("path_not_allowed")
        headers = dict(kwargs.pop("headers", {}) or {})
        if self._cookies:
            headers["cookie"] = "; ".join(f"{k}={v}" for k, v in self._cookies.items())
        try:
            r = await self._client.request(method, path, headers=headers, **kwargs)
        except httpx.TimeoutException:
            raise BookBridgeWebError("timeout") from None
        except httpx.HTTPError:
            raise BookBridgeWebError("unreachable") from None
        self._store_cookies(r)
        return r

    async def login(self) -> None:
        """Sign in and read this session's CSRF token."""
        self.forget_session()
        r = await self._send("POST", "/login", data={"username": self._username, "password": self._password})
        if r.status_code == 429:
            raise BookBridgeWebError("login_rate_limited", 429)
        if r.status_code != 302 or _location_path(r).startswith("/login") or not self._cookies:
            self.forget_session()
            raise BookBridgeWebError("login_refused", r.status_code)
        m = None
        status = None
        # Newest page first; the old Add Book page only for BookBridge before 7.13.
        for path, params in (("/suggestions", {"tab": "search"}), ("/add-book", None)):
            r = await self._send("GET", path, params=params, headers={"accept": "text/html"})
            status = r.status_code
            m = CSRF_RE.search(r.text) if r.status_code == 200 else None
            if m:
                break
        if not m:
            self.forget_session()
            raise BookBridgeWebError("csrf_missing", status)
        self._csrf = m.group(1)

    async def ensure_session(self) -> None:
        if not self.signed_in:
            await self.login()

    async def _get_api(self, path: str) -> dict:
        r = await self._send("GET", path, headers={"accept": "application/json"})
        if r.status_code in (401, 302):
            self.forget_session()
            raise SessionLost(r.status_code)
        if r.status_code != 200:
            raise BookBridgeWebError("api_error", r.status_code)
        try:
            data = r.json()
        except ValueError:
            raise BookBridgeWebError("api_bad_json", r.status_code) from None
        if not isinstance(data, dict):
            raise BookBridgeWebError("api_bad_json", r.status_code)
        return data

    async def mapping_status(self, doc_hash: str, item_id: str) -> str:
        """'mapped': BookBridge already links this hash (to any book); 'conflict': the
        ABS item already has a book with another hash (left alone); else 'unmapped'."""
        doc_hash = doc_hash.lower()
        books = (await self._get_api("/api/me/books")).get("books") or []
        for b in books:
            if isinstance(b, dict) and str(b.get("kosync_doc_id") or "").lower() == doc_hash:
                return "mapped"
        docs = (await self._get_api("/api/me/kosync-documents")).get("documents") or []
        for d in docs:
            if (isinstance(d, dict) and str(d.get("document_hash") or "").lower() == doc_hash
                    and d.get("linked_abs_id")):
                return "mapped"
        for b in books:
            if isinstance(b, dict) and b.get("abs_id") == item_id:
                return "conflict"
        return "unmapped"

    async def book_hash(self, item_id: str) -> str | None:
        """kosync_doc_id of the user's book for this ABS item, None when there is none."""
        for b in (await self._get_api("/api/me/books")).get("books") or []:
            if isinstance(b, dict) and b.get("abs_id") == item_id:
                return str(b.get("kosync_doc_id") or "").lower() or None
        return None

    async def create_ebook_only(self, item_id: str, ext: str) -> None:
        """The Add Book submit: no audiobook, the ABS-badged ebook. Raises on refusal."""
        if not UUID_RE.match(item_id) or ext not in ("epub",):
            raise BookBridgeWebError("bad_request")
        if self._csrf is None:
            raise SessionLost()
        form = {
            "csrf_token": self._csrf,
            "audiobook_id": "",
            "ebook_filename": f"{item_id}_abs.{ext}",
            "ebook_source": "ABS",
            "ebook_source_id": item_id,
        }
        r = await self._send("POST", "/match", data=form,
                             headers={"x-csrf-token": self._csrf, "accept": "text/html"})
        if r.status_code == 302 and not _location_path(r).startswith("/login"):
            return
        if r.status_code in (302, 401):
            self.forget_session()
            raise SessionLost(r.status_code)
        if r.status_code == 403 and "csrf" in r.text.lower():
            self.forget_session()
            raise BookBridgeWebError("csrf_refused", 403)
        raise BookBridgeWebError("create_refused", r.status_code)
