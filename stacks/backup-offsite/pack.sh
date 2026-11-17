#!/bin/sh
# Pack step for backup-offsite, runs BEFORE the rclone passes. Writes ONE
# tar.zst per host per night into /data/offsite/<host>-YYYY-MM-DD.tar.zst, built
# from /data/<host>/ with the regenerable + must-never-leave paths dropped.
#
# Why: the offsite rclone sync never finished a night because it pushed the raw
# tree -- thousands of churny small files (prowlarr Definitions/*.yml, bazarr
# logs, caches). Proton Drive's API chokes on small-file VOLUME, not bytes
# (502 on block uploads, 422 "already exists", 400 "empty", "no response" on
# listings), so passes alternated exit 10 (time) / exit 2 (errors). Shipping a
# handful of large archives instead cuts the object count by ~3 orders of
# magnitude. GNU tar stores symlinks as symlinks and skips sockets (so the
# proton-bridge .gnupg sockets no longer error), handled by the flags below.
#
# zstd + GNU tar are apk-added at runtime -- same pattern as backup-verify's
# curl; alpine is already pinned + in the advisory-watch catalog, so no new
# image. Best-effort: always exits 0 (offsite.sh + backup-verify are the source
# of truth). Keeps the newest KEEP archives per host locally. Also packs the
# CAD library (/src/3d-prints, read-only) as offsite/3d-prints-YYYY-MM-DD.tar.zst.
#
# After packing it REPORTS what the night produced, as logfmt lines on stdout
# (Alloy ships them to Loki as {stack="backup-offsite", service="pack"}; the
# Grafana "Nightly backups" dashboard and the BackupFileShrank rule read them):
#   backup-manifest date=D kind=archive|pgdump host=H series=S name=F bytes=N prev_bytes=N|none pct=P|none
#     one per archive in /data/offsite/ and per /data/<host>/pgdump/ dump dated D;
#     prev = the same series' previous file (yesterday's archive, the previous
#     dump of that DB); pct = bytes*100/prev_bytes.
#   backup-change date=D op=added|changed|removed path="..."      (at most 300)
#   backup-change-summary date=D added=N changed=N removed=N truncated=true|false baseline=false files=N
#     the file list (path, size, mtime) of the newest 3d-prints archive against the
#     previous one. With no previous archive: ONE summary line with baseline=true.
# Reporting is best-effort (a subshell, errors ignored) and never fails pack.
#
# Usage: pack.sh                          pack, rotate, copy the marker, report (nightly)
#        pack.sh --report-only [DATE]     ONLY report, from the files already on disk:
#            no pack, no rotation, no marker copy, nothing in /data is written.
#            DATE defaults to the newest archive date in /data/offsite. Then sleeps
#            REPORT_HOLD seconds (default 90): Alloy's discovery.docker refreshes
#            every 60 s and only sees RUNNING containers, so a container that exits
#            sooner is never shipped (the dkr-01 one-shot gap).
set -u
KEEP=3
DATE=$(date +%F)
OUT=/data/offsite
CHANGE_MAX=300
MODE=pack
# --report-only is looked for in EVERY argument (and a YYYY-MM-DD anywhere is the
# date), so `compose run pack /pack.sh --report-only` without --entrypoint (which
# runs `/bin/sh /pack.sh /pack.sh --report-only`) still only reports, never packs.
RDATE=""
for a in "$@"; do
  case "$a" in
    --report-only) MODE=report ;;
    [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) RDATE=$a ;;
  esac
