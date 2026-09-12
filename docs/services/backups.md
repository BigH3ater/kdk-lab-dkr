---
title: Backups
host: kdk-dkr-01, kdk-dkr-dmz-01
stack: backup-dkr-01, backup-dmz
tier: infra
status: live
storage: yes
secrets: [proton-mirror-crypt, authelia-postgres, immich-postgres]
updated: 2026-09-12
tags: [homelab/stack, tier/infra, status/live]
---

# Backups

Nightly app-state backups run as one-shot stacks that a Komodo Procedure redeploys on a schedule; local NAS is armed, encrypted offsite to Proton is built but gated.

**Related:** [[kdk-dkr-01]] · [[kdk-dkr-dmz-01]] · [[komodo]]

## At a glance

| Field | Value |
|---|---|
| Purpose | pg dumps + `/opt/kdk-lab` rsync to the NAS, then selected sets offsite |
| Status | 🟢 Local NAS live (nightly 03:00) · 🟡 offsite gated (Proton auth cooldown) |
| Hosts | [[kdk-dkr-01]] (`backup-dkr-01`), [[kdk-dkr-dmz-01]] (`backup-dmz`) |
| Deploy | Komodo Procedure `nightly-backups` redeploys both stacks (`deploy = false` otherwise) |
| Destinations | `/mnt/backup/dkr/<host>` (NFS tankz3) → Proton Drive (crypt) |
| Blast radius | No new restore points; existing backups intact |

## Verify

```bash
ssh kdkadmin@10.1.20.20 'ls -t /mnt/backup/dkr/kdk-dkr-01 | head'                    # recent state
ssh kdkadmin@10.1.20.20 'ls -t /mnt/backup/dkr/kdk-dkr-dmz-01/pgdump | head'         # dated .sql.gz
```
In Komodo, Procedure `nightly-backups` shows a recent successful run.

## Troubleshooting

| Problem | Cause | Command to validate |
|---|---|---|
| No new dumps | Procedure didn't run / stack failed | Komodo Procedure history; `docker logs <backup-container>` |
| Offsite never runs | gated behind `offsite` profile | expected — Proton auth is in cooldown |
| Offsite accumulates dead objects | crypt password changed without a purge | `rclone purge proton:kdk-lab-backup` before the first sync under a new password |
| rclone can't read cached tokens | image newer than the login version | pin `rclone/rclone:1.75` (the version that did the interactive login) |

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | `/mnt/backup` (NFS tankz3) | nowhere to write |
| Needs | [[identity]] + [[immich]] Postgres | pg dumps |
| Needs | Proton Drive (offsite only) | offsite tier |

## Observability

No dashboard. Success is visible in the Komodo Procedure run history.

## Architecture

Three layers: (1) ZFS snapshots — vmpool covers the DMZ VM zvol, tankz3 covers bulk + backup datasets; (2) VM-level — vzdump of VM 300 to PBS, TrueNAS zvol replication of the DMZ VM; (3) app-level — the two backup stacks. `backup-dmz` runs `pg_dump` for Authelia and Immich (keeps 15) then rsyncs state; `backup-dkr-01` rsyncs `/opt/kdk-lab` (excluding scratch/DBs), then the gated `offsite` service syncs to Proton.

## Configuration

| Path / setting | What it is |
|---|---|
| `stacks/backup-dkr-01/compose.yaml` | rsync + gated rclone offsite |
| `stacks/backup-dmz/compose.yaml` | Authelia + Immich pg dumps + rsync |
| `/opt/kdk-lab/backup/rclone/rclone.conf` | `proton` + `proton-crypt` remotes |
| Procedure `nightly-backups` | schedule `0 0 3 * * *` (03:00 daily) |

Restore: rsync/untar back from `/mnt/backup`, `psql < dump`. Migration tars remain under `tankz3/backup/migrate` until parity is confirmed.

## Secret Rotation

| Secret (1P item) | Used for | Class | Rotate live? |
|---|---|---|---|
| `proton-mirror-crypt` (kdk-ops) | rclone crypt password | 🔴 purge-on-change | rotating it orphans every object — `rclone purge` the Proton target first |
| `authelia-postgres`, `immich-postgres` | pg_dump auth | 🟢 self-service | rotate with the DB, then redeploy |

## Offsite status (2026-09-12)

Encrypted offsite to Proton is **built and proven** but **gated** (`offsite` profile) after Proton throttled password auth during setup. To re-arm: do one clean interactive `rclone config` login on a stable host, copy the fresh config to `/opt/kdk-lab/backup/rclone/rclone.conf`, confirm `rclone lsd proton-crypt:`, then remove `profiles: ["offsite"]`. Do not run repeated headless logins — Proton throttles them.
