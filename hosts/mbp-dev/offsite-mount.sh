#!/bin/bash
# Read-only rclone crypt mount of the Proton offsite backups (kdk-lab-backup),
# for viewing and validating restores from the laptop.
#
#   offsite-mount.sh          mount at ~/mnt/offsite (read-only, background,
#                             unmounts itself after OFFSITE_MAXRUN, default 2h)
#   offsite-mount.sh -u       unmount now
#   offsite-mount.sh status   show whether it is mounted, and since when
#
# Auto-unmount: a mount nobody is using must not stay up. On 2026-11-16 a
# forgotten mount let an unrelated whole-disk search (`bfs / -name ...`) walk
# ~10k Proton files; every directory is an API call plus two layers of
# decryption, so rclone sat at 260% CPU. The mount therefore runs under
# `timeout`: when OFFSITE_MAXRUN expires rclone gets SIGTERM (clean unmount),
# and a lazy `fusermount3 -uz` follows in case something still held it open.
#
# Uses the laptop's OWN `proton` remote (~/.config/rclone/rclone.conf), a
# separate Proton session from kdk-dkr-04's. Never copy dkr-04's rclone.conf
# here: Proton refresh tokens are single-use, so two clients on one session
# break the nightly offsite upload (see stacks/backup-offsite/offsite.sh).
#
# The crypt remote is NOT stored in rclone.conf: it lives only in the mount
# process's environment (never on a command line, never in a unit file), built
# from 1Password at mount time, so the key that decrypts every backup never sits
# on the laptop disk. Settings must match dkr-04's [proton-crypt]: remote =
# proton:kdk-lab-backup, one password, no password2, default filename encryption.
set -euo pipefail
RCLONE=${RCLONE:-$HOME/.local/bin/rclone}
MNT=${MNT:-$HOME/mnt/offsite}
MAXRUN=${OFFSITE_MAXRUN:-2h}
PIDF=${XDG_RUNTIME_DIR:-/tmp}/offsite-mount.pid
LOG=$HOME/.cache/offsite-mount.log

stop() {
  # Clean unmount first; a busy mount (a shell cd'd into it, a crawler holding
  # directories open) gets a lazy unmount. A lazily detached rclone lives on for
  # as long as anything holds a directory open (a crawler keeps walking from the
  # handles it has), so rclone itself must be stopped: TERM the process group of
  # `timeout` (timeout + rclone; GNU timeout runs in its OWN group, not the
  # wrapper's), then KILL after 10 s.
  fusermount3 -u "$MNT" 2>/dev/null || fusermount3 -uz "$MNT" 2>/dev/null || true
  if [ -f "$PIDF" ]; then
    pg=$(cut -d" " -f1 "$PIDF")
    kill -TERM -- "-$pg" 2>/dev/null || true
    for _ in $(seq 10); do kill -0 -- "-$pg" 2>/dev/null || break; sleep 1; done
    kill -KILL -- "-$pg" 2>/dev/null || true
    rm -f "$PIDF"
  fi
}

case "${1:-}" in
  -u|umount|unmount) stop; echo "unmounted $MNT"; exit 0 ;;
  status)
    if findmnt "$MNT" >/dev/null; then
      echo "mounted: $MNT (since $(stat -c %y "$PIDF" 2>/dev/null | cut -d. -f1), auto-unmount after $(cut -d" " -f2 "$PIDF" 2>/dev/null))"
    else
      echo "not mounted"
    fi
    exit 0 ;;
esac

findmnt "$MNT" >/dev/null && { echo "already mounted: $MNT"; exit 0; }
mkdir -p "$MNT" "$(dirname "$LOG")"

export RCLONE_CONFIG_OFFSITE_TYPE=crypt
export RCLONE_CONFIG_OFFSITE_REMOTE=proton:kdk-lab-backup
RCLONE_CONFIG_OFFSITE_PASSWORD=$(op read 'op://kdk-ops/proton-mirror-crypt/password' | "$RCLONE" obscure -)
export RCLONE_CONFIG_OFFSITE_PASSWORD

# Foreground rclone under timeout, detached into its own session (setsid -f) so
# closing the terminal does not kill it; the key travels in the environment.
# Low concurrency, as on dkr-04: Proton's API is flaky under load.
# shellcheck disable=SC2016  # expands in the child bash, on purpose
setsid -f bash -c '
  timeout --signal=TERM --kill-after=30s "$3" "$1" mount offsite: "$2" \
    --read-only --vfs-cache-mode off --dir-cache-time 5m \
    --transfers 2 --checkers 4 --log-file "$5" --log-level NOTICE &
  echo "$! $3" > "$4"
  wait $!
  fusermount3 -uz "$2" 2>/dev/null
  rm -f "$4"
' _ "$RCLONE" "$MNT" "$MAXRUN" "$PIDF" "$LOG" </dev/null >/dev/null 2>&1

for _ in $(seq 60); do
  findmnt "$MNT" >/dev/null && { echo "mounted $MNT (read-only, auto-unmount after $MAXRUN). Unmount now: offsite-mount.sh -u"; exit 0; }
  sleep 1
done
echo "mount did not come up within 60 s; see $LOG" >&2
stop
exit 1
