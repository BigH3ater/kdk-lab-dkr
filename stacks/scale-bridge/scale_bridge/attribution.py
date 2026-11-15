"""Whose weigh-in is it: a weight band per person, from the host-managed config.

Config (JSON, the SCALE_BRIDGE_PEOPLE value in the host env file; never in git):
    [{"name": "alice", "min_lb": 110, "max_lb": 132, "token_env": "WGER_API_KEY_ALICE"}, ...]
Each person gives exactly one band pair: pounds (`min_lb`/`max_lb`, the documented
default) or kilograms (`min_kg`/`max_kg`). Pounds are converted to kg once, here,
with the wger client's LB_PER_KG; every comparison after that is in kg.
Bands are inclusive. Exactly one band containing the weight = that person. No
band, or more than one, = nobody: the caller logs a warning and writes nothing.

Optional engine profile per person (body-composition engine, engine.py): `height_cm`,
`sex` ("male"/"female") and either `birth_date` (YYYY-MM-DD, preferred: the age is worked
out at each measurement) or `age`. All or nothing: a person without these keys gets no
engine run; a partial or invalid profile is a config error. Health data: never logged.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date

from .wger import LB_PER_KG

ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")
PERSON_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PROFILE_KEYS = frozenset({"height_cm", "sex", "birth_date", "age"})
SEXES = ("male", "female")


@dataclass(frozen=True)
class Profile:
    """Engine inputs for one person (health data: repr hides it)."""
    height_cm: float
    sex: str                          # male | female
    birth_date: date | None = None    # preferred
    age: int | None = None            # only when there is no birth_date

    def __repr__(self) -> str:
        return "Profile(<hidden>)"


@dataclass(frozen=True)
class Person:
    name: str
    min_kg: float
    max_kg: float
    token_env: str
    profile: Profile | None = field(default=None, repr=False)

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
        missing = {"name", "token_env"} - set(item)
        if missing:
            raise ConfigError(f"people[{i}] is missing {', '.join(sorted(missing))}")
        unit = _band_unit(i, item)
        name, env = item["name"], item["token_env"]
        if not isinstance(name, str) or not PERSON_NAME.match(name):
            raise ConfigError(f"people[{i}].name must be a short lowercase login")
        if not isinstance(env, str) or not ENV_NAME.match(env):
            raise ConfigError(f"people[{i}].token_env must be an env var name")
        lo_key, hi_key = f"min_{unit}", f"max_{unit}"
        try:
            lo, hi = float(item[lo_key]), float(item[hi_key])
        except (TypeError, ValueError):
            raise ConfigError(f"people[{i}] {lo_key}/{hi_key} must be numbers") from None
        if not 0 < lo < hi:
            raise ConfigError(f"people[{i}] needs 0 < {lo_key} < {hi_key}")
        if unit == "lb":
            lo, hi = lo / LB_PER_KG, hi / LB_PER_KG
        people.append(Person(name=name, min_kg=lo, max_kg=hi, token_env=env, profile=_profile(i, item)))
    names = [x.name for x in people]
    if len(set(names)) != len(names):
        raise ConfigError("people names must be unique")
    return people


def _profile(i: int, item: dict, today: date | None = None) -> Profile | None:
    """None without any profile key; otherwise a complete, plausible profile or ConfigError.

    Error messages name the field, never its value (the value is health data).
    """
    keys = PROFILE_KEYS & set(item)
    if not keys:
        return None
    where = f"people[{i}] profile"
    missing = {"height_cm", "sex"} - keys
    if missing:
        raise ConfigError(f"{where} needs height_cm, sex and birth_date or age: missing {', '.join(sorted(missing))}")
    if ("birth_date" in keys) == ("age" in keys):
        raise ConfigError(f"{where} needs exactly one of birth_date (preferred) or age")
    h = item["height_cm"]
    if isinstance(h, bool) or not isinstance(h, (int, float)) or not 50 <= h <= 250:
        raise ConfigError(f"{where} height_cm must be a number of cm (50-250)")
    sex = item["sex"]
    if sex not in SEXES:
        raise ConfigError(f'{where} sex must be "male" or "female"')
    birth, age = None, None
    if "birth_date" in keys:
        b = item["birth_date"]
        try:
            if not isinstance(b, str) or not ISO_DATE.match(b):
                raise ValueError
            birth = date.fromisoformat(b)
        except ValueError:
            raise ConfigError(f"{where} birth_date must be a date YYYY-MM-DD") from None
        if not date(1900, 1, 1) <= birth < (today or date.today()):
            raise ConfigError(f"{where} birth_date must be in the past (after 1900)")
    else:
        a = item["age"]
        if isinstance(a, bool) or not isinstance(a, int) or not 1 <= a <= 120:
            raise ConfigError(f"{where} age must be a whole number of years (1-120)")
        age = a
    return Profile(height_cm=float(h), sex=sex, birth_date=birth, age=age)


def _band_unit(i: int, item: dict) -> str:
    """'lb' or 'kg': exactly one complete min/max pair, never both, never mixed."""
    keys = {"min_kg", "max_kg", "min_lb", "max_lb"} & set(item)
    if keys == {"min_lb", "max_lb"}:
        return "lb"
    if keys == {"min_kg", "max_kg"}:
        return "kg"
    if not keys:
        raise ConfigError(f"people[{i}] is missing its band: min_lb+max_lb (or min_kg+max_kg)")
    if len(keys) == 4:
        raise ConfigError(f"people[{i}] gives both min_kg/max_kg and min_lb/max_lb: use one pair")
    raise ConfigError(f"people[{i}] band must be one pair, min_lb+max_lb or min_kg+max_kg "
                      f"(not mixed, not half): got {', '.join(sorted(keys))}")


def attribute(people: list[Person], weight_kg: float) -> Attribution:
    hits = [x for x in people if x.contains(weight_kg)]
    return Attribution(person=hits[0] if len(hits) == 1 else None,
                       candidates=tuple(x.name for x in hits))


def overlaps(people: list[Person]) -> list[tuple[str, str]]:
    """Name pairs whose (inclusive) bands share at least one weight."""
    return [(a.name, b.name) for i, a in enumerate(people) for b in people[i + 1:]
            if a.min_kg <= b.max_kg and b.min_kg <= a.max_kg]
