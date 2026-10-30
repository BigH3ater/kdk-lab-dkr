#!/bin/bash
# Monthly dump of the cloud-init base template (VM 9000, debian13-cloudinit) into
# the shared backup tree so kdk-dkr-01's backup-offsite ships it to Proton. It is
# the one DR asset not reconstructable from Git (PBS also holds 9000 nightly, but
# PBS is on-site). Installed at /usr/local/sbin/kdk-template-backup.sh on
# kdk-hyp-01, run by /etc/cron.d/kdk-template-backup (04:00 on the 1st).
#
# The backup dataset is mounted ON DEMAND over the storage network and released
# at the end, with soft NFS options, so a dead NAS fails this run instead of
# hanging the hypervisor. 2026-10-01: the old run hung for 25 days on a stale
# hard mount of the NAS's pre-renumber address (10.1.1.110) until the 10-26
# reboot, and no offsite dump had been written since 2026-09-14.
set -euo pipefail

NAS=172.16.30.2                      # kdk-nas-01 on the storage net (hyp-01 is .3)
EXPORT=/mnt/tankz3/backup            # == kdk-dkr-01:/mnt/backup
MNT=/mnt/pve/backups
DEST=$MNT/dkr/pve-templates          # backup-verify warns if nothing here is < 40 d
LOCK=/run/lock/kdk-template-backup.lock

exec 9>"$LOCK"
flock -n 9 || { echo "$(date -Is) another run holds $LOCK, exiting"; exit 1; }
echo "$(date -Is) start"

mkdir -p "$MNT"
if ! mountpoint -q "$MNT"; then
  timeout 60 mount -t nfs4 -o vers=4.1,soft,timeo=100,retrans=3,noatime \
    "$NAS:$EXPORT" "$MNT"
  # Only release what this run mounted.
  trap 'umount "$MNT" 2>/dev/null || umount -l "$MNT" 2>/dev/null || true' EXIT
fi
mountpoint -q "$MNT" || { echo "$MNT is not mounted"; exit 1; }
# Never create the tree on the local disk: the directory must come from the NAS.
[ -d "$DEST" ] || { echo "$DEST missing on $NAS:$EXPORT"; exit 1; }

timeout 30m vzdump 9000 --mode stop --compress zstd --dumpdir "$DEST"

# Retention: keep the newest 2 of each artifact type (Proton keeps dated history).
for ext in vma.zst log; do
  ls -t "$DEST"/vzdump-qemu-9000-*."$ext" 2>/dev/null | tail -n +3 | xargs -r rm --
done
echo "$(date -Is) done: $(ls -t "$DEST"/vzdump-qemu-9000-*.vma.zst | head -1)"
