---
title: Docker network IPAM plan
status: active
tier: infra
tags: [docker, networking, ipam]
---

# Docker network IPAM (address plan)

## Why

Docker's default address pool (`172.17.0.0/12` then `192.168.0.0/16` in `/20`
chunks) is unaware of the lab's real networks. On `kdk-dkr-01` the `172.x` pool was
exhausted (30 stacks), so the next auto-assigned network took **`192.168.176.0/20`**,
which **overlaps the DMZ `192.168.191.0/24`** and black-holed `kdk-dkr-01 -> DMZ`
routing (Authelia forward-auth), taking the whole media stack "down" (incident
2026-09-19). Fix: give every docker host an explicit `default-address-pools` inside a
dedicated supernet that overlaps nothing.

## Supernet

**`10.208.0.0/12`** (`10.208.0.0`–`10.223.255.255`) — reserved for all kdk docker
bridge networks. Clear of every real network: VLANs `10.1.0.0/16`, storage
`172.16.30.0/24`, DMZ `192.168.191.0/24`, Tailscale `100.64.0.0/10`.

One `/16` per host (256 `/24`s each). Bridge networks are host-local, but distinct
per-host /16s keep them unambiguous:

| Host | Mgmt IP | Docker /16 | daemon.json base |
|---|---|---|---|
| kdk-dkr-01 | 10.1.20.20 | `10.208.0.0/16` | `10.208.0.0/16` size 24 |
| kdk-dkr-02 | 10.1.30.21 | `10.209.0.0/16` | `10.209.0.0/16` size 24 |
| kdk-dkr-03 | 10.1.30.22 | `10.210.0.0/16` | `10.210.0.0/16` size 24 |
| kdk-dkr-dmz-01 | 192.168.191.20 | `10.211.0.0/16` | `10.211.0.0/16` size 24 |
| kdk-mon-01 | 10.1.20.30 | `10.212.0.0/16` | `10.212.0.0/16` size 24 |

Spare `/16`s for future hosts: `10.213.0.0/16` – `10.223.0.0/16`.

The per-host `daemon.json` files are version-controlled under `hosts/<host>/daemon.json`
and applied to `/etc/docker/daemon.json` on each host.

## Rule for new stacks

With `default-address-pools` set, **any** new compose network auto-allocates a safe
`/24` inside the host's `/16` — no per-stack pinning required. Pin an explicit subnet
(`networks.default.ipam.config.subnet`) only when a stack needs a stable, known range
(e.g. for firewall rules). Never let a stack use `192.168.176.0/20`–`192.168.191.0/24`.

## Rollout runbook (per host)

Order least→most critical: **dkr-03 → dkr-02 → mon-01 → dmz-01 → dkr-01**. Validate
each host before moving on.

1. **Place config:** copy `hosts/<host>/daemon.json` to `/etc/docker/daemon.json`
   (back up any existing file first; merge if it already has keys).
2. **Restart docker:** `sudo systemctl restart docker` (bounces all containers on that
   host briefly; they return on their *existing* subnets — the pool only affects
   networks created from now on).
3. **Migrate existing networks:** for each stack, recreate its network so it moves into
   the new pool — `docker compose down && docker compose up -d` (or Komodo
   DestroyStack + DeployStack). External shared networks (`proxy`, `proxy-dmz`) must be
   recreated with their dependent stacks stopped, then dependents restarted.
4. **Verify:** `docker network inspect <net> -f '{{range .IPAM.Config}}{{.Subnet}}{{end}}'`
   shows a `10.20x.x` subnet; containers healthy; for dkr-01 confirm
   `ip route get 192.168.191.20` still routes `via 10.1.20.1` (DMZ reachable) and media
   endpoints return non-`000`.

Host-network stacks (`tailscale-subnet-router`, `observability-agent`, `node-exporter`)
and `docker0` (`172.17`) have nothing to migrate.

## Related

- Komodo git-zombie leak: add `init: true` to the `komodo-core` service so tini reaps
  re-parented `git` children (repo polling). See `stacks/komodo/compose.yaml`.
