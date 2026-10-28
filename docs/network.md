---
title: Network & addresses
---
# Network & addresses

Address registry. Git history is the audit trail; edit this table in the same
PR as any change that adds or moves an address.

## Hosts

| Host | IP | VLAN | Where | VMID |
|---|---|---|---|---|
| kdk-hyp-01 | 10.1.1.100 | 10 | bare metal (R720) | — |
| kdk-nas-01 | 10.1.3.5 (storage 172.16.30.2) | 1 | bare metal (R730xd) | — |
| kdk-dkr-01 | 10.1.20.20 (storage 172.16.30.50) | 20 | VM on kdk-hyp-01 | 300 |
| kdk-log-01 (Loki, syslog) | 10.1.20.21 (no storage NIC) | 20 | VM on kdk-hyp-01 | 320 |
|  kdk-dkr-dmz-01 | 192.168.191.20 ( storage 172.16.30.51) | 191 | VM 23 on kdk-nas-01 | — |
| kdk-dkr-02 (Pi, HA) | 10.1.30.21 | 30 | bare metal Pi 5 | — |
| kdk-dkr-03 (Pi, HomeKit) | 10.1.30.22 | 30 | bare metal Pi 5 | — |
| kdk-mon-01 | 10.1.20.30 | 20 | bare metal Pi 5 | — |
| kdk-dns-01/02/03 | 10.1.1.120/.121/.122 (VIP .53/.54) | 10 | VM / NAS VM / Pi | 210/—/— |
| kdk-dee-01 | 10.1.1.130 | 10 | VM on kdk-hyp-01 | 220 |
| kdk-pbs-01 | 10.1.1.102 | 10 | VM 23 on kdk-nas-01 | — |

VLAN 20 is a `/27`: gateway `.1`, kdk-dkr-01 `.20`, kdk-log-01 `.21`, kdk-mon-01 `.30`.

## Inter-VLAN rules for the log host (UniFi)

kdk-log-01 receives logs from every VLAN. Its own nft policy
(`hosts/kdk-log-01/nftables.conf`) is the allowlist of record; the gateway needs
these allows for the cross-VLAN senders (VLAN 20 senders are same-subnet):

| Source | Destination | Ports | Why |
|---|---|---|---|
| `192.168.191.20` (DMZ) | `10.1.20.21` | tcp 3100, tcp 514 | dmz-01 Alloy pushes to Loki (no longer over the storage net); rsyslog forwarder |
| `10.1.30.21`, `10.1.30.22` (Pis) | `10.1.20.21` | tcp 3100, tcp 514 | Pi Alloy pushes; Pi rsyslog → `syslog.kmkdp.com` |
| `10.1.1.100`, `10.1.1.120`, `10.1.3.5` | `10.1.20.21` | tcp/udp 514, tcp 3100 | hypervisor, DNS origin and NAS syslog (when configured) |

## Storage network (NFS)

Storage/NFS runs on its own flat L2 (bond1/br0 on the NAS, jumbo MTU 9000):
**`172.16.30.0/24`**, NFS server `kdk-nas-01` at **`172.16.30.2`**.

| Host | Storage IP |
|---|---|
| kdk-nas-01 (NFS server, `br0` alias) | 172.16.30.2 |
| kdk-hyp-01 (`vmbr1`) | 172.16.30.3 |
| kdk-dkr-01 (`eth1`) | 172.16.30.50 |
| kdk-dkr-dmz-01 (`eth1`) | 172.16.30.51 |

