---
title: Observability (metrics, logs, fan control)
host: kdk-mon-01, kdk-log-01
stack: monitoring, loki, observability-agent, node-exporter
tier: infra
status: live
storage: yes
secrets: [pushover-kdk-lab, kdk-lab-idrac-fanctl-admin]
updated: 2026-10-28
tags: [homelab/stack, tier/infra, status/live]
---

# Observability — metrics, logs, fan control

Metrics + alerting live on the independent monitor [[kdk-mon-01]]; logs live on the dedicated log VM [[kdk-log-01]] (moved off [[kdk-dkr-01]] 2026-10). Fans on both PowerEdge BMCs are steered by a community controller.

**Related:** [[kdk-mon-01]] · [[kdk-log-01]] · [[kdk-dkr-01]] · [[komodo]]

## At a glance

| Field | Value |
|---|---|
| Metrics/alerts | Prometheus + Alertmanager (→ Pushover) + Grafana on [[kdk-mon-01]] |
| Logs | Loki 3.7.9 on [[kdk-log-01]] (`10.1.20.21`), storage on the VM disk (`/opt/kdk-lab/loki/data`, PBS vzdump of VM 320), 30-day retention; ruler group `shipping` → Alertmanager |
| Collection | per-host `observability-agent` (cAdvisor + Alloy + **docker-event-watch**); `node-exporter` on the two VMs |
| Fan control | two `tigerblue77` containers on [[kdk-mon-01]]; `fanctl-hyp` **active** (R720 ~3000 RPM, 20%), `fanctl-nas` **active** (R730xd ~4800 RPM, 20%) |
| Web UI | Grafana `https://grafana.kmkdp.com` (via Traefik on kdk-dkr-01; direct fallback `http://10.1.20.30:3000` when dkr-01 is down) — Prometheus + Loki datasources; Explore for logs |
| Blast radius | Loss of dashboards/log search; the mon-01 alerting path is independent of the rest |

## Verify

```bash
curl -s http://10.1.20.21:3100/ready                                  # ready (Loki; from an allowlisted source)
curl -s http://10.1.20.30:9090/api/v1/targets | grep -c '"up"'        # cadvisor/node/blackbox targets up
curl -sG http://10.1.20.21:3100/loki/api/v1/label/host/values         # every host present (6)
curl -s http://10.1.20.21:3100/loki/api/v1/rules                      # group shipping
ssh kdkadmin@10.1.20.30 'sudo docker logs --tail 3 fanctl-hyp'        # temps logged, profile applied
```

## Troubleshooting

| Problem | Cause | Command to validate |
|---|---|---|
| Logs missing from a host (`LokiShippingStalled`) | that host's Alloy down, wrong LOKI_HOST, or its IP not allowed by kdk-log-01's nft / UniFi | `docker logs alloy` on the host; every host uses `LOKI_HOST=10.1.20.21` |
| "timestamp too old" in Alloy | one-time replay of pre-existing container logs | benign; new logs still ingest |
| DMZ host metrics/logs absent | DMZ→internal path | node/cadvisor pulled over LAN→DMZ; Alloy pushes via the UniFi rule `192.168.191.20 → 10.1.20.21:3100` |
| `FansPinnedHigh` firing | that BMC on auto (loud) | expected only if a controller is monitoring-only/stopped |
| Per-container panels empty | cAdvisor older than v0.55 can't read containers under the containerd-snapshotter image store (`failed to identify the read-write layer ID`) | runs `ghcr.io/google/cadvisor:v0.60.6` (gcr.io stops at v0.55.1) with `/var/lib/containerd` mounted; `count by (host) (container_memory_usage_bytes{name!=""})` should be > 0 on every host. Crash/unhealthy alerting is still `docker-event-watch`, not cAdvisor |
| One-shot job output missing in Loki | Alloy's `discovery.docker` lists running containers, and the backup jobs exit within seconds | the Backups dashboard counts runs from `docker-event-watch` die events instead; capturing their output needs a follow-up (exited-container discovery or pushing logs from the job) |

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | [[kdk-log-01]] (VM 320 on kdk-hyp-01) | no log ingestion or search |
| Needs | idrac-exporter (BMC Redfish) | thermal alerting is blind |
| Needed by | operator | the only alerting + log-search path for the lab |

## Observability

This *is* the observability. Prometheus jobs: `idrac`, `node-external` (11), `cadvisor` (5), `blackbox-icmp/-dns/-http-app`, `blackbox-service` (14 migrated `*.kmkdp.com`). Alert rules: `thermal.yml` (idrac-exporter), `service.yml` (host/container/endpoint/cert), `smart-home.yml`. All route to Pushover.

## Architecture

`loki:3100` on kdk-log-01, published on `10.1.20.21` only and fenced by its nft allowlist, with an Alloy syslog receiver on `:514` tcp+udp (`syslog.kmkdp.com` → 10.1.20.21). The Loki ruler reads `stacks/loki/rules/` and sends to Alertmanager `10.1.20.30:9093`, which routes `source="loki"` to Pushover grouped by `alertname, host`. Each host runs cAdvisor (`:9280`) + Alloy (Docker log discovery → Loki). The three VMs (dkr-01, dmz-01, log-01) add node-exporter (`:9100`); the Pis have it as a package. Fan controllers reach the R720/R730xd BMCs by IPMI as `idracrw`. Container-state alerting is per-host: `docker-event-watch` tails the Docker events API and pages Pushover on a container crash (die with a non-graceful exit) or unhealthy status; user-facing apps are also covered by `EndpointDown` (blackbox HTTPS probes of the 14 `*.kmkdp.com` services).

## Configuration

| Path / setting | What it is |
|---|---|
| `stacks/loki/` | Loki + syslog Alloy (git-linked to kdk-log-01); `rules/` = Loki ruler rules |
| `stacks/observability-agent/`, `stacks/node-exporter/` | per-host agents |
| `stacks/monitoring/` | mirror of the mon-01 stack (adopted, files-on-host — git-link deferred, see its README) |
| `/opt/kdk-lab/loki/data` on kdk-log-01 | Loki data (VM disk). The old `/mnt/loki` NFS dataset on kdk-dkr-01 is released after 2026-11-28 |

## Secret Rotation

| Secret (1P item) | Used for | Class | Rotate live? |
|---|---|---|---|
| `pushover-kdk-lab` | alert delivery | 🟡 external party | rewrite the two 0600 token files (uid 65534), recreate alertmanager |
| `kdk-lab-idrac-fanctl-admin` (kdk-ops, `idracrw`) | BMC fan control | 🟡 admin creds | rotate on both BMCs + 1P, redeploy the fan controllers |

## User Guide

Open Grafana at `https://grafana.kmkdp.com` and sign in with **Authelia** (members of `internal-users` land as Admin); the local `admin` login (1P `grafana-admin`) stays as break-glass, and `http://10.1.20.30:3000` works when kdk-dkr-01 is down. Explore → Loki datasource to search logs by label (`{host="kdk-dkr-01"}`, then `|= "error"`). To make the servers quiet, set `MONITORING_ONLY_MODE: "false"` on a fan controller in the mon-01 stack and redeploy; watch fan RPM drop in Grafana.
