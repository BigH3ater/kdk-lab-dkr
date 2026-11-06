"""gen_wger.py against an in-memory fake of the wger 2.7 REST API (requests.Session patched).

The guide data here is synthetic (the real workout.json comes from the private vault);
it uses the real catalog's slugs and the real dose formats."""
import datetime as dt
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import gen_wger as gw  # noqa: E402
import gen_workout  # noqa: E402

TODAY = dt.date(2026, 11, 6)          # a Friday; the routine must start Monday 2026-11-02
URL = "https://wger.test"
KEYS = {"WGER_API_KEY_JMACK": "tok-jmack", "WGER_API_KEY_RACH": "tok-rach"}

PRIVATE = {"routine", "day", "slot", "slot-entry", "sets-config", "repetitions-config",
           "max-repetitions-config", "workoutlog"}
EXERCISE_WRITE = {"exercise", "exercise-translation", "exerciseimage"}
CHILDREN = {"day": [("slot", "day")], "slot": [("slot-entry", "slot")],
            "slot-entry": [("sets-config", "slot_entry"), ("repetitions-config", "slot_entry"),
                           ("max-repetitions-config", "slot_entry"), ("workoutlog", "slot_entry")]}


class Resp:
    def __init__(self, code, body):
        self.status_code = code
        self._body = body
        self.text = json.dumps(body)
        self.content = self.text.encode() if body is not None else b""

    def json(self):
        return self._body


# wger 2.7's muscle rows (id, Latin name, English name) as this instance has them
MUSCLES = [("Biceps brachii", "Biceps"), ("Anterior deltoid", "Shoulders"), ("Serratus anterior", ""),
           ("Pectoralis major", "Chest"), ("Triceps brachii", "Triceps"), ("Rectus abdominis", "Abs"),
           ("Gastrocnemius", "Calves"), ("Gluteus maximus", "Glutes"), ("Trapezius", ""),
           ("Quadriceps femoris", "Quads"), ("Biceps femoris", "Hamstrings"), ("Latissimus dorsi", "Lats"),
           ("Brachialis", ""), ("Obliquus externus abdominis", ""), ("Soleus", ""), ("Erector spinae", "Lower back")]


