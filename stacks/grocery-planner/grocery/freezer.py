"""Freezer inventory: what is in the chest freezer, and what a planned week takes.

One JSON file on kdk-dkr-01 (/opt/kdk-lab/dashboard/pantry/freezer.json, mounted
at /pantry in the dashboard sidecar and in this job). The owner edits counts on
the groceries page's Pantry tab; the weekly build reads it to plan only what
the freezer can cover, then reserves (decrements) what the plan takes.

Schema (version 1):

  {"version": 1, "updated": "<iso>", "updated_by": "<who>",
   "cuts": [{"id": "ribeye", "name": "Ribeye steak", "unit": "portion"|"lb"|"package",
             "count": 6, "package": "12 oz x4, 10 oz x2", "package_lb": 0.71,
             "portions_per_package": null, "source": "Amana",
             "fill_in": true?},  ...],
   "reservations": [{"week": "2026-W48", "cut": "ribeye", "amount": 2,
                     "unit": "portion", "recipe": "...", "date": "2026-11-29"}]}

`fill_in: true` = count unknown (seeded 0): treated like an unmapped food
(available, nothing reserved) until the owner sets a count. Recipe foods map to
cuts through config `freezer.map` (food-name substring -> cut id, longest match).
Pure helpers + small file IO; no Tandoor calls.
"""
from __future__ import annotations

import copy
import datetime
import json
import math
import os

SCHEMA_VERSION = 1
DEFAULT_FILE = "/pantry/freezer.json"

_TO_LB = {"lb": 1.0, "lbs": 1.0, "pound": 1.0, "pounds": 1.0, "oz": 1 / 16,
          "ounce": 1 / 16, "ounces": 1 / 16, "g": 1 / 453.6, "gram": 1 / 453.6,
          "grams": 1 / 453.6, "kg": 2.2046}
_COUNT = {"", "each", "whole", "piece", "pieces", "steak", "steaks", "fillet",
          "fillets", "filet", "filets", "breast", "breasts", "chop", "chops",
          "patty", "patties", "link", "links", "small", "medium", "large",
          "package", "packages", "pkg", "slice", "slices", "strip", "strips"}


# ---- file IO ---------------------------------------------------------------
def path(env=None):
    env = os.environ if env is None else env
    return (env.get("FREEZER_FILE") or DEFAULT_FILE).strip()


def load(p):
    """(data, None) or (None, reason). Never raises."""
    try:
        with open(p) as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return None, "missing"
    except (OSError, ValueError) as e:
        return None, f"unreadable: {type(e).__name__}"
    if not isinstance(data, dict) or not isinstance(data.get("cuts"), list):
        return None, "bad shape (no cuts list)"
    if int(data.get("version") or 0) != SCHEMA_VERSION:
        return None, f"unknown version {data.get('version')!r}"
    return data, None


def save(p, data, who="grocery-planner"):
    """Atomic write: the current file goes to <p>.bak, the new one lands via
    tmp + rename. Both by rename, so a file owned by another uid in the
    (uid-1000) directory never blocks the write."""
    data = dict(data, version=SCHEMA_VERSION, updated_by=who,
                updated=datetime.datetime.now().astimezone().isoformat(timespec="seconds"))
    d = os.path.dirname(p) or "."
    if os.path.exists(p):
        with open(p, "rb") as fh:
            old = fh.read()
        _write(p + ".bak", old, d)
    _write(p, (json.dumps(data, indent=1, ensure_ascii=False) + "\n").encode(), d)


def _write(p, raw, d):
    tmp = os.path.join(d, f".{os.path.basename(p)}.{os.getpid()}.tmp")
    with open(tmp, "wb") as fh:
        fh.write(raw)
    os.chmod(tmp, 0o644)
    os.replace(tmp, p)


# ---- mapping ---------------------------------------------------------------
def cut_for(food, mapping):
    """Cut id for a Tandoor food name: the LONGEST `freezer.map` key that is a
    substring of the name (like foods.yaml / thaw), else None."""
    low, best = (food or "").lower(), None
    for k in mapping or {}:
        kl = str(k).lower()
        if kl and kl in low and (best is None or len(kl) > len(best)):
            best = kl
    return None if best is None else {str(k).lower(): v for k, v in mapping.items()}[best]


