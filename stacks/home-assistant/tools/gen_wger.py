#!/usr/bin/env python3
"""Load the workout catalog into wger: custom exercises + weekday-pinned routines per person.

    WGER_URL=https://wger.kmkdp.com WGER_API_KEY_JMACK=... WGER_API_KEY_RACH=... \\
    python3 stacks/home-assistant/tools/gen_wger.py --posters DIR            # dry run: print the plan
    python3 stacks/home-assistant/tools/gen_wger.py --posters DIR --apply    # write it
    python3 stacks/home-assistant/tools/gen_wger.py --posters DIR --check    # exit 1 on drift
    ... --person rach                                                        # one person only

Reads  tools/workout_catalog.json (people + the slugs; same loader as workout_catalog.py)
       tools/wger_exercises.json  (per slug: wger muscles, secondary muscles, equipment,
                                   optional what/safety lines; workout_catalog.py ignores it)
       DIR/workout.json           (gen-workout-guide.py output from the private vault, written
                                  to a laptop scratch dir, never published: days, steps, doses,
                                  titles, cues), for
                                  every person WITHOUT their own catalog plan ("plans".<login>);
                                  a person with one gets their week from it (plan_guide())
       DIR/exercise/ex-<slug>-step<N>.png (per-step photos, N from 1; step 1 = start position)
       DIR/exercise/ex-<slug>-rom.png     (the poster; used only when a slug has no step photos)
Config from the environment only: WGER_URL, and one API key per person in
WGER_API_KEY_<LOGIN> (login upper-cased, e.g. WGER_API_KEY_JMACK). Keys never go in argv.

What it writes (runbook: docs/runbooks/wger.md, "Load the catalog"):
  1. One custom exercise per slug (catalog slugs + any slug the guide uses): category,
     equipment and muscles (primary + secondary, from wger_exercises.json), English
     translation: name = catalog name (or the guide title for multi-slug items),
     description from the template in describe() (what it trains, dose per weekday,
     cues, safety line) and a marker "<!-- kdk:<slug> v:<text hash> img:<image-set
     hash>#<image ids> -->". wger renders description_source to the HTML description
     and strips comments, so the marker is invisible in the app and the web UI but stays
     in description_source, where it is found. The marker is the idempotency key:
     exercises are found by it, the text is patched only when its hash changes, the
     images are uploaded only when the hash over all of the slug's image files changes
     (then every other image of the exercise is deleted). Exercises are global in wger, so they are written with the FIRST
     person's key (jmack, a wger superuser: trusted + exempt from the 20/hour
     exercise-create throttle) and only when that person is selected.
  2. Units: looked up by name (Repetitions, Seconds, Minutes, "10-ft lengths"; lb).
     The REST API cannot create units. A missing one is printed as an admin step and
     --apply then stops before the routines.
  3. Per person, one routine per weekday, each holding that whole day in the guide's order
     (Jacob: walk, PT block, hands, lifts or flow/yoga, PT stretches; Rachel: desk breaks,
     the day's session, evening mobility), so the app's routine list reads Mon..Sun and a
     routine has nothing but its day. Name "<Mon> · <the day's title>" (Jacob: the guide
     title after the dash, e.g. "Mon · Lower A"; Rachel: her non-daily groups, else "Daily
     parts"), shortened by SHORTEN until it fits wger's 25 characters (the run stops when it
     cannot). Each routine: ONE day (name = the weekday, description = the guide's summary),
     fit_in_week off: a one-day routine without the weekly schedule repeats that day on
     every date, so the app always shows it, with no rest-day padding and no weekday
     mapping (with fit_in_week on, wger restarts the cycle each Monday and the day would
     only land on Mondays). One slot per step, supersets and "either one" pairs as one slot
     with two entries, "Every day" steps expanded, sets/reps/max-reps from the dose text,
     rest/max-rest from "rest 60–90 s" (the app's rest timer then counts that down), weights
     left blank. A per-side dose ("/ leg", "/ side", "/ arm", "/ hand", "/ direction", "each
     way") doubles the sets, one logged set per side (odd = left, even = right), said in the
     slot and entry comments; the exercise description keeps the dose as written. A
     "2–3× a day" dose is one break's worth (the break is not multiplied). The description
     ends with the tag "[kdk:<mon..sun>]": routines are found by it (the HA treadmill
     auto-log that also read it was retired 2026-11-08).
     Window: wger caps a routine at 120 days and lists routines by -start, -created, so the
     starts are staggered one day per weekday (Monday's routine starts on this week's
     Monday, Sunday's six days earlier) with one shared end 16 weeks after that Monday: the
     list reads Mon..Sun whatever order the routines were created in. An --apply within 28
     days of the end rolls all seven forward to this week.
     Migration (from the block routines of 2026-11-07, or the older "Week (kdk)"): a
     generated routine without a weekday tag is reused for a weekday: one with logs or
     sessions for the weekday of its first logs (jmack's Daily: Friday, its walk slot keeps
     the logs); each other logged day of it moves, the same day row with its slots, into
     that weekday's routine, its logs and sessions re-pointed (PATCH routine); the rest go to
     the free weekdays whose exercises they already hold most of.
     Leftover generated routines are deleted when they have no logs and no sessions.
  4. Per person, one routine TEMPLATE per weekday routine, owned by that person:
     is_template + is_public (wger 2.7: public templates are listed to every user at
     public-templates/ and in the web UI's Templates view, and anyone can copy one; the iOS
     app 2.1.x syncs no templates), the same single day, slots and configs, the same
     window, named "<routine name> (template)" when that fits 25 characters, else
     "Tpl · <name>" (shortened further by SHORTEN), tagged "[kdk:tpl:<mon..sun>]" and found
     by that tag among the person's own templates (templates/). A copy is an ordinary
     routine of whoever made it (wger keeps no link and copies the description, whose
     tpl tag the routine search ignores), so copies are never touched and deleting a
     template never touches them; a duplicate template is deleted only without logs or
     sessions.
Slots are matched by their exercises, never rebuilt: a WorkoutLog cascades away with
its slot entry, so a slot that has logs is never deleted (it is moved to the end and
marked "[retired]" instead). A day is matched by its weekday name, and a day the plan does
not want is deleted only when it has no logs and no sessions (a session cascades away
with its day). A routine with logs or sessions is never deleted.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import itertools
import json
import os
import re
import sys
from pathlib import Path

import requests

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import workout_catalog  # noqa: E402  (the catalog loader + validation)

LEGACY_NAME = "Week (kdk)"      # the one-routine-per-person layout before 2026-11-07
ROUTINE_NAME_MAX = 25           # wger Routine.name max_length
TAG_RE = re.compile(r"\[kdk:([a-z_]+)\]")
DAY_TAGS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
SHORTEN = [("flexibility flow", "flow"), ("Treadmill", "Walk"), (" + ", "+")]   # in turn until it fits
TPL_RE = re.compile(r"\[kdk:tpl:([a-z]+)\]")
TPL_SUFFIX = " (template)"
TPL_PREFIX = "Tpl · "
DAILY_PARTS = "Daily parts"     # Rachel's title for a day with only her every-day groups
# wger rejects a translation whose name is >= 0.85 trigram-similar to any existing English
# exercise (11 catalog names match upstream ones exactly). "(kdk)" is not distinct enough
# for long names; this suffix clears the rule for every catalog name (tested 2026-11-06).
ALT_SUFFIX = " · kdk plan"


def alt_name(name: str) -> str:
    return f"{name}{ALT_SUFFIX}"
ROUTINE_DESC = ("Generated by gen_wger.py from the kdk workout plan; edits here are overwritten. "
                "Weights are yours to fill in.")
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
WINDOW_DAYS = 118          # longest span (Sunday's routine): wger MAX_DURATION_DAYS = 120
END_DAYS = WINDOW_DAYS - 6  # the shared end: base Monday + 112 (16 weeks)
RENEW_DAYS = 28            # --apply rolls the window when it ends sooner than this
RETIRED = "[retired] "     # slot comment prefix for a removed slot that still has logs
RETIRED_ORDER = 900
LICENSE_AUTHOR = "kdk-lab"
TIMEOUT = 30

UNIT_NAMES = {"reps": "Repetitions", "seconds": "Seconds", "minutes": "Minutes",
              "lengths": "10-ft lengths"}
WEIGHT_UNIT = "lb"
KIND_UNIT = {"w": "reps", "r": "reps", "t": "seconds", "wt": "seconds", "wl": "lengths",
             "min": "minutes"}
WEIGHTED = {"w", "wt", "wl"}
ADMIN_UNIT = {"10-ft lengths": ("DISTANCE", 3)}   # unit_type, multiplier (to metres; 10 ft = 3.05 m)

# Category (wger's 8 built-in ones): group default, then per-slug overrides.
SIDECAR = TOOLS / "wger_exercises.json"
GROUP_CATEGORY = {"activation": "Legs", "lower_a": "Legs", "lower_b": "Legs", "upper_a": "Arms",
                  "upper_b": "Arms", "hands": "Arms", "cardio": "Legs", "daily": "Legs",
                  "swaps": "Chest"}
SLUG_CATEGORY = {
    "calf-raise": "Calves", "sl-calf": "Calves", "tib-raise": "Calves",
    "incline-press": "Chest", "pushup": "Chest", "flat-press": "Chest", "flex-doorway-chest": "Chest",
    "cs-row": "Back", "one-arm-row": "Back", "flex-cat-cow": "Back", "flex-thread-needle": "Back",
    "flex-supine-twist": "Back", "flex-sphinx": "Back",
    "shoulder-press": "Shoulders", "lateral-raise": "Shoulders", "rear-fly": "Shoulders",
    "arm-circles": "Shoulders", "flex-dolphin": "Shoulders", "flex-neck": "Shoulders",
    "plank": "Abs", "side-plank": "Abs", "dead-bug": "Abs", "suitcase": "Abs", "flex-side-bend": "Abs",
    "treadmill-walk": "Cardio",
    "pallof-press": "Abs", "pelvic-tilt": "Abs", "wall-pushup": "Chest", "seated-band-row": "Back",
    "glute-calf-raise": "Calves", "water-walk": "Cardio", "recumbent-bike": "Cardio",
}
EITHER_COMMENT = "Either one: log the one you did"
BODYWEIGHT = "none (bodyweight exercise)"
# wger's 16 built-in muscles as the sidecar spells them (name_en, else the Latin name): the
# only ones the app's body map can colour (it draws bundled SVGs per muscle id).
MUSCLES = ("Shoulders", "Biceps", "Hamstrings", "Brachialis", "Lower back", "Calves", "Glutes", "Lats",
           "Obliquus externus abdominis", "Chest", "Quads", "Abs", "Serratus anterior", "Soleus",
           "Trapezius", "Triceps")
# Local muscle rows (runbook step 13) for areas the built-ins miss: name -> is_front. They stay in
# the sidecar for the description text, but LINK_CUSTOM_MUSCLES keeps them off the wger exercise:
# the iOS app loads a bundled muscle-<id>.svg for every linked muscle, ships only ids 1-16, and
# raises an error for any other id (seen on app 2.1.2, 2026-11-09). Set it to True once the app
# skips muscles it has no image for. While True, a sidecar name the server lacks is left out with
# a warning when it is one of these, and is an error (a typo) otherwise.
LINK_CUSTOM_MUSCLES = False
CUSTOM_MUSCLES = {"Forearm flexors": True, "Forearm extensors": False, "Hand intrinsics": True,
                  "Adductors": True, "Tibialis anterior": True}
PROSE = {"Obliquus externus abdominis": "obliques", "Serratus anterior": "serratus",
         "Trapezius": "upper back (trapezius)", "Hand intrinsics": "small hand muscles",
         "Adductors": "inner thighs (adductors)", "Tibialis anterior": "shins (tibialis anterior)"}
SIDECAR_KEYS = {"muscles", "secondary", "equipment", "what", "safety", "stretch"}
SAFETY_WEIGHTED = "Start light; stop two reps before your form slips."
SAFETY_STRETCH = "Ease into a mild stretch; never bounce, keep breathing."
SAFETY_DEFAULT = "Move slowly in a pain-free range; stop if anything hurts sharply."
STEP_RE = re.compile(r"^ex-(?P<slug>[a-z0-9-]+)-step(?P<n>\d+)\.png$")
STYLE_PHOTO = "4"          # wger 2.7 ExerciseImage.STYLE: 1 line, 2 3D, 3 low-poly, 4 photo, 5 other
WEEKDAY_ABBR = [w[:3] for w in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]]

CONFIGS = {"sets": "sets-config", "reps": "repetitions-config", "max_reps": "max-repetitions-config",
           "rest": "rest-config", "max_rest": "max-rest-config"}
# Per-side doses ("2 × 15–30 / leg", "30 s / side", "30 s each way"): every logged set is ONE side,
# so the sets double (L, R, L, R ...) and the comments say so. "each position" is not a side.
SIDE_RE = re.compile(r"(?:/\s*|\beach\s+)(leg|side|arm|hand|direction|way)\b")
SIDE_NOTE = {"leg": "L/R", "side": "L/R", "arm": "L/R", "hand": "L/R", "direction": "dir", "way": "dir"}
SIDE_ENTRY = {"L/R": "Each set is one side: odd sets left, even sets right",
              "dir": "Each set is one direction: odd sets one way, even sets the other"}
SIDE_SLOT = {"L/R": " · each set one side (L, R)", "dir": " · each set one direction"}
REST_RE = re.compile(r"\brest\s+(\d+)(?:\s*[–-]\s*(\d+))?\s*s\b")      # "rest 60–90 s" (seconds)

MARKER_RE = re.compile(r"kdk:(?P<slug>[a-z0-9-]+) v:(?P<v>[0-9a-f]{12}) img:(?P<img>[0-9a-f]{12}|none)"
                       r"(?:#(?P<imgid>\d+(?:,\d+)*))?")


class WgerError(RuntimeError):
    pass


# ---------------------------------------------------------------- dose parsing
UNIT_WORD = {"s": "seconds", "sec": "seconds", "min": "minutes", "length": "lengths",
             "lengths": "lengths", "rep": "reps", "reps": "reps"}
DOSE_SETS = re.compile(r"^(\d+)\s*[×x]\s*(\d+)(?:\s*[–-]\s*(\d+))?(?:\s*(s|sec|min|lengths?|reps?)\b)?")
DOSE_SETS_NOVALUE = re.compile(r"^(\d+)\s*[×x]\s*[^\d\s]")    # "5 × each position"
DOSE_SINGLE = re.compile(r"^~?(\d+)(?:\s*[–-]\s*(\d+))?\s*(?:slow\s+)?(s|sec|min|lengths?|reps?)\b")


def parse_dose(text: str, kind: str | None = None) -> dict:
    """'3 × 8–12 · start light' -> {sets: 3, value: 8, max: 12, unit: 'reps'}.

    unit is reps | seconds | minutes | lengths. An unparseable dose gives sets/value
    None and the kind's unit; `explicit` says whether the text named the unit."""
    t = (text or "").strip()
    default = KIND_UNIT.get(kind or "", "reps")
    m = DOSE_SETS.match(t)
    if m:
        unit = UNIT_WORD.get(m.group(4) or "")
        return {"sets": int(m.group(1)), "value": int(m.group(2)),
                "max": int(m.group(3)) if m.group(3) else None,
                "unit": unit or default, "explicit": bool(unit)}
    m = DOSE_SINGLE.match(t)
    if m:
        return {"sets": 1, "value": int(m.group(1)), "max": int(m.group(2)) if m.group(2) else None,
                "unit": UNIT_WORD[m.group(3)], "explicit": True}
    m = DOSE_SETS_NOVALUE.match(t)
    if m:
        return {"sets": 1, "value": int(m.group(1)), "max": None, "unit": "reps", "explicit": False}
    return {"sets": None, "value": None, "max": None, "unit": default, "explicit": False}


