"""Optional-ingredient review (owner 2026-11-11): classification of the live
cases named in the brief, and --apply backs up before it deletes."""
import json
import os

from grocery import optional as O


def _ing(iid, food, note="", text="", header=False):
    return {"id": iid, "food": {"name": food}, "note": note, "original_text": text,
            "is_header": header}


def _rec(rid, name, *ings):
    return {"id": rid, "name": name, "steps": [{"ingredients": list(ings)}]}


RECIPES = [
    _rec(7, "Seared Tuna Steaks",
         _ing(70, "green onions, toasted sesame seeds, and lime wedges (for serving",
              "optional)"),
         _ing(71, "black pepper", "to taste"),
         _ing(72, "tuna steaks", "about 4 oz. each")),
    _rec(50, "Fall Chicken Sheet Pan Dinner",
         _ing(500, "brussels sprouts", "halved", "2 cups brussels sprouts (halved)")),
    _rec(65, "Seared Ahi Tuna with Crispy Sesame Green Beans",
         _ing(650, "red cabbage", "thinly sliced, optional",
              "¼ cup red cabbage (thinly sliced, optional)"),
         _ing(651, "carrots", "optional", "¼ cup grated carrots (optional)")),
    _rec(120, "Stuffed Peppers",
         _ing(1200, "parsley", "plus more for garnish",
              "3 Tbsp minced fresh parsley, (plus more for garnish)")),
    _rec(57, "Ground Beef and Broccoli Stir Fry",
         _ing(570, "cooked rice", "or cauliflower rice, for serving (optional)")),
    _rec(142, "Roasted Cabbage Steaks", _ing(1420, "cabbage", "", "1 head of cabbage")),
    _rec(114, "Caprese Salad",
         _ing(1140, "Balsamic glaze", "", "1-2 Tbsp Balsamic glaze (or added to taste (optional))")),
    _rec(1, "Header only", _ing(10, "FOR THE SAUCE", header=True)),
]


def _rows(cfg):
    return {r["ingredient_id"]: r for r in O.review(RECIPES, cfg, pantry=["black pepper"])}


def test_known_cases(cfg):
    rows = _rows(cfg)
    assert rows[70]["action"] == "remove"            # recipe 7's junk garnish line
    assert rows[500]["action"] == "remove" and rows[500]["reason"] == "never_buy"
    assert rows[650]["action"] == "remove" and rows[650]["reason"] == "never_buy"
    assert rows[651]["action"] == "remove"
    assert rows[71]["action"] == "to-taste" and rows[71]["reason"] == "pantry: yes"
    assert 72 not in rows and 10 not in rows         # plain line, header


def test_ambiguous_lines_are_review_only(cfg):
    rows = _rows(cfg)
    assert rows[1200]["action"] == "review"          # some parsley is cooked
    assert rows[570]["action"] == "review"           # rice side detection reads it
    assert rows[1420]["action"] == "review"          # cabbage IS the dish
    assert rows[1140]["action"] == "review"          # optional AND to taste


def test_render_groups_by_action(cfg):
    text = O.render(O.review(RECIPES, cfg))
    assert text.index("== remove: 4") < text.index("== review: 4") < text.index("== to-taste: 1")


class FakeT:
    def __init__(self):
        self.deleted = []

    def delete(self, path):
        self.deleted.append(path)


def test_apply_backs_up_then_deletes_only_remove(cfg, tmp_path):
    rows = O.review(RECIPES, cfg)
    t = FakeT()
    by_id = {r["id"]: r for r in RECIPES}
    done = O.apply(t, rows, by_id, str(tmp_path / "bk"))
    assert sorted(done) == [70, 500, 650, 651]
    assert t.deleted == [f"/api/ingredient/{i}/" for i in (70, 500, 650, 651)]
    saved = sorted(os.listdir(tmp_path / "bk"))
    assert saved == ["50.json", "65.json", "7.json"]
    assert json.load(open(tmp_path / "bk" / "65.json"))["name"].startswith("Seared Ahi")