def _optional(ing):
    text = " ".join(str(ing.get(k) or "") for k in ("original_text", "note")).lower()
    return "optional" in text


def recipe_lines(recipe, mapping):
    """The recipe's mapped freezer ingredients: [{cut, food, amount, unit, base}]
    (base = the recipe's own servings). Optional and header lines skipped."""
    out = []
    base = float(recipe.get("servings") or 1) or 1.0
    for step in recipe.get("steps") or []:
        for ing in step.get("ingredients") or []:
            name = ((ing.get("food") or {}).get("name") or "").strip()
            if not name or ing.get("is_header") or _optional(ing):
                continue
            cut = cut_for(name, mapping)
            if cut:
                out.append({"cut": cut, "food": name, "amount": float(ing.get("amount") or 0),
                            "unit": ((ing.get("unit") or {}).get("name") or "").strip().lower(),
                            "base": base})
    return out


def is_freezer_cat(cat, stores_cfg):
    """A stock or bulk store's freezer aisle (same test as the Thaw tab)."""
    store, _, aisle = (cat or "").partition(":")
    store = store.split("/", 1)[0].strip()
    stores = set(stores_cfg.get("stock_stores") or []) | set(stores_cfg.get("bulk_stores") or [])
    return store in stores and "freezer" in aisle.lower()


# ---- stock ------------------------------------------------------------------
class Stock:
    """Counts in memory. The generator uses a scratch copy (need/fits/take) so
    two dinners never count the same steaks; run.reserve applies the final
    plan to a fresh read of the file."""

    def __init__(self, data):
        self.data = copy.deepcopy(data)
        self.cuts = {c["id"]: c for c in self.data.get("cuts") or [] if c.get("id")}
        self.short = []          # [{slot, recipe, need}] picks the stock could not cover
        self.fish = None         # the fish variant the generator settled on

    def known(self, cut_id):
        c = self.cuts.get(cut_id)
        return c is not None and not c.get("fill_in")

    def remaining(self, cut_id):
        return float((self.cuts.get(cut_id) or {}).get("count") or 0)

    def need(self, lines, servings):
        """{cut: amount in the cut's unit} for `lines` at `servings`. Unknown
        cuts (not in the file, or fill_in) and amounts that cannot be converted
        are left out: they count as available, as before the inventory."""
        out = {}
        for ln in lines or []:
            c = self.cuts.get(ln["cut"])
            if c is None or c.get("fill_in"):
                continue
            amt = ln["amount"] * (float(servings) / (ln.get("base") or 1))
            v = _convert(amt, ln["unit"], c, servings)
            if v is not None:
                out[ln["cut"]] = out.get(ln["cut"], 0.0) + v
        return {k: _round(v, self.cuts[k].get("unit")) for k, v in out.items()}

    def need_for(self, item, servings):
        return self.need(item.get("freezer_lines"), servings)

    def fits(self, need):
        return all(self.remaining(k) + 1e-9 >= v for k, v in need.items())

    def take(self, need):
        for k, v in need.items():
            c = self.cuts[k]
            c["count"] = round(max(0.0, self.remaining(k) - v), 2)

    def has(self, items, servings):
        """True when some item has a NON-empty need the stock covers (the food
        is known to be in the freezer, not just unmapped)."""
        for it in items:
            n = self.need_for(it, servings)
            if n and self.fits(n):
                return True
        return False


def _convert(amt, unit, cut, servings):
    lb = amt * _TO_LB[unit] if unit in _TO_LB else None
    n = amt if unit in _COUNT else None
    kind = cut.get("unit") or "portion"
    plb = float(cut.get("package_lb") or 0) or None
    ppp = float(cut.get("portions_per_package") or 0) or None
    if kind == "lb":
        if lb is not None:
            return lb
        return n * plb if (n is not None and plb) else None
    if kind == "package":
        if lb is not None and plb:
            return lb / plb
        if n is not None and ppp:
            return n / ppp
        return 1.0                                  # portions unknown: one package
    # portion
    if n is not None:
        return n
    if lb is not None and plb:
        return lb / plb
    return float(servings)                          # one portion per person


