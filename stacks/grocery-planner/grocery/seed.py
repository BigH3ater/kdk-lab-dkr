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
import re
import sys

import yaml

from . import candidates as C

HERE = os.path.dirname(os.path.abspath(__file__))
STACK = os.path.dirname(HERE)
log = logging.getLogger("grocery.seed")

# keyword vocabulary (brief)
VOCAB = ["main", "simple", "mediterranean", "veg-side", "lunch-prep", "breakfast",
         "snack", "fish", "salmon", "tuna", "poultry", "pork", "ground-beef",
         "steak", "roast", "comfort", "tex-mex", "asian", "italian", "greek",
         "feta", "spring", "summer", "fall", "winter"]
BOOKS = {"spring": "Spring", "summer": "Summer", "fall": "Fall", "winter": "Winter"}


def load_configs(stack=STACK):
    cfg = yaml.safe_load(open(os.path.join(stack, "config.yaml")))
    foods = yaml.safe_load(open(os.path.join(stack, "recipes", "foods.yaml")))
    fixed = yaml.safe_load(open(os.path.join(stack, "recipes", "fixed.yaml")))
    cf = C.load_candidate_files(os.path.join(stack, "recipes", "candidates"))
    return cfg, foods, fixed, cf


def keywords_for_slot(slot_id, kind, season):
    kw = {season}
    if kind == "side":
        kw |= {"veg-side"}
    elif kind == "lunch":
        kw |= {"lunch-prep"}
    elif kind == "roast":
        kw |= {"main", "roast"}        # slow-cooker roast (not simple/mediterranean)
    elif kind == "ground_beef":
        # comfort dishes: ground-beef + the beef-style, no "mediterranean".
        kw |= {"main", "ground-beef", C.beef_style_for_slot(slot_id)}
    elif kind == "pork":
        kw |= {"main", "simple", "pork"}
    elif kind in ("fish", "chicken", "steak"):
        kw |= {"main", "simple", "mediterranean"}
        if kind == "fish":
            kw |= {"fish", "salmon" if slot_id.endswith("-a") else "tuna"}
        elif kind == "chicken":
            kw |= {"poultry"}
        elif kind == "steak":
            kw |= {"steak"}
    return sorted(kw)


def route_food(name, foods_cfg, stores_cfg):
    """Return the supermarket-category name for a food by the LONGEST matching
    rule (most specific wins, so 'crushed tomato' beats 'tomato', 'popcorn'
    beats 'corn', 'olive oil' beats 'oil', 'rice cake' beats 'rice', 'ground
    beef' beats 'beef'), else the shelf-stable freshness default."""
    low = (name or "").lower()
    # Frozen vegetables -> Target frozen aisle. Handled before longest-match
    # because a wording like "frozen cut green beans" would otherwise hit the
    # fresh "green bean" produce rule. Frozen FISH keeps its explicit routing
    # (frozen salmon -> Costco, frozen tuna -> New Pi).
    if "frozen" in low and "salmon" not in low and "tuna" not in low:
        return "Target/weekly: Frozen"
    best = None
    for rule in foods_cfg.get("rules", []):
        m = rule["match"].lower()
        if m in low and (best is None or len(m) > len(best["match"])):
            best = rule
    if best:
        return best["category"]
    return f"{stores_cfg['default_shelf_stable']}/weekly: Other"


def build_seed_plan(cfg, foods_cfg, fixed_cfg, scored):
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
        "fixed": [r["name"] for r in (fixed_cfg.get("recipes") or [])],
        "imports": imports,
        "no_candidate": no_candidate,
        "shortfalls": C.coverage_shortfalls(scored),
        "side_shortfalls": C.side_shortfalls(scored),
    }


def print_plan(plan):
    print("=== grocery-planner seed plan (dry-run) ===")
    print(f"supermarkets: {', '.join(plan['supermarkets'])}")
    print(f"meal types:   {', '.join(plan['meal_types'])}")
    print(f"recipe books: {', '.join(plan['books'])}")
    print(f"fixed recipes (seeded directly): {', '.join(plan['fixed'])}")
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
    print("\n=== seed preflight (per-season approved pool vs the weekly needs) ===")
    if plan["shortfalls"]:
        for season, short in plan["shortfalls"].items():
            print(f"  FAIL {season}: needs {', '.join(short)}")
        print("  --apply is blocked until these are filled (or pass --force).")
    else:
        print("  OK: every season meets the hard weekly minimums (both parities).")
    if plan["side_shortfalls"]:
        print("  sides below the 7-a-week target (interim repeat rule covers it; "
              "approve more sides to remove the repeat):")
        for season, (n, target) in plan["side_shortfalls"].items():
            print(f"    - {season}: {n}/{target} approved sides")


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


