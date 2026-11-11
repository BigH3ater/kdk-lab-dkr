"""Optional-ingredient review for the planner's recipes (owner 2026-11-11).

  python -m grocery.cleanup --optional-review           # dry run: list, write nothing
  python -m grocery.cleanup --optional-review --apply   # OWNER / main session only

Lists, for every planner recipe (the season books + recipes/fixed.yaml, plus any
recipe on a Tandoor meal plan, e.g. recipe 7 "Seared Tuna Steaks" which is in no
book), the ingredient lines that are not part of the dish as cooked:

  remove   the line says optional / for serving / garnish / if desired, or its
           food is on config `never_buy`. --apply deletes these ingredient lines
           from the recipe in Tandoor, after saving the whole recipe as JSON
           (<backup dir>/<recipe id>.json; restore by hand from it).
  to-taste "to taste" lines (salt, pepper, soy sauce): pantry staples; left in
           place, only marked here. `pantry: no` = the food is not on the
           cleanup.yaml pantry list, so Tandoor would order it.
  review   ambiguous, never touched: part of the amount is used in the cooking
           ("plus more for garnish"), an alternative ("or mirin (optional)"), a
           serving-rice food the rice side detection reads, "serving suggestion",
           a line that is both optional and to taste, or a never_buy food that IS
           the dish (Roasted Cabbage Steaks: retire the recipe instead).

Pure classification (unit tested offline); only `apply` writes. No secrets.
"""
from __future__ import annotations

import datetime
import json
import os
import re

from . import shoplist as S

_OPTIONAL = re.compile(r"\boptional\b|\bfor serving\b|\bgarnish|\bif desired\b")
_TO_TASTE = re.compile(r"\bto taste\b")
_SUGGEST = re.compile(r"\bserving suggestion\b")
# Words that make an optional line ambiguous: some of it is cooked, or it is
# one of two choices.
_PARTIAL = re.compile(r"\b(plus|extra|more|additional|or)\b|and/or")


def _text(ing):
    """original_text, note and food name of a line, lower-cased."""
    food = (ing.get("food") or {}).get("name") or ""
    return " ".join(str(x) for x in (ing.get("original_text") or "", ing.get("note") or "",
                                     food)).lower()


def classify(ing, recipe_name, never_buy, rice_foods=(), pantry=()):
    """(action, reason) for one ingredient line, or None when it is a plain line.
    action: remove | to-taste | review."""
    if ing.get("is_header"):
        return None
    food = ((ing.get("food") or {}).get("name") or "").strip()
    if not food:
        return None
    text = _text(ing)
    if S.is_never_buy(food, never_buy):
        if S.is_never_buy(recipe_name, never_buy):
            return "review", "never_buy food is the dish itself: retire the recipe instead"
        return "remove", "never_buy"
    opt = _OPTIONAL.search(text)
    taste = _TO_TASTE.search(text)
    if _SUGGEST.search(text):
        return "review", "serving suggestion"
    if opt and taste:
        return "review", "optional and to taste"
    if taste:
        low = food.lower()
        return "to-taste", "pantry: " + ("yes" if low in pantry else "no")
    if not opt:
        return None
    if food.lower() in rice_foods:
        return "review", "serving rice: the rice-side detection reads it"
    m = _PARTIAL.search(text)
    if m:
        return "review", f"ambiguous ('{m.group(0)}'): part is cooked, or a choice"
    return "remove", opt.group(0).strip()


def review(recipes, cfg, pantry=()):
    """Rows for every classified line of `recipes` (Tandoor recipe details):
    {recipe_id, recipe, ingredient_id, food, text, action, reason}."""
    never = cfg.get("never_buy") or []
    rice = {str(f).lower() for f in (cfg.get("rice") or {}).get("serving_foods") or []}
    pantry = {str(p).lower() for p in pantry}
    rows = []
    for r in sorted(recipes, key=lambda r: r["id"]):
        for step in r.get("steps") or []:
            for ing in step.get("ingredients") or []:
                c = classify(ing, r.get("name") or "", never, rice, pantry)
                if not c:
                    continue
                rows.append({"recipe_id": r["id"], "recipe": r.get("name") or "",
                             "ingredient_id": ing.get("id"),
                             "food": (ing.get("food") or {}).get("name") or "",
                             "text": (ing.get("original_text") or "").strip()
                             or " ".join(x for x in ((ing.get("food") or {}).get("name"),
                                                     ing.get("note")) if x),
                             "action": c[0], "reason": c[1]})
    return rows


def render(rows):
    """Plain-text report, grouped by action (remove, review, to-taste)."""
    out = []
    for action in ("remove", "review", "to-taste"):
        part = [r for r in rows if r["action"] == action]
        out.append(f"== {action}: {len(part)}")
        for r in part:
            out.append(f"  {r['recipe_id']:>4} {r['recipe'][:40]:<40} | {r['text'][:70]}"
                       f"  [{r['reason']}]")
    return "\n".join(out)


def apply(t, rows, recipes_by_id, backup_dir):
    """Delete the `remove` lines from Tandoor, each recipe saved to
    backup_dir/<id>.json first. Returns the deleted ingredient ids."""
    os.makedirs(backup_dir, exist_ok=True)
    done, saved = [], set()
    for r in rows:
        if r["action"] != "remove" or r["ingredient_id"] is None:
            continue
        rid = r["recipe_id"]
        if rid not in saved:
            with open(os.path.join(backup_dir, f"{rid}.json"), "w") as fh:
                json.dump(recipes_by_id[rid], fh, indent=1)
            saved.add(rid)
        t.delete(f"/api/ingredient/{r['ingredient_id']}/")
        done.append(r["ingredient_id"])
    return done


def default_backup_dir(now=None):
    now = now or datetime.datetime.now()
    base = os.environ.get("GROCERY_BACKUP_DIR") or os.getcwd()
    return os.path.join(base, f"optional-review-backup-{now:%Y%m%d-%H%M%S}")
