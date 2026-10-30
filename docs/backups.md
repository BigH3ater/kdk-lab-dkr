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

`nightly-backups` Procedure (03:20 America/Chicago — `:20` avoids the `gitops-sync` `:00/:15/:30/:45` slots), five sequential stages:

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

The base VM template is **VM 9000** (`debian13-cloudinit`; VMs are built with
`qm clone 9000` + `--cicustom`). Because it isn't reconstructable from Git, it
belongs offsite. PBS (`kdk-pbs-01`) also backs it up nightly, but PBS is on-site.

**Automated, from the PVE host.** A monthly cron on kdk-hyp-01 dumps VM 9000
straight into the shared backup tree (`tankz3/backup`, == dkr-01's
`/mnt/backup`) and the `offsite` stage ships it:

- `hosts/kdk-hyp-01/kdk-template-backup.sh` → `/usr/local/sbin/kdk-template-backup.sh`:
  mounts `172.16.30.2:/mnt/tankz3/backup` on `/mnt/pve/backups` **on demand over
  the storage network** (NFSv4.1, `soft`, 60 s mount timeout), refuses to run if
  `dkr/pve-templates/` is not there (so it can never fill the local disk), then
  `vzdump 9000 --mode stop --compress zstd --dumpdir /mnt/pve/backups/dkr/pve-templates`
  under a 30 min `timeout`, keeps the newest 2 dumps, and unmounts. `flock` keeps
  runs from overlapping.
- `hosts/kdk-hyp-01/kdk-template-backup.cron` → `/etc/cron.d/kdk-template-backup`,
  `0 4 1 * *` (1st of the month, 04:00), log `/var/log/kdk-template-backup.log`.

This is the one backup component that lives on the PVE host, not in Komodo (PVE
host config isn't a Komodo periphery), so it is applied by hand from `hosts/`
(see `hosts/README.md`). A dump is ~283 MB (template disk is 95% sparse).
`backup-verify` warns if no dump is < 40 days old.

Why on demand: hyp-01 used to carry a permanent hard NFS mount of the NAS's old
address (`10.1.1.110`). After the storage network was renumbered to
`172.16.30.0/24` that mount went stale, the 2026-10-01 run hung on it for 25 days
(until the 10-26 reboot) and no offsite dump existed after 2026-09-14. A soft,
on-demand mount fails loudly instead.

Run it by hand (after an install, or to check): `sudo /usr/local/sbin/kdk-template-backup.sh`,
then `sudo ls -la /mnt/backup/dkr/pve-templates/` on dkr-01 shows today's
`vzdump-qemu-9000-*.vma.zst`.

