"""Ingredient clean-up for the planner's recipes (config: recipes/cleanup.yaml).

  python -m grocery.cleanup                  # dry run: print every change, write nothing
  python -m grocery.cleanup --apply          # OWNER: write the changes to Tandoor
  python -m grocery.cleanup --apply --refresh-list
                                             # ...then rebuild the shopping-list entries
                                             # of the planned meals from the clean recipes

What --apply does, in order:
  1. Automations: UNIT_ALIAS / FOOD_ALIAS / NEVER_UNIT, so new imports arrive
     clean (created once, matched by name "grocery: ...").
  2. Ingredients of the season-book + fixed recipes: unread lines ("▢ 1 pound
     carrots", "2 garlic cloves" read as unit garlic) are re-read; every food
     and unit is repointed to its canonical name. original_text is kept.
  3. Foods: pantry staples -> food_onhand; not-food lines -> ignore_shopping;
     every canonical food gets its store/aisle from foods.yaml.
  4. --refresh-list: Tandoor never updates an existing shopping entry's food or
     unit, so each planned meal's entries are deleted and re-added from the
     clean recipe (on-hand and ignored foods left out).

Tandoor's food merge is NOT used: it can't be undone. Old spellings stay as
unused foods. No secrets are printed.
"""
from __future__ import annotations

import argparse
import datetime
import logging
import os
import re
import sys
from fractions import Fraction

import yaml

from . import seed as S

log = logging.getLogger("grocery.cleanup")

_FRAC = {"½": "1/2", "⅓": "1/3", "⅔": "2/3", "¼": "1/4", "¾": "3/4", "⅛": "1/8",
         "⅜": "3/8", "⅝": "5/8", "⅞": "7/8", "⅕": "1/5"}
_BOX = "▢□☐"
# Preparation words that don't change what is bought ("minced celery" = celery).
_DESCRIPTORS = re.compile(r"^((fresh|freshly|chopped|minced|diced|finely|thinly|sliced|halved|"
                          r"crumbled|packed|peeled|cubed|trimmed|pitted|large|small|"
                          r"medium)\b[\s,]*)+", re.I)
# Size/measure words left in front of a food ("6-oz salmon fillets", "1-inch piece
# fresh ginger", "tightly packed cups shredded cheese", "recipe tzatziki sauce").
_LEAD_JUNK = re.compile(r"^((\d+(\.\d+)?-?(inch|in|oz|ounce|cm)\b\.?|inch|piece|pieces|dry|"
                        r"tightly|loosely|packed|cups?|pint|recipe|batch|of|pitted|bone-in|"
                        r"boneless|skinless)\b[\s,]*)+", re.I)
# Units read straight from text even when not in cleanup.yaml (sizes, packages).
_EXTRA_UNITS = {"small", "medium", "large", "whole", "bag", "bottle", "jar", "box", "pack",
                "package", "packet", "tub", "block", "bar", "piece", "pinch", "dash",
                "stalk", "stalks", "pint", "quart", "handful", "kg", "ml", "l", "batch", "envelope",
                "container", "carton", "loaf", "sheet", "fillet", "fillets", "steak", "steaks"}


def load_cleanup(stack=S.STACK):
    return yaml.safe_load(open(os.path.join(stack, "recipes", "cleanup.yaml")))


class Maps:
    """Lowercase lookup tables built from cleanup.yaml."""

    def __init__(self, cc):
        self.unit = {}
        for canon, alts in (cc.get("units") or {}).items():
            self.unit[canon.lower()] = canon
            for a in alts or []:
                self.unit[str(a).lower()] = canon
        self.food = {}
        for canon, alts in (cc.get("foods") or {}).items():
            self.food[canon.lower()] = canon
            for a in alts or []:
                self.food[str(a).lower()] = canon
        self.never_unit = {str(w).lower() for w in cc.get("never_unit") or []}
        self.pantry = {str(w).lower() for w in cc.get("pantry") or []}
        self.not_food = {str(w).lower() for w in cc.get("not_food") or []}
        self.known_units = set(self.unit) | _EXTRA_UNITS

    def canon_unit(self, name):
        if not name:
            return None
        return self.unit.get(name.lower(), name)

    def canon_food(self, name):
        n = tidy_food(name)
        if n.lower() in self.food:
            return self.food[n.lower()]
        bare = _DESCRIPTORS.sub("", _LEAD_JUNK.sub("", n)).strip() or n
        return self.food.get(bare.lower(), bare)


