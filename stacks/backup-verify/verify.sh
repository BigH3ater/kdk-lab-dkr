#!/bin/sh
# Validate the whole nightly backup pass and page Pushover on ANY failure. Runs
# as the final stage so every staging + offsite step has completed. This is the
# single source of truth for backup health (the data/offsite stages are
# best-effort and exit 0). Checks:
#   * each host staged this run   -> .last-backup marker < 12h old
#   * DMZ pg dumps present, non-empty, gzip-valid for today
#   * offsite rclone exit==0 and its status is fresh (< 6h)
#   * Proxmox cloud-init template (VM 9000) vzdump present < 40d  [WARN only]
#   * CAD library archive offsite/3d-prints-<today>.tar.zst present + zstd -t
#   * CAD laptop sync marker (.3d-prints-last-sync, copied by pack) < 14d  [WARN only]
#   * photo originals synced to Proton (.photos-status-proton, see section 6)
# FAIL -> Pushover priority 1. WARN -> priority 0. All-clear -> quiet (-1).
apk add --no-cache curl zstd >/dev/null 2>&1 || true
. /pushover.env
PROB=""; WARN=""; now=$(date +%s)
add_prob() { PROB="$PROB\n- $1"; }
add_warn() { WARN="$WARN\n- $1"; }

# 1. every host staged in this run (marker touched < 12h ago). Not tighter:
#    verify runs after offsite, which can take hours on a big upload.
for h in kdk-dkr-01 kdk-dkr-dmz-01 kdk-dkr-02 kdk-dkr-03 kdk-mon-01 kdk-dkr-04; do
  m="/dest/$h/.last-backup"
  if [ ! -f "$m" ]; then add_prob "$h: never staged (no marker)"; continue; fi
  age=$(( now - $(date -r "$m" +%s) ))
  [ "$age" -gt 43200 ] && add_prob "$h: stale backup (marker ${age}s old)"
done

# 2. pg dumps for today: present, non-empty, gzip-valid.
#    DMZ host: authelia + immich + tandoor. dkr-01 host: jellystat + wger.
today=$(date +%F)
for db in authelia immich tandoor; do
  f="/dest/kdk-dkr-dmz-01/pgdump/$db-$today.sql.gz"
  if [ ! -s "$f" ]; then add_prob "pgdump $db missing/empty ($today)"
  elif ! gzip -t "$f" 2>/dev/null; then add_prob "pgdump $db corrupt (gzip -t failed)"; fi
done
for db in jellystat wger; do
  f="/dest/kdk-dkr-01/pgdump/$db-$today.sql.gz"
  if [ ! -s "$f" ]; then add_prob "pgdump $db missing/empty ($today)"
  elif ! gzip -t "$f" 2>/dev/null; then add_prob "pgdump $db corrupt (gzip -t failed)"; fi
done

# 3. offsite result
if [ ! -f /dest/.offsite-status ]; then add_prob "offsite: no status file"
else
  rc=""; ts=0; . /dest/.offsite-status 2>/dev/null || true
  [ "${rc:-1}" = "0" ] || add_prob "offsite: rclone exit=${rc:-?}"
  oage=$(( now - ${ts:-0} ))
  [ "$oage" -gt 21600 ] && add_prob "offsite: status stale (${oage}s)"
fi

# 4. Proxmox cloud-init template (monthly) -> warn only
if [ -z "$(find /dest/pve-templates -type f -mtime -40 2>/dev/null | head -1)" ]; then
  add_warn "pve-template: no VM 9000 vzdump < 40d (see /var/log/kdk-template-backup.log on kdk-hyp-01)"
fi

# 5. CAD library (docs/runbooks/cad-library.md). The archive is built by
#    backup-offsite's pack on kdk-dkr-04 from /mnt/3d-prints; this host cannot
#    see that mount, so pack also copies the laptop's .last-sync marker (with
#    its mtime) to /dest/.3d-prints-last-sync. Missing/corrupt archive = FAIL
#    (same class as a pg dump); a stale laptop sync = WARN (the laptop may just
#    be away from home).
f="/dest/offsite/3d-prints-$today.tar.zst"
if [ ! -s "$f" ]; then add_prob "3d-prints archive missing/empty ($today)"
elif ! command -v zstd >/dev/null 2>&1; then add_warn "3d-prints archive not tested (apk add zstd failed)"
elif ! zstd -t -q "$f" 2>/dev/null; then add_prob "3d-prints archive corrupt (zstd -t failed)"; fi
m=/dest/.3d-prints-last-sync
if [ ! -f "$m" ]; then add_warn "3d-prints: no laptop .last-sync marker (cad-sync never ran?)"
else
  sage=$(( now - $(date -r "$m" +%s) ))
  [ "$sage" -gt 1209600 ] && add_warn "3d-prints: laptop cad-sync last ran $(( sage / 86400 ))d ago ($(cat "$m" 2>/dev/null))"
