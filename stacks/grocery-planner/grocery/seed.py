"""Idempotent Tandoor seed for the grocery planner.

Creates the stores, namespaced supermarket categories, the keyword vocabulary,
the four seasonal recipe books, and imports the CHOSEN candidate recipe for each
slot via Tandoor's URL import (recipe-from-source), then tags it with our
keywords + season book and routes its foods.

  python -m grocery.seed                 # --dry-run (default): print the plan, write the review doc
  python -m grocery.seed --apply         # OWNER runs this after review: writes to Tandoor
  python -m grocery.seed --refresh       # re-read candidate ratings into the YAML (best-effort)
  python -m grocery.seed --report PATH   # (re)write the Markdown candidate review table

Nothing here runs by itself; the weekly job never seeds. No secrets are printed.
"""
from __future__ import annotations

import argparse
import glob
import logging
import os
import sys

import yaml

from . import candidates as C

HERE = os.path.dirname(os.path.abspath(__file__))
STACK = os.path.dirname(HERE)
log = logging.getLogger("grocery.seed")

# keyword vocabulary (brief)
VOCAB = ["main", "simple", "mediterranean", "veg-side", "lunch-prep", "breakfast",
         "snack", "fish", "salmon", "tuna", "poultry", "ground-beef", "steak",
         "greek", "asian", "feta", "spring", "summer", "fall", "winter"]
BOOKS = {"spring": "Spring", "summer": "Summer", "fall": "Fall", "winter": "Winter"}


def load_configs(stack=STACK):
    cfg = yaml.safe_load(open(os.path.join(stack, "config.yaml")))
    foods = yaml.safe_load(open(os.path.join(stack, "recipes", "foods.yaml")))
    cf = C.load_candidate_files(os.path.join(stack, "recipes", "candidates"))
    return cfg, foods, cf


def keywords_for_slot(slot_id, kind, season):
    kw = {season}
    if kind == "side":
        kw |= {"veg-side"}
    elif kind == "lunch":
        kw |= {"lunch-prep"}
    elif kind in ("fish", "chicken", "ground_beef", "steak"):
        kw |= {"main", "simple", "mediterranean"}
        if kind == "fish":
            kw |= {"fish", "salmon" if slot_id.endswith("-a") else "tuna"}
        elif kind == "chicken":
            kw |= {"poultry"}
        elif kind == "ground_beef":
            kw |= {"ground-beef", "greek" if slot_id.endswith("-a") else "asian"}
            if slot_id.endswith("-a"):
                kw |= {"feta"}
        elif kind == "steak":
            kw |= {"steak"}
    return sorted(kw)


def route_food(name, foods_cfg, stores_cfg):
    """Return the supermarket-category name for a food, by first-match rule, else
    the freshness default."""
    low = (name or "").lower()
    for rule in foods_cfg.get("rules", []):
        if rule["match"].lower() in low:
            return rule["category"]
    # fallback: ambiguous -> shelf-stable Target by default (documented); fresh
    # produce words would have matched above.
    return f"{stores_cfg['default_shelf_stable']}/weekly: Other"


def build_seed_plan(cfg, foods_cfg, scored):
    """Pure: the full set of idempotent actions the seed would take."""
    stores_cfg = cfg["stores"]
    # categories = every namespaced aisle in the routing table + the per-store
    # aisle order from config (so empty aisles still exist in the right order).
    cats = set()
    for rule in foods_cfg.get("rules", []):
        cats.add(rule["category"])
    categories = sorted(cats)

    imports, no_candidate = [], []
    for season, slots in scored.items():
        for slot in slots:
            ch = slot["chosen"]
            if not ch:
                no_candidate.append(slot["slot"])
                continue
            imports.append({
                "slot": slot["slot"],
                "season": season,
                "title": ch["title"],
                "url": ch["url"],
                "site": ch["site"],
                "verdict": ch["verdict"],
                "score": ch["score"],
                "keywords": keywords_for_slot(slot["slot"], slot["kind"], season),
                "book": BOOKS.get(season, season),
            })
    return {
        "supermarkets": stores_cfg["order"],
        "categories": categories,
        "keywords": VOCAB,
        "books": list(BOOKS.values()),
        "meal_types": list(cfg["tandoor"]["meal_types"].values()),
        "imports": imports,
        "no_candidate": no_candidate,
    }


