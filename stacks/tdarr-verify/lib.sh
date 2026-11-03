# shellcheck shell=sh
# Shared helpers for tdarr-verify: DEE /healthz classification, alert signals,
# and fingerprint de-duplication. Sourced by verify.sh (mounted at /lib.sh in
# the container) and by the offline test harness (stacks/tdarr-verify/tests).
# POSIX sh (alpine /bin/sh, busybox). Defines functions only -- no side effects
# at source time, so a test can source it, stub `curl` and `push`, and drive it.
#
# Alert model (2026-11-03):
#   * A problem is a SIGNAL with a stable FINGERPRINT, a severity (fail|warn)
#     and a kind (sticky|edge). The fingerprint keys on the problem's identity
#     (e.g. host + status + reason) and never on changing numbers (free-GB),
#     so the same ongoing problem fingerprints identically every run.
#   * A signal is NOTIFIED only when its fingerprint is new, has changed, or has
#     been continuously present for 24h (one daily reminder) -- so an unchanged
#     warning never repeats hourly (the kdk-dee-01 low-disk page every hour on
#     2026-11-03).
#   * When a previously alerted STICKY fingerprint clears, one priority-0
#     "resolved" message is sent. EDGE fingerprints (one-shot rises, per-file
#     fix/quarantine results) clear silently.

: "${TDARR_VERIFY_ALERTS:=/kdk/tdarr-verify.alerts}"
ALERT_REMINDER_SECS=86400   # re-notify an unchanged sticky problem once a day
TAB=$(printf '\t')

now_epoch() { echo "${TDARR_VERIFY_NOW:-$(date +%s)}"; }

# --- signal collection --------------------------------------------------------
# _CUR_FILE holds this run's active signals, one per line:
#   fp <TAB> sev <TAB> kind <TAB> message
# The message carries literal backslash-n for line breaks; `push` expands them
# with printf '%b' at send time (matching the pre-existing verify.sh style).
signals_init() { _CUR_FILE=$(mktemp); }

add_signal() {  # $1 fp  $2 sev(fail|warn)  $3 kind(sticky|edge)  $4 message
  printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" >> "$_CUR_FILE"
}

# --- DEE /healthz classification ---------------------------------------------
# float "a <= b"; a "?" (unknown) is never treated as at-or-below. We compare
# with <= (not <) because /healthz rounds the *_free_gb numbers to one decimal:
# the 2026-11-03 incident reported work_free_gb=20.0 against min_free_gb=20 for
# an actual ~19.9 GB, so a strict < would miss the very case that paged.
_le() {  # $1 a  $2 b  -> rc 0 if a <= b
  case "$1 $2" in *'?'*) return 1 ;; esac
  awk -v a="$1" -v b="$2" 'BEGIN{exit !(a<=b)}'
}

# Classify one DEE /healthz result into "status<TAB>reason<TAB>message", or ""
# when the wrapper is healthy. Reason is a stable token (never a GB number).
dee_classify() {  # $1 host  $2 http_code  $3 body
  _h=$1; _code=$2; _body=$3
  if [ "$_code" != "200" ]; then
    printf 'down\tunreachable\tDEE wrapper DOWN (%s:8484 /healthz http=%s) -- restart the dee-wrapper task on %s' \
      "$_h" "$_code" "$_h"
    return
  fi
  if printf '%s' "$_body" | grep -q '"exchange_reachable":false'; then
    _ef=$(printf '%s' "$_body" | jq -r '.exchange_free_gb // "?"' 2>/dev/null)
    printf 'degraded\texchange-unreachable\tDEE dee-exchange SMB mount dropped (%s): exchange_free=%sGB' \
      "$_h" "$_ef"
    return
  fi
  if printf '%s' "$_body" | grep -q '"status":"ok"'; then
    return   # healthy
  fi
  # Reachable but degraded => a drive is at/under min_free (the wrapper's only
  # other degraded reason). Name which drive; fingerprint reason stays stable.
  _wf=$(printf '%s' "$_body" | jq -r '.work_free_gb // "?"' 2>/dev/null)
  _ef=$(printf '%s' "$_body" | jq -r '.exchange_free_gb // "?"' 2>/dev/null)
  _mf=$(printf '%s' "$_body" | jq -r '.min_free_gb // "?"' 2>/dev/null)
  if _le "$_wf" "$_mf"; then
    printf 'degraded\tlow-disk\tDEE wrapper degraded (%s): low disk on C: (DeeZy temp?) work_free=%sGB <= min_free=%sGB floor (exchange_free=%sGB)' \
      "$_h" "$_wf" "$_mf" "$_ef"
  elif _le "$_ef" "$_mf"; then
    printf 'degraded\tlow-disk\tDEE wrapper degraded (%s): low disk on exchange volume, exchange_free=%sGB <= min_free=%sGB floor (work_free=%sGB)' \
      "$_h" "$_ef" "$_mf" "$_wf"
  else
    printf 'degraded\tother\tDEE wrapper degraded (%s): status!=ok (work_free=%sGB exchange_free=%sGB min_free=%sGB)' \
      "$_h" "$_wf" "$_ef" "$_mf"
  fi
}

