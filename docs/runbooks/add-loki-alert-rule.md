---
title: Add a Loki alert rule
---

# Add a Loki alert rule

Log-based alerts are LogQL rules evaluated by the Loki ruler on
[[kdk-log-01]] (`10.1.20.21`). Firing alerts go to Alertmanager on
[[kdk-mon-01]] (`10.1.20.30:9093`) and on to Pushover. Metric alerts are a
different path (`stacks/monitoring/prometheus/rules/`, see
[Add a Pushover alert](pushover-alerts.md)).

**Related:** [Security alerts](security-alerts.md) · [Observability → Alert rules](../services/observability.md#alert-rules)

## 1. Pick the file

One file per source family in `stacks/loki/rules/`. Add to the existing file;
a new family gets a new file **and** a new section in
[security-alerts.md](security-alerts.md).

| File | Source |
|---|---|
| `edge.yml` | DMZ Traefik tunnel traffic, cloudflared |
| `identity.yml` | Authelia, lldap |
| `jellyfin.yml` | Jellyfin via the DMZ Traefik + its own log |
| `wizarr.yml` | Wizarr |
| `request-apps.yml` | Seerr, Libreseerr |
| `mobile-apps.yml` | Audiobookshelf, Immich, Tandoor |
| `home-assistant.yml` | HA, Node-RED, zigbee2mqtt, Mosquitto, Scrypted |
| `hosts.yml` | syslog (sshd, sudo), docker-event-watch |
| `advisories.yml` | advisory-watch, trivy-scan |
| `shipping.yml` | `LokiShippingStalled` per host |

Only `*.yml` files belong there. The ruler parses **every** non-hidden file in
the directory (tenant `fake`), and the `pre_deploy` hash covers only
`rules/*.yml`, so a `.yaml` or a `README` breaks the load or the recreate.

## 2. Write the rule

```yaml
groups:
  - name: wizarr                 # one group per file, named after the file
    interval: 1m
    rules:
      # What it catches, where the log line comes from, how to test it.
      - alert: WizarrInviteGuessBurst
        expr: |
          sum by (ClientHost) (count_over_time({host="kdk-dkr-dmz-01", container="traefik"}
            |= "wizarr.kmkdp.com" | json | RequestHost="wizarr.kmkdp.com"
            | RouterName=~".*wizarr-public@docker" | RequestPath=~"/(j/|join).*" [5m])) > 30
        for: 0m
        labels:
          severity: warning      # critical | warning | info
          source: loki           # required: Alertmanager routes on it
          scope: wizarr          # = the file / family
        annotations:
          summary: "Wizarr: {{ $labels.ClientHost }} hit /j or /join more than 30 times in 5m"
          runbook: docs/runbooks/security-alerts.md#wizarr
          # kdk_inferred: "true"  # only when the log text was not seen in this lab
```

Conventions (from `stacks/loki/rules/*`):

- **DMZ Traefik selectors** always carry the host: `{host="kdk-dkr-dmz-01", container="traefik"} | json`.
- **From the internet** = `entryPointName="tunnel"` (cloudflared's entrypoint).
  Fallback `ClientAddr=~"10\\.211\\.2\\.2:.*"`. **Never** `request_Cf_Connecting_Ip!=""`:
  the field is `-` on LAN lines, so it matches everything. Until the dmz-proxy
  two-entrypoint split (PR #68) is live, every request logs `websecure` and
  tunnel-only rules stay silent.
- Label filters are fully anchored and `RequestPath` includes the query string:
  write `"/Items(\\?.*)?"` or `".*"` suffixes, not a bare path.
- Public-IP checks use `ip()` CIDR filters (`ClientHost != ip("10.0.0.0/8")`), not
  prefix regexes.
- Router names in the log are `<entrypoint>-<router>@docker`: match `.*<router>@docker`.
- Annotation names have no dots (`kdk_inferred`, not `kdk.inferred`).
- A series label named `severity` is overwritten by the rule's `severity`; copy it
  to another name first (see `adv_severity` in `advisories.yml`).
- `runbook` = `docs/runbooks/security-alerts.md#<family>`, and that anchor exists.
- `info` = "expected while you work on X". It is e-mailed as a digest (the
  `email-info` receiver), not paged.

## 3. Test the LogQL before the PR

1. Grafana (`https://grafana.kmkdp.com`, Authelia login) → Explore → Loki.
2. Paste the selector and pipeline **without** the outer `sum(...) > N` and run it
   over 7 days. You should see the lines you mean, and not normal traffic.
3. Then run the full `expr` as a metric query over 24 h. Normal days stay under
   the threshold.

The laptop cannot query Loki directly (`10.1.20.21:3100` is nft-fenced to the
log senders and Grafana). Use Grafana, or run curl on an allowed host:

```bash
ssh kdkadmin@10.1.20.30 "curl -sG http://10.1.20.21:3100/loki/api/v1/query \
  --data-urlencode 'query=sum(count_over_time({host=\"kdk-dkr-dmz-01\", container=\"traefik\"}[5m]))'"
```

## 4. Lint locally

```bash
# preferred: lokitool from the Loki 3.7.9 release archive (used for PR #74)
lokitool rules lint --dry-run stacks/loki/rules/*.yml     # SUCCESS: N rules found
# fallback: YAML parse + required labels
python3 - <<'PY'
import glob, yaml
for f in glob.glob("stacks/loki/rules/*.yml"):
    for g in yaml.safe_load(open(f))["groups"]:
        for r in g["rules"]:
            assert r["labels"].get("source") == "loki", (f, r["alert"])
            assert r["labels"].get("severity") in ("critical", "warning", "info"), (f, r["alert"])
print("ok")
PY
```

## 5. Merge and verify the load

On merge, the `loki` stack's `pre_deploy` (`komodo/resources.toml`) writes
`RULES_SHA` into `.env`; the `kdk.rules-sha` label changes, so `loki` is
recreated with the new rules (a few seconds of no ingestion; senders buffer).

```bash
# expected hash: run in the repo
(cd stacks/loki && sha256sum rules/*.yml | sha256sum | cut -c1-12)
ssh kdkadmin@10.1.20.21 'sudo docker inspect loki --format "{{index .Config.Labels \"kdk.rules-sha\"}}"'   # same 12 chars
ssh kdkadmin@10.1.20.30 'curl -s http://10.1.20.21:3100/loki/api/v1/rules | grep -c "alert:"'                # old count + new rules (76 on 2026-10-30)
ssh kdkadmin@10.1.20.21 'sudo docker logs loki --since 10m 2>&1 | grep -i rule | grep -i -E "err|fail"'       # empty
```

If the label did not change, Komodo did not run the `pre_deploy`: run
`RunSync`, then Deploy the `loki` stack.

## 6. Fire it once

Prefer a real trigger from the LAN (the rule comment names it), e.g. for
`WizarrInviteGuessBurst`:

```bash
for i in $(seq 31); do curl -sk -o /dev/null https://wizarr.kmkdp.com/j/x; done
ssh kdkadmin@10.1.20.30 'sudo docker exec alertmanager-ext amtool --alertmanager.url=http://127.0.0.1:9093 \
  alert query alertname=WizarrInviteGuessBurst'
```

To test only the routing (no Loki involved), post a fake alert straight to
Alertmanager; it expires on its own:

```bash
ssh kdkadmin@10.1.20.30 'sudo docker exec alertmanager-ext amtool --alertmanager.url=http://127.0.0.1:9093 \
  alert add RouteTest source=loki severity=info --annotation=summary="route test" --end=$(date -u -d +5min +%FT%TZ)'
```

`severity=info` should arrive as an e-mail digest (`[INFO] RouteTest`), not a
page. See [Alerting standards](../reference/alerting-standards.md).

## Routing

`stacks/monitoring/alertmanager/alertmanager.yml`:

| Match | Receiver | Grouping / repeat |
|---|---|---|
| `severity="critical"` | `email-ops` (Pushover + e-mail) | `alertname, host`, 1 h |
| `severity="info"` | `email-info` (e-mail only, digest) | `alertname`, 6 h / 24 h |
| `source="loki"` (the rest → warning) | `pushover` (priority 1 firing, 0 resolved) | `alertname, host`, 4 h |

An `alertmanager.yml` change reaches the container through the
`monitoring-ext` `post_deploy` reload (`POST /-/reload`); a rejected config
keeps the old one. Check with `amtool check-config` before merging (see PR #74).

## Silence

```bash
ssh kdkadmin@10.1.20.30 'sudo docker exec alertmanager-ext amtool --alertmanager.url=http://127.0.0.1:9093 \
  silence add alertname=<Name> --duration=4h --comment="<why>"'
```

Or in the UI: `https://alertmanager.kmkdp.com` (Authelia, `internal-users`) →
Silences → New. The Pushover message's link opens the same UI. A permanent
false positive is a rule fix (PR), not a long silence.
