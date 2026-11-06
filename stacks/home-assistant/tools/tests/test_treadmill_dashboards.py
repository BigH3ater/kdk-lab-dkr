"""The two HA Workout dashboards are the Treadmill view only (the per-exercise views were
retired with the ex_* helpers when wger won the pilot, 2026-11-06): same view for both
people, and every helper/script they use is defined in packages/treadmill.yaml."""
import re
from pathlib import Path

import yaml

HA = Path(__file__).resolve().parents[2]
DASH = HA / "config" / "dashboards"
PKG = HA / "config" / "packages"


class _HALoader(yaml.SafeLoader):
    """SafeLoader that reads HA tags (!secret, !include ...) as plain strings."""


_HALoader.add_multi_constructor("!", lambda loader, suffix, node: loader.construct_scalar(node))


def _load(name):
    return yaml.safe_load((DASH / name).read_text(encoding="utf-8"))


def test_both_dashboards_are_the_same_treadmill_view():
    jacob, rachel = _load("workout.yaml"), _load("workout_rachel.yaml")
    assert jacob["title"] == "Workout" and rachel["title"] == "Workout · Rachel"
    assert [v["path"] for v in jacob["views"]] == ["treadmill-workouts"]
    assert jacob["views"] == rachel["views"]


def test_no_retired_workout_entities_anywhere():
    """Nothing in the HA config may still point at an ex_* helper or the mark-done script."""
    pat = re.compile(r"\b(input_number|input_datetime)\.ex_|workout_mark_done")
    hits = [p.name for d in (DASH, PKG) for p in d.glob("*.yaml") if pat.search(p.read_text(encoding="utf-8"))]
    assert hits == []
    assert not list(PKG.glob("workout_strength*.yaml"))


def test_dashboard_helpers_exist_in_the_treadmill_package():
    pkg = yaml.load((PKG / "treadmill.yaml").read_text(encoding="utf-8"), Loader=_HALoader)
    text = (DASH / "workout.yaml").read_text(encoding="utf-8")
    for domain, obj in set(re.findall(r"\b(input_boolean|input_select|script)\.([a-z0-9_]+)", text)):
        assert obj in pkg[domain], f"{domain}.{obj} not in treadmill.yaml"
    # per-person prefixes built in templates: input_number.tm_total_time_min_<walker> etc.
    for domain, prefix in set(re.findall(r"\b(input_number|input_datetime)\.([a-z0-9_]+_)\b", text)):
        assert any(k.startswith(prefix) for k in pkg[domain]), f"{domain}.{prefix}* not in treadmill.yaml"
