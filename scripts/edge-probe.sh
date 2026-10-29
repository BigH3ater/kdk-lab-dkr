#!/usr/bin/env bash
# Probe the Cloudflare edge for the public kmkdp.com hostnames and print a
# PASS/FAIL table. Read-only (GET/HEAD, plus bogus-credential POSTs for the
# login and rate-limit checks); needs bash, curl, python3. No secrets.
#
# The lab's split DNS points *.kmkdp.com at the DMZ Traefik, so every request is
# pinned to the Cloudflare edge address that public DNS (DoH) returns. Run it
# from the laptop on the LAN, the tailnet, or anywhere outside: the edge sees
# your public IP either way.
#
# Usage: scripts/edge-probe.sh [--no-ratelimit]
#   JF_STREAM_ID=<jellyfin item id>  optional: also probe a real item's stream
#                                    (HEAD only) with and without a bogus key
#
# The rate-limit bursts POST a nonexistent user to the Jellyfin and Authelia
# login endpoints (expect failed-login lines in Loki) and get this IP blocked
# on those paths for 10 s. Since security batch 5 the Authelia burst also trips
# Authelia's own IP regulation: the address you run from is banned from sso for
# 5 minutes. Prefer --no-ratelimit; lift a ban on kdk-dkr-dmz-01 with
# `sudo docker exec authelia authelia storage bans ip revoke <ip>`.
#
# What the checks verify: vault note "Cloudflare edge rules (2026-10)"
# (10.Homelab/Security). Exits 1 if any row is FAIL. Needs curl >= 7.84.
set -uo pipefail

RATELIMIT=1
for a in "$@"; do
  case "$a" in
    --no-ratelimit) RATELIMIT=0 ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    *) echo "unknown argument: $a" >&2; exit 2 ;;
  esac
done

JF=jellyfin.kmkdp.com
SSO=sso.kmkdp.com
WZ=wizarr.kmkdp.com
APEX=kmkdp.com
ZERO=00000000000000000000000000000000   # syntactically valid, non-existent item id
UA="kdk-edge-probe/1 (+scripts/edge-probe.sh)"

PASS=0 FAIL=0 SKIP=0
ROWS=()
row() { # status check detail
  case "$1" in PASS) PASS=$((PASS+1));; FAIL) FAIL=$((FAIL+1));; *) SKIP=$((SKIP+1));; esac
  ROWS+=("$(printf '%-5s | %-58s | %s' "$1" "$2" "$3")")
}

# --- public DNS (DoH) -------------------------------------------------------
doh() { # name type -> answers, one per line
  curl -fsS -m 10 -H 'accept: application/dns-json' \
    "https://cloudflare-dns.com/dns-query?name=$1&type=$2" 2>/dev/null |
    python3 -I -c '
import json, sys
want = {"A": 1, "CAA": 257}[sys.argv[1]]
try:
    for a in json.load(sys.stdin).get("Answer", []):
        if a.get("type") == want:
            print(a["data"])
except Exception:
    pass' "$2"
}
declare -A EDGE
for h in "$JF" "$SSO" "$WZ" "$APEX"; do
  EDGE[$h]=$(doh "$h" A | head -n1)
done

# --- HTTP helpers (all pinned to the edge IP) --------------------------------
# req METHOD HOST PATH [extra curl args...] -> prints "code server"
req() {
  local m=$1 h=$2 p=$3; shift 3
  local ip=${EDGE[$h]}
  local -a mflag=(-X "$m")
  [ "$m" = HEAD ] && mflag=(-I)
  curl -sS -o /dev/null -m 20 -A "$UA" "${mflag[@]}" \
    --resolve "$h:443:$ip" -w '%{http_code} %header{server}' "$@" \
    "https://$h$p" 2>/dev/null || echo "000 -"
}
code_of() { echo "${1%% *}"; }

