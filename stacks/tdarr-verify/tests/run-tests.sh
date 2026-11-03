#!/bin/sh
# Offline test harness for stacks/tdarr-verify/lib.sh -- the DEE health
# classification, the WARN-vs-FAIL rule, and the fingerprint de-duplication.
# No network, no Tdarr, no VM: `curl` and `push` are stubbed, /healthz bodies
# are fixtures, the alert state and "now" are injected. POSIX sh (run under
# dash and busybox sh). Usage: sh run-tests.sh
set -u
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
LIB="$HERE/../lib.sh"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
PASS=0; FAIL=0
ok()   { PASS=$((PASS+1)); }
bad()  { FAIL=$((FAIL+1)); printf 'FAIL: %s\n' "$1"; }
check() {  # $1 desc  $2 got  $3 want
  if [ "$2" = "$3" ]; then ok; else bad "$1 (got [$2] want [$3])"; fi
}
contains() {  # $1 desc  $2 haystack  $3 needle
  case "$2" in *"$3"*) ok;; *) bad "$1 (missing [$3] in [$2])";; esac
}

# --- stubs -------------------------------------------------------------------
# push records each call as "priority|title|message" (one line, \n kept literal).
PUSH_LOG="$WORK/push.log"
: > "$PUSH_LOG"
push() { printf '%s|%s|%s\n' "$1" "$2" "$3" >> "$PUSH_LOG"; }
push_count() { wc -l < "$PUSH_LOG" | tr -d ' '; }
push_last()  { tail -1 "$PUSH_LOG"; }
push_reset() { : > "$PUSH_LOG"; }

# curl stub: returns the fixture body + "\n<code>" for the requested DEE URL,
# matching `curl ... -w '\n%{http_code}' <url>`. Driven by CURL_* globals.
CURL_DEE1_BODY=""; CURL_DEE1_CODE="200"
CURL_DEE2_BODY=""; CURL_DEE2_CODE="200"
curl() {
  _url=""
  for _a in "$@"; do _url=$_a; done   # last arg = URL
  case "$_url" in
    *10.1.1.130*) printf '%s\n%s' "$CURL_DEE1_BODY" "$CURL_DEE1_CODE" ;;
    *10.1.1.131*) printf '%s\n%s' "$CURL_DEE2_BODY" "$CURL_DEE2_CODE" ;;
    *) printf '\n000' ;;
  esac
}

OK_BODY='{"status":"ok","deezy_present":true,"exchange_reachable":true,"exchange_free_gb":72484.5,"work_free_gb":74.9,"min_free_gb":20}'
LOWDISK_BODY='{"status":"degraded","deezy_present":true,"exchange_reachable":true,"exchange_free_gb":72484.5,"work_free_gb":20.0,"min_free_gb":20}'
# same problem, different (changing) free-GB number -- must fingerprint identically
LOWDISK_BODY2='{"status":"degraded","deezy_present":true,"exchange_reachable":true,"exchange_free_gb":72010.1,"work_free_gb":12.3,"min_free_gb":20}'
EXCHANGE_BODY='{"status":"degraded","deezy_present":true,"exchange_reachable":false,"work_free_gb":74.9,"min_free_gb":20}'

ALERTS="$WORK/alerts"
export TDARR_VERIFY_ALERTS="$ALERTS"
. "$LIB"

reset_state() { : > "$ALERTS"; push_reset; }

DEE1=http://10.1.1.130:8484/healthz
DEE2=http://10.1.1.131:8484/healthz

# ============================================================================
# 1. dee_classify
# ============================================================================
check "classify ok -> healthy (empty)" "$(dee_classify kdk-dee-01 200 "$OK_BODY")" ""

res=$(dee_classify kdk-dee-01 200 "$LOWDISK_BODY")
check "classify low-disk status" "$(printf '%s' "$res" | cut -f1)" "degraded"
check "classify low-disk reason" "$(printf '%s' "$res" | cut -f2)" "low-disk"
contains "classify low-disk message mentions C:" "$res" "low disk on C: (DeeZy temp?)"
contains "classify low-disk message has work_free" "$res" "work_free=20GB <= min_free=20GB floor"

res=$(dee_classify kdk-dee-01 200 "$EXCHANGE_BODY")
check "classify exchange reason" "$(printf '%s' "$res" | cut -f2)" "exchange-unreachable"

res=$(dee_classify kdk-dee-01 503 "")
check "classify http!=200 -> down" "$(printf '%s' "$res" | cut -f2)" "unreachable"

# ============================================================================
# 2. dee_check WARN-vs-FAIL + correlation flag
# ============================================================================
# one sick (low disk) + one healthy -> WARN, half capacity still up
reset_state; signals_init
CURL_DEE1_BODY="$LOWDISK_BODY"; CURL_DEE1_CODE=200
CURL_DEE2_BODY="$OK_BODY";      CURL_DEE2_CODE=200
dee_check "$DEE1" "$DEE2"
check "one sick: dee_up" "$dee_up" "1"
check "one sick: DEE_DOWN" "$DEE_DOWN" "0"
check "one sick: severity is warn" "$(cut -f2 "$_CUR_FILE")" "warn"
check "one sick: fingerprint" "$(cut -f1 "$_CUR_FILE")" "dee:kdk-dee-01:degraded:low-disk"