def tidy_food(name):
    """'cloves (, finely minced' -> 'cloves'; 'olive oil,' -> 'olive oil'."""
    n = (name or "").strip().lstrip(_BOX).strip()
    n = re.split(r"\(|\s\.\s|\.\s", n)[0]
    n = re.sub(r"^(optional:\s*)", "", n, flags=re.I)
    return re.sub(r"\s+", " ", n).strip(" ,*").strip()


def _num(tok):
    try:
        return float(sum(Fraction(p) for p in tok.split("+")))
    except (ValueError, ZeroDivisionError):
        return None


def parse_line(text, maps):
    """Own small ingredient reader (Tandoor's mis-reads the cases we need to fix).
    Returns {amount, unit, food, note}."""
    t = (text or "").strip().lstrip(_BOX).strip()
    for k, v in _FRAC.items():
        t = re.sub(rf"(\d)\s*{k}", rf"\1+{v}", t)       # 1½ -> 1+1/2
        t = t.replace(k, v)
    t = t.replace("(,", "(").replace("( ,", "(")
    # parentheticals (nested-aware) -> note
    notes, depth, out, buf = [], 0, [], []
    for ch in t:
        if ch == "(":
            if depth == 0:
                buf = []
            else:
                buf.append(ch)
            depth += 1
        elif ch == ")" and depth:
            depth -= 1
            if depth == 0:
                notes.append("".join(buf).strip(" ,"))
            else:
                buf.append(ch)
        elif depth:
            buf.append(ch)
        else:
            out.append(ch)
    if depth:                                            # unclosed "(..." -> note
        notes.append("".join(buf).strip(" ,"))
    t = re.sub(r"\s+", " ", "".join(out)).strip()
    m = re.match(r"^(optional)\s*:\s*", t, re.I)
    if m:
        notes.append("optional")
        t = t[m.end():]
    # "1.5 tbsp / 25g butter", "250g/ 8oz beef" -> drop the alternative measure
    t = re.sub(r"^([\d./+\s-]*\d\s*[a-zA-Z.]+)\s*/\s*[\d./]+\s*[a-zA-Z.]+", r"\1", t)
    t = re.sub(r"^(\d[\d./+]*)([a-zA-Z]+)\b", r"\1 \2", t)        # 250g -> 250 g
    t = re.sub(r"^(\d+)\s*x\s*[\d./\s-]+[a-zA-Z]*\s+", r"\1 ", t)    # 2 x 180 - 200g tuna -> 2 tuna
    t = re.sub(r"^(\d[\d./+]*)\s*(-|to)\s*(\d[\d./+]*)", r"\3", t)  # 4 - 5 / 1/4 to 1/2 -> upper
    t = re.sub(r"^(\d[\d./+]*)\s+(\d+/\d+)", r"\1+\2", t)          # 1 1/2 -> 1+1/2
    # "2 celery 1/2 cup minced celery", "2 teaspoons 1/4 cup soy sauce": a second
    # measure follows the first; the second is the real one.
    m = re.match(r"^\d[\d./+]*\s+\S+\s+(\d[\d./+]*\s+(\S+)\s+.*)$", t)
    if m and m.group(2).lower().rstrip(".") in maps.known_units:
        t = m.group(1)
    amount, rest = 0.0, t
    m = re.match(r"^(\d[\d./+]*)\s*(.*)$", t)
    if m and _num(m.group(1)) is not None:
        amount, rest = _num(m.group(1)), m.group(2)
    if "," in rest:
        rest, tail = rest.split(",", 1)
        if tail.strip():
            notes.append(tail.strip())
    words = rest.split()
    unit = None
    if len(words) > 1:
        w = words[0].lower()
        key = w if w in maps.known_units else w.rstrip(".")
        if key in maps.known_units and key not in maps.never_unit:
            unit = maps.canon_unit(key)
            words = words[1:]
    food = " ".join(words)
    food = re.sub(r"^of\s+", "", food, flags=re.I)
    # "2 garlic cloves" -> 2 clove garlic
    m = re.match(r"^(garlic\s+cloves?|cloves?\s+garlic|cloves?)$", food, re.I)
    if m:
        unit, food = "clove", "garlic"
    return {"amount": amount, "unit": unit, "food": maps.canon_food(food),
            "note": "; ".join(n for n in notes if n)}


