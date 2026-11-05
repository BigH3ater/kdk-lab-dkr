"""Schema guard for the optional `newpi_url` on foods.yaml rules.

The groceries page turns a New Pi line into a tap-to-order link from the rule's
`newpi_url`. These checks keep a future link from being added wrong: it must be a
real market.newpi.coop /product/<id> URL and it must sit on a rule that actually
routes to New Pi (otherwise the link would never show, or point the wrong store).
"""
import os
import re

import yaml

from grocery.seed import route_food

STACK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PRODUCT_URL = re.compile(r"^https://market\.newpi\.coop/product/[0-9A-Za-z]+$")


def _foods():
    return yaml.safe_load(open(os.path.join(STACK, "recipes", "foods.yaml")))


def test_newpi_url_shape_and_store(cfg):
    foods = _foods()
    for rule in foods["rules"]:
        url = rule.get("newpi_url")
        if url is None:
            continue
        match = rule["match"]
        assert _PRODUCT_URL.match(url), f"{match!r}: bad newpi_url {url!r}"
        # The rule carrying the link must itself route to New Pi, and the same
        # longest-match that routes the food must pick THIS rule.
        assert rule["category"].startswith("NewPi/"), f"{match!r}: newpi_url on non-NewPi rule"
        assert route_food(match, foods, cfg["stores"]).startswith("NewPi/"), match


def test_newpi_note_is_short_string():
    for rule in _foods()["rules"]:
        note = rule.get("newpi_note")
        if note is not None:
            assert isinstance(note, str) and len(note) <= 80, rule["match"]


def test_buy_map_shape():
    """`buy:` (buy-unit conversion on the groceries page) needs a store `unit`
    and positive numbers for every recipe unit it converts."""
    for rule in _foods()["rules"]:
        buy = rule.get("buy")
        if buy is None:
            continue
        match = rule["match"]
        assert isinstance(buy, dict), f"{match!r}: buy must be a map"
        assert isinstance(buy.get("unit"), str) and buy["unit"], f"{match!r}: buy needs a unit"
        for key, val in buy.items():
            if key == "unit":
                continue
            assert isinstance(val, (int, float)) and not isinstance(val, bool), (match, key)
            assert val > 0, (match, key)
