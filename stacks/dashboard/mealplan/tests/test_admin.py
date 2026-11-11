"""admin.kmkdp.com: host gating (ADMIN_USERS, 403, 404 off the allowlist),
every status tile (live, not configured, failed, loading), the alerts list,
Authelia sign-in parsing, charts, LogQL escaping (with injection attempts),
the syslog search path and the household Admin link. Fixtures only, no network."""
import datetime
import json
import os
import time
from zoneinfo import ZoneInfo

import pytest

import admin
import app
import devices
from test_kitchen import home  # noqa: F401  (the fixture-wired sidecar)

A = "https://admin.kmkdp.com"
JM = {"Remote-User": "jmack"}
TZ = ZoneInfo("America/Chicago")
NOW = 1794300000.0                      # 2026-11-10 ~01:20 local


def ns(t):
    return int(t * 1e9)


# ---- fixtures (shapes copied from the live APIs, values invented) -------------------
AM_ALERTS = [
    {"labels": {"alertname": "HostDiskFull", "severity": "warning", "instance": "10.1.20.20:9100"},
     "annotations": {"summary": "root disk 91%"}, "startsAt": "2026-11-10T05:20:00.123456789Z",
     "status": {"state": "active"}},
    {"labels": {"alertname": "ProbeDown", "severity": "critical", "instance": "https://x.kmkdp.com"},
     "annotations": {}, "startsAt": "2026-11-10T07:10:00Z", "status": {"state": "active"}},
    {"labels": {"alertname": "Silenced", "severity": "critical"}, "startsAt": "2026-11-10T07:10:00Z",
     "status": {"state": "suppressed"}},
]
PROBES = {"probe": [("https://radarr.kmkdp.com/ping", "blackbox-service", 1.0),
                    ("https://sso.kmkdp.com", "blackbox-service", 0.0),
                    ("https://docs.kmkdp.com/healthz", "blackbox-service", 1.0),
                    ("10.1.1.100", "blackbox-icmp", 1.0),
                    ("http://10.1.30.21:8123", "blackbox-http-app", 1.0)],
          "up": [("10.1.20.20:9100", "node-external", 1.0), ("10.1.3.1", "snmp", 0.0)]}
BACKUP_LINES = [
    (ns(NOW - 16 * 3600), {}, "2026-11-09 09:33:45 event name=backup-verify-verify-1 action=die code=0 project=backup-verify"),
    (ns(NOW - 16.2 * 3600), {}, "2026-11-09 09:21:27 event name=backup-collect-collect-1 action=die code=0 project=backup-collect"),
    (ns(NOW - 16.1 * 3600), {}, "2026-11-09 09:26:04 event name=backup-offsite-offsite-1 action=die code=0 project=backup-offsite"),
]
GH_PULLS = [
    {"number": 451, "draft": True, "title": "feat(dashboard): admin page", "user": {"login": "BigH3ater"}},
    {"number": 450, "draft": True, "title": "feat: add box", "user": {"login": "BigH3ater"}},
    {"number": 430, "draft": False, "title": "chore(deps): update grafana", "user": {"login": "renovate[bot]"}},
    {"number": 431, "draft": False, "title": "chore(deps): update loki", "user": {"login": "renovate[bot]"}},
    {"number": 432, "draft": False, "title": "fix: thing", "user": {"login": "BigH3ater"}},
]
ADV_ROWS = [(ns(NOW - 86400), {"job": "advisory-watch"},
             "ts=2026-11-09T13:03:03Z summary=1 images_checked=78 advisories=99 vulnerable=3 unknown=2 patched=93 source_errors=0")]
P = "sensor.computer_room_eaton_5px_"
HA_STATES = [{"entity_id": P + "status", "state": "Online, Battery Charging"},
             {"entity_id": P + "status_data", "state": "OL CHRG"},
             {"entity_id": P + "load", "state": "36"},
             {"entity_id": P + "battery_charge", "state": "100"},
             {"entity_id": "vacuum.roborock", "state": "docked"}]
KOMODO_BODY = {"updates": [
    {"operation": "DeployStack", "success": True, "status": "Complete", "target": {"type": "Stack", "id": "a1"}},
    {"operation": "DeployStackIfChanged", "success": False, "status": "Complete", "target": {"type": "Stack", "id": "b2"}},
    {"operation": "DeployStack", "success": False, "status": "InProgress", "target": {"type": "Stack", "id": "c3"}},
    {"operation": "RunProcedure", "success": False, "status": "Complete", "target": {"type": "Procedure", "id": "p"}},
], "next_page": None}


def _authelia(msg, t="2026-11-10T06:00:00Z", **extra):
    return json.dumps({"level": "error" if "Unsuccessful" in msg else "info", "method": "POST",
                       "msg": msg, "path": "/api/firstfactor", "remote_ip": "203.0.113.7", "time": t, **extra})


SIGNIN_ROWS = [
    (ns(NOW - 60), {}, _authelia("Unsuccessful 1FA authentication attempt by user 'jmack'", "2026-11-10T07:10:00Z")),
    (ns(NOW - 120), {}, _authelia("Unsuccessful 1FA authentication attempt by user ''", "2026-11-10T07:09:00Z")),
    (ns(NOW - 120), {}, _authelia("Error occurred getting details for user with username input 'bob' which usually indicates they do not exist", "2026-11-10T07:09:00Z")),
    (ns(NOW - 130), {}, _authelia("Error occurred getting details for user with username input 'edge-probe-nonexistent' which usually indicates they do not exist", "2026-11-10T07:08:00Z")),
    (ns(NOW - 200), {}, _authelia("https://wger.kmkdp.com/en/gallery requires 2FA, cannot be redirected yet", "2026-11-10T07:05:00Z")),
    (ns(NOW - 300), {}, _authelia("OpenID Connect 1.0 client requires 2FA", "2026-11-10T07:00:00Z",
                                  username="rach", client_id="actual")),
    (ns(NOW - 400), {}, _authelia("Unsuccessful TOTP authentication attempt by user 'rach' and they are banned until 2026-11-10 08:00:00 -0600 CST", "2026-11-10T06:50:00Z")),
    (ns(NOW - 500), {}, "not json at all"),
    (ns(NOW - 600), {}, _authelia("Access to https://tdarr.kmkdp.com/ (method GET) is not authorized to user <anonymous>")),
]
CHART_SERIES = {"cpu": [(NOW - 900, 8.0), (NOW, 12.0)], "mem": [(NOW - 900, 59.0), (NOW, 60.0)],
                "disk": [(NOW, 37.0)], "nas": [(NOW - 900, 55.4), (NOW, 55.6)],
                "net": [(NOW - 900, 2.1e8), (NOW, 2.4e8)], "ups": None}


