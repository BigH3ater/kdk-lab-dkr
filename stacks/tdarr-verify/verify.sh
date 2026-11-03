#!/bin/sh
# Tdarr transcode/health validation + alerting. Runs hourly (tdarr-verify
# procedure) on kdk-dkr-01 and pages Pushover on:
#   * DEE wrapper down/degraded (kdk-dee-01/-02:8484 /healthz; both single-slot
#     wrappers behind dee-lb are probed) -- dependency for all lossless-audio
#     (DTS-HD MA / TrueHD) transcodes; its 2026-09-11 outage silently failed
#     ~78 files. With ONE wrapper sick and the other healthy Tdarr keeps working
#     at half DEE capacity, so that is a WARN (priority 0); only with BOTH down
#     is it a FAIL (priority 1). Correlated: if transcode errors are ALSO rising
#     while DEE has no capacity, the alert names that exact scenario.
#   * Tdarr server unreachable (localhost:8266).
#   * Transcode-error count rising vs last run (names sample files).
#   * Health-check failures rising -- unreadable/corrupt files (FFprobe empty).
#   * .iso files in the library -- disc images that can't be transcoded and will
#     error (BR-DISK etc.); flagged so they can be excluded/converted/removed.
#   * Library MKVs changed in the last ~26h whose audio isn't interleaved from
#     the start -- Apple TV/Neptune direct play is silent on these (2026-10-04,
#     85 files from the old DEE remux). Check: audio share of the first 5000
#     packets (broken = 1; healthy ~2500). Byte-window piping was dropped: Tdarr's
#     mkvpropedit can move the Tracks element to the file end, which a pipe can't
#     reach (false positive on The Avengers, 2026-10-05).
#     AUTO-FIX (max 3/run): extract audio to a side file, mux video + side audio
#     with -max_interleave_delta 0 under a 6 GB memory cap (a two-input remux of
#     the same file buffers the gap and was OOM-killed at ~40 GB), re-verify,
#     then swap in and keep the original in /data/quarantine/interleave-backup.
#     Fixed -> WARN; fix failed (original untouched) -> FAIL.
#   * Files newly moved to /data/quarantine by the flow's FAIL branch -- these
#     never show up as "Transcode error" in the file DB.
#
# De-duplication (lib.sh): every alert is a fingerprinted SIGNAL. A signal is
# notified only when its fingerprint is new, has changed, or has been present
# 24h (one daily reminder), so an unchanged problem never repeats hourly (the
# kdk-dee-01 low-disk page every hour on 2026-11-03). A cleared STICKY problem
# (DEE health, Tdarr server) sends one priority-0 "resolved"; edge events (rises,
# per-file fix/quarantine results) clear silently. FAIL -> priority 1,
# WARN/resolved -> priority 0, nothing notable -> silent. Count baselines persist
# in /kdk/tdarr-verify.count; alert state in /kdk/tdarr-verify.alerts.
apk add --no-cache curl jq >/dev/null 2>&1 || true
. /kdk/pushover.env
. "$(dirname "$0")/lib.sh"
DEE1="http://10.1.1.130:8484/healthz"   # kdk-dee-01
DEE2="http://10.1.1.131:8484/healthz"   # kdk-dee-02
TDARR="http://localhost:8266"
STATE=/kdk/tdarr-verify.count
THRESH=${TDARR_ERR_THRESHOLD:-1}

push() {
  curl -s --max-time 25 \
    --form-string "token=$PUSHOVER_TOKEN" --form-string "user=$PUSHOVER_USER" \
    --form-string "priority=$1" --form-string "title=$2" \
    --form-string "message=$(printf '%b' "$3")" \
    https://api.pushover.net/1/messages.json >/dev/null
}

signals_init   # collect alert signals; finalize() de-dups + sends at the end

# Reference point for "since the last run" (STATE is rewritten every run).
LAST=/tmp/last-run; if [ -f "$STATE" ]; then touch -r "$STATE" "$LAST"; else touch -d '-1 hour' "$LAST"; fi

# 1. DEE wrapper health -- both single-slot wrappers behind dee-lb. One sick
#    wrapper with the other healthy is a WARN; both down is a FAIL. dee_check
#    sets dee_up and DEE_DOWN (the transcode-correlation flag used in section 3).
dee_check "$DEE1" "$DEE2"

# 2. One file-DB dump -> transcode-error / health-error / iso counts + samples
resp=$(curl -s -m 90 -X POST "$TDARR/api/v2/cruddb" -H 'content-type: application/json' \
  -d '{"data":{"collection":"FileJSONDB","mode":"getAll"}}' 2>/dev/null)
if [ -z "$resp" ] || ! printf '%s' "$resp" | jq -e . >/dev/null 2>&1; then
  add_signal "tdarr:server:unreachable" fail sticky \
    "Tdarr server unreachable or bad response ($TDARR)"
  finalize
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
    add_signal "transcode:rising:dee-down" fail edge \
      "TRANSCODE FAILURES +$d (now $te) WHILE DEE HAS NO CAPACITY -- lossless-audio (DTS-HD MA/TrueHD) files erroring because no DEE wrapper is available. Fix DEE first, then re-queue. Sample:\n$list"
  else
    add_signal "transcode:rising" fail edge \
      "TRANSCODE FAILURES +$d (now $te), DEE is healthy -- check node/flow (av1-pipeline). Sample:\n$list"
  fi
fi

