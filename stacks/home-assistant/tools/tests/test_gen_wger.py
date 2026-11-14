"""gen_wger.py against an in-memory fake of the wger 2.7 REST API (requests.Session patched).

The fake holds one built-in exercise per wger_plan.json entry (uuid + English name as the plan
says), a few kdk-lab custom exercises and old kdk routines, so the tests cover the built-in
lookup, the description patch, the routines and --purge-kdk without a live server."""
import copy
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

TODAY = dt.date(2026, 11, 6)          # a Friday; Monday's routine must start Monday 2026-11-02
URL = "https://wger.test"
KEYS = {"WGER_API_KEY_JMACK": "tok-jmack", "WGER_API_KEY_RACH": "tok-rach"}
PLAN = gw.load_plan()

PRIVATE = {"routine", "day", "slot", "slot-entry", "sets-config", "repetitions-config",
           "max-repetitions-config", "weight-config", "rest-config", "max-rest-config", "workoutlog",
           "workoutsession"}
EXERCISE_WRITE = {"exercise", "exercise-translation"}
CHILDREN = {"routine": [("day", "routine"), ("workoutsession", "routine"), ("workoutlog", "routine")],
            "day": [("slot", "day"), ("workoutsession", "day")], "workoutsession": [("workoutlog", "session")],
            "slot": [("slot-entry", "slot")],
            "slot-entry": [(c, "slot_entry") for c in gw.CONFIGS.values()] + [("workoutlog", "slot_entry")],
            "exercise": [("exercise-translation", "exercise"), ("slot-entry", "exercise"),
                         ("workoutlog", "exercise")]}


class Resp:
    def __init__(self, code, body):
        self.status_code = code
        self._body = body
        self.text = json.dumps(body)
        self.content = self.text.encode() if body is not None else b""

    def json(self):
        return self._body


