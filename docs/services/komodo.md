---
title: Komodo
host: kdk-dkr-01
stack: komodo
tier: infra
status: live
storage: yes
secrets: [komodo-db, komodo-passkey, komodo-jwt-secret, komodo-webhook-secret, komodo-admin]
updated: 2026-09-12
tags: [homelab/stack, tier/infra, status/live]
---

# Komodo

Deployment control plane and the lab's single pane of glass; every other stack is deployed from git through it.

**Related:** [[kdk-dkr-01]] · [[traefik]] · [[op-connect]]

## At a glance

| Field | Value |
|---|---|
| Purpose | Deploys and monitors every stack on all five hosts from this repo |
| Status | 🟢 Live — `curl https://komodo.kmkdp.com/version` returns 2.3.3, all servers green |
| Host | [[kdk-dkr-01]] (10.1.20.20) |
| Endpoint | `https://komodo.kmkdp.com` — local login `kdkadmin` (1P `komodo-admin`) |
| Images | core `2.3.3`, ferretdb `2`, postgres-documentdb `17` |
| Deploy | Komodo stack `komodo` (git-linked); Resource Sync reads `komodo/resources.toml` from main |
| Storage | `/opt/kdk-lab/komodo/{postgres,ferretdb,syncs,backups}` on the VM disk |
| Blast radius | No new deploys, redeploys, or drift-repair; running containers keep running |

## Verify

```bash
curl -s https://komodo.kmkdp.com/version          # 2.3.3
docker ps --filter label=komodo.skip --format '{{.Names}} {{.Status}}'   # core, ferretdb, postgres Up
```
In the UI, Servers shows all five hosts (`kdk-dkr-01/-dmz-01/-02/-03`, `kdk-mon-01`) green.

## Troubleshooting

| Problem | Cause | Command to validate |
|---|---|---|
| Core pulled 1.19.5 with a v1 UI | `latest` tag lags; must pin | `docker inspect ghcr.io/moghtech/komodo-core:2.3.3 --format '{{.Id}}'` |
| A stack won't pick up a config change (e.g. new `pre_deploy`) | Sync not applied | Run `RunSync` before `DeployStack` in the UI |
| Server shows offline | Periphery down on that host | `ssh kdkadmin@<host> systemctl is-active periphery` → `active` |

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | [[traefik]] | `komodo.kmkdp.com` ingress + TLS |
| Needs | [[op-connect]] | `pre_deploy` `op inject` fails, so secret-rendering stacks won't deploy |
| Needed by | every stack | nothing new deploys; existing containers keep running |

## Observability

No service dashboard. Host reachability via `blackbox-http-app` on [[kdk-mon-01]]; alerts route to Pushover.

## Architecture

`core` listens on `9120` (Traefik-routed) and joins the `proxy` network; `ferretdb` (Mongo wire on 27017) and `postgres` (5432) stay on the stack-internal network. Periphery agents on each host listen on `8120` and Komodo connects out to them.

## Configuration

| Path / setting | What it is |
|---|---|
| `komodo/resources.toml` | Servers, stacks, and Procedures (Resource Sync source of truth) |
| `stacks/komodo/compose.yaml` | Core + FerretDB + Postgres definition |
| `KOMODO_HOST` | `https://komodo.kmkdp.com` (must match the ingress host) |

Core and periphery must track the same release line; pin both to `2.3.3`.

## Secret Rotation

| Secret (1P item) | Used for | Class | Rotate live? |
|---|---|---|---|
| `komodo-db` | Postgres/FerretDB auth | 🟢 self-service | yes — rotate DB + this item together, then redeploy |
| `komodo-jwt-secret` | Session JWTs | 🟢 self-service | yes — invalidates all sessions |
| `komodo-passkey` | Core↔periphery auth | 🔴 never on live alone | must update every periphery host's passkey in the same change |
| `komodo-webhook-secret` | GitHub webhook HMAC | 🟢 self-service | yes — update the GitHub webhook too |
| `komodo-admin` | Local admin login | 🟢 self-service | yes |

Rotate in 1P (`op item edit <item> --vault kdk-cluster ...`), then redeploy the stack so `op inject` re-renders `.env`.

## User Guide

Log in at `komodo.kmkdp.com` with `kdkadmin`. Stacks lists every deployment with logs and a Deploy/Redeploy button; Servers is the health dashboard for all five hosts.
