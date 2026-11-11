"""The grocery add box's destination (owner decision 2026-11-10, Q59 a): a known
Tandoor food keeps routing to its store (NewPi/Target by its supermarket
category), an unknown name goes to the Manual list, the answer says where it
went (ajax JSON and the form fallback's ?added=), and "Manual list instead"
keeps a known food on the Manual list through the forced-manual registry on
/state (pruned on read; an unwritable /state disables the toggle only)."""
import json
import os
import time

import pytest

import app
import forced
from test_kitchen_pantry import KNOWN, fake  # noqa: F401  (Tandoor faked at the seams)


def _groceries(c, q=""):
    return c.get("/groceries" + q).get_data(as_text=True)


def _tab(html, tid):
    return html.split(f"id='{tid}'>", 1)[1].split("</section>", 1)[0]


def _add(c, item, **extra):
    data = {"item": item, "ajax": "1", **extra}
    return c.post("/groceries/add", data=data)


# ---- routing + destination text -------------------------------------------------------
def test_known_food_routes_to_its_store_and_says_so(fake):  # noqa: F811
    c = app.app.test_client()
    j = _add(c, "milk").get_json()
    assert j == {"status": "added", "item": "milk", "store": "NewPi", "aisle": "Dairy & Eggs",
                 "text": "Added to New Pi · Dairy & Eggs"}
    j = _add(c, "Rice").get_json()
    assert (j["store"], j["aisle"], j["text"]) == ("Target", "Pantry & Grains",
                                                   "Added to Target · Pantry & Grains")
    html = _groceries(c)
    assert "milk" in _tab(html, "newpi") and "Rice" in _tab(html, "target")
    assert "milk" not in _tab(html, "manual") and "Rice" not in _tab(html, "manual")
    # Tandoor is only asked to create the entry: no food or category edit
    assert [(m, p) for m, p, _ in fake["writes"]] == [("POST", "/api/shopping-list-entry/")] * 2


def test_unknown_food_goes_to_the_manual_list(fake):  # noqa: F811
    c = app.app.test_client()
    j = _add(c, "birthday candles").get_json()
    assert (j["store"], j["aisle"], j["text"]) == ("Manual", "", "Added to Manual list")
    assert "birthday candles" in _tab(_groceries(c), "manual")


def test_pet_food_is_reported_as_off_the_lists(fake):  # noqa: F811
    j = _add(app.app.test_client(), "dog food").get_json()
    assert j["store"] == "Off" and "off the store lists" in j["text"]


def test_destination_from_the_reread_when_the_answer_has_no_category(fake, monkeypatch):  # noqa: F811
    real = app._api_create

    def bare(path, body):
        e = real(path, body)
        return {"id": e["id"], "food": {"name": e["food"]["name"]}}   # no supermarket_category
    monkeypatch.setattr(app, "_api_create", bare)
    j = _add(app.app.test_client(), "milk").get_json()
    assert (j["store"], j["aisle"]) == ("NewPi", "Dairy & Eggs")


def test_unknown_destination_says_added_without_guessing(fake, monkeypatch):  # noqa: F811
    monkeypatch.setattr(app, "_api_create", lambda path, body: {})
    j = _add(app.app.test_client(), "milk").get_json()
    assert j["status"] == "added" and j["store"] == "" and j["text"] == "Added to the list"


def test_dest_text():
    assert app._dest_text("NewPi", "Dairy & Eggs") == "Added to New Pi · Dairy & Eggs"
    assert app._dest_text("Target", "") == "Added to Target"
    assert app._dest_text("Manual", "") == "Added to Manual list"
    assert app._dest_text("", "") == "Added to the list"


# ---- Manual list instead ---------------------------------------------------------------
def test_toggle_renders_off_by_default_on_every_add_box(fake):  # noqa: F811
    c = app.app.test_client()
    for html in (c.get("/kitchen/pantry").get_data(as_text=True), _groceries(c)):
        box = html.split("method='post'", 1)[1].split("</form>", 1)[0]
        assert "name='manual' value='1'" in box and "Manual list instead" in box
        assert "checked" not in box and "disabled" not in box
    css = open(os.path.join(os.path.dirname(app.__file__), "static", "home.css")).read()
    assert "min-height:44px;min-width:44px" in css.split(".mtog{", 1)[1].split("}", 1)[0]
    # the groceries page uses that same .mtog from home.css (no own sizing)
    assert "/static/home.css" in _groceries(c) and app.GROCERIES_CSS.count(".mtog") == 1   # only the phone order


