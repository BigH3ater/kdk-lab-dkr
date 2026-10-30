#!/bin/sh
# Ship the offsite set to Proton, encrypted via the rclone crypt remote. The
# pack step (service `pack`, runs first) has written one tar.zst per host into
# /data/offsite/. rclone now syncs ONLY those dated archives + the Proxmox
# template (pve-templates/) + each host's pg_dumps (*/pgdump/) -- a few large
# objects, not the thousands of churny small files that made the Proton API
# throw 502/422/empty-upload every night and never let a pass finish.
# --backup-dir keeps a dated copy of anything deleted/overwritten (point-in-time
# recovery, not a destructive mirror); yesterday's archives land there too.
#
# Proton sessions: a login's access token lasts 30 min, and its refresh token is
# single-use -- rclone's concurrent workers race on the refresh and Proton kills
# the session (Code=10013 "Invalid refresh token"). So never refresh: run sync
# in <=25 min passes, each on a throwaway copy of the config (no cached tokens)
# so every pass logs in fresh with the stored password + OTP secret. A pass that
# hits --max-duration exits 10 and the next pass resumes; rc=0 = fully in sync.
#
# Conservative transfer flags: Proton is a flaky, rate-limited API, so keep
# concurrency low (--transfers 2 --checkers 4) and retry hard on blips
# (--retries 3 --low-level-retries 10). --protondrive-replace-existing-draft
# clears the draft left by a cancelled/failed upload so a resumed pass does not
# 422 on the same path. Filenames are dated, so each night's archive is a new
# object uploaded once (Proton has no mod-time support but does have SHA1, so a
# same-name re-sync would still be size+hash-checked, not blindly re-uploaded).
#
# Writes .offsite-status with the final rclone exit code for backup-verify, then
# always exits 0 so the verify stage runs and is the single Pushover source.
pass=1
while :; do
  cp /config/rclone.conf /tmp/rclone.conf
  rclone sync /data proton-crypt: \
    --config /tmp/rclone.conf \
    --transfers 2 --checkers 4 --retries 3 --low-level-retries 10 \
    --log-level NOTICE --max-duration 25m \
    --protondrive-replace-existing-draft=true \
    --backup-dir "proton-crypt:_archive/$(date +%F)" \
    --include "offsite/**" \
    --include "pve-templates/**" \
    --include "*/pgdump/**" \
    --exclude "**"
  rc=$?
  echo "offsite pass $pass: rclone exit=$rc"
  [ "$rc" -eq 0 ] || [ "$pass" -ge 6 ] && break
  pass=$((pass + 1))
done
echo "rc=$rc ts=$(date +%s) date=$(date -Iseconds)" > /data/.offsite-status
echo "offsite rclone exit=$rc"
exit 0
