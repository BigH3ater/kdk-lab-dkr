#!/usr/bin/env bash
# Build kdk-log-01 (VM 320) on kdk-hyp-01: the lab log host (Loki + syslog).
# Run ON the hypervisor as kdkadmin, after copying
# cloud-init/kdk-log-01-vendor-data.yaml to /var/lib/vz/snippets/.
# Idempotence: refuses if VM 320 exists.
#
# Differences from kdk-dkr-01: no storage-net NIC (Loki data stays on the VM
# disk, backed up by the PBS vzdump of VM 320), no GPU, 4 vCPU / 8 GiB.
set -euo pipefail

VMID=320
NAME=kdk-log-01
SNIPPET=/var/lib/vz/snippets/kdk-log-01-vendor-data.yaml

sudo qm status $VMID >/dev/null 2>&1 && { echo "VM $VMID already exists"; exit 1; }
[ -f "$SNIPPET" ] || { echo "missing $SNIPPET (copy cloud-init/kdk-log-01-vendor-data.yaml first)"; exit 1; }

sudo qm clone 9000 $VMID --name $NAME --full --storage local-zfs
sudo qm set $VMID \
  --cores 4 --memory 8192 --cpu host --machine q35 \
  --net0 virtio,bridge=vmbr0,tag=20 \
  --ipconfig0 ip=10.1.20.21/27,gw=10.1.20.1 \
  --nameserver 10.1.1.53 --searchdomain kmkdp.com \
  --cicustom vendor=local:snippets/kdk-log-01-vendor-data.yaml \
  --agent enabled=1 \
  --onboot 1
# Grow the template disk to 200 GiB total (absolute size; qm refuses to shrink,
# so a template disk already >= 200G just keeps its size).
sudo qm resize $VMID scsi0 200G || echo "WARN: resize to 200G refused; check: sudo qm config $VMID | grep scsi0"
sudo qm start $VMID
echo "Started $NAME ($VMID). Wait for: ssh kdkadmin@10.1.20.21 cloud-init status --wait"
