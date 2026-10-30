---
title: Apps stack
host: kdk-dkr-01
stack: apps
tier: internal
status: live
storage: yes
secrets: [kdk-dns-adguard-home-admin, rmfakecloud-app]
updated: 2026-10-29
tags: [homelab/stack, tier/internal, status/live]
---

# Apps stack

Small internal utilities on kdk-dkr-01: time, AdGuard replication, reMarkable cloud, a Proton SMTP bridge, and the Roborock local server.

**Related:** [[kdk-dkr-01]] · [[traefik]]

## At a glance

| Field | Value |
|---|---|
| Purpose | NTP, AdGuard sync, rmfakecloud, proton-bridge, roborock |
| Status | 🟢 Live |
| Host | [[kdk-dkr-01]] |
| Endpoints | `remarkable.kmkdp.com`; others are host-port services |
| Deploy | Komodo stack `apps` (git-linked, `op inject` pre_deploy) |
| Storage | `/opt/kdk-lab/apps/<svc>` |
| Blast radius | Per-service; no shared failure domain |

## Verify

```bash
docker ps --filter name=ntp --filter name=adguardhome-sync --format '{{.Names}} {{.Status}}'
docker logs adguardhome-sync 2>&1 | tail -3          # last sync ok to dns-02/03
curl -s -o /dev/null -w '%{http_code}\n' https://remarkable.kmkdp.com/   # 200
```

## Troubleshooting

| Problem | Cause | Command to validate |
|---|---|---|
| AdGuard replicas drift | sync creds wrong or origin down | `docker logs adguardhome-sync` shows auth/connection error |
| reMarkable won't sync | `STORAGE_URL` mismatch | must be `https://remarkable.kmkdp.com` |
| Roborock devices offline | TLS/config not migrated | check `/opt/kdk-lab/apps/roborock/{config.toml,tls}` present |
| Roborock cert stale / `TLSCertExpiringSoon` for `api-slave` | `cfroborock` ACME renewal failing, dumper not writing, or roborock not restarted since renewal | `docker logs roborock-certs-dumper`; `python3 -c "import json;d=json.load(open('/opt/kdk-lab/proxy/letsencrypt-roborock/acme.json'));print([ (c['domain']) for v in d.values() for c in (v.get('Certificates') or []) ])"` (domains only); `echo \| openssl s_client -connect 10.1.20.20:555 -servername api-slave.kmkdp.com 2>/dev/null \| openssl x509 -noout -subject -dates`; run procedure `roborock-cert-refresh` |

## Roborock TLS certificate

The vacuum (VLAN 30, internet blocked) opens TLS to `api-slave.kmkdp.com:555` on the roborock container and validates the cert, so an expired cert = vacuum offline. It must stay a **dedicated `CN=api-slave.kmkdp.com`** cert (the vacuum is not trusted to accept a `*.kmkdp.com` wildcard SAN). It auto-renews now:

