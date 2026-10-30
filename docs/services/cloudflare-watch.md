---
title: cloudflare-watch — read-only Cloudflare edge watch
host: kdk-log-01
stack: cloudflare-watch
tier: infra
status: new
storage: yes
secrets: [cloudflare-monitor-ro]
updated: 2026-10-29
tags: [homelab/stack, tier/infra, status/new, security]
---

# cloudflare-watch — read-only Cloudflare edge watch

Two one-shots on [[kdk-log-01]] that read the `kmkdp.com` Cloudflare zone with a zone-scoped read-only token and print one JSON line per check to stdout. The host Alloy ships stdout to Loki and `stacks/loki/rules/cloudflare.yml` pages. It answers "is the edge still configured and healthy the way we left it?" — tunnel up, Universal SSL valid, TLS/HSTS policy intact, and nobody changed the WAF rules or DNS without us noticing.

**Related:** [[observability]] · [[dmz-proxy]] · [[kdk-log-01]] · [security alerts runbook](../runbooks/security-alerts.md#cloudflare-watch)

## At a glance

| Field | Value |
|---|---|
| Runs | `cloudflare-watch` hourly at `:17`; `cloudflare-watch-weekly` Monday 07:10 America/Chicago. Both: procedure → stack (deploy=false, destroy_before_deploy) on [[kdk-log-01]] |
| Image | `kdk/cloudflare-watch:latest` (local `Dockerfile`, `python:3.12-slim`, stdlib only), uid 1000, read-only, no caps, 128 MB |
| Output | Loki `{container="cloudflare-watch"}` (hourly) and `{container="cloudflare-watch-weekly"}` (weekly); one JSON line per check (`check`, `status`, `ts`, `mode`, `zone`, fields) |
| State | `/opt/kdk-lab/cloudflare-watch/state.json` (weekly rule + DNS shas) |
| Checks | hourly: `tunnel`, `edge_cert`, `zone_settings`, `firewall_events`; weekly: `rule_drift` |
| Alerts | `CloudflareTunnelUnhealthy`, `CloudflareEdgeCertExpiring`, `CloudflareZoneSettingDrift`, `CloudflareRuleDrift`, `CloudflareWatchMissing` (all warning) |
| Blast radius | none: a read-only token (no write scope), writes only its state dir and Loki |

## Verify

```bash
# Komodo -> Procedures -> cloudflare-watch -> Run, then on kdk-log-01:
ssh kdkadmin@10.1.20.21 'sudo docker logs cloudflare-watch 2>&1 | tail -4'   # 4 JSON lines, check=tunnel/edge_cert/zone_settings/firewall_events
# From mon-01 (the laptop cannot reach Loki directly):
ssh kdkadmin@10.1.20.30 "curl -sG http://10.1.20.21:3100/loki/api/v1/query_range \
  --data-urlencode 'query={container=\"cloudflare-watch\"} | json | check=\"tunnel\"' --data-urlencode 'since=3h' | head -c 400"
# Local dry run on a laptop with op (no container, writable state in /tmp):
CF_API_TOKEN=$(op read op://kdk-cluster/cloudflare-monitor-ro/credential) \
  CF_ZONE=kmkdp.com STATE=/tmp/cfwatch.json python3 stacks/cloudflare-watch/watch.py
```

## Troubleshooting

| Problem | Cause | Command to validate |
|---|---|---|
| `startup status=fail reason="CF_API_TOKEN not set"` | `/etc/kdk/cloudflare-watch.env` missing or empty on kdk-log-01 | `ssh kdkadmin@10.1.20.21 'sudo test -s /etc/kdk/cloudflare-watch.env && echo ok'` |
| `firewall_events available=false reason=graphql_forbidden` | expected: this token/plan cannot read zone GraphQL analytics | none — it is a benign `warn`, not alerted |
| `tunnel source=probe` lines | the account `cfd_tunnel` scope was denied; script fell back to the HTTPS probe | grant the token account-level `Cloudflare Tunnel:Read`, or accept the probe |
| `rule_drift status=warn state_saved=false` | the state dir is not writable by uid 1000 | `ssh kdkadmin@10.1.20.21 'ls -ld /opt/kdk-lab/cloudflare-watch'` (want `1000:1000`) |
| `CloudflareWatchMissing` fires | first run not done yet, or the procedure is disabled/failing | Komodo → Procedures → `cloudflare-watch` → run history |

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | Loki + Alloy on kdk-log-01, internet egress (`api.cloudflare.com`), the read-only token | no output / all checks fail |
| Needed by | the edge posture (tunnel, SSL, WAF, DNS) is otherwise only checked by hand | drift or an expiring cert goes unseen |

## Observability

Lines land in Loki under `{container="cloudflare-watch"}` and `{container="cloudflare-watch-weekly"}`. The five rules in `stacks/loki/rules/cloudflare.yml` are listed in [Observability → Alert rules](observability.md#alert-rules). firewall_events is deliberately not alerted (GraphQL analytics is forbidden for this token).

## Architecture

`watch.py` is stdlib-only (`urllib` + `json` + `hashlib`), baked into a local image. The hourly stack runs the four edge checks; the weekly stack runs the same script with `CFWATCH_MODE=weekly` (equivalent to `--weekly`) for the rule/DNS sha drift check, keeping its own state in `/opt/kdk-lab/cloudflare-watch/state.json`. The two stacks share `stacks/cloudflare-watch/` and the image but use distinct container names (`CFWATCH_SUFFIX`), because two one-shots cannot share a `container_name` on one host.

## Configuration

| Path / setting | What it is |
|---|---|
| `watch.py` `EXPECTED_SETTINGS` | the zone-setting policy compared each hour (min_tls_version, always_use_https, ssl, security_level, tls_1_3, automatic_https_rewrites) |
| `watch.py` `HSTS_MIN_MAX_AGE` (15552000) / `WARN_CERT_DAYS` (14) | HSTS min max-age; edge-cert warn threshold |
| `TUNNEL_HOSTS` (sso,jellyfin) | hostnames probed only if the account tunnel scope is denied |
| `CF_ZONE` (kmkdp.com) | the zone to read; `CFWATCH_MODE=weekly` switches to the weekly check |
| `.env.tpl` | `CF_API_TOKEN=op://kdk-cluster/cloudflare-monitor-ro/credential`, rendered only where `/etc/komodo/op.env` exists |

## Secret Rotation

| Secret (1P item) | Used for | Class | Rotate live? |
|---|---|---|---|
| `kdk-cluster/cloudflare-monitor-ro` | Cloudflare API read token (zone-scoped: Zone/DNS/Firewall/SSL/Analytics Read) | 🟢 low | yes: rotate in the Cloudflare dashboard, `op item edit cloudflare-monitor-ro --vault kdk-cluster credential=<new>`, then rewrite `/etc/kdk/cloudflare-watch.env` on kdk-log-01 (`op read … | ssh kdkadmin@10.1.20.21 'sudo install -m 600 -o root -g root /dev/stdin /etc/kdk/cloudflare-watch.env'` as `CF_API_TOKEN=<value>`) |

## User Guide

There is no UI. Read the lines in Grafana → Explore → Loki (`{container="cloudflare-watch"}`). An alert tells you what changed; the matching row in the [runbook](../runbooks/security-alerts.md#cloudflare-watch) says where to look. An intended edge change (a new WAF rule, a DNS record, a deliberate setting change) resolves itself: the next run records the new baseline, except a `zone_settings` change you meant to keep, which needs the expected value updated in `watch.py`.