# 4. Health-check failures rising (unreadable/corrupt: FFprobe empty)
if [ "$he" -gt "$phe" ] && [ $((he - phe)) -ge "$THRESH" ]; then
  list=$(printf '%s' "$he_files" | sed 's/^/    /')
  add_signal "health:rising" warn edge \
    "HEALTH-CHECK FAILURES +$((he - phe)) (now $he) -- unreadable/corrupt files. Sample:\n$list"
fi

# 5. .iso files present (can't be transcoded; will error) -- alert when new ones appear
if [ "$iso" -gt 0 ] && [ "$iso" -gt "$piso" ]; then
  list=$(printf '%s' "$iso_files" | sed 's/^/    /')
  add_signal "iso:present" warn edge \
    "$iso .iso disc image(s) in library (can't transcode -- exclude/convert/remove):\n$list"
fi

# 6. Recently changed library files: audio interleaved from the start? Auto-fix.
audio_share() {  # audio packets among the first 5000 packets of the file
  ai=$(ffprobe -v error -show_entries stream=index,codec_type -of csv=p=0 "$1" | awk -F, '$2=="audio"{printf "%s|",$1}')
  [ -z "$ai" ] && { echo 0; return; }
  ffprobe -v error -read_intervals '%+#5000' -show_entries packet=stream_index -of csv=p=0 "$1" | grep -cE "^(${ai%|})\$"
}
reinterleave() {  # $1 = library file; echoes a result line
  f="$1"; d=$(dirname "$f"); tmp="$d/.remux.tmp"; aud="$d/.remux.audio"
  (
    ulimit -v 6000000
    ffmpeg -nostdin -v error -y -i "$f" -map 0:a -c copy -f matroska "$aud" &&
    ffmpeg -nostdin -v error -y -i "$f" -i "$aud" -map 0:v -map 1:a -map '0:s?' \
      -map_metadata 0 -map_chapters 0 -c copy -max_interleave_delta 0 -f matroska "$tmp"
  ) >/dev/null 2>&1
  rc=$?; rm -f "$aud"
  if [ $rc -ne 0 ] || [ ! -s "$tmp" ]; then rm -f "$tmp"; echo "FAIL ffmpeg rc=$rc: ${f##*/}"; return; fi
  n=$(audio_share "$tmp")
  d0=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$f"); d1=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$tmp")
  s0=$(ffprobe -v error -show_entries stream=codec_type,codec_name -of csv=p=0 "$f" | sort | tr '\n' ' ')
  s1=$(ffprobe -v error -show_entries stream=codec_type,codec_name -of csv=p=0 "$tmp" | sort | tr '\n' ' ')
  if [ "$n" -ge 100 ] && [ "$s0" = "$s1" ] && awk -v a="$d0" -v b="$d1" 'BEGIN{exit !((a-b)^2<1)}'; then
    mkdir -p /media/quarantine/interleave-backup
    chown "$(stat -c %u:%g "$f")" "$tmp"; chmod "$(stat -c %a "$f")" "$tmp"
    mv "$f" /media/quarantine/interleave-backup/ && mv "$tmp" "$f" && echo "FIXED ($n audio/5000): ${f##*/}" && return
    echo "FAIL swap: ${f##*/}"; return
  fi
  rm -f "$tmp"; echo "FAIL verify (audio=$n dur $d0->$d1): ${f##*/}"
}
# Leftovers from an interrupted run (the container is recreated every hour).
find /media/movies /media/tv -maxdepth 3 \( -name '.remux.tmp' -o -name '.remux.audio' \) -mmin +120 -delete 2>/dev/null
recent=$(find /media/movies /media/tv -name '*.mkv' ! -name '.*' -mmin -1560 -mmin +5 2>/dev/null)
if [ -n "$recent" ]; then
  apk add --no-cache ffmpeg >/dev/null 2>&1 || true
  busy=$(curl -s -m 20 "$TDARR/api/v2/get-nodes" 2>/dev/null)
  deferred=""; budget=3
  while IFS= read -r f; do
    [ "$(audio_share "$f")" -ge 10 ] && continue
    base=${f##*/}
    case "$busy" in *"$base"*) deferred="$deferred\n    (busy in Tdarr) $base"; continue;; esac
    if [ "$budget" -le 0 ]; then deferred="$deferred\n    (next run) $base"; continue; fi
    budget=$((budget - 1)); r=$(reinterleave "$f"); echo "$r"
    case "$r" in
      FIXED*) add_signal "interleave-fixed:$base" warn edge \
                "Audio re-interleaved automatically (original in quarantine/interleave-backup): $r";;
      *)      add_signal "interleave-fail:$base" fail edge \
                "AUDIO NOT INTERLEAVED and auto-fix FAILED (original untouched; silent on Apple TV/Neptune): $r";;
    esac
  done <<EOF2
$recent
EOF2
  [ -n "$deferred" ] && add_signal "interleave:deferred" warn edge \
    "Non-interleaved audio, fix deferred:$deferred"
fi

# 7. Files the flow moved to quarantine since the last run
find /media/quarantine -type f -name '*.mkv' ! -path '*/interleave-backup/*' -newer "$LAST" 2>/dev/null | head -6 | while IFS= read -r qf; do
  qb=${qf##*/}
  add_signal "quarantine:$qb" warn edge \
    "Flow FAILed file moved to quarantine since last run (check the job report): $qb"
done

finalize
