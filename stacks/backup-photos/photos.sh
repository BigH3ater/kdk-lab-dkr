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
# Remotes run ONE AFTER THE OTHER in PHOTOS_REMOTES order (compose: "proton b2"),
# each in its own subshell with its own passes, caps and status file: one
# remote's exit code, crash or missing secret never stops the next, and Proton
# runs first exactly as before. The guard runs ONCE, before any remote, so a
# refused source is left out for every remote.
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
# B2 (Backblaze, the second offsite copy; docs/runbooks/photo-offsite.md#b2):
# no rclone.conf at all (--config ""), so a B2 run never reads or rewrites the
# Proton session file. b2_setup reads the host-managed /b2/b2.env (directory bind
# of /etc/kdk/backup-photos-b2 on dkr-04, root 600, written from 1Password by the
# owner; read with sed, never sourced) and defines two remotes as RCLONE_CONFIG_*
# environment, exported in the b2 subshell only:
#   b2photos      b2, a key restricted to the one bucket WITHOUT deleteFiles,
#                 hard_delete off: a sync delete only HIDES a file; the bucket's
#                 lifecycle rule keeps hidden/overwritten versions 90 days.
#   b2photoscrypt crypt over the bucket root, its OWN password + salt (not the
#                 Proton key), file and directory names encrypted. The two are
#                 plain in b2.env and 1Password; obscured here through stdin.
# No b2.env, or an empty field = b2 skipped with rc=92 (verify FAILs "not
# configured"); Proton has already run and is not touched.
# B2 also gets the Immich DB dumps: $DBSRC/immich-*.sql.gz -> b2photoscrypt:immich-db/
# (one pass, 15-min cap, before the photos; same mode, --max-delete 5; refused
# when the folder holds no immich dump). Proton already has them through the host
# pgdumps (backup-offsite's `+ /*/pgdump/**` filter).
# B2 flags: rclone's default comparison (size + modtime: B2 stores the source
# modtime in the file info that the listing returns and crypt passes it through,
# so a same-size change is still caught at no extra API cost), --fast-list,
# --transfers 4 --checkers 8, 48M parts x 2 per large file (part buffers stay
# under 4 x 2 x 48M), 60-min passes with --cutoff-mode soft (in-flight uploads
# finish, no half large files), PHOTOS_B2_MAX_PASSES (2) a night.
#
# Writes, per remote, /data/.photos-status-<remote> (read by backup-verify):
#   rc=N ts=EPOCH date=ISO passes=N guard=ok|refused:<why>,... last_ok=EPOCH|0 seed_start=EPOCH crashes=N
# and for b2 also: db_rc=N db_guard=ok|no-dir|no-dumps|not-configured db_last_ok=EPOCH|0
# rc = the last pass's rclone exit, except 90 = no rclone run (every source
# refused), 91 = the last pass crashed (Go panic, raw exit 2; see rclone_pass)
# and 92 = the remote is not configured (b2: no b2.env or an empty field).
# last_ok = time of the last run that synced every source with rc=0 (carried
# forward); seed_start = time of the first ever run (carried forward);
# crashes = passes tonight that crashed (each one was retried like an exit 10).
# Guard state (last accepted file/byte counts): /data/.photos-state.
# Always exits 0 so verify runs and stays the single Pushover source.
#
# Log lines for the "Nightly backups" dashboard (Alloy -> Loki
# {stack="backup-photos", service="photos"}), logfmt like pack.sh's:
#   backup-manifest date=D kind=photos host=kdk-nas-01 series=immich|insta360-flat name=photos/<series> bytes=N prev_bytes=N|none pct=P|none files=N prev_files=N|none guard=ok|<why>
#   photos pass N: remote=R rclone exit=N secs=S[ raw_exit=2 crash=panic]
#   photos rclone exit=N remote=R passes=N secs=S guard=... mode=sync|copy crashes=N
#   photos db: remote=b2 rclone exit=N files=N secs=S guard=ok|<why>
#   photos: b2 not configured (<why>), skipped ...
#
# Env (all optional): PHOTOS_MODE (sync; copy = never delete on the remote),
# PHOTOS_REMOTES ("proton"; compose.yaml sets "proton b2"), PHOTOS_MAX_PASSES
# (4, Proton), PHOTOS_B2_MAX_PASSES (2), PHOTOS_MAX_DELETE (100), PHOTOS_MIN_PCT
# (50), PHOTOS_ALLOW_SHRINK (0), PHOTOS_DRY_RUN (0: 1 = rclone --dry-run,
# writes no status/state).
# Test hooks (tests/guard-test.sh): PHOTOS_SRC, PHOTOS_DATA, PHOTOS_MOUNTINFO,
# PHOTOS_RCLONE_CONFIG, PHOTOS_DEST_<remote>, PHOTOS_DBDEST_<remote>,
# PHOTOS_DB_SRC, PHOTOS_B2_ENV, PHOTOS_B2_UNDERLAY (what the crypt wraps, instead
# of b2photos:<bucket>).
set -u
MODE=${PHOTOS_MODE:-sync}
case $MODE in sync|copy) ;; *) echo "photos: PHOTOS_MODE=$MODE is not sync|copy, using sync"; MODE=sync ;; esac
SRC=${PHOTOS_SRC:-/src}
DATA=${PHOTOS_DATA:-/data}
MOUNTINFO=${PHOTOS_MOUNTINFO:-/proc/self/mountinfo}
CONF=${PHOTOS_RCLONE_CONFIG:-/config/rclone.conf}
REMOTES=${PHOTOS_REMOTES:-proton}
MAX_PASSES=${PHOTOS_MAX_PASSES:-4}
B2_MAX_PASSES=${PHOTOS_B2_MAX_PASSES:-2}
B2_ENV=${PHOTOS_B2_ENV:-/b2/b2.env}
MAX_DELETE=${PHOTOS_MAX_DELETE:-100}
MIN_PCT=${PHOTOS_MIN_PCT:-50}
ALLOW_SHRINK=${PHOTOS_ALLOW_SHRINK:-0}
DRY=${PHOTOS_DRY_RUN:-0}
DATE=$(date +%F)
STATE=$DATA/.photos-state
DBSRC=${PHOTOS_DB_SRC:-$DATA/kdk-dkr-dmz-01/pgdump}
export RCLONE_CACHE_DIR=/tmp/rclone-cache
TMPD=$(mktemp -d 2>/dev/null) || { TMPD=$DATA/.photos-tmp; mkdir -p "$TMPD"; }
trap 'rm -rf "$TMPD"' EXIT

