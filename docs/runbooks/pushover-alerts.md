---
title: Add a Pushover alert
---

# Add a Pushover alert

All hosts push to the same Pushover application. Credentials live at
`/etc/kdk/pushover.env` on every compose host (out of git):

```sh
PUSHOVER_TOKEN=...
PUSHOVER_USER=...
```

The canonical send is a single form POST — priority `-1` quiet, `0` normal,
`1` high:

```sh
. /pushover.env
curl -s -o /dev/null --max-time 10 https://api.pushover.net/1/messages.json \
  --form-string token="$PUSHOVER_TOKEN" --form-string user="$PUSHOVER_USER" \
  --form-string title="kdk <what>" --form-string message="<detail>" \
  --form-string priority="1"
```

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
`kdk-mon-01`) reads Pushover as secret **files**
(`alertmanager/pushover-user-key`, `pushover-app-token`), not the env file — add
a rule to its Prometheus rules and a route in the Alertmanager config there.
Keep `kdk-mon-01` lean; heavy collectors belong on `kdk-dkr-01`.

The three Prometheus rule files each carry a `scope` label (= the file) and a
`runbook` annotation pointing at the matching section below.

## Service and host alerts

`stacks/monitoring/prometheus/rules/service.yml` (`scope: service`). Host and
service availability from kdk-mon-01's external vantage point.

| Alert | First checks |
|---|---|
| `InstanceDown` (critical) | `node_exporter` on the host is unscrapable 5m. Is the host up (ping / console)? If up, is the exporter running? |
| `EndpointDown` (critical) | A `*.kmkdp.com` HTTPS probe failed 5m (a ForwardAuth 302 counts as up, so this is a real outage). Confirm it is an outage, not a deploy blip; check the stack on its host |
| `CadvisorDown` | cAdvisor on the host is unscrapable 5m; container metrics are blind. Restart the `cadvisor` service |
| `NetExporterDown` | A network exporter on kdk-dkr-01 (unpoller / adguard / snmp) is down 10m; the Network dashboards are blind. Check the `net-exporters` stack |
| `HostDiskSpaceLow` | Root filesystem under 10% free 30m. Clear space or grow the disk on the named instance |
| `HostMemoryHigh` | Available memory under 10% for 15m (kdk-nas-01 is excluded — its RAM is ZFS ARC). Find the hungry process/container |
| `TLSCertExpiringSoon` | An LE cert for a probed name expires in under 14 days. Check the Traefik cf resolver (most names) or the roborock-cert lego one-shot (api-slave.kmkdp.com) |

## Smart-home availability

`stacks/monitoring/prometheus/rules/smart-home.yml` (`scope: smart-home`). The
out-of-cluster monitor that answers the 2026-08-30 Home Assistant outage.

| Alert | First checks |
|---|---|
| `SmartHomeEndpointDown` (critical) | A smart-home app endpoint (HA / Scrypted) failed its probe 3m. `docker compose ps` on kdk-dkr-01 / kdk-dkr-02; `docker compose up -d` converges the stack |
| `SmartHomeProbeExporterDown` | Prometheus cannot scrape the blackbox exporter on kdk-mon-01; `SmartHomeEndpointDown` is blind until it recovers. Restart the probe exporter |

## Thermal alerts

`stacks/monitoring/prometheus/rules/thermal.yml` (`scope: thermal`). Keyed on
idrac-exporter (Redfish), independent of any fan controller.

| Alert | First checks |
|---|---|
| `IdracExporterDown` | The BMC is unscrapable 5m; thermal alerting for that host is blind. Check idrac-exporter and BMC reachability |
| `HostCpuTempHigh` | CPU above 82C for 5m. Check load and airflow on the named host |
| `HostCpuTempCritical` (critical) | CPU above 92C. If a fan controller manages this BMC, release fans to auto now: `ipmitool -I lanplus -L ADMINISTRATOR -H <bmc> -U idracrw -E raw 0x30 0x30 0x01 0x00` |
| `HostInletTempHigh` | Inlet air above 30C for 10m — ambient/room cooling issue |
| `FanUnhealthy` | A chassis fan reports a non-OK health status |
| `FansPinnedHigh` | Peak fan speed above 10000 RPM for 20m. Expected while the fan controller is in monitoring-only mode; otherwise sustained load or stopped fan control |

## Alerting standard

The lab's alerting standard — the severity ladder, the required `severity` /
`source` / `scope` labels and `summary` / `runbook` annotations, the `for`
minimums and the Alertmanager grouping/repeat targets — is being introduced by
the `feat/alerting-email-and-komodo` change; see that PR for the authoritative
text. Until it lands, follow the per-rule conventions in
[Add a Loki alert rule](add-loki-alert-rule.md) (labels `severity`, `source`,
`scope`; annotations `summary`, `runbook`). `LokiShippingStalled` carries `host`
instead of `scope` by design (`absent_over_time` has no series to carry it).

## Test it

Fire a one-off from the host to confirm creds and reachability before relying on
the logic:

```sh
ssh kdkadmin@<host> "set -a; . /etc/kdk/pushover.env; \
  curl -s -o /dev/null -w '%{http_code}\n' https://api.pushover.net/1/messages.json \
  --form-string token=\$PUSHOVER_TOKEN --form-string user=\$PUSHOVER_USER \
  --form-string title='kdk test' --form-string message='ignore' --form-string priority=-1"
```

A `200` means delivered. Use priority `-1` for tests so it doesn't buzz.
