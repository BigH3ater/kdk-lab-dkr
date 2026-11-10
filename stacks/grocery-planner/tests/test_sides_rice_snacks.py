"""Owner fixes 2026-11-10: no veg side when the main already holds a vegetable,
a brown-rice side for mains served over rice, the full lunch-prep count, and the
Snack meal type. Fixtures are trimmed copies of the live W46 Tandoor recipes
(ids and food names as Tandoor holds them)."""
import datetime

import pytest

from grocery import generator as G
from grocery import run as R
from tests.test_generator import SUNDAY, make_pool

RICE_NAME = "Brown Rice (microwave packet)"


def _ing(food, text=None):
    return {"food": {"id": abs(hash(food)) % 10_000, "name": food}, "amount": 1,
            "unit": None, "is_header": False, "note": "", "original_text": text or food}


def _recipe(rid, name, foods, steps=(), keywords=()):
    return {"id": rid, "name": name, "servings": 4,
            "keywords": [{"name": k} for k in keywords],
            "steps": [{"instruction": s, "ingredients": []} for s in steps]
            + [{"instruction": "", "ingredients": [f if isinstance(f, dict) else _ing(f)
                                                   for f in foods]}]}


STIR_FRY_164 = _recipe(164, "Chicken and Broccoli Stir Fry Recipe",
                       ["chicken broth", "soy sauce", "ginger", "garlic", "chicken breast",
                        "broccoli", "onion", "mushrooms"],
                       steps=["Combine and Serve - Return chicken to the pan and stir another "
                              "30 seconds. Add more soy sauce to taste if needed and serve "
                              "over hot rice."],
                       keywords=("poultry", "fall"))
AHI_65 = _recipe(65, "Seared Ahi Tuna with Crispy Sesame Green Beans",
                 ["tuna", "cauliflower florets", "carrots", "garlic", "green beans",
                  _ing("red cabbage", "1/4 cup red cabbage (thinly sliced, optional)")],
                 keywords=("fish", "tuna", "fall"))
CHILI_61 = _recipe(61, "Beef Chili Recipe",
                   ["ground beef", "onion", "carrots", "bell pepper", "garlic",
                    "diced tomatoes", "kidney beans", "beef broth"],
                   keywords=("ground-beef", "tex-mex", "fall"))
BEEF_BROC_57 = _recipe(57, "Ground Beef and Broccoli Stir Fry",
                       ["soy sauce", "ground beef", "onion", "broccoli", "green onions",
                        _ing("cooked rice", "cooked rice (or cauliflower rice, for serving "
                                            "(optional))")],
                       keywords=("ground-beef", "asian", "fall"))
FILET_46 = _recipe(46, "Filet Mignon Recipe in Mushroom Sauce (VIDEO)",
                   ["filet mignon steaks", "mushrooms", "shallot", "garlic", "red wine"],
                   keywords=("steak", "fall"))
POT_ROAST_54 = _recipe(54, "Mississippi Pot Roast", ["chuck roast", "pepperoncini"],
                       steps=["Serve over mashed potatoes, egg noodles, or rice."],
                       keywords=("roast", "fall"))
TERIYAKI_52 = _recipe(52, "Teriyaki Grilled Chicken and Veggie Rice Bowls",
                      ["rice vinegar", "white or brown rice", "chicken breast", "broccoli"],
                      steps=["Divide the rice between 4 bowls."],
                      keywords=("lunch-prep", "fall"))
KOREAN_246 = _recipe(246, "Korean Ground Beef and Rice Bowls",
                     ["ground beef", "rice vinegar", "soy sauce"],
                     steps=["Cook rice as per package instructions.",
                            "Fluff rice with a fork and serve Korean ground beef over it."],
                     keywords=("ground-beef", "asian", "fall", "beef-lunch"))
CARROTS_47 = _recipe(47, "Roasted Carrots", ["carrots", "olive oil"],
                     keywords=("veg-side", "fall"))


