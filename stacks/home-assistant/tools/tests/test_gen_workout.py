"""Checks for gen_workout.py: committed files are current, and every entity a
dashboard references is defined by that person's package (the 2026-11 Rachel bug:
the dashboard said ex_<id>_rachel_weight, the package defined ex_<id>_weight_rachel)."""
import re
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import gen_workout as g  # noqa: E402


def defined(pkg_text: str) -> set[str]:
    out, domain = set(), None
    for line in pkg_text.splitlines():
        m = re.match(r"^(input_number|input_datetime|script):", line)
        if m:
            domain = m.group(1)
            continue
        m = re.match(r"^  ([a-z0-9_]+):$", line)
        if m and domain:
            out.add(f"{domain}.{m.group(1)}")
    return out


def test_committed_files_are_current():
    r = subprocess.run([sys.executable, str(TOOLS / "gen_workout.py"), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_dashboard_entities_exist_in_package():
    cat = g.load()
    outs = g.outputs(cat)
    all_defined = set()
    for path, text in outs.items():
        if "packages" in path.parts:
            all_defined |= defined(text)
    for p in cat["people"]:
        sfx = p["suffix"]
        dash = outs[g.HA / "config" / "dashboards" / f"workout{sfx}.yaml"]
        pkg = defined(outs[g.HA / "config" / "packages" / f"workout_strength{sfx}.yaml"])
        refs = set(re.findall(r"(?:entity|target_entity): ((?:input_number|input_datetime)\.ex_[a-z0-9_]+)", dash))
        assert refs, "dashboard references no helpers"
        missing = refs - pkg
        assert not missing, f"{p['key']}: dashboard references undefined helpers {sorted(missing)[:5]}"
        for s in re.findall(r"perform_action: (script\.[a-z0-9_]+)", dash):
            assert s in all_defined, f"{s} not defined in any workout package"


def test_every_item_has_a_helper_per_person():
    cat = g.load()
    outs = g.outputs(cat)
    for p in cat["people"]:
        pkg = defined(outs[g.HA / "config" / "packages" / f"workout_strength{p['suffix']}.yaml"])
        for it in cat["items"]:
            for f in g.fields(it):
                assert g.entity(it, f, p["suffix"]) in pkg


# ---------------------------------------------------------------- per-person plans
def test_first_person_output_ignores_the_plans():
    """Jacob's package + dashboard are byte-identical with and without the "plans" block."""
    cat = g.load()
    bare = {k: v for k, v in cat.items() if k != "plans"}
    with_plans, without = g.outputs(cat), g.outputs(bare)
    for name in ("packages/workout_strength.yaml", "dashboards/workout.yaml"):
        path = g.HA / "config" / name
        assert with_plans[path] == without[path], name


def test_rach_dashboard_is_her_plan_and_package_keeps_the_old_list():
    cat = g.load()
    rach = next(p for p in cat["people"] if p["login"] == "rach")
    pl = g.plan(cat, rach)
    assert pl is cat["plans"]["rach"]
    outs = g.outputs(cat)
    dash = outs[g.HA / "config" / "dashboards" / "workout_rachel.yaml"]
    pkg = defined(outs[g.HA / "config" / "packages" / "workout_strength_rachel.yaml"])
    refs = set(re.findall(r"entity: ((?:input_number|input_datetime)\.ex_[a-z0-9_]+)", dash))
    mine = {g.entity(it, f, "_rachel") for it in pl["items"] for f in g.fields(it)}
    assert refs == mine                                   # her dashboard = her plan only
    assert mine <= pkg
    old = {g.entity(it, f, "_rachel") for it in cat["items"] for f in g.fields(it)}
    assert old <= pkg                                     # kept: history + the Today page
    assert "home.kmkdp.com/guides/workout/workout.html" not in dash   # not Jacob's guide
    for it in pl["items"]:
        for st in it["steps"]:
            assert st["title"] in dash and st["dose"] in dash


def test_rach_plan_weekdays():
    pl = g.load()["plans"]["rach"]
    days = {d: sorted(it["id"] for it in pl["items"] if d in g.item_days(it)) for d in g.WEEKDAYS}
    daily = sorted(it["id"] for it in pl["items"] if it["days"] == ["daily"])
    assert len(daily) == 6
    assert days["wed"] == days["sat"] == days["sun"] == daily
    assert set(days["mon"]) - set(daily) == {"high_box_squat", "pallof_press", "wall_pushup"}
    assert set(days["thu"]) - set(daily) == {"elevated_rdl", "seated_band_row", "glute_bridge"}
    assert set(days["tue"]) - set(daily) == set(days["fri"]) - set(daily) == {"pool_or_bike"}


def test_plan_validation_rejects_a_clash_with_kept_helpers(tmp_path):
    import json
    cat = json.loads(g.CATALOG.read_text(encoding="utf-8"))
    cat["plans"]["rach"]["items"][0]["id"] = "box_squat"          # same id as a kept top-level helper
    bad = tmp_path / "cat.json"
    bad.write_text(json.dumps(cat), encoding="utf-8")
    try:
        g.load(bad)
    except AssertionError as e:
        assert "clash" in str(e)
    else:
        raise AssertionError("clash not detected")
