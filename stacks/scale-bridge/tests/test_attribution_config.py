"""Attribution table and env parsing. Made-up bands only (never real weights)."""
import json
import pathlib

import pytest

from scale_bridge.attribution import ConfigError, attribute, load_people
from scale_bridge.config import from_env, parse_proxies

EXAMPLE = pathlib.Path(__file__).parent.parent / "scale-bridge.env.example"
PEOPLE = json.dumps([
    {"name": "alice", "min_kg": 50, "max_kg": 62, "token_env": "WGER_API_KEY_ALICE"},
    {"name": "bob", "min_kg": 60, "max_kg": 80, "token_env": "WGER_API_KEY_BOB"},
])


@pytest.mark.parametrize("kg,outcome,person,candidates", [
    (55.0, "matched", "alice", ("alice",)),
    (50.0, "matched", "alice", ("alice",)),        # bands are inclusive
    (70.0, "matched", "bob", ("bob",)),
    (80.0, "matched", "bob", ("bob",)),
    (61.0, "ambiguous", None, ("alice", "bob")),   # overlap -> nobody
    (49.99, "no_match", None, ()),
    (95.0, "no_match", None, ()),
])
def test_attribution_table(kg, outcome, person, candidates):
    a = attribute(load_people(PEOPLE), kg)
    assert a.outcome == outcome
    assert (a.person.name if a.person else None) == person
    assert a.candidates == candidates


def test_empty_config_matches_nobody():
    assert load_people("") == [] and attribute([], 70.0).outcome == "no_match"


@pytest.mark.parametrize("bad,msg", [
    ("{", "valid JSON"),
    ('{"name": "x"}', "list"),
    ('[{"name": "alice", "min_kg": 1, "max_kg": 2}]', "token_env"),
    ('[{"name": "Alice!", "min_kg": 1, "max_kg": 2, "token_env": "X"}]', "login"),
    ('[{"name": "a", "min_kg": 1, "max_kg": 2, "token_env": "lower"}]', "env var"),
    ('[{"name": "a", "min_kg": 5, "max_kg": 2, "token_env": "X"}]', "min_kg < max_kg"),
    ('[{"name": "a", "min_kg": "x", "max_kg": 2, "token_env": "X"}]', "numbers"),
    ('[{"name": "a", "min_kg": 1, "max_kg": 2, "token_env": "X"},'
     ' {"name": "a", "min_kg": 3, "max_kg": 4, "token_env": "Y"}]', "unique"),
])
def test_bad_people_config(bad, msg):
    with pytest.raises(ConfigError, match=msg):
        load_people(bad)


def test_defaults_are_dry_run_and_placeholders():
    s = from_env({})
    assert s.dry_run is True and s.people == () and s.proxies == () and s.scale_address is None
    assert (s.profile_height_cm, s.profile_age, s.scale_name) == (170, 30, "Body Pod 2")
    assert s.wger_url == "https://wger.kmkdp.com"


def test_tokens_come_from_the_named_env_vars_and_stay_out_of_repr():
    s = from_env({"SCALE_BRIDGE_PEOPLE": PEOPLE, "WGER_API_KEY_ALICE": "t" * 40, "SCALE_BRIDGE_DRY_RUN": "false"})
    assert s.dry_run is False
    assert s.token_for(s.people[0]) == "t" * 40 and s.token_for(s.people[1]) == ""
    assert "t" * 40 not in repr(s)


def test_address_filter_validated_and_normalised():
    assert from_env({"SCALE_ADDRESS": "aa:bb:cc:dd:ee:01"}).scale_address == "AA:BB:CC:DD:EE:01"
    with pytest.raises(ValueError):
        from_env({"SCALE_ADDRESS": "not-a-mac"})


def test_proxy_spec():
    env = {"NOISE_ONE": "k" * 44}
    got = parse_proxies(" 192.0.2.10=NOISE_ONE , 192.0.2.11 ", env)
    assert [(x.host, x.noise_psk) for x in got] == [("192.0.2.10", "k" * 44), ("192.0.2.11", None)]
    assert "k" * 44 not in repr(got)
    with pytest.raises(ValueError, match="empty"):
        parse_proxies("192.0.2.10=MISSING", env)
    assert parse_proxies("", env) == ()


def test_committed_example_env_parses():
    """The example file must stay loadable (and hold only made-up values)."""
    env = {}
    for line in EXAMPLE.read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip("'")
    s = from_env(env)
    assert [x.name for x in s.people] == ["person-a", "person-b"]
    assert s.dry_run is True