# ---- 1. no redundant veg side ----------------------------------------------
@pytest.mark.parametrize("recipe, want", [
    (STIR_FRY_164, ["broccoli"]),                              # W46: got green beans
    (AHI_65, ["carrots", "cauliflower florets", "green beans"]),  # W46: got carrots
    (CHILI_61, ["bell pepper", "carrots"]),                    # W46: got carrots
    (FILET_46, []),                                            # mushrooms/shallot: keeps a side
    (POT_ROAST_54, []),
])
def test_veg_foods_from_ingredients(cfg, recipe, want):
    assert R.veg_foods(recipe, cfg["sides"]["vegetables"]) == want


def test_veg_match_is_whole_word(cfg):
    r = _recipe(1, "x", ["cornstarch", "peanut butter", "chickpeas", "baby carrots", "frozen peas"])
    assert R.veg_foods(r, cfg["sides"]["vegetables"] + ["corn"]) == ["baby carrots", "frozen peas"]


def test_build_pool_sets_veg_and_rice(cfg):
    pool = {p["id"]: p for p in R.build_pool(
        [STIR_FRY_164, AHI_65, CHILI_61, BEEF_BROC_57, FILET_46, CARROTS_47], "fall", cfg)}
    assert pool[164]["veg_foods"] == ["broccoli"] and pool[164]["rice"] is True
    assert pool[57]["rice"] is True
    assert pool[46]["veg_foods"] == [] and pool[46]["rice"] is False
    assert pool[47]["kind"] == "side" and pool[47]["veg_foods"] == [] and pool[47]["rice"] is False
    # without cfg (old callers) nothing changes: every main keeps its veg side
    assert R.build_pool([STIR_FRY_164], "fall")[0]["veg_foods"] == []


def _with(pool, **by_kind):
    """Give the first recipe of each kind the given fields."""
    done = set()
    for p in pool:
        if p["kind"] in by_kind and p["kind"] not in done:
            p.update(by_kind[p["kind"]])
            done.add(p["kind"])
    return pool


def test_main_with_veg_gets_no_side(cfg):
    pool = make_pool()
    for p in pool:                                  # every chicken dish has broccoli
        if p["kind"] == "chicken":
            p["veg_foods"] = ["broccoli"]
    entries = G.generate_week(pool, SUNDAY, cfg)
    kind = {p["id"]: p for p in pool}
    for d in range(7):
        date = SUNDAY + datetime.timedelta(days=d)
        main = [e for e in entries if e.date == date and e.role == "dinner-main"][0]
        sides = [e for e in entries if e.date == date and e.role == "side"]
        assert len(sides) == (0 if kind[main.recipe_id].get("veg_foods") else 1), main.recipe_name
    s = G.plan_summary(entries)
    chicken_mains = sum(1 for e in entries if e.role == "dinner-main"
                        and kind[e.recipe_id]["kind"] == "chicken")
    assert chicken_mains >= 2 and s["sides"] == 7 - chicken_mains


def test_skip_rule_can_be_switched_off(cfg):
    cfg["sides"]["skip_when_main_has_veg"] = False
    pool = make_pool()
    for p in pool:
        p["veg_foods"] = ["broccoli"] if p["kind"] != "side" else []
    assert G.plan_summary(G.generate_week(pool, SUNDAY, cfg))["sides"] == 7


# ---- 2. rice side -----------------------------------------------------------
@pytest.mark.parametrize("recipe, want", [
    (STIR_FRY_164, True),    # step: "serve over hot rice"
    (BEEF_BROC_57, True),    # ingredient: cooked rice (for serving)
    (KOREAN_246, True),      # steps cook/serve rice, no rice ingredient listed
    (TERIYAKI_52, False),    # lists its own raw rice: cooks it, no packet
    (POT_ROAST_54, False),   # "potatoes, egg noodles, or rice" is a choice
    (CHILI_61, False),
    (FILET_46, False),
])
def test_wants_rice_detection(cfg, recipe, want):
    assert R.wants_rice(recipe, cfg["rice"]) is want


