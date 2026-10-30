#!/bin/sh
# Ship the offsite set to Proton, encrypted via the rclone crypt remote. The
# pack step (service `pack`, runs first) has written one tar.zst per host into
# /data/offsite/. rclone syncs ONLY those dated archives + the Proxmox template
# (pve-templates/) + each host's pg_dumps (*/pgdump/) -- a few large objects,
# not the thousands of churny small files that made the Proton API throw
# 502/422/empty-upload and never let a pass finish.
#
# Proton SESSION persistence (this is load-bearing -- do not reintroduce a
# throwaway config). rclone's protondrive backend logs in once with
# username/password + otp_secret_key (it computes the TOTP itself), then caches
# client_uid / client_access_token / client_refresh_token / client_salted_key_pass
# back into rclone.conf. Those cached tokens must survive between passes AND
# between nights, so we run against the WRITABLE host config /config/rclone.conf
# (mounted rw from /opt/kdk-lab/backup/rclone) and let rclone rotate the tokens
# in place. An earlier version copied the config to /tmp per pass and logged in
# fresh every pass; Proton rejects repeated logins that reuse an already-spent
# one-time 2FA with 422 "Incorrect login credentials (Code=8002)", which is why
# passes 2/3/5/6 failed on 2026-10-30. Low concurrency (--transfers 2
# --checkers 4) also avoids the single-use-refresh-token race (Code=10013) that
# the throwaway hack was originally trying to dodge. If the cached session is
# ever fully dead (e.g. untouched for weeks), the owner reseeds it once by hand
# -- see stacks/backup-offsite/README.md.
#
# NO --backup-dir. It must not overlap the sync destination, and every sensible
# place for it on this remote (the sync root is the whole remote) overlaps it --
# that is the exit-7 "destination and parameter to --backup-dir mustn't overlap"
# failure. Remote point-in-time versioning adds little here anyway: `pack` keeps
# the newest 3 archives per host locally and each pg_dump keeps 15, so there is
# already generational history on the NAS. So this is a plain mirror.
#
# Conservative transfer flags: Proton is a flaky, rate-limited API, so keep
# concurrency low and retry hard on blips (--retries 3 --low-level-retries 10).
# --protondrive-replace-existing-draft clears the draft a cancelled/failed
# upload leaves, so a resumed pass does not 422 on the same path. Filters are
# all --filter rules (not mixed --include/--exclude), so their order is
# deterministic -- rclone warns when both are used together.
#
# Writes .offsite-status with the final rclone exit code for backup-verify, then
# always exits 0 so the verify stage runs and is the single Pushover source. A
# pass that hits --max-duration exits 10 and the next pass resumes (reusing the
# persisted session); rc=0 = fully in sync.
pass=1
while :; do
  rclone sync /data proton-crypt: \
    --config /config/rclone.conf \
    --transfers 2 --checkers 4 --retries 3 --low-level-retries 10 \
    --log-level NOTICE --max-duration 25m \
    --protondrive-replace-existing-draft=true \
    --filter "+ /offsite/**" \
    --filter "+ /pve-templates/**" \
    --filter "+ /*/pgdump/**" \
    --filter "- **"
  rc=$?
  echo "offsite pass $pass: rclone exit=$rc"
  [ "$rc" -eq 0 ] || [ "$pass" -ge 6 ] && break
  pass=$((pass + 1))
done
echo "rc=$rc ts=$(date +%s) date=$(date -Iseconds)" > /data/.offsite-status
echo "offsite rclone exit=$rc"
exit 0
