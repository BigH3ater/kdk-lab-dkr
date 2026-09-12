#!/bin/sh
# Per-host container watchdog: pages Pushover when a container crashes
# (die with a non-graceful exit code) or reports an unhealthy health status.
# Uses the Docker API (socket), so it is independent of the storage driver
# that breaks cAdvisor name resolution on this host. NODE_NAME comes from the
# container env; Pushover creds from the mounted /pushover.env.
set -u
apk add --no-cache curl >/dev/null 2>&1 || true
. /pushover.env
log() { echo "$(date '+%F %T') $*"; }
notify() {
  log "PUSH[$3]: $1 -- $2"
  curl -s -o /dev/null --max-time 10 https://api.pushover.net/1/messages.json \
    --form-string token="$PUSHOVER_TOKEN" --form-string user="$PUSHOVER_USER" \
    --form-string title="$1" --form-string message="$2" --form-string priority="${3:-1}"
}
notify "kdk container watch online" "Docker-event watcher started on ${NODE_NAME}." 0
while true; do
  docker events --filter type=container --filter event=die --filter event=health_status \
    --format '{{.Actor.Attributes.name}}|{{.Action}}|{{.Actor.Attributes.exitCode}}' 2>/dev/null |
  while IFS='|' read -r name action code; do
    log "event name=$name action=$action code=$code"
    case "$action" in
      die)
        # 0 = clean exit, 143 = SIGTERM (graceful stop / redeploy) -> ignore.
        case "${code:-0}" in
          0|143) : ;;
          *) notify "kdk container down: ${NODE_NAME}" "${name} exited (code ${code}) on ${NODE_NAME}" 1 ;;
        esac ;;
      *unhealthy*) notify "kdk container unhealthy: ${NODE_NAME}" "${name} is unhealthy on ${NODE_NAME}" 1 ;;
    esac
  done
  sleep 3   # reconnect if the events stream drops
done
