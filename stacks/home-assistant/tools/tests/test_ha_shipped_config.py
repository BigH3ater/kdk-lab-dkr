"""Guards on the HA config dirs pre_deploy ships (config/dashboards, config/packages).

pre_deploy copies with `cp -f` and never deletes, so a stray copy or a retired file in
the repo would stay live on the host. Retired here: the per-exercise ex_* helpers and
script.workout_mark_done (2026-11-06, wger won the tracking pilot) and the whole treadmill
integration (2026-11-08: tm_* helpers, sensor.treadmill_*, the Treadmill dashboard).
"""
import re
from pathlib import Path

HA = Path(__file__).resolve().parents[2]
DASH = HA / "config" / "dashboards"
PKG = HA / "config" / "packages"

RETIRED = re.compile(
    r"\b(input_number|input_datetime)\.ex_|workout_mark_done"
    r"|\b\w+\.tm_\w+|\bsensor\.treadmill_\w+|treadmill-workouts"
)


def test_no_backup_files_in_shipped_config():
    """The repo is the backup: no *.bak / *.orig copies in the dirs pre_deploy ships."""
    stray = [p.name for d in (DASH, PKG) for p in d.iterdir() if not p.name.endswith(".yaml")]
    assert stray == []


def test_no_retired_entities_anywhere():
    """Nothing in the shipped HA config may still point at a retired helper or dashboard."""
    hits = [p.name for d in (DASH, PKG) for p in d.glob("*.yaml") if RETIRED.search(p.read_text(encoding="utf-8"))]
    assert hits == []
    assert not list(PKG.glob("workout_strength*.yaml"))
    assert not list(PKG.glob("treadmill*.yaml"))
    assert not list(DASH.glob("workout*.yaml"))
