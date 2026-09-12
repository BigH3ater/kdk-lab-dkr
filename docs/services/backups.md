---
title: Backups
---
# Backups

| Layer | What | Where |
|---|---|---|
| ZFS snapshots | vmpool (DMZ VM zvol), tankz3 (bulk + backup datasets) | TrueNAS tasks |
| VM-level | vzdump of VM 300 to PBS (follow-up: PBS auth fix); TrueNAS zvol replication for the DMZ VM | Proxmox / TrueNAS |
| App-level | nightly Komodo Procedure `nightly-backups`: pg dumps (authelia, immich) + rsync /opt/kdk-lab → tankz3/backup/dkr/<host> | `stacks/backup-*` |
| Offsite | rclone → Proton Drive from the backup dataset (pending kdk-ops vault grant for the Connect token) | `stacks/backup-dkr-01` offsite service |

Restore: untar/rsync back from /mnt/backup, psql < dump. Jellyfin/media tars from
the migration remain under tankz3/backup/migrate until parity is confirmed.

## Offsite encryption (Proton)

Offsite uploads go through an rclone `crypt` remote (`proton-crypt`, on kdk-dkr-01
at `/opt/kdk-lab/backup/rclone/rclone.conf`) wrapping `proton:kdk-lab-backup`, so
everything on Proton Drive is client-side encrypted. The crypt password is
`op://kdk-ops/proton-mirror-crypt/password`; the Proton account login uses an
`otp_secret_key` so rclone mints TOTP codes headless. Pin the rclone image to the
version that performed the interactive login (currently 1.75) — older images
cannot read its cached session tokens.

> **Rotating the crypt password purges the target.** Objects encrypted under the
> old password are undecryptable by the new remote and `sync` will never remove
> them — they accumulate as dead cruft. On any crypt-password change you MUST
> `rclone purge proton:kdk-lab-backup` (the whole encrypted tree) before the first
> sync under the new password. Same applies to a Proton account change.

## Offsite status (2026-09-11)

Encrypted offsite to Proton is **built and proven** — the crypt remote uploaded a
batch of encrypted objects successfully. It is **currently gated** (compose
`offsite` profile) because Proton throttled/soft-locked password auth after the
many login attempts during setup, and rclone's cached session tokens expired.
The nightly Procedure runs only the local NAS rsync until offsite is re-armed.

**To re-arm** (after Proton auth cools down, hours): do ONE clean interactive
`rclone config` login for the `proton` remote on a stable host, copy the raw
config (with fresh cached tokens) to `/opt/kdk-lab/backup/rclone/rclone.conf`,
confirm `rclone lsd proton-crypt:` works, then remove `profiles: ["offsite"]`
from `stacks/backup-dkr-01/compose.yaml`. Do NOT run repeated headless logins —
Proton throttles them. If unattended offsite proves persistently fragile,
prefer a provider with a stable API token (or the ZFS-replication/Garage tier)
over Proton Drive for automation.
