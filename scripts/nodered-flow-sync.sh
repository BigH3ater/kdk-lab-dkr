#!/bin/sh
# Sync the Home Assistant Node-RED flow (Roborock daily run, Vikunja chore
# bridge) between this repo (stacks/home-assistant/node-red/flows.json) and the
# live Node-RED on kdk-dkr-02. The repo copy is the source of truth: edit here,
# commit, then `import`. `export` pulls the live flow (after a UI edit).
#
#   scripts/nodered-flow-sync.sh export   live -> repo
#   scripts/nodered-flow-sync.sh diff     repo vs live
#   scripts/nodered-flow-sync.sh import   repo -> live (backup, then restart node-red)
#
# flows.json holds NO secrets: the HA token and the Vikunja bearer token live
# in flows_cred.json on the host (plaintext, credentialSecret:false) and are
# never exported. import refuses a file that contains a bearer token.
set -eu

HOST="${NODERED_SSH:-kdkadmin@10.1.30.21}"
LIVE=/opt/kdk-lab/home-assistant/node-red/flows.json
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
FILE="$REPO_DIR/stacks/home-assistant/node-red/flows.json"

fetch() {
  ssh "$HOST" "sudo -n cat $LIVE" | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin), indent=4))'
}

case "${1:-}" in
  export)
    fetch > "$FILE"
    echo "exported live flow -> $FILE"
    ;;
  diff)
    fetch | diff -u - "$FILE" && echo "repo matches live"
    ;;
  import)
    if grep -qE 'Bearer |eyJhbGci' "$FILE"; then
      echo "refusing: $FILE contains a token" >&2; exit 1
    fi
    python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$FILE"
    ts=$(date +%Y%m%d-%H%M%S)
    ssh "$HOST" "sudo -n cp -p $LIVE $LIVE.bak.$ts && sudo -n tee $LIVE >/dev/null && sudo -n chown 1000:1000 $LIVE && sudo -n docker restart node-red >/dev/null" < "$FILE"
    echo "imported (backup $LIVE.bak.$ts); node-red restarted"
    ;;
  *)
    echo "usage: $0 export|diff|import" >&2; exit 2
    ;;
esac
