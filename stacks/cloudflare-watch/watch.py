#!/usr/bin/env python3
"""Read-only Cloudflare edge watch for the kmkdp.com zone.

Emits one JSON line per check to stdout. The host Alloy (observability-agent)
ships this container's stdout to Loki as {container="cloudflare-watch"}; the
Loki ruler (stacks/loki/rules/cloudflare.yml) pages through Alertmanager.

Two run modes, one per schedule:
  (default)   hourly checks: tunnel health, Universal SSL expiry, zone-setting
              drift, firewall-events summary.
  --weekly    weekly check: sha of the custom + rate-limit rule set and of the
              DNS record set, compared against the last run (state file).

Stdlib only (urllib + json + hashlib). The token is zone-scoped read-only
(op://kdk-cluster/cloudflare-monitor-ro/credential): Zone/DNS/Firewall/SSL/
Analytics Read. It CANNOT read account notification policies, and the zone
GraphQL analytics path (firewallEventsAdaptiveGroups) returns an auth error on
this plan/token -- that check then reports available=false and does not page.

Every line carries: check, status (ok|warn|fail), ts (UTC), mode, zone, plus
per-check fields. The token value is never printed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

API = "https://api.cloudflare.com/client/v4"
GRAPHQL = "https://api.cloudflare.com/client/v4/graphql"

TOKEN = os.environ.get("CF_API_TOKEN", "").strip()
ZONE_NAME = os.environ.get("CF_ZONE", "kmkdp.com").strip()
STATE_PATH = os.environ.get("STATE", "/state/state.json")
# Hostnames whose proxied CNAMEs ride the tunnel; used by the probe fallback.
TUNNEL_HOSTS = [h.strip() for h in os.environ.get(
    "TUNNEL_HOSTS", "sso.kmkdp.com,jellyfin.kmkdp.com").split(",") if h.strip()]
HTTP_TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", "25"))

# Expected zone settings. A live value different from this is drift (status
# fail). Keep this map small and explicit -- it is the policy, in one place.
EXPECTED_SETTINGS = {
    "min_tls_version": "1.2",
    "always_use_https": "on",
    "ssl": "strict",
    "security_level": "medium",
    "tls_1_3": "on",
    "automatic_https_rewrites": "on",
}
# HSTS (security_header): must be enabled with at least a 180-day max-age.
HSTS_MIN_MAX_AGE = 15552000

WARN_CERT_DAYS = 14


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def emit(check: str, status: str, mode: str, **fields) -> None:
    line = {"check": check, "status": status, "ts": now_iso(),
            "mode": mode, "zone": ZONE_NAME}
    line.update(fields)
    print(json.dumps(line, sort_keys=True, separators=(",", ":")), flush=True)


class CFError(Exception):
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code


def _request(url: str, data: bytes | None = None):
    req = urllib.request.Request(url, data=data,
                                 method="POST" if data is not None else "GET")
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        body = b""
        try:
            body = e.read()
        except Exception:  # noqa: BLE001
            pass
        msg = ""
        try:
            parsed = json.loads(body or b"{}")
            errs = parsed.get("errors") or []
            msg = "; ".join(str(x.get("message", x)) for x in errs) or str(parsed)
        except Exception:  # noqa: BLE001
            msg = body.decode("utf-8", "replace")[:200]
        raise CFError(e.code, msg or str(e)) from None
    except urllib.error.URLError as e:
        raise CFError(0, str(e.reason)) from None


def cf_get(path: str):
    resp = _request(f"{API}{path}")
    if not resp.get("success", False):
        errs = resp.get("errors") or []
        raise CFError(0, "; ".join(str(x.get("message", x)) for x in errs) or "unknown")
    return resp.get("result"), resp.get("result_info")


def resolve_zone() -> tuple[str, str]:
    result, _ = cf_get(f"/zones?name={ZONE_NAME}")
    if not result:
        raise CFError(0, f"zone {ZONE_NAME} not found or not readable")
    z = result[0]
    return z["id"], (z.get("account") or {}).get("id", "")


# ---------------------------------------------------------------- hourly checks

def check_tunnel(mode: str, account_id: str) -> None:
    """Tunnel health via the account cfd_tunnel API; probe fallback if denied."""
    if account_id:
        try:
            result, _ = cf_get(f"/accounts/{account_id}/cfd_tunnel?is_deleted=false")
            tunnels = result or []
            summary = []
            worst = "ok"
            for t in tunnels:
                st = (t.get("status") or "unknown").lower()
                conns = len(t.get("connections") or [])
                summary.append({"name": t.get("name"), "status": st, "connections": conns})
                if st != "healthy":
                    worst = "fail"
            status = "ok" if tunnels and worst == "ok" else "fail"
            primary = summary[0] if summary else {}
            emit("tunnel", status, mode, source="api",
                 tunnel_count=len(tunnels),
                 tunnel_name=primary.get("name"),
                 tunnel_status=primary.get("status"),
                 connections=primary.get("connections", 0),
                 tunnels=summary)
            return
        except CFError as e:
            if e.status_code not in (401, 403):
                emit("tunnel", "fail", mode, source="api", error=str(e))
                return
            # 401/403: fall through to the zone-level probe.
    _check_tunnel_probe(mode, denied=bool(account_id))


def _check_tunnel_probe(mode: str, denied: bool) -> None:
    """Fallback: HTTPS probe of the tunnel hostnames, look for a 2xx/3xx + cf-ray."""
    results = []
    worst = "ok"
    for host in TUNNEL_HOSTS:
        entry = {"host": host}
        try:
            req = urllib.request.Request(f"https://{host}/", method="GET")
            req.add_header("User-Agent", "kdk-cloudflare-watch/1")
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                code = resp.getcode()
                cf_ray = resp.headers.get("cf-ray")
        except urllib.error.HTTPError as e:
            code = e.code
            cf_ray = e.headers.get("cf-ray") if e.headers else None
        except Exception as e:  # noqa: BLE001
            entry.update(code=0, cf_ray=False, error=str(e))
            results.append(entry)
            worst = "fail"
            continue
        ok = 200 <= code < 400
        entry.update(code=code, cf_ray=bool(cf_ray))
        results.append(entry)
        if not ok:
            worst = "fail"
        elif not cf_ray and worst != "fail":
            # Answered but no edge header (e.g. split-horizon DNS to the LAN
            # origin): the host is up but we cannot confirm the edge path.
            worst = "warn"
    emit("tunnel", worst if results else "fail", mode, source="probe",
         api_denied=denied, probes=results)


def check_edge_cert(mode: str, zone_id: str) -> None:
    result, _ = cf_get(f"/zones/{zone_id}/ssl/certificate_packs?status=all")
    packs = result or []
    active = [p for p in packs if (p.get("status") == "active")]
    pool = active or packs
    best = None  # (days_left, expires_on, issuer, pack_status)
    for p in pool:
        for cert in p.get("certificates") or []:
            exp = cert.get("expires_on")
            if not exp:
                continue
            try:
                dt = datetime.strptime(exp[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            days = (dt - datetime.now(timezone.utc)).days
            if best is None or days < best[0]:
                best = (days, exp, cert.get("issuer"), p.get("status"))
    if best is None:
        emit("edge_cert", "fail", mode, reason="no_certificate_found",
             pack_count=len(packs))
        return
    days, exp, issuer, pstatus = best
    status = "ok"
    if days < 0:
        status = "fail"
    elif days < WARN_CERT_DAYS:
        status = "warn"
    emit("edge_cert", status, mode, issuer=issuer, expires_on=exp,
         days_left=days, pack_status=pstatus, active_pack=bool(active))


def check_zone_settings(mode: str, zone_id: str) -> None:
    result, _ = cf_get(f"/zones/{zone_id}/settings")
    by_id = {s.get("id"): s.get("value") for s in (result or [])}
    drift = []
    for key, want in EXPECTED_SETTINGS.items():
        got = by_id.get(key)
        if got != want:
            drift.append(f"{key}={got!r} want {want!r}")
    # HSTS lives inside security_header.
    sh = by_id.get("security_header") or {}
    hsts = (sh.get("strict_transport_security") or {}) if isinstance(sh, dict) else {}
    hsts_enabled = bool(hsts.get("enabled"))
    hsts_max_age = hsts.get("max_age") or 0
    if not hsts_enabled:
        drift.append("hsts.enabled=False want True")
    elif hsts_max_age < HSTS_MIN_MAX_AGE:
        drift.append(f"hsts.max_age={hsts_max_age} want >={HSTS_MIN_MAX_AGE}")
    status = "fail" if drift else "ok"
    emit("zone_settings", status, mode,
         checked=len(EXPECTED_SETTINGS) + 1, drift_count=len(drift),
         drift=drift, hsts_enabled=hsts_enabled, hsts_max_age=hsts_max_age)


def check_firewall_events(mode: str, zone_id: str) -> None:
    query = (
        "query($zone:String!,$since:Time!,$until:Time!){viewer{zones("
        "filter:{zoneTag:$zone}){firewallEventsAdaptiveGroups(limit:100,"
        "filter:{datetime_geq:$since,datetime_lt:$until}){count,dimensions{"
        "action,ruleId}}}}}"
    )
    until = datetime.now(timezone.utc)
    since = until.replace(microsecond=0)
    since = datetime.fromtimestamp(until.timestamp() - 3600, tz=timezone.utc)
    body = json.dumps({
        "query": query,
        "variables": {
            "zone": zone_id,
            "since": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "until": until.strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    }).encode()
    try:
        resp = _request(GRAPHQL, data=body)
    except CFError as e:
        emit("firewall_events", "warn", mode, available=False,
             reason="request_failed", error=str(e))
        return
    errs = resp.get("errors") or []
    if errs:
        codes = [((e.get("extensions") or {}).get("code")) for e in errs]
        reason = "graphql_forbidden" if any(c == "authz" for c in codes) else "graphql_error"
        emit("firewall_events", "warn", mode, available=False, reason=reason,
             message="; ".join(str(e.get("message", "")) for e in errs)[:200])
        return
    zones = ((resp.get("data") or {}).get("viewer") or {}).get("zones") or []
    groups = (zones[0].get("firewallEventsAdaptiveGroups") if zones else []) or []
    by_action: dict[str, int] = {}
    total = 0
    for g in groups:
        c = g.get("count", 0)
        total += c
        act = (g.get("dimensions") or {}).get("action", "unknown")
        by_action[act] = by_action.get(act, 0) + c
    emit("firewall_events", "ok", mode, available=True, window="1h",
         total=total, by_action=by_action)


# ----------------------------------------------------------------- weekly check

def _norm_rules(ruleset_result) -> list[dict]:
    rules = []
    for r in (ruleset_result or {}).get("rules") or []:
        expr = re.sub(r"\s+", " ", r.get("expression", "")).strip()
        entry = {
            "action": r.get("action"),
            "enabled": bool(r.get("enabled", True)),
            "expr": expr,
        }
        if r.get("ratelimit"):
            entry["ratelimit"] = r["ratelimit"]
        rules.append(entry)
    return rules


def _sha(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]


def _dns_records(zone_id: str) -> list[dict]:
    records = []
    page = 1
    while True:
        result, info = cf_get(
            f"/zones/{zone_id}/dns_records?per_page=100&page={page}")
        for r in result or []:
            records.append({"name": r.get("name"), "type": r.get("type"),
                            "proxied": bool(r.get("proxied"))})
        info = info or {}
        if page >= (info.get("total_pages") or 1):
            break
        page += 1
    records.sort(key=lambda r: (r["name"], r["type"]))
    return records


def _load_state() -> dict:
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> bool:
    try:
        os.makedirs(os.path.dirname(STATE_PATH) or ".", exist_ok=True)
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, STATE_PATH)
        return True
    except OSError:
        return False


def check_rule_drift(mode: str, zone_id: str) -> None:
    custom, _ = cf_get(
        f"/zones/{zone_id}/rulesets/phases/http_request_firewall_custom/entrypoint")
    ratelimit, _ = cf_get(
        f"/zones/{zone_id}/rulesets/phases/http_ratelimit/entrypoint")
    custom_rules = _norm_rules(custom)
    rl_rules = _norm_rules(ratelimit)
    rules_sha = _sha({"custom": custom_rules, "ratelimit": rl_rules})
    dns = _dns_records(zone_id)
    dns_sha = _sha(dns)

    state = _load_state()
    prev_rules = state.get("rules_sha")
    prev_dns = state.get("dns_sha")
    first_run = prev_rules is None and prev_dns is None
    rules_changed = (prev_rules is not None) and (prev_rules != rules_sha)
    dns_changed = (prev_dns is not None) and (prev_dns != dns_sha)
    changed = rules_changed or dns_changed

    state.update(rules_sha=rules_sha, dns_sha=dns_sha, last_run=now_iso())
    saved = _save_state(state)

    status = "ok"
    if changed:
        status = "fail"
    elif not saved:
        status = "warn"  # cannot persist state: next run cannot detect drift
    emit("rule_drift", status, mode,
         rules_sha=rules_sha, dns_sha=dns_sha,
         prev_rules_sha=prev_rules, prev_dns_sha=prev_dns,
         custom_count=len(custom_rules), ratelimit_count=len(rl_rules),
         dns_count=len(dns), changed=changed, rules_changed=rules_changed,
         dns_changed=dns_changed, first_run=first_run, state_saved=saved)


# ------------------------------------------------------------------------- main

def main() -> int:
    mode = "weekly" if ("--weekly" in sys.argv[1:] or
                         os.environ.get("CFWATCH_MODE", "").lower() == "weekly") else "hourly"
    if not TOKEN:
        emit("startup", "fail", mode, reason="CF_API_TOKEN not set")
        return 1
    try:
        zone_id, account_id = resolve_zone()
    except CFError as e:
        emit("startup", "fail", mode, reason="zone_resolve_failed", error=str(e))
        return 1

    checks = [
        ("rule_drift", lambda: check_rule_drift(mode, zone_id)),
    ] if mode == "weekly" else [
        ("tunnel", lambda: check_tunnel(mode, account_id)),
        ("edge_cert", lambda: check_edge_cert(mode, zone_id)),
        ("zone_settings", lambda: check_zone_settings(mode, zone_id)),
        ("firewall_events", lambda: check_firewall_events(mode, zone_id)),
    ]
    for name, fn in checks:
        try:
            fn()
        except CFError as e:
            emit(name, "fail", mode, error=str(e), status_code=e.status_code)
        except Exception as e:  # noqa: BLE001
            emit(name, "fail", mode, error=f"{type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
