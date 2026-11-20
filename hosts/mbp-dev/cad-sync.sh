#!/usr/bin/env bash
# cad-sync.sh -- push the laptop CAD library (~/cad) to the kdk-nas-01 SMB share
# 3d-prints, mounted at /mnt/3d-prints by mnt-3d\x2dprints.automount. Run every
# 15 min by the user timer cad-sync.timer. docs/runbooks/cad-library.md
#
# One-way, additive: NO --delete, so a file removed or renamed on the laptop
# stays on the NAS (snapshots + offsite keep history either way). New project
# folders need nothing: -r picks them up.
#
# Away from home (NAS unreachable) it exits 0 without a word, so the journal
# and `systemctl --user --failed` stay clean. Every network touch is under a
# timeout, so a dead automount can never hang the timer.
#
# Lock files: LibreOffice `.~lock.<name>#` (.~lock*) and SolidCraft
# `.~<name>.sc.lock` (.~*.lock); keep in step with stacks/backup-offsite/pack.sh.
#
# Names with a backslash (e.g. a systemd unit copy `mnt-foo\x2dbar.mount`) cannot
# exist on an SMB share: the server takes `\` as a path separator, rsync gets
# "Invalid argument" and the whole run fails with rc 23, so .last-sync goes
# stale. They are excluded and named in one log line per run.
#
# CIFS has no unix owners/modes: -rt only (no -a, -o, -g, -p).
# --modify-window=2 absorbs the SMB timestamp rounding so unchanged files are
# not re-sent every run.
#
# On success writes /mnt/3d-prints/.last-sync ("<ISO time> <hostname>");
# backup-verify warns when it is older than 14 days.
#
# Usage: cad-sync.sh [--dry-run]   (dry run lists what would change, writes nothing)
set -uo pipefail

SRC="$HOME/cad/"
DST=/mnt/3d-prints
NAS=10.1.3.5
DRY=()
[ "${1:-}" = "--dry-run" ] && DRY=(--dry-run --itemize-changes)

[ -d "$SRC" ] || { echo "cad-sync: $SRC missing, nothing to push"; exit 0; }

# 1. NAS reachable on SMB? 3 s TCP probe; fails fast on any other network.
timeout 3 bash -c "exec 3<>/dev/tcp/$NAS/445" 2>/dev/null || exit 0

# 2. Share mounted? statfs on the path triggers the automount; the timeout
#    covers a slow or failing mount. Anything but cifs/smb2 (e.g. the bare
#    local directory when the automount is not installed) means: do not write.
fstype=$(timeout 30 stat -f -c %T "$DST/." 2>/dev/null) || fstype=""
case "$fstype" in
  cifs|smb2|smb) ;;
  *) echo "cad-sync: $DST is not the SMB share (got '${fstype:-no answer}'), skipping"; exit 0 ;;
esac

# 3. Names the share cannot hold: say which, once per run, then leave them out.
bs=$(find "$SRC" -name '*\\*' -printf '%P\n' 2>/dev/null | head -5 | paste -sd, -)
[ -n "$bs" ] && echo "cad-sync: not pushed, backslash in the name (SMB cannot store it): $bs"

# 4. Push. rc 24 = a source file vanished mid-run (a model being saved):
#    everything else was sent, counts as success.
timeout 900 rsync -rt --modify-window=2 "${DRY[@]}" \
  --exclude='*.log' --exclude='__pycache__/' --exclude='.~lock*' --exclude='.~*.lock' \
  --exclude='/.last-sync' --exclude='*\\*' \
  "$SRC" "$DST/"
rc=$?
if [ "$rc" -ne 0 ] && [ "$rc" -ne 24 ]; then
  echo "cad-sync: rsync failed rc=$rc"
  exit "$rc"
fi

[ "${#DRY[@]}" -gt 0 ] && exit 0
if ! { printf '%s %s\n' "$(date -Iseconds)" "$(hostname)" > "$DST/.last-sync.tmp" \
       && mv -f "$DST/.last-sync.tmp" "$DST/.last-sync"; }; then
  echo "cad-sync: could not write $DST/.last-sync"
  exit 1
fi
exit 0
