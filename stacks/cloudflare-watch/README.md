# cloudflare-watch

Read-only watch of the Cloudflare edge for the `kmkdp.com` zone, on
[kdk-log-01]. Two one-shot Komodo stacks share this directory and image; each
run of `watch.py` (stdlib only: `urllib` + `json` + `hashlib`) prints one JSON
line per check to stdout. The host Alloy ships stdout to Loki, and
`stacks/loki/rules/cloudflare.yml` alerts through Alertmanager → Pushover.

| Stack | Schedule | Mode | Container / Loki stream |
|---|---|---|---|
| `cloudflare-watch` | hourly, `:17` | hourly checks | `{container="cloudflare-watch"}` |
| `cloudflare-watch-weekly` | Monday 07:10 CT | `--weekly` (via `CFWATCH_MODE=weekly`) | `{container="cloudflare-watch-weekly"}` |

## Checks

Each JSON line has `check`, `status` (`ok`|`warn`|`fail`), `ts`, `mode`, `zone`
plus per-check fields.

Hourly:

- **`tunnel`** — `GET accounts/{account}/cfd_tunnel`: tunnel `status` and
  connector count. `fail` if any tunnel is not `healthy`. If the account scope is
  denied it falls back to an HTTPS probe of the proxied tunnel hostnames
  (`sso`, `jellyfin`) and looks for a `2xx/3xx` + `cf-ray` (`source=probe`).
- **`edge_cert`** — `GET zones/{zone}/ssl/certificate_packs`: Universal SSL edge
  certificate; `warn` under 14 days, `fail` if expired or none found.
- **`zone_settings`** — `GET zones/{zone}/settings`: compares `min_tls_version`,
  `always_use_https`, `ssl`, `security_level`, `tls_1_3`,
  `automatic_https_rewrites` and HSTS (`security_header`) against the expected
  map in `watch.py` (`EXPECTED_SETTINGS` + `HSTS_MIN_MAX_AGE`); `fail` + a
  `drift` list on any mismatch.
- **`firewall_events`** — GraphQL `firewallEventsAdaptiveGroups` for the last
  hour grouped by action/ruleId. This token/plan returns an `authz` error for
  the zone analytics path, so the line is `warn` with `available=false,
  reason=graphql_forbidden` and does **not** page. If the scope is ever granted
  it becomes `ok` with per-action counts.

Weekly (`--weekly`):

- **`rule_drift`** — sha256 of the normalized custom-firewall + rate-limit rule
  set (`rulesets/phases/http_request_firewall_custom|http_ratelimit/entrypoint`)
  and of the DNS record set (name/type/proxied). Compared against the last run
  in `/state/state.json`; `fail` when a sha changed, `ok` on first run or no
  change, `warn` if the state file could not be written.

## Run it by hand

On the host (both stacks share the compose; pick the service `watch`):

```bash
cd /opt/kdk-lab/stacks/cloudflare-watch        # periphery clone (or /etc/komodo/stacks/…)
sudo docker compose run --rm watch             # hourly checks
sudo CFWATCH_MODE=weekly docker compose run --rm watch   # weekly check
```

Or from a laptop with `op`, against the real API, no container:

```bash
CF_API_TOKEN=$(op read op://kdk-cluster/cloudflare-monitor-ro/credential) \
  CF_ZONE=kmkdp.com STATE=/tmp/cfwatch.json python3 stacks/cloudflare-watch/watch.py
CF_API_TOKEN=$(op read op://kdk-cluster/cloudflare-monitor-ro/credential) \
  CF_ZONE=kmkdp.com STATE=/tmp/cfwatch.json python3 stacks/cloudflare-watch/watch.py --weekly
```

## Secret

`CF_API_TOKEN` = `op://kdk-cluster/cloudflare-monitor-ro/credential` — a
zone-scoped read-only API token (Zone/DNS/Firewall/SSL/Analytics Read). It
cannot read account notification policies or (on this plan) the zone GraphQL
analytics path. kdk-log-01 has no 1Password Connect / `op` CLI, so the token is
delivered by hand to `/etc/kdk/cloudflare-watch.env` (root `600`); `.env.tpl` is
only rendered on hosts that have `/etc/komodo/op.env`.

The token value is never printed by the script.