class FakeWger:
    def __init__(self, lengths_unit=True, plan=PLAN):
        self.users = {"tok-jmack": ("jmack", True), "tok-rach": ("rach", False)}
        self.t = {k: {} for k in PRIVATE | EXERCISE_WRITE | {"language", "equipment", "setting-repetitionunit",
                                                            "setting-weightunit"}}
        self.next_id = 5000
        self.writes = []
        for i, (s, n) in enumerate([("de", "Deutsch"), ("en", "English")], 1):
            self.t["language"][i] = {"id": i, "short_name": s, "full_name": n}
        for i, n in [(3, "Dumbbell"), (4, "Gym mat"), (7, "none (bodyweight exercise)"), (8, "Bench"),
                     (9, "Incline bench"), (13, "Treadmill")]:
            self.t["equipment"][i] = {"id": i, "name": n}
        units = ["Repetitions", "Until Failure", "Seconds", "Minutes", "Miles"] + (["10-ft lengths"] if lengths_unit else [])
        for i, n in enumerate(units, 1):
            self.t["setting-repetitionunit"][i] = {"id": i, "name": n}
        for i, n in enumerate(["kg", "lb", "Body Weight"], 1):
            self.t["setting-weightunit"][i] = {"id": i, "name": n}
        self.ex_of = {}
        for n, (key, ex) in enumerate(plan["exercises"].items(), 1):
            self.add_exercise(100 + n, ex["uuid"], ex["name"], "upstream author", [7])
            self.ex_of[key] = 100 + n
        # an upstream exercise with a German translation only: never in the plan, never touched
        self.add_exercise(90, "00000000-0000-4000-8000-000000000090", None, "wger.de", [])
        # the old custom exercises (two translated, one without a translation: invisible to the API)
        for i in (1, 2):
            self.add_exercise(i, f"00000000-0000-4000-8000-00000000000{i}", f"Old kdk {i} · kdk plan", "kdk-lab", [3])
        self.add_exercise(3, "00000000-0000-4000-8000-000000000003", None, "kdk-lab", [], translated=False)

    def add_exercise(self, eid, uuid, name, author, equipment, translated=True):
        self.t["exercise"][eid] = {"id": eid, "uuid": uuid, "license_author": author, "equipment": list(equipment)}
        if translated:
            tid = eid * 10
            self.t["exercise-translation"][tid] = {
                "id": tid, "exercise": eid, "language": 2 if name else 1, "name": name or "Ein Name",
                "description_source": "Upstream text.", "license_author": author}

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

    def visible_exercises(self):
        """wger 2.7: the exercise API lists only exercises with a translation."""
        have = {t["exercise"] for t in self.t["exercise-translation"].values()}
        return [e for e in self.t["exercise"].values() if e["id"] in have]

    def info(self, e):
        return {**self.pub(e), "equipment": [{"id": i, "name": self.t["equipment"][i]["name"]} for i in e["equipment"]],
                "translations": [self.pub(t) for t in self.t["exercise-translation"].values()
                                 if t["exercise"] == e["id"]]}

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
        params = dict(params or {})
        limit, offset = int(params.pop("limit", 20)), int(params.pop("offset", 0))

        def page(rows):
            rows = [r for r in rows if all(str(r.get(k)) == str(v) for k, v in params.items())]
            rows.sort(key=lambda r: r["id"])
            return Resp(200, {"count": len(rows), "next": "more" if offset + limit < len(rows) else None,
                              "results": rows[offset:offset + limit]})

        if method == "GET" and table in ("templates", "public-templates"):
            own = table == "templates"
            return page([self.pub(r) for r in self.t["routine"].values() if r.get("is_template")
                         and (r["_user"] == user if own else r.get("is_public"))])
        if method == "GET" and table == "exerciseinfo":
            rows = [self.info(e) for e in self.visible_exercises()]
            if oid is not None:
                row = next((r for r in rows if r["id"] == oid), None)
                return Resp(404, {"detail": "nf"}) if row is None else Resp(200, row)
            return page(rows)
        if table in PRIVATE and user is None:
            return Resp(401, {"detail": "not authenticated"})
        if method == "GET":
            rows = self.visible_exercises() if table == "exercise" else self.rows(table, user)
            if table == "routine":          # wger 2.7: everyone's public templates are listed too
                rows = rows + [r for r in self.t["routine"].values() if r["_user"] != user and r.get("is_public")]
            if oid is not None:
                row = next((r for r in rows if r["id"] == oid), None)
                return Resp(404, {"detail": "nf"}) if row is None else Resp(200, self.pub(row))
            return page([self.pub(r) for r in rows])
        self.writes.append((method, table, user))
        if table in EXERCISE_WRITE and not perm:
            return Resp(403, {"detail": "not trustworthy"})
        if method == "DELETE":
            row = self.t[table].get(oid)
            if row is None or (table in PRIVATE and row["_user"] != user) \
                    or (table == "exercise" and row not in self.visible_exercises()):
                return Resp(404, {"detail": "nf"})
            self.delete(table, oid)
            return Resp(204, None)
        body = dict(json or {})
        if method == "PATCH":
            row = self.t[table].get(oid)
            if row is None or (table in PRIVATE and row["_user"] != user):
                return Resp(404, {"detail": "nf"})
            row.update(self.norm(table, body))
            return Resp(200, self.pub(row))
        self.next_id += 1
        row = {"id": self.next_id, **self.norm(table, body), "_user": user}
        self.t[table][self.next_id] = row
        return Resp(201, self.pub(row))

    @staticmethod
    def norm(table, body):
        if table.endswith("-config") and "value" in body:
            body = {**body, "value": f"{float(body['value']):.2f}"}    # wger returns decimals as strings
        return body

    def routine(self, user, tag):
        (r,) = [r for r in self.rows("routine", user) if f"[kdk:{tag}]" in r.get("description", "")
                and not r.get("is_template")]
        return r

    def tree(self, user, tag):
        """[(plan key, unit, sets, reps, max, weight, rest, max_rest), ...] in slot order."""
        key_of = {v: k for k, v in self.ex_of.items()}
        unit = {u["id"]: u["name"] for u in self.t["setting-repetitionunit"].values()}

        def cfg(table, eid):
            v = [c["value"] for c in self.t[table].values() if c["slot_entry"] == eid]
            return float(v[0]) if v else None

        r = self.routine(user, tag)
        (d,) = [d for d in self.rows("day", user) if d["routine"] == r["id"]]
        out = []
        for s in sorted((s for s in self.rows("slot", user) if s["day"] == d["id"]), key=lambda s: s["order"]):
            for e in sorted((e for e in self.rows("slot-entry", user) if e["slot"] == s["id"]), key=lambda e: e["order"]):
                out.append((key_of[e["exercise"]], unit[e["repetition_unit"]],
                            *(cfg(t, e["id"]) for t in gw.CONFIGS.values())))
        return out


