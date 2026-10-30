---
title: kdk-log-01
host: kdk-log-01
fqdn: kdk-log-01.kmkdp.com
ip: 10.1.20.21
role: log-host
tier: internal
status: live
provisioner: proxmox-template
hypervisor: kdk-hyp-01
vmid: 320
console: Proxmox → VM 320 → Console
standards: compliant
secrets: []
updated: 2026-10-28
tags: [homelab/host, role/log-host, status/live, tier/internal]
---

# kdk-log-01 — lab log host

A small VM that holds the lab's logs: Loki plus the Alloy syslog receiver. It has no auth of its own, so it sits on its own box behind an nftables allowlist instead of sharing kdk-dkr-01 (posture finding C-02).

**Related:** [[kdk-hyp-01]] · [[observability]] · [[kdk-mon-01]] · [[komodo]]

## At a glance

| Field | Value |
|---|---|
| Purpose | Log storage and search (Loki), syslog intake, log-based alerts (Loki ruler) |
| Status | 🟢 Live since 2026-10-28 (PR #67, fix #84) — built by `scripts/create-vm-kdk-log-01.sh` (security plan B1); logs from all six hosts, ruler 10 groups / 69 rules |
| Host | VM 320 on kdk-hyp-01 |
| Address | `10.1.20.21/27` (VLAN 20), gw `10.1.20.1`, DNS `10.1.1.53`. No storage-net NIC |
| OS | Debian 13 (template 9000) |
| Hardware | 4 vCPU / 8 GiB / 200 GiB on `local-zfs` |
| Managed by | cloud-init at build (`cloud-init/kdk-log-01-vendor-data.yaml`); Komodo periphery thereafter |
| Runs | **loki** (Loki 3.7.9 `:3100` + Alloy syslog `:514` tcp/udp), observability-agent, node-exporter, the weekly `advisory-watch` and `trivy-scan-log01` one-shots |
| Blast radius | No log ingestion or log search lab-wide; Loki alerts (`LokiShippingStalled`) stop. Senders buffer (Alloy WAL, rsyslog disk queue) and catch up |

## Access

| Path | How | Use when |
|---|---|---|
| Console | Proxmox → VM 320 → Console | SSH down, or the nft policy locked you out |
| SSH | `ssh kdkadmin@10.1.20.21` | from `10.1.30.0/24`, the tailnet (`100.64.0.0/10`) or `192.168.2.0/24` only |

Admin: `kdkadmin`, key-only, `NOPASSWD:ALL`. sshd also refuses root, forwarding and every other user.

## Verify

```bash
ssh kdkadmin@10.1.20.21 'cloud-init status'                         # status: done
ssh kdkadmin@10.1.20.21 'systemctl is-active periphery nftables rsyslog docker'   # active x4
ssh kdkadmin@10.1.20.21 'sudo nft list table inet kdk-input | head -5'
ssh kdkadmin@10.1.20.21 'docker info | grep -A2 "Default Address Pools"'          # 10.213.0.0/16 /24
curl -s http://10.1.20.21:3100/ready                                 # ready (from an allowed source)
curl -s http://10.1.20.21:3100/loki/api/v1/rules | grep -c 'alert:'   # 69 (10 groups)
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| A host's logs stop, `LokiShippingStalled` fires | that host's Alloy down, or its source IP is not in the nft allowlist | `docker logs alloy` on the sender; `sudo nft list set inet kdk-input log_clients_v4` here |
| SSH times out from a new admin box | not in `admin_v4` | Proxmox console, add the address to `hosts/kdk-log-01/nftables.conf` + `/etc/nftables.conf`, `sudo systemctl reload nftables` |
| Komodo shows the server offline | periphery stopped, or auth line missing | `systemctl status periphery`; compare the auth line with kdk-dkr-01's `/etc/komodo/periphery.config.toml` |
| A published port answers from anywhere | the NIC is not called `eth0`, so the guard chain never matches | `ip -br link`; fix `define WAN` in the nft file |
| Docker NAT gone after an nft change | someone ran `nft flush ruleset` | `sudo systemctl restart docker` (re-creates its tables); our unit drop-in only deletes `inet kdk-input` |

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | [[kdk-hyp-01]] | the VM itself |
| Needs | [[kdk-mon-01]] Alertmanager `10.1.20.30:9093` | Loki ruler alerts are not delivered |
| Needed by | every host's observability-agent + rsyslog | logs pile up in sender buffers |
| Needed by | Grafana on [[kdk-mon-01]] (Loki datasource) | Explore / Logs dashboards empty |

## Observability

Prometheus on [[kdk-mon-01]] scrapes `10.1.20.21:9100` (`node-external`) and `:9280` (`cadvisor`, `host=kdk-log-01`) and pings it (`blackbox-icmp`). Its own container logs ship to its own Loki (`observability-agent-log01`). Loki ruler group `shipping` alerts when any host goes silent for 30 minutes.

## Standards compliance

| Standard | Required | This host | Note |
|---|---|---|---|
| Hostname | `{site}-{role}-{nn}` | ✅ `kdk-log-01` | |
| Admin account | `kdkadmin`, key-only, NOPASSWD | ✅ | from template 9000 |
| Timezone | America/Chicago | ✅ | cloud-init |
| NTP | `ntp.kmkdp.com` primary | ⚠️ timesyncd defaults | same gap as the other VMs |
| Syslog | `syslog.kmkdp.com:514` | ✅ forwards to itself (`/etc/rsyslog.d/90-kdk-loki.conf`) | |
| sshd hardening | drop-in, no root, key-only, `AllowUsers kdkadmin` | ✅ `/etc/ssh/sshd_config.d/98-kdk-admin.conf` | `MaxAuthTries 3`, `LoginGraceTime 20`, no X11/TCP/agent forwarding |
| Host firewall | default-deny inbound, source allowlists | ✅ `inet kdk-input` (`hosts/kdk-log-01/nftables.conf`) | covers Docker-published ports via a prerouting guard |
| Docker daemon | pool + log rotation + `live-restore` + `no-new-privileges` | ✅ `hosts/kdk-log-01/daemon.json` | pool `10.213.0.0/16` |
| Periphery bind | VLAN address only | ✅ `bind_ip = "10.1.20.21"`, `allowed_ips = ["10.1.20.20"]` | |

## Architecture

VLAN 20 only. Inbound is default-deny on `eth0`; allowed new connections:

| Port | From | Service |
|---|---|---|
| tcp 22 | `10.1.30.0/24`, `100.64.0.0/10`, `192.168.2.0/24` | sshd |
| tcp 3100 | `10.1.20.20`, `10.1.20.30`, `10.1.30.21`, `10.1.30.22`, `192.168.191.20`, `10.1.1.100`, `10.1.1.120`, `10.1.3.5` | Loki push + query |
| tcp/udp 514 | same list | Alloy syslog (container port 1514) |
| tcp 8120 | `10.1.20.20` | Komodo periphery |
| tcp 9100, 9280 | `10.1.20.30` | node_exporter, cAdvisor |

Docker-published ports are DNATed before INPUT, so the rules live in a prerouting chain at priority -150 (after conntrack, before Docker's DNAT) plus an input chain for host services, both in the separate `inet kdk-input` table. Every published port is also bound to `10.1.20.21`, not `0.0.0.0`.

### Storage map

| Mount | Source | FS | Consumed by | Backup |
|---|---|---|---|---|
| `/opt/kdk-lab/loki/data` → `/loki` | VM disk (`local-zfs`) | ext4 | Loki chunks, TSDB index, compactor (30-day retention) | PBS vzdump of VM 320 |
| `/opt/kdk-lab/stacks/<stack>` | VM disk | ext4 | Komodo repo clones | git |

## Configuration

| Path | What it is |
|---|---|
| `/etc/nftables.conf` | inbound policy (from `hosts/kdk-log-01/nftables.conf`) |
| `/etc/systemd/system/nftables.service.d/kdk.conf` | stop = delete only `inet kdk-input`, never flush Docker's tables |
| `/etc/docker/daemon.json` | from `hosts/kdk-log-01/daemon.json` |
| `/etc/ssh/sshd_config.d/98-kdk-admin.conf` | sshd baseline |
| `/etc/rsyslog.d/90-kdk-loki.conf` | forwarder (from `hosts/_common/rsyslog.d/`) |
| `/etc/komodo/periphery.config.toml` | periphery; auth line copied from kdk-dkr-01 at build, not in git |
| `/etc/kdk/pushover.env` | Pushover creds for `docker-event-watch` (copied from kdk-dkr-01, 0600) |

## Secret Rotation

Host holds no credential of its own beyond the periphery auth line and `/etc/kdk/pushover.env`; both follow the lab-wide rotation in [[komodo]] and [[observability]].

## Out-of-band changes

None by design. Build-time hand steps: the periphery auth line and `/etc/kdk/pushover.env`.

## Provisioning

Built by `scripts/create-vm-kdk-log-01.sh` + `cloud-init/kdk-log-01-vendor-data.yaml` (`qm clone 9000`, VM 320). Rebuild: restore VM 320 from PBS, or replay the script and redeploy the `loki` stack (an empty Loki loses history only).