_JUNK_START = re.compile(r"^([" + _BOX + r"\d½⅓⅔¼¾⅛/(]|of |lb |oz\)|teaspoon |tablespoon |"
                         r"cups? |pinch |head |piece |packed |can |box |each |optional)", re.I)


def is_broken(ing, maps):
    """Does this ingredient need a re-read (not only a rename)?"""
    food = (ing.get("food") or {}).get("name") or ""
    unit = ((ing.get("unit") or {}).get("name") or "").lower()
    low = food.lower().strip()
    if not food:
        return False
    if _JUNK_START.search(food) or "(" in food or low in {"cloves", "clove", "breasts", "chops"}:
        return True
    if unit in maps.never_unit or re.match(r"^\d+-(oz|inch)$", unit) or unit in {"g/", "cup/250ml"}:
        return True
    if unit and unit not in maps.known_units:     # "poblano", "roma", "fresh" read as a unit
        return True
    return False


def ingredient_text(ing):
    """Best source text for a re-read: original_text, else rebuilt from the fields."""
    if (ing.get("original_text") or "").strip():
        return ing["original_text"]
    food = (ing.get("food") or {}).get("name") or ""
    if food.lstrip().startswith(tuple(_BOX)):
        return food
    unit = (ing.get("unit") or {}).get("name") or ""
    amt = ing.get("amount") or 0
    amt_s = (f"{amt:g} " if amt else "")
    note = f" ({ing['note']})" if ing.get("note") else ""
    return f"{amt_s}{unit} {food}{note}".strip()


def plan_ingredient(ing, maps):
    """Return the PATCH body for one ingredient, or None when it is already clean."""
    if ing.get("is_header"):
        return None
    cur_food = (ing.get("food") or {}).get("name")
    cur_unit = (ing.get("unit") or {}).get("name")
    if not cur_food:
        return None
    if is_broken(ing, maps):
        p = parse_line(ingredient_text(ing), maps)
        if not p["food"]:
            return None
        body = {"food": {"name": p["food"]},
                "unit": {"name": p["unit"]} if p["unit"] else None,
                "amount": p["amount"] if p["amount"] else (ing.get("amount") or 0)}
        old_note = ing.get("note") or ""
        note = old_note if (not p["note"] or p["note"] in old_note) else \
            "; ".join(x for x in (old_note, p["note"]) if x)
        if note != (ing.get("note") or ""):
            body["note"] = note[:256]
    else:
        food = maps.canon_food(cur_food)
        unit = maps.canon_unit(cur_unit)
        if food == "garlic" and "clove" in cur_food.lower() and unit not in ("clove", "tsp", "Tbsp"):
            unit = "clove"
        body = {}
        if food != cur_food:
            body["food"] = {"name": food}
        if unit != cur_unit:
            body["unit"] = {"name": unit} if unit else None
    if not body:
        return None
    # Tandoor matches food names case-insensitively ("Salt and pepper" is the
    # food "salt and pepper"), so a case-only difference is no change.
    same_food = body.get("food", {"name": cur_food})["name"].lower() == cur_food.lower()
    same_unit = ("unit" not in body) or ((body["unit"] or {}).get("name") or "").lower() == (cur_unit or "").lower()
    same_amount = "amount" not in body or float(body["amount"] or 0) == float(ing.get("amount") or 0)
    if same_food and same_unit and same_amount and "note" not in body:
        return None
    return body