class FakeWger:
    def __init__(self, lengths_unit=True):
        self.users = {"tok-jmack": ("jmack", True), "tok-rach": ("rach", False)}
        self.t = {k: {} for k in PRIVATE | EXERCISE_WRITE | {"language", "exercisecategory", "equipment",
                                                            "setting-repetitionunit", "setting-weightunit", "muscle"}}
        self.next_id = 1000
        self.writes = []
        self.writers = []
        self.images = {}
        for i, (s, n) in enumerate([("de", "Deutsch"), ("en", "English")], 1):
            self.t["language"][i] = {"id": i, "short_name": s, "full_name": n}
        for i, n in enumerate(["Arms", "Legs", "Abs", "Chest", "Back", "Shoulders", "Calves", "Cardio"], 8):
            self.t["exercisecategory"][i] = {"id": i, "name": n}
        for i, n in enumerate(["Barbell", "SZ-Bar", "Dumbbell", "Gym mat", "Swiss Ball", "Pull-up bar",
                               "none (bodyweight exercise)", "Bench", "Incline bench"], 1):
            self.t["equipment"][i] = {"id": i, "name": n}
        for i, (n, en) in enumerate(MUSCLES, 1):
            self.t["muscle"][i] = {"id": i, "name": n, "name_en": en}
        units = ["Repetitions", "Until Failure", "Seconds", "Minutes", "Miles", "Kilometers", "Max Reps", "Meters"]
        if lengths_unit:
            units.append("10-ft lengths")
        for i, n in enumerate(units, 1):
            self.t["setting-repetitionunit"][i] = {"id": i, "name": n}
        for i, n in enumerate(["kg", "lb", "Body Weight", "Plates"], 1):
            self.t["setting-weightunit"][i] = {"id": i, "name": n}
        # an upstream (wger.de) exercise whose name collides with the catalog's "Hammer Curl"
        self.t["exercise"][1] = {"id": 1, "category": 8, "equipment": [3]}
        self.t["exercise-translation"][1] = {"id": 1, "exercise": 1, "language": 2, "name": "Hammer Curls",
                                             "description_source": "Upstream text."}

    # -- helpers
    def rows(self, table, user=None):
        return [r for r in self.t[table].values() if table not in PRIVATE or r["_user"] == user]

    def delete(self, table, oid):
        self.t[table].pop(oid, None)
        for child, fk in CHILDREN.get(table, []):
            for cid in [c["id"] for c in self.t[child].values() if c.get(fk) == oid]:
                self.delete(child, cid)

    def pub(self, row):
        return {k: v for k, v in row.items() if not k.startswith("_")}

    def handle(self, method, url, headers, params=None, json=None, data=None, files=None):
        path = urlparse(url).path
        assert path.startswith("/api/v2/"), path
        parts = [p for p in path[len("/api/v2/"):].split("/") if p]
        tok = headers.get("Authorization", "").removeprefix("Token ")
        user, perm = self.users.get(tok, (None, False))
        if parts == ["check-permission"]:
            return Resp(200, {"result": perm})
        table = parts[0]
        oid = int(parts[1]) if len(parts) > 1 else None
        if table in PRIVATE and user is None:
            return Resp(401, {"detail": "not authenticated"})
        if method == "GET":
            params = dict(params or {})
            limit, offset = int(params.pop("limit", 20)), int(params.pop("offset", 0))
            rows = self.rows(table, user)
            if oid is not None:
                row = next((r for r in rows if r["id"] == oid), None)
                return Resp(404, {"detail": "nf"}) if row is None else Resp(200, self.pub(row))
            rows = [r for r in rows if all(str(r.get(k)) == str(v) for k, v in params.items())]
            rows.sort(key=lambda r: r["id"])
            page = rows[offset:offset + limit]
            more = offset + limit < len(rows)
            return Resp(200, {"count": len(rows), "next": "more" if more else None,
                              "results": [self.pub(r) for r in page]})
        self.writes.append((method, table))
        self.writers.append(user)
        if table in EXERCISE_WRITE and not perm:
            return Resp(403, {"detail": "not trustworthy"})
        if method == "DELETE":
            if oid not in self.t[table]:
                return Resp(404, {"detail": "nf"})
            self.delete(table, oid)
            return Resp(204, None)
        body = dict(json if json is not None else data or {})
        if method == "PATCH":
            row = self.t[table].get(oid)
            if row is None or (table in PRIVATE and row["_user"] != user):
                return Resp(404, {"detail": "nf"})
            row.update(self.norm(table, body))
            return Resp(200, self.pub(row))
        # POST
        if table == "exercise-translation":
            norm = re.sub(r"[\s\-_]+", "", body["name"].lower())
            for r in self.t[table].values():
                if re.sub(r"[\s\-_]+", "", r["name"].lower()).rstrip("s") == norm.rstrip("s"):
                    return Resp(400, {"name": f'The name "{body["name"]}" is too similar to the existing '
                                              f'exercise "{r["name"]}".'})
        if table == "exerciseimage":
            name, content, _ = files["image"]
            body = {"exercise": int(data["exercise"]), "is_main": data["is_main"] == "true",
                    "image": f"/media/{name}", "style": data.get("style"),
                    "is_ai_generated": data.get("is_ai_generated") == "true"}
            self.images[self.next_id + 1] = content
        self.next_id += 1
        row = {"id": self.next_id, **self.norm(table, body), "_user": user}
        self.t[table][self.next_id] = row
        return Resp(201, self.pub(row))

    @staticmethod
    def norm(table, body):
        if table.endswith("-config") and "value" in body:
            body = {**body, "value": f"{float(body['value']):.2f}"}    # wger returns decimals as strings
        return body

    def routine_tree(self, user):
        """{weekday name: [[(slug, unit name, sets, reps, max), ...] per slot]} for one user's routine."""
        slug_of = {}
        for tr in self.t["exercise-translation"].values():
            m = gw.MARKER_RE.search(tr.get("description_source", ""))
            if m:
                slug_of[tr["exercise"]] = m.group("slug")
        unit = {u["id"]: u["name"] for u in self.t["setting-repetitionunit"].values()}

        def cfg(table, eid):
            v = [c["value"] for c in self.t[table].values() if c["slot_entry"] == eid]
            return int(float(v[0])) if v else None

        (r,) = [r for r in self.rows("routine", user) if r["name"] == gw.ROUTINE_NAME]
        out = {}
        for d in sorted((d for d in self.rows("day", user) if d["routine"] == r["id"]), key=lambda d: d["order"]):
            slots = sorted((s for s in self.rows("slot", user) if s["day"] == d["id"]), key=lambda s: s["order"])
            out[d["name"]] = [[(slug_of[e["exercise"]], unit[e["repetition_unit"]], cfg("sets-config", e["id"]),
                                cfg("repetitions-config", e["id"]), cfg("max-repetitions-config", e["id"]))
                               for e in sorted((e for e in self.rows("slot-entry", user) if e["slot"] == s["id"]),
                                               key=lambda e: e["order"])]
                              for s in slots]
        return r, out


class FakeSession:
    def __init__(self, fake):
        self.fake = fake
        self.headers = {}

    def request(self, method, url, timeout=None, **kw):
        return self.fake.handle(method, url, self.headers, **kw)


# ---------------------------------------------------------------- synthetic guide
WALK = ("treadmill-walk", "Treadmill — zone-2 walk", "15 min · easy pace, zone 2")
LONG_WALK = ("treadmill-walk", "Treadmill — zone-2 walk", "25–30 min · zone 2 (today's walk)")
PT = [("pt-slr", "PT — Straight leg raise", "2 × 15–30 / leg"),
      ("pt-bridge", "PT — Bridge with knee extension", "2 × 10 / leg"),
      ("pt-abduction", "PT — Side-lying hip abduction", "2 × 15–30 / side"),
      ("adductor", "Adductor squeeze", "3 × 20 s")]
HANDS = [("hand-warm", "Warm up the hands", "30 s"),
         ("hand-glides", "Tendon glides", "5 × each position"),
         ("hand-median", "Median nerve glide (gentle)", "5 slow reps / side"),
         ("finger-ext", "Finger extensions", "2 × 15–20 · level 1 → 2 → 3")]
FLEX = [("flex-side-bend", "Standing side bend", "2 × 20 s / side"),
        ("flex-cat-cow", "Cat–cow on forearms", "8 slow reps")]
