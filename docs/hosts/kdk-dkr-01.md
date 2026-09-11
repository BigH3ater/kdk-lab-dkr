---
title: kdk-dkr-01
---
# kdk-dkr-01 — main compose host

| | |
|---|---|
| Where | VM 300 on kdk-hyp-01, 12 vCPU / 48G / 580G local-zfs |
| Net | 10.1.20.20/27 VLAN 20 · storage 100.100.100.50 (vmbr1, mtu 9000) |
| GPU | GTX 1050 Ti (hostpci, attached at media cutover) — NVENC for Tdarr |
| Built | `scripts/create-vm-kdk-dkr-01.sh` + `cloud-init/kdk-dkr-01-vendor-data.yaml` |
| Mounts | /mnt/media, /mnt/dee-exchange, /mnt/backup (NFS 100.100.100.2) |
| Runs | komodo, op-connect, proxy, docs, media, apps, backup-dkr-01 |
| Access | `ssh kdkadmin@10.1.20.20`; console Proxmox VM 300 |

Rebuild: replay the create script; app state restores from tankz3/backup.