class FakeSession:
    def __init__(self, fake):
        self.fake = fake
        self.headers = {}

    def request(self, method, url, timeout=None, **kw):
        return self.fake.handle(method, url, self.headers, **kw)


@pytest.fixture
def fake(monkeypatch):
    f = FakeWger()
    monkeypatch.setattr(gw.requests, "Session", lambda: FakeSession(f))
    monkeypatch.setenv("WGER_URL", URL)
    for k, v in KEYS.items():
        monkeypatch.setenv(k, v)
    return f


def run(*args, plan_path=gw.PLAN):
    return gw.main(list(args), today=TODAY, plan_path=plan_path)


def write_plan(tmp_path, plan):
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(plan), encoding="utf-8")
    return p


def old_routine(fake, user, tag, name, template=False, logs=0, exercise=None):
    """An old generated routine with one day, one slot, one entry and `logs` logs."""
    fake.next_id += 1
    rid = fake.next_id
    fake.t["routine"][rid] = {"id": rid, "name": name, "description": f"old [kdk:{tag}]", "is_template": template,
                              "is_public": template, "start": "2026-11-02", "end": "2027-02-22", "_user": user}
    did, sid, eid = rid + 1, rid + 2, rid + 3
    fake.t["day"][did] = {"id": did, "routine": rid, "name": "Monday", "order": 1, "_user": user}
    fake.t["slot"][sid] = {"id": sid, "day": did, "order": 1, "_user": user}
    fake.t["slot-entry"][eid] = {"id": eid, "slot": sid, "exercise": exercise or 1, "order": 1, "_user": user}
    for n in range(logs):
        lid = rid + 10 + n
        fake.t["workoutlog"][lid] = {"id": lid, "routine": rid, "slot_entry": eid, "exercise": exercise or 1,
                                     "_user": user}
    fake.next_id = rid + 20
    return rid


# ---------------------------------------------------------------- the plan file
def test_plan_shape_four_days_for_jmack_seven_for_rach():
    j = gw.plan_routines(PLAN, "jmack")
    assert [b["key"] for b in j] == ["mon", "wed", "fri", "sat"]
    assert [b["name"] for b in j] == ["Mon · Lower A", "Wed · Upper A", "Fri · Lower B", "Sat · Upper B"]
    r = gw.plan_routines(PLAN, "rach")
    assert [b["key"] for b in r] == gw.DAY_TAGS
    assert [b["name"] for b in r] == ["Mon · Strength A", "Tue · Cardio", "Wed · Daily parts", "Thu · Strength B",
                                      "Fri · Cardio", "Sat · Daily parts", "Sun · Daily parts"]
    for b in j:
        first = b["days"][0]["slots"][0]["entries"][0]
        assert (first["key"], first["unit"], first["sets"], first["reps"]) == ("treadmill-walk", "minutes", 1, 30)
        assert len(b["days"][0]["slots"]) == 10                 # walk + 9 exercises
    for b in j + r:
        assert len(b["name"]) <= gw.ROUTINE_NAME_MAX and b["description"].endswith(f"[kdk:{b['key']}]")


