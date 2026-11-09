"""treadmill.yaml session distance: the console resets its counter ~4 s after the belt stops.

Replays the 2026-11-06 15:02 walk the way HA saw it (US customary unit system, so the
counter is in ft and the speed in mph; counter 3.28 ft per tick up to 951.44 ft, then
0.0 four seconds after the stop) through the package's START, tracker and END
automation actions, with a small Jinja stand-in for HA templates (results parsed the
way HA does: strip, then literal_eval). Before the fix END read 0 after the reset and
credited nothing."""
import ast
from pathlib import Path

import jinja2.sandbox
import yaml

PKG = Path(__file__).resolve().parents[2] / "config" / "packages" / "treadmill.yaml"
FT = 0.3048


class SecretLoader(yaml.SafeLoader):
    pass


SecretLoader.add_constructor("!secret", lambda loader, node: ("SECRET", loader.construct_scalar(node)))

ENV = jinja2.sandbox.ImmutableSandboxedEnvironment()


def parse_result(text):
    """homeassistant.helpers.template: strip, numeric fast path, literal_eval, keep strings."""
    text = text.strip()
    try:
        v = ast.literal_eval(text)
    except (ValueError, TypeError, SyntaxError, MemoryError):
        return text
    return text if isinstance(v, str) else v


def render(value, ctx):
    if isinstance(value, str):
        if "{{" in value or "{%" in value:
            return parse_result(ENV.from_string(value).render(**ctx))
        return value
    if isinstance(value, dict):
        return {k: render(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, ctx) for v in value]
    return value


def is_number(v):
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


ENV.filters.setdefault("is_number", is_number)


class State:
    def __init__(self, state, **attributes):
        self.state, self.attributes = str(state), attributes


class Fake:
    """Just enough of HA for these automations: a state store and the helper actions."""

    def __init__(self):
        self.pkg = yaml.load(PKG.read_text(), Loader=SecretLoader)
        self.autos = {a["id"]: a for a in self.pkg["automation"]}
        self.now = 1_000_000.0
        self.s = {}
        for k in self.pkg["input_number"]:
            self.set(f"input_number.{k}", 0)
        self.set("input_boolean.tm_session_active", "off")
        self.set("input_select.tm_current_walker", "Jacob")
        self.set("input_datetime.tm_session_start", "unknown", timestamp=0)
        self.distance("0.0")
        self.set("sensor.treadmill_speed", "0.0", unit_of_measurement="mph")

    def set(self, entity, state, **attrs):
        self.s[entity] = State(state, **attrs)

    def distance(self, v):
        old = self.s.get("sensor.treadmill_distance")
        self.set("sensor.treadmill_distance", v, unit_of_measurement="ft")
        new = self.s["sensor.treadmill_distance"]
        self.run("tm_session_distance_track", {"from_state": old, "to_state": new})

    def ctx(self, **extra):
        class _Now:
            def __init__(self, t):
                self.t = t

            def timestamp(self):
                return self.t

        return dict(states=lambda e: self.s[e].state if e in self.s else "unknown",
                    state_attr=lambda e, a: self.s[e].attributes.get(a) if e in self.s else None,
                    is_state=lambda e, v: e in self.s and self.s[e].state == v,
                    now=lambda: _Now(self.now), **extra)

    def run(self, auto_id, trigger=None):
        a = self.autos[auto_id]
        ctx = self.ctx(trigger=trigger)
        for c in a.get("conditions", []):
            if c["condition"] == "state" and self.s[c["entity_id"]].state != c["state"]:
                return False
            if c["condition"] == "template" and render(c["value_template"], ctx) is not True:
                return False
        self.seq(a["actions"], ctx)
        return True

    def seq(self, steps, ctx):
        for st in steps:
            if "variables" in st:
                for k, v in st["variables"].items():
                    ctx[k] = render(v, ctx)
            elif "if" in st:
                self.seq(st.get("then" if render(st["if"], ctx) is True else "else", []), ctx)
            else:
                act = st["action"]
                ent = render(st["target"]["entity_id"], ctx)
                data = render(st.get("data", {}), ctx)
                if act == "input_number.set_value":
                    self.set(ent, float(data["value"]))
                elif act == "input_datetime.set_datetime":
                    self.set(ent, "set", timestamp=float(data["timestamp"]))
                elif act == "input_boolean.turn_on":
                    self.set(ent, "on")
                elif act == "input_boolean.turn_off":
                    self.set(ent, "off")
                else:
                    raise AssertionError(act)

    def num(self, e):
        return float(self.s[e].state)


