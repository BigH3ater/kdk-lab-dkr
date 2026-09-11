---
title: Media stack
---
# Media stack

Acquisition + transcode on kdk-dkr-01. Everything behind ForwardAuth (sso).

| | |
|---|---|
| Host | kdk-dkr-01 |
| Stack | `stacks/media` — prowlarr, sabnzbd, sonarr, radarr, bazarr, chaptarr, wizarr, tdarr + tdarr-node (NVENC, GTX 1050 Ti) |
| URLs | `<app>.kmkdp.com` via the wildcard |
| Data | /opt/kdk-lab/media/<app> (migrated 2026-09-11 from k3s PVCs); bulk on /mnt/media |
| Layout | /config, /data/staging + /downloads = media/staging, /data/media/{tv,movies} |
| Tdarr | server :8266 published for external nodes (RGB-Nebula 4090); internal node kdk-tdarr-nvenc-01; flow = NVENC, no AV1 |

Verify: each UI 302s to sso then loads; `docker exec media-tdarr-node-1 nvidia-smi -L` shows the 1050 Ti.
