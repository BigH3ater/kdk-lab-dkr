#!/usr/bin/env python3
"""Reconcile AdGuard DNS rewrites from live Traefik Host() labels + a static map.

Desired state has two sources:
  1. Traefik service records -- every `traefik.http.routers.*.rule: Host(`x`)`
     label on a running container on an ingress host, mapped to that host's
     Traefik ingress IP (internal / DMZ / Pi tier).
  2. Static infra records -- host FQDNs and non-Traefik services that can't be
     derived from labels (pve, truenas, grafana, kdk-* hosts, the wildcard).

It compares desired vs the AdGuard origin (kdk-dns-01) and prints ADD / UPDATE /
PRUNE. Default is DRY-RUN; pass --apply to write (add/update). Pruning of
stale owned records requires --prune (opt-in, since it deletes).

Env: ADGUARD_USERNAME, ADGUARD_PASSWORD (or it reads via `op`).
SSH: agent auth by default; set SSH_KEY to force a specific key file.
"""
import os, sys, json, subprocess, base64, urllib.request, re

ADGUARD = "10.1.1.120"                       # origin; adguardhome-sync -> dns-02/03
SSH_KEY = os.environ.get("SSH_KEY")          # optional key file; agent auth if unset

# ingress hosts that run a Traefik: ssh_ip -> (tier, ingress_answer_ip)
INGRESS = {
    "10.1.20.20":     ("internal", "10.1.20.20"),
    "192.168.191.20": ("dmz",      "192.168.191.20"),
    "10.1.30.21":     ("pi",       "10.1.30.21"),
}

# static/infra records not derivable from Traefik labels (domain -> answer)
STATIC = {
    "*.kmkdp.com":              "10.1.20.20",   # internal Traefik wildcard
    "dns.kmkdp.com":            "10.1.1.53",    # AdGuard VIP
    "pve.kmkdp.com":            "10.1.1.100",
    "pbs.kmkdp.com":            "10.1.1.102",
    "truenas.kmkdp.com":        "10.1.3.5",
    "grafana.kmkdp.com":        "10.1.20.30",   # mon-01 (pending: front w/ Traefik?)
    "grafana-embed.kmkdp.com":  "10.1.20.30",
    # host FQDNs
    "kdk-gw-01.kmkdp.com":      "10.1.3.1",
    "kdk-dkr-01.kmkdp.com":     "10.1.20.20",
    "kdk-dkr-02.kmkdp.com":     "10.1.30.21",
    "kdk-dkr-03.kmkdp.com":     "10.1.30.22",
    "kdk-mon-01.kmkdp.com":     "10.1.20.30",
    "kdk-dns-01.kmkdp.com":     "10.1.1.120",
    "kdk-dns-02.kmkdp.com":     "10.1.1.121",
    "kdk-dns-03.kmkdp.com":     "10.1.1.122",
    "kdk-dee-01.kmkdp.com":     "10.1.1.130",
    "kdk-pbs-01.kmkdp.com":     "10.1.1.102",
    "kdk-gns3-01.kmkdp.com":    "10.1.1.65",
    "kdk-tdarr-gpu-01.kmkdp.com": "10.1.30.218",
}

HOST_RE = re.compile(r"Host\(`([^`]+)`\)")

def ssh(ip, cmd):
    base = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=10"]
    if SSH_KEY:
        base += ["-i", SSH_KEY, "-o", "IdentitiesOnly=yes", "-o", "IdentityAgent=none"]
    return subprocess.run(base + [f"kdkadmin@{ip}", cmd],
                          capture_output=True, text=True)

