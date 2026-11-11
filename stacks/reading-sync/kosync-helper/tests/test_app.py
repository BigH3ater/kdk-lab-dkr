import json

import httpx

from conftest import AUTH, EPUB_HASH, KEY, PDF_ITEM, USER


def pdf_put(client, doc, page, pct=None, headers=AUTH, device_id="DEV1"):
    body = {"document": doc, "progress": page, "percentage": pct if pct is not None else page / 50,
            "device": "reMarkable", "device_id": device_id}
    return client.put("/syncs/progress", json=body, headers=headers)


# ---------------- PDF: KOReader -> ABS ----------------

def test_pdf_put_writes_abs_page(client, fakes, ebooks):
    _, abs_ = fakes
    doc = ebooks[2]
    r = pdf_put(client, doc, 40)
    assert r.status_code == 200 and r.json()["document"] == doc
    assert abs_.patches == [(PDF_ITEM, {"ebookLocation": "40", "ebookProgress": 39 / 50})]


def test_pdf_put_last_page_finishes(client, fakes, ebooks):
    _, abs_ = fakes
    assert pdf_put(client, ebooks[2], 50).status_code == 200
    assert abs_.patches[-1][1] == {"ebookLocation": "50", "ebookProgress": 49 / 50, "isFinished": True}


def test_pdf_put_uppercase_hash(client, fakes, ebooks):
    _, abs_ = fakes
    assert pdf_put(client, ebooks[2].upper(), 3).status_code == 200
    assert abs_.patches[-1][0] == PDF_ITEM


def test_pdf_put_never_reaches_bookbridge_progress(client, fakes, ebooks):
    bb, _ = fakes
    pdf_put(client, ebooks[2], 4)
    assert [c.url.path for c in bb.calls] == ["/users/auth"]


def test_pdf_put_invalid_progress(client, fakes, ebooks):
    _, abs_ = fakes
    r = pdf_put(client, ebooks[2], "/body/DocFragment[2]", pct=0.1)
    assert r.status_code == 400 and abs_.patches == []


# ---------------- PDF: ABS -> KOReader ----------------

def test_pdf_get_from_abs(client, fakes, ebooks):
    _, abs_ = fakes
    abs_.progress[PDF_ITEM] = {"ebookLocation": "55", "ebookProgress": 0.5, "lastUpdate": 1_800_000_500_000}
    r = client.get(f"/syncs/progress/{ebooks[2]}", headers=AUTH)
    # 55 > 50 pages: clamped to the last page
    assert r.json() == {"document": ebooks[2], "progress": "50", "percentage": 1.0, "timestamp": 1_800_000_500,
                        "device": "Audiobookshelf", "device_id": "audiobookshelf"}


def test_pdf_get_phone_moved_reports_abs_device(client, fakes, ebooks):
    _, abs_ = fakes
    pdf_put(client, ebooks[2], 10)
    abs_.progress[PDF_ITEM]["ebookLocation"] = "25"  # phone read on
    body = client.get(f"/syncs/progress/{ebooks[2]}", headers=AUTH).json()
    assert body["progress"] == "25" and body["percentage"] == 0.5 and body["device_id"] == "audiobookshelf"


def test_pdf_get_own_write_reports_this_device(client, fakes, ebooks):
    pdf_put(client, ebooks[2], 10, device_id="DEV1")
    body = client.get(f"/syncs/progress/{ebooks[2]}", headers=AUTH).json()
    assert body["progress"] == "10" and body["device"] == "reMarkable" and body["device_id"] == "DEV1"


def test_pdf_get_no_progress_is_empty(client, ebooks):
    r = client.get(f"/syncs/progress/{ebooks[2]}", headers=AUTH)
    assert r.status_code == 200 and r.json() == {}


def test_pdf_round_trip_ten_pages_each_way(client, fakes, ebooks):
    _, abs_ = fakes
    doc = ebooks[2]
    for page in range(1, 11):  # tablet reads 10 pages
        assert pdf_put(client, doc, page).status_code == 200
    assert abs_.progress[PDF_ITEM]["ebookLocation"] == "10"
    abs_.progress[PDF_ITEM]["ebookLocation"] = "20"  # phone reads 10 more
    assert client.get(f"/syncs/progress/{doc}", headers=AUTH).json()["progress"] == "20"