def automations_plan(cc):
    """[(type, name, param_1, param_2)] for the Tandoor automations."""
    out = []
    for canon, alts in (cc.get("units") or {}).items():
        for a in alts or []:
            out.append(("UNIT_ALIAS", f"grocery: unit {a} -> {canon}", str(a), canon))
    for canon, alts in (cc.get("foods") or {}).items():
        for a in alts or []:
            out.append(("FOOD_ALIAS", f"grocery: food {a} -> {canon}", str(a), canon))
    for w in cc.get("never_unit") or []:
        out.append(("NEVER_UNIT", f"grocery: never unit {w}", str(w), None))
    return out


def planner_recipe_ids(t, fixed_cfg):
    ids = {e["recipe"] for e in t.get_all("/api/recipe-book-entry/")}
    fixed = {r["name"] for r in fixed_cfg.get("recipes") or []}
    ids |= {r["id"] for r in t.get_all("/api/recipe/") if r["name"] in fixed}
    return sorted(ids)


def run(t, cc, cfg, foods_cfg, fixed_cfg, apply=False, refresh_list=False):
    maps = Maps(cc)
    changes = 0
    # 1. automations
    have = {a.get("name") for a in t.get_all("/api/automation/")}
    autos = [a for a in automations_plan(cc) if a[1] not in have]
    log.info("automations to add: %d", len(autos))
    if apply:
        for typ, name, p1, p2 in autos:
            body = {"type": typ, "name": name[:128], "param_1": p1[:128]}
            if p2:
                body["param_2"] = p2[:128]
            t.post("/api/automation/", body)
    # 2. ingredients
    touched_foods = set()
    for rid in planner_recipe_ids(t, fixed_cfg):
        recipe = t.get(f"/api/recipe/{rid}/")
        for step in recipe.get("steps") or []:
            for ing in step.get("ingredients") or []:
                body = plan_ingredient(ing, maps)
                if body is None:
                    if (ing.get("food") or {}).get("name"):
                        touched_foods.add(ing["food"]["name"])
                    continue
                changes += 1
                old = f"{ing.get('amount') or 0:g} {(ing.get('unit') or {}).get('name') or ''} {ing['food']['name']}"
                new_unit = (body.get("unit") or {}).get("name") if "unit" in body else (ing.get("unit") or {}).get("name")
                new = f"{body.get('amount', ing.get('amount') or 0):g} {new_unit or ''} {body.get('food', ing['food'])['name']}"
                log.info("  %s [%s]: %s  ->  %s", rid, recipe["name"][:30], " ".join(old.split()), " ".join(new.split()))
                if apply:
                    t.patch(f"/api/ingredient/{ing['id']}/", body)
                touched_foods.add(body.get("food", ing["food"])["name"])
    log.info("ingredients to change: %d", changes)
    # 3. foods: pantry on hand, not-food ignored, store/aisle routing
    foods = {f["name"].lower(): f for f in t.get_all("/api/food/")}
    cats = t.supermarket_categories()
    for name in sorted(touched_foods, key=str.lower):
        f = foods.get(name.lower())
        if not f and not apply:
            f = {"id": None, "name": name}               # created by the patch above
        if not f:
            continue
        low = name.lower()
        body = {}
        if low in maps.pantry and not f.get("food_onhand"):
            body["food_onhand"] = True
        if low in maps.not_food and not f.get("ignore_shopping"):
            body["ignore_shopping"] = True
        cat_name = S.route_food(name, foods_cfg, cfg["stores"])
        cur = (f.get("supermarket_category") or {}).get("name") if isinstance(f.get("supermarket_category"), dict) else None
        if cat_name != cur and not cur:
            cat = cats.get(cat_name) or (t.ensure_supermarket_category(cat_name) if apply else {"id": None})
            cats[cat_name] = cat
            body["supermarket_category"] = {"id": cat["id"], "name": cat_name}
            if cat_name.startswith("Amana/"):
                body["food_onhand"] = True
        if body:
            log.info("  food %r: %s", name, ", ".join(
                f"{k}={v['name'] if isinstance(v, dict) else v}" for k, v in body.items()))
            if apply and f.get("id"):
                t.patch(f"/api/food/{f['id']}/", body)
    if refresh_list:
        refresh_shopping(t, apply)
    if not apply:
        log.info("dry run: nothing written. Re-run with --apply.")
    return changes