def _feeds_ok():
    f = app.ADMIN_FEEDS
    f["alerts"].set(admin.parse_alerts(AM_ALERTS))
    f["probes"].set(PROBES)
    f["backups"].set(admin.parse_backups(BACKUP_LINES))
    f["komodo"].set({"configured": False})
    f["github"].set(admin.parse_github(GH_PULLS))
    f["advisories"].set(admin.parse_advisory_summary(ADV_ROWS))
    f["ups"].set(admin.parse_ups(HA_STATES, P))
    f["charts"].set(CHART_SERIES)
    f["signins"].set(admin.parse_signins(SIGNIN_ROWS))
    f["hosts"].set(app.SYSLOG_HOSTS_FALLBACK)


@pytest.fixture
def adm(home, monkeypatch):  # noqa: F811
    monkeypatch.setattr(app, "SYSLOG_LIMIT", devices.RateLimiter(30, 600))
    _feeds_ok()
    yield home
    for f in app.ADMIN_FEEDS.values():
        f.value, f.ts, f.tried, f.error = None, 0.0, 0.0, ""


def aget(c, path="/", headers=JM, **kw):
    return c.get(path, base_url=A, headers=headers, **kw)


def tiles(src_over=None):
    src = {"alerts": (admin.parse_alerts(AM_ALERTS), "ok"), "probes": (PROBES, "ok"),
           "backups": (admin.parse_backups(BACKUP_LINES), "ok"), "komodo": ({"configured": False}, "ok"),
           "github": (admin.parse_github(GH_PULLS), "ok"),
           "advisories": (admin.parse_advisory_summary(ADV_ROWS), "ok"),
           "ups": (admin.parse_ups(HA_STATES, P), "ok")}
    src.update(src_over or {})
    return {t["label"]: t for t in admin.status_tiles(src, NOW, TZ)}


# ---- host gating -----------------------------------------------------------------------
def test_admin_page_for_admin_user(adm):
    r = aget(adm)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "<title>Lab admin</title>" in body and "Firing alerts" in body and "Syslog search" in body
    assert r.headers["Cache-Control"] == "no-store"
    assert "default-src 'none'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "<script" not in body                       # no JS on the page at all


def test_non_admin_and_anonymous_get_403(adm):
    for headers in ({"Remote-User": "rach"}, {}, {"Remote-User": " "}):
        for path in ("/", "/static/home.css", "/kitchen", "/nope"):
            r = aget(adm, path, headers=headers)
            assert r.status_code == 403 and r.get_data(as_text=True) == "forbidden"


def test_admin_host_serves_only_admin_routes(adm):
    for path in ("/kitchen", "/home", "/groceries", "/kitchen/devices", "/mealplan.json", "/healthz",
                 "/rate", "/pair", "/admin", "/index.html", "/static"):
        assert aget(adm, path).status_code == 404, path
    assert adm.post("/", base_url=A, headers=JM).status_code == 404
    assert adm.post("/groceries/add", base_url=A, headers=JM, data={"item": "x"}).status_code == 404
    r = aget(adm, "/static/home.css")
    assert r.status_code == 200 and "ad-stats" in r.get_data(as_text=True)


def test_admin_page_does_not_exist_on_other_hosts(adm):
    # home.kmkdp.com (default test host) and the kitchen host: no admin page
    r = adm.get("/", headers=JM)
    assert "Lab admin" not in r.get_data(as_text=True)
    r = adm.get("/", base_url="https://kitchen.kmkdp.com", headers=JM)
    assert "Lab admin" not in r.get_data(as_text=True)
    # the kitchen marker wins over an admin Host (it only narrows)
    r = adm.get("/", base_url=A, headers={**JM, "X-Kdk-Kitchen": "1"})
    assert "Lab admin" not in r.get_data(as_text=True)


def test_admin_users_env_list(adm, monkeypatch):
    monkeypatch.setattr(app, "ADMIN_USERS", {"jmack", "ops"})
    assert aget(adm, headers={"Remote-User": "ops"}).status_code == 200
    assert aget(adm, headers={"Remote-User": "rach"}).status_code == 403


def test_household_admin_link_only_for_admins(adm):
    assert "hh-admin" in adm.get("/home", headers=JM).get_data(as_text=True)
    assert "https://admin.kmkdp.com/" in adm.get("/home", headers=JM).get_data(as_text=True)
    assert "hh-admin" not in adm.get("/home", headers={"Remote-User": "rach"}).get_data(as_text=True)
    assert "hh-admin" not in adm.get("/home").get_data(as_text=True)


def test_page_never_blocks_on_upstreams(adm):
    # a feed that was never tried renders "loading" and does not fetch on the request
    for f in app.ADMIN_FEEDS.values():
        f.value, f.ts, f.tried, f.error = None, 0.0, 0.0, ""
    for f in app.ADMIN_FEEDS.values():
        f.kick = lambda: None                       # no background thread in tests
    r = aget(adm)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert body.count("loading") >= 7 and "Loading…" in body
    for f in app.ADMIN_FEEDS.values():
        del f.kick


