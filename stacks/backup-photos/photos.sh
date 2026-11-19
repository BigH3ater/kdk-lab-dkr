#!/bin/sh
# backup-photos: nightly FILE-BY-FILE rclone sync of the photo ORIGINALS to the
# offsite crypt remote(s). Runs on kdk-dkr-04 as its own stage of the
# nightly-backups procedure, AFTER backup-offsite (never at the same time: both
# use the one Proton session in /config/rclone.conf, whose refresh token is
# single-use, see stacks/backup-offsite/offsite.sh) and BEFORE backup-verify.
#
# Sources (read-only binds, see compose.yaml):
#   /src/immich/{library,upload,profile}  Immich originals (NAS tankz3/appdata/immich,
#                                         ro NFS on dkr-04 at /mnt/immich). NOT thumbs/,
#                                         encoded-video/ or backups/ (regenerable / the
#                                         DB dump already ships with the host pgdumps).
#   /src/insta360-flat                    finished Insta360 stills (tankz3/insta360-flat)
# Destination per remote: <remote>:photos/immich/... and <remote>:photos/insta360-flat/...
#
# Why not the tar pack: photos are large, immutable and only ever added, so
# packing them would re-upload the whole (growing) library every night. A
# file-by-file sync uploads each photo once; a normal night moves only the new ones.
#
# DELETION SAFETY (rclone sync propagates deletes):
#   1. per source, BEFORE any rclone run, the guard refuses the source when
#      a. a bind is not NFS in /proc/self/mountinfo (host mount down: Docker binds
#         the bare local directory, which is empty),
#      b. its marker is missing (Immich's library/.immich; >= 1 trip folder for Insta360),
#      c. it has no files, or
#      d. it has fewer than MIN_PCT % of the files of the last accepted run
#         (PHOTOS_ALLOW_SHRINK=1 on a by-hand run accepts a real big delete).
#      A refused source is left out with a `- /<source>/**` filter, and rclone sync
#      never deletes destination files its filters exclude (no --delete-excluded),
#      so its offsite copy is untouched. verify FAILs the night on any refusal.
#   2. --max-delete MAX_DELETE: more deletes than that in one run is a fatal error
#      (rclone exit 7) and nothing past the limit is deleted. verify FAILs.
#   3. rclone itself skips all deletes when a pass had any I/O error, and deletes
#      run after the transfers (--delete-after, the default), so a pass cut short
#      by --max-duration deletes nothing.
#
# PROTON: same lessons as offsite.sh. Persisted writable session config, low
# concurrency (--transfers 2 --checkers 4), hard retries, a light --tpslimit,
# --protondrive-replace-existing-draft, passes capped at 25 min, up to MAX_PASSES
# passes a night. The first run uploads the whole library and will not finish
# in one night: each pass and each night resumes where the last stopped (files
# already on the remote are skipped). --size-only: Proton cannot update
# modification times, and originals are immutable (a changed original is a new
# file name), so size is the safe and cheap comparison.
#
# Writes, per remote, /data/.photos-status-<remote> (read by backup-verify):
#   rc=N ts=EPOCH date=ISO passes=N guard=ok|refused:<why>,... last_ok=EPOCH|0 seed_start=EPOCH
# last_ok = time of the last run that synced every source with rc=0 (carried
# forward); seed_start = time of the first ever run (carried forward).
# Guard state (last accepted file/byte counts): /data/.photos-state.
# Always exits 0 so verify runs and stays the single Pushover source.
#
# Log lines for the "Nightly backups" dashboard (Alloy -> Loki
# {stack="backup-photos", service="photos"}), logfmt like pack.sh's:
#   backup-manifest date=D kind=photos host=kdk-nas-01 series=immich|insta360-flat name=photos/<series> bytes=N prev_bytes=N|none pct=P|none files=N prev_files=N|none guard=ok|<why>
#   photos pass N: remote=R rclone exit=N secs=S
#   photos rclone exit=N remote=R passes=N secs=S guard=... mode=sync|copy
#
# B2 (PR 4b) = one more name in PHOTOS_REMOTES plus its case in dest_of/flags_of.
#
# Env (all optional): PHOTOS_MODE (sync; copy = never delete on the remote),
# PHOTOS_REMOTES ("proton"), PHOTOS_MAX_PASSES (4), PHOTOS_MAX_DELETE (100),
# PHOTOS_MIN_PCT (50), PHOTOS_ALLOW_SHRINK (0), PHOTOS_DRY_RUN (0: 1 = rclone
# --dry-run, writes no status/state).
# Test hooks (tests/guard-test.sh): PHOTOS_SRC, PHOTOS_DATA, PHOTOS_MOUNTINFO,
# PHOTOS_RCLONE_CONFIG, PHOTOS_DEST_<remote>.
set -u
MODE=${PHOTOS_MODE:-sync}
case $MODE in sync|copy) ;; *) echo "photos: PHOTOS_MODE=$MODE is not sync|copy, using sync"; MODE=sync ;; esac
SRC=${PHOTOS_SRC:-/src}
DATA=${PHOTOS_DATA:-/data}
MOUNTINFO=${PHOTOS_MOUNTINFO:-/proc/self/mountinfo}
CONF=${PHOTOS_RCLONE_CONFIG:-/config/rclone.conf}
REMOTES=${PHOTOS_REMOTES:-proton}
MAX_PASSES=${PHOTOS_MAX_PASSES:-4}
MAX_DELETE=${PHOTOS_MAX_DELETE:-100}
MIN_PCT=${PHOTOS_MIN_PCT:-50}
ALLOW_SHRINK=${PHOTOS_ALLOW_SHRINK:-0}
DRY=${PHOTOS_DRY_RUN:-0}
DATE=$(date +%F)
STATE=$DATA/.photos-state
export RCLONE_CACHE_DIR=/tmp/rclone-cache

