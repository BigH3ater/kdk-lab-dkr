---
title: Media stack
host: kdk-dkr-01
stack: media
tier: internal
status: live
storage: yes
secrets: [kdk-dee-01-api-token]
updated: 2026-10-27
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
| Endpoints | `prowlarr / sabnzbd / sonarr / radarr / bazarr / chaptarr / tdarr .kmkdp.com` |
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
| Transcode error "Subtitle codec 94213 is not supported" | MP4 source with tx3g subs, container step not conforming | both MKV steps in the flow have `forceConform=true`; re-queue |
| AV1 file stays AV1 / 4K remux keeps foreign audio | flow bypass (bitrate gate / old DV hold) | `scripts/tdarr-flow-sync.sh diff` shows repo flow == live (see tdarr runbook) |
| Hindi/Tamil/Telugu movie appears in Radarr (or gets downloaded) | IMDb Popular / Top 250 import lists carry Indian titles; Radarr cannot filter IMDb lists by language | `arr-list-guard` procedure (every 6 h) removes untagged, file-less ones with an exclusion; already-downloaded ones are reported, delete by hand. Want one? request it in Seerr (tagged) |

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

Each app joins `default` + `proxy`; ports are `prowlarr 9696`, `sabnzbd 8080`, `sonarr 8989`, `radarr 7878`, `bazarr 6767`, `chaptarr 8789`, `tdarr 8265`. Tdarr also publishes node RPC `8266` (on `10.1.20.20`) for the external RGB-Nebula 4090 Windows node, which is no longer registered (see the [Tdarr runbook](../runbooks/tdarr-troubleshooting.md)); transcoding runs on the local node. Sonarr/Radarr have a higher-priority `/api` router without ForwardAuth so the DMZ Seerr can use the API with a key. The local node `tdarr-node` (`kdk-tdarr-nvenc-01`) uses the NVIDIA runtime and the GTX 1050 Ti; flow encodes to HEVC with NVENC and re-encodes any AV1 source (software decode). Tdarr 3.x (Rust engine).

## Configuration

| Path / setting | What it is |
|---|---|
| `stacks/media/compose.yaml` | Images, volumes, Traefik + `/api`-bypass labels |
| `*__AUTH__METHOD=External` | arr UIs trust Authelia; API keys still gate `/api` |
| `<app>/config.xml` `<ApiKey>` | per-app API key (not in 1P) |
| `sabnzbd/sabnzbd.ini` `host_whitelist` | accepted Host headers |
| Radarr → Settings → Import Lists | 15 lists (13 TMDb directors, IMDb Popular, IMDb Top 250); `listSyncLevel=disabled`. Language block = `stacks/arr-list-guard` (`LANGUAGES` env) |


### Wizarr (invites) — MOVED

Wizarr left the media stack on 2026-10-27 for its own internet-facing DMZ stack
(`stacks/wizarr` on kdk-dkr-dmz-01). Its LDAP settings table, the public-path allowlist,
and the `wizarr-sync` invite-reconcile automation now live in
[Wizarr](wizarr.md).

### Trailarr

`stacks/trailarr` on kdk-dkr-01 (`nandyalu/trailarr`, `/opt/kdk-lab/trailarr:/config`,
`trailarr.kmkdp.com` behind `forwardauth@file`). It keeps its own web login
(default `admin` / `trailarr`); ours is `op://kdk-ops/trailarr-admin`. Its Radarr and
Sonarr connections hold copies of their API keys (connections 1 and 2).

Reset the login (forgotten password): Trailarr hashes `WEBUI_PASSWORD` once at
start-up and stores only the hash, so apply it from a one-off container and never
put it in compose:

```bash
IMG=$(grep -oE "nandyalu/trailarr:[^ ]+" stacks/trailarr/compose.yaml)
op read op://kdk-ops/trailarr-admin/password | tr -d '\n' | sed 's/^/WEBUI_PASSWORD=/' | ssh kdkadmin@10.1.20.20 "
  sudo install -m 600 -o root -g root /dev/stdin /root/trailarr-pwreset.env
  sudo docker stop trailarr
  sudo docker run -d --name trailarr-pwreset --env-file /root/trailarr-pwreset.env -e PUID=1000 -e PGID=1000 -e TZ=America/Chicago \
    -v /opt/kdk-lab/trailarr:/config -v /mnt/media:/data/media --network none $IMG
  sleep 40; sudo docker logs trailarr-pwreset 2>&1 | grep -c 'Application startup complete'   # 1
  sudo docker stop trailarr-pwreset; sudo docker rm trailarr-pwreset; sudo shred -u /root/trailarr-pwreset.env
  sudo docker start trailarr"
```

Check: `POST /api/v1/auth/login` with `{"username":"admin","password":…}` from
inside the container returns 200 and a `trailarr_session` cookie. After an arr
API-key rotation, update both connections with `PUT /api/v1/connections/<id>`
(body = the connection's `name, arr_type, url, external_url, monitor_new_media,
path_mappings` plus the new `api_key`) using that cookie, then
`GET /api/v1/connections/<id>/refresh`; the log shows `Trailarr refreshed the data
from 'Radarr'` / `'Sonarr'` instead of `could not read the media data`.

## Secret Rotation

| Secret (1P item) | Used for | Class | Rotate live? |
|---|---|---|---|
| `kdk-dee-01-api-token` | Tdarr DEE wrapper auth | 🟢 self-service | yes — rotate + redeploy |
| arr API keys | app-to-app auth | 🟢 self-service | regenerate in each app UI; update consumers (Prowlarr, Seerr, Bazarr, Trailarr, Libreseerr for Chaptarr); arr-upgrade-search / arr-list-guard / tdarr-remediate read `config.xml` and need nothing |
| `kdk-ops/trailarr-admin` | Trailarr web login | 🟢 self-service | one-off `WEBUI_PASSWORD` start (see Trailarr above) |

## User Guide

Prowlarr feeds indexers to Sonarr/Radarr; downloads land via SABnzbd into `/mnt/media/staging`, are imported to `tv`/`movies`, then Tdarr transcodes. Jellyfin invites are issued by [Wizarr](wizarr.md) (now on the DMZ).
