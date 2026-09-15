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
services are covered automatically. To get the *unhealthy* signal, give the
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
