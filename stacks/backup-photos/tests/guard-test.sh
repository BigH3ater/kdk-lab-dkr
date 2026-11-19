#!/bin/sh
# Local simulation of photos.sh's deletion guard, with the real rclone binary
# and a LOCAL directory as the "remote" (nothing touches Proton or any config:
# PHOTOS_RCLONE_CONFIG points at a missing file, the destination is a temp dir).
# Fake /proc/self/mountinfo lines say which binds are "NFS".
# B2 (13-21): a test-only b2.env and a real crypt remote over a LOCAL directory
# (PHOTOS_B2_UNDERLAY), so no Backblaze account is touched either; covers both
# remotes in one night, the Immich DB dumps, the restore helper b2-rclone.sh,
# "Proton fails, B2 still runs" and the reverse, B2 not configured, the guard
# applying to both remotes, and that Proton's rclone flags/env are unchanged.
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
# calls.log: which --config and how many b2 remote variables each call saw.
cfg=none; prev=""; for a in "\$@"; do [ "\$prev" = --config ] && cfg="[\$a]"; prev=\$a; done
echo "call=\$n dest=\$3 cfg=\$cfg b2env=\$(env | grep -c '^RCLONE_CONFIG_B2PHOTOS')" >> "$T/calls.log"
echo "\$*" >> "$T/args.\$n"
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

# 13-21. B2, the second remote. b2.env holds test-only values; the crypt wraps a
# LOCAL directory (PHOTOS_B2_UNDERLAY) instead of the bucket, so the real crypt
# layer, the environment-defined remotes and --config "" are all exercised.
B2U=$T/b2bucket; DB=$T/pgdump; mkdir -p "$B2U" "$DB"
for d in 2026-11-17 2026-11-18; do
  echo "immich dump $d" | gzip > "$DB/immich-$d.sql.gz"; echo "authelia $d" | gzip > "$DB/authelia-$d.sql.gz"
done
TPW=test-crypt-password-$$; TSALT=test-crypt-salt-$$; TKEY=test-app-key-$$
printf 'PHOTOS_B2_ACCOUNT=test-key-id\nPHOTOS_B2_KEY=%s\nPHOTOS_B2_BUCKET=test-bucket\nPHOTOS_B2_CRYPT_PASSWORD=%s\nPHOTOS_B2_CRYPT_SALT=%s\n' \
  "$TKEY" "$TPW" "$TSALT" > "$T/b2.env"
run2() { run env PHOTOS_REMOTES="proton b2" PHOTOS_B2_ENV="$T/b2.env" PHOTOS_B2_UNDERLAY="$B2U" \
  PHOTOS_DB_SRC="$DB" PHOTOS_B2_MAX_PASSES=1 "$@"; }
fake2() { rm -f "$T/calls" "$T/calls.log" "$T"/args.*; plan=$1; shift; run2 env PATH="$T/bin:$PATH" FAKE_PLAN="$plan" "$@"; }
st2() { tr ' ' '\n' < "$DATA/.photos-status-b2" | sed -n "s/^$1=//p"; }
# dec: read the B2 copy the way the laptop restore does: a fresh crypt remote
# built from nothing but the password and salt (docs/runbooks/photo-offsite.md#b2).
dec() {
  RCLONE_CONFIG_R_TYPE=crypt RCLONE_CONFIG_R_REMOTE="$B2U" RCLONE_CONFIG_R_FILENAME_ENCRYPTION=standard \
  RCLONE_CONFIG_R_DIRECTORY_NAME_ENCRYPTION=true \
  RCLONE_CONFIG_R_PASSWORD=$(printf %s "$TPW" | rclone obscure -) \
  RCLONE_CONFIG_R_PASSWORD2=$(printf %s "$TSALT" | rclone obscure -) rclone --config "" "$@"
}
dcnt() { dec lsf -R --files-only "r:photos/$1" 2>/dev/null | wc -l | tr -d ' '; }
INSTA=$SRC/insta360-flat/2025-08-maine

