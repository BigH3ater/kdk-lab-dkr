"""New Pi product-link lookup for the groceries page.

The weekly grocery planner routes each food to a store with the SAME longest-match
rule table the seed uses (stacks/grocery-planner/recipes/foods.yaml, read by
grocery.seed.route_food). A New Pi food's rule may carry a `newpi_url` (a link to
the item on https://market.newpi.coop) so the New Pi tab can show each line as a
tap-to-order link.

This module is a dependency-light mirror of that routing (yaml only, no Flask), so
it can be unit-tested on its own. app.py mounts foods.yaml read-only at
/app/foods.yaml (see stacks/dashboard/compose.yaml) and calls url_for() per item.

Keeping the lookup here, reading the SAME foods.yaml, means the store rules stay in
ONE place: adding a New Pi link is a one-line edit to foods.yaml, no code change.
"""
from __future__ import annotations

import html
import os
from typing import Optional

import yaml

# Mounted read-only from the grocery-planner stack's recipes/foods.yaml (the single
# source of truth for store routing). Overridable for tests.
FOODS_FILE = os.environ.get("NEWPI_FOODS_FILE", "/app/foods.yaml")

_cache: Optional[dict] = None


def load_foods(path: str = FOODS_FILE) -> dict:
    """Parse foods.yaml -> {"rules": [...]}. Missing/broken file -> no rules (the
    page still renders; every New Pi line just shows the no-link marker)."""
    try:
        with open(path) as fh:
            data = yaml.safe_load(fh) or {}
        if isinstance(data, dict) and isinstance(data.get("rules"), list):
            return data
    except (OSError, yaml.YAMLError):
        pass
    return {"rules": []}


def foods(path: str = FOODS_FILE) -> dict:
    """Process-cached load (foods.yaml is read-only and changes only on redeploy)."""
    global _cache
    if _cache is None:
        _cache = load_foods(path)
    return _cache


def url_for(name: str, foods_cfg: Optional[dict] = None) -> Optional[str]:
    """The New Pi product URL for a food name, or None.

    Mirrors grocery.seed.route_food: the food is matched to the LONGEST rule whose
    `match` is a substring of the (lower-cased) name, so the link comes from the
    very rule that routed the food to New Pi. The frozen-veg special case (which
    route_food sends to Target) carries no New Pi link.
    """
    if foods_cfg is None:
        foods_cfg = foods()
    low = (name or "").lower()
    # Frozen veg -> Target in route_food; fish keeps its explicit routing.
    if "frozen" in low and "salmon" not in low and "tuna" not in low:
        return None
    best = None
    for rule in foods_cfg.get("rules", []):
        m = str(rule.get("match", "")).lower()
        if m and m in low and (best is None or len(m) > len(str(best.get("match", "")))):
            best = rule
    if best:
        url = best.get("newpi_url")
        if url:
            return str(url)
    return None


def name_cell_html(food: str, url: Optional[str]) -> str:
    """Inner HTML for a New Pi item's name: a tap-to-order link (new tab) when the
    food has a product URL, else the plain name plus a small "no link yet" marker.
    Both branches HTML-escape their inputs (the page is user-facing)."""
    safe = html.escape(food or "")
    if url:
        return (f'<a class="np" href="{html.escape(url)}" target="_blank" '
                f'rel="noopener">{safe}<span class="ext" aria-hidden="true">↗</span></a>')
    return f'{safe}<span class="nolink" title="No New Pi link yet" aria-hidden="true">+</span>'