EVERY = ("", "Every day: PT stretches", "~6 min — any time today")
SUPER = " · superset: alternate with the next exercise, rest after each pair"
DAYS = {
    "Monday": [WALK, *PT, *HANDS, ("box-squat", "Goblet box squat", "3 × 8–12 · start light"),
               ("wall-sit", "Wall sit", "4 × 30–45 s"), EVERY],
    "Tuesday": [WALK, ("arm-circles", "Arm circles", "30 s each way"),
                ("hammer-curl", "Hammer curl", "2 × 10–12 · start light" + SUPER),
                ("triceps-ext", "Overhead triceps extension", "2 × 10–12"),
                ("farmer", "Grip — farmer's hold", "3 × 20–30 s · start light"), EVERY],
    "Wednesday": [LONG_WALK, *FLEX, EVERY],
    "Thursday": [WALK, *PT, *HANDS, ("step-up", "Low step-up", "3 × 8–10 / leg · body weight"),
                 ("tib-raise", "Tibialis toe raise", "2 × 15"), EVERY],
    "Friday": [WALK, ("suitcase", "Suitcase carry (10-ft lengths)", "3 × 4 lengths (10 ft) / side, one dumbbell"),
               ("side-plank", "Side plank", "2 × 20 s / side"), EVERY],
    "Saturday": [LONG_WALK, *FLEX, *PT, *HANDS, EVERY],
    "Sunday": [WALK, *FLEX, ("flex-sphinx", "Sphinx", "3 × 20 s · Sunday"),
               ("flex-legs-up-wall", "Legs up the wall", "3–5 min · Sunday or any evening"), EVERY],
}
EVERYDAY = [("pt-hamstring", "PT — Hamstring stretch", "3 × 30 s hold · daily"),
            ("pt-quad", "PT — Quad stretch", "3 × 30 s hold · daily")]
NO_POSTER = "wall-sit"


def all_slugs() -> set[str]:
    """Every person's catalog slugs: the top-level list + every "plans" entry."""
    cat = gen_workout.load()
    return {s for pl in [cat, *cat.get("plans", {}).values()] for it in pl["items"] for s in it["slugs"]}


def rach_plan() -> dict:
    return gen_workout.load()["plans"]["rach"]


def step(n, s):
    slug, title, dose = s
    return {"num": str(n), "title": title, "dose": dose, "slug": slug,
            "image": f"exercise/ex-{slug}-rom.png" if slug else "", "everyday": not slug}