def test_plan_exercises_carry_how_safety_and_a_youtube_video():
    for key, ex in PLAN["exercises"].items():
        assert 3 <= len(ex["how"]) <= 5 and ex["safety"], key
        assert gw.VIDEO_RE.match(ex["video"]), key


def test_plan_validation_catches_mistakes(tmp_path):
    bad = copy.deepcopy(PLAN)
    bad["exercises"]["plank"]["how"] = ["only one"]
    bad["people"]["jmack"]["routines"][0]["slots"][1]["reps"] = [12, 8]
    bad["people"]["jmack"]["routines"][0]["slots"][2]["exercise"] = "nope"
    bad["people"]["jmack"]["routines"][1]["name"] = "Wed · " + "x" * 30
    bad["exercises"]["extra"] = {**PLAN["exercises"]["plank"], "uuid": "11111111-1111-4111-8111-111111111111"}
    with pytest.raises(gw.WgerError) as e:
        gw.load_plan(write_plan(tmp_path, bad))
    msg = str(e.value)
    for want in ("plank: how needs 3-5 lines", "reps must be", "unknown exercise 'nope'", "longer than 25",
                 "exercises.extra: not used"):
        assert want in msg, want


def test_per_side_doubles_the_sets_and_says_so():
    mon = gw.plan_routines(PLAN, "jmack")[0]["days"][0]["slots"]
    step = next(s for s in mon if s["entries"][0]["key"] == "step-up")
    e = step["entries"][0]
    assert (e["sets"], e["reps"], e["max_reps"], e["side"]) == (6, 8, 10, True)
    assert e["comment"].startswith(gw.SIDE_ENTRY) and "3 × 8–10 / side" in e["comment"]
    assert step["comment"].endswith(gw.SIDE_SLOT)
    goblet = next(s for s in mon if s["entries"][0]["key"] == "goblet-squat")["entries"][0]
    assert (goblet["sets"], goblet["weight"], goblet["rest"], goblet["max_rest"]) == (3, 15, 60, 90)
    assert goblet["comment"] == "3 × 8–12 @ 15 lb · rest 60–90 s"


def test_rach_doses_carried_over():
    r = {b["key"]: b["days"][0]["slots"] for b in gw.plan_routines(PLAN, "rach")}
    keys = [s["entries"][0]["key"] for s in r["mon"]]
    assert keys == ["double-calf-raise", "hip-flexor-stretch", "box-squat", "pallof-press", "incline-push-up",
                    "wall-squat", "cow-cat"]
    pallof = r["mon"][3]["entries"][0]
    assert (pallof["sets"], pallof["reps"], pallof["rest"], pallof["max_rest"]) == (6, 10, 40, 60)
    walk = r["tue"][2]["entries"][0]
    assert (walk["key"], walk["unit"], walk["reps"], walk["max_reps"]) == ("walking", "minutes", 20, 30)
    assert "water walking" in r["tue"][2]["comment"].lower()
    assert [s["entries"][0]["key"] for s in r["wed"]] == ["double-calf-raise", "hip-flexor-stretch", "wall-squat",
                                                          "cow-cat"]
    rdl = r["thu"][2]["entries"][0]
    assert (rdl["key"], rdl["sets"], rdl["reps"], rdl["rest"], rdl["weight"]) == ("db-rdl", 3, 8, 90, None)


def test_describe_how_safety_video_and_hidden_marker():
    ex = PLAN["exercises"]["plank"]
    text = gw.describe("plank", ex)
    assert text.startswith("**How**\n\n- ")
    assert f"**Safety:** {ex['safety']}" in text
    assert f"\n\nVideo: {ex['video']}\n\n" in text
    m = gw.MARKER_RE.search(text)
    assert m and text.endswith(m.group(0)) and m.group(1) == gw.h12(text[:m.start()].rstrip("\n"))
    assert "Video:" not in gw.describe("plank", {**ex, "video": ""})