# ---- status tiles -------------------------------------------------------------------------
def test_tiles_with_live_sources():
    t = tiles()
    assert list(t) == ["Alerts firing", "Services down", "Backups", "Deploys failed", "Draft PRs", "Updates", "UPS"]
    assert t["Alerts firing"]["value"] == "2" and t["Alerts firing"]["sub"] == "1 critical"
    assert t["Alerts firing"]["state"] == "bad"
    assert t["Services down"]["value"] == "2"
    assert "https://sso.kmkdp.com" in t["Services down"]["sub"] and t["Services down"]["state"] == "bad"
    assert t["Backups"]["value"] == "OK" and "3 steps" in t["Backups"]["sub"]
    assert t["Draft PRs"]["value"] == "2" and t["Draft PRs"]["state"] == "warn"
    # the 2 "to review" count too (2 + 3 + 2)
    assert t["Updates"]["value"] == "7" and t["Updates"]["sub"] == "2 Renovate · 3 advisories · 2 to review"
    assert t["UPS"]["value"] == "Online" and t["UPS"]["sub"] == "load 36% · charge 100%"


def test_tile_komodo_not_configured_and_configured():
    assert tiles()["Deploys failed"] == {"label": "Deploys failed", "value": "–", "sub": "not configured", "state": "off",
                                         "href": ""}
    k = {"configured": True, **admin.parse_komodo(KOMODO_BODY)}
    t = tiles({"komodo": (k, "ok")})["Deploys failed"]
    # only finished Deploy* updates count; the in-progress one and the procedure do not
    assert t["value"] == "1" and t["sub"] == "of 3 in 24 h" and t["state"] == "bad"


def test_komodo_fetch_without_key_makes_no_call(monkeypatch):
    monkeypatch.setattr(admin.requests, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("called")))
    assert admin.fetch_komodo("https://komodo.example", "", "") == {"configured": False}


def test_komodo_fetch_sends_read_only_listupdates(monkeypatch):
    seen = {}

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return KOMODO_BODY

    def post(url, **kw):
        seen.update(url=url, **kw)
        return R()
    monkeypatch.setattr(admin.requests, "post", post)
    out = admin.fetch_komodo("https://komodo.example", "k", "s", now=NOW)
    assert seen["url"] == "https://komodo.example/read"
    assert seen["json"]["type"] == "ListUpdates"
    assert seen["json"]["params"]["query"]["start_ts"]["$gte"] == int((NOW - 86400) * 1000)
    assert out["configured"] and out["failed"] == 1


def test_tiles_failed_and_loading_sources():
    failed = {k: (None, "failed") for k in ("alerts", "probes", "backups", "komodo", "github", "advisories", "ups")}
    t = tiles(failed)
    for label in ("Alerts firing", "Services down", "Backups", "Deploys failed", "Draft PRs", "Updates", "UPS"):
        assert t[label]["value"] == "?" and "unreachable" in t[label]["sub"] and t[label]["state"] == "bad", label
    loading = {k: (None, "loading") for k in failed}
    t = tiles(loading)
    assert all(x["value"] == "…" and x["sub"] == "loading" for x in t.values())


def test_tiles_partial_and_empty_values():
    t = tiles({"alerts": ([], "ok"), "probes": ({"probe": [("a", "j", 1.0)], "up": []}, "ok"),
               "backups": ([], "ok"), "advisories": (None, "failed"),
               "ups": (admin.parse_ups([], P), "ok")})
    assert t["Alerts firing"]["value"] == "0" and t["Alerts firing"]["state"] == "ok"
    assert t["Services down"]["sub"] == "1 targets up"
    assert t["Backups"]["value"] == "No run" and t["Backups"]["state"] == "bad"
    assert t["Updates"]["sub"] == "2 Renovate · advisories ?"
    assert t["UPS"]["sub"] == "no NUT sensors in HA"


def test_backup_failure_and_latest_run_wins():
    rows = BACKUP_LINES + [(ns(NOW - 17 * 3600), {}, "event name=backup-verify-verify-1 action=die code=1 ")]
    t = tiles({"backups": (admin.parse_backups(rows), "ok")})
    assert t["Backups"]["value"] == "OK"                 # the later code=0 run replaces the older failure
    rows = BACKUP_LINES + [(ns(NOW - 15 * 3600), {}, "event name=backup-dmz-rsync-1 action=die code=23 ")]
    t = tiles({"backups": (admin.parse_backups(rows), "ok")})
    assert t["Backups"]["value"] == "1 failed" and t["Backups"]["state"] == "bad"


def test_ups_on_battery_and_runtime():
    st = admin.parse_ups(HA_STATES + [{"entity_id": P + "battery_runtime", "state": "1800"}], P)
    st["flags"] = "OB DISCHRG"
    t = tiles({"ups": (st, "ok")})["UPS"]
    assert t["value"] == "On battery" and t["state"] == "bad" and t["sub"].endswith("30 min")


def test_github_parse():
    g = admin.parse_github(GH_PULLS)
    assert g == {"open": 5, "drafts": 2, "renovate": 2,
                 "draft_titles": ["feat(dashboard): admin page", "feat: add box"]}


def test_tile_text_is_escaped(adm):
    app.ADMIN_FEEDS["probes"].set({"probe": [("<script>x</script>", "j", 0.0)], "up": []})
    body = aget(adm).get_data(as_text=True)
    assert "<script>x" not in body and "&lt;script&gt;x" in body


# ---- alerts list ----------------------------------------------------------------------------
def test_alerts_sorted_and_suppressed_dropped():
    a = admin.parse_alerts(AM_ALERTS)
    assert [x["name"] for x in a] == ["ProbeDown", "HostDiskFull"]
    assert a[1]["instance"] == "10.1.20.20:9100" and a[1]["summary"] == "root disk 91%"
    assert a[1]["since"] == pytest.approx(datetime.datetime(2026, 11, 10, 5, 20, 0, 123456,
                                                             tzinfo=datetime.timezone.utc).timestamp())


