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

> Host FQDNs (`kdk-*.kmkdp.com`) are A-records written by `stacks/dns-reconcile`
> (`EXTRA` map) from this table; unknown names fall through the `*.kmkdp.com`
> wildcard to the internal Traefik (10.1.20.20), so a host without a record
> silently "resolves" to the wrong box. Add new hosts here AND in `EXTRA`.

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
the Cloudflare tunnel hostnames (`sso.kmkdp.com`, `jellyfin.kmkdp.com`) have public DNS records;
every other `*.kmkdp.com` name resolves only through lab DNS, i.e. only on the LAN or the tailnet.

### Public path (Cloudflare tunnel, since 2026-10-28)

```
visitor ─► Cloudflare edge (WAF/geo rules) ─► tunnel (outbound from cloudflared, TCP 7844)
        ─► cloudflared 10.211.2.2 ─► https://traefik:8443  (DMZ Traefik 10.211.2.3, `tunnel` entrypoint;
           8443 is not published, proxy-dmz only; trusts XFF only from 10.211.2.2, keeps Cf-*)
        ─► app on proxy-dmz — only routers labelled entrypoints=websecure,tunnel (sso, jellyfin, wizarr-public)

LAN / tailnet ─► 192.168.191.20:443 (`websecure`; XFF trusted only from 10.1.20.20 + 10.1.30.21,
                 Cf-*/True-Client-Ip stripped) ─► any DMZ router
```

`proxy-dmz` is `10.211.2.0/24` with an explicit ipam block (`stacks/dmz-proxy/compose.yaml`):
cloudflared `.2` and Traefik `.3` are pinned, other containers get `.128`–`.254`. Jellyfin known
proxies and `IMMICH_TRUSTED_PROXIES` are `10.211.2.3/32`. See [[dmz-proxy]].

Public edge (Cloudflare tunnel: `sso`, `jellyfin`): `scripts/edge-probe.sh` checks the WAF rules,
rate limit, HSTS, minimum TLS, apex and CAA from outside and prints a PASS/FAIL table
(`--no-ratelimit` skips the login bursts). The rule expressions and dashboard steps are in the
vault note *Cloudflare edge rules (2026-10)* (10.Homelab/Security), not in this repo.

| Symptom | Cause | Check / fix |
|---|---|---|
| Remote client reaches `10.1.20.20` (Komodo, Radarr…) but nothing on the DMZ, VLAN 1 or VLAN 30 | (a) Docker's nftables `FORWARD` policy DROP eats forwarded tailnet packets; tailscaled's rules are in iptables-legacy. (b) The gateway static route `100.64.0.0/10 → 10.1.20.20` is missing, so replies to the un-masqueraded client never come back | `sudo iptables -S DOCKER-USER` on dkr-01 must start with `-i tailscale0 -j ACCEPT` and `-o tailscale0 -j ACCEPT`; the host unit `kdk-docker-user` (timer, every 5 min and on every Docker start) writes them -- `systemctl status kdk-docker-user.timer`, [Host firewall](runbooks/host-firewall.md). (Until security batch 7 a `docker-forward` sidecar in the subnet-router stack did this.) |
| Everything via DERP ("direct connection not established") | Lab side has no inbound UDP; hotel/CGNAT on the client side | Fine functionally; throughput measured 2026-10-26 at ~21 Mbit/s from a 28 Mbit/s hotel link. A UDP 41641 forward to dkr-01 on the UniFi gateway would allow direct paths |
| A new subnet is advertised but tailnet clients cannot reach it | ACL: the route needs an `autoApprovers.routes` entry for `tag:subnet-router` AND a `grants` dst for `autogroup:member` | Tailscale console → Access controls (API writes are guarded for the ops token) |
| `tailscale-dns` exited after a dkr-01 reboot | Docker started it before tailscale0 existed; a failed start is not retried | fixed 2026-10-26: host network + `bind 100.66.29.117` + `restart: always`; check `docker ps -a \| grep tailscale-dns` |
| Hotel/captive Wi-Fi: `dig @1.1.1.1` times out | Network blocks outbound port 53 | Tailnet DNS (100.100.100.100 → router forwarder) still works; use DoH for public lookups |