def test_video_url_is_the_single_address_source(monkeypatch):
    monkeypatch.setattr(gw, "video_url", lambda key, ex: f"https://jellyfin.test/{key}")
    assert "Video: https://jellyfin.test/plank" in gw.describe("plank", PLAN["exercises"]["plank"])


# ---------------------------------------------------------------- built-in exercises
def test_dry_run_writes_nothing(fake, capsys):
    assert run() == 0
    assert fake.writes == []
    assert "planned (dry run" in capsys.readouterr().out


def test_dry_run_without_keys_checks_exercises_only(fake, monkeypatch, capsys):
    for k in KEYS:
        monkeypatch.delenv(k)
    assert run() == 0
    out, err = capsys.readouterr()
    assert "exercise-translation" in out and "routine/" not in out and fake.writes == []
    assert "dry run checks the exercises only" in err


@pytest.mark.parametrize("args", [["--apply"], ["--check"], ["--purge-kdk"]])
def test_missing_key_is_an_error_for_real_runs(fake, monkeypatch, capsys, args):
    monkeypatch.delenv("WGER_API_KEY_RACH")
    assert run(*args) == 2
    assert "WGER_API_KEY_RACH is not set" in capsys.readouterr().err


def test_apply_then_second_run_makes_no_writes_and_check_passes(fake, capsys):
    assert run("--check") == 1
    assert run("--apply") == 0
    fake.writes.clear()
    assert run("--apply") == 0 and fake.writes == []
    assert run("--check") == 0
    assert "wger matches the plan" in capsys.readouterr().out


def test_descriptions_patched_with_the_owner_key_only(fake):
    assert run("--apply") == 0
    for key, eid in fake.ex_of.items():
        (tr,) = [t for t in fake.t["exercise-translation"].values() if t["exercise"] == eid]
        assert tr["description_source"] == gw.describe(key, PLAN["exercises"][key])
    assert {u for m, t, u in fake.writes if t in EXERCISE_WRITE} == {"jmack"}
    other = fake.t["exercise-translation"][900]
    assert other["description_source"] == "Upstream text."                  # not in the plan: untouched
    assert fake.t["exercise-translation"][10]["description_source"] == "Upstream text."


def test_hand_edited_description_is_restored(fake):
    assert run("--apply") == 0
    tr = next(t for t in fake.t["exercise-translation"].values() if t["exercise"] == fake.ex_of["plank"])
    tr["description_source"] = "edited in the web UI"
    assert run("--check") == 1
    assert run("--apply") == 0 and tr["description_source"] == gw.describe("plank", PLAN["exercises"]["plank"])


def test_name_mismatch_stops_the_run(fake, capsys):
    tr = next(t for t in fake.t["exercise-translation"].values() if t["exercise"] == fake.ex_of["side-plank"])
    tr["name"] = "Side Plank (other one)"
    assert run("--apply") == 2
    assert "named 'Side Plank (other one)' in wger" in capsys.readouterr().err
    assert fake.writes == []


def test_unknown_uuid_and_kdk_exercise_stop_the_run(fake, capsys):
    fake.t["exercise"][fake.ex_of["plank"]]["uuid"] = "99999999-9999-4999-8999-999999999999"
    assert run() == 2
    assert "no wger exercise with uuid" in capsys.readouterr().err
    fake.t["exercise"][fake.ex_of["plank"]].update(uuid=PLAN["exercises"]["plank"]["uuid"], license_author="kdk-lab")
    assert run() == 2
    assert "built-ins only" in capsys.readouterr().err


def test_treadmill_equipment_linked_and_missing_row_warns(fake, capsys):
    assert run("--apply") == 0
    assert fake.t["exercise"][fake.ex_of["treadmill-walk"]]["equipment"] == [7, 13]
    assert "no equipment 'Resistance band'" in capsys.readouterr().err
    fake.t["equipment"][22] = {"id": 22, "name": "Resistance band"}
    assert run("--apply") == 0
    assert fake.t["exercise"][fake.ex_of["pallof-press"]]["equipment"] == [7, 22]
    fake.writes.clear()
    assert run("--apply") == 0 and fake.writes == []