def test_wants_rice_keywords(cfg):
    tagged = _recipe(1, "x", ["chicken breast"], keywords=("serve-with-rice",))
    opted_out = dict(STIR_FRY_164, keywords=[{"name": "no-rice-side"}])
    vinegar = _recipe(2, "y", ["rice vinegar"], steps=["Whisk the rice vinegar in."])
    assert R.wants_rice(tagged, cfg["rice"]) is True
    assert R.wants_rice(opted_out, cfg["rice"]) is False
    assert R.wants_rice(vinegar, cfg["rice"]) is False
    assert R.wants_rice(STIR_FRY_164, dict(cfg["rice"], enabled=False)) is False


def _rice_pool():
    pool = make_pool()
    pool.append({"id": 900, "name": RICE_NAME, "kind": "staple", "season": None,
                 "servings": 2})
    for p in pool:
        if p["kind"] in ("chicken", "lunch"):
            p["rice"] = True
    return pool


def test_rice_side_planned_for_rice_mains(cfg):
    pool = _rice_pool()
    entries = G.generate_week(pool, SUNDAY, cfg)
    by_id = {p["id"]: p for p in pool}
    rice = [e for e in entries if e.role == "rice"]
    dinner_rice = [e for e in rice if e.meal_type == "dinner"]
    rice_mains = [e for e in entries if e.role == "dinner-main" and by_id[e.recipe_id].get("rice")]
    assert {e.date for e in dinner_rice} == {e.date for e in rice_mains}
    assert all(e.recipe_id == 900 and e.servings == 2 for e in dinner_rice)   # 1 packet
    assert all(e.note.endswith("] side") for e in rice)                       # note vocabulary kept
    lunch_rice = [e for e in rice if e.meal_type == "lunch"]
    assert len(lunch_rice) == 3 and all(e.servings == 4 for e in lunch_rice)  # 2 packets per batch
    assert G.plan_summary(entries)["rice"] == len(rice)


def test_no_rice_entry_when_recipe_missing(cfg):
    pool = [p for p in _rice_pool() if p["name"] != RICE_NAME]
    assert not [e for e in G.generate_week(pool, SUNDAY, cfg) if e.role == "rice"]


def test_rice_recipe_in_fixed_yaml():
    from grocery import seed as S
    _, foods, fixed, _ = S.load_configs()
    rec = [r for r in fixed["recipes"] if r["name"] == RICE_NAME][0]
    assert rec["servings"] == 2
    assert rec["ingredients"] == [{"food": "brown rice microwave packet", "amount": 1,
                                   "unit": "packet"}]
    assert rec["instruction"].startswith("Microwave the packet")
    cfg = S.load_configs()[0]
    assert S.route_food("brown rice microwave packet", foods, cfg["stores"]) == \
        "Target/weekly: Pantry & Grains"


# ---- 3. lunch prep count ----------------------------------------------------
W46_NOTES = (["[gp:2026-W46] dinner", "[gp:2026-W46] side"] * 7
             + ["[gp:2026-W46] lunch"] * 2
             + ["[gp:2026-W46] breakfast", "[gp:2026-W46] snack", "[gp:2026-W46] snack"])


def test_w46_shortfall_reported(cfg):
    """W46 was written 2026-11-01 13:13 CT (Vikunja task 90) by the build that
    still had recipes_per_week: 2; the 3-batch rule merged that night (#311) and
    every later build skipped the planned week. The skip now names the gap."""
    assert G.missing_counts(W46_NOTES, cfg) == {"lunch": (2, 3)}
    full = W46_NOTES + ["[gp:2026-W46] lunch"]
    assert G.missing_counts(full, cfg) == {}


