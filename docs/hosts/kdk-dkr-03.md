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
updated: 2026-10-28
tags: [homelab/host, role/docker-host, status/live, tier/internal]
---

# kdk-dkr-03 — HomeKit / Scrypted Pi

A Raspberry Pi 5 running Scrypted (cameras + its own HomeKit bridge). Renamed from the old Pi `kdk-dkr-02`; adopted into Komodo in place. Homebridge was removed 2026-10-28: it served zero accessories and its UI setup was never claimed.

**Related:** [[kdk-dkr-02]] · [[komodo]]

## At a glance

| Field | Value |
|---|---|
| Purpose | Scrypted (cameras, HomeKit bridge) |
| Status | 🟢 Live — periphery active; scrypted up |
| Host | Raspberry Pi 5 (bare metal) |
| Address | `10.1.30.22` (VLAN 30) |
| OS | Debian 13, kernel `6.18.34+rpt-rpi-2712 aarch64` |
| Hardware | 4 cores / 7.9 GiB / 470 GB |
| Endpoints | `scrypted.kmkdp.com` (resolves to the Pi Traefik on [[kdk-dkr-02]], see [[network]]) |
| Managed by | GitOps Komodo stack `scrypted-homebridge` (`stacks/scrypted-homebridge/`; name kept — a rename destroys and recreates the stack) |
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
ssh kdkadmin@10.1.30.22 'sudo docker ps --format "{{.Names}} {{.Status}}"'    # scrypted Up
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| HomeKit accessories unresponsive | Scrypted container down | `docker restart scrypted` |
| Cameras missing in HomeKit | Scrypted plugin/bridge issue | check the Scrypted management console |
| HomeKit won't re-pair | mDNS/host-network conflict | verify Scrypted is on host networking |

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
| SSH hardening | `98-kdk-admin.conf` + kdkadmin key `from=` admin sources | ⚠️ drop-in present; ⏳ pending apply (2026-10-28) for the repo copy (adds `LoginGraceTime 20`) and the key `from=` | `hosts/README.md` |
| Kernel sysctl | `90-kdk-hardening.conf` (ptrace_scope 1, kptr/dmesg restrict, no redirects, martians, sysrq 0) | ⏳ pending apply (2026-10-28) | |
| Patching | unattended-upgrades: Debian + Raspberry Pi Foundation + Docker CE, reboot 04:30 | ⏳ pending apply (2026-10-28) | Pi Foundation origin missing today: kernel/libc/OpenSSL never auto-apply |
| Docker daemon | `daemon.json`: log rotation 10m×3, `live-restore`, `no-new-privileges`, `userland-proxy: false`, `icc: false` | ⏳ pending apply (2026-10-28) | [[docker-daemon]]; never recreate `nut` as part of the rollout |
| Host firewall | storage-net fence + DOCKER-USER unit (VMs with a storage NIC) | n/a | no storage NIC; `DOCKER-USER` empty |
| Timezone | America/Chicago | ✅ | CDT |
| NTP | `ntp.kmkdp.com` primary | ⚠️ synced via public pools (timesyncd) | |
| Syslog | `syslog.kmkdp.com:514` | ✅ forwards (`*.* @@…:514`) to the receiver on [[kdk-log-01]] | restart rsyslog once after the DNS flip so the open TCP session re-resolves |

## Architecture

Runs `scrypted v0.147.0` (`-noble-full`; upstream stopped `-jammy-*` tags at v0.123.58) on host networking for mDNS/HomeKit. 0.147.0 is the fix for GHSA-fgj8-7r76-h852 (unauthenticated admin RPC, ≤ 0.146.1). Periphery on `8120`. Scrypted's UI ports (10443/11080) are open to VLAN 30: the planned UniFi rule was dropped because dkr-02 and dkr-03 share the VLAN (a gateway rule cannot filter same-VLAN traffic).

### Storage map

`N/A — OS disk only; container config on local volumes.`

## Configuration

| Path | What it is |
|---|---|
| `stacks/scrypted-homebridge/compose.yaml` (repo) | the stack definition |
| `/opt/kdk-lab/scrypted/volume` | Scrypted config/state on disk |
| `/opt/kdk-lab/_archive/homebridge-2026-10-28` | archived Homebridge data (removed service) |

## Secret Rotation

Host holds no lab-managed credential; HomeKit pairing state lives in the app config.

## Out-of-band changes

Hostname/DNS/UniFi-alias renamed from the old `kdk-dkr-02` at adoption; service names and data untouched.

## Provisioning

Bare-metal Raspberry Pi 5 imaged manually; stack predates the migration and was adopted. Rebuild: reflash, reinstall Docker + periphery, restore the Scrypted config (HomeKit accessories re-pair on a bridge identity change).
