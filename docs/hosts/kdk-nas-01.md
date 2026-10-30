---
title: kdk-nas-01
host: kdk-nas-01
fqdn: none
ip: 10.1.3.5
role: storage
tier: infra
status: live
provisioner: baremetal
hypervisor: none
vmid: none
console: TrueNAS UI; iDRAC/physical
standards: out-of-scope (not a Komodo compose host)
secrets: []
updated: 2026-10-30
tags: [homelab/host, role/storage, status/live, tier/infra]
---

# kdk-nas-01 — TrueNAS storage

Dell PowerEdge R730xd running TrueNAS; the lab's primary storage and NFS server.
Not a Komodo compose host — it is referenced by the stacks that mount its
datasets.

**Related:** [[network]] · [[backups]] · [[kdk-dkr-dmz-01]]

## At a glance

| Field | Value |
|---|---|
| Purpose | Bulk storage, NFS server, SMB shares, TrueNAS VM host |
| Host | Dell PowerEdge R730xd (bare metal) |
| Address | `10.1.3.5` (VLAN 1); storage NIC `br0` alias at `172.16.30.2` (NFS net) |
| NFS | server for `172.16.30.0/24` (jumbo MTU 9000); per-host backup datasets `tankz3/backup/dkr/<host>`; clients `172.16.30.50` (dkr-01), `.51` (dmz-01) |
| SMB | bound to `10.1.3.5` (not offered on the storage bridge) — e.g. `\\10.1.3.5\dee-exchange` for [[kdk-dee-01]] |
| Guests | TrueNAS VMs: [[kdk-dkr-dmz-01]] and `kdk-pbs-01` |
| Fan/BMC | R730xd BMC steered over IPMI by the `fanctl-nas` controller on [[kdk-mon-01]]; drive-temp watchdog `/root/kdk-temp-watch.sh` |
| Blast radius | NFS/SMB offline lab-wide; backup targets unreachable |

## Notes

This page is a reference stub for the facts the repo already knows. The NFS and
SMB server configuration lives in TrueNAS (`nfs.config`, `sharing.nfs`,
`sharing.smb`), not in this repo. See [[network]] for the storage-network layout
and [[backups]] for the backup datasets.
