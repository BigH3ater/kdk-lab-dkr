#!/bin/sh
# Runs inside tecnativa/docker-socket-proxy after the image entrypoint rendered
# /tmp/haproxy.cfg. Serves the GET-only Docker API to `scan` and `report`, and
# exits once trivy-scan-report has finished so the one-shot stack ends fully
# stopped (Komodo state "stopped"; deploy-changed then leaves it alone).
# Exit 0 always: docker-event-watch ignores clean exits.
set -u
haproxy -W -db -f /tmp/haproxy.cfg &
pid=$!
max=${PROXY_MAX_SECONDS:-10800}   # hard stop after 3 h whatever happens
waited=0
sleep 20
while [ "$waited" -lt "$max" ]; do
  kill -0 "$pid" 2>/dev/null || break
  state=$(wget -qO- http://127.0.0.1:2375/containers/trivy-scan-report/json 2>/dev/null \
    | grep -o '"Status":"[a-z]*"' | head -n1)
  case "$state" in
    *exited*|*dead*) break ;;
  esac
  sleep 15
  waited=$((waited + 15))
done
kill "$pid" 2>/dev/null
wait "$pid" 2>/dev/null
exit 0
