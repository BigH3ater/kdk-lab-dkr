---
title: kdk-dkr-02
host: kdk-dkr-02
fqdn: none
ip: 10.1.30.21
role: docker-host
tier: internal
status: live
provisioner: baremetal
hypervisor: none
vmid: none
console: physical (Raspberry Pi 5); keyboard + HDMI
standards: drifted
secrets: []
updated: 2026-09-12
tags: [homelab/host, role/docker-host, status/live, tier/internal]
---

# kdk-dkr-02 — home-automation Pi

A Raspberry Pi 5 running the Home Assistant stack (HA, Zigbee2MQTT, Node-RED, Mosquitto, Dockge) behind its own Traefik. Renamed from the old Pi `kdk-dkr-01`; adopted into Komodo in place.

**Related:** [[kdk-dkr-03]] · [[komodo]]

## At a glance

| Field | Value |
|---|---|
| Purpose | Home Assistant + Zigbee + automation |
| Status | 🟢 Live — periphery active; stack up (2026-09-12) |
| Host | Raspberry Pi 5 (bare metal) |
| Address | `10.1.30.21` (VLAN 30) |
| OS | Debian 13, kernel `6.18.34+rpt-rpi-2712 aarch64` |
| Hardware | 4 cores / 7.9 GiB / 470 GB |
| Endpoints | `homeassistant / zigbee / nodered / dockge .kmkdp.com` (this host's Traefik) |
| Managed by | adopted Komodo stack `home-assistant` (files on host, `/opt/kdk-lab/home-assistant`) |
| Blast radius | Home automation offline; lab infra unaffected |

## Access

| Path | How | Use when |
|---|---|---|
| Console | physical keyboard + HDMI | SSH down |
| SSH | `ssh kdkadmin@10.1.30.21` | normal operation |
| Web | `https://dockge.kmkdp.com` | manage the stack |

Admin: `kdkadmin`, key-only, `NOPASSWD:ALL`.

## Verify

```bash
ssh kdkadmin@10.1.30.21 'systemctl is-active periphery'                       # active
ssh kdkadmin@10.1.30.21 'sudo docker ps --format "{{.Names}} {{.Status}}"'    # 6 containers Up
curl -s -o /dev/null -w '%{http_code}\n' https://homeassistant.kmkdp.com/     # 200/302
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Zigbee devices offline | USB coordinator not passed to zigbee2mqtt | check the device mapping in `docker-compose.yml` |
| HA UI 404 at Traefik | container off the proxy net or label missing | inspect this host's Traefik labels |
| MQTT clients can't connect | mosquitto down | `docker restart mosquitto` |

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | Zigbee USB coordinator | device control |
| Needed by | HomeKit bridge on [[kdk-dkr-03]] | accessory state |

## Observability

`SmartHomeEndpointDown` probe from [[kdk-mon-01]]; alerts → Pushover. `node_exporter` — Unverified.

## Standards compliance

| Standard | Required | This host | Note |
|---|---|---|---|
| Hostname | `{site}-{role}-{nn}` | ✅ `kdk-dkr-02` | renamed from old Pi `kdk-dkr-01` |
| Admin account | `kdkadmin`, key-only, NOPASSWD | ✅ | |
| Timezone | America/Chicago | ✅ | CDT |
| NTP | `ntp.kmkdp.com` primary | ⚠️ synced via public pools (timesyncd) | |
| Syslog | `syslog.kmkdp.com:514` | ⚠️ forwards (`*.* @@…:514`), but the receiver is absent | |

## Architecture

Runs its own `traefik:v3.3` on `80/443` for the home-automation names, plus `homeassistant 2026.8`, `zigbee2mqtt 2.9.2`, `node-red 4.1`, `mosquitto 2.0`, `dockge 1`. Periphery on `8120`. These names resolve to `10.1.30.21` (see [[network]]).

### Storage map

`N/A — OS disk only; container config on local volumes.`

## Configuration

| Path | What it is |
|---|---|
| `/opt/kdk-lab/home-assistant/docker-compose.yml` | the adopted stack |
| per-container volumes | HA config, zigbee2mqtt data, node-red flows |

## Secret Rotation

Host holds no lab-managed credential; app secrets live inside the HA stack config.

## Out-of-band changes

Hostname/DNS/UniFi-alias renamed from the old `kdk-dkr-01` at adoption; service names and data untouched.

## Provisioning

Bare-metal Raspberry Pi 5 imaged manually; stack predates the migration and was adopted. Rebuild: reflash, reinstall Docker + periphery, restore `/opt/kdk-lab/home-assistant`.
