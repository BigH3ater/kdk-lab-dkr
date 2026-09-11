#!/usr/bin/env bash
# Build kdk-dkr-01 (VM 300) on kdk-hyp-01. Run ON the hypervisor as kdkadmin.
# Idempotence: refuses if VM 300 exists.
set -euo pipefail

VMID=300
NAME=kdk-dkr-01

sudo qm status $VMID >/dev/null 2>&1 && { echo "VM $VMID already exists"; exit 1; }

# Free the GTX 1050 Ti from the stopped Plex VM (103) if still attached.
if sudo qm config 103 2>/dev/null | grep -q '^hostpci'; then
  sudo qm set 103 --delete hostpci0
fi

sudo qm clone 9000 $VMID --name $NAME --full
sudo qm set $VMID \
  --cores 12 --memory 49152 --cpu host --machine q35 \
  --net0 virtio,bridge=vmbr0,tag=20 \
  --net1 virtio,bridge=vmbr1,mtu=9000 \
  --ipconfig0 ip=10.1.20.20/27,gw=10.1.20.1 \
  --ipconfig1 ip=100.100.100.50/24 \
  --nameserver 10.1.1.53 --searchdomain kmkdp.com \
  --hostpci0 0000:42:00,pcie=1 \
  --cicustom vendor=local:snippets/kdk-dkr-01-vendor-data.yaml \
  --agent enabled=1 \
  --onboot 1
sudo qm resize $VMID scsi0 +280G
sudo qm start $VMID
echo "Started $NAME ($VMID). Cloud-init will take several minutes (nvidia driver build)."