def refresh_shopping(t, apply):
    """Rebuild the planned meals' shopping entries from the (clean) recipes."""
    today = datetime.date.today()
    plans = {m["id"]: m for m in t.meal_plan(today, today + datetime.timedelta(days=14))}
    slrs = [s for s in t.get_all("/api/shopping-list-recipe/") if s.get("mealplan") in plans]
    entries = t.shopping_list_entries()
    log.info("refresh: %d planned meals with shopping items", len(slrs))
    for s in slrs:
        mine = [e for e in entries if e.get("list_recipe") == s["id"]]
        if any(e.get("checked") for e in mine):
            log.info("  skip %s (%s): has ticked items", s["id"], (s.get("recipe_data") or {}).get("name"))
            continue
        recipe = t.get(f"/api/recipe/{s['recipe']}/")
        # Same scaling Tandoor applies when a meal plan adds its recipe.
        factor = float(s.get("servings") or 2) / float(recipe.get("servings") or 1)
        want = []
        for step in recipe.get("steps") or []:
            for i in step.get("ingredients") or []:
                f = i.get("food") or {}
                if i.get("is_header") or not f.get("id"):
                    continue
                full = t.get(f"/api/food/{f['id']}/")
                if full.get("food_onhand") or full.get("ignore_shopping"):
                    continue
                want.append({"ingredient_id": i["id"], "food_id": f["id"],
                             "unit_id": (i.get("unit") or {}).get("id"),
                             "amount": round(float(i.get("amount") or 0) * factor, 3)})
        log.info("  %s %s: %d -> %d items", s["id"], recipe["name"][:40], len(mine), len(want))
        if apply:
            for e in mine:
                t.delete(f"/api/shopping-list-entry/{e['id']}/")
            # Not PUT /api/recipe/{id}/shopping/: Tandoor finds a list recipe through
            # its entries, so once they are deleted that call 500s
            # (shopping_helper.get_shopping_list_recipe, 2.7.2). bulk_create_entries
            # addresses the list recipe by id.
            if want:
                t.post(f"/api/shopping-list-recipe/{s['id']}/bulk_create_entries/",
                       {"entries": want})


# ---- per-recipe shopping-list test ---------------------------------------
# Dark meat is never bought (owner: chicken breast only).
_DARK_MEAT = re.compile(r"\b(thigh|thighs|drumstick|drumsticks|leg quarters?|wings?)\b", re.I)
_JUNK_NAME = re.compile(r"[(▢□☐*]|,$|^\W|^\d+\s|^(teaspoons?|tablespoons?|cups?|pounds?|ounces?|"
                        r"tsp|tbsp|lbs?|oz)\b", re.I)


def check_line(food, unit, maps, cfg, foods_cfg):
    """Problems for one shopping line (empty list = OK)."""
    low = food.lower()
    probs = []
    if _JUNK_NAME.search(food):
        probs.append("unread name")
    if unit and unit.lower() not in maps.known_units:
        probs.append(f"unknown unit {unit!r}")
    if _DARK_MEAT.search(low) and "chicken" in low:
        probs.append("dark meat (breast only)")
    for bad in cfg.get("exclude_ingredients") or []:
        if bad.lower() in low:
            probs.append(f"excluded ingredient {bad!r}")
    if any(x.lower() in low for x in cfg.get("exclude") or []):
        probs.append("excluded from grocery lists (pets)")
    cat = S.route_food(food, foods_cfg, cfg["stores"])
    if cat.endswith(": Other"):
        probs.append("no store rule (falls to Other)")
    return probs, cat


