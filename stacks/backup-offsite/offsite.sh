#!/bin/sh
# Ship the whole backup tree (all hosts) to Proton, encrypted via the rclone
# crypt remote. Proton must hold ONLY application state + the Proxmox cloud-init
# template (VM 9000 vzdump under pve-templates/); everything regenerable or
# redundant is excluded below. --backup-dir keeps a dated copy of anything
# deleted/overwritten (point-in-time recovery vs a destructive mirror).
#
# Proton sessions: a login's access token lasts 30 min, and its refresh token is
# single-use -- rclone's concurrent workers race on the refresh and Proton kills
# the session (Code=10013 "Invalid refresh token"). That failed every nightly
# offsite 2026-09-21..10-04. So never refresh: run sync in <=25 min passes, each
# on a throwaway copy of the config (no cached tokens) so every pass logs in
# fresh with the stored password + OTP secret. A pass that hits --max-duration
# exits 10 and the next pass resumes; rc=0 means the tree is fully in sync.
#
# Writes .offsite-status with the final rclone exit code for backup-verify, then
# always exits 0 so the verify stage runs and is the single Pushover source.
pass=1
while :; do
  cp /config/rclone.conf /tmp/rclone.conf
  rclone sync /data proton-crypt: \
    --config /tmp/rclone.conf --transfers 4 --checkers 8 --log-level NOTICE \
    --max-duration 25m \
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
    --exclude "**/proton-bridge/**/updates/**" \
    --exclude "**/node_modules/**" \
    --exclude "**/Tdarr/DB2/JobReports/**" \
    --exclude "**/tandoor/staticfiles/**" \
    --exclude "**/audiobookshelf/podcasts/**"
  rc=$?
  echo "offsite pass $pass: rclone exit=$rc"
  [ "$rc" -eq 0 ] || [ "$pass" -ge 12 ] && break
  pass=$((pass + 1))
done
echo "rc=$rc ts=$(date +%s) date=$(date -Iseconds)" > /data/.offsite-status
echo "offsite rclone exit=$rc"
exit 0