> ⚠️ **Do not use `100.64.0.0/10` for storage (or any LAN).** It was `100.100.100.0/24`
> until 2026-09-16, which sits **inside Tailscale's CGNAT range `100.64.0.0/10`**. When the
> Tailscale subnet router came up on kdk-dkr-01 (host networking), Tailscale's `ts-input`
> anti-spoof rule (`! -i tailscale0 -s 100.64.0.0/10 -j DROP`) silently dropped every NFS
> reply on the storage NIC → hard-mount hangs → SABnzbd/Chaptarr/media wedged. Renumbering
> off CGNAT is the permanent fix. `172.16.0.0/16` is the one part of `172.16.0.0/12` free of
> Docker's default bridge pool (`172.17`–`172.31`). NFS server config lives in TrueNAS
> (`nfs.config` bindip + `sharing.nfs` export networks); client mounts are systemd
> `.mount` units seeded from `cloud-init/`. See `docs/runbooks/storage-network.md`.

## Remote access (Tailscale)

Tailnet login is Authelia OIDC; `stacks/tailscale-subnet-router` on kdk-dkr-01 advertises
`10.1.1.0/24, 10.1.3.0/24, 10.1.20.0/24, 10.1.30.0/24, 192.168.191.0/24` (10.1.3 = VLAN 1
management: TrueNAS, iDRACs, UPS card; added 2026-10-26) and `stacks/tailscale-dns`
serves ad-blocking DNS on the router's tailnet IP (the tailnet's global nameserver).
Clients join with `tailscale up --accept-routes` (DNS accepted by default). The router does
**not** masquerade (`--snat-subnet-routes=false`, 2026-10-26): lab hosts see the client's
tailnet address (100.64.0.0/10) and reply via the UniFi static route `100.64.0.0/10 → 10.1.20.20`.
That route is load-bearing — if it disappears, remote access dies while the router looks healthy. Only
`sso.kmkdp.com` has a public DNS record (Cloudflare tunnel); every other `*.kmkdp.com`
name resolves only through lab DNS, i.e. only on the LAN or the tailnet.

| Symptom | Cause | Check / fix |
|---|---|---|
| Remote client reaches `10.1.20.20` (Komodo, Radarr…) but nothing on the DMZ, VLAN 1 or VLAN 30 | (a) Docker's nftables `FORWARD` policy DROP eats forwarded tailnet packets; tailscaled's rules are in iptables-legacy. (b) The gateway static route `100.64.0.0/10 → 10.1.20.20` is missing, so replies to the un-masqueraded client never come back | `sudo iptables -S DOCKER-USER` on dkr-01 must show `-i tailscale0 -j ACCEPT` and `-o tailscale0 -j ACCEPT`; the `docker-forward` sidecar in the subnet-router stack re-adds them every minute |
| Everything via DERP ("direct connection not established") | Lab side has no inbound UDP; hotel/CGNAT on the client side | Fine functionally; throughput measured 2026-10-26 at ~21 Mbit/s from a 28 Mbit/s hotel link. A UDP 41641 forward to dkr-01 on the UniFi gateway would allow direct paths |
| A new subnet is advertised but tailnet clients cannot reach it | ACL: the route needs an `autoApprovers.routes` entry for `tag:subnet-router` AND a `grants` dst for `autogroup:member` | Tailscale console → Access controls (API writes are guarded for the ops token) |
| `tailscale-dns` exited after a dkr-01 reboot | Docker started it before tailscale0 existed; a failed start is not retried | fixed 2026-10-26: host network + `bind 100.66.29.117` + `restart: always`; check `docker ps -a \| grep tailscale-dns` |
| Hotel/captive Wi-Fi: `dig @1.1.1.1` times out | Network blocks outbound port 53 | Tailnet DNS (100.100.100.100 → router forwarder) still works; use DoH for public lookups |

## DNS (AdGuard — live configuration, verified 2026-09-21)

Three AdGuard Home instances behind a VRRP VIP; rewrites are edited on the
**origin** `kdk-dns-01` (10.1.1.120) and replicated to dns-02/03 by
adguardhome-sync. `scripts/dns-sync.py` reconciles service rewrites from live
Traefik `Host()` labels plus a static map (creds op://kdk-cluster/
kdk-dns-adguard-home-admin); the dns-reconcile stack runs it on the
gitops-sync cadence, but **adopted Pi-tier names are excluded** from
auto-reconciliation.