# ---------------- pass-through ----------------

def test_epub_put_passes_through(client, fakes):
    bb, abs_ = fakes
    body = {"document": EPUB_HASH, "progress": "/body/DocFragment[3]/body/p[2]/text().0",
            "percentage": 0.25, "device": "reMarkable", "device_id": "DEV1"}
    r = client.put("/syncs/progress", content=json.dumps(body), headers={**AUTH, "content-type": "application/json",
                                                                          "x-forwarded-for": "100.100.1.9"})
    assert r.status_code == 200
    sent = bb.calls[-1]
    assert sent.method == "PUT" and sent.url.path == "/syncs/progress"
    assert json.loads(sent.content) == body
    assert sent.headers["x-auth-user"] == USER and sent.headers["x-auth-key"] == KEY
    assert sent.headers["x-forwarded-for"] == "100.100.1.9, 100.64.1.2"
    assert abs_.patches == []


def test_epub_get_passes_through(client, fakes):
    bb, _ = fakes
    r = client.get(f"/syncs/progress/{EPUB_HASH}", headers=AUTH)
    assert r.status_code == 200 and r.json()["progress"] == "/body/DocFragment[3]"
    assert bb.calls[-1].url.path == f"/syncs/progress/{EPUB_HASH}"


def test_pdf_hash_for_another_user_passes_through(client, fakes, ebooks):
    bb, abs_ = fakes
    bb.users["rach"] = "k" * 32
    pdf_put(client, ebooks[2], 5, headers={"x-auth-user": "rach", "x-auth-key": "k" * 32})
    assert bb.calls[-1].url.path == "/syncs/progress" and abs_.patches == []


def test_users_auth_and_create_pass_through(client, fakes):
    bb, _ = fakes
    assert client.get("/users/auth", headers=AUTH).status_code == 200
    r = client.post("/users/create", json={"username": USER, "password": KEY}, headers={"accept": AUTH["accept"]})
    assert r.status_code == 201
    assert json.loads(bb.calls[-1].content) == {"username": USER, "password": KEY}
    assert client.get("/users/auth", headers={**AUTH, "x-auth-key": "wrong"}).status_code == 401


def test_healthcheck_passes_through(client):
    assert client.get("/healthcheck").text == "OK"


def test_other_bookbridge_routes_are_not_exposed(client, fakes):
    bb, _ = fakes
    for path in ("/", "/settings", "/api/kosync-documents", "/koreader/syncs/progress",
                 "/device-sync/manifest", "/users/../settings"):
        assert client.get(path, headers=AUTH).status_code in (404, 405)
    assert bb.calls == []


# ---------------- auth ----------------

def test_pdf_requires_valid_auth(client, fakes, ebooks):
    _, abs_ = fakes
    r = pdf_put(client, ebooks[2], 5, headers={**AUTH, "x-auth-key": "nope"})
    assert r.status_code == 401 and abs_.patches == []
    r = client.get(f"/syncs/progress/{ebooks[2]}", headers={"x-auth-user": USER})
    assert r.status_code == 401
    assert not any(c.url.path.startswith("/audiobookshelf/api/me") for c in abs_.calls)


def test_auth_header_validation(client, fakes, ebooks):
    bb, abs_ = fakes
    for hdr in ({"x-auth-user": USER, "x-auth-key": "a b"}, {"x-auth-user": USER, "x-auth-key": "k" * 300},
                {"x-auth-user": USER, "x-auth-key": ""}):
        assert client.get(f"/syncs/progress/{ebooks[2]}", headers=hdr).status_code == 401
    assert bb.calls == []
    # a different (malformed) user name is not the PDF user: BookBridge decides, ABS is never asked
    assert client.get(f"/syncs/progress/{ebooks[2]}", headers={"x-auth-user": "jm ack", "x-auth-key": KEY}).status_code == 401
    assert bb.calls[-1].url.path.startswith("/syncs/progress/")
    assert not any(c.url.path.startswith("/audiobookshelf/api/me") for c in abs_.calls)


def test_auth_is_cached(client, fakes, ebooks):
    bb, _ = fakes
    for page in (1, 2, 3):
        pdf_put(client, ebooks[2], page)
    assert [c.url.path for c in bb.calls].count("/users/auth") == 1


