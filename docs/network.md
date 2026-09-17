---
title: Network & addresses
---
# Network & addresses

Address registry. Git history is the audit trail; edit this table in the same
PR as any change that adds or moves an address.

## Hosts

| Host | IP | VLAN | Where | VMID |
|---|---|---|---|---|
| kdk-hyp-01 | 10.1.1.100 | 10 | bare metal (R720) | — |
| kdk-nas-01 | 10.1.3.5 (storage 172.16.30.2) | 1 | bare metal (R730xd) | — |
| kdk-dkr-01 | 10.1.20.20 (storage 172.16.30.50) | 20 | VM on kdk-hyp-01 | 300 |
|  kdk-dkr-dmz-01 | 192.168.191.20 ( storage 172.16.30.51) | 191 | VM 23 on kdk-nas-01 | — |
| kdk-dkr-02 (Pi, HA) | 10.1.30.21 | 30 | bare metal Pi 5 | — |
| kdk-dkr-03 (Pi, HomeKit) | 10.1.30.22 | 30 | bare metal Pi 5 | — |
| kdk-mon-01 | 10.1.20.30 | 20 | bare metal Pi 5 | — |
| kdk-dns-01/02/03 | 10.1.1.120/.121/.122 (VIP .53/.54) | 10 | VM / NAS VM / Pi | 210/—/— |
| kdk-dee-01 | 10.1.1.130 | 10 | VM on kdk-hyp-01 | 220 |
| kdk-pbs-01 | 10.1.1.102 | 10 | VM 23 on kdk-nas-01 | — |

## Storage network (NFS)

Storage/NFS runs on its own flat L2 (bond1/br0 on the NAS, jumbo MTU 9000):
**`172.16.30.0/24`**, NFS server `kdk-nas-01` at **`172.16.30.2`**.

| Host | Storage IP |
|---|---|
| kdk-nas-01 (NFS server, `br0` alias) | 172.16.30.2 |
| kdk-hyp-01 (`vmbr1`) | 172.16.30.3 |
| kdk-dkr-01 (`eth1`) | 172.16.30.50 |
| kdk-dkr-dmz-01 (`eth1`) | 172.16.30.51 |

> ⚠️ **Do not use `100.64.0.0/10` for storage (or any LAN).** It was `100.100.100.0/24`
> until 2026-09-16, which sits **inside Tailscale's CGNAT range `100.64.0.0/10`**. When the
> Tailscale subnet router came up on kdk-dkr-01 (host networking), Tailscale's `ts-input`
> anti-spoof rule (`! -i tailscale0 -s 100.64.0.0/10 -j DROP`) silently dropped every NFS
> reply on the storage NIC → hard-mount hangs → SABnzbd/Chaptarr/media wedged. Renumbering
> off CGNAT is the permanent fix. `172.16.0.0/16` is the one part of `172.16.0.0/12` free of
> Docker's default bridge pool (`172.17`–`172.31`). NFS server config lives in TrueNAS
> (`nfs.config` bindip + `sharing.nfs` export networks); client mounts are systemd
> `.mount` units seeded from `cloud-init/`. See `docs/runbooks/storage-network.md`.

## DNS (AdGuard rewrites, end state)

| Record | Answer | Serves |
|---|---|---|
| *.kmkdp.com | 10.1.20.20 | everything on internal Traefik (kdk-dkr-01) |
| sso, seerr, jellyfin, immich, users .kmkdp.com | 192.168.191.20 | DMZ Traefik (kdk-dkr-dmz-01) |
| homeassistant, zigbee, nodered, dockge, scrypted, homebridge .kmkdp.com | 10.1.30.21 | Pi Traefik (kdk-dkr-02) |

Public (via Cloudflare tunnel → DMZ Traefik): sso, seerr, jellyfin, immich.
