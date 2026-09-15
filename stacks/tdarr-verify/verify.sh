#!/bin/sh
# Tdarr transcode/health validation + alerting. Runs hourly (tdarr-verify
# procedure) on kdk-dkr-01 and pages Pushover on:
#   * DEE wrapper down (kdk-dee-01:8484 /healthz) -- dependency for all
#     lossless-audio (DTS-HD MA / TrueHD) transcodes; its 2026-09-11 outage
#     silently failed ~78 files. Correlated: if transcode errors are ALSO rising
#     while DEE is down, the alert names that exact scenario.
#   * Tdarr server unreachable (localhost:8266).
#   * Transcode-error count rising vs last run (names sample files).
#   * Health-check failures rising -- unreadable/corrupt files (FFprobe empty).
#   * .iso files in the library -- disc images that can't be transcoded and will
#     error (BR-DISK etc.); flagged so they can be excluded/converted/removed.
# FAIL -> Pushover priority 1, WARN -> priority 0, clean -> silent (no hourly
# spam). Baselines persist in /kdk/tdarr-verify.count as "te=.. he=.. iso=..".
apk add --no-cache curl jq >/dev/null 2>&1 || true
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
DEE_DOWN=0
if [ "$deecode" != "200" ]; then
  DEE_DOWN=1; add_prob "DEE wrapper DOWN (kdk-dee-01:8484 /healthz http=$deecode) -- restart the dee-wrapper task on kdk-dee-01"
elif ! printf '%s' "$deebody" | grep -q '"status":"ok"'; then
  DEE_DOWN=1; add_prob "DEE wrapper unhealthy: $deebody"
elif printf '%s' "$deebody" | grep -q '"exchange_reachable":false'; then
  DEE_DOWN=1; add_prob "DEE dee-exchange share unreachable: $deebody"
fi

# 2. One file-DB dump -> transcode-error / health-error / iso counts + samples
resp=$(curl -s -m 90 -X POST "$TDARR/api/v2/cruddb" -H 'content-type: application/json' \
  -d '{"data":{"collection":"FileJSONDB","mode":"getAll"}}' 2>/dev/null)
if [ -z "$resp" ] || ! printf '%s' "$resp" | jq -e . >/dev/null 2>&1; then
  add_prob "Tdarr server unreachable or bad response ($TDARR)"
  push() { curl -s --max-time 25 --form-string "token=$PUSHOVER_TOKEN" --form-string "user=$PUSHOVER_USER" \
    --form-string "priority=$1" --form-string "title=$2" --form-string "message=$(printf '%b' "$3")" \
    https://api.pushover.net/1/messages.json >/dev/null; }
  printf 'TDARR VALIDATION FAILED:%b\n' "$PROB"
  push 1 "kdk-lab Tdarr FAILED" "Tdarr validation failed:$PROB"
  exit 0
fi

te=$(printf '%s' "$resp" | jq '[.[]|select(.TranscodeDecisionMaker=="Transcode error")]|length')
he=$(printf '%s' "$resp" | jq '[.[]|select(.HealthCheck=="Error")]|length')
iso=$(printf '%s' "$resp" | jq '[.[]|select(.container=="iso")]|length')
te_files=$(printf '%s' "$resp" | jq -r '[.[]|select(.TranscodeDecisionMaker=="Transcode error")|.file]|.[0:6][]' | sed 's#.*/##')
he_files=$(printf '%s' "$resp" | jq -r '[.[]|select(.HealthCheck=="Error")|.file]|.[0:6][]' | sed 's#.*/##')
iso_files=$(printf '%s' "$resp" | jq -r '[.[]|select(.container=="iso")|.file]|.[0:6][]' | sed 's#.*/##')

# baselines
pte=0; phe=0; piso=0
[ -f "$STATE" ] && . "$STATE" 2>/dev/null
pte=${te_prev:-0}; phe=${he_prev:-0}; piso=${iso_prev:-0}
printf 'te_prev=%s\nhe_prev=%s\niso_prev=%s\n' "$te" "$he" "$iso" > "$STATE"
echo "transcode-error=$te (was $pte) | health-error=$he (was $phe) | iso=$iso (was $piso)"

# 3. Transcode errors rising -- with DEE-scenario correlation
if [ "$te" -gt "$pte" ] && [ $((te - pte)) -ge "$THRESH" ]; then
  d=$((te - pte)); list=$(printf '%s' "$te_files" | sed 's/^/    /')
  if [ "$DEE_DOWN" = 1 ]; then
    add_prob "TRANSCODE FAILURES +$d (now $te) WHILE DEE IS DOWN -- lossless-audio (DTS-HD MA/TrueHD) files erroring because the DEE wrapper is unavailable. Fix DEE first, then re-queue. Sample:\n$list"
  else
    add_prob "TRANSCODE FAILURES +$d (now $te), DEE is healthy -- check node/flow (av1-pipeline). Sample:\n$list"
  fi
fi

# 4. Health-check failures rising (unreadable/corrupt: FFprobe empty)
if [ "$he" -gt "$phe" ] && [ $((he - phe)) -ge "$THRESH" ]; then
  list=$(printf '%s' "$he_files" | sed 's/^/    /')
  add_warn "HEALTH-CHECK FAILURES +$((he - phe)) (now $he) -- unreadable/corrupt files. Sample:\n$list"
fi

# 5. .iso files present (can't be transcoded; will error) -- alert when new ones appear
if [ "$iso" -gt 0 ] && [ "$iso" -gt "$piso" ]; then
  list=$(printf '%s' "$iso_files" | sed 's/^/    /')
  add_warn "$iso .iso disc image(s) in library (can't transcode -- exclude/convert/remove):\n$list"
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
elif [ -n "$WARN" ]; then
  printf 'TDARR warnings:%b\n' "$WARN"
  push 0 "kdk-lab Tdarr: attention" "Warnings:$WARN"
else
  echo "TDARR OK -- DEE healthy, server up, transcode/health errors steady, no new .iso (te=$te he=$he iso=$iso)"
fi
