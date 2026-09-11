#!/usr/bin/env bash
# Build kdk-dkr-dmz-01 on kdk-nas-01 (TrueNAS, midclt over SSH).
# Prereq: k3s VM kdkk3snaswkdmz01 drained/stopped/deleted (frees the A380).
# Steps follow the proven "Provision a TrueNAS-hosted k3s Control Plane"
# runbook: zvol + qcow2 convert, NoCloud seed ISO (vol id cidata),
# vm.create with cpu_mode HOST-MODEL, devices DISK/NIC(br191)/NIC(br0)/CDROM/PCI.
# The network-config is MAC-matched, so the ISO is built AFTER the NICs exist.
set -euo pipefail
NAS=kdkadmin@10.1.3.5
echo "Run the numbered midclt/zfs commands in this file by hand or via the session; MACs are read back between steps."
