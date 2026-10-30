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

Nightly app-state backups run as one-shot stacks that a Komodo Procedure redeploys on a schedule; local NAS is armed, and encrypted offsite to Proton runs every night (it does not yet complete the full delta inside its window — open operational item).

**Related:** [[kdk-dkr-01]] · [[kdk-dkr-dmz-01]] · [[komodo]]

## At a glance

| Field | Value |
|---|---|
| Purpose | pg dumps + `/opt/kdk-lab` rsync to the NAS, then selected sets offsite |
| Status | 🟢 Local NAS live (nightly 03:20) · 🟡 offsite runs nightly but has not yet fully caught up (each pass hits `--max-duration 25m`) |
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
| Offsite pass exits `rc=10` | the pass hit `--max-duration 25m` before the whole delta synced | expected today — the next nightly pass resumes; `rc=0` in `/mnt/backup/dkr/.offsite-status` means the tree is fully in sync |
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

Three layers: (1) ZFS snapshots — vmpool covers the DMZ VM zvol, tankz3 covers bulk + backup datasets; (2) VM-level — vzdump of VM 300 and VM 320 (kdk-log-01: Loki data lives on its disk, no app-level backup) to PBS, TrueNAS zvol replication of the DMZ VM; (3) app-level — the two backup stacks. `backup-dmz` runs `pg_dump` for Authelia, Immich and Tandoor (keeps 15) then rsyncs state; `backup-dkr-01` runs `pg_dump` for Jellystat (keeps 15, into `kdk-dkr-01/pgdump`) then rsyncs `/opt/kdk-lab` (excluding scratch + the live Postgres cluster dirs `komodo/postgres` and `jellystat/postgres`), then the separate `backup-offsite` stage runs `rclone sync` of the whole backup tree to Proton (encrypted, in ≤25-min passes). `backup-verify` checks all four pg dumps are present + gzip-valid for the day.

DMZ data that lives on `/mnt/appdata` instead of `/opt/kdk-lab` (Seerr, Wizarr, Libreseerr, Jellyfin metadata, Audiobookshelf podcasts) is **not** in the nightly rsync or offsite: it is covered by tankz3 snapshots only. The Jellyfin metadata and podcasts are regenerable (re-scan / re-download from the feeds), so that is deliberate.

## Configuration

| Path / setting | What it is |
|---|---|
| `stacks/backup-dkr-01/compose.yaml` | Jellystat pg dump + rsync of `/opt/kdk-lab` to the NAS |
| `stacks/backup-offsite/compose.yaml` | separate stage: `rclone sync` of the whole backup tree to Proton (crypt), `--max-duration 25m` per pass, writes `.offsite-status` |
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

Encrypted offsite to Proton runs **every night** as the `backup-offsite` stage of the `nightly-backups` Procedure — there is **no** profile gate. `offsite.sh` runs `rclone sync /data proton-crypt:` in passes capped at `--max-duration 25m` each (Proton kills long-lived token refreshes, so each pass logs in fresh and runs short), then writes `/mnt/backup/dkr/.offsite-status` with rclone's exit code and always exits 0 so the verify stage still runs.

**Open operational item:** the delta is larger than one 25-min pass can clear, so passes currently exit `rc=10` (hit `--max-duration`) and the next night resumes where the last stopped — the full tree is **not yet** fully in sync. `rc=0` in `.offsite-status` is the signal that it has finally caught up.

```bash
ssh kdkadmin@10.1.20.20 'cat /mnt/backup/dkr/.offsite-status'   # rc=0 = fully synced; rc=10 = pass hit --max-duration, resumes next night
```
