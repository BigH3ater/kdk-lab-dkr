---
title: kdk-dkr-dmz-01
---
# kdk-dkr-dmz-01 — DMZ compose host

| | |
|---|---|
| Where | VM 23 on kdk-nas-01 (TrueNAS `vm.*` API), 8 vCPU / 24G / 60G vmpool zvol |
| Net | 192.168.191.20/24 VLAN 191 (br191) · storage 100.100.100.51 (br0) |
| GPU | Intel Arc A380 passthrough (pci_0000_84_00_0 + audio) — QSV for Jellyfin. Needs `linux-image-amd64`; the cloud kernel has no i915 |
| Built | `scripts/create-vm-kdk-dkr-dmz-01.sh` + `cloud-init/kdk-dkr-dmz-01-user-data.yaml` (NoCloud seed ISO, MAC-matched netplan) |
| Mounts | /mnt/media (ro), /mnt/appdata, /mnt/backup |
| Runs | dmz-proxy, identity, dmz-apps, jellyfin, immich, backup-dmz |
| Access | `ssh kdkadmin@192.168.191.20`; console TrueNAS UI → VM kdkdkrdmz01 |

Firewall: LAN→DMZ 443 allowed; DMZ VM→10.1.20.20:8080 (1P Connect) required
for Komodo pre-deploy secret injection.