# expect HOST LABEL METHOD PATH WANT [curl args]; WANT is "403", "!403", or "200|401|..."
expect() {
  local h=$1 label=$2 m=$3 p=$4 want=$5; shift 5
  if [ -z "${EDGE[$h]}" ]; then row SKIP "$label" "$h has no public A record"; return; fi
  local out c; out=$(req "$m" "$h" "$p" "$@"); c=$(code_of "$out")
  local ok=0
  case "$want" in
    '!403') [ "$c" != 403 ] && [ "$c" != 000 ] && [ "${c:0:1}" != 5 ] && ok=1 ;;
    *) [[ "|$want|" == *"|$c|"* ]] && ok=1 ;;
  esac
  if [ "$ok" = 1 ]; then row PASS "$label" "$c (want $want)"
  else row FAIL "$label" "$c (want $want)"; fi
}

# --- 1. reachability via Cloudflare ------------------------------------------
if [ -z "${EDGE[$JF]}" ]; then
  row FAIL "DoH A record for $JF" "none: cannot reach the edge"
else
  out=$(req GET "$JF" /health); c=$(code_of "$out")
  if [ "$c" = 200 ] && [ "${out#* }" = cloudflare ]; then row PASS "GET $JF/health via cloudflare" "$c server=cloudflare (edge ${EDGE[$JF]})"
  else row FAIL "GET $JF/health via cloudflare" "$out"; fi
fi
expect "$SSO" "GET $SSO/ (portal)" GET / 200

# --- 2. Jellyfin admin API blocked at the edge (rule "Block admin APIs") ------
for p in /System/Configuration /system/CONFIGURATION /System/Configuration/encoding \
         /System/Logs /System/Restart /System/Shutdown /System/Info/Storage \
         /System/ActivityLog/Entries /ScheduledTasks /Plugins /Packages /Repositories \
         /Devices /Startup/Configuration /Environment/DefaultDirectoryBrowser /Auth/Keys \
         /Library/VirtualFolders /Library/Refresh /Library/Media/Updated /Branding/Configuration \
         /Backup /Users /Users/new "/Users/$ZERO/Policy" "/Users/$ZERO/Password" \
         "/Items/$ZERO/Refresh" "/Sessions/$ZERO/Command" "/Sessions/$ZERO/Message" \
         /LiveTv/TunerHosts; do
  expect "$JF" "admin  GET $p" GET "$p" 403
done
expect "$JF" "admin  DELETE /Users/<id>" DELETE "/Users/$ZERO" 403
expect "$JF" "admin  DELETE /Items/<id>" DELETE "/Items/$ZERO" 403

# --- 3. Jellyfin client paths must NOT be blocked -----------------------------
expect "$JF" "client GET /System/Info/Public" GET /System/Info/Public 200
expect "$JF" "client GET /Users/Public" GET /Users/Public '!403'
expect "$JF" "client GET /System/Ping" GET /System/Ping '!403'
expect "$JF" "client GET /System/Endpoint (used by clients)" GET /System/Endpoint '!403'
expect "$JF" "client GET /System/Info (needs a token)" GET /System/Info 401
expect "$JF" "client GET /web/" GET /web/ '200|301|302'
expect "$JF" "client GET /Items (no token)" GET /Items 401
expect "$JF" "client GET /Branding/Css" GET /Branding/Css '!403'
expect "$JF" "client GET /Items/<id>/Images/Primary (anon image)" GET "/Items/$ZERO/Images/Primary" '!403'
expect "$JF" "client POST /QuickConnect/Initiate" POST /QuickConnect/Initiate '!403'
expect "$JF" "client DELETE /Users/<id>/FavoriteItems/<id> (unfav)" DELETE "/Users/$ZERO/FavoriteItems/$ZERO" '!403'
expect "$JF" "client GET subtitles .../Subtitles/0/Stream.vtt" GET "/Videos/$ZERO/$ZERO/Subtitles/0/Stream.vtt" '!403'

