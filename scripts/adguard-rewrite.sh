#!/usr/bin/env bash
# Flip an AdGuard DNS rewrite on the origin (kdk-dns-01). adguardhome-sync
# propagates to dns-02/03. Usage: adguard-rewrite.sh <name.kmkdp.com> <ip>
# Creds: ADGUARD_USERNAME / ADGUARD_PASSWORD in env (op run recommended).
set -euo pipefail
NAME=$1; IP=$2
BASE="http://10.1.1.120/control"
AUTH="$ADGUARD_USERNAME:$ADGUARD_PASSWORD"
old=$(curl -fsS -u "$AUTH" "$BASE/rewrite/list" | python3 -c "
import json,sys
for r in json.load(sys.stdin):
    if r['domain']=='$NAME': print(r['answer']); break")
if [ -n "$old" ]; then
  curl -fsS -u "$AUTH" -H 'Content-Type: application/json' -X PUT "$BASE/rewrite/update" \
    -d "{\"target\":{\"domain\":\"$NAME\",\"answer\":\"$old\"},\"update\":{\"domain\":\"$NAME\",\"answer\":\"$IP\"}}"
else
  curl -fsS -u "$AUTH" -H 'Content-Type: application/json' -X POST "$BASE/rewrite/add" \
    -d "{\"domain\":\"$NAME\",\"answer\":\"$IP\"}"
fi
echo "$NAME -> $IP (was: ${old:-none})"
