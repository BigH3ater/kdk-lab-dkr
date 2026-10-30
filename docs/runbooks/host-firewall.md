---
title: Host firewall (storage fence + DOCKER-USER)
---

# Host firewall: storage-net fence + DOCKER-USER policy

Two host-level controls on the two VMs, both in git under `hosts/` and installed by hand
(security plan A1.4 / A1.6; hosts report C-01, C-02, H-09, M-04):

| Control | What | Hosts | Files |
|---|---|---|---|
| **Storage fence** | nft table `inet kdk-storage`: the host talks only NFS (+ ping) to the NAS on `eth1`; nothing new comes in on `eth1`; nothing is routed onto or off the storage net; on dmz-01 also containers -> DMZ segment and containers -> host services | dkr-01, dmz-01 | `hosts/<host>/nftables.d/kdk-storage.nft`, `hosts/_common/systemd/kdk-storage-fence.service` |
| **DOCKER-USER policy** | Containers may not open connections to private addresses (RFC1918) outside their own Docker bridges, except listed exceptions. dkr-01 also keeps the tailnet forwarding rules | dkr-01 (`MODE=log` first), dmz-01 (`MODE=enforce`) | `hosts/_common/sbin/kdk-docker-user.sh`, `hosts/<host>/kdk-docker-user.conf`, `hosts/_common/systemd/kdk-docker-user.{service,timer}` |

The DOCKER-USER unit replaces the `docker-forward` sidecar that
`stacks/tailscale-subnet-router` ran until security batch 7.

## How the pieces fit

- Docker 29 programs its rules through **iptables-nft** (tables `ip filter`, `ip nat`).
  tailscaled on dkr-01 writes **iptables-legacy**, which Docker's `FORWARD` (policy DROP)
  never sees; that is why `-i/-o tailscale0 -j ACCEPT` must sit in `DOCKER-USER`.
- `DOCKER-USER` is the chain Docker jumps to first from `FORWARD` and never writes to.
  `kdk-docker-user.sh` replaces **only that chain**, atomically, from
  `/etc/default/kdk-docker-user`. It refuses to run if Docker's jump is not in the
  iptables-nft `FORWARD` (wrong backend = a chain nothing would read).
- The fence is its own nft table at forward priority -10 / output / input priority 0.
  Every base chain on a hook must accept a packet, so a drop in either control is final
  and Docker's chains still run after both.
- Published ports are DNATed in prerouting, so in FORWARD (and in the fence's forward
  chain) a connection to `192.168.191.20:443` already carries the container's address.
  A container reaching a port published on its own host is therefore bridge-to-bridge;
  the policy returns those to Docker (`-i br+ -o br+ -j RETURN`), whose own isolation
  then applies exactly as before.
- **Bridge-local traffic is untouched.** With `br_netfilter`, traffic between two
  containers on the same network (seerr -> jellyfin on `proxy-dmz`) does pass FORWARD,
  but in and out on a `br-*` interface, so it hits the bridge-to-bridge RETURN before any
  drop. Check: `docker exec seerr wget -qO- http://jellyfin:8096/health` still answers.
- Host services (sshd, Periphery, node_exporter, rpcbind) are INPUT, not FORWARD;
  DOCKER-USER cannot see them. On dmz-01 the fence's `host_guard` chain drops new
  container connections to them (H-09).

## Rules: storage fence (`table inet kdk-storage`)

| Chain (hook) | Rule | dkr-01 | dmz-01 |
|---|---|---|---|
| `storage_out` (output) | `eth1` established/related accept | yes | yes |
| | `eth1` -> `172.16.30.2` tcp/udp `111, 2049` accept | yes | yes |
| | `eth1` -> `172.16.30.2` icmp echo-request accept (storage-health ping) | yes | yes |
| | `eth1` anything else drop | yes | yes |
| `storage_in` (input) | `eth1` established/related accept, else drop | yes | yes |
| `fence_forward` (forward, prio -10) | in or out on `eth1` drop | yes | yes |
| | `br-*` -> `172.16.30.0/24` drop | yes | yes |
| | `br-*` -> `192.168.191.0/24` tcp 6360 accept (inert: published LDAPS is DNATed before this chain, and since batch 5 LDAP rides `identity_ldap`) | -- | yes |
| | `br-*` -> `192.168.191.0/24` new drop | -- | yes |
| `host_guard` (input) | `br-*` new tcp `22, 111, 8120, 9100` drop; udp `111, 5355` drop | -- | yes |

## Rules: DOCKER-USER (top to bottom)

