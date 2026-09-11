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
