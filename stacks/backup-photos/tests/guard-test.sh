#!/bin/sh
# Local simulation of photos.sh's deletion guard, with the real rclone binary
# and a LOCAL directory as the "remote" (nothing touches Proton or any config:
# PHOTOS_RCLONE_CONFIG points at a missing file, the destination is a temp dir).
# Fake /proc/self/mountinfo lines say which binds are "NFS".
#
#   sh stacks/backup-photos/tests/guard-test.sh            (needs rclone on PATH)
#   SH="busybox sh" sh stacks/backup-photos/tests/guard-test.sh   (busybox ash, as in the alpine-based image)
#
# Exits non-zero on the first failed expectation.
set -u
HERE=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
SRC=$T/src; DATA=$T/data; DEST=$T/remote; MI=$T/mountinfo
mkdir -p "$SRC/immich/library/jmack/2026/2026-11-16" "$SRC/immich/upload" \
  "$SRC/immich/profile" "$SRC/insta360-flat/2025-08-maine" "$DATA" "$DEST"
echo immich > "$SRC/immich/library/.immich"
for i in 1 2 3 4 5 6 7 8 9 10; do
  echo "photo $i" > "$SRC/immich/library/jmack/2026/2026-11-16/IMG_$i.jpg"
  echo "trip $i" > "$SRC/insta360-flat/2025-08-maine/I360_$i.jpg"
done
echo marker > "$SRC/immich/upload/.immich"; echo marker > "$SRC/immich/profile/.immich"

# mountinfo: one line per bind, fstype after " - ".
mi() { # mi FSTYPE_IMMICH FSTYPE_INSTA
  : > "$MI"; n=100
  for b in immich/library immich/upload immich/profile; do
    n=$((n + 1)); echo "$n 1 0:50 /x $SRC/$b ro,noatime - $1 172.16.30.2:/mnt/tankz3/appdata/immich ro" >> "$MI"
  done
  echo "200 1 0:51 / $SRC/insta360-flat ro,noatime - $2 172.16.30.2:/mnt/tankz3/insta360-flat ro" >> "$MI"
}
run() {
  PHOTOS_SRC=$SRC PHOTOS_DATA=$DATA PHOTOS_MOUNTINFO=$MI \
  PHOTOS_RCLONE_CONFIG=$T/no-such-rclone.conf PHOTOS_DEST_proton=$DEST \
  PHOTOS_MAX_PASSES=1 "$@" ${SH:-sh} "$HERE/photos.sh" > "$T/out" 2>&1
}
st() { tr ' ' '\n' < "$DATA/.photos-status-proton" | sed -n "s/^$1=//p"; }
cnt() { find "$DEST/$1" -type f 2>/dev/null | wc -l | tr -d ' '; }
fail() { echo "FAIL: $*"; echo "--- photos.sh output:"; cat "$T/out"; exit 1; }
ok() { echo "ok   $*"; }

# 1. normal: everything is NFS and populated -> both trees land, rc=0, guard=ok.
mi nfs4 nfs4; run env
[ "$(st rc)" = 0 ] && [ "$(st guard)" = ok ] || fail "normal: rc=$(st rc) guard=$(st guard)"
[ "$(cnt immich)" = 13 ] && [ "$(cnt insta360-flat)" = 10 ] || fail "normal: dest immich=$(cnt immich) insta=$(cnt insta360-flat)"
[ "$(st last_ok)" -gt 0 ] || fail "normal: last_ok not set"
grep -q 'backup-manifest .*series=immich .*files=13 .*guard=ok' "$T/out" || fail "normal: manifest line"
ok "normal run: 13 immich + 10 insta360 files synced, rc=0, guard=ok, manifest line"
seed=$(st seed_start)

# 1b. insta360-flat worker mid-write: its .<name>.jpg.part is never shipped; the
#     finished file (renamed into place) goes on the next run.
P="$SRC/insta360-flat/2025-08-maine/.I360_11.jpg.part"; echo half > "$P"; run env
[ ! -e "$DEST/insta360-flat/2025-08-maine/.I360_11.jpg.part" ] && [ "$(cnt insta360-flat)" = 10 ] || fail "part: .part file shipped"
mv "$P" "$SRC/insta360-flat/2025-08-maine/I360_11.jpg"; run env
[ -f "$DEST/insta360-flat/2025-08-maine/I360_11.jpg" ] && [ "$(st rc)" = 0 ] || fail "part: renamed file not shipped"
rm "$SRC/insta360-flat/2025-08-maine/I360_11.jpg"; run env
[ "$(cnt insta360-flat)" = 10 ] || fail "part: cleanup"
ok "worker .part file skipped; the renamed still uploads on the next run"

