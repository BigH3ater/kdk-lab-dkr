"""The Today page: per-set forms by kind, prefill (CSV beats HA), per-set CSV rows,
the logged rail, the day-complete footer, ?date= backdating, group ticks,
workout.json v1 + v2, the JSON reply for the offline queue, and the app shell."""
import copy
import csv
import datetime
import json
import re
import struct

from conftest import GUIDE

J = {"Remote-User": "jmack"}
R = {"Remote-User": "rach"}


def noscript(html):
    return re.sub(r"<script>.*?</script>", "", html, flags=re.S)


def page(c, q="", who=J):
    return noscript(c.get(f"/{q}", headers=who).get_data(as_text=True))


def rows(m):
    with open(m.LOG_CSV, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def box(html, item):
    """The logbox div for one item (up to the next logbox or section end)."""
    i = html.rindex("<div", 0, html.index(f'id="ex-{item}"'))
    j = min(x for x in (html.find('<div class="logbox', i + 1), html.find("</section>", i)) if x > 0)
    return html[i:j]


def set_guide(m, g):
    m.GUIDE.write_text(json.dumps(g), encoding="utf-8")


def today(m):
    return m.today_local()


def posts(m):
    return [(u, b) for meth, u, b in m._calls if meth == "POST"]


# ------------------------------------------------------------------ form shape
def test_set_rows_follow_the_dose_and_the_kind(appmod):
    html = page(appmod.app.test_client())
    sq = box(html, "box_squat")                     # "3 × 8–12", kind w
    assert [f"weight_{i}" in sq and f"reps_{i}" in sq for i in (1, 2, 3)] == [True] * 3
    assert "weight_4" not in sq and 'class="copy" hidden' in sq
    ws = box(html, "wall_sit")                      # "4 × 30 s", kind t
    assert "seconds_4" in ws and "seconds_5" not in ws and "weight_1" not in ws


def test_wl_rows_and_v2_sets_override(appmod):
    g = copy.deepcopy(GUIDE)
    g["version"] = 2
    for d in g["days"]:
        d["steps"].insert(0, {"num": "0", "title": "Suitcase carry", "dose": "3 × 4 lengths / side @25 lb",
                              "slug": "suitcase", "image": "", "everyday": False, "cues": ["Tall."], "sets": 2})
    set_guide(appmod, g)
    sc = box(page(appmod.app.test_client()), "suitcase")
    assert "weight_1" in sc and "lengths_2" in sc and "lengths_3" not in sc and "seconds_1" not in sc


def test_done_is_one_tick_and_scores_are_a_checkin_card(appmod):
    html = page(appmod.app.test_client())
    assert html.count('name="item" value="hand_routine"') == 1
    hr = box(html, "hand_routine")
    assert 'name="done" value="1"' in hr and 'class="tick"' in hr and "weight_1" not in hr
    checkin = html[html.index("Daily check-in"):]
    assert 'id="ex-knee_pain"' in checkin and 'id="ex-hand_pain"' in checkin
    assert box(html, "knee_pain").count('name="score"') == 1


def test_group_tick_goes_on_the_first_slug_only(appmod):
    g = copy.deepcopy(GUIDE)
    g["everyday"].insert(0, {"num": "0", "title": "Warm up the hands", "dose": "30 s", "slug": "hand-warm",
                             "image": "exercise/ex-hand-warm.svg", "everyday": False})
    set_guide(appmod, g)
    html = page(appmod.app.test_client())
    assert html.count('name="item" value="hand_routine"') == 1
    assert 'name="slug" value="hand-warm"' in html and 'name="slug" value="hand-glides"' not in html
    assert "ex-hand-glides.svg" in html                 # the later step keeps its picture


def test_step_without_a_catalog_item_shows_picture_and_dose_only(appmod):
    html = page(appmod.app.test_client())
    sec = html[html.index("Arm circles"):]
    sec = sec[:sec.index("</section>")]
    assert "ex-arm-circles.svg" in sec and "30 s" in sec and "<form" not in sec


# ------------------------------------------------------------------ workout.json versions
def test_v1_and_v2_workout_json_both_render(appmod):
    c = appmod.app.test_client()
    v1 = page(c)
    assert "box_squat" in v1 and '<ul class="cues">' not in v1
    g = copy.deepcopy(GUIDE)
    g["version"] = 2
    for d in g["days"]:
        d["steps"][0].update(cues=["Hold the dumbbell at your chest.", "Tap <the> bench."], sets=4)
    set_guide(appmod, g)
    v2 = page(c)
    assert '<ul class="cues"><li>Hold the dumbbell at your chest.</li><li>Tap &lt;the&gt; bench.</li></ul>' in v2
    assert "weight_4" in box(v2, "box_squat")


# ------------------------------------------------------------------ logging
def test_per_set_rows_with_set_index_and_ha_gets_the_last_set(appmod):
    c = appmod.app.test_client()
    d = today(appmod).isoformat()
    r = c.post("/log", data={"item": "box_squat", "slug": "box-squat", "date": d, "weight_1": "20", "reps_1": "12",
                             "weight_2": "22.5", "reps_2": "10", "weight_3": "", "reps_3": "",
                             "weight_4": "25", "reps_4": "8"}, headers=R)
    assert r.status_code == 303 and r.headers["Location"] == f"./?date={d}&ok=box_squat#ex-box_squat"
    out = rows(appmod)
    assert [(x["set_index"], x["weight"], x["reps"], x["date"]) for x in out] == [
        ("1", "20", "12", d), ("2", "22.5", "10", d), ("3", "25", "8", d)]
    p = posts(appmod)
    assert {"entity_id": "input_number.ex_box_squat_weight_rachel", "value": 25} in [b for _, b in p]
    assert {"entity_id": "input_number.ex_box_squat_reps_rachel", "value": 8} in [b for _, b in p]
    assert len(p) == 2                                  # one set_value per field, last set only
    c.post("/log", data={"item": "box_squat", "date": d, "weight_1": "25", "reps_1": "6"}, headers=R)
    assert rows(appmod)[-1]["set_index"] == "4"         # a second submission continues the count


def test_bad_set_writes_nothing(appmod):
    c = appmod.app.test_client()
    r = c.post("/log", data={"item": "box_squat", "weight_1": "20", "reps_1": "10", "weight_2": "999"}, headers=J)
    assert "err=box_squat" in r.headers["Location"] and not appmod.LOG_CSV.exists()


def test_logged_rail_and_more_sets(appmod):
    c = appmod.app.test_client()
    d = today(appmod).isoformat()
    c.post("/log", data={"item": "box_squat", "date": d, "weight_1": "20", "reps_1": "10", "weight_2": "20",
                         "reps_2": "9"}, headers=J)
    c.post("/log", data={"item": "hand_routine", "date": d, "done": "1"}, headers=J)
    html = page(c)
    sq = box(html, "box_squat")
    assert 'class="logbox logged"' in sq and re.search(r'logged \d\d:\d\d · 2 sets', sq)
    assert "<summary>Log more sets</summary>" in sq and '<span class="si">3</span>' in sq
    hr = box(html, "hand_routine")
    assert "· done" in hr and "<button" not in hr      # ticked: no second tick
    assert 'class="logbox logged"' not in box(page(c, who=R), "box_squat")   # per person


def test_day_complete_footer(appmod):
    c = appmod.app.test_client()
    d = today(appmod).isoformat()
    html = page(c)
    assert "Still to do:" in html and "0 / 7 done" in html
    for data in ({"item": "box_squat", "weight_1": "20", "reps_1": "10"}, {"item": "wall_sit", "seconds_1": "30"},
                 {"item": "hand_routine", "done": "1"}, {"item": "finger_ext", "weight_1": "6.6", "reps_1": "15"},
                 {"item": "stretches", "done": "1"}, {"item": "knee_pain", "score": "2"}):
        c.post("/log", data={**data, "date": d}, headers=J)
    html = page(c)
    assert "6 / 7 done" in html and "Still to do:</b> Hand Pain" in html
    c.post("/log", data={"item": "hand_pain", "score": "0", "date": d}, headers=J)
    html = page(c)
    assert "Day complete ✓" in html and "7 / 7 done" in html and "Still to do" not in html


def test_prefill_from_the_csv_beats_ha(appmod):
    c = appmod.app.test_client()
    y = (today(appmod) - datetime.timedelta(days=1)).isoformat()
    c.post("/log", data={"item": "box_squat", "date": y, "weight_1": "20", "reps_1": "10", "weight_2": "21",
                         "reps_2": "9"}, headers=R)
    sq = box(page(c, who=R), "box_squat")
    assert 'name="weight_1" value="20"' in sq and 'name="weight_2" value="21"' in sq
    assert 'name="weight_3" value="21"' in sq           # beyond last time's sets: repeat its last set
    assert "17.5" not in sq and f"last {y}" in sq       # HA's 17.5 is only the fallback


def test_backdating_with_date(appmod):
    c = appmod.app.test_client()
    t = today(appmod)
    past = t - datetime.timedelta(days=3)
    html = page(c, f"?date={past.isoformat()}")
    assert f'name="date" value="{past.isoformat()}"' in html and "backdated" in html
    assert f"{past:%A} · {past.day} {past:%B %Y}" in html
    r = c.post("/log", data={"item": "wall_sit", "date": past.isoformat(), "seconds_1": "40"}, headers=J)
    assert r.headers["Location"].startswith(f"./?date={past.isoformat()}&ok=wall_sit")
    assert rows(appmod)[0]["date"] == past.isoformat()
    assert posts(appmod) == []                          # backdated: CSV only, HA keeps today's state
    fut = (t + datetime.timedelta(days=1)).isoformat()
    assert "<button" not in page(c, f"?date={fut}")      # future: plan only
    r = c.post("/log", data={"item": "wall_sit", "date": fut, "seconds_1": "40"}, headers=J)
    assert "err=wall_sit" in r.headers["Location"] and len(rows(appmod)) == 1
    assert 'name="date" value="' + t.isoformat() in page(c, "?date=garbage")   # bad date -> today


def test_day_param_still_works(appmod):
    t = today(appmod)
    mon = t - datetime.timedelta(days=t.weekday())
    assert f'name="date" value="{mon.isoformat()}"' in page(appmod.app.test_client(), "?day=mon")


# ------------------------------------------------------------------ offline queue + shell
def test_json_reply_and_replay_dedup(appmod):
    c = appmod.app.test_client()
    h = {**J, "X-Workout-Queue": "1"}
    d = today(appmod).isoformat()
    body = {"item": "wall_sit", "date": d, "seconds_1": "40", "qid": "abc123"}
    r = c.post("/log", data=body, headers=h)
    assert r.status_code == 200 and r.get_json()["ok"] and r.get_json()["sets"] == 1
    assert r.get_json()["next"] == f"./?date={d}&ok=wall_sit#ex-wall_sit"
    r = c.post("/log", data=body, headers=h)            # replay of a POST that did land
    assert r.get_json()["duplicate"] is True and len(rows(appmod)) == 1
    r = c.post("/log", data={"item": "wall_sit", "seconds_1": "9999"}, headers=h)
    assert r.status_code == 400 and "err=wall_sit" in r.get_json()["next"]


def test_page_works_without_js(appmod):
    html = page(appmod.app.test_client())
    forms = re.findall(r"<form[^>]*>", html)
    assert forms and all('method="post"' in f and 'action="log"' in f for f in forms)
    assert all(b.endswith("hidden>") for b in re.findall(r'<button type="button" class="copy"[^>]*>', html))
    assert html.count('<button type="submit"') == len(forms)    # every card submits by itself
    assert 'id="qbanner" class="banner" hidden' in html.replace("'", '"')


def test_manifest_icons_and_service_worker(appmod):
    c = appmod.app.test_client()
    m = c.get("/manifest.webmanifest")
    d = json.loads(m.get_data(as_text=True))
    assert m.mimetype == "application/manifest+json" and d["name"] == "Workout" and d["display"] == "standalone"
    for ic in d["icons"]:
        png = c.get("/" + ic["src"]).data
        n = int(ic["sizes"].split("x")[0])
        assert png[:8] == b"\x89PNG\r\n\x1a\n" and struct.unpack(">II", png[16:24]) == (n, n)
    assert c.get("/icon-180.png").status_code == 200 and c.get("/icon-7.png").status_code == 404
    sw = c.get("/sw.js")
    js = sw.get_data(as_text=True)
    assert sw.mimetype == "text/javascript" and "__V__" not in js and appmod.SW_VERSION in js
    assert "!r.redirected" in js and "r.type === 'basic'" in js     # never caches a login redirect
    html = c.get("/", headers=J).get_data(as_text=True)
    assert 'rel="manifest" href="manifest.webmanifest" crossorigin="use-credentials"' in html
    assert "serviceWorker.register('sw.js'" in html and "X-Workout-Queue" in html
    assert c.get("/history", headers=J).status_code == 200