# --- 4. anonymous stream rule ("Block anonymous Jellyfin streams") -----------
expect "$JF" "stream HEAD /Videos/<zero>/stream (anon)" HEAD "/Videos/$ZERO/stream" 403
expect "$JF" "stream HEAD /Videos/<zero>/master.m3u8 (anon)" HEAD "/Videos/$ZERO/master.m3u8" 403
expect "$JF" "stream HEAD /Audio/<zero>/stream.mp3 (anon)" HEAD "/Audio/$ZERO/stream.mp3" 403
expect "$JF" "stream HEAD /Videos/<zero>/Trickplay/320/0.jpg (anon)" HEAD "/Videos/$ZERO/Trickplay/320/0.jpg" 403
expect "$JF" "stream HEAD /Videos/<zero>/stream?ApiKey=bogus" HEAD "/Videos/$ZERO/stream?ApiKey=bogus" '!403'
expect "$JF" "stream HEAD /Videos/<zero>/stream + MediaBrowser hdr" HEAD "/Videos/$ZERO/stream" '!403' \
  -H 'Authorization: MediaBrowser Client="edge-probe", Token="bogus"'
if [ -n "${JF_STREAM_ID:-}" ]; then
  expect "$JF" "stream HEAD /Videos/\$JF_STREAM_ID/stream (anon)" HEAD "/Videos/$JF_STREAM_ID/stream" 403
  expect "$JF" "stream HEAD /Videos/\$JF_STREAM_ID/stream?api_key=bogus" HEAD "/Videos/$JF_STREAM_ID/stream?api_key=bogus" '!403'
  # 200 here means Jellyfin serves the file to any key, valid or not: the edge
  # rule only stops credential-less requests (see the vault note, rule 2).
else
  row SKIP "stream real item (JF_STREAM_ID)" "JF_STREAM_ID not set"
fi

# --- 5. Authelia OIDC back-channel (never 403/429 from the edge) -------------
expect "$SSO" "oidc GET /.well-known/openid-configuration" GET /.well-known/openid-configuration 200
expect "$SSO" "oidc GET /jwks.json" GET /jwks.json 200
expect "$SSO" "oidc POST /api/oidc/token (no creds)" POST /api/oidc/token '400|401' \
  -H 'Content-Type: application/x-www-form-urlencoded' --data 'grant_type=authorization_code&code=bogus'
row SKIP "oidc paths from a non-US address" "cannot be tested from the US; check Security Events for the skip rule"

# --- 6. Wizarr (only once published on the tunnel) ---------------------------
expect "$WZ" "wizarr GET /j/TEST (invitee page)" GET /j/TEST 200
expect "$WZ" "wizarr GET /admin (edge block)" GET /admin 403
expect "$WZ" "wizarr GET /api/users (edge block)" GET /api/users 403

# --- 7. zone settings ---------------------------------------------------------
for h in "$SSO" "$JF"; do
  if [ -z "${EDGE[$h]}" ]; then row SKIP "HSTS on $h" "no A record"; continue; fi
  hdr=$(curl -sS -m 15 -o /dev/null -D - --resolve "$h:443:${EDGE[$h]}" -A "$UA" "https://$h/" 2>/dev/null | tr -d '\r')
  hsts=$(grep -i '^strict-transport-security:' <<<"$hdr" | head -n1)
  if grep -q 'max-age=15552000' <<<"$hsts" && ! grep -qi 'includesubdomains\|preload' <<<"$hsts"; then
    row PASS "HSTS on $h" "${hsts#*: }"
  else
    row FAIL "HSTS on $h (max-age=15552000, no subdomains/preload)" "${hsts:-header absent}"
  fi
  if grep -qi '^x-content-type-options: *nosniff' <<<"$hdr"; then row PASS "nosniff on $h" "present"
  else row FAIL "nosniff on $h" "header absent"; fi
done

for v in 1.0 1.1; do
  if [ -z "${EDGE[$SSO]}" ]; then row SKIP "TLS $v refused" "no A record"; continue; fi
  err=$(curl -sS -o /dev/null -m 15 --resolve "$SSO:443:${EDGE[$SSO]}" --tlsv"$v" --tls-max "$v" \
        --ciphers 'DEFAULT:@SECLEVEL=0' "https://$SSO/" 2>&1); rc=$?
  if [ $rc = 0 ]; then row FAIL "TLS $v handshake refused by the edge" "handshake succeeded (Minimum TLS < 1.2)"
  elif grep -qi 'protocol version\|alert\|handshake failure' <<<"$err"; then row PASS "TLS $v handshake refused by the edge" "refused (curl rc=$rc)"
  else row SKIP "TLS $v handshake refused by the edge" "local client cannot test: ${err:0:60}"; fi