def test_person_without_owner_cannot_patch_exercises(fake, capsys):
    assert run("--apply", "--person", "rach") == 2
    assert "first person's key" in capsys.readouterr().err


# ---------------------------------------------------------------- routines
def test_routines_slots_weights_rest(fake):
    assert run("--apply") == 0
    j = [r for r in fake.rows("routine", "jmack")]
    assert sorted(gw.routine_tag(r) for r in j) == ["fri", "mon", "sat", "wed"]
    assert len(fake.rows("routine", "rach")) == 7
    mon = fake.tree("jmack", "mon")
    assert mon[0] == ("treadmill-walk", "Minutes", 1.0, 30.0, None, None, None, None)
    assert mon[1] == ("goblet-squat", "Repetitions", 3.0, 8.0, 12.0, 15.0, 60.0, 90.0)
    assert mon[3] == ("step-up", "Repetitions", 6.0, 8.0, 10.0, None, 60.0, 90.0)
    wed = fake.tree("jmack", "wed")
    assert ("suitcase-carry", "10-ft lengths", 6.0, 4.0, None, 25.0, 60.0, 90.0) in wed
    assert ("side-plank", "Seconds", 4.0, 20.0, 30.0, None, 60.0, 90.0) in wed
    thu = fake.tree("rach", "thu")
    assert ("db-rdl", "Repetitions", 3.0, 8.0, None, None, 90.0, None) in thu
    # the same built-in in both people's routines
    assert fake.ex_of["db-rdl"] in {e["exercise"] for e in fake.rows("slot-entry", "jmack")}
    assert fake.ex_of["db-rdl"] in {e["exercise"] for e in fake.rows("slot-entry", "rach")}


def test_app_lists_the_weekdays_in_order(fake):
    assert run("--apply") == 0
    for user in ("jmack", "rach"):
        rs = sorted(fake.rows("routine", user), key=lambda r: (r["start"], r["id"]), reverse=True)
        tags = [gw.routine_tag(r) for r in rs]
        assert tags == sorted(tags, key=gw.DAY_TAGS.index)
        assert len({r["end"] for r in rs}) == 1
        mon = fake.routine(user, "mon")
        assert mon["start"] == "2026-11-02" and mon["fit_in_week"] is False
        assert (dt.date.fromisoformat(mon["end"]) - dt.date(2026, 11, 2)).days == gw.END_DAYS


def test_weight_change_patches_only_that_config(fake, tmp_path):
    assert run("--apply") == 0
    plan = copy.deepcopy(PLAN)
    plan["people"]["jmack"]["routines"][0]["slots"][1]["weight"] = 20
    p = write_plan(tmp_path, plan)
    fake.writes.clear()
    assert run("--apply", plan_path=p) == 0
    assert fake.writes == [("PATCH", "slot-entry", "jmack"), ("PATCH", "weight-config", "jmack")]  # comment + value


def test_slot_with_logs_is_retired_not_deleted(fake, tmp_path):
    assert run("--apply") == 0
    r = fake.routine("jmack", "mon")
    entry = next(e for e in fake.rows("slot-entry", "jmack") if e["exercise"] == fake.ex_of["tib-raise"])
    fake.t["workoutlog"][1] = {"id": 1, "routine": r["id"], "slot_entry": entry["id"], "_user": "jmack"}
    plan = copy.deepcopy(PLAN)
    slots = plan["people"]["jmack"]["routines"][0]["slots"]
    plan["people"]["jmack"]["routines"][0]["slots"] = [s for s in slots if s["exercise"] != "tib-raise"]
    plan["people"]["jmack"]["routines"][1]["slots"].append({"exercise": "tib-raise", "sets": 2, "reps": 15})
    p = write_plan(tmp_path, plan)
    assert run("--apply", plan_path=p) == 0
    assert 1 in fake.t["workoutlog"]
    slot = fake.t["slot"][entry["slot"]]
    assert slot["comment"].startswith(gw.RETIRED) and slot["order"] > gw.RETIRED_ORDER
    fake.writes.clear()
    assert run("--apply", plan_path=p) == 0 and fake.writes == []