def test_alert_rows_link_to_alertmanager_filter(adm, monkeypatch):
    monkeypatch.setattr(app.time, "time", lambda: NOW)
    body = aget(adm).get_data(as_text=True)
    assert "HostDiskFull" in body and "ProbeDown" in body
    link = admin.am_filter_link("https://alertmanager.kmkdp.com", admin.parse_alerts(AM_ALERTS)[1])
    assert link == ("https://alertmanager.kmkdp.com/#/alerts?silenced=false&inhibited=false&active=true&filter="
                    "%7Balertname%3D%22HostDiskFull%22%2Cinstance%3D%2210.1.20.20%3A9100%22%7D")
    assert link.replace("&", "&amp;") in body
    assert ">critical<" in body and ">warning<" in body


def test_alert_label_quotes_are_escaped_in_the_filter():
    a = {"name": 'X"}', "labels": {"instance": 'a"b'}}
    link = admin.am_filter_link("https://am", a)
    assert "%22X%5C%22%7D%22" in link and "a%5C%22b" in link


def test_alerts_empty_and_failed_render(adm):
    app.ADMIN_FEEDS["alerts"].set([])
    assert "Nothing firing." in aget(adm).get_data(as_text=True)
    f = app.ADMIN_FEEDS["alerts"]
    f.value, f.tried, f.error = None, time.time(), "ConnectTimeout"
    f.kick = lambda: None
    assert "Alertmanager is unreachable right now." in aget(adm).get_data(as_text=True)
    del f.kick


# ---- sign-ins -------------------------------------------------------------------------------
def test_signin_parsing():
    s = admin.parse_signins(SIGNIN_ROWS)
    rows = [(r["user"], r["app"], r["result"]) for r in s["rows"]]
    assert rows == [("jmack", "1FA sign-in", "failed"),
                    ("bob", "unknown user", "failed"),
                    ("", "wger.kmkdp.com", "ok"),
                    ("rach", "OIDC actual", "ok"),
                    ("rach", "TOTP sign-in", "banned")]
    assert s["hidden_probe"] == 1
    assert s["rows"][0]["ts"] == datetime.datetime(2026, 11, 10, 7, 10, tzinfo=datetime.timezone.utc).timestamp()


def test_signins_render(adm):
    body = aget(adm).get_data(as_text=True)
    assert "Recent sign-ins" in body and "1 edge-probe attempts hidden." in body
    assert "wger.kmkdp.com" in body and "OIDC actual" in body and ">banned<" in body
    assert "203.0.113.7" not in body                    # no client IPs on the page


def test_signin_hostile_values_escaped(adm):
    rows = [(ns(NOW), {}, _authelia("Unsuccessful 1FA authentication attempt by user '<img src=x>'"))]
    app.ADMIN_FEEDS["signins"].set(admin.parse_signins(rows))
    body = aget(adm).get_data(as_text=True)
    assert "<img src=x>" not in body and "&lt;img src=x&gt;" in body


def test_signin_logql_is_fixed():
    assert admin.SIGNIN_LOGQL == '{container="authelia"} |~ "Unsuccessful|getting details for user|requires 2FA"'


# ---- charts ---------------------------------------------------------------------------------
def test_chart_defs_link_to_dashboards():
    defs = {c["key"]: c for c in app.CHART_DEFS}
    assert [c["label"] for c in app.CHART_DEFS] == ["Host CPU", "Host memory", "Disk used", "NAS space", "Network", "UPS load"]
    assert defs["cpu"]["href"] == "https://grafana.kmkdp.com/d/rYdddlPWk"
    assert defs["nas"]["href"] == "https://grafana.kmkdp.com/d/zfs-zpool-health"
    assert 'device=~"tankz3(/.*)?"' in defs["nas"]["q"] and 'instance="10.1.3.5:9100"' in defs["nas"]["q"]
    assert defs["ups"]["ha"] == P + "load" and defs["ups"]["href"].startswith("https://homeassistant.kmkdp.com/history?")


def test_spark_points_and_values():
    assert admin.spark_points([(0, 0.0), (10, 10.0)]) == "0.0,33.0 120.0,3.0"
    assert admin.spark_points([(0, 5.0), (10, 5.0)]) == "0.0,18.0 120.0,18.0"
    assert admin.spark_points([(0, 5.0)]) == "0.0,18.0 120.0,18.0"
    assert admin.spark_points([]) == ""
    assert admin.fmt_value(2.4e8, "bps") == "240 Mb/s"
    assert admin.fmt_value(1.25e9, "bps") == "1.2 Gb/s"
    assert admin.fmt_value(55.6, "%") == "56%"
    assert admin.fmt_value(None, "%") == "–"


def test_charts_render_and_one_missing_series(adm):
    body = aget(adm).get_data(as_text=True)
    assert body.count("<polyline points=") == 5
    # the UPS series failed (None): its source is named, like the other cards
    assert "240 Mb/s" in body and "56%" in body and "Home Assistant unreachable" in body
    assert "no data" not in body


def test_charts_unreachable_vs_empty_vs_loading():
    defs = app.CHART_DEFS
    # the whole feed failed (Prometheus and HA both down)
    html = admin._charts_html(defs, None, "failed")
    assert html.count("Prometheus unreachable") == 5 and html.count("Home Assistant unreachable") == 1
    assert "no data" not in html and "class='ad-nodata bad'" in html
    # a source answered with an empty series: "no data", not "unreachable"
    html = admin._charts_html(defs, dict(CHART_SERIES, cpu=[], ups=[]), "ok")
    assert html.count("no data") == 2 and "unreachable" not in html
    # one series failed inside an otherwise good fetch
    html = admin._charts_html(defs, dict(CHART_SERIES, nas=None, ups=[(NOW, 30.0)]), "ok")
    assert html.count("Prometheus unreachable") == 1 and "Home Assistant" not in html.split("UPS load")[1]
    html = admin._charts_html(defs, None, "loading")
    assert html.count(">loading<") == 6