def short(text: str, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def h12(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()[:12]


# ---------------------------------------------------------------- desired state
def load_guide(posters: Path | None) -> dict:
    if posters is None:
        raise WgerError("--posters DIR is required (the guide output dir with workout.json and exercise/)")
    path = posters / "workout.json"
    if not path.is_file():
        raise WgerError(f"{path} not found (run tools/gen-workout-guide.py first; see the runbook)")
    return json.loads(path.read_text(encoding="utf-8"))


def plan_guide(pl: dict) -> dict:
    """A catalog plan ("plans".<login>) in workout.json's shape: 7 days, each the plan's items
    for that weekday in group order (daily groups included), one step per slug."""
    order = {g["key"]: i for i, g in enumerate(pl["groups"])}
    items = sorted(pl["items"], key=lambda it: order[it["group"]])     # stable: catalog order in a group
    titles = {g["key"]: g["title"].split(" · ")[0] for g in pl["groups"]}
    days = []
    for wd in WEEKDAYS:
        abbr = wd[:3].lower()
        its = [it for it in items if abbr in workout_catalog.item_days(it)]
        steps = []
        for it in its:
            for k, st in enumerate(it["steps"]):
                steps.append({"num": str(len(steps) + 1), "slug": st["slug"], "title": st["title"],
                              "dose": st["dose"], "cues": list(st.get("cues", [])),
                              "either": bool(it.get("either")) and k == 0})
        groups, own = [], []
        for it in its:
            if titles[it["group"]] not in groups:
                groups.append(titles[it["group"]])
            if it.get("days") != ["daily"] and titles[it["group"]] not in own:
                own.append(titles[it["group"]])
        days.append({"id": abbr, "name": wd, "title": " + ".join(own) or DAILY_PARTS,
                     "summary": " + ".join(groups), "steps": steps})
    return {"version": 2, "everyday": [], "days": days}


def plan_sources(cat: dict, guide_for) -> dict[str, tuple[dict, dict]]:
    """login -> (plan, guide) for every person, in catalog order. guide_for() loads workout.json
    (only called when some person has no catalog plan)."""
    out, shared = {}, None
    for p in cat["people"]:
        pl = workout_catalog.plan(cat, p)
        if pl is cat:
            shared = shared if shared is not None else guide_for()
            out[p["login"]] = (cat, shared)
        else:
            out[p["login"]] = (pl, plan_guide(pl))
    return out


def all_specs(sources: dict, posters: Path, warn, sidecar: dict | None = None) -> dict[str, dict]:
    """Every person's exercise specs, merged (first person first). Exercises are global in wger,
    so a slug in two people's plans would need two doses on one exercise: an error."""
    specs: dict[str, dict] = {}
    owner: dict[str, str] = {}
    seen = set()
    for login, (pl, g) in sources.items():
        if id(pl) in seen:
            continue
        seen.add(id(pl))
        for slug, sp in exercise_specs(pl, g, posters, sidecar=sidecar, warn=warn).items():
            if slug in specs:
                raise WgerError(f"slug {slug} is in the plans of {owner[slug]} and {login}; give one of "
                                "them its own slug (a wger exercise carries one dose text)")
            specs[slug], owner[slug] = sp, login
    return specs


def day_steps(guide: dict, day: dict) -> list[dict]:
    """The day's steps with the 'Every day' pointer expanded, steps without a slug dropped."""
    out = []
    for st in day["steps"]:
        if st.get("slug"):
            out.append(st)
        elif st.get("everyday"):
            out.extend(s for s in guide.get("everyday", []) if s.get("slug"))
    return out


def strip_prefix(title: str) -> str:
    return re.sub(r"^(PT|Grip|Every day)\s+—\s+", "", title).strip()


def load_sidecar(path: Path = SIDECAR, known: set[str] | None = None) -> dict[str, dict]:
    """slug -> {muscles, secondary, equipment, what?, safety?, stretch?}. With `known` (the server's
    muscle names), a name that is neither there nor in CUSTOM_MUSCLES is a typo and raises; without
    it (offline) only the structure is checked, since the server may have local muscles."""
    data = json.loads(path.read_text(encoding="utf-8"))["exercises"]
    for slug, e in data.items():
        bad = set(e) - SIDECAR_KEYS
        if bad:
            raise WgerError(f"{path.name} {slug}: unknown key(s) {sorted(bad)}")
        for k in ("muscles", "secondary"):
            if known is None:
                continue
            unknown = [m for m in e.get(k, []) if m not in known and m not in CUSTOM_MUSCLES]
            if unknown:
                raise WgerError(f"{path.name} {slug}: unknown muscle(s) {unknown}; wger has {sorted(known)}")
        both = set(e.get("muscles", [])) & set(e.get("secondary", []))
        if both:
            raise WgerError(f"{path.name} {slug}: {sorted(both)} both primary and secondary")
        if not e.get("equipment"):
            raise WgerError(f"{path.name} {slug}: equipment is empty (use [\"{BODYWEIGHT}\"])")
    return data


def image_files(posters: Path, slug: str) -> list[Path]:
    """ex-<slug>-step<N>.png in N order; else the -rom poster; else []."""
    d = posters / "exercise"
    steps = sorted(((int(m.group("n")), f) for f in d.glob(f"ex-{slug}-step*.png")
                    if (m := STEP_RE.match(f.name)) and m.group("slug") == slug), key=lambda x: x[0])
    if steps:
        return [f for _, f in steps]
    rom = d / f"ex-{slug}-rom.png"
    return [rom] if rom.is_file() else []


def images_hash(files: list[Path]) -> str:
    """The marker's img hash. A lone poster keeps the old per-file hash (no re-upload on upgrade)."""
    if not files:
        return "none"
    if len(files) == 1 and files[0].name.endswith("-rom.png"):
        return h12(files[0].read_bytes())
    return h12("\n".join(f"{f.name}:{hashlib.sha256(f.read_bytes()).hexdigest()}" for f in files))


def prose_list(names: list[str]) -> str:
    words = [PROSE.get(n, n.lower()) for n in names]
    return "the " + (words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1])


def what_line(side: dict) -> str:
    if side.get("what"):
        return side["what"]
    if not side.get("muscles"):
        return ""
    verb = "Stretches" if side.get("stretch") else "Works"
    line = f"{verb} {prose_list(side['muscles'])}"
    if side.get("secondary"):
        line += f"; also {prose_list(side['secondary'])}"
    return line + "."


def safety_line(slug: str, kind: str | None, side: dict) -> str:
    if side.get("safety"):
        return side["safety"]
    if side.get("stretch"):
        return SAFETY_STRETCH
    if kind in WEIGHTED:
        return SAFETY_WEIGHTED
    return SAFETY_DEFAULT


def describe(name: str, title: str, doses: dict[str, list[str]], cues: list[str], safety: str,
             what: str) -> str:
    """The description template (markdown; wger keeps only p/strong/em/ul/ol/li, so every block
    is its own paragraph or list, never a bare line break):

        [<guide title>. ]<what it trains>.
        **Dose**  - <weekdays>: <dose>     (one bullet per distinct dose; "Every day" for all 7)
        **How**   - <cue>                  (the guide's cues, in order)
        **Safety:** <one line>
    """
    head = strip_prefix(title)
    first = " ".join(x for x in ((head + ".") if head and head.lower() != name.lower() else "", what) if x)
    blocks = [first] if first else []
    if doses:
        bullets = []
        for d, wds in doses.items():
            days = sorted(set(wds), key=WEEKDAY_ABBR.index)
            label = "Every day" if len(days) == 7 else ", ".join(days)
            bullets.append(f"- {label}: {d}" if d else f"- {label}")
        blocks.append("**Dose**\n\n" + "\n".join(bullets))
    else:
        blocks.append("**Dose**: not in the weekly plan (a swap or spare exercise).")
    if cues:
        blocks.append("**How**\n\n" + "\n".join(f"- {c}" for c in cues))
    blocks.append(f"**Safety:** {safety}")
    return "\n\n".join(blocks)


def exercise_specs(cat: dict, guide: dict, posters: Path, sidecar: dict | None = None,
                   warn=lambda m: None) -> dict[str, dict]:
    """slug -> {slug, name, text, category, equipment, muscles, secondary, images, kind, side}"""
    sidecar = load_sidecar() if sidecar is None else sidecar
    items = {s: it for it in cat["items"] for s in it["slugs"]}
    uses: dict[str, list] = {}
    order: list[str] = []
    for day in guide["days"]:
        for st in day_steps(guide, day):
            if st["slug"] not in uses:
                uses[st["slug"]] = []
                order.append(st["slug"])
            uses[st["slug"]].append((day["name"][:3], st))
    for s in items:
        if s not in uses:
            uses[s] = []
            order.append(s)
    specs = {}
    for slug in order:
        it = items.get(slug)
        steps = uses[slug]
        title = steps[0][1]["title"] if steps else (it["name"] if it else slug)
        if it and len(it["slugs"]) == 1:
            name = it["name"]
        elif steps:
            name = strip_prefix(title)
        else:
            name = slug.replace("-", " ").capitalize()
        doses: dict[str, list[str]] = {}
        cues: list[str] = []
        for wd, st in steps:
            doses.setdefault(st.get("dose", "").strip(), []).append(wd)
            c = st.get("cues") or []
            for cue in ([c] if isinstance(c, str) else c):
                if cue and cue not in cues:
                    cues.append(cue)
        kind = it["kind"] if it else None
        group = it["group"] if it else None
        side = sidecar.get(slug)
        if side is None:
            warn(f"exercise {slug}: no entry in {SIDECAR.name}; no muscles, bodyweight")
            side = {"muscles": [], "secondary": [], "equipment": [BODYWEIGHT], "_missing": True}
        category = SLUG_CATEGORY.get(slug) or GROUP_CATEGORY.get(group or "", "Legs")
        text = describe(name, title, doses, cues, safety_line(slug, kind, side), what_line(side))
        specs[slug] = {"slug": slug, "name": short(name, 190), "text": text,
                       "category": category, "equipment": list(side["equipment"]),
                       "muscles": list(side.get("muscles", [])), "secondary": list(side.get("secondary", [])),
                       "kind": kind, "images": image_files(posters, slug), "side": side}
    return specs


def make_slots(wd: str, steps: list[dict], kinds: dict, warn) -> list[dict]:
    """One day's steps -> [{comment, entries: [...]}]: a superset or "either one" step takes the
    next step into its slot."""
    slots, i = [], 0
    while i < len(steps):
        group = [steps[i]]
        either = bool(steps[i].get("either"))
        if ("superset" in steps[i].get("dose", "").lower() or either) and i + 1 < len(steps):
            group.append(steps[i + 1])
        i += len(group)
        entries = []
        for st in group:
            kind = kinds.get(st["slug"])
            d = parse_dose(st.get("dose", ""), kind)
            if kind in KIND_UNIT and d["explicit"] and d["unit"] != KIND_UNIT[kind]:
                warn(f"{wd} {st['slug']}: dose says {d['unit']}, catalog kind {kind} logs "
                     f"{KIND_UNIT[kind]}; using {d['unit']}")
            if d["sets"] is None:
                warn(f"{wd} {st['slug']}: dose {st.get('dose')!r} not parsed; no sets/reps")
            dose = re.sub(r"\s*·\s*superset:.*$", "", st.get("dose", "")).strip()
            side = per_side(dose)
            sets = d["sets"] * 2 if side and d["sets"] else d["sets"]
            rest = REST_RE.search(dose)
            note = f"{SIDE_ENTRY[side]} · {dose}" if side else dose
            entries.append({"slug": st["slug"], "unit": d["unit"], "sets": sets, "side": side,
                            "reps": d["value"], "max_reps": d["max"], "comment": short(note, 100),
                            "rest": int(rest.group(1)) if rest else None,
                            "max_rest": int(rest.group(2)) if rest and rest.group(2) else None})
        if len(group) == 2 and either:
            comment = EITHER_COMMENT
        elif len(group) == 2:
            comment = "Superset: alternate the two, rest after each pair"
        else:
            side = entries[0]["side"]
            comment = short(group[0].get("title", "") + (SIDE_SLOT[side] if side else ""), 200)
        slots.append({"comment": comment, "entries": entries})
    return slots


def per_side(dose: str) -> str | None:
    """'L/R' for a per-side dose, 'dir' for a per-direction one, else None."""
    m = SIDE_RE.search(dose or "")
    return SIDE_NOTE[m.group(1)] if m else None


def routine_name(abbr: str, title: str, limit: int = ROUTINE_NAME_MAX) -> str:
    """'Mon · Lower A' from the day title (any 'Monday — ' prefix dropped), shortened to fit."""
    title = re.sub(r"^\w+day\s+[—-]\s+", "", title or "").strip()
    name = f"{abbr} · {title}" if title else abbr
    for old, new in SHORTEN:
        if len(name) <= limit:
            break
        name = name.replace(old, new)
    if len(name) > limit:
        raise WgerError(f"routine name {name!r} is longer than wger's {ROUTINE_NAME_MAX} characters; "
                        "shorten the day title or add a SHORTEN rule")
    return name


def template_name(abbr: str, title: str) -> str:
    """'Mon · Lower A (template)' when that fits, else 'Tpl · Wed · Walk + flow'."""
    name = routine_name(abbr, title)
    if len(name) + len(TPL_SUFFIX) <= ROUTINE_NAME_MAX:
        return name + TPL_SUFFIX
    return TPL_PREFIX + routine_name(abbr, title, ROUTINE_NAME_MAX - len(TPL_PREFIX))


def plan_week(guide: dict, pl: dict, warn) -> list[dict]:
    """Seven routines, Monday..Sunday: [{key, name, description, days: [one day]}]."""
    kinds = {s: it["kind"] for it in pl["items"] for s in it["slugs"]}
    by_name = {d["name"]: d for d in guide["days"]}
    out = []
    for wd, tag in zip(WEEKDAYS, DAY_TAGS):
        day = by_name.get(wd)
        if day is None:
            raise WgerError(f"workout.json has no {wd}")
        slots = make_slots(wd, day_steps(guide, day), kinds, warn)
        out.append({"key": tag, "name": routine_name(wd[:3], day.get("title", "")),
                    "template_name": template_name(wd[:3], day.get("title", "")),
                    "description": f"{wd}. {ROUTINE_DESC} [kdk:{tag}]",
                    "days": [{"name": wd, "description": short(day.get("summary", ""), 1000),
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

    def write(self, method: str, path: str, what: str, payload: dict | None = None, files=None) -> dict:
        self.actions.append(f"{self.who}: {method} {path} {what}")
        if not self.apply:
            self._dry += 1
            return {**(payload or {}), "id": f"dry-{self._dry}"}
        if files is not None:
            return self._req(method, path, data=payload, files=files)
        return self._req(method, path, json=payload)


def is_dry(x) -> bool:
    return isinstance(x, str) and x.startswith("dry-")


def num(v):
    return None if v in (None, "") else float(v)


# ---------------------------------------------------------------- reference data
def reference(api: Api) -> dict:
    langs = {x["short_name"]: x["id"] for x in api.list("language/")}
    if "en" not in langs:
        raise WgerError("wger has no language 'en'")
    return {
        "en": langs["en"],
        "category": {c["name"]: c["id"] for c in api.list("exercisecategory/")},
        "equipment": {e["name"]: e["id"] for e in api.list("equipment/")},
        "muscle": {k: m["id"] for m in api.list("muscle/") for k in (m.get("name_en"), m["name"]) if k},
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
def server_exercises(api: Api, warn) -> dict[str, dict]:
    """slug -> {translation, marker} for every translation carrying a kdk marker."""
    found: dict[str, dict] = {}
    for t in api.list("exercise-translation/"):
        m = MARKER_RE.search(t.get("description_source") or t.get("description") or "")
        if not m:
            continue
        slug = m.group("slug")
        if slug in found:
            warn(f"exercise {slug}: two translations carry its marker (ids {found[slug]['translation']['id']}, "
                 f"{t['id']}); using the first")
            continue
        found[slug] = {"translation": t, "marker": m.groupdict()}
    return found


def marker(slug: str, text: str, img: str, imgids) -> str:
    ids = ",".join(str(i) for i in (imgids or []))
    return f"kdk:{slug} v:{h12(text)} img:{img}" + (f"#{ids}" if ids else "")


def with_marker(text: str, slug: str, img: str, imgids) -> str:
    # its own paragraph after a blank line: a markdown html block, dropped by wger's sanitizer
    return f"{text}\n\n<!-- {marker(slug, text, img, imgids)} -->"


def marker_ids(m: dict) -> list[int]:
    return [int(x) for x in (m.get("imgid") or "").split(",") if x]


def upload_images(api: Api, sp: dict, img_hash: str, ex_id, img: str, imgids: list, need_write, warn):
    """Upload the slug's images (in order, the first one main) when the set's hash differs from
    the marker's, then delete every other image of the exercise. Returns the new (img, ids)."""
    slug = sp["slug"]
    if img_hash == img:
        return img, imgids
    files = sp["images"]
    if not files:
        warn(f"exercise {slug}: no ex-{slug}-step<N>.png or ex-{slug}-rom.png; the old image(s) stay")
        return img, imgids
    need_write(f"upload images {slug}")
    new = []
    for i, f in enumerate(files):
        up = api.write("POST", "exerciseimage/", f"{slug} {f.name} ({i + 1}/{len(files)}, {img_hash})",
                       {"exercise": ex_id, "is_main": "true" if i == 0 else "false", "style": STYLE_PHOTO,
                        "is_ai_generated": "true", "license_author": LICENSE_AUTHOR},
                       files={"image": (f.name, f.read_bytes(), "image/png")})
        new.append(up["id"])
    stale = [] if is_dry(ex_id) else [x["id"] for x in api.list("exerciseimage/", exercise=ex_id)
                                      if x["id"] not in new and x.get("exercise") == ex_id]
    for old in stale:
        try:
            api.write("DELETE", f"exerciseimage/{old}/", f"{slug} old image {old}")
        except WgerError as e:
            warn(f"exercise {slug}: old image {old} not deleted ({e})")
    return img_hash, ([] if any(is_dry(x) for x in new) else new)


def sync_exercises(api: Api, specs: dict, ref: dict, can_write: bool, warn) -> dict[str, object]:
    """Returns slug -> exercise id (a dry-run placeholder for exercises it would create)."""
    have = server_exercises(api, warn)
    ids: dict[str, object] = {}
    exercises = {e["id"]: e for e in api.list("exercise/")} if have else {}
    missing_equipment: dict[str, list[str]] = {}
    missing_muscles: dict[str, list[str]] = {}

    def need_write(what):
        if not can_write and api.apply:
            raise WgerError(f"{what}: exercises are written with the first person's key; run with "
                            "that person selected (no --person, or --person <first login>)")

    for slug, sp in specs.items():
        cat_id = ref["category"].get(sp["category"])
        if cat_id is None:
            raise WgerError(f"wger has no exercise category {sp['category']!r} (for {slug})")
        equip = []
        for e in sp["equipment"]:
            if e in ref["equipment"]:
                equip.append(ref["equipment"][e])
            else:
                missing_equipment.setdefault(e, []).append(slug)
        equip.sort()
        muscles, secondary = [], []
        for names, out in ((sp["muscles"], muscles), (sp["secondary"], secondary)):
            for n in names:
                if n in CUSTOM_MUSCLES and not LINK_CUSTOM_MUSCLES:
                    continue
                if n in ref["muscle"]:
                    out.append(ref["muscle"][n])
                elif n in CUSTOM_MUSCLES:
                    missing_muscles.setdefault(n, []).append(slug)
                else:
                    raise WgerError(f"wger has no muscle {n!r} (for {slug}); fix {SIDECAR.name}")
        muscles.sort()
        secondary.sort()
        img_hash = images_hash(sp["images"])

        cur = have.get(slug)
        if cur is None:
            need_write(f"create exercise {slug}")
            ex = api.write("POST", "exercise/", f"{slug} (category {sp['category']})",
                           {"category": cat_id, "equipment": equip, "muscles": muscles,
                            "muscles_secondary": secondary, "license_author": LICENSE_AUTHOR})
            try:
                img, imgids = upload_images(api, sp, img_hash, ex["id"], "none", [], need_write, warn)
                text = with_marker(sp["text"], slug, img, imgids)
                for name in (sp["name"], alt_name(sp["name"])):
                    try:
                        api.write("POST", "exercise-translation/", f"{slug} {name!r}",
                                  {"exercise": ex["id"], "language": ref["en"], "name": name,
                                   "description_source": text, "license_author": LICENSE_AUTHOR})
                        break
                    except WgerError as e:
                        if "too similar" not in str(e) or name != sp["name"]:
                            raise
                        warn(f"exercise {slug}: name {name!r} too similar to a wger exercise; "
                             f"using {alt_name(name)!r}")
            except WgerError as e:
                # no translation = no marker: the half-made exercise must go. The API's exercise
                # queryset only lists translated exercises, so DELETE 404s on it (wger 2.7);
                # print the manage.py cleanup instead of masking the real error.
                try:
                    api.write("DELETE", f"exercise/{ex['id']}/", f"{slug} (translation failed)")
                except WgerError:
                    warn(f"exercise {slug}: wger exercise {ex['id']} has no translation and the API "
                         f"cannot delete it; on kdk-dkr-01 run: printf '%s\\n' "
                         f"\"from wger.exercises.models import Exercise\" "
                         f"\"print(Exercise.objects.filter(license_author='{LICENSE_AUTHOR}', "
                         f"translations__isnull=True).delete())\" "
                         f"| sudo docker exec -i wger-web python3 manage.py shell")
                raise e
            ids[slug] = ex["id"]
            continue

        tr = cur["translation"]
        ex_id = ids[slug] = tr["exercise"]
        ex = exercises.get(ex_id)
        want = {"category": cat_id, "equipment": equip, "muscles": muscles, "muscles_secondary": secondary}
        if ex is not None:
            patch = {k: v for k, v in want.items()
                     if (sorted(ex.get(k) or []) if isinstance(v, list) else ex.get(k)) != v}
            if patch:
                need_write(f"patch exercise {slug}")
                api.write("PATCH", f"exercise/{ex_id}/", f"{slug} {'/'.join(sorted(patch))}", patch)
        if tr.get("name") not in (sp["name"], alt_name(sp["name"])):
            need_write(f"rename exercise {slug}")
            api.write("PATCH", f"exercise-translation/{tr['id']}/", f"{slug} name -> {sp['name']!r}",
                      {"name": sp["name"]})
        m = cur["marker"]
        img, imgids = upload_images(api, sp, img_hash, ex_id, m["img"], marker_ids(m), need_write, warn)
        want_src = with_marker(sp["text"], slug, img, imgids)
        if (m["v"] != h12(sp["text"]) or m["img"] != img or marker_ids(m) != list(imgids)
                or (tr.get("description_source") or "") != want_src):
            need_write(f"update exercise text {slug}")
            api.write("PATCH", f"exercise-translation/{tr['id']}/", f"{slug} description",
                      {"description_source": want_src})
    for e, slugs in sorted(missing_equipment.items()):
        warn(f"wger has no equipment {e!r} (wanted by {', '.join(slugs)}); left out until the runbook's "
             "equipment admin step (12) adds it")
    for n, slugs in sorted(missing_muscles.items()):
        warn(f"wger has no muscle {n!r} (wanted by {', '.join(slugs)}); left out until the runbook's "
             "custom-muscle admin step (13) adds it")
    return ids


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

    def history_days(self, rid) -> list[dict]:
        """The routine's days that hold logs or sessions, first used first."""
        first: dict = {}
        for x in self.sessions:
            first.setdefault(x.get("day"), []).append(str(x.get("datetime_start") or ""))
        for x in self.logs:
            first.setdefault(self.day_of_entry.get(x.get("slot_entry")), []).append(str(x.get("date") or ""))
        days = [d for d in self.days if d["routine"] == rid and d["id"] in first]
        return sorted(days, key=lambda d: (min(first[d["id"]]), str(d["id"])))

    def has_history(self, rid) -> bool:
        return any(x.get("routine") == rid for x in self.logs + self.sessions) or bool(self.used_days(rid))

    def exercises_on(self, rid, day_name: str) -> set:
        days = {d["id"] for d in self.days if d["routine"] == rid and d.get("name") == day_name}
        slots = {s["id"] for s in self.slots if s["day"] in days}
        return {e["exercise"] for e in self.entries if e["slot"] in slots}


def assign(rs: list[dict], week: list[dict], st: State, ex_ids: dict, warn, who: str) -> dict:
    """weekday tag -> existing routine: by tag, then generated routines without a weekday tag
    (block routines, "Week (kdk)"): those with history go to the weekday their logs and sessions
    are on, the others to the free weekdays whose exercises they already hold most of."""
    got: dict = {}
    for r in rs:
        t = routine_tag(r)
        if t in DAY_TAGS:
            if t in got:
                warn(f"{who}: two routines tagged [kdk:{t}]; using id {got[t]['id']}")
            else:
                got[t] = r
    taken = {r["id"] for r in got.values()}
    spare = [r for r in rs if r["id"] not in taken and (routine_tag(r) or r.get("name") == LEGACY_NAME)]
    free_pool = []
    for r in sorted(spare, key=lambda r: str(r["id"])):
        tags = [day_tag(d) for d in st.history_days(r["id"])]
        wd = next((t for t in tags if t is not None and t not in got), None)
        if wd is not None:
            got[wd] = r                 # the weekday of its first logs (jmack routine 1: Friday)
        else:
            free_pool.append(r)
    held = {day_tag(d) for r in got.values() for d in st.history_days(r["id"])}
    free = [t for t in DAY_TAGS if t not in got and t not in held]
    want = {b["key"]: {ex_ids[e["slug"]] for s in b["days"][0]["slots"] for e in s["entries"]} for b in week}
    overlap = {(r["id"], t): len(want[t] & st.exercises_on(r["id"], WEEKDAYS[DAY_TAGS.index(t)]))
               for r in free_pool for t in free}
    pool = free_pool[:len(free)]
    best, best_score = (), -1
    for perm in itertools.permutations(free, len(pool)):
        score = sum(overlap[(r["id"], t)] for r, t in zip(pool, perm))
        if score > best_score:
            best, best_score = perm, score
    for r, t in zip(pool, best):
        got[t] = r
    moves: dict = {}
    for t, r in got.items():
        for d in st.history_days(r["id"]):
            w = day_tag(d)
            if w is None or w == t:
                continue
            if w in moves or (w in got and any(day_tag(x) == w for x in st.history_days(got[w]["id"]))):
                warn(f"{who}: day {d.get('name')!r} (id {d['id']}) of routine {r.get('name')!r} has logs or "
                     f"sessions but {w} already has some; left where it is")
                continue
            moves[w] = d
    return got, moves


def day_tag(d: dict) -> str | None:
    return DAY_TAGS[WEEKDAYS.index(d["name"])] if d.get("name") in WEEKDAYS else None


def sync_routines(api: Api, week: list[dict], ex_ids: dict, units: dict, today: dt.date, warn):
    """All seven of one person's weekday routines (see the module docstring)."""
    rs = [r for r in api.list("routine/") if not r.get("is_template")]
    st = State(api)
    got, moves = assign(rs, week, st, ex_ids, warn, api.who)
    end = base_monday(today) + dt.timedelta(days=END_DAYS)
    for t in DAY_TAGS:                  # one window for all: the first still-valid one
        r = got.get(t)
        if r is not None and end_ok(dt.date.fromisoformat(r["end"]), today):
            end = dt.date.fromisoformat(r["end"])
            break
    base = end - dt.timedelta(days=END_DAYS)
    order = sorted(range(len(week)), key=lambda i: week[i]["key"] not in moves)   # moved days first
    for i in order:
        b = week[i]
        sync_routine(api, b, got.get(b["key"]), base - dt.timedelta(days=i), end, st, ex_ids, units, warn,
                     adopt=moves.get(b["key"]))
    sync_templates(api, week, st, base, end, ex_ids, units, warn)
    used = {r["id"] for r in got.values()}
    for r in rs:
        if r["id"] in used or not (routine_tag(r) or r.get("name") == LEGACY_NAME):
            continue
        if st.has_history(r["id"]):
            warn(f"{api.who}: routine {r.get('name')!r} (id {r['id']}) is not in the plan but has logs or "
                 "sessions; left alone (deleting a routine deletes them)")
        else:
            api.write("DELETE", f"routine/{r['id']}/", f"{r.get('name')!r} (not in the plan, no logs, no sessions)")


def sync_templates(api: Api, week: list[dict], st: State, base: dt.date, end: dt.date, ex_ids: dict,
                   units: dict, warn):
    """One public template per weekday routine, owned by this person (the web UI's Templates view;
    anyone can copy a public one). Found by [kdk:tpl:<day>] among the person's own templates; a
    copy is an ordinary routine of whoever copied it and is never touched (wger keeps no link)."""
    have: dict = {}
    extra = []
    for r in api.list("templates/"):
        m = TPL_RE.search(r.get("description") or "")
        if not m or not r.get("is_template"):
            continue
        if m.group(1) in DAY_TAGS and m.group(1) not in have:
            have[m.group(1)] = r
        else:
            extra.append(r)
    for i, b in enumerate(week):
        wd = b["days"][0]["name"]
        tb = {**b, "key": f"tpl:{b['key']}", "name": b["template_name"], "template": True,
              "description": f"{wd}, as a template: copy it to get this day as your own routine. "
                             f"{ROUTINE_DESC} [kdk:tpl:{b['key']}]"}
        sync_routine(api, tb, have.get(b["key"]), base - dt.timedelta(days=i), end, st, ex_ids, units, warn)
    for r in extra:
        if st.has_history(r["id"]):
            warn(f"{api.who}: template {r.get('name')!r} (id {r['id']}) is a duplicate but has logs or sessions; "
                 "left alone")
        else:
            api.write("DELETE", f"routine/{r['id']}/", f"template {r.get('name')!r} (duplicate, no logs, no sessions)")


def sync_routine(api: Api, block: dict, r: dict | None, start: dt.date, end: dt.date, st: State,
                 ex_ids: dict, units: dict, warn, adopt: dict | None = None):
    name = block["name"]
    want = {"name": name, "description": block["description"], "fit_in_week": False,
            "start": start.isoformat(), "end": end.isoformat()}
    if block.get("template"):
        want.update(is_template=True, is_public=True)
    if r is None:
        r = api.write("POST", "routine/", f"{name!r} {want['start']}..{want['end']}", want)
    else:
        patch = {k: v for k, v in want.items() if r.get(k) != v}
        if patch:
            api.write("PATCH", f"routine/{r['id']}/", f"{name!r} {sorted(patch)}", patch)
    rid = r["id"]
    if adopt is not None:
        # a day with logs or sessions that sits in another routine (e.g. Saturday in the old Daily
        # block): move it here with its slots, and point its logs and sessions at this routine
        api.write("PATCH", f"day/{adopt['id']}/", f"{name} takes over day {adopt.get('name')!r} (id {adopt['id']}) "
                  "with its logs", {"routine": rid})
        for x in st.sessions:
            if x.get("day") == adopt["id"] and x.get("routine") != rid:
                api.write("PATCH", f"workoutsession/{x['id']}/", f"{name} session of the moved day", {"routine": rid})
                x["routine"] = rid
        for x in st.logs:
            if st.day_of_entry.get(x.get("slot_entry")) == adopt["id"] and x.get("routine") != rid:
                api.write("PATCH", f"workoutlog/{x['id']}/", f"{name} log of the moved day", {"routine": rid})
                x["routine"] = rid
        adopt["routine"] = rid
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
        exs = tuple(ex_ids[e["slug"]] for e in ps["entries"])
        s = next((x for x in existing if x["id"] not in used
                  and tuple(e["exercise"] for e in entries_of(x["id"])) == exs), None)
        if s is None:
            s = api.write("POST", "slot/", f"{pd['name']} #{order} {[e['slug'] for e in ps['entries']]}",
                          {"day": day_id, "order": order, "comment": ps["comment"]})
            cur_entries = [None] * len(ps["entries"])
        else:
            used.add(s["id"])
            patch = {k: v for k, v in (("order", order), ("comment", ps["comment"])) if s.get(k) != v}
            if patch:
                api.write("PATCH", f"slot/{s['id']}/", f"{pd['name']} #{order} {sorted(patch)}", patch)
            cur_entries = entries_of(s["id"])
        for eorder, (pe, ce) in enumerate(zip(ps["entries"], cur_entries, strict=True), 1):
            ewant = {"exercise": ex_ids[pe["slug"]], "order": eorder, "comment": pe["comment"],
                     "repetition_unit": units[pe["unit"]], "weight_unit": units["lb"]}
            label = f"{pd['name']} {pe['slug']}"
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
                    api.write("POST", f"{path}/", f"{label} {k}={v}",
                              {"slot_entry": ce["id"], "iteration": 1, "value": v})
                elif num(first.get("value")) != float(v):
                    api.write("PATCH", f"{path}/{first['id']}/", f"{label} {k}={v}", {"value": v})
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


# ---------------------------------------------------------------- main
def env_key(login: str) -> str:
    return "WGER_API_KEY_" + re.sub(r"[^A-Z0-9]", "_", login.upper())


def main(argv: list[str] | None = None, today: dt.date | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="write to wger (default: dry run)")
    mode.add_argument("--check", action="store_true", help="exit 1 if wger differs from the catalog")
    ap.add_argument("--posters", type=Path,
                    help="guide output dir (workout.json + exercise/ex-<slug>-step<N>.png or -rom.png)")
    ap.add_argument("--person", action="append", help="login (or key) to sync; repeatable; default all")
    args = ap.parse_args(argv)
    today = today or dt.date.today()
    warnings: list[str] = []

    def warn(msg):
        warnings.append(msg)
        print(f"WARN {msg}", file=sys.stderr)

    try:
        base = os.environ.get("WGER_URL")
        if not base:
            raise WgerError("WGER_URL is not set (e.g. https://wger.kmkdp.com)")
        cat = workout_catalog.load()
        people = cat["people"]
        sel = people
        if args.person:
            sel = [p for p in people if p["login"] in args.person or p["key"] in args.person]
            unknown = set(args.person) - {p["login"] for p in sel} - {p["key"] for p in sel}
            if unknown:
                raise WgerError(f"unknown --person {sorted(unknown)}; known: {[p['login'] for p in people]}")
        keys = {}
        for p in sel:
            k = os.environ.get(env_key(p["login"]))
            if not k:
                raise WgerError(f"{env_key(p['login'])} is not set (op read it into the env; never argv)")
            keys[p["login"]] = k
        if args.posters is None:
            raise WgerError("--posters DIR is required (the guide output dir with workout.json and exercise/)")
        sources = plan_sources(cat, lambda: load_guide(args.posters))
        specs = all_specs(sources, args.posters, warn)
        blocks = {p["login"]: plan_week(sources[p["login"]][1], sources[p["login"]][0], warn) for p in sel}

        actions: list[str] = []
        if args.check:
            actions += [f"{SIDECAR.name}: no entry for {s}" for s, sp in specs.items() if sp["side"].get("_missing")]
        apply = args.apply
        owner = people[0]
        owner_sel = owner in sel
        reader = Api(base, keys[sel[0]["login"]], sel[0]["login"], apply, actions)
        writer = Api(base, keys[owner["login"]], owner["login"], apply, actions) if owner_sel else reader
        if owner_sel and apply:
            perm = writer.get("check-permission/", permission="exercises.add_exercise")
            if not perm.get("result"):
                raise WgerError(f"{owner['login']} lacks exercises.add_exercise; make them a wger "
                                "superuser first (docs/runbooks/wger.md step 7)")
        ref = reference(reader)
        ex_ids = sync_exercises(writer, specs, ref, owner_sel, warn)

        needed = {e["unit"] for bs in blocks.values() for b in bs for d in b["days"] for s in d["slots"]
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

        for p in sel:
            api = writer if p is owner else Api(base, keys[p["login"]], p["login"], apply, actions)
            sync_routines(api, blocks[p["login"]], ex_ids, units, today, warn)
    except WgerError as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 2

    verb = "DRIFT" if args.check else ("did" if apply else "would")
    for a in actions:
        print(f"{verb}: {a}")
    n = len(actions)
    if args.check:
        print(f"{n} difference(s) between wger and the catalog" if n else "wger matches the catalog")
        return 1 if n else 0
    print(f"{n} write(s) {'made' if apply else 'planned (dry run; add --apply to write)'}; "
          f"{len(warnings)} warning(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
