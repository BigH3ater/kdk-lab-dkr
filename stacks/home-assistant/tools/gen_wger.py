#!/usr/bin/env python3
"""Load the household exercise plan (wger_plan.json) into wger: built-in exercises + routines.

    WGER_URL=https://wger.kmkdp.com WGER_API_KEY_JMACK=... WGER_API_KEY_RACH=... \\
    python3 stacks/home-assistant/tools/gen_wger.py                 # dry run: print the writes
    python3 stacks/home-assistant/tools/gen_wger.py --apply         # write them
    python3 stacks/home-assistant/tools/gen_wger.py --check         # exit 1 on drift
    python3 stacks/home-assistant/tools/gen_wger.py --check-videos  # every video answers YouTube oEmbed
    python3 stacks/home-assistant/tools/gen_wger.py --purge-kdk     # dry run: what the purge deletes
    python3 stacks/home-assistant/tools/gen_wger.py --purge-kdk --apply
    ... --person rach                                               # one person only (repeatable)

Config from the environment only: WGER_URL and one API key per person in WGER_API_KEY_<LOGIN>
(e.g. WGER_API_KEY_JMACK). Keys never go in argv. A dry run without keys still checks the
exercises (public endpoints) and skips the routines with a warning; --apply, --check and
--purge-kdk need the keys. Runbook: docs/runbooks/workout-tracking-update.md.

What a normal run writes:
  1. Exercises: wger's own built-ins only (no custom exercises, no images). Each plan entry
     is found by uuid (exerciseinfo/?uuid=); the run stops when the uuid is missing, the
     exercise is a local kdk-lab one, or its English name differs from the plan's `name`.
     Its English description_source is replaced by the How / Safety / "Video: <address>"
     text from describe(), ending in a hidden "<!-- kdk-plan v:<hash> -->" comment (wger
     renders Markdown to HTML and drops the comment). Patched only when it differs, so a
     second run writes nothing. The address is the video's Jellyfin page
     (https://jellyfin.kmkdp.com/web/#/details?id=<item id>), read from wger_videos.json, which
     exercise_videos.py jellyfin writes; the plan's YouTube address is only the download source.
     While an exercise has no item id there (or the id is for another YouTube video than the
     plan's), --apply stops before any write, --check reports it as a difference, and a dry
     run warns and leaves that description alone: a run never writes a YouTube or empty line.
     An exercise with "video": null (no video by design, e.g. treadmill-walk) gets How /
     Safety only, no Video: line, and is never reported as missing one. Plan `equipment` names are added to the exercise (never
     removed); a name the server lacks is a warning. Exercise writes use the first
     person's key (jmack, a wger superuser) and only when that person is selected.
  2. Units: looked up by name (Repetitions, Seconds, Minutes, "10-ft lengths"; lb). REST
     cannot create units: a missing one prints an ADMIN STEP and --apply stops.
  3. Per person, one routine per plan weekday (jmack Mon/Wed/Fri/Sat, rach all seven), one
     day each, fit_in_week off (a one-day routine repeats that day on every date), tagged
     "[kdk:<mon..sun>]" at the end of the description. Starts staggered one day per weekday
     back from this week's Monday with one shared end 16 weeks after it (wger caps a
     routine at 120 days and lists by -start), so the app lists them Mon..Sun. An --apply
     within 28 days of the end rolls the window. Each slot = one plan slot: sets, reps (or
     seconds / minutes / lengths) and max, rest and max rest, the planned weight (lb). A
     per-side slot doubles the sets, one logged set per side (odd = left, even = right).
Slots are matched by their exercises, never rebuilt: a WorkoutLog cascades away with its
slot entry, so a slot with logs is moved to the end and marked "[retired]" instead. A normal
run never deletes a routine: kdk-tagged routines that are not in the plan are listed for
--purge-kdk.

--purge-kdk (owner-approved, destructive, dry run unless --apply): deletes every routine and
template whose description carries a "[kdk:...]" tag and that is NOT one of the plan's
routines (tag = a weekday the person's plan uses), logs and sessions included, then every
exercise whose license_author is exactly "kdk-lab" (their images, translations, slot entries
and logs cascade). It prints counts first and the manage.py shell fallback for exercises the
API cannot see or delete (an exercise without a translation is invisible to the API), and
with --apply ends by re-reading the server and exiting 1 unless none remain.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import requests

TOOLS = Path(__file__).resolve().parent
PLAN = TOOLS / "wger_plan.json"
VIDEOS = TOOLS / "wger_videos.json"      # plan key -> Jellyfin item id, written by exercise_videos.py

ROUTINE_NAME_MAX = 25           # wger Routine.name max_length
TAG_RE = re.compile(r"\[kdk:([a-z_]+)\]")
KDK_ANY_RE = re.compile(r"\[kdk:[^\]]+\]")      # weekday, tpl:<day>, block tags (daily, lifts ...)
DAY_TAGS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
ROUTINE_DESC = "Generated by gen_wger.py from wger_plan.json; edits here are overwritten."
WINDOW_DAYS = 118          # longest span (Sunday's routine): wger MAX_DURATION_DAYS = 120
END_DAYS = WINDOW_DAYS - 6  # the shared end: base Monday + 112 (16 weeks)
RENEW_DAYS = 28            # --apply rolls the window when it ends sooner than this
RETIRED = "[retired] "     # slot comment prefix for a removed slot that still has logs
RETIRED_ORDER = 900
LICENSE_AUTHOR = "kdk-lab"  # the old custom exercises: what --purge-kdk deletes, nothing else
TIMEOUT = 30
OEMBED = "https://www.youtube.com/oembed"
VIDEO_RE = re.compile(r"^https://www\.youtube\.com/watch\?v=[A-Za-z0-9_-]{11}$")
MARKER_RE = re.compile(r"<!-- kdk-plan v:([0-9a-f]{12}) -->")
JF_ID_RE = re.compile(r"^[0-9a-f]{32}$")
JF_DETAILS = "{base}/web/#/details?id={id}"

UNIT_NAMES = {"reps": "Repetitions", "seconds": "Seconds", "minutes": "Minutes", "lengths": "10-ft lengths"}
UNIT_SHORT = {"reps": "", "seconds": " s", "minutes": " min", "lengths": " lengths"}
DOSE_KEYS = tuple(UNIT_NAMES)
WEIGHT_UNIT = "lb"
ADMIN_UNIT = {"10-ft lengths": ("DISTANCE", 3)}   # unit_type, multiplier (to metres; 10 ft = 3.05 m)

CONFIGS = {"sets": "sets-config", "reps": "repetitions-config", "max_reps": "max-repetitions-config",
           "weight": "weight-config", "rest": "rest-config", "max_rest": "max-rest-config"}
SIDE_ENTRY = "Each set is one side: odd sets left, even sets right"
SIDE_SLOT = " · each set one side (L, R)"
EXERCISE_KEYS = {"uuid", "name", "how", "safety", "video", "equipment"}
SLOT_KEYS = {"exercise", "sets", "weight", "rest", "per_side", "comment", *DOSE_KEYS}


class WgerError(RuntimeError):
    pass


def short(text: str, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def h12(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def span(v) -> tuple[int, int | None]:
    """8 -> (8, None); [8, 12] -> (8, 12); [60, 60] -> (60, None)."""
    if isinstance(v, list):
        return v[0], (v[1] if v[1] != v[0] else None)
    return v, None


# ---------------------------------------------------------------- the plan
def load_plan(path: Path = PLAN) -> dict:
    """Read and validate wger_plan.json; raises WgerError on any structural problem."""
    plan = json.loads(path.read_text(encoding="utf-8"))
    exs = plan.get("exercises") or {}
    errs = []
    uuids = {}
    for key, ex in exs.items():
        where = f"exercises.{key}"
        if set(ex) - EXERCISE_KEYS:
            errs.append(f"{where}: unknown key(s) {sorted(set(ex) - EXERCISE_KEYS)}")
        if not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", ex.get("uuid", "")):
            errs.append(f"{where}: uuid missing or malformed")
        elif ex["uuid"] in uuids:
            errs.append(f"{where}: same uuid as {uuids[ex['uuid']]}")
        else:
            uuids[ex["uuid"]] = key
        if not ex.get("name"):
            errs.append(f"{where}: name missing")
        how = ex.get("how") or []
        if not 3 <= len(how) <= 5 or not all(isinstance(x, str) and x.strip() for x in how):
            errs.append(f"{where}: how needs 3-5 lines")
        if not (ex.get("safety") or "").strip():
            errs.append(f"{where}: safety line missing")
        # "video": null = deliberately no video (owner decision 2026-11-14: treadmill-walk); a
        # missing or empty video is a mistake, not a way to say "none"
        if "video" not in ex:
            errs.append(f"{where}: video missing (https://www.youtube.com/watch?v=<id>, or null for none)")
        elif ex["video"] is not None and not VIDEO_RE.match(str(ex["video"])):
            errs.append(f"{where}: video must be https://www.youtube.com/watch?v=<id> (or null for none)")
    used = set()
    for login, person in (plan.get("people") or {}).items():
        parts = person.get("parts", {})
        tags = set()
        for r in person.get("routines", []):
            where = f"people.{login}.{r.get('day')}"
            if r.get("day") not in DAY_TAGS or r["day"] in tags:
                errs.append(f"{where}: day must be one of {DAY_TAGS}, once each")
            tags.add(r.get("day"))
            if not r.get("name") or len(r["name"]) > ROUTINE_NAME_MAX:
                errs.append(f"{where}: name missing or longer than {ROUTINE_NAME_MAX} characters")
            slots = expand_slots(r.get("slots", []), parts, where, errs)
            if not slots:
                errs.append(f"{where}: no slots")
            for i, s in enumerate(slots, 1):
                sw = f"{where} slot {i}"
                if set(s) - SLOT_KEYS:
                    errs.append(f"{sw}: unknown key(s) {sorted(set(s) - SLOT_KEYS)}")
                if s.get("exercise") not in exs:
                    errs.append(f"{sw}: unknown exercise {s.get('exercise')!r}")
                used.add(s.get("exercise"))
                if not isinstance(s.get("sets"), int) or s["sets"] < 1:
                    errs.append(f"{sw}: sets must be a positive integer")
                doses = [k for k in DOSE_KEYS if k in s]
                if len(doses) != 1:
                    errs.append(f"{sw}: exactly one of {list(DOSE_KEYS)}")
                for k in [*doses, *(["rest"] if "rest" in s else [])]:
                    v = s[k]
                    ok = (isinstance(v, int) and v > 0) or (isinstance(v, list) and len(v) == 2 and all(
                        isinstance(x, int) and x > 0 for x in v) and v[0] <= v[1])
                    if not ok:
                        errs.append(f"{sw}: {k} must be a positive integer or [min, max]")
                if "weight" in s and not (isinstance(s["weight"], (int, float)) and s["weight"] > 0):
                    errs.append(f"{sw}: weight must be a positive number (lb)")
                if len(s.get("comment", "")) > 160:
                    errs.append(f"{sw}: comment longer than 160 characters")
    for key in sorted(set(exs) - used):
        errs.append(f"exercises.{key}: not used by any routine")
    if errs:
        raise WgerError(f"{path.name}:\n  " + "\n  ".join(errs))
    return plan


def expand_slots(slots: list[dict], parts: dict, where: str, errs: list) -> list[dict]:
    out = []
    for s in slots:
        if "part" in s:
            if s["part"] not in parts:
                errs.append(f"{where}: unknown part {s['part']!r}")
            out.extend(parts.get(s["part"], []))
        else:
            out.append(s)
    return out


def load_videos(path: Path | None = None) -> dict:
    """wger_videos.json -> {"base": https base, "ids": {plan key: {"id", "youtube", ...}}}."""
    path = path or VIDEOS
    if not path.exists():
        return {"base": "", "ids": {}}
    doc = json.loads(path.read_text(encoding="utf-8"))
    base = (doc.get("base_url") or "").rstrip("/")
    if not base.startswith("https://"):
        raise WgerError(f"{path.name}: base_url must be an https:// address")
    ids = {}
    for key, it in (doc.get("items") or {}).items():
        if not JF_ID_RE.match(str(it.get("id", ""))) or not re.fullmatch(r"[A-Za-z0-9_-]{11}", str(it.get("youtube", ""))):
            raise WgerError(f"{path.name}: items.{key} needs a 32-hex id and an 11-character youtube id")
        ids[key] = it
    return {"base": base, "ids": ids}


def video_url(key: str, ex: dict, videos: dict | None = None) -> str:
    """The address on the description's Video: line: the video's Jellyfin details page, from
    wger_videos.json. "" when the mapping has no item id for this key, or the id belongs to
    another YouTube video than the plan's (the plan changed: re-run exercise_videos.py)."""
    it = (videos or {}).get("ids", {}).get(key)
    m = VIDEO_RE.match(ex.get("video") or "")
    if not it or not m or it["youtube"] != ex["video"][-11:]:
        return ""
    return JF_DETAILS.format(base=videos["base"], id=it["id"])