def traefik_hosts():
    """Collect Host() domains per ingress host, from BOTH container labels
    (docker provider) AND the Traefik file provider config, so it works whether
    a service is label-routed (dkr-01, dmz) or file-routed (Pi). Skips hosts
    whose answer equals the internal wildcard -- those are already covered by
    `*.kmkdp.com` and need no explicit record."""
    wildcard = STATIC.get("*.kmkdp.com")
    desired = {}
    for ssh_ip, (tier, answer) in INGRESS.items():
        cmd = (
            "T=$(sudo docker ps --format '{{.Names}}' | grep -iE 'traefik' | head -1); "
            # docker-label routes
            "sudo docker ps -q | xargs -r sudo docker inspect "
            "--format '{{range $k,$v := .Config.Labels}}{{$k}}={{$v}}\n{{end}}' 2>/dev/null "
            "| grep -iE 'routers.*rule'; "
            # file-provider routes (inside the traefik container's config)
            "[ -n \"$T\" ] && sudo docker exec \"$T\" sh -c "
            "'grep -rhoE \"Host\\(.[^)]+.\\)\" /etc/traefik /config /dynamic 2>/dev/null'; "
            "true")
        r = ssh(ssh_ip, cmd)
        for dom in HOST_RE.findall(r.stdout):
            if dom.endswith("kmkdp.com") and answer != wildcard:
                desired[dom] = answer
        if not r.stdout.strip():
            print(f"  WARN: no routes read on {ssh_ip}: {r.stderr.strip()[:80]}", file=sys.stderr)
    return desired

def adguard_creds():
    u = os.environ.get("ADGUARD_USERNAME")
    p = os.environ.get("ADGUARD_PASSWORD")
    if not p:
        env = dict(os.environ, KDK_OP_MODE="sa")
        u = u or subprocess.run(["op","read","op://kdk-cluster/kdk-dns-adguard-home-admin/username"],
                                capture_output=True, text=True, env=env).stdout.strip() or "admin"
        p = subprocess.run(["op","read","op://kdk-cluster/kdk-dns-adguard-home-admin/password"],
                           capture_output=True, text=True, env=env).stdout.strip()
    return u, p

def ag_req(path, data=None, method=None):
    u, p = adguard_creds()
    auth = base64.b64encode(f"{u}:{p}".encode()).decode()
    req = urllib.request.Request(f"http://{ADGUARD}/control{path}",
                                 data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Authorization": "Basic "+auth, "Content-Type": "application/json"},
                                 method=method)
    return urllib.request.urlopen(req, timeout=15)

def adguard_current():
    return {r["domain"]: r["answer"] for r in json.load(ag_req("/rewrite/list"))}

def main():
    apply = "--apply" in sys.argv
    prune = "--prune" in sys.argv
    desired = dict(STATIC)
    desired.update(traefik_hosts())          # Traefik labels win for service names
    current = adguard_current()

    adds    = {d: ip for d, ip in desired.items() if d not in current}
    updates = {d: (current[d], ip) for d, ip in desired.items() if d in current and current[d] != ip}
    stale   = {d: current[d] for d in current if d not in desired}

    print(f"\n=== DNS reconcile (desired {len(desired)} / current {len(current)}) "
          f"[{'APPLY' if apply else 'DRY-RUN'}{' +PRUNE' if prune else ''}] ===")
    print(f"\nADD ({len(adds)}):")
    for d, ip in sorted(adds.items()): print(f"  + {d} -> {ip}")
    print(f"\nUPDATE ({len(updates)}):")
    for d, (old, ip) in sorted(updates.items()): print(f"  ~ {d}: {old} -> {ip}")
    print(f"\nSTALE / not-desired ({len(stale)}){' [will PRUNE]' if prune else ' [kept; use --prune]'}:")
    for d, ip in sorted(stale.items()): print(f"  - {d} -> {ip}")

    if not apply:
        print("\n(dry-run; re-run with --apply to write, --prune to also delete stale)")
        return
    for d, ip in adds.items():
        ag_req("/rewrite/add", {"domain": d, "answer": ip}); print(f"added {d} -> {ip}")
    for d, (old, ip) in updates.items():
        ag_req("/rewrite/update", {"target": {"domain": d, "answer": old},
                                   "update": {"domain": d, "answer": ip}}); print(f"updated {d} -> {ip}")
    if prune:
        for d, ip in stale.items():
            ag_req("/rewrite/delete", {"domain": d, "answer": ip}); print(f"pruned {d}")

if __name__ == "__main__":
    main()