# 13. both remotes: Proton as before, B2 gets the photos + the Immich dumps only,
#     encrypted names, and a restore from password + salt matches byte for byte.
mi nfs4 nfs4; run2 env
[ "$(st rc)" = 0 ] && [ "$(st2 rc)" = 0 ] && [ "$(st2 guard)" = ok ] || fail "b2 normal: proton rc=$(st rc) b2 rc=$(st2 rc) guard=$(st2 guard)"
[ "$(st2 db_rc)" = 0 ] && [ "$(st2 db_guard)" = ok ] && [ "$(st2 db_last_ok)" -gt 0 ] && [ "$(st2 last_ok)" -gt 0 ] || fail "b2 normal: db_rc=$(st2 db_rc) db_guard=$(st2 db_guard)"
[ "$(dcnt immich)" = "$(find "$SRC/immich" -type f | wc -l | tr -d ' ')" ] && [ "$(dcnt insta360-flat)" = "$(find "$SRC/insta360-flat" -type f | wc -l | tr -d ' ')" ] \
  || fail "b2 normal: b2 immich=$(dcnt immich) insta=$(dcnt insta360-flat)"
[ "$(dec lsf r:immich-db | tr '\n' ' ')" = "immich-2026-11-17.sql.gz immich-2026-11-18.sql.gz " ] || fail "b2 db: $(dec lsf r:immich-db | tr '\n' ' ')"
[ -z "$(find "$B2U" \( -name '*.jpg' -o -name '*.gz' -o -name 'photos' -o -name 'immich*' -o -name 'insta360*' \) -print)" ] || fail "b2: plaintext names in the bucket"
f=$(find "$INSTA" -type f -name '*.jpg' | head -1); g=$SRC/immich/library/jmack/2026/2026-11-16/IMG_3.jpg
[ "$(dec cat "r:photos/insta360-flat/2025-08-maine/${f##*/}" | sha256sum)" = "$(sha256sum < "$f")" ] \
  && [ "$(dec cat r:photos/immich/library/jmack/2026/2026-11-16/IMG_3.jpg | sha256sum)" = "$(sha256sum < "$g")" ] \
  && [ "$(dec cat r:immich-db/immich-2026-11-18.sql.gz | sha256sum)" = "$(sha256sum < "$DB/immich-2026-11-18.sql.gz")" ] || fail "b2 restore: sha256 differs"
grep -q '^photos db: remote=b2 rclone exit=0 files=2 secs=[0-9]* guard=ok$' "$T/out" || fail "b2: db log line"
grep -q '^photos rclone exit=0 remote=b2 passes=1 ' "$T/out" || fail "b2: final line"
! grep -qF -e "$TPW" -e "$TSALT" -e "$TKEY" "$T/out" || fail "b2: a secret reached the log"
[ "$(st2 seed_start)" != "$seed" ] || fail "b2: seed_start copied from proton"
ok "proton + b2: both rc=0, B2 has photos + the 2 Immich dumps (not authelia), names encrypted, restore sha256 matches, no secret in the log"

# 13b. b2-rclone.sh (the restore helper) reads the same copy, from b2.env and from
#      "1Password" (a fake op that answers the five op:// references).
cat > "$T/fakeop" <<EOF
#!/bin/sh
[ "\$1" = read ] || exit 1
case \$2 in
  op://kdk-ops/b2-photos-app-key/username) echo test-key-id ;;
  op://kdk-ops/b2-photos-app-key/credential) echo "$TKEY" ;;
  op://kdk-ops/b2-photos-app-key/bucket) echo test-bucket ;;
  op://kdk-ops/b2-photos-crypt/password) echo "$TPW" ;;
  op://kdk-ops/b2-photos-crypt/salt) echo "$TSALT" ;;
  *) exit 1 ;;
esac
EOF
h1=$(B2_ENV=$T/b2.env PHOTOS_B2_UNDERLAY=$B2U ${SH:-sh} "$HERE/b2-rclone.sh" cat b2photoscrypt:photos/immich/library/jmack/2026/2026-11-16/IMG_3.jpg | sha256sum)
h2=$(OP="sh $T/fakeop" PHOTOS_B2_UNDERLAY=$B2U ${SH:-sh} "$HERE/b2-rclone.sh" --op cat b2photoscrypt:photos/immich/library/jmack/2026/2026-11-16/IMG_3.jpg | sha256sum)
[ "$h1" = "$(sha256sum < "$g")" ] && [ "$h2" = "$h1" ] || fail "b2-rclone.sh: hashes differ ($h1 / $h2)"
B2_ENV=$T/no-such-b2.env ${SH:-sh} "$HERE/b2-rclone.sh" lsf b2photoscrypt: 2>/dev/null && fail "b2-rclone.sh: ran without b2.env"
ok "b2-rclone.sh restores the same bytes from b2.env and from 1Password (--op); refuses without credentials"

# 14. Proton fails (usage error), B2 still runs and syncs. Proton's rclone saw its
#     config file and no b2 variable; B2's calls ran with --config "" and the b2 remotes.
echo "trip 12" > "$INSTA/I360_12.jpg"
fake2 usage,real,real
[ "$(st rc)" = 2 ] && [ "$(st2 rc)" = 0 ] && [ "$(st2 db_rc)" = 0 ] || fail "proton fails: proton rc=$(st rc) b2 rc=$(st2 rc) db_rc=$(st2 db_rc)"
dec lsf r:photos/insta360-flat/2025-08-maine/I360_12.jpg | grep -q . || fail "proton fails: b2 did not get the new still"
[ ! -e "$DEST/insta360-flat/2025-08-maine/I360_12.jpg" ] || fail "proton fails: proton got it anyway?"
grep -q "^call=1 dest=$DEST cfg=\[$T/no-such-rclone.conf\] b2env=0$" "$T/calls.log" || fail "proton call env: $(cat "$T/calls.log")"
grep -q '^call=2 dest=b2photoscrypt:immich-db cfg=\[\] b2env=1[0-9]$' "$T/calls.log" && grep -q '^call=3 dest=b2photoscrypt:photos cfg=\[\] b2env=1[0-9]$' "$T/calls.log" \
  || fail "b2 call env: $(cat "$T/calls.log")"
# Proton's flags are exactly the Proton-only version's (same set, order aside); none of B2's.
pa=" $(cat "$T/args.1") "
for fl in "--size-only" "--transfers 2" "--checkers 4" "--retries 3" "--low-level-retries 10" "--max-delete 100" \
  "--max-duration 25m" "--log-level NOTICE" "--stats 5m" "--stats-one-line" "--stats-log-level NOTICE" \
  "--protondrive-replace-existing-draft=true" "--tpslimit 10"; do
  case $pa in *" $fl "*) ;; *) fail "proton args: missing $fl in$pa" ;; esac
