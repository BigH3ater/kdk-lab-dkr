---
title: kdk-dkr-03
host: kdk-dkr-03
fqdn: none
ip: 10.1.30.22
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

# kdk-dkr-03 — HomeKit / Scrypted Pi

A Raspberry Pi 5 running Scrypted and Homebridge for HomeKit. Renamed from the old Pi `kdk-dkr-02`; adopted into Komodo in place.

**Related:** [[kdk-dkr-02]] · [[komodo]]

## At a glance

| Field | Value |
|---|---|
| Purpose | Scrypted (cameras) + Homebridge (HomeKit) |
| Status | 🟢 Live — periphery active; both containers up (2026-09-12) |
| Host | Raspberry Pi 5 (bare metal) |
| Address | `10.1.30.22` (VLAN 30) |
| OS | Debian 13, kernel `6.18.34+rpt-rpi-2712 aarch64` |
| Hardware | 4 cores / 7.9 GiB / 470 GB |
| Endpoints | `scrypted / homebridge .kmkdp.com` (resolve to the Pi Traefik on [[kdk-dkr-02]], see [[network]]) |
| Managed by | adopted Komodo stack `scrypted-homebridge` (files on host) |
| Blast radius | HomeKit + camera integration offline; lab infra unaffected |

## Access

| Path | How | Use when |
|---|---|---|
| Console | physical keyboard + HDMI | SSH down |
| SSH | `ssh kdkadmin@10.1.30.22` | normal operation |

Admin: `kdkadmin`, key-only, `NOPASSWD:ALL`.

## Verify

```bash
ssh kdkadmin@10.1.30.22 'systemctl is-active periphery'                       # active
ssh kdkadmin@10.1.30.22 'sudo docker ps --format "{{.Names}} {{.Status}}"'    # scrypted + homebridge Up
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| HomeKit accessories unresponsive | Homebridge container down | `docker restart homebridge` |
| Cameras missing in HomeKit | Scrypted plugin/bridge issue | check the Scrypted management console |
| HomeKit won't re-pair | mDNS/host-network conflict | verify Homebridge is on host networking |

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | camera devices on the LAN | Scrypted feeds |
| Needed by | Apple Home | HomeKit accessory state |

## Observability

`SmartHomeEndpointDown` probe from [[kdk-mon-01]]; alerts → Pushover. `node_exporter` — Unverified.

## Standards compliance

| Standard | Required | This host | Note |
|---|---|---|---|
| Hostname | `{site}-{role}-{nn}` | ✅ `kdk-dkr-03` | renamed from old Pi `kdk-dkr-02` |
| Admin account | `kdkadmin`, key-only, NOPASSWD | ✅ | |
| Timezone | America/Chicago | ✅ | CDT |
| NTP | `ntp.kmkdp.com` primary | ⚠️ synced via public pools (timesyncd) | |
| Syslog | `syslog.kmkdp.com:514` | ⚠️ forwards (`*.* @@…:514`), but the receiver is absent | |

## Architecture

Runs `scrypted v0.123.0` and `homebridge 2026-08-17` (both typically on host networking for mDNS/HomeKit). Periphery on `8120`. The adopted Komodo stack points at `/opt/kdk-lab/scrypted-homebridge`; the running containers also have separate `scrypted/` and `homebridge/` directories on disk.

### Storage map

`N/A — OS disk only; container config on local volumes.`

## Configuration

| Path | What it is |
|---|---|
| `/opt/kdk-lab/scrypted-homebridge/docker-compose.yml` | the adopted stack path |
| `/opt/kdk-lab/{scrypted,homebridge}/` | per-app config/state on disk |

## Secret Rotation

Host holds no lab-managed credential; HomeKit pairing state lives in the app config.

## Out-of-band changes

Hostname/DNS/UniFi-alias renamed from the old `kdk-dkr-02` at adoption; service names and data untouched.

## Provisioning

Bare-metal Raspberry Pi 5 imaged manually; stack predates the migration and was adopted. Rebuild: reflash, reinstall Docker + periphery, restore the Scrypted/Homebridge config (HomeKit accessories re-pair on a bridge identity change).
