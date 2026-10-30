---
title: Alerting standard (Pushover + email)
---

# Alerting standard (Pushover + email)

> This page was "Add a Pushover alert"; it is now the lab's alerting standard
> and still covers how to add one. Inbound links to `pushover-alerts.md` are
> unchanged.

## The standard

**Channels.** Two transports, both terminating at Alertmanager on
[[kdk-mon-01]] (`stacks/monitoring/alertmanager/alertmanager.yml`):

| Channel | Gets | Why |
|---|---|---|
| **Pushover** | **every** alert, firing and resolved | SaaS, shares no failure domain with the lab; the channel that must always work |
| **Email** | **`severity=critical` only**, additionally | a second, durable record of the things that matter most. Through the in-lab Proton bridge (`10.1.20.20:25`); it is **not** a digest |

There is no daily digest and email never replaces Pushover. A critical alert is
delivered to **both** (the `email-ops` receiver carries a Pushover config and an
email config), so email going down never loses a page.

**Severity.** Every rule carries one:

| `severity` | Meaning | Routing |
|---|---|---|
| `critical` | something is broken or unsafe now; act | Pushover (high) **+ email** |
| `warning` | degraded or suspicious; look soon | Pushover (high) |
| `info` | expected while you work on X; a quiet record | Pushover **quiet** (priority -1, repeat 24 h) |

**`source` label.** Set on every rule; Alertmanager routes on it:

- `source: loki` — a log-based rule evaluated by the Loki ruler on
  [[kdk-log-01]] (`stacks/loki/rules/*.yml`).
- `source: external-monitor` — a metric rule on kdk-mon-01's Prometheus
  (`stacks/monitoring/prometheus/rules/*.yml`).

**Where rules live and how routing picks them up.**

| Kind | Lives in | Evaluated by | Delivered to AM |
|---|---|---|---|
| Metric threshold/`up` | `stacks/monitoring/prometheus/rules/*.yml` | Prometheus (kdk-mon-01) | internal `alertmanager:9093` |
| Log pattern (LogQL) | `stacks/loki/rules/*.yml` | Loki ruler (kdk-log-01) | published `10.1.20.30:9093` |

The alerting pipeline watches itself: `stacks/monitoring/prometheus/rules/alerting-pipeline.yml`
pages (`AlertmanagerDown`, `LokiRulerCannotNotify`, `LokiRulerNoAlertmanager`,
`LokiRulerConfigReloadFailed`) if the published :9093 or the Loki ruler's
delivery path breaks — the gap that hid a 1.5 h outage on 2026-10-30
(hosts/README.md "Legacy boot units").

**How to add a rule:** a metric rule → section **C** below; a log rule →
[Add a Loki alert rule](add-loki-alert-rule.md). Pick `severity` from the table,
always set `source`, and give a `runbook` annotation.

---

There are three places an alert can come from. Pick by what you're alerting on.

## A. A container that crashed or went unhealthy — nothing to do

Every compose host runs `observability-agent`, whose `docker-event-watch`
service tails the Docker event stream and pages on any compose service that
dies with a non-graceful exit (not `0`/`143`) or reports `unhealthy`. New
services are covered automatically. A container Komodo recreates can exit 137
(SIGKILL after the stop timeout) and page as "container down": that is a
recreate, not a crash. The watcher also sends a quiet "kdk container watch
online" when it starts, and logs (never pages) every `create` and image `pull`
for the Loki rules. To get the *unhealthy* signal, give the
service a `healthcheck:` in its compose. The watchdog deliberately ignores
ad-hoc `docker run` containers (no compose project) and one-shot `backup-*`
stacks, so those don't page from here.

## B. A custom condition on a schedule — add to a verify one-shot

