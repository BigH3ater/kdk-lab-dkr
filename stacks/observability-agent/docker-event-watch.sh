#!/bin/sh
# Per-host container watchdog: pages Pushover when a container crashes
# (die with a non-graceful exit code) or reports an unhealthy health status.
# It also LOGS (never pages) container `create` and image `pull` events with the
# image reference, so the Loki rules HostUnknownImageOnDmz and
# RequestAppsContainerChange (stacks/loki/rules/) can see what got pulled/run.
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
# Per-container rate limit for crash/unhealthy pages. A restart:unless-stopped
# container that can't start pages every ~25s otherwise (NUT 2026-10-07: 525
# pages in 12h; 2026-10-17: 896). First page is priority 1; while the problem
# persists, at most one priority-0 reminder per COOLDOWN with the suppressed
# count. State is per-kind in /tmp (a watcher restart re-arms the first page).
COOLDOWN=14400
page_limited() {   # $1 = state key, $2 = title, $3 = message
  st="/tmp/rl-$1"; now=$(date +%s); last=0; n=0
  [ -f "$st" ] && read -r last n < "$st"
  if [ ! -f "$st" ] || [ $((now - last)) -ge "$COOLDOWN" ]; then
    if [ "${n:-0}" -gt 0 ]; then
      notify "$2" "$3 (still failing: +${n} more since the last page)" 0
    else
      notify "$2" "$3" 1
    fi
    echo "$now 0" > "$st"
  else
    echo "$last $((n + 1))" > "$st"
    log "  rate-limited: $1 (suppressed $((n + 1)) since last page)"
  fi
}
notify "kdk container watch online" "Docker-event watcher started on ${NODE_NAME}." -1
while true; do
  # Same-key filters OR, different keys AND: (container|image) x (die|health_status|create|pull).
  docker events --filter type=container --filter type=image \
    --filter event=die --filter event=health_status --filter event=create --filter event=pull \
    --format '{{.Actor.Attributes.name}}|{{.Action}}|{{.Actor.Attributes.exitCode}}|{{index .Actor.Attributes "com.docker.compose.project"}}|{{.Type}}|{{.Actor.Attributes.image}}|{{.Actor.ID}}' 2>/dev/null |
  while IFS='|' read -r name action code project etype image actor; do
    # Image pull: log the reference for Loki, nothing else (Actor.ID = the ref).
    if [ "$etype" = "image" ]; then
      log "event type=image action=$action image=$actor"
      continue
    fi
    log "event name=$name action=$action code=$code project=$project image=$image"
    # Container create: logged above for Loki, never paged.
    [ "$action" = "create" ] && continue
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
          *) page_limited "die-${name}" "kdk container down: ${NODE_NAME}" \
               "${name} exited (code ${code}) on ${NODE_NAME}" ;;
        esac ;;
      *unhealthy*)
        # Debounce transient health flaps (e.g. fanctl BMC/IPMI blips recover in
        # ~30s): re-check after 60s in the background and only page if STILL
        # unhealthy. Backgrounded so the event loop keeps reading new events.
        ( sleep 60
          h=$(docker inspect -f '{{.State.Health.Status}}' "$name" 2>/dev/null || echo gone)
          if [ "$h" = "unhealthy" ]; then
            page_limited "unhealthy-${name}" "kdk container unhealthy: ${NODE_NAME}" \
              "${name} is unhealthy on ${NODE_NAME}"
          else
            log "  unhealthy debounced: ${name} recovered (now ${h})"
          fi
        ) & ;;
    esac
  done
  sleep 3   # reconnect if the events stream drops
done
