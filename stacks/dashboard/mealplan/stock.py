"""Pantry + freezer-stock rules for the groceries page (Pantry and Thaw tabs).

Dependency-light (yaml only, no Flask) so it unit-tests on its own, like
newpi.py. It reads three files from the grocery-planner stack, all mounted
read-only into the sidecar (stacks/dashboard/compose.yaml):

  /app/foods.yaml          store routing (newpi.py already reads it)
  /app/cleanup.yaml        `pantry:` -- the foods kept permanently on hand
  /app/planner-config.yaml `stores:` (stock + bulk stores, default store) and
                           `thaw:` (freezer-to-fridge lead time per food kind)

Routing mirrors grocery.seed.route_food (frozen veg -> Target Frozen, else the
longest foods.yaml match, else the shelf-stable default) so the page groups a
food exactly where the weekly run would put it. No prices, no secrets.
"""
from __future__ import annotations

import datetime
import os
import re
from typing import Optional

import yaml

import newpi

CLEANUP_FILE = os.environ.get("PANTRY_CLEANUP_FILE", "/app/cleanup.yaml")
CONFIG_FILE = os.environ.get("PLANNER_CONFIG_FILE", "/app/planner-config.yaml")

_cleanup: Optional[dict] = None
_config: Optional[dict] = None


def _load(path: str) -> dict:
    try:
        with open(path) as fh:
            data = yaml.safe_load(fh) or {}
        return data if isinstance(data, dict) else {}
    except (OSError, yaml.YAMLError):
        return {}


def cleanup_cfg() -> dict:
    global _cleanup
    if _cleanup is None:
        _cleanup = _load(CLEANUP_FILE)
    return _cleanup


def planner_cfg() -> dict:
    global _config
    if _config is None:
        _config = _load(CONFIG_FILE)
    return _config


def pantry_names(cleanup: Optional[dict] = None) -> list[str]:
    """cleanup.yaml `pantry:` in file order, lower-cased, de-duplicated."""
    cleanup = cleanup_cfg() if cleanup is None else cleanup
    out: list[str] = []
    for n in cleanup.get("pantry") or []:
        low = str(n).strip().lower()
        if low and low not in out:
            out.append(low)
    return out


def _stores(config: Optional[dict]) -> dict:
    config = planner_cfg() if config is None else config
    return config.get("stores") or {}


def route_for(name: str, foods_cfg: Optional[dict] = None,
              config: Optional[dict] = None) -> str:
    """Supermarket-category name for a food, like grocery.seed.route_food."""
    low = (name or "").lower()
    if "frozen" in low and "salmon" not in low and "tuna" not in low:
        return "Target/weekly: Frozen"
    rule = newpi.rule_for(name, foods_cfg)
    if rule and rule.get("category"):
        return str(rule["category"])
    return f"{_stores(config).get('default_shelf_stable', 'Target')}/weekly: Other"


def split_cat(cat: str) -> tuple[str, str]:
    """'<store>/<cadence>: <aisle>' -> (store, aisle)."""
    head, _, aisle = (cat or "").partition(":")
    return head.split("/", 1)[0].strip() or "Other", aisle.strip() or "Other"


def stock_stores(config: Optional[dict] = None) -> list[str]:
    s = _stores(config)
    return list(s.get("stock_stores") or s.get("freezer_stock_stores") or ["Amana"])


def is_stock(cat: str, config: Optional[dict] = None) -> bool:
    """Stock store (Amana, O'Connell, Penzeys): bought ahead, kept on hand.
    Same test as grocery.seed.is_freezer_stock."""
    return split_cat(cat)[0] in stock_stores(config)


def is_freezer(cat: str, config: Optional[dict] = None) -> bool:
    """Freezer stock = a stock or bulk store's freezer aisle (Amana "Freezer
    Beef", O'Connell "Freezer", Costco "Freezer"). Penzeys is stock but not
    frozen, so it never shows on the Thaw tab."""
    store, aisle = split_cat(cat)
    s = _stores(config)
    stores = set(stock_stores(config)) | set(s.get("bulk_stores") or [])
    return store in stores and "freezer" in aisle.lower()


def is_pantry_or_stock(name: str, foods_cfg: Optional[dict] = None,
                       config: Optional[dict] = None,
                       cleanup: Optional[dict] = None) -> bool:
    low = (name or "").strip().lower()
    return low in pantry_names(cleanup) or is_stock(route_for(name, foods_cfg, config), config)


def catalog(foods_cfg: Optional[dict] = None, config: Optional[dict] = None,
            cleanup: Optional[dict] = None) -> dict[str, dict[str, list[str]]]:
    """{store: {aisle: [food]}}: the cleanup.yaml pantry list plus every
    foods.yaml rule routed to a stock store, grouped where route_for puts them.
    Stores follow config `stores.order`, aisles `stores.aisles`."""
    foods_cfg = newpi.foods() if foods_cfg is None else foods_cfg
    names = pantry_names(cleanup)
    for rule in foods_cfg.get("rules", []):
        m = str(rule.get("match", "")).strip().lower()
        if m and m not in names and is_stock(str(rule.get("category", "")), config):
            names.append(m)
    out: dict[str, dict[str, list[str]]] = {}
    for n in names:
        store, aisle = split_cat(route_for(n, foods_cfg, config))
        out.setdefault(store, {}).setdefault(aisle, []).append(n)
    s = _stores(config)
    order = list(s.get("order") or [])
    aisles = s.get("aisles") or {}

    def _idx(seq, x):
        return (seq.index(x) if x in seq else len(seq), x)

    return {st: {a: sorted(out[st][a]) for a in sorted(out[st], key=lambda a: _idx(list(aisles.get(st) or []), a))}
            for st in sorted(out, key=lambda st: _idx(order, st))}


# ---- thaw lead times --------------------------------------------------------
_DUR = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([hd]?)\s*$", re.I)
# When a meal of each type is eaten; the move-by time counts back from here.
MEAL_HOUR = {"breakfast": 8, "lunch": 12, "dinner": 18}


def _hours(v) -> float:
    m = _DUR.match(str(v))
    if not m:
        return 24.0
    n = float(m.group(1))
    return n * 24 if m.group(2).lower() == "d" else n


def thaw_hours(food: str, config: Optional[dict] = None) -> float:
    """Lead time for a food: the `thaw:` key that is the LONGEST substring of
    the food name, else `default` (24 h when the table is missing)."""
    config = planner_cfg() if config is None else config
    table = config.get("thaw") or {}
    low = (food or "").lower()
    best = None
    for k in table:
        kl = str(k).lower()
        if kl != "default" and kl in low and (best is None or len(kl) > len(best)):
            best = kl
    if best is not None:
        return _hours({str(k).lower(): v for k, v in table.items()}[best])
    return _hours(table.get("default", "24h"))


def move_by(meal_date: datetime.date, meal_type: str, food: str,
            config: Optional[dict] = None) -> datetime.datetime:
    """Naive local datetime the food must go from freezer to fridge."""
    hour = MEAL_HOUR.get((meal_type or "").strip().lower(), 18)
    at = datetime.datetime.combine(meal_date, datetime.time(hour))
    return at - datetime.timedelta(hours=thaw_hours(food, config))


def plan_week_start(today: datetime.date, config: Optional[dict] = None) -> datetime.date:
    """First day of the plan week holding `today` (config cadence, Sunday)."""
    config = planner_cfg() if config is None else config
    wd = int((config.get("cadence") or {}).get("plan_start_weekday", 6))
    return today - datetime.timedelta(days=(today.weekday() - wd) % 7)