1. The **internal Traefik** (`stacks/proxy`) has a second ACME resolver, `cfroborock`, with its own store in a **separate host directory**, `/opt/kdk-lab/proxy/letsencrypt-roborock/acme.json` (not the main `/opt/kdk-lab/proxy/letsencrypt/` that holds the `*.kmkdp.com` wildcard private key). The store is separated precisely so the `certs-dumper` below can bind only this directory and never gains access to the wildcard key. A file-provider router (`stacks/proxy/dynamic/roborock-cert.yml`) for `Host(\`api-slave.kmkdp.com\`)` with `tls.certResolver=cfroborock` makes it obtain + renew that one cert via Cloudflare DNS-01. A separate resolver is required because Traefik will not issue a sub-domain cert the `cf` store's `*.kmkdp.com` wildcard already covers. DNS-01 means Traefik never has to be reachable on that name; nothing legitimate hits it on `:443`.
2. The **`certs-dumper` sidecar** in this stack (`roborock-certs-dumper`, same `ldez/traefik-certs-dumper` image as [[identity]]'s) binds **only** `/opt/kdk-lab/proxy/letsencrypt-roborock` (read-only, mounted at `/acme`), reads `/acme/acme.json` and copies the cert into `/opt/kdk-lab/apps/roborock/tls/{tls.crt,tls.key}` (the names `config.toml` `[tls] mode="provided"` points at), root-owned `0644`/`0600`. It waits for the first issuance before touching the files, so the hand-copied cert is never clobbered prematurely.
3. The roborock server reads the cert only at start, so the **`roborock-cert-refresh`** Komodo procedure (`komodo/resources.toml`, cron `0 40 4 1,15`) restarts the `roborock` service on the 1st + 15th. Renewal happens 30 days before expiry, so the served cert is never more than ~15 days behind.

Verify (read-only, from `kdk-dkr-01`):

```bash
docker logs roborock-certs-dumper 2>&1 | tail
echo | openssl s_client -connect 10.1.20.20:555 -servername api-slave.kmkdp.com 2>/dev/null \
  | openssl x509 -noout -subject -dates          # CN=api-slave.kmkdp.com, notAfter advances after renewal+restart
```

Alerting: `api-slave.kmkdp.com:555/healthz` is probed by `job="blackbox-http-app"`; `TLSCertExpiringSoon` now matches `job=~"blackbox-.*"` (was `blackbox-service` only, which never covered it) and warns at 14 days. **Rollback:** `git revert` the change; the dedicated cert keeps renewing while the previous hand-copied cert is valid until 2026-11-28.

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | AdGuard trio (dns-01/02/03) | sync has no origin/replicas |
| Needs | [[traefik]] | `remarkable.kmkdp.com` ingress |
| Needed by | AdGuard replicas | rules/records stop propagating |

## Observability

No dashboard. `ntp` (123/udp) and `proton-bridge` (25) are host-port; alerting → Pushover.

## Architecture

| Service | Image | Port |
|---|---|---|
| ntp | `cturra/ntp` | `123/udp` |
| adguardhome-sync | `bakito/adguardhome-sync:v0.9.2` | none (cron `*/15`) |
| rmfakecloud | `ddvk/rmfakecloud:0.0.31` | `3000` (Traefik) |
| proton-bridge | `shenxn/protonmail-bridge:3.19.0-1` | `10.1.20.20:25` |
| roborock | `python-roborock/local_roborock_server:1.2.1` | `555`, `8881` |
| certs-dumper | `ldez/traefik-certs-dumper:v2.11.4` | none (writes roborock `/tls`) |

`proton-bridge` is an in-lab Proton SMTP relay on `10.1.20.20:25`. **Nothing uses it today**: Alertmanager notifies through Pushover and Authelia's notifier sends through `submission://smtp.protonmail.ch:587` directly. The bridge has **no account logged in** (`Loading users count=0` in its session log, 2026-10-29); log one in with `sudo docker exec -it proton-bridge protonmail-bridge --cli` → `login` before pointing anything at it. Its self-updates land on a `tmpfs` (PR #143): the image's own bridge binary runs at every start, and newer bridge builds come only through Renovate.

## Configuration

| Path / setting | What it is |
|---|---|
| `stacks/apps/compose.yaml` | All five services |
| AdGuard origin/replicas | `10.1.1.120` → `.121`/`.122` |
| `/opt/kdk-lab/apps/roborock/` | `config.toml` + `tls/` (migrated from k8s Secrets) |

## Secret Rotation

| Secret (1P item) | Used for | Class | Rotate live? |
|---|---|---|---|
| `kdk-dns-adguard-home-admin` | AdGuard API auth (all three) | 🟢 self-service | yes — rotate in AdGuard + 1P, redeploy |
| `rmfakecloud-app` (`jwt_secret_key`) | rmfakecloud session signing | 🟢 self-service | yes — invalidates device sessions |

## User Guide

reMarkable devices point at `remarkable.kmkdp.com`. AdGuard sync is unattended. The others are headless integrations.
