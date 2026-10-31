#!/usr/bin/env bash
# Build kdk-dkr-04 on kdk-nas-01 (TrueNAS, `vm.*` API via midclt over SSH).
# Run from the admin workstation (needs `ssh kdkadmin@10.1.3.5`, scp, qemu-img
# only if you convert locally, and either genisoimage/xorriso OR `pip install
# pycdlib` for the seed ISO).
#
# Shape follows hosts/kdk-dkr-dmz-01 (zvol + qemu-img convert, NoCloud seed ISO
# with a MAC-matched network-config, vm.create cpu_mode HOST-MODEL) but with NO
# PCI passthrough and the NICs on br20 (VLAN 20) + br0 (storage). The network-
# config is MAC-matched, so we pick the NIC MACs up front and build the ISO with
# them before vm.create.
#
# Idempotence: refuses if VM `kdkdkr04` already exists.
set -euo pipefail

NAS=kdkadmin@10.1.3.5
VM_NAME=kdkdkr04
HOST=kdk-dkr-04
ZVOL=vmpool/vm-kdk-dkr-04-os
ZVOL_DEV=/dev/zvol/${ZVOL}
ZVOL_BYTES=$((120 * 1024 * 1024 * 1024))   # 120 GiB
VCPU_CORES=12
MEM_MIB=32768                              # 32 GiB (24576 if memory is short)
BR20_MAC=00:a0:98:dd:04:20
BR0_MAC=00:a0:98:dd:04:52
ISO_DIR=/mnt/tankz3/isos
SEED_ISO=${ISO_DIR}/${HOST}-seed.iso
IMG_URL=https://cloud.debian.org/images/cloud/trixie/latest/debian-13-genericcloud-amd64.qcow2
IMG_SUMS=https://cloud.debian.org/images/cloud/trixie/latest/SHA512SUMS
WORK=/mnt/tankz3/isos/.kdk-dkr-04-build
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"

say() { echo "== $*"; }

# 0) refuse if the VM already exists --------------------------------------------
if ssh "$NAS" "sudo midclt call vm.query '[[\"name\",\"=\",\"$VM_NAME\"]]'" \
     | grep -q '"id"'; then
  echo "VM $VM_NAME already exists on the NAS -- refusing (rollback first)"; exit 1
fi

# 1) zvol ------------------------------------------------------------------------
say "create zvol $ZVOL ($ZVOL_BYTES bytes, 16K blocks, sparse)"
ssh "$NAS" "sudo midclt call pool.dataset.create '{
  \"name\": \"$ZVOL\", \"type\": \"VOLUME\",
  \"volsize\": $ZVOL_BYTES, \"volblocksize\": \"16K\", \"sparse\": true
}' >/dev/null && echo zvol-created"

