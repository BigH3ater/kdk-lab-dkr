---
title: kdk-dkr-01
host: kdk-dkr-01
fqdn: none
ip: 10.1.20.20
role: docker-host
tier: internal
status: live
provisioner: proxmox-template
hypervisor: kdk-hyp-01
vmid: 300
console: Proxmox → VM 300 → Console
standards: drifted
secrets: []
updated: 2026-09-12
tags: [homelab/host, role/docker-host, status/live, tier/internal]
---

# kdk-dkr-01 — main compose host

The internal Docker host: Komodo control plane, 1Password Connect, internal Traefik, docs, media (Tdarr NVENC), and the apps stack.

**Related:** [[kdk-hyp-01]] · [[komodo]] · [[traefik]] · [[media]]

## At a glance

| Field | Value |
|---|---|
| Purpose | Runs all internal stacks + the Komodo control plane |
| Status | 🟢 Live — periphery active, all stacks up (2026-09-12) |
| Host | VM 300 on kdk-hyp-01 |
| Address | `10.1.20.20/27` (VLAN 20) · storage `172.16.30.50` (vmbr1, MTU 9000) |
| OS | Debian 13, kernel `6.12.107+deb13-amd64` (full kernel — required for the NVIDIA driver) |
| Hardware | 12 vCPU / 47 GiB / 571 GB local-zfs · GTX 1050 Ti (hostpci) |
| Managed by | cloud-init at build; Komodo periphery thereafter |
| Runs | komodo, op-connect, proxy, docs, media, apps, backup-dkr-01, **loki** + Alloy syslog (:3100/:514), observability-agent, node-exporter |
| Blast radius | All internal services + new deploys lab-wide; also lab log ingestion (Loki) |

## Access

| Path | How | Use when |
|---|---|---|
| Console | Proxmox → VM 300 → Console | SSH down |
| SSH | `ssh kdkadmin@10.1.20.20` | normal operation |

Admin: `kdkadmin`, key-only, `NOPASSWD:ALL`.

## Verify

```bash
ssh kdkadmin@10.1.20.20 'sudo -n true && echo ok'          # ok
ssh kdkadmin@10.1.20.20 'systemctl is-active periphery'    # active
ssh kdkadmin@10.1.20.20 'nvidia-smi -L'                    # GTX 1050 Ti
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `nvidia-smi` fails | cloud kernel has no GPU driver | boot the full `linux-image-amd64` kernel |
| A stack won't deploy | `op inject` can't reach Connect | `curl http://10.1.20.20:8080/heartbeat` → `.` |
| NFS mounts missing | storage NIC / NFS down | `mount | grep 172.16.30.2` |

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | [[kdk-hyp-01]] | the VM itself |
| Needs | kdk-nas-01 NFS (172.16.30.2) | media, backup, dee-exchange |
| Needed by | [[kdk-dkr-dmz-01]] and all hosts | Komodo control + 1P Connect |

## Observability

Host reachability via `blackbox-http-app` on [[kdk-mon-01]]; alerts → Pushover. `node_exporter` scrape (`node-external` job) — Unverified; confirm the target on kdk-mon-01.

## Standards compliance

| Standard | Required | This host | Note |
|---|---|---|---|
| Hostname | `{site}-{role}-{nn}` | ✅ `kdk-dkr-01` | |
| Admin account | `kdkadmin`, key-only, NOPASSWD | ✅ | |
| Timezone | America/Chicago | ✅ | CDT |
| NTP | `ntp.kmkdp.com` primary | ⚠️ synced via public pools (timesyncd), not the internal server | |
| Syslog | `syslog.kmkdp.com:514` | ⚠️ no forward rule set (cloud-init gap); receiver also absent | |

## Architecture

VLAN 20 for services (`80/443` Traefik, `8080` Connect bound to the VLAN address, `8120` periphery, `8266` Tdarr node RPC, `123/udp` NTP, `25` proton-bridge). Storage NIC on vmbr1 (MTU 9000) for NFS.

### Storage map

| Mount | Source | FS | Consumed by | Backup |
|---|---|---|---|---|
| `/mnt/media` | `172.16.30.2:/mnt/tankz3/media` | NFS | media, jellyfin(RO) | tankz3 snapshots |
| `/mnt/dee-exchange` | `…/dee-exchange` | NFS | Tdarr DEE | snapshots |
| `/mnt/backup` | `…/backup` | NFS | backup stacks | snapshots |
| `/opt/kdk-lab` | local-zfs | zfs | all stack config | nightly rsync → NAS |

## Configuration

| Path | What it is |
|---|---|
| `/opt/kdk-lab/<stack>` | per-stack bind-mount state |
| `/etc/komodo/op.env` | Connect host + token for `op inject` |
| `/etc/kdk-lab/1password-credentials.json` | Connect credentials (999:999, 0600) |

## Secret Rotation

Host holds no credential of its own beyond the Connect files ([[op-connect]]).

## Out-of-band changes

GTX 1050 Ti `hostpci` attached to VM 300 (detached from the old Plex VM). NVIDIA driver + container toolkit installed by cloud-init. None outside that.

## Provisioning

Built by `scripts/create-vm-kdk-dkr-01.sh` + `cloud-init/kdk-dkr-01-vendor-data.yaml` (`qm clone 9000`). Rebuild: replay the script; app state restores from `tankz3/backup/dkr/kdk-dkr-01`.
