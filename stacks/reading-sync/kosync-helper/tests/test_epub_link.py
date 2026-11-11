"""EPUB auto-link: the helper creates BookBridge's ebook-only link for a tablet EPUB."""
import json
import logging
import os

import httpx
import pytest

from conftest import AUTH, CSRF, EPUB_HASH, EPUB_ITEM, KEY, PDF_ITEM, USER, WEB_PASSWORD, WEB_USER
from kosync_helper.app import JsonFormatter
from kosync_helper.partial_md5 import partial_md5


def epub_hash(ebooks):
    root, _, _ = ebooks
    return partial_md5(os.path.join(str(root), "Author/Book/book.epub"))


def epub_put(c, doc, headers=AUTH, pct=0.1):
    body = {"document": doc, "progress": "/body/DocFragment[3]/body/p[2]/text().0", "percentage": pct,
            "device": "reMarkable", "device_id": "DEV1"}
    r = c.put("/syncs/progress", json=body, headers=headers)
    c.portal.call(c.app.state.helper.linker.drain)
    return r


def events(caplog):
    return [json.loads(JsonFormatter().format(r)) for r in caplog.records if r.name == "kosync_helper"]


def web_paths(web):
    return [(c.method, c.url.path) for c in web.calls]


# ---------------- the map ----------------

def test_epub_map_holds_primary_epubs_and_pdf_map_is_unchanged(link_client, ebooks):
    helper = link_client.app.state.helper
    assert len(helper.library) == 1 and helper.library.epub_count == 1
    assert helper.library.lookup(epub_hash(ebooks)) is None          # never on the PDF path
    assert helper.library.lookup_epub(epub_hash(ebooks)).item_id == EPUB_ITEM
    assert helper.library.lookup(ebooks[2]).item_id == PDF_ITEM
    assert helper.library.lookup_epub(ebooks[2]) is None


def test_health_reports_epubs_and_autolink(link_client):
    assert link_client.get("/health").json() == {"pdfs": 1, "epubs": 1, "epub_autolink": True,
                                                  "map_loaded": True, "bookbridge_login": "never",
                                                  "status": "ok"}


def test_health_shows_the_last_login(link_client, ebooks):
    epub_put(link_client, epub_hash(ebooks))
    assert link_client.get("/health").json()["bookbridge_login"] == "ok"


# ---------------- creating the link ----------------

def test_epub_put_creates_exactly_one_mapping(link_client, fakes, web, ebooks, caplog):
    caplog.set_level(logging.INFO, logger="kosync_helper")
    bb, abs_ = fakes
    doc = epub_hash(ebooks)
    r = epub_put(link_client, doc)
    assert r.status_code == 200                      # the tablet's PUT went to BookBridge as before
    assert bb.calls[-1].url.path == "/syncs/progress"
    assert len(web.matches) == 1
    form = dict(httpx.QueryParams(web.matches[0].content.decode()))
    assert form == {"csrf_token": CSRF, "audiobook_id": "", "ebook_filename": f"{EPUB_ITEM}_abs.epub",
                    "ebook_source": "ABS", "ebook_source_id": EPUB_ITEM}
    assert web.books == [{"abs_id": EPUB_ITEM, "kosync_doc_id": doc, "sync_mode": "ebook_only"}]
    created = [e for e in events(caplog) if e["event"] == "epub_mapping_created"]
    assert created == [{"ts": created[0]["ts"], "level": "info", "event": "epub_mapping_created",
                        "item": EPUB_ITEM, "status": 200}]
    # More page turns: no second mapping, and no more BookBridge web calls at all.
    n = len(web.calls)
    for _ in range(3):
        assert epub_put(link_client, doc).status_code == 200
    assert len(web.matches) == 1 and len(web.calls) == n
    assert abs_.patches == []                        # EPUB progress is BookBridge's, never the helper's


def test_uppercase_hash_is_the_same_document(link_client, web, ebooks):
    epub_put(link_client, epub_hash(ebooks).upper())
    assert len(web.matches) == 1


def test_already_mapped_hash_is_skipped(link_client, web, ebooks, caplog):
    caplog.set_level(logging.INFO, logger="kosync_helper")
    web.books.append({"abs_id": EPUB_ITEM, "kosync_doc_id": epub_hash(ebooks)})
    epub_put(link_client, epub_hash(ebooks))
    assert web.matches == []
    assert "epub_mapping_exists" in [e["event"] for e in events(caplog)]


