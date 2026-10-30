---
title: Backups
host: kdk-dkr-01, kdk-dkr-dmz-01
stack: backup-dkr-01, backup-dmz
tier: infra
status: live
storage: yes
secrets: [proton-mirror-crypt, authelia-postgres, immich-postgres, jellystat, authelia-app-secrets]
updated: 2026-10-30
tags: [homelab/stack, tier/infra, status/live]
---

# Backups

Nightly app-state backups run as one-shot stacks that a Komodo Procedure redeploys on a schedule; local NAS is armed, and encrypted offsite to Proton runs every night. The offsite stage now **packs one `tar.zst` per host** before uploading, so rclone ships a handful of large objects instead of thousands of small files the Proton API could not keep up with.

**Related:** [[kdk-dkr-01]] · [[kdk-dkr-dmz-01]] · [[komodo]]

## At a glance

| Field | Value |
|---|---|
| Purpose | pg dumps + `/opt/kdk-lab` rsync to the NAS, then selected sets offsite |
| Status | 🟢 Local NAS live (nightly 03:20) · 🟢 offsite packs per-host `tar.zst` then syncs `offsite/` + `pve-templates/` + `*/pgdump/` to Proton |
| Hosts | [[kdk-dkr-01]] (`backup-dkr-01`), [[kdk-dkr-dmz-01]] (`backup-dmz`) |
| Deploy | Komodo Procedure `nightly-backups` redeploys both stacks (`deploy = false` otherwise) |
| Destinations | `/mnt/backup/dkr/<host>` (NFS tankz3) → Proton Drive (crypt) |
| Blast radius | No new restore points; existing backups intact |

## Verify

```bash
ssh kdkadmin@10.1.20.20 'ls -t /mnt/backup/dkr/kdk-dkr-01 | head'                    # recent state
ssh kdkadmin@10.1.20.20 'ls -t /mnt/backup/dkr/kdk-dkr-01/pgdump | head'             # jellystat .sql.gz
ssh kdkadmin@10.1.20.20 'ls -t /mnt/backup/dkr/kdk-dkr-dmz-01/pgdump | head'         # dated .sql.gz
```
In Komodo, Procedure `nightly-backups` shows a recent successful run.

## Troubleshooting

| Problem | Cause | Command to validate |
|---|---|---|
| No new dumps | Procedure didn't run / stack failed | Komodo Procedure history; `docker logs <backup-container>` |
| Offsite pass exits `rc=10` | the pass hit `--max-duration 25m` before the (now much smaller) delta synced | the next pass resumes; with per-host archives a clean night should reach `rc=0` in one or two passes. `rc=0` in `/mnt/backup/dkr/.offsite-status` means fully in sync |
| Offsite pack wrote nothing / `tar` failed | `apk add tar zstd` could not reach the Alpine CDN, or a host dir was absent | `docker logs` the `backup-offsite-pack-*` container; `sudo ls -la /mnt/backup/dkr/offsite/` shows today's `<host>-*.tar.zst` |
| Offsite accumulates dead objects | crypt password changed without a purge | `rclone purge proton:kdk-lab-backup` before the first sync under a new password |
| rclone can't read cached tokens | image newer than the login version | pin `rclone/rclone:1.75.2` (the version that did the interactive login) |

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | `/mnt/backup` (NFS tankz3) | nowhere to write |
| Needs | [[identity]] + [[immich]] + Tandoor Postgres | pg dumps |
| Needs | Proton Drive (offsite only) | offsite tier |

## Observability

No dashboard. Success is visible in the Komodo Procedure run history.

## Architecture

Three layers: (1) ZFS snapshots — vmpool covers the DMZ VM zvol, tankz3 covers bulk + backup datasets; (2) VM-level — vzdump of VM 300 and VM 320 (kdk-log-01: Loki data lives on its disk, no app-level backup) to PBS, TrueNAS zvol replication of the DMZ VM; (3) app-level — the two backup stacks. `backup-dmz` runs `pg_dump` for Authelia, Immich and Tandoor (keeps 15) then rsyncs state; `backup-dkr-01` runs `pg_dump` for Jellystat (keeps 15, into `kdk-dkr-01/pgdump`) then rsyncs `/opt/kdk-lab` (excluding scratch + the live Postgres cluster dirs `komodo/postgres` and `jellystat/postgres`), then the separate `backup-offsite` stage first **packs one `tar.zst` per host** (`pack.sh`: GNU tar + zstd, excluding caches/logs/clones/creds, newest 3 kept locally) into `/mnt/backup/dkr/offsite/`, then runs `rclone sync` of only `offsite/` + `pve-templates/` + each host's `pgdump/` to Proton (encrypted, in ≤25-min passes). Packing first turns thousands of small files into a few large objects, which the Proton API handles without the 502/422/empty-upload churn that stopped the old whole-tree sync from finishing. `backup-verify` checks all four pg dumps are present + gzip-valid for the day.

