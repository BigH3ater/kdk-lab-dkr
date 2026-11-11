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
    # owner 2026-11-11: tuna and salmon are bought fresh, not freezer stock
    assert "tuna-steaks" not in by and "salmon" not in by
    # owner 2026-11-11: an O'Connell package = 2 breasts / 2 chops
    assert by["chicken-breast"]["unit"] == "package" and by["chicken-breast"]["portions_per_package"] == 2
    assert by["pork-chops"]["unit"] == "package" and by["pork-chops"]["portions_per_package"] == 2
    # the Amana standing order is the seeded Amana stock
    amana = cfg["standing_orders"]["amana"]["items"]
    assert all(by[k]["count"] == v for k, v in amana.items())


def test_fish_is_fresh_not_freezer_stock(cfg):
    # Owner 2026-11-11: tuna and salmon are bought from New Pi as needed.
    from grocery import seed as SD
    _, foods_cfg, _, _ = SD.load_configs()
    assert "tuna" not in cfg["thaw"]
    for name in ("tuna", "ahi tuna steaks", "tuna (yellowfin tuna) steaks", "salmon fillets"):
        cat = SD.route_food(name, foods_cfg, cfg["stores"])
        assert cat == "NewPi/weekly: Meat & Seafood", name          # not Amana beef
        assert not F.is_freezer_cat(cat, cfg["stores"]), name
        assert F.cut_for(name, cfg["freezer"]["map"]) is None, name
    assert not {"tuna-steaks", "salmon"} & set(cfg["freezer"]["map"].values())


def test_cuts_no_recipe_maps_to_are_ignored(cfg):
    # The owner's live file still lists tuna-steaks / salmon at 0: they must not
    # stop a fish dinner, and the build logs them once (freezer_unknown_cut).
    data = _seed()
    data["cuts"] += [{"id": "tuna-steaks", "unit": "portion", "count": 1},
                     {"id": "salmon", "unit": "portion", "count": 0}]
    st = F.Stock(data, cfg["freezer"]["map"])
    assert {"tuna-steaks", "salmon", "snack-sticks"} <= set(st.unknown)
    assert not st.known("salmon") and st.need([_line("salmon", 2)], 2) == {}
    pool = _freezer_pool()
    for r in pool:
        if r["kind"] == "fish":
            r["freezer_lines"] = [_line("salmon" if r["variant"] == "salmon" else "tuna-steaks", 2)]
    entries = G.generate_week(pool, SUNDAY, cfg, stock=st)
    assert "fish" in _kinds(pool, entries) and not st.fallbacks and not st.short


def test_load_stock_logs_unknown_cuts_once(cfg, tmp_path, monkeypatch):
    data = _seed()
    data["cuts"].append({"id": "salmon", "unit": "portion", "count": 0})
    p = tmp_path / "freezer.json"
    p.write_text(json.dumps(data))
    logged = []
    monkeypatch.setattr(R, "jlog", lambda **kw: logged.append(kw))
    st = R.load_stock(cfg, env={"FREEZER_FILE": str(p)})
    assert "salmon" not in st.cuts
    ev = [e for e in logged if e["event"] == "freezer_unknown_cut"]
    assert len(ev) == 1 and "salmon" in ev[0]["cuts"]


