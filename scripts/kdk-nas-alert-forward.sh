#!/usr/bin/env bash
# Forward TrueNAS WARNING/CRITICAL alerts to Pushover (TrueNAS has no native
# Pushover/webhook alert service). Dedupes via /root/.kdk-alert-sent.
set -u
. /root/.kdk-pushover.env
SENT=/root/.kdk-alert-sent; touch "$SENT"
exec 9>/run/kdk-alert-forward.lock; flock -n 9 || exit 0
notify(){ curl -s -o /dev/null --max-time 10 https://api.pushover.net/1/messages.json \
  --form-string token="$PUSHOVER_TOKEN" --form-string user="$PUSHOVER_USER" \
  --form-string title="$1" --form-string message="$2" --form-string priority="${3:-0}"; }
notify "kdk-nas alert forwarder started" "TrueNAS WARNING/CRITICAL alerts now route to Pushover." 0
while true; do
  midclt call alert.list 2>/dev/null | python3 -c '
import sys,json
try: sent=set(open("/root/.kdk-alert-sent").read().split())
except: sent=set()
for a in json.load(sys.stdin):
    if a.get("dismissed"): continue
    lvl=(a.get("level") or "").upper()
    if lvl in ("WARNING","ERROR","CRITICAL","ALERT","EMERGENCY"):
        u=a.get("uuid")
        if u and u not in sent:
            print(u+"\t"+lvl+"\t"+(a.get("formatted") or a.get("text") or "")[:900].replace("\n"," "))
' > /tmp/.kdk_new_alerts 2>/dev/null
  while IFS=$'\t' read -r u lvl msg; do
    [ -z "${u:-}" ] && continue
    pr=0; case "$lvl" in CRITICAL|ALERT|EMERGENCY) pr=1;; esac
    notify "kdk-nas $lvl" "$msg" "$pr" && echo "$u" >> "$SENT"
  done < /tmp/.kdk_new_alerts
  rm -f /tmp/.kdk_new_alerts
  sleep 300
done
