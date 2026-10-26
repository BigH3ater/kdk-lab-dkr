#!/bin/sh
# Top the Tdarr transcode queue up from the backlog list. See compose.yaml.
# Each line of LIST is a Tdarr file path (its FileJSONDB _id). Lines are
# consumed from the top: re-queued or skipped (gone from Tdarr) lines are
# removed, a failed update stays in the list for the next run.
apk add --no-cache curl jq >/dev/null 2>&1 || true
TDARR="${TDARR_URL:-http://localhost:8266}"
LIST="${LIST:-/kdk/tdarr-backlog.list}"
MIN_QUEUED="${MIN_QUEUED:-3}"; BATCH="${BATCH:-6}"
TARGET_GPU_WORKERS="${TARGET_GPU_WORKERS:-2}"   # restore this many GPU workers once the list is empty
PUSHOVER_ENV="${PUSHOVER_ENV:-/kdk/pushover.env}"
notify() { # title, message
  [ -r "$PUSHOVER_ENV" ] || return 0
  tok=$(sed -n 's/^PUSHOVER_TOKEN=//p' "$PUSHOVER_ENV" | tr -d '"'); usr=$(sed -n 's/^PUSHOVER_USER=//p' "$PUSHOVER_ENV" | tr -d '"')
  [ -n "$tok" ] && [ -n "$usr" ] && curl -s -m 20 -o /dev/null -F "token=$tok" -F "user=$usr" -F "title=$1" -F "message=$2" https://api.pushover.net/1/messages.json || true
}
if [ ! -s "$LIST" ]; then
  # Backlog drained. The node ran on 1 GPU worker during the backlog so the
  # single-slot DEE wrapper never 503'd; put it back to TARGET_GPU_WORKERS once.
  nodes=$(curl -s -m 30 "$TDARR/api/v2/get-nodes")
  id=$(printf '%s' "$nodes" | jq -r 'to_entries[0].key // empty'); cur=$(printf '%s' "$nodes" | jq -r 'to_entries[0].value.workerLimits.transcodegpu // empty')
  if [ -n "$id" ] && [ -n "$cur" ] && [ "$cur" -lt "$TARGET_GPU_WORKERS" ]; then
    while [ "$cur" -lt "$TARGET_GPU_WORKERS" ]; do
      curl -s -m 30 -o /dev/null -X POST "$TDARR/api/v2/alter-worker-limit" -H 'content-type: application/json' \
        -d "$(jq -cn --arg id "$id" '{data:{nodeID:$id,process:"increase",workerType:"transcodegpu"}}')"; cur=$((cur+1))
    done
    echo "backlog empty -- GPU workers on $id raised to $TARGET_GPU_WORKERS"
    notify "kdk-lab tdarr-backlog done" "Backlog list empty; node $id back to $TARGET_GPU_WORKERS GPU workers."
  else
    echo "backlog empty -- nothing to do (gpu workers=${cur:-?})"
  fi
  exit 0
fi
db() { curl -s -m 120 -X POST "$TDARR/api/v2/cruddb" -H 'content-type: application/json' -d "$1"; }
queued=$(db '{"data":{"collection":"FileJSONDB","mode":"getAll"}}' | jq '[.[]|select(.TranscodeDecisionMaker=="Queued")]|length' 2>/dev/null)
[ -n "$queued" ] || { echo "tdarr unreachable"; exit 1; }
echo "queued=$queued remaining=$(wc -l < "$LIST")"
[ "$queued" -lt "$MIN_QUEUED" ] || { echo "queue busy, not topping up"; exit 0; }
added=0; new="$LIST.new"; : > "$new"
while IFS= read -r f; do
  [ -n "$f" ] || continue
  if [ "$added" -ge "$BATCH" ]; then printf '%s\n' "$f" >> "$new"; continue; fi
  doc=$(db "$(jq -cn --arg id "$f" '{data:{collection:"FileJSONDB",mode:"getById",docID:$id}}')")
  if ! printf '%s' "$doc" | jq -e '._id' >/dev/null 2>&1; then
    echo "  skip (no longer in Tdarr DB): ${f##*/}"; continue
  fi
  code=$(printf '%s' "$doc" | jq -c --arg id "$f" '{data:{collection:"FileJSONDB",mode:"update",docID:$id,obj:(.|.TranscodeDecisionMaker="Queued"|.holdUntil=0)}}' \
    | curl -s -m 120 -o /dev/null -w '%{http_code}' -X POST "$TDARR/api/v2/cruddb" -H 'content-type: application/json' -d @-)
  if [ "$code" = "200" ]; then echo "  requeued: ${f##*/}"; added=$((added+1))
  else echo "  FAILED http $code (kept): ${f##*/}"; printf '%s\n' "$f" >> "$new"; fi
done < "$LIST"
mv "$new" "$LIST"
echo "added=$added remaining=$(wc -l < "$LIST")"
