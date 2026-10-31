---
title: kdk-dkr-04
host: kdk-dkr-04
fqdn: kdk-dkr-04.kmkdp.com
ip: 10.1.20.22
role: docker-host
tier: internal
status: building
provisioner: truenas-vm
hypervisor: kdk-nas-01
vmid: kdkdkr04
console: TrueNAS UI → Virtualization → kdkdkr04 → Console
standards: baseline-at-build
secrets: []
updated: 2026-10-30
tags: [homelab/host, role/docker-host, status/building, tier/internal]
---

# kdk-dkr-04 — second internal compose host

A second internal Docker host, added 2026-10-30 to split heavy render/encode
work off the over-committed kdk-dkr-01 (load 14 on 2026-10-30: a Tdarr ffmpeg at
~790 % CPU, two WeasyPrint renders OOM-killed). It is a TrueNAS VM on kdk-nas-01,
which was running only ~13 vCPU of VMs at load ~3. Phase A (this build) stands
the host up, hardened and enrolled in Komodo, running only the agent stacks.
Phase B moves workloads in later PRs.

**Related:** [[kdk-nas-01]] · [[kdk-dkr-01]] · [[komodo-gitops]] · [[network]]

## At a glance

| Field | Value |
|---|---|
| Purpose | Internal compose host for heavy CPU/memory work moved off kdk-dkr-01 |
| Status | 🟡 Building (Phase A) — periphery installed, auth + enrol after merge |
| Host | VM `kdkdkr04` on kdk-nas-01 (TrueNAS `vm.*` API) |
| Address | `10.1.20.22/27` (VLAN 20, br20) · storage `172.16.30.52` (br0) |
| OS | Debian 13 (trixie) genericcloud, stock cloud kernel |
| Hardware | 12 vCPU / 32 GiB / 120 GB vmpool zvol · no PCI passthrough |
| Docker pool | `10.214.0.0/16` size 24 ([[docker-ipam]]) |
| Managed by | cloud-init (NoCloud seed ISO) at build; Komodo periphery thereafter |
| Blast radius | Phase A: none beyond its own agents. Phase B: the moved workloads |

## Access

| Path | How | Use when |
|---|---|---|
| Console | TrueNAS UI → VM `kdkdkr04` → Console | SSH down |
| SSH | `ssh kdkadmin@10.1.20.22` | normal operation |

Admin: `kdkadmin`, key-only, `NOPASSWD:ALL`, **not** in the `docker` group.

## Verify

```bash
ssh kdkadmin@10.1.20.22 'cat /etc/os-release | head -1; sudo docker info --format "{{.SecurityOptions}} {{json .DefaultAddressPools}}"'
ssh kdkadmin@10.1.20.22 'systemctl is-active kdk-storage-fence kdk-docker-user; sudo nft list table inet kdk-storage >/dev/null && echo fence-ok'
ssh kdkadmin@10.1.20.22 'sudo ss -lntp | grep -E ":22 |:8120 "'   # sshd + periphery on 10.1.20.22
```

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | [[kdk-nas-01]] | the VM itself (zvol + compute) |
| Needs | [[kdk-dkr-01]] | Komodo Core (periphery inbound), backup-collect pull |
| Needs | [[kdk-log-01]] | log shipping (Alloy → Loki, rsyslog) |
| Needed by | Phase B workloads | nothing in Phase A |

## Observability

- `node_exporter` on `10.1.20.22:9100` (node-exporter-dkr04 stack), scraped by
  Prometheus on [[kdk-mon-01]] (`node-external` job).
- cAdvisor on `10.1.20.22:9280` (observability-agent-dkr04), scraped as
  `host="kdk-dkr-04"`.
- Alloy ships container logs to Loki on `10.1.20.21:3100`; rsyslog forwards host
  syslog to `10.1.20.21:514`. `LokiShippingStalled{host="kdk-dkr-04"}` alerts on
  30 min of silence (`stacks/loki/rules/shipping.yml`).
- `blackbox-icmp` pings `10.1.20.22`.

## Standards compliance