done
case $pa in *--fast-list*|*--cutoff-mode*|*--b2-*) fail "proton args: B2 flag in$pa" ;; esac
[ "$(printf "%s" "$pa" | tr -s " " "\n" | grep -c .)" = 40 ] || fail "proton args: word count:$pa"
ok "Proton usage error (rc=2): B2 still synced (DB + photos, rc=0); Proton's rclone never saw a b2 variable, B2 ran without rclone.conf; Proton flags unchanged"

# 15. B2 fails (every call crashes), Proton is unaffected and in sync.
lo2=$(st2 last_ok); fake2 real,panic,panic
[ "$(st rc)" = 0 ] && [ -e "$DEST/insta360-flat/2025-08-maine/I360_12.jpg" ] || fail "b2 fails: proton rc=$(st rc)"
[ "$(st2 rc)" = 91 ] && [ "$(st2 crashes)" = 1 ] && [ "$(st2 db_rc)" = 91 ] && [ "$(st2 last_ok)" = "$lo2" ] || fail "b2 fails: rc=$(st2 rc) crashes=$(st2 crashes) db_rc=$(st2 db_rc)"
ok "B2 crashes (DB + photos rc=91): Proton synced (rc=0), B2 last_ok unchanged"

# 16. Order does not matter: B2 first and broken, Proton after it still syncs.
echo "trip 13" > "$INSTA/I360_13.jpg"
fake2 usage,usage,real PHOTOS_REMOTES="b2 proton"
[ "$(st2 rc)" = 2 ] && [ "$(st2 db_rc)" = 2 ] && [ "$(st rc)" = 0 ] && [ -e "$DEST/insta360-flat/2025-08-maine/I360_13.jpg" ] \
  || fail "b2 first: b2 rc=$(st2 rc) db_rc=$(st2 db_rc) proton rc=$(st rc)"
ok "B2 first with usage errors (rc=2): Proton after it still synced"
run2 env; [ "$(st2 rc)" = 0 ] || fail "b2 recover: rc=$(st2 rc)"

