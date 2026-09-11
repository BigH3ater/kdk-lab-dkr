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
| kdk-nas-01 | 10.1.3.5 (storage 100.100.100.2) | 1 | bare metal (R730xd) | — |
| kdk-dkr-01 | 10.1.20.20 (storage 100.100.100.50) | 20 | VM on kdk-hyp-01 | 300 |
|  kdk-dkr-dmz-01 | 192.168.191.20 ( storage 100.100.100.51) | 191 | VM 23 on kdk-nas-01 | — |
| kdk-dkr-02 (Pi, HA) | 10.1.30.21 | 30 | bare metal Pi 5 | — |
| kdk-dkr-03 (Pi, HomeKit) | 10.1.30.22 | 30 | bare metal Pi 5 | — |
| kdk-mon-01 | 10.1.20.30 | 20 | bare metal Pi 5 | — |
| kdk-dns-01/02/03 | 10.1.1.120/.121/.122 (VIP .53/.54) | 10 | VM / NAS VM / Pi | 210/—/— |
| kdk-dee-01 | 10.1.1.130 | 10 | VM on kdk-hyp-01 | 220 |
| kdk-pbs-01 | 10.1.1.102 | 10 | VM 23 on kdk-nas-01 | — |

## DNS (AdGuard rewrites, end state)

| Record | Answer | Serves |
|---|---|---|
| *.kmkdp.com | 10.1.20.20 | everything on internal Traefik (kdk-dkr-01) |
| sso, seerr, jellyfin, immich, users .kmkdp.com | 192.168.191.20 | DMZ Traefik (kdk-dkr-dmz-01) |
| homeassistant, zigbee, nodered, dockge, scrypted, homebridge .kmkdp.com | 10.1.30.21 | Pi Traefik (kdk-dkr-02) |

Public (via Cloudflare tunnel → DMZ Traefik): sso, seerr, jellyfin, immich.