# 2. a real delete propagates (1 of 10, under --max-delete).
rm "$SRC/insta360-flat/2025-08-maine/I360_10.jpg"; run env
[ "$(cnt insta360-flat)" = 9 ] && [ "$(st rc)" = 0 ] || fail "delete: insta=$(cnt insta360-flat) rc=$(st rc)"
[ "$(st seed_start)" = "$seed" ] || fail "seed_start not carried"
ok "single delete propagates, seed_start carried forward"

# 3. missing mount: the Insta360 bind is a bare local dir (ext4) AND empty.
mv "$SRC/insta360-flat" "$T/insta-away"; mkdir "$SRC/insta360-flat"; mi nfs4 ext4; run env
[ "$(cnt insta360-flat)" = 9 ] || fail "missing mount: remote insta360 copy changed ($(cnt insta360-flat))"
case $(st guard) in refused:insta360-flat:not-nfs:insta360-flat:ext4) ;; *) fail "missing mount: guard=$(st guard)";; esac
[ "$(cnt immich)" = 13 ] && [ "$(st rc)" = 0 ] || fail "missing mount: immich side rc=$(st rc)"
grep -q 'series=insta360-flat .*bytes=0 .*guard=not-nfs' "$T/out" || fail "missing mount: manifest shows the shrink"
ok "missing mount: insta360 refused (not-nfs), remote copy untouched, immich still synced"
rmdir "$SRC/insta360-flat"; mv "$T/insta-away" "$SRC/insta360-flat"

# 4. empty source on a live mount: Immich library emptied (marker gone too).
mkdir "$T/lib-away"; mv "$SRC/immich/library/"* "$SRC/immich/library/.immich" "$T/lib-away/"; mi nfs4 nfs4; run env
[ "$(cnt immich)" = 13 ] || fail "empty: remote immich copy changed ($(cnt immich))"
case $(st guard) in refused:immich:no-marker) ;; *) fail "empty: guard=$(st guard)";; esac
ok "empty library (no .immich marker): refused, remote copy untouched"

# 5. marker present but library empty -> 'shrank' (last accepted = 13 files).
echo immich > "$SRC/immich/library/.immich"; run env
case $(st guard) in refused:immich:shrank:3/13) ;; *) fail "shrink: guard=$(st guard)";; esac
[ "$(cnt immich)" = 13 ] || fail "shrink: remote immich copy changed"
ok "library down to 3 of 13 files: refused (shrank), remote copy untouched"

# 6. the owner accepts a real big delete by hand: PHOTOS_ALLOW_SHRINK=1 (+ a high cap).
run env PHOTOS_ALLOW_SHRINK=1 PHOTOS_MAX_DELETE=1000
[ "$(st guard)" = ok ] && [ "$(st rc)" = 0 ] && [ "$(cnt immich)" = 3 ] || fail "allow-shrink: guard=$(st guard) rc=$(st rc) immich=$(cnt immich)"
ok "PHOTOS_ALLOW_SHRINK=1: accepted, the deletes propagate"
mv "$T/lib-away/"* "$SRC/immich/library/"; run env   # back to 13

# 7. --max-delete: 4 deletes with a cap of 2 -> rclone exit 7, at most 2 deleted.
for i in 1 2 3 4; do rm "$SRC/insta360-flat/2025-08-maine/I360_$i.jpg"; done
run env PHOTOS_MAX_DELETE=2 PHOTOS_MAX_PASSES=3
[ "$(st rc)" = 7 ] && [ "$(st passes)" = 1 ] || fail "max-delete: rc=$(st rc) passes=$(st passes)"
[ "$(cnt insta360-flat)" -ge 7 ] || fail "max-delete: deleted more than 2 ($(cnt insta360-flat) left)"
ok "max-delete 2 with 4 deletes: rclone exit 7, no retry, $(( 9 - $(cnt insta360-flat) )) deleted"

# 8. dry run: no status, no state written.
cp "$DATA/.photos-status-proton" "$T/st.before"; cp "$DATA/.photos-state" "$T/state.before"
run env PHOTOS_DRY_RUN=1 PHOTOS_MAX_DELETE=1000
cmp -s "$DATA/.photos-status-proton" "$T/st.before" && cmp -s "$DATA/.photos-state" "$T/state.before" || fail "dry run wrote status/state"
[ "$(cnt insta360-flat)" -ge 7 ] || fail "dry run deleted files"
ok "dry run: nothing written, nothing deleted"

# 8b. copy mode never deletes on the remote (the 4 deletes above stay offsite).
before=$(cnt insta360-flat); run env PHOTOS_MODE=copy
[ "$(st rc)" = 0 ] && [ "$(cnt insta360-flat)" = "$before" ] || fail "copy mode: rc=$(st rc) insta=$(cnt insta360-flat) (was $before)"
grep -q 'mode=copy' "$T/out" || fail "copy mode: final line has no mode=copy"
ok "PHOTOS_MODE=copy: rc=0, nothing deleted offsite ($before files kept)"