# dest_of REMOTE: the rclone destination root for that remote.
dest_of() {
  eval "o=\${PHOTOS_DEST_$1:-}"
  [ -n "$o" ] && { echo "$o"; return 0; }
  case $1 in
    proton) echo "proton-crypt:photos" ;;
    *) return 1 ;;
  esac
}
# flags_of REMOTE: backend-specific flags.
flags_of() {
  case $1 in
    proton) echo "--protondrive-replace-existing-draft=true --tpslimit 10" ;;
    *) echo "" ;;
  esac
}

# fstype_of PATH: filesystem type of the mount whose mount point is exactly PATH
# (field 5 of mountinfo; the type follows the " - " separator), or empty.
fstype_of() {
  awk -v m="$1" '$5 == m { for (i = 7; i <= NF; i++) if ($i == "-") { t = $(i + 1); break } }
    END { print t }' "$MOUNTINFO" 2>/dev/null
}

# size_of DIR: "COUNT BYTES" of the regular files under DIR (rclone's local backend).
size_of() {
  j=$(rclone size --json --config "" "$1" 2>/dev/null) || return 1
  c=$(printf '%s' "$j" | sed -n 's/.*"count":\([0-9]*\).*/\1/p')
  b=$(printf '%s' "$j" | sed -n 's/.*"bytes":\([0-9]*\).*/\1/p')
  [ -n "$c" ] && [ -n "$b" ] || return 1
  echo "$c $b"
}

# Last accepted counts (immich_files=, immich_bytes=, insta360_files=, insta360_bytes=).
immich_files=0; immich_bytes=0; insta360_files=0; insta360_bytes=0
[ -f "$STATE" ] && . "$STATE" 2>/dev/null

# guard KEY SERIES DIR MARKER BIND...: decide one source; sets g_why (empty = ok),
# g_files, g_bytes, and prints its manifest line.
guard() {
  key=$1; series=$2; dir=$3; marker=$4; shift 4
  g_why=""; g_files=""; g_bytes=""
  for b in "$@"; do
    t=$(fstype_of "$b")
    case $t in nfs|nfs4) ;; *) g_why="not-nfs:${b#"$SRC"/}:${t:-none}"; break ;; esac
  done
  if [ -z "$g_why" ]; then
    case $marker in
      dir) [ -n "$(find "$dir" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | head -1)" ] || g_why="no-marker" ;;
      *) [ -e "$dir/$marker" ] || g_why="no-marker" ;;
    esac
  fi
  if s=$(size_of "$dir"); then
    g_files=${s% *}; g_bytes=${s#* }
  else
    g_files=0; g_bytes=0; [ -n "$g_why" ] || g_why="unreadable"
  fi
  eval "lf=\$${key}_files; lb=\$${key}_bytes"
  if [ -z "$g_why" ] && [ "$g_files" -eq 0 ]; then g_why="empty"; fi
  if [ -z "$g_why" ] && [ "$lf" -gt 0 ] && [ $((g_files * 100)) -lt $((lf * MIN_PCT)) ]; then
    if [ "$ALLOW_SHRINK" = 1 ]; then
      echo "photos: $series shrank to $g_files files (last $lf), accepted (PHOTOS_ALLOW_SHRINK=1)"
    else
      g_why="shrank:$g_files/$lf"
    fi
  fi
  pb=none; pct=none; pf=none
  if [ "$lb" -gt 0 ]; then
    pb=$lb; pf=$lf
    pct=$(awk -v b="$g_bytes" -v p="$lb" 'BEGIN { printf "%.1f", b * 100 / p }')
  fi
  echo "backup-manifest date=$DATE kind=photos host=kdk-nas-01 series=$series name=photos/$series bytes=$g_bytes prev_bytes=$pb pct=$pct files=$g_files prev_files=$pf guard=${g_why:-ok}"
}