def test_hash_linked_as_a_kosync_document_is_skipped(link_client, web, ebooks, caplog):
    caplog.set_level(logging.INFO, logger="kosync_helper")
    web.documents.append({"document_hash": epub_hash(ebooks), "linked_abs_id": EPUB_ITEM})
    epub_put(link_client, epub_hash(ebooks))
    assert web.matches == []
    assert "epub_mapping_exists" in [e["event"] for e in events(caplog)]


@pytest.mark.parametrize("where", ["book", "document"])
def test_hash_linked_to_another_item_is_reported_and_looked_at_again(link_client, web, ebooks, caplog, where):
    """Review 2026-11-11 #7: a hash BookBridge links to ANOTHER book is not "mapped"
    (no 24 h done state: progress would silently go to that other book)."""
    caplog.set_level(logging.INFO, logger="kosync_helper")
    doc = epub_hash(ebooks)
    if where == "book":
        web.books.append({"abs_id": "ebook-0123456789abcdef", "kosync_doc_id": doc})
    else:
        web.documents.append({"document_hash": doc, "linked_abs_id": "some-book"})
    epub_put(link_client, doc)
    assert web.matches == []
    evs = [e["event"] for e in events(caplog)]
    assert "epub_mapping_other_item" in evs and "epub_mapping_exists" not in evs
    linker = link_client.app.state.helper.linker
    assert doc not in linker._done and linker._state[doc].failures == 0
    n = len(web.calls)
    epub_put(link_client, doc)                        # waits for the next refresh
    assert len(web.calls) == n
    link_client.now[0] += 3601
    epub_put(link_client, doc)                        # looked at again, not 24 h later
    assert len(web.calls) > n


def test_item_with_a_book_under_another_hash_is_left_alone(link_client, web, ebooks, caplog):
    caplog.set_level(logging.INFO, logger="kosync_helper")
    web.books.append({"abs_id": EPUB_ITEM, "kosync_doc_id": "f" * 32})
    epub_put(link_client, epub_hash(ebooks))
    assert web.matches == []
    assert "epub_mapping_conflict" in [e["event"] for e in events(caplog)]


def test_pdf_hash_never_creates_a_mapping(link_client, fakes, web, ebooks):
    _, abs_ = fakes
    for page in (1, 2, 3):
        body = {"document": ebooks[2], "progress": page, "percentage": page / 50, "device": "rm", "device_id": "D"}
        assert link_client.put("/syncs/progress", json=body, headers=AUTH).status_code == 200
    link_client.portal.call(link_client.app.state.helper.linker.drain)
    assert web.calls == [] and len(abs_.patches) == 3


def test_unknown_hash_is_skipped(link_client, fakes, web):
    bb, _ = fakes
    assert epub_put(link_client, EPUB_HASH).status_code == 200    # passed through as before
    assert bb.calls[-1].url.path == "/syncs/progress"
    assert web.calls == []


def test_other_sync_users_never_trigger_a_link(link_client, fakes, web, ebooks):
    bb, _ = fakes
    bb.users["rach"] = "k" * 32
    epub_put(link_client, epub_hash(ebooks), headers={"x-auth-user": "rach", "x-auth-key": "k" * 32})
    assert web.calls == []


def test_put_bookbridge_refused_never_triggers_a_link(link_client, web, ebooks):
    r = epub_put(link_client, epub_hash(ebooks), headers={**AUTH, "x-auth-key": "wrong"})
    assert r.status_code == 401 and web.calls == []


def test_autolink_off_without_a_password(client, fakes, ebooks):
    assert client.app.state.helper.linker is None
    assert client.get("/health").json()["epub_autolink"] is False
    body = {"document": epub_hash(ebooks), "progress": "/body/x", "percentage": 0.1}
    assert client.put("/syncs/progress", json=body, headers=AUTH).status_code == 200


# ---------------- the ABS and file checks ----------------

@pytest.mark.parametrize("override,reason", [
    ({"libraryId": "00000000-0000-4000-8000-000000000000"}, "not_in_ebooks_library"),
    ({"mediaType": "podcast"}, "not_a_book"),
    ({"media": {"audioFiles": [{"ino": "1"}]}}, "has_audio"),
    ({"media": {"ebookFile": {"ebookFormat": "pdf", "metadata": {"path": "/x.pdf", "size": 1}}}}, "primary_not_epub"),
    ({"media": {"ebookFile": {"ebookFormat": "epub", "metadata": {"path": "/ebooks/other.epub", "size": 2900}}}},
     "primary_path_changed"),
])
def test_abs_item_checks_refuse(link_client, fakes, web, ebooks, caplog, override, reason):
    caplog.set_level(logging.INFO, logger="kosync_helper")
    _, abs_ = fakes
    abs_.overrides[EPUB_ITEM] = override
    epub_put(link_client, epub_hash(ebooks))
    assert web.matches == []
    refused = [e for e in events(caplog) if e["event"] == "epub_mapping_refused"]
    assert refused and refused[0]["item"] == EPUB_ITEM and refused[0]["reason"] == reason