| Resolver | Role |
|---|---|
| 10.1.1.53 (`dns.kmkdp.com`) | VRRP VIP — what clients use |
| 10.1.1.120 `kdk-dns-01` | origin (edit rewrites HERE) |
| 10.1.1.121 `kdk-dns-02` / 10.1.1.122 `kdk-dns-03` | replicas via adguardhome-sync |

**Service rewrites** (59 total live; the tiers):

| Record | Answer | Serves |
|---|---|---|
| `*.kmkdp.com` | 10.1.20.20 | everything on internal Traefik (kdk-dkr-01) |
| sso, seerr, jellyfin, immich, users, recipes, audiobookshelf, libreseerr .kmkdp.com | 192.168.191.20 | DMZ Traefik (kdk-dkr-dmz-01) |
| homeassistant, zigbee, nodered, scrypted, homebridge .kmkdp.com | 10.1.30.21 / .22 | Pi Traefik (kdk-dkr-02; scrypted/homebridge on dkr-03 behind the dkr-02 answer is historical — both resolve 10.1.30.21) |
| ollama, kdk-tdarr-gpu-01 .kmkdp.com | 10.1.30.218 | GPU workstation |
| reMarkable cloud domains (my/ping/tectonic/…, appspot hosts) | 10.1.20.20 (2 blackholed → 0.0.0.0) | rmfakecloud intercept |
| host FQDNs (pve, pbs, truenas, kdk-*-NN) | per-host | infra |

Public (Cloudflare tunnel → DMZ Traefik): sso, seerr, jellyfin, immich —
**no internal A records exist in public DNS** (verified against 1.1.1.1/8.8.8.8),
so DoH-using browsers simply fail closed off-LAN rather than leak.

> **Stale-record warning (found 2026-09-21):** the live origin still carried
> pre-rename Pi records — `kdk-dkr-01.kmkdp.com → 10.1.30.21` and
> `kdk-dkr-02.kmkdp.com → 10.1.30.22`, with no `kdk-dkr-03` record.
> `scripts/dns-sync.py`'s static map had the correct values
> (01 → 10.1.20.20, 02 → 10.1.30.21, 03 → 10.1.30.22); **fixed on the origin
> 2026-09-21** (manual, operator-run — these names are excluded from
> auto-reconcile).

### Troubleshooting: external domain "connection refused" from lab hosts

Check AdGuard **blocklists** before blaming the remote service: the HaGeZi
"Most Abused TLDs" list wildcards whole TLDs (`.life` bit us 2026-09-21 —
Nzb.su's new home nzb.life answered 0.0.0.0, surfacing as
`Connection refused (nzb.life:443)` in Chaptarr/Prowlarr). Diagnose with
`dig @10.1.1.53 <domain>` → `0.0.0.0` = blocked. Fix: allowlist rule
`@@||<domain>^$important` in user rules on the ORIGIN (10.1.1.120), then make
sure adguardhome-sync has propagated to the replicas (`docker restart
adguardhome-sync` on kdk-dkr-01 forces it) — otherwise the fix silently
vanishes on VRRP failover. Note: user rules still carry ~90 inert k3s-era
external-dns `dnsrewrite` rules (→10.1.20.10; masked by the rewrite list) —
cleanup candidate.

### Troubleshooting: redirect to `*.svc.cluster.local`

A browser landing on `https://authelia.dmz.svc.cluster.local/...` (or any
`svc.cluster.local` name) is replaying a **permanently cached 301 from the
pre-migration k3s ingress** (HAR-verified 2026-09-21: `fromCache: disk`,
response date 2026-09-11). No current server issues that redirect — DNS and
Traefik are fine. Fix per device: clear cached data for the affected site
("Empty cache and hard reload"). Bonus quirk: the dead cluster.local name then
404s against internal Traefik instead of NXDOMAIN because the client's
`kmkdp.com` search domain expands it into the `*.kmkdp.com` wildcard.
