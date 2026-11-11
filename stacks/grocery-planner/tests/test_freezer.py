"""Freezer inventory: mapping, unit conversion, the planner's stock rules
(never short, fish parity yields, most stock first), the reservation write
(decrement + .bak + idempotent) and the missing-file fallback. Offline."""
import datetime
import json
import os

import pytest

from grocery import freezer as F
from grocery import generator as G
from grocery import run as R
from tests.test_generator import make_pool, SUNDAY

STACK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED = os.path.join(STACK, "freezer-seed.json")


def _seed():
    data, err = F.load(SEED)
    assert err is None
    return data


def _line(cut, amount, unit="", base=2):
    return {"cut": cut, "food": cut, "amount": amount, "unit": unit, "base": base}


# ---- seed + config -----------------------------------------------------------
def test_seed_loads_and_every_map_and_order_cut_exists(cfg):
    data = _seed()
    ids = [c["id"] for c in data["cuts"]]
    assert len(ids) == len(set(ids))
    assert set(cfg["freezer"]["map"].values()) <= set(ids)
    for name, order in cfg["standing_orders"].items():
        assert set(order["items"]) <= set(ids), name
    by = {c["id"]: c for c in data["cuts"]}
    assert by["filet-mignon"]["count"] == 2 and by["ribeye"]["count"] == 6
    assert by["ground-beef"] == {**by["ground-beef"], "unit": "lb", "count": 30}
    assert by["chuck-roast"]["count"] == 12.25
    assert by["tuna-steaks"]["fill_in"] and by["tuna-steaks"]["count"] == 0
    assert by["chicken-breast"]["unit"] == "package" and by["chicken-breast"]["portions_per_package"] is None
    # the Amana standing order is the seeded Amana stock
    amana = cfg["standing_orders"]["amana"]["items"]
    assert all(by[k]["count"] == v for k, v in amana.items())


def test_tuna_thaws_like_salmon(cfg):
    assert cfg["thaw"]["tuna"] == cfg["thaw"]["salmon"] == "12h"


def test_cut_for_longest_match(cfg):
    m = cfg["freezer"]["map"]
    assert F.cut_for("filet mignon steaks", m) == "filet-mignon"
    assert F.cut_for("beef chuck roast", m) == "chuck-roast"
    assert F.cut_for("ground beef", m) == "ground-beef"
    assert F.cut_for("ground beef patties", m) == "burger-patties"
    assert F.cut_for("tuna (yellowfin tuna) steaks", m) == "tuna-steaks"
    assert F.cut_for("ahi tuna steaks", m) == "tuna-steaks"
    assert F.cut_for("chicken breast", m) == "chicken-breast"
    assert F.cut_for("boneless pork chops", m) == "pork-chops"
    assert F.cut_for("All-beef cheddar brats", m) == "brats-cheddar"
    assert F.cut_for("broccoli", m) is None


# ---- conversion ----------------------------------------------------------------
def test_need_units():
    st = F.Stock(_seed())
    assert st.need([_line("filet-mignon", 2)], 2) == {"filet-mignon": 2}
    assert st.need([_line("filet-mignon", 4, base=4)], 2) == {"filet-mignon": 2}
    assert st.need([_line("filet-mignon", 1, "lb")], 2) == {"filet-mignon": 2}      # 1 lb / 8 oz
    assert st.need([_line("ground-beef", 1.5, "lb", base=4)], 2) == {"ground-beef": 0.75}
    assert st.need([_line("ground-beef", 24, "oz")], 2) == {"ground-beef": 1.5}
    # chicken by the 1.1 lb package, rounded up (a thawed pack is used whole)
    assert st.need([_line("chicken-breast", 1.5, "lb", base=4)], 2) == {"chicken-breast": 1}
    assert st.need([_line("chicken-breast", 2, "lb")], 2) == {"chicken-breast": 2}
    # portions per package unknown: a count of breasts = one package
    assert st.need([_line("chicken-breast", 2, "")], 2) == {"chicken-breast": 1}
    # fill_in (count unknown) and cuts not in the file count as available
    assert st.need([_line("tuna-steaks", 2), _line("nope", 1)], 2) == {}


# ---- generator rules ------------------------------------------------------------
def _stock(**counts):
    data = _seed()
    for c in data["cuts"]:
        if c["id"] in counts:
            c["count"] = counts[c["id"]]
            c.pop("fill_in", None)
    return F.Stock(data)


