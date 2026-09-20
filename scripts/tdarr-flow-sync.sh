#!/bin/sh
# Sync tdarr flows between this repo (stacks/media/tdarr-flows/<id>.json) and
# the live tdarr server. The repo copy is the source of truth: edit the JSON
# here, commit, then `import`. `export` pulls the live flow into the repo
# (e.g. after editing in the tdarr UI); `diff` shows repo vs live.
#
#   scripts/tdarr-flow-sync.sh export [flowId]   live -> repo
#   scripts/tdarr-flow-sync.sh diff   [flowId]   repo vs live
#   scripts/tdarr-flow-sync.sh import [flowId]   repo -> live (then restarts tdarr)
#
# flowId defaults to hevc-pipeline (the only flow). Requires reach to
# tdarr on kdk-dkr-01:8266 (LAN/Tailscale) and, for import, SSH to restart
# the container so the server drops its in-memory copy.
set -eu

TDARR="${TDARR_URL:-http://10.1.20.20:8266}"
TDARR_HOST="${TDARR_SSH:-kdkadmin@10.1.20.20}"
FLOW="${2:-hevc-pipeline}"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
FILE="$REPO_DIR/stacks/media/tdarr-flows/$FLOW.json"

fetch() {
  curl -sf -X POST "$TDARR/api/v2/cruddb" -H 'Content-Type: application/json' \
    -d "{\"data\":{\"collection\":\"FlowsJSONDB\",\"mode\":\"getById\",\"docID\":\"$FLOW\"}}" \
    | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin), indent=2))'
}

case "${1:-}" in
  export)
    fetch > "$FILE"
    echo "exported $FLOW -> $FILE"
    ;;
  diff)
    fetch | diff -u - "$FILE" && echo "repo matches live ($FLOW)"
    ;;
  import)
    python3 - "$FILE" "$FLOW" <<'PY'
import json, subprocess, sys, urllib.request, os
file, flow = sys.argv[1], sys.argv[2]
doc = json.load(open(file))
assert doc["_id"] == flow, f'file _id {doc["_id"]!r} != {flow!r}'
body = json.dumps({"data": {"collection": "FlowsJSONDB", "mode": "update",
                            "docID": flow, "obj": doc}}).encode()
url = os.environ.get("TDARR_URL", "http://10.1.20.20:8266") + "/api/v2/cruddb"
req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
print("update:", urllib.request.urlopen(req).status)
PY
    ssh "$TDARR_HOST" 'sudo docker restart media-tdarr-1' >/dev/null
    echo "imported $FLOW and restarted tdarr; verifying..."
    sleep 20
    "$0" diff "$FLOW"
    ;;
  *)
    echo "usage: $0 export|diff|import [flowId]" >&2
    exit 2
    ;;
esac
