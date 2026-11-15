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
set -u
KEEP=3
DATE=$(date +%F)
OUT=/data/offsite

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
# CAD project folders. Only regenerable noise is dropped here.
# An empty /src/3d-prints means the NFS mount is down (Docker bind-mounts the
# bare mountpoint dir): skip, so a dead mount never rotates good archives out;
# backup-verify then fails on the missing archive.
CADEX=/tmp/pack-excludes-3d-prints
cat > "$CADEX" <<'EOF'
*.log
__pycache__
.~lock*
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
exit 0
