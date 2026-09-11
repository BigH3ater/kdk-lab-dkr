#!/usr/bin/env bash
# Delete an AdGuard rewrite on the origin so the name falls through to the
# wildcard. Usage: adguard-delete-rewrite.sh <name.kmkdp.com>
set -euo pipefail
NAME=$1; BASE="http://10.1.1.120/control"; AUTH="$ADGUARD_USERNAME:$ADGUARD_PASSWORD"
old=$(curl -fsS -u "$AUTH" "$BASE/rewrite/list" | python3 -c "
import json,sys
for r in json.load(sys.stdin):
    if r['domain']=='$NAME': print(r['answer']); break")
[ -n "$old" ] || { echo "$NAME: no rewrite"; exit 0; }
curl -fsS -u "$AUTH" -H 'Content-Type: application/json' -X POST "$BASE/rewrite/delete" \
  -d "{\"domain\":\"$NAME\",\"answer\":\"$old\"}"
echo "$NAME deleted (was $old) -> wildcard"