@pytest.mark.parametrize("offset_weeks", [0, 1])     # both parities (A has the beef lunch)
def test_three_lunch_batches_of_four(cfg, offset_weeks):
    pool = make_pool()
    # The beef lunch also carries the lunch-prep keyword: it must not be planned twice.
    pool.append({"id": 500, "name": "Ground Beef and Broccoli Stir Fry", "kind": "lunch",
                 "season": "spring", "working_time": 20, "ingredient_count": 6,
                 "tags": ["beef-lunch"]})
    start = SUNDAY + datetime.timedelta(weeks=offset_weeks)
    lunches = [e for e in G.generate_week(pool, start, cfg) if e.role == "lunch"]
    assert len(lunches) == cfg["lunches"]["recipes_per_week"] == 3
    assert len({e.recipe_id for e in lunches}) == 3
    assert sum(e.servings for e in lunches) == 12          # 6 lunch days x 2 people


# ---- 4. Snack meal type -----------------------------------------------------
def test_snacks_filed_as_snack(cfg):
    entries = G.generate_week(make_pool(), SUNDAY, cfg)
    snacks = [e for e in entries if e.role == "snack"]
    assert len(snacks) == 2 and {e.meal_type for e in snacks} == {"snack"}
    assert [e.meal_type for e in entries if e.role == "breakfast"] == ["breakfast"]
    assert cfg["tandoor"]["meal_types"]["snack"] == "Snack"


class FakeTandoor:
    def __init__(self, meal_types=("Dinner", "Lunch", "Breakfast"), recipes=()):
        self.mt = {n: {"id": i + 1, "name": n} for i, n in enumerate(meal_types)}
        self.kws = {"fall": {"id": 1, "name": "fall"}}
        self.recipes = {r["id"]: r for r in recipes}
        self.posts, self.patches = [], []

    def meal_types(self):
        return dict(self.mt)

    def keywords(self):
        return dict(self.kws)

    def ensure_keyword(self, name):
        self.kws.setdefault(name, {"id": len(self.kws) + 1, "name": name})
        return self.kws[name]

    def supermarket_categories(self):
        return {}

    def ensure_supermarket_category(self, name):
        return {"id": 7, "name": name}

    def get_all(self, path, **kw):
        assert path == "/api/recipe/"
        q = (kw.get("query") or "").lower()
        return [r for r in self.recipes.values() if q in r["name"].lower()]

    def get(self, path):
        return self.recipes[int(path.rstrip("/").split("/")[-1])]

    def post(self, path, body):
        self.posts.append((path, body))
        if path == "/api/meal-type/":
            self.mt[body["name"]] = {"id": len(self.mt) + 1, "name": body["name"]}
            return self.mt[body["name"]]
        if path == "/api/recipe/":
            rid = 1000 + len(self.recipes)
            ings = [dict(i, food=dict(i["food"], id=rid)) for i in body["steps"][0]["ingredients"]]
            self.recipes[rid] = dict(body, id=rid, steps=[dict(body["steps"][0], ingredients=ings)])
            return {"id": rid}
        return {}

    def patch(self, path, body):
        self.patches.append((path, body))


def test_ensure_setup_creates_once(cfg):
    t = FakeTandoor()
    created = R.ensure_setup(t, cfg)
    assert created == ["meal-type:Snack", "keyword:serve-with-rice", "keyword:no-rice-side",
                       f"recipe:{RICE_NAME}"]
    rice = [b for p, b in t.posts if p == "/api/recipe/"][0]
    assert rice["name"] == RICE_NAME and rice["servings"] == 2
    assert rice["steps"][0]["instruction"].startswith("Microwave the packet per its directions")
    assert rice["steps"][0]["ingredients"][0]["unit"] == {"name": "packet"}
    assert [k["name"] for k in rice["keywords"]] == ["rice-side"]
    # the packet food is routed to the Target pantry aisle, so it reaches the list
    assert any(b.get("supermarket_category", {}).get("name") == "Target/weekly: Pantry & Grains"
               for _, b in t.patches)
    n_posts = len(t.posts)
    assert R.ensure_setup(t, cfg) == []                 # idempotent: second run is a no-op
    assert len(t.posts) == n_posts
