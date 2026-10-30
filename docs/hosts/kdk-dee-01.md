---
title: kdk-dee-01
host: kdk-dee-01
fqdn: none
ip: 10.1.1.130
role: windows-vm
tier: infra
status: live
provisioner: proxmox-vm
hypervisor: kdk-hyp-01
vmid: 220
console: Proxmox UI → kdk-hyp-01 → VM 220 → Console
standards: out-of-scope (Windows VM, not a Komodo compose host)
secrets: [kdk-dee-01-api-token, kdk-dee-01-admin]
updated: 2026-10-30
tags: [homelab/host, role/windows-vm, status/live, tier/infra]
---

# kdk-dee-01 — Windows DEE VM

Windows 11 VM running the **DeeZy wrapper** — a FastAPI service on `:8484` that
encodes TrueHD/DTS-HD MA lossless audio to Dolby DD+ (`.ec3`) for Tdarr. It is
the dependency behind every lossless-audio transcode.

**Related:** [[kdk-hyp-01]] · [[media]] · [Troubleshoot the Windows DEE VM](../runbooks/windows-vm-dee.md)

## At a glance

| Field | Value |
|---|---|
| Purpose | DeeZy wrapper (Dolby DD+ encode) for Tdarr lossless-audio jobs |
| Host | VM 220 on [[kdk-hyp-01]] (Windows 11) |
| Address | `10.1.1.130` (VLAN 10) |
| Service | `dee-wrapper` scheduled task (BootTrigger), FastAPI on `:8484`; health at `http://10.1.1.130:8484/healthz` |
| Exchange | `\\10.1.3.5\dee-exchange` (TrueNAS SMB on [[kdk-nas-01]]) |
| Blast radius | Tdarr lossless-audio transcodes error until it is back |

## Notes

This page is a reference stub. The wrapper, its scheduled task and config live on
the Windows host, not in this repo. Full runbook:
[Troubleshoot the Windows DEE VM](../runbooks/windows-vm-dee.md).