# dest_of REMOTE: the rclone destination root for that remote.
dest_of() {
  eval "o=\${PHOTOS_DEST_$1:-}"
  [ -n "$o" ] && { echo "$o"; return 0; }
  case $1 in
    proton) echo "proton-crypt:photos" ;;
    b2) echo "b2photoscrypt:photos" ;;
    *) return 1 ;;
  esac
}
# dbdest_of REMOTE: where the Immich DB dumps go (empty = this remote gets none).
dbdest_of() {
  eval "o=\${PHOTOS_DBDEST_$1:-}"
  [ -n "$o" ] && { echo "$o"; return 0; }
  case $1 in
    b2) echo "b2photoscrypt:immich-db" ;;
    *) echo "" ;;
  esac
}
# conf_of REMOTE: the --config value (b2: none; its remotes are environment).
conf_of() {
  case $1 in
    b2) echo "" ;;
    *) echo "$CONF" ;;
  esac
}
# flags_of REMOTE: comparison, concurrency, retries and backend flags.
# Proton: exactly the flags of the Proton-only version (see PROTON above).
flags_of() {
  case $1 in
    proton) echo "--size-only --transfers 2 --checkers 4 --retries 3 --low-level-retries 10 --protondrive-replace-existing-draft=true --tpslimit 10" ;;
    b2) echo "--fast-list --transfers 4 --checkers 8 --retries 3 --low-level-retries 10 --cutoff-mode soft --b2-chunk-size 48M --b2-upload-concurrency 2 --b2-hard-delete=false" ;;
    *) echo "--retries 3 --low-level-retries 10" ;;
  esac
}
# cap_of REMOTE / passes_of REMOTE: --max-duration per pass, passes per night.
cap_of() { case $1 in b2) echo 60m ;; *) echo 25m ;; esac; }
passes_of() { case $1 in b2) echo "$B2_MAX_PASSES" ;; *) echo "$MAX_PASSES" ;; esac; }

