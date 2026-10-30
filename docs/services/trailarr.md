---
title: Trailarr
host: kdk-dkr-01
stack: trailarr
tier: internal
status: live
secrets: [trailarr-admin]
updated: 2026-10-30
tags: [homelab/service, tier/internal, status/live, media]
---

# Trailarr (trailarr.kmkdp.com)

Downloads trailers (server-side `yt-dlp`) for titles that **Radarr/Sonarr**
manage and files them as Jellyfin local trailers (`<title>/trailers/*.mp4`),
which **Neptune** on Apple TV plays natively (no YouTube on the TV). Internal
tier on **kdk-dkr-01**, behind the internal Traefik with **Authelia
ForwardAuth** — and Trailarr keeps its own web login as well.

**Related:** [[kdk-dkr-01]] · [[traefik]] · [[media]] · [[jellyfin]]

## At a glance

| Field | Value |
|---|---|
| Purpose | Download + file local trailers for Radarr/Sonarr titles |
| URL | https://trailarr.kmkdp.com (internal Traefik + Authelia; own login too) |
| Host | [[kdk-dkr-01]] |
| Stack | `stacks/trailarr` — `nandyalu/trailarr` (pinned `0.14.0`) |
| Policy | **new titles only** — connections added `monitor=none`, synced, then switched to `new`, so only newly added movies/shows get trailers (Trailarr's large-library advice) |
| Paths | the same single `/mnt/media:/data/media` mount as Radarr/Sonarr, so their paths resolve unchanged (no Trailarr path mappings); config at `/opt/kdk-lab/trailarr:/config` |
| Deploy | Komodo stack `trailarr` (`deploy = true`), webhook + 15-min poll |
| Blast radius | No new trailers download; existing trailers and playback unaffected |

Tdarr's libraries ignore `trailers`/`-trailer` paths (`foldersToIgnore`), so it
never transcodes these files.

## Verify

```bash
ssh kdkadmin@10.1.20.20 'docker ps --filter name=trailarr --format "{{.Names}} {{.Status}}"'   # trailarr Up
curl -s -o /dev/null -w '%{http_code}\n' https://trailarr.kmkdp.com/   # 302 to sso.kmkdp.com (Authelia)
```

A new movie added in Radarr gets a `trailers/*.mp4` under its folder; Neptune
plays it as a Trailer extra.

## Troubleshooting

| Problem | Cause | Command to validate |
|---|---|---|
| Forgotten web login | `WEBUI_PASSWORD` is hashed once at start-up | run the one-off reset below |
| `could not read the media data` in logs | an arr API key rotated; the connection copy is stale | refresh the connection (see Secret Rotation) |
| No trailers for a title | title not covered by the `new` policy (added before the switch) | expected — only newly added titles are monitored |
| 502 at the URL | `trailarr` not healthy / wrong port | `docker logs trailarr`; service port is 7889 |

### Reset the web login (forgotten password)

Trailarr hashes `WEBUI_PASSWORD` once at start-up and stores only the hash, so
apply it from a one-off container and never put it in compose:

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
inside the container returns 200 and a `trailarr_session` cookie.

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | [[traefik]] (`proxy` network) | no ingress / TLS |
| Needs | [[identity]] (Authelia, `sso.kmkdp.com`) | no SSO in front of the UI |
| Needs | [[media]] Radarr + Sonarr (connections 1 and 2) | no titles to fetch trailers for |
| Feeds | [[jellyfin]] / Neptune | local trailers to play |

## Observability

No dedicated dashboard. Container health via Komodo / `docker ps`; logs ship to
Loki (`{container="trailarr"}`) via the observability agent.

## Architecture

Single container `trailarr` (`nandyalu/trailarr:0.14.0`) on `default` + the
external `proxy` network, port `7889`, `forwardauth@file` in front. It mounts the
same `/mnt/media:/data/media` as the arr apps so paths resolve without mappings,
and holds its own copies of the Radarr/Sonarr API keys in connections 1 and 2.
Trailer downloads run server-side via `yt-dlp`.

## Configuration

| Path / setting | What it is |
|---|---|
| `stacks/trailarr/compose.yaml` | the service + Traefik labels (`forwardauth@file`) |
| `/opt/kdk-lab/trailarr` | Trailarr config (`/config`) |
| Radarr/Sonarr connections | `monitor=none` → sync → `new` (new titles only) |
| Komodo stack `trailarr` | server kdk-dkr-01, webhook on |

## Secret Rotation

| Secret (1P item) | Used for | Class | Rotate live? |
|---|---|---|---|
| `kdk-ops/trailarr-admin` | Trailarr web login | 🟢 self-service | one-off `WEBUI_PASSWORD` start (see "Reset the web login" above) |

After an arr API-key rotation, update both connections with
`PUT /api/v1/connections/<id>` (body = the connection's `name, arr_type, url,
external_url, monitor_new_media, path_mappings` plus the new `api_key`) using the
`trailarr_session` cookie, then `GET /api/v1/connections/<id>/refresh`; the log
shows `Trailarr refreshed the data from 'Radarr'` / `'Sonarr'`.

## User Guide

Internal tool — not on the family dashboard. Open `https://trailarr.kmkdp.com`
on the LAN or over Tailscale and sign in with your Authelia login (then the
Trailarr login). It fills trailers for newly added movies and shows.
