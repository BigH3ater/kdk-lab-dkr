#!/bin/bash
# Read-only rclone crypt mount of the Proton offsite backups (kdk-lab-backup),
# for viewing and validating restores from the laptop.
#
#   offsite-mount.sh          mount at ~/mnt/offsite (read-only, background)
#   offsite-mount.sh -u       unmount
#   offsite-mount.sh status   show whether it is mounted
#
# Uses the laptop's OWN `proton` remote (~/.config/rclone/rclone.conf), a
# separate Proton session from kdk-dkr-04's. Never copy dkr-04's rclone.conf
# here: Proton refresh tokens are single-use, so two clients on one session
# break the nightly offsite upload (see stacks/backup-offsite/offsite.sh).
#
# The crypt remote is NOT stored in rclone.conf: it lives only in this
# process's environment, built from 1Password at mount time, so the key that
# decrypts every backup never sits on the laptop disk. Settings must match
# dkr-04's [proton-crypt]: remote = proton:kdk-lab-backup, one password, no
# password2, default filename encryption.
set -euo pipefail
RCLONE=${RCLONE:-$HOME/.local/bin/rclone}
MNT=${MNT:-$HOME/mnt/offsite}

case "${1:-}" in
  -u|umount|unmount) fusermount3 -u "$MNT" && echo "unmounted $MNT"; exit 0 ;;
  status) findmnt "$MNT" >/dev/null && echo "mounted: $MNT" || echo "not mounted"; exit 0 ;;
esac

findmnt "$MNT" >/dev/null && { echo "already mounted: $MNT"; exit 0; }
mkdir -p "$MNT"

export RCLONE_CONFIG_OFFSITE_TYPE=crypt
export RCLONE_CONFIG_OFFSITE_REMOTE=proton:kdk-lab-backup
RCLONE_CONFIG_OFFSITE_PASSWORD=$(op read 'op://kdk-ops/proton-mirror-crypt/password' | "$RCLONE" obscure -)
export RCLONE_CONFIG_OFFSITE_PASSWORD

# Low concurrency, as on dkr-04: Proton's API is flaky under load.
"$RCLONE" mount offsite: "$MNT" \
  --read-only --daemon --daemon-wait 60s \
  --vfs-cache-mode off --dir-cache-time 5m \
  --transfers 2 --checkers 4 \
  --log-file "$HOME/.cache/offsite-mount.log" --log-level NOTICE
echo "mounted $MNT (read-only). Unmount: offsite-mount.sh -u"