def test_cut_for_longest_match(cfg):
    m = cfg["freezer"]["map"]
    assert F.cut_for("filet mignon steaks", m) == "filet-mignon"
    assert F.cut_for("beef chuck roast", m) == "chuck-roast"
    assert F.cut_for("ground beef", m) == "ground-beef"
    assert F.cut_for("ground beef patties", m) == "burger-patties"
    assert F.cut_for("chicken breast", m) == "chicken-breast"
    assert F.cut_for("boneless pork chops", m) == "pork-chops"
    assert F.cut_for("All-beef cheddar brats", m) == "brats-cheddar"
    assert F.cut_for("broccoli", m) is None
    # steak cuts in stock (live Tandoor food names, 2026-11-11)
    assert F.cut_for("top sirloin steak", m) == "sirloin-steak"
    assert F.cut_for("sirloin tip steak", m) == "sirloin-tip-steak"
    assert F.cut_for("boneless ribeye steaks", m) == "ribeye"
    assert F.cut_for("New York strip steaks", m) == "ny-strip"
    assert F.cut_for("flank steak", m) == "fajita-strips"
    assert F.cut_for("flank steak or skirt steak", m) == "fajita-strips"
    assert F.cut_for("skirt steak", m) == "fajita-strips"


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
    # 2 breasts = one package of 2
    assert st.need([_line("chicken-breast", 2, "")], 2) == {"chicken-breast": 1}
    # 2 per package: breasts / chops divide by 2, rounded up to whole packages
    assert st.need([_line("chicken-breast", 3, "breasts")], 2) == {"chicken-breast": 2}
    assert st.need([_line("pork-chops", 4, "", base=4)], 2) == {"pork-chops": 1}
    assert st.need([_line("pork-chops", 1, "chop")], 2) == {"pork-chops": 1}
    # fill_in (count unknown) and cuts not in the file count as available
    assert st.need([_line("tuna-steaks", 2), _line("nope", 1)], 2) == {}
    assert st.unconvertible == []


def test_unconvertible_is_none_and_listed_never_a_guess():
    # Review 2026-11-11 #8: no package_lb / portions_per_package -> no guess
    data = _seed()
    data["cuts"] += [{"id": "pkg-nolb", "unit": "package", "count": 3},
                     {"id": "por-nolb", "unit": "portion", "count": 3},
                     {"id": "lb-cut", "unit": "lb", "count": 3}]
    st = F.Stock(data)
    assert F._convert(1, "lb", {"unit": "package"}, 2) is None        # was 1 package
    assert F._convert(2, "", {"unit": "package"}, 2) is None          # was 1 package
    assert F._convert(1, "lb", {"unit": "portion"}, 2) is None        # was 2 (servings)
    assert F._convert(1, "cup", {"unit": "lb"}, 2) is None
    assert st.need([_line("pkg-nolb", 1, "lb"), _line("por-nolb", 1, "lb"),
                    _line("lb-cut", 1, "cup"), _line("ground-beef", 1, "lb")], 2) == {"ground-beef": 1}
    assert [(u["cut"], u["unit"]) for u in st.unconvertible] == [
        ("pkg-nolb", "lb"), ("por-nolb", "lb"), ("lb-cut", "cup")]
    st.need([_line("pkg-nolb", 1, "lb")], 2)
    assert len(st.unconvertible) == 3                                 # listed once


def test_run_logs_freezer_unconvertible(cfg, monkeypatch):
    data = _seed()
    data["cuts"] = [c for c in data["cuts"] if c["id"] != "chicken-breast"] + [
        {"id": "chicken-breast", "unit": "package", "count": 9}]   # no package_lb, no ppp
    st = F.Stock(data)
    pool = _freezer_pool()
    logs = []
    monkeypatch.setattr(R, "jlog", lambda **kw: logs.append(kw))
    entries, got = R.generate(pool, SUNDAY, cfg, st, {})
    assert got is st and entries
    assert any(u["cut"] == "chicken-breast" for u in st.unconvertible)


# ---- review 2026-11-11 #4: the inventory never fails the build ----------------
def test_non_numeric_version_or_count_never_fails_the_build(cfg, tmp_path, capsys):
    p = tmp_path / "freezer.json"
    data = _seed()
    data["version"] = "one"
    p.write_text(json.dumps(data))
    assert F.load(str(p)) == (None, "unknown version 'one'")
    assert R.load_stock(cfg, {"FREEZER_FILE": str(p)}) is None
    data["version"] = 1
    next(c for c in data["cuts"] if c["id"] == "ground-beef")["count"] = "lots"
    p.write_text(json.dumps(data))
    assert R.load_stock(cfg, {"FREEZER_FILE": str(p)}) is None
    out = capsys.readouterr().out
    assert out.count('"event": "freezer_inventory_missing"') == 2 and "ValueError" in out


