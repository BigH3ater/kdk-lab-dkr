---
title: Audiobookshelf
host: kdk-dkr-dmz-01
stack: audiobookshelf
tier: dmz
status: live
storage: yes
secrets: []
updated: 2026-10-28
tags: [homelab/stack, tier/dmz, status/live]
---

# Audiobookshelf

Audiobook + podcast server on the DMZ node; LAN/Tailscale only — its router is on the DMZ Traefik's `websecure` entrypoint, not the Cloudflare tunnel (publishing planned). Login is ABS's own auth or Authelia OIDC (client `audiobookshelf`), no ForwardAuth (it would break the mobile apps).

**Related:** [[kdk-dkr-dmz-01]] · [[dmz-proxy]] · [[identity]] · [[media]]

## At a glance

| Field | Value |
|---|---|
| Purpose | Audiobooks (Chaptarr-managed, read-only) and podcasts (RSS auto-download) |
| Host | [[kdk-dkr-dmz-01]] |
| Endpoint | `https://audiobookshelf.kmkdp.com` (native `/audiobookshelf` base path) |
| Image | `ghcr.io/advplyr/audiobookshelf` (pinned in `stacks/audiobookshelf/compose.yaml`) |
| Deploy | Komodo stack `audiobookshelf` (git-linked) |
| Storage | config + metadata (SQLite DB) local in `/opt/kdk-lab/audiobookshelf`; audiobooks RO from `/mnt/media/books/audiobook`; podcasts rw on NFS `/mnt/appdata/audiobookshelf/podcasts` |
| Blast radius | No audiobook/podcast streaming; progress sync pauses |

## Verify

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://audiobookshelf.kmkdp.com/healthcheck   # 200
ssh kdkadmin@192.168.191.20 'ls /mnt/appdata/audiobookshelf/podcasts | head'          # podcast folders
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| New podcast episodes fail to download | `/mnt/appdata` NFS not mounted or not writable by the container user | `findmnt /mnt/appdata`; NAS dir is `1000:1000` (works as root today and as uid 1000 after hardening) |
| Client assets 404 at the bare domain | base path changed | keep `ROUTER_BASE_PATH=/audiobookshelf` (see compose comment) |

## Configuration

| Path / setting | What it is |
|---|---|
| `/opt/kdk-lab/audiobookshelf/config` | SQLite DB, users, settings (local disk, nightly backup-dmz) |
| `/opt/kdk-lab/audiobookshelf/metadata` | covers, cache (local disk) |
| `/mnt/media/books/audiobook` → `/audiobooks:ro` | audiobook library (media NFS is read-only on the DMZ) |
| `/mnt/appdata/audiobookshelf/podcasts` → `/podcasts` | podcast library, written by ABS. Moved off the 59 GB root disk 2026-10-28 (7.5 GB). Library path in ABS stays `/podcasts`. Not in backup-dmz (tankz3 snapshots only; re-downloadable) |

## Secret Rotation

N/A in this stack; the OIDC client secret lives in [[identity]].