def test_abs_size_must_match(link_client, fakes, web, ebooks):
    _, abs_ = fakes
    root, _, _ = ebooks
    path = os.path.join(str(root), "Author/Book/book.epub")
    abs_.overrides[EPUB_ITEM] = {"media": {"ebookFile": {"ebookFormat": "epub",
                                                          "metadata": {"path": path, "size": 1}}}}
    epub_put(link_client, epub_hash(ebooks))
    assert web.matches == []


def test_file_changed_since_the_map_is_refused(link_client, web, ebooks):
    root, _, _ = ebooks
    doc = epub_hash(ebooks)
    with open(os.path.join(str(root), "Author/Book/book.epub"), "r+b") as f:
        f.write(b"XX")  # same size, other bytes: the hash no longer matches
    epub_put(link_client, doc)
    assert web.matches == []


def test_refused_item_waits_for_the_next_refresh(link_client, fakes, web, ebooks):
    _, abs_ = fakes
    abs_.overrides[EPUB_ITEM] = {"libraryId": "00000000-0000-4000-8000-000000000000"}
    epub_put(link_client, epub_hash(ebooks))
    n = len(web.calls)
    epub_put(link_client, epub_hash(ebooks))
    assert len(web.calls) == n
    abs_.overrides.clear()
    link_client.now[0] += 3601
    epub_put(link_client, epub_hash(ebooks))
    assert len(web.matches) == 1


def test_explicit_waits_do_not_lengthen_the_backoff(link_client, fakes, web, ebooks):
    """Review 2026-11-11 #6: refusals (and conflicts, pauses) wait for a refresh but are
    not failures: the first real failure after them still waits 60 s, not 4 min."""
    _, abs_ = fakes
    doc = epub_hash(ebooks)
    abs_.overrides[EPUB_ITEM] = {"libraryId": "00000000-0000-4000-8000-000000000000"}
    epub_put(link_client, doc)                        # refused
    link_client.now[0] += 3601
    epub_put(link_client, doc)                        # refused again
    assert web.matches == []
    abs_.overrides.clear()
    web.match_status = 500
    link_client.now[0] += 3601
    epub_put(link_client, doc)                        # the first real failure: 60 s
    assert len(web.matches) == 1
    web.match_status = None
    link_client.now[0] += 61
    epub_put(link_client, doc)
    assert len(web.matches) == 2 and web.books[0]["abs_id"] == EPUB_ITEM


def test_conflict_and_login_pause_are_not_failures(link_client, web, ebooks):
    doc = epub_hash(ebooks)
    linker = link_client.app.state.helper.linker
    web.books.append({"abs_id": EPUB_ITEM, "kosync_doc_id": "f" * 32})
    epub_put(link_client, doc)
    assert linker._state[doc].failures == 0
    linker._login_pause_until = link_client.now[0] + 10_000
    link_client.now[0] += 3601
    epub_put(link_client, doc)
    assert linker._state[doc].failures == 0


# ---------------- BookBridge failures ----------------

def test_bookbridge_failure_backs_off_and_retries(link_client, web, ebooks, caplog):
    caplog.set_level(logging.INFO, logger="kosync_helper")
    web.match_status = 500
    doc = epub_hash(ebooks)
    assert epub_put(link_client, doc).status_code == 200
    assert len(web.matches) == 1
    failed = [e for e in events(caplog) if e["event"] == "epub_mapping_failed"]
    assert failed[0]["reason"] == "create_refused" and failed[0]["status"] == 500
    epub_put(link_client, doc)                       # inside the 60 s backoff: nothing sent
    assert len(web.matches) == 1
    link_client.now[0] += 61
    epub_put(link_client, doc)                       # second failure: now 120 s
    assert len(web.matches) == 2
    link_client.now[0] += 61
    epub_put(link_client, doc)
    assert len(web.matches) == 2
    web.match_status = None
    link_client.now[0] += 60
    epub_put(link_client, doc)
    assert len(web.matches) == 3 and web.books[0]["abs_id"] == EPUB_ITEM


