"""New Pi product-link lookup (mealplan sidecar).

Covers the two load-bearing bits the groceries page relies on:
  * url_for -- the SAME longest-match routing the seed uses (grocery.seed.route_food),
    so a line's link comes from the rule that put the food at New Pi.
  * name_cell_html -- the New Pi tab renders a tap-to-order link when a food has a
    url, and a plain name + "no link" marker when it does not.
"""
import os

import newpi

# A small routing table in the shape of recipes/foods.yaml. "spinach" and "pepper"
# carry New Pi links; "crushed tomato" is a LONGER Target rule with no link.
FOODS = {"rules": [
    {"match": "spinach", "category": "NewPi/weekly: Produce",
     "newpi_url": "https://market.newpi.coop/product/39574"},
    {"match": "pepper", "category": "NewPi/weekly: Produce",
     "newpi_url": "https://market.newpi.coop/product/12345"},
    {"match": "tomato", "category": "NewPi/weekly: Produce",
     "newpi_url": "https://market.newpi.coop/product/222"},
    {"match": "crushed tomato", "category": "Target/weekly: Canned"},
    {"match": "kale", "category": "NewPi/weekly: Produce"},   # NewPi, but no link yet
    {"match": "broccoli", "category": "NewPi/weekly: Produce"},
]}


def test_url_for_matches_substring():
    assert newpi.url_for("spinach", FOODS) == "https://market.newpi.coop/product/39574"
    # "baby spinach" still matches the "spinach" rule.
    assert newpi.url_for("baby spinach", FOODS) == "https://market.newpi.coop/product/39574"


def test_url_for_longest_match_wins():
    # "crushed tomato" is a longer rule than "tomato" and routes to Target, so the
    # tomato New Pi link must NOT leak onto it (mirror of route_food).
    assert newpi.url_for("crushed tomatoes", FOODS) is None
    # the plain "tomato" line still links
    assert newpi.url_for("roma tomato", FOODS) == "https://market.newpi.coop/product/222"


def test_url_for_newpi_food_without_link_is_none():
    assert newpi.url_for("kale", FOODS) is None
    assert newpi.url_for("broccoli", FOODS) is None


def test_url_for_frozen_veg_has_no_link():
    # route_food sends frozen veg to Target/Frozen; frozen fish keeps its routing.
    assert newpi.url_for("frozen broccoli", FOODS) is None


def test_url_for_unmatched_is_none():
    assert newpi.url_for("flux capacitor", FOODS) is None


def test_name_cell_html_links_when_url():
    html = newpi.name_cell_html("spinach", "https://market.newpi.coop/product/39574")
    assert 'href="https://market.newpi.coop/product/39574"' in html
    assert 'target="_blank"' in html and 'rel="noopener"' in html
    assert ">spinach<" in html
    assert "nolink" not in html


def test_name_cell_html_marker_when_no_url():
    html = newpi.name_cell_html("kale", None)
    assert "<a " not in html
    assert "nolink" in html and "kale" in html


def test_name_cell_html_escapes():
    html = newpi.name_cell_html("salt & pepper", None)
    assert "salt &amp; pepper" in html
    html2 = newpi.name_cell_html("x", 'https://market.newpi.coop/product/1"onerror=1')
    assert '"onerror=1' not in html2 and "&quot;" in html2


def test_load_foods_missing_file_is_empty():
    assert newpi.load_foods("/no/such/foods.yaml") == {"rules": []}


def test_load_real_foods_yaml_parses():
    # The mounted foods.yaml (grocery-planner) must parse into rules.
    # tests -> mealplan -> dashboard -> stacks, then into grocery-planner.
    stacks = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    foods_yaml = os.path.join(stacks, "grocery-planner", "recipes", "foods.yaml")
    data = newpi.load_foods(foods_yaml)
    assert isinstance(data.get("rules"), list) and len(data["rules"]) > 50