def missing_videos(plan: dict, videos: dict) -> list[str]:
    """Plan keys whose Video: line cannot be built yet (an exercise with "video": null has none
    to build: its description simply has no Video: line)."""
    return [k for k, ex in plan["exercises"].items() if ex.get("video", "") is not None and not video_url(k, ex, videos)]


def describe(key: str, ex: dict, videos: dict | None = None) -> str:
    """The description_source (Markdown; wger keeps p/strong/em/ul/ol/li, so every block is its
    own paragraph or list), then the hidden marker:

        **How**  - <line> ...
        **Safety:** <line>
        Video: <Jellyfin details page>  (left out without an item id; runs never write that)
        <!-- kdk-plan v:<hash> -->
    """
    blocks = ["**How**\n\n" + "\n".join(f"- {x}" for x in ex["how"]), f"**Safety:** {ex['safety']}"]
    url = video_url(key, ex, videos)
    if url:
        blocks.append(f"Video: {url}")
    text = "\n\n".join(blocks)
    return f"{text}\n\n<!-- kdk-plan v:{h12(text)} -->"


def dose_text(s: dict) -> str:
    """'3 × 8–12 @ 15 lb · rest 60–90 s' (the entry comment)."""
    unit = next(k for k in DOSE_KEYS if k in s)
    lo, hi = span(s[unit])
    t = f"{s['sets']} × {lo}" + (f"–{hi}" if hi else "") + UNIT_SHORT[unit]
    if s.get("per_side"):
        t += " / side"
    if s.get("weight"):
        t += f" @ {s['weight']:g} lb"
    if s.get("rest"):
        rlo, rhi = span(s["rest"])
        t += f" · rest {rlo}" + (f"–{rhi}" if rhi else "") + " s"
    return t