def _route_recipe_foods(t, recipe, foods_cfg, cfg, cats):
    """Assign each ingredient food's supermarket_category (and food_onhand for
    Amana freezer stock)."""
    for step in recipe.get("steps", []) or []:
        for ing in step.get("ingredients", []) or []:
            food = ing.get("food") or {}
            fid = food.get("id")
            if not fid:
                continue
            cat_name = route_food(food.get("name", ""), foods_cfg, cfg["stores"])
            cat = cats.get(cat_name) or t.ensure_supermarket_category(cat_name)
            cats[cat_name] = cat
            body = {"supermarket_category": {"id": cat["id"], "name": cat_name}}
            if cat_name.startswith("Amana/"):     # eighth-share freezer stock -> on hand
                body["food_onhand"] = True
            t.patch(f"/api/food/{fid}/", body)


def seed_fixed(t, fixed_cfg, cfg, foods_cfg, cats, kws):
    """Create the hand-written fixed recipes (Weekly staples, Snacks to buy,
    Snack prep) if missing, tag + route them. Returns the names created/present."""
    existing = {r["name"] for r in t.get_all("/api/recipe/")}
    names = []
    for rec in fixed_cfg.get("recipes") or []:
        names.append(rec["name"])
        if rec["name"] in existing:
            log.info("skip (fixed recipe exists): %s", rec["name"])
            rid = next(r["id"] for r in t.get_all("/api/recipe/") if r["name"] == rec["name"])
        else:
            kw_objs = [kws.get(k) or t.ensure_keyword(k) for k in rec.get("keywords", [])]
            ings = [{"food": {"name": i["food"]},
                     "unit": ({"name": i["unit"]} if i.get("unit") else None),
                     "amount": i.get("amount", 1), "note": ""}
                    for i in rec.get("ingredients", [])]
            body = {"name": rec["name"], "servings": rec.get("servings", 1),
                    "servings_text": rec.get("servings_text", "")[:32], "internal": True,   # Tandoor max 32
                    "keywords": [{"id": k["id"], "name": k["name"]} for k in kw_objs if k],
                    "steps": [{"instruction": "Buy / prep the weekly items.",
                               "ingredients": ings}]}
            created = t.post("/api/recipe/", body)
            rid = created.get("id")
            log.info("created fixed recipe %s -> %s", rec["name"], rid)
        _route_recipe_foods(t, t.get(f"/api/recipe/{rid}/"), foods_cfg, cfg, cats)
    return names


def apply_seed(plan, cfg, foods_cfg, fixed_cfg, force=False):
    """Execute the plan against Tandoor. Idempotent: ensure_* checks existence.
    OWNER-ONLY (writes). Credentials from env. Refuses to run if any season is
    short of the generator minimums unless force=True (preflight)."""
    if plan["shortfalls"] and not force:
        for season, short in plan["shortfalls"].items():
            log.error("seed preflight: %s needs %s", season, ", ".join(short))
        raise SystemExit("seed preflight failed: fill the slots above (or re-run "
                         "with --force to seed what exists). See the runbook.")
    from .tandoor import Tandoor
    t = Tandoor(cfg["tandoor"]["url"], os.environ["TANDOOR_USER"],
                os.environ["TANDOOR_PASS"], cfg["tandoor"].get("space", "Mack House"),
                space_id=cfg["tandoor"].get("space_id"),
                household=cfg["tandoor"].get("household"),
                household_id=cfg["tandoor"].get("household_id")).login()
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
    share_ids = list((cfg.get("tandoor", {}).get("share_with") or {}).values())
    books = {name: t.ensure_recipe_book(name, share_ids=share_ids) for name in plan["books"]}
    cats = t.supermarket_categories()
    kws = t.keywords()
    seed_fixed(t, fixed_cfg, cfg, foods_cfg, cats, kws)
    # Detail view: the list endpoint has no source_url, which made every run
    # re-import everything (duplicates found 2026-11-01). Lowest id wins per URL.
    existing = {}
    for r in sorted(t.recipes_detail(), key=lambda r: r["id"]):
        if r.get("source_url"):
            existing.setdefault(url_key(r["source_url"]), r)
    # Book entries already present: re-posting one is a 400 "unique set".
    in_book = {(e["book"], e["recipe"]) for e in t.get_all("/api/recipe-book-entry/")}
    imaged = set()   # one image fetch per recipe, even when several slots share it
    for im in plan["imports"]:
        key = url_key(im["url"])
        if key in existing:
            log.info("skip (already imported): %s", im["url"])
            rid = existing[key]["id"]
            if not existing[key].get("image") and rid not in imaged:
                if t.backfill_image(rid, im["url"]):
                    log.info("image set for %s (recipe %s)", im["slot"], rid)
            imaged.add(rid)
        else:
            try:
                rid, _ = t.import_from_url(im["url"])
                log.info("imported %s -> recipe %s", im["slot"], rid)
            except Exception as e:  # noqa: BLE001
                log.error("import failed for %s (%s): %s", im["slot"], im["url"], e)
                continue
            existing[key] = {"id": rid, "image": True}
            imaged.add(rid)
        recipe = t.get(f"/api/recipe/{rid}/")
        # MERGE keywords (don't overwrite): a recipe reused across seasons (e.g. a
        # roasted-carrots side in both Fall and Winter) must keep every season's
        # keyword, or a second import would drop the first season's tag.
        merged = {k["name"]: {"id": k["id"], "name": k["name"]}
                  for k in (recipe.get("keywords") or [])}
        for name in im["keywords"]:
            if name not in merged:
                k = kws.get(name) or t.ensure_keyword(name)
                merged[name] = {"id": k["id"], "name": k["name"]}
        t.patch(f"/api/recipe/{rid}/", {"keywords": list(merged.values()), "servings": 2})
        book_id = books[im["book"]]["id"]
        if (book_id, rid) not in in_book:
            t.post("/api/recipe-book-entry/", {"book": book_id, "recipe": rid})
            in_book.add((book_id, rid))
        _route_recipe_foods(t, recipe, foods_cfg, cfg, cats)
    log.info("seed apply complete")