| # | Rule | dkr-01 | dmz-01 |
|---|---|---|---|
| 1 | `-i tailscale0 -j ACCEPT`, `-o tailscale0 -j ACCEPT` (the sidecar's two rules, verbatim) | yes | -- |
| 2 | `RELATED,ESTABLISHED -> RETURN` (replies, incl. published-port replies) | yes | yes |
| 3 | containers -> own cAdvisor `:9280` (pre-DNAT address) -> LOG + DROP | `10.1.20.20` | `192.168.191.20` |
| 4 | bridge -> bridge -> RETURN (`br+`/`docker0` pairs) | yes | yes |
| 5 | DNS (udp+tcp 53) to the host's upstream resolvers -> RETURN | `10.1.1.53/.54` | public, no-op |
| 6 | exceptions -> RETURN | see the conf: Komodo -> Periphery `:8120` on every host, backup-collect -> `:22` on mon-01/dkr-02/dkr-03, `192.168.191.20:443` (DMZ Traefik: jellystat, forward-auth, OIDC), Grafana `10.1.20.30:3000`, Loki `10.1.20.21:3100`, AdGuard `:80`, UniFi `10.1.3.1` 443/udp 161, DEE `10.1.1.130:8484`, Ollama `10.1.30.218:11434`, reMarkable `10.1.30.245:22` | `seerr`, `libreseerr` -> `10.1.20.20:443`; `alloy` -> `10.1.20.21:3100` (per container address, refreshed every 5 min) |
| 7 | bridge -> `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16` -> LOG (`kdk-du-drop:` / `kdk-du-would-drop:`, 6/min) + DROP | LOG only (`MODE=log`) | LOG + DROP |
| 8 | `RETURN` | yes | yes |

Not restricted: internet egress (immich-ml still downloads models; Wizarr's update check
still works) and anything to the tailnet `100.64.0.0/10`.

## Install

Prerequisites: security batch 2 is live (every Alloy pushes to `10.1.20.21`; UniFi
`192.168.191.20 -> 10.1.20.21:3100`), the host has the baseline daemon.json
([[docker-daemon]]), and you have the files in `/tmp/kdk-host` (see `hosts/README.md`).
Do **dmz-01 first**, then dkr-01.

The fence allows NFS v4 only (2049; 111 is let through but nothing else of the v3 side
channels): every NFS mount on the host must be v4.x before you load it, or it hangs.

```sh
mount | grep -E ' type nfs4? ' | grep -v 'vers=4' || echo all-v4    # must print all-v4
```

```sh
cd /tmp/kdk-host
H=$(hostname)        # kdk-dkr-dmz-01 or kdk-dkr-01
sudo apt-get install -y nftables          # nft CLI; do NOT enable nftables.service
systemctl is-enabled nftables 2>/dev/null # must not say "enabled" (its config flushes Docker's rules)

# 1. storage fence
sudo install -d -m 755 /etc/nftables.d
sudo install -m 644 $H/nftables.d/kdk-storage.nft /etc/nftables.d/kdk-storage.nft
sudo nft -c -f /etc/nftables.d/kdk-storage.nft && echo syntax-ok
sudo install -m 644 _common/systemd/kdk-storage-fence.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kdk-storage-fence.service

# 2. DOCKER-USER policy
sudo install -m 755 _common/sbin/kdk-docker-user.sh /usr/local/sbin/kdk-docker-user.sh
sudo install -m 644 $H/kdk-docker-user.conf /etc/default/kdk-docker-user
sudo /usr/local/sbin/kdk-docker-user.sh --dry-run        # read it
sudo install -m 644 _common/systemd/kdk-docker-user.service _common/systemd/kdk-docker-user.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kdk-docker-user.service kdk-docker-user.timer
journalctl -u kdk-docker-user -n 5 --no-pager            # "policy applied: MODE=..."
```

On **dkr-01**, while the `docker-forward` sidecar is still running it keeps checking for
the two tailscale0 rules and finds them (no duplicates). After the unit is verified,
merge the compose half of batch 7 (removes the sidecar from the stack) and then remove
the now-orphaned container: `sudo docker rm -f tailscale-docker-forward`.

### rpcbind, LLMNR (both VMs, hosts report M-07)

NFS on both VMs is v4.1 only, which needs no rpcbind. Check before masking:

```sh
mount | grep ' type nfs'            # every line must show vers=4.1 (or vers=4.x); stop if any shows vers=3
sudo systemctl mask --now rpcbind.socket rpcbind.service
sudo mkdir -p /etc/systemd/resolved.conf.d
printf '[Resolve]\nLLMNR=no\n' | sudo tee /etc/systemd/resolved.conf.d/90-kdk-no-llmnr.conf
sudo systemctl restart systemd-resolved
sudo ss -lntup | grep -E ':(111|5355) ' || echo closed
ls /mnt/media >/dev/null && echo nfs-ok    # mounts unaffected
```

## Verify

```sh
sudo nft list table inet kdk-storage                       # counters on the drop rules
sudo iptables -S DOCKER-USER                                # matches `kdk-docker-user.sh --dry-run`
systemctl list-timers kdk-docker-user.timer
```

dmz-01 (enforce):

```sh
docker exec jellyfin sh -c 'timeout 3 bash -c "</dev/tcp/172.16.30.2/2049" && echo OPEN || echo blocked'   # blocked
docker exec jellyfin sh -c 'timeout 3 bash -c "</dev/tcp/172.16.30.50/22" && echo OPEN || echo blocked'    # blocked
docker exec jellyfin sh -c 'timeout 3 bash -c "</dev/tcp/10.211.2.1/8120" && echo OPEN || echo blocked'    # blocked (host_guard)
docker exec seerr node -e "require('net').connect(443,'10.1.20.20').on('connect',()=>{console.log('open');process.exit(0)}).on('error',e=>{console.log(e.code);process.exit(1)})"   # open (exception)
docker exec wizarr python3 -c 'import socket;s=socket.socket();s.settimeout(3);print(s.connect_ex(("10.1.20.20",443)))'  # non-zero
docker exec seerr wget -qO- --timeout=5 http://jellyfin:8096/health                                        # bridge-local: Healthy
```

(`nc -zvw2 172.16.30.2 2049` is the same check where the image ships `nc`.) Also: a
Seerr search returns Radarr results; Jellyfin LDAP login works; `{host="kdk-dkr-dmz-01"}`
logs keep arriving in Grafana Explore.

dkr-01 (log mode): tailnet clients still reach every advertised subnet (a phone on
cellular opens `https://seerr.kmkdp.com` and `https://homeassistant.kmkdp.com`), and

```sh
sudo journalctl -k --since -1h | grep -c 'kdk-du-would-drop'
sudo journalctl -k --since -1d | grep 'kdk-du-would-drop' | grep -oE 'SRC=[0-9.]+ DST=[0-9.]+ .*DPT=[0-9]+' | sed -E 's/ LEN=.*PROTO=/ /;s/ SPT=[0-9]+//' | sort | uniq -c | sort -rn | head -30
```

Map a `SRC=` to its container with
`docker ps -q | xargs docker inspect -f '{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' | grep <ip>`.

### Canary: storage-health

The hourly `storage-health` one-shot on dkr-01 pings `172.16.30.2`, connects to 2049 and
stats every NFS mount; it pages Pushover on failure. After installing the fence on
dkr-01, run it once by hand (Komodo -> Procedures -> `storage-health` -> Run) and expect
no page. A page after a fence change = roll the fence back first, debug second.

## dkr-01: log -> enforce

After 7 days in `MODE=log` with every `kdk-du-would-drop` line either explained by a new
`allow_dst`/`allow_ctr` entry (a PR to `hosts/kdk-dkr-01/kdk-docker-user.conf`) or
confirmed unwanted:

```sh
sudo sed -i 's/^MODE=log$/MODE=enforce/' /etc/default/kdk-docker-user
sudo systemctl start kdk-docker-user.service
sudo iptables -S DOCKER-USER | grep -c -- '-j DROP'      # > 0
```

and change `MODE=enforce` in the repo copy in the same PR.

## Add an exception

Edit `hosts/<host>/kdk-docker-user.conf` in a PR (`allow_dst <ip> <tcp|udp> <ports>` for
any container, `allow_ctr <container> <ip> <tcp|udp> <ports>` for one), copy it to
`/etc/default/kdk-docker-user`, `sudo systemctl start kdk-docker-user.service`.
`allow_ctr` resolves the container's addresses at each run (every 5 min, and on every
Docker start): after the container is recreated, its exception is missing for up to five
minutes (fail closed) unless you start the service by hand.