@pytest.fixture
def guide(tmp_path):
    d = tmp_path / "guide"
    (d / "exercise").mkdir(parents=True)
    data = {"version": 1, "everyday": [step(i, s) for i, s in enumerate(EVERYDAY, 1)], "days": []}
    for name, steps in DAYS.items():
        data["days"].append({"id": name[:3].lower(), "name": name, "title": f"{name} — test", "summary": "s",
                             "youneed": "", "steps": [step(i, s) for i, s in enumerate(steps, 1)]})
    data["days"][0]["steps"][1]["cues"] = ["Lock the knee", "Lift slowly"]
    (d / "workout.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    slugs = {s for it in gen_workout.load()["items"] for s in it["slugs"]} | {"arm-circles"}
    for s in slugs - {NO_POSTER}:
        (d / "exercise" / f"ex-{s}-rom.png").write_bytes(b"\x89PNG fake " + s.encode())
    return d


@pytest.fixture
def fake(monkeypatch):
    f = FakeWger()
    monkeypatch.setattr(gw.requests, "Session", lambda: FakeSession(f))
    monkeypatch.setenv("WGER_URL", URL)
    for k, v in KEYS.items():
        monkeypatch.setenv(k, v)
    return f


def run(guide, *args):
    return gw.main(["--posters", str(guide), *args], today=TODAY)


# ---------------------------------------------------------------- dose parsing
# one sample per catalog kind (score has no dose: it is not in the routine)
KIND_SAMPLES = {
    "w": [("3 × 8–12 · start light", (3, 8, 12, "reps")), ("2 × 15 · start light", (2, 15, None, "reps"))],
    "r": [("2 × 15–30 / leg", (2, 15, 30, "reps")), ("3 × 8–12 (stop 2 short of failure)", (3, 8, 12, "reps"))],
    "t": [("3 × 20 s", (3, 20, None, "seconds")), ("4 × 30–45 s", (4, 30, 45, "seconds"))],
    "wt": [("3 × 20–30 s · start light", (3, 20, 30, "seconds"))],
    "wl": [("3 × 4 lengths (10 ft) / side, one dumbbell", (3, 4, None, "lengths"))],
    "min": [("15 min · easy pace, zone 2 · before the session", (1, 15, None, "minutes")),
            ("25–30 min · zone 2 (today's walk)", (1, 25, 30, "minutes"))],
    "done": [("30 s", (1, 30, None, "seconds")), ("5 × each position", (1, 5, None, "reps")),
             ("2 × 20 s / hand", (2, 20, None, "seconds")), ("5 slow reps / side", (1, 5, None, "reps")),
             ("8 slow reps", (1, 8, None, "reps")), ("30 s each way", (1, 30, None, "seconds")),
             ("3–5 min · Sunday or any evening", (1, 3, 5, "minutes")),
             ("3 × 30 s hold · daily", (3, 30, None, "seconds")), ("2 × 10", (2, 10, None, "reps"))],
}


def test_dose_samples_cover_every_catalog_kind():
    kinds = {it["kind"] for it in gen_workout.load()["items"]} - {"score"}
    assert kinds <= set(KIND_SAMPLES), kinds - set(KIND_SAMPLES)


@pytest.mark.parametrize("kind,text,want", [(k, t, w) for k, v in KIND_SAMPLES.items() for t, w in v])
def test_parse_dose(kind, text, want):
    d = gw.parse_dose(text, kind)
    assert (d["sets"], d["value"], d["max"], d["unit"]) == want
    if kind in gw.KIND_UNIT:
        assert d["unit"] == gw.KIND_UNIT[kind]


def test_parse_dose_unparsed_falls_back_to_kind_unit():
    d = gw.parse_dose("as many as feel good", "t")
    assert d["sets"] is None and d["unit"] == "seconds"


def test_every_guide_dose_parses(guide):
    warns = []
    gw.plan_days(json.loads((guide / "workout.json").read_text()), gen_workout.load(), warns.append)
    assert warns == []


# ---------------------------------------------------------------- end to end
def test_dry_run_writes_nothing(guide, fake, capsys):
    assert run(guide) == 0
    assert fake.writes == []
    out = capsys.readouterr().out
    assert "would: jmack: POST exercise/ treadmill-walk" in out
    assert "would: rach: POST routine/" in out
    assert "tok-" not in out


def test_apply_then_second_run_makes_no_writes(guide, fake, capsys):
    assert run(guide, "--apply") == 0
    first = len(fake.writes)
    assert first > 0
    fake.writes.clear()
    assert run(guide, "--apply") == 0
    assert fake.writes == []
    assert "0 write(s) made" in capsys.readouterr().out
    assert run(guide, "--check") == 0


def test_exercises_markers_images_and_owner(guide, fake):
    assert run(guide, "--apply") == 0
    trs = [t for t in fake.t["exercise-translation"].values() if gw.MARKER_RE.search(t["description_source"])]
    slugs = {gw.MARKER_RE.search(t["description_source"]).group("slug") for t in trs}
    assert slugs == all_slugs() | {"arm-circles"}      # both plans + the guide-only slug
    assert all(t["_user"] == "jmack" for t in trs)     # exercises are global: the owner writes them
    by = {gw.MARKER_RE.search(t["description_source"]).group("slug"): t for t in trs}
    assert by["pt-slr"]["name"] == "Straight Leg Raise"
    assert by["hand-glides"]["name"] == "Tendon glides"          # multi-slug item -> guide title
    assert by["hammer-curl"]["name"] == "Hammer Curl · kdk plan"   # collided with upstream "Hammer Curls"
    assert "- Lock the knee\n- Lift slowly" in by["pt-slr"]["description_source"]
    assert "- Mon, Tue, Thu, Fri, Sun: 15 min · easy pace, zone 2" in by["treadmill-walk"]["description_source"]
    assert "img:none" in by[NO_POSTER]["description_source"]
    m = gw.MARKER_RE.search(by["pt-slr"]["description_source"])
    assert m.group("img") != "none" and int(m.group("imgid")) in fake.t["exerciseimage"]
    hers = {s for it in rach_plan()["items"] for s in it["slugs"]}      # no posters yet (fixture as in life)
    assert len(fake.t["exerciseimage"]) == len(slugs - hers) - 1        # every other slug but NO_POSTER
    assert sum(1 for t in fake.t["exerciseimage"].values()
               if t["exercise"] == by[NO_POSTER]["exercise"]) == 0


def test_image_upload_skipped_when_unchanged_and_replaced_when_changed(guide, fake):
    assert run(guide, "--apply") == 0
    fake.writes.clear()
    assert run(guide, "--apply") == 0
    assert ("POST", "exerciseimage") not in fake.writes
    old = {i for i, r in fake.t["exerciseimage"].items() if r["image"].endswith("ex-rdl-rom.png")}
    (guide / "exercise" / "ex-rdl-rom.png").write_bytes(b"\x89PNG new poster")
    fake.writes.clear()
    assert run(guide, "--apply") == 0
    assert sorted(fake.writes) == [("DELETE", "exerciseimage"), ("PATCH", "exercise-translation"),
                                   ("POST", "exerciseimage")]
    now = {i for i, r in fake.t["exerciseimage"].items() if r["image"].endswith("ex-rdl-rom.png")}
    assert len(now) == 1 and not (now & old)
    assert fake.images[now.pop()] == b"\x89PNG new poster"


def test_weekday_mapping(guide, fake):
    assert run(guide, "--apply") == 0
    for user in ("jmack",):
        r, tree = fake.routine_tree(user)
        start = dt.date.fromisoformat(r["start"])
        assert start == dt.date(2026, 11, 2) and start.weekday() == 0
        assert (dt.date.fromisoformat(r["end"]) - start).days <= 120
        assert r["fit_in_week"] is True
        assert list(tree) == gw.WEEKDAYS
        days = [d for d in fake.rows("day", user)]
        assert sorted(d["order"] for d in days) == list(range(1, 8))
        assert not any(d["need_logs_to_advance"] or d["is_rest"] for d in days)
        for wd, slots in tree.items():
            flat = [e[0] for s in slots for e in s]
            assert flat[0] == "treadmill-walk", wd
            assert flat[-2:] == ["pt-hamstring", "pt-quad"], wd           # stretches every day
            assert ("pt-slr" in flat) == (wd in ("Monday", "Thursday", "Saturday")), wd
            assert ("hand-warm" in flat) == (wd in ("Monday", "Thursday", "Saturday")), wd
        assert tree["Monday"][0] == [("treadmill-walk", "Minutes", 1, 15, None)]
        assert tree["Wednesday"][0] == [("treadmill-walk", "Minutes", 1, 25, 30)]
        assert tree["Saturday"][0] == [("treadmill-walk", "Minutes", 1, 25, 30)]
        assert ("flex-sphinx", "Seconds", 3, 20, None) in [e for s in tree["Sunday"] for e in s]
        assert [("hammer-curl", "Repetitions", 2, 10, 12), ("triceps-ext", "Repetitions", 2, 10, 12)] \
            in tree["Tuesday"]                                            # superset = one slot, two entries
        assert [("suitcase", "10-ft lengths", 3, 4, None)] in tree["Friday"]
        assert [("pt-slr", "Repetitions", 2, 15, 30)] in tree["Monday"]
    lb = next(u["id"] for u in fake.t["setting-weightunit"].values() if u["name"] == "lb")
    assert all(e["weight_unit"] == lb for e in fake.t["slot-entry"].values())
    assert not any("weight" in t for _, t in fake.writes)                 # weights left blank


def test_check_detects_drift_without_writing(guide, fake, capsys):
    assert run(guide, "--apply") == 0
    cfg = next(c for c in fake.t["sets-config"].values() if c["_user"] == "rach")
    cfg["value"] = "9.00"
    fake.writes.clear()
    capsys.readouterr()
    assert run(guide, "--check") == 1
    assert fake.writes == []
    assert "DRIFT: rach: PATCH sets-config/" in capsys.readouterr().out
    assert run(guide, "--apply") == 0
    assert run(guide, "--check") == 0


def test_check_flags_a_routine_about_to_expire(guide, fake):
    assert run(guide, "--apply") == 0
    later = TODAY + dt.timedelta(days=100)
    assert gw.main(["--posters", str(guide), "--check"], today=later) == 1
    assert gw.main(["--posters", str(guide), "--apply"], today=later) == 0
    r, _ = fake.routine_tree("jmack")
    assert dt.date.fromisoformat(r["start"]) == later - dt.timedelta(days=later.weekday())


def test_person_filter(guide, fake):
    assert run(guide, "--apply", "--person", "jmack") == 0
    assert not fake.rows("routine", "rach")
    fake.writes.clear()
    fake.writers.clear()
    assert run(guide, "--apply", "--person", "rach") == 0
    assert fake.writes and all(t in PRIVATE for _, t in fake.writes)   # rach never writes exercises
    assert set(fake.writers) == {"rach"}
    assert fake.routine_tree("rach")[1]["Monday"][0] == [("glute-calf-raise", "Repetitions", 1, 15, None)]


def test_person_without_owner_cannot_create_exercises(guide, fake, capsys):
    assert run(guide, "--apply", "--person", "rach") == 2
    assert "first person's key" in capsys.readouterr().err
    assert fake.writes == []


def test_missing_lengths_unit_prints_admin_step_and_stops(guide, monkeypatch, capsys):
    f = FakeWger(lengths_unit=False)
    monkeypatch.setattr(gw.requests, "Session", lambda: FakeSession(f))
    monkeypatch.setenv("WGER_URL", URL)
    for k, v in KEYS.items():
        monkeypatch.setenv(k, v)
    assert run(guide, "--apply") == 3
    out = capsys.readouterr().out
    assert "RepetitionUnit.objects.get_or_create(name='10-ft lengths'" in out
    assert "docker exec -i wger-web python3 manage.py shell" in out
    assert not f.t["routine"]


def test_slot_with_logs_is_retired_not_deleted(guide, fake):
    assert run(guide, "--apply") == 0
    r, _ = fake.routine_tree("jmack")
    farmer_ex = next(t["exercise"] for t in fake.t["exercise-translation"].values()
                     if "kdk:farmer " in t.get("description_source", ""))
    entry = next(e for e in fake.rows("slot-entry", "jmack") if e["exercise"] == farmer_ex)
    fake.t["workoutlog"][1] = {"id": 1, "routine": r["id"], "slot_entry": entry["id"], "_user": "jmack"}
    data = json.loads((guide / "workout.json").read_text())
    tue = next(d for d in data["days"] if d["name"] == "Tuesday")
    tue["steps"] = [s for s in tue["steps"] if s["slug"] != "farmer"]
    (guide / "workout.json").write_text(json.dumps(data, ensure_ascii=False))
    assert run(guide, "--apply") == 0
    assert entry["id"] in fake.t["slot-entry"] and 1 in fake.t["workoutlog"]   # log survives
    slot = fake.t["slot"][entry["slot"]]
    assert slot["comment"].startswith(gw.RETIRED) and slot["order"] > gw.RETIRED_ORDER
    fake.writes.clear()
    assert run(guide, "--apply") == 0 and fake.writes == []                    # stays idempotent
    assert [e["id"] for e in fake.rows("slot-entry", "jmack") if e["exercise"] == farmer_ex] == [entry["id"]]


def test_missing_key_is_an_error_not_a_prompt(guide, fake, monkeypatch, capsys):
    monkeypatch.delenv("WGER_API_KEY_RACH")
    assert run(guide) == 2
    assert "WGER_API_KEY_RACH is not set" in capsys.readouterr().err


# ---------------------------------------------------------------- presentation (muscles, marker, images, equipment)
def by_slug(fake):
    out = {}
    for t in fake.t["exercise-translation"].values():
        m = gw.MARKER_RE.search(t.get("description_source", ""))
        if m:
            out[m.group("slug")] = t
    return out


def muscle_ids(fake, names):
    by = {k: m["id"] for m in fake.t["muscle"].values() for k in (m["name_en"], m["name"]) if k}
    return sorted(by[n] for n in names)


def test_sidecar_covers_every_slug_with_known_names(guide):
    side = gw.load_sidecar()
    assert all_slugs() | {"arm-circles"} == set(side)
    fake_names = {k for n, en in MUSCLES for k in (n, en) if k} | set(gw.CUSTOM_MUSCLES)
    for slug, e in side.items():
        assert set(e["muscles"] + e["secondary"]) <= fake_names, slug
        assert e["equipment"], slug
        assert e["muscles"] or e.get("what"), f"{slug}: no muscle and no 'what' line"


def test_sidecar_typo_is_an_error(tmp_path):
    bad = tmp_path / "side.json"
    bad.write_text(json.dumps({"exercises": {"rdl": {"muscles": ["Hamstring"], "secondary": [],
                                                      "equipment": ["Dumbbell"]}}}))
    gw.load_sidecar(bad)                                   # offline: the server may have it
    with pytest.raises(gw.WgerError, match="unknown muscle"):
        gw.load_sidecar(bad, known={"Hamstrings"})


def test_muscles_written_and_drift_patched(guide, fake, capsys):
    assert run(guide, "--apply") == 0
    by = by_slug(fake)
    side = gw.load_sidecar()
    for slug in ("rdl", "pt-slr", "flex-sphinx", "side-plank"):
        ex = fake.t["exercise"][by[slug]["exercise"]]
        assert sorted(ex["muscles"]) == muscle_ids(fake, side[slug]["muscles"]), slug
        assert sorted(ex["muscles_secondary"]) == muscle_ids(fake, side[slug]["secondary"]), slug
    assert fake.t["exercise"][by["hand-glides"]["exercise"]]["muscles"] == []     # hands: no wger muscle
    fake.t["exercise"][by["rdl"]["exercise"]]["muscles"] = []
    fake.writes.clear()
    capsys.readouterr()
    assert run(guide, "--check") == 1
    assert "PATCH exercise/" in capsys.readouterr().out and fake.writes == []
    assert run(guide, "--apply") == 0
    assert fake.writes == [("PATCH", "exercise")]
    assert run(guide, "--check") == 0


def test_marker_is_a_trailing_html_comment_and_template_blocks(guide, fake):
    assert run(guide, "--apply") == 0
    by = by_slug(fake)
    src = by["pt-slr"]["description_source"]
    visible, _, comment = src.rpartition("\n\n<!-- ")
    assert comment.startswith("kdk:pt-slr v:") and comment.endswith(" -->")
    assert "kdk:" not in visible and "<!--" not in visible
    assert "part of our home workout plan" not in visible
    assert visible.startswith("Works the quads; also the abs.")
    assert "**Dose**\n\n- Mon, Thu, Sat: 2 × 15–30 / leg" in visible
    assert "**How**\n\n- Lock the knee\n- Lift slowly" in visible
    assert visible.endswith("**Safety:** " + gw.SAFETY_DEFAULT)
    assert "- Every day: 3 × 30 s hold · daily" in by["pt-hamstring"]["description_source"]
    assert gw.SAFETY_STRETCH in by["pt-hamstring"]["description_source"]
    assert gw.SAFETY_WEIGHTED in by["rdl"]["description_source"]
    assert "**Dose**: not in the weekly plan" in by["flat-press"]["description_source"]
    assert by["box-squat"]["description_source"].startswith("Goblet box squat. Works the quads and glutes")


def test_legacy_plain_marker_is_migrated_in_place(guide, fake):
    assert run(guide, "--apply") == 0
    tr = by_slug(fake)["rdl"]
    m = gw.MARKER_RE.search(tr["description_source"])
    tr["description_source"] = ("Romanian deadlift. This exercise is part of our home workout plan.\n"
                                + m.group(0).replace(m.group("v"), "0" * 12))       # the pre-2026-11-07 shape
    n_ex, n_img = len(fake.t["exercise"]), len(fake.t["exerciseimage"])
    fake.writes.clear()
    assert run(guide, "--apply") == 0
    assert fake.writes == [("PATCH", "exercise-translation")]         # text only: same exercise, same image
    assert (len(fake.t["exercise"]), len(fake.t["exerciseimage"])) == (n_ex, n_img)
    assert tr["description_source"].endswith(" -->") and "part of our home" not in tr["description_source"]


def test_hand_edited_description_is_restored(guide, fake):
    assert run(guide, "--apply") == 0
    tr = by_slug(fake)["plank"]
    tr["description_source"] = tr["description_source"].replace("**Safety:**", "**Careful:**")
    assert run(guide, "--check") == 1
    assert run(guide, "--apply") == 0 and "**Safety:**" in tr["description_source"]


def test_step_images_upload_in_order_replace_the_poster_and_stay_idempotent(guide, fake):
    assert run(guide, "--apply") == 0
    ex_id = by_slug(fake)["rdl"]["exercise"]
    for n in (2, 1, 3):
        (guide / "exercise" / f"ex-rdl-step{n}.png").write_bytes(f"\x89PNG step {n}".encode())
    (guide / "exercise" / "ex-rdl-step10.png").write_bytes(b"\x89PNG step 10")
    fake.writes.clear()
    assert run(guide, "--apply") == 0
    imgs = sorted((i for i in fake.t["exerciseimage"].values() if i["exercise"] == ex_id), key=lambda i: i["id"])
    assert [i["image"] for i in imgs] == [f"/media/ex-rdl-step{n}.png" for n in (1, 2, 3, 10)]   # numeric order
    assert [i["is_main"] for i in imgs] == [True, False, False, False]
    assert all(i["style"] == gw.STYLE_PHOTO and i["is_ai_generated"] for i in imgs)
    m = gw.MARKER_RE.search(by_slug(fake)["rdl"]["description_source"])
    assert [int(x) for x in m.group("imgid").split(",")] == [i["id"] for i in imgs]
    assert sorted(fake.writes) == sorted([("POST", "exerciseimage")] * 4 + [("DELETE", "exerciseimage"),
                                                                            ("PATCH", "exercise-translation")])
    fake.writes.clear()
    assert run(guide, "--apply") == 0 and fake.writes == []
    (guide / "exercise" / "ex-rdl-step10.png").unlink()
    (guide / "exercise" / "ex-rdl-step2.png").write_bytes(b"\x89PNG step 2 v2")
    assert run(guide, "--check") == 1
    assert run(guide, "--apply") == 0
    now = sorted((i for i in fake.t["exerciseimage"].values() if i["exercise"] == ex_id), key=lambda i: i["id"])
    assert [i["image"] for i in now] == [f"/media/ex-rdl-step{n}.png" for n in (1, 2, 3)]
    assert not {i["id"] for i in now} & {i["id"] for i in imgs}
    assert fake.images[now[1]["id"]] == b"\x89PNG step 2 v2"


def test_lone_poster_keeps_the_legacy_hash(tmp_path):
    d = tmp_path / "exercise"
    d.mkdir()
    (d / "ex-rdl-rom.png").write_bytes(b"poster")
    (d / "ex-rdl-x-step1.png").write_bytes(b"other slug")          # ex-rdl-x is not rdl
    files = gw.image_files(tmp_path, "rdl")
    assert [f.name for f in files] == ["ex-rdl-rom.png"]
    assert gw.images_hash(files) == gw.h12(b"poster")
    assert gw.images_hash([]) == "none"


def test_missing_equipment_warns_once_then_links_when_added(guide, fake, capsys):
    assert run(guide, "--apply") == 0
    err = capsys.readouterr().err
    assert err.count("wger has no equipment 'Treadmill'") == 1
    ex = fake.t["exercise"][by_slug(fake)["treadmill-walk"]["exercise"]]
    assert ex["equipment"] == []
    fake.t["equipment"][50] = {"id": 50, "name": "Treadmill"}       # the admin step ran
    assert run(guide, "--check") == 1
    assert run(guide, "--apply") == 0
    assert ex["equipment"] == [50]
    eq = {e["name"]: e["id"] for e in fake.t["equipment"].values()}
    assert sorted(fake.t["exercise"][by_slug(fake)["incline-press"]["exercise"]]["equipment"]) == \
        sorted([eq["Dumbbell"], eq["Incline bench"]])
    assert fake.t["exercise"][by_slug(fake)["plank"]["exercise"]]["equipment"] == [eq["Gym mat"]]


def test_check_counts_a_slug_missing_from_the_sidecar(guide, fake, monkeypatch, capsys):
    assert run(guide, "--apply") == 0
    side = gw.load_sidecar()
    side.pop("arm-circles")
    monkeypatch.setattr(gw, "load_sidecar", lambda path=gw.SIDECAR: side)
    capsys.readouterr()
    assert run(guide, "--check") == 1
    assert "no entry for arm-circles" in capsys.readouterr().out


# the household list after runbook step 12 (owner answers 2026-11-07)
KEEP_EQUIPMENT = {"Dumbbell", "Gym mat", "none (bodyweight exercise)", "Bench", "Incline bench"}
LOCAL_EQUIPMENT = ["Treadmill", "Finger stretcher", "Strap", "Towel", "Pillow", "Door frame", "Wall"]
# Rachel's plan (runbook wger step 12b): local rows to add + built-ins step 12 deleted; owner decides
PENDING_EQUIPMENT = {"Pool", "Recumbent bike", "Resistance band", "Swiss Ball"}


def add_rows(fake, table, names, **extra):
    for i, n in enumerate(names, 200 + len(fake.t[table])):
        fake.t[table][i] = {"id": i, "name": n, **extra}


def test_sidecar_uses_only_the_household_equipment():
    used = {e for v in gw.load_sidecar().values() for e in v["equipment"]}
    allowed = KEEP_EQUIPMENT | set(LOCAL_EQUIPMENT) | PENDING_EQUIPMENT
    assert used <= allowed, used - allowed
    assert set(LOCAL_EQUIPMENT) <= used                               # every local row is used by a slug


def test_local_equipment_rows_link_per_slug(guide, fake):
    add_rows(fake, "equipment", LOCAL_EQUIPMENT)
    assert run(guide, "--apply") == 0
    eq = {e["name"]: e["id"] for e in fake.t["equipment"].values()}
    by = by_slug(fake)

    def names(slug):
        return sorted(n for n, i in eq.items() if i in fake.t["exercise"][by[slug]["exercise"]]["equipment"])
    assert names("finger-ext") == ["Finger stretcher"]
    assert names("pt-hamstring") == ["Gym mat", "Strap"]
    assert names("towel-wring") == ["Towel"]
    assert names("adductor") == ["Gym mat", "Pillow"]
    assert names("flex-doorway-chest") == ["Door frame"]
    assert names("wall-sit") == ["Wall"]
    assert names("treadmill-walk") == ["Treadmill"]


def test_custom_muscles_left_out_until_created_then_linked(guide, fake, capsys):
    assert run(guide, "--apply") == 0
    err = capsys.readouterr().err
    for n in gw.CUSTOM_MUSCLES:
        assert err.count(f"wger has no muscle {n!r}") == 1, n
    by = by_slug(fake)
    glides = fake.t["exercise"][by["hand-glides"]["exercise"]]
    assert glides["muscles"] == [] and glides["muscles_secondary"] == []
    assert by["hand-glides"]["description_source"].startswith("Moves the finger tendons")
    assert by["wrist-curl"]["description_source"].startswith("Works the forearm flexors; also the brachialis.")
    fake.t["muscle"][90] = {"id": 90, "name": "Forearm flexors", "name_en": "Forearm flexors"}   # step 13a
    assert run(guide, "--check") == 1
    assert run(guide, "--apply", "--person", "jmack") == 0
    assert glides["muscles"] == [90] and glides["muscles_secondary"] == []      # Hand intrinsics still missing
    wc = fake.t["exercise"][by["wrist-curl"]["exercise"]]
    assert wc["muscles"] == [90] and wc["muscles_secondary"] == muscle_ids(fake, ["Brachialis"])
    fake.writes.clear()
    assert run(guide, "--apply") == 0 and fake.writes == []


def test_muscle_typo_is_an_error_at_run_time(guide, fake, monkeypatch, capsys):
    side = gw.load_sidecar()
    side["rdl"] = {**side["rdl"], "muscles": ["Hamstring"]}
    monkeypatch.setattr(gw, "load_sidecar", lambda path=gw.SIDECAR, known=None: side)
    assert run(guide, "--apply") == 2
    assert "wger has no muscle 'Hamstring' (for rdl)" in capsys.readouterr().err


# ---------------------------------------------------------------- per-person plans
def test_jacob_specs_and_days_ignore_the_other_plans(guide):
    """Jacob's exercise texts + week are what they were before per-person plans existed."""
    cat = gen_workout.load()
    g = json.loads((guide / "workout.json").read_text())
    sources = gw.plan_sources(cat, lambda: g)
    assert sources["jmack"] == (cat, g)
    alone = gw.exercise_specs(cat, g, guide)
    merged = gw.all_specs(sources, guide, lambda m: None)
    assert list(merged)[:len(alone)] == list(alone)
    assert all(merged[k]["text"] == alone[k]["text"] for k in alone)
    assert gw.plan_days(*sources["jmack"][::-1], lambda m: None) == gw.plan_days(g, cat, lambda m: None)


def test_rach_routine_is_her_plan(guide, fake):
    assert run(guide, "--apply") == 0
    _, tree = fake.routine_tree("rach")
    assert list(tree) == gw.WEEKDAYS
    desk = ["glute-calf-raise", "seated-nerve-glide", "kneeling-hip-flexor"]
    evening = ["wall-slide", "pelvic-tilt", "side-lying-rest"]
    session = {"Monday": ["high-box-squat", "pallof-press", "wall-pushup"],
               "Tuesday": ["water-walk", "recumbent-bike"], "Thursday": ["elevated-rdl", "seated-band-row", "glute-bridge"],
               "Friday": ["water-walk", "recumbent-bike"]}
    for wd, slots in tree.items():
        assert [e[0] for s in slots for e in s] == desk + session.get(wd, []) + evening, wd
    assert [("water-walk", "Minutes", 1, 20, 30), ("recumbent-bike", "Minutes", 1, 20, 25)] in tree["Tuesday"]
    assert [("high-box-squat", "Repetitions", 3, 8, None)] in tree["Monday"]
    assert [("kneeling-hip-flexor", "Seconds", 1, 30, None)] in tree["Sunday"]
    assert [("side-lying-rest", "Minutes", 1, 3, 5)] in tree["Wednesday"]
    either = [s for s in fake.rows("slot", "rach") if s["comment"] == gw.EITHER_COMMENT]
    assert len(either) == 2                                              # Tue + Fri, one slot each
    jm = {e[0] for s in fake.routine_tree("jmack")[1]["Monday"] for e in s}
    assert not jm & set(desk + evening)                                  # Jacob's week has none of hers


def test_every_rach_dose_parses():
    warns = []
    gw.plan_days(gw.plan_guide(rach_plan()), rach_plan(), warns.append)
    assert warns == []


def test_rach_exercise_text_lists_her_weekdays(guide, fake):
    assert run(guide, "--apply") == 0
    by = by_slug(fake)
    assert "Mon: 3 × 8 · RPE 6–7" in by["high-box-squat"]["description_source"]
    assert "Every day: 15 reps" in by["glute-calf-raise"]["description_source"]
    assert "Tue, Fri: 20–30 min" in by["water-walk"]["description_source"]


def test_a_slug_in_two_plans_is_an_error(guide):
    cat = gen_workout.load()
    g = json.loads((guide / "workout.json").read_text())
    pl = json.loads(json.dumps(cat["plans"]["rach"]))
    pl["items"][0]["slugs"] = ["box-squat"]
    pl["items"][0]["steps"][0]["slug"] = "box-squat"
    cat["plans"]["rach"] = pl
    with pytest.raises(gw.WgerError, match="box-squat is in the plans of jmack and rach"):
        gw.all_specs(gw.plan_sources(cat, lambda: g), guide, lambda m: None)
