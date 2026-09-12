---
title: Observability (metrics, logs, fan control)
host: kdk-mon-01, kdk-dkr-01
stack: monitoring, loki, observability-agent, node-exporter
tier: infra
status: live
storage: yes
secrets: [pushover-kdk-lab, kdk-lab-idrac-fanctl-admin]
updated: 2026-09-12
tags: [homelab/stack, tier/infra, status/live]
---

# Observability — metrics, logs, fan control

Metrics + alerting live on the independent monitor [[kdk-mon-01]]; logs live on [[kdk-dkr-01]] with storage on the NVMe array. Fans on both PowerEdge BMCs are steered by a community controller.

**Related:** [[kdk-mon-01]] · [[kdk-dkr-01]] · [[komodo]]

## At a glance

| Field | Value |
|---|---|
| Metrics/alerts | Prometheus + Alertmanager (→ Pushover) + Grafana on [[kdk-mon-01]] |
| Logs | Loki 3.5.5 on [[kdk-dkr-01]], storage on `vmpool/loki` (NVMe) via NFS, 30-day retention |
| Collection | per-host `observability-agent` (cAdvisor + Grafana Alloy); `node-exporter` on the two VMs |
| Fan control | two `tigerblue77` containers on [[kdk-mon-01]]; `fanctl-hyp` **active** (R720 ~2880 RPM at 15%), `fanctl-nas` monitoring-only |
| Web UI | Grafana `http://10.1.20.30:3000` — Prometheus + Loki datasources; Explore for logs |
| Blast radius | Loss of dashboards/log search; the mon-01 alerting path is independent of the rest |

## Verify

```bash
curl -s http://10.1.20.20:3100/ready                                  # ready (Loki)
curl -s http://10.1.20.30:9090/api/v1/targets | grep -c '"up"'        # cadvisor/node/blackbox targets up
curl -sG http://10.1.20.20:3100/loki/api/v1/label/host/values         # every host present
ssh kdkadmin@10.1.20.30 'sudo docker logs --tail 3 fanctl-hyp'        # temps logged, "monitoring only"
```

## Troubleshooting

| Problem | Cause | Command to validate |
|---|---|---|
| Logs missing from a host | that host's Alloy down or wrong LOKI_HOST | `docker logs alloy` on the host; dmz-01 uses `100.100.100.50` |
| "timestamp too old" in Alloy | one-time replay of pre-existing container logs | benign; new logs still ingest |
| DMZ host metrics/logs absent | DMZ→internal path | node/cadvisor pulled over LAN→DMZ; Alloy pushes over the storage net |
| `FansPinnedHigh` firing on kdk-hyp-01 | R720 on BMC auto (~12000 RPM) | expected until `fanctl-hyp` is enabled |

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | kdk-nas-01 `vmpool/loki` (NFS) | Loki cannot store/serve logs |
| Needs | idrac-exporter (BMC Redfish) | thermal alerting is blind |
| Needed by | operator | the only alerting + log-search path for the lab |

## Observability

This *is* the observability. Prometheus jobs: `idrac`, `node-external` (11), `cadvisor` (5), `blackbox-icmp/-dns/-http-app`, `blackbox-service` (14 migrated `*.kmkdp.com`). Alert rules: `thermal.yml` (idrac-exporter), `service.yml` (host/container/endpoint/cert), `smart-home.yml`. All route to Pushover.

## Architecture

`loki:3100` on kdk-dkr-01 (bound to the host; reachable on VLAN 20 and the storage net) with an Alloy syslog receiver on `:514` (`syslog.kmkdp.com` → 10.1.20.20). Each host runs cAdvisor (`:9280`) + Alloy (Docker log discovery → Loki). The two VMs add node-exporter (`:9100`); the Pis have it as a package. Fan controllers reach the R720/R730xd BMCs by IPMI as `idracrw`.

## Configuration

| Path / setting | What it is |
|---|---|
| `stacks/loki/` | Loki + syslog Alloy (git-linked to kdk-dkr-01) |
| `stacks/observability-agent/`, `stacks/node-exporter/` | per-host agents |
| `stacks/monitoring/` | mirror of the mon-01 stack (adopted, files-on-host — git-link deferred, see its README) |
| `/mnt/loki` on kdk-dkr-01 | NFS mount of `vmpool/loki` (NVMe) |

## Secret Rotation

| Secret (1P item) | Used for | Class | Rotate live? |
|---|---|---|---|
| `pushover-kdk-lab` | alert delivery | 🟡 external party | rewrite the two 0600 token files (uid 65534), recreate alertmanager |
| `kdk-lab-idrac-fanctl-admin` (kdk-ops, `idracrw`) | BMC fan control | 🟡 admin creds | rotate on both BMCs + 1P, redeploy the fan controllers |

## User Guide

Open Grafana at `http://10.1.20.30:3000`. Explore → Loki datasource to search logs by label (`{host="kdk-dkr-01"}`, then `|= "error"`). To make the servers quiet, set `MONITORING_ONLY_MODE: "false"` on a fan controller in the mon-01 stack and redeploy; watch fan RPM drop in Grafana.
