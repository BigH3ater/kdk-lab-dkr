#!/bin/sh
# Storage/NFS health validation + alerting. Runs hourly (storage-health
# procedure) on kdk-dkr-01 and pages Pushover on:
#   * NAS storage IP unreachable (ping) -- the storage network / NAS is down.
#   * NFS 2049 refused/filtered on the NAS -- nfsd not listening for us (e.g.
#     bindip missing our IP after a NAS network change).
#   * An NFS mount hung or missing -- a bounded `stat` on each mount times out
#     (hard-mount hang, the 2026-09-16 signature) or the mount is gone.
#   * A CGNAT regression -- the storage IP has drifted back inside Tailscale's
#     100.64.0.0/10 (which silently blackholes NFS on any Tailscale host).
# FAIL -> Pushover priority 1; clean -> silent (no hourly spam).
# See docs/runbooks/storage-network.md.
set -u
apk add --no-cache curl iputils netcat-openbsd >/dev/null 2>&1 || true
. /kdk/pushover.env

NAS_IP="${NAS_IP:-172.16.30.2}"
NFS_MOUNTS="${NFS_MOUNTS:-media backup dee-exchange loki}"
PROB=""
add_prob() { PROB="$PROB\n- $1"; }

# 1. NAS reachable on the storage network
if ! ping -c 2 -W 2 "$NAS_IP" >/dev/null 2>&1; then
  add_prob "NAS storage IP $NAS_IP UNREACHABLE (ping) -- storage network or NAS down"
fi

# 2. NFS server port 2049 accepting connections (nc -- busybox sh has no /dev/tcp)
if ! nc -z -w 5 "$NAS_IP" 2049 >/dev/null 2>&1; then
  add_prob "NFS 2049 on $NAS_IP refused/filtered -- nfsd not listening for us (check TrueNAS nfs.config bindip)"
fi

# 3. Each mount responsive (bounded -- a hung hard mount blocks stat forever)
for m in $NFS_MOUNTS; do
  if ! timeout 8 stat -f "/hostmnt/$m" >/dev/null 2>&1; then
    # distinguish hung (timeout) from simply not mounted (empty mountpoint)
    if [ ! -d "/hostmnt/$m" ]; then
      add_prob "NFS mount /mnt/$m MISSING (mountpoint absent)"
    else
      add_prob "NFS mount /mnt/$m HUNG or unmounted -- bounded stat failed (hard-mount hang?)"
    fi
  fi
done

# 4. CGNAT regression guard: storage IP must NOT be inside 100.64.0.0/10
case "$NAS_IP" in
  100.6[4-9].*|100.7*.*|100.8*.*|100.9*.*|100.1[01]*.*|100.12[0-7].*)
    add_prob "Storage IP $NAS_IP is inside Tailscale CGNAT 100.64.0.0/10 -- WILL be blackholed on Tailscale hosts; renumber off CGNAT" ;;
esac

push() {
  curl -s --max-time 25 \
    --form-string "token=$PUSHOVER_TOKEN" --form-string "user=$PUSHOVER_USER" \
    --form-string "priority=$1" --form-string "title=$2" \
    --form-string "message=$(printf '%b' "$3")" \
    https://api.pushover.net/1/messages.json >/dev/null
}

if [ -n "$PROB" ]; then
  printf 'STORAGE HEALTH FAILED:%b\n' "$PROB"
  push 1 "kdk-lab storage FAILED" "Storage/NFS health failed on kdk-dkr-01:$PROB"
else
  echo "storage-health OK ($NAS_IP, mounts: $NFS_MOUNTS)"
fi
exit 0
