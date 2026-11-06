"""config/packages/treadmill_wger.yaml (treadmill walk -> wger) run against a fake wger 2.7.

A small stand-in for Home Assistant's script engine runs the package's script: the
`variables`, `if/then/else`, `stop` and action steps, templates rendered with Jinja
plus the few HA filters the package uses, and results parsed the way HA does
(strip, then literal_eval). The fake wger accepts only the POST fields its live
OPTIONS listed (fixtures/wger_2_7_api.json, captured 2026-11-06), so a renamed or
misspelt field fails here before it reaches the server.

What this does not cover: HA's own config validation (runbook wger step 14 runs
check_config on the host) and the real network call."""
import ast
import datetime as dt
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import jinja2
import jinja2.sandbox
import pytest
import yaml

HERE = Path(__file__).resolve().parent
PKG = HERE.parents[1] / "config" / "packages" / "treadmill_wger.yaml"
API = json.loads((HERE / "fixtures" / "wger_2_7_api.json").read_text())
TZ = ZoneInfo("America/Chicago")
EX = API["exercise_treadmill_walk"]["id"]
MINUTES = API["repetition_units"]["Minutes"]


# ------------------------------------------------------------------ package loading
class SecretLoader(yaml.SafeLoader):
    pass


SecretLoader.add_constructor("!secret", lambda loader, node: ("SECRET", loader.construct_scalar(node)))


def load_pkg():
    return yaml.load(PKG.read_text(), Loader=SecretLoader)


# ------------------------------------------------------------------ HA template stand-in
def timestamp_custom(ts, fmt="%Y-%m-%d %H:%M:%S", local=True):
    d = dt.datetime.fromtimestamp(float(ts), tz=TZ if local else dt.timezone.utc)
    return d.strftime(fmt)


def as_timestamp(v, default=None):
    try:
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, dt.datetime):
            return v.timestamp()
        d = dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=TZ)
        return d.timestamp()
    except (TypeError, ValueError):
        if default is None:
            raise
        return default


ENV = jinja2.sandbox.ImmutableSandboxedEnvironment()
ENV.filters.update(timestamp_custom=timestamp_custom, to_json=lambda v: json.dumps(v, ensure_ascii=False),
                   as_timestamp=as_timestamp)
ENV.globals.update(as_timestamp=as_timestamp)


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


def render_raw(template, ctx):
    """rest_command renders url/payload with parse_result=False."""
    return ENV.from_string(template).render(**ctx)


class Stop(Exception):
    pass


class FakeHA:
    def __init__(self, pkg, wger, keys):
        self.pkg, self.wger, self.keys = pkg, wger, keys
        self.notifications, self.log, self.calls = [], [], []

    def run_script(self, name, **fields):
        ctx = dict(fields)
        try:
            self._seq(self.pkg["script"][name]["sequence"], ctx)
        except Stop as e:
            ctx["_stopped"] = str(e)
        return ctx

    def _seq(self, steps, ctx):
        for step in steps:
            if "variables" in step:
                for k, v in step["variables"].items():
                    ctx[k] = render(v, ctx)
            elif "if" in step:
                branch = "then" if render(step["if"], ctx) is True else "else"
                self._seq(step.get(branch, []), ctx)
            elif "stop" in step:
                raise Stop(step["stop"])
            elif "action" in step:
                self._action(step, ctx)
            else:
                raise AssertionError(f"step kind not modelled: {step}")

    def _action(self, step, ctx):
        action = render(step["action"], ctx)
        data = render(step.get("data", {}), ctx)
        if action == "persistent_notification.create":
            self.notifications.append(data)
            return
        if action == "system_log.write":
            self.log.append(data)
            return
        domain, svc = action.split(".", 1)
        assert domain == "rest_command", action
        rc = self.pkg["rest_command"][svc]
        secret = rc["headers"]["authorization"]
        assert secret[0] == "SECRET", "the key must be a !secret header"
        url = render_raw(rc["url"], data)
        body = json.loads(render_raw(rc["payload"], data)) if "payload" in rc else None
        self.calls.append((rc["method"], url, body))
        status, content = self.wger.handle(rc["method"], url, body, self.keys.get(secret[1]))
        if "response_variable" in step:
            ctx[step["response_variable"]] = {"status": status, "content": content}


