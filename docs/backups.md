---
title: Backups
tags: [homelab/process, backups, komodo, proton]
updated: 2026-09-14
---

# Backups

Two independent layers protect kdk-lab:

1. **VM-level DR** — Proxmox `vzdump` → PBS (`kdk-pbs-01`). Full VM restore points.
   Nightly job, all live guests. Separate from everything below.
2. **Application backups** — Komodo `nightly-backups` Procedure. App state from
   **every host** → the NFS backup tree (`tankz3/backup`), then **offsite to
   Proton Drive** (encrypted), then **validated with a Pushover alert on any
   failure**. This page documents layer 2.

**Related:** [[komodo-gitops]] · `stacks/backup-*`

## What goes offsite (and what must not)

Proton holds **only application state + the Proxmox cloud-init template**.
The offsite `rclone sync` (`stacks/backup-offsite/offsite.sh`) excludes:

| Excluded | Why |
|---|---|
| `**/stacks/**` | Komodo repo clones — already on GitHub |
| `**/backup/**` | `rclone.conf` — the Proton creds themselves; never ship to Proton |
| `**/jellyfin/**/metadata`, `/cache`, `/transcodes` | 6.3 GB of re-fetchable artwork (the real Jellyfin DB is `data/data`) |
| `**/MediaCover/**` | radarr/sonarr poster cache — regenerable |
| `**/sabnzbd/logs/**` | logs |
| `_archive/**` | the dated version history (see below) |

Immich and Authelia Postgres are **not** rsynced; they are dumped with `pg_dump`
(`stacks/backup-dmz`) and the dumps go offsite instead.

**Versioning:** `--backup-dir proton-crypt:_archive/<date>` keeps a dated copy of
anything deleted or overwritten, so the offsite is point-in-time recoverable, not
a pure destructive mirror.

## The nightly pipeline

`nightly-backups` Procedure (03:00 America/Chicago), five sequential stages:

| Stage | Stack | Host | Does |
|---|---|---|---|
| stage-dkr-01 | `backup-dkr-01` | dkr-01 | rsync `/opt/kdk-lab` → `tankz3/backup/dkr/kdk-dkr-01/` |
| stage-dmz | `backup-dmz` | dmz-01 | `pg_dump` authelia + immich, rsync app state |
| stage-collect | `backup-collect` | dkr-01 | SSH-pull `/opt/kdk-lab` from dkr-02, dkr-03, mon-01 (not on the storage net) |
| offsite | `backup-offsite` | dkr-01 | `rclone sync` whole tree → Proton (encrypted) |
| verify | `backup-verify` | dkr-01 | validate everything, alert Pushover on failure |

The staging + offsite stages are **best-effort** (their containers exit 0 even on
error, so one failure never blocks the rest). `backup-verify` is the **single
source of truth** and the only thing that pages.

Every staging stage drops a `.last-backup` marker on success — `rsync -a`
preserves source mtimes, so a freshly-touched marker is the only reliable
"this ran tonight" signal.

## Validation → Pushover

`backup-verify` (`stacks/backup-verify/verify.sh`) checks, and pages Pushover on
any failure:

- every host's `.last-backup` marker is < 2 h old (host silently skipped?)
- DMZ `pg_dump`s for today exist, are non-empty, and pass `gzip -t`
- `backup-offsite` reported `rclone exit=0` and its status is < 6 h old
- the Proxmox template vzdump is present (< 40 d) — **warning only**

Priority 1 (retries) on failure, priority 0 on warning, quiet (-1) when clean.
Creds from `/etc/kdk/pushover.env` (already on every host).

## Proxmox cloud-init template → offsite

The base VM template is **VM 9000** (`qm clone 9000` + `--cicustom`). Because it
isn't reconstructable from Git, it belongs offsite. It is **not** yet automated —
`backup-verify` warns until it lands. To wire it, install on **kdk-hyp-01** a
monthly job that dumps VM 9000 into the NFS backup tree so the offsite stage
ships it:

```bash
# /etc/cron.d/kdk-template-backup on kdk-hyp-01 (NFS backup mounted there)
0 4 1 * * root vzdump 9000 --mode stop --compress zstd \
  --dumpdir /mnt/backup/dkr/pve-templates >/var/log/kdk-template-backup.log 2>&1
```

(Path: whatever local mount maps to `tankz3/backup/dkr/pve-templates` on the PVE
host. Operator-owned — PVE host config is not managed by Komodo.)