def plan_routines(plan: dict, login: str) -> list[dict]:
    """[{key, name, description, days: [one day {name, description, is_rest, slots}]}], Mon first."""
    person = plan["people"][login]
    out = []
    for r in sorted(person["routines"], key=lambda r: DAY_TAGS.index(r["day"])):
        wd = WEEKDAYS[DAY_TAGS.index(r["day"])]
        slots = []
        for s in expand_slots(r["slots"], person.get("parts", {}), login, []):
            unit = next(k for k in DOSE_KEYS if k in s)
            lo, hi = span(s[unit])
            rlo, rhi = span(s["rest"]) if s.get("rest") else (None, None)
            side = bool(s.get("per_side"))
            dose = dose_text(s)
            entry = {"key": s["exercise"], "unit": unit, "sets": s["sets"] * (2 if side else 1),
                     "reps": lo, "max_reps": hi, "weight": s.get("weight"), "rest": rlo, "max_rest": rhi,
                     "side": side, "comment": short(f"{SIDE_ENTRY} · {dose}" if side else dose, 100)}
            comment = s.get("comment") or plan["exercises"][s["exercise"]]["name"]
            slots.append({"comment": short(comment + (SIDE_SLOT if side else ""), 200), "entries": [entry]})
        desc = f"{wd}. {person.get('about', '')} {ROUTINE_DESC} [kdk:{r['day']}]"
        out.append({"key": r["day"], "name": r["name"], "description": short(desc, 1000),
                    "days": [{"name": wd, "description": short(r.get("summary", ""), 1000),
                              "is_rest": False, "slots": slots}]})
    return out


