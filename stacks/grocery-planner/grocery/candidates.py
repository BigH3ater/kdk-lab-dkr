"""Load candidate recipe YAML, score each candidate, and pick the default.

Candidate files live in recipes/candidates/<season>.yaml. Each slot lists 2-3
real published recipes with the ratings read from the page's schema.org JSON-LD.
This module scores them (scoring.py) and marks a default selection (top-scoring
"good" candidate). The owner overrides by setting `selected: true` on a
candidate; the seed only imports selected (or defaulted) candidates.
"""
from __future__ import annotations

import glob
import os

import yaml

from .scoring import evaluate_candidate


# slot id prefix -> slot_kind for the scorer / generator
def slot_kind(slot_id: str) -> str:
    s = slot_id
    if "fish" in s:
        return "fish"
    if "chicken" in s:
        return "chicken"
    if "beef" in s:
        return "ground_beef"
    if "steak" in s:
        return "steak"
    if "side" in s:
        return "side"
    if "lunch" in s:
        return "lunch"
    if "snack" in s:
        return "snack"
    return "staple"


def load_candidate_files(candidates_dir: str) -> dict:
    """Return {season: parsed_yaml}. Missing dir -> {}."""
    out = {}
    for path in sorted(glob.glob(os.path.join(candidates_dir, "*.yaml"))):
        season = os.path.splitext(os.path.basename(path))[0]
        with open(path) as f:
            out[season] = yaml.safe_load(f) or {}
    return out


def score_slot(slot: dict, config: dict) -> dict:
    """Score every candidate in a slot, pick the default. Returns an enriched
    copy with per-candidate score/verdict/flags and a chosen candidate (or None).
    `selected: true` on a candidate wins; otherwise the top-scoring "good" one."""
    kind = slot_kind(slot["slot"])
    scored = []
    for cand in slot.get("candidates") or []:
        ev = evaluate_candidate(cand, kind, config)
        c = dict(cand)
        c["score"] = None if ev.score is None else round(ev.score, 4)
        c["verdict"] = ev.verdict
        c["flags"] = ev.flags
        c["reject_reasons"] = ev.reject_reasons
        scored.append(c)

    owner = [c for c in scored if c.get("selected")]
    chosen = None
    if owner:
        chosen = owner[0]
    else:
        good = [c for c in scored if c["verdict"] == "good"]
        pool = good or [c for c in scored if c["verdict"] == "acceptable"]
        if pool:
            chosen = max(pool, key=lambda c: (c["score"] or 0.0))

    return {
        "slot": slot["slot"],
        "slot_label": slot.get("slot_label", slot["slot"]),
        "kind": kind,
        "candidates": scored,
        "chosen": chosen,          # the candidate dict, or None (no good/acceptable)
    }


def score_all(candidate_files: dict, config: dict) -> dict:
    """{season: [scored_slot, ...]} for every slot in every candidate file."""
    out = {}
    for season, data in candidate_files.items():
        out[season] = [score_slot(s, config) for s in (data.get("slots") or [])]
    return out


def _variant(slot_id, kind):
    if kind == "fish":
        return "salmon" if slot_id.endswith("-a") else "tuna"
    if kind == "ground_beef":
        return "greek" if slot_id.endswith("-a") else "asian"
    return None


def pool_from_selected(scored_slots, season, start_id=1):
    """Build a generator pool from the CHOSEN candidate per slot for a season.
    working_time is None (unknown until import -> the generator's active-time cap
    does not apply pre-import; it applies to Tandoor's real field at plan time).
    ingredient_count is None because the scorer already enforced the ingredient
    cap on every chosen candidate."""
    pool = []
    i = start_id
    for slot in scored_slots:
        if not slot["chosen"]:
            continue
        pool.append({"id": i, "name": f"{slot['slot']}:{slot['chosen']['title'][:40]}",
                     "kind": slot["kind"], "season": season,
                     "variant": _variant(slot["slot"], slot["kind"]),
                     "working_time": None, "ingredient_count": None, "servings": 2})
        i += 1
    return pool


# Minimum distinct recipes the generator needs per season to build any week at
# either parity (fish is 1/week but both variants are used across the A/B weeks).
SEASON_MINIMUMS = {"salmon": 1, "tuna": 1, "chicken": 4, "greek": 1, "asian": 1,
                   "steak": 1, "side": 7, "lunch": 2}


def season_coverage(scored_slots):
    """Count selectable recipes per requirement for one season."""
    got = {k: 0 for k in SEASON_MINIMUMS}
    for slot in scored_slots:
        if not slot["chosen"]:
            continue
        sid, k = slot["slot"], slot["kind"]
        if k == "fish":
            got["salmon" if sid.endswith("-a") else "tuna"] += 1
        elif k == "ground_beef":
            got["greek" if sid.endswith("-a") else "asian"] += 1
        elif k in got:
            got[k] += 1
    return got


def coverage_shortfalls(scored_by_season):
    """{season: ["chicken 2/4", ...]} for seasons that can't build a week."""
    out = {}
    for season, slots in scored_by_season.items():
        got = season_coverage(slots)
        short = [f"{k} {got[k]}/{SEASON_MINIMUMS[k]}"
                 for k in SEASON_MINIMUMS if got[k] < SEASON_MINIMUMS[k]]
        if short:
            out[season] = short
    return out


def summarize(scored_by_season: dict) -> dict:
    """Counts for the report: per season + overall good/acceptable/rejected and
    slots with no good candidate."""
    overall = {"slots": 0, "candidates": 0, "good": 0, "acceptable": 0,
               "rejected": 0, "slots_no_good": 0, "slots_empty": 0}
    per_season = {}
    for season, slots in scored_by_season.items():
        s = {"slots": 0, "candidates": 0, "good": 0, "acceptable": 0,
             "rejected": 0, "slots_no_good": 0, "slots_empty": 0}
        for slot in slots:
            s["slots"] += 1
            cands = slot["candidates"]
            s["candidates"] += len(cands)
            for c in cands:
                s[c["verdict"]] += 1
            if not cands:
                s["slots_empty"] += 1
            if not any(c["verdict"] == "good" for c in cands):
                s["slots_no_good"] += 1
        per_season[season] = s
        for k in overall:
            overall[k] += s[k]
    return {"overall": overall, "per_season": per_season}
