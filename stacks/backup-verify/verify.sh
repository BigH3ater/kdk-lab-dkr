#!/bin/sh
# Validate the whole nightly backup pass and page Pushover on ANY failure. Runs
# as the final stage so every staging + offsite step has completed. This is the
# single source of truth for backup health (the data/offsite stages are
# best-effort and exit 0). Checks:
#   * each host staged this run   -> .last-backup marker < 2h old
#   * DMZ pg dumps present, non-empty, gzip-valid for today
#   * offsite rclone exit==0 and its status is fresh (< 6h)
#   * Proxmox cloud-init template (VM 9000) vzdump present < 40d  [WARN only]
# FAIL -> Pushover priority 1. WARN -> priority 0. All-clear -> quiet (-1).
apk add --no-cache curl >/dev/null 2>&1 || true
. /pushover.env
PROB=""; WARN=""; now=$(date +%s)
add_prob() { PROB="$PROB\n- $1"; }
add_warn() { WARN="$WARN\n- $1"; }

# 1. every host staged in this run (marker touched < 2h ago)
for h in kdk-dkr-01 kdk-dkr-dmz-01 kdk-dkr-02 kdk-dkr-03 kdk-mon-01; do
  m="/dest/$h/.last-backup"
  if [ ! -f "$m" ]; then add_prob "$h: never staged (no marker)"; continue; fi
  age=$(( now - $(date -r "$m" +%s) ))
  [ "$age" -gt 7200 ] && add_prob "$h: stale backup (marker ${age}s old)"
done

# 2. DMZ pg dumps for today: present, non-empty, gzip-valid
today=$(date +%F)
for db in authelia immich; do
  f="/dest/kdk-dkr-dmz-01/pgdump/$db-$today.sql.gz"
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
  add_warn "pve-template: no VM 9000 vzdump < 40d (wire the PVE cron)"
fi

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
  echo "BACKUP OK -- all hosts fresh, pg dumps valid, offsite clean"
  push -1 "kdk-lab backup OK" "All 5 hosts staged, pg dumps valid, offsite rclone exit=0, template present."
fi