# ------------------------------------------------------------------ fake wger 2.7
class FakeWger:
    """Owner-scoped like wger: each key sees only its user's rows."""

    def __init__(self, users):
        self.users = users           # token -> login
        self.routines = {"jmack": {"id": 1, "name": "Week (kdk)"}}
        # gen_wger layout: day order 1..7 = Monday..Sunday; walk entry on each day
        self.days = [{"id": d, "routine": 1, "order": d} for d in range(1, 8)]
        self.slots = [{"id": 100 + d, "day": d} for d in range(1, 8)] + \
                     [{"id": 200 + d, "day": d} for d in range(1, 8)]
        self.entries = [{"id": 1000 + d, "slot": 100 + d, "exercise": EX} for d in range(1, 8)] + \
                       [{"id": 2000 + d, "slot": 200 + d, "exercise": 999} for d in range(1, 8)]
        self.sessions, self.logs, self.n = [], [], 0

    @staticmethod
    def page(rows):
        return {"count": len(rows), "next": None, "previous": None, "results": rows}

    def check_fields(self, ep, body, partial=False):
        spec = API["endpoints"][ep]
        unknown = set(body) - set(spec)
        if unknown:
            return {k: ["unknown field"] for k in unknown}
        if not partial:
            missing = [k for k, f in spec.items() if f["required"] and body.get(k) is None]
            if missing:
                return {k: ["This field is required."] for k in missing}
        for k, f in spec.items():
            if k in body and "choices" in f and body[k] not in f["choices"]:
                return {k: [f'"{body[k]}" is not a valid choice.']}
            if k in body and f["type"] == "decimal" and body[k] is not None:
                if not isinstance(body[k], (int, float)) or round(body[k], f["decimal_places"]) != body[k]:
                    return {k: ["not a decimal with the allowed places"]}
            if k in body and f["type"] == "datetime" and body[k] is not None:
                if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", body[k]):
                    return {k: ["Datetime has wrong format."]}
        return None

    def handle(self, method, url, body, token):
        u = urlparse(url)
        assert u.scheme == "https" and u.netloc == "wger.kmkdp.com", url
        who = self.users.get(token)
        if who is None:
            return 403, {"detail": "Invalid token."}
        path = u.path.removeprefix("/api/v2/")
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        mine = [s for s in self.sessions if s["_user"] == who]
        if method == "GET":
            if path == "routine/":
                r = self.routines.get(who)
                return 200, self.page([r] if r and r["name"] == q.get("name") else [])
            if path == "day/":
                return 200, self.page([d for d in self.days if who == "jmack" and str(d["routine"]) == q["routine"]
                                       and str(d["order"]) == q["order"]])
            if path == "slot/":
                return 200, self.page([s for s in self.slots if who == "jmack" and str(s["day"]) == q["day"]])
            if path == "slot-entry/":
                return 200, self.page([e for e in self.entries if who == "jmack" and str(e["exercise"]) == q["exercise"]])
            if path == "workoutsession/":
                lo, hi = as_timestamp(q["datetime_start__gte"]), as_timestamp(q["datetime_start__lt"])
                rows = [s for s in mine if lo <= as_timestamp(s["datetime_start"]) < hi]
                return 200, self.page([{k: v for k, v in s.items() if k != "_user"} for s in rows])
        if method == "POST" and path in ("workoutsession/", "workoutlog/"):
            ep = path.rstrip("/")
            err = self.check_fields(ep, body)
            if err:
                return 400, err
            self.n += 1
            row = dict(body, id=f"0190-{self.n:04d}", _user=who)
            (self.sessions if ep == "workoutsession" else self.logs).append(row)
            return 201, {k: v for k, v in row.items() if k != "_user"}
        m = re.fullmatch(r"workoutsession/([^/]+)/", path)
        if method == "PATCH" and m:
            err = self.check_fields("workoutsession", body, partial=True)
            if err:
                return 400, err
            s = next((s for s in mine if s["id"] == m.group(1)), None)
            if s is None:
                return 404, {"detail": "Not found."}
            s.update(body)
            return 200, {k: v for k, v in s.items() if k != "_user"}
        return 404, {"detail": f"fake: {method} {path} not modelled"}


