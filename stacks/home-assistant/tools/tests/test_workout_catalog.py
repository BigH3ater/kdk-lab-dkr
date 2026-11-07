"""workout_catalog.py: the catalog loads, validates, and each person's plan resolves."""
import json
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import workout_catalog as wc  # noqa: E402


def test_catalog_loads_with_both_people():
    cat = wc.load()
    assert [p["login"] for p in cat["people"]] == ["jmack", "rach"]
    assert wc.plan(cat, cat["people"][0]) is cat                     # Jacob: top-level list
    assert wc.plan(cat, cat["people"][1]) is cat["plans"]["rach"]    # Rachel: her own plan


def test_rach_plan_weekdays():
    pl = wc.load()["plans"]["rach"]
    days = {d: sorted(it["id"] for it in pl["items"] if d in wc.item_days(it)) for d in wc.WEEKDAYS}
    daily = sorted(it["id"] for it in pl["items"] if it["days"] == ["daily"])
    assert len(daily) == 6
    assert days["wed"] == days["sat"] == days["sun"] == daily
    assert set(days["mon"]) - set(daily) == {"high_box_squat", "pallof_press", "wall_pushup"}
    assert set(days["thu"]) - set(daily) == {"elevated_rdl", "seated_band_row", "glute_bridge"}
    assert set(days["tue"]) - set(daily) == set(days["fri"]) - set(daily) == {"pool_or_bike"}


def test_no_home_assistant_generator_keys_left():
    """The HA helper generator is retired; its catalog keys must not creep back."""
    cat = wc.load()
    dead = {"package_head", "generated_banner", "strength_intro", "routines"}   # routines: derived since 2026-11-07
    assert not dead & set(cat)
    assert not dead & set(cat["plans"]["rach"]) and "keep_default_helpers" not in cat["plans"]["rach"]
    for p in cat["people"]:
        assert not {"suffix", "title", "owns_scripts", "package_intro"} & set(p)


def _broken(tmp_path, mutate):
    cat = json.loads(wc.CATALOG.read_text(encoding="utf-8"))
    mutate(cat)
    bad = tmp_path / "cat.json"
    bad.write_text(json.dumps(cat), encoding="utf-8")
    return bad


@pytest.mark.parametrize("mutate,msg", [
    (lambda c: c["items"][0].update(kind="kg"), "unknown kind"),
    (lambda c: c["items"][0].update(group="nope"), "unknown group"),
    (lambda c: c["items"].append(dict(c["items"][0])), "duplicate id"),
    (lambda c: c["plans"]["rach"]["items"][0].update(days=["someday"]), "bad days"),
    (lambda c: c["plans"]["rach"]["items"][0].update(slugs=["x"]), "steps must list"),
    (lambda c: c["plans"].update(nobody=c["plans"]["rach"]), "no person"),
])
def test_validation_rejects(tmp_path, mutate, msg):
    with pytest.raises(AssertionError, match=msg):
        wc.load(_broken(tmp_path, mutate))