# Filter rules, one per line ("+ /immich/**" or "- /insta360-flat/**").
NL='
'
FILTERS=""; REFUSED=""; ACCEPTED=0; new_state=""
# check KEY SERIES SUBPATH MARKER BIND...: run the guard and record the result.
# A refused source keeps its last accepted counts in the state file.
check() {
  k=$1; ser=$2; sub=$3; shift 3
  guard "$k" "$ser" "$SRC/$sub" "$@"
  if [ -z "$g_why" ]; then
    FILTERS="$FILTERS+ /$sub/**$NL"; ACCEPTED=$((ACCEPTED + 1))
    new_state="$new_state${k}_files=$g_files$NL${k}_bytes=$g_bytes$NL"
  else
    FILTERS="$FILTERS- /$sub/**$NL"; REFUSED="$REFUSED${REFUSED:+,}$ser:$g_why"
    new_state="$new_state${k}_files=$lf$NL${k}_bytes=$lb$NL"
    echo "photos: REFUSED $ser ($g_why): left out tonight, its offsite copy is not touched"
  fi
}
check immich immich immich library/.immich \
  "$SRC/immich/library" "$SRC/immich/upload" "$SRC/immich/profile"
check insta360 insta360-flat insta360-flat dir "$SRC/insta360-flat"
GUARD=${REFUSED:+refused:$REFUSED}; GUARD=${GUARD:-ok}

if [ "$DRY" != 1 ]; then
  # Persist the accepted counts now: the guard compares against the last
  # ACCEPTED run, whatever the upload does.
  printf '%s' "$new_state" > "$STATE.tmp" && mv "$STATE.tmp" "$STATE"
fi

# Filter rules -> positional "--filter" "<rule>" pairs (set -f: the ** must
# reach rclone literally), then the final catch-all exclude. First rule: never
# ship the insta360-flat worker's in-progress `.<name>.jpg.part` files (it writes
# them in the trip folder, then renames them into place; 02:35 run, normally
# done long before this stage, but a big trip can overlap).
set -f; set -- --filter "- .*.part"; IFS=$NL
for f in $FILTERS; do set -- "$@" --filter "$f"; done
unset IFS; set +f
set -- "$@" --filter "- **"

for remote in $REMOTES; do
  dest=$(dest_of "$remote") || { echo "photos: unknown remote $remote, skip"; continue; }
  sf=$DATA/.photos-status-$remote
  last_ok=0; seed_start=0
  # The status line is plain shell assignments; read the carried fields in a subshell.
  [ -f "$sf" ] && eval "$( . "$sf" 2>/dev/null; echo "last_ok=${last_ok:-0} seed_start=${seed_start:-0}")"
  now=$(date +%s); [ "${seed_start:-0}" -gt 0 ] || seed_start=$now
  pass=0; rc=90; t0=$now
  if [ "$ACCEPTED" -eq 0 ]; then
    echo "photos: every source refused, no rclone run for $remote"
  else
    # shellcheck disable=SC2046  # flags_of is a word list on purpose
    while :; do
      pass=$((pass + 1)); tp=$(date +%s)
      rclone "$MODE" "$SRC" "$dest" --config "$CONF" "$@" \
        --size-only --transfers 2 --checkers 4 --retries 3 --low-level-retries 10 \
        --max-delete "$MAX_DELETE" --max-duration 25m \
        --log-level NOTICE --stats 5m --stats-one-line --stats-log-level NOTICE \
        $(flags_of "$remote") $([ "$DRY" = 1 ] && echo --dry-run)
      rc=$?
      echo "photos pass $pass: remote=$remote rclone exit=$rc secs=$(( $(date +%s) - tp ))"
      # 0 = in sync; 2 = usage; 7 = fatal (incl. --max-delete exceeded): no retry.
      case $rc in 0|2|7) break ;; esac
      [ "$pass" -ge "$MAX_PASSES" ] && break
    done
  fi
  end=$(date +%s)
  [ "$rc" = 0 ] && [ "$GUARD" = ok ] && last_ok=$end
  echo "photos rclone exit=$rc remote=$remote passes=$pass secs=$(( end - t0 )) guard=$GUARD mode=$MODE"
  if [ "$DRY" = 1 ]; then
    echo "photos: dry run, $sf not written"
  else
    echo "rc=$rc ts=$end date=$(date -Iseconds) passes=$pass guard=$GUARD last_ok=$last_ok seed_start=$seed_start" > "$sf.tmp" \
      && mv "$sf.tmp" "$sf"
  fi
done
exit 0
