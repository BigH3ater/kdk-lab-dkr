---
title: Media stack
host: kdk-dkr-01
stack: media
tier: internal
status: live
storage: yes
secrets: [kdk-dee-01-api-token]
updated: 2026-09-12
tags: [homelab/stack, tier/internal, status/live]
---

# Media stack

Acquisition, subtitles, request-library management and GPU transcode on kdk-dkr-01; every UI is behind Authelia ForwardAuth.

**Related:** [[kdk-dkr-01]] · [[traefik]] · [[jellyfin]] · [[seerr]]

## At a glance

| Field | Value |
|---|---|
| Purpose | Sonarr/Radarr/Prowlarr/Bazarr/SABnzbd + Tdarr NVENC transcode |
| Status | 🟢 Live — each UI 302s to SSO; arr health clean |
| Host | [[kdk-dkr-01]] |
| Endpoints | `prowlarr / sabnzbd / sonarr / radarr / bazarr / chaptarr / wizarr / tdarr .kmkdp.com` |
| Deploy | Komodo stack `media` (git-linked, `op inject` pre_deploy) |
| Storage | `/opt/kdk-lab/media/<app>` (config); bulk on `/mnt/media/{tv,movies,staging,quarantine}` + `/mnt/dee-exchange` |
| Blast radius | No new downloads/imports/transcodes; Jellyfin playback unaffected |

## Verify

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://sonarr.kmkdp.com/          # 302 (SSO gate)
curl -s -o /dev/null -w '%{http_code}\n' -H "X-Api-Key: $KEY" https://sonarr.kmkdp.com/api/v3/health  # 200 (bypass)
docker exec media-tdarr-node-1 nvidia-smi -L                                # GTX 1050 Ti listed
```

## Troubleshooting

| Problem | Cause | Command to validate |
|---|---|---|
| arr indexer/download-client points at old cluster | stale `*.media.svc.cluster.local` host | in the app, host should be `sabnzbd` / `prowlarr` (compose service name) |
| SABnzbd returns 403 to arr | Host header not whitelisted | `grep host_whitelist /opt/kdk-lab/media/sabnzbd/sabnzbd.ini` includes `sabnzbd` |
| Seerr can't reach Sonarr/Radarr API | DMZ→internal firewall closed | operator must open `192.168.191.20 → 10.1.20.20:443` |
| Tdarr idle with jobs queued | staged jobs pinned to dead nodes / 0 workers | clear StagedJSONDB; set transcode workers to 1 |
| Transcode drops Atmos to native E-AC3 | DEE wrapper 503 (single slot) | keep exactly 1 transcode worker on the node |

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | [[traefik]] + [[identity]] | ingress and ForwardAuth |
| Needs | `/mnt/media` (NFS, kdk-nas-01) | no library or staging access |
| Needs | [[kdk-dee-01]] wrapper | Dolby Atmos audio conversion in Tdarr |
| Needed by | [[jellyfin]], [[seerr]] | library content and request fulfilment |

## Observability

No dashboard. Host probes via [[kdk-mon-01]]; alerts → Pushover.

## Architecture

Each app joins `default` + `proxy`; ports are `prowlarr 9696`, `sabnzbd 8080`, `sonarr 8989`, `radarr 7878`, `bazarr 6767`, `chaptarr 8789`, `wizarr 5690`, `tdarr 8265`. Tdarr also publishes node RPC `8266` for the external RGB-Nebula 4090 node. Sonarr/Radarr have a higher-priority `/api` router without ForwardAuth so the DMZ Seerr can use the API with a key. `tdarr-node` (`kdk-tdarr-nvenc-01`) uses the NVIDIA runtime and the GTX 1050 Ti; flow is NVENC, no AV1.

## Configuration

| Path / setting | What it is |
|---|---|
| `stacks/media/compose.yaml` | Images, volumes, Traefik + `/api`-bypass labels |
| `*__AUTH__METHOD=External` | arr UIs trust Authelia; API keys still gate `/api` |
| `<app>/config.xml` `<ApiKey>` | per-app API key (not in 1P) |
| `sabnzbd/sabnzbd.ini` `host_whitelist` | accepted Host headers |

## Secret Rotation

| Secret (1P item) | Used for | Class | Rotate live? |
|---|---|---|---|
| `kdk-dee-01-api-token` | Tdarr DEE wrapper auth | 🟢 self-service | yes — rotate + redeploy |
| arr API keys | app-to-app auth | 🟢 self-service | regenerate in each app UI; update consumers (Prowlarr, Seerr, Bazarr) |

## User Guide

Prowlarr feeds indexers to Sonarr/Radarr; downloads land via SABnzbd into `/mnt/media/staging`, are imported to `tv`/`movies`, then Tdarr transcodes. Wizarr issues Jellyfin invites.
