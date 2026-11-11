"""Fakes for BookBridge's KOSync port and the Audiobookshelf API (httpx.MockTransport)."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

import httpx
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from kosync_helper.app import create_app
from kosync_helper.config import Settings
from kosync_helper.partial_md5 import partial_md5

LIB_ID = "edfded58-4a7d-43b1-84a2-271a2798719c"
PDF_ITEM = "11111111-2222-4333-8444-555555555555"
EPUB_ITEM = "66666666-7777-4888-9999-000000000000"
USER, KEY = "jmack", "0123456789abcdef0123456789abcdef"  # KOReader sends md5(password)
AUTH = {"x-auth-user": USER, "x-auth-key": KEY, "accept": "application/vnd.koreader.v1+json"}
EPUB_HASH = "a" * 32


def make_pdf(path: str, pages: int) -> None:
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)
    with open(path, "wb") as f:
        w.write(f)


@dataclass
class FakeBookBridge:
    users: dict = field(default_factory=lambda: {USER: KEY})
    calls: list = field(default_factory=list)
    fail: Exception | None = None
    auth_status: int | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.fail:
            raise self.fail
        if self.auth_status and request.url.path == "/users/auth":
            return httpx.Response(self.auth_status, json={"message": "nope"})
        ok = self.users.get(request.headers.get("x-auth-user")) == request.headers.get("x-auth-key")
        path = request.url.path
        if path == "/healthcheck":
            return httpx.Response(200, text="OK")
        if path == "/users/auth":
            return httpx.Response(200 if ok else 401, json={"username": USER} if ok else {"message": "Unauthorized"})
        if path in ("/users/create", "/users/login"):
            return httpx.Response(201 if ok or path == "/users/create" else 401, json={"username": USER})
        if not ok:
            return httpx.Response(401, json={"error": "Unauthorized"})
        if path == "/syncs/progress" and request.method == "PUT":
            return httpx.Response(200, json={"document": json.loads(request.content)["document"], "timestamp": 1})
        if path.startswith("/syncs/progress/"):
            return httpx.Response(200, json={"document": path.rsplit("/", 1)[1], "progress": "/body/DocFragment[3]",
                                             "percentage": 0.25, "device": "rm", "device_id": "X"})
        return httpx.Response(404)


@dataclass
class FakeAbs:
    root: str
    items: list = field(default_factory=list)  # (item_id, format, path)
    overrides: dict = field(default_factory=dict)  # item_id -> fields merged into GET /api/items/:id
    progress: dict = field(default_factory=dict)
    patches: list = field(default_factory=list)
    calls: list = field(default_factory=list)
    fail: Exception | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.fail:
            raise self.fail
        assert request.headers["authorization"] == "Bearer abs-test-key"
        path = request.url.path
        assert path.startswith("/audiobookshelf/api/")
        path = path[len("/audiobookshelf"):]
        if path == f"/api/libraries/{LIB_ID}/items":
            return httpx.Response(200, json={"results": [
                {"id": i, "media": {"ebookFormat": fmt}} for i, fmt, _ in self.items]})
        if path.startswith("/api/items/"):
            item_id = path.rsplit("/", 1)[1]
            for i, fmt, p in self.items:
                if i == item_id:
                    size = os.stat(p).st_size if os.path.exists(p) else 0
                    item = {"id": i, "libraryId": LIB_ID, "mediaType": "book",
                            "media": {"audioFiles": [], "ebookFile": {
                                "ebookFormat": fmt, "metadata": {"path": p, "size": size}}}}
                    for k, v in self.overrides.get(i, {}).items():
                        if k == "media":
                            item["media"].update(v)
                        else:
                            item[k] = v
                    return httpx.Response(200, json=item)
            return httpx.Response(404)
        if path.startswith("/api/me/progress/"):
            item_id = path.rsplit("/", 1)[1]
            if request.method == "PATCH":
                body = json.loads(request.content)
                self.patches.append((item_id, body))
                cur = self.progress.setdefault(item_id, {"isFinished": False})
                cur.update(body)
                cur["lastUpdate"] = 1_800_000_000_000
                return httpx.Response(200)
            if item_id in self.progress:
                return httpx.Response(200, json=self.progress[item_id])
            return httpx.Response(404)
        return httpx.Response(404)


@pytest.fixture
def ebooks(tmp_path):
    root = tmp_path / "ebooks"
    (root / "Author" / "Book").mkdir(parents=True)
    pdf = root / "Author" / "Book" / "book.pdf"
    make_pdf(str(pdf), 50)
    epub = root / "Author" / "Book" / "book.epub"
    epub.write_bytes(b"PK\x03\x04 not really an epub" * 100)
    return root, str(pdf), partial_md5(str(pdf))


@pytest.fixture
def settings(ebooks):
    root, _, _ = ebooks
    return Settings(abs_url="http://audiobookshelf/audiobookshelf", abs_api_key="abs-test-key",
                    abs_library_id=LIB_ID, bookbridge_url="http://bookbridge:5758", kosync_user=USER,
                    ebooks_root=str(root), upstream_timeout=1.0)


@pytest.fixture
def fakes(ebooks):
    root, pdf, _ = ebooks
    abs_ = FakeAbs(root=str(root), items=[(PDF_ITEM, "pdf", pdf),
                                          (EPUB_ITEM, "epub", os.path.join(str(root), "Author/Book/book.epub"))])
    return FakeBookBridge(), abs_


@pytest.fixture
def client(settings, fakes):
    bb, abs_ = fakes
    app = create_app(settings, abs_transport=httpx.MockTransport(abs_.handler),
                     bb_transport=httpx.MockTransport(bb.handler), start_refresh=False)
    with TestClient(app, client=("100.64.1.2", 5000)) as c:
        c.portal.call(app.state.helper.refresh)
        yield c


WEB_USER, WEB_PASSWORD = "kdkadmin", "web-pass-NEVER-LOGGED-1234567890"
CSRF = "csrf-token-0123456789abcdefghij"


@dataclass
class FakeBookBridgeWeb:
    """The BookBridge web UI routes the helper uses (session cookie + CSRF).

    Behaves like v7.13.0 by default: /suggestions (any tab) carries the token and
    GET /add-book is a 302 to /suggestions?tab=search. legacy_add_book=True is the
    7.12.0 shape the fallback covers (/add-book answers 200 with the token) with
    /suggestions forced to carry no token."""
    books: list = field(default_factory=list)       # {"abs_id", "kosync_doc_id"}
    documents: list = field(default_factory=list)   # {"document_hash", "linked_abs_id"}
    calls: list = field(default_factory=list)
    hash_for: dict = field(default_factory=dict)    # item_id -> hash BookBridge computes
    login_status: int | None = None   # force a login answer (401/429)
    match_status: int | None = None   # force a /match answer
    no_csrf: bool = False
    legacy_add_book: bool = False
    drop_session_once: bool = False
    fail: Exception | None = None
    sessions: set = field(default_factory=set)
    logins: int = 0

    def _session(self, request):
        for part in request.headers.get("cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "session" and v in self.sessions:
                return v
        return None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.fail:
            raise self.fail
        path, method = request.url.path, request.method
        if (method, path) == ("POST", "/login"):
            self.logins += 1
            form = dict(httpx.QueryParams(request.content.decode()))
            if self.login_status:
                return httpx.Response(self.login_status, text="<html>Invalid username or password</html>")
            if form.get("username") != WEB_USER or form.get("password") != WEB_PASSWORD:
                return httpx.Response(401, text="<html>Invalid username or password</html>")
            sid = f"s{self.logins}"
            self.sessions.add(sid)
            return httpx.Response(302, headers={"location": "/", "set-cookie": f"session={sid}; HttpOnly; Path=/"})
        sid = self._session(request)
        if self.drop_session_once and sid:
            self.drop_session_once = False
            self.sessions.discard(sid)
            sid = None
        if sid is None:
            if path.startswith("/api/"):
                return httpx.Response(401, json={"error": "authentication required"})
            return httpx.Response(302, headers={"location": f"/login?next={path}"})
        script = "" if self.no_csrf else f'<script>(function(){{\n  var t = "{CSRF}";\n}})();</script>'
        if (method, path) == ("GET", "/suggestions"):
            body = "" if self.legacy_add_book else script
            return httpx.Response(200, text=f"<html><body>Discover{body}</body></html>",
                                  headers={"content-type": "text/html; charset=utf-8"})
        if (method, path) == ("GET", "/add-book"):
            if not self.legacy_add_book:
                return httpx.Response(302, headers={"location": "/suggestions?tab=search"})
            return httpx.Response(200, text=f"<html><body>Add Book{script}</body></html>",
                                  headers={"content-type": "text/html; charset=utf-8"})
        if (method, path) == ("GET", "/api/me/books"):
            return httpx.Response(200, json={"books": self.books, "total": len(self.books)})
        if (method, path) == ("GET", "/api/me/kosync-documents"):
            return httpx.Response(200, json={"documents": self.documents})
        if (method, path) == ("POST", "/match"):
            form = dict(httpx.QueryParams(request.content.decode()))
            if request.headers.get("x-csrf-token") != CSRF or form.get("csrf_token") != CSRF:
                return httpx.Response(403, text="CSRF token missing or invalid")
            if self.match_status:
                return httpx.Response(self.match_status, text="Could not compute KOSync ID for ebook")
            item = form["ebook_source_id"]
            assert form["audiobook_id"] == "" and form["ebook_source"] == "ABS"
            assert form["ebook_filename"] == f"{item}_abs.epub"
            self.books.append({"abs_id": item, "kosync_doc_id": self.hash_for.get(item), "sync_mode": "ebook_only"})
            return httpx.Response(302, headers={"location": "/"})
        return httpx.Response(404)

    @property
    def matches(self):
        return [c for c in self.calls if c.url.path == "/match"]


@pytest.fixture
def web():
    return FakeBookBridgeWeb()


@pytest.fixture
def link_settings(settings):
    from dataclasses import replace

    return replace(settings, bookbridge_web_user=WEB_USER, bookbridge_web_password=WEB_PASSWORD,
                   epub_link_delay_seconds=0)


@pytest.fixture
def link_client(link_settings, fakes, web, ebooks):
    bb, abs_ = fakes
    root, _, _ = ebooks
    web.hash_for[EPUB_ITEM] = partial_md5(os.path.join(str(root), "Author/Book/book.epub"))
    now = [1_000_000.0]
    app = create_app(link_settings, abs_transport=httpx.MockTransport(abs_.handler),
                     bb_transport=httpx.MockTransport(bb.handler), start_refresh=False,
                     bb_web_transport=httpx.MockTransport(web.handler), clock=lambda: now[0])
    with TestClient(app, client=("100.64.1.2", 5000)) as c:
        c.portal.call(app.state.helper.refresh)
        c.now = now
        yield c
