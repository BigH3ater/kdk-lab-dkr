---
title: Storage network & NFS
---

# Storage network & NFS

The NFS/storage plane is a flat L2 on the NAS `bond1`/`br0` (802.3ad, jumbo MTU
9000): **`172.16.30.0/24`**. NFS server is `kdk-nas-01` (TrueNAS SCALE) at
**`172.16.30.2`**. See the address table in [Network](../network.md#storage-network-nfs).

| Host | Storage IP | How it's set |
|---|---|---|
| kdk-nas-01 | 172.16.30.2 | TrueNAS `br0` alias (`interface.update`) |
| kdk-hyp-01 | 172.16.30.3 | `/etc/network/interfaces` (`vmbr1`) |
| kdk-dkr-01 | 172.16.30.50 | `/etc/netplan/50-cloud-init.yaml` (`eth1`) |
| kdk-dkr-dmz-01 | 172.16.30.51 | `/etc/netplan/50-cloud-init.yaml` (`eth1`) |

Client mounts are systemd `.mount` units (`/etc/systemd/system/mnt-*.mount`),
seeded on build from `cloud-init/` in the repo. Current clients:

- **kdk-dkr-01** — `/mnt/media`, `/mnt/backup`, `/mnt/dee-exchange`, `/mnt/loki`
- **kdk-dkr-dmz-01** — `/mnt/media` (RO), `/mnt/backup`, `/mnt/appdata`

## ⚠️ Never put storage (or any LAN) inside `100.64.0.0/10`

Tailscale's CGNAT range is `100.64.0.0/10` (100.64.0.0 – 100.127.255.255). Any lab
subnet inside it collides with Tailscale. The storage net was `100.100.100.0/24`
(inside CGNAT) until **2026-09-16**, when this bit us hard:

- **Symptom:** SABnzbd/Chaptarr/media on kdk-dkr-01 hung ("readarr and sabnzbd are
  down"); containers stayed `running` but their processes wedged in D-state;
  `docker stop`/`umount` hung.
- **Root cause:** the `tailscale-subnet-router` stack (host networking) came up on
  kdk-dkr-01 — the same host that mounts NFS over the storage net. Tailscale installs
  an anti-spoof rule in its `ts-input` chain:
  `! -i tailscale0 -s 100.64.0.0/10 -j DROP`. Because the storage subnet was inside
  `100.64.0.0/10`, **every NFS reply from the NAS was dropped on `eth1`.** ARP (L2)
  still worked, so the link looked alive, but all IP traffic to `172.x`… er,
  `100.100.100.x` died. Hard NFS mounts (`hard,timeo=600`) then hung forever.
- **Tell-tale:** `sudo iptables-legacy -L ts-input -nv` shows a rising drop count on
  the `100.64.0.0/10` rule. Only the host running Tailscale is affected (dmz-01/hyp-01
  were fine).
- **Fix:** renumber storage off CGNAT (done → `172.16.30.0/24`). No firewall
  workaround needed afterward. `172.16.0.0/16` is the only part of `172.16.0.0/12`
  free of Docker's default bridge pool (`172.17`–`172.31`).

## Add / change an NFS export (TrueNAS)

NFS server config is API-driven; edit via `midclt` over SSH (`kdkadmin@10.1.3.5`):

```bash
# export ACLs — allowed networks per share
sudo midclt call sharing.nfs.query | python3 -m json.tool   # inspect
sudo midclt call sharing.nfs.update <id> '{"networks":["172.16.30.0/24", ...]}'

# nfsd listen addresses — MUST include the storage IP or clients get RST on :2049
sudo midclt call nfs.config | python3 -c 'import sys,json;print(json.load(sys.stdin)["bindip"])'
sudo midclt call nfs.update '{"bindip":[...,"172.16.30.2"]}'   # restarts nfsd
```

Gotcha: `showmount -e` (rpcbind/mountd) can succeed while an NFSv4 `mount` hangs — v4
only uses **2049**. If `mount` hangs but ping works, check `bindip` / `ss -tlnp | grep 2049`.

## Renumber the storage network (dual-address cutover)

Safe, near-zero-downtime procedure (used for the CGNAT migration):

1. **NAS:** add the new IP as a second `br0` alias (`interface.update` → `commit`
   with `rollback:true` → `checkin`); **add** the new subnet to each active export's
   `networks`; **add** the new IP to `nfs.bindip`.
2. **Each host:** `ip addr add <new>/24 dev <nic>` (live), confirm `ping <nas-new>`.
3. **Clients:** stop the containers bound to the mounts; `umount -l` (lazy — handles
   busy/hung); `sed -i s/<old-nas>/<new-nas>/ /etc/systemd/system/mnt-*.mount`;
   `systemctl daemon-reload`; `systemctl reset-failed` + `restart` each mount; restart
   the containers (`docker start` re-binds the current host mount).
4. **Verify:** RW test on each mount; `ss -tn | grep <new>:2049` shows the clients.
5. **Persist + clean:** update netplan / `interfaces` to the new IP and apply; remove
   the old subnet from export ACLs, `bindip`, and the `br0` alias; update the repo
   (`cloud-init/`, `komodo/resources.toml` `LOKI_HOST`, `docs/`).

If a hard mount is already hung (server unreachable), unblocking the old path first
lets the wedged I/O drain so containers stop cleanly — during the CGNAT migration this
meant a temporary `iptables-legacy -I ts-input 4 -s <old>/24 -j RETURN` on the Tailscale
host (removed after cutover; Tailscale rebuilds `ts-input` on restart anyway).

## Troubleshoot a hung mount

```bash
# from the VM (or via the Proxmox guest agent if SSH is wedged):
#   ssh kdkadmin@10.1.1.100 'sudo qm guest exec 300 -- <cmd>'
findmnt -t nfs4 -o TARGET,SOURCE          # what's mounted from where
dmesg -T | grep -i 'nfs: server'          # "not responding" = network path down
ping <nas-storage-ip>                       # L3 to the NAS
sudo iptables-legacy -L ts-input -nv | grep 100.64   # Tailscale CGNAT drop rising?
timeout 8 stat -f /mnt/media || echo HUNG  # bounded hang check
```