def url_key(url):
    """Compare source URLs the way Tandoor stores them: it drops `www.` (the
    spring souvlaki and spinach-salad candidates re-imported every run because
    the YAML had www.), so ignore scheme, www., case of the host and a trailing /."""
    u = re.sub(r"^https?://", "", (url or "").strip())
    host, _, path = u.partition("/")
    host = host.lower().removeprefix("www.")
    return f"{host}/{path.rstrip('/')}"


def dedupe_plan(recipes):
    """Group recipes by source_url; keep the lowest id. Returns
    [(keeper, [duplicates...])] for groups with more than one recipe."""
    groups = {}
    for r in sorted(recipes, key=lambda r: r["id"]):
        if r.get("source_url"):
            groups.setdefault(url_key(r["source_url"]), []).append(r)
    return [(g[0], g[1:]) for g in groups.values() if len(g) > 1]


def apply_dedupe(t, dry_run=True):
    """Merge duplicate imports into the keeper: union of keywords, the duplicate's
    book entries and meal-plan entries moved to the keeper, then delete the copy.
    Prints the plan; writes only with dry_run=False."""
    plan = dedupe_plan(t.recipes_detail())
    log.info("dedupe: %d groups, %d extra copies", len(plan), sum(len(d) for _, d in plan))
    if dry_run:
        for keep, dups in plan:
            log.info("  keep %s %r, remove %s", keep["id"], keep["name"][:50], [d["id"] for d in dups])
        return plan
    entries = t.get_all("/api/recipe-book-entry/")
    meals = t.get_all("/api/meal-plan/")
    for keep, dups in plan:
        kw = {k["name"]: {"id": k["id"], "name": k["name"]} for k in keep.get("keywords") or []}
        keep_books = {e["book"] for e in entries if e.get("recipe") == keep["id"]}
        for d in dups:
            for k in d.get("keywords") or []:
                kw.setdefault(k["name"], {"id": k["id"], "name": k["name"]})
            for e in [e for e in entries if e.get("recipe") == d["id"]]:
                if e["book"] not in keep_books:
                    t.post("/api/recipe-book-entry/", {"book": e["book"], "recipe": keep["id"]})
                    keep_books.add(e["book"])
            for m in [m for m in meals if (m.get("recipe") or {}).get("id") == d["id"]]:
                t.patch(f"/api/meal-plan/{m['id']}/", {"recipe": {"id": keep["id"], "name": keep["name"]}})
        t.patch(f"/api/recipe/{keep['id']}/", {"keywords": list(kw.values())})
        for d in dups:
            t.delete(f"/api/recipe/{d['id']}/")
            log.info("removed duplicate %s (kept %s)", d["id"], keep["id"])
    return plan


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true", help="write to Tandoor (owner only)")
    ap.add_argument("--force", action="store_true", help="apply even if the preflight finds gaps")
    ap.add_argument("--refresh", action="store_true", help="re-read candidate ratings into the YAML")
    ap.add_argument("--report", metavar="PATH", help="write the Markdown candidate review table")
    ap.add_argument("--dedupe", action="store_true",
                    help="merge duplicate imports (same source URL); dry run unless --apply")
    a = ap.parse_args(argv)
    cfg, foods_cfg, fixed_cfg, cf = load_configs()
    if a.refresh:
        refresh()
        cfg, foods_cfg, fixed_cfg, cf = load_configs()
    scored = C.score_all(cf, cfg)
    plan = build_seed_plan(cfg, foods_cfg, fixed_cfg, scored)
    if a.report:
        with open(a.report, "w") as f:
            f.write(render_candidate_markdown(scored, cfg))
        log.info("wrote %s", a.report)
    if a.dedupe:
        from .tandoor import Tandoor
        t = Tandoor(cfg["tandoor"]["url"], os.environ["TANDOOR_USER"],
                    os.environ["TANDOOR_PASS"], cfg["tandoor"].get("space", "Mack House"),
                    space_id=cfg["tandoor"].get("space_id"),
                    household=cfg["tandoor"].get("household"),
                    household_id=cfg["tandoor"].get("household_id")).login()
        apply_dedupe(t, dry_run=not a.apply)
        return 0
    if a.apply:
        apply_seed(plan, cfg, foods_cfg, fixed_cfg, force=a.force)
    else:
        print_plan(plan)
    return 1 if plan["shortfalls"] else 0


if __name__ == "__main__":
    sys.exit(main())