def test_fetch_charts_isolates_failures(monkeypatch):
    def prom(base, q, s, e, step):
        if "node_memory" in q:
            raise TimeoutError
        return [(s, 1.0), (e, 2.0)]
    monkeypatch.setattr(admin, "prom_range", prom)
    monkeypatch.setattr(admin, "ha_history", lambda *a: [(1, 30.0), (2, 31.0)])
    out = admin.fetch_charts("p", "h", "t", app.CHART_DEFS, now=NOW)
    assert out["mem"] is None and out["cpu"] == [(NOW - 86400, 1.0), (NOW, 2.0)] and out["ups"][-1] == (2, 31.0)


def test_ha_history_parses_minimal_response(monkeypatch):
    t0 = NOW - 3600
    iso = lambda t: datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat()  # noqa: E731
    body = [[{"entity_id": P + "load", "state": "35", "last_changed": iso(t0)},
             {"state": "unavailable", "last_changed": iso(t0 + 60)},
             {"state": "37", "last_changed": iso(t0 + 120)},      # same 15 min bucket -> averaged
             {"state": "40", "last_changed": iso(t0 + 1800)}]]
    monkeypatch.setattr(admin, "_get_json", lambda *a, **k: body)
    pts = admin.ha_history("h", "tok", P + "load", 86400, now=NOW)
    assert [v for _t, v in pts] == [36.0, 40.0]


def test_syslog_lines_lose_ansi_codes(monkeypatch):
    monkeypatch.setattr(admin, "loki_range", lambda *a, **k: [
        (ns(NOW), {"host": "kdk-log-01"}, "#033[2mtime#033[0m #033[31mERROR#033[0m x \x1b[1mbold\x1b[0m")])
    assert admin.search_syslog("l", "x", "", "1h")[0]["line"] == "time ERROR x bold"


# ---- service tiles ----------------------------------------------------------------------------
def test_service_groups_and_dots(adm):
    assert [g for g, _ in admin.TILES] == ["Media ops", "Books", "Home automation", "Identity", "Deploy",
                                           "Infrastructure", "Monitoring", "Household data"]
    st = admin.probe_status(PROBES)
    assert admin.tile_dot("https://radarr.kmkdp.com/ping", st) == "up"
    assert admin.tile_dot("https://sso.kmkdp.com", st) == "down"
    assert admin.tile_dot("10.1.1.100", st) == "up"                       # ICMP probe of that host
    assert admin.tile_dot("http://10.1.30.21:8123", st) == "up"
    assert admin.tile_dot("https://homeassistant.kmkdp.com", st) is None   # no series (yet)
    assert admin.tile_dot(None, st) is None                               # tile without a probe
    # scrape targets are keyed job@instance: the snmp exporter being down for
    # 10.1.3.1 does not paint the UniFi console's ICMP probe
    assert st["node-external@10.1.20.20:9100"] is True and st["snmp@10.1.3.1"] is False
    assert "10.1.3.1" not in st
    body = aget(adm).get_data(as_text=True)
    assert body.count("class='dot up'") >= 3 and "class='dot down'" in body
    assert "https://wizarr.kmkdp.com/admin" in body and "https://github.com/BigH3ater/kdk-lab-dkr" in body


def test_service_groups_pack_instead_of_stretching_rows(adm):
    """2026-11-11 screenshot: the groups sat in rows as tall as the tallest card,
    leaving an empty band under Books / Home automation / Identity. The grid now
    packs: 2 px rows, dense flow, each card spanning (60 + items x 34|44 px) / 2."""
    css = open(os.path.join(os.path.dirname(app.__file__), "static", "home.css")).read()

    def rule(block, sel):
        return block.split(sel + "{", 1)[1].split("}", 1)[0]

    grid = rule(css, ".ad-groups")
    for decl in ("grid-template-columns:repeat(4,minmax(0,1fr))", "grid-auto-rows:2px",
                 "grid-auto-flow:row dense", "row-gap:0", "align-items:start", "--svc:34px"):
        assert decl in grid, decl
    assert "grid-row:span var(--rd)" in rule(css, ".ad-group")
    # card height is fixed by construction: 22 px heading line, rows exactly --svc tall
    assert "line-height:22px" in rule(css, ".ad-group h3") and "white-space:nowrap" in rule(css, ".ad-group h3")
    assert "height:var(--svc)" in rule(css, ".ad-svc")
    touch = css.split("@media (max-width:1180px),(pointer:coarse){", 1)[1].split("\n}", 1)[0]
    assert ".ad-groups{--svc:44px}" in touch and ".ad-group{grid-row:span var(--rt)}" in touch
    phone = css.rsplit("@media (max-width:760px){", 1)[1].split("\n}", 1)[0]   # the admin block is last
    assert ".ad-groups{grid-auto-rows:auto;row-gap:12px}" in phone and ".ad-group{grid-row:auto}" in phone
    body = aget(adm).get_data(as_text=True)
    assert "style=" not in body                        # the admin CSP allows no inline styles
    for name, items in admin.TILES:
        n = len(items)
        assert f"<div class='card ad-group ad-n{n}'><h3>{name}</h3>" in body
        assert f".ad-n{n}{{--rd:{30 + 17 * n};--rt:{30 + 22 * n}}}" in css   # every group size has a span


def _tile(label):
    return next(t for _g, items in admin.TILES for t in items if t[0] == label)


