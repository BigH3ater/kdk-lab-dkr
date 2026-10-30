---
title: kdk-hyp-01
host: kdk-hyp-01
fqdn: none
ip: 10.1.1.100
role: hypervisor
tier: infra
status: live
provisioner: baremetal
hypervisor: none
vmid: none
console: Proxmox UI → Datacenter → kdk-hyp-01; iDRAC/physical
standards: out-of-scope (not a Komodo compose host)
secrets: []
updated: 2026-10-30
tags: [homelab/host, role/hypervisor, status/live, tier/infra]
---

# kdk-hyp-01 — Proxmox hypervisor

Dell PowerEdge R720 running Proxmox VE; the hypervisor for the lab's Linux VMs.
Not a Komodo compose host (no periphery) — it is referenced by the stacks and
hosts that run on top of it.

**Related:** [[kdk-dkr-01]] · [[kdk-log-01]] · [[network]]

## At a glance

| Field | Value |
|---|---|
| Purpose | Proxmox VE hypervisor for the lab's Linux VMs |
| Host | Dell PowerEdge R720 (bare metal) |
| Address | `10.1.1.100` (VLAN 10, management); storage NIC `vmbr1` at `172.16.30.3` (NFS net) |
| Guests | VM 300 → [[kdk-dkr-01]], VM 320 → [[kdk-log-01]], VM 220 → [[kdk-dee-01]]; template VM `9000` (Debian 13 cloud-init base) |
| Backups | nightly Proxmox → PBS (`kdk-pbs-01`) vzdump; see [[backups]] |
| Fan/BMC | R720 BMC steered over IPMI by the `fanctl-hyp` controller on [[kdk-mon-01]] |
| Blast radius | Every VM it hosts goes down |

## Notes

This page is a reference stub for the facts the repo already knows. The
hypervisor is administered through the Proxmox UI and iDRAC, not through this
repo; VM lifecycle for the Komodo hosts is scripted (e.g.
`scripts/create-vm-kdk-log-01.sh`) and the template is VM `9000`.
