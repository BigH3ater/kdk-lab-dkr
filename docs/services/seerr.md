---
title: Seerr
---
# Seerr

Request UI, public via the tunnel; its own Jellyfin-account auth.

| | |
|---|---|
| Host | kdk-dkr-dmz-01 |
| URL | https://seerr.kmkdp.com |
| Stack | `stacks/dmz-apps` — seerr v3.4.1 |
| Data | /mnt/appdata/jellyseerr (NFS, same data as the k3s deploy — no copy needed) |

After the jellyfin move, re-point Seerr's Jellyfin address to
https://jellyfin.kmkdp.com if it referenced an in-cluster address.