def test_groceries_add_box_wraps_on_a_phone():
    # 390 px: input + toggle + Add overflowed the page by 16 px; the box wraps,
    # the input may shrink, and the toggle takes its own row under 480 px
    css = app.GROCERIES_CSS
    assert "flex-wrap:wrap" in css.split(".gadd{", 1)[1].split("}", 1)[0]
    assert "min-width:0" in css.split(".gadd input[name=item]{", 1)[1].split("}", 1)[0]
    phone = css.split("@media(max-width:480px){", 1)[1].split("}}", 1)[0]
    assert ".gadd .mtog{order:3;flex-basis:100%" in phone


def test_forced_manual_keeps_a_known_food_off_the_store_list(fake, _forced_registry):  # noqa: F811
    c = app.app.test_client()
    j = _add(c, "milk", manual="1").get_json()
    assert (j["store"], j["aisle"], j["text"]) == ("Manual", "", "Added to Manual list")
    eid = fake["entries"][-1]["id"]
    assert _forced_registry.ids() == {eid}
    with open(_forced_registry.path) as fh:
        assert list(json.load(fh)["ids"]) == [str(eid)]
    assert oct(os.stat(_forced_registry.path).st_mode & 0o777) == "0o600"
    html = _groceries(c)
    assert "milk" in _tab(html, "manual") and "milk" not in _tab(html, "newpi")
    assert "<li>1 milk</li>" in c.get("/kitchen/pantry").get_data(as_text=True)
    # the same food added again without the toggle still routes to New Pi
    assert _add(c, "milk").get_json()["store"] == "NewPi"
    stores, manual = app._split_groceries(fake["entries"])
    assert [e["id"] for e in manual] == [eid]
    assert [e["id"] for e in stores["NewPi"]["Dairy & Eggs"]] == [eid + 1]
    # Tandoor data untouched: the entry still carries its real category
    assert fake["entries"][-2]["food"]["supermarket_category"] == {"name": KNOWN["milk"]}


def test_form_post_with_the_toggle(fake, _forced_registry):  # noqa: F811
    c = app.app.test_client()
    r = c.post("/groceries/add", data={"item": "rice", "manual": "1", "next": "pantry"})
    assert r.headers["Location"].endswith("/meal/kitchen/pantry?added=Manual%7C%7Crice")
    assert len(_forced_registry.ids()) == 1


def test_registry_prunes_checked_and_gone_ids(tmp_path):
    reg = forced.Registry(str(tmp_path / "manual-forced.json"))
    now = 1_000_000.0
    old = now - forced.GRACE_S - 1
    for eid, ts in ((1, old), (2, old), (3, old), (4, now - 5)):
        assert reg.add(eid, now=ts)
    entries = [{"id": 1, "checked": False}, {"id": 2, "checked": True}]
    # 1 open -> kept; 2 checked -> pruned; 3 gone and old -> pruned;
    # 4 gone but just added (a snapshot read before the add) -> kept
    assert reg.ids(entries, now=now) == {1, 4}
    assert reg.ids() == {1, 4}                                  # rewritten on disk
    assert reg.ids([], now=now + forced.GRACE_S) == set()       # all gone, all old


def test_prune_runs_when_the_page_reads_the_registry(fake, _forced_registry):  # noqa: F811
    c = app.app.test_client()
    _add(c, "milk", manual="1")
    fake["entries"][-1]["checked"] = True
    app._gcache.update(ts=0.0)                                   # force a re-read
    _groceries(c)
    assert _forced_registry.ids() == set()


def test_registry_tolerates_a_broken_file(tmp_path):
    p = tmp_path / "manual-forced.json"
    p.write_text("{not json")
    reg = forced.Registry(str(p))
    assert reg.ids() == set()
    p.write_text(json.dumps({"ids": {"7": 1.0, "x": 2.0, "8": "bad"}}))
    assert reg.ids() == {7}