def session_distance(f):
    tpl = next(t for t in f.pkg["template"][0]["sensor"] if t["unique_id"] == "tm_session_distance")
    return render(tpl["state"], f.ctx())


def walk_1502(f, ticks=290, reset=True, gap_at=None):
    f.set("sensor.treadmill_speed", "2.0", unit_of_measurement="mph")
    assert f.run("tm_session_start")
    for i in range(1, ticks + 1):
        f.now += 1
        f.distance(f"{i * 3.28084:.6f}")                # 1 m per tick, shown in ft
        if i == gap_at:
            f.distance("unavailable")
    peak_live = session_distance(f)
    f.set("sensor.treadmill_speed", "0.0", unit_of_measurement="mph")
    if reset:
        f.now += 4
        f.distance("0.0")                                # console reset 4 s after the stop
    after_reset = session_distance(f)
    f.now += 120
    assert f.run("tm_session_end")
    return peak_live, after_reset


def test_reset_after_stop_still_credits_the_walk():
    f = Fake()
    peak_live, after_reset = walk_1502(f)
    assert round(f.num("input_number.tm_session_peak_distance"), 1) == 290.0
    assert round(f.num("input_number.tm_total_distance_jacob"), 1) == 290.0      # metres, not ft
    assert round(f.num("input_number.tm_total_time_min_jacob"), 1) == round((290 + 4 + 120) / 60, 1)
    assert round(peak_live, 1) == 290.0 and round(after_reset, 1) == 290.0       # live sensor holds
    assert f.s["input_boolean.tm_session_active"].state == "off"


def test_gap_mid_walk_loses_nothing_and_next_start_resets():
    f = Fake()
    walk_1502(f, gap_at=100)
    assert round(f.num("input_number.tm_total_distance_jacob"), 1) == 290.0
    f.set("sensor.treadmill_speed", "2.0", unit_of_measurement="mph")
    assert f.run("tm_session_start")
    assert f.num("input_number.tm_session_peak_distance") == 0
    assert session_distance(f) == 0


def test_reboot_mid_walk_keeps_counting():
    f = Fake()
    f.set("sensor.treadmill_speed", "2.0", unit_of_measurement="mph")
    f.run("tm_session_start")
    for i in range(1, 101):
        f.distance(f"{i * 3.28084:.6f}")
    f.distance("0.0")                                     # console reboot while walking
    for i in range(1, 51):
        f.distance(f"{i * 3.28084:.6f}")
    f.now += 600
    f.run("tm_session_end")
    assert round(f.num("input_number.tm_total_distance_jacob"), 1) == 150.0


def test_tracker_ignores_changes_outside_a_session():
    f = Fake()
    f.distance("500.0")
    assert f.num("input_number.tm_session_peak_distance") == 0


def test_end_falls_back_to_the_raw_delta_when_untracked():
    f = Fake()
    f.set("input_boolean.tm_session_active", "on")         # session opened before the tracker existed
    f.set("input_number.tm_session_start_distance", 100.0)
    f.set("sensor.treadmill_distance", "1100.0", unit_of_measurement="ft")
    f.now += 600
    f.run("tm_session_end")
    assert round(f.num("input_number.tm_total_distance_jacob"), 2) == round(1000 * FT, 2)


def test_calorie_rate_converts_mph():
    f = Fake()
    tpl = next(t for t in f.pkg["template"][0]["sensor"] if t["unique_id"] == "tm_calorie_rate")
    f.set("sensor.treadmill_speed", "3.0", unit_of_measurement="mph")
    f.set("sensor.treadmill_incline", "0")
    f.set("input_number.tm_weight_kg_jacob", 80.0)
    mph = render(tpl["state"], f.ctx())
    f.set("sensor.treadmill_speed", str(3.0 * 1.609344), unit_of_measurement="km/h")
    assert mph == render(tpl["state"], f.ctx())
    smm = 3.0 * 1.609344 * 1000 / 60
    assert mph == round((0.1 * smm + 3.5) * 80 / 200, 2)