# ---------------------------------------------------------------- API
class Api:
    """Thin REST client. In dry-run (apply=False) writes are recorded, not sent."""

    def __init__(self, base: str, key: str | None, who: str, apply: bool, actions: list):
        self.base = base.rstrip("/") + "/api/v2/"
        self.who = who
        self.apply = apply
        self.actions = actions
        self.s = requests.Session()
        self.s.headers["Accept"] = "application/json"
        if key:
            self.s.headers["Authorization"] = f"Token {key}"
        self._dry = 0

    def _req(self, method: str, path: str, **kw):
        r = self.s.request(method, self.base + path, timeout=TIMEOUT, **kw)
        if r.status_code >= 400:
            raise WgerError(f"{method} /api/v2/{path} as {self.who}: HTTP {r.status_code} {short(r.text, 400)}")
        return r.json() if r.content else {}

    def get(self, path: str, **params):
        return self._req("GET", path, params=params)

    def list(self, path: str, **params) -> list[dict]:
        out, offset = [], 0
        while True:
            page = self.get(path, limit=999, offset=offset, **params)
            res = page.get("results", [])
            out.extend(res)
            offset += len(res)
            if not res or not page.get("next") or offset >= page.get("count", 0):
                return out

    def write(self, method: str, path: str, what: str, payload: dict | None = None) -> dict:
        self.actions.append(f"{self.who}: {method} {path} {what}")
        if not self.apply:
            self._dry += 1
            return {**(payload or {}), "id": f"dry-{self._dry}"}
        return self._req(method, path, json=payload)


def num(v):
    return None if v in (None, "") else float(v)


def reference(api: Api) -> dict:
    langs = {x["short_name"]: x["id"] for x in api.list("language/")}
    if "en" not in langs:
        raise WgerError("wger has no language 'en'")
    return {
        "en": langs["en"],
        "equipment": {e["name"]: e["id"] for e in api.list("equipment/")},
        "rep_unit": {u["name"]: u["id"] for u in api.list("setting-repetitionunit/")},
        "weight_unit": {u["name"]: u["id"] for u in api.list("setting-weightunit/")},
    }


def admin_unit_steps(missing: list[str]) -> str:
    lines = ["from wger.core.models import RepetitionUnit"]
    for n in missing:
        utype, mult = ADMIN_UNIT.get(n, ("REPETITIONS", 1))
        lines.append(f"print(RepetitionUnit.objects.get_or_create(name='{n}', "
                     f"defaults={{'unit_type': '{utype}', 'multiplier': {mult}}}))")
    body = " \\\n  ".join(f'"{x}"' for x in lines)
    return ("ADMIN STEP (the REST API cannot create units). Laptop -> kdk-dkr-01:\n"
            f"printf '%s\\n' \\\n  {body} \\\n"
            "  | ssh kdkadmin@10.1.20.20 'sudo docker exec -i wger-web python3 manage.py shell'\n"
            "then re-run this command.")


# ---------------------------------------------------------------- exercises
def resolve_exercise(api: Api, key: str, ex: dict, en: int) -> dict:
    """The built-in for a plan entry: {id, translation (fresh), exercise (fresh)}."""
    res = [e for e in api.get("exerciseinfo/", uuid=ex["uuid"]).get("results", []) if e.get("uuid") == ex["uuid"]]
    if not res:
        raise WgerError(f"{key}: no wger exercise with uuid {ex['uuid']} ({ex['name']!r})")
    info = res[0]
    if info.get("license_author") == LICENSE_AUTHOR:
        raise WgerError(f"{key}: uuid {ex['uuid']} is a local {LICENSE_AUTHOR} exercise; the plan uses built-ins only")
    trs = [t for t in info.get("translations", []) if t.get("language") == en]
    if len(trs) != 1:
        raise WgerError(f"{key}: exercise {info['id']} has {len(trs)} English translations (want 1)")
    if trs[0].get("name") != ex["name"]:
        raise WgerError(f"{key}: exercise {info['id']} is named {trs[0].get('name')!r} in wger, the plan says "
                        f"{ex['name']!r}; check the uuid")
    # exerciseinfo is cached server-side; compare against the uncached rows
    return {"id": info["id"], "translation": api.get(f"exercise-translation/{trs[0]['id']}/"),
            "exercise": api.get(f"exercise/{info['id']}/")}


def sync_exercises(reader: Api, writer: Api | None, plan: dict, ref: dict, warn,
                   videos: dict | None = None) -> dict[str, int]:
    """Returns plan key -> wger exercise id. writer None = the exercise owner is not selected:
    differences are only reported (and --apply stops on them). An exercise without a Jellyfin
    item id keeps its current description (main() stops --apply and fails --check first)."""
    videos = videos or {"base": "", "ids": {}}
    no_video = set(missing_videos(plan, videos))
    ids = {}
    missing_eq: dict[str, list[str]] = {}
    found = {key: resolve_exercise(reader, key, ex, ref["en"]) for key, ex in plan["exercises"].items()}
    for key, ex in plan["exercises"].items():        # every lookup passed: only now write
        cur = found[key]
        ids[key] = cur["id"]
        api = writer or reader
        want_src = describe(key, ex, videos)
        tr = cur["translation"]
        if key not in no_video and (tr.get("description_source") or "") != want_src:
            need_owner(writer, reader, f"description of {key}")
            api.write("PATCH", f"exercise-translation/{tr['id']}/", f"{key} description (How / Safety / Video)",
                      {"description_source": want_src})
        have = set(cur["exercise"].get("equipment") or [])
        add = set()
        for name in ex.get("equipment", []):
            if name in ref["equipment"]:
                add.add(ref["equipment"][name])
            else:
                missing_eq.setdefault(name, []).append(key)
        if add - have:
            need_owner(writer, reader, f"equipment of {key}")
            api.write("PATCH", f"exercise/{cur['id']}/", f"{key} equipment +{sorted(add - have)}",
                      {"equipment": sorted(have | add)})
    for name, keys in sorted(missing_eq.items()):
        warn(f"wger has no equipment {name!r} (wanted by {', '.join(keys)}); linked once the row exists "
             "(runbook wger.md step 12)")
    return ids