done
if [ "$MODE" = report ]; then
  DATE=$RDATE
  if [ -z "$DATE" ]; then
    for f in "$OUT"/*-[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].tar.zst; do
      [ -f "$f" ] || continue
      b=${f%.tar.zst}; d=${b#"${b%??????????}"}   # the last 10 chars: YYYY-MM-DD
      awk -v a="$d" -v c="$DATE" 'BEGIN { exit !(a > c) }' && DATE=$d
    done
  fi
fi

# prev_of DIR SERIES DATE EXT: basename of SERIES' newest file in DIR dated
# before DATE (SERIES-YYYY-MM-DD.EXT), or empty. The glob sorts by name, and
# ISO dates sort by name, so the last match before DATE wins.
prev_of() {
  cur="$2-$3$4"; best=""
  for p in "$1/$2"-[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]"$4"; do
    [ -f "$p" ] || continue
    b=${p##*/}
    awk -v a="$b" -v c="$cur" 'BEGIN { exit !(a < c) }' && best=$b
  done
  printf '%s' "$best"
}

# manifest KIND HOST SERIES DIR FILE PREV: one backup-manifest line.
manifest() {
  bytes=$(stat -c %s "$4/$5" 2>/dev/null) || return 0
  pb=none; pct=none
  if [ -n "$6" ] && pb=$(stat -c %s "$4/$6" 2>/dev/null); then
    pct=$(awk -v b="$bytes" -v p="$pb" 'BEGIN { if (p > 0) printf "%.1f", b * 100 / p; else print "none" }')
  else
    pb=none
  fi
  echo "backup-manifest date=$DATE kind=$1 host=$2 series=$3 name=$5 bytes=$bytes prev_bytes=$pb pct=$pct"
}

# cad_list ARCHIVE: "path<TAB>size<TAB>mtime" for every regular file in a
# 3d-prints archive, path without the leading 3d-prints/. GNU tar's default
# quoting (escape) turns tabs/newlines in names into \t / \n, so one line per file.
cad_list() {
  tar -I zstd --full-time -tvf "$1" 2>/dev/null | awk '
    /^-/ {
      if (!match($0, /^[^ ]+ +[^ ]+ +[0-9]+ +[0-9-]+ +[0-9:]+ /)) next
      split(substr($0, 1, RLENGTH), f, / +/)
      p = substr($0, RLENGTH + 1); sub(/^3d-prints\//, "", p)
      printf "%s\t%s\t%s %s\n", p, f[3], f[4], f[5]
    }'
}

# cad_changes: added/changed/removed between the newest two 3d-prints archives.
cad_changes() {
  new="3d-prints-$DATE.tar.zst"
  [ -f "$OUT/$new" ] || return 0
  if ! tar --version 2>/dev/null | grep -q 'GNU tar' || ! command -v zstd >/dev/null 2>&1; then
    echo "pack: report: GNU tar/zstd missing, CAD change list skipped"; return 0
  fi
  tmpd=$(mktemp -d) || return 0
  cad_list "$OUT/$new" > "$tmpd/new"
  prev=$(prev_of "$OUT" 3d-prints "$DATE" .tar.zst)
  if [ -z "$prev" ]; then
    echo "backup-change-summary date=$DATE added=0 changed=0 removed=0 truncated=false baseline=true files=$(wc -l < "$tmpd/new" | tr -d ' ')"
    rm -rf "$tmpd"; return 0
  fi
  cad_list "$OUT/$prev" > "$tmpd/old"
  # Paths are always quoted, with \ and " escaped, so the line stays logfmt.
  awk -F '\t' -v date="$DATE" -v max="$CHANGE_MAX" '
    function q(s) { gsub(/\\/, "\\\\", s); gsub(/"/, "\\\"", s); return "\"" s "\"" }
    function out(op, p) {
      cnt[op]++
      if (shown < max) { printf "backup-change date=%s op=%s path=%s\n", date, op, q(p); shown++ }
      else trunc = "true"
    }
    FILENAME == ARGV[1] { n++; np[n] = $1; nv[$1] = $2 FS $3; next }
    { m++; op_[m] = $1; ov[$1] = $2 FS $3 }
    END {
      trunc = "false"
      for (i = 1; i <= n; i++) { p = np[i]; if (!(p in ov)) out("added", p); else if (ov[p] != nv[p]) out("changed", p) }
      for (i = 1; i <= m; i++) { p = op_[i]; if (!(p in nv)) out("removed", p) }
      printf "backup-change-summary date=%s added=%d changed=%d removed=%d truncated=%s baseline=false files=%d\n",
        date, cnt["added"], cnt["changed"], cnt["removed"], trunc, n
    }' "$tmpd/new" "$tmpd/old"
  rm -rf "$tmpd"
}

# report: the manifest for DATE (archives, then every host's pg dumps), then the
# CAD change list. Reads only.
report() {
  for f in "$OUT"/*-"$DATE".tar.zst; do
    [ -f "$f" ] || continue
    b=${f##*/}; s=${b%-"$DATE".tar.zst}
    manifest archive "$s" "$s" "$OUT" "$b" "$(prev_of "$OUT" "$s" "$DATE" .tar.zst)"
  done
  for d in /data/*/pgdump; do
    [ -d "$d" ] || continue
    h=${d%/pgdump}; h=${h##*/}
    for f in "$d"/*-"$DATE".sql.gz; do
      [ -f "$f" ] || continue
      b=${f##*/}; s=${b%-"$DATE".sql.gz}
      manifest pgdump "$h" "$s" "$d" "$b" "$(prev_of "$d" "$s" "$DATE" .sql.gz)"
    done
  done
  cad_changes
}