def test_normal_run_never_deletes_routines(fake, capsys):
    tue = old_routine(fake, "jmack", "tue", "Tue · Upper A", logs=2)
    tue2 = old_routine(fake, "jmack", "thu", "Thu · Lower B")
    tpl = old_routine(fake, "jmack", "tpl:mon", "Mon · Lower A (template)", template=True)
    assert run("--apply") == 0
    assert {tue, tue2, tpl} <= set(fake.t["routine"])
    assert not any(m == "DELETE" and t == "routine" for m, t, _ in fake.writes)
    assert "--purge-kdk removes them" in capsys.readouterr().err


def test_a_plan_routine_from_the_old_layout_is_reused(fake):
    rid = old_routine(fake, "jmack", "mon", "Mon · Lower A", logs=1)
    assert run("--apply") == 0
    assert fake.routine("jmack", "mon")["id"] == rid
    assert len([x for x in fake.t["workoutlog"].values() if x["routine"] == rid]) == 1   # old slot kept (logs)


def test_missing_lengths_unit_prints_admin_step_and_stops(monkeypatch, capsys):
    f = FakeWger(lengths_unit=False)
    monkeypatch.setattr(gw.requests, "Session", lambda: FakeSession(f))
    monkeypatch.setenv("WGER_URL", URL)
    for k, v in KEYS.items():
        monkeypatch.setenv(k, v)
    assert run("--apply") == 3
    assert "RepetitionUnit.objects.get_or_create(name='10-ft lengths'" in capsys.readouterr().out
    assert not f.t["routine"]


# ---------------------------------------------------------------- purge
@pytest.fixture
def old(fake):
    """jmack: old Tue (2 logs), a block routine, a template, the plan's Mon (1 log), a hand-made
    routine; rach: an old public template; plus a public template of rach jmack can see."""
    ids = {
        "tue": old_routine(fake, "jmack", "tue", "Tue · Upper A", logs=2),
        "daily": old_routine(fake, "jmack", "daily", "Daily", logs=1, exercise=2),
        "tpl": old_routine(fake, "jmack", "tpl:wed", "Tpl · Wed · Walk + flow", template=True),
        "mon": old_routine(fake, "jmack", "mon", "Mon · Lower A", logs=1, exercise=2),
        "rach_tpl": old_routine(fake, "rach", "tpl:sun", "Tpl · Sun · Daily parts", template=True),
        "rach_mon": old_routine(fake, "rach", "mon", "Mon · Strength A"),
    }
    fake.next_id += 1
    mine = fake.next_id
    fake.t["routine"][mine] = {"id": mine, "name": "My own", "description": "hand-made", "_user": "jmack"}
    ids["own"] = mine
    return ids


def test_purge_dry_run_counts_and_writes_nothing(fake, old, capsys):
    assert run("--purge-kdk") == 0
    out = capsys.readouterr().out
    assert fake.writes == []
    assert "SUMMARY: 4 routine(s)/template(s) with 3 log(s) and 0 session(s); 2 kdk-lab exercise(s)" in out
    assert "(their 1 log(s) in the kept routines cascade with them)" in out
    assert "dry run: nothing deleted" in out and "manage.py shell" in out
    assert "license_author='kdk-lab'" in out