done

if [ -z "${EDGE[$APEX]}" ]; then row SKIP "apex $APEX not dangling" "no A record"
else
  expect "$APEX" "apex https://$APEX/ (404 or redirect, not 522)" GET / '301|302|307|308|404'
fi

caa=$(doh "$APEX" CAA)
for ca in letsencrypt.org pki.goog; do
  if grep -q "issue \"$ca\"" <<<"$caa" && grep -q "issuewild \"$ca\"" <<<"$caa"; then row PASS "CAA issue+issuewild $ca" "present"
  else row FAIL "CAA issue+issuewild $ca" "${caa:+not both present}${caa:-no CAA records}"; fi
done

# --- 8. rate limit (last: it blocks this IP on the matched paths for 10 s) ---
burst() { # host path label
  # Fire 30 POSTs in parallel, wait for the edge counter to sync, then send 6
  # more one by one: the tail must contain a 429. Sequential-only bursts never
  # trip the rule on slow endpoints (Authelia hashes a bad password in ~1.2 s,
  # so 12 sequential attempts span ~15 s and no 10-s window holds >10), and a
  # parallel burst alone ends before the per-PoP counter syncs; 15 parallel was
  # not enough on the Authelia path, 30 is (2026-10-29).
  local h=$1 p=$2 label=$3 hits=0 c i codes="" body
  if [ -z "${EDGE[$h]}" ]; then row SKIP "$label" "no A record"; return; fi
  body='{"Username":"edge-probe-nonexistent","Pw":"x","username":"edge-probe-nonexistent","password":"x"}'
  for i in $(seq 1 30); do
    req POST "$h" "$p" -H 'Content-Type: application/json' --data "$body" >/dev/null 2>&1 &
  done
  wait
  sleep 4
  for i in $(seq 1 6); do
    c=$(code_of "$(req POST "$h" "$p" -H 'Content-Type: application/json' --data "$body")")
    codes+="$c "
    [ "$c" = 429 ] && hits=$((hits+1))
  done
  if [ $hits -gt 0 ]; then row PASS "$label" "$hits x 429 in the 6-request tail after a 30-request burst"
  else row FAIL "$label" "no 429 in the tail: ${codes% }"; fi
}
if [ "$RATELIMIT" = 1 ]; then
  burst "$JF" /Users/AuthenticateByName "ratelimit burst+tail POST /Users/AuthenticateByName"
  sleep 11
  burst "$JF" /users/authenticatebyname "ratelimit burst+tail POST /users/authenticatebyname (case)"
  sleep 11
  burst "$SSO" /api/firstfactor "ratelimit burst+tail POST $SSO/api/firstfactor"
else
  row SKIP "rate limit bursts" "--no-ratelimit"
fi

# --- 9. informational: client X-Forwarded-For must not reach the origin ------
if [ -n "${EDGE[$SSO]}" ]; then
  marker="edgeprobe$(date +%s)"
  req GET "$SSO" "/api/health?$marker" -H 'X-Forwarded-For: 203.0.113.9' >/dev/null
  row INFO "XFF strip (Transform Rule)" "check Traefik log for $marker: ClientHost must not be 203.0.113.9"
fi

# --- table -------------------------------------------------------------------
printf '%-5s | %-58s | %s\n' STATE CHECK DETAIL
printf '%s\n' "------+------------------------------------------------------------+-------------------------"
printf '%s\n' "${ROWS[@]}"
echo
echo "PASS=$PASS FAIL=$FAIL SKIP/INFO=$SKIP  (edge: jellyfin=${EDGE[$JF]:-none} sso=${EDGE[$SSO]:-none})"
[ "$FAIL" -eq 0 ]