# 17. not configured: no b2.env, then an empty field -> rc=92, Proton untouched.
rm "$INSTA/I360_13.jpg"; run2 env PHOTOS_B2_ENV="$T/no-such-b2.env"
[ "$(st2 rc)" = 92 ] && [ "$(st2 db_guard)" = not-configured ] && [ "$(st2 passes)" = 0 ] || fail "no env: b2 rc=$(st2 rc) db_guard=$(st2 db_guard)"
[ "$(st rc)" = 0 ] && [ ! -e "$DEST/insta360-flat/2025-08-maine/I360_13.jpg" ] || fail "no env: proton rc=$(st rc)"
grep -q '^photos: b2 not configured (no-env-file), skipped' "$T/out" || fail "no env: log line"
dec lsf r:photos/insta360-flat/2025-08-maine/I360_13.jpg | grep -q . || fail "no env: b2 copy changed"
grep -v '^PHOTOS_B2_CRYPT_SALT=' "$T/b2.env" > "$T/b2-nosalt.env"; run2 env PHOTOS_B2_ENV="$T/b2-nosalt.env"
[ "$(st2 rc)" = 92 ] && grep -q 'b2 not configured (empty-PHOTOS_B2_CRYPT_SALT)' "$T/out" || fail "no salt: b2 rc=$(st2 rc)"
ok "b2.env missing or a field empty: b2 rc=92 (not configured), nothing touched on B2, Proton rc=0"

# 18. the guard applies to both remotes: a dead Insta360 mount is left out of each.
bc=$(dcnt insta360-flat); mv "$SRC/insta360-flat" "$T/insta-away"; mkdir "$SRC/insta360-flat"; mi nfs4 ext4; run2 env
case "$(st guard)|$(st2 guard)" in "refused:insta360-flat:not-nfs:insta360-flat:ext4|refused:insta360-flat:not-nfs:insta360-flat:ext4") ;; *) fail "guard both: $(st guard) | $(st2 guard)" ;; esac
[ "$(dcnt insta360-flat)" = "$bc" ] && [ "$(st2 rc)" = 0 ] || fail "guard both: b2 insta copy changed ($(dcnt insta360-flat), was $bc)"
ok "dead Insta360 mount: refused for Proton AND B2, the B2 copy untouched"
rmdir "$SRC/insta360-flat"; mv "$T/insta-away" "$SRC/insta360-flat"; mi nfs4 nfs4

# 19. DB guard: no immich dump in the folder -> no DB sync (B2 dumps kept), photos still sync.
mkdir "$T/db-away"; mv "$DB"/immich-* "$T/db-away/"; run2 env
[ "$(st2 db_guard)" = no-dumps ] && [ "$(st2 db_rc)" = 90 ] && [ "$(st2 rc)" = 0 ] || fail "db guard: db_guard=$(st2 db_guard) db_rc=$(st2 db_rc) rc=$(st2 rc)"
[ "$(dec lsf r:immich-db | wc -l | tr -d ' ')" = 2 ] || fail "db guard: B2 dumps deleted"
mv "$T/db-away"/* "$DB/"
ok "no Immich dump: DB sync refused (db_rc=90), B2 dumps kept, photos rc=0"

# 20. dump rotation propagates on B2 (sync), other DB dumps never ship.
rm "$DB/immich-2026-11-17.sql.gz"; echo "immich dump 2026-11-19" | gzip > "$DB/immich-2026-11-19.sql.gz"; run2 env
[ "$(dec lsf r:immich-db | tr '\n' ' ')" = "immich-2026-11-18.sql.gz immich-2026-11-19.sql.gz " ] && [ "$(st2 db_rc)" = 0 ] || fail "db rotate: $(dec lsf r:immich-db | tr '\n' ' ')"
ok "dump rotation: B2 immich-db follows the 15-kept folder (old dump hidden on real B2)"

# 21. dry run: neither status file written.
cp "$DATA/.photos-status-b2" "$T/st2.before"; cp "$DATA/.photos-status-proton" "$T/st.before"
echo "trip 14" > "$INSTA/I360_14.jpg"; run2 env PHOTOS_DRY_RUN=1
cmp -s "$DATA/.photos-status-b2" "$T/st2.before" && cmp -s "$DATA/.photos-status-proton" "$T/st.before" || fail "b2 dry run wrote a status"
! dec lsf r:photos/insta360-flat/2025-08-maine/I360_14.jpg 2>/dev/null | grep -q . || fail "b2 dry run uploaded"
ok "dry run with both remotes: no status written, nothing uploaded to B2"
echo "all guard tests passed"