# ------------------------------------------------------------------ fixtures
def ts(y, mo, d, h, mi):
    return dt.datetime(y, mo, d, h, mi, tzinfo=TZ).timestamp()


FRI_0712 = ts(2026, 11, 6, 7, 12)       # a Friday
FRI_0742 = ts(2026, 11, 6, 7, 42)


@pytest.fixture
def env():
    pkg = load_pkg()
    wger = FakeWger({"tok-jmack": "jmack"})          # rach: placeholder key, no user
    ha = FakeHA(pkg, wger, {"wger_api_key_jmack": "tok-jmack", "wger_api_key_rach": "Token none"})
    return pkg, wger, ha


def walk(ha, walker="Jacob", start=FRI_0712, end=FRI_0742, dist=2410.0):
    return ha.run_script("tm_wger_log_walk", walker=walker, start_ts=start, end_ts=end, dist_m=dist)


# ------------------------------------------------------------------ tests
def test_rest_commands_one_secret_per_login(env):
    pkg, _, _ = env
    rc = pkg["rest_command"]
    for login in ("jmack", "rach"):
        for method in ("get", "post", "patch"):
            c = rc[f"wger_{method}_{login}"]
            assert c["method"] == method.upper()
            assert c["headers"]["authorization"] == ("SECRET", f"wger_api_key_{login}")
            assert c["url"] == "https://wger.kmkdp.com/api/v2/{{ path }}"
            if method != "get":
                assert c["payload"] == "{{ body | to_json }}" and c["content_type"] == "application/json"
    script = PKG.read_text()
    login_map = re.search(r"\{'Jacob': '(\w+)', 'Rachel': '(\w+)'\}", script).groups()
    assert set(login_map) == {"jmack", "rach"}


def test_first_walk_creates_session_and_log(env):
    _, wger, ha = env
    ctx = walk(ha)
    assert "_stopped" not in ctx and not ha.notifications
    (s,) = wger.sessions
    assert s["routine"] == 1 and s["day"] == 5                       # Friday = day order 5
    assert s["impression"] == "2"                                      # Neutral
    assert s["datetime_start"] == "2026-11-06T13:12:00Z" and s["datetime_end"] == "2026-11-06T13:42:00Z"
    assert s["notes"] == "treadmill auto-log: 2.41 km, 4.8 km/h avg, 30.0 min (07:12–07:42)"
    (lg,) = wger.logs
    assert lg == {"exercise": EX, "session": s["id"], "routine": 1, "slot_entry": 1005,
                  "date": "2026-11-06T13:42:00Z", "repetitions": 30.0, "repetitions_unit": MINUTES,
                  "id": lg["id"], "_user": "jmack"}
    assert "weight" not in lg                                          # wger needs reps OR weight


def test_payload_fields_are_wger_2_7_fields(env):
    _, _, ha = env
    walk(ha)
    for method, url, body in ha.calls:
        if body is not None:
            ep = "workoutlog" if "workoutlog" in url else "workoutsession"
            assert set(body) <= set(API["endpoints"][ep]), (url, body)
            assert not {"date", "time_start", "time_end"} & set(body) or ep == "workoutlog"


def test_same_walk_twice_writes_nothing_new(env):
    _, wger, ha = env
    walk(ha)
    n_calls = len(ha.calls)
    ctx = walk(ha)
    assert ctx["_stopped"] == "this walk is already in wger"
    assert len(wger.sessions) == 1 and len(wger.logs) == 1
    assert all(m == "GET" for m, _, _ in ha.calls[n_calls:])


def test_second_walk_same_day_appends_to_the_session(env):
    _, wger, ha = env
    walk(ha)
    walk(ha, start=ts(2026, 11, 6, 10, 0), end=ts(2026, 11, 6, 10, 20), dist=1600.0)
    (s,) = wger.sessions
    assert s["notes"].splitlines() == [
        "treadmill auto-log: 2.41 km, 4.8 km/h avg, 30.0 min (07:12–07:42)",
        "treadmill auto-log: 1.60 km, 4.8 km/h avg, 20.0 min (10:00–10:20)"]
    assert s["datetime_end"] == "2026-11-06T16:20:00Z"                # still within 5 h of the start
    assert [lg["repetitions"] for lg in wger.logs] == [30.0, 20.0]
    assert {lg["session"] for lg in wger.logs} == {s["id"]}