def _freezer_pool():
    pool = make_pool(season="spring")
    cut_by_kind = {"chicken": ("chicken-breast", 1, "lb"), "ground_beef": ("ground-beef", 1, "lb"),
                   "pork": ("pork-chops", 1, "lb"), "roast": ("chuck-roast", 3.5, "lb")}
    steaks = iter(["filet-mignon", "ribeye"])
    for r in pool:
        k = r["kind"]
        if k in cut_by_kind:
            c, a, u = cut_by_kind[k]
            r["freezer_lines"] = [_line(c, a, u)]
        elif k == "steak":
            r["freezer_lines"] = [_line(next(steaks), 2)]
            r["name"] = r["freezer_lines"][0]["cut"]
        elif k == "fish":
            c = "salmon" if r["variant"] == "salmon" else "tuna-steaks"
            r["freezer_lines"] = [_line(c, 2)]
    return pool


def _dinners(entries):
    return [e.recipe_name for e in entries if e.role == "dinner-main"]


def test_no_stock_is_the_old_plan(cfg):
    pool = _freezer_pool()
    old = G.generate_week(make_pool(), SUNDAY, cfg)
    new = G.generate_week(pool, SUNDAY, cfg, stock=None)
    assert [(e.date, e.recipe_id, e.servings) for e in old] == \
        [(e.date, e.recipe_id, e.servings) for e in new]


def test_never_picks_a_short_steak_and_prefers_most_stock(cfg):
    pool = _freezer_pool()
    # 1 filet left (a dinner for 2 needs 2) -> the ribeye, whatever the shuffle says
    for wk in range(6):
        st = _stock(**{"filet-mignon": 1, "ribeye": 6})
        names = _dinners(G.generate_week(pool, SUNDAY + datetime.timedelta(weeks=wk), cfg, stock=st))
        assert "ribeye" in names and "filet-mignon" not in names
        assert not [s for s in st.short if s["slot"] == "steak"]
    # both covered: the cut with more dinners' worth wins
    st = _stock(**{"filet-mignon": 8, "ribeye": 2})
    assert "filet-mignon" in _dinners(G.generate_week(pool, SUNDAY, cfg, stock=st))


def test_short_everywhere_still_plans_and_notes_it(cfg):
    pool = _freezer_pool()
    st = _stock(**{"filet-mignon": 0, "ribeye": 0})
    s = G.plan_summary(G.generate_week(pool, SUNDAY, cfg, stock=st))
    assert s["dinners"] == cfg["proteins"]["dinners_per_week"] == 5
    assert [x["slot"] for x in st.short] == ["steak"]


def _sunday(parity):
    d = SUNDAY
    while G.parity_for_week(d) != parity:
        d += datetime.timedelta(weeks=1)
    return d


def test_fish_parity_yields_to_the_fish_in_stock(cfg):
    pool = _freezer_pool()
    salmon_wk = next(p for p, v in cfg["proteins"]["fish_parity"].items() if v == "salmon")
    d = _sunday(salmon_wk)
    fish = lambda es: next(e.recipe_id for e in es if e.role == "dinner-main" and e.date == d)
    by_id = {r["id"]: r for r in pool}
    # salmon week, no salmon, tuna on hand -> tuna
    st = _stock(salmon=0, **{"tuna-steaks": 4})
    assert by_id[fish(G.generate_week(pool, d, cfg, stock=st))]["variant"] == "tuna"
    assert st.fish == "tuna"
    # both in stock -> parity holds
    st = _stock(salmon=4, **{"tuna-steaks": 4})
    assert by_id[fish(G.generate_week(pool, d, cfg, stock=st))]["variant"] == "salmon"
    # neither (unknown counts, the seed) -> the parity fish, as before
    st = F.Stock(_seed())
    assert by_id[fish(G.generate_week(pool, d, cfg, stock=st))]["variant"] == "salmon"


def test_flex_switches_kind_when_pork_runs_out(cfg):
    pool = _freezer_pool()
    pork_wk = "A" if cfg["flex"]["rotation"][0] == "pork" else "B"
    d = _sunday(pork_wk)
    by_id = {r["id"]: r for r in pool}
    kinds = lambda es: [by_id[e.recipe_id]["kind"] for e in es if e.role == "dinner-main"]
    assert "pork" in kinds(G.generate_week(pool, d, cfg, stock=_stock(**{"pork-chops": 4})))
    out = kinds(G.generate_week(pool, d, cfg, stock=_stock(**{"pork-chops": 0})))
    assert "pork" not in out and out.count("chicken") >= 2   # 1 chicken + the chicken flex


