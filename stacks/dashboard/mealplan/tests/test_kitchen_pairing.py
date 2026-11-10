"""kitchen.kmkdp.com: device pairing, the signed device cookie, the device
registry, the host allowlist, Remote-* stripping, CSRF checks, the pair-code
rules (single use, expiry, rate limit), revoke, the missing-key 503 and the
Kitchen Vikunja token fallback. Fixtures only, no network."""
import importlib.util
import json
import logging
import os
import re
import time

import pytest

import app
import devices
import kitchen
from test_kitchen import home  # noqa: F401  (the fixture-wired sidecar)

K = "https://kitchen.kmkdp.com"
H = "https://home.kmkdp.com"
KEY = "test-kitchen-device-key-" + "x" * 40          # not a real secret
ADMIN = {"Remote-User": "jmack", "Origin": H}
CODE_RE = re.compile(r"/pair\?code=([A-Z2-7]{26})")


def _reset_pairing():
    app._pair_state.update(sig=None, signer=None, registry=None)


@pytest.fixture
def kit(home, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr(app, "KITCHEN_DEVICE_KEY", KEY)
    monkeypatch.setattr(app, "KITCHEN_STATE", str(tmp_path / "state" / "devices.json"))
    monkeypatch.setattr(app, "PAIR_LIMIT", devices.RateLimiter(10, 600))
    _reset_pairing()
    yield home
    _reset_pairing()


def kget(c, path, **kw):
    return c.get(path, base_url=K, **kw)


def kpost(c, path, origin=K, **kw):
    headers = dict(kw.pop("headers", {}) or {})
    if origin:
        headers.setdefault("Origin", origin)
    return c.post(path, base_url=K, headers=headers, **kw)


def issue_code(c) -> str:
    r = c.post("/kitchen/devices/pair", headers=ADMIN)
    assert r.status_code == 200
    return CODE_RE.search(r.get_data(as_text=True)).group(1)


def pair(c, name="Kitchen iPad"):
    code = issue_code(c)
    r = kpost(c, "/pair", data={"code": code, "name": name})
    assert r.status_code == 303 and r.headers["Location"] == "/"
    return r


def registry():
    return app._pairing()[1]


# ---- missing key ---------------------------------------------------------------------
def test_missing_key_answers_503_on_the_kitchen_host_only(home, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr(app, "KITCHEN_STATE", str(tmp_path / "devices.json"))
    for key in ("", "too-short"):
        monkeypatch.setattr(app, "KITCHEN_DEVICE_KEY", key)
        _reset_pairing()
        for path in ("/", "/pair", "/static/home.css", "/manifest.webmanifest"):
            r = kget(home, path)
            assert r.status_code == 503 and r.get_data(as_text=True) == "kitchen pairing not configured"
    # home.kmkdp.com keeps working, and the devices page says why pairing is off
    assert home.get("/kitchen").status_code == 200
    page = home.get("/kitchen/devices", headers={"Remote-User": "jmack"}).get_data(as_text=True)
    assert "Pairing is not configured" in page and "Pair a device" not in page
    _reset_pairing()


# ---- pairing flow -----------------------------------------------------------------------
def test_pair_flow_sets_a_strict_host_only_cookie(kit):
    code = issue_code(kit)
    page = kit.post("/kitchen/devices/pair", headers=ADMIN).get_data(as_text=True)
    if importlib.util.find_spec("segno"):                            # pinned in the Dockerfile
        assert "<svg" in page                                       # QR code
    assert "Or type the code" in page
    r = kget(kit, f"/pair?code={code}")
    assert r.status_code == 200 and "name='name'" in r.get_data(as_text=True)
    r = kpost(kit, "/pair", data={"code": devices.group_code(code).lower(), "name": "  Kitchen   <iPad> "})
    assert r.status_code == 303 and r.headers["Location"] == "/"
    sc = r.headers["Set-Cookie"]
    assert sc.startswith("kitchen_device=k1.")
    for attr in ("HttpOnly", "Secure", "SameSite=Strict", "Path=/", "Max-Age=31536000"):
        assert attr in sc
    assert "Domain" not in sc
    dev = registry().devices()[0]
    assert dev["name"] == "Kitchen <iPad>" and not dev["revoked"]
    # the kitchen views at the host root
    html = kget(kit, "/").get_data(as_text=True)
    assert "href='/ingredients'" in html and "href='/steps'" in html and "href='/pantry'" in html
    assert "data-fragment='/?fragment=1'" in html and "action='/tasks/complete'" in html
    assert "href='/static/home.css?v=" in html and "/meal/" not in html
    for path in ("/ingredients", "/steps", "/steps?r=48", "/pantry"):
        sub = kget(kit, path)
        assert sub.status_code == 200, path
        assert "data-home='/'" in sub.get_data(as_text=True)
    frag = kget(kit, "/?fragment=1").get_data(as_text=True)
    assert frag.startswith("<div class='board-in'")
    assert "src='/step-photo/16'" in kget(kit, "/steps").get_data(as_text=True)
    assert kget(kit, "/step-photo/999999").status_code == 404     # not a planned step photo
    # home.kmkdp.com keeps its /meal links
    assert "href='/meal/kitchen/ingredients'" in kit.get("/kitchen").get_data(as_text=True)


def test_kitchen_manifest_scope(kit):
    m = json.loads(kget(kit, "/manifest.webmanifest").get_data(as_text=True))
    assert m["start_url"] == "https://kitchen.kmkdp.com/" and m["scope"] == "https://kitchen.kmkdp.com/"
    assert m["icons"][0]["src"] == "/static/icon-192.png"
    assert json.loads(kit.get("/manifest.webmanifest").get_data(as_text=True))["start_url"] == "/meal/kitchen"


def test_unpaired_device_gets_the_pair_screen(kit):
    r = kget(kit, "/")
    assert r.status_code == 401
    html = r.get_data(as_text=True)
    assert "This screen isn't paired" in html and "action='/pair'" in html
    assert kget(kit, "/pantry").status_code == 401
    assert kget(kit, "/step-photo/16").status_code == 401
    assert kpost(kit, "/groceries/add", data={"item": "milk"}).status_code == 401
    # not secret: styles, icons, manifest
    assert kget(kit, "/static/home.css").status_code == 200
    assert kget(kit, "/manifest.webmanifest").status_code == 200
    assert kget(kit, "/pair").status_code == 200                    # the code entry form


def test_pair_code_is_single_use(kit):
    code = issue_code(kit)
    assert kpost(kit, "/pair", data={"code": code, "name": "one"}).status_code == 303
    again = kpost(kit, "/pair", data={"code": code, "name": "two"})
    assert again.status_code == 400 and "Set-Cookie" not in again.headers
    assert kget(kit, f"/pair?code={code}").status_code == 400
    assert [d["name"] for d in registry().devices()] == ["one"]


def test_pair_get_does_not_consume_the_code(kit):
    code = issue_code(kit)
    assert kget(kit, f"/pair?code={code}").status_code == 200       # a link preview
    assert kget(kit, f"/pair?code={code}").status_code == 200
    assert kpost(kit, "/pair", data={"code": code}).status_code == 303


def test_pair_code_expires_after_10_minutes(kit):
    code = registry().new_code("jmack", time.time() - devices.CODE_TTL - 1)
    assert kget(kit, f"/pair?code={code}").status_code == 400
    assert kpost(kit, "/pair", data={"code": code}).status_code == 400
    assert registry().devices() == []


def test_pair_codes_are_random_128_bit_and_stored_hashed(kit):
    codes = {issue_code(kit) for _ in range(3)}
    assert len(codes) == 3 and all(len(c) == 26 for c in codes)       # 16 bytes, base32
    raw = open(app.KITCHEN_STATE).read()
    assert not any(c in raw for c in codes)
    assert oct(os.stat(app.KITCHEN_STATE).st_mode & 0o777) == "0o600"


def test_pair_rate_limit_per_ip(kit):
    bad = "A" * 26
    for _ in range(10):
        assert kget(kit, f"/pair?code={bad}", headers={"X-Forwarded-For": "10.1.30.50"}).status_code == 400
    r = kget(kit, f"/pair?code={bad}", headers={"X-Forwarded-For": "10.1.30.50"})
    assert r.status_code == 429
    good = issue_code(kit)
    r = kpost(kit, "/pair", data={"code": good}, headers={"X-Forwarded-For": "10.1.30.50"})
    assert r.status_code == 429                                     # even a good code, for 10 min
    # another client is not affected; the last X-Forwarded-For hop (Traefik's) counts
    assert kget(kit, f"/pair?code={good}", headers={"X-Forwarded-For": "10.1.30.50, 10.1.30.51"}).status_code == 200


def test_pair_post_needs_the_kitchen_origin(kit):
    code = issue_code(kit)
    assert kpost(kit, "/pair", origin="https://evil.example", data={"code": code}).status_code == 403
    assert kpost(kit, "/pair", origin=None, data={"code": code}).status_code == 403
    assert kpost(kit, "/pair", origin=None, data={"code": code},
                 headers={"Referer": K + "/pair?code=x"}).status_code == 303


def test_pair_is_404_on_the_home_host(kit):
    assert kit.get("/pair").status_code == 404
    assert kit.post("/pair", headers={"Origin": H}).status_code == 404


def test_logs_never_carry_the_code_or_the_token(kit, caplog):
    caplog.set_level(logging.INFO)
    code = issue_code(kit)
    r = kpost(kit, "/pair", data={"code": code, "name": "iPad"})
    token = r.headers["Set-Cookie"].split(";")[0].split("=", 1)[1]
    kit.post("/kitchen/devices/revoke-all", headers=ADMIN)
    kget(kit, "/")
    assert "device paired" in caplog.text and "all devices revoked" in caplog.text
    assert code not in caplog.text and token not in caplog.text and KEY not in caplog.text


# ---- the device cookie ------------------------------------------------------------------
def _set(c, value):
    c.set_cookie(devices.COOKIE, value, domain="kitchen.kmkdp.com")


def test_forged_cookies_are_refused(kit):
    pair(kit)
    good = kit.get_cookie(devices.COOKIE, domain="kitchen.kmkdp.com").value
    assert kget(kit, "/").status_code == 200
    dev = registry().devices()[0]
    other = devices.Signer("another-key-entirely-" + "y" * 40)
    forged = [
        other.issue(dev["id"], dev["issued"], dev["expires"]),             # wrong key
        good.rsplit(".", 1)[0] + ".AAAA",                                   # bad signature
        good.replace(dev["id"], "AAAAAAAAAAAAAAAA"),                        # edited device id
        "k1." + ".".join(good.split(".")[1:5]),                             # signature dropped
        "garbage", "", "k1." + "a." * 300,
    ]
    for value in forged:
        _set(kit, value)
        assert kget(kit, "/").status_code == 401, value[:30]


def test_expired_token_is_refused(kit):
    pair(kit)
    dev = registry().devices()[0]
    signer = app._pairing()[0]
    _set(kit, signer.issue(dev["id"], dev["issued"], int(time.time()) - 1))
    assert kget(kit, "/").status_code == 401
    claims, why = signer.verify(signer.issue(dev["id"], dev["issued"], int(time.time()) - 1), time.time())
    assert claims is None and why == "expired"


def test_unknown_device_with_a_good_signature_is_refused(kit):
    signer = app._pairing()[0]
    now = int(time.time())
    _set(kit, signer.issue("NotInTheRegistry", now, now + 3600))
    assert kget(kit, "/").status_code == 401
    # a token for a real device id but a different issued time (an older pairing)
    pair(kit)
    dev = registry().devices()[0]
    _set(kit, signer.issue(dev["id"], dev["issued"] - 10, dev["expires"]))
    assert kget(kit, "/").status_code == 401


def test_revoke_and_revoke_all(kit):
    pair(kit, "first")
    first = kit.get_cookie(devices.COOKIE, domain="kitchen.kmkdp.com").value
    pair(kit, "second")
    second = kit.get_cookie(devices.COOKIE, domain="kitchen.kmkdp.com").value
    devs = {d["name"]: d for d in registry().devices()}
    page = kit.get("/kitchen/devices", headers={"Remote-User": "jmack"}).get_data(as_text=True)
    assert "first" in page and "second" in page and page.count(">Revoke</button>") == 2
    assert "Last seen" in page and "Revoke all" in page
    r = kit.post("/kitchen/devices/revoke", data={"id": devs["first"]["id"]}, headers=ADMIN)
    assert r.status_code == 303 and r.headers["Location"] == "/meal/kitchen/devices"
    _set(kit, first)
    assert kget(kit, "/").status_code == 401
    _set(kit, second)
    assert kget(kit, "/").status_code == 200
    kit.post("/kitchen/devices/revoke-all", headers=ADMIN)
    assert kget(kit, "/").status_code == 401
    assert all(d["revoked"] for d in registry().devices())
    on_disk = json.load(open(app.KITCHEN_STATE))
    assert all(d["revoked"] for d in on_disk["devices"])


def test_last_seen_is_tracked(kit, monkeypatch):
    pair(kit)
    did = registry().devices()[0]["id"]
    registry()._data["devices"][0]["last_seen"] = 0
    kget(kit, "/")
    assert registry().devices()[0]["last_seen"] > 0 and did


def test_key_rotation_revokes_every_device(kit, monkeypatch):
    pair(kit)
    assert kget(kit, "/").status_code == 200
    monkeypatch.setattr(app, "KITCHEN_DEVICE_KEY", "rotated-kitchen-key-" + "z" * 40)
    assert kget(kit, "/").status_code == 401
    assert all(d["revoked"] and d["revoked_by"] == "key rotation" for d in registry().devices())


def test_registry_survives_a_restart(kit):
    pair(kit)
    _reset_pairing()                                    # a new process reads the file
    assert kget(kit, "/").status_code == 200


# ---- devices page: KITCHEN_ADMINS only ------------------------------------------------
def test_devices_page_is_jmack_only(kit):
    assert kit.get("/kitchen/devices").status_code == 403
    assert kit.get("/kitchen/devices", headers={"Remote-User": "rach"}).status_code == 403
    assert kit.post("/kitchen/devices/pair", headers={"Remote-User": "rach", "Origin": H}).status_code == 403
    assert kit.post("/kitchen/devices/revoke-all", headers={"Remote-User": "rach", "Origin": H}).status_code == 403
    assert kit.get("/kitchen/devices", headers={"Remote-User": "jmack"}).status_code == 200
    # admin POSTs need the home.kmkdp.com Origin too
    assert kit.post("/kitchen/devices/pair", headers={"Remote-User": "jmack"}).status_code == 403
    assert kit.post("/kitchen/devices/pair", headers={"Remote-User": "jmack",
                                                     "Origin": "https://evil.example"}).status_code == 403


def test_admin_list_is_configurable(kit, monkeypatch):
    monkeypatch.setattr(app, "KITCHEN_ADMINS", {"jmack", "rach"})
    assert kit.get("/kitchen/devices", headers={"Remote-User": "rach"}).status_code == 200


# ---- Remote-* ignored on the kitchen host ------------------------------------------------
def test_remote_user_header_is_ignored_on_the_kitchen_host(kit):
    hdr = {"Remote-User": "jmack", "Remote-Groups": "admins", "Remote_User": "jmack"}
    assert kget(kit, "/", headers=hdr).status_code == 401
    assert kget(kit, "/kitchen/devices", headers=hdr).status_code == 404
    assert kpost(kit, "/kitchen/devices/pair", headers=hdr).status_code == 404
    with app.app.test_request_context("/", base_url=K, headers=hdr):
        app._host_gate()
        assert app.remote_user() == "" and app.rater_from_headers() == "family"
        assert "HTTP_REMOTE_USER" not in app.request.environ
    kitchen.set_kitchen_host(False)


def test_kitchen_router_marker_header_forces_kitchen_rules(kit):
    # X-Kdk-Kitchen (set by the Traefik middleware) narrows any Host to the kitchen rules
    assert kit.get("/kitchen", headers={"X-Kdk-Kitchen": "1"}).status_code == 404
    assert kit.get("/", headers={"X-Kdk-Kitchen": "1", "Remote-User": "jmack"}).status_code == 401


def test_host_normalisation(kit):
    assert kit.get("/", base_url="https://KITCHEN.kmkdp.com.:443").status_code == 401


# ---- allowlist --------------------------------------------------------------------------
def test_paths_outside_the_allowlist_are_404(kit):
    pair(kit)
    for path in ("/kitchen", "/kitchen/pantry", "/meal/kitchen", "/home", "/groceries", "/tasks",
                 "/weather", "/mealplan.json", "/healthz", "/kitchen/devices", "/kitchen/step-photo/16",
                 "/step-photo/abc", "/ingredients/x", "/nope"):
        assert kget(kit, path).status_code == 404, path
    for path in ("/rate", "/groceries/tick", "/groceries/thaw-tick", "/", "/kitchen/devices/revoke-all"):
        assert kpost(kit, path, headers={"X-Kitchen": "1"}).status_code == 404, path
    assert kit.put("/tasks/complete", base_url=K, headers={"Origin": K}).status_code == 404


# ---- CSRF ---------------------------------------------------------------------------------
@pytest.fixture
def writes(monkeypatch):
    seen = []
    monkeypatch.setattr(app, "_manual_add", lambda raw: (seen.append(raw) or "added", raw))
    return seen


def test_kitchen_posts_need_cookie_and_origin(kit, writes):
    pair(kit)
    assert kpost(kit, "/groceries/add", origin=None, data={"item": "milk"}).status_code == 403
    assert kpost(kit, "/groceries/add", origin="https://evil.example", data={"item": "milk"}).status_code == 403
    assert kpost(kit, "/groceries/add", origin=H, data={"item": "milk"}).status_code == 403
    assert kpost(kit, "/groceries/add", origin="null", data={"item": "milk"}).status_code == 403
    assert writes == []
    # form fallback: Origin (or Referer) is enough, then back to the pantry view
    r = kpost(kit, "/groceries/add", data={"item": "milk", "next": "pantry"})
    assert r.status_code == 303 and r.headers["Location"] == "/pantry"
    r = kpost(kit, "/groceries/add", origin=None, data={"item": "eggs", "next": "home"},
              headers={"Referer": K + "/pantry"})
    assert r.status_code == 303 and r.headers["Location"] == "/"
    assert writes == ["milk", "eggs"]


def test_kitchen_ajax_posts_need_the_custom_header(kit, writes):
    pair(kit)
    r = kpost(kit, "/groceries/add", data={"item": "milk", "ajax": "1"})
    assert r.status_code == 403 and writes == []
    r = kpost(kit, "/groceries/add", data={"item": "milk", "ajax": "1"}, headers={"X-Kitchen": "1"})
    assert r.status_code == 200 and json.loads(r.get_data(as_text=True))["status"] == "added"


def test_kitchen_security_headers(kit):
    r = kget(kit, "/")
    assert r.headers["X-Frame-Options"] == "DENY" and r.headers["Referrer-Policy"] == "same-origin"
    assert "X-Frame-Options" not in kit.get("/kitchen").headers


# ---- chore ticks: Kitchen Vikunja token, else jmack's -------------------------------------
@pytest.fixture
def vik(monkeypatch):
    calls = []

    def fake(method, path, token, body=None):
        calls.append((method, path, token))
        return {"id": 5, "done": False}
    monkeypatch.setattr(app, "_vik", fake)
    monkeypatch.setattr(app.TASKS_FEED, "refresh", lambda: None)
    monkeypatch.setitem(app.VIKUNJA_TOKENS, "jmack", "tok-jmack")
    monkeypatch.setitem(app.VIKUNJA_TOKENS, "rach", "tok-rach")
    return calls


def test_kitchen_tick_falls_back_to_the_jmack_token(kit, vik, monkeypatch, caplog):
    monkeypatch.setattr(app, "VIKUNJA_TOKEN_KITCHEN", "")
    pair(kit)
    r = kpost(kit, "/tasks/complete", data={"task_id": "5", "ajax": "1"},
              headers={"X-Kitchen": "1", "Remote-User": "rach"})
    assert r.status_code == 204
    assert {t for _m, _p, t in vik} == {"tok-jmack"}                 # never rach's: Remote-User ignored
    caplog.set_level(logging.INFO)
    app.log_kitchen_token_mode()
    assert "fallback active" in caplog.text
    # plain form: back to the board
    r = kpost(kit, "/tasks/complete", data={"task_id": "5"})
    assert r.status_code == 303 and r.headers["Location"] == "/"


def test_kitchen_tick_uses_the_kitchen_token_when_set(kit, vik, monkeypatch, caplog):
    monkeypatch.setattr(app, "VIKUNJA_TOKEN_KITCHEN", "tok-kitchen")
    pair(kit)
    kpost(kit, "/tasks/complete", data={"task_id": "5", "ajax": "1"}, headers={"X-Kitchen": "1"})
    assert {t for _m, _p, t in vik} == {"tok-kitchen"}
    caplog.set_level(logging.INFO)
    app.log_kitchen_token_mode()
    assert "fallback active" not in caplog.text
    # home.kmkdp.com ticks still use the viewer's own token
    vik.clear()
    kit.post("/tasks/complete", data={"task_id": "5", "ajax": "1"}, headers={"Remote-User": "rach"})
    assert {t for _m, _p, t in vik} == {"tok-rach"}


# ---- unit bits -------------------------------------------------------------------------------
def test_rate_limiter_window():
    rl = devices.RateLimiter(limit=2, window=10)
    assert rl.hit("a", 0) and rl.hit("a", 1) and not rl.hit("a", 2)
    assert rl.hit("b", 2) and rl.hit("a", 11)


def test_origin_check():
    assert devices.origin_ok(K, None, K)
    assert devices.origin_ok(None, K + "/pantry", K)
    assert not devices.origin_ok(None, K + ".evil.example/", K)
    assert not devices.origin_ok(None, None, K)
    assert not devices.origin_ok("https://kitchen.kmkdp.com.evil.example", K + "/", K)


def test_code_normalisation():
    code = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    assert devices.normalize_code(devices.group_code(code).lower()) == code
    assert devices.normalize_code("short") == ""
    assert devices.clean_name("") == "Kitchen iPad" and len(devices.clean_name("x" * 99)) == devices.NAME_MAX
