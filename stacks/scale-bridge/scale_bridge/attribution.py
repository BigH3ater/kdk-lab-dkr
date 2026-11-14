"""Whose weigh-in is it: a weight band per person, from the host-managed config.

Config (JSON, the SCALE_BRIDGE_PEOPLE value in the host env file; never in git):
    [{"name": "alice", "min_kg": 50.0, "max_kg": 60.0, "token_env": "WGER_API_KEY_ALICE"}, ...]
Bands are inclusive. Exactly one band containing the weight = that person. No
band, or more than one, = nobody: the caller logs a warning and writes nothing.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")
PERSON_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


@dataclass(frozen=True)
class Person:
    name: str
    min_kg: float
    max_kg: float
    token_env: str

    def contains(self, weight_kg: float) -> bool:
        return self.min_kg <= weight_kg <= self.max_kg


@dataclass(frozen=True)
class Attribution:
    person: Person | None
    candidates: tuple[str, ...]      # names of every band that matched

    @property
    def outcome(self) -> str:
        if self.person is not None:
            return "matched"
        return "ambiguous" if self.candidates else "no_match"


class ConfigError(ValueError):
    pass


def load_people(text: str | None) -> list[Person]:
    if not text or not text.strip():
        return []
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as e:
        raise ConfigError(f"people config is not valid JSON (line {e.lineno} col {e.colno})") from None
    if not isinstance(raw, list):
        raise ConfigError("people config must be a JSON list")
    people: list[Person] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ConfigError(f"people[{i}] must be an object")
        missing = {"name", "min_kg", "max_kg", "token_env"} - set(item)
        if missing:
            raise ConfigError(f"people[{i}] is missing {', '.join(sorted(missing))}")
        name, env = item["name"], item["token_env"]
        if not isinstance(name, str) or not PERSON_NAME.match(name):
            raise ConfigError(f"people[{i}].name must be a short lowercase login")
        if not isinstance(env, str) or not ENV_NAME.match(env):
            raise ConfigError(f"people[{i}].token_env must be an env var name")
        try:
            lo, hi = float(item["min_kg"]), float(item["max_kg"])
        except (TypeError, ValueError):
            raise ConfigError(f"people[{i}] min_kg/max_kg must be numbers") from None
        if not 0 < lo < hi:
            raise ConfigError(f"people[{i}] needs 0 < min_kg < max_kg")
        people.append(Person(name=name, min_kg=lo, max_kg=hi, token_env=env))
    names = [x.name for x in people]
    if len(set(names)) != len(names):
        raise ConfigError("people names must be unique")
    return people


def attribute(people: list[Person], weight_kg: float) -> Attribution:
    hits = [x for x in people if x.contains(weight_kg)]
    return Attribution(person=hits[0] if len(hits) == 1 else None,
                       candidates=tuple(x.name for x in hits))