def test_unwritable_state_disables_the_toggle_and_keeps_routing(fake, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr(app, "FORCED", forced.Registry(str(tmp_path / "missing" / "manual-forced.json")))
    monkeypatch.setattr(app, "_recipe", lambda rid: {"servings": 4, "ingredients": [], "steps": []})
    c = app.app.test_client()
    pages = (c.get("/kitchen/pantry").get_data(as_text=True), c.get("/home").get_data(as_text=True),
             _groceries(c))
    for html in pages:
        box = html.split("name='manual'", 1)[1].split(">", 1)[0]
        assert "disabled" in box
        assert "the dashboard can&#39;t save it right now" in html
    # a stale page that still sends manual=1: the add works, normal routing, honest answer
    j = _add(c, "milk", manual="1").get_json()
    assert (j["status"], j["store"], j["text"]) == ("added", "NewPi", "Added to New Pi · Dairy & Eggs")
    assert _add(c, "birthday candles").get_json()["store"] == "Manual"
    assert not os.path.exists(tmp_path / "missing")


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores modes")
def test_read_only_state_dir(fake, monkeypatch, tmp_path):  # noqa: F811
    d = tmp_path / "ro"
    d.mkdir()
    reg = forced.Registry(str(d / "manual-forced.json"))
    d.chmod(0o500)
    try:
        monkeypatch.setattr(app, "FORCED", reg)
        assert not reg.writable() and reg.add(5) is False
        assert _add(app.app.test_client(), "milk", manual="1").get_json()["store"] == "NewPi"
    finally:
        d.chmod(0o700)


# ---- form fallback: ?added= and escaping ---------------------------------------------
def test_form_fallback_shows_where_it_went(fake, monkeypatch):  # noqa: F811
    monkeypatch.setattr(app, "_recipe", lambda rid: {"servings": 4, "ingredients": [], "steps": []})
    c = app.app.test_client()
    loc = c.post("/groceries/add", data={"item": "milk"}).headers["Location"]
    assert loc.endswith("groceries?added=NewPi%7CDairy+%26+Eggs%7Cmilk#manual")
    html = _groceries(c, "?" + loc.split("?", 1)[1].split("#", 1)[0])
    assert "class='addflash' role='status'>Added to New Pi · Dairy &amp; Eggs: milk</div>" in _tab(html, "manual")
    for path in ("/kitchen/pantry", "/home"):
        loc = c.post("/groceries/add", data={"item": "rice", "next": path.strip("/").replace("kitchen/", "")}
                     ).headers["Location"]
        html = c.get(loc.replace("/meal", "", 1)).get_data(as_text=True)
        added = html.split("<div class='added'>", 1)[1].split("</ul>", 1)[0]
        assert "<span class='ai'>rice</span><span class='where'>Added to Target · Pantry &amp; Grains</span>" in added


def test_flash_values_are_escaped_and_validated(fake, monkeypatch):  # noqa: F811
    monkeypatch.setattr(app, "_recipe", lambda rid: {"servings": 4, "ingredients": [], "steps": []})
    c = app.app.test_client()
    bad = "<img src=x onerror=alert(1)>"
    loc = c.post("/groceries/add", data={"item": bad, "next": "pantry"}).headers["Location"]
    assert "<" not in loc and " " not in loc                      # URL-encoded
    for page in (c.get(loc.replace("/meal", "", 1)), c.get("/groceries?" + loc.split("?", 1)[1])):
        html = page.get_data(as_text=True)
        assert "<img src=x" not in html and "&lt;img src=x onerror=alert(1)&gt;" in html
    # a forged aisle is escaped; an unknown store or an empty item shows nothing
    html = _groceries(c, "?added=NewPi%7C%3Cb%3Ex%3C%2Fb%3E%7Cmilk")
    assert "<b>x</b>" not in html and "&lt;b&gt;x&lt;/b&gt;" in html
    for q in ("?added=Evil%7Cx%7Cmilk", "?added=NewPi%7Cx%7C", "?added=nonsense"):
        assert "addflash" not in _tab(_groceries(c, q), "manual"), q


def test_form_fallback_reports_a_failed_add(fake, monkeypatch):  # noqa: F811
    monkeypatch.setattr(app, "_api_create", lambda *a: None)
    c = app.app.test_client()
    loc = c.post("/groceries/add", data={"item": "milk", "next": "pantry"}).headers["Location"]
    assert loc.endswith("?added=error%7C%7Cmilk")
    html = c.get(loc.replace("/meal", "", 1)).get_data(as_text=True)
    assert "<li class='err'><span class='ai'>milk</span><span class='where'>Couldn&#39;t add it" in html


def test_home_js_shows_and_keeps_the_destination():
    js = open(os.path.join(os.path.dirname(app.__file__), "static", "home.js")).read()
    assert 'where: String(j.text || "")' in js                    # saved with the chip
    assert "w.textContent = where" in js and "a.textContent = item" in js
    assert 'typeof x === "string"' in js                          # older saved chips
    assert "tog.checked = false" in js                            # toggle back to off


def test_new_registry_starts_empty(tmp_path):
    reg = forced.Registry(str(tmp_path / "manual-forced.json"))
    assert reg.writable() and reg.ids([{"id": 1}]) == set()
    assert not os.path.exists(reg.path)                           # nothing written for nothing
    t0 = time.time()
    reg.add(3)
    assert reg.ids([{"id": 3, "checked": False}], now=t0 + forced.GRACE_S * 10) == {3}