def test_auth_rate_limit_passes_429(client, fakes, ebooks):
    bb, abs_ = fakes
    bb.auth_status = 429
    assert pdf_put(client, ebooks[2], 1).status_code == 429
    bb.auth_status = 500  # anything else BookBridge says is a refusal, never a pass
    assert pdf_put(client, ebooks[2], 1).status_code == 401
    assert abs_.patches == []


# ---------------- input validation ----------------

def test_invalid_document_ids(client, fakes):
    bb, _ = fakes
    for doc in ("../../etc/passwd", "g" * 32, "a" * 31, "a" * 33):
        r = client.put("/syncs/progress", json={"document": doc, "progress": 1, "percentage": 0.1}, headers=AUTH)
        assert r.status_code == 400
    assert client.get("/syncs/progress/" + "z" * 32, headers=AUTH).status_code == 400
    assert bb.calls == []


def test_invalid_json(client):
    assert client.put("/syncs/progress", content=b"{not json", headers=AUTH).status_code == 400
    assert client.put("/syncs/progress", content=b"[1,2]", headers=AUTH).status_code == 400
    assert client.put("/syncs/progress", content=b"\xff\xfe", headers=AUTH).status_code == 400


def test_body_too_large(client, fakes):
    bb, _ = fakes
    big = json.dumps({"document": EPUB_HASH, "progress": "x" * 70_000})
    assert client.put("/syncs/progress", content=big, headers=AUTH).status_code == 413
    assert client.post("/users/create", content=big).status_code == 413
    assert bb.calls == []


# ---------------- timeouts and outages ----------------

def test_bookbridge_timeout(client, fakes):
    bb, _ = fakes
    bb.fail = httpx.ReadTimeout("slow")
    assert client.get(f"/syncs/progress/{EPUB_HASH}", headers=AUTH).status_code == 504
    assert client.get("/users/auth", headers=AUTH).status_code == 504


def test_bookbridge_down(client, fakes, ebooks):
    bb, _ = fakes
    bb.fail = httpx.ConnectError("refused")
    assert client.get(f"/syncs/progress/{EPUB_HASH}", headers=AUTH).status_code == 502
    assert pdf_put(client, ebooks[2], 2).status_code == 502  # cannot authenticate


def test_abs_timeout_on_pdf(client, fakes, ebooks):
    _, abs_ = fakes
    client.get("/users/auth", headers=AUTH)
    pdf_put(client, ebooks[2], 1)  # auth cached
    abs_.fail = httpx.ReadTimeout("slow")
    assert pdf_put(client, ebooks[2], 2).status_code == 504
    assert client.get(f"/syncs/progress/{ebooks[2]}", headers=AUTH).status_code == 504


def test_abs_error_on_pdf(client, fakes, ebooks):
    _, abs_ = fakes
    pdf_put(client, ebooks[2], 1)
    abs_.fail = httpx.ConnectError("refused")
    assert pdf_put(client, ebooks[2], 2).status_code == 502


def test_upstream_timeouts_are_configured(client):
    helper = client.app.state.helper
    assert helper.bb.timeout.read == 1.0
    assert helper.abs._client.timeout.read == 1.0


# ---------------- health ----------------

def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"pdfs": 1, "epubs": 1, "epub_autolink": False,
                                                 "map_loaded": True, "status": "ok"}


def test_health_degraded_when_map_never_loads(settings, fakes):
    from fastapi.testclient import TestClient

    from kosync_helper.app import create_app

    bb, abs_ = fakes
    abs_.fail = httpx.ConnectError("refused")
    now = [1000.0]
    app = create_app(settings, abs_transport=httpx.MockTransport(abs_.handler),
                     bb_transport=httpx.MockTransport(bb.handler), start_refresh=False, clock=lambda: now[0])
    with TestClient(app) as c:
        c.portal.call(app.state.helper.refresh)
        assert c.get("/health").status_code == 200  # start-up grace
        now[0] += 301
        assert c.get("/health").status_code == 503
        # PDFs fall back to BookBridge while the map is empty
        assert c.get("/syncs/progress/" + "b" * 32, headers=AUTH).status_code == 200
