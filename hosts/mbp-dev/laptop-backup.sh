#!/usr/bin/env bash
# laptop-backup.sh -- nightly restic backup of the laptop (/home/jmack, /bench,
# /etc) to the kdk-nas-01 SMB share laptop-backup, mounted at
# /mnt/laptop-backup by mnt-laptop\x2dbackup.automount. Run daily by the system
# timer laptop-backup.timer, as root. docs/runbooks/laptop-backup.md
#
# Repository: /mnt/laptop-backup/restic (restic init once, by hand: runbook 2e).
# Password:   /etc/restic/laptop-backup.pass (root 0600, from 1Password
#             kdk-ops/mbp-dev-restic-repo).
# Host name in the repo is always mbp-dev (also before the 2026-11-19 reinstall,
# when the laptop was still debian-mbp), so the snapshot history is one line.
#
# Away from home (NAS unreachable) it exits 0 with one log line, so
# `systemctl --failed` stays clean. Every network touch is under a timeout.
#
# Retention every run: 7 daily, 4 weekly, 12 monthly, and every snapshot
# tagged pre-wipe forever. prune on Sundays (or --prune), check on the 1st
# (or --check). Only restic 0.14 options (Debian 12 apt); 0.18 runs it too.
#
# Usage: laptop-backup.sh [--tag TAG] [--prune] [--check]
#   e.g. laptop-backup.sh --tag pre-wipe
set -uo pipefail

REPO=/mnt/laptop-backup/restic
MNT=/mnt/laptop-backup
PASS=/etc/restic/laptop-backup.pass
EXCLUDES=/etc/restic/laptop-backup.excludes
HOST=mbp-dev
NAS=10.1.3.5
TAGS=()
PRUNE=0
CHECK=0
[ "$(date +%u)" = 7 ] && PRUNE=1
[ "$(date +%d)" = 01 ] && CHECK=1

while [ $# -gt 0 ]; do
  case "$1" in
    --tag) [ -n "${2:-}" ] || { echo "laptop-backup: --tag needs a value"; exit 2; }
           TAGS+=(--tag "$2"); shift 2 ;;
    --prune) PRUNE=1; shift ;;
    --check) CHECK=1; shift ;;
    *) echo "usage: laptop-backup.sh [--tag TAG] [--prune] [--check]"; exit 2 ;;
  esac
done

[ "$(id -u)" -eq 0 ] || { echo "laptop-backup: run as root"; exit 2; }
[ -r "$PASS" ] || { echo "laptop-backup: $PASS missing"; exit 1; }
[ -r "$EXCLUDES" ] || { echo "laptop-backup: $EXCLUDES missing"; exit 1; }

# 1. NAS reachable on SMB? 3 s TCP probe; fails fast on any other network.
if ! timeout 3 bash -c "exec 3<>/dev/tcp/$NAS/445" 2>/dev/null; then
  echo "laptop-backup: NAS $NAS not reachable, skipping"
  exit 0
fi

# 2. Share mounted? statfs on the path triggers the automount; the timeout
#    covers a slow or failing mount. Anything but cifs/smb2 (e.g. the bare
#    local directory) means: do not write.
fstype=$(timeout 30 stat -f -c %T "$MNT/." 2>/dev/null) || fstype=""
case "$fstype" in
  cifs|smb2|smb) ;;
  *) echo "laptop-backup: $MNT is not the SMB share (got '${fstype:-no answer}'), skipping"; exit 0 ;;
esac

export RESTIC_REPOSITORY="$REPO" RESTIC_PASSWORD_FILE="$PASS"

# 3. Backup, streamed live (journal / terminal) and kept in a temp file for the
#    snapshot id. rc 3 = some files could not be read; the snapshot is still made.
log=$(mktemp) || { echo "laptop-backup: mktemp failed"; exit 1; }
trap 'rm -f "$log"' EXIT
restic backup /home/jmack /bench /etc --host "$HOST" "${TAGS[@]}" \
  --exclude-file "$EXCLUDES" --exclude-caches --one-file-system 2>&1 | tee "$log"
rc=${PIPESTATUS[0]}
if [ "$rc" -ne 0 ] && [ "$rc" -ne 3 ]; then
  echo "laptop-backup: restic backup failed rc=$rc"
  exit "$rc"
fi
snap=$(sed -n 's/^snapshot \([0-9a-f]*\) saved$/\1/p' "$log" | tail -1)
echo "laptop-backup: backup OK snapshot=${snap:-unknown} rc=$rc"

# 4. Retention (prune only when asked or on Sundays: it rewrites packs).
PR=()
[ "$PRUNE" -eq 1 ] && PR=(--prune)
if ! restic forget --host "$HOST" --keep-daily 7 --keep-weekly 4 --keep-monthly 12 \
       --keep-tag pre-wipe "${PR[@]}"; then
  echo "laptop-backup: restic forget failed"
  exit 1
fi

# 5. Repository check (structure only, no data read).
if [ "$CHECK" -eq 1 ]; then
  if ! restic check; then
    echo "laptop-backup: restic check failed"
    exit 1
  fi
  echo "laptop-backup: check OK"
fi
exit 0
