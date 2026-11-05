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
