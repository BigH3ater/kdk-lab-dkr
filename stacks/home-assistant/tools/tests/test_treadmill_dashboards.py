"""The HA Treadmill dashboard (workout.yaml) is the Treadmill view only (the per-exercise
views were retired with the ex_* helpers when wger won the pilot, 2026-11-06), it is the
ONE dashboard for both people since 2026-11-07 (Rachel's identical copy was dropped), and
every helper/script it uses is defined in packages/treadmill.yaml."""
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


def test_one_shared_treadmill_dashboard():
    dash = _load("workout.yaml")
    assert dash["title"] == "Treadmill"
    assert [v["path"] for v in dash["views"]] == ["treadmill-workouts"]
    assert sorted(p.name for p in DASH.glob("workout*.yaml")) == ["workout.yaml"]


def test_dashboard_covers_both_people():
    """The single dashboard must keep the walker selector and every person's totals."""
    text = (DASH / "workout.yaml").read_text(encoding="utf-8")
    assert "input_select.tm_current_walker" in text
    assert "script.tm_set_walker_from_user" in text
    for name, slug in (("Jacob", "jacob"), ("Rachel", "rachel"), ("Guest", "guest")):
        assert f"('{name}','{slug}')" in text


def test_no_backup_files_in_shipped_config():
    """git is the backup: no *.bak / *.orig copies in the dirs pre_deploy ships."""
    stray = [p.name for d in (DASH, PKG) for p in d.iterdir() if not p.name.endswith(".yaml")]
    assert stray == []


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