def test_bookbridge_unreachable_does_not_crash(link_client, web, ebooks):
    web.fail = httpx.ConnectError("refused")
    assert epub_put(link_client, epub_hash(ebooks)).status_code == 200
    assert link_client.get("/health").status_code == 200
    web.fail = httpx.ReadTimeout("slow")
    link_client.now[0] += 61
    assert epub_put(link_client, epub_hash(ebooks)).status_code == 200


@pytest.mark.parametrize("how", ["unreachable", "status", "not_json"])
def test_abs_errors_on_the_item_check_are_abs_error(link_client, fakes, web, ebooks, caplog, how):
    """Review 2026-11-11 #12: transport errors and a non-JSON body from GET /api/items/:id
    are logged as abs_error, not as an internal error."""
    caplog.set_level(logging.INFO, logger="kosync_helper")
    _, abs_ = fakes
    if how == "unreachable":
        abs_.fail = httpx.ConnectError("refused")
    elif how == "status":
        abs_.item_status = 500
    else:
        abs_.item_text = "<html>proxy error</html>"
    epub_put(link_client, epub_hash(ebooks))
    assert web.matches == []
    failed = [e for e in events(caplog) if e["event"] == "epub_mapping_failed"]
    assert failed[0]["reason"] == "abs_error"


def test_unexpected_error_logs_the_class_name_only(link_client, web, ebooks, caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger="kosync_helper")
    linker = link_client.app.state.helper.linker

    async def boom(doc, entry):
        raise RuntimeError("http://abs/api/items?token=sekrit-value")

    monkeypatch.setattr(linker, "_verify", boom)
    epub_put(link_client, epub_hash(ebooks))
    failed = [e for e in events(caplog) if e["event"] == "epub_mapping_failed"]
    assert failed[0]["reason"] == "internal" and failed[0]["error"] == "RuntimeError"
    assert "sekrit-value" not in "\n".join(JsonFormatter().format(r) for r in caplog.records)


def test_hash_mismatch_is_reported_not_retried_soon(link_client, web, ebooks, caplog):
    caplog.set_level(logging.INFO, logger="kosync_helper")
    web.hash_for[EPUB_ITEM] = "e" * 32
    epub_put(link_client, epub_hash(ebooks))
    assert "epub_mapping_hash_mismatch" in [e["event"] for e in events(caplog)]
    link_client.now[0] += 3600
    epub_put(link_client, epub_hash(ebooks))
    assert len(web.matches) == 1


# ---------------- login and CSRF ----------------

@pytest.mark.parametrize("status,reason", [(401, "login_refused"), (429, "login_rate_limited")])
def test_login_failure_pauses_every_hash(link_client, web, ebooks, caplog, status, reason):
    caplog.set_level(logging.INFO, logger="kosync_helper")
    web.login_status = status
    epub_put(link_client, epub_hash(ebooks))
    assert web.logins == 1 and web.matches == []
    ev = [e for e in events(caplog) if e["event"] == "bookbridge_login_failed"]
    assert ev[0]["reason"] == reason
    link_client.now[0] += 600                         # still inside the 15 min pause
    epub_put(link_client, epub_hash(ebooks))
    assert web.logins == 1
    web.login_status = None
    link_client.now[0] += 301
    epub_put(link_client, epub_hash(ebooks))
    assert web.logins == 2 and len(web.matches) == 1


def test_wrong_password_is_a_refused_login(link_settings, fakes, web, ebooks):
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from kosync_helper.app import create_app

    bb, abs_ = fakes
    s = replace(link_settings, bookbridge_web_password="not-the-password")
    app = create_app(s, abs_transport=httpx.MockTransport(abs_.handler), bb_transport=httpx.MockTransport(bb.handler),
                     start_refresh=False, bb_web_transport=httpx.MockTransport(web.handler))
    with TestClient(app) as c:
        c.portal.call(app.state.helper.refresh)
        epub_put(c, epub_hash(ebooks))
        assert c.get("/health").json()["bookbridge_login"] == "refused"   # without reading logs
    assert web.logins == 1 and web.matches == []


def test_missing_csrf_token_never_posts(link_client, web, ebooks, caplog):
    caplog.set_level(logging.INFO, logger="kosync_helper")
    web.no_csrf = True
    epub_put(link_client, epub_hash(ebooks))
    assert web.matches == [] and ("POST", "/match") not in web_paths(web)
    ev = [e for e in events(caplog) if e["event"] == "epub_mapping_failed"]
    assert ev[0]["reason"] == "csrf_missing"