def need_owner(writer, reader, what):
    if writer is None and reader.apply:
        raise WgerError(f"{what}: exercises are written with the first person's key; run with that person "
                        "selected (no --person, or --person <first login>)")


# ---------------------------------------------------------------- routines
def base_monday(today: dt.date) -> dt.date:
    return today - dt.timedelta(days=today.weekday())


def end_ok(end: dt.date, today: dt.date) -> bool:
    base = end - dt.timedelta(days=END_DAYS)
    return base.weekday() == 0 and base <= today and end >= today + dt.timedelta(days=RENEW_DAYS)


def routine_tag(r: dict) -> str | None:
    m = TAG_RE.search(r.get("description") or "")
    return m.group(1) if m else None


class State:
    """One person's routine rows, fetched once (the API scopes every list to the key's user)."""

    def __init__(self, api: Api):
        self.days = api.list("day/")
        self.slots = api.list("slot/")
        self.entries = api.list("slot-entry/")
        self.cfg = {k: api.list(p + "/") for k, p in CONFIGS.items()}
        self.logs = api.list("workoutlog/")
        self.sessions = api.list("workoutsession/")
        day_of_slot = {s["id"]: s["day"] for s in self.slots}
        self.day_of_entry = {e["id"]: day_of_slot.get(e["slot"]) for e in self.entries}

    def used_days(self, rid) -> dict:
        """day id -> number of logs + sessions on it, for one routine's days."""
        mine = {d["id"] for d in self.days if d["routine"] == rid}
        n: dict = {}
        for x in self.sessions:
            if x.get("day") in mine:
                n[x["day"]] = n.get(x["day"], 0) + 1
        for x in self.logs:
            d = self.day_of_entry.get(x.get("slot_entry"))
            if d in mine:
                n[d] = n.get(d, 0) + 1
        return n

    def history(self, rid) -> tuple[int, int]:
        """(logs, sessions) of one routine."""
        mine = {d["id"] for d in self.days if d["routine"] == rid}
        logs = sum(1 for x in self.logs if x.get("routine") == rid
                   or self.day_of_entry.get(x.get("slot_entry")) in mine)
        sessions = sum(1 for x in self.sessions if x.get("routine") == rid or x.get("day") in mine)
        return logs, sessions


def own_routines(api: Api) -> list[dict]:
    """The person's routines and own templates (routine/ also lists other people's public templates)."""
    rs = {r["id"]: r for r in api.list("routine/") if not r.get("is_template")}
    for r in api.list("templates/"):
        rs.setdefault(r["id"], r)
    return list(rs.values())


def sync_routines(api: Api, week: list[dict], ex_ids: dict, units: dict, today: dt.date, warn):
    """The person's plan routines (see the module docstring). Never deletes a routine."""
    rs = [r for r in api.list("routine/") if not r.get("is_template")]
    st = State(api)
    got: dict = {}
    for r in sorted(rs, key=lambda r: str(r["id"])):
        t = routine_tag(r)
        if t in DAY_TAGS and t not in got:
            got[t] = r
    want = {b["key"] for b in week}
    end = base_monday(today) + dt.timedelta(days=END_DAYS)
    for t in DAY_TAGS:                  # one window for all: the first still-valid one
        r = got.get(t)
        if t in want and r is not None and end_ok(dt.date.fromisoformat(r["end"]), today):
            end = dt.date.fromisoformat(r["end"])
            break
    base = end - dt.timedelta(days=END_DAYS)
    for b in week:
        sync_routine(api, b, got.get(b["key"]), base - dt.timedelta(days=DAY_TAGS.index(b["key"])), end, st,
                     ex_ids, units, warn)
    keep = {got[k]["id"] for k in want if k in got}
    stale = [r for r in own_routines(api) if r["id"] not in keep and KDK_ANY_RE.search(r.get("description") or "")]
    if stale:
        warn(f"{api.who}: {len(stale)} kdk routine(s)/template(s) not in the plan "
             f"({', '.join(repr(r.get('name')) for r in stale[:6])}{' ...' if len(stale) > 6 else ''}); "
             "--purge-kdk removes them")


