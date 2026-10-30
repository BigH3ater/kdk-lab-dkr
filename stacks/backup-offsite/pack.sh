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
# of truth). Keeps the newest KEEP archives per host locally.
set -u
KEEP=3
DATE=$(date +%F)
OUT=/data/offsite

apk add --no-cache tar zstd >/dev/null 2>&1 || { echo "pack: apk add tar zstd failed"; exit 0; }
mkdir -p "$OUT"

# Dropped from every host archive, read by `tar -X` (patterns are matched by
# tar itself, so NOT shell-globbed here). backup/ = rclone.conf (the Proton
# creds themselves -- must never go offsite); stacks/ = Komodo clones (already
# on GitHub); pgdump/ already ships as its own single files; the rest is cache,
# logs, artwork and other regenerable churn (mirrors offsite.sh's old exclude
# set). GNU tar wildcards cross '/', so */metadata drops that dir at any depth;
# a pattern that matches nothing is ignored, so one list covers every host.
EXFILE=/tmp/pack-excludes
cat > "$EXFILE" <<'EOF'
*/backup
*/stacks
*/pgdump
*/lost+found
*/node_modules
*cache*
*/Definitions
*/MediaCover
*/metadata
*/transcodes
*/logs
*.log
*/Tdarr/DB2/JobReports
*/tandoor/staticfiles
*/audiobookshelf/podcasts
*/proton-bridge/*/updates
EOF

for host in kdk-dkr-01 kdk-dkr-dmz-01 kdk-dkr-02 kdk-dkr-03 kdk-mon-01; do
  [ -d "/data/$host" ] || { echo "pack: $host missing, skip"; continue; }
  tmp="$OUT/.$host-$DATE.tar.zst.partial"
  final="$OUT/$host-$DATE.tar.zst"
  echo "pack: $host -> $host-$DATE.tar.zst"
  # --warning=no-file-ignored silences the "socket ignored" notices (bridge
  # .gnupg sockets); --ignore-failed-read keeps going if a live file vanishes.
  # zstd -T0 uses all cores; level 3 keeps memory tiny (fits mem_limit).
  tar --use-compress-program="zstd -T0 -3 -q" -X "$EXFILE" \
      --warning=no-file-ignored --ignore-failed-read \
      -cf "$tmp" -C /data "$host"
  rc=$?
  # tar rc=1 = a file changed/vanished mid-read (live state) -- archive is still
  # good, keep it. Any other non-zero is a real failure; drop the partial.
  if [ "$rc" = "0" ] || [ "$rc" = "1" ]; then
    mv "$tmp" "$final"
  else
    echo "pack: $host tar failed rc=$rc"
    rm "$tmp" 2>/dev/null || true
    continue
  fi
  # Keep the newest KEEP archives for this host; delete older ones.
  ls -t "$OUT/$host-"*.tar.zst 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
    rm "$old" 2>/dev/null || true
  done
done

echo "pack: done ($(ls -1 "$OUT"/*.tar.zst 2>/dev/null | wc -l) archives in $OUT)"
exit 0
