---
title: Seerr
host: kdk-dkr-dmz-01
stack: dmz-apps
tier: dmz
status: live
storage: yes
secrets: []
updated: 2026-09-12
tags: [homelab/stack, tier/dmz, status/live]
---

# Seerr

Media request UI, public via the Cloudflare tunnel, authenticating against Jellyfin accounts.

**Related:** [[kdk-dkr-dmz-01]] · [[jellyfin]] · [[media]] · [[dmz-proxy]]

## At a glance

| Field | Value |
|---|---|
| Purpose | Users request TV/movies; Seerr forwards to Sonarr/Radarr |
| Status | 🟢 Live — Jellyfin link works; Sonarr/Radarr pending a firewall rule |
| Host | [[kdk-dkr-dmz-01]] |
| Endpoint | `https://seerr.kmkdp.com` |
| Image | `ghcr.io/seerr-team/seerr:v3.4.1` |
| Deploy | Komodo stack `dmz-apps` (git-linked) |
| Storage | `/mnt/appdata/jellyseerr` (NFS, same data as the k3s deploy) |
| Blast radius | No new requests; existing library unaffected |

## Verify

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://seerr.kmkdp.com/          # 200/307
docker exec seerr getent hosts sonarr.kmkdp.com                            # 10.1.20.20 (extra_hosts)
```
In Settings, the Jellyfin, Sonarr, and Radarr integrations each test green.

## Troubleshooting

| Problem | Cause | Command to validate |
|---|---|---|
| Sonarr/Radarr test fails ("connection refused/timeout") | DMZ→internal firewall closed | operator must open `192.168.191.20 → 10.1.20.20:443` |
| arr hostnames don't resolve on the DMZ | public DNS points them at the DMZ, not internal Traefik | `extra_hosts` maps `sonarr/radarr.kmkdp.com → 10.1.20.20` |
| Jellyfin link broken | wrong `externalHostname` scheme | must be `https://jellyfin.kmkdp.com` (lowercase scheme) |

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | [[jellyfin]] | user auth + library status |
| Needs | [[media]] Sonarr/Radarr `/api` | request fulfilment (needs the firewall rule) |
| Needs | [[dmz-proxy]] + tunnel | ingress + public access |

## Observability

No dashboard. `seerr.kmkdp.com` probed by [[kdk-mon-01]]; alerts → Pushover.

## Architecture

Listens on `5055`, joins `proxy-dmz`. It reaches Sonarr/Radarr over the internal Traefik `/api` bypass at `https://sonarr.kmkdp.com` and `radarr.kmkdp.com` (port 443), resolved via `extra_hosts` to `10.1.20.20`. Jellyfin is reached in-DMZ at `jellyfin:8096`.

## Configuration

| Path / setting | What it is |
|---|---|
| `/mnt/appdata/jellyseerr/settings.json` | Jellyfin + Sonarr/Radarr backends, API keys |
| `stacks/dmz-apps/compose.yaml` `extra_hosts` | pins arr hostnames to internal Traefik |

Edit `settings.json` only with Seerr stopped (it rewrites the file on shutdown).

## Secret Rotation

N/A — no credential of its own; holds arr API keys inside `settings.json` (rotate those in the arr apps, then update here).

## User Guide

Users log in at `seerr.kmkdp.com` with their Jellyfin account and request titles; approved requests flow to Sonarr/Radarr for download.