## Rollback

Storage fence (instant, no restarts):

```sh
sudo systemctl disable --now kdk-storage-fence.service    # ExecStop = nft delete table inet kdk-storage
# or, if the unit is not installed: sudo nft delete table inet kdk-storage
```

DOCKER-USER policy (dkr-01: put the tailscale rules back first, or remote access dies):

```sh
sudo systemctl disable --now kdk-docker-user.timer kdk-docker-user.service
sudo iptables -F DOCKER-USER
sudo iptables -A DOCKER-USER -i tailscale0 -j ACCEPT      # dkr-01 only
sudo iptables -A DOCKER-USER -o tailscale0 -j ACCEPT      # dkr-01 only
sudo iptables -A DOCKER-USER -j RETURN
```

If the compose half of batch 7 is already merged on dkr-01, the sidecar is gone: the
two manual rules above last until the next reboot or Docker restart. Either re-enable
the unit with `TAILSCALE=1` and an empty exception list in `MODE=log`, or revert the
sidecar removal (`git revert` of that hunk, Komodo deploys it).

## Enforce history

| Host | MODE=enforce since | Evidence before the switch |
|---|---|---|
| kdk-dkr-01 | 2026-10-29 23:02 CDT | 3 days in `log` mode: 26 `kdk-du-would-drop` lines, all late `ACK`/`FIN`/`PSH` segments from containers back to scrape/HA clients after the conntrack entry expired (no `SYN`), i.e. nothing a container needed. After the switch: 0 drops in the first minutes, every dkr-01 Prometheus target up, Alloy still shipping. |