# ---- reservation write ------------------------------------------------------------
def _entries():
    E = G.PlanEntry
    d = datetime.date(2026, 11, 22)
    return [E(d, "dinner", 1, "Filet Mignon", 2, "dinner-main", "", "[gp:2026-W48] dinner"),
            E(d, "dinner", 2, "Chili", 2, "dinner-main", "", "[gp:2026-W48] dinner"),
            E(d, "lunch", 3, "Pot Roast", 2, "leftover", "", "[gp:2026-W48] leftover")]


DETAILS = {
    1: {"name": "Filet Mignon", "servings": 2, "steps": [{"ingredients": [
        {"food": {"name": "filet mignon steaks"}, "amount": 2, "unit": None},
        {"food": {"name": "butter"}, "amount": 2, "unit": {"name": "tbsp"}}]}]},
    2: {"name": "Chili", "servings": 4, "steps": [{"ingredients": [
        {"food": {"name": "ground beef"}, "amount": 1, "unit": {"name": "lb"}},
        {"food": {"name": "tri-tip"}, "amount": 1, "unit": {"name": "lb"}}]}]},
    3: {"name": "Pot Roast", "servings": 4, "steps": [{"ingredients": [
        {"food": {"name": "chuck roast"}, "amount": 3.5, "unit": {"name": "lb"}}]}]},
}


def test_reserve_decrements_backs_up_and_is_idempotent(cfg, tmp_path, monkeypatch, capsys):
    p = tmp_path / "freezer.json"
    p.write_text(open(SEED).read())
    env = {"FREEZER_FILE": str(p)}
    items = R.reserve_freezer(cfg, "2026-W48", _entries(), DETAILS, env=env)
    assert [(i["cut"], i["amount"], i["recipe"]) for i in items] == [
        ("filet-mignon", 2, "Filet Mignon"), ("ground-beef", 0.5, "Chili")]   # leftover skipped
    data = json.loads(p.read_text())
    by = {c["id"]: c for c in data["cuts"]}
    assert by["filet-mignon"]["count"] == 0 and by["ground-beef"]["count"] == 29.5
    assert by["chuck-roast"]["count"] == 12.25
    assert len(data["reservations"]) == 2 and data["updated_by"] == "grocery-planner"
    assert json.loads((tmp_path / "freezer.json.bak").read_text())["reservations"] == []
    out = capsys.readouterr().out
    assert '"event": "freezer_reserved"' in out and '"status": "applied"' in out
    assert '"event": "freezer_unmapped"' in out and "tri-tip" in out
    # second run: nothing taken twice
    R.reserve_freezer(cfg, "2026-W48", _entries(), DETAILS, env=env)
    assert {c["id"]: c for c in json.loads(p.read_text())["cuts"]}["ground-beef"]["count"] == 29.5
    assert '"status": "already"' in capsys.readouterr().out


def test_missing_or_bad_file_plans_as_before(cfg, tmp_path, capsys):
    env = {"FREEZER_FILE": str(tmp_path / "nope.json")}
    assert R.load_stock(cfg, env) is None
    assert '"event": "freezer_inventory_missing"' in capsys.readouterr().out
    assert R.reserve_freezer(cfg, "2026-W48", _entries(), DETAILS, env=env) is None
    assert not (tmp_path / "nope.json").exists()
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert R.load_stock(cfg, {"FREEZER_FILE": str(bad)}) is None
    bad.write_text(json.dumps({"version": 2, "cuts": []}))
    assert R.load_stock(cfg, {"FREEZER_FILE": str(bad)}) is None
    assert "unknown version" in capsys.readouterr().out


def test_build_pool_carries_freezer_lines(cfg):
    rec = dict(DETAILS[1], id=1, keywords=[{"name": "spring"}, {"name": "steak"}])
    pool = R.build_pool([rec], "spring", cfg)
    assert pool[0]["freezer_lines"] == [{"cut": "filet-mignon", "food": "filet mignon steaks",
                                         "amount": 2.0, "unit": "", "base": 2.0}]