def sync_routine(api: Api, block: dict, r: dict | None, start: dt.date, end: dt.date, st: State,
                 ex_ids: dict, units: dict, warn):
    name = block["name"]
    want = {"name": name, "description": block["description"], "fit_in_week": False,
            "start": start.isoformat(), "end": end.isoformat()}
    if r is None:
        r = api.write("POST", "routine/", f"{name!r} {want['start']}..{want['end']}", want)
    else:
        patch = {k: v for k, v in want.items() if r.get(k) != v}
        if patch:
            api.write("PATCH", f"routine/{r['id']}/", f"{name!r} {sorted(patch)}", patch)
    rid = r["id"]
    sdays = [d for d in st.days if d["routine"] == rid]
    day_ids = {d["id"] for d in sdays}
    sslots = [s for s in st.slots if s["day"] in day_ids]
    slot_ids = {s["id"] for s in sslots}
    sentries = [e for e in st.entries if e["slot"] in slot_ids]
    entry_ids = {e["id"] for e in sentries}
    scfg = {k: [c for c in st.cfg[k] if c["slot_entry"] in entry_ids] for k in CONFIGS}
    logged = {x.get("slot_entry") for x in st.logs}
    used = st.used_days(rid)

    used_days = set()
    for order, pd in enumerate(block["days"], 1):
        d = next((x for x in sdays if x["id"] not in used_days and x.get("name") == pd["name"]), None) or \
            next((x for x in sdays if x["id"] not in used_days and x["id"] not in used
                  and x.get("order") == order), None)
        dwant = {"name": pd["name"], "description": pd["description"], "order": order,
                 "is_rest": pd["is_rest"], "need_logs_to_advance": False}
        if d is None:
            d = api.write("POST", "day/", name, {"routine": rid, "type": "custom", **dwant})
        else:
            used_days.add(d["id"])
            patch = {k: v for k, v in dwant.items() if d.get(k) != v}
            if patch:
                api.write("PATCH", f"day/{d['id']}/", f"{name} {sorted(patch)}", patch)
        sync_slots(api, {**pd, "name": name}, d["id"], [s for s in sslots if s["day"] == d["id"]],
                   sentries, scfg, logged, ex_ids, units, warn)
    for x in sdays:
        if x["id"] in used_days:
            continue
        if x["id"] in used:
            warn(f"{api.who}: {name}: extra day {x.get('name')!r} (id {x['id']}) has logs or sessions; left "
                 "alone: delete it in the app once you do not need them")
        else:
            api.write("DELETE", f"day/{x['id']}/", f"{name} extra day {x.get('name')!r} (no logs, no sessions)")


def sync_slots(api, pd, day_id, sslots, sentries, scfg, logged, ex_ids, units, warn):
    def entries_of(slot_id):
        return sorted((e for e in sentries if e["slot"] == slot_id), key=lambda e: (e.get("order", 0), e["id"]))

    existing = sorted(sslots, key=lambda s: (s.get("order", 0), s["id"]))
    used = set()
    for order, ps in enumerate(pd["slots"], 1):
        exs = tuple(ex_ids[e["key"]] for e in ps["entries"])
        s = next((x for x in existing if x["id"] not in used
                  and tuple(e["exercise"] for e in entries_of(x["id"])) == exs), None)
        if s is None:
            s = api.write("POST", "slot/", f"{pd['name']} #{order} {[e['key'] for e in ps['entries']]}",
                          {"day": day_id, "order": order, "comment": ps["comment"]})
            cur_entries = [None] * len(ps["entries"])
        else:
            used.add(s["id"])
            patch = {k: v for k, v in (("order", order), ("comment", ps["comment"])) if s.get(k) != v}
            if patch:
                api.write("PATCH", f"slot/{s['id']}/", f"{pd['name']} #{order} {sorted(patch)}", patch)
            cur_entries = entries_of(s["id"])
        for eorder, (pe, ce) in enumerate(zip(ps["entries"], cur_entries, strict=True), 1):
            ewant = {"exercise": ex_ids[pe["key"]], "order": eorder, "comment": pe["comment"],
                     "repetition_unit": units[pe["unit"]], "weight_unit": units["lb"]}
            label = f"{pd['name']} {pe['key']}"
            if ce is None:
                ce = api.write("POST", "slot-entry/", label, {"slot": s["id"], "type": "normal", **ewant})
                cfgs = {k: [] for k in CONFIGS}
            else:
                patch = {k: v for k, v in ewant.items() if ce.get(k) != v}
                if patch:
                    api.write("PATCH", f"slot-entry/{ce['id']}/", f"{label} {sorted(patch)}", patch)
                cfgs = {k: [c for c in scfg[k] if c["slot_entry"] == ce["id"]] for k in CONFIGS}
            for k, path in CONFIGS.items():
                v = pe[k]
                first = next((c for c in cfgs[k] if c.get("iteration") == 1), None)
                if v is None:
                    if first is not None:
                        api.write("DELETE", f"{path}/{first['id']}/", f"{label} {k}")
                elif first is None:
                    api.write("POST", f"{path}/", f"{label} {k}={v:g}",
                              {"slot_entry": ce["id"], "iteration": 1, "value": v})
                elif num(first.get("value")) != float(v):
                    api.write("PATCH", f"{path}/{first['id']}/", f"{label} {k}={v:g}", {"value": v})
    tail = RETIRED_ORDER
    for x in existing:
        if x["id"] in used:
            continue
        ents = entries_of(x["id"])
        if any(e["id"] in logged for e in ents):
            tail += 1
            comment = x.get("comment") or ""
            if comment.startswith(RETIRED) and x.get("order", 0) >= RETIRED_ORDER:
                continue
            warn(f"{api.who}: {pd['name']} slot {x['id']} is no longer in the plan but has logs; kept at the end")
            api.write("PATCH", f"slot/{x['id']}/", f"{pd['name']} retire slot {x['id']}",
                      {"order": tail, "comment": short(RETIRED + comment, 200)})
        else:
            api.write("DELETE", f"slot/{x['id']}/", f"{pd['name']} slot {x['id']} (not in the plan, no logs)")