# both down -> FAIL
reset_state; signals_init
CURL_DEE1_BODY=""; CURL_DEE1_CODE=503
CURL_DEE2_BODY=""; CURL_DEE2_CODE=503
dee_check "$DEE1" "$DEE2"
check "both down: dee_up" "$dee_up" "0"
check "both down: DEE_DOWN" "$DEE_DOWN" "1"
check "both down: two signals" "$(wc -l < "$_CUR_FILE" | tr -d ' ')" "2"
check "both down: severity is fail" "$(cut -f2 "$_CUR_FILE" | sort -u)" "fail"

# both healthy -> no signals
reset_state; signals_init
CURL_DEE1_BODY="$OK_BODY"; CURL_DEE1_CODE=200
CURL_DEE2_BODY="$OK_BODY"; CURL_DEE2_CODE=200
dee_check "$DEE1" "$DEE2"
check "both ok: dee_up" "$dee_up" "2"
check "both ok: no signals" "$(wc -c < "$_CUR_FILE" | tr -d ' ')" "0"

# ============================================================================
# 3. finalize: de-duplication, 24h reminder, resolved, number-change immunity
# ============================================================================
T0=1793000000

# first sighting of a WARN -> one priority-0 page
reset_state; signals_init
TDARR_VERIFY_NOW=$T0
CURL_DEE1_BODY="$LOWDISK_BODY"; CURL_DEE1_CODE=200
CURL_DEE2_BODY="$OK_BODY";      CURL_DEE2_CODE=200
dee_check "$DEE1" "$DEE2"; finalize
check "new warn: one push" "$(push_count)" "1"
contains "new warn: priority 0" "$(push_last)" "0|kdk-lab Tdarr: attention"
contains "new warn: message body" "$(push_last)" "low disk on C:"

# second run, +1h, SAME problem but a different free-GB number -> suppressed
push_reset; signals_init
TDARR_VERIFY_NOW=$((T0 + 3600))
CURL_DEE1_BODY="$LOWDISK_BODY2"; CURL_DEE1_CODE=200
dee_check "$DEE1" "$DEE2"; finalize
check "unchanged warn (+1h, new GB number): no push" "$(push_count)" "0"

# third run, +25h from first notify -> daily reminder fires once
push_reset; signals_init
TDARR_VERIFY_NOW=$((T0 + 25 * 3600))
CURL_DEE1_BODY="$LOWDISK_BODY"; CURL_DEE1_CODE=200
dee_check "$DEE1" "$DEE2"; finalize
check "24h reminder: one push" "$(push_count)" "1"

# fourth run: problem cleared -> one priority-0 resolved
push_reset; signals_init
TDARR_VERIFY_NOW=$((T0 + 26 * 3600))
CURL_DEE1_BODY="$OK_BODY"; CURL_DEE1_CODE=200
dee_check "$DEE1" "$DEE2"; finalize
check "resolved: one push" "$(push_count)" "1"
contains "resolved: priority 0 recovered" "$(push_last)" "0|kdk-lab Tdarr: recovered"
contains "resolved: names the fingerprint" "$(push_last)" "dee:kdk-dee-01:degraded:low-disk cleared"

# fifth run: all clean, nothing outstanding -> silent
push_reset; signals_init
TDARR_VERIFY_NOW=$((T0 + 27 * 3600))
dee_check "$DEE1" "$DEE2"; finalize
check "clean: silent" "$(push_count)" "0"

# both-down FAIL pages at priority 1
reset_state; signals_init
TDARR_VERIFY_NOW=$T0
CURL_DEE1_BODY=""; CURL_DEE1_CODE=503
CURL_DEE2_BODY=""; CURL_DEE2_CODE=503
dee_check "$DEE1" "$DEE2"; finalize
check "both down: one push" "$(push_count)" "1"
contains "both down: priority 1 FAILED" "$(push_last)" "1|kdk-lab Tdarr FAILED"

# an edge warning does NOT repeat hourly, and clears silently (no resolved)
reset_state
signals_init; TDARR_VERIFY_NOW=$T0
add_signal "iso:present" warn edge "2 .iso disc image(s)"
finalize
check "edge warn: first push" "$(push_count)" "1"
push_reset; signals_init; TDARR_VERIFY_NOW=$((T0 + 3600))
add_signal "iso:present" warn edge "2 .iso disc image(s)"
finalize
check "edge warn: suppressed next hour" "$(push_count)" "0"
push_reset; signals_init; TDARR_VERIFY_NOW=$((T0 + 7200))
finalize   # iso gone
check "edge warn: clears silently (no resolved)" "$(push_count)" "0"

# a NEW fail while a warn is unchanged pages at priority 1 for the new problem
reset_state
signals_init; TDARR_VERIFY_NOW=$T0
add_signal "iso:present" warn edge "iso"
finalize; push_reset
signals_init; TDARR_VERIFY_NOW=$((T0 + 60))
add_signal "iso:present" warn edge "iso"
add_signal "transcode:rising" fail edge "TRANSCODE FAILURES +5"
finalize
check "new fail beside unchanged warn: one push" "$(push_count)" "1"
contains "new fail: priority 1" "$(push_last)" "1|kdk-lab Tdarr FAILED"

# ============================================================================
printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
