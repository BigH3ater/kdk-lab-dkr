#!/bin/sh
# Tdarr transcode-failure alert + validation. Runs hourly (tdarr-verify
# procedure) on kdk-dkr-01 and pages Pushover on:
#   * DEE wrapper down  -- kdk-dee-01:8484 /healthz not 200/ok. This is the
#     dependency for all lossless-audio (DTS-HD MA / TrueHD) transcodes; when it
#     died (2026-09-11) it silently failed ~78 files. THE recurring root cause.
#   * Tdarr server unreachable (localhost:8266).
#   * Transcode-error count rising -- new "Transcode error" files since the last
#     run (delta > threshold), which catches node/flow regressions.
# FAIL -> Pushover priority 1. WARN (new errors) -> priority 0. Clean -> silent
# (no hourly OK spam). Baseline persists in /kdk/tdarr-verify.count.
apk add --no-cache curl >/dev/null 2>&1 || true
. /kdk/pushover.env
DEE="http://10.1.1.130:8484/healthz"
TDARR="http://localhost:8266"
STATE=/kdk/tdarr-verify.count
THRESH=${TDARR_ERR_THRESHOLD:-1}
PROB=""; WARN=""
add_prob() { PROB="$PROB\n- $1"; }
add_warn() { WARN="$WARN\n- $1"; }

# 1. DEE wrapper health
dee=$(curl -s -m 10 -w '\n%{http_code}' "$DEE" 2>/dev/null)
deecode=$(printf '%s' "$dee" | tail -1)
deebody=$(printf '%s' "$dee" | sed '$d')
if [ "$deecode" != "200" ]; then
  add_prob "DEE wrapper DOWN (kdk-dee-01:8484 /healthz http=$deecode) -- lossless-audio transcodes will fail"
elif ! printf '%s' "$deebody" | grep -q '"status":"ok"'; then
  add_prob "DEE wrapper unhealthy: $deebody"
elif printf '%s' "$deebody" | grep -q '"exchange_reachable":false'; then
  add_prob "DEE dee-exchange share unreachable: $deebody"
fi

# 2. Tdarr server + current transcode-error count (grep the file DB dump)
resp=$(curl -s -m 60 -X POST "$TDARR/api/v2/cruddb" -H 'content-type: application/json' \
  -d '{"data":{"collection":"FileJSONDB","mode":"getAll"}}' 2>/dev/null)
if [ -z "$resp" ]; then
  add_prob "Tdarr server unreachable ($TDARR)"
  count=""
else
  count=$(printf '%s' "$resp" | grep -o '"TranscodeDecisionMaker":"Transcode error"' | wc -l | tr -d ' ')
fi

# 3. Transcode-error delta vs last run
if [ -n "$count" ]; then
  prev=$(cat "$STATE" 2>/dev/null | tr -d ' \n')
  echo "$count" > "$STATE"
  if [ -n "$prev" ] && [ "$count" -gt "$prev" ]; then
    delta=$((count - prev))
    [ "$delta" -ge "$THRESH" ] && add_warn "Transcode errors rose by $delta (now $count, was $prev)"
  fi
  echo "transcode-error count: $count (prev: ${prev:-none})"
fi

push() {
  curl -s --max-time 25 \
    --form-string "token=$PUSHOVER_TOKEN" --form-string "user=$PUSHOVER_USER" \
    --form-string "priority=$1" --form-string "title=$2" \
    --form-string "message=$(printf '%b' "$3")" \
    https://api.pushover.net/1/messages.json >/dev/null
}

if [ -n "$PROB" ]; then
  printf 'TDARR VALIDATION FAILED:%b%b\n' "$PROB" "$WARN"
  push 1 "kdk-lab Tdarr FAILED" "Tdarr validation failed:$PROB${WARN:+\nWARN:$WARN}"
  exit 0
elif [ -n "$WARN" ]; then
  printf 'TDARR warnings:%b\n' "$WARN"
  push 0 "kdk-lab Tdarr: new transcode errors" "Warnings:$WARN"
else
  echo "TDARR OK -- DEE healthy, server up, transcode errors steady (${count:-?})"
fi