# b2_setup: define the b2photos / b2photoscrypt remotes from $B2_ENV (KEY=value
# lines, values unquoted; read with sed, the file is never sourced). On a
# missing file/field sets b2_why and returns 1. Call only in the b2 subshell
# (not inside $(...): the exports must stay in that subshell).
b2_val() { sed -n "s/^$1=//p" "$B2_ENV" 2>/dev/null | tail -n 1 | tr -d '\r'; }
b2_setup() {
  b2_why=""
  [ -r "$B2_ENV" ] || { b2_why=no-env-file; return 1; }
  for v in ACCOUNT KEY BUCKET CRYPT_PASSWORD CRYPT_SALT; do
    [ -n "$(b2_val "PHOTOS_B2_$v")" ] || { b2_why=empty-PHOTOS_B2_$v; return 1; }
  done
  o1=$(b2_val PHOTOS_B2_CRYPT_PASSWORD | rclone obscure - 2>/dev/null)
  o2=$(b2_val PHOTOS_B2_CRYPT_SALT | rclone obscure - 2>/dev/null)
  [ -n "$o1" ] && [ -n "$o2" ] || { b2_why=obscure-failed; return 1; }
  RCLONE_CONFIG_B2PHOTOS_TYPE=b2
  RCLONE_CONFIG_B2PHOTOS_ACCOUNT=$(b2_val PHOTOS_B2_ACCOUNT)
  RCLONE_CONFIG_B2PHOTOS_KEY=$(b2_val PHOTOS_B2_KEY)
  RCLONE_CONFIG_B2PHOTOS_HARD_DELETE=false
  RCLONE_CONFIG_B2PHOTOSCRYPT_TYPE=crypt
  RCLONE_CONFIG_B2PHOTOSCRYPT_REMOTE=${PHOTOS_B2_UNDERLAY:-b2photos:$(b2_val PHOTOS_B2_BUCKET)}
  RCLONE_CONFIG_B2PHOTOSCRYPT_FILENAME_ENCRYPTION=standard
  RCLONE_CONFIG_B2PHOTOSCRYPT_FILENAME_ENCODING=base32
  RCLONE_CONFIG_B2PHOTOSCRYPT_DIRECTORY_NAME_ENCRYPTION=true
  RCLONE_CONFIG_B2PHOTOSCRYPT_PASSWORD=$o1
  RCLONE_CONFIG_B2PHOTOSCRYPT_PASSWORD2=$o2
  export RCLONE_CONFIG_B2PHOTOS_TYPE RCLONE_CONFIG_B2PHOTOS_ACCOUNT RCLONE_CONFIG_B2PHOTOS_KEY \
    RCLONE_CONFIG_B2PHOTOS_HARD_DELETE RCLONE_CONFIG_B2PHOTOSCRYPT_TYPE RCLONE_CONFIG_B2PHOTOSCRYPT_REMOTE \
    RCLONE_CONFIG_B2PHOTOSCRYPT_FILENAME_ENCRYPTION RCLONE_CONFIG_B2PHOTOSCRYPT_FILENAME_ENCODING \
    RCLONE_CONFIG_B2PHOTOSCRYPT_DIRECTORY_NAME_ENCRYPTION RCLONE_CONFIG_B2PHOTOSCRYPT_PASSWORD \
    RCLONE_CONFIG_B2PHOTOSCRYPT_PASSWORD2
  unset o1 o2
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

# rclone_pass ARGS...: one rclone run. Its output streams straight through awk
# to the container log (line by line, nothing held back); awk only notes a Go
# crash in $TMPD/panic. Sets rc and extra.
rclone_pass() {
  rm -f "$TMPD/panic" "$TMPD/rc"
  { rclone "$@" 2>&1
    echo $? > "$TMPD/rc"; } |
    awk -v f="$TMPD/panic" '{ print; fflush() } /^(panic|fatal error): / { print "1" > f; close(f) }'
  rc=$(cat "$TMPD/rc" 2>/dev/null); rc=${rc:-1}; extra=""
  # A Go crash (panic / runtime fatal error) also exits 2, rclone's usage-error
  # code. Seen 2026-11-19: go-proton-api panicked when --max-duration cancelled
  # a folder create. It is an interrupted pass (nothing deleted: deletes run
  # after the transfers and only after a clean pass), so record it as 91 and
  # let the next pass resume, like exit 10.
  if [ "$rc" = 2 ] && [ -f "$TMPD/panic" ]; then
    rc=91; extra=" raw_exit=2 crash=panic"
  fi
}

# sync_db REMOTE: the Immich DB dumps, one pass capped at 15 min, before the
# photos (b2 only: dbdest_of). Sets db_rc and db_guard.
sync_db() {
  db_rc=0; db_guard=ok; dd=$(dbdest_of "$1")
  n=$(find "$DBSRC" -maxdepth 1 -type f -name 'immich-*.sql.gz' 2>/dev/null | wc -l | tr -d ' ')
  if [ ! -d "$DBSRC" ]; then db_guard=no-dir
  elif [ "$n" -eq 0 ]; then db_guard=no-dumps; fi
  if [ "$db_guard" != ok ]; then
    db_rc=90
    echo "photos: REFUSED immich-db ($db_guard): no DB sync tonight, its offsite copy is not touched"
    echo "photos db: remote=$1 rclone exit=90 files=$n secs=0 guard=$db_guard"
    return 0
  fi
  td=$(date +%s)
  # shellcheck disable=SC2046  # flags_of is a word list on purpose
  rclone_pass "$MODE" "$DBSRC" "$dd" --config "$(conf_of "$1")" \
    --filter "+ /immich-*.sql.gz" --filter "- **" --max-delete 5 --max-duration 15m \
    --log-level NOTICE --stats-one-line --stats-log-level NOTICE \
    $(flags_of "$1") $([ "$DRY" = 1 ] && echo --dry-run)
  db_rc=$rc
  echo "photos db: remote=$1 rclone exit=$db_rc files=$n secs=$(( $(date +%s) - td )) guard=$db_guard$extra"
}

# sync_remote REMOTE FILTER-ARGS...: every pass for one remote, then its status
# file. Called in a subshell per remote: b2's exported RCLONE_CONFIG_* never
# reach another remote's rclone, and nothing one remote does stops the next.
sync_remote() {
  remote=$1; shift
  dest=$(dest_of "$remote") || { echo "photos: unknown remote $remote, skip"; return 0; }
  sf=$DATA/.photos-status-$remote
  last_ok=0; seed_start=0; db_last_ok=0
  # The status line is plain shell assignments; read the carried fields in a subshell.
  [ -f "$sf" ] && eval "$( . "$sf" 2>/dev/null; echo "last_ok=${last_ok:-0} seed_start=${seed_start:-0} db_last_ok=${db_last_ok:-0}")"
  now=$(date +%s); [ "${seed_start:-0}" -gt 0 ] || seed_start=$now
  pass=0; rc=90; t0=$now; crashes=0; dbf=""; maxp=$(passes_of "$remote")
  if [ "$remote" = b2 ] && ! b2_setup; then
    rc=92
    echo "photos: b2 not configured ($b2_why), skipped: owner hand steps in docs/runbooks/photo-offsite.md#b2 (Proton is not affected)"
    dbf=" db_rc=92 db_guard=not-configured db_last_ok=$db_last_ok"
  else
    if [ -n "$(dbdest_of "$remote")" ]; then
      sync_db "$remote"
      [ "$db_rc" = 0 ] && db_last_ok=$(date +%s)
      dbf=" db_rc=$db_rc db_guard=$db_guard db_last_ok=$db_last_ok"
    fi
    if [ "$ACCEPTED" -eq 0 ]; then
      echo "photos: every source refused, no rclone run for $remote"
    else
      while :; do
        pass=$((pass + 1)); tp=$(date +%s)
        # shellcheck disable=SC2046  # flags_of is a word list on purpose
        rclone_pass "$MODE" "$SRC" "$dest" --config "$(conf_of "$remote")" "$@" \
          --max-delete "$MAX_DELETE" --max-duration "$(cap_of "$remote")" \
          --log-level NOTICE --stats 5m --stats-one-line --stats-log-level NOTICE \
          $(flags_of "$remote") $([ "$DRY" = 1 ] && echo --dry-run)
        [ "$rc" = 91 ] && crashes=$((crashes + 1))
        echo "photos pass $pass: remote=$remote rclone exit=$rc secs=$(( $(date +%s) - tp ))$extra"
        # 0 = in sync; 2 = usage; 7 = fatal (incl. --max-delete exceeded): no retry.
        # 91 (crash) and everything else: the next pass resumes.
        case $rc in 0|2|7) break ;; esac
        [ "$pass" -ge "$maxp" ] && break
      done
    fi
  fi
  end=$(date +%s)
  [ "$rc" = 0 ] && [ "$GUARD" = ok ] && last_ok=$end
  echo "photos rclone exit=$rc remote=$remote passes=$pass secs=$(( end - t0 )) guard=$GUARD mode=$MODE crashes=$crashes"
  if [ "$DRY" = 1 ]; then
    echo "photos: dry run, $sf not written"
  else
    echo "rc=$rc ts=$end date=$(date -Iseconds) passes=$pass guard=$GUARD last_ok=$last_ok seed_start=$seed_start crashes=$crashes$dbf" > "$sf.tmp" \
      && mv "$sf.tmp" "$sf"
  fi
}

for remote in $REMOTES; do
  ( sync_remote "$remote" "$@" )
done
exit 0
