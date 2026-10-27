---
title: Jellyfin
host: kdk-dkr-dmz-01
stack: jellyfin
tier: dmz
status: live
storage: yes
secrets: []
updated: 2026-10-25
tags: [homelab/stack, tier/dmz, status/live]
---

# Jellyfin

Media server on the DMZ node with Intel Arc A380 QSV transcode; public via the Cloudflare tunnel with its own login (LDAP plugin), no ForwardAuth.

**Related:** [[kdk-dkr-dmz-01]] · [[dmz-proxy]] · [[media]] · [[seerr]]

## At a glance

| Field | Value |
|---|---|
| Purpose | Streaming server for the TV/movie library |
| Status | 🟢 Live — login + playback work; plugins Active |
| Host | [[kdk-dkr-dmz-01]], `/dev/dri` mapped (i915) |
| Endpoint | `https://jellyfin.kmkdp.com` (LAN + Cloudflare tunnel) |
| Image | `lscr.io/linuxserver/jellyfin:12.3ubu2604-ls56` — 12.x line, pinned; server and plugins move together |
| Deploy | Komodo stack `jellyfin` (git-linked) |
| Storage | `/opt/kdk-lab/jellyfin/config` (byte-for-byte from k3s); library RO from `/mnt/media` |
| Blast radius | No streaming; requests via Seerr still queue |

## Verify

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://jellyfin.kmkdp.com/health   # 200
docker exec jellyfin ls /dev/dri                                             # renderD128 present
```
In the dashboard, an active transcode shows `(hw)`, watched state is present, all plugins read Active.

## Troubleshooting

| Problem | Cause | Command to validate |
|---|---|---|
| No `/dev/dri` in container | host on the cloud kernel (no i915) | host runs `linux-image-amd64`, not the cloud kernel |
| Plugins read "Restart"/broken after an image bump | plugin build targets an older ABI | reinstall the matching build from its repository (`stacks/jellyfin/plugins.yaml`) |
| Trickplay: "Error creating trickplay images" / Read-only file system | library option *Save trickplay images next to media* on a RO mount | keep `SaveTrickplayWithMedia=false` in both library options |
| Episode metadata: "Error in The Open Movie Database" JSON errors | OMDb episode endpoint returns malformed JSON | OMDb removed from the Shows library fetchers (movies keep it) |
| Intro Skipper: "ffmpeg did not exit within 60000ms" | two 4K software decodes in parallel on the DMZ VM | `MaxParallelism=1` in IntroSkipper.xml |
| Watched/resume state lost | config not copied byte-for-byte | restore `/opt/kdk-lab/jellyfin/config` from the migration tar |
| Playback buffers off-LAN | Cloudflare free-plan proxy throttling | test on LAN to isolate |

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | [[dmz-proxy]] + tunnel | ingress + public access |
| Needs | A380 `/dev/dri` | hardware transcode (falls back to CPU) |
| Needs | `/mnt/media` (NFS, RO) | no library content |
| Needed by | [[seerr]] | request fulfilment + user auth |

## Observability

No dashboard. `jellyfin.kmkdp.com` probed by [[kdk-mon-01]]; alerts → Pushover.

## Architecture

Listens on `8096`, joins `proxy-dmz`. `/dev/dri` gives QSV. Library mounted read-only; transcode scratch is a 6G tmpfs at `/config/cache/transcodes` (Jellyfin's real transcode path), never backed up. In-container library paths match k3s (`/data/media/...`) so item ids stay attached. Ramdisk + Arc QSV tuning: see [[jellyfin-transcoding]].

## Configuration
| LDAP-Auth plugin (dashboard, host-managed) | Server `lldap.kmkdp.com`, port 6360, SSL on, **SkipSslVerify off** (LE wildcard via lldap's certs-dumper, 2026-10-27); base `ou=people,dc=kmkdp,dc=com`; filter `(memberof=cn=jellyfin-users,ou=groups,dc=kmkdp,dc=com)`; create users from LDAP on |
| Networking (dashboard, host-managed) | Known proxies `10.211.2.0/24` (proxy-dmz: Traefik + cloudflared); LAN networks `10.1.1.0/24, 10.1.3.0/24, 10.1.20.0/24, 10.1.30.0/24, 192.168.191.0/24` (Tailscale clients arrive un-masqueraded as 100.64.0.0/10 since 2026-10-26 and are therefore remote; before that 10.1.20.0/24 had to be left out); auto discovery off; Internet streaming bitrate limit applies to remote clients (set 2026-10-26, raise for more remote viewers) |

| Path / setting | What it is |
|---|---|
| `/opt/kdk-lab/jellyfin/config` | jellyfin.db (users, watched), plugins, config |
| `stacks/jellyfin/plugins.yaml` | plugin GUIDs + pinned versions |
| `tmpfs /config/cache/transcodes` | 6G RAM-disk scratch (`mode=1777`), excluded from backup — see [[jellyfin-transcoding]] |
| `/opt/kdk-lab/jellyfin/config/encoding.xml` | Arc QSV/HDR encoding settings (host state, not in git) |

## Secret Rotation

N/A — no credential of its own; user auth is delegated to LLDAP via the LDAP plugin.

## User Guide

Users log in at `jellyfin.kmkdp.com` with their directory account (Wizarr issues invites). Playback, resume, and favourites carry over from the k3s instance.
