---
title: Docker daemon baseline (daemon.json)
status: active
tier: infra
tags: [docker, security, baseline]
---

# Docker daemon baseline (`daemon.json`)

Every compose host runs the same daemon settings on top of its address pool
([[docker-ipam]]). The files are `hosts/<host>/daemon.json`, copied by hand to
`/etc/docker/daemon.json` (security plan A2; hosts report H-03, H-05, M-09).

```json
{
  "default-address-pools": [ { "base": "10.2xx.0.0/16", "size": 24 } ],
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" },
  "live-restore": true,
  "no-new-privileges": true,
  "userland-proxy": false,
  "icc": false
}
```

kdk-dkr-01 also keeps `"runtimes": {"nvidia": ...}` (tdarr-node uses `runtime: nvidia`;
the 2026-09-19 IPAM rollout dropped it once).

| Key | Effect | When it applies |
|---|---|---|
| `log-driver` / `log-opts` | Default json-file rotation, 10 MB x 3, for containers without their own `logging:` block (zigbee2mqtt had a 6.7 GB log) | containers **created** after the daemon restart |
| `live-restore` | Containers keep running while dockerd restarts or upgrades (also lets unattended-upgrades take Docker CE safely) | immediately on `systemctl reload docker` (one of the reloadable options) |
| `no-new-privileges` | Default `no-new-privileges` for every container: setuid binaries and file capabilities inside the image cannot raise privileges. A root process that drops to a user (s6, gosu, su-exec) is unaffected | containers **created** after the restart. Per-service opt-out: `security_opt: ["no-new-privileges:false"]` with a comment |
| `userland-proxy: false` | No `docker-proxy` processes; published ports are pure NAT, and containers see the real client address on hairpin/loopback paths too | port mappings set up after the restart; recreate to be sure |
| `icc: false` | Containers on the **default `docker0` bridge** cannot talk to each other. User-defined (compose) networks are not affected; their isolation is per network as before | daemon restart (docker0 is set up at start) |

Docker 29 keeps the iptables firewall backend by default (nftables is opt-in and
experimental); do not set `firewall-backend`. The `DOCKER-USER` chain the host
firewall uses ([[host-firewall]]) exists only with the iptables backend.

## Pre-tests (done when writing the change, 2026-10-28)

**`userland-proxy: false`**

- **dkr-02, mosquitto `127.0.0.1:1883`** -- Home Assistant runs with host networking and
  reaches the broker on loopback. Without the proxy, Docker serves loopback-published
  ports through NAT (`route_localnet` on the bridge + an OUTPUT DNAT rule). Test on dkr-02
  right after the restart: `nc -zv 127.0.0.1 1883` on the host and the MQTT integration
  shows connected in HA.
- **dkr-01, tdarr `8266`** -- `tdarr-backlog`, `tdarr-remediate` and `tdarr-verify` run
  with host networking and call `http://localhost:8266`. Security batch 7 publishes tdarr
  on `127.0.0.1:8266` *and* `10.1.20.20:8266` for that reason; after the change,
  `curl -s -o /dev/null -w '%{http_code}' http://localhost:8266/api/v2/status` on the host
  returns 200.
- Every other published port is reached from other hosts (Prometheus scrapes, Traefik,
  NTP, the vacuum, the Tdarr GPU node) or by a container on another bridge via the host
  address (NAT hairpin; unchanged).

**`icc: false`** -- checked every `stacks/*/compose.yaml`: no service uses
`network_mode: bridge` or the default `docker0` network; every stack runs on its own
compose network, an external shared one (`proxy`, `proxy-dmz`, `identity_default`,
`immich_internal`, ...), `network_mode: host` (tailscale, tailscale-dns, node-exporter,
the tdarr/arr/storage one-shots, home-assistant, scrypted/homebridge) or
`network_mode: container:`. Cross-stack traffic always goes over an explicitly shared
network, which `icc` does not touch. Only ad-hoc `docker run` containers without
`--network` land on `docker0`.

**`no-new-privileges`** -- the risk is an image that starts as a non-root user and then
calls a setuid helper (`sudo`, `su`) or relies on file capabilities (`ping` as non-root).
None of the stacks is known to; the recreate in the apply steps below is the test. A
service that breaks gets `security_opt: ["no-new-privileges:false"]` with a comment and
an entry in the compose-baseline exceptions.

## Apply on one host

Rollout order, least to most critical: **dkr-03 -> dkr-02 -> mon-01 -> dmz-01 -> dkr-01**.

```sh
# 1. Install (keep the old file for rollback)
sudo cp -a /etc/docker/daemon.json /etc/docker/daemon.json.pre-baseline
sudo install -m 644 /tmp/kdk-host/<host>/daemon.json /etc/docker/daemon.json
python3 -m json.tool /etc/docker/daemon.json >/dev/null && echo json-ok

# 2. Reload: applies live-restore only (reloadable), containers untouched
sudo systemctl reload docker
docker info --format '{{.LiveRestoreEnabled}}'          # -> true. If false: STOP, do not restart.

# 3. Restart: applies the rest. live-restore keeps every container running.
sudo systemctl restart docker
docker info --format '{{.LiveRestoreEnabled}} {{.LoggingDriver}}'   # -> true json-file
docker ps --format '{{.Names}} {{.Status}}' | grep -v ' Up ' || echo all-up

# 4. Recreate so log-opts / no-new-privileges / userland-proxy reach every container.
#    Komodo: Deploy each stack on this server (or `docker compose up -d --force-recreate`
#    in each stack dir). NEVER the `nut` stack on dkr-03: leave it running; it picks up
#    the defaults whenever it is next recreated on purpose.
docker inspect -f '{{.Name}} nnp={{.HostConfig.SecurityOpt}} log={{.HostConfig.LogConfig.Config}}' $(docker ps -q)
ps -C docker-proxy -o pid= | wc -l                       # -> 0 once everything is recreated
```

Step 4 is when a broken image shows up: do it in the change window, not weeks later
when a Renovate bump recreates the container.

## Rollback

`live-restore` stays on in every rollback (turning it off with a restart stops every
container). Remove only the key that caused the problem, or go back to the saved file
and keep `live-restore`:

```sh
sudo cp /etc/docker/daemon.json.pre-baseline /etc/docker/daemon.json
sudo python3 - <<'EOF'
import json; p="/etc/docker/daemon.json"; d=json.load(open(p)); d["live-restore"]=True
json.dump(d, open(p,"w"), indent=2)
EOF
sudo systemctl restart docker      # containers stay up (live-restore)
# then recreate the affected stack(s) so their containers pick up the old behaviour
```

For a single service that breaks under `no-new-privileges`, prefer the per-service
`security_opt: ["no-new-privileges:false"]` override over a host rollback.