def test_purge_apply_deletes_only_kdk_outside_the_plan(fake, old, capsys):
    assert run("--purge-kdk", "--apply") == 0
    out = capsys.readouterr().out
    for k in ("tue", "daily", "tpl", "rach_tpl"):
        assert old[k] not in fake.t["routine"], k
    for k in ("mon", "rach_mon", "own"):
        assert old[k] in fake.t["routine"], k
    assert 1 not in fake.t["exercise"] and 2 not in fake.t["exercise"]
    assert 3 in fake.t["exercise"]                      # no translation: invisible to the API ...
    assert "FALLBACK" in out and "license_author='kdk-lab'" in out   # ... so the fallback is printed
    left = [e for e in fake.t["exercise"].values() if e["license_author"] != "kdk-lab"]
    assert len(left) == len(PLAN["exercises"]) + 1     # every built-in survives
    assert all(m == "DELETE" for m, _, _ in fake.writes)
    assert {u for m, t, u in fake.writes if t == "routine"} == {"jmack", "rach"}


def test_purge_exits_1_when_something_remains(fake, old, monkeypatch, capsys):
    real = fake.handle

    def handle(method, url, headers, **kw):
        if method == "DELETE" and url.endswith("/exercise/2/"):
            return Resp(403, {"detail": "no"})
        return real(method, url, headers, **kw)
    monkeypatch.setattr(fake, "handle", handle)
    assert run("--purge-kdk", "--apply") == 1
    out = capsys.readouterr().out
    assert "FAILED: exercise 2" in out and "NOT CLEAN" in out and "1 kdk-lab exercise(s) remain" in out


def test_purge_clean_exit_and_normal_run_afterwards(fake, old, capsys):
    assert run("--purge-kdk", "--apply") == 0
    assert "clean: 0 kdk routines" in capsys.readouterr().out
    assert run("--apply") == 0
    assert run("--check") == 0
    assert len(fake.rows("routine", "jmack")) == 5     # 4 plan routines + the hand-made one


def test_purge_refuses_when_a_plan_exercise_is_kdk(fake, old, capsys):
    fake.t["exercise"][fake.ex_of["plank"]]["license_author"] = "kdk-lab"
    assert run("--purge-kdk", "--apply") == 2
    assert "refusing to purge" in capsys.readouterr().err
    assert fake.writes == []


def test_purge_for_rach_only_leaves_exercises(fake, old, capsys):
    assert run("--purge-kdk", "--apply", "--person", "rach") == 0
    assert old["rach_tpl"] not in fake.t["routine"] and old["tue"] in fake.t["routine"]
    assert 1 in fake.t["exercise"]
    assert "exercises skipped" in capsys.readouterr().out


# ---------------------------------------------------------------- videos
class OResp:
    def __init__(self, code, body=None):
        self.status_code = code
        self._b = body or {}

    def json(self):
        return self._b


def test_check_videos(monkeypatch, capsys, tmp_path):
    seen = []

    def get(url, params=None, timeout=None):
        seen.append(params["url"])
        return OResp(200, {"title": "t", "author_name": "a"})
    monkeypatch.setattr(gw.requests, "get", get)
    assert run("--check-videos") == 0
    assert len(seen) == len(PLAN["exercises"])
    plank = PLAN["exercises"]["plank"]["video"]
    monkeypatch.setattr(gw.requests, "get", lambda url, params=None, timeout=None:
                        OResp(404 if params["url"] == plank else 200))
    assert run("--check-videos") == 1
    assert "FAIL plank" in capsys.readouterr().out
    plan = copy.deepcopy(PLAN)
    plan["exercises"]["plank"]["video"] = ""
    monkeypatch.setattr(gw.requests, "get", get)
    assert run("--check-videos", plan_path=write_plan(tmp_path, plan)) == 1
    assert "MISSING plank" in capsys.readouterr().out


def test_no_retired_files_left():
    for name in ("gen-workout-guide.py", "workout_catalog.py", "workout_catalog.json", "wger_exercises.json"):
        assert not (TOOLS / name).exists(), name
    assert not re.search(r"workout_catalog|wger_exercises|posters", (TOOLS / "gen_wger.py").read_text())
