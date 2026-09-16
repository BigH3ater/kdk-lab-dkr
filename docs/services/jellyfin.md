---
title: Jellyfin
host: kdk-dkr-dmz-01
stack: jellyfin
tier: dmz
status: live
storage: yes
secrets: []
updated: 2026-09-12
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
| Image | `lscr.io/linuxserver/jellyfin:10.11.11ubu2604-ls46` — the 10.11.11 line, pinned |
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
| Plugins read "Restart"/broken after an image bump | 12.0 breaks the plugin ABI (ADR 0027) | stay on the 10.11.11 line |
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