def print_plan(plan):
    print("=== grocery-planner seed plan (dry-run) ===")
    print(f"supermarkets: {', '.join(plan['supermarkets'])}")
    print(f"meal types:   {', '.join(plan['meal_types'])}")
    print(f"recipe books: {', '.join(plan['books'])}")
    print(f"keywords ({len(plan['keywords'])}): {', '.join(plan['keywords'])}")
    print(f"supermarket categories ({len(plan['categories'])}):")
    for c in plan["categories"]:
        print(f"  - {c}")
    print(f"\nrecipe imports ({len(plan['imports'])} chosen):")
    for im in plan["imports"]:
        print(f"  [{im['verdict']:10}] {im['slot']:30} {im['site']:18} {im['title'][:48]}")
        print(f"       {im['url']}")
        print(f"       keywords: {', '.join(im['keywords'])}  book: {im['book']}")
    if plan["no_candidate"]:
        print(f"\nslots with NO good/acceptable candidate ({len(plan['no_candidate'])}):")
        for s in plan["no_candidate"]:
            print(f"  - {s}  (owner: add a candidate URL or set selected:true, then re-run)")


def render_candidate_markdown(scored, cfg):
    sc = cfg["scoring"]
    out = ["# Grocery recipe candidates — review table",
           "",
           "Generated by `python -m grocery.seed --report`. Ratings are public",
           f"data read from each recipe page's schema.org JSON-LD on the dates shown.",
           "",
           "**Scoring:** Bayesian `score = (C*m + n*r)/(C+n)` with "
           f"`m={sc['prior_m']}`, `C={sc['prior_weight_C']}`. "
           f"Verdict: **good** = rating ≥ {sc['good']['min_rating']} & count ≥ "
           f"{sc['good']['min_count']}; **acceptable** = rating ≥ "
           f"{sc['acceptable']['min_rating']} & count ≥ {sc['acceptable']['min_count']}; "
           "else rejected. The chosen default per slot is the top-scoring "
           "good/acceptable candidate (override with `selected: true`).",
           ""]
    s = C.summarize(scored)["overall"]
    out.append(f"**Totals:** {s['slots']} slots, {s['candidates']} candidates — "
               f"good {s['good']}, acceptable {s['acceptable']}, rejected {s['rejected']}; "
               f"{s['slots_no_good']} slots with no good candidate.\n")
    for season in sorted(scored):
        out.append(f"\n## {season.title()}\n")
        out.append("| Slot | Chosen | Site | Rating | Count | Score | Time | Verdict | Flags |")
        out.append("|---|---|---|---|---|---|---|---|---|")
        for slot in scored[season]:
            for c in slot["candidates"]:
                chosen = "★" if slot["chosen"] is c else ""
                flags = "; ".join(c.get("flags") or []) or "-"
                rr = "; ".join(c.get("reject_reasons") or [])
                note = flags if c["verdict"] != "rejected" else (rr or "-")
                out.append(f"| {slot['slot']} {chosen} | {c['title'][:40]} | {c['site']} | "
                           f"{c.get('rating')} | {c.get('count')} | {c.get('score')} | "
                           f"{c.get('total_time_min')} | {c['verdict']} | {note[:60]} |")
    return "\n".join(out) + "\n"


def refresh(stack=STACK, timeout=20):
    """Re-read each candidate URL's schema.org rating into the YAML (best-effort,
    one GET per page). Sites that block are left unchanged and logged."""
    import json
    import re
    import requests
    hdrs = {"User-Agent": "Mozilla/5.0 (kdk-grocery refresh)"}
    for path in sorted(glob.glob(os.path.join(stack, "recipes", "candidates", "*.yaml"))):
        data = yaml.safe_load(open(path))
        changed = 0
        for slot in data.get("slots") or []:
            for cand in slot.get("candidates") or []:
                try:
                    r = requests.get(cand["url"], headers=hdrs, timeout=timeout)
                    if not r.ok:
                        log.warning("refresh %s -> %s (left unchanged)", cand["url"], r.status_code)
                        continue
                    agg = _extract_aggregate(r.text)
                    if agg:
                        cand["rating"], cand["count"] = agg
                        cand["checked_on"] = _today()
                        changed += 1
                except Exception as e:  # noqa: BLE001
                    log.warning("refresh %s failed: %s", cand["url"], e)
        if changed:
            yaml.safe_dump(data, open(path, "w"), sort_keys=False, allow_unicode=True)
        log.info("refreshed %s: %d candidates updated", os.path.basename(path), changed)


def _today():
    import datetime
    return datetime.date.today().isoformat()


