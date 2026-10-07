#!/bin/sh
# Per-host container watchdog: pages Pushover when a container crashes
# (die with a non-graceful exit code) or reports an unhealthy health status.
# Uses the Docker API (socket), so it is independent of the storage driver
# that breaks cAdvisor name resolution on this host. NODE_NAME comes from the
# container env; Pushover creds from the mounted /pushover.env.
set -u
apk add --no-cache curl >/dev/null 2>&1 || true
. /pushover.env
log() { echo "$(date '+%F %T') $*" >&2; }   # stderr = unbuffered, visible in docker logs
notify() {
  log "PUSH[$3]: $1 -- $2"
  curl -s -o /dev/null --max-time 10 https://api.pushover.net/1/messages.json \
    --form-string token="$PUSHOVER_TOKEN" --form-string user="$PUSHOVER_USER" \
    --form-string title="$1" --form-string message="$2" --form-string priority="${3:-1}"
}
notify "kdk container watch online" "Docker-event watcher started on ${NODE_NAME}." -1
while true; do
  docker events --filter type=container --filter event=die --filter event=health_status \
    --format '{{.Actor.Attributes.name}}|{{.Action}}|{{.Actor.Attributes.exitCode}}|{{index .Actor.Attributes "com.docker.compose.project"}}' 2>/dev/null |
  while IFS='|' read -r name action code project; do
    log "event name=$name action=$action code=$code project=$project"
    # Only alert on containers that are part of a long-running compose SERVICE.
    # Skip:
    #   - ad-hoc `docker run` (no compose project) -- diagnostics, one-off tools;
    #     these exit non-zero routinely and are not services.
    #   - one-shot backup stacks (backup-*) -- they exit by design; backup-verify
    #     is their dedicated alert and would otherwise double-page.
    case "$project" in
      "") log "  ignore: no compose project (ad-hoc container)"; continue ;;
      backup-*) log "  ignore: one-shot backup stack ($project)"; continue ;;
    esac
    case "$action" in
      die)
        # 0 = clean exit, 143 = SIGTERM (graceful stop / redeploy) -> ignore.
        case "${code:-0}" in
          0|143) : ;;
          *)
            # Crash-loop cooldown: at most one page per container per hour. A
            # restart:unless-stopped container that can't start (NUT 2026-10-07,
            # UPS card unreachable) otherwise pages every ~25s -- 525 pages in 12h.
            # Suppressed crashes are counted and reported with the next page.
            st="/tmp/die-${name}"; now=$(date +%s); last=0; n=0
            [ -f "$st" ] && read -r last n < "$st"
            if [ $((now - last)) -ge 3600 ]; then
              extra=""; [ "${n:-0}" -gt 0 ] && extra=" (+${n} more crashes in the last hour -- crash-looping)"
              notify "kdk container down: ${NODE_NAME}" "${name} exited (code ${code}) on ${NODE_NAME}${extra}" 1
              echo "$now 0" > "$st"
            else
              echo "$last $((n + 1))" > "$st"
              log "  die cooldown: ${name} (suppressed $((n + 1)) since last page)"
            fi ;;
        esac ;;
      *unhealthy*)
        # Debounce transient health flaps (e.g. fanctl BMC/IPMI blips recover in
        # ~30s): re-check after 60s in the background and only page if STILL
        # unhealthy. Backgrounded so the event loop keeps reading new events.
        ( sleep 60
          h=$(docker inspect -f '{{.State.Health.Status}}' "$name" 2>/dev/null || echo gone)
          if [ "$h" = "unhealthy" ]; then
            notify "kdk container unhealthy: ${NODE_NAME}" "${name} is unhealthy on ${NODE_NAME}" 1
          else
            log "  unhealthy debounced: ${name} recovered (now ${h})"
          fi
        ) & ;;
    esac
  done
  sleep 3   # reconnect if the events stream drops
done