DMZ data that lives on `/mnt/appdata` instead of `/opt/kdk-lab` (Seerr, Wizarr, Libreseerr, Jellyfin metadata, Audiobookshelf podcasts) is **not** in the nightly rsync or offsite: it is covered by tankz3 snapshots only. The Jellyfin metadata and podcasts are regenerable (re-scan / re-download from the feeds), so that is deliberate.

## Configuration

| Path / setting | What it is |
|---|---|
| `stacks/backup-dkr-01/compose.yaml` | Jellystat pg dump + rsync of `/opt/kdk-lab` to the NAS |
| `stacks/backup-offsite/compose.yaml` | separate stage: `pack` service builds per-host `tar.zst`, then `offsite` service `rclone sync`s `offsite/` + `pve-templates/` + `*/pgdump/` to Proton (crypt), `--max-duration 25m` per pass, writes `.offsite-status` |
| `stacks/backup-offsite/pack.sh` | builds `/mnt/backup/dkr/offsite/<host>-YYYY-MM-DD.tar.zst` per host (excludes caches/logs/clones/creds/pgdump), keeps newest 3 |
| `stacks/backup-dmz/compose.yaml` | Authelia + Immich + Tandoor pg dumps + rsync of `/opt/kdk-lab` (excludes live Postgres dirs, certs, clones, Jellyfin metadata/cache/transcodes, `audiobookshelf/podcasts`) |
| `/opt/kdk-lab/backup/rclone/rclone.conf` | `proton` + `proton-crypt` remotes |
| Procedure `nightly-backups` | schedule `0 20 3 * * *` (03:20 daily, `Cron` format) |

Restore: rsync/untar back from `/mnt/backup`, `psql < dump`.

**PBS job for VM 320 (kdk-log-01).** The nightly Proxmox → PBS (`kdk-pbs-01`)
vzdump job on kdk-hyp-01 must include VM 320: if its selection is "all", it is
picked up automatically; otherwise the owner adds it (Datacenter → Backup → the
job → Selection), mode `snapshot`. Loki's chunks and
index are in that disk image; a restore loses at most the day since the last dump. Migration tars remain under `tankz3/backup/migrate` until parity is confirmed.

## Secret Rotation

| Secret (1P item) | Used for | Class | Rotate live? |
|---|---|---|---|
| `proton-mirror-crypt` (kdk-ops) | rclone crypt password | 🔴 purge-on-change | rotating it orphans every object — `rclone purge` the Proton target first |
| `authelia-postgres`, `immich-postgres` | pg_dump auth | 🟢 self-service | rotate with the DB, then redeploy |
| `jellystat` (postgres_password) | pg_dump auth for Jellystat | 🟢 self-service | rotate with the DB, then redeploy `jellystat` + `backup-dkr-01` |
| `authelia-app-secrets` (`tandoor_postgres_password`) | pg_dump auth for Tandoor | 🟢 self-service | rotate with the DB, then redeploy `tandoor` + `backup-dmz` |

## Offsite status (2026-10-30)

Encrypted offsite to Proton runs **every night** as the `backup-offsite` stage of the `nightly-backups` Procedure — there is **no** profile gate. The stage now packs before it uploads: the `pack` service writes one `tar.zst` per host into `/mnt/backup/dkr/offsite/`, then `offsite.sh` runs `rclone sync` of only `offsite/` + `pve-templates/` + `*/pgdump/` in passes capped at `--max-duration 25m` each (Proton kills long-lived token refreshes, so each pass logs in fresh and runs short), then writes `/mnt/backup/dkr/.offsite-status` with rclone's exit code and always exits 0 so the verify stage still runs.

**Why the change:** the old whole-tree `rclone sync` never finished a night — thousands of churny small files against Proton's flaky API alternated `rc=10` (hit `--max-duration`) and `rc=2` (API errors: `502` block uploads, `422 already exists`, `400 empty`, "no response" on listings, plus `Can't follow symlink` on proton-bridge sockets). Packing cuts the Proton object count by ~3 orders of magnitude, so a clean night should reach `rc=0` in one or two passes. `rc=0` in `.offsite-status` is the signal it is fully in sync.

```bash
ssh kdkadmin@10.1.20.20 'sudo ls -la /mnt/backup/dkr/offsite/'   # today's per-host <host>-YYYY-MM-DD.tar.zst
ssh kdkadmin@10.1.20.20 'cat /mnt/backup/dkr/.offsite-status'    # rc=0 = fully synced; rc=10 = pass hit --max-duration, resumes next night
```