| Standard | Required | This host | Note |
|---|---|---|---|
| Hostname | `{site}-{role}-{nn}` | ✅ `kdk-dkr-04` | |
| Admin account | `kdkadmin`, key-only, NOPASSWD, not in `docker` | ✅ at build | |
| SSH hardening | `98-kdk-admin.conf` + kdkadmin key `from=` admin sources | ✅ at build | `hosts/_common/sshd_config.d` |
| Kernel sysctl | `90-kdk-hardening.conf` | ✅ at build | |
| Patching | unattended-upgrades: Debian + Docker CE origins | ✅ at build | |
| Docker daemon | `daemon.json`: log rotation, `live-restore`, `no-new-privileges`, `userland-proxy: false`, `icc: false`, pool 10.214 | ✅ at build | [[docker-daemon]] |
| Host firewall | storage-net fence (`inet kdk-storage`) + DOCKER-USER unit `MODE=enforce` | ✅ at build | [[host-firewall]] |
| rpcbind / LLMNR | masked / off | ✅ at build | NFS is v4.1 only |
| Timezone | America/Chicago | ✅ | |
| Syslog | `syslog.kmkdp.com:514` | ✅ at build | rsyslog seeded by cloud-init |

## Architecture

VLAN 20 (`/27`). NIC 1 `br20` → `10.1.20.22/27`, gw `10.1.20.1`, DNS
`10.1.1.53`/`10.1.1.54`. NIC 2 `br0` → `172.16.30.52/24` (storage net, no
gateway), fenced to NFS-to-NAS only. Periphery on `8120` (inbound; Komodo Core
on kdk-dkr-01 connects). No PCI passthrough.

### Storage map (Phase A)

No NFS mounts yet. The NAS exports `tankz3/media` and `tankz3/backup` to
`172.16.30.52` since 2026-10-30, and a read-only test mount of each passed. Phase B adds
the `/mnt/media` and `/mnt/backup` mount units, with the same options as kdk-dkr-01,
when the first workload moves. App state lives on the root zvol under
`/opt/kdk-lab` and is pulled nightly by `backup-collect` over SSH (the host is
not on the NFS backup tree in Phase A).

## Secret Rotation

Host holds no credential of its own beyond the periphery auth line (passkeys,
rendered from `op://kdk-cluster/komodo-passkey`) and, once the agents deploy,
`/etc/kdk/pushover.env` (copied 0600 from kdk-dkr-01). Both follow the lab-wide
rotation in [[komodo]] and [[observability]].

## Provisioning

Built by `scripts/create-vm-kdk-dkr-04.sh` + `cloud-init/kdk-dkr-04-user-data.yaml`
(NoCloud seed ISO, MAC-matched netplan). The script (run from the admin
workstation) creates the `vmpool/vm-kdk-dkr-04-os` zvol, writes the Debian 13
genericcloud image onto it with `qemu-img`, creates VM `kdkdkr04` (12 vCPU, 32
GiB, `cpu_mode HOST-MODEL`, UEFI, autostart) with a DISK, two VIRTIO NICs
(`br20` MAC `00:a0:98:dd:04:20`, `br0` MAC `00:a0:98:dd:04:52`) and the seed
CDROM, then starts it.

Hand steps after the VM is up:

1. Append the periphery passkeys line (never printed):
   ```sh
   op read 'op://kdk-cluster/komodo-passkey/credential' \
     | ssh kdkadmin@10.1.20.22 'read -r PK; printf "passkeys = [\"%s\"]\n" "$PK" \
       | sudo tee -a /etc/komodo/periphery.config.toml >/dev/null'
   ssh kdkadmin@10.1.20.22 'sudo systemctl enable --now periphery'
   ```
2. Stage Pushover creds for `docker-event-watch` before the agents deploy:
   ```sh
   op read 'op://kdk-cluster/pushover-komodo/...' | ssh kdkadmin@10.1.20.22 \
     'sudo install -m 600 -o root -g root /dev/stdin /etc/kdk/pushover.env'
   ```
   (or copy kdk-dkr-01's `/etc/kdk/pushover.env` 1:1).

Rebuild: replay the script; Phase A carries no durable state (agents are
stateless). Rollback: `vm.stop` / `vm.delete` `kdkdkr04`, then
`zfs destroy vmpool/vm-kdk-dkr-04-os`, and revert the PR.