# Probe both DEE wrappers, then emit DEE signals. Severity depends on how many
# wrappers are healthy: with at least one slot up (dee_up>=1) a single sick
# wrapper is a WARN (Tdarr keeps working at half capacity); only with NO slot
# up is it a FAIL. Sets dee_up and DEE_DOWN for the caller's transcode
# correlation.
dee_check() {  # $1 dee1_url  $2 dee2_url  (labels fixed to the two known hosts)
  _stash=$(mktemp)
  dee_up=0
  _probe() {  # $1 label  $2 url
    _r=$(curl -s -m 10 -w '\n%{http_code}' "$2" 2>/dev/null)
    _code=$(printf '%s' "$_r" | tail -1)
    _body=$(printf '%s' "$_r" | sed '$d')
    _res=$(dee_classify "$1" "$_code" "$_body")
    if [ -z "$_res" ]; then
      dee_up=$((dee_up + 1))
    else
      printf '%s\t%s\n' "$1" "$_res" >> "$_stash"
    fi
  }
  _probe "kdk-dee-01" "$1"
  _probe "kdk-dee-02" "$2"

  if [ "$dee_up" -eq 0 ]; then _sev=fail; else _sev=warn; fi
  while IFS="$TAB" read -r _host _status _reason _msg; do
    [ -z "$_host" ] && continue
    add_signal "dee:$_host:$_status:$_reason" "$_sev" sticky "$_msg"
  done < "$_stash"
  rm -f "$_stash"

  DEE_DOWN=0
  [ "$dee_up" -eq 0 ] && DEE_DOWN=1
}

# --- de-duplication + send ----------------------------------------------------
# Diff this run's signals (_CUR_FILE) against the persisted alert state, send a
# single Pushover message for the notable ones via `push` (defined by the
# caller / stubbed by the test), then rewrite the state. State line format:
#   fp <TAB> first_seen <TAB> last_notified <TAB> kind <TAB> sev
finalize() {
  _now=$(now_epoch)
  _old="$TDARR_VERIFY_ALERTS"
  [ -f "$_old" ] || : > "$_old"
  _new=$(mktemp)
  : > "$_new"
  _nfail=""; _nwarn=""; _resolved=""

  # 1. Walk this run's signals; decide notable, carry timestamps forward.
  if [ -s "$_CUR_FILE" ]; then
    while IFS="$TAB" read -r _fp _sev _kind _msg; do
      [ -z "$_fp" ] && continue
      _oldline=$(awk -F"$TAB" -v fp="$_fp" '$1==fp{print; exit}' "$_old")
      if [ -z "$_oldline" ]; then
        _first=$_now; _last=$_now; _notable=1
      else
        _first=$(printf '%s' "$_oldline" | cut -f2)
        _last=$(printf '%s' "$_oldline" | cut -f3)
        if [ $((_now - _last)) -ge "$ALERT_REMINDER_SECS" ]; then
          _last=$_now; _notable=1
        else
          _notable=0
        fi
      fi
      printf '%s\t%s\t%s\t%s\t%s\n' "$_fp" "$_first" "$_last" "$_kind" "$_sev" >> "$_new"
      if [ "$_notable" = 1 ]; then
        if [ "$_sev" = fail ]; then _nfail="$_nfail\n- $_msg"; else _nwarn="$_nwarn\n- $_msg"; fi
      fi
    done < "$_CUR_FILE"
  fi

  # 2. Resolved: sticky fingerprints that were active last run but not now.
  while IFS="$TAB" read -r _fp _f _l _kind _sev; do
    [ -z "$_fp" ] && continue
    if ! awk -F"$TAB" -v fp="$_fp" '$1==fp{f=1} END{exit !f}' "$_CUR_FILE" 2>/dev/null; then
      [ "$_kind" = sticky ] && _resolved="$_resolved\n- $_fp cleared"
    fi
  done < "$_old"

  # 3. One message at the highest notable severity (resolved folded in).
  if [ -n "$_nfail" ]; then
    push 1 "kdk-lab Tdarr FAILED" "FAIL:$_nfail${_nwarn:+\nWARN:$_nwarn}${_resolved:+\nRESOLVED:$_resolved}"
  elif [ -n "$_nwarn" ]; then
    push 0 "kdk-lab Tdarr: attention" "WARN:$_nwarn${_resolved:+\nRESOLVED:$_resolved}"
  elif [ -n "$_resolved" ]; then
    push 0 "kdk-lab Tdarr: recovered" "RESOLVED:$_resolved"
  fi

  mv "$_new" "$_old"
  rm -f "$_CUR_FILE"
}