def test_second_walk_past_five_hours_keeps_the_session_end(env):
    _, wger, ha = env
    walk(ha)
    walk(ha, start=ts(2026, 11, 6, 19, 0), end=ts(2026, 11, 6, 19, 20))
    (s,) = wger.sessions
    assert s["datetime_end"] == "2026-11-06T13:42:00Z"               # wger caps a session at 5 h
    assert len(s["notes"].splitlines()) == 2 and len(wger.logs) == 2


def test_app_session_today_is_reused_without_touching_its_times(env):
    _, wger, ha = env
    wger.sessions.append({"id": "app-1", "routine": 1, "day": 5, "notes": None, "impression": "3",
                          "datetime_start": "2026-11-06T12:00:00Z", "datetime_end": None, "_user": "jmack"})
    walk(ha)
    (s,) = wger.sessions
    assert s["notes"] == "treadmill auto-log: 2.41 km, 4.8 km/h avg, 30.0 min (07:12–07:42)"
    assert s["datetime_end"] is None and s["impression"] == "3"
    assert wger.logs[0]["session"] == "app-1"


def test_yesterdays_session_is_not_reused(env):
    _, wger, ha = env
    walk(ha, start=ts(2026, 11, 5, 21, 0), end=ts(2026, 11, 5, 21, 30))     # Thursday evening
    walk(ha)                                                                 # Friday morning
    assert [s["day"] for s in wger.sessions] == [4, 5]
    assert [lg["slot_entry"] for lg in wger.logs] == [1004, 1005]


def test_distance_not_reported(env):
    _, wger, ha = env
    walk(ha, dist=0)
    assert wger.sessions[0]["notes"] == "treadmill auto-log: distance not reported, 30.0 min (07:12–07:42)"
    assert wger.logs[0]["repetitions"] == 30.0


def test_guest_is_skipped_with_a_notification(env):
    _, wger, ha = env
    ctx = walk(ha, walker="Guest")
    assert ctx["_stopped"] == "walker has no wger login"
    assert not ha.calls and not wger.sessions
    (n,) = ha.notifications
    assert n["notification_id"] == "tm_wger_autolog" and '"Guest"' in n["message"]


def test_person_without_key_is_skipped_quietly(env):
    _, wger, ha = env
    ctx = walk(ha, walker="Rachel")
    assert ctx["_stopped"] == "no usable wger key for this person"
    assert not ha.notifications and not wger.sessions
    (w,) = ha.log
    assert w["level"] == "warning" and "rach" in w["message"] and "403" in w["message"]
    assert [m for m, _, _ in ha.calls] == ["GET"]


def test_person_without_routine_still_logs_the_walk(env):
    _, wger, ha = env
    del wger.routines["jmack"]
    walk(ha)
    assert wger.sessions[0]["routine"] is None and wger.sessions[0]["day"] is None
    assert wger.logs[0]["slot_entry"] is None and wger.logs[0]["exercise"] == EX


def test_short_and_stuck_walks(env):
    _, wger, ha = env
    assert walk(ha, end=FRI_0712 + 40)["_stopped"] == "no start time, or shorter than a minute"
    assert walk(ha, start=0)["_stopped"] == "no start time, or shorter than a minute"
    assert walk(ha, end=FRI_0712 + 6 * 3600)["_stopped"] == "longer than a wger session may last"
    assert not wger.sessions and len(ha.notifications) == 1


def test_wger_error_notifies_with_the_walk(env):
    _, wger, ha = env
    wger.handle = lambda *a: (502, "Bad Gateway")
    ctx = walk(ha)
    assert ctx["_stopped"] == "wger unreachable"
    assert "07:12–07:42" in ha.notifications[0]["message"]


def test_automation_triggers_on_the_latch_and_reads_only(env):
    pkg, _, _ = env
    (auto,) = pkg["automation"]
    assert auto["triggers"] == [{"trigger": "state", "entity_id": "input_boolean.tm_session_active",
                                 "from": "on", "to": "off"}]
    text = yaml.safe_dump(auto["actions"])
    assert "set_value" not in text and "set_datetime" not in text and "turn_off" not in text
    assert auto["actions"][-1]["action"] == "script.tm_wger_log_walk"