def validate_recipes(t, cc, cfg, foods_cfg, fixed_cfg, live=False):
    """Test every planner recipe's shopping list against the rules.

    simulate (default): read-only; applies the clean-up plan in memory and
      predicts the lines Tandoor would add (on-hand / ignored / Amana left out).
    live: for each recipe, ask Tandoor to add it to the shopping list, read the
      entries it really created, check them, then delete that test list again.
    Returns the number of recipes with problems."""
    maps = Maps(cc)
    foods = {f["name"].lower(): f for f in t.get_all("/api/food/")}
    bad_recipes = 0
    total_lines = 0
    for rid in planner_recipe_ids(t, fixed_cfg):
        recipe = t.get(f"/api/recipe/{rid}/")
        lines = []
        if live:
            # Only the list this test creates is read and deleted: the planned
            # week's lists for the same recipe must survive.
            before = {x["id"] for x in t.get_all("/api/shopping-list-recipe/")}
            t.put(f"/api/recipe/{rid}/shopping/", {"servings": 2, "ingredients": []})
            slr = [x for x in t.get_all("/api/shopping-list-recipe/")
                   if x["id"] not in before and x.get("recipe") == rid]
            slr_ids = {x["id"] for x in slr}
            try:
                for e in t.shopping_list_entries():
                    if e.get("list_recipe") in slr_ids and not e.get("checked"):
                        lines.append(((e.get("food") or {}).get("name") or "?",
                                      (e.get("unit") or {}).get("name"), e.get("amount") or 0))
            finally:
                for x in slr:
                    t.delete(f"/api/shopping-list-recipe/{x['id']}/")
        else:
            for step in recipe.get("steps") or []:
                for ing in step.get("ingredients") or []:
                    if ing.get("is_header") or not (ing.get("food") or {}).get("name"):
                        continue
                    body = plan_ingredient(ing, maps) or {}
                    food = body.get("food", ing["food"])["name"]
                    unit = ((body["unit"] or {}).get("name") if "unit" in body
                            else (ing.get("unit") or {}).get("name"))
                    low = food.lower()
                    cur = foods.get(low) or {}
                    cat = S.route_food(food, foods_cfg, cfg["stores"])
                    if (cur.get("food_onhand") or low in maps.pantry or cat.startswith("Amana/")
                            or cur.get("ignore_shopping") or low in maps.not_food):
                        continue
                    lines.append((food, unit, body.get("amount", ing.get("amount") or 0)))
        probs = []
        for food, unit, _amt in lines:
            p, _cat = check_line(food, unit, maps, cfg, foods_cfg)
            probs += [f"{food!r}: {x}" for x in p]
        total_lines += len(lines)
        status = "OK " if not probs else "FIX"
        log.info("%s %4s %-45s %2d lines", status, rid, recipe["name"][:45], len(lines))
        for p in probs:
            log.info("        - %s", p)
        bad_recipes += bool(probs)
    log.info("test %s: %d recipes with problems, %d shopping lines in all",
             "live" if live else "simulated", bad_recipes, total_lines)
    return bad_recipes


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true", help="write to Tandoor (owner only)")
    ap.add_argument("--refresh-list", action="store_true",
                    help="rebuild the planned meals' shopping entries from the clean recipes")
    ap.add_argument("--test", action="store_true",
                    help="test each planner recipe's shopping list against the rules (read-only)")
    ap.add_argument("--test-live", action="store_true",
                    help="as --test, but really add each recipe to the shopping list, check, delete")
    a = ap.parse_args(argv)
    cfg, foods_cfg, fixed_cfg, _ = S.load_configs()
    from .tandoor import Tandoor
    t = Tandoor(cfg["tandoor"]["url"], os.environ["TANDOOR_USER"],
                os.environ["TANDOOR_PASS"], cfg["tandoor"].get("space", "Mack House"),
                space_id=cfg["tandoor"].get("space_id"),
                household=cfg["tandoor"].get("household"),
                household_id=cfg["tandoor"].get("household_id")).login()
    if a.test or a.test_live:
        bad = validate_recipes(t, load_cleanup(), cfg, foods_cfg, fixed_cfg, live=a.test_live)
        return 1 if bad else 0
    run(t, load_cleanup(), cfg, foods_cfg, fixed_cfg, apply=a.apply, refresh_list=a.refresh_list)
    return 0


if __name__ == "__main__":
    sys.exit(main())
