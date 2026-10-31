"""Recipe-candidate scoring and verdicts.

We do NOT write recipes. The seed imports real published recipes by URL (Tandoor
recipe-from-source). Each candidate carries the page's schema.org ratings; this
module turns those into a Bayesian score and a good/acceptable/rejected verdict,
all driven by config.yaml. Pure + stdlib, so it is unit tested offline.

Bayesian average (so a 5.0 with 3 reviews does not beat a 4.7 with 3000):

    score = (C*m + n*r) / (C + n)

r = rating, n = (site-scaled) count, m = prior mean, C = prior weight.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Evaluation:
    score: float | None          # Bayesian score; None if unrated
    verdict: str                 # "good" | "acceptable" | "rejected"
    flags: list[str] = field(default_factory=list)       # non-fatal (e.g. swap to olive oil)
    reject_reasons: list[str] = field(default_factory=list)


def bayesian_score(rating, count, prior_m, prior_weight_C):
    """(C*m + n*r)/(C + n). Returns None when there is no rating to blend."""
    if rating is None or count is None:
        return None
    n = max(0.0, float(count))
    r = float(rating)
    C = float(prior_weight_C)
    m = float(prior_m)
    return (C * m + n * r) / (C + n)


def _non_pantry_ingredient_count(ingredients, pantry_staples):
    """Count ingredient lines that are not a pantry staple. Approximate: a line
    is a staple if any staple term is a word-ish substring of it. Documented as
    approximate in the runbook; the owner can correct a slot by hand."""
    if not ingredients:
        return None
    staples = [s.lower() for s in pantry_staples]
    n = 0
    for line in ingredients:
        text = str(line).lower()
        if not any(s in text for s in staples):
            n += 1
    return n


def _total_time_cap(slot_kind, caps):
    """Hard cap on a candidate's PUBLISHED total time (includes passive time).
    Lunch preps may run to lunch_total_time_max; everything else to
    candidate_total_time_max (falls back to working_time_max if unset)."""
    if slot_kind == "lunch":
        return caps.get("lunch_total_time_max", caps.get("candidate_total_time_max",
                                                          caps["working_time_max"]))
    return caps.get("candidate_total_time_max", caps["working_time_max"])


def evaluate_candidate(cand: dict, slot_kind: str, config: dict) -> Evaluation:
    """Apply hard filters, then rate. slot_kind one of:
    fish|chicken|ground_beef|steak|side|lunch|snack|staple.

    Hard filters (reject): no rating, beans/lentils, over time cap, over
    ingredient cap, chicken slot not using breast (unless the page flags breast
    works). Non-olive fat and an unparseable/odd yield are FLAGS, not rejects.
    """
    caps = config["caps"]
    sc = config["scoring"]
    flags: list[str] = []
    rejects: list[str] = []

    # --- flags (never fatal) ---
    fats = cand.get("non_olive_fats") or []
    if fats:
        flags.append("swap to olive oil (" + ", ".join(fats) + ")")

    # --- hard filters ---
    if cand.get("has_beans_lentils"):
        rejects.append("contains beans/lentils")

    if slot_kind == "chicken":
        ucb = cand.get("uses_chicken_breast")
        if ucb is False:
            if cand.get("breast_ok"):
                flags.append("recipe uses another cut; site says breast works")
            else:
                rejects.append("not chicken breast")
        elif ucb is None:
            flags.append("verify cut is chicken breast")

    tmin = cand.get("total_time_min")
    hard_cap = _total_time_cap(slot_kind, caps)
    active_cap = caps["working_time_max"]
    if tmin is not None:
        if tmin > hard_cap:
            rejects.append(f"over total-time cap ({tmin} > {hard_cap} min)")
        elif tmin > active_cap:
            flags.append(f"published total {tmin} min over {active_cap}-min active cap "
                         f"(may include passive time; verify active time)")

    nipc = _non_pantry_ingredient_count(cand.get("ingredients"), caps["pantry_staples"])
    if nipc is not None and nipc > caps["ingredient_max"]:
        rejects.append(f"too many ingredients ({nipc} > {caps['ingredient_max']})")

    # --- scoring ---
    rating = cand.get("rating")
    count = cand.get("count")
    weight = (sc.get("site_count_weight") or {}).get(cand.get("site"), 1.0) or 1.0
    scaled_count = None if count is None else count / weight
    score = bayesian_score(rating, scaled_count, sc["prior_m"], sc["prior_weight_C"])

    if rating is None or count is None:
        rejects.append("no rating found")

    # --- verdict ---
    if rejects:
        verdict = "rejected"
    elif rating >= sc["good"]["min_rating"] and count >= sc["good"]["min_count"]:
        verdict = "good"
    elif rating >= sc["acceptable"]["min_rating"] and count >= sc["acceptable"]["min_count"]:
        verdict = "acceptable"
    else:
        verdict = "rejected"
        rejects.append(
            f"below acceptable ({rating} / {count} reviews)")

    return Evaluation(score=score, verdict=verdict, flags=flags, reject_reasons=rejects)
