---
title: Jellystat
host: kdk-dkr-01
stack: jellystat
tier: internal
status: planned
secrets: [jellystat]
updated: 2026-10-27
tags: [homelab/service, tier/internal, status/planned, media, stats]
---

# Jellystat (jellystat.kmkdp.com)

Playback and library statistics for the household **Jellyfin** (`jellyfin.kmkdp.com`,
DMZ). Internal tier on **kdk-dkr-01**, behind the internal Traefik with **Authelia
ForwardAuth** (`forwardauth@file`) — admin/stats tooling, so it sits behind SSO like the
arr apps, not on the DMZ tunnel. Resolves only on the LAN/tailnet (lab DNS).

- Stack: `stacks/jellystat/compose.yaml` — image `cyfershepard/jellystat` (pinned `1.1.12`).
- Own **PostgreSQL 16** (`postgres:16-alpine`), db `jfstat`, user `jellystat` — data at
  `/opt/kdk-lab/jellystat/postgres` (excluded from the raw rsync; pg_dumped by
  `backup-dkr-01`, see [[backups]]).
- In-app backup dir `/opt/kdk-lab/jellystat/backup-data` — plain files, swept by the
  nightly `/opt/kdk-lab` rsync.
- Reads Jellyfin via the **first-run UI** (URL + API key set in-app, not in compose).

## At a glance

| Field | Value |
|---|---|
| Purpose | Jellyfin watch/library stats dashboard |
| URL | https://jellystat.kmkdp.com (internal Traefik + Authelia) |
| Host | [[kdk-dkr-01]] |
| Stack | `stacks/jellystat` (`jellystat` + `jellystat-db`) |
| Auth | Authelia ForwardAuth (`forwardauth@file`) |
| Data | `/opt/kdk-lab/jellystat/{postgres,backup-data}` |
| Secrets | `op://kdk-cluster/jellystat` (`postgres_password`, `jwt_secret`) |
| Deploy | Komodo stack `jellystat`, gitops-sync (webhook + 15-min poll) |

## Verify

- Komodo shows `jellystat` **Deployed / running** (both `jellystat` and `jellystat-db`
  healthy) on kdk-dkr-01.
- `https://jellystat.kmkdp.com` redirects through `sso.kmkdp.com` (Authelia) and then
  loads the Jellystat UI.
- DNS: `jellystat.kmkdp.com` resolves on the LAN/tailnet (AdGuard rewrite reconciled
  from the `Host()` label by `dns-reconcile`).
- After first-run config, the dashboard shows the Jellyfin libraries and a sync runs.

```bash
ssh kdkadmin@10.1.20.20 'docker ps --filter name=jellystat --format "{{.Names}} {{.Status}}"'
ssh kdkadmin@10.1.20.20 'ls -t /mnt/backup/dkr/kdk-dkr-01/pgdump | head'   # jellystat-<date>.sql.gz after first nightly backup
```

## Troubleshooting

| Problem | Cause | Command to validate |
|---|---|---|
| 502 at the URL | `jellystat` not healthy / wrong port | `docker logs jellystat`; service port is 3000 |
| "Postgres details not defined" in logs | `.env` not injected / missing field | confirm `op://kdk-cluster/jellystat` has `postgres_password`; re-run deploy (pre_deploy `op inject`) |
| App up but DB errors | db not ready / wrong db name | db is `jfstat`, user `jellystat`; `docker logs jellystat-db`; `pg_isready` |
| Redirect loop / no login | Authelia middleware or DNS | check `forwardauth@file` label and that `sso.kmkdp.com` is up |
| No stats | Jellyfin URL/API key not set in first-run UI | re-enter under Settings; see Configuration |

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | [[proxy]] (internal Traefik, `proxy` network) | no ingress / TLS |
| Needs | [[identity]] (Authelia, `sso.kmkdp.com`) | no auth → no access |
| Needs | `jellystat-db` (own Postgres 16) | app cannot start |
| Needs | [[jellyfin]] (`jellyfin.kmkdp.com`) + an API key | no data to report on |
| Feeds | [[backups]] (`backup-dkr-01` pg_dump + rsync) | no Postgres restore point |

## Observability

No dedicated dashboard. Container health via Komodo / `docker ps`; logs ship to Loki
(`{container="jellystat"}`) via the observability agent.

## Architecture

Two containers from `stacks/jellystat/compose.yaml`: `jellystat-db` (`postgres:16-alpine`,
db `jfstat`) on the stack default network only, and `jellystat` on both `default` and the
external `proxy` network. `jellystat` depends on `jellystat-db` being healthy
(`pg_isready`). Jellystat signs session tokens with `JWT_SECRET`; both the DB password
and the JWT secret come from `.env` rendered by `op inject` in the Komodo pre_deploy
(`op://kdk-cluster/jellystat`). The Jellyfin connection is configured at runtime, so no
Jellyfin secret lives in this repo.

## Configuration

### First-run (in the Jellystat UI, after deploy)

1. Open `https://jellystat.kmkdp.com` (authenticate via Authelia), then create the
   initial Jellystat admin account when prompted.
2. In **Jellyfin → Dashboard → API Keys**, create a new API key for Jellystat.
3. In Jellystat's setup, set:
   - **Jellyfin URL:** `https://jellyfin.kmkdp.com`
   - **API key:** the key created in step 2.
4. Run the initial **sync** so Jellystat pulls the library + user list.
5. To get historical watch data, in Jellyfin install/enable the **Playback Reporting**
   plugin, then in Jellystat use **Settings → Import** to import the Playback Reporting
   history (one-time). Ongoing activity is tracked live after the sync.

### Files / settings

| Path / setting | What it is |
|---|---|
| `stacks/jellystat/compose.yaml` | the two services + Traefik labels |
| `stacks/jellystat/.env.tpl` | op-injected `POSTGRES_PASSWORD`, `JWT_SECRET` |
| `/opt/kdk-lab/jellystat/postgres` | Postgres cluster (pg_dumped, not rsynced raw) |
| `/opt/kdk-lab/jellystat/backup-data` | Jellystat in-app backups (rsynced) |
| Komodo stack `jellystat` | server kdk-dkr-01, pre_deploy `op inject`, webhook on |

## Secret Rotation

| Secret (1P item) | Used for | Class | Rotate live? |
|---|---|---|---|
| `jellystat` / `postgres_password` | Postgres auth (app + pg_dump) | 🟢 self-service | rotate with the DB, then redeploy `jellystat` + `backup-dkr-01` |
| `jellystat` / `jwt_secret` | session token signing | 🟢 self-service | rotate, redeploy; existing sessions invalidated |

The Jellyfin API key is stored by Jellystat in its own DB (not a 1P item); rotate it in
Jellyfin and re-enter it in the Jellystat UI.

## User Guide

Internal admin tool — not a household-facing surface, so it is not on the family
dashboard. Jellystat answers "what got watched, by whom, how much" for the Jellyfin
server. Open `https://jellystat.kmkdp.com` on the LAN or over Tailscale and sign in with
your Authelia login.