if [ "$MODE" = report ]; then
  # tar + zstd are only needed for the CAD change list; the manifest works without.
  apk add --no-cache tar zstd >/dev/null 2>&1 || echo "pack: apk add tar zstd failed"
  if [ -z "$DATE" ]; then
    echo "pack: report-only: no dated archives in $OUT, nothing to report"
  else
    echo "pack: report-only for $DATE (no pack, no rotation, nothing written)"
    ( report ) || echo "pack: report failed (ignored)"
  fi
  sleep "${REPORT_HOLD:-90}"
  exit 0
fi

apk add --no-cache tar zstd >/dev/null 2>&1 || { echo "pack: apk add tar zstd failed"; exit 0; }
mkdir -p "$OUT"

# Dropped from every host archive, read by `tar -X` (patterns are matched by
# tar itself, so NOT shell-globbed here). GNU tar wildcards cross '/', so a
# pattern like */cache drops a dir named `cache` at any depth; a pattern that
# matches nothing is ignored, so one list covers every host. Patterns are kept
# SPECIFIC on purpose: an earlier `*cache*` / `*/metadata` would have eaten real
# state -- homebridge `cachedAccessories` + `IdentifierCache.json` (HomeKit
# re-pair), HA `unifiprotect_cache`, arr `cache.db`, and Audiobookshelf's own
# `metadata/` (which holds ABS backups + covers). So:
#   backup/  = rclone.conf (the Proton creds themselves -- must never go offsite)
#   stacks/  = Komodo repo clones (already on GitHub)
#   pgdump/  = already ships as its own single .sql.gz files
#   caches   = only dirs literally named cache/.cache/Cache/__pycache__
#   jellyfin config/data/{metadata,transcodes} = re-fetchable artwork (6.5 G);
#              the real Jellyfin DB (config/data/data) is kept
#   the rest = logs, art, node_modules and other regenerable churn
EXFILE=/tmp/pack-excludes
cat > "$EXFILE" <<'EOF'
*/backup
*/stacks
*/pgdump
*/lost+found
*/node_modules
*/cache
*/Cache
*/.cache
*/__pycache__
*/Definitions
*/MediaCover
*/jellyfin/config/data/metadata
*/jellyfin/config/data/transcodes
*/logs
*.log
*/Tdarr/DB2/JobReports
*/tandoor/staticfiles
*/audiobookshelf/podcasts
*/proton-bridge/*/updates
EOF

# pack_one NAME PARENT DIR EXCLUDES: tar.zst PARENT/DIR into
# $OUT/NAME-$DATE.tar.zst (archive paths start with DIR/), then keep the newest
# KEEP archives for NAME.
pack_one() {
  name=$1; parent=$2; dir=$3; excl=$4
  tmp="$OUT/.$name-$DATE.tar.zst.partial"
  final="$OUT/$name-$DATE.tar.zst"
  echo "pack: $name -> $name-$DATE.tar.zst"
  # --warning=no-file-ignored silences the "socket ignored" notices (bridge
  # .gnupg sockets); --ignore-failed-read keeps going if a live file vanishes.
  # zstd -T0 uses all cores; level 3 keeps memory tiny (fits mem_limit).
  tar --use-compress-program="zstd -T0 -3 -q" -X "$excl" \
      --warning=no-file-ignored --ignore-failed-read \
      -cf "$tmp" -C "$parent" "$dir"
  rc=$?
  # tar rc=1 = a file changed/vanished mid-read (live state) -- archive is still
  # good, keep it. Any other non-zero is a real failure; drop the partial.
  if [ "$rc" = "0" ] || [ "$rc" = "1" ]; then
    mv "$tmp" "$final"
  else
    echo "pack: $name tar failed rc=$rc"
    rm "$tmp" 2>/dev/null || true
    return 0
  fi
  # Keep the newest KEEP archives for this name; delete older ones.
  ls -t "$OUT/$name-"*.tar.zst 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
    rm "$old" 2>/dev/null || true
  done
}

# Only these explicit host dirs are packed (each via `tar -C /data <host>`), so
# the archive never recurses /data/offsite (our own output) or /data/_archive
# (rclone's backup-dir) -- both are siblings of the host dirs, not under one.
for host in kdk-dkr-01 kdk-dkr-dmz-01 kdk-dkr-02 kdk-dkr-03 kdk-mon-01 kdk-dkr-04; do
  [ -d "/data/$host" ] || { echo "pack: $host missing, skip"; continue; }
  pack_one "$host" /data "$host" "$EXFILE"
done

# CAD library: the owner's laptop ~/cad, pushed every 15 min by cad-sync to
# kdk-nas-01 tankz3/3d-prints, mounted read-only on dkr-04 at /mnt/3d-prints
# (docs/runbooks/cad-library.md). Its OWN exclude list: the host list above
# drops dirs named backup/stacks/cache/logs/Definitions..., which could be real
# CAD project folders. Only regenerable noise is dropped here. Lock files come in
# two shapes: LibreOffice's `.~lock.<name>#` (.~lock*) and SolidCraft's
# `.~<name>.sc.lock` (.~*.lock); keep both in step with hosts/debian-mbp/cad-sync.sh.
# An empty /src/3d-prints means the NFS mount is down (Docker bind-mounts the
# bare mountpoint dir): skip, so a dead mount never rotates good archives out;
# backup-verify then fails on the missing archive.
CADEX=/tmp/pack-excludes-3d-prints
cat > "$CADEX" <<'EOF'
*.log
__pycache__
.~lock*
.~*.lock
EOF
if [ -n "$(ls -A /src/3d-prints 2>/dev/null)" ]; then
  pack_one 3d-prints /src 3d-prints "$CADEX"
  # backup-verify runs on kdk-dkr-01 and cannot see /mnt/3d-prints, so hand it
  # the laptop's sync marker (content + mtime) at the backup root. Outside
  # offsite/, so it is never shipped to Proton.
  if [ -f /src/3d-prints/.last-sync ]; then
    cat /src/3d-prints/.last-sync > /data/.3d-prints-last-sync.tmp \
      && touch -r /src/3d-prints/.last-sync /data/.3d-prints-last-sync.tmp \
      && mv /data/.3d-prints-last-sync.tmp /data/.3d-prints-last-sync
  else
    rm -f /data/.3d-prints-last-sync
  fi
else
  echo "pack: /src/3d-prints empty or missing (is /mnt/3d-prints mounted on dkr-04?), skip"
fi

echo "pack: done ($(ls -1 "$OUT"/*.tar.zst 2>/dev/null | wc -l) archives in $OUT)"
( report ) || echo "pack: report failed (ignored)"
exit 0