def test_each_tile_maps_to_its_own_probe():
    # Home Assistant by its hostname, not the IP probe; Grafana and the Loki
    # Explore link share a host but not a probe
    assert _tile("Home Assistant")[3] == "https://homeassistant.kmkdp.com"
    assert _tile("Grafana")[3] == "https://grafana.kmkdp.com/api/health"
    assert _tile("Loki (Grafana Explore)")[3] == "loki@10.1.20.21:3100"
    st = admin.probe_status({"probe": [("https://grafana.kmkdp.com/api/health", "blackbox-service", 0.0)],
                             "up": [("10.1.20.21:3100", "loki", 1.0)]})
    assert admin.tile_dot(_tile("Grafana")[3], st) == "down"
    assert admin.tile_dot(_tile("Loki (Grafana Explore)")[3], st) == "up"
    # the only tile without a dot: GitHub (external); Calibre retired in #458
    assert sorted(t[0] for _g, items in admin.TILES for t in items if t[3] is None) == [
        "GitHub repo"]


def test_new_reading_tiles_link_working_paths():
    books = dict((t[0], t) for t in dict(admin.TILES)["Books"])
    assert books["Reading catalogue"][1] == "https://opds.kmkdp.com/opds"
    assert books["Reading sync"][1] == "https://kosync.kmkdp.com/healthcheck"


def test_every_tile_probe_is_a_prometheus_target():
    """Each tile's probe is a blackbox target (or, as job@host:port, a scrape
    target) in the mon-01 Prometheus config, so a typo never leaves a blank dot."""
    import os
    import yaml
    path = os.path.join(os.path.dirname(app.__file__), "..", "..", "monitoring", "prometheus", "prometheus.yml")
    cfg = yaml.safe_load(open(path))
    keys = set()
    for job in cfg["scrape_configs"]:
        for sc in job.get("static_configs", []):
            for t in sc["targets"]:
                keys.add(t if job["job_name"].startswith("blackbox-") else f"{job['job_name']}@{t}")
    for _g, items in admin.TILES:
        for label, _href, _host, probe in items:
            assert probe is None or probe in keys, (label, probe)


def test_updates_sub_line_wraps_between_parts():
    html = admin._stats_html([admin._tile("Updates", "7", "1 Renovate · 3 advisories · 3 to review", "warn")])
    assert ("<span>1 Renovate ·</span> <span>3 advisories ·</span> <span>3 to review</span>") in html
    t = tiles({"advisories": ({"vulnerable": 0, "unknown": 4}, "ok")})["Updates"]
    assert t["value"] == "6" and t["state"] == "warn"                     # 2 Renovate + 4 to review


# Every link the retired Homepage service directory had (config/services.yaml,
# removed 2026-11-10). Each must still be reachable from the admin tiles, the
# household app grid or the shared sidebar, so retiring Homepage loses no link.
HOMEPAGE_HREFS = [
    "https://recipes.kmkdp.com/mealplan", "https://jellyfin.kmkdp.com/",
    "https://audiobookshelf.kmkdp.com/", "https://jellystat.kmkdp.com/",
    "https://radarr.kmkdp.com/", "https://sonarr.kmkdp.com/", "https://bazarr.kmkdp.com/",
    "https://prowlarr.kmkdp.com/", "https://sabnzbd.kmkdp.com/", "https://tdarr.kmkdp.com/",
    "https://trailarr.kmkdp.com/", "https://seerr.kmkdp.com/", "https://libreseerr.kmkdp.com/",
    "https://lazylibrarian.kmkdp.com/",  # calibre.kmkdp.com retired in #458
    "https://wizarr.kmkdp.com/admin", "https://immich.kmkdp.com/", "https://recipes.kmkdp.com/",
    "https://homeassistant.kmkdp.com/", "https://scrypted.kmkdp.com/",
    "https://zigbee.kmkdp.com/", "https://nodered.kmkdp.com/", "https://budget.kmkdp.com/",
    "https://tasks.kmkdp.com/", "https://wger.kmkdp.com/", "https://search.kmkdp.com/",
    "https://sso.kmkdp.com/", "https://users.kmkdp.com/", "https://komodo.kmkdp.com/",
    "https://10.1.1.100:8006/", "https://10.1.1.102:8007/", "https://10.1.3.5/",
    "https://10.1.3.1/", "http://10.1.1.120/", "https://grafana.kmkdp.com/",
    "http://10.1.20.30:9090/", "https://alertmanager.kmkdp.com/", "https://docs.kmkdp.com/",
]


def _shared_nav_hrefs() -> set[str]:
    import re
    path = os.path.join(os.path.dirname(app.__file__), "..", "guides", "shared-nav.js")
    return set(re.findall(r'\["[^"]+",\s*"([^"]+)"\]', open(path).read()))


def test_no_homepage_link_lost():
    import kitchen
    have = {href for _g, items in admin.TILES for _n, href, _h, _p in items}
    have |= {href for _n, href in kitchen.APPS}
    nav = _shared_nav_hrefs()
    assert "https://search.kmkdp.com/" in nav and len(nav) > 10
    have |= nav
    missing = [h for h in HOMEPAGE_HREFS if h not in have]
    assert not missing, missing


def test_shared_nav_home_is_household_page():
    path = os.path.join(os.path.dirname(app.__file__), "..", "guides", "shared-nav.js")
    js = open(path).read()
    assert '["Home",              "https://home.kmkdp.com/meal/home"]' in js
    assert 'var HOME = "https://home.kmkdp.com/meal/home";' in js
    assert '"https://home.kmkdp.com/"' not in js


def test_root_redirects_to_household_page():
    # home.kmkdp.com/ is a Traefik redirect (the sidecar's own / is the week grid):
    # the catch-all router must outrank the old Homepage router name/priority and
    # keep forwardauth, and Homepage itself must be gone from the stack.
    import yaml
    path = os.path.join(os.path.dirname(app.__file__), "..", "compose.yaml")
    stack = yaml.safe_load(open(path))
    assert "homepage" not in stack["services"]
    labels = stack["services"]["mealplan"]["labels"]
    assert labels["traefik.http.routers.home-root.rule"] == "Host(`home.kmkdp.com`)"
    assert int(labels["traefik.http.routers.home-root.priority"]) == 2
    assert labels["traefik.http.routers.home-root.middlewares"] == "forwardauth@file,home-to-household"
    assert labels["traefik.http.middlewares.home-to-household.redirectregex.replacement"] == \
        "https://home.kmkdp.com/meal/home"
    assert int(labels["traefik.http.routers.mealplan.priority"]) > 2
    assert not any(k.startswith("traefik.http.routers.dashboard.") for k in labels)
    for svc in stack["services"].values():
        assert "./config:/app/config" not in (svc.get("volumes") or [])