## Owner checklist: UniFi and TrueNAS (security plan A1.7)

Gateway and NAS settings no repo file can carry. The host-side half (nft storage fence,
DOCKER-USER policy) is in [Host firewall](runbooks/host-firewall.md). Tick a row by
changing its status in a PR, with the date.

| # | Where | Change | Why | Status |
|---|---|---|---|---|
| 1 | UniFi policy | allow `192.168.191.20 → 10.1.20.21` tcp 3100 + tcp 514 | DMZ Alloy and rsyslog to kdk-log-01 instead of the storage net (security batch 2) | ✅ 2026-10-30 verified via the UniFi API: policies "Allow DKR DMZ workers to syslog" (tcp 514, 3100) and "… syslog UDP" (udp 514) |
| 2 | UniFi policy | remove `192.168.191.20 → 10.1.20.20:514` once row 1 works | undocumented syslog hole (hosts report C-01); Loki left dkr-01 | ☐ open: the policy "DKR DMZ services syslog" (network → network, port 514, all protocols) still exists (2026-10-30); nothing on dkr-01 listens on 514 any more, delete it |
| 3 | UniFi policy | keep `192.168.191.20 → 10.1.20.20` tcp 8080 (1Password Connect) and tcp 443 | deploy secret rendering; Seerr/Libreseerr → arr. The 443 rule is now scoped to those two containers on the host (DOCKER-USER) | ✅ 2026-10-30 both present ("Allow DKR DMZ to Komodo" tcp 8080, "Allow DKR DMZ to sonarr and radarr /api" tcp 443) |
| 3b | UniFi policy | allow `192.168.191.20 → 10.1.20.20` tcp 25 | Authelia notifier → Proton Mail Bridge (2026-10-29); host side scoped to the authelia container in DOCKER-USER | ✅ 2026-10-30 present ("Allow DKR DMZ workers to SMTP") |
| 4 | UniFi policy | allow `10.1.20.30 → 192.168.191.20` tcp 6360 only from VLAN 20 | mon-01's LDAPS certificate probe; nothing on dkr-01 still binds LDAP (plan K12, confirmed 2026-10-30: no LDAP listener on dkr-01) | ☐ open, together with the `tcp_tls_lldap` blackbox probe (security batch 8): no probe and no rule exist yet |
| 5 | UniFi policy | `10.1.30.22` tcp 10443, 11080 (Scrypted UI) only from `10.1.30.21` | only the Pi Traefik fronts Scrypted (security batch 0) | ⛔ not enforceable at the gateway: both Pis sit on VLAN 30, same L2 (plan A1.7 dropped it 2026-10-28). Other VLANs are covered by the zone defaults; confirm IoT/Guest/DMZ zones cannot reach VLAN 30 |
| 6 | UniFi policy | Home Assistant `8123` only from `10.1.30.21`; go2rtc `18555` from VLAN 30 only | HA is reached through the Pi Traefik; WebRTC stays on the user VLAN | ☐ open, inter-VLAN only (same-VLAN clients reach 8123/18555 on L2 regardless); HA binds `0.0.0.0:8123` and go2rtc `*:18555` (2026-10-30), so the gateway rule is the only fence for IoT/Guest/DMZ |
| 7 | UniFi port forwarding / UPnP | no forwards except optional `udp/41641 → 10.1.20.20` (Tailscale direct); UPnP off | nothing else may reach the lab from the WAN (Cloudflare edge report M-4) | ✅ 2026-10-30 UPnP and NAT-PMP off; no enabled forwards. Two DISABLED leftovers (`DST-10999`, `DST-11000` udp → 192.168.190.85, a VLAN 190 host that no longer exists): delete them |
| 8 | UniFi policy | DMZ egress: tcp/7844 to `198.41.192.0/24`, `198.41.200.0/24`, plus `api.cloudflare.com` and Let's Encrypt for the DMZ host | cloudflared tunnel + ACME DNS-01; first step towards a DMZ egress allowlist | ☐ open: today "Allow DMZ Internet" is any → any. An allowlist also needs registries (ghcr.io, docker.io), apt mirrors, TMDB/OMDb (Jellyfin, Seerr), Hugging Face (Immich ML); scoped with security batch 7 |
| 9 | UniFi networks | delete VLAN 21 (`10.1.21.0/24`, k3s-era workers) and its rules; VLAN 190 removed too | unused (plan A1.0) | ✅ 2026-10-29 |
| 10 | UniFi / SSH logs | identify SSH source `192.168.2.3`; if unknown, rotate `op://kdk-ops/kdkadmin-ssh-key` | 2026-10-29: it is a peer of the UniFi "WireGuard VPN Server" (`192.168.2.1/24`); the admin allowlists said `192.168.3.0/24` by mistake and were corrected to `192.168.2.0/24` | ☑ done |
| 11 | TrueNAS → Shares → SMB / Network | SMB not offered on the storage bridge (`172.16.30.2:445` closed; bind SMB to `10.1.3.5`) | storage net is NFS-only; the DMZ VM could reach SMB (hosts report C-01) | ✅ 2026-10-30 SMB bound to `10.1.3.5`, `10.1.1.5`, `10.1.20.5` (VLAN 20 SMB share for the Tdarr Windows node); `172.16.30.2:445` and `192.168.191.5:445` closed; clients (DEE VM, 4090 box, admin workstation) reconnected |
| 12 | TrueNAS → Shares → NFS | per-host backup datasets (`tankz3/backup/dkr/<host>`) with `mapall` to an unprivileged user; dmz-01's export covers only its own subtree; the PBS datastore is not exported to `172.16.30.51` | a root compromise of the DMZ VM must not reach every VM backup (hosts report M-06) | ☐ open: 2026-10-30 the PBS export still lists `172.16.30.0/24` (owner script removes it, VLAN 10 only); per-host datasets + `mapall` stay a security batch 7 item (changes the backup-collect/rsync paths) |
| 13 | TrueNAS → Shares → NFS | each share's Authorized Networks/Hosts lists only the client(s) that mount it (`172.16.30.50`, `.51`) | export ACLs could not be read in the audit | ☐ open: read 2026-10-30 via `midclt sharing.nfs.query`: live clients are only `.50`/`.51` (+ hyp-01 `.3` for the template dump), but media/appdata/dee-exchange still allow `10.1.20.0/27`, `192.168.191.0/24`, the deleted `10.1.21.0/24` and the whole storage /24, and 29 dead k3s PVC exports (hosts `100.100.100.x`) remain. Owner script (session scratchpad `truenas-nfs-tighten.sh`) pins each export to its hosts and deletes the dead ones; datasets untouched |

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
| homeassistant, zigbee, nodered, scrypted .kmkdp.com | 10.1.30.21 | Pi Traefik (kdk-dkr-02; proxies scrypted across to dkr-03). homebridge.kmkdp.com retired 2026-10-28 — dns-reconcile deletes it (`RETIRED`), so it falls through to the wildcard |
| ollama, kdk-tdarr-gpu-01 .kmkdp.com | 10.1.30.218 | GPU workstation |
| reMarkable cloud domains (my/ping/tectonic/…, appspot hosts) | 10.1.20.20 (2 blackholed → 0.0.0.0) | rmfakecloud intercept |
| host FQDNs (pve, pbs, truenas, kdk-*-NN) | per-host | infra |

Public (Cloudflare tunnel → DMZ Traefik `tunnel` entrypoint): sso, jellyfin (wizarr invitee paths when published) —
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
