---
title: kdk-dkr-dmz-01
host: kdk-dkr-dmz-01
fqdn: none
ip: 192.168.191.20
role: docker-host
tier: dmz
status: live
provisioner: truenas-vm
hypervisor: kdk-nas-01
vmid: 23
console: TrueNAS UI → Virtualization → kdkdkrdmz01 → Console
standards: drifted
secrets: []
updated: 2026-09-12
tags: [homelab/host, role/docker-host, status/live, tier/dmz]
---

# kdk-dkr-dmz-01 — DMZ compose host

The DMZ Docker host: DMZ Traefik + cloudflared, Authelia/LLDAP identity, Seerr, Jellyfin (Arc A380 QSV), and a segmented Immich.

**Related:** [[kdk-nas-01]] · [[dmz-proxy]] · [[identity]] · [[jellyfin]]

## At a glance

| Field | Value |
|---|---|
| Purpose | Runs the DMZ/public stacks and lab identity |
| Status | 🟢 Live — periphery active, `/dev/dri` present (2026-09-12) |
| Host | VM 23 on kdk-nas-01 (TrueNAS `vm.*` API) |
| Address | `192.168.191.20/24` (VLAN 191, br191) · storage `100.100.100.51` |
| OS | Debian 13, kernel `6.12.107+deb13-amd64` (full kernel — required for i915) |
| Hardware | 8 vCPU / 23 GiB / 59 GB vmpool zvol · Intel Arc A380 passthrough |
| Managed by | cloud-init (NoCloud seed ISO) at build; Komodo periphery thereafter |
| Blast radius | All public services + all lab authentication |

## Access

| Path | How | Use when |
|---|---|---|
| Console | TrueNAS UI → VM `kdkdkrdmz01` → Console | SSH down |
| SSH | `ssh kdkadmin@192.168.191.20` | normal operation |

Admin: `kdkadmin`, key-only, `NOPASSWD:ALL`.

## Verify

```bash
ssh kdkadmin@192.168.191.20 'systemctl is-active periphery'   # active
ssh kdkadmin@192.168.191.20 'ls /dev/dri'                     # card0 card1 renderD128
curl -s -o /dev/null -w '%{http_code}\n' https://sso.kmkdp.com/   # 200
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| No `/dev/dri` | booted the cloud kernel | boot full `linux-image-amd64` |
| Deploys fail rendering `.env` | can't reach Connect | needs firewall `192.168.191.20 → 10.1.20.20:8080` |
| Seerr can't reach arr | 443 to internal closed | needs `192.168.191.20 → 10.1.20.20:443` (open as of 2026-09-12) |

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | [[kdk-nas-01]] | the VM + its NFS |
| Needs | [[op-connect]] (`:8080`) | secret rendering for deploys |
| Needed by | LAN + public | all `*.kmkdp.com` public services + SSO |

## Observability

Public hostnames probed by `blackbox-http-app` on [[kdk-mon-01]]; alerts → Pushover. `node_exporter` — Unverified.

## Standards compliance

| Standard | Required | This host | Note |
|---|---|---|---|
| Hostname | `{site}-{role}-{nn}` | ✅ `kdk-dkr-dmz-01` | |
| Admin account | `kdkadmin`, key-only, NOPASSWD | ✅ | |
| Timezone | America/Chicago | ✅ | CDT |
| NTP | `ntp.kmkdp.com` primary | ⚠️ synced via public pools (timesyncd) | |
| Syslog | `syslog.kmkdp.com:514` | ⚠️ no forward rule (cloud-init gap); receiver also absent | |

## Architecture

VLAN 191 (DMZ). Traefik `80/443`, periphery `8120`. Outbound: cloudflared to Cloudflare; DMZ→internal to `10.1.20.20:8080` (Connect) and `:443` (arr APIs via Seerr). Storage NIC `100.100.100.51` for NFS.

### Storage map

| Mount | Source | FS | Consumed by | Backup |
|---|---|---|---|---|
| `/mnt/media` (RO) | `100.100.100.2:/mnt/tankz3/media` | NFS | Jellyfin | tankz3 snapshots |
| `/mnt/appdata` | `…/appdata` | NFS | Seerr, Immich library | snapshots |
| `/mnt/backup` | `…/backup` | NFS | backup-dmz | snapshots |
| `/opt/kdk-lab` | vmpool zvol | zfs | identity, immich, jellyfin config | nightly rsync + pg dumps → NAS; zvol replication |

## Configuration

| Path | What it is |
|---|---|
| `/opt/kdk-lab/<stack>` | per-stack state (incl. Authelia storage key, paired with its DB) |
| `/etc/komodo/op.env` | Connect host + token |

## Secret Rotation

Host holds no credential of its own; stack secrets are in [[identity]], [[immich]], [[dmz-proxy]].

## Out-of-band changes

Arc A380 (`0000:84:00.0` + audio) passed through via `midclt vm.device.create`. Full kernel installed by cloud-init. None outside that.

## Provisioning

Built by `scripts/create-vm-kdk-dkr-dmz-01.sh` + `cloud-init/kdk-dkr-dmz-01-user-data.yaml` (NoCloud seed ISO, MAC-matched netplan). Rebuild: replay the script; state restores from `tankz3/backup/dkr/kdk-dkr-dmz-01` + pg dumps; Authelia storage key and its DB restore together.
