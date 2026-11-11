"""CSP on the household, groceries and kitchen pages (2026-11-11 hardening): every
inline <script> those pages send must be allowed by a hash in the policy (no
'unsafe-inline' for scripts), and the admin page keeps its own stricter CSP."""
import base64
import hashlib
import re

import app
from test_admin import adm, aget  # noqa: F401  (admin feeds wired to fixtures)
from test_kitchen import home  # noqa: F401  (the fixture-wired sidecar)
from test_kitchen_pairing import ADMIN, kit, kget, pair  # noqa: F401

# Any case and any attributes, so a <SCRIPT type=...> tag cannot slip past the
# hash check (CodeQL "bad HTML filtering regexp").
INLINE = re.compile(r"<script\b([^>]*)>(.*?)</script\s*>", re.S | re.I)
EXTERNAL = re.compile(r"\bsrc\s*=", re.I)


def _allowed(csp, html):
    script_src = next(d for d in csp.split(";") if d.strip().startswith("script-src"))
    assert "'unsafe-inline'" not in script_src and "'unsafe-eval'" not in script_src
    for attrs, js in INLINE.findall(html):
        if EXTERNAL.search(attrs):
            continue                      # <script src=...> is covered by 'self', not a hash
        h = "'sha256-" + base64.b64encode(hashlib.sha256(js.encode()).digest()).decode() + "'"
        assert h in script_src, "inline script not covered by the CSP hash list"


def test_household_groceries_kitchen_pages_carry_the_csp(home):  # noqa: F811
    for path in ("/home", "/groceries", "/kitchen", "/kitchen/ingredients", "/kitchen/pantry"):
        r = home.get(path)
        assert r.status_code == 200, path
        csp = r.headers["Content-Security-Policy"]
        assert csp == app.PAGE_CSP and "frame-ancestors 'none'" in csp
        _allowed(csp, r.get_data(as_text=True))
    # the groceries tab script really is inline and really is covered
    html = home.get("/groceries").get_data(as_text=True)
    assert app.GROCERIES_JS in html


def test_kitchen_host_and_pair_page_carry_the_csp(kit):  # noqa: F811
    r = kget(kit, "/pair")
    html = r.get_data(as_text=True)
    assert "<script>" in html                      # the pair-code copy script
    _allowed(r.headers["Content-Security-Policy"], html)
    pair(kit)
    r = kget(kit, "/")
    assert r.status_code == 200 and r.headers["Content-Security-Policy"] == app.PAGE_CSP


def test_other_pages_and_admin_keep_their_own_policy(home, adm):  # noqa: F811
    assert "Content-Security-Policy" not in home.get("/healthz").headers
    r = aget(adm, "/")
    assert r.headers["Content-Security-Policy"] == app.ADMIN_CSP