def test_stock_failure_mid_plan_plans_without_it(cfg, monkeypatch):
    data = _seed()
    st = F.Stock(data)
    st.cuts["ground-beef"]["count"] = "lots"          # bad after load (e.g. a mutated file)
    logs = []
    monkeypatch.setattr(R, "jlog", lambda **kw: logs.append(kw))
    rep = {}
    entries, got = R.generate(_freezer_pool(), SUNDAY, cfg, st, rep)
    assert got is None and G.plan_summary(entries)["dinners"] == 5
    assert [e["event"] for e in logs] == ["freezer_inventory_missing"]
    assert "lunch_gaps" in rep


# ---- review 2026-11-11 #1: burger night is checked against the patties ---------
class _BurgerT:
    def _kw(self):
        return {"burger-night": {"id": 7}}

    def keywords(self):
        return self._kw()

    def get_all(self, path, keyword=None):
        return [{"id": 77, "name": "Burger night", "servings": 2}] if keyword == 7 else []

    def get(self, path):
        assert path == "/api/recipe/77/"
        return {"id": 77, "name": "Burger night", "servings": 2, "steps": [{"ingredients": [
            {"food": {"name": "ground beef patties"}, "amount": 2, "unit": {"name": "patty"}},
            {"food": {"name": "buns"}, "amount": 2, "unit": None}]}]}


def _burger_week():
    s = SUNDAY
    while int(G.week_key(s).split("W")[1]) % 5:
        s += datetime.timedelta(weeks=1)
    return s


def test_fixed_pool_gives_burger_night_its_patty_lines(cfg):
    pool = R._fixed_pool(_BurgerT(), cfg)
    assert pool[0]["kind"] == "burger"
    assert [ln["cut"] for ln in pool[0]["freezer_lines"]] == ["burger-patties"]


def test_zero_patties_skips_burger_night(cfg):
    sun = _burger_week()
    pool = _freezer_pool() + R._fixed_pool(_BurgerT(), cfg)
    got = G.generate_week(pool, sun, cfg, stock=(st := _stock(**{"burger-patties": 0})))
    assert 77 not in [e.recipe_id for e in got]
    assert [s["slot"] for s in st.short] == ["burger_night"]
    assert st.short[0]["need"] == {"burger-patties": 2}
    # with patties it is planned and takes them
    got = G.generate_week(pool, sun, cfg, stock=(st := _stock(**{"burger-patties": 20})))
    assert 77 in [e.recipe_id for e in got] and st.remaining("burger-patties") == 18


# ---- generator rules ------------------------------------------------------------
def _stock(**counts):
    """A Stock from the seed with these counts; a cut the seed lacks (the fish
    cuts, gone since 2026-11-11) is added as portions, so the fish-stock rules
    are still exercised."""
    data = _seed()
    have = {c["id"] for c in data["cuts"]}
    data["cuts"] += [{"id": k, "unit": "portion", "count": 0} for k in counts if k not in have]
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


def _kinds(pool, entries):
    by_id = {r["id"]: r for r in pool}
    return [by_id[e.recipe_id]["kind"] for e in entries if e.role == "dinner-main"]


