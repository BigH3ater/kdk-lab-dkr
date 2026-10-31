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
