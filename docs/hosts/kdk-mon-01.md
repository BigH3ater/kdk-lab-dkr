---
title: kdk-mon-01
host: kdk-mon-01
fqdn: none
ip: 10.1.20.30
role: monitor
tier: internal
status: live
provisioner: baremetal
hypervisor: none
vmid: none
console: physical (Raspberry Pi 5); keyboard + HDMI
standards: drifted
secrets: [pushover-kdk-lab, proton-bridge-smtp-incluster]
updated: 2026-09-12
tags: [homelab/host, role/monitor, status/live, tier/internal]
---

# kdk-mon-01 — out-of-cluster monitor

A Raspberry Pi 5 running the standalone monitoring stack so alerting survives a full outage of everything else: Prometheus, Grafana, Alertmanager (→ Pushover), blackbox + iDRAC exporters, and the iDRAC fan controller.

**Related:** [[kdk-hyp-01]] · [[kdk-nas-01]] · [[komodo]]

## At a glance

| Field | Value |
|---|---|
| Purpose | Independent metrics, uptime probes, alerting, and BMC fan control |
| Status | 🟢 Live — periphery active; stack up (2026-09-12) |
| Host | Raspberry Pi 5 (bare metal) |
| Address | `10.1.20.30` (VLAN 20) |
| OS | Debian 13, kernel `6.18.34+rpt-rpi-2712 aarch64` |
| Hardware | 4 cores / 7.9 GiB / 470 GB |
| Managed by | adopted Komodo stack `monitoring-ext` (files on host, `/opt/kdk-lab/monitoring`) |
| Blast radius | No metrics, no uptime alerting, and BMC fans revert to firmware control |

## Access

| Path | How | Use when |
|---|---|---|
| Console | physical keyboard + HDMI on the Pi | SSH down |
| SSH | `ssh kdkadmin@10.1.20.30` | normal operation |

Admin: `kdkadmin`, key-only, `NOPASSWD:ALL`.

## Verify

```bash
ssh kdkadmin@10.1.20.30 'systemctl is-active periphery'                       # active
ssh kdkadmin@10.1.20.30 'sudo docker ps --format "{{.Names}} {{.Status}}"'    # prometheus/grafana/alertmanager Up
ssh kdkadmin@10.1.20.30 'sudo docker exec alertmanager-ext amtool check-config /etc/alertmanager/alertmanager.yml'  # SUCCESS
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| No alerts on the phone | Alertmanager → Pushover misconfig | validate creds: Pushover `users/validate.json` returns `status:1` |
| Alert config won't load | bad `alertmanager.yml` | `amtool check-config` before recreating the container |
| BMC fans loud / firmware curve | `idrac-fan-control` stopped | `docker restart idrac-fan-control`; it fails safe to the BMC |

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | Pushover (SaaS) | alert delivery |
| Needs | kdk-hyp-01 / kdk-nas-01 IPMI | fan control + iDRAC metrics |
| Needed by | operator | this is the alerting path for the whole lab |

## Observability

This host **is** the observability. Prometheus jobs: `idrac`, `node-external`, `blackbox-icmp/-dns/-http-app`, `prometheus-self`. Alert rules: `FanControl*` (BMC temps/reachability on hyp + nas), `SmartHome*` (endpoint probes). Alertmanager delivers to Pushover.

## Standards compliance

| Standard | Required | This host | Note |
|---|---|---|---|
| Hostname | `{site}-{role}-{nn}` | ✅ `kdk-mon-01` | |
| Admin account | `kdkadmin`, key-only, NOPASSWD | ✅ | |
| Timezone | America/Chicago | ✅ | CDT |
| NTP | `ntp.kmkdp.com` primary | ⚠️ synced via public pools (timesyncd) | |
| Syslog | `syslog.kmkdp.com:514` | ⚠️ forwards (rsyslog TCP), but the receiver is absent | |

## Architecture

Containers: `prometheus-ext v3.7.3`, `grafana-ext 12.3.1`, `alertmanager-ext v0.28.1`, `blackbox-exporter v0.28.0`, `idrac-exporter 2.6.2`, `idrac-fan-control`. Alertmanager gossip `9094`, API `9093`; scrapers reach targets across the lab; the fan controller drives the hyp/nas BMCs over IPMI.

### Storage map

`N/A — OS disk only; Prometheus/Grafana data on local volumes, no external storage.`

## Configuration

| Path | What it is |
|---|---|
| `/opt/kdk-lab/monitoring/docker-compose.yml` | the stack (adopted, files on host) |
| `/opt/kdk-lab/monitoring/alertmanager/alertmanager.yml` | routes to the `pushover` receiver |
| `.../alertmanager/pushover-user-key`, `pushover-app-token` | 0600, owned by `nobody:65534` (the container UID) |

## Secret Rotation

| Secret (1P item) | Lands as | Class | Rotate live? |
|---|---|---|---|
| `pushover-kdk-lab` (`user_key`, `api_token`) | the two 0600 token files | 🟡 external party | rewrite both files (owned `65534`), recreate `alertmanager-ext` |
| `proton-bridge-smtp-incluster` | `proton-bridge-smtp-password` file | 🟡 external party | email path currently unused |

## Out-of-band changes

Alertmanager switched from the ntfy webhook + in-cluster SMTP to Pushover (2026-09-12); ntfy and the cluster relay are retired. The `blackbox-http-app`/`blackbox-k3s-api` jobs still target the old cluster and `ntfy.kmkdp.com` — stale, pending the monitoring re-home.

## Provisioning

Bare-metal Raspberry Pi 5, imaged manually; the monitoring stack predates the migration and was adopted in place. Rebuild: reflash, reinstall Docker + periphery, restore `/opt/kdk-lab/monitoring`.
