"""Build the shopping list from Tandoor shopping-list entries: drop on-hand
items, split by store, order by aisle. Pure; unit tested offline.

A shopping-list entry (live Tandoor 2.7 shape, confirmed):
  {id, amount, unit:{name}|null, checked, food:{name, food_onhand,
   supermarket_category:{name}|null}, list_recipe, ...}

Store + cadence + aisle come from the supermarket-category NAME, namespaced
"<store>/<cadence>: <aisle>" (e.g. "NewPi/weekly: Produce", "Costco/bulk:
Freezer"). A bare "<store>: <aisle>" (no cadence) is accepted too.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Item:
    entry_id: int
    food: str
    amount: float | None
    unit: str | None
    aisle: str
    store: str
    cadence: str | None

    def label(self) -> str:
        qty = ""
        if self.amount:
            a = self.amount
            a = int(a) if float(a).is_integer() else a
            qty = f"{a} "
            if self.unit:
                qty += f"{self.unit} "
        return f"{qty}{self.food}".strip()


@dataclass
class StoreList:
    store: str
    aisles: dict = field(default_factory=dict)   # aisle -> [Item]

    def entry_ids(self):
        return [it.entry_id for items in self.aisles.values() for it in items]

    def count(self):
        return sum(len(v) for v in self.aisles.values())


import math
import re


def is_excluded(name, excludes):
    """True if the food name matches any config `exclude` term (case-insensitive
    substring) -- e.g. dog/pet food, which must never reach a store list."""
    low = (name or "").lower()
    return any(str(x).lower() in low for x in (excludes or []))


def is_never_buy(name, terms):
    """True if the food name holds a config `never_buy` term (owner 2026-11-11:
    brussels sprouts, cabbage, red cabbage). Case-insensitive substring of the
    canonical food name, on word boundaries; a trailing "s" is optional either
    way ("brussels sprouts" also hits "brussels sprout", "pea" hits "peas")
    but a term never matches inside another word ("peas" is not "peanut")."""
    low = (name or "").lower()
    for t in terms or []:
        t = str(t).lower().strip()
        if t.endswith("s"):
            t = t[:-1]
        if t and re.search(r"\b" + re.escape(t) + r"s?\b", low):
            return True
    return False


def packs_needed(weekly_amount, weeks, pack_size):
    """Whole packs for a bulk item: ceil(weekly_amount * weeks / pack_size).
    Returns None if pack_size is missing/invalid, 0 if nothing is needed."""
    if not pack_size or pack_size < 1:
        return None
    total = (weekly_amount or 0) * weeks
    return math.ceil(total / pack_size) if total > 0 else 0


def food_pack_size(name, foods_cfg):
    """pack_size of the longest-matching foods.yaml rule for a food, or None."""
    low = (name or "").lower()
    best = None
    for rule in foods_cfg.get("rules", []):
        m = rule["match"].lower()
        if m in low and "pack_size" in rule and (best is None or len(m) > len(best["match"])):
            best = rule
    return best["pack_size"] if best else None


def approved_stores(choice):
    """Map the weekly approval choice to the set of approved stores.
    all -> both; newpi/target -> that one; skipped/None/unknown -> none."""
    return {"all": {"NewPi", "Target"}, "newpi": {"NewPi"},
            "target": {"Target"}}.get(choice, set())


def parse_category(name: str):
    """'<store>/<cadence>: <aisle>' -> (store, cadence, aisle). Lenient:
    missing cadence -> None; missing aisle -> 'Other'. Unparseable -> (None,...)."""
    if not name:
        return (None, None, "Other")
    store_part, sep, aisle = name.partition(":")
    aisle = aisle.strip() or "Other"
    store_part = store_part.strip()
    if "/" in store_part:
        store, cadence = store_part.split("/", 1)
        return (store.strip(), cadence.strip() or None, aisle)
    return (store_part or None, None, aisle)


def _food_of(entry):
    f = entry.get("food")
    return f if isinstance(f, dict) else {}


def _unit_of(entry):
    u = entry.get("unit")
    if isinstance(u, dict):
        return u.get("name")
    return u if isinstance(u, str) else None


def _parse_dt(value, default_tz):
    """ISO datetime (Tandoor returns offset-aware, e.g. ...-06:00 or Z) -> aware
    datetime; a naive value is read in `default_tz`. None if unparseable."""
    import datetime as _dt
    if not value:
        return None
    try:
        d = _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=default_tz)


def is_open(entry, now):
    """True if the entry is still to buy: not checked and not delayed into the
    future. Tandoor's list endpoint returns unchecked entries PLUS entries
    checked in the last `shopping_recent_days` (<= 14), and it does not filter
    `delay_until` at all (read from the 2.7 ShoppingListEntryViewSet), so both
    must be dropped here -- the dashboard page applies the same rule."""
    if entry.get("checked"):
        return False
    until = _parse_dt(entry.get("delay_until"), now.tzinfo)
    return not (until and until > now)


def _from_recipe(entry):
    """True if a recipe / meal plan added the entry (ShoppingListRecipe set)."""
    return bool(entry.get("list_recipe") or entry.get("list_recipe_data"))


def entry_mealplan_id(entry):
    """The meal-plan id an entry was added for (via its ShoppingListRecipe), or
    None for an ad-hoc entry (the page's add box, a manual Tandoor add)."""
    lr = entry.get("list_recipe_data")
    return lr.get("mealplan") if isinstance(lr, dict) else None


def build_store_lists(entries, config, run_mode="weekly", now=None, stats=None):
    """Return (store_lists, unrouted, skipped_onhand).

    store_lists: {store: StoreList} limited to the run's stores (weekly ->
    stores.weekly_stores, bulk -> stores.bulk_stores). unrouted: [Item] with no
    parseable store (owner must assign a category). skipped_onhand: count.
    Checked and future-delayed entries are not on any list (see is_open);
    `now` (aware datetime) defaults to the current UTC time. A recipe line whose
    food is on config `never_buy` is on no list; pass a dict as `stats` to get
    the count in stats["skipped_never_buy"]."""
    import datetime as _dt
    now = now or _dt.datetime.now(_dt.timezone.utc)
    stores_cfg = config["stores"]
    run_stores = set(stores_cfg["weekly_stores"] if run_mode == "weekly"
                     else stores_cfg["bulk_stores"])
    aisle_order = stores_cfg.get("aisles", {})
    excludes = config.get("exclude") or []
    never = config.get("never_buy") or []
    n_never = 0
    lists: dict[str, StoreList] = {}
    unrouted: list[Item] = []
    skipped = 0

    for e in entries:
        if not is_open(e, now):
            continue                           # bought (recently checked) or hidden until later
        food = _food_of(e)
        if is_excluded(food.get("name", ""), excludes):
            continue                           # dog/pet food etc. -- never ordered here
        if _from_recipe(e) and is_never_buy(food.get("name", ""), never):
            n_never += 1                       # owner never buys it (a hand add still lists)
            continue
        if food.get("food_onhand") and _from_recipe(e):
            skipped += 1                       # pantry/stock line a recipe pulled in
            continue
        # A recipe-less entry for an on-hand food is deliberate (the groceries
        # page's Pantry-tab "reorder", or the add box): list it like any line.
        cat = food.get("supermarket_category")
        cat_name = cat.get("name") if isinstance(cat, dict) else (cat or "")
        store, cadence, aisle = parse_category(cat_name)
        item = Item(entry_id=e.get("id"), food=food.get("name", "?"),
                    amount=e.get("amount"), unit=_unit_of(e),
                    aisle=aisle, store=store, cadence=cadence)
        if store is None:
            unrouted.append(item)
            continue
        if store not in run_stores:
            continue                           # e.g. a bulk item on the weekly run
        lists.setdefault(store, StoreList(store)).aisles.setdefault(aisle, []).append(item)

    # order aisles per store
    for store, sl in lists.items():
        order = aisle_order.get(store, [])
        def keyf(a):
            return (order.index(a) if a in order else len(order), a)
        sl.aisles = {a: sl.aisles[a] for a in sorted(sl.aisles, key=keyf)}
    if stats is not None:
        stats["skipped_never_buy"] = n_never
    return lists, unrouted, skipped


# ---- deep links -----------------------------------------------------------
import urllib.parse


def target_search_url(food: str) -> str:
    return "https://www.target.com/s?searchTerm=" + urllib.parse.quote(food)


def newpi_search_url(food: str) -> str:
    # shop.newpi.coop uses #! hash routing; keep it best-effort (degrades to a
    # plain list if the format drifts -- never hard-fail on a link).
    return "https://shop.newpi.coop/shop#!/?q=" + urllib.parse.quote(food)