Point-in-time checks (an endpoint down, a count that rose, a file that
shouldn't exist) belong in a **verify** stack: an alpine one-shot that runs the
checks and pages, driven by a Komodo procedure on a cron. Two exist to copy
from: `stacks/backup-verify` and `stacks/tdarr-verify`.

1. Add checks to the stack's `verify.sh`. Keep it **silent when clean** — only
   POST on a real failure, or it becomes hourly noise. Compare against a
   baseline file under `/etc/kdk/` when alerting on a *rise* rather than a raw
   count.

   ```sh
   . /pushover.env
   code=$(curl -s -m 10 -o /dev/null -w '%{http_code}' http://10.1.1.130:8484/healthz)
   if [ "$code" != "200" ]; then
     curl -s -o /dev/null https://api.pushover.net/1/messages.json \
       --form-string token="$PUSHOVER_TOKEN" --form-string user="$PUSHOVER_USER" \
       --form-string title="kdk DEE wrapper down" \
       --form-string message="healthz returned $code" --form-string priority="1"
   fi
   ```

2. Mount creds into the one-shot in `compose.yaml`:

   ```yaml
   volumes:
     - /etc/kdk/pushover.env:/pushover.env:ro
     - ./verify.sh:/verify.sh:ro
   ```

3. Schedule it with a Komodo `[[procedure]]` on a cron. **Avoid the
   `gitops-sync` slots** (`0 */15` — every :00/:15/:30/:45): a concurrent
   `RunSync` holds the stack lock and your run fails "Resource is busy". Land it
   off those minutes, e.g. `0 40 * * * *`. Use `schedule_format = "Cron"` (the
   default "English" format mis-parses a cron string and fires erratically).

Then push — see [Deploy a new service](deploy-new-service.md) for the GitOps
mechanics.

## C. A metric threshold — Alertmanager on kdk-mon-01

Rate/threshold alerts over Prometheus metrics (CPU, disk, memory, scrape-down)
are Alertmanager's job, not a shell POST. That stack (`stacks/monitoring` on
`kdk-mon-01`) reads Pushover **and** the Proton bridge SMTP password as secret
**files** (`alertmanager/pushover-user-key`, `pushover-app-token`,
`proton-bridge-smtp-password`), not the env file — add a rule to its Prometheus
rules (`stacks/monitoring/prometheus/rules/*.yml`, `source: external-monitor`)
and, if a new route is needed, a route in the Alertmanager config there. A
`severity: critical` rule is e-mailed automatically; no routing change needed.
Keep `kdk-mon-01` lean; heavy collectors belong on `kdk-dkr-01`.

## Komodo manual deploy

`KomodoManualDeploy` (`stacks/loki/rules/komodo.yml`, `severity: info`) is a
quiet note that a **person** ran `DeployStack`/`RestartStack` instead of letting
a repo push reconcile the fleet through gitops-sync. The signal is Komodo Core's
`EXECUTE REQUEST … method="DeployStack" … username="…"` log line
(`container="komodo-core-1"` on kdk-dkr-01): the gitops `deploy-changed` action
authenticates as `username="Action"`, so a line whose method is a deploy and
whose username is **not** `"Action"` is a hand deploy. Heuristic limits, stated
honestly: it keys on the username string, so a future non-human automation that
authenticates as a named user would also show here, and a hand deploy whose
operator somehow logged as `"Action"` would be missed. Expected while you deploy
by hand; otherwise make the stack's on-host files match the repo so the next
gitops tick does not revert your change.

## Test it

Fire a one-off Pushover from the host to confirm creds and reachability before
relying on the logic:

```sh
ssh kdkadmin@<host> "set -a; . /etc/kdk/pushover.env; \
  curl -s -o /dev/null -w '%{http_code}\n' https://api.pushover.net/1/messages.json \
  --form-string token=\$PUSHOVER_TOKEN --form-string user=\$PUSHOVER_USER \
  --form-string title='kdk test' --form-string message='ignore' --form-string priority=-1"
```

A `200` means delivered. Use priority `-1` for tests so it doesn't buzz.

To test the Alertmanager routing end-to-end (both channels), post a fake alert
and watch for a Pushover **and**, when `severity=critical`, an email:

```sh
# critical -> Pushover + email ; expires on its own
ssh kdkadmin@10.1.20.30 'sudo docker exec alertmanager-ext amtool \
  --alertmanager.url=http://127.0.0.1:9093 alert add RouteTest \
  source=external-monitor severity=critical \
  --annotation=summary="route test" --end=$(date -u -d +5min +%FT%TZ)'
```

`amtool check-config /etc/alertmanager/alertmanager.yml` (inside the container)
validates a config change before it is deployed.
