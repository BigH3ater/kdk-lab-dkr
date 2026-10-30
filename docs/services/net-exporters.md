---
title: net-exporters
host: kdk-dkr-01
stack: net-exporters
tier: infra
status: live
secrets: [unifi-api-token-ro, kdk-dns-adguard-home-admin, unifi-snmp-ro-v3-user]
updated: 2026-10-30
tags: [homelab/stack, tier/infra, status/live, observability]
---

# net-exporters

Network Prometheus exporters on **kdk-dkr-01**, scraped by Prometheus on
**kdk-mon-01** for the Grafana **"Network"** folder (UniFi / AdGuard / SNMP
dashboards). They live here, not on mon-01, to keep the alerting Pi lean.

**Related:** [[kdk-dkr-01]] · [[observability]] · [[kdk-mon-01]]

## At a glance

| Field | Value |
|---|---|
| Purpose | UniFi, AdGuard and SNMP metrics for the Grafana "Network" dashboards |
| Host | [[kdk-dkr-01]] (metrics ports bound to `10.1.20.20`, VLAN 20 only) |
| Stack | `stacks/net-exporters` (`unpoller` + `adguard-exporter` + `snmp-exporter`) |
| Scraped by | Prometheus on [[kdk-mon-01]] (jobs `unpoller`/`adguard`/`snmp`) |
| Deploy | Komodo stack `net-exporters` (`deploy = true`), `pre_deploy` `op inject`, webhook |
| Blast radius | Network dashboards go stale; no alerting impact (metrics only) |

## Verify

```bash
ssh kdkadmin@10.1.20.20 'docker ps --filter name=unpoller --filter name=adguard-exporter --filter name=snmp-exporter --format "{{.Names}} {{.Status}}"'
curl -s http://10.1.20.20:9130/metrics | head   # unpoller (UniFi)
curl -s http://10.1.20.20:9618/metrics | head   # adguard-exporter
curl -s http://10.1.20.20:9116/metrics | head   # snmp-exporter
```

In Grafana, the "Network" folder panels populate.

## Troubleshooting

| Problem | Cause | Command to validate |
|---|---|---|
| UniFi panels empty | wrong controller URL / bad API key | `docker logs unpoller`; controller is `https://10.1.3.1` (UDM, port 443) |
| AdGuard panels empty | exporter can't auth to a DNS node | `docker logs adguard-exporter`; servers `10.1.1.120/.121/.122` |
| SNMP panels empty | SNMPv3 creds / snmp.yml not expanded | `docker logs snmp-exporter`; `--config.expand-environment-variables` on |
| `.env not injected` | `op inject` pre_deploy failed | confirm [[op-connect]] is up; re-run the deploy |

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | [[op-connect]] | `.env` (API key / passwords) not rendered, exporters won't auth |
| Needs | UniFi controller `kdk-gw-01` (`10.1.3.1`), AdGuard nodes, gateway SNMP | no metrics |
| Needed by | Prometheus on [[kdk-mon-01]] | the "Network" Grafana dashboards |

## Observability

The exporters *are* observability inputs; they have no dashboard of their own.
Container health via Komodo / `docker ps`; logs ship to Loki via the
observability agent.

## Architecture

Three services from `stacks/net-exporters/compose.yaml`, each with its metrics
port bound to `10.1.20.20` (VLAN 20) so only Prometheus on mon-01 reaches them:

| Service | Image | Port | Source |
|---|---|---|---|
| unpoller | `ghcr.io/unpoller/unpoller:v5.8.3` | `9130` | UniFi controller `kdk-gw-01` (read-only API key) |
| adguard-exporter | `ghcr.io/henrywhitaker3/adguard-exporter:v1.2.1` | `9618` | AdGuard Home on `10.1.1.120/.121/.122` |
| snmp-exporter | `prom/snmp-exporter:v0.31.0` | `9116` | gateway IF-MIB counters (SNMPv3) |

Secrets come from the `op`-injected `.env` (`.env.tpl`, `pre_deploy` in
`komodo/resources.toml`). `snmp-exporter` mounts `./snmp` as a directory (not a
single file) so git's new inode is seen on deploy.

## Configuration

| Path / setting | What it is |
|---|---|
| `stacks/net-exporters/compose.yaml` | the three exporters |
| `stacks/net-exporters/.env.tpl` | op-injected UniFi API key, AdGuard creds, SNMPv3 password |
| `stacks/net-exporters/snmp/snmp.yml` | SNMP exporter config (env-expanded at start) |
| Prometheus `stacks/monitoring/prometheus/prometheus.yml` | the `unpoller`/`adguard`/`snmp` scrape jobs on mon-01 |

## Secret Rotation

| Secret (1P item, `kdk-cluster`) | Used for | Class | Rotate live? |
|---|---|---|---|
| `unifi-api-token-ro` | unpoller UniFi read-only API | 🟢 self-service | regenerate in UniFi, update item, redeploy |
| `kdk-dns-adguard-home-admin` | adguard-exporter auth to the DNS nodes | 🟢 self-service | rotate with AdGuard, update item, redeploy |
| `unifi-snmp-ro-v3-user` | snmp-exporter SNMPv3 auth | 🟢 self-service | rotate on the gateway, update item, redeploy |

Rotate in 1P, then redeploy so `op inject` re-renders `.env`.

## User Guide

No UI. These are metrics inputs for the Grafana "Network" dashboards
(https://grafana.kmkdp.com). See [[observability]].