# ---- LogQL escaping ---------------------------------------------------------------------------
def test_logql_string_escapes_quotes_and_backslashes():
    assert admin.logql_string('a"b\\c') == '"a\\"b\\\\c"'


def test_syslog_logql_plain_and_host():
    assert admin.syslog_logql("error") == '{job="syslog"} |~ "(?i)error"'
    assert admin.syslog_logql("disk full", "kdk-dkr-01") == '{job="syslog", host="kdk-dkr-01"} |~ "(?i)disk full"'
    assert admin.syslog_logql("") == '{job="syslog"}'


def test_syslog_logql_regex_metacharacters_are_literal():
    q = admin.syslog_logql("a.b*(c)|[d]{2}^$+?\\")
    assert q == '{job="syslog"} |~ "(?i)a\\\\.b\\\\*\\\\(c\\\\)\\\\|\\\\[d\\\\]\\\\{2\\\\}\\\\^\\\\$\\\\+\\\\?\\\\\\\\"'


def _unquote_go(lit: str) -> str:
    """What Loki's parser sees inside a double-quoted literal (Go strconv.Unquote)."""
    assert lit[0] == lit[-1] == '"'
    out, i, body = [], 0, lit[1:-1]
    while i < len(body):
        c = body[i]
        if c == '"':
            raise AssertionError("unescaped quote ends the literal early")
        if c == "\\":
            out.append(body[i + 1])
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


@pytest.mark.parametrize("evil", [
    '"} or {job=~".+',
    '" | json | line_format "{{.password}}',
    '`} |= `x',
    '\\" } {job="docker',
    '"} |~ ".*" or vector(1) #',
    "{{ .Values }}",
])
def test_injection_stays_inside_one_literal(evil):
    q = admin.syslog_logql(evil)
    prefix = '{job="syslog"} |~ '
    assert q.startswith(prefix)
    lit = q[len(prefix):]
    regex = _unquote_go(lit)                     # one well-formed literal, nothing after it
    assert regex == "(?i)" + admin.re2_literal(evil)
    # and the regex matches the user's text literally
    import re as pyre
    assert pyre.search(regex[4:], evil)


def test_host_injection_rejected(adm):
    for host in ('kdk-dkr-01", job="docker', "KDK", "x" * 70, "../etc", "not-a-known-host"):
        r = aget(adm, query_string={"q": "error", "host": host})
        assert "Unknown host." in r.get_data(as_text=True)


def test_clean_query_limits():
    assert admin.clean_query("  error  ") == "error"
    with pytest.raises(ValueError):
        admin.clean_query("x" * 201)
    for bad in ("a\nb", "a\rb", "a\x00b", "a\tb", "a\x7fb"):
        with pytest.raises(ValueError):
            admin.clean_query(bad)


def test_explore_url_carries_the_same_query():
    import urllib.parse as up
    expr = admin.syslog_logql('say "hi"', "kdk-gw-01")
    u = admin.explore_url("https://grafana.kmkdp.com", expr, "6h")
    qs = up.parse_qs(up.urlsplit(u).query)
    panes = json.loads(qs["panes"][0])
    assert panes["a"]["queries"][0]["expr"] == expr
    assert panes["a"]["datasource"] == "loki-dkr01" and panes["a"]["range"]["from"] == "now-6h"


# ---- syslog search path -------------------------------------------------------------------------
def test_syslog_search_runs_server_side(adm, monkeypatch):
    seen = {}

    def fake(loki, q, seconds, limit, timeout=10, now=None):
        seen.update(loki=loki, q=q, seconds=seconds, limit=limit, timeout=timeout)
        return [(ns(NOW), {"host": "kdk-dkr-02"}, 'omfwd: error <b>x</b> "quoted"')]
    monkeypatch.setattr(admin, "loki_range", fake)
    r = aget(adm, query_string={"q": "error", "host": "kdk-dkr-02", "range": "6h"})
    body = r.get_data(as_text=True)
    assert seen == {"loki": "http://10.1.20.21:3100", "q": '{job="syslog", host="kdk-dkr-02"} |~ "(?i)error"',
                    "seconds": 21600, "limit": 200, "timeout": 10}
    assert "omfwd: error &lt;b&gt;x&lt;/b&gt;" in body and "<b>x</b>" not in body
    assert "http-equiv='refresh'" not in body            # a search result page does not auto-reload
    assert "Open in Grafana Explore" in body and "explore?schemaVersion=1" in body
    assert "<option value='kdk-dkr-02' selected>" in body and "<option value='6h' selected>" in body


def test_syslog_search_bad_range_defaults_and_timeout(adm, monkeypatch):
    def boom(*a, **k):
        raise TimeoutError
    monkeypatch.setattr(admin, "loki_range", boom)
    body = aget(adm, query_string={"q": "x", "range": "999d"}).get_data(as_text=True)
    assert "Loki did not answer in time." in body and "<option value='1h' selected>" in body


def test_syslog_search_rate_limited(adm, monkeypatch):
    calls = []
    monkeypatch.setattr(admin, "loki_range", lambda *a, **k: calls.append(1) or [])
    monkeypatch.setattr(app, "SYSLOG_LIMIT", devices.RateLimiter(2, 600))
    for _ in range(2):
        assert "No matching lines in the last 1 hour." in aget(adm, query_string={"q": "x"}).get_data(as_text=True)
    assert "Too many searches" in aget(adm, query_string={"q": "x"}).get_data(as_text=True)
    assert len(calls) == 2


