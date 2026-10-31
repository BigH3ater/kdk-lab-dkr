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


def build_store_lists(entries, config, run_mode="weekly"):
    """Return (store_lists, unrouted, skipped_onhand).

    store_lists: {store: StoreList} limited to the run's stores (weekly ->
    stores.weekly_stores, bulk -> stores.bulk_stores). unrouted: [Item] with no
    parseable store (owner must assign a category). skipped_onhand: count."""
    stores_cfg = config["stores"]
    run_stores = set(stores_cfg["weekly_stores"] if run_mode == "weekly"
                     else stores_cfg["bulk_stores"])
    aisle_order = stores_cfg.get("aisles", {})
    lists: dict[str, StoreList] = {}
    unrouted: list[Item] = []
    skipped = 0

    for e in entries:
        food = _food_of(e)
        if food.get("food_onhand"):
            skipped += 1
            continue
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
    return lists, unrouted, skipped


# ---- deep links -----------------------------------------------------------
import urllib.parse


def target_search_url(food: str) -> str:
    return "https://www.target.com/s?searchTerm=" + urllib.parse.quote(food)


def newpi_search_url(food: str) -> str:
    # shop.newpi.coop uses #! hash routing; keep it best-effort (degrades to a
    # plain list if the format drifts -- never hard-fail on a link).
    return "https://shop.newpi.coop/shop#!/?q=" + urllib.parse.quote(food)