fi

# 6. Photo originals (backup-photos on kdk-dkr-04, docs/runbooks/photo-offsite.md):
#    one status line per remote, written by photos.sh after its passes:
#      rc= ts= date= passes= guard=ok|refused:... last_ok=EPOCH|0 seed_start=EPOCH crashes=N
#    FAIL: no/stale status; any guard refusal (a mount down, an empty or shrunken
#      source: that source was left out, its offsite copy is kept); rclone exit 2/7
#      (usage error, or fatal e.g. --max-delete exceeded); not in sync and no full
#      sync in 3 days; the first upload still not complete after 14 days.
#    WARN: the first upload is still seeding (< 14 days since the first run), or
#      tonight hit the pass cap (exit 10) / a Proton blip / an rclone crash (rc=91:
#      Go panic, raw exit 2, retried like a cap) with a full sync < 3 days ago.
for r in proton; do
  f=/dest/.photos-status-$r
  if [ ! -f "$f" ]; then add_prob "photos/$r: no status file (backup-photos never ran?)"; continue; fi
  rc=""; ts=0; passes=""; guard=""; last_ok=0; seed_start=0; crashes=0
  . "$f" 2>/dev/null || true
  page=$(( now - ${ts:-0} ))
  if [ "$page" -gt 21600 ]; then add_prob "photos/$r: status stale (${page}s)"; continue; fi
  case "${guard:-?}" in ok) ;; *) add_prob "photos/$r: guard ${guard:-?} (left out tonight, offsite copy kept)" ;; esac
  rc=${rc:-1}; last_ok=${last_ok:-0}; seed_start=${seed_start:-$now}
  # 91 = the last pass crashed (rclone Go panic): an interrupted pass, like exit 10.
  ex="rclone exit=$rc after ${passes:-?} passes"
  [ "${crashes:-0}" -gt 0 ] 2>/dev/null && ex="$ex, $crashes crashed: rclone Go panic, retried"
  if [ "$rc" = 0 ] || [ "$rc" = 90 ]; then
    :   # in sync, or every source refused (already a FAIL above)
  elif [ "$rc" = 2 ] || [ "$rc" = 7 ]; then
    add_prob "photos/$r: rclone exit=$rc (2 = usage error, 7 = fatal, e.g. more than PHOTOS_MAX_DELETE deletes)"
  elif [ "$last_ok" -eq 0 ]; then
    sd=$(( (now - seed_start) / 86400 ))
    if [ "$sd" -lt 14 ]; then add_warn "photos/$r: first upload still seeding (day $((sd + 1)), $ex)"
    else add_prob "photos/$r: first upload not complete after ${sd}d ($ex)"; fi
  elif [ $(( now - last_ok )) -lt 259200 ]; then
    add_warn "photos/$r: not fully synced tonight ($ex), last full sync $(( (now - last_ok) / 3600 ))h ago"
  else
    add_prob "photos/$r: $ex, last full sync $(( (now - last_ok) / 86400 ))d ago"
  fi
done

push() {
  curl -s --max-time 25 \
    --form-string "token=$PUSHOVER_TOKEN" --form-string "user=$PUSHOVER_USER" \
    --form-string "priority=$1" --form-string "title=$2" \
    --form-string "message=$(printf '%b' "$3")" \
    https://api.pushover.net/1/messages.json >/dev/null
}

if [ -n "$PROB" ]; then
  printf 'BACKUP VALIDATION FAILED:%b%b\n' "$PROB" "$WARN"
  push 1 "kdk-lab backup FAILED" "Validation failed:$PROB${WARN:+\nWARN:$WARN}"
  exit 1
elif [ -n "$WARN" ]; then
  printf 'BACKUP OK with warnings:%b\n' "$WARN"
  push 0 "kdk-lab backup: warnings" "Warnings:$WARN"
else
  echo "BACKUP OK -- all hosts fresh, pg dumps valid, 3d-prints archive valid, offsite clean, photos in sync"
  push -1 "kdk-lab backup OK" "All 6 hosts staged, pg dumps valid, 3d-prints archive valid, offsite rclone exit=0, photo originals in sync, template present."
fi