# 9. every source refused: no rclone run, rc=90.
mi ext4 ext4; run env
[ "$(st rc)" = 90 ] && [ "$(st passes)" = 0 ] || fail "all refused: rc=$(st rc) passes=$(st passes)"
ok "all sources refused: no rclone run, rc=90"

# 10-12. rclone crashes vs usage errors. A fake rclone first on PATH plays one
# scripted outcome per sync/copy call (FAKE_PLAN, comma-separated: panic = Go
# panic on stderr + exit 2, as seen 2026-11-19; usage = plain exit 2; real = the
# real rclone); every other rclone call (the guard's `rclone size`) is real.
REAL_RCLONE=$(command -v rclone); mkdir -p "$T/bin"
cat > "$T/bin/rclone" <<EOF
#!/bin/sh
case \$1 in sync|copy) ;; *) exec "$REAL_RCLONE" "\$@" ;; esac
n=\$(( \$(cat "$T/calls" 2>/dev/null || echo 0) + 1 )); echo \$n > "$T/calls"
case \$(echo "\$FAKE_PLAN" | cut -d, -f\$n) in
  panic) echo "2026/11/19 03:59:58 NOTICE: stats line"
         echo "panic: runtime error: invalid memory address or nil pointer dereference" >&2
         echo "goroutine 47 [running]:" >&2; exit 2 ;;
  usage) echo "Error: unknown flag: --bogus" >&2; exit 2 ;;
  *) exec "$REAL_RCLONE" "\$@" ;;
esac
EOF
chmod +x "$T/bin/rclone"
fake() { rm -f "$T/calls"; plan=$1; shift; run env PATH="$T/bin:$PATH" FAKE_PLAN="$plan" "$@"; }
mi nfs4 nfs4; run env; lo=$(st last_ok)   # back in sync
rm "$SRC/insta360-flat/2025-08-maine/I360_9.jpg"   # one pending delete

# 10. a pass crashes -> the next pass runs and finishes the sync; rc=0.
sleep 1; fake panic,real PHOTOS_MAX_PASSES=3
[ "$(st rc)" = 0 ] && [ "$(st passes)" = 2 ] && [ "$(st crashes)" = 1 ] || fail "panic then ok: rc=$(st rc) passes=$(st passes) crashes=$(st crashes)"
grep -q '^photos pass 1: remote=proton rclone exit=91 secs=[0-9]* raw_exit=2 crash=panic$' "$T/out" || fail "panic: pass line"
grep -q '^panic: runtime error' "$T/out" || fail "panic: rclone output not streamed to the log"
[ ! -e "$DEST/insta360-flat/2025-08-maine/I360_9.jpg" ] && [ "$(st last_ok)" -gt "$lo" ] || fail "panic then ok: pass 2 did not sync"
ok "pass 1 crashed (panic, exit 2): logged as exit=91 crash=panic, pass 2 resumed and synced, rc=0"
lo=$(st last_ok); echo "trip 9" > "$SRC/insta360-flat/2025-08-maine/I360_9.jpg"

# 11. every pass crashes -> rc=91 (verify: seeding / not-synced path), last_ok kept,
#     nothing shipped.
fake panic,panic PHOTOS_MAX_PASSES=2
[ "$(st rc)" = 91 ] && [ "$(st passes)" = 2 ] && [ "$(st crashes)" = 2 ] || fail "all panic: rc=$(st rc) passes=$(st passes) crashes=$(st crashes)"
[ "$(st last_ok)" = "$lo" ] || fail "all panic: last_ok moved"
[ ! -e "$DEST/insta360-flat/2025-08-maine/I360_9.jpg" ] || fail "all panic: fake rclone shipped a file"
grep -q '^photos rclone exit=91 remote=proton passes=2 .* crashes=2$' "$T/out" || fail "all panic: final line"
ok "every pass crashed: rc=91 passes=2 crashes=2, last_ok unchanged"

# 12. a real usage error (exit 2, no panic) stops at once and stays rc=2.
fake usage,real PHOTOS_MAX_PASSES=3
[ "$(st rc)" = 2 ] && [ "$(st passes)" = 1 ] && [ "$(st crashes)" = 0 ] || fail "usage: rc=$(st rc) passes=$(st passes) crashes=$(st crashes)"
[ "$(st last_ok)" = "$lo" ] || fail "usage: last_ok moved"
ok "usage error (exit 2, no panic): no retry, rc=2, last_ok unchanged"
echo "all guard tests passed"