def test_csrf_token_comes_from_the_search_tab(link_client, web, ebooks):
    """BookBridge v7.13.0: the token is read from /suggestions?tab=search; the old
    /add-book page (now a 302) is never fetched."""
    epub_put(link_client, epub_hash(ebooks))
    assert len(web.matches) == 1
    pages = [c for c in web.calls if c.method == "GET" and c.url.path in ("/suggestions", "/add-book")]
    assert [(c.url.path, c.url.params.get("tab")) for c in pages] == [("/suggestions", "search")]


def test_csrf_token_falls_back_to_add_book(link_client, web, ebooks):
    """A BookBridge whose search page carries no token: the 7.12.0 Add Book page."""
    web.legacy_add_book = True
    epub_put(link_client, epub_hash(ebooks))
    assert len(web.matches) == 1
    assert [p for p in web_paths(web) if p[0] == "GET" and p[1] in ("/suggestions", "/add-book")] == [
        ("GET", "/suggestions"), ("GET", "/add-book")]


def test_refused_csrf_token_signs_in_again_once(link_client, web, ebooks):
    import conftest

    link_client.portal.call(link_client.app.state.helper.linker.web.login)
    link_client.app.state.helper.linker.web._csrf = "stale-token-0000000000000"
    epub_put(link_client, epub_hash(ebooks))
    assert len(web.matches) == 2                      # 403 on the stale token, then the fresh one
    assert web.matches[-1].headers["x-csrf-token"] == conftest.CSRF
    assert web.books and web.books[0]["abs_id"] == EPUB_ITEM


def test_expired_session_signs_in_again(link_client, web, ebooks):
    link_client.portal.call(link_client.app.state.helper.linker.web.login)
    web.drop_session_once = True
    epub_put(link_client, epub_hash(ebooks))
    assert web.logins == 2 and len(web.matches) == 1


def test_concurrent_attempts_sign_in_once(web):
    """Review 2026-11-11 #1: two attempts that both find no session share one login
    (the second waits for the first), so neither clears the other's session."""
    import asyncio

    from kosync_helper.bookbridge_web import BookBridgeWeb

    async def slow(request):
        await asyncio.sleep(0.01)                      # let the other caller run meanwhile
        return web.handler(request)

    async def run():
        bw = BookBridgeWeb("http://bookbridge:5758", WEB_USER, WEB_PASSWORD, 5.0, httpx.MockTransport(slow))
        try:
            await asyncio.gather(bw.ensure_session(), bw.ensure_session())
            return bw.signed_in
        finally:
            await bw.aclose()

    assert asyncio.run(run()) is True
    assert web.logins == 1
    assert [p for p in web_paths(web) if p == ("GET", "/suggestions")] == [("GET", "/suggestions")]


def test_failed_login_keeps_the_working_session(link_client, web):
    """A login builds its cookies and token aside and only replaces the session on success."""
    from kosync_helper.bookbridge_web import BookBridgeWebError

    w = link_client.app.state.helper.linker.web
    link_client.portal.call(w.login)
    cookies, token = dict(w._cookies), w._csrf
    web.no_csrf = True
    with pytest.raises(BookBridgeWebError):
        link_client.portal.call(w.login)
    assert w.signed_in and w._cookies == cookies and w._csrf == token


def test_only_the_allowed_routes_can_be_sent(link_client):
    from kosync_helper.bookbridge_web import BookBridgeWebError

    web_client = link_client.app.state.helper.linker.web
    with pytest.raises(BookBridgeWebError):
        link_client.portal.call(web_client._send, "POST", "/api/kosync-documents/x/link")
    with pytest.raises(BookBridgeWebError):
        link_client.portal.call(web_client._send, "GET", "/settings")


# ---------------- logs ----------------

def test_no_secrets_or_hashes_in_the_logs(link_client, fakes, web, ebooks, caplog):
    caplog.set_level(logging.DEBUG)
    doc = epub_hash(ebooks)
    epub_put(link_client, doc)                         # created
    web.login_status = 401
    link_client.app.state.helper.linker.web.forget_session()
    link_client.app.state.helper.linker._done.clear()
    epub_put(link_client, doc)                         # login refused
    logs = "\n".join(JsonFormatter().format(r) for r in caplog.records)
    for secret in (WEB_PASSWORD, KEY, "abs-test-key", CSRF, "session=", doc, USER, WEB_USER):
        assert secret not in logs
    assert EPUB_ITEM in logs
    reprs = repr(link_client.app.state.helper.s) + repr(link_client.app.state.helper.linker.web)
    for secret in (WEB_PASSWORD, "abs-test-key", CSRF):
        assert secret not in reprs