def test_short_steak_falls_back_to_ground_beef_never_beyond_the_freezer(cfg):
    # W48 2026-11-22: filet at 0 and the only in-season steak was the filet ->
    # it was planned anyway ("planned beyond the freezer"). Now: never planned,
    # the steak slot takes the next protein on the ladder that has stock.
    pool = _freezer_pool()
    for wk in range(6):
        st = _stock(**{"filet-mignon": 0, "ribeye": 0})
        d = SUNDAY + datetime.timedelta(weeks=wk)
        entries = G.generate_week(pool, d, cfg, stock=st)
        names = _dinners(entries)
        assert "filet-mignon" not in names and "ribeye" not in names
        assert G.plan_summary(entries)["dinners"] == 5          # the slot was filled
        assert st.fallbacks == [{"slot": "steak", "planned": "ground_beef",
                                 "recipe": st.fallbacks[0]["recipe"]}]
        assert _kinds(pool, entries).count("ground_beef") == \
            cfg["proteins"]["ground_beef"] - (1 if G.parity_for_week(d) == "A" else 0) + 1
        assert {x["recipe"] for x in st.short} == {"filet-mignon", "ribeye"}
        assert not st.short_slots


def test_fallback_keeps_the_asian_beef_rule(cfg):
    pool = _freezer_pool()
    for r in pool:                      # only Asian-style beef left besides one
        if r["kind"] == "ground_beef":
            r["beef_style"] = "asian"
    for wk in range(4):
        st = _stock(**{"filet-mignon": 0, "ribeye": 0})
        d = SUNDAY + datetime.timedelta(weeks=wk)
        entries = G.generate_week(pool, d, cfg, stock=st)
        by_id = {r["id"]: r for r in pool}
        asian = [e for e in entries if e.role == "dinner-main"
                 and by_id[e.recipe_id].get("beef_style") == "asian"]
        assert len(asian) <= cfg["beef"]["max_asian_per_week"]
        if st.fallbacks:                # the steak went to the next rung, not a 2nd Asian beef
            assert st.fallbacks[0]["planned"] in ("ground_beef", "chicken")


def test_nothing_fits_leaves_the_dinner_unplanned_and_logs_the_slot(cfg):
    pool = _freezer_pool()
    st = _stock(**{"filet-mignon": 0, "ribeye": 0, "ground-beef": 0, "chicken-breast": 0,
                   "salmon": 0, "tuna-steaks": 0, "pork-chops": 0, "chuck-roast": 0})
    entries = G.generate_week(pool, SUNDAY, cfg, stock=st, report=(rep := {}))   # never raises
    assert G.plan_summary(entries)["dinners"] == 0
    assert {"fish", "chicken", "steak"} <= {s["slot"] for s in st.short_slots}
    # each unplanned slot names the night it left empty: the five cooked nights
    assert sorted(s["day"] for s in st.short_slots) == sorted(rep["no_dinner"])
    steak = next(s for s in st.short_slots if s["slot"] == "steak")
    # the steak slot checked its own cuts and every rung of the ladder
    assert {"filet-mignon", "ribeye", "ground-beef", "chicken-breast"} <= set(steak["checked"])
    assert steak["counts"]["ribeye"] == 0
    assert len(rep["no_dinner"]) == 5
    assert not [e for e in entries if e.role in ("side", "rice")]
    # no recipe in the plan needs more than the freezer has
    tot = F.Stock(st.data)
    for e in entries:
        if e.role != "leftover":
            r = next(p for p in pool if p["id"] == e.recipe_id)
            assert not tot.need_for(r, e.servings)


def test_one_hole_one_night_off(cfg):
    # steak short and the ladder empty too (beef, chicken and fish at 0 while
    # this week's picks already used the last of them) -> one extra night off.
    pool = [r for r in _freezer_pool() if r["kind"] != "steak" or r["name"] == "filet-mignon"]
    for r in pool:
        if r["kind"] in ("ground_beef", "chicken", "fish"):
            r["freezer_lines"] = []     # covered (unmapped) for their own slots...
    st = _stock(**{"filet-mignon": 0})
    fb = {k: [] for k in G.FALLBACK_DEFAULT}            # ...but no fallback allowed
    c = {**cfg, "freezer": {**cfg["freezer"], "fallback": fb}}
    entries = G.generate_week(pool, SUNDAY, c, stock=st, report=(rep := {}))
    assert G.plan_summary(entries)["dinners"] == 4
    assert len(rep["no_dinner"]) == 1
    assert [s["slot"] for s in st.short_slots] == ["steak"]
    assert st.short_slots[0]["checked"] == ["filet-mignon"]
    assert st.short_slots[0]["counts"] == {"filet-mignon": 0}


