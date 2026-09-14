#!/bin/sh
# Ship the whole backup tree (all hosts) to Proton, encrypted via the rclone
# crypt remote. Proton must hold ONLY application state + the Proxmox cloud-init
# template (VM 9000 vzdump under pve-templates/); everything regenerable or
# redundant is excluded below. --backup-dir keeps a dated copy of anything
# deleted/overwritten (point-in-time recovery vs a destructive mirror).
# Writes .offsite-status with the rclone exit code for backup-verify, then
# always exits 0 so the verify stage runs and is the single Pushover source.
rclone sync /data proton-crypt: \
  --config /config/rclone.conf --transfers 4 --log-level NOTICE \
  --protondrive-replace-existing-draft=true \
  --backup-dir "proton-crypt:_archive/$(date +%F)" \
  --exclude "_archive/**" \
  --exclude ".offsite-status" \
  --exclude "**/.last-backup" \
  --exclude "**/stacks/**" \
  --exclude "**/backup/**" \
  --exclude "**/lost+found/**" \
  --exclude "**/jellyfin/**/metadata/**" \
  --exclude "**/jellyfin/**/cache/**" \
  --exclude "**/jellyfin/**/transcodes/**" \
  --exclude "**/MediaCover/**" \
  --exclude "**/sabnzbd/logs/**" \
  --exclude "**/proton-bridge/**/updates/**"
rc=$?
echo "rc=$rc ts=$(date +%s) date=$(date -Iseconds)" > /data/.offsite-status
echo "offsite rclone exit=$rc"
exit 0
