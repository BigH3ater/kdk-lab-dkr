#!/bin/sh
# Top the Tdarr transcode queue up from the backlog list. See compose.yaml.
# Each line of LIST is a Tdarr file path (its FileJSONDB _id). Lines are
# consumed from the top: re-queued or skipped (gone from Tdarr) lines are
# removed, a failed update stays in the list for the next run.
apk add --no-cache curl jq >/dev/null 2>&1 || true
TDARR="${TDARR_URL:-http://localhost:8266}"
LIST="${LIST:-/kdk/tdarr-backlog.list}"
MIN_QUEUED="${MIN_QUEUED:-3}"; BATCH="${BATCH:-6}"
[ -s "$LIST" ] || { echo "backlog empty -- nothing to do"; exit 0; }
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