def _round(v, unit):
    if unit in ("portion", "package"):
        return float(math.ceil(v - 1e-9))
    return round(v, 2)


def order_by_stock(cands, stock, servings):
    """Stable re-order: recipes whose freezer cut has the most dinners' worth
    left first. A cut's worth = remaining / the smallest need among the
    candidates using it, so recipe size does not bias the pick within a cut
    (the shuffle order is kept there). Recipes with no known cut rank as 1."""
    if stock is None:
        return cands
    prim, smallest = {}, {}
    for r in cands:
        d = stock.need_for(r, servings)
        if not d:
            continue
        c = min(d, key=lambda k: stock.remaining(k) / d[k] if d[k] else math.inf)
        prim[r["id"]] = c
        smallest[c] = min(smallest.get(c, math.inf), d[c] or math.inf)

    def worth(r):
        c = prim.get(r["id"])
        if c is None:
            return 1.0
        return stock.remaining(c) / smallest[c] if smallest[c] not in (0, math.inf) else math.inf
    return sorted(cands, key=worth, reverse=True)


def choose(cands, n, stock, servings, slot, ok=None):
    """Pick n from the (already shuffled) candidates. With a stock: covered
    recipes first, most stock first, each pick taken before the next is tested;
    if fewer than n are covered, the rest are filled from the short ones (noted
    in stock.short, never a build failure). `ok(chosen, r)` is an extra rule
    (e.g. at most one Asian-style beef)."""
    ok = ok or (lambda chosen, r: True)
    if stock is None:
        chosen = []
        for r in cands:
            if len(chosen) == n:
                break
            if ok(chosen, r):
                chosen.append(r)
        return chosen
    chosen = []
    for r in order_by_stock(cands, stock, servings):
        if len(chosen) == n:
            break
        need = stock.need_for(r, servings)
        if ok(chosen, r) and stock.fits(need):
            stock.take(need)
            chosen.append(r)
    for r in cands:
        if len(chosen) == n:
            break
        if r in chosen or not ok(chosen, r):
            continue
        need = stock.need_for(r, servings)
        stock.short.append({"slot": slot, "recipe": r.get("name"), "need": need,
                            "have": {k: stock.remaining(k) for k in need}})
        stock.take(need)
        chosen.append(r)
    return chosen


# ---- reservation -------------------------------------------------------------
def reservations(entries, details, data, mapping, week):
    """[{week, cut, amount, unit, recipe, date}] for the planned entries (the
    leftover lunch excluded: its roast was reserved with the dinner), and the
    cuts the stock could not fully cover."""
    st = Stock(data)
    out, short = [], []
    for e in entries:
        if getattr(e, "role", None) == "leftover":
            continue
        r = details.get(e.recipe_id) or {}
        need = st.need(recipe_lines(r, mapping), e.servings or r.get("servings") or 2)
        for cut, amt in need.items():
            if st.remaining(cut) + 1e-9 < amt:
                short.append({"cut": cut, "want": amt, "have": st.remaining(cut),
                              "recipe": e.recipe_name})
            out.append({"week": week, "cut": cut, "amount": amt,
                        "unit": st.cuts[cut].get("unit") or "portion",
                        "recipe": e.recipe_name, "date": e.date.isoformat()})
        st.take(need)
    return out, short


def apply_reservations(p, week, items, who="grocery-planner"):
    """Re-read the file (the owner may have tapped since the plan was built),
    decrement each reserved cut (floor 0), append the reservations, keep the
    last 8 weeks of them. Idempotent per week: a week already reserved is not
    taken twice. Returns 'applied' | 'already' | the load error."""
    data, err = load(p)
    if data is None:
        return err
    if any(r.get("week") == week for r in data.get("reservations") or []):
        return "already"
    st = Stock(data)
    for it in items:
        if it["cut"] in st.cuts:
            st.take({it["cut"]: it["amount"]})
    weeks = sorted({r.get("week") for r in st.data.get("reservations") or []} | {week})[-8:]
    st.data["reservations"] = [r for r in st.data.get("reservations") or []
                               if r.get("week") in weeks] + list(items)
    save(p, st.data, who)
    return "applied"
