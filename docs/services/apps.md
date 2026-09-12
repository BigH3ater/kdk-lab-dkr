---
title: Apps stack
host: kdk-dkr-01
stack: apps
tier: internal
status: live
storage: yes
secrets: [kdk-dns-adguard-home-admin, rmfakecloud-app]
updated: 2026-09-12
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
| proton-bridge | `shenxn/protonmail-bridge:3.9.1-1` | `10.1.20.20:25` |
| roborock | `python-roborock/local_roborock_server:1.0.2` | `555`, `8881` |

`proton-bridge` is the in-lab SMTP relay used by Authelia's notifier.

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
