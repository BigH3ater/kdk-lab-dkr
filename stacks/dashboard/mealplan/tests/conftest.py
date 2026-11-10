import os
import sys

# Put the mealplan app dir on the path so tests can `import newpi`.
MEALPLAN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, MEALPLAN)

import pytest
import yaml

PLANNER = os.path.join(os.path.dirname(os.path.dirname(MEALPLAN)), "grocery-planner")


def _yaml(*parts):
    with open(os.path.join(PLANNER, *parts)) as fh:
        return yaml.safe_load(fh)


@pytest.fixture
def repo_cfg(monkeypatch):
    """The real foods.yaml, cleanup.yaml and planner config.yaml from the repo,
    loaded into the sidecar's process caches (what the container mounts)."""
    import newpi
    import stock
    cfg = {"foods": _yaml("recipes", "foods.yaml"),
           "cleanup": _yaml("recipes", "cleanup.yaml"),
           "config": _yaml("config.yaml")}
    monkeypatch.setattr(newpi, "_cache", cfg["foods"])
    monkeypatch.setattr(stock, "_cleanup", cfg["cleanup"])
    monkeypatch.setattr(stock, "_config", cfg["config"])
    return cfg


@pytest.fixture(autouse=True)
def _forced_registry(monkeypatch, tmp_path):
    """The forced-manual registry lives in a per-test /state stand-in."""
    import app
    import forced
    d = tmp_path / "state"
    d.mkdir()
    reg = forced.Registry(str(d / "manual-forced.json"))
    monkeypatch.setattr(app, "FORCED", reg)
    return reg


@pytest.fixture(autouse=True)
def _fresh_caches():
    """Each test starts with an empty grocery snapshot and recipe cache (both
    are process-wide in the app)."""
    import app
    app._gcache.update(ts=0.0, day=None, entries=None, plan=None, lines=[])
    app._RECIPE_CACHE.clear()
    yield