def _extract_aggregate(html):
    import json
    import re
    for m in re.finditer(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>',
                         html, re.S):
        try:
            blob = json.loads(m.group(1))
        except Exception:  # noqa: BLE001
            continue
        for node in _iter_recipe(blob):
            agg = node.get("aggregateRating")
            if isinstance(agg, dict):
                rv = agg.get("ratingValue")
                rc = agg.get("ratingCount") or agg.get("reviewCount")
                if rv is not None and rc is not None:
                    try:
                        return float(rv), int(float(rc))
                    except (TypeError, ValueError):
                        return None
    return None


def _iter_recipe(blob):
    stack = [blob]
    while stack:
        n = stack.pop()
        if isinstance(n, list):
            stack.extend(n)
        elif isinstance(n, dict):
            t = n.get("@type")
            types = t if isinstance(t, list) else [t]
            if "Recipe" in types:
                yield n
            if "@graph" in n:
                stack.extend(n["@graph"])


def apply_seed(plan, cfg, foods_cfg):
    """Execute the plan against Tandoor. Idempotent: ensure_* checks existence.
    OWNER-ONLY (writes). Credentials from env."""
    from .tandoor import Tandoor
    t = Tandoor(cfg["tandoor"]["url"], os.environ["TANDOOR_USER"],
                os.environ["TANDOOR_PASS"], cfg["tandoor"].get("space", "Mack House")).login()
    for name in plan["meal_types"]:
        if name not in t.meal_types():
            t.post("/api/meal-type/", {"name": name})
    for name in plan["supermarkets"]:
        if name not in t.supermarkets():
            t.post("/api/supermarket/", {"name": name})
    for name in plan["categories"]:
        t.ensure_supermarket_category(name)
    for name in plan["keywords"]:
        t.ensure_keyword(name)
    books = {name: t.ensure_recipe_book(name) for name in plan["books"]}
    existing = {r.get("source_url"): r for r in t.get_all("/api/recipe/")
                if r.get("source_url")}
    cats = t.supermarket_categories()
    kws = t.keywords()
    for im in plan["imports"]:
        if im["url"] in existing:
            log.info("skip (already imported): %s", im["url"])
            rid = existing[im["url"]]["id"]
        else:
            try:
                rid, _ = t.import_from_url(im["url"])
                log.info("imported %s -> recipe %s", im["slot"], rid)
            except Exception as e:  # noqa: BLE001
                log.error("import failed for %s (%s): %s", im["slot"], im["url"], e)
                continue
        # tag keywords + set servings=2 + add to the season book + route foods
        recipe = t.get(f"/api/recipe/{rid}/")
        kw_objs = [kws.get(k) or t.ensure_keyword(k) for k in im["keywords"]]
        recipe["keywords"] = [{"id": k["id"], "name": k["name"]} for k in kw_objs if k]
        recipe["servings"] = 2
        t.patch(f"/api/recipe/{rid}/", {"keywords": recipe["keywords"], "servings": 2})
        book = books[im["book"]]
        t.post("/api/recipe-book-entry/", {"book": book["id"], "recipe": rid})
        # route each food of the recipe
        for step in recipe.get("steps", []):
            for ing in step.get("ingredients", []):
                food = ing.get("food") or {}
                fid = food.get("id")
                if not fid:
                    continue
                cat_name = route_food(food.get("name", ""), foods_cfg, cfg["stores"])
                cat = cats.get(cat_name) or t.ensure_supermarket_category(cat_name)
                cats[cat_name] = cat
                t.patch(f"/api/food/{fid}/", {"supermarket_category": {"id": cat["id"], "name": cat_name}})
    log.info("seed apply complete")


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true", help="write to Tandoor (owner only)")
    ap.add_argument("--refresh", action="store_true", help="re-read candidate ratings into the YAML")
    ap.add_argument("--report", metavar="PATH", help="write the Markdown candidate review table")
    a = ap.parse_args(argv)
    cfg, foods_cfg, cf = load_configs()
    if a.refresh:
        refresh()
        cfg, foods_cfg, cf = load_configs()
    scored = C.score_all(cf, cfg)
    plan = build_seed_plan(cfg, foods_cfg, scored)
    if a.report:
        with open(a.report, "w") as f:
            f.write(render_candidate_markdown(scored, cfg))
        log.info("wrote %s", a.report)
    if a.apply:
        apply_seed(plan, cfg, foods_cfg)
    else:
        print_plan(plan)
    return 0


if __name__ == "__main__":
    sys.exit(main())
