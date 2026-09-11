---
title: Jellyfin
---
# Jellyfin

Media server on the DMZ node, QSV transcode on the Arc A380, public via the
Cloudflare tunnel. Own login (LDAP plugin) — no ForwardAuth.

| | |
|---|---|
| Host | kdk-dkr-dmz-01, `/dev/dri` mapped (i915, requires the FULL kernel — cloud kernel has no GPU drivers) |
| URL | https://jellyfin.kmkdp.com (LAN + tunnel) |
| Stack | `stacks/jellyfin` — image pinned to the 10.11.11 line (12.0 breaks plugin ABI, ADR 0027) |
| Data | /opt/kdk-lab/jellyfin/config (byte-for-byte from k3s); library RO from /mnt/media |
| Transcode | 6G tmpfs at /transcodes, never backed up |
| Plugins | see `stacks/jellyfin/plugins.yaml` (GUID + pinned version) |

Verify: login works, watched state present, playback transcodes show
`(hw)` in the dashboard, all plugins read Active.
