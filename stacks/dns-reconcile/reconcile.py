#!/usr/bin/env python3
"""Reconcile AdGuard DNS rewrites from the Traefik Host() labels in the repo.

Source of truth = each stack's compose `traefik.http.routers.<name>.rule:
Host(`x.kmkdp.com`)` label + the stack's target server (komodo/resources.toml).
Each hostname is pointed at the ingress IP of the tier its stack runs on, then
add/update/prune is reconciled into AdGuard on the origin (kdk-dns-01);
adguardhome-sync propagates to dns-02/03.

Runs as a one-shot from the gitops-sync procedure (fresh repo clone each run).
Any failure pages Pushover. Creds: ADGUARD_USERNAME/PASSWORD (op-injected),
Pushover from the mounted /pushover.env.
"""
from __future__ import annotations
import base64, glob, json, os, re, sys, urllib.request, urllib.error

REPO = "/repo"
ADG = "http://10.1.1.120/control"
# server -> Traefik ingress IP for that tier
INGRESS = {
    "kdk-dkr-01": "10.1.20.20",       # internal Traefik (also the *.kmkdp.com wildcard)
    "kdk-dkr-dmz-01": "192.168.191.20",  # DMZ Traefik
    "kdk-dkr-02": "10.1.30.21",       # Pi tier
    "kdk-dkr-03": "10.1.30.22",
}
# Prune is deliberately narrow. Only *.kmkdp.com records whose answer is one of
# these IPs AND that no longer have a repo stack are removed:
#   10.1.20.10       dead k3s VIP (argocd/grafana/... stragglers)
#   10.1.20.20       internal Traefik ingress (a removed repo service)
#   192.168.191.20   DMZ Traefik ingress (a removed repo service)
# The Pi-tier ingress IPs (10.1.30.x) are intentionally EXCLUDED: they carry
# ADOPTED services (Home Assistant, Scrypted, Homebridge, Node-RED, Zigbee) that
# live outside this repo, plus (currently mis-pointed) host FQDNs. Never prune
# host FQDNs (kdk-*) or the wildcard either.
PRUNE_IPS = {"10.1.20.10", "10.1.20.20", "192.168.191.20"}
PROTECT = {"*.kmkdp.com"}

# Static rewrites NOT derived from Traefik labels: the reMarkable cloud domains
# a device hits, redirected to rmfakecloud (kdk-dkr-01 internal Traefik) so the
# tablet syncs to the self-hosted cloud instead of reMarkable's. Authoritative
# host list from the ddvk/rmfakecloud device setup docs. These are non-.kmkdp.com
# so the prune step (kmkdp-only) never touches them; listing them here keeps them
# in git + recreated if AdGuard is rebuilt. See the readwise-to-remarkable stack.
STATIC = {
    host: "10.1.20.20"
    for host in (
        "my.remarkable.com",
        "ping.remarkable.com",
        "internal.cloud.remarkable.com",
        "local.appspot.com",
        "hwr-production-dot-remarkable-production.appspot.com",
        "service-manager-production-dot-remarkable-production.appspot.com",
        "document-storage-production-dot-remarkable-production.appspot.com",
        "eu.tectonic.remarkable.com",
        "backtrace-proxy.cloud.remarkable.engineering",
        "dev.ping.remarkable.com",
        "dev.tectonic.remarkable.com",
        "dev.internal.cloud.remarkable.com",
        "eu.internal.tctn.cloud.remarkable.com",
    )
}

# reMarkable OTA + telemetry is delivered via Memfault, proxied through
# device.cloud.remarkable.com (memfaultd base_url; swupdate/suricatta polls it).
# Blackhole it (and Memfault's default endpoint) to 0.0.0.0 so the Paper Pro
# cannot pull firmware updates on lab DNS -- updates would wipe the custom
# /usr/share/remarkable/templates and are unwanted. Also stops Memfault telemetry.
# Native document sync uses other (already-redirected) domains, so it is unaffected.
BLOCK = {
    host: "0.0.0.0"
    for host in (
        "device.cloud.remarkable.com",
        "device.memfault.com",
    )
}


# Extra lab host A-records (not Traefik-derived, not .kmkdp cloud redirects).
# ollama = the 4090 personal workstation serving Ollama for handwriting OCR;
# opportunistic (not always on). Not in PRUNE_IPS, so the prune step never touches it.
EXTRA = {
    "ollama.kmkdp.com": "10.1.30.218",
}


def adg(path, method="GET", body=None):
    auth = base64.b64encode(
        f"{os.environ['ADGUARD_USERNAME']}:{os.environ['ADGUARD_PASSWORD']}".encode()
    ).decode()
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(
        ADG + path, data=data, method=method,
        headers={"Authorization": "Basic " + auth, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(r, timeout=20) as resp:
        raw = resp.read().decode()
        return json.loads(raw) if raw.strip() else None


def notify(title, msg):
    tok, usr = os.environ.get("PUSHOVER_TOKEN"), os.environ.get("PUSHOVER_USER")
    if not (tok and usr):
        return
    import urllib.parse
    body = urllib.parse.urlencode(
        {"token": tok, "user": usr, "title": title, "message": msg, "priority": 1}
    ).encode()
    try:
        urllib.request.urlopen("https://api.pushover.net/1/messages.json", data=body, timeout=10)
    except Exception:
        pass


def desired_records():
    toml = open(f"{REPO}/komodo/resources.toml").read()
    stack_server = {}
    for m in re.finditer(r'\[\[stack\]\].*?name = "([^"]+)".*?server = "([^"]+)"', toml, re.S):
        stack_server[m.group(1)] = m.group(2)
    want = {}
    for cf in glob.glob(f"{REPO}/stacks/*/compose.yaml"):
        stack = cf.split("/")[-2]
        ip = INGRESS.get(stack_server.get(stack, ""))
        if not ip:
            continue
        for host in re.findall(r"Host\(`([^`]+)`\)", open(cf).read()):
            want[host] = ip
    want.update(STATIC)   # reMarkable cloud domains -> rmfakecloud (see STATIC)
    want.update(BLOCK)    # reMarkable OTA/telemetry -> 0.0.0.0 blackhole (see BLOCK)
    want.update(EXTRA)    # lab host A-records (see EXTRA)
    return want


def main():
    want = desired_records()
    if not want:
        raise SystemExit("no Host() records derived — refusing to reconcile (repo empty?)")
    have = {r["domain"]: r["answer"] for r in (adg("/rewrite/list") or [])}
    added = updated = pruned = 0

    for domain, ip in sorted(want.items()):
        cur = have.get(domain)
        if cur == ip:
            continue
        if cur is None:
            adg("/rewrite/add", "POST", {"domain": domain, "answer": ip})
            print(f"  + {domain} -> {ip}"); added += 1
        else:
            adg("/rewrite/update", "PUT",
                {"target": {"domain": domain, "answer": cur},
                 "update": {"domain": domain, "answer": ip}})
            print(f"  ~ {domain} -> {ip} (was {cur})"); updated += 1

    for domain, ans in sorted(have.items()):
        if (domain not in want and domain not in PROTECT
                and domain.endswith(".kmkdp.com") and not domain.startswith("kdk-")
                and ans in PRUNE_IPS):
            adg("/rewrite/delete", "POST", {"domain": domain, "answer": ans})
            print(f"  - {domain} (stale, was {ans})"); pruned += 1

    print(f"DNS reconcile OK: {len(want)} desired, +{added} ~{updated} -{pruned}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        print("DNS reconcile FAILED:", msg, file=sys.stderr)
        notify("kdk DNS reconcile failed", msg)
        sys.exit(1)