# 2) write the Debian 13 genericcloud image onto the zvol -----------------------
say "download + verify + convert the Debian 13 image onto $ZVOL_DEV"
ssh "$NAS" "set -euo pipefail
  sudo mkdir -p '$WORK'; cd '$WORK'
  sudo curl -fsSL -o img.qcow2 '$IMG_URL'
  sudo curl -fsSL -o SHA512SUMS '$IMG_SUMS'
  want=\$(grep 'debian-13-genericcloud-amd64.qcow2\$' SHA512SUMS | awk '{print \$1}')
  got=\$(sudo sha512sum img.qcow2 | awk '{print \$1}')
  [ -n \"\$want\" ] && [ \"\$want\" = \"\$got\" ] || { echo 'IMAGE CHECKSUM MISMATCH'; exit 1; }
  echo image-verified
  sudo qemu-img convert -f qcow2 -O raw img.qcow2 '$ZVOL_DEV'
  echo image-written
  sudo rm -f img.qcow2 SHA512SUMS; cd /; sudo rmdir '$WORK' 2>/dev/null || true"

# 3) build the NoCloud seed ISO (meta-data + user-data + MAC-matched netcfg) -----
say "build the seed ISO locally and copy it to $SEED_ISO"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
cat > "$TMP/meta-data" <<EOF
instance-id: ${HOST}-$(date +%Y%m%d)
local-hostname: ${HOST}
EOF
cat > "$TMP/network-config" <<EOF
version: 2
ethernets:
  eth0:
    match: {macaddress: "${BR20_MAC}"}
    set-name: eth0
    addresses: [10.1.20.22/27]
    routes:
      - to: default
        via: 10.1.20.1
    nameservers:
      addresses: [10.1.1.53, 10.1.1.54]
      search: [kmkdp.com]
  eth1:
    match: {macaddress: "${BR0_MAC}"}
    set-name: eth1
    addresses: [172.16.30.52/24]
    mtu: 9000
EOF
cp "$REPO/cloud-init/${HOST}-user-data.yaml" "$TMP/user-data"
python3 "$HERE/make-nocloud-seed.py" "$TMP/seed.iso" \
  "user-data=$TMP/user-data" "meta-data=$TMP/meta-data" \
  "network-config=$TMP/network-config"
ssh "$NAS" "sudo mkdir -p '$ISO_DIR'"
scp "$TMP/seed.iso" "$NAS:/tmp/${HOST}-seed.iso"
ssh "$NAS" "sudo mv /tmp/${HOST}-seed.iso '$SEED_ISO' && echo seed-placed"

# 4) create the VM (top-level) ---------------------------------------------------
say "vm.create $VM_NAME ($VCPU_CORES vCPU, ${MEM_MIB} MiB, UEFI, HOST-MODEL)"
VMID=$(ssh "$NAS" "sudo midclt call vm.create '{
  \"name\": \"$VM_NAME\",
  \"description\": \"kdk-dkr-04 -- internal compose host (NAS-hosted)\",
  \"vcpus\": 1, \"cores\": $VCPU_CORES, \"threads\": 1,
  \"memory\": $MEM_MIB, \"bootloader\": \"UEFI\",
  \"bootloader_ovmf\": \"OVMF_CODE_4M.fd\",
  \"cpu_mode\": \"HOST-MODEL\", \"autostart\": true,
  \"time\": \"LOCAL\", \"shutdown_timeout\": 90,
  \"ensure_display_device\": false
}' | python3 -c 'import sys,json;print(json.load(sys.stdin)[\"id\"])'")
say "VM id = $VMID"

# 5) devices: DISK, NIC br20, NIC br0, CDROM (seed) ------------------------------
say "add devices"
ssh "$NAS" "sudo midclt call vm.device.create '{
  \"vm\": $VMID, \"order\": 1001,
  \"attributes\": {\"dtype\": \"DISK\", \"path\": \"$ZVOL_DEV\",
    \"type\": \"VIRTIO\", \"iotype\": \"THREADS\"}
}' >/dev/null && echo disk-ok"
ssh "$NAS" "sudo midclt call vm.device.create '{
  \"vm\": $VMID, \"order\": 1002,
  \"attributes\": {\"dtype\": \"NIC\", \"type\": \"VIRTIO\",
    \"nic_attach\": \"br20\", \"mac\": \"$BR20_MAC\"}
}' >/dev/null && echo nic-br20-ok"
ssh "$NAS" "sudo midclt call vm.device.create '{
  \"vm\": $VMID, \"order\": 1003,
  \"attributes\": {\"dtype\": \"NIC\", \"type\": \"VIRTIO\",
    \"nic_attach\": \"br0\", \"mac\": \"$BR0_MAC\"}
}' >/dev/null && echo nic-br0-ok"
ssh "$NAS" "sudo midclt call vm.device.create '{
  \"vm\": $VMID, \"order\": 1000,
  \"attributes\": {\"dtype\": \"CDROM\", \"path\": \"$SEED_ISO\"}
}' >/dev/null && echo cdrom-ok"

# 6) start -----------------------------------------------------------------------
say "available VM memory before start:"
ssh "$NAS" "sudo midclt call vm.get_available_memory"
say "start $VM_NAME"
ssh "$NAS" "sudo midclt call vm.start $VMID && echo started"
echo "Started $VM_NAME ($VMID). Cloud-init takes a few minutes; then:"
echo "  ssh kdkadmin@10.1.20.22 cloud-init status --wait"