# ---------------------------------------------------------------- purge
def purge_fallback() -> str:
    return ("FALLBACK (exercises the API cannot see or delete, e.g. ones without a translation). "
            "Laptop -> kdk-dkr-01:\n"
            "printf '%s\\n' \\\n"
            "  \"from wger.exercises.models import Exercise\" \\\n"
            f"  \"qs = Exercise.objects.filter(license_author='{LICENSE_AUTHOR}')\" \\\n"
            "  \"print(qs.count(), 'kdk-lab exercises'); print(qs.delete())\" \\\n"
            "  | ssh kdkadmin@10.1.20.20 'sudo docker exec -i wger-web python3 manage.py shell'")


def kdk_exercises(api: Api) -> list[dict]:
    return [e for e in api.list("exerciseinfo/") if e.get("license_author") == LICENSE_AUTHOR]


def purge_kdk(apis: dict[str, Api], owner: Api | None, plan: dict, apply: bool, out) -> int:
    """--purge-kdk: see the module docstring. apis = login -> that person's Api (selected people)."""
    plan_ids, plan_uuids = set(), {ex["uuid"] for ex in plan["exercises"].values()}
    totals = {"routines": 0, "logs": 0, "sessions": 0}
    doomed: dict[str, list] = {}
    states: dict[str, State] = {}
    for login, api in apis.items():
        keep_tags = {r["day"] for r in plan["people"][login]["routines"]}
        st = states[login] = State(api)
        kill = []
        for r in sorted(own_routines(api), key=lambda r: str(r["id"])):
            desc = r.get("description") or ""
            if not KDK_ANY_RE.search(desc):
                continue                                    # not generated by us: never touched
            if not r.get("is_template") and routine_tag(r) in keep_tags:
                plan_ids.add(r["id"])                       # a plan routine: never touched
                continue
            logs, sessions = st.history(r["id"])
            kill.append((r, logs, sessions))
            totals["routines"] += 1
            totals["logs"] += logs
            totals["sessions"] += sessions
            out(f"{login}: delete {'template' if r.get('is_template') else 'routine'} {r.get('name')!r} "
                f"(id {r['id']}, {logs} log(s), {sessions} session(s))")
        doomed[login] = kill
    reader = owner or next(iter(apis.values()))
    exs = kdk_exercises(reader)
    clash = [e["id"] for e in exs if e.get("uuid") in plan_uuids]
    if clash:
        raise WgerError(f"plan exercise(s) {clash} are {LICENSE_AUTHOR} exercises; refusing to purge")
    ex_ids = {e["id"] for e in exs}
    gone = {r["id"] for kill in doomed.values() for r, _, _ in kill}
    cascade = sum(1 for st in states.values() for x in st.logs
                  if x.get("exercise") in ex_ids and x.get("routine") not in gone)
    out(f"SUMMARY: {totals['routines']} routine(s)/template(s) with {totals['logs']} log(s) and "
        f"{totals['sessions']} session(s); {len(exs)} {LICENSE_AUTHOR} exercise(s) "
        + (f"(their {cascade} log(s) in the kept routines cascade with them)" if apis else "")
        + ("" if owner else "; exercises skipped (the first person is not selected)"))
    if plan_ids:
        out(f"kept: {len(plan_ids)} plan routine(s) (ids {sorted(plan_ids, key=str)})")
    if not apply:
        out("dry run: nothing deleted; add --apply to delete")
        out(purge_fallback())
        return 0
    for login, kill in doomed.items():
        for r, _, _ in kill:
            apis[login].write("DELETE", f"routine/{r['id']}/", f"{r.get('name')!r}")
    failed = []
    if owner is not None:
        for e in exs:
            try:
                owner.write("DELETE", f"exercise/{e['id']}/", f"{LICENSE_AUTHOR} exercise {e['id']}")
            except WgerError as err:
                failed.append(f"{e['id']}: {err}")
    # verify: nothing kdk is left
    left_r = [(login, r["id"]) for login, api in apis.items() for r in own_routines(api)
              if KDK_ANY_RE.search(r.get("description") or "") and r["id"] not in plan_ids]
    left_e = kdk_exercises(owner) if owner is not None else []
    for f in failed:
        out(f"FAILED: exercise {f}")
    if left_r or left_e or failed:
        out(f"NOT CLEAN: {len(left_r)} kdk routine(s) {left_r}, {len(left_e)} {LICENSE_AUTHOR} exercise(s) remain")
        out(purge_fallback())
        return 1
    out(f"clean: 0 kdk routines outside the plan, 0 {LICENSE_AUTHOR} exercises visible to the API. Run the "
        "fallback once anyway: it also catches untranslated exercises the API cannot see")
    out(purge_fallback())
    return 0


# ---------------------------------------------------------------- videos
def check_videos(plan: dict, out) -> int:
    """Every exercise's video answers YouTube oEmbed with 200 (prints the title to compare).
    An exercise with "video": null is listed as having none, not counted."""
    bad = total = 0
    for key, ex in plan["exercises"].items():
        url = ex.get("video")
        if url is None:
            out(f"none {key} ({ex['name']}): no video by design (\"video\": null)")
            continue
        total += 1
        try:
            r = requests.get(OEMBED, params={"url": url, "format": "json"}, timeout=TIMEOUT)
        except requests.RequestException as e:
            out(f"FAIL {key}: {url} ({e.__class__.__name__})")
            bad += 1
            continue
        if r.status_code != 200:
            out(f"FAIL {key}: {url} HTTP {r.status_code}")
            bad += 1
            continue
        j = r.json()
        out(f"ok   {key}: {url} | {j.get('author_name')} | {j.get('title')}")
    out(f"{total - bad}/{total} video(s) answer oEmbed")
    return 1 if bad else 0