def test_syslog_search_overlong_makes_no_call(adm, monkeypatch):
    monkeypatch.setattr(admin, "loki_range", lambda *a, **k: (_ for _ in ()).throw(AssertionError("called")))
    assert "limited to 200" in aget(adm, query_string={"q": "x" * 300}).get_data(as_text=True)
    assert "control characters" in aget(adm, query_string={"q": "a\nb"}).get_data(as_text=True)


def test_syslog_empty_search_lists_newest_lines(adm, monkeypatch):
    seen = {}

    def fake(loki, q, seconds, limit, timeout=10, now=None):
        seen.update(q=q, limit=limit)
        return [(ns(NOW), {"host": "kdk-dkr-01"}, "newest line")]
    monkeypatch.setattr(admin, "loki_range", fake)
    body = aget(adm, query_string={"q": "  "}).get_data(as_text=True)
    assert seen == {"q": '{job="syslog"}', "limit": 200}                  # no line filter, same cap
    assert "newest line" in body and "No search text: the newest lines." in body
    assert "Type something" not in body


def test_syslog_query_that_is_a_host_name_filters_by_host(adm, monkeypatch):
    seen = {}
    monkeypatch.setattr(admin, "loki_range", lambda loki, q, *a, **k: seen.update(q=q) or [])
    body = aget(adm, query_string={"q": "kdk-dkr-02"}).get_data(as_text=True)
    assert seen["q"] == '{job="syslog", host="kdk-dkr-02"}'
    assert "is a host: showing that host" in body and "<option value='kdk-dkr-02' selected>" in body
    # a partial name is still a text search; an explicit host keeps the text as a line filter
    aget(adm, query_string={"q": "kdk-dkr"})
    assert seen["q"] == '{job="syslog"} |~ "(?i)kdk-dkr"'
    aget(adm, query_string={"q": "kdk-dkr-02", "host": "kdk-log-01"})
    assert seen["q"] == '{job="syslog", host="kdk-log-01"} |~ "(?i)kdk-dkr-02"'


def test_syslog_zero_results_offer_24h(adm, monkeypatch):
    monkeypatch.setattr(admin, "loki_range", lambda *a, **k: [])
    body = aget(adm, query_string={"q": "disk <full>", "range": "6h"}).get_data(as_text=True)
    assert "No matching lines in the last 6 hours." in body
    assert "href='/?q=disk+%3Cfull%3E&amp;host=&amp;range=24h'" in body
    body = aget(adm, query_string={"q": "x", "range": "24h"}).get_data(as_text=True)
    assert "No matching lines in the last 24 hours." in body and "Search the last 24 hours" not in body


def test_syslog_errors_are_told_apart(adm, monkeypatch):
    import requests

    def raiser(exc):
        def f(*a, **k):
            raise exc
        return f
    monkeypatch.setattr(admin, "loki_range", raiser(requests.ReadTimeout()))
    assert "Loki did not answer in time." in aget(adm, query_string={"q": "x"}).get_data(as_text=True)
    monkeypatch.setattr(admin, "loki_range", raiser(admin.UpstreamError(
        400, "parse error at line 1, col 5: <syntax error> " + "x" * 600)))
    body = aget(adm, query_string={"q": "x"}).get_data(as_text=True)
    assert "Loki returned an error (400): parse error at line 1, col 5: &lt;syntax error&gt;" in body
    assert "<syntax error>" not in body and "x" * 201 not in body                   # escaped, truncated
    monkeypatch.setattr(admin, "loki_range", raiser(ValueError("boom")))
    assert "Loki search failed (ValueError)" in aget(adm, query_string={"q": "x"}).get_data(as_text=True)


def test_get_json_carries_the_error_body(monkeypatch):
    class R:
        status_code, text = 400, "  too many outstanding requests  "
    monkeypatch.setattr(admin.requests, "get", lambda *a, **k: R())
    with pytest.raises(admin.UpstreamError) as e:
        admin._get_json("http://loki")
    assert e.value.status == 400 and e.value.body == "too many outstanding requests"


def test_search_input_value_is_escaped(adm, monkeypatch):
    monkeypatch.setattr(admin, "loki_range", lambda *a, **k: [])
    body = aget(adm, query_string={"q": "'><script>alert(1)</script>"}).get_data(as_text=True)
    assert "<script>alert" not in body and "&#39;&gt;&lt;script&gt;" in body


def test_syslog_query_text_not_logged(adm, monkeypatch, caplog):
    import logging
    monkeypatch.setattr(admin, "loki_range", lambda *a, **k: [])
    with caplog.at_level(logging.INFO, logger="mealplan"):
        aget(adm, query_string={"q": "secret-looking-text"})
    assert "secret-looking-text" not in caplog.text and "admin: syslog search user=jmack" in caplog.text


# ---- feed retry -----------------------------------------------------------------------------------
def test_feed_retry_override():
    f = app.Feed("t", 900, lambda: 1, retry=300)
    f.tried = time.time() - 100
    assert not f.due()
    f.tried = time.time() - 301
    assert f.due()
    assert app.Feed("u", 900, lambda: 1).retry == 60


# ---- service-group packing (review 2026-11-11) ------------------------------------
def test_every_tile_group_has_a_span_class():
    """home.css defines .ad-n1..12; a bigger group falls back to the .ad-group
    default span (blank space, never overlap). Keep groups within 12."""
    import re
    css = open(os.path.join(os.path.dirname(app.__file__), "static", "home.css")).read()
    have = {int(n) for n in re.findall(r"\.ad-n(\d+)\{--rd:", css)}
    for name, items in admin.TILES:
        assert 1 <= len(items) <= 12 and len(items) in have, name
    group = css.split("\n.ad-group{", 1)[1].split("}", 1)[0]
    rd = int(re.search(r"--rd:(\d+)", group).group(1))
    rt = int(re.search(r"--rt:(\d+)", group).group(1))
    assert rd >= (60 + 13 * 34) // 2 and rt >= (60 + 13 * 44) // 2     # covers a 13th item