def test_short_fish_falls_back_to_chicken_on_sunday(cfg):
    pool = _freezer_pool()
    st = _stock(salmon=0, **{"tuna-steaks": 0})
    entries = G.generate_week(pool, SUNDAY, cfg, stock=st)
    sun = [e for e in entries if e.role == "dinner-main" and e.date == SUNDAY]
    assert len(sun) == 1
    assert st.fallbacks[0]["slot"] == "fish" and st.fallbacks[0]["planned"] == "chicken"
    assert "fish" not in _kinds(pool, entries)


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
    (tmp_path / "freezer.json.bak").write_text("owner-snapshot")
    env = {"FREEZER_FILE": str(p)}
    items = R.reserve_freezer(cfg, "2026-W48", _entries(), DETAILS, env=env)
    assert [(i["cut"], i["amount"], i["recipe"]) for i in items] == [
        ("filet-mignon", 2, "Filet Mignon"), ("ground-beef", 0.5, "Chili")]   # leftover skipped
    data = json.loads(p.read_text())
    by = {c["id"]: c for c in data["cuts"]}
    assert by["filet-mignon"]["count"] == 0 and by["ground-beef"]["count"] == 29.5
    assert by["chuck-roast"]["count"] == 12.25
    assert len(data["reservations"]) == 2 and data["updated_by"] == "grocery-planner"
    # review 2026-11-11 #6: the owner's .bak (last Pantry tap) is untouched; the
    # pre-reservation file goes to .prebuild for a by-hand recovery
    assert (tmp_path / "freezer.json.bak").read_text() == "owner-snapshot"
    assert json.loads((tmp_path / "freezer.json.prebuild").read_text())["reservations"] == []
    assert not [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]
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


# ---- review 2026-11-11 #5: one lock + unique tmp names across both writers ------
def test_apply_reservations_waits_for_the_lock_and_rechecks_its_week(tmp_path):
    import fcntl
    import threading
    p = tmp_path / "freezer.json"
    p.write_text(open(SEED).read())
    lock = open(tmp_path / F.LOCK_NAME, "a+")
    fcntl.flock(lock, fcntl.LOCK_EX)                 # the dashboard mid-change
    res = {}
    th = threading.Thread(target=lambda: res.update(
        s=F.apply_reservations(str(p), "2026-W48", [{"week": "2026-W48", "cut": "ribeye",
                                                      "amount": 2, "unit": "portion"}])))
    th.start()
    th.join(0.3)
    assert th.is_alive()                             # blocked on the lock
    # meanwhile the other writer reserved the same week (e.g. a second run)
    d = json.loads(p.read_text())
    d["reservations"] = [{"week": "2026-W48", "cut": "ribeye", "amount": 2}]
    p.write_text(json.dumps(d))
    fcntl.flock(lock, fcntl.LOCK_UN)
    lock.close()
    th.join(5)
    assert res["s"] == "already"                     # re-checked inside the lock
    assert {c["id"]: c for c in json.loads(p.read_text())["cuts"]}["ribeye"]["count"] == 6


def test_write_uses_a_unique_tmp_name(tmp_path, monkeypatch):
    seen = []
    real = os.replace
    monkeypatch.setattr(os, "replace", lambda a, b: (seen.append(os.path.basename(a)), real(a, b)))
    F._write(str(tmp_path / "freezer.json"), b"{}", str(tmp_path))
    F._write(str(tmp_path / "freezer.json"), b"{}", str(tmp_path))
    assert len(set(seen)) == 2 and all(f".{os.getpid()}.tmp" not in n for n in seen)