# ---------------------------------------------------------------- main
def env_key(login: str) -> str:
    return "WGER_API_KEY_" + re.sub(r"[^A-Z0-9]", "_", login.upper())


def main(argv: list[str] | None = None, today: dt.date | None = None, plan_path: Path = PLAN,
         videos_path: Path | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="exit 1 if wger differs from the plan")
    mode.add_argument("--check-videos", action="store_true", help="every video answers YouTube oEmbed")
    mode.add_argument("--purge-kdk", action="store_true",
                      help="delete the old kdk routines/templates and kdk-lab exercises (dry run unless --apply)")
    ap.add_argument("--apply", action="store_true", help="write to wger (default: dry run)")
    ap.add_argument("--person", action="append", help="login to sync; repeatable; default all")
    args = ap.parse_args(argv)
    today = today or dt.date.today()
    warnings: list[str] = []

    def warn(msg):
        warnings.append(msg)
        print(f"WARN {msg}", file=sys.stderr)

    try:
        if args.apply and (args.check or args.check_videos):
            raise WgerError("--apply does not combine with --check / --check-videos")
        plan = load_plan(plan_path)
        if args.check_videos:
            return check_videos(plan, print)
        videos = load_videos(videos_path)
        no_video = missing_videos(plan, videos)
        base = os.environ.get("WGER_URL")
        if not base:
            raise WgerError("WGER_URL is not set (e.g. https://wger.kmkdp.com)")
        logins = list(plan["people"])
        sel = logins
        if args.person:
            unknown = set(args.person) - set(logins)
            if unknown:
                raise WgerError(f"unknown --person {sorted(unknown)}; known: {logins}")
            sel = [x for x in logins if x in args.person]
        keys = {}
        for login in sel:
            k = os.environ.get(env_key(login))
            if k:
                keys[login] = k
            elif args.apply or args.check or args.purge_kdk:
                raise WgerError(f"{env_key(login)} is not set (op read it into the env; never argv)")
            else:
                warn(f"{env_key(login)} is not set: dry run checks the exercises only, not {login}'s routines")
        apply = args.apply
        actions: list[str] = []
        owner_login = logins[0]
        owner_sel = owner_login in sel
        writer = Api(base, keys.get(owner_login), owner_login, apply, actions) if owner_sel else None
        if writer is not None and apply:
            perm = writer.get("check-permission/", permission="exercises.add_exercise")
            if not perm.get("result"):
                raise WgerError(f"{owner_login} lacks exercises.add_exercise; make them a wger "
                                "superuser first (docs/runbooks/wger.md step 7)")

        if args.purge_kdk:
            apis = {x: (writer if x == owner_login else Api(base, keys[x], x, apply, actions)) for x in sel}
            rc = purge_kdk(apis, writer, plan, apply, print)
            print(f"{len(actions)} delete(s) {'made' if apply else 'planned (dry run)'}")
            return rc

        if no_video and owner_sel:
            msg = (f"{len(no_video)} plan exercise(s) have no Jellyfin item id in {VIDEOS.name} "
                   f"({', '.join(no_video[:5])}{' ...' if len(no_video) > 5 else ''}); run "
                   "exercise_videos.py download + jellyfin --apply and commit the file first "
                   "(docs/runbooks/exercise-videos.md)")
            if apply:
                raise WgerError(msg + ". Nothing written")
            if args.check:
                actions.extend(f"{k}: no Jellyfin item id for the Video: line" for k in no_video)
            else:
                warn(msg + ". Their descriptions stay as they are")
        reader = writer or Api(base, keys.get(sel[0]), sel[0], apply, actions)
        ref = reference(reader)
        ex_ids = sync_exercises(reader, writer, plan, ref, warn, videos)

        weeks = {x: plan_routines(plan, x) for x in sel}
        needed = {e["unit"] for w in weeks.values() for b in w for d in b["days"] for s in d["slots"]
                  for e in s["entries"]}
        units, missing = {}, []
        for u in sorted(needed):
            uid = ref["rep_unit"].get(UNIT_NAMES[u])
            if uid is None:
                missing.append(UNIT_NAMES[u])
            units[u] = uid if uid is not None else f"dry-missing-{u}"
        if WEIGHT_UNIT not in ref["weight_unit"]:
            raise WgerError(f"wger has no weight unit {WEIGHT_UNIT!r}")
        units["lb"] = ref["weight_unit"][WEIGHT_UNIT]
        if missing:
            print(admin_unit_steps(missing))
            if apply:
                print("STOP: routines not written until the unit exists.", file=sys.stderr)
                return 3
            actions.append(f"admin: create repetition unit(s) {missing}")

        for login in sel:
            if login not in keys:
                continue
            api = writer if login == owner_login else Api(base, keys[login], login, apply, actions)
            sync_routines(api, weeks[login], ex_ids, units, today, warn)
    except WgerError as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 2

    verb = "DRIFT" if args.check else ("did" if apply else "would")
    for a in actions:
        print(f"{verb}: {a}")
    n = len(actions)
    if args.check:
        print(f"{n} difference(s) between wger and the plan" if n else "wger matches the plan")
        return 1 if n else 0
    print(f"{n} write(s) {'made' if apply else 'planned (dry run; add --apply to write)'}; "
          f"{len(warnings)} warning(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
